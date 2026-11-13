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

try:
    import psutil
except ImportError:  # only the process-inspection tests need it; the rest must still run
    psutil = None
needs_psutil = pytest.mark.skipif(psutil is None, reason="psutil not installed")

import anygeometry  # noqa: E402
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


def alive(pid: int) -> bool:
    """True while the process still runs (a terminated process whose handle is still open elsewhere is not alive)."""
    if os.name != "nt":
        if psutil is not None:
            return psutil.pid_exists(pid)
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes
    k = ctypes.WinDLL("kernel32")
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_uint32()
        return bool(k.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        k.CloseHandle(handle)


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
    monkeypatch.setattr(tr, "last_adoption", {})
    monkeypatch.setattr(tr, "last_outcome", None)
    yield
    tr.configure(reset=True)


@pytest.fixture
def route_on_any_host():
    tr.configure(enabled=True, min_faces=0, runtime=str(RUNTIME_ROOT))


@pytest.fixture
def route_on(route_on_any_host):
    if os.name != "nt":  # the route runs on Windows only; these tests exercise it past the host guard
        pytest.skip("the thread route is Windows only")


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


def valid_health():
    """Exactly what the real runtime reports (the stub child stands in for a refusing planner on that runtime)."""
    measured = tr.check_health(PYTHON)
    assert measured.status == tr.OK
    return dict(measured.body["health"])


def valid_modules():
    import anygeometry
    package = Path(anygeometry.__file__).resolve().parent
    return dict(modules=["errors.py"], loaded_digest=_batch_prepare.files_digest(package, ["errors.py"]),
                gil_final=False, route=_batch_prepare.resolved_route())


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

HELPERS_MARK = None


def closure_of(builder):
    model = builder()
    return model.extract_model_closure([model.handle("face", i) for i in sorted(model.faces)],
                                       include_structural_closure=True, include_features=False)


def prepare_closure(closure, **kwargs):
    return prepare_structural_connectivity(closure.working_model, source_model_id=str(closure.source_model_id),
                                           source_revision=closure.source_revision, member_ids=(), **kwargs)


def proof_facts(closure):
    """What downstream consumes: the boolean proof, the authenticated source-edge paths and the binding contents."""
    import anygeometry
    from anyfem.structural_preparation import source_edge_paths
    paths = source_edge_paths(closure)
    return dict(proof=bool(anygeometry.has_current_intersection_preparation(closure.working_model)),
                paths=len(paths), path_values=sorted(map(repr, paths.items())),
                fingerprint=tr.binding_fingerprint(closure.working_model))


def serial_facts(builder=crossing):
    tr.configure(enabled=False)
    closure = closure_of(builder)
    prepare_closure(closure)
    facts = proof_facts(closure)
    tr.configure(enabled=True)
    return facts


def tamper_plan_result(monkeypatch, change):
    """Let the real child run, then edit its (already validated) result the way a faulty or hostile child could."""
    original = tr.run_child

    def wrapped(python, job, document, **kwargs):
        outcome = original(python, job, document, **kwargs)
        if job["kind"] == "plan" and outcome.status == tr.OK:
            change(outcome, job)
        return outcome

    monkeypatch.setattr(tr, "run_child", wrapped)


def spy_owner_calls(monkeypatch):
    """Record the owner's adopt and apply calls made through the route (the parent must apply the adopted plan itself)."""
    from anygeometry import plan_adoption
    import anyfem._batch_prepare as batch
    calls = {"adopt": [], "apply": []}
    real_adopt, real_apply = plan_adoption.adopt_intersection_plan, anygeometry.apply_intersections

    def adopt(*args, **kwargs):
        plan = real_adopt(*args, **kwargs)
        if kwargs.get("job_id") != "probe":
            calls["adopt"].append((plan, kwargs))
        return plan

    def apply(geometry, plan, **kwargs):
        calls["apply"].append((geometry, plan, kwargs))
        return real_apply(geometry, plan, **kwargs)

    monkeypatch.setattr(plan_adoption, "adopt_intersection_plan", adopt)
    monkeypatch.setattr(anygeometry, "apply_intersections", apply)
    return calls


@needs_runtime
def test_adopted_child_plan_gives_the_genuine_proof_equal_to_serial(route_on, jobs_dir, monkeypatch):
    """The point of the slice: with the route ON the parent holds the owner proof and the same authenticated bindings as
    serial (source_edge_paths 8 == 8, binding fingerprint equal), the report equals route-off, and the parent itself applied
    the plan it adopted (receipts are minted by the owner's own apply in this process)."""
    expected = reference(crossing)
    serial = serial_facts()
    assert serial["proof"] and serial["paths"] == 8
    calls = spy_owner_calls(monkeypatch)
    kinds = []
    original = tr.run_child
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: kinds.append(a[1]["kind"]) or original(*a, **k))
    tr._INJECT.update(temp_root=str(jobs_dir))
    closure = closure_of(crossing)
    prepare_closure(closure)
    assert tr.last_adoption.get("adopted") is True and tr.last_outcome.status == tr.OK
    assert proof_facts(closure) == serial
    assert tr.last_adoption["fingerprint"] == serial["fingerprint"]
    assert len(calls["adopt"]) == 1 and len(calls["apply"]) == 1
    (adopted_plan, _), (applied_to, applied_plan, _) = calls["adopt"][0], calls["apply"][0]
    assert applied_plan is adopted_plan and applied_to is closure.working_model
    assert closure.working_model._intersection_preparation_receipt[0] is adopted_plan  # minted by the owner's apply here
    assert kinds.count("plan") == 1 and "batch_prepare" not in kinds
    assert list(jobs_dir.iterdir()) == []
    model, report, _ = run_on(crossing, temp_root=str(jobs_dir))
    assert result_of(model, report) == expected


