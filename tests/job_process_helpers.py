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
