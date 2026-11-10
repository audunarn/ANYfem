"""Tests of the S6 shell assessment input builder (development candidate, not frozen).

Part 1 needs no solver: aggregation, grading, thresholds, inventory and CLI behaviour.
Part 2 runs the real cases through the public ANYsolver API and therefore needs the
installed ``anysolver``.  It is deliberately NOT skipped when the solver is missing: an
import failure is a test failure, not a masked pass.  A numerical failure there is
assessment evidence about the candidate, not a reason to loosen a check.

The module is loaded by file path (no ``sys.path`` edits).
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

_PATH = Path(__file__).resolve().parent / "shell_assessment.py"
_spec = importlib.util.spec_from_file_location("s6_shell_assessment_under_test", _PATH)
sa = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sa
_spec.loader.exec_module(sa)


# ----------------------------------------------------------------------------------------
# part 1: pure logic
# ----------------------------------------------------------------------------------------
def chk(status, cid="x"):
    return sa.Check(cid, "m", 0.0, 1.0, "actual_allowance", status)


def test_ratio_zero_allowance_conventions():
    assert sa.ratio(0.0, 0.0) == 0.0
    assert sa.ratio(1e-30, 0.0) == math.inf
    assert sa.ratio(math.nan, 1.0) == math.inf
    assert sa.ratio(2.0, 4.0) == 0.5
    assert sa.ratio(-2.0, 4.0) == 0.5


def test_worst_ratio_names_the_sensor_and_reports_bounds():
    w = sa.worst_ratio([1.0, -3.0, 0.5], [2.0, 2.0, 1.0], ["a", "b", "c"])
    assert w["sensor"] == "b" and w["ratio"] == 1.5 and w["count"] == 3
    assert w["max_abs_error"] == 3.0 and w["max_allowance"] == 2.0
    assert sa.worst_ratio([], [], [])["count"] == 0
    assert sa.worst_ratio([1.0], [1.0, 2.0], ["a"])["count"] == 0     # shape mismatch measures nothing


def test_ratio_check_status_rules():
    ok = sa.worst_ratio([1.0], [2.0], ["a"])
    bad = sa.worst_ratio([3.0], [2.0], ["a"])
    edge = sa.worst_ratio([2.0], [2.0], ["a"])
    assert sa.ratio_check("c", "m", ok).status == sa.PASS
    assert sa.ratio_check("c", "m", edge).status == sa.PASS             # ratio == 1 is within the allowance
    assert sa.ratio_check("c", "m", bad).status == sa.FAIL
    assert sa.ratio_check("c", "m", ok, inconclusive=True).status == sa.INCONCLUSIVE
    assert sa.ratio_check("c", "m", bad, inconclusive=True).status == sa.INCONCLUSIVE   # plan: neither passed nor failed
    assert sa.ratio_check("c", "m", bad, graded=False).status == sa.REPORTED
    assert sa.ratio_check("c", "m", sa.worst_ratio([], [], [])).status == sa.INCONCLUSIVE
    c = sa.ratio_check("c", "m", ok)
    assert c.threshold == sa.REGISTERED["ratio_max"] and c.threshold_source == "actual_allowance"


def test_grade_aggregation_matrix():
    P, F, I, R = sa.PASS, sa.FAIL, sa.INCONCLUSIVE, sa.REPORTED
    g = lambda *s, **kw: sa.grade_checks([chk(x) for x in s], **kw)       # noqa: E731
    assert g(P, P, R) == "pass"
    assert g(P, F, I) == "failed"                      # a failure outranks inconclusive
    assert g(P, I) == "inconclusive"
    assert g(R, R) == "inconclusive"                   # nothing graded is never a pass
    assert g() == "inconclusive"
    assert g(P, pending=1) == "inconclusive"           # external rows not yet executed
    assert g(P, unavailable=1) == "inconclusive"       # genuinely unavailable rows are not masked
    assert g(F, pending=3, unavailable=2) == "failed"
    assert sa.EXIT_CODES["pass"] == 0 and sa.EXIT_CODES["failed"] == 1 and sa.EXIT_CODES["inconclusive"] == 2


def test_expect_code_distinguishes_wrong_code_and_no_exception():
    class SubmodelTransferError(ValueError):
        def __init__(self, code):
            super().__init__(code)
            self.code = code

    def raiser(code):
        def fn():
            raise SubmodelTransferError(code)
        return fn

    assert sa.expect_code("a", raiser("X"), "X").status == sa.PASS
    assert sa.expect_code("a", raiser("Y"), "X").status == sa.FAIL
    assert sa.expect_code("a", lambda: None, "X").status == sa.FAIL
    assert sa.expect_code("a", lambda: (_ for _ in ()).throw(KeyError("k")), "X").status == sa.FAIL


def test_observed_orders_and_dispositions():
    orders = sa.observed_orders([0.25, 0.125, 0.0625], [1e-2, 2.5e-3, 6.25e-4])
    assert orders == pytest.approx([2.0, 2.0])
    assert sa.observed_orders([0.25, 0.125], [0.0, 1.0]) == [None]
    assert sa.observed_orders([0.25, 0.125], [1.0, math.nan]) == [None]
    assert sa.metric_disposition(1.0, 1.0 + 1e-12, 1e-9) == "tie"
    assert sa.metric_disposition(1.0, 2.0, 1e-9) == "P1_preferred"
    assert sa.metric_disposition(3.0, 2.0, 1e-9) == "P2_preferred"
    assert sa.metric_disposition(math.nan, 2.0, 1e-9) == "inconclusive"
    assert sa.overall_disposition({"a": "P1_preferred", "b": "tie"}) == "P1_preferred"
    assert sa.overall_disposition({"a": "P1_preferred", "b": "P2_preferred"}) == "inconclusive"
    assert sa.overall_disposition({"a": "tie", "b": "tie"}) == "inconclusive"      # ties never pick a default
    assert sa.overall_disposition({"a": "P2_preferred", "b": "inconclusive"}) == "inconclusive"


def test_clean_makes_json_strict():
    data = {"a": np.float64(1.5), "b": np.array([1, math.inf, math.nan]), "c": (np.int64(3), np.bool_(True)),
            "d": sa.Check("i", "m", -math.inf, 1.0, "none", sa.REPORTED)}
    text = json.dumps(sa.clean(data), allow_nan=False)
    back = json.loads(text)
    assert back["b"] == [1, "inf", "nan"] and back["c"] == [3, True] and back["d"]["value"] == "-inf"


def test_inventory_is_complete_and_consistent():
    inv = sa.inventory()
    json.dumps(inv, allow_nan=False)
    ids = [c["id"] for c in inv["cases"]]
    assert len(ids) == len(set(ids)) == len(sa.CASES)
    stages = {c["stage"] for c in inv["cases"]}
    assert stages == {"A0", "A1", "A2", "A7"}
    for q in ("Q4", "S3"):
        assert {f"A0-INTERP-{q}", f"A0-MPC-{q}", f"A1-{q}"} <= set(ids)
    for s in ("Q4", "S3"):
        for t in ("Q4", "S3"):
            assert {f"A2-EXACT-{s}-{t}", f"A2-BEND-{s}-{t}"} <= set(ids)       # the four registered pairings
    for c in inv["cases"]:
        assert c["solve_ceiling"] >= 0 and c["wall_ceiling_s"] > 0 and c["checks"] and c["public_apis"]
    assert inv["registered"]["ratio_max"] == 1.0
    assert {r["id"] for r in inv["a7_pending_external"]} and {r["id"] for r in inv["a7_unavailable"]}
    assert set(inv["not_in_scope"]) == {"A3", "A4", "A5", "A6", "A8"}
    bend = inv["registered"]["a2_bend"]
    assert bend["parent_sizes_m"] == [0.25, 0.125, 0.0625, 0.03125] and bend["child_size_m"] == 0.025


def test_a7_external_rows_are_listed_not_masked():
    assert any("empty_volume" in c for *_x, c in sa.A7_EXTERNAL)
    assert any("clipped_distributed_load" in c for *_x, c in sa.A7_EXTERNAL)
    assert {u[0] for u in sa.A7_UNAVAILABLE} >= {"A7-U01", "A7-U02"}


def test_output_path_rules(tmp_path):
    new = tmp_path / "r.json"
    assert sa.output_path_error(new) is None
    new.write_text("{}")
    assert "already exists" in sa.output_path_error(new)
    assert "does not exist" in sa.output_path_error(tmp_path / "missing" / "r.json")
    for name in sa.PROTECTED_DIRS:
        assert "historical evidence" in sa.output_path_error(sa.HANDOFF_ROOT / name / "results.json") or \
            "does not exist" in sa.output_path_error(sa.HANDOFF_ROOT / name / "results.json")


def _stub(monkeypatch, runner, case_id="STUB", ceiling=3):
    spec = sa.CaseSpec(case_id, "A0", "stub", runner, ceiling, 10.0, (("stub.*", "m", "t"),), ("api",), ())
    monkeypatch.setitem(sa.CASES, case_id, spec)
    return case_id


def _outcome(*statuses):
    o = sa.CaseOutcome()
    o.checks = [chk(s, f"stub.{k}") for k, s in enumerate(statuses)]
    return o


@pytest.mark.parametrize("statuses,code,grade", [((sa.PASS, sa.REPORTED), 0, "pass"), ((sa.PASS, sa.FAIL), 1, "failed"),
                                                 ((sa.PASS, sa.INCONCLUSIVE), 2, "inconclusive"), ((sa.REPORTED,), 2, "inconclusive")])
def test_cli_exit_codes_and_exclusive_output(monkeypatch, tmp_path, capsys, statuses, code, grade):
    case = _stub(monkeypatch, lambda log: _outcome(*statuses))
    out = tmp_path / "result.json"
    assert sa.main(["--case", case, "--output", str(out)]) == code
    data = json.loads(out.read_text())
    assert data["grade"] == grade and data["exit_code"] == code and data["schema"] == sa.SCHEMA
    assert data["undeclared_check_ids"] == [] and data["counts"]["solves"] == 0
    assert {"candidate", "timing", "registered", "ceilings", "digest_sha256_of_deterministic_part"} <= set(data)
    before = out.read_text()
    assert sa.main(["--case", case, "--output", str(out)]) == sa.EXIT_CODES["invocation_error"]
    assert out.read_text() == before                      # an existing result is never overwritten


def test_cli_preserves_harness_failure_and_ceiling(monkeypatch, tmp_path):
    def boom(log):
        raise RuntimeError("fixture broke")

    case = _stub(monkeypatch, boom, "STUB_BOOM")
    out = tmp_path / "boom.json"
    assert sa.main(["--case", case, "--output", str(out)]) == sa.EXIT_CODES["harness_error"]
    data = json.loads(out.read_text())
    assert data["error"]["type"] == "RuntimeError" and "fixture broke" in data["error"]["traceback"]

    def ceiling(log):
        raise sa.ResourceCeiling("solve ceiling 3 reached")

    case = _stub(monkeypatch, ceiling, "STUB_CEIL")
    out = tmp_path / "ceil.json"
    assert sa.main(["--case", case, "--output", str(out)]) == sa.EXIT_CODES["inconclusive"]
    assert json.loads(out.read_text())["error"]["type"] == "ResourceCeiling"


def test_cli_flags_undeclared_checks(monkeypatch, tmp_path):
    def runner(log):
        o = sa.CaseOutcome()
        o.checks = [chk(sa.PASS, "other.prefix")]
        return o

    case = _stub(monkeypatch, runner, "STUB_UNDECLARED")
    out = tmp_path / "u.json"
    assert sa.main(["--case", case, "--output", str(out)]) == sa.EXIT_CODES["failed"]
    assert json.loads(out.read_text())["undeclared_check_ids"] == ["other.prefix"]


def test_cli_requires_output_and_prints_inventory(tmp_path, capsys):
    assert sa.main(["--case", "A7-SOLVER"]) == sa.EXIT_CODES["invocation_error"]
    capsys.readouterr()
    assert sa.main(["--inventory"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "inventory"


def test_call_log_enforces_solve_ceiling_before_solving():
    log = sa.CallLog(solve_ceiling=0, wall_ceiling_s=10.0)
    with pytest.raises(sa.ResourceCeiling):
        log.solve("x", None, None)           # guarded before the model is touched
    wall = sa.CallLog(solve_ceiling=5, wall_ceiling_s=-1.0)
    with pytest.raises(sa.ResourceCeiling):
        wall.trace(None, {})


# ----------------------------------------------------------------------------------------
# part 2: real cases through the public ANYsolver API (not skipped; failures are evidence)
# ----------------------------------------------------------------------------------------
def _run(case_id):
    result = sa.run_case(case_id)
    assert result["error"] is None, result["error"]
    assert result["undeclared_check_ids"] == []
    return result


def _bad(result):
    return [(c["id"], c["value"], c["sensor"]) for c in result["checks"] if c["status"] == "fail"]


@pytest.mark.parametrize("case_id", ["A0-INTERP-Q4", "A0-INTERP-S3", "A0-MPC-Q4", "A0-MPC-S3"])
def test_a0_cases_pass_with_actual_allowances(case_id):
    result = _run(case_id)
    assert _bad(result) == []
    assert result["grade"] == "pass"
    assert all(c["threshold"] == 1.0 for c in result["checks"] if c["threshold_source"] == "actual_allowance")


def test_a0_is_deterministic():
    a, b = _run("A0-INTERP-Q4"), _run("A0-INTERP-Q4")
    assert a["digest_sha256_of_deterministic_part"] == b["digest_sha256_of_deterministic_part"]


@pytest.mark.parametrize("case_id", ["A1-Q4", "A1-S3"])
def test_a1_graded_p2_checks_pass_and_p1_is_reported(case_id):
    result = _run(case_id)
    assert _bad(result) == []
    assert result["counts"]["solves"] == 4 and result["counts"]["interface_force_evaluations"] == 3
    ids = {c["id"]: c for c in result["checks"]}
    kind = case_id.split("-")[1]
    for group in ("face_south", "face_east", "face_north", "face_west", "corner_sw", "corner_se", "corner_ne", "corner_nw"):
        assert f"A1.{kind}.newton.{group}" in ids and f"A1.{kind}.P2.oracle.{group}" in ids
    assert all(c["status"] == "reported" for i, c in ids.items() if i.startswith(f"A1.{kind}.P1."))
    assert ids[f"A1.{kind}.P2.closed_cut_total"]["status"] == "reported"


@pytest.mark.parametrize("src,tgt", [("Q4", "Q4"), ("Q4", "S3"), ("S3", "Q4"), ("S3", "S3")])
def test_a2_exact_matrix(src, tgt):
    result = _run(f"A2-EXACT-{src}-{tgt}")
    assert _bad(result) == []
    graded = [c for c in result["checks"] if c["threshold_source"] == "actual_allowance"]
    fields = {"tension", "affine_membrane_rotation", "rigid"}
    for f in fields:
        for pol in ("P1", "P2"):
            assert any(c["id"] == f"A2.exact.{src}to{tgt}.{f}.{pol}.field" for c in graded)
    assert any(c["id"].endswith("zero_interface_force") for c in graded)


def test_a2_bend_smoke_small_configuration_and_moment_sign():
    S = sa._sdk()                                           # noqa: F841 - import failure must fail the test
    log = sa.CallLog(solve_ceiling=40, wall_ceiling_s=600.0)
    out = sa.run_a2_bend("Q4", "S3", log, parent_sizes=(4, 8), child_n=10)
    assert out.data["registered_configuration"] is False
    assert any("NON-REGISTERED" in n for n in out.notes)
    bnd = [c for c in out.checks if c.id.endswith("boundary_equals_interpolation")]
    assert {c.id.split(".")[3] for c in bnd} == {"physical_parent", "sampled_analytic"}
    assert all(c.status != sa.FAIL for c in bnd), [(c.id, c.value, c.sensor) for c in bnd]
    parent = {c.id: c for c in out.checks if ".physical_parent.error." in c.id}
    err = parent["A2.bend.Q4.physical_parent.error.uz"].detail["errors"][-1]
    assert err < 0.1 * 0.5 * sa.KAPPA, "parent pure-moment solution does not follow w = k x^2/2 (moment sign or load convention)"
    assert any(c.id.endswith("d3_disposition") for c in out.checks)
    assert {u["id"] for u in out.informational_unavailable} >= {"A2.bend.translations_only_control", "A2.bend.d3_energy_split"}


def test_a7_runs_solver_rows_and_never_passes_with_external_rows_pending():
    result = _run("A7-SOLVER")
    assert _bad(result) == []
    assert result["grade"] == "inconclusive"
    assert result["pending_external"] and result["unavailable"]
    codes = {c["threshold"] for c in result["checks"] if c["threshold_source"] == "expected_code"}
    assert {"TRACE_INCONSISTENT", "TRACE_RANK_AMBIGUOUS", "TRACE_JUNCTION_ILL_CONDITIONED", "TRACE_MODEL_MISMATCH",
            "TRACE_MODEL_CHANGED", "TRACE_VALUES", "FORCE_UNCERTIFIED", "FORCE_EQUILIBRIUM",
            "ALLOWANCE_RECONSTRUCTION"} <= codes