@needs_runtime
def test_parent_adopts_and_applies_with_one_policy_carrying_its_own_cancellation(route_on, jobs_dir, monkeypatch):
    from anygeometry import plan_adoption
    import anyfem._batch_prepare as batch
    seen = {}
    real_adopt, real_apply = plan_adoption.adopt_intersection_plan, anygeometry.apply_intersections

    def adopt(model, packet, **kwargs):
        if kwargs["job_id"] != "probe":
            seen["adopt"] = kwargs["policy"]
        return real_adopt(model, packet, **kwargs)

    def apply(geometry, plan, **kwargs):
        seen["apply"] = kwargs["policy"]
        return real_apply(geometry, plan, **kwargs)

    monkeypatch.setattr(plan_adoption, "adopt_intersection_plan", adopt)
    monkeypatch.setattr(anygeometry, "apply_intersections", apply)
    phases = []
    tr._INJECT.update(temp_root=str(jobs_dir))
    prepare_structural_connectivity(crossing(), cancellation_check=phases.append)
    assert seen["adopt"] is seen["apply"]  # ONE policy object, built in the parent
    assert seen["apply"].cancellation_check is not None
    assert seen["apply"].cancellation_check() is False
    assert "structural intersection batch" in phases


@needs_runtime
def test_parent_cancellation_during_adopt_and_apply_propagates_without_a_serial_rerun(route_on, jobs_dir, monkeypatch):
    done = []
    original = tr.run_child

    def wrapped(*a, **k):
        outcome = original(*a, **k)
        done.append(a[1]["kind"])  # recorded only AFTER the child has finished: cancellation now comes from the parent phase
        return outcome

    monkeypatch.setattr(tr, "run_child", wrapped)
    serial_calls = []
    real = anygeometry.plan_intersections
    monkeypatch.setattr(anygeometry, "plan_intersections", lambda *a, **k: serial_calls.append(1) or real(*a, **k))

    reached = []

    def check(phase):
        if "plan" in done:
            reached.append(phase)
            raise Cancel(phase)

    tr._INJECT.update(temp_root=str(jobs_dir))
    with pytest.raises(Cancel):
        prepare_structural_connectivity(crossing(), cancellation_check=check)
    assert reached == ["structural intersection batch"]  # raised by the parent's adopt/apply, not by the child polling loop
    assert serial_calls == [] and list(jobs_dir.iterdir()) == []


