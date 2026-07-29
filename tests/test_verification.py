"""The verification suite and the parity ledger.

These test the machinery, not the physics: the physics is what the suite
itself checks, and it is run here in full so a regression in any analytical
case fails the build.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from anyfem import parity, verification


@pytest.fixture(scope="module")
def report():
    """One full run, shared: the cases are the slow part."""

    return verification.run_verification()


# ----------------------------------------------------------------------
# verification
# ----------------------------------------------------------------------
def test_every_verification_case_passes(report):
    failures = [
        f"{result.case_id}: {result.error or f'{result.relative_error:.3%} > {result.tolerance:.3%}'}"
        for result in report.results
        if not result.passed
    ]
    assert not failures, "verification failures: " + "; ".join(failures)
    assert report.passed


def test_the_suite_covers_every_analysis_family(report):
    prefixes = {result.case_id.split("-")[0] for result in report.results}
    assert {"GEOM", "MESH", "STAT", "LOAD", "MODE", "BUCK", "DYN"} <= prefixes


def test_every_case_states_what_it_is_checked_against():
    for case in verification.cases():
        assert case.reference, f"{case.case_id} has no reference"
        assert case.tolerance >= 0.0
        assert case.title


def test_case_ids_are_unique():
    ids = [case.case_id for case in verification.cases()]
    assert len(ids) == len(set(ids))


def test_the_report_records_its_environment(report):
    assert report.generated
    assert "anyfem" in report.environment
    assert "python" in report.environment
    assert "numpy" in report.environment


def test_a_failing_case_is_recorded_rather_than_raised():
    def explode():
        raise RuntimeError("no")

    case = verification.VerificationCase(
        "X-01", "Broken", "nothing", 0.01, "", explode
    )
    result = case.evaluate()
    assert not result.passed
    assert "RuntimeError: no" in result.error
    # The failure is carried into the evidence rather than swallowed.
    recorded = result.to_dict()
    assert recorded["status"] == "failed"
    assert recorded["error"] == result.error


def test_a_case_outside_tolerance_fails():
    result = verification.VerificationResult(
        case_id="X-02", title="off", reference="ref",
        computed=1.10, expected=1.0, tolerance=0.05,
    )
    assert result.relative_error == pytest.approx(0.10)
    assert not result.passed


def test_selecting_cases_runs_only_those():
    partial = verification.run_verification(["STAT-01"])
    assert [result.case_id for result in partial.results] == ["STAT-01"]


def test_the_report_writes_json_and_markdown(report):
    with tempfile.TemporaryDirectory() as directory:
        written = verification.write_verification_report(report, directory)
        assert written["json"].exists()
        assert written["markdown"].exists()

        data = json.loads(written["json"].read_text(encoding="utf-8"))
        assert data["status"] == "passed"
        assert len(data["results"]) == len(report.results)

        text = written["markdown"].read_text(encoding="utf-8")
        assert "# ANYfem verification" in text
        # It states the limits of what it claims.
        assert "do not claim" in text
        assert "ANYsolver" in text


def test_the_summary_says_pass_or_fail(report):
    assert "PASSED" in report.summary()
    assert str(report.counts["total"]) in report.summary()


# ----------------------------------------------------------------------
# parity ledger
# ----------------------------------------------------------------------
def test_every_ledger_entry_is_well_formed():
    for entry in parity.LEDGER:
        assert entry.area
        assert entry.capability
        assert entry.status in parity.STATUSES
        # Anything not fully covered has to say why.
        if entry.status != "covered":
            assert entry.note, f"{entry.capability} is {entry.status} with no note"


def test_ledger_capabilities_are_unique():
    names = [(entry.area, entry.capability) for entry in parity.LEDGER]
    assert len(names) == len(set(names))


def test_an_invalid_status_is_refused():
    with pytest.raises(ValueError, match="status must be one of"):
        parity.ParityEntry("Area", "Thing", "probably fine")


def test_the_summary_counts_add_up():
    summary = parity.parity_summary()
    counts = summary["counts"]
    assert sum(counts.values()) == summary["total"] == len(parity.LEDGER)
    assert 0.0 <= summary["coverage"] <= 1.0

    by_area = summary["by_area"]
    assert sum(
        sum(area.values()) for area in by_area.values()
    ) == summary["total"]


def test_the_gate_is_not_ready_and_says_what_blocks_it():
    gate = parity.gate_status()
    assert gate["ready"] is False
    assert gate["blocking"], "an open ledger must list what is open"
    assert gate["reason"]
    assert len(gate["criteria"]) == len(parity.GATE_CRITERIA)


def test_the_gate_excludes_only_what_it_names():
    gate = parity.gate_status()
    blocking = {entry["capability"] for entry in gate["blocking"]}
    for capability in parity.OUT_OF_SCOPE:
        assert capability not in blocking
    # Everything excluded must actually be in the ledger, so the exclusion
    # list cannot quietly hide something that was never tracked.
    tracked = {entry.capability for entry in parity.LEDGER}
    for capability in parity.OUT_OF_SCOPE:
        assert capability in tracked


def test_the_gate_would_clear_if_the_ledger_did(monkeypatch):
    """The gate reads the ledger rather than hard-coding an answer."""

    clear = tuple(
        parity.ParityEntry(entry.area, entry.capability, "covered", entry.note)
        for entry in parity.LEDGER
    )
    monkeypatch.setattr(parity, "LEDGER", clear)
    assert parity.gate_status()["ledger_clear"] is True


def test_the_known_gaps_are_recorded():
    """What ANYfem cannot fully do yet must still be visible in the ledger.

    Checked as "not covered" rather than specifically "missing", so a
    capability that moves from missing to partial stays tracked instead of
    quietly disappearing from the gaps.

    This list is expected to shrink.  When a phase closes one of these, move
    the name into :func:`test_the_delivered_capabilities_are_marked_covered`
    rather than deleting it -- a gap that closes should end up asserted from
    the other side, not merely unwatched.
    """

    open_items = {
        entry.capability.lower()
        for entry in parity.LEDGER
        if entry.status != "covered"
    }
    for expected in ("collision", "symmetry", "refinement"):
        assert any(expected in name for name in open_items), expected


def test_the_delivered_capabilities_are_marked_covered():
    """The other side of the gap list: what has actually been finished.

    Every name here was an open entry that a phase closed and verified.  If one
    of them ever slips back to partial, that is a regression in the product,
    not a bookkeeping change.
    """

    covered = {
        entry.capability.lower()
        for entry in parity.LEDGER
        if entry.status == "covered"
    }
    for expected in ("eccentricity", "fracture", "hardening curve"):
        assert any(expected in name for name in covered), expected


def test_partially_covered_capabilities_still_block_the_gate():
    gate = parity.gate_status()
    blocking = {entry["capability"] for entry in gate["blocking"]}
    partial = {
        entry.capability
        for entry in parity.LEDGER
        if entry.status == "partial"
        and entry.capability not in parity.OUT_OF_SCOPE
    }
    assert partial <= blocking


def test_the_ledger_writes_json_and_markdown():
    with tempfile.TemporaryDirectory() as directory:
        written = parity.write_parity_report(directory)
        assert written["json"].exists()

        data = json.loads(written["json"].read_text(encoding="utf-8"))
        assert data["gate"]["ready"] is False
        assert len(data["entries"]) == len(parity.LEDGER)

        text = written["markdown"].read_text(encoding="utf-8")
        assert "# ANYstructure parity ledger" in text
        assert "Migration gate" in text
        assert "176 options" in text


# ----------------------------------------------------------------------
# packaging
# ----------------------------------------------------------------------
def test_the_package_declares_its_entry_points():
    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'anyfem = "anyfem.ui.app:main"' in text
    assert 'anyfem-verify = "anyfem.verification:main"' in text
    assert 'anyfem-parity = "anyfem.parity:main"' in text


def test_the_viewport_is_an_optional_extra():
    """The modelling and solving layers must not need Tk."""

    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'gui = ["ANYtk3D' in text
    # ANYsolver is required; ANYtk3D is not.
    dependencies = text.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert "ANYsolver" in dependencies
    assert "ANYtk3D" not in dependencies


def test_the_headless_layers_do_not_import_tk():
    import ast

    root = Path(__file__).resolve().parents[1] / "src" / "anyfem"
    offenders = []
    for path in root.rglob("*.py"):
        if "ui" in path.relative_to(root).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name.split(".")[0] in ("tkinter", "anytk3d") for name in names):
                offenders.append(str(path.relative_to(root)))
    assert not offenders, f"headless modules importing a GUI library: {offenders}"


def test_anyfem_never_imports_anystructure():
    """The one-way dependency, checked rather than merely stated."""

    import ast

    root = Path(__file__).resolve().parents[1] / "src" / "anyfem"
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(
                name.split(".")[0] in ("anystruct", "ANYstructure")
                for name in names
            ):
                offenders.append(str(path.relative_to(root)))
    assert not offenders, f"ANYfem must never import ANYstructure: {offenders}"
