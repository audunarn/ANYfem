"""Native macOS lifecycle scenarios for contained worker trees.

Every test here is skipped off Darwin, so a green run elsewhere proves nothing
about macOS.  The Mac CI runner passes ``REQUIRED_TEST_IDS`` to
``tools/test_anyfem.py --require-passed`` so that a skipped, missing, xfailed or
failed scenario is a non-success rather than a quiet gap.

Job functions are module-level because the spawned worker imports them by name.
"""

from __future__ import annotations

import os
import signal
import select
import subprocess
import sys
import time
from importlib.util import find_spec

import pytest

import job_process_helpers as helpers
import anyfem.job_process as job_process
from anyfem.document import DocumentSession
from anyfem.job_process import WorkerProcessError, run_in_worker_process
from anyfem.jobs import _cancellation_token
from anyfem.model.project import Project

HAS_DIAGNOSIS = find_spec("anyfem.geometry_diagnosis_worker") is not None

REQUIRED_TEST_IDS = [
    "tests/test_macos_containment.py::test_completion_retires_the_owned_group",
    "tests/test_macos_containment.py::test_worker_leads_its_group_and_guardian_acknowledged_before_go",
    "tests/test_macos_containment.py::test_caller_cancellation_retires_the_tree",
    "tests/test_macos_containment.py::test_startup_refusal_never_dispatches",
    "tests/test_macos_containment.py::test_worker_crash_retires_the_pool_child",
    "tests/test_macos_containment.py::test_guardian_death_retires_the_tree",
    "tests/test_macos_containment.py::test_unrelated_processes_are_never_signalled",
    "tests/test_job_process_containment.py::test_contained_worker_runs_inside_a_registered_owned_tree",
    "tests/test_job_process_containment.py::test_owned_tree_takes_the_pool_children_with_it_on_completion",
    "tests/test_job_process_containment.py::test_cancellation_during_startup_never_dispatches",
    "tests/test_job_process_containment.py::test_registration_failure_terminates_the_worker_before_dispatch",
    "tests/test_job_process_containment.py::test_parent_death_kills_the_owned_tree",
    "tests/test_darwin_process.py::test_real_live_child_is_running_and_exited_child_is_absent",
    "tests/test_darwin_process.py::test_real_zombie_is_dead_but_not_absent",
    "tests/test_darwin_process.py::test_darwin_retirement_fails_while_another_group_of_the_owned_session_lives",
    "tests/test_darwin_process.py::test_reaped_darwin_worker_never_signals_a_reusable_group",
    "tests/test_darwin_process.py::test_unexpected_watch_failure_kills_only_the_owned_group[parent-watch-error]",
    "tests/test_darwin_process.py::test_unexpected_watch_failure_kills_only_the_owned_group[guardian-watch-error]",
    "tests/test_macos_containment.py::test_other_group_in_the_owned_session_fails_the_retirement_gate",
    "tests/test_macos_containment.py::test_startup_and_ack_durations_are_recorded_within_the_deadlines",
]
if HAS_DIAGNOSIS:
    REQUIRED_TEST_IDS.append("tests/test_macos_containment.py::test_diagnosis_worker_cancellation_retires_the_tree_without_result")

darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="native macOS containment scenarios")

#: Generous but finite bounds for real spawns on a hosted runner.
RUN_SECONDS = 60.0
DEATH_SECONDS = 20.0


# ----------------------------------------------------------------------
# jobs (imported by name inside the worker)
# ----------------------------------------------------------------------
def _start_pool_child(progress):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    progress(f"child {child.pid}")
    return child


def pool_then_wait(*, project, progress, cancellation_token=None):
    _start_pool_child(progress)
    time.sleep(120)


def pool_in_other_group_then_return(*, project, progress, cancellation_token=None):
    """Like an ANYmesh pool worker: own process group, same session."""
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", "import os,time; os.setpgid(0,0); print('ready',flush=True); time.sleep(120)"],
        stdout=subprocess.PIPE,
    )
    ready, _, _ = select.select([child.stdout], [], [], 5.0)
    if not ready or child.stdout.readline() != b"ready\n":
        child.kill()
        child.wait(timeout=5)
        raise RuntimeError("separate-group child did not acknowledge startup")
    child.stdout.close()
    progress(f"child {child.pid}")
    return {"child_pid": child.pid, "child_pgid": os.getpgid(child.pid), "worker_pgid": os.getpgrp()}


def diagnose_after_pool_child(*, project, progress, cancellation_token=None):
    """The production diagnosis entry, entered after a pool child exists.

    The parent cancels on the child message; messages are processed in order, so
    the cancellation is seen before any diagnosis message or result.
    """
    from anyfem.geometry_diagnosis_worker import diagnose_snapshot_project

    _start_pool_child(progress)
    return diagnose_snapshot_project(project=project, progress=progress, cancellation_token=cancellation_token)