@needs_runtime
def test_stale_job_is_refused_and_serial_runs_on_the_edited_geometry(route_on, jobs_dir, monkeypatch):
    def edit_parent_geometry(outcome, job):
        edited[0].add_points(((9, 9, 9),))

    edited = [None]
    tamper_plan_result(monkeypatch, edit_parent_geometry)
    model = crossing()
    edited[0] = model
    tr._INJECT.update(temp_root=str(jobs_dir))
    report = prepare_structural_connectivity(model)
    assert tr.last_outcome.status == tr.UNAVAILABLE and "plan adoption refused" in tr.last_outcome.reason
    twin = crossing()
    twin.add_points(((9, 9, 9),))
    tr.configure(enabled=False)
    expected_report = prepare_structural_connectivity(twin)
    # the report's source_revision was read at entry (before the concurrent edit); topology and connections must equal serial
    assert digest(to_dict(model)) == digest(to_dict(twin)) and report.connections == expected_report.connections
    assert list(jobs_dir.iterdir()) == []


def _flip_packet(outcome, job, fix_hash=True):
    import base64
    raw = bytearray(base64.b64decode(outcome.body["packet"]))
    raw[len(raw) // 2] ^= 0x01
    outcome.body["packet"] = base64.b64encode(bytes(raw)).decode("ascii")
    if fix_hash:
        outcome.body["packet_sha256"] = hashlib.sha256(bytes(raw)).hexdigest()


@needs_runtime
@pytest.mark.parametrize("name, change, part", [
    ("packet-bytes-flipped-hash-fixed", _flip_packet, "plan adoption refused"),
    ("packet-bytes-flipped", lambda o, j: _flip_packet(o, j, fix_hash=False), "packet checksum mismatch"),
    ("job-id-of-another-job", lambda o, j: setattr(o, "job_id", "0" * 32), "plan adoption refused (job)"),
    ("gil-enabled-reported", lambda o, j: o.body["health"].update(gil_enabled=True), "GIL-disabled"),
    ("other-numpy-reported", lambda o, j: o.body["health"].update(numpy="2.0.0"), "NumPy"),
    ("not-a-free-threaded-build", lambda o, j: o.body["health"].update(gil_disabled_build=False), "free-threaded"),
    ("other-loaded-code", lambda o, j: o.body.update(loaded_digest="0" * 64), "different anygeometry code"),
    ("other-build-same-version", lambda o, j: o.body["health"].update(version_full="3.14.10 (another build)"),
     "interpreter identity"),
    ("gil-enabled-after-planning", lambda o, j: o.body.update(gil_final=True), "after planning"),
    ("planning-configuration-differs", lambda o, j: o.body.update(route=["other", "route"]), "planning configuration differs"),
    ("module-list-with-escape", lambda o, j: o.body.update(modules=["../secret.py"]), "digest could not be recomputed"),
    ("module-list-empty", lambda o, j: o.body.update(modules=[]), "loaded-module list"),
    ("packet-not-base64", lambda o, j: o.body.update(packet="***"), "base64"),
])
def test_forged_or_foreign_child_results_are_refused_and_serial_runs(route_on, jobs_dir, monkeypatch, name, change, part):
    expected = reference(crossing)
    tamper_plan_result(monkeypatch, change)
    model, report, _ = run_on(crossing, temp_root=str(jobs_dir))
    assert tr.last_outcome.status == tr.UNAVAILABLE and part in tr.last_outcome.reason, tr.last_outcome.reason
    assert result_of(model, report) == expected  # the serial fallback equals route-off
    assert tr.last_adoption == {}
    import anygeometry
    assert anygeometry.has_current_intersection_preparation(model)


@needs_runtime
@pytest.mark.parametrize("name, change, part", [
    ("identity-missing", lambda o, j: [o.body["health"].pop(k) for k in ("version", "cache_tag", "executable")], "interpreter identity"),
    ("other-python-version", lambda o, j: o.body["health"].update(version="3.13.1"), "interpreter identity"),
    ("other-cache-tag", lambda o, j: o.body["health"].update(cache_tag="cpython-313t"), "interpreter identity"),
    ("other-executable", lambda o, j: o.body["health"].update(executable=sys.executable), "interpreter identity"),
])
def test_child_interpreter_identity_is_validated(route_on, jobs_dir, monkeypatch, name, change, part):
    expected = reference(crossing)
    tamper_plan_result(monkeypatch, change)
    model, report, _ = run_on(crossing, temp_root=str(jobs_dir))
    assert tr.last_outcome.status == tr.UNAVAILABLE and part in tr.last_outcome.reason, tr.last_outcome.reason
    assert result_of(model, report) == expected


@needs_runtime
def test_child_refusal_of_a_stale_job_is_not_final_serial_decides_on_the_current_geometry(route_on, jobs_dir, monkeypatch):
    """A refusal is only final for the geometry it was computed on: when the parent changed meanwhile, the child's
    refusal is not raised; the ordinary serial path decides on the current geometry (here: it refuses by itself)."""
    original = tr.run_child
    model = overlapping()

    def wrapped(python, job, document, **kwargs):
        outcome = original(python, job, document, **kwargs)
        if job["kind"] == "plan":
            assert outcome.status == tr.REFUSAL  # the child refused the overlap it saw
            model.add_points(((9, 9, 9),))  # the parent geometry changed after the child was started
        return outcome

    serial_calls = []
    real = _batch_prepare.plan_apply_phase
    monkeypatch.setattr(_batch_prepare, "plan_apply_phase", lambda *a, **k: serial_calls.append(1) or real(*a, **k))
    monkeypatch.setattr(tr, "run_child", wrapped)
    tr._INJECT.update(temp_root=str(jobs_dir))
    with pytest.raises(StructuralPreparationError):
        prepare_structural_connectivity(model)
    assert serial_calls == [1] and tr.last_outcome.status == tr.UNAVAILABLE and "stale" in tr.last_outcome.reason
    assert list(jobs_dir.iterdir()) == []


def test_invalid_planning_configuration_in_the_parent_never_spawns_a_child(route_on, monkeypatch):
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: pytest.fail("child spawned despite an invalid planning configuration"))
    monkeypatch.setattr(_batch_prepare, "resolved_route", lambda: (_ for _ in ()).throw(GeometryError("bad planning mode")))
    model = crossing()
    assert tr.try_prepare(model, tuple(model.handle("face", i) for i in sorted(model.faces))) is None
    assert "planning configuration is invalid" in tr.last_outcome.reason


