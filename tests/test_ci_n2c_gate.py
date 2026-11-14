"""The N2c identity gate must fail closed: a changed digest, counter, missing file or zero-face result is red, and says which group."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ci_n2c_gate", ROOT / "tools" / "ci_n2c_gate.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

UUID_A = "11111111-2222-3333-4444-555555555555"
UUID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SHA_A = "a" * 64
SHA_B = "b" * 64
COUNTERS = {"alpha": 3, "beta": 1142, "gamma_time_ns": 123456, "delta": 0}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def counters_sha(counters):
    return sha(json.dumps({k: v for k, v in sorted(counters.items()) if not k.endswith("_time_ns")}, sort_keys=True, default=repr))


def masked(text):
    text = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<UUID>", text)
    return re.sub(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", "<SHA64>", text)


def model_text(uuid, shav, faces="[1, 2, 3]"):
    return json.dumps({"faces": "@F@", "id": uuid, "digest": shav}).replace('"@F@"', faces)


def plan_text(uuid, tag="x"):
    return json.dumps({"plan": tag, "owner": uuid})


def make_out(folder, uuid=UUID_A, shav=SHA_A, counters=None, faces_out=7, threads="4", gil=False, numpy="2.4.6", model=None, plan=None):
    counters = dict(COUNTERS if counters is None else counters)
    out = Path(folder) / "out"
    out.mkdir(parents=True)
    (out / "prepared-model.json").write_text(model if model is not None else model_text(uuid, shav))
    (out / "plan.json").write_text(plan if plan is not None else plan_text(uuid))
    (out / "counters-all.json").write_text(json.dumps(counters))
    result = {"identity": {"counters_deterministic_sha256": counters_sha(counters), "working_faces_out": faces_out, "faces_in": 5},
              "versions": {"numpy": numpy}, "gil": gil, "threads_env": threads,
              "origins": {"anygeometry": str(Path(folder) / "geo" / "__init__.py"), "anyfem": str(Path(folder) / "fem" / "__init__.py")}}
    (out / "child-result.json").write_text(json.dumps(result))
    return Path(folder)


def constants(path):
    m, p = masked(model_text(UUID_A, SHA_A)), masked(plan_text(UUID_A))
    c = {"schema": 1,
         "prepared_model": {"masked_sha256": sha(m), "masked_chars": len(m)},
         "plan": {"masked_sha256": sha(p), "masked_chars": len(p)},
         "counters_deterministic_sha256": counters_sha(COUNTERS), "counters_nontime_keys": 3,
         "faces_in": 5, "working_faces_out": 7, "numpy_version": "2.4.6"}
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    out = path / "constants.json"
    out.write_text(json.dumps(c))
    return out


@pytest.fixture
def pair(tmp_path):
    serial = make_out(tmp_path / "s", threads=None, gil=True)
    on = make_out(tmp_path / "t", uuid=UUID_B, shav=SHA_B)   # different UUID/SHA values: masking must make them equal
    return serial, on, constants(tmp_path / "c")


def run(p):
    return gate.main([str(p[0]), str(p[1]), "--constants", str(p[2])])


def edit_result(folder, fn):
    p = Path(folder) / "out/child-result.json"
    d = json.loads(p.read_text())
    fn(d)
    p.write_text(json.dumps(d))


def test_identical_runs_pass_and_masking_ignores_uuid_and_sha(pair):
    assert run(pair) == 0


def test_masking_is_exactly_uuid_and_64hex():
    t = "x %s y %s z %s" % (UUID_A, SHA_A, "c" * 65)
    assert gate.mask(t) == "x <UUID> y <SHA64> z " + "c" * 65
    assert gate.mask("f" * 63) == "f" * 63


def test_changed_model_digest_is_a_finding_naming_the_group(pair, capsys):
    (pair[1] / "out/prepared-model.json").write_text(model_text(UUID_B, SHA_B, faces="[1, 2, 4]"))
    assert run(pair) == gate.EXIT_FINDING
    assert "prepared-model" in capsys.readouterr().out


def test_model_digest_changed_identically_in_both_runs_is_a_finding(pair, capsys):
    (pair[0] / "out/prepared-model.json").write_text(model_text(UUID_A, SHA_A, faces="[9]"))
    (pair[1] / "out/prepared-model.json").write_text(model_text(UUID_B, SHA_B, faces="[9]"))
    assert run(pair) == gate.EXIT_FINDING
    assert "serial: prepared-model" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["prepared-model.json", "plan.json"])
def test_malformed_json_in_model_or_plan_is_input_error_not_finding(pair, name):
    (pair[1] / "out" / name).write_text('{"truncated": ')
    assert run(pair) == gate.EXIT_INPUT


def test_origin_must_lie_under_the_checkout_in_both_runs(pair, tmp_path):
    args = lambda root: [str(pair[0]), str(pair[1]), "--constants", str(pair[2]), "--origin", "anygeometry=" + str(root)]
    assert gate.main(args(pair[0])) == gate.EXIT_FINDING        # serial's geo is under pair[0]; ON's is not
    stale = tmp_path / "elsewhere"
    assert gate.main(args(stale)) == gate.EXIT_FINDING
    for f in pair[:2]:                                          # point both runs at the same checkout
        edit_result(f, lambda d: d["origins"].update(anygeometry=str(tmp_path / "ck" / "src" / "anygeometry" / "__init__.py")))
    assert gate.main(args(tmp_path / "ck")) == 0
    assert gate.main([str(pair[0]), str(pair[1]), "--constants", str(pair[2]), "--origin", "nopkg=" + str(tmp_path / "ck")]) == gate.EXIT_FINDING
    assert gate.main([str(pair[0]), str(pair[1]), "--constants", str(pair[2]), "--origin", "garbage"]) == gate.EXIT_INPUT


def test_changed_plan_digest_is_a_finding(pair, capsys):
    (pair[0] / "out/plan.json").write_text(plan_text(UUID_A, "y"))
    (pair[1] / "out/plan.json").write_text(plan_text(UUID_B, "y"))
    assert run(pair) == gate.EXIT_FINDING
    assert "plan" in capsys.readouterr().out


def test_serial_vs_on_difference_is_a_finding(pair, capsys):
    (pair[1] / "out/plan.json").write_text(plan_text(UUID_B, "z"))
    assert run(pair) == gate.EXIT_FINDING
    assert "serial vs ON" in capsys.readouterr().out


def test_changed_counter_with_identical_digests_is_refresh_required(tmp_path, capsys):
    changed = dict(COUNTERS, beta=1143)
    serial = make_out(tmp_path / "s", counters=changed, threads=None, gil=True)
    on = make_out(tmp_path / "t", counters=changed, uuid=UUID_B, shav=SHA_B)
    assert gate.main([str(serial), str(on), "--constants", str(constants(tmp_path / "c"))]) == gate.EXIT_REFRESH
    assert "counters" in capsys.readouterr().out


def test_counter_difference_between_serial_and_on_is_a_finding(tmp_path):
    serial = make_out(tmp_path / "s", threads=None, gil=True)
    on = make_out(tmp_path / "t", counters=dict(COUNTERS, beta=1143), uuid=UUID_B, shav=SHA_B)
    assert gate.main([str(serial), str(on), "--constants", str(constants(tmp_path / "c"))]) == gate.EXIT_FINDING


def test_digest_finding_outranks_counter_refresh(tmp_path):
    changed = dict(COUNTERS, beta=1143)
    serial = make_out(tmp_path / "s", counters=changed, threads=None, gil=True, plan=plan_text(UUID_A, "y"))
    on = make_out(tmp_path / "t", counters=changed, uuid=UUID_B, shav=SHA_B, plan=plan_text(UUID_B, "y"))
    assert gate.main([str(serial), str(on), "--constants", str(constants(tmp_path / "c"))]) == gate.EXIT_FINDING


def test_time_counters_are_ignored(tmp_path):
    serial = make_out(tmp_path / "s", threads=None, gil=True)
    on = make_out(tmp_path / "t", counters=dict(COUNTERS, gamma_time_ns=999), uuid=UUID_B, shav=SHA_B)
    assert gate.main([str(serial), str(on), "--constants", str(constants(tmp_path / "c"))]) == 0


def test_child_counters_claim_must_match_the_counters_file(pair):
    edit_result(pair[1], lambda d: d["identity"].update(counters_deterministic_sha256="0" * 64))
    assert run(pair) == gate.EXIT_FINDING


@pytest.mark.parametrize("name", ["prepared-model.json", "plan.json", "counters-all.json", "child-result.json"])
@pytest.mark.parametrize("which", [0, 1])
def test_missing_file_fails_closed(pair, name, which):
    (pair[which] / "out" / name).unlink()
    assert run(pair) == gate.EXIT_INPUT


def test_empty_file_fails_closed(pair):
    (pair[1] / "out/plan.json").write_text("")
    assert run(pair) == gate.EXIT_INPUT


def test_malformed_child_result_fails_closed(pair):
    (pair[1] / "out/child-result.json").write_text("{not json")
    assert run(pair) == gate.EXIT_INPUT
    (pair[1] / "out/child-result.json").write_text("{}")
    assert run(pair) == gate.EXIT_INPUT


def test_missing_folder_fails_closed(pair, tmp_path):
    assert gate.main([str(tmp_path / "nope"), str(pair[1]), "--constants", str(pair[2])]) == gate.EXIT_INPUT


def test_missing_or_bad_constants_fail_closed(pair, tmp_path):
    assert gate.main([str(pair[0]), str(pair[1]), "--constants", str(tmp_path / "none.json")]) == gate.EXIT_INPUT
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert gate.main([str(pair[0]), str(pair[1]), "--constants", str(bad)]) == gate.EXIT_INPUT


def test_zero_faces_out_fails_even_when_constants_say_zero(tmp_path):
    c = json.loads(constants(tmp_path / "c").read_text())
    c["working_faces_out"] = 0
    (tmp_path / "c0.json").write_text(json.dumps(c))
    serial = make_out(tmp_path / "s", faces_out=0, threads=None, gil=True)
    on = make_out(tmp_path / "t", faces_out=0, uuid=UUID_B, shav=SHA_B)
    assert gate.main([str(serial), str(on), "--constants", str(tmp_path / "c0.json")]) != 0


@pytest.mark.parametrize("which", [0, 1])
def test_changed_face_count_is_a_finding(pair, which):
    edit_result(pair[0], lambda d: d["identity"].update(working_faces_out=8))
    edit_result(pair[1], lambda d: d["identity"].update(working_faces_out=8))   # both agree: only the frozen value can catch it
    assert run(pair) == gate.EXIT_FINDING


def test_changed_faces_in_is_a_finding(pair):
    for f in pair[:2]:
        edit_result(f, lambda d: d["identity"].update(faces_in=6))
    assert run(pair) == gate.EXIT_FINDING


def test_wrong_numpy_in_both_runs_is_a_finding(pair):
    for f in pair[:2]:
        edit_result(f, lambda d: d["versions"].update(numpy="2.5.0"))
    assert run(pair) == gate.EXIT_FINDING


@pytest.mark.parametrize("which", [0, 1])
def test_numpy_differing_between_runs_is_a_finding(pair, which):
    edit_result(pair[which], lambda d: d["versions"].update(numpy="2.5.0"))
    assert run(pair) == gate.EXIT_FINDING


def test_on_run_on_a_gil_build_is_refused(pair):
    edit_result(pair[1], lambda d: d.update(gil=True))
    assert run(pair) == gate.EXIT_FINDING


def test_on_run_without_threads_is_refused(pair):
    edit_result(pair[1], lambda d: d.update(threads_env=None))
    assert run(pair) == gate.EXIT_FINDING


def test_serial_run_with_threads_is_refused(pair):
    edit_result(pair[0], lambda d: d.update(threads_env="4"))
    assert run(pair) == gate.EXIT_FINDING


def test_same_folder_twice_is_refused(pair):
    assert gate.main([str(pair[0]), str(pair[0]), "--constants", str(pair[2])]) == gate.EXIT_INPUT


def test_committed_constants_are_well_formed():
    c = gate.load_constants(ROOT / "tools" / "n2c_identity_constants.json")
    assert c["prepared_model"]["masked_sha256"].startswith("d20cafe66636")
    assert c["plan"]["masked_sha256"].startswith("68ca1fb31c98")
    assert c["counters_deterministic_sha256"].startswith("70d74bd5e14cfff6")
    assert c["working_faces_out"] == 1269 and c["numpy_version"] == "2.4.6"


SAVED = Path(os.environ.get("ANYFEM_N2C_SAVED", "C:/ANYworkspaceWork/wt-identity/harness-5377460"))
HAVE_SAVED = (SAVED / "NC1-N-serial/out/plan.json").exists() and (SAVED / "NC4-T-4t/out/plan.json").exists()
LOCAL_ONLY = "saved 2026-11-13 N2c outputs not on this machine (local-only test, set ANYFEM_N2C_SAVED)"


@pytest.mark.skipif(not HAVE_SAVED, reason=LOCAL_ONLY)
def test_saved_2026_11_13_outputs_pass_the_committed_constants():
    c = ROOT / "tools" / "n2c_identity_constants.json"
    assert gate.main([str(SAVED / "NC1-N-serial"), str(SAVED / "NC4-T-4t"), "--constants", str(c)]) == 0


@pytest.mark.skipif(not HAVE_SAVED, reason=LOCAL_ONLY)
def test_saved_outputs_with_one_changed_byte_fail(tmp_path):
    s = tmp_path / "s"
    shutil.copytree(SAVED / "NC1-N-serial", s)
    p = s / "out/plan.json"
    p.write_bytes(p.read_bytes() + b" ")   # valid JSON, one byte longer
    c = ROOT / "tools" / "n2c_identity_constants.json"
    assert gate.main([str(s), str(SAVED / "NC4-T-4t"), "--constants", str(c)]) == gate.EXIT_FINDING


WORKFLOW = (ROOT / ".github/workflows/n2c-identity-nightly.yml").read_text(encoding="utf-8")


def test_workflow_is_nightly_or_manual_only_and_sha_pinned():
    on_block = WORKFLOW.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "schedule:" in on_block and "workflow_dispatch:" in on_block
    assert "push" not in on_block and "pull_request" not in on_block
    for line in WORKFLOW.splitlines():
        if "uses:" in line:
            assert re.search(r"uses: \S+@[0-9a-f]{40} # v", line), line
    assert "contents: read" in WORKFLOW


def test_workflow_never_interpolates_inputs_or_vars_into_scripts():
    allowed = re.compile(r"^\s*(- )?(if|ref|path|name|repository|N2C_\w+): .*\$\{\{|^\s*\$\{\{ runner\.temp \}\}/")
    for line in WORKFLOW.splitlines():
        if "${{" in line and not line.lstrip().startswith("#"):
            assert allowed.match(line), line
    assert "N2C_ENABLED" in WORKFLOW and "ci_n2c_gate.py" in WORKFLOW and "--constants tools/n2c_identity_constants.json" in WORKFLOW
    assert "--origin anygeometry=" in WORKFLOW and "--origin anyfem=" in WORKFLOW
