"""Phase 14: the migration gate.

Two things are checked here that are easy to get wrong in opposite directions.

The **state importer** must read ANYstructure's files without importing
ANYstructure — that one-way dependency is the whole architecture — and must
report what it cannot honour rather than dropping it.

The **gate** must stay closed while any criterion is unmet, and must never
report a criterion met because the evidence for it is missing. A gate that
opens when nobody supplies the numbers is worse than no gate, so there is a
test for exactly that.
"""

from __future__ import annotations

import gzip
import json
import sys
import tempfile
from pathlib import Path

import pytest

from anyfem import migration, parity
from anyfem.migration import (
    ComparisonCase,
    RuntimeState,
    StateFileError,
    compare_case,
    gate_markdown,
    gate_report,
    headless_model_report,
    read_runtime_fem_state,
)


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def state_payload(**options) -> dict:
    """A minimal file in the shape fem_integration writes."""

    base = {
        "mesh_size_m": 0.08,
        "mesh_fidelity": "fine",
        "shell_element_order": "S4",
        "beam_element_order": "B2",
        "pressure_pa": 120_000.0,
        "num_buckling_modes": 6,
        "axial_force_n": -2.5e6,
        "allow_unbalanced_free_free": False,
    }
    base.update(options)
    return {
        "format": migration.STATE_FORMAT,
        "saved_utc": "2026-07-30T00:00:00Z",
        "options": base,
        "snapshot": {"line_name": "L1", "domain": "hull", "is_cylinder": False},
        "result": {
            "status": "completed",
            "summary": {"elements": 4200},
            "diagnostics": ["mesh refined at supports"],
            "buckling_factors": [1.84, 2.31, 3.02],
            "stress_percentiles": [["p95", 2.4e8], ["max", 3.1e8]],
            "displacement_scale": 0.0123,
            "visualization": {"type": "plate"},
        },
    }


def write_state(path: Path, payload: dict) -> Path:
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write(json.dumps(payload))
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# the one-way dependency
# ----------------------------------------------------------------------
def in_clean_interpreter(script: str) -> str:
    """Run a script in a fresh interpreter and return its stdout.

    ``sys.modules`` is process-global, so asserting "X was never imported"
    inside the test suite only proves no *earlier test* imported it. A clean
    interpreter is the only honest way to ask the question.
    """

    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=300,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_reading_a_state_file_does_not_import_anystructure(workspace):
    """The architecture's central rule, checked where it could break."""

    path = write_state(workspace / "state.json", state_payload())
    loaded = in_clean_interpreter(
        "import sys; sys.path.insert(0, 'src')\n"
        "from anyfem.migration import read_runtime_fem_state\n"
        f"read_runtime_fem_state(r{str(path)!r})\n"
        "print([n for n in sys.modules if n.startswith('anystruct')])"
    )
    assert loaded == "[]"


def test_importing_anyfem_never_pulls_in_anystructure():
    loaded = in_clean_interpreter(
        "import sys; sys.path.insert(0, 'src')\n"
        "import anyfem, anyfem.migration, anyfem.parity\n"
        "print([n for n in sys.modules if n.startswith('anystruct')])"
    )
    assert loaded == "[]"


# ----------------------------------------------------------------------
# reading state files
# ----------------------------------------------------------------------
def test_a_plain_json_state_reads(workspace):
    path = write_state(workspace / "state.json", state_payload())
    state = read_runtime_fem_state(path)

    assert state.saved_utc == "2026-07-30T00:00:00Z"
    assert state.has_result
    assert state.status == "completed"
    assert state.buckling_factors == (1.84, 2.31, 3.02)
    assert state.stress_percentiles["p95"] == pytest.approx(2.4e8)


def test_a_gzipped_state_reads(workspace):
    """The writer gzips anything named .gz, so the reader has to as well."""

    path = write_state(workspace / "state.json.gz", state_payload())
    assert read_runtime_fem_state(path).buckling_factors


def test_a_missing_file_is_refused(workspace):
    with pytest.raises(StateFileError, match="no state file"):
        read_runtime_fem_state(workspace / "absent.json")


def test_a_file_with_the_wrong_format_tag_is_refused(workspace):
    payload = state_payload()
    payload["format"] = "something-else-v9"
    path = write_state(workspace / "wrong.json", payload)
    with pytest.raises(StateFileError, match="not an ANYstructure runtime FE state"):
        read_runtime_fem_state(path)