@needs_runtime
def test_runtime_lock_can_pin_the_interpreter_build(route_on, jobs_dir, tmp_path):
    lock = tmp_path / "runtime.lock.json"
    lock.write_text(json.dumps({"python_version_full": "3.14.0 (a different build)"}))
    assert_serial_fallback(crossing, jobs_dir, "different interpreter build", lock_path=lock)


@needs_runtime
def test_child_that_planned_other_operands_is_refused(route_on, jobs_dir, monkeypatch):
    original = tr.run_child

    def wrapped(python, job, document, **kwargs):
        if job["kind"] == "plan":
            job = dict(job, operands=job["operands"][:1])
        return original(python, job, document, **kwargs)

    monkeypatch.setattr(tr, "run_child", wrapped)
    expected = reference(crossing)
    model, report, _ = run_on(crossing, temp_root=str(jobs_dir))
    assert "plan adoption refused (operands)" in tr.last_outcome.reason
    assert result_of(model, report) == expected


@needs_runtime
def test_missing_proof_after_the_apply_is_a_hard_failure_and_disables_the_route(route_on, jobs_dir, monkeypatch):
    """If the parent's own apply did not leave the genuine owner proof, the adopted result must never be used silently:
    typed failure, route off for the session, no serial rerun on the (unsoundly rollbackable) geometry."""
    import anygeometry
    serial_calls = []
    real = anygeometry.plan_intersections
    monkeypatch.setattr(anygeometry, "plan_intersections", lambda *a, **k: serial_calls.append(1) or real(*a, **k))
    monkeypatch.setattr(anygeometry, "has_current_intersection_preparation", lambda *a, **k: False)
    tr._INJECT.update(temp_root=str(jobs_dir))
    with pytest.raises(StructuralPreparationError, match="owner proof check"):
        prepare_structural_connectivity(crossing())
    assert tr._session["disabled"] and serial_calls == [] and tr.last_adoption == {}
    assert list(jobs_dir.iterdir()) == []


