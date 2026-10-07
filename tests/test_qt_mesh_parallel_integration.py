"""Real Qt caller, contained parallel job, cancellation and measured UI ticks."""
import json
import os
import time
from pathlib import Path

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1" or os.environ.get("ANYFEM_RUN_PARALLEL_MESH_TESTS") != "1":
    pytest.skip("requires explicit Qt and real parallel mesh acceptance", allow_module_level=True)

from PySide6.QtCore import QTimer
from anyfem.io.project_file import save_project
from anyfem.mesh_execution import MeshExecutionPolicy
from parallel_mesh_contract import separated_plates
from test_qt_geometry_actions import qapp, window  # noqa: F401
from test_job_process_containment import _pid_alive


def owned_descendants(tree):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        from anyfem.job_process import _windows_kernel32

        class JobPids(ctypes.Structure):
            _fields_ = [("assigned", wintypes.DWORD), ("count", wintypes.DWORD),
                        ("pids", ctypes.c_size_t * 128)]

        info = JobPids()
        assert _windows_kernel32().QueryInformationJobObject(
            tree._job, 3, ctypes.byref(info), ctypes.sizeof(info), None)
        return list(info.pids[:info.count])
    parents = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_bytes().rsplit(b") ", 1)[1].split()
            if fields[0] != b"Z":
                parents[int(path.parent.name)] = int(fields[1])
        except FileNotFoundError:
            continue
    owned = {tree._process.pid}
    while True:
        children = {pid for pid, parent in parents.items() if parent in owned}
        if children <= owned:
            return list(owned)
        owned.update(children)


def wait_until(qapp, predicate, timeout=30):
    end = time.monotonic() + timeout
    while not predicate():
        qapp.processEvents()
        time.sleep(0.002)
        assert time.monotonic() < end, "Qt job timed out"


def prepare(window, tmp_path, count=16):
    path = save_project(separated_plates(count), tmp_path / "parallel.anyfem")
    window.open_project(str(path))


def test_real_qt_parallel_job_attaches_mesh_and_records_route(window, qapp, tmp_path):
    prepare(window, tmp_path, 4)
    record = window.generate_mesh_async(0.4, strategy="native", execution_policy=MeshExecutionPolicy.explicit(4))
    wait_until(qapp, lambda: record.status in ("completed", "failed", "cancelled"))
    assert record.status == "completed", record.summary
    assert record.summary["execution"]["route"] == "parallel"
    assert window.mesh is not None and window.mesh_record_id == record.id
    assert record.mesh_hash


def test_real_qt_parallel_cancel_does_not_attach_partial_mesh(window, qapp, tmp_path, monkeypatch):
    prepare(window, tmp_path)
    stages = []
    observe = window._observe_run_event

    def track(kind, event):
        stages.append(event.message)
        observe(kind, event)

    monkeypatch.setattr(window, "_observe_run_event", track)
    record = window.generate_mesh_async(0.04, strategy="native", execution_policy=MeshExecutionPolicy.explicit(4))
    wait_until(qapp, lambda: any(text.startswith("parallel component ") for text in stages))
    pids = owned_descendants(window.mesh_task_manager._tree)
    assert len(pids) >= 5, "the parallel pool was not present before cancellation"
    started = time.perf_counter()
    assert window.cancel_mesh()
    wait_until(qapp, lambda: record.status in ("completed", "failed", "cancelled"))
    latency = time.perf_counter() - started
    assert record.status == "cancelled", record.summary
    assert window.mesh is None
    assert latency < 1.0, f"parallel cancellation took {latency:.3f}s"
    assert not [pid for pid in pids if _pid_alive(pid)], "owned mesh workers survived cancellation"


def test_real_qt_worker_crash_fails_without_retry(window, qapp, tmp_path, monkeypatch):
    prepare(window, tmp_path)
    stages = []
    observe = window._observe_run_event

    def track(kind, event):
        stages.append(event.message)
        observe(kind, event)

    monkeypatch.setattr(window, "_observe_run_event", track)
    record = window.generate_mesh_async(0.04, strategy="native", execution_policy=MeshExecutionPolicy.explicit(4))
    wait_until(qapp, lambda: any(text.startswith("parallel component ") for text in stages))
    tree = window.mesh_task_manager._tree
    pids = owned_descendants(tree)
    assert len(pids) >= 5
    if os.name == "nt":
        import ctypes
        worker_pid = next(pid for pid in pids if pid != tree._process.pid)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(1, False, worker_pid)  # PROCESS_TERMINATE
        assert handle
        try:
            assert kernel.TerminateProcess(handle, 1)
        finally:
            kernel.CloseHandle(handle)
    else:
        import signal
        # The frozen mesher's workers have a parent-watch thread; its separate
        # guardian has one thread. Avoid fault-injecting into that guardian.
        worker_pid = next(pid for pid in pids if pid != tree._process.pid
                          and os.getpgid(pid) == pid
                          and len(list(Path(f"/proc/{pid}/task").iterdir())) > 1)
        os.kill(worker_pid, signal.SIGKILL)
    wait_until(qapp, lambda: record.status in ("completed", "failed", "cancelled"))
    assert record.status == "failed", record.summary
    assert window.mesh is None
    assert not any("serial meshing fallback" in text for text in stages)
    assert not [pid for pid in pids if _pid_alive(pid)]


def test_qt_mesh_tick_measurement(window, qapp, tmp_path):
    output = os.environ.get("ANYFEM_QT_TICK_EVIDENCE")
    if not output:
        pytest.skip("set ANYFEM_QT_TICK_EVIDENCE for measured responsiveness")
    prepare(window, tmp_path)
    rows = []
    for label, policy in (("serial", MeshExecutionPolicy.off()),
                          ("parallel", MeshExecutionPolicy.explicit(4))):
        # Match initial document and viewport state. Keeping the previous mesh
        # only for the second run confounds pool responsiveness with rendering.
        window.session.mark_saved()
        prepare(window, tmp_path)
        ticks = []
        previous = [time.perf_counter()]
        timer = QTimer()
        timer.setInterval(10)

        def tick():
            now = time.perf_counter()
            ticks.append(now - previous[0])
            previous[0] = now

        timer.timeout.connect(tick)
        timer.start()
        started = time.perf_counter()
        try:
            record = window.generate_mesh_async(0.08, strategy="native", execution_policy=policy)
            wait_until(qapp, lambda: record.status in ("completed", "failed", "cancelled"), timeout=90)
        finally:
            timer.stop()
        assert record.status == "completed", record.summary
        assert len(ticks) >= 20
        ordered = sorted(ticks)
        rows.append({"route": label, "wall_seconds": time.perf_counter() - started,
                     "tick_count": len(ticks), "p99_seconds": ordered[int(0.99 * (len(ordered) - 1))],
                     "max_seconds": max(ticks), "ticks": ticks,
                     "execution": record.summary["execution"]})
        Path(output).write_text(json.dumps(rows, indent=2), encoding="utf-8")
    assert rows[1]["p99_seconds"] <= rows[0]["p99_seconds"], [
        {key: row[key] for key in ("route", "p99_seconds", "max_seconds")}
        for row in rows]
