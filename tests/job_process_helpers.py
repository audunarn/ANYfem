"""Module-level job functions for the worker-process tests.

A worker process imports its job function by name, so these cannot be closures
defined inside a test.  Deliberately no pytest import: the worker loads this module.
"""

from __future__ import annotations

import os
import time
import types


def add(*, project, progress, cancellation_token=None, a=1, b=2):
    progress("hello from the worker")
    return {"sum": a + b, "pid": os.getpid()}


def structured(*, project, progress, cancellation_token=None):
    from anyfem.solve.run import _StructuredProgressText

    progress(_StructuredProgressText("linear_static.complete", {"stage": "linear_static.complete", "completed": 1, "total": 3}))
    return "done"


def fail(*, project, progress, cancellation_token=None):
    raise ValueError("boom in the worker")


def crash(*, project, progress, cancellation_token=None):
    progress("about to die")
    os._exit(7)  # what a native fault does: no exception, no cleanup


def wait_for_cancel(*, project, progress, cancellation_token=None):
    progress("ready")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        cancellation_token.raise_if_cancelled("wait_for_cancel")
        time.sleep(0.01)
    return "never cancelled"


def read_only_views(*, project, progress, cancellation_token=None):
    import numpy as np

    return {"view": types.MappingProxyType({"a": 1, "nested": types.MappingProxyType({"b": 2})}), "array": np.arange(5.0)}


def thread_local_result(*, project, progress, cancellation_token=None):
    """Like the solver's cached contact geometry: a per-thread work buffer inside a result."""
    import threading

    local = threading.local()
    local.buffer = [1, 2, 3]
    return {"cache": local, "value": 42}


def unpicklable_result(*, project, progress, cancellation_token=None):
    return lambda: None


def priority(*, project, progress, cancellation_token=None):
    import sys

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetPriorityClass.argtypes = (wintypes.HANDLE,)
        kernel32.GetPriorityClass.restype = wintypes.DWORD
        return {"below_normal": kernel32.GetPriorityClass(kernel32.GetCurrentProcess()) == 0x4000}
    return {"below_normal": os.nice(0) > 0}


def busy(*, project, progress, cancellation_token=None, seconds=2.0):
    """Pure-Python CPU work, like the solver's validation and element code."""
    progress("busy")
    end = time.monotonic() + seconds
    total = 0
    while time.monotonic() < end:
        total += sum(i * i for i in range(2000))
    return total


def sleep_long(*, project, progress, cancellation_token=None):
    progress("sleeping")
    time.sleep(60)


def spawn_child_and_return(*, project, progress, cancellation_token=None):
    """Like the ANYmesh pool: create a child process, then keep working.

    The child outlives the job function on purpose: only the owned containment
    tree may take it with the worker.
    """
    import subprocess
    import sys

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    progress(f"child {child.pid}")
    return {"child_pid": child.pid}


def touch_marker(*, project, progress, cancellation_token=None, path=""):
    """Write a marker file so the parent can prove this job really ran."""
    import pathlib

    pathlib.Path(str(path)).write_text("ran", encoding="utf-8")
    return "ran"


#: Test-only environment variable naming the gate directory shared by the
#: pytest process, the contained mesh worker and its pool workers.
MESH_GATE_ENV = "_ANYFEM_TEST_MESH_GATE_DIR"

#: Bounded gate wait: an unreleased, uncancelled gate fails the job instead
#: of hanging the pool or the test.
MESH_GATE_TIMEOUT_SECONDS = 15.0


def gated_component(payload, *, cancellation_check=None):
    """Test-only pool task: acknowledge receipt, wait for release, crash.

    Replaces ANYmesher's ``_mesh_component`` for the duration of one gated
    mesh job.  The per-PID ready marker is written the moment a submitted
    task really starts executing in a pool worker; that acknowledged receipt
    is the in-flight barrier the Qt fault tests synchronize on, because
    while it is pending the job cannot complete.  The wait honours the pool's
    cooperative cancellation signal and is bounded.  On release the worker
    dies hard, like the pinned test-only crash hook: no exception, no
    cleanup.
    """
    import pathlib

    gate = os.environ.get(MESH_GATE_ENV)
    if not gate:
        raise RuntimeError(f"gated mesh component requires {MESH_GATE_ENV}")
    gate_dir = pathlib.Path(gate)
    (gate_dir / f"ready-{os.getpid()}").write_text("ready", encoding="utf-8")
    release = gate_dir / "release"
    deadline = time.monotonic() + MESH_GATE_TIMEOUT_SECONDS
    while not release.exists():
        if cancellation_check is not None:
            cancellation_check("gated mesh component")
        if time.monotonic() >= deadline:
            raise TimeoutError("gated mesh component was never released")
        time.sleep(0.005)
    os._exit(3)  # test-only injected pool-worker crash


def gated_mesh_job_entry(*, project, progress, cancellation_token, settings, model_hash):
    """``mesh_job_entry`` with the pool's component task gated for fault tests.

    Temporarily replaces ``anymesher.component_parallel._mesh_component`` with
    :func:`gated_component`, then calls the production entry point with the
    unchanged production inputs and restores the component afterwards.  The
    only intercepted thing is the per-task function the pool submits; every
    other production path (planning, dispatch, join, cancellation, teardown)
    runs for real.
    """
    import anyfem.mesh_jobs as mesh_jobs
    import anymesher.component_parallel as component_parallel

    original = component_parallel._mesh_component
    component_parallel._mesh_component = gated_component
    try:
        return mesh_jobs.mesh_job_entry(
            project=project,
            progress=progress,
            cancellation_token=cancellation_token,
            settings=settings,
            model_hash=model_hash,
        )
    finally:
        component_parallel._mesh_component = original


def _gated_error_diagnostic(error):
    """Keep the production failure diagnostic; the gate never masks it."""
    import anyfem.mesh_jobs as mesh_jobs

    return mesh_jobs.mesh_job_entry.error_diagnostic(error)


gated_mesh_job_entry.error_diagnostic = _gated_error_diagnostic
