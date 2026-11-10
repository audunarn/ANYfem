"""Offline tests of ``reference_ccx`` (no CCX, no subprocess, no files outside ``tmp_path``).

NOT RUN by the author (no execution available).  The coordinator runs them on the recorded
dependencies.  What they prove is limited to the arithmetic, deck structure and parsers of this
module; they say nothing about what CCX 2.22 does with the decks (that is the qualification run).

The DAT fragments below are hand-written parser fixtures, NOT CalculiX output, and no result is derived from them.

Run: ``python docs/handoff/submodelling-20261009/s4a/run_tests.py docs/handoff/submodelling-20261009/s6/test_reference_ccx.py --basetemp=<writable dir>``
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[3] / "tests"))

import reference_ccx as R  # noqa: E402

# The real A5 section: Flatbar flange_width 0.08 (in the plate, local y) x flange_thickness 0.01 (along the plate normal, local z);
# offset 0.005 is the project's explicit vector (0, 0, 0.005), NOT derived from either dimension.
SPEC = R.StiffenedSpec(E=2.1e11, nu=0.3, plate_t=0.01, pressure=1.0e4, bar_width=0.08, bar_height=0.01, offset=0.005)
EPS = float(np.finfo(float).eps)

DAT_SAMPLE = """
 displacements (vx,vy,vz) for set PROBES and time  0.1000000E+01

         5  1.000000E-06 2.000000E-06 -3.000000E-06
         9  0.000000E+00 1.000000E-05 4.000000E-06

 forces (fx,fy,fz) for set SUPPORT and time  0.1000000E+01

         1 -1.000000E+01  0.000000E+00  5.000000E+00
