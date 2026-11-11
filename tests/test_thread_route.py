"""Fault-injection tests of the opt-in thread route (child-process prepare).  Normal Python, no heavy planning.

Every failure ends in the right outcome; every ``route_unavailable`` ends in a serial result identical to route-off; the
setting is OFF by default; a golden digest fails if the route-off path changes.  Tests that need the free-threaded runtime
use ``ANYGEOMETRY_THREAD_RUNTIME`` or the programme's ``venv-314t`` and are skipped (not weakened) when it is absent.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

psutil = pytest.importorskip("psutil")

from anygeometry import GeometryModel, to_dict  # noqa: E402
from anygeometry.errors import GeometryError  # noqa: E402

from anyfem import _batch_prepare, thread_route as tr  # noqa: E402
from anyfem.structural_preparation import StructuralPreparationError, prepare_structural_connectivity  # noqa: E402

RUNTIME_ROOT = Path(os.environ.get(
    "ANYGEOMETRY_THREAD_RUNTIME", r"C:\ANYworkspaceWork\anygeometry-r11-20261103\venv-314t"))
PYTHON = tr._runtime_python(RUNTIME_ROOT)
needs_runtime = pytest.mark.skipif(PYTHON is None, reason="no free-threaded runtime at %s" % RUNTIME_ROOT)
MODEL_ID = "12345678-1234-5678-1234-567812345678"


class Cancel(Exception):
    pass


def crossing() -> GeometryModel:
    model = GeometryModel(model_id=MODEL_ID)
    model.add_plate(model.add_points(((0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0))))
    model.add_plate(model.add_points(((2, 0, -1), (2, 4, -1), (2, 4, 1), (2, 0, 1))))
    return model


def overlapping() -> GeometryModel:
    model = GeometryModel(model_id=MODEL_ID)
    model.add_plate(model.add_points(((0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0))))
    model.add_plate(model.add_points(((2, 2, 0), (6, 2, 0), (6, 6, 0), (2, 6, 0))))
    return model


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def result_of(model, report) -> str:
    return digest([to_dict(model), report.to_dict()])


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    for name in (tr.ENV_ENABLE, tr.ENV_RUNTIME, tr.ENV_THREADS, tr.ENV_MIN_FACES, tr.ENV_APP_DIR):
        monkeypatch.delenv(name, raising=False)
    tr.configure(reset=True)
    monkeypatch.setattr(tr, "_INJECT", {})
    monkeypatch.setattr(tr, "last_outcome", None)
    yield
    tr.configure(reset=True)


@pytest.fixture
def route_on():
    tr.configure(enabled=True, min_faces=0, runtime=str(RUNTIME_ROOT))


@pytest.fixture
def jobs_dir(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    return root


def reference(builder):
    """Route-off result (default setting) for the same model."""
    tr.configure(enabled=False)
    model = builder()
    report = prepare_structural_connectivity(model)
    tr.configure(enabled=True)
    return result_of(model, report)


def run_on(builder, **injection):
    model = builder()
    before = digest(to_dict(model))
    tr._INJECT.update(injection)
    report = prepare_structural_connectivity(model)
    return model, report, before


def assert_serial_fallback(builder, jobs_dir, reason_part, **injection):
    expected = reference(builder)
    model, report, _ = run_on(builder, temp_root=str(jobs_dir), **injection)
    assert tr.last_outcome.status == tr.UNAVAILABLE
    assert reason_part in tr.last_outcome.reason, tr.last_outcome.reason
    assert result_of(model, report) == expected  # serial result identical to route-off
    assert list(jobs_dir.iterdir()) == []  # no temp folder left


STUB_PRELUDE = textwrap.dedent('''
    import hashlib, json, os, sys, time
    job_dir = sys.argv[1]
    job = json.load(open(os.path.join(job_dir, "job.json")))
    def write(status, body, job_id=None, sha=None, text=None):
        body_bytes = json.dumps(body, sort_keys=True, separators=(",", ":"), default=repr).encode()
        env = dict(format=1, job_id=job_id or job["job_id"], status=status,
                   body_sha256=sha or hashlib.sha256(body_bytes).hexdigest(), body=body)
        path = os.path.join(job_dir, "result.json")
        open(path, "w").write(text if text is not None else json.dumps(env))
    pidfile = os.environ.get("STUB_PIDFILE")
    if pidfile:
        open(pidfile, "w").write(str(os.getpid()))
''')


def stub(tmp_path, body: str) -> Path:
    path = tmp_path / "stub_child.py"
    path.write_text(STUB_PRELUDE + textwrap.dedent(body))
    return path


# ------------------------------------------------------------------------------------------ setting / eligibility

def test_default_is_off_and_does_not_touch_the_runtime(monkeypatch):
    assert tr.route_enabled() is False
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: pytest.fail("child spawned with the route off"))
    monkeypatch.setattr(tr, "locate_runtime", lambda: pytest.fail("runtime located with the route off"))
    model = crossing()
    report = prepare_structural_connectivity(model)
    assert report.connections and tr.last_outcome is None


def test_env_switch_enables_only_explicit_values(monkeypatch):
    for value, expected in (("1", True), ("on", True), ("0", False), ("", False), ("off", False), ("2x", False)):
        monkeypatch.setenv(tr.ENV_ENABLE, value)
        assert tr.route_enabled() is expected, value


def test_below_face_threshold_stays_in_process(monkeypatch):
    tr.configure(enabled=True)  # default threshold 40 faces; the crossing model has 2
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: pytest.fail("small model must stay in-process"))
    prepare_structural_connectivity(crossing())
    assert tr.last_outcome is None


def test_active_evidence_collector_keeps_the_route_off(monkeypatch, route_on):
    from anyfem import _preparation_context as context
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: pytest.fail("route used while a plan collector is active"))
    with context._collect_preparation_evidence() as rows:
        prepare_structural_connectivity(crossing())
    assert rows and rows[0]["plan"] is not None


def test_thread_count_rule(monkeypatch):
    for cpus, expected in ((32, 8), (64, 8), (16, 4), (8, 2), (4, 1), (2, 1), (None, 1)):
        monkeypatch.setattr(os, "cpu_count", lambda cpus=cpus: cpus)
        assert tr.thread_count() == expected, cpus
    tr.configure(threads=99)
    assert tr.thread_count() == 8


def test_runtime_locator_order_and_never_path(monkeypatch, tmp_path):
    app = tmp_path / "app"
    runtime = app / "runtime-314t"
    runtime.mkdir(parents=True)
    fake = runtime / "python.exe"
    fake.write_text("x")
    path_dir = tmp_path / "onpath"
    path_dir.mkdir()
    (path_dir / "python.exe").write_text("x")
    monkeypatch.setenv("PATH", str(path_dir))
    assert tr.locate_runtime() is None or tr.locate_runtime().parent.name != "onpath"
    monkeypatch.setenv(tr.ENV_APP_DIR, str(app))
    assert tr.locate_runtime() == fake
    override = tmp_path / "override"
    (override / "Scripts").mkdir(parents=True)
    (override / "Scripts" / "python.exe").write_text("x")
    monkeypatch.setenv(tr.ENV_RUNTIME, str(override))
    assert tr.locate_runtime() == override / "Scripts" / "python.exe"
    monkeypatch.setenv(tr.ENV_RUNTIME, str(tmp_path / "missing"))
    assert tr.locate_runtime() is None  # an override that does not exist wins and is NOT replaced by PATH or runtime-314t


# ------------------------------------------------------------------------------------------ happy path

@needs_runtime
def test_child_route_equals_route_off_and_reports_progress(route_on, jobs_dir):
    expected = reference(crossing)
    events = []
    model, report, _ = run_on(crossing, temp_root=str(jobs_dir), progress_cb=events.append)
    assert tr.last_outcome.status == tr.OK and tr.last_outcome.temp_removed
    assert result_of(model, report) == expected
    assert {"start", "imports_done", "document_loaded", "planned_and_applied"} <= {e["event"] for e in events}
    assert list(jobs_dir.iterdir()) == []


@needs_runtime
def test_typed_refusal_is_reported_as_serial_would_and_not_retried(route_on, jobs_dir, monkeypatch):
    tr.configure(enabled=False)
    with pytest.raises(StructuralPreparationError) as serial:
        prepare_structural_connectivity(overlapping())
    tr.configure(enabled=True)
    runs = []
    original = tr.run_child
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: runs.append(a[1]["kind"]) or original(*a, **k))
    tr._INJECT.update(temp_root=str(jobs_dir))
    with pytest.raises(StructuralPreparationError) as routed:
        prepare_structural_connectivity(overlapping())
    assert str(routed.value) == str(serial.value)
    assert tr.last_outcome.status == tr.REFUSAL
    assert runs.count("batch_prepare") == 1  # one child, no retry, no serial repeat
    assert list(jobs_dir.iterdir()) == []


@needs_runtime
def test_phase_two_refusal_propagates_unwrapped(route_on, tmp_path, jobs_dir):
    child = stub(tmp_path, '''
        write("refusal", dict(phase=2, type="GeometryError", message="rows refused", health={}))
    ''')
    model = crossing()
    tr._INJECT.update(temp_root=str(jobs_dir), child_main=child)
    with pytest.raises(tr.PhaseTwoRefusal, match="rows refused"):
        prepare_structural_connectivity(model)
    assert digest(to_dict(model)) == digest(to_dict(crossing()))  # geometry untouched


# ------------------------------------------------------------------------------------------ route_unavailable -> serial

def test_missing_runtime_falls_back(route_on, jobs_dir, tmp_path):
    tr.configure(runtime=str(tmp_path / "no-such-runtime"))
    expected = reference(crossing)
    model, report, _ = run_on(crossing)
    assert tr.last_outcome.status == tr.UNAVAILABLE and "runtime missing" in tr.last_outcome.reason
    assert result_of(model, report) == expected


@needs_runtime
def test_wrong_numpy_falls_back(route_on, jobs_dir, monkeypatch):
    monkeypatch.setattr(tr, "EXPECT_NUMPY", "0.0.1")
    assert_serial_fallback(crossing, jobs_dir, "NumPy")


@needs_runtime
def test_gil_enabled_falls_back(route_on, jobs_dir):
    assert_serial_fallback(crossing, jobs_dir, "GIL enabled", python_args_health=("-X", "gil=1"))


@needs_runtime
def test_bad_source_digest_falls_back(route_on, jobs_dir, monkeypatch):
    monkeypatch.setattr(tr, "_parent_source_digest", lambda: "0" * 64)
    assert_serial_fallback(crossing, jobs_dir, "source digest")


@needs_runtime
def test_bad_frozen_smoke_falls_back(route_on, jobs_dir, monkeypatch):
    monkeypatch.setattr(_batch_prepare, "SMOKE_DIGEST", "f" * 64)
    assert_serial_fallback(crossing, jobs_dir, "smoke digest")


@needs_runtime
@pytest.mark.parametrize("content, part", [
    ("not json", "malformed"),
    (json.dumps({"numpy": "0.0.1"}), "pins NumPy"),
    (json.dumps({"source_digest": "0" * 64}), "source digest"),
])
def test_bad_runtime_lock_falls_back(route_on, jobs_dir, tmp_path, content, part):
    lock = tmp_path / "runtime.lock.json"
    lock.write_text(content)
    assert_serial_fallback(crossing, jobs_dir, part, lock_path=lock)


@needs_runtime
def test_child_crash_falls_back(route_on, jobs_dir, tmp_path):
    child = stub(tmp_path, "sys.exit(3)\n")
    assert_serial_fallback(crossing, jobs_dir, "exited with code 3", child_main=child)


@needs_runtime
def test_child_timeout_falls_back_and_kills_the_child(route_on, jobs_dir, tmp_path):
    pidfile = tmp_path / "pid.txt"
    child = stub(tmp_path, "time.sleep(60)\n")
    assert_serial_fallback(crossing, jobs_dir, "timeout", child_main=child, timeout=1.5,
                           extra_env={"STUB_PIDFILE": str(pidfile)})
    assert not psutil.pid_exists(int(pidfile.read_text()))


@needs_runtime
@pytest.mark.parametrize("name, body, part", [
    ("garbage", 'write("ok", {}, text="{{{ not json")', "malformed"),
    ("stale-job-id", 'write("ok", {}, job_id="deadbeef")', "does not belong"),
    ("bad-checksum", 'write("ok", {"x": 1}, sha="0" * 64)', "checksum mismatch"),
    ("truncated", 'write("ok", {}, text=\'{"format":1,"job_id":"\')', "malformed"),
    ("no-result-exit-0", "pass", "no result"),
    ("missing-prepared", 'write("ok", {})', "invalid child result"),
    ("bad-rows", 'write("ok", dict(prepared={}, prepared_checksum="x", rows={"pairs": 1}))', "invalid child result"),
    ("unavailable-status", 'write("unavailable", dict(reason="child said no"))', "child said no"),
])
def test_malformed_or_partial_results_fall_back(route_on, jobs_dir, tmp_path, name, body, part):
    child = stub(tmp_path, body + "\n")
    assert_serial_fallback(crossing, jobs_dir, part, child_main=child)


@needs_runtime
def test_prepared_document_of_another_model_is_refused(route_on, jobs_dir, tmp_path):
    other = GeometryModel()  # different model id, valid document
    other.add_plate(other.add_points(((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))))
    document = to_dict(other)
    body = dict(prepared=document, prepared_checksum=document["checksum"]["value"],
                rows=dict(pairs=[], pair_relations=[], relations=[], reused=False))
    child = stub(tmp_path, "write('ok', json.loads(%r))\n" % json.dumps(body))
    assert_serial_fallback(crossing, jobs_dir, "does not match", child_main=child)


@needs_runtime
def test_claimed_checksum_mismatch_is_refused(route_on, jobs_dir, tmp_path):
    model = crossing()
    document = to_dict(model)
    body = dict(prepared=document, prepared_checksum="0" * 64,
                rows=dict(pairs=[], pair_relations=[], relations=[], reused=False))
    child = stub(tmp_path, "write('ok', json.loads(%r))\n" % json.dumps(body))
    assert_serial_fallback(crossing, jobs_dir, "does not match", child_main=child)


def test_disk_full_on_the_temp_folder_falls_back(route_on, tmp_path):
    expected = reference(crossing)
    model, report, _ = run_on(crossing, temp_root=str(tmp_path / "does" / "not" / "exist"))
    assert tr.last_outcome.status == tr.UNAVAILABLE and "cannot start child" in tr.last_outcome.reason
    assert result_of(model, report) == expected


@needs_runtime
def test_disk_full_while_writing_the_document_falls_back_and_cleans_up(route_on, jobs_dir, monkeypatch):
    import errno
    real = tr._atomic_write

    def full(path, data):
        if path.name == "document.json":
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(path, data)

    monkeypatch.setattr(tr, "_atomic_write", full)
    assert_serial_fallback(crossing, jobs_dir, "cannot start child")


@needs_runtime
def test_health_check_is_cached_and_invalidated_by_a_changed_lock(route_on, jobs_dir, monkeypatch, tmp_path):
    runs = []
    original = tr.run_child
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: runs.append(a[1]["kind"]) or original(*a, **k))
    tr._INJECT.update(temp_root=str(jobs_dir))
    prepare_structural_connectivity(crossing())
    prepare_structural_connectivity(crossing())
    assert runs.count("selftest") <= 1 and runs.count("batch_prepare") == 2
    lock = tmp_path / "lock.json"
    lock.write_text("{}")
    tr._INJECT.update(lock_path=lock)
    before = runs.count("selftest")
    prepare_structural_connectivity(crossing())
    lock.write_text('{"numpy": "%s"}' % tr.EXPECT_NUMPY)
    prepare_structural_connectivity(crossing())
    assert runs.count("selftest") == before + 2  # a new lock hash forces a new health check


# ------------------------------------------------------------------------------------------ cancel

@needs_runtime
def test_cancel_kills_only_the_child_removes_temp_and_leaves_geometry_untouched(route_on, jobs_dir, tmp_path):
    pidfile = tmp_path / "pid.txt"
    bystander = psutil.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    child = stub(tmp_path, "time.sleep(60)\n")
    state = {"polls": 0}

    def check(phase):
        # cancel once the child has really started (its pid file exists)
        if pidfile.exists() and pidfile.read_text():
            raise Cancel(phase)

    model = crossing()
    expected_before = digest(to_dict(model))
    tr._INJECT.update(temp_root=str(jobs_dir), child_main=child, extra_env={"STUB_PIDFILE": str(pidfile)})
    try:
        with pytest.raises(Cancel):
            prepare_structural_connectivity(model, cancellation_check=check)
        pid = int(pidfile.read_text())
        assert not psutil.pid_exists(pid)  # the child is gone
        assert bystander.is_running()  # an unrelated python process is untouched
        assert list(jobs_dir.iterdir()) == []
        assert digest(to_dict(model)) == expected_before
    finally:
        bystander.kill()


# ------------------------------------------------------------------------------------------ child properties

@needs_runtime
def test_child_runs_below_normal_with_clean_env_and_the_thread_switch(route_on, jobs_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("ANYGEOMETRY_NOT_FORWARDED", "1")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    tr.configure(threads=3)
    child = stub(tmp_path, '''
        import ctypes
        prio = 0x4000
        if os.name == "nt":
            k = ctypes.WinDLL("kernel32")
            k.GetCurrentProcess.restype = ctypes.c_void_p
            k.GetPriorityClass.argtypes = [ctypes.c_void_p]
            prio = k.GetPriorityClass(k.GetCurrentProcess())
        info = dict(prio=prio, threads=os.environ.get("ANYGEOMETRY_PLANNING_THREADS"),
                    leaked=sorted(k for k in os.environ if k.startswith("ANYGEOMETRY_NOT") or k == "PYTHONPATH"),
                    has_path="PATH" in os.environ)
        write("unavailable", dict(reason=json.dumps(info)))
    ''')
    run_on(crossing, temp_root=str(jobs_dir), child_main=child)
    info = json.loads(tr.last_outcome.reason)
    assert info["prio"] == 0x4000 and info["threads"] == "3" and info["leaked"] == [] and info["has_path"] is False


@needs_runtime
def test_child_dies_with_the_parent(tmp_path):
    pidfile = tmp_path / "pid.txt"
    script = tmp_path / "parent.py"
    child = stub(tmp_path, "time.sleep(120)\n")
    script.write_text(textwrap.dedent('''
        import sys, os, time
        sys.path[:0] = %r
        from pathlib import Path
        from anyfem import thread_route as tr
        import threading
        threading.Timer(1.0, lambda: os._exit(0)).start()
        tr.run_child(Path(%r), dict(kind="x"), None, threads=1, timeout=100, child_main=Path(%r),
                     extra_env={"STUB_PIDFILE": %r})
    ''' % ([p for p in sys.path if p], str(PYTHON), str(child), str(pidfile))))
    import subprocess
    subprocess.run([sys.executable, str(script)], timeout=60)
    pid = int(pidfile.read_text())
    deadline = time.time() + 10
    while psutil.pid_exists(pid) and time.time() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(pid)


# ------------------------------------------------------------------------------------------ route-off regression

ROUTE_OFF_GOLDEN = "f258569596de90843367ad7e4dc46b4d4720e4418e78bc440df284e6ce841bab"


def test_route_off_result_is_frozen():
    """The route-off path must stay bit-identical to the code that existed before the hook (golden measured on main)."""
    model = crossing()
    report = prepare_structural_connectivity(model)
    assert tr.last_outcome is None
    assert result_of(model, report) == ROUTE_OFF_GOLDEN


def test_shared_core_equals_frozen_smoke():
    assert _batch_prepare.smoke_digest() == _batch_prepare.SMOKE_DIGEST
