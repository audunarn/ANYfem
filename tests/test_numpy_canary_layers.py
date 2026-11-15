"""T7: a NumPy other than the pinned 2.4.6 (the 2.5.x line) must be refused or detected by every layer that can see it.

No NumPy 2.5 is installed here: each layer gets a lock / wheelhouse / child report that CLAIMS 2.5.x.  The real 2.5 run (is there an observable
bit change at all?) is the manual canary, docs/NUMPY_CANARY.md.  Layers: (1) lock parse, installed-bundle check, route lock check, per-job child
check, end-to-end route decline with an unchanged serial result; (2) build_runtime refuses an unlocked NumPy; (3) the canary verdict logic.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

from anyfem import runtime_lock as rl
from anyfem import thread_route as tr
from test_runtime_lock import _built_lock, _input_lock
from test_thread_route_canaries import app, assert_declined, route_on_result, serial_reference, spawned, write_bundle  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ["2.5.0rc1", "2.5.0", "2.5.1", "2.5.2", "2.5.3", "2.5.4", "2.5.99"]
WIN = pytest.mark.skipif(os.name != "nt", reason="the thread route is Windows only")


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _claiming(version, *, built=False):
    """A lock that is internally consistent for NumPy ``version`` (pin and wheel list edited together)."""
    data = _built_lock() if built else _input_lock()
    data["numpy"] = version
    data["wheels"][0].update(version=version, filename="numpy-%s-cp314-cp314t-win_amd64.whl" % version)
    return data


# --------------------------------------------------------------------------------------------- layer 1: lock / health / route

@pytest.mark.parametrize("version", VERSIONS)
def test_l1_parse_lock_refuses_a_consistent_numpy_25_lock(version):
    with pytest.raises(rl.LockError, match="numpy"):
        rl.parse_lock(_claiming(version))


@pytest.mark.parametrize("version", VERSIONS)
def test_l1_parse_lock_refuses_a_25_wheel_under_the_24_pin(version):
    data = _input_lock()  # pin says 2.4.6, the wheel list carries 2.5.x
    data["wheels"][0].update(version=version, filename="numpy-%s-cp314-cp314t-win_amd64.whl" % version)
    with pytest.raises(rl.LockError, match="numpy"):
        rl.parse_lock(data)


@pytest.mark.parametrize("version", VERSIONS)
def test_l1_installed_check_names_numpy(tmp_path, version):
    (tmp_path / rl.LOCK_NAME).write_text(json.dumps(_claiming(version, built=True)), encoding="utf-8")
    reason, lock = rl.check_installed_lock(tmp_path, expect_numpy="2.4.6", expect_source_digest="b" * 64)
    assert lock is None and "numpy" in reason.lower()


@pytest.mark.parametrize("version", VERSIONS)
def test_l1_installed_check_is_its_own_barrier_against_the_caller_expectation(tmp_path, version):
    """A lock the parser accepts (pin 2.4.6) is still refused when the caller expects another NumPy: no silent re-qualification."""
    (tmp_path / rl.LOCK_NAME).write_text(json.dumps(_built_lock()), encoding="utf-8")
    reason, lock = rl.check_installed_lock(tmp_path, expect_numpy=version, expect_source_digest="b" * 64)
    assert lock is None and "numpy" in reason.lower()


@pytest.mark.parametrize("version", VERSIONS)
def test_l1_route_lock_check_refuses_a_25_pin(version):
    reason, _ = tr._check_lock(json.dumps({"schema_version": 1, "numpy": version}).encode())
    assert "NumPy" in reason and version in reason


@pytest.mark.parametrize("version", VERSIONS)
def test_l1_per_job_child_check_refuses_a_child_running_25(version):
    reason = tr._check_runtime({"health": {"gil_enabled": False, "numpy": version}})
    assert reason.startswith("child NumPy") and version in reason


@WIN
@pytest.mark.parametrize("version", ["2.5.0", "2.5.4"])
def test_l1_route_declines_a_numpy_25_bundle_and_the_serial_result_is_identical(app, spawned, version):  # noqa: F811
    expected = serial_reference()
    write_bundle(app, numpy=version)
    assert route_on_result() == expected
    assert_declined("numpy")
    assert spawned == []


@WIN
def test_l1_a_numpy_swapped_into_a_bundle_whose_lock_still_says_24_changes_the_bundle_digest(app, spawned):  # noqa: F811
    """The lock claims 2.4.6 and describes the folder; a 2.5.4 numpy tree then replaces the 2.4.6 one: the bundle digest no longer matches."""
    site = app / "Lib" / "site-packages"
    old = site / "numpy-2.4.6.dist-info"
    old.mkdir(parents=True)
    (old / "METADATA").write_text("Name: numpy\nVersion: 2.4.6\n", encoding="utf-8")
    write_bundle(app)
    expected = serial_reference()
    (old / "METADATA").unlink()
    old.rmdir()
    new = site / "numpy-2.5.4.dist-info"
    new.mkdir()
    (new / "METADATA").write_text("Name: numpy\nVersion: 2.5.4\n", encoding="utf-8")
    assert route_on_result() == expected
    assert_declined("bundle")
    assert spawned == []


# --------------------------------------------------------------------------------------------- layer 2: build_runtime

@pytest.fixture(scope="module")
def build_tool():
    return _load_tool("build_runtime")


def _build_inputs(tmp_path, version):
    base = tmp_path / "base"
    (base / "Lib").mkdir(parents=True)
    (base / "python3.14t.exe").write_bytes(b"EXE")
    (base / "Lib" / "os.py").write_bytes(b"os")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel = wheels / ("numpy-%s-cp314-cp314t-win_amd64.whl" % version)
    wheel.write_bytes(b"not a wheel")
    data = _claiming(version)
    data["interpreter"].update(root_files=["python3.14t.exe"], trees=["Lib"], payload_files=2)
    data["wheels"] = [dict(data["wheels"][0], sha256=hashlib.sha256(wheel.read_bytes()).hexdigest())]
    probe = _input_lock()
    probe["interpreter"] = data["interpreter"]
    digest, count = rl.payload_digest(base, rl.parse_lock(probe).interpreter)
    data["interpreter"].update(payload_sha256=digest, payload_files=count)
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps(data), encoding="utf-8")
    return lock, base, wheels


@pytest.mark.parametrize("version", ["2.5.0", "2.5.4"])
def test_l2_build_refuses_a_lock_that_pins_numpy_25_and_creates_nothing(build_tool, tmp_path, capsys, version):
    lock, base, wheels = _build_inputs(tmp_path, version)  # the wheel hash really matches the edited lock: only the pin is wrong
    out = tmp_path / "out" / "runtime-314t"
    assert build_tool.main(["build", "--lock", str(lock), "--base-install", str(base), "--wheelhouse", str(wheels), "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and "numpy" in err.lower()
    assert not out.exists() and not (tmp_path / "out").exists()


@pytest.mark.parametrize("version", ["2.5.0", "2.5.4"])
def test_l2_requirements_refuses_to_emit_hash_lines_for_numpy_25(build_tool, tmp_path, capsys, version):
    lock, _, _ = _build_inputs(tmp_path, version)
    assert build_tool.main(["requirements", "--lock", str(lock)]) == 2
    captured = capsys.readouterr()
    assert "numpy" in captured.err.lower() and captured.out == ""


def test_l2_a_foreign_wheel_under_the_24_lock_is_refused(build_tool, tmp_path, capsys):
    lock, base, wheels = _build_inputs(tmp_path, "2.4.6")
    (wheels / "numpy-2.4.6-cp314-cp314t-win_amd64.whl").write_bytes(b"this is the numpy 2.5.4 wheel")  # same name, other content
    out = tmp_path / "out" / "runtime-314t"
    assert build_tool.main(["build", "--lock", str(lock), "--base-install", str(base), "--wheelhouse", str(wheels), "--out", str(out)]) == 2
    err = capsys.readouterr().err
    assert "numpy-2.4.6-cp314-cp314t-win_amd64.whl has sha256" in err  # refused by the hash pin, not by some later failure
    assert not out.exists()


def test_l2_the_shipped_lock_pins_24_and_no_workflow_installs_an_unpinned_numpy():
    assert json.loads((ROOT / "tools" / "runtime.lock.json").read_text(encoding="utf-8"))["numpy"] == "2.4.6"
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        if workflow.name == "numpy-canary.yml":  # the canary installs 2.5.x on purpose, hash-pinned, into a throwaway venv
            continue
        for line in workflow.read_text(encoding="utf-8").splitlines():
            if "pip install" in line and "numpy" in line:
                assert "numpy==2.4.6" in line, "%s installs an unpinned NumPy: %s" % (workflow.name, line.strip())


# --------------------------------------------------------------------------------------------- layer 3: canary verdict logic

@pytest.fixture(scope="module")
def canary():
    return _load_tool("ci_numpy_canary")


BASE_BLOB = {"faces": [1, 2, 3], "x": 0.1, "k": 2.0, "name": "n", "nested": {"a": 1.5, "b": [2.5, 3.5]}}


def _env(**over):
    return dict({"OPENBLAS_CORETYPE": "", "NPY_DISABLE_CPU_FEATURES": "", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                 "NUMPY_MADVISE_HUGEPAGE": ""}, **over)


def _record(numpy="2.4.6", blob=None, **over):
    raw = json.dumps(BASE_BLOB if blob is None else blob, sort_keys=True, separators=(",", ":"))
    record = {"numpy": numpy, "python": "3.14.10", "scipy": "1.17.1", "shapely": "2.1.2", "anygeometry": "/ag", "anyfem": "/af", "closure_sha256": "c" * 64,
              "anygeometry_src_sha256": "d" * 64, "anyfem_src_sha256": "e" * 64,
              "masked_sha256": hashlib.sha256(raw.encode()).hexdigest(), "faces": 97, "env": _env()}
    record.update(over)
    return record, raw


def _write(tmp_path, name, numpy="2.4.6", blob=None, blob_text=None, **over):
    record, raw = _record(numpy, blob, **over)
    path = tmp_path / (name + ".json")
    path.write_text(json.dumps(record), encoding="utf-8")
    if blob_text is not False:
        (tmp_path / (name + ".json.blob")).write_bytes((raw if blob_text is None else blob_text).encode())
    return path


def _drift(**changes):
    blob = json.loads(json.dumps(BASE_BLOB))
    blob.update(changes)
    return blob


def test_l3_identical_result_under_25_is_reported_not_detected(canary, tmp_path):
    a, b = _write(tmp_path, "a"), _write(tmp_path, "b", numpy="2.5.4")
    assert canary.compare(a, b, "2.4.6") == canary.NOT_DETECTED


def test_l3_a_pure_float_difference_is_detected(canary, tmp_path, capsys):
    a, b = _write(tmp_path, "a"), _write(tmp_path, "b", numpy="2.5.4", blob=_drift(x=0.1 + 2e-17, nested={"a": 1.5000000000000002, "b": [2.5, 3.5]}))
    assert canary.compare(a, b, "2.4.6") == canary.DETECTED
    assert "2 differing float leaves, nothing else" in capsys.readouterr().out


@pytest.mark.parametrize("changes", [dict(faces=[1, 2, 3, 4]), dict(name="m"), dict(faces=[1, 2, 4]), dict(k=2), dict(nested={"a": 1.5}),
                                     dict(extra=0.5), dict(name=None), dict(faces=[1, 2, 3.0])])
def test_l3_a_difference_that_is_not_purely_float_is_an_input_problem_not_a_detection(canary, tmp_path, changes):
    """Every case also carries a genuine float drift (x), so only the 'nothing else changed' rule can refuse it."""
    drift = _drift(**changes)
    drift["x"] = 0.2
    a, b = _write(tmp_path, "a"), _write(tmp_path, "b", numpy="2.5.4", blob=drift)
    assert canary.compare(a, b, "2.4.6") == canary.INPUT


def test_l3_the_verdict_is_bound_to_the_blobs(canary, tmp_path):
    ok = _write(tmp_path, "ok")
    drift = _drift(x=0.2)
    # a recorded digest that is not the digest of the blob next to it, even though the blobs differ in a float: not a detection
    a, b = _write(tmp_path, "a"), _write(tmp_path, "b", numpy="2.5.4", blob=_drift(x=0.2), masked_sha256="b" * 64)
    assert canary.compare(a, b, "2.4.6") == canary.INPUT
    # identical recorded digests but different blob contents
    a2 = _write(tmp_path, "a2")
    b2 = _write(tmp_path, "b2", numpy="2.5.4")
    (tmp_path / "b2.json.blob").write_bytes(json.dumps(drift, sort_keys=True, separators=(",", ":")).encode())
    assert canary.compare(a2, b2, "2.4.6") == canary.INPUT
    # missing blob / malformed blob / blob not JSON with matching digest
    c = _write(tmp_path, "c", numpy="2.5.4", blob=_drift(x=0.3), blob_text=False)
    assert canary.compare(ok, c, "2.4.6") == canary.INPUT
    (tmp_path / "d.json").write_text(json.dumps(dict(_record("2.5.4")[0], masked_sha256=hashlib.sha256(b"{").hexdigest())), encoding="utf-8")
    (tmp_path / "d.json.blob").write_bytes(b"{")
    assert canary.compare(ok, tmp_path / "d.json", "2.4.6") == canary.INPUT


@pytest.mark.parametrize("pinned,other", [(dict(numpy="2.4.5"), dict()), (dict(), dict(numpy="2.4.6")), (dict(), dict(numpy="2.6.0")),
                                          (dict(), dict(python="3.14.2")), (dict(), dict(scipy="1.18.0")), (dict(), dict(shapely="2.2.0")),
                                          (dict(), dict(anygeometry="/other")), (dict(), dict(anyfem="/other")), (dict(), dict(faces=98)),
                                          (dict(), dict(closure_sha256="f" * 64)), (dict(), dict(anygeometry_src_sha256="f" * 64)),
                                          (dict(), dict(anyfem_src_sha256="f" * 64)),
                                          (dict(), dict(env=_env(OPENBLAS_CORETYPE="HASWELL"))),
                                          (dict(), dict(env=_env(NPY_DISABLE_CPU_FEATURES="AVX2")))])
def test_l3_anything_but_numpy_that_differs_between_the_environments_fails_closed(canary, tmp_path, pinned, other):
    a = _write(tmp_path, "a", **dict({"numpy": "2.4.6"}, **pinned))
    b = _write(tmp_path, "b", **dict({"numpy": "2.5.4", "blob": _drift(x=0.2)}, **other))
    assert canary.compare(a, b, "2.4.6") == canary.INPUT


def test_l3_signed_zero_is_a_float_difference(canary, tmp_path):
    a = _write(tmp_path, "a", blob=_drift(x=0.0))
    b = _write(tmp_path, "b", numpy="2.5.4", blob=_drift(x=-0.0))
    assert canary.compare(a, b, "2.4.6") == canary.DETECTED


def test_l3_nan_and_infinity_constants_fail_closed_even_for_whitespace_only_differences(canary, tmp_path):
    a = _write(tmp_path, "a", blob_text='{"x":NaN}', masked_sha256=hashlib.sha256(b'{"x":NaN}').hexdigest())
    b = _write(tmp_path, "b", numpy="2.5.4", blob_text='{"x": NaN}', masked_sha256=hashlib.sha256(b'{"x": NaN}').hexdigest())
    assert canary.compare(a, b, "2.4.6") == canary.INPUT
    c = _write(tmp_path, "c", blob_text='{"x":Infinity}', masked_sha256=hashlib.sha256(b'{"x":Infinity}').hexdigest())
    d = _write(tmp_path, "d", numpy="2.5.4", blob_text='{"x":1.0}', masked_sha256=hashlib.sha256(b'{"x":1.0}').hexdigest())
    assert canary.compare(c, d, "2.4.6") == canary.INPUT


def test_l3_malformed_records_fail_closed(canary, tmp_path):
    a = _write(tmp_path, "a")
    assert canary.compare(a, tmp_path / "missing.json", "2.4.6") == canary.INPUT
    bad = _write(tmp_path, "bad", numpy="2.5.4", masked_sha256="zz")
    assert canary.compare(a, bad, "2.4.6") == canary.INPUT
    (tmp_path / "x.json").write_text("{")
    assert canary.compare(a, tmp_path / "x.json", "2.4.6") == canary.INPUT
    no_env = json.loads(_write(tmp_path, "ne", numpy="2.5.4", blob=_drift(x=0.2)).read_text())
    no_env.pop("env")
    (tmp_path / "y.json").write_text(json.dumps(no_env))
    assert canary.compare(a, tmp_path / "y.json", "2.4.6") == canary.INPUT
    boolfaces = _write(tmp_path, "z", numpy="2.5.4", faces=True, blob=_drift(x=0.2))
    assert canary.compare(a, boolfaces, "2.4.6") == canary.INPUT


def test_l3_masking_hides_uuids_and_hashes_but_not_numbers(canary):
    text = '{"id":"12345678-1234-5678-1234-567812345678","h":"%s","x":1.0000000000000002}' % ("a" * 64)
    assert canary.mask(text) == '{"id":"X","h":"X","x":1.0000000000000002}'


def _canary_workflow() -> str:
    return (ROOT / ".github" / "workflows" / "numpy-canary.yml").read_text(encoding="utf-8")


def test_the_nightly_canary_is_a_small_separate_job_outside_every_heavy_regime():
    text = _canary_workflow()
    jobs = text.split("\njobs:\n", 1)[1]
    assert re.findall(r"^  ([A-Za-z0-9_-]+):$", jobs, re.M) == ["numpy-canary"]  # exactly one job
    assert int(re.search(r"timeout-minutes: (\d+)", text).group(1)) <= 20  # small: the measurement is ~15 s per interpreter
    assert "NOT part of any heavy test regime" in text.split("\non:\n", 1)[0]
    for heavy in ("ci_n2c_gate", "n2c-model", "N2C_ENABLED", "test_t3_identity", "pytest", "build_runtime.py build"):
        assert heavy not in text, heavy


def test_no_other_workflow_calls_the_canary_and_it_has_no_pull_request_trigger():
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        if workflow.name == "numpy-canary.yml":
            head = text.split("\npermissions:", 1)[0]
            code = "\n".join(line for line in head.splitlines() if not line.lstrip().startswith("#"))
            assert "pull_request" not in code and "push" not in code
        else:
            assert "ci_numpy_canary" not in text and "numpy-canary" not in text, workflow.name


def test_the_canary_requirement_hash_is_recorded_as_verified_by_download():
    head = (ROOT / "tools" / "numpy_canary_requirements.txt").read_text(encoding="utf-8")
    assert "verified by download" in head and "12901584" in head and "not yet verified" not in head
    assert "not verified" not in _canary_workflow().lower()


def test_the_canary_workflow_is_nightly_and_manual_never_on_pull_requests_and_hash_pins_its_numpy():
    text = _canary_workflow()
    triggers = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    keys = sorted(line.strip().rstrip(":") for line in triggers.splitlines() if line.startswith("  ") and not line.startswith("   "))
    assert keys == ["schedule", "workflow_dispatch"], keys  # no push / pull_request / pull_request_target / workflow_run
    assert len(re.findall(r"- cron: '[0-9*/, -]+ [0-9*/, -]+ \* \* \*'", triggers)) == 1  # one nightly cron
    assert "--require-hashes" in text and "tools/numpy_canary_requirements.txt" in text
    pins = [line for line in (ROOT / "tools" / "numpy_canary_requirements.txt").read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    assert len(pins) == 1 and pins[0].startswith("numpy==2.5.") and "--hash=sha256:" in pins[0]


CASES = {"overflow": ('{"x":1e400}', '{"x":1.0}'), "negative-overflow": ('{"x":1.0}', '{"x":-1e999}'),
         "duplicate-keys": ('{"k":0,"k":1,"x":0.1}', '{"k":9,"k":1,"x":0.2}'),
         "runaway-nesting": ("[" * 200000 + "]" * 200000, "[" * 200000 + "0.5" + "]" * 200000)}


@pytest.mark.parametrize("case", sorted(CASES))
def test_l3_overflow_duplicate_keys_and_runaway_nesting_fail_closed(canary, tmp_path, case):
    text_a, text_b = CASES[case]
    a = _write(tmp_path, "a", blob_text=text_a, masked_sha256=hashlib.sha256(text_a.encode()).hexdigest())
    b = _write(tmp_path, "b", numpy="2.5.4", blob_text=text_b, masked_sha256=hashlib.sha256(text_b.encode()).hexdigest())
    assert canary.compare(a, b, "2.4.6") == canary.INPUT


def test_l3_masked_blob_refuses_what_masking_would_corrupt(canary):
    u1, u2 = "12345678-1234-5678-1234-567812345678", "87654321-1234-5678-1234-567812345678"
    assert canary.masked_blob('{"a":"%s","b":1.5}' % u1) == '{"a":"X","b":1.5}'
    with pytest.raises(ValueError):
        canary.masked_blob('{"%s":1,"%s":2}' % (u1, u2))  # two keys collapse into one
    with pytest.raises(ValueError):
        canary.masked_blob("[%s]" % ("1" * 64))  # a bare 64-digit integer would become the invalid token X


def test_l3_duplicate_metadata_keys_and_incomplete_env_fail_closed(canary, tmp_path):
    a = _write(tmp_path, "a")
    b = _write(tmp_path, "b", numpy="2.5.4", blob=_drift(x=0.2))
    good = b.read_text()
    assert canary.compare(a, b, "2.4.6") == canary.DETECTED
    b.write_text(good.replace('"numpy": "2.5.4"', '"numpy": "2.4.6", "numpy": "2.5.4"'))
    assert canary.compare(a, b, "2.4.6") == canary.INPUT
    for env in ({}, {"OPENBLAS_CORETYPE": ""}, dict(_env(), EXTRA="x"), dict(_env(), OMP_NUM_THREADS=1)):
        record = json.loads(good)
        record["env"] = env
        b.write_text(json.dumps(record))
        assert canary.compare(a, b, "2.4.6") == canary.INPUT