"""


def lines_of(text, header):
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(header))
    out = []
    for l in lines[start + 1:]:
        if l.startswith("*"):
            break
        out.append(l)
    return out


def cload(text):
    out = {}
    for l in lines_of(text, "*CLOAD"):
        node, dof, value = (f.strip() for f in l.split(","))
        out[(int(node), int(dof))] = float(value)
    return out


def block(kind, set_name, time, rows):
    return R.DatBlock(kind, (), set_name, time, rows)


# ----------------------------------------------------------------------
# gauge, parsers, convergence arithmetic
# ----------------------------------------------------------------------
def test_gauge_recovers_an_imposed_rigid_rotation_to_a_forward_error_bound():
    result = R.rigid_gauge_selftest()
    assert result["abs_error"] <= result["error_bound"]
    # the bound is far below the physical scale, so no physical tolerance is loosened by it
    assert result["error_bound"] <= 1e-9 * abs(result["expected_rx"])
    assert result["error_bound"] >= EPS * abs(result["expected_rx"])  # a bound that cannot be zero


def test_gauge_uses_the_two_actual_nodes_and_refuses_a_zero_offset():
    g = R.link_rotation_x((0.0, 1.0e-3, 0.0), (0.0, 1.0e-3 - 5.0e-6, 0.0), 0.005)
    assert g["rx"] == pytest.approx(1.0e-3, rel=1e-12)  # uy_b - uy_p = -e rx
    with pytest.raises(ValueError):
        R.link_rotation_x((0, 0, 0), (0, 0, 0), 0.0)


def test_dat_parser_reads_blocks_and_keeps_missing_sets_loud():
    blocks = R.parse_dat(DAT_SAMPLE)
    assert [b.kind for b in blocks] == ["displacements", "forces"]
    assert R.displacements(blocks, "probes")[9] == (0.0, 1.0e-5, 4.0e-6)
    assert R.reactions(blocks, "SUPPORT")[1] == (-10.0, 0.0, 5.0)
    assert R.reactions_at(blocks, "SUPPORT", 1.0)[1][2] == 5.0
    with pytest.raises(KeyError):
        R.displacements(blocks, "NOPE")


def test_displacements_at_selects_the_step_by_time():
    blocks = [block("displacements", "TIP", 1.0, [(11, 0.0, 0.0, -1.0)]), block("displacements", "TIP", 2.0, [(11, 0.0, -2.0, 0.0)])]
    assert R.displacements_at(blocks, "tip", 1.0)[11][2] == -1.0 and R.displacements_at(blocks, "tip", 2.0)[11][1] == -2.0
    with pytest.raises(KeyError):
        R.displacements_at(blocks, "tip", 3.0)


def test_convergence_reports_order_and_refuses_to_extrapolate_bad_data():
    f = lambda h: 1.0 + 3.0 * h ** 2  # noqa: E731
    good = R.reference_convergence([(h, f(h)) for h in (0.1, 0.05, 0.025, 0.0125)])
    assert good["status"] == "monotone" and good["order"] == pytest.approx(2.0, abs=1e-9)
    assert good["estimate"] == pytest.approx(1.0, abs=1e-12)
    wiggle = R.reference_convergence([(0.05, 1.0), (0.025, 1.2), (0.0125, 1.1)])
    assert wiggle["status"] == "non_monotone_or_stalled" and wiggle["estimate"] is None
    diverging = R.reference_convergence([(0.05, 1.0), (0.025, 1.1), (0.0125, 1.3)])
    assert diverging["estimate"] is None and diverging["status"] == "not_converging_in_asymptotic_range"
    uneven = R.reference_convergence([(0.05, 1.0), (0.03, 1.1), (0.0125, 1.15)])
    assert uneven["status"] == "refinement_ratio_not_constant"
    with pytest.raises(ValueError):
        R.reference_convergence([(0.05, 1.0), (0.025, 1.1)])


def test_a5_report_keeps_each_quantity_separate():
    per = {n: {"w_center": 1.0 + 3 / n ** 2, "w_stiffener": 2.0 + 5 / n ** 2, "rx": 0.5 + 7 / n ** 2} for n in (20, 40, 80)}
    rep = R.a5_report(per)
    assert set(rep["quantities"]) == {"w_center", "w_stiffener", "rx"} and rep["h"] == [1 / 20, 1 / 40, 1 / 80]
    assert rep["quantities"]["rx"]["estimate"] == pytest.approx(0.5, abs=1e-12)


def test_original_run_sensors_are_the_frozen_a5_points():
    # s4b/test_a5_budget.py POINTS: (0.5, 0.5) comp 2, (0.5, 0.3) comp 2, (0.5, 0.3) comp 3 (shell rotation rx)
    assert R.A5_ORIGINAL_QUANTITIES == {"w_centre": ("w_center", (0.5, 0.5), 2), "w_stiffener": ("w_stiffener", (0.5, 0.3), 2),
                                        "rx_stiffener": ("rx", (0.5, 0.3), 3)}


# ----------------------------------------------------------------------
# A5 deck structure, ids, loads
# ----------------------------------------------------------------------
def test_a5_deck_structure_n10():
    text = R.a5_deck(SPEC, 10)
    shells = lines_of(text, "*ELEMENT, TYPE=S8R")
    assert len([l for l in shells if l.strip()]) == 100
    assert len(lines_of(text, "*ELEMENT, TYPE=B31, ELSET=STIFX")) == 10
    assert len(lines_of(text, "*ELEMENT, TYPE=B31, ELSET=STIFY")) == 10
    beam_nodes = lines_of(text, "*NODE, NSET=NBEAM")
    assert len(beam_nodes) == 11 + 11 - 1  # two lines of 11 stations sharing the crossing node
    assert "*DLOAD" not in text and "P1" not in text and "P2" not in text  # no unmeasured pressure label
    assert "*NODE FILE" not in text


def test_a5_beam_nodes_stand_off_by_the_offset_and_share_the_crossing():
    text = R.a5_deck(SPEC, 10)
    rows = [l.split(",") for l in lines_of(text, "*NODE, NSET=NBEAM")]
    assert all(float(r[3]) == SPEC.offset for r in rows)
    ids = [int(r[0]) for r in rows]
    assert len(ids) == len(set(ids))
    crossing = [r for r in rows if abs(float(r[1]) - 0.7) < 1e-12 and abs(float(r[2]) - 0.3) < 1e-12]
    assert len(crossing) == 1


def test_a5_ids_are_dense_at_every_level_and_the_old_sparse_ids_are_gone():
    for n in R.DEFAULT_LEVELS:
        lattice = R.a5_beam_lattice(SPEC, n)
        plate_count = (2 * n + 1) ** 2 - n ** 2
        assert min(lattice.values()) == (2 * n + 1) ** 2 + 1
        assert max(lattice.values()) <= 2 * (plate_count + len(lattice))  # nothing near 10^5-10^6 for a few probes
        assert len(set(lattice.values())) == len(lattice)
    text = R.a5_deck(SPEC, 20)
    stats = R.check_deck_syntax(text)
    assert stats["max_node_id"] < 4 * stats["nodes"] and stats["max_element_id"] == stats["elements"] and stats["longest_line"] <= 132
    ids = [int(l.split(",")[0]) for l in lines_of(text, "*NODE, NSET=NPLATE") + lines_of(text, "*NODE, NSET=NBEAM")]
    assert max(ids) < 10 * 20 * 20  # n = 20: ids stay near the node count (~2e3), nowhere near the old 900001 / 1000000


def test_deck_syntax_review_flags_long_lines_sparse_ids_and_bad_equations():
    with pytest.raises(ValueError, match="characters"):
        R.check_deck_syntax("*NSET, NSET=A\n" + ", ".join(str(i) for i in range(60)) + "\n")
    with pytest.raises(ValueError, match="sparse"):
        R.check_deck_syntax("*NODE\n1, 0, 0, 0\n2, 1, 0, 0\n1000000, 2, 0, 0\n")
    with pytest.raises(ValueError, match="terms announced"):
        R.check_deck_syntax("*EQUATION\n3\n1, 1, 1.0, 2, 1, -1.0\n")
    with pytest.raises(ValueError, match="duplicate"):
        R.check_deck_syntax("*NODE\n1, 0, 0, 0\n1, 1, 0, 0\n")
    assert R.check_deck_syntax("*NODE\n1, 0, 0, 0\n2, 1, 0, 0\n")["nodes"] == 2


def test_a5_equations_are_well_formed_and_every_beam_dof_is_dependent_once():
    text = R.a5_deck(SPEC, 10)
    lines = lines_of(text, "*EQUATION")
    dependent = []
    for count, terms in zip(lines[0::2], lines[1::2]):
        fields = [f.strip() for f in terms.split(",")]
        assert len(fields) == 3 * int(count)
        dependent.append((int(fields[0]), int(fields[1])))
    assert len(dependent) == len(set(dependent))
    beam = {int(r.split(",")[0]) for r in lines_of(text, "*NODE, NSET=NBEAM")}
    assert {n for n, _d in dependent} == beam  # no probe ties: the gauge is two actual nodes
    for b in beam:
        assert {d for n, d in dependent if n == b} == {1, 2, 3, 4, 5, 6}


def test_link_equations_encode_u_b_equals_u_p_plus_theta_cross_r():
    rows = R.link_equations(50, 7, (0.0, 0.0, 0.005))
    assert rows[0:2] == ["3", "50, 1, 1.0, 7, 1, -1.0, 7, 5, -0.005"]  # ux_b = ux_p + ry e
    assert rows[2:4] == ["3", "50, 2, 1.0, 7, 2, -1.0, 7, 4, 0.005"]  # uy_b = uy_p - rx e
    assert rows[4:6] == ["2", "50, 3, 1.0, 7, 3, -1.0"]
    assert rows[6:8] == ["2", "50, 4, 1.0, 7, 4, -1.0"]
    four = R.link_equations(50, 7, (0.1, 0.2, 0.3))
    assert four[0] == "4" and len(four[1].split(",")) == 12  # two non-zero rotation terms: four terms on one line


def test_a5_probe_nodes_exist_at_every_level_and_at_the_right_place():
    for n in R.DEFAULT_LEVELS:
        ids = R.probe_nodes(SPEC, n)
        m = 2 * n
        for name, (x, y) in (("centre", (0.5, 0.5)), ("stiffener", (0.5, 0.3))):
            i, j = round(x * m), round(y * m)
            assert ids[name] == R.plate_node(n, i, j) and not (i % 2 and j % 2)
        assert ids["beam"] == R.a5_beam_lattice(SPEC, n)[(n, round(0.3 * m))]
    text = R.a5_deck(SPEC, 10)
    ids = R.probe_nodes(SPEC, 10)
    nodes = {int(l.split(",")[0]): [f.strip() for f in l.split(",")] for l in lines_of(text, "*NODE, NSET=NPLATE") + lines_of(text, "*NODE, NSET=NBEAM")}
    assert nodes[ids["stiffener"]][1:] == ["0.5", "0.3", "0.0"] and nodes[ids["beam"]][1:3] == ["0.5", "0.3"]
    assert float(nodes[ids["beam"]][3]) == SPEC.offset


def test_a5_deck_refuses_levels_that_miss_the_stiffener_lines():
    with pytest.raises(ValueError):
        R.a5_deck(SPEC, 12)


def test_a5_loads_are_signed_toward_plus_z_and_sum_to_the_applied_totals():
    text = R.a5_deck(SPEC, 10)
    loads = cload(text)
    assert sum(v for (_n, d), v in loads.items() if d == 1) == pytest.approx(SPEC.line_load_x1 + SPEC.line_load_y1, rel=1e-12)
    assert sum(v for (_n, d), v in loads.items() if d == 3) == pytest.approx(+SPEC.pressure, rel=1e-12)  # ANYfem sign: +z
    assert not any(d in (2, 4, 5, 6) for (_n, d) in loads)
    # the S8R consistent weights: corners negative, mid-sides positive, one element sums to one
    assert sum(R.S8R_PRESSURE_WEIGHTS) == pytest.approx(1.0, abs=1e-15) and R.S8R_PRESSURE_WEIGHTS[0] < 0 < R.S8R_PRESSURE_WEIGHTS[4]


def test_applied_totals_keep_the_anyfem_sign_and_moment():
    totals = R.applied_totals(SPEC)
    assert totals["force"] == [SPEC.line_load_x1 + SPEC.line_load_y1, 0.0, +SPEC.pressure]
    # the development preflight measured the real assembly: z +10000 and moment (+5000, -5000, -70000)
    assert totals["moment_about_origin"] == pytest.approx([5000.0, -5000.0, -70000.0], rel=1e-12)


def test_deck_constraints_follow_the_edge_dof_sets():
    cons = R.deck_constraints(SPEC, 10)
    assert cons[R.plate_node(10, 0, 5)] == (1, 2, 3)  # x = 0 edge
    assert cons[R.plate_node(10, 20, 5)] == (3,)  # x = 1
    assert cons[R.plate_node(10, 0, 0)] == (1, 2, 3)  # corner: union
    assert R.plate_node(10, 10, 10) not in cons


def test_a5_quantities_read_the_three_probe_nodes():
    n = 10
    ids = R.probe_nodes(SPEC, n)
    rx = 2.0e-4
    blocks = [block("displacements", "PROBES", 1.0, [(ids["centre"], 0.0, 0.0, -3.0e-3), (ids["stiffener"], 0.0, 1.0e-3, -2.0e-3),
                                                      (ids["beam"], 0.0, 1.0e-3 - SPEC.offset * rx, -2.0e-3)])]
    q = R.a5_quantities(blocks, n, SPEC)
    assert q["w_center"] == -3.0e-3 and q["w_stiffener"] == -2.0e-3 and q["rx"] == pytest.approx(rx, rel=1e-9)


# ----------------------------------------------------------------------
# preflight arithmetic
# ----------------------------------------------------------------------
class StubProject:
    """Real ``BeamSection`` factory and the real ``Project.beam_offset_vector``; only the geometry (frames) is a stub.

    Three x-lines and three y-lines on a +z plate; ``local_y = local_z x axis`` exactly as ``Project.beam_section_frame``.
    """

    def __init__(self, section, *, plate_t=0.01, normal=(0.0, 0.0, 1.0)):
        from types import SimpleNamespace

        self.beam_sections = {"flat bar": section}
        self.edge_sections = {i: "flat bar" for i in range(1, 7)}
        self.materials = {"S355": SimpleNamespace(elastic_modulus=2.1e11, poisson_ratio=0.3)}
        self.plate_sections = {"plate": SimpleNamespace(thickness=plate_t)}
        self.load_cases = {"default": SimpleNamespace(pressures=[SimpleNamespace(value=1.0e4)] * 9)}
        self._normal = np.asarray(normal, dtype=float)

    def beam_section_of(self, edge):
        return self.beam_sections[self.edge_sections[edge]]

    def beam_section_frame(self, edge):
        axis = np.array([1.0, 0.0, 0.0]) if edge <= 3 else np.array([0.0, 1.0, 0.0])
        lz = self._normal - float(self._normal @ axis) * axis
        lz /= np.linalg.norm(lz)
        return axis, np.cross(lz, axis), lz

    def beam_offset_vector(self, edge):
        pytest.importorskip("anyfem.model.project")
        from anyfem.model.project import Project

        return Project.beam_offset_vector(self, edge)


def _section(**kwargs):
    pytest.importorskip("anysolver")
    from anyfem.model import BeamSection

    options = dict(flange_width=0.08, flange_thickness=0.01, offset_mode="automatic")
    options.update(kwargs)
    return BeamSection("flat bar", "Flatbar", "S355", **options)


def test_spec_reads_extents_from_the_section_factory_and_the_offset_from_the_project_vector():
    spec, pre = R.spec_from_project(StubProject(_section()))
    pre.require()
    assert (spec.bar_width, spec.bar_height) == (0.08, 0.01)  # width in the plate, thickness along the normal
    assert spec.offset == pytest.approx(0.005, abs=1e-15)
    assert math.isclose(spec.solver_section["area"], 0.08 * 0.01, rel_tol=1e-9)
    assert any(c["name"] == "offset_vs_geometry_coincidences" and c.get("kind") == "observation" for c in pre.checks)


def test_offset_does_not_decide_the_section_extents():
    """Same bar, manual eccentricity 0.05 (matches neither t/2 + h/2 reading): the extents stay, the offset follows the project."""

    spec, pre = R.spec_from_project(StubProject(_section(offset_mode="manual", eccentricity=0.05)))
    pre.require()
    assert (spec.bar_width, spec.bar_height, spec.offset) == (0.08, 0.01, pytest.approx(0.05, abs=1e-15))
    spec, pre = R.spec_from_project(StubProject(_section(flange_thickness=0.03)))
    pre.require()
    assert (spec.bar_width, spec.bar_height) == (0.08, 0.03) and spec.offset == pytest.approx(0.015, abs=1e-15)
    assert spec.inertia_vertical() == pytest.approx(0.08 * 0.03 ** 3 / 12.0, rel=1e-14)
    assert spec.inertia_lateral() == pytest.approx(0.03 * 0.08 ** 3 / 12.0, rel=1e-14)


def test_a_frame_whose_local_z_is_not_the_plate_normal_refuses():
    spec, pre = R.spec_from_project(StubProject(_section(), normal=(0.0, 0.6, 0.8)))
    assert any(c["name"] == "local_z_is_plate_normal_plus_z" for c in pre.failed())
    with pytest.raises(R.PreflightError):
        pre.require()


def test_swapping_the_flat_bar_extents_changes_the_inertias_not_the_offset_rule():
    a = R.StiffenedSpec(E=2.1e11, nu=0.3, plate_t=0.01, pressure=1.0, bar_width=0.08, bar_height=0.01, offset=0.005)
    b = R.StiffenedSpec(E=2.1e11, nu=0.3, plate_t=0.01, pressure=1.0, bar_width=0.01, bar_height=0.08, offset=0.005)
    assert a.beam_area == pytest.approx(b.beam_area, rel=1e-14)
    assert a.inertia_vertical() == pytest.approx(b.inertia_lateral(), rel=1e-14) and a.inertia_lateral() == pytest.approx(b.inertia_vertical(), rel=1e-14)
    assert a.inertia_vertical() < a.inertia_lateral() and b.inertia_vertical() > b.inertia_lateral()


def test_support_projection_is_lossless_or_refuses():
    good = {}
    for j in range(0, 11):
        good[(0.0, j / 10, 0.0)] = ("ux", "uy", "uz")
    for i in range(1, 11):
        good[(i / 10, 0.0, 0.0)] = ("uz",)
        good[(i / 10, 1.0, 0.0)] = ("uz",)
    for j in range(1, 10):
        good[(1.0, j / 10, 0.0)] = ("uz",)
    good[(1.0, 0.0, 0.0)] = ("uz",)
    good[(1.0, 1.0, 0.0)] = ("uz",)
    pre = R.Preflight()
    edges = R.project_supports_to_edges(good, pre)
    assert not pre.failed() and edges["x0"] == ("ux", "uy", "uz") and edges["y1"] == ("uz",)
    bad = dict(good)
    bad[(0.0, 0.5, 0.0)] = ("ux", "uy", "uz", "rx")
    pre = R.Preflight()
    R.project_supports_to_edges(bad, pre)
    assert any(c["name"] == "support_uniform_x0" for c in pre.failed())
    rotated = {k: tuple(v) + (("ry",) if k[0] == 1.0 and 0 < k[1] < 1 else ()) for k, v in good.items()}
    pre = R.Preflight()
    edges = R.project_supports_to_edges(rotated, pre)
    assert edges["x1"] == ("uz", "ry")  # rotational restraint is carried into the deck spec, not dropped


def test_load_closure_against_an_anyfem_assembly_is_checked_not_assumed():
    good = {"force": [1.2e5, 0.0, +1.0e4], "moment_about_origin": [5000.0, -5000.0, -70000.0]}
    pre = R.Preflight()
    R.check_load_closure_vs_anyfem(SPEC, good, pre)
    assert not pre.failed()
    pre = R.Preflight()
    R.check_load_closure_vs_anyfem(SPEC, {**good, "force": [1.2e5, 0.0, -1.0e4]}, pre)  # reversed pressure sign
    assert [c["name"] for c in pre.failed()] == ["load_totals_match_anyfem_assembly"]
    pre = R.Preflight()
    R.check_load_closure_vs_anyfem(SPEC, {**good, "moment_about_origin": [-5000.0, 5000.0, -70000.0]}, pre)  # opposite x/y moments
    assert [c["name"] for c in pre.failed()] == ["moment_about_origin_matches_anyfem_assembly"]


# ----------------------------------------------------------------------
# calibration decks and their readers
# ----------------------------------------------------------------------
def test_calibration_decks_are_ordered_syntax_clean_and_dense():
    decks = R.calibration_decks(SPEC)
    assert list(decks) == list(R.CALIBRATION_ORDER) == ["cal1_beam_frame", "cal2_shell_beam_link_ry", "cal3_shell_beam_link_rx", "cal4_pressure_sign"]
    for name, text in decks.items():
        stats = R.check_deck_syntax(text)
        assert stats["max_node_id"] < 4 * stats["nodes"], name


def test_beam_frame_deck_has_no_rotational_clamp_and_two_steps():
    text = R.calibration_beam_frame_deck(SPEC)
    assert text.count("*STEP") == 2 and "*CLOAD, OP=NEW" in text and "SECTION=RECT" in text
    assert lines_of(text, "*BOUNDARY") == ["1, 1, 4", "11, 2, 3"]  # translations (+ torsion at one end): no clamp of bending rotations
    assert "RF" in text and "NSET=MID" in text


def test_rotation_link_decks_tie_the_real_a5_equations_to_a_strip_with_a_clamp_and_a_tip_load():
    ids = R.link_ids()
    for axis in ("x", "y"):
        text = R.calibration_rotation_link_deck(SPEC, axis)
        eq = lines_of(text, "*EQUATION")
        assert eq == R.link_equations(ids["beam"], ids["plate"], (0.0, 0.0, SPEC.offset))
        assert lines_of(text, "*BOUNDARY") == [f"{k}, 1, 6" for k in ids["clamp"]]
        loads = cload(text)
        assert set(loads) == {(k, 3) for k in ids["tip_edge"]} and sum(loads.values()) == pytest.approx(R.LINK_LOAD, rel=1e-12)
        assert "TYPE=S8R" in text and "TYPE=B31" in text and "0.0\n" in text  # nu = 0 material
        assert [l for l in lines_of(text, "*ELASTIC")] == [f"{R._fmt(SPEC.E)}, 0.0"]
    # strip along y is mirrored so that the shells stay counter-clockwise about +z
    ty = R.calibration_rotation_link_deck(SPEC, "y")
    nodes = {int(l.split(",")[0]): [float(f) for f in l.split(",")[1:]] for l in lines_of(ty, "*NODE, NSET=NSTRIP")}
    first = [int(f) for f in lines_of(ty, "*ELEMENT, TYPE=S8R")[0].split(",")[1:5]]
    p = [np.array(nodes[k]) for k in first]
    assert np.cross(p[1] - p[0], p[3] - p[0])[2] > 0
    with pytest.raises(ValueError):
        R.calibration_rotation_link_deck(SPEC, "z")


def _link_blocks(spec, axis, *, transmit=True, w_scale=1.0, closure=1.0):
    exp = R.rotation_link_expected(spec)
    ids = R.link_ids()
    up = np.array([0.0, 0.0, exp["w_tip"] * w_scale])
    ub = up.copy()
    if transmit:
        ub[0 if axis == "x" else 1] += exp["offset_along_axis"]
    return [block("displacements", "LINK", 1.0, [(ids["plate"], *up), (ids["beam"], *ub)]),
            block("forces", "CLAMP", 1.0, [(ids["clamp"][0], 0.0, 0.0, -R.LINK_LOAD * closure / 3), (ids["clamp"][1], 0.0, 0.0, -R.LINK_LOAD * closure / 3),
                                           (ids["clamp"][2], 0.0, 0.0, -R.LINK_LOAD * closure / 3)])]


def test_rotation_link_check_separates_transmitted_ignored_and_unclamped():
    for axis in ("x", "y"):
        ok = R.check_rotation_link(SPEC, _link_blocks(SPEC, axis), axis)
        assert ok["link_effective"] and ok["offset_ratio"] == pytest.approx(1.0, rel=1e-9)
        assert ok["theta_gauge"] == pytest.approx(R.rotation_link_expected(SPEC)["theta_tip"], rel=1e-9)
        ignored = R.check_rotation_link(SPEC, _link_blocks(SPEC, axis, transmit=False), axis)  # the dev-04 failure: no rotation seen
        assert not ignored["link_effective"] and ignored["offset_ratio"] == 0.0
        unclamped = R.check_rotation_link(SPEC, _link_blocks(SPEC, axis, w_scale=1.0e6, closure=0.0), axis)  # tip w gross, no reaction
        assert not unclamped["link_effective"] and not unclamped["clamp_reaction_closure"] <= R.REACTION_SCREEN


def test_rotation_link_expected_values_follow_the_kirchhoff_strip():
    exp = R.rotation_link_expected(SPEC)
    EI = SPEC.E * R.LINK_WIDTH * SPEC.plate_t ** 3 / 12.0
    assert exp["theta_tip"] == pytest.approx(R.LINK_LOAD * R.LINK_LENGTH ** 2 / (2 * EI), rel=1e-12)
    assert exp["w_tip"] > R.LINK_LOAD * R.LINK_LENGTH ** 3 / (3 * EI)  # the shear part adds
    assert exp["offset_along_axis"] == pytest.approx(-SPEC.offset * exp["theta_tip"], rel=1e-12)


def _frame_blocks(spec, uz, uy, closure=1.0):
    load = R.BEAM_CAL_LOAD
    return [block("displacements", "MID", 1.0, [(R.BEAM_CAL_MID, 0.0, 0.0, -uz)]), block("displacements", "MID", 2.0, [(R.BEAM_CAL_MID, 0.0, -uy, 0.0)]),
            block("forces", "SUPPORT", 1.0, [(1, 0.0, 0.0, load * closure / 2), (11, 0.0, 0.0, load * closure / 2)]),
            block("forces", "SUPPORT", 2.0, [(1, 0.0, load * closure / 2, 0.0), (11, 0.0, load * closure / 2, 0.0)])]


def test_beam_frame_classifier_discriminates_the_two_inertias_per_direction():
    pred_z = R.beam_midspan_prediction(SPEC, SPEC.inertia_vertical())
    pred_y = R.beam_midspan_prediction(SPEC, SPEC.inertia_lateral())
    assert pred_z > 10 * pred_y  # the two readings differ by ~64 in inertia
    assert R.classify_beam_frame(-pred_z, -pred_y, SPEC)["frame_confirmed"]
    swapped = R.classify_beam_frame(-pred_y, -pred_z, SPEC)  # CCX using the other inertia for each direction
    assert not swapped["frame_confirmed"] and swapped["z_step"]["closest"].startswith("lateral")
    gross = R.classify_beam_frame(-2.88e4 * pred_z, -pred_y, SPEC)  # the dev-04 magnitude: closest candidate is nowhere near it
    assert not gross["frame_confirmed"] and not gross["within_gross_screen"]


def test_beam_frame_check_also_requires_the_supports_to_hold():
    pred_z = R.beam_midspan_prediction(SPEC, SPEC.inertia_vertical())
    pred_y = R.beam_midspan_prediction(SPEC, SPEC.inertia_lateral())
    assert R.check_beam_frame(_frame_blocks(SPEC, pred_z, pred_y), SPEC)["frame_confirmed"]
    loose = R.check_beam_frame(_frame_blocks(SPEC, pred_z, pred_y, closure=0.5), SPEC)
    assert not loose["supports_held"] and not loose["frame_confirmed"]


def test_pressure_sign_check_records_p1_and_verifies_the_explicit_loads():
    text = R.calibration_pressure_sign_deck(SPEC)
    assert lines_of(text, "*BOUNDARY") == ["1, 1, 6", "4, 1, 6", "8, 1, 6"] and "PLATE, P1, 1.0" in text and text.count("*STEP") == 2
    forces = [float(l.split(",")[2]) for l in lines_of(text, "*CLOAD") if l.startswith(tuple("12345678"))][:8]
    assert sum(forces) == pytest.approx(1.0 * 0.2, rel=1e-12)  # p = 1 on 1.0 x 0.2, toward +z
    ok = R.check_pressure_sign([block("displacements", "TIP", 1.0, [(6, 0.0, 0.0, 3.0e-6)]), block("displacements", "TIP", 2.0, [(6, 0.0, 0.0, -3.0e-6)])])
    assert ok["explicit_load_verified"] and ok["p1_acts_toward"] == "-z" and ok["p1_over_explicit_magnitude"] == pytest.approx(1.0)
    down = R.check_pressure_sign([block("displacements", "TIP", 1.0, [(6, 0.0, 0.0, -3.0e-6)]), block("displacements", "TIP", 2.0, [(6, 0.0, 0.0, -3.0e-6)])])
    assert not down["explicit_load_verified"]  # explicit forces toward -z would contradict the ANYfem sign
    with pytest.raises(ValueError):
        R.check_pressure_sign([block("displacements", "TIP", 1.0, [(6, 0.0, 0.0, 0.0)]), block("displacements", "TIP", 2.0, [(6, 0.0, 0.0, 1.0)])])


def test_calibration_gate_names_every_failed_mechanism():
    ok = R.CalibrationRecord(True, True, True, True)
    assert ok.require_usable() is ok
    for flags in ((False, True, True, True), (True, False, True, True), (True, True, False, True), (True, True, True, False)):
        with pytest.raises(ValueError, match="INCONCLUSIVE"):
            R.CalibrationRecord(*flags).require_usable()


# ----------------------------------------------------------------------
# runner guards
# ----------------------------------------------------------------------
def test_output_directories_are_explicit_new_and_never_historical(tmp_path):
    with pytest.raises(ValueError):
        R.require_output_dir(tmp_path / "handoff" / "x")
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "f.txt").write_text("evidence")
    with pytest.raises(ValueError):
        R.require_output_dir(tmp_path / "full")
    assert R.require_output_dir(tmp_path / "fresh").is_dir()


def test_write_a5_decks_writes_only_into_the_given_directory(tmp_path):
    with pytest.raises(ValueError):  # a calibration that did not establish the link cannot produce production decks
        R.write_a5_decks(SPEC, tmp_path / "refused", levels=(10,), calibration=R.CalibrationRecord(True, False, True, True))
    assert not (tmp_path / "refused").exists()
    cal = R.write_calibration_decks(SPEC, tmp_path / "cal")
    assert sorted(p.name for p in (tmp_path / "cal").iterdir()) == sorted([f"{n}.inp" for n in R.CALIBRATION_ORDER] + ["manifest.json"])
    assert cal["order"] == list(R.CALIBRATION_ORDER)
    manifest = R.write_a5_decks(SPEC, tmp_path / "decks", levels=(10, 20, 40), calibration=R.CalibrationRecord(True, True, True, True, "-z", "test"))
    names = sorted(p.name for p in (tmp_path / "decks").iterdir())
    assert names == ["a5_n10.inp", "a5_n20.inp", "a5_n40.inp", "manifest.json"]
    assert manifest["decks"]["a5_n20"]["sha256"] == R.sha256_text((tmp_path / "decks" / "a5_n20.inp").read_text())
    assert manifest["decks"]["a5_n40"]["syntax"]["longest_line"] <= 132


def test_importing_the_module_starts_no_process():
    assert "subprocess" not in vars(R)


# ----------------------------------------------------------------------
# against the real repository (needs the project dependencies; skipped otherwise)
# ----------------------------------------------------------------------
def test_the_real_stiffened_project_passes_the_physical_preflight():
    pytest.importorskip("anygeometry.volume_cut")
    from test_submodel_cut import stiffened_project

    project, _faces, _stiffeners = stiffened_project()
    spec, pre = R.spec_from_project(project, plate_thickness=0.01)  # 0.01 is the literal in tests/test_submodel_workflow.grid_project
    for check in pre.checks:
        print(check)
    pre.require()
    assert spec.nu == pytest.approx(project.materials["S355"].poisson_ratio)
    assert spec.offset == pytest.approx(0.005, abs=1e-15) and (spec.bar_width, spec.bar_height) == (0.08, 0.01)


def test_the_real_assembly_has_the_sign_and_moment_the_deck_applies():
    """The development preflight failed here: real assembly +10000 z, moment (+5000, -5000, -70000)."""

    pytest.importorskip("anygeometry.volume_cut")
    from test_submodel_cut import stiffened_project

    project, _faces, _stiffeners = stiffened_project()
    spec, _pre = R.spec_from_project(project, plate_thickness=0.01)
    totals = R.anyfem_load_totals(project, project.generate_mesh(0.1))
    pre = R.Preflight()
    R.check_load_closure_vs_anyfem(spec, totals, pre)
    print(totals, R.applied_totals(spec))
    assert not pre.failed(), pre.failed()


# ----------------------------------------------------------------------
# Physical-offset fallback (section 12).  Hand-built .frd / .dat fixtures: NOT CalculiX output.  They test the checkers'
# arithmetic and that a joint with NO rotation arm (the recorded cal2/cal3 failure mode) is rejected.
# ----------------------------------------------------------------------
GOOD_CAL = R.CalibrationRecord(True, False, False, True, source="recorded: cal2/cal3 failed")


def frd_text(points, field):
    """points: [(xyz, kind)], field(xyz, kind) -> (ux, uy, uz).  Fixed-width ' -1' rows, E12.5 values."""

    def row(i, v):
        return " -1" + f"{i:10d}" + "".join(f"{c:12.5E}" for c in v)

    nodes = [row(i + 1, p) for i, (p, _k) in enumerate(points)]
    disp = [row(i + 1, field(p, k)) for i, (p, k) in enumerate(points)]
    return "\n".join(["    1UUSER", "    2C                             %d                                     1" % len(points), *nodes,
                      " -3", " 100CL  101000000                 1.00000E+00                     1           1  1STEP",
                      " -4  DISP        4    1", " -5  D1          1    2    1    0", " -5  D2          1    2    2    0",
                      " -5  D3          1    2    3    0", " -5  ALL         1    2    0    0    1ALL", *disp, " -3", "9999"]) + "\n"


def joint_points(axis, spec=SPEC):
    t, e, h, b, length = spec.plate_t, spec.offset, R.LINK_STUB_SIZE, R.LINK_WIDTH, R.LINK_LENGTH
    pts = [(R._strip_xyz(axis, length, b / 2, t / 2), "top"), (R._strip_xyz(axis, length, b / 2, -t / 2), "bot")]
    pts += [(R._strip_xyz(axis, length, b / 2 + dt, e + dz), "corner") for dt in (-h / 2, h / 2) for dz in (-h / 2, h / 2)]
    return pts


def rigid_field(axis, slope, arm_on, uz=0.0, spec=SPEC):
    """Rigid rotation of the midsurface: u_axis = -slope*z (skin); corners get the arm only if ``arm_on``."""

    def field(p, kind):
        z = p[2] if (kind != "corner" or arm_on) else 0.0
        u = [0.0, 0.0, uz]
        u[0 if axis == "x" else 1] = -slope * z
        return tuple(u)

    return field


def test_frd_parser_and_node_lookup():
    pts = [((0.5, 0.3, 0.005), "a"), ((0.5, 0.3, -0.005), "b")]
    coords, steps = R.parse_frd(frd_text(pts, lambda p, k: (1.0e-3, -2.0e-3, 3.0e-3 if k == "a" else -3.0e-3)))
    assert coords[1] == pytest.approx((0.5, 0.3, 0.005)) and len(steps) == 1
    assert steps[0][2] == pytest.approx((1.0e-3, -2.0e-3, -3.0e-3))
    assert R.frd_node_at(coords, (0.5, 0.3, 0.005)) == 1
    with pytest.raises(ValueError):
        R.frd_node_at(coords, (0.5, 0.3, 0.0))
    with pytest.raises(ValueError):
        R.frd_node_at({1: (0, 0, 0), 2: (0, 0, 0)}, (0, 0, 0))


def test_offset_law_deck_structure():
    text = R.offset_law_deck(SPEC)
    R.check_deck_syntax(text)
    assert sum("OFFSET2=0.5" in row for row in text.splitlines() if row.startswith("*BEAM SECTION")) == 2 and "*BEAM SECTION, ELSET=BC, MATERIAL=STEEL, SECTION=RECT\n" in text  # control has no offset
    assert "*EQUATION" not in text


def offset_law_blocks(e_x, e_y, control=0.0):
    p, length, el = R.OFFSET_LAW_LOAD, R.OFFSET_LAW_LENGTH, R.OFFSET_LAW_ELEMENTS
    stiff = 8.0 * SPEC.E * SPEC.inertia_vertical() / (p * length ** 2)
    e1, mid = el + 1, el // 2 + 1
    mids = block("displacements", "MIDS", 1.0, [(mid, 0, 0, -e_x / stiff), (e1 + mid, 0, 0, -e_y / stiff), (2 * e1 + mid, 0, 0, control)])
    rf = block("forces", "SUPPORT", 1.0, [(1, -p, 0, 0), (e1, 0, 0, 0), (e1 + 1, 0, -p, 0), (2 * e1, 0, 0, 0), (2 * e1 + 1, -p, 0, 0), (3 * e1, 0, 0, 0)])
    return [mids, rf]


def test_offset_law_checker_reads_signs_and_refuses_a_drawing_only_offset():
    ok = R.check_offset_law(SPEC, offset_law_blocks(SPEC.offset, -SPEC.offset))
    assert ok["effective"] and (ok["sign_x"], ok["sign_y"]) == (1, -1)
    none = R.check_offset_law(SPEC, offset_law_blocks(0.0, 0.0))  # offset only drawn: nothing bends
    assert not none["effective"] and "INCONCLUSIVE" in none["note"]
    assert not R.check_offset_law(SPEC, offset_law_blocks(SPEC.offset, SPEC.offset, control=1.0e-3))["effective"]
    wrong_unit = R.check_offset_law(SPEC, offset_law_blocks(SPEC.offset * 8, SPEC.offset * 8))  # normalized by bar_width instead
    assert not wrong_unit["effective"] and wrong_unit["x"]["implied_unit_over_bar_width"] == pytest.approx(1.0)  # reported, not accepted


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("mode", R.PHYS_MODES)
def test_joint_deck_shares_the_tip_node_without_equations(axis, mode):
    text = R.physical_joint_deck(SPEC, axis, mode, -1)
    R.check_deck_syntax(text)
    assert "*EQUATION" not in text and "OFFSET2=-5" in text and "OUTPUT=3D" in text
    ids = R.joint_ids()
    assert f"{R.LINK_NX + 1}, {ids['tip']}, {ids['stub_end']}" in text  # the stub root IS the strip's tip node
    assert ("*BOUNDARY" in text.split("*STEP")[1]) == (mode != "bending")  # prescribed values live inside the step
    with pytest.raises(ValueError):
        R.physical_joint_deck(SPEC, axis, mode, 0)


@pytest.mark.parametrize("axis", ["x", "y"])
def test_joint_checker_accepts_a_rigid_arm_and_rejects_no_arm(axis):
    pts = joint_points(axis)
    length = R.LINK_LENGTH
    good = R.check_joint(SPEC, frd_text(pts, rigid_field(axis, R.PHYS_SLOPE, True, uz=R.PHYS_SLOPE * length)), axis, "rotation")
    assert good["effective"], good
    assert good["arm_measured"] == pytest.approx(-SPEC.offset * R.PHYS_SLOPE, rel=1e-4)
    bad = R.check_joint(SPEC, frd_text(pts, rigid_field(axis, R.PHYS_SLOPE, False, uz=R.PHYS_SLOPE * length)), axis, "rotation")
    assert not bad["effective"] and not bad["gates"]["arm_is_minus_e_slope"] and bad["arm_measured"] == 0.0  # zero rotation-arm extension


def test_joint_checker_translation_and_bending():
    for axis in ("x", "y"):
        pts = joint_points(axis)
        tr = R.check_joint(SPEC, frd_text(pts, lambda p, k: (0.0, 0.0, R.PHYS_DELTA)), axis, "translation")
        assert tr["effective"], tr
        theta = R.rotation_link_expected(SPEC)["theta_tip"]
        bend = R.check_joint(SPEC, frd_text(pts, rigid_field(axis, theta, True)), axis, "bending")
        assert bend["effective"] and bend["slope_over_closed_form"] == pytest.approx(1.0, rel=1e-4)
        assert not R.check_joint(SPEC, frd_text(pts, rigid_field(axis, theta, False)), axis, "bending")["effective"]
        assert not R.check_joint(SPEC, frd_text(pts, rigid_field(axis, 0.0, True)), axis, "bending")["effective"]  # no rotation at all
    with pytest.raises(ValueError):
        R.check_joint(SPEC, frd_text(joint_points("x")[:2], lambda p, k: (0, 0, 0)), "x", "rotation")  # corner nodes absent: evidence


def full_record(**over):
    joints = {f"{m}_{a}": True for m in R.PHYS_MODES for a in ("x", "y")}
    joints.update(over)
    return R.PhysicalRecord(True, 1, -1, joints)


def test_physical_record_refuses_inconclusive_and_never_consults_cal2_cal3():
    assert full_record().require_usable(GOOD_CAL)
    with pytest.raises(ValueError, match="INCONCLUSIVE"):
        full_record(rotation_y=False).require_usable(GOOD_CAL)
    with pytest.raises(ValueError, match="INCONCLUSIVE"):
        R.PhysicalRecord(False, 1, 1, {}).require_usable(GOOD_CAL)
    with pytest.raises(ValueError, match="cal1"):
        full_record().require_usable(R.CalibrationRecord(False, False, False, True))
    with pytest.raises(ValueError, match="INCONCLUSIVE"):
        GOOD_CAL.require_usable()  # the original gate still refuses


def test_native_deck_shares_ids_and_has_dense_numbering():
    text = R.a5_native_deck(SPEC, 10, 1, -1)
    info = R.check_deck_syntax(text)
    assert "*EQUATION" not in text and "NBEAM" not in text and info["nodes"] == 21 ** 2 - 100
    assert "OFFSET2=0.5" in text and "OFFSET2=-0.5" in text and "OUTPUT=3D" in text
    ids = R.probe_nodes(SPEC, 10)
    assert f"{ids['centre']}, {ids['stiffener']}" in text
    for elset in ("STIFX", "STIFY"):
        beam_lines = lines_of(text, f"*ELEMENT, TYPE=B31, ELSET={elset}")
        assert len(beam_lines) == 10 and all(int(x) <= 21 ** 2 for l in beam_lines for x in l.split(",")[1:])  # plate ids only


def test_native_decks_are_refused_without_the_physical_record(tmp_path):
    with pytest.raises(ValueError, match="INCONCLUSIVE"):
        R.write_native_decks(SPEC, tmp_path / "n", calibration=GOOD_CAL, physical=full_record(translation_x=False))
    assert not (tmp_path / "n").exists()
    manifest = R.write_native_decks(SPEC, tmp_path / "ok", calibration=GOOD_CAL, physical=full_record())
    assert sorted(manifest["decks"]) == ["a5_native_n10", "a5_native_n20", "a5_native_n40"] and "COMPARATIVE" in manifest["label"]
    assert R.DEFAULT_LEVELS == (10, 20, 40, 80)  # the assessment freeze is a separate count