def three_plates() -> GeometryModel:
    model = crossing()
    model.add_plate(model.add_points(((1, 1, -1), (1, 3, -1), (1, 3, 1), (1, 1, 1))))
    return model


def test_genuine_proof_check_sees_a_missing_owner_receipt_with_the_real_fingerprint():
    """The real check, no stubs: a boolean proof alone is not enough; the bindings must exist and differ per model."""
    import anygeometry
    tr.configure(enabled=False)
    first, second = crossing(), three_plates()
    prepare_structural_connectivity(first)
    prepare_structural_connectivity(second)
    assert tr._genuine_proof(first) == "" and tr._genuine_proof(second) == ""
    assert tr.binding_fingerprint(first) != tr.binding_fingerprint(second)  # not a constant
    del first._prepared_face_preimages_receipt  # owner receipt of the face preimages vanishes, the boolean may stay true
    assert anygeometry.has_current_intersection_preparation(first)
    assert "owner bindings unusable" in tr._genuine_proof(first)
    del second._edge_subcurve_preimages_receipt
    assert tr._genuine_proof(second) != ""


def test_non_windows_hosts_run_serially(route_on_any_host, monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: pytest.fail("child spawned on a non-Windows host"))
    model = crossing()
    assert tr.try_prepare(model, tuple(model.handle("face", i) for i in sorted(model.faces))) is None
    assert "Windows only" in tr.last_outcome.reason


@needs_runtime
def test_unusable_bindings_after_the_apply_are_a_hard_failure(route_on, jobs_dir, monkeypatch):
    def broken(geometry):
        raise GeometryError("edge subcurve provenance is unavailable")

    monkeypatch.setattr(tr, "binding_fingerprint", broken)
    tr._INJECT.update(temp_root=str(jobs_dir))
    with pytest.raises(StructuralPreparationError, match="owner bindings unusable"):
        prepare_structural_connectivity(crossing())
    assert tr._session["disabled"] and tr.last_adoption == {}


@needs_runtime
def test_application_checksum_mismatch_is_a_hard_failure_and_disables_the_route_for_the_session(route_on, jobs_dir, monkeypatch):
    tr.configure(verify_apply=True)
    tamper_plan_result(monkeypatch, lambda outcome, job: outcome.body.update(apply_checksum="0" * 64))
    tr._INJECT.update(temp_root=str(jobs_dir))
    with pytest.raises(StructuralPreparationError, match="application checksum differs"):
        prepare_structural_connectivity(crossing())
    assert tr._session["disabled"]
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: pytest.fail("route used after it was disabled"))
    expected = reference(crossing)  # route-off baseline is unaffected
    tr.configure(enabled=True)
    model, report, _ = run_on(crossing)  # disabled for the session: ordinary serial path, no child
    assert result_of(model, report) == expected
    tr.configure(reset=True)
    assert tr._session["disabled"] == ""


@needs_runtime
def test_matching_application_checksum_is_adopted(route_on, jobs_dir):
    tr.configure(verify_apply=True)
    tr._INJECT.update(temp_root=str(jobs_dir))
    prepare_structural_connectivity(crossing())
    assert tr.last_adoption.get("adopted") is True and not tr._session["disabled"]


@pytest.mark.parametrize("make", ["journaled", "registry"])
def test_models_the_owner_declines_never_spawn_a_child(route_on, jobs_dir, monkeypatch, make):
    """journaled / existing-material / bounded / epoch models: decided from the parent's own model, before any child."""
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: pytest.fail("child spawned for a model the owner declines"))
    model = crossing()
    if make == "journaled":
        model._transaction_journal = object()
    else:
        from anygeometry import existing_material_preparation
        monkeypatch.setattr(existing_material_preparation, "eligible", lambda *a, **k: True)
    tr._INJECT.update(temp_root=str(jobs_dir))
    operands = tuple(model.handle("face", i) for i in sorted(model.faces))
    state = tr.try_prepare(model, operands)
    assert state is None and "before any child is spawned" in tr.last_outcome.reason


