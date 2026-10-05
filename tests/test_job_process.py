"""Jobs in a separate worker process: the GUI and the solver no longer share a lock.

Reproduces the reported defect (the window freezing during a long run) at its
cause: a CPU-bound Python job in a *thread* makes every lock hand-off by the main
thread cost milliseconds; in a worker process it does not.
"""

from __future__ import annotations

import copy
import os
import pickle
import sys
import time
import types

import pytest

import job_process_helpers as helpers
from anyfem.document import DocumentSession
from anyfem.jobs import JobManager, _resolve_executor
from anyfem.job_process import dumps, loads
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition, JobStatus


def run(function, *, executor="process", kwargs=None, timeout=60.0):
    project = Project("worker process")
    manager = JobManager(project, executor=executor)
    record = manager.submit(AnalysisDefinition("Job"), DocumentSession(project).snapshot(), function, kwargs=kwargs or {})
    manager.wait(record.id, timeout=timeout)
    return manager, record, manager.poll()


def test_job_runs_in_another_process_and_reports_progress():
    manager, record, events = run(helpers.add, kwargs={"a": 40, "b": 2})
    assert record.status == JobStatus.COMPLETED
    result = manager.result(record.id)
    assert result["sum"] == 42 and result["pid"] != os.getpid()
    assert [e.kind for e in events if e.kind in ("started", "progress", "completed")] == ["started", "progress", "completed"]
    assert any(e.message == "hello from the worker" for e in events)
    assert any(entry["kind"] == "progress" for entry in manager.log(record.id))


def test_structured_progress_keeps_its_payload():
    _, record, events = run(helpers.structured)
    progress = next(e for e in events if e.kind == "progress")
    assert str(progress.payload) == "linear_static.complete"          # still readable as text
    assert progress.payload.payload["completed"] == 1 and progress.payload.get("total") == 3


def test_a_failure_in_the_worker_keeps_its_type_and_traceback():
    manager, record, events = run(helpers.fail)
    assert record.status == JobStatus.FAILED
    diagnostic = record.diagnostics[-1]
    assert diagnostic["type"] == "ValueError" and "boom in the worker" in diagnostic["message"]
    assert "job_process_helpers.py" in diagnostic["traceback"] and "def fail" not in diagnostic["traceback"] or "fail" in diagnostic["traceback"]
    assert any(e.kind == "failed" for e in events)


def test_a_worker_that_dies_fails_the_job_not_the_application():
    manager, record, events = run(helpers.crash)
    assert record.status == JobStatus.FAILED
    assert "exit code 7" in record.diagnostics[-1]["message"]
    assert "application and the model are unaffected" in record.diagnostics[-1]["message"]
    # the manager is still usable afterwards
    again = manager.submit(AnalysisDefinition("Job"), DocumentSession(Project("x")).snapshot(), helpers.add, kwargs={})
    assert manager.wait(again.id, timeout=60).status == JobStatus.COMPLETED


def test_cancellation_reaches_the_worker_at_its_safe_point():
    project = Project("cancel")
    manager = JobManager(project, executor="process")
    record = manager.submit(AnalysisDefinition("Job"), DocumentSession(project).snapshot(), helpers.wait_for_cancel)
    deadline = time.monotonic() + 60
    while not any(e.message == "ready" for e in manager.poll()):
        assert time.monotonic() < deadline, "worker never reported ready"
        time.sleep(0.02)
    assert manager.cancel(record.id)
    assert manager.wait(record.id, timeout=20).status == JobStatus.CANCELLED


def test_a_job_that_cannot_be_transferred_fails_with_an_actionable_message():
    manager, record, _ = run(lambda **kwargs: 1)          # a closure cannot be imported by name
    assert record.status == JobStatus.FAILED
    assert record.diagnostics[-1]["type"] == "JobNotTransferable"
    assert "picklable" in record.diagnostics[-1]["message"]


def test_result_with_read_only_views_round_trips_and_the_rule_stays_global():
    manager, record, _ = run(helpers.read_only_views)
    result = manager.result(record.id)
    assert isinstance(result["view"], types.MappingProxyType) and isinstance(result["view"]["nested"], types.MappingProxyType)
    assert result["view"]["nested"]["b"] == 2 and list(result["array"]) == [0.0, 1.0, 2.0, 3.0, 4.0]
    with pytest.raises(TypeError):
        result["view"]["a"] = 5                              # still read-only
    # ANYfem's rule that these views are not silently copyable elsewhere is untouched
    with pytest.raises(TypeError):
        pickle.dumps(types.MappingProxyType({}))
    with pytest.raises(TypeError):
        copy.deepcopy(types.MappingProxyType({}))
    assert loads(dumps({"v": types.MappingProxyType({"k": 1})}))["v"]["k"] == 1


