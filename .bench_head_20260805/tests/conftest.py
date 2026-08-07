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

import pytest


@pytest.fixture(autouse=True)
def _collect_tk_objects_on_the_main_thread():
    yield
    gc.collect()