def test_a_file_without_options_is_refused(workspace):
    payload = state_payload()
    del payload["options"]
    path = write_state(workspace / "bare.json", payload)
    with pytest.raises(StateFileError, match="no options block"):
        read_runtime_fem_state(path)


def test_invalid_json_is_refused(workspace):
    path = workspace / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateFileError, match="not valid JSON"):
        read_runtime_fem_state(path)


def test_a_state_without_a_result_still_reads(workspace):
    payload = state_payload()
    del payload["result"]
    state = read_runtime_fem_state(write_state(workspace / "s.json", payload))

    assert not state.has_result
    assert state.buckling_factors == ()
    assert "settings only" in state.summary()


# ----------------------------------------------------------------------
# option mapping
# ----------------------------------------------------------------------
def test_options_are_sorted_into_mapped_unmapped_and_out_of_scope(workspace):
    state = read_runtime_fem_state(
        write_state(workspace / "s.json", state_payload())
    )

    assert "mesh_size_m" in state.mapped_options
    # A section resultant: out of scope by decision, not outstanding work.
    assert "axial_force_n" in state.out_of_scope_options
    assert "axial_force_n" not in state.unmapped_options
    # A solver internal ANYfem does not surface: outstanding, not excluded.
    assert "allow_unbalanced_free_free" in state.unmapped_options

    everything = (
        set(state.mapped_options)
        | set(state.unmapped_options)
        | set(state.out_of_scope_options)
    )
    assert everything == set(state.options)


def test_the_out_of_scope_options_say_why():
    for name, reason in migration._OUT_OF_SCOPE_OPTIONS.items():
        assert reason, f"{name} is excluded with no reason"


def test_the_element_order_maps_from_both_member_types(workspace):
    for shell, beam, expected in (
        ("S4", "B2", "linear"),
        ("S8", "B3", "quadratic"),
    ):
        state = read_runtime_fem_state(
            write_state(
                workspace / f"{shell}{beam}.json",
                state_payload(shell_element_order=shell, beam_element_order=beam),
            )
        )
        assert state.element_order == expected


def test_disagreeing_element_orders_give_no_answer(workspace):
    """S8 shells with B2 beams is a mesh ANYfem cannot build."""

    state = read_runtime_fem_state(
        write_state(
            workspace / "mixed.json",
            state_payload(shell_element_order="S8", beam_element_order="B2"),
        )
    )
    assert state.element_order is None


def test_a_mesh_size_of_zero_pins_nothing(workspace):
    """A named fidelity band is not a number, and one is not invented."""

    state = read_runtime_fem_state(
        write_state(workspace / "s.json", state_payload(mesh_size_m=0.0))
    )
    assert state.target_size is None

    state = read_runtime_fem_state(
        write_state(workspace / "t.json", state_payload(mesh_size_m=0.05))
    )
    assert state.target_size == pytest.approx(0.05)


# ----------------------------------------------------------------------
# the headless seam
# ----------------------------------------------------------------------
def test_the_headless_api_builds_every_model_type():
    """Gate criterion 4, and the property the whole migration rests on."""

    report = headless_model_report()

    assert report["ok"], report
    assert set(report["models"]) == {"stiffened panel", "cylinder"}
    for name, entry in report["models"].items():
        assert entry["nodes"] > 0, name
        assert entry["elements"] > 0, name
        assert entry["max_translation_m"] > 0.0, name
        # Timings are recorded so a regression is measurable later.
        assert entry["mesh_seconds"] >= 0.0
        assert entry["solve_seconds"] >= 0.0


def test_the_headless_models_need_no_tk():
    """"Headless" has to mean it in a fresh process, not just in this one."""

    loaded = in_clean_interpreter(
        "import sys; sys.path.insert(0, 'src')\n"
        "from anyfem.migration import headless_model_report\n"
        "report = headless_model_report()\n"
        "assert report['ok'], report\n"
        "print('tkinter' in sys.modules)"
    )
    assert loaded == "False"


# ----------------------------------------------------------------------
# the comparison harness
# ----------------------------------------------------------------------
def test_a_comparison_case_that_matches_passes():
    case = ComparisonCase(
        name="ok", build=lambda: {"critical": 1.84}, expected={"critical": 1.85},
        tolerance=0.02,
    )
    result = compare_case(case)
    assert result.passed
    assert result.deviations["critical"] < 0.02


