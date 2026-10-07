"""Containment lifecycle for parallel mesh workers: real spawns, real cleanup.

Covers the safety repairs from the parallel-meshing review:

* the startup handshake polls in 100 ms slices and refuses containment
  (never an uncontained job) when the worker dies, never confirms, or the
  platform cannot contain trees at all;
* ownership is registered BEFORE "go", and a registration failure tears the
  worker down before anything is dispatched;
* cancellation during startup aborts before dispatch and stops the
  undispatched owned worker immediately (one shared 5 s deadline, no
  join-before-terminate);
* the owned tree takes the worker's own children with it on completion;
* the guardian is never dismissed: the outer worker stays anchored after its
  terminal result/error, the parent kills the owned group while the anchor
  is alive and confirms retirement before the outcome is published, and
  the guardian kills its OWN group when the outer worker or the GUI
  disappears;
* a cleanup whose native stop is not confirmed is recorded, discards the
  result and never masks the original failure;
* a stop request is not retirement: a permission refusal never confirms the
  stop, a surviving owned process and a failed inspection (job query or
  /proc scan) fail closed, and the Windows job accounting is verified
  against a real assigned process;
* a predispatch refusal (the worker was never placed inside the owned tree)
  stops only the created worker promptly, so refusal cleanup does not
  inherently fail and the serial fallback stays allowed;
* killing only the parent (the GUI stand-in) kills the whole owned tree:
  the Windows job object's kill-on-close, or on Linux the parent-death
  signal plus the cleanup guardian.

Platform conditions are exact: real-spawn containment tests run on Windows
(job object) and Linux (process group + guardian).  The guardian's pipe
watch is Linux-only production logic (POSIX select), so those tests are
marked by platform; the Linux parent-death path is exercised by root in WSL,
the identical test body runs here on Windows.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import sys
import time
from pathlib import Path

import pytest

import job_process_helpers as helpers
import anyfem.job_process as job_process
from anyfem.document import DocumentSession
from anyfem.job_process import WorkerProcessError, run_in_worker_process
from anyfem.jobs import _cancellation_token
from anyfem.model.project import Project

CONTAINED_PLATFORMS = ("win32", "linux")
requires_containment = pytest.mark.skipif(
    sys.platform not in CONTAINED_PLATFORMS,
    reason="contained worker trees are supported on Windows (job object) and Linux (process group + guardian)",
)
requires_linux = pytest.mark.skipif(
    sys.platform != "linux",
    reason="the guardian's pipe watch is Linux-only production logic (POSIX select)",
)


def _snapshot():
    return DocumentSession(Project("containment")).snapshot()


def _run(function, *, token=None, containment=True, on_process=None, on_tree=None,
         kwargs=None, progress=None):
    return run_in_worker_process(
        function=function,
        kwargs=kwargs or {},
        snapshot=_snapshot(),
        project_override=None,
        progress=progress if progress is not None else (lambda value: None),
        token=token if token is not None else _cancellation_token(),
        containment=containment,
        on_process=on_process,
        on_tree=on_tree,
    )


def _pid_alive(pid: int) -> bool:
    pid = int(pid)
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            # A joined multiprocessing.Process can still retain a handle to
            # an exited PID. A queryable PID alone does not mean it is alive.
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
            state = stat.read().rsplit(") ", 1)[1][0]
        return state != "Z"
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return False


# ----------------------------------------------------------------------
# guardian loop (Linux-only production logic: POSIX select on a pipe)
# ----------------------------------------------------------------------
class _AliveParent:
    def is_alive(self):
        return True


class _DeadParent:
    def is_alive(self):
        return False


@requires_linux
def test_guardian_loop_kills_the_group_when_the_outer_worker_dies():
    read_fd, write_fd = os.pipe()
    os.close(write_fd)  # EOF: the outer worker's liveness pipe closed
    try:
        assert job_process._guardian_loop(read_fd, _AliveParent()) == "outer"
    finally:
        os.close(read_fd)


@requires_linux
def test_guardian_loop_kills_the_group_when_the_gui_sentinel_closes():
    read_fd, write_fd = os.pipe()
    try:
        assert job_process._guardian_loop(read_fd, _DeadParent()) == "parent"
    finally:
        os.close(read_fd)
        os.close(write_fd)


@requires_linux
def test_guardian_loop_keeps_watching_while_the_outer_worker_lives():
    import threading

    read_fd, write_fd = os.pipe()
    result = {}

    def watch():
        result["outcome"] = job_process._guardian_loop(read_fd, _AliveParent())

    thread = threading.Thread(target=watch)
    thread.start()
    try:
        time.sleep(0.3)  # several poll intervals: the worker lives, the GUI lives
        assert thread.is_alive(), "the guardian stopped watching a live outer worker"
        os.close(write_fd)  # the outer worker dies: its liveness pipe closes
        thread.join(5.0)
        assert result.get("outcome") == "outer"
    finally:
        if thread.is_alive():
            try:
                os.close(write_fd)
            except OSError:
                pass
            thread.join(5.0)
        os.close(read_fd)


# ----------------------------------------------------------------------
# startup handshake: 100 ms polls, bounded, cancellation-aware
# ----------------------------------------------------------------------
class _QuietToken:
    def is_cancelled(self):
        return False


class _CancelledToken:
    def is_cancelled(self):
        return True


def test_await_startup_returns_the_ready_message():
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    try:
        child.send(("ready", 4242))
        assert job_process._await_startup(parent, _QuietToken()) == ("ready", 4242)
    finally:
        parent.close()
        child.close()


def test_await_startup_aborts_on_cancellation_before_dispatch():
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    try:
        with pytest.raises(WorkerProcessError) as error:
            job_process._await_startup(parent, _CancelledToken())
        assert error.value.remote_type == "CancelledBeforeDispatch"
    finally:
        parent.close()
        child.close()


def test_await_startup_refuses_containment_when_the_worker_dies():
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    child.close()  # EOF: the worker died during startup
    try:
        with pytest.raises(WorkerProcessError) as error:
            job_process._await_startup(parent, _QuietToken())
        assert error.value.remote_type == "ContainmentUnavailable"
    finally:
        parent.close()


def test_await_startup_is_bounded(monkeypatch):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    monkeypatch.setattr(job_process, "_HANDSHAKE_SECONDS", 0.3)
    try:
        started = time.monotonic()
        with pytest.raises(WorkerProcessError, match="did not confirm startup"):
            job_process._await_startup(parent, _QuietToken())
        assert time.monotonic() - started < 5.0
    finally:
        parent.close()
        child.close()


# ----------------------------------------------------------------------
# real contained spawns (Windows job object / Linux process group + guardian)
# ----------------------------------------------------------------------
@requires_containment
def test_contained_worker_runs_inside_a_registered_owned_tree():
    trees = []
    processes = []
    result = _run(
        helpers.add,
        kwargs={"a": 40, "b": 2},
        on_process=processes.append,
        on_tree=lambda process, tree: trees.append((process, tree)),
    )
    assert result["sum"] == 42 and result["pid"] != os.getpid()
    assert len(trees) == 1 and len(processes) == 1
    process, tree = trees[0]
    if sys.platform == "win32":
        assert tree.group is None  # the job object is the mechanism
    else:
        assert tree.group == process.pid  # the worker is its own group leader
    assert not process.is_alive()


@requires_containment
def test_owned_tree_takes_the_pool_children_with_it_on_completion():
    result = _run(helpers.spawn_child_and_return)
    child_pid = result["child_pid"]
    deadline = time.monotonic() + 10.0
    while _pid_alive(child_pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _pid_alive(child_pid), "the pool child outlived the owned tree"


@requires_containment
def test_cancellation_during_startup_never_dispatches(tmp_path):
    token = _cancellation_token()
    token.cancel("cancelled before the worker could start")
    marker = tmp_path / "ran.txt"
    started = time.monotonic()
    with pytest.raises(WorkerProcessError) as error:
        _run(helpers.touch_marker, kwargs={"path": str(marker)}, token=token)
    elapsed = time.monotonic() - started
    assert error.value.remote_type == "CancelledBeforeDispatch"
    assert not marker.exists()
    # One shared deadline, terminate before waiting: the undispatched owned
    # worker is stopped at once, not joined for seconds first.
    assert elapsed < 4.0, f"startup cancellation took {elapsed:.1f} s"


@requires_containment
def test_registration_failure_terminates_the_worker_before_dispatch(tmp_path):
    marker = tmp_path / "ran.txt"

    def refuse_registration(process, tree):
        raise RuntimeError("mesh task manager is closing")

    with pytest.raises(RuntimeError, match="closing"):
        _run(helpers.touch_marker, kwargs={"path": str(marker)}, on_tree=refuse_registration)
    assert not marker.exists()


def test_start_failure_refuses_containment_before_dispatch(monkeypatch):
    class FailingProcess:
        def __init__(self, *args, **kwargs):
            self.pid = None
            self.exitcode = None

        def start(self):
            raise OSError("cannot spawn")

        def is_alive(self):
            return False

        def join(self, timeout=None):
            return

        def terminate(self):
            return

        def close(self):
            return

    real_context = multiprocessing.get_context("spawn")
    pipes = []
    closed_trees = []

    class RecordingTree(job_process.WorkerTree):
        def close(self):
            closed_trees.append(self)
            super().close()

    class ContextProxy:
        def Pipe(self, *args, **kwargs):
            ends = real_context.Pipe(*args, **kwargs)
            pipes.extend(ends)
            return ends

        def Event(self):
            return real_context.Event()

        def Process(self, *args, **kwargs):
            return FailingProcess()

    monkeypatch.setattr(multiprocessing, "get_context", lambda method: ContextProxy())
    monkeypatch.setattr(job_process, "WorkerTree", RecordingTree)
    with pytest.raises(WorkerProcessError) as error:
        _run(helpers.add)
    assert error.value.remote_type == "ContainmentUnavailable"
    # The allocated job object and BOTH ends of every pipe are released on
    # the start-failure path: ownership exists before the start attempt.
    assert len(closed_trees) == 1, "the allocated job object was not released"
    assert pipes, "no pipes were created"
    assert all(connection.closed for connection in pipes), (
        "a pipe end leaked on the start-failure path"
    )


# ----------------------------------------------------------------------
# cleanup confirmation: native outcomes checked, failures recorded
# ----------------------------------------------------------------------
def test_owned_group_requires_live_leadership_proof(monkeypatch):
    class FakeProcess:
        pid = 111

        def __init__(self, alive):
            self.alive = alive

        def is_alive(self):
            return self.alive

    tree = job_process.WorkerTree(FakeProcess(alive=True))
    monkeypatch.setattr(job_process.os, "getpgid", lambda pid: 111, raising=False)
    assert tree._owned_group(None) == 111  # its own new session: owned
    monkeypatch.setattr(job_process.os, "getpgid", lambda pid: 999, raising=False)
    assert tree._owned_group(None) is None  # the spawning group: never signalled
    assert job_process.WorkerTree(FakeProcess(alive=False))._owned_group(None) is None
    # A reported group is signalled only when it is the worker's own.
    assert tree._owned_group(111) == 111
    assert tree._owned_group(999) is None


@pytest.mark.skipif(os.name != "nt", reason="the job object is the Windows containment mechanism")
def test_terminate_records_a_failed_native_stop(monkeypatch):
    class FakeKernel32:
        def TerminateJobObject(self, job, code):
            return 0  # the native stop failed

        def CloseHandle(self, handle):
            return 0  # the native close failed

    class FakeProcess:
        pid = 4321

        def is_alive(self):
            return False

    tree = job_process.WorkerTree(FakeProcess(), job=object())
    monkeypatch.setattr(job_process, "_windows_kernel32", lambda: FakeKernel32())
    tree.terminate()
    assert not tree.terminated, "an unconfirmed native stop was marked as success"
    assert "terminating the owned worker tree failed" in "; ".join(tree.cleanup_failures)
    tree.close()
    assert "closing the owned worker tree failed" in "; ".join(tree.cleanup_failures)


def test_cleanup_worker_reports_an_unretired_process():
    class StuckProcess:
        pid = 1234

        def is_alive(self):
            return True

        def join(self, timeout=None):
            return

        def terminate(self):
            return

    failures = job_process._cleanup_worker(
        StuckProcess(), None, None, None, None, None, True
    )
    assert failures and "did not exit within" in failures[0]


@requires_containment
def test_a_cleanup_failure_discards_the_result(monkeypatch):
    class FailingStopTree(job_process.WorkerTree):
        def terminate(self):
            # Simulate a native stop whose outcome cannot be confirmed; the
            # fallback stop of the worker itself still retires the process,
            # but the unconfirmed tree stop must discard the result.
            with self._lock:
                self._failures.append("terminating the owned worker tree failed: simulated")

    monkeypatch.setattr(job_process, "WorkerTree", FailingStopTree)
    with pytest.raises(WorkerProcessError) as error:
        _run(helpers.add, kwargs={"a": 40, "b": 2})
    assert error.value.remote_type == "WorkerCleanupFailed"
    assert "discarded" in str(error.value)


@requires_containment
def test_a_cleanup_failure_never_masks_the_original_error(monkeypatch):
    class FailingStopTree(job_process.WorkerTree):
        def terminate(self):
            with self._lock:
                self._failures.append("terminating the owned worker tree failed: simulated")

    monkeypatch.setattr(job_process, "WorkerTree", FailingStopTree)
    with pytest.raises(WorkerProcessError) as error:
        _run(helpers.fail)
    assert error.value.remote_type == "ValueError", "the original failure identity was replaced"
    assert any("cleanup did not confirm retirement" in note for note in error.value.__notes__)


# ----------------------------------------------------------------------
# retirement verification: a stop request is not retirement
# ----------------------------------------------------------------------
def test_a_permission_refusal_never_confirms_the_stop(monkeypatch):
    class FakeProcess:
        pid = 111

        def is_alive(self):
            return True

    def refuse(target, sig):
        raise PermissionError(f"killpg refused for pgid {target}")

    monkeypatch.setattr(job_process.os, "name", "posix")
    monkeypatch.setattr(job_process.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(job_process.os, "getpgid", lambda pid: 111, raising=False)
    monkeypatch.setattr(job_process.os, "killpg", refuse, raising=False)
    tree = job_process.WorkerTree(FakeProcess())
    tree.terminate()
    assert not tree.terminated, "a permission refusal was marked as a confirmed stop"
    failures = "; ".join(tree.cleanup_failures)
    assert "terminating the owned worker tree failed" in failures
    assert "killpg refused" in failures
    # An unconfirmed stop can never read as a retired tree.
    assert tree.confirm_retirement(time.monotonic() + 0.1) is not None


def test_confirm_retirement_reports_a_surviving_group_member(monkeypatch):
    class FakeProcess:
        pid = 222

        def is_alive(self):
            return True

    monkeypatch.setattr(job_process.os, "name", "posix")
    monkeypatch.setattr(job_process.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(job_process.os, "getpgid", lambda pid: 222, raising=False)
    monkeypatch.setattr(job_process.os, "killpg", lambda target, sig: None, raising=False)
    tree = job_process.WorkerTree(FakeProcess())
    tree.terminate()
    assert tree.terminated, "the native stop itself succeeded"
    monkeypatch.setattr(job_process, "_linux_group_survivor", lambda group, deadline=None: 3333)
    failure = tree.confirm_retirement(time.monotonic() - 1.0)  # the deadline already passed
    assert failure, "a surviving owned process was reported as retired"
    assert "cleanup deadline" in failure


def test_confirm_retirement_accepts_a_fully_retired_group(monkeypatch):
    class FakeProcess:
        pid = 222

        def is_alive(self):
            return True

    monkeypatch.setattr(job_process.os, "name", "posix")
    monkeypatch.setattr(job_process.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(job_process.os, "getpgid", lambda pid: 222, raising=False)
    monkeypatch.setattr(job_process.os, "killpg", lambda target, sig: None, raising=False)
    tree = job_process.WorkerTree(FakeProcess())
    tree.terminate()
    monkeypatch.setattr(job_process, "_linux_group_survivor", lambda group, deadline=None: None)
    assert tree.confirm_retirement(time.monotonic() + 1.0) is None


def test_confirm_retirement_fails_closed_when_inspection_fails(monkeypatch):
    class FakeKernel32:
        def TerminateJobObject(self, job, code):
            return 1

        def CloseHandle(self, handle):
            return 1

    class FakeProcess:
        pid = 4321

        def is_alive(self):
            return False

    tree = job_process.WorkerTree(FakeProcess(), job=object())
    monkeypatch.setattr(job_process, "_windows_kernel32", lambda: FakeKernel32())
    tree.terminate()
    assert tree.terminated

    def failing_query(job):
        raise OSError(5, "QueryInformationJobObject failed (5)")

    monkeypatch.setattr(job_process, "_windows_job_active_processes", failing_query)
    failure = tree.confirm_retirement(time.monotonic() + 1.0)
    assert failure, "an inability to inspect read as retirement"
    assert "verifying the owned worker tree retirement failed" in failure


@pytest.mark.skipif(os.name != "nt", reason="the job object is the Windows containment mechanism")
def test_windows_job_accounting_confirms_real_retirement():
    import subprocess

    job = job_process._create_kill_on_close_job()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        job_process._assign_process_to_job(job, child.pid)
        assert job_process._windows_job_active_processes(job) == 1
        kernel32 = job_process._windows_kernel32()
        assert kernel32.TerminateJobObject(job, 1)
        child.wait(timeout=10)
        deadline = time.monotonic() + 5.0
        while job_process._windows_job_active_processes(job) > 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert job_process._windows_job_active_processes(job) == 0, (
            "the job's active-process accounting never reached zero"
        )
    finally:
        kernel32 = job_process._windows_kernel32()
        kernel32.CloseHandle(job)
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


@pytest.mark.skipif(
    os.name != "nt",
    reason="the job-object assignment is the Windows predispatch refusal",
)
def test_an_assignment_failure_stops_the_owned_worker_promptly(monkeypatch, tmp_path):
    def failing_assign(job, pid):
        raise OSError(5, "AssignProcessToJobObject failed (5)")

    marker = tmp_path / "ran.txt"
    monkeypatch.setattr(job_process, "_assign_process_to_job", failing_assign)
    started = time.monotonic()
    with pytest.raises(WorkerProcessError) as error:
        _run(helpers.touch_marker, kwargs={"path": str(marker)})
    elapsed = time.monotonic() - started
    assert error.value.remote_type == "ContainmentUnavailable"
    assert not getattr(error.value, "cleanup_failures", None), (
        "a predispatch refusal must not inherently fail its own cleanup"
    )
    assert elapsed < 4.0, f"the undispatched worker was not stopped promptly ({elapsed:.1f} s)"
    assert not marker.exists()


@pytest.mark.skipif(
    sys.platform in ("win32", "linux"),
    reason="only a non-Linux POSIX system reaches the safe-decline branch",
)
def test_unsupported_posix_containment_safely_declines():
    with pytest.raises(WorkerProcessError) as error:
        _run(helpers.add)
    assert error.value.remote_type == "ContainmentUnavailable"


# ----------------------------------------------------------------------
# parent death: killing ONLY the GUI kills the whole owned tree
# ----------------------------------------------------------------------
@requires_containment
def test_parent_death_kills_the_owned_tree(tmp_path):
    helper = Path(__file__).resolve().parent / "parallel_parent_death_helper.py"
    report = tmp_path / "report.txt"
    stdout = (tmp_path / "helper-stdout.txt").open("wb")
    stderr = (tmp_path / "helper-stderr.txt").open("wb")
    helper_process = None
    try:
        import subprocess

        helper_process = subprocess.Popen(
            [sys.executable, str(helper), str(report)],
            stdout=stdout,
            stderr=stderr,
        )
        deadline = time.monotonic() + 30.0
        while not report.exists() and helper_process.poll() is None:
            assert time.monotonic() < deadline, "the helper never reported its owned tree"
            time.sleep(0.05)
        assert report.exists(), "the helper never reported its owned tree"
        outer_pid, child_pid = (
            int(line.split("=")[1]) for line in report.read_text(encoding="utf-8").splitlines()
        )
        assert _pid_alive(outer_pid) and _pid_alive(child_pid)

        # Kill ONLY the GUI stand-in: no tree-wide taskkill, no group signal.
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.restype = ctypes.c_void_p
            handle = kernel32.OpenProcess(0x0001, False, int(helper_process.pid))  # PROCESS_TERMINATE
            assert handle, "could not open the helper process"
            try:
                assert kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
        else:
            os.kill(helper_process.pid, signal.SIGKILL)
        helper_process.wait(timeout=10)

        deadline = time.monotonic() + 15.0
        while (_pid_alive(outer_pid) or _pid_alive(child_pid)) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _pid_alive(outer_pid), "the contained worker outlived the GUI"
        assert not _pid_alive(child_pid), "the pool child outlived the GUI"
    finally:
        if helper_process is not None and helper_process.poll() is None:
            helper_process.kill()
        if helper_process is not None:
            helper_process.wait(timeout=10)
        stdout.close()
        stderr.close()