def test_thread_local_state_in_a_result_travels_as_an_empty_one():
    # An Impact result carries a threading.local (solver work buffers) in its cached contact
    # geometry.  What one thread stored in it is invisible to every other thread, so the
    # receiving process correctly starts with an empty one, as a thread job's reader would.
    manager, record, _ = run(helpers.thread_local_result)
    assert record.status == JobStatus.COMPLETED
    result = manager.result(record.id)
    assert result["value"] == 42
    assert isinstance(result["cache"], type(__import__("threading").local()))
    assert not hasattr(result["cache"], "buffer")


def test_an_unpicklable_result_is_reported_not_lost():
    _, record, _ = run(helpers.unpicklable_result)
    assert record.status == JobStatus.FAILED
    assert record.diagnostics[-1]["type"] == "ResultTransferError"
    assert "could not be transferred" in record.diagnostics[-1]["message"]


def test_worker_runs_below_normal_priority_so_the_gui_wins_contention():
    manager, record, _ = run(helpers.priority)
    assert manager.result(record.id)["below_normal"] is True


def test_shutdown_stops_the_worker_at_once():
    project = Project("shutdown")
    manager = JobManager(project, executor="process")
    record = manager.submit(AnalysisDefinition("Job"), DocumentSession(project).snapshot(), helpers.sleep_long)
    deadline = time.monotonic() + 60
    while not any(e.message == "sleeping" for e in manager.poll()):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    process = manager._requests[record.id].process
    assert process.is_alive()
    manager.shutdown()
    assert manager.wait(record.id, timeout=20).status == JobStatus.FAILED   # no cooperative wait, no 60 s sleep
    assert not process.is_alive()


def test_executor_selection(monkeypatch):
    assert JobManager().executor == "thread"                  # the library default is unchanged
    assert JobManager(executor="process").executor == "process"
    monkeypatch.setenv("ANYFEM_JOB_EXECUTOR", "process")
    assert JobManager().executor == "process" and JobManager(executor="thread").executor == "thread"
    with pytest.raises(ValueError, match="unknown job executor"):
        _resolve_executor("gpu")


def test_shutdown_during_spawn_does_not_leave_a_late_worker_or_start_queue(monkeypatch):
    import threading
    import anyfem.jobs as jobs
    original = jobs.run_in_worker_process
    spawned, release = threading.Event(), threading.Event()
    processes = []

    def delayed_registration(**kwargs):
        callback = kwargs.pop("on_process")

        def register(process):
            processes.append(process)
            spawned.set()
            assert release.wait(10)
            callback(process)

        return original(**kwargs, on_process=register)

    monkeypatch.setattr(jobs, "run_in_worker_process", delayed_registration)
    project = Project("shutdown race")
    manager = JobManager(project, executor="process")
    snapshot = DocumentSession(project).snapshot()
    active = manager.submit(AnalysisDefinition("Active"), snapshot, helpers.sleep_long)
    assert spawned.wait(20)
    queued = manager.submit(AnalysisDefinition("Queued"), snapshot, helpers.sleep_long)
    try:
        manager.shutdown()
    finally:
        release.set()
    manager.wait(active.id, timeout=20)
    assert not processes[0].is_alive()
    assert queued.status == JobStatus.CANCELLED
    with pytest.raises(RuntimeError, match="shut down"):
        manager.submit(AnalysisDefinition("Late"), snapshot, helpers.sleep_long)


def _main_thread_cost(executor):
    """Seconds for the main thread to do 100 short sleeps while a CPU-bound Python job runs.

    Each sleep releases and re-acquires the interpreter lock, like every Qt call a
    paint or a click makes.  Uncontended this takes about 0.06 s.
    """
    project = Project("contention")
    manager = JobManager(project, executor=executor)
    record = manager.submit(AnalysisDefinition("Job"), DocumentSession(project).snapshot(), helpers.busy, kwargs={"seconds": 4.0})
    deadline = time.monotonic() + 60
    while not any(e.message == "busy" for e in manager.poll()):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    time.sleep(0.3)                                           # let the worker get properly busy
    start = time.perf_counter()
    for _ in range(100):
        time.sleep(0.0005)
    cost = time.perf_counter() - start
    manager.wait(record.id, timeout=60)
    return cost


def test_a_cpu_bound_job_does_not_starve_the_main_thread_in_a_worker_process():
    process = _main_thread_cost("process")
    thread = _main_thread_cost("thread")
    # the reported defect: with a thread every hand-off waits for the lock
    assert process < 0.35, f"main thread was slowed to {process:.2f} s by a worker process"
    assert thread > 2 * process, f"expected the thread job to be much worse (thread {thread:.2f} s, process {process:.2f} s)"