def pool_then_crash(*, project, progress, cancellation_token=None):
    _start_pool_child(progress)
    time.sleep(0.3)
    os._exit(9)


# ----------------------------------------------------------------------
# harness
# ----------------------------------------------------------------------
def _alive(pid: int) -> bool:
    from anyfem._darwin_process import pid_state

    return pid_state(int(pid)) == "running"


def _wait_dead(pid: int, seconds: float = DEATH_SECONDS) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


def _guardians_of(worker_pid: int):
    import psutil

    found = []
    for child in psutil.Process(worker_pid).children():
        try:
            if any("_darwin_guardian.py" in part for part in child.cmdline()):
                found.append(child)
        except psutil.NoSuchProcess:
            continue
    return found


class _Run:
    def __init__(self):
        self.token = _cancellation_token()
        self.processes = []
        self.trees = []
        self.child_pids = []
        self.messages = []
        self.on_message = None
        self.tree_facts = []

    def progress(self, value):
        text = str(getattr(value, "message", value))
        self.messages.append(text)
        if text.startswith("child "):
            self.child_pids.append(int(text.split()[1]))
        if self.on_message is not None:
            self.on_message(text)

    def on_tree(self, process, tree):
        self.processes.append(process)
        self.trees.append(tree)
        facts = {"group": tree.group, "pid": process.pid, "pgid": os.getpgid(process.pid)}
        try:
            facts["guardians"] = [guardian.pid for guardian in _guardians_of(process.pid)]
        except Exception as error:  # noqa: BLE001 - recorded, asserted by the test
            facts["guardians_error"] = repr(error)
        self.tree_facts.append(facts)

    def run(self, function, *, kwargs=None, terminate_on_cancel=False):
        return run_in_worker_process(
            function=function,
            kwargs=kwargs or {},
            snapshot=DocumentSession(Project("macos-containment")).snapshot(),
            project_override=None,
            progress=self.progress,
            token=self.token,
            containment=True,
            on_tree=self.on_tree,
            terminate_on_cancel=terminate_on_cancel,
        )


def _assert_group_empty(run: _Run) -> None:
    from anyfem._darwin_process import group_survivor

    group = run.tree_facts[0]["group"]
    assert group_survivor(group, time.monotonic() + 10.0) is None, "owned group still has live members"


# ----------------------------------------------------------------------
# scenarios
# ----------------------------------------------------------------------
@darwin_only
def test_completion_retires_the_owned_group():
    run = _Run()
    result = run.run(helpers.spawn_child_and_return)
    assert result["child_pid"] in run.child_pids
    assert _wait_dead(result["child_pid"]), "the pool child outlived the completed job"
    _assert_group_empty(run)


@darwin_only
def test_worker_leads_its_group_and_guardian_acknowledged_before_go():
    run = _Run()
    run.run(helpers.add, kwargs={"a": 1, "b": 2})
    facts = run.tree_facts[0]
    assert facts["group"] == facts["pid"] == facts["pgid"] != os.getpgrp()
    assert "guardians_error" not in facts, facts.get("guardians_error")
    # on_tree runs after "ready" and before "go": the guardian ACK preceded both.
    assert len(facts["guardians"]) == 1
    _assert_group_empty(run)


@darwin_only
def test_caller_cancellation_retires_the_tree():
    run = _Run()
    run.on_message = lambda text: run.token.cancel("test cancels") if text.startswith("child ") else None
    started = time.monotonic()
    with pytest.raises(WorkerProcessError) as error:
        run.run(pool_then_wait, terminate_on_cancel=True)
    assert error.value.remote_type == "CancelledByCaller"
    assert time.monotonic() - started < RUN_SECONDS
    assert not getattr(error.value, "cleanup_failures", None)
    assert _wait_dead(run.child_pids[0])
    _assert_group_empty(run)


@darwin_only
def test_startup_refusal_never_dispatches(monkeypatch, tmp_path):
    import psutil

    monkeypatch.setattr(job_process, "_darwin_containment_refusal", lambda: "simulated refusal")
    before = {child.pid for child in psutil.Process().children(recursive=True)}
    marker = tmp_path / "ran.txt"
    run = _Run()
    with pytest.raises(WorkerProcessError) as error:
        run.run(helpers.touch_marker, kwargs={"path": str(marker)})
    assert error.value.remote_type == "ContainmentUnavailable"
    assert "simulated refusal" in str(error.value)
    assert not marker.exists() and not run.processes
    assert {child.pid for child in psutil.Process().children(recursive=True)} == before


@darwin_only
def test_worker_crash_retires_the_pool_child():
    run = _Run()
    with pytest.raises(WorkerProcessError) as error:
        run.run(pool_then_crash)
    assert error.value.remote_type == "WorkerProcessExited"
    assert _wait_dead(run.child_pids[0]), "the pool child outlived its crashed worker"
    _assert_group_empty(run)


