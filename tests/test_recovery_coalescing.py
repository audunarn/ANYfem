"""Recovery demand is coalesced before immutable UI-thread capture."""
from concurrent.futures import Future
from types import SimpleNamespace
from threading import get_ident

import pytest
from anyfem.application.workflow import WorkbenchWorkflow
from anyfem.commands import AddPoint
from anyfem.document import DocumentSession
from anyfem.model import Project


class Executor:
    def __init__(self):
        self.calls = []
        self.immediate = False

    def submit(self, function, *args, **kwargs):
        future = Future()
        self.calls.append((future, args, kwargs))
        if self.immediate:
            future.set_result(None)
        return future


@pytest.fixture
def app():
    class Frontend(WorkbenchWorkflow):
        def set_status(self, message, **kwargs):
            self.statuses.append((message, kwargs))
        def _poll_result_artifacts(self):
            pass
        def _poll_job_log_artifacts(self):
            pass
    value = Frontend()
    project = Project()
    value.workbench = SimpleNamespace(project=project, session=DocumentSession(project), path=None)
    value.scheduler = SimpleNamespace(call_later=lambda *args: object(), cancel_call=lambda identifier: None)
    value.statuses = []
    value._recovery_executor = Executor()
    value._recovery_future = None
    value._recovery_future_epoch = 0
    value._recovery_epoch = 0
    value._recovery_pending = None
    value._autosave_after = value._autosave_hard_after = None
    value._artifact_futures = {}; value._log_futures = {}
    value.session.execute(AddPoint(0, 0, 0))
    return value


def test_requests_capture_only_first_and_latest_on_ui_thread(app, monkeypatch):
    captures = []
    original = app.session.snapshot
    def snapshot():
        captures.append(get_ident())
        return original()
    monkeypatch.setattr(app.session, 'snapshot', snapshot)
    app._write_recovery()
    first = app._recovery_executor.calls[0]
    for i in range(5):
        app.session.execute(AddPoint(i + 1, 0, 0))
        app._write_recovery()
    assert len(captures) == 1
    assert len(app._recovery_executor.calls) == 1
    first[0].set_result(None)
    app._poll_recovery_write()
    assert captures == [get_ident(), get_ident()]
    latest = app._recovery_executor.calls[-1]
    assert latest[2]['revision_sequence'] > first[2]['revision_sequence']
    assert latest[1][0] != first[1][0]
    assert latest[1][0] == dict(original().document)
    latest[0].set_result(None)
    app.flush_project_writes()
    assert app._recovery_pending is None


def test_deferred_capture_failure_keeps_demand_and_flush_retries(app, monkeypatch):
    app._write_recovery(); app._write_recovery()
    original = app.session.snapshot
    def fail():
        raise ValueError('invalid snapshot')
    monkeypatch.setattr(app.session, 'snapshot', fail)
    app._recovery_future.set_result(None)
    app._poll_recovery_write()
    assert app._recovery_pending == app._recovery_epoch
    assert 'snapshot failed' in app.statuses[-1][0]
    with pytest.raises(ValueError, match='invalid snapshot'):
        app.flush_project_writes()
    monkeypatch.setattr(app.session, 'snapshot', original)
    app._recovery_executor.immediate = True
    app.flush_project_writes()
    assert len(app._recovery_executor.calls) == 2
    assert app._recovery_pending is None


@pytest.mark.parametrize('stale', [False, True])
def test_clean_or_replaced_document_discards_pending_capture(app, stale):
    app._write_recovery(); app._write_recovery()
    if stale:
        app._recovery_epoch += 1
    else:
        app.session.dirty = False
    app._recovery_future.set_result(None)
    app._poll_recovery_write()
    assert len(app._recovery_executor.calls) == 1
    assert app._recovery_pending is None


def test_failed_write_does_not_lose_latest_pending_request(app):
    app._write_recovery()
    app.session.execute(AddPoint(2, 3, 4))
    app._write_recovery()
    app._recovery_future.set_exception(OSError('disk unavailable'))
    app._recovery_executor.immediate = True
    app.flush_project_writes()
    assert len(app._recovery_executor.calls) == 2
    assert any('autosave failed' in message for message, _ in app.statuses)
    assert app._recovery_future is None