def test_a_comparison_case_outside_tolerance_fails():
    case = ComparisonCase(
        name="off", build=lambda: {"critical": 1.20}, expected={"critical": 1.85},
        tolerance=0.05,
    )
    assert not compare_case(case).passed


def test_a_comparison_case_that_raises_is_recorded_not_swallowed():
    def explode():
        raise RuntimeError("model would not build")

    result = compare_case(
        ComparisonCase(name="broken", build=explode, expected={"x": 1.0})
    )
    assert not result.passed
    assert "RuntimeError: model would not build" in result.error


def test_a_quantity_the_model_did_not_produce_fails_rather_than_passing():
    """Silence is not agreement."""

    result = compare_case(
        ComparisonCase(
            name="partial", build=lambda: {"a": 1.0},
            expected={"a": 1.0, "b": 2.0},
        )
    )
    assert result.missing == ["b"]
    assert not result.passed


def test_the_comparison_set_is_empty_and_that_is_recorded():
    """The honest state of it: the harness exists, the numbers do not."""

    assert migration.COMPARISON_CASES == ()


# ----------------------------------------------------------------------
# the gate
# ----------------------------------------------------------------------
def test_the_gate_is_not_ready_and_says_which_criteria_are_unmet():
    report = gate_report()

    assert report["ready"] is False
    assert report["unmet"]
    for name in report["unmet"]:
        assert report["criteria"][name]["met"] is False
        assert report["criteria"][name]["detail"], name


def test_a_criterion_without_evidence_is_unmet_not_passed():
    """The failure mode worth guarding: a gate that opens on missing data."""

    report = gate_report()
    comparison = report["criteria"]["analysis paths reproduced on recorded models"]
    performance = report["criteria"][
        "no performance regression on representative models"
    ]

    assert comparison["met"] is False
    assert "no recorded" in comparison["detail"]
    assert performance["met"] is False
    assert "cannot be answered" in performance["detail"]


def test_the_criteria_ANYfem_can_meet_are_met():
    report = gate_report()

    assert report["criteria"]["save_runtime_fem_state files importable"]["met"]
    assert report["criteria"]["headless API builds every model type"]["met"]
    # The ledger closed with Phase 14; the gate reads it rather than asserting.
    assert report["criteria"][
        "parity ledger clear outside ANYstructure's own domain"
    ]["met"]


def test_the_gate_reports_the_files_it_was_given(workspace):
    good = write_state(workspace / "good.json", state_payload())
    bad = workspace / "bad.json"
    bad.write_text("{}", encoding="utf-8")

    report = gate_report([good, bad])
    entries = {Path(item["source"]).name: item for item in report["states"]}

    assert entries["good.json"]["ok"]
    assert not entries["bad.json"]["ok"]
    assert "format tag" in entries["bad.json"]["error"]


def test_the_gate_markdown_states_its_own_rule():
    text = gate_markdown()

    assert "# ANYfem migration gate" in text
    assert "ready: **no**" in text
    assert "never passed by default" in text


def test_the_gate_report_writes_its_evidence(workspace):
    written = migration.write_gate_report(directory=workspace / "out")

    assert written["json"].exists()
    assert written["markdown"].exists()
    data = json.loads(written["json"].read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["criteria"]


# ----------------------------------------------------------------------
# the ledger, now that the last entry closed
# ----------------------------------------------------------------------
def test_the_parity_ledger_has_no_blocking_entries_left():
    gate = parity.gate_status()

    assert gate["ledger_clear"] is True
    assert gate["blocking"] == []


def test_section_resultants_are_excluded_by_name_not_by_omission():
    """Out of scope has to be a decision on the record, not a quiet gap."""

    assert (
        "Axial force, moment and shear resultants on a section"
        in parity.OUT_OF_SCOPE
    )
    tracked = {entry.capability for entry in parity.LEDGER}
    assert "Axial force, moment and shear resultants on a section" in tracked


def test_the_migration_is_still_not_ready_overall():
    """The ledger being clear is one criterion of five, not the whole gate."""

    assert parity.gate_status()["ledger_clear"] is True
    assert gate_report()["ready"] is False
