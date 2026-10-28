"""Publication-path hard kill of the REAL ``save_prepared`` and truthful reporting of staging-cleanup failures.

Scope (labelled): the encoder is replaced INSIDE the worker process by a tiny deterministic fixture (``prepared_to_bytes -> bytes``) so
the test exercises the real staging / fsync / replace sequence of ``save_prepared`` without a prepared record.  No claim is made about
crashes of the GUI or undeletable disks: those leave the owned directory behind, which is then REPORTED, not hidden.
"""
import shutil
import threading
import time
from pathlib import Path

import pytest

from anysolver import CancellationToken
from anyfem.application import floating_job as fj
from anyfem.application.floating_job import FloatingCleanupError, owned_publication_workspace
from anyfem.job_process import run_in_worker_process


def real_save_then_block(*, project=None, publication_directory, target, ready_file, progress=None, cancellation_token=None):
    """Worker body: the real save_prepared with a tiny encoder fixture; blocks in the pre-replace check, after the temp is written."""
    from anyfem.application import floating_apply as fa_
    fa_.prepared_to_bytes = lambda prepared: b'tiny deterministic candidate'      # labelled encoder fixture
    calls = []

    def cancel():
        calls.append(1)
        if len(calls) == 3:                      # before encoding, after encoding, BEFORE THE ATOMIC REPLACE: the staged file exists
            staged = list(Path(publication_directory).iterdir())
            Path(ready_file).write_text(staged[0].name)
            time.sleep(120)
        return False
    fa_.save_prepared(object(), target, cancel=cancel, workspace=publication_directory)
    return {'published': True}


def test_real_save_prepared_hard_kill_before_replace_cleans_only_the_owned_workspace(tmp_path):
    target = tmp_path / 'record.npz'
    target.write_bytes(b'previous')
    other = tmp_path / 'other-job.tmp'
    other.write_bytes(b'unrelated')
    ready = tmp_path / 'ready.txt'
    outcome, holder, token = {}, {}, CancellationToken()

    def job():
        try:
            with owned_publication_workspace(target) as workspace:
                holder['workspace'] = workspace
                outcome['result'] = run_in_worker_process(
                    function=real_save_then_block,
                    kwargs=dict(publication_directory=str(workspace), target=str(target), ready_file=str(ready)),
                    snapshot=None, project_override={}, progress=lambda value: None, token=token, containment=True,
                    on_tree=lambda process, tree: holder.__setitem__('tree', tree))
        except BaseException as error:
            outcome['error'] = error

    thread = threading.Thread(target=job)
    thread.start()
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline and not (ready.exists() and ready.read_text()):
        time.sleep(0.05)
    assert ready.exists() and ready.read_text(), 'save_prepared never staged its temp'
    staged = holder['workspace'] / ready.read_text()
    assert staged.is_file() and staged.read_bytes() == b'tiny deterministic candidate'      # real fsynced staged file, not yet published
    assert target.read_bytes() == b'previous'
    token.cancel('hard cancel')
    holder['tree'].terminate()
    thread.join(40)
    assert not thread.is_alive()
    assert 'result' not in outcome and 'error' in outcome
    assert not staged.exists() and not holder['workspace'].exists()
    assert target.read_bytes() == b'previous' and other.read_bytes() == b'unrelated'
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(['record.npz', 'other-job.tmp', 'ready.txt'])


@pytest.fixture
def failing_rmtree(monkeypatch):
    real = shutil.rmtree
    state = {}

    def broken(path, *args, **kwargs):
        state['path'] = Path(path)
        raise PermissionError('simulated open handle')
    monkeypatch.setattr(fj.shutil, 'rmtree', broken)
    yield state
    monkeypatch.undo()
    if 'path' in state:
        real(state['path'], ignore_errors=True)       # test hygiene only


def test_cleanup_failure_after_a_successful_job_is_reported_and_names_the_leftover(tmp_path, failing_rmtree):
    target = tmp_path / 'record.npz'
    target.write_bytes(b'previous')
    with pytest.raises(FloatingCleanupError, match='publication staging') as caught:
        with owned_publication_workspace(target) as workspace:
            staged = workspace / 'x.tmp'
            staged.write_bytes(b'x')
            staged.replace(target)                       # the job's atomic commit happened
    assert str(failing_rmtree['path']) in str(caught.value) and 'not changed' in str(caught.value)
    assert target.read_bytes() == b'x'                   # the commit is NOT undone or denied by the cleanup report
    assert failing_rmtree['path'].exists()               # the leftover is reported, not pretended away


def test_cleanup_failure_never_replaces_the_primary_worker_failure(tmp_path, failing_rmtree):
    target = tmp_path / 'record.npz'
    with pytest.raises(RuntimeError, match='worker boom') as caught:
        with owned_publication_workspace(target):
            raise RuntimeError('worker boom')
    notes = getattr(caught.value, '__notes__', [])
    assert any('could not remove the owned publication staging directory' in n for n in notes)
    assert not isinstance(caught.value, FloatingCleanupError)


class _Rec:
    def __init__(self, sha, keys, settings):
        self.history = type('H', (), {'array_sha256': sha})()
        self.stage_keys, self.settings = keys, settings


def _reopen(monkeypatch, tmp_path, old, new, cancel_after_remap=False, save_calls=None):
    from anyfem.application import floating_apply as fa
    saved = [] if save_calls is None else save_calls
    monkeypatch.setattr(fj, '_load', lambda *a: (None, None))
    monkeypatch.setattr(fj, '_settings', lambda s: s)
    monkeypatch.setattr(fa, 'recover_prepared', lambda path: old)
    monkeypatch.setattr(fa, 'reopen_or_remap', lambda *a, **k: new)
    monkeypatch.setattr(fa, 'save_prepared', lambda prepared, path, cancel=None, workspace=None: saved.append(prepared))
    monkeypatch.setattr(fj, '_summary', lambda prepared, path: {'ok': True})

    class Token:
        is_cancelled = cancel_after_remap
    kwargs = dict(document={}, mesh_data={}, settings={}, record_path=str(tmp_path / 'r.npz'),
                  cancellation_token=Token() if cancel_after_remap else None)
    try:
        fj.reopen_floating_job(**kwargs)
    finally:
        pass
    return saved


def test_metadata_only_regate_with_identical_arrays_is_published(monkeypatch, tmp_path):
    old = _Rec('same-array-hash', {'mass': 'a'}, {'x': 1})
    new = _Rec('same-array-hash', {'mass': 'a', 'restoring_gate': 'marker'}, {'x': 1})
    assert _reopen(monkeypatch, tmp_path, old, new) == [new]


def test_identical_arrays_keys_and_settings_are_not_rewritten(monkeypatch, tmp_path):
    old = _Rec('h', {'mass': 'a'}, {'x': 1})
    assert _reopen(monkeypatch, tmp_path, old, _Rec('h', {'mass': 'a'}, {'x': 1})) == []


def test_metadata_only_publication_still_honours_cancellation(monkeypatch, tmp_path):
    old = _Rec('h', {'mass': 'a'}, {'x': 1})
    new = _Rec('h', {'mass': 'a', 'restoring_gate': 'marker'}, {'x': 1})
    saved = []
    with pytest.raises(Exception, match='cancelled'):
        _reopen(monkeypatch, tmp_path, old, new, cancel_after_remap=True, save_calls=saved)
    assert saved == []


def test_successful_cleanup_raises_nothing_and_removes_the_workspace(tmp_path):
    target = tmp_path / 'record.npz'
    with owned_publication_workspace(target) as workspace:
        (workspace / 'f').write_bytes(b'1')
    assert not workspace.exists()