@darwin_only
def test_guardian_death_retires_the_tree():
    run = _Run()

    def kill_guardian(text):
        if text.startswith("child "):
            guardians = _guardians_of(run.processes[0].pid)
            assert len(guardians) == 1, "expected exactly one live guardian"
            os.kill(guardians[0].pid, signal.SIGKILL)

    run.on_message = kill_guardian
    started = time.monotonic()
    with pytest.raises(WorkerProcessError) as error:
        run.run(pool_then_wait)
    assert time.monotonic() - started < RUN_SECONDS
    assert error.value.remote_type == "WorkerProcessExited"
    assert _wait_dead(run.child_pids[0]), "the pool child outlived the guardian"
    _assert_group_empty(run)


@darwin_only
def test_other_group_in_the_owned_session_fails_the_retirement_gate():
    # The group kill cannot reach a member that moved to its own group, so the
    # session-wide gate must refuse to publish the result.  Children that
    # escape into arbitrary NEW sessions are outside this claim.
    run = _Run()
    try:
        with pytest.raises(WorkerProcessError) as error:
            run.run(pool_in_other_group_then_return)
        assert error.value.remote_type == "WorkerCleanupFailed", error.value
        assert "discarded" in str(error.value), error.value
        child = run.child_pids[0]
        assert _alive(child), "the positive survivor control did not remain alive"
        group = run.tree_facts[0]["group"]
        assert os.getsid(child) == group and os.getpgid(child) != group, "scenario did not leave the group"
    finally:
        for pid in run.child_pids:  # only the child this test created
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for pid in run.child_pids:
            assert _wait_dead(pid)


@darwin_only
@pytest.mark.skipif(not HAS_DIAGNOSIS, reason="this candidate does not include the optional diagnosis feature")
def test_diagnosis_worker_cancellation_retires_the_tree_without_result():
    run = _Run()
    run.on_message = lambda text: run.token.cancel("test cancels") if text.startswith("child ") else None
    session = DocumentSession(Project("macos-diagnosis"))
    revision_before = repr(session.snapshot())
    outcome = []
    with pytest.raises(WorkerProcessError) as error:
        outcome.append(
            run_in_worker_process(
                function=diagnose_after_pool_child, kwargs={}, snapshot=session.snapshot(),
                project_override=None, progress=run.progress, token=run.token,
                containment=True, on_tree=run.on_tree, terminate_on_cancel=True,
            )
        )
    assert error.value.remote_type == "CancelledByCaller" and not outcome, "a result was attached"
    assert not getattr(error.value, "cleanup_failures", None)
    assert all(not text.startswith("Checking") for text in run.messages), "diagnosis ran after cancel"
    assert repr(session.snapshot()) == revision_before, "the document changed"
    assert _wait_dead(run.child_pids[0])
    _assert_group_empty(run)


@darwin_only
def test_startup_and_ack_durations_are_recorded_within_the_deadlines(record_property):
    # Measured, not asserted from constants.  on_tree runs after the worker's
    # guardian ACK and "ready", so the elapsed time to it is startup + ACK.
    # Platform evidence is PENDING until a real Mac run records these values.
    run = _Run()
    stamps = []
    on_tree = run.on_tree
    run.on_tree = lambda process, tree: (stamps.append(time.monotonic()), on_tree(process, tree))
    started = time.monotonic()
    run.run(helpers.add)
    startup = stamps[0] - started
    record_property("startup_to_ready_seconds", f"{startup:.3f}")
    record_property("platform_evidence", "pending-mac-run")
    print(f"darwin startup_to_ready_seconds={startup:.3f}")
    assert startup < job_process._HANDSHAKE_SECONDS, startup
    _assert_group_empty(run)


@darwin_only
def test_unrelated_processes_are_never_signalled():
    sleeper = [sys.executable, "-c", "import time; time.sleep(120)"]
    same_group = subprocess.Popen(sleeper)
    other_session = subprocess.Popen(sleeper, start_new_session=True)
    try:
        completed = _Run()
        completed.run(helpers.spawn_child_and_return)
        cancelled = _Run()
        cancelled.on_message = lambda text: cancelled.token.cancel("test") if text.startswith("child ") else None
        with pytest.raises(WorkerProcessError):
            cancelled.run(pool_then_wait, terminate_on_cancel=True)
        for run in (completed, cancelled):
            assert run.tree_facts[0]["group"] not in (os.getpgrp(), os.getpgid(other_session.pid))
        time.sleep(0.5)
        assert same_group.poll() is None and _alive(same_group.pid)
        assert other_session.poll() is None and _alive(other_session.pid)
    finally:
        for process in (same_group, other_session):
            process.kill()
            process.wait(timeout=10)
