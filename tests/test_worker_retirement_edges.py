"""Deadline and OS metadata edges found during independent integration review."""
import ctypes
import time
from types import SimpleNamespace

import pytest
import anyfem.job_process as worker


def test_late_empty_job_query_cannot_confirm_retirement(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock[0])

    def late_query(job):
        clock[0] = 6.0
        return 0

    monkeypatch.setattr(worker, "_windows_job_active_processes", late_query)
    tree = worker.WorkerTree(SimpleNamespace(pid=42), job=object())
    tree._terminated = True
    assert "deadline" in tree.confirm_retirement(5.0)


def test_late_empty_group_scan_cannot_confirm_retirement(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock[0])

    def late_scan(group, deadline):
        clock[0] = 6.0
        return None

    monkeypatch.setattr(worker, "_linux_group_survivor", late_scan)
    tree = worker.WorkerTree(SimpleNamespace(pid=42), group=42)
    tree._proven_group = 42
    tree._terminated = True
    assert "deadline" in tree.confirm_retirement(5.0)


@pytest.mark.skipif(worker.sys.platform != "linux", reason="Linux proc metadata")
def test_group_scan_tolerates_a_non_utf8_process_name():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    old_name = ctypes.create_string_buffer(16)
    assert libc.prctl(16, old_name, 0, 0, 0) == 0  # PR_GET_NAME
    try:
        assert libc.prctl(15, ctypes.create_string_buffer(b"metadata-\xff"), 0, 0, 0) == 0
        assert worker._linux_group_survivor(-1, time.monotonic() + 2) is None
    finally:
        assert libc.prctl(15, old_name, 0, 0, 0) == 0
