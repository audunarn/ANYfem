"""Portable extract of the reviewed supervisor-helper control mechanics.

Provenance: the two functions below are the exact source segments of
``_suspended_thread_id`` and ``_wait_job_empty`` from the reviewed
supervisor helpers at ``reports/prepared-mesh-preassembly/
current-2cbb161/control-source/reviewed_supervisor_helpers.py``
(original sha256 73f1940eacb0794e21f7ba806112d34647ea6e33a3393ae69ba9093054d80000). Only those two functions and the
standard library import they require are carried here. The original
helpers module is never imported or executed from this file: its
``main`` entry, its module-level gates and its runtime acceptance are
not inherited and cannot run from this extract.
"""
import time


def _suspended_thread_id(pid):
    """Read the sole initial thread of a CREATE_SUSPENDED child."""
    import psutil

    threads = psutil.Process(pid).threads()
    if len(threads) != 1:
        raise RuntimeError(f"suspended child has {len(threads)} threads, expected one")
    return int(threads[0].id)



def _wait_job_empty(job, seconds=2.0):
    deadline = time.monotonic() + seconds
    while True:
        snapshot = job.snapshot()
        if snapshot["active_processes"] == 0 or time.monotonic() >= deadline:
            return snapshot
        time.sleep(.05)
