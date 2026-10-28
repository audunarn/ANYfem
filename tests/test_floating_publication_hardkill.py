"""REAL hard-kill of an owned worker while it holds a staged publication temp (no cooperative callback is involved).

A genuine worker PROCESS (the production ``run_in_worker_process`` containment) stages a temp file in the parent-owned per-job workspace and
blocks; the test then does exactly what ``WaveCalculation.cancel`` does (token cancel + tree.terminate).  Afterwards: the owned workspace and
the staged temp are gone, no result was produced, the previous target is byte-identical, and an unrelated file is untouched.
"""
import os
import threading
import time
from pathlib import Path

import pytest

from anysolver import CancellationToken
from anyfem.application import floating_apply as fa
from anyfem.application.floating_job import owned_publication_workspace
from anyfem.application.floating_prepare import FloatingPrepareError, FloatingSettings, FloatingComponent, _check_components
from anyfem.job_process import run_in_worker_process


def stage_then_block(*, project=None, publication_directory, ready_file, progress=None, cancellation_token=None):
    """Worker body (importable by the spawned child): stage a temp inside the owned workspace, announce it, then block."""
    import tempfile
    fd, name = tempfile.mkstemp(dir=publication_directory, prefix='record.npz.', suffix='.tmp')
    os.write(fd, b'staged candidate')
    os.fsync(fd)
    Path(ready_file).write_text(name)
    time.sleep(120)
    return {'published': True}


def test_hard_killed_worker_tree_leaves_no_staged_temp_no_result_and_the_old_record(tmp_path):
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
                    function=stage_then_block, kwargs=dict(publication_directory=str(workspace), ready_file=str(ready)),
                    snapshot=None, project_override={}, progress=lambda value: None, token=token, containment=True,
                    on_tree=lambda process, tree: holder.__setitem__('tree', tree))
        except BaseException as error:         # the terminated worker surfaces as an error in the parent thread
            outcome['error'] = error

    thread = threading.Thread(target=job)
    thread.start()
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline and not (ready.exists() and ready.read_text()):
        time.sleep(0.05)
    assert ready.exists() and ready.read_text(), 'the worker never staged its temp'
    staged = Path(ready.read_text())
    assert staged.is_file() and staged.parent == holder['workspace'] and staged.read_bytes() == b'staged candidate'
    token.cancel('hard cancel')
    holder['tree'].terminate()                                    # exactly WaveCalculation.cancel
    thread.join(40)
    assert not thread.is_alive()
    assert 'result' not in outcome and 'error' in outcome         # no result is ever produced
    assert not staged.exists() and not holder['workspace'].exists()
    assert target.read_bytes() == b'previous' and other.read_bytes() == b'unrelated'
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(['record.npz', 'other-job.tmp', 'ready.txt'])


def test_workspace_must_be_an_owned_directory_inside_the_absolute_target_directory(tmp_path):
    target = tmp_path / 'record.npz'
    target.write_bytes(b'previous')
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    nested = tmp_path / 'a' / 'b'
    nested.mkdir(parents=True)
    for bad in (elsewhere / 'x', tmp_path / 'missing', nested):
        with pytest.raises(FloatingPrepareError, match='workspace'):
            fa.save_prepared(object(), target, workspace=bad)
    with pytest.raises(FloatingPrepareError, match='absolute'):
        owned_publication_workspace('relative/record.npz').__enter__()
    assert target.read_bytes() == b'previous'


def test_public_max_panels_above_the_native_limit_is_refused_early():
    comp = FloatingComponent(1.0, 0.1, 0.0, 0.0)
    for bad in (601, 3, 10_000):
        with pytest.raises(FloatingPrepareError, match='600'):
            _check_components(FloatingSettings((comp,), (0.0, 1.0), 20.0, max_panels=bad))
    _check_components(FloatingSettings((comp,), (0.0, 1.0), 20.0, max_panels=600))      # the native limit itself is admitted