def test_epoch_models_never_spawn_a_child(route_on, monkeypatch):
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: pytest.fail("child spawned for an epoch model"))
    from anygeometry import preparation_epochs
    monkeypatch.setattr(preparation_epochs, "_epoch_plan_required", lambda model: True)
    model = crossing()
    assert tr.try_prepare(model, tuple(model.handle("face", i) for i in sorted(model.faces))) is None
    assert "epoch_model" in tr.last_outcome.reason


def test_without_the_owner_adoption_api_the_route_runs_serially(route_on, monkeypatch):
    monkeypatch.setattr(tr, "_adoption_api", lambda: None)
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: pytest.fail("child spawned without an adoption API"))
    expected = reference(crossing)
    model, report, _ = run_on(crossing)
    assert "no verified plan adoption API" in tr.last_outcome.reason
    assert result_of(model, report) == expected


@needs_runtime
def test_job_object_failure_kills_the_child_and_falls_back(route_on, jobs_dir, tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("job objects are Windows only")
    monkeypatch.setattr(tr._KillOnCloseJob, "assign", lambda self, pid: False)
    pidfile = tmp_path / "pid.txt"
    child = stub(tmp_path, "time.sleep(60)\n")
    assert_serial_fallback(crossing, jobs_dir, "job object", child_main=child, extra_env={"STUB_PIDFILE": str(pidfile)})
    deadline = time.time() + 10
    while pidfile.exists() and pidfile.read_text() and alive(int(pidfile.read_text())) and time.time() < deadline:
        time.sleep(0.1)
    assert not pidfile.exists() or not pidfile.read_text() or not alive(int(pidfile.read_text()))


@needs_runtime
def test_cancel_during_the_health_check_stops_the_health_child(jobs_dir, tmp_path, monkeypatch):
    tr.configure(enabled=True, min_faces=0, runtime=str(RUNTIME_ROOT))
    monkeypatch.setattr(tr, "_parent_source_digest", lambda: "1" * 64)  # fresh cache key: forces a new health run
    pidfile = tmp_path / "pid.txt"
    child = stub(tmp_path, "time.sleep(60)\n")

    def check(phase):
        if pidfile.exists() and pidfile.read_text():
            raise Cancel(phase)

    tr._INJECT.update(temp_root=str(jobs_dir), child_main_health=child, extra_env={"STUB_PIDFILE": str(pidfile)})
    started = time.time()
    with pytest.raises(Cancel):
        prepare_structural_connectivity(crossing(), cancellation_check=check)
    assert time.time() - started < 30
    assert not alive(int(pidfile.read_text()))
    assert list(jobs_dir.iterdir()) == []


def test_lock_lookup_for_both_runtime_layouts(tmp_path):
    root = tmp_path / "rt"
    (root / "Scripts").mkdir(parents=True)
    assert tr._runtime_root(root / "python.exe") == root
    assert tr._runtime_root(root / "Scripts" / "python.exe") == root
    (root / "runtime.lock.json").write_text(json.dumps({"numpy": "0.0.1"}))
    assert "pins NumPy" in tr.check_health(root / "python.exe").reason  # production lookup finds <runtime>/runtime.lock.json


def test_vanished_interpreter_is_unavailable_not_an_exception(tmp_path):
    outcome = tr.check_health(tmp_path / "gone" / "python.exe")
    assert outcome.status == tr.UNAVAILABLE and "cannot be inspected" in outcome.reason


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
    serial_calls = []
    real_plan_apply = _batch_prepare.plan_apply_phase
    monkeypatch.setattr(_batch_prepare, "plan_apply_phase", lambda *a, **k: serial_calls.append(1) or real_plan_apply(*a, **k))
    with pytest.raises(StructuralPreparationError) as routed:
        prepare_structural_connectivity(overlapping())
    assert serial_calls == []  # the refusal is final: no in-process planning after it
    assert str(routed.value) == str(serial.value)
    assert tr.last_outcome.status == tr.REFUSAL
    assert runs.count("plan") == 1  # one child, no retry, no serial repeat
    assert list(jobs_dir.iterdir()) == []


@needs_runtime
@pytest.mark.parametrize("name, body_change, part", [
    ("gil-on", lambda b: b["health"].update(gil_enabled=True), "GIL-disabled"),
    ("other-version", lambda b: b["health"].update(version="3.13.1"), "interpreter identity"),
    ("other-build-same-version", lambda b: b["health"].update(version_full=b["health"]["version_full"] + " [other build]"),
     "interpreter identity"),
    ("gil-enabled-after-planning", lambda b: b.update(gil_final=True), "after planning"),
    ("planning-configuration-differs", lambda b: b.update(route=["other", "route"]), "planning configuration differs"),
    ("other-code", lambda b: b.update(loaded_digest="0" * 64), "different anygeometry code"),
    ("no-modules", lambda b: b.pop("modules"), "loaded-module list"),
])
def test_refusal_from_an_untrusted_runtime_is_not_final(route_on, jobs_dir, tmp_path, name, body_change, part):
    body = dict(phase=1, type="GeometryError", message="refused by a runtime we do not trust", health=valid_health(),
                diagnostic=None, diagnostic_type=None, **valid_modules())
    body_change(body)
    child = stub(tmp_path, "write('refusal', json.loads(%r))" % json.dumps(body) + chr(10))
    assert_serial_fallback(crossing, jobs_dir, part, child_main=child)


@needs_runtime
def test_owner_diagnostic_of_a_child_refusal_survives_to_the_wrapper(route_on, jobs_dir, tmp_path):
    child = stub(tmp_path, '''
        write("refusal", dict(phase=1, type="GeometryError", message="face 1 vs 2 refused", health=%r,
                              diagnostic={"reason": "residual", "pair": [1, 2], "tolerance": 1e-9},
                              diagnostic_type="PlaneRuledSupportError", **%r))
    ''' % (valid_health(), valid_modules()))
    tr._INJECT.update(temp_root=str(jobs_dir), child_main=child)
    with pytest.raises(StructuralPreparationError) as routed:
        prepare_structural_connectivity(crossing())
    payload = routed.value.to_diagnostic()
    assert payload["cause"] == {"reason": "residual", "pair": [1, 2], "tolerance": 1e-9}
    assert payload["cause_type"] == "PlaneRuledSupportError"


@needs_runtime
def test_unreadable_progress_file_falls_back_instead_of_aborting(route_on, jobs_dir, tmp_path, monkeypatch):
    child = stub(tmp_path, "time.sleep(30)\n")
    real_open = open

    def flaky_open(path, *args, **kwargs):
        if str(path).endswith("progress.jsonl"):
            raise PermissionError("locked by a scanner")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(tr, "open", flaky_open, raising=False)
    # the progress file must exist for drain() to try opening it: the stub creates it
    child = stub(tmp_path, "open(os.path.join(job_dir, 'progress.jsonl'), 'w').write('{}' + chr(10))\ntime.sleep(30)\n")
    assert_serial_fallback(crossing, jobs_dir, "progress file unreadable", child_main=child)


@needs_runtime
def test_unreadable_source_file_falls_back_instead_of_aborting(route_on, jobs_dir, monkeypatch):
    def broken():
        raise PermissionError("source file locked")
    monkeypatch.setattr(tr, "_parent_source_digest", broken)
    assert_serial_fallback(crossing, jobs_dir, "could not read")


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
    assert not alive(int(pidfile.read_text()))


@needs_runtime
@pytest.mark.parametrize("name, body, part", [
    ("garbage", 'write("ok", {}, text="{{{ not json")', "malformed"),
    ("stale-job-id", 'write("ok", {}, job_id="deadbeef")', "does not belong"),
    ("bad-checksum", 'write("ok", {"x": 1}, sha="0" * 64)', "checksum mismatch"),
    ("truncated", 'write("ok", {}, text=\'{"format":1,"job_id":"\')', "malformed"),
    ("no-result-exit-0", "pass", "no result"),
    ("missing-packet", 'write("ok", {})', "invalid child result"),
    ("unavailable-status", 'write("unavailable", dict(reason="child said no"))', "child said no"),
    ("empty-refusal", 'write("refusal", {})', "malformed refusal"),
    ("refusal-bad-phase", 'write("refusal", dict(phase=3, type="GeometryError", message="m"))', "malformed refusal"),
    ("refusal-no-message", 'write("refusal", dict(phase=1, type="GeometryError", message=""))', "malformed refusal"),
])
def test_malformed_or_partial_results_fall_back(route_on, jobs_dir, tmp_path, name, body, part):
    child = stub(tmp_path, body + "\n")
    assert_serial_fallback(crossing, jobs_dir, part, child_main=child)


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
    assert runs.count("selftest") <= 1 and runs.count("plan") == 2
    lock = tmp_path / "lock.json"
    lock.write_text("{}")
    tr._INJECT.update(lock_path=lock)
    before = runs.count("selftest")
    prepare_structural_connectivity(crossing())
    lock.write_text('{"numpy": "%s"}' % tr.EXPECT_NUMPY)
    prepare_structural_connectivity(crossing())
    assert runs.count("selftest") == before + 2  # a new lock hash forces a new health check


# ------------------------------------------------------------------------------------------ cancel

@needs_psutil
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
        assert not alive(pid)  # the child is gone
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
                     extra_env={"STUB_PIDFILE": %r}, temp_root=%r)
    ''' % ([p for p in sys.path if p], str(PYTHON), str(child), str(pidfile), str(tmp_path))))
    import subprocess
    subprocess.run([sys.executable, str(script)], timeout=60)
    pid = int(pidfile.read_text())
    deadline = time.time() + 10
    while alive(pid) and time.time() < deadline:
        time.sleep(0.1)
    assert not alive(pid)


@needs_runtime
def test_child_exits_when_its_parent_is_already_gone(tmp_path):
    """The window before job-object containment: a child whose parent died must not keep running."""
    import subprocess
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "job.json").write_text(json.dumps(dict(job_id="x", kind="selftest", threads=1, expect_numpy=tr.EXPECT_NUMPY,
                                                      src_paths=tr._src_paths(), parent_pid=dead.pid)))
    started = time.time()
    code = subprocess.run([str(PYTHON), "-I", "-B", str(tr.CHILD_MAIN), str(job_dir)], timeout=60).returncode
    if os.name == "nt":
        assert code == 4 and not (job_dir / "result.json").exists() and time.time() - started < 30


def test_stale_job_folders_are_swept_but_fresh_ones_and_strangers_are_kept(tmp_path):
    old, fresh, stranger = (tmp_path / "anyfem-thread-old"), (tmp_path / "anyfem-thread-new"), (tmp_path / "other-old")
    for folder in (old, fresh, stranger):
        folder.mkdir()
        (folder / "document.json").write_text("{}")
    long_ago = time.time() - 3 * 24 * 3600
    os.utime(old, (long_ago, long_ago))
    os.utime(stranger, (long_ago, long_ago))
    tr._sweep_stale(str(tmp_path))
    assert not old.exists() and fresh.exists() and stranger.exists()


@needs_runtime
def test_every_child_run_sweeps_stale_job_folders(jobs_dir, tmp_path):
    old = jobs_dir / "anyfem-thread-abandoned"
    old.mkdir()
    long_ago = time.time() - 3 * 24 * 3600
    os.utime(old, (long_ago, long_ago))
    child = stub(tmp_path, 'write("unavailable", dict(reason="x"))\n')
    outcome = tr.run_child(PYTHON, dict(kind="x"), None, threads=1, timeout=60, child_main=child, temp_root=str(jobs_dir))
    assert outcome.status == tr.UNAVAILABLE and not old.exists()


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


@pytest.mark.parametrize("name", ["plan_intersections", "apply_intersections"])
def test_owner_entry_points_are_looked_up_at_call_time(monkeypatch, name):
    """Tests and diagnostics replace ``anygeometry.<entry point>``; the batch phases must call the replacement."""
    class Seen(Exception):
        pass

    def spy(*args, **kwargs):
        raise Seen(name)
    model = crossing()
    operands = tuple(model.handle("face", i) for i in sorted(model.faces))
    real_plan = _batch_prepare.plan_only(model, operands)
    monkeypatch.setattr(anygeometry, name, spy)
    with pytest.raises(Seen):
        if name == "plan_intersections":
            _batch_prepare.plan_only(model, operands)
        else:
            _batch_prepare.plan_apply_phase(model, operands, plan_source=lambda policy: real_plan)
