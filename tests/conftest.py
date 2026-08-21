"""Shared test configuration.

Tk objects must be finalized on the main thread.  A destroyed window leaves
``tkinter.Variable`` instances alive until the garbage collector gets to them,
and if that happens to be during an allocation inside the solver's worker
thread, ``Variable.__del__`` calls into Tcl from the wrong thread:

    RuntimeError: main thread is not in main loop

which leaves the Tcl interpreter wedged, so subsequent solves never report
back.  Collecting on the main thread after every test keeps those finalizers
where they belong.  Do not remove this.
"""

from __future__ import annotations

import gc
import inspect
import os
from pathlib import Path
from uuid import uuid4

import pytest


_RUN_GUI_TESTS = os.environ.get("ANYFEM_RUN_GUI_TESTS", "").casefold() in {
    "1",
    "true",
    "yes",
}


def pytest_configure(config):
    if getattr(config.option, "basetemp", None) is None:
        config.option.basetemp = str(
            Path(__file__).resolve().parents[1]
            / f".pytest_tmp_{uuid4().hex}"
        )
    config.addinivalue_line(
        "markers",
        "gui: opt-in test that creates a real Tk desktop window",
    )


def pytest_collection_modifyitems(items):
    """Keep real Tk sessions out of ordinary regression runs.

    A number of older tests use shared ``root`` fixtures; a few construct a
    root inside the test body.  Detect both without skipping headless tests in
    the same module.
    """

    if _RUN_GUI_TESTS:
        return
    skip = pytest.mark.skip(
        reason="real Tk GUI test is opt-in; set ANYFEM_RUN_GUI_TESTS=1"
    )
    for item in items:
        try:
            source = inspect.getsource(item.obj)
        except (OSError, TypeError):
            source = ""
        real_tk = any(name.endswith("root") for name in item.fixturenames) or any(
            token in source for token in ("tk.Tk(", "tkinter.Tk(")
        )
        if real_tk:
            item.add_marker("gui")
            item.add_marker(skip)


# A developer test run must leave the workstation usable.  Numerical kernels
# and BLAS libraries otherwise each assume they own every logical CPU, and a
# mixed UI/solver suite can oversubscribe the machine badly enough to freeze
# mouse/desktop updates.  Explicit performance qualification opts into its own
# resource policy; ordinary functional tests use one numerical worker.
_TEST_THREADS = os.environ.get("ANYFEM_TEST_THREADS", "1")
for _name in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
    "NUMBA_NUM_THREADS",
):
    # Deliberately override inherited machine-wide settings.  A workstation
    # configured with (for example) OPENBLAS_NUM_THREADS=32 must still get the
    # safe test default.  Developers can opt into more test workers through
    # the single, explicit ANYFEM_TEST_THREADS switch.
    os.environ[_name] = _TEST_THREADS


@pytest.fixture(scope="session", autouse=True)
def _bound_native_thread_pools():
    """Bound already-loaded BLAS runtimes as well as future imports."""

    try:
        from threadpoolctl import threadpool_limits
    except ImportError:
        yield
        return
    with threadpool_limits(limits=max(1, int(_TEST_THREADS))):
        yield


@pytest.fixture(autouse=True)
def _collect_tk_objects_on_the_main_thread():
    yield
    gc.collect()
