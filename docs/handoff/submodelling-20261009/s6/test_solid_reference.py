"""Tests of the S6 loaded shell-to-solid reference study (``solid_reference.py``).

Everything above the "real stack" marker is pure numpy: deck orientation / totals / nodes, the independent parent
field, the CCX ``.dat`` reader, typed missing-output failures and the CLI.  The last test builds the smallest
preset through the public ANYfem route and is skipped when the solid stack is not importable.  None of these
tests launches CalculiX.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import solid_reference as sr  # noqa: E402

LEVEL_CASES = [("C3D8", 2, 1), ("C3D8", 4, 2), ("C3D20", 2, 1), ("C3D20", 4, 2)]


@pytest.mark.parametrize("element, n, layers", LEVEL_CASES)
def test_every_element_is_right_handed_and_nodes_are_unique(element, n, layers):
    mesh = sr.reference_mesh(element, n, layers)
    for _eid, nodes, _ in mesh.elements:
        assert len(nodes) == sr.ELEMENT_NODES[element] and len(set(nodes)) == len(nodes)
        assert np.all(sr.hex_corner_jacobians(np.array([mesh.nodes[k] for k in nodes[:8]])) > 0.0)


@pytest.mark.parametrize("element, n, layers", LEVEL_CASES)
def test_node_and_element_counts_and_extent(element, n, layers):
    mesh = sr.reference_mesh(element, n, layers)
    assert len(mesh.elements) == n * n * layers
    if element == "C3D8":
        expected = (n + 1) ** 2 * (layers + 1)
    else:  # corners + midedges of a (n, n, layers) brick grid
        corners = (n + 1) ** 2 * (layers + 1)
        edges = 2 * n * (n + 1) * (layers + 1) + (n + 1) ** 2 * layers
        expected = corners + edges
    assert len(mesh.nodes) == expected
    xyz = np.array(list(mesh.nodes.values()))
    assert np.allclose(xyz.min(axis=0), sr.DETAIL_BOX[:3]) and np.allclose(xyz.max(axis=0), sr.DETAIL_BOX[3:])
    assert sum(1 for k in mesh.nodes if k == mesh.nid(*mesh.ijk[k])) == len(mesh.nodes)


def test_c3d20_midside_nodes_sit_on_edge_midpoints():
    mesh = sr.reference_mesh("C3D20", 2, 1)
    for _eid, nodes, _ in mesh.elements:
        for (a, b), mid in zip(sr.EDGES20, nodes[8:]):
            assert np.allclose(mesh.nodes[mid], 0.5 * (np.array(mesh.nodes[nodes[a]]) + np.array(mesh.nodes[nodes[b]])))


@pytest.mark.parametrize("element", ["C3D8", "C3D20"])
@pytest.mark.parametrize("sign", [1.0, -1.0])
@pytest.mark.parametrize("side, z", [("top", 0.005), ("bottom", -0.005)])
def test_skin_forces_are_signed_total_traction_on_the_named_skin(element, sign, side, z):
    mesh = sr.reference_mesh(element, 4, 2)
    traction = np.array([0.0, 0.0, sign * 1.0e4])
    forces = sr.skin_forces(mesh, side, traction)
    total = np.sum(list(forces.values()), axis=0)
    assert np.allclose(total, traction * 0.2 * 0.2, rtol=1e-12)
    assert all(abs(mesh.nodes[k][2] - z) < 1e-15 for k in forces)
    moment = np.sum([np.cross(mesh.nodes[k], f) for k, f in forces.items()], axis=0)
    assert np.allclose(moment, np.cross([0.5, 0.5, z], traction * 0.04), rtol=1e-10, atol=1e-12)
    if element == "C3D20":
        corner = mesh.nid(0, 0, mesh.nz if side == "top" else 0)
        assert np.dot(forces[corner], traction) < 0.0  # serendipity corner weights are negative
    assert sr.skin_side(z) == side


def test_deck_round_trip_prescribes_only_lateral_nodes_and_never_loads_them():
    mesh = sr.reference_mesh("C3D20", 2, 2)
    lateral = mesh.lateral()
    prescribed = {k: (1e-6 * k, -2e-6, 3e-7) for k in lateral}
    applied, withheld = sr.split_loads(sr.skin_forces(mesh, "top", (0.0, 0.0, 1.0e4)), lateral)
    parsed = sr.parse_deck(sr.deck_text(mesh, 2.1e11, 0.3, prescribed, applied, "t"))
    assert set(parsed["boundary"]) == set(lateral)
    for k in lateral:
        np.testing.assert_allclose(parsed["boundary"][k], prescribed[k], rtol=sr.DECK_REPR_REL, atol=0.)
    assert not set(parsed["cload"]) & set(lateral) and withheld
    assert parsed["elements"] == {e[0]: e[1] for e in mesh.elements}
    assert set(parsed["nodes"]) == set(mesh.nodes) and parsed["elastic"] == (2.1e11, 0.3)
    for k in mesh.nodes:
        np.testing.assert_allclose(parsed["nodes"][k], mesh.nodes[k], rtol=sr.DECK_REPR_REL, atol=0.)
    assert applied and all(mesh.ijk[k][2] == mesh.nz and 0 < mesh.ijk[k][0] < mesh.nx and 0 < mesh.ijk[k][1] < mesh.ny
                           for k in applied)  # loaded free nodes are interior skin nodes only
    total = np.sum(list(applied.values()) + list(withheld.values()), axis=0)
    assert np.allclose(total, [0.0, 0.0, 1.0e4 * 0.04])


def test_independent_parent_field_reproduces_rigid_and_affine_fields_on_a_skewed_quad():
    X = np.array([[0.0, 0.0, 0.0], [1.0, 0.1, 0.0], [1.2, 1.1, 0.0], [-0.1, 0.9, 0.0]])
    theta, t, a = np.array([0.01, -0.02, 0.03]), np.array([1e-3, 2e-3, -1e-3]), np.array([0.2, 0.4, 0.1])
    rigid = np.array([np.concatenate([t + np.cross(theta, x - a), theta]) for x in X])
    patch = sr.ShellPatch((1,), X[None], rigid[None])
    points = np.array([[0.5, 0.5, 0.0], [0.4, 0.6, 0.005], [0.6, 0.3, -0.005]])
    values, spread = sr.evaluate_patch(patch, points)
    assert np.allclose(values, [t + np.cross(theta, p - a) for p in points], atol=1e-15) and spread == 0.0
    L = np.array([[1e-3, 2e-4], [-3e-4, 5e-4], [1e-4, 0.0]])  # membrane + deflection, no rotation: linear in x, y
    affine = np.array([np.concatenate([L @ x[:2], np.zeros(3)]) for x in X])
    values, _ = sr.evaluate_patch(sr.ShellPatch((1,), X[None], affine[None]), points[:1])
    assert np.allclose(values[0], L @ points[0, :2], atol=1e-15)


def test_shared_edge_hits_agree_and_outside_points_are_a_typed_miss():
    X = np.array([[[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], [[1, 0, 0], [2, 0, 0], [2, 1, 0], [1, 1, 0]]], float)
    U = np.zeros((2, 4, 6))
    U[:, :, 0] = X[:, :, 0]  # u_x = x, continuous across the shared edge
    patch = sr.ShellPatch((1, 2), X, U)
    values, spread = sr.evaluate_patch(patch, [[1.0, 0.5, 0.0]])
    assert np.allclose(values, [[1.0, 0.0, 0.0]]) and spread < 1e-15
    with pytest.raises(sr.StudyError) as error:
        sr.evaluate_patch(patch, [[3.0, 0.5, 0.0]])
    assert error.value.code == "PARENT_LOCATE_MISS"


def _dat(mesh, element, rf_nodes, *, drop_node=None, drop_stress=False, total=None):
    lines = [" displacements (vx,vy,vz) for set NALL and time  0.1000000E+01", ""]
    lines += [f"{k:10d}  {1e-6 * k:.6E}  {0.0:.6E}  {-2e-6:.6E}" for k in sorted(mesh.nodes) if k != drop_node]
    lines += ["", " forces (fx,fy,fz) for set NPRESC and time  0.1000000E+01", ""]
    lines += [f"{k:10d}  {0.0:.6E}  {0.0:.6E}  {-1.0:.6E}" for k in sorted(rf_nodes)]
    if total is not None:  # CCX layout of *NODE PRINT ... TOTALS=YES
        lines += ["", " total force (fx,fy,fz) for set NPRESC and time  0.1000000E+01", "",
                  "       " + "  ".join(f"{c:.6E}" for c in total)]
    if element == "C3D8" and not drop_stress:
        lines += ["", " stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz) for set EALL and time  0.1000000E+01", ""]
        lines += [f"{e[0]:10d} {ip:3d}  1.0E+06  2.0E+06  0.0E+00  3.0E+05  0.0E+00  0.0E+00"
                  for e in mesh.elements for ip in range(1, 9)]
    return "\n".join(lines) + "\n"


def test_dat_reader_parses_blocks_and_refuses_malformed_rows():
    mesh = sr.reference_mesh("C3D8", 2, 1)
    parsed = sr.parse_dat(_dat(mesh, "C3D8", mesh.lateral()))
    assert set(parsed["displacements"]) == set(mesh.nodes) and set(parsed["forces"]) == set(mesh.lateral())
    assert len(parsed["stresses"]) == len(mesh.elements) and len(parsed["stresses"][1]) == 8
    with pytest.raises(sr.StudyError) as error:
        sr.parse_dat(" displacements (vx,vy,vz) for set NALL and time 1.0\n 1 1.0 2.0\n")
    assert error.value.code == "CCX_OUTPUT_MALFORMED"


def _manifest_with_decks(out: Path, levels=((4, 2),)):
    """A manifest and decks exactly as ``build`` writes them, without the ANYfem stack."""

    traction = [0.0, 0.0, 1.0e4]
    decks = []
    for n, layers in levels:
        for element in ("C3D8", "C3D20"):
            mesh = sr.reference_mesh(element, n, layers)
            lateral = mesh.lateral()
            applied, _ = sr.split_loads(sr.skin_forces(mesh, "top", traction), lateral)
            text = sr.deck_text(mesh, 2.1e11, 0.3, {k: (0.0, 0.0, 0.0) for k in lateral}, applied, "t")
            name = sr.deck_name(element, n, layers)
            (out / f"{name}.inp").write_text(text, encoding="ascii")
            import hashlib

            decks.append({"name": name, "element": element, "n": n, "layers": layers, "inp": f"{name}.inp",
                          "dat": f"{name}.dat", "sha256": hashlib.sha256(text.encode("ascii")).hexdigest()})
    return {"study": sr.STUDY_ID, "schema": sr.SCHEMA, "preset": "smoke", "levels": [list(l) for l in levels],
            "decks": decks, "runs": [], "box": list(sr.DETAIL_BOX), "skin_side": "top", "traction": traction,
            "z_end": 0.005, "region_half": sr.REGION_HALF, "assumptions": list(sr.ASSUMPTIONS), "sources": {},
            "script_sha256": "x", "parent_field_sha256": "y", "project": "parent.anyfem", "job_id": "j", "face_id": 1}


def test_missing_ccx_output_is_a_typed_failure_per_deck_and_nothing_is_invented(tmp_path):
    manifest = _manifest_with_decks(tmp_path)
    results = sr.read_reference_outputs(tmp_path, manifest)
    assert {r["code"] for r in results.values()} == {"CCX_OUTPUT_MISSING"}
    assert all(r["status"] == "failed" for r in results.values())


def test_complete_output_gives_metrics_equilibrium_and_stress_only_for_c3d8(tmp_path):
    manifest = _manifest_with_decks(tmp_path)
    mesh8 = sr.reference_mesh("C3D8", 4, 2)
    held = set(mesh8.lateral())
    (tmp_path / "ref_C3D8_4x4x2.dat").write_text(_dat(mesh8, "C3D8", held))
    metrics = sr.reference_metrics(tmp_path, manifest, manifest["decks"][0])
    assert metrics["status"] == "ok" and metrics["stress_region"]["status"] == "ok"
    assert np.allclose(metrics["stress_region"]["average"], [1e6, 2e6, 0, 3e5, 0, 0])
    assert metrics["stress_region"]["volume"] == pytest.approx(0.1 * 0.1 * 0.01)
    assert metrics["u_end_centre"][0] == pytest.approx(1e-6 * mesh8.nid(2, 2, 2), rel=1e-5)
    assert np.allclose(metrics["applied_force"], [0.0, 0.0, 400.0])
    mesh20 = sr.reference_mesh("C3D20", 4, 2)
    (tmp_path / "ref_C3D20_4x4x2.dat").write_text(_dat(mesh20, "C3D20", set(mesh20.lateral())))
    assert sr.reference_metrics(tmp_path, manifest, manifest["decks"][1])["stress_region"]["status"] == "not_computed"


@pytest.mark.parametrize("damage, code", [({"drop_node": 5}, "CCX_OUTPUT_INCOMPLETE"), ({"drop_stress": True}, "CCX_OUTPUT_INCOMPLETE")])
def test_incomplete_output_and_changed_decks_are_typed(tmp_path, damage, code):
    manifest = _manifest_with_decks(tmp_path)
    mesh = sr.reference_mesh("C3D8", 4, 2)
    (tmp_path / "ref_C3D8_4x4x2.dat").write_text(_dat(mesh, "C3D8", mesh.lateral(), **damage))
    with pytest.raises(sr.StudyError) as error:
        sr.reference_metrics(tmp_path, manifest, manifest["decks"][0])
    assert error.value.code == code
    deck = tmp_path / "ref_C3D8_4x4x2.inp"
    deck.write_text(deck.read_text(encoding="ascii") + "\n")
    with pytest.raises(sr.StudyError) as changed:
        sr.reference_metrics(tmp_path, manifest, manifest["decks"][0])
    assert changed.value.code == "DECK_CHANGED"


@pytest.mark.parametrize("token, expected", [("1.234567E+02", 0.5e-4), ("-4.000000E+02", 0.5e-4), ("1.0E-05", 0.5e-6),
                                             ("0.000000E+00", 0.0), ("123.456", 0.5e-3), ("-9.999999E-08", 0.5e-14)])
def test_print_half_ulp_is_half_a_unit_of_the_last_printed_digit(token, expected):
    assert sr.print_half_ulp(token) == pytest.approx(expected, rel=1e-12, abs=0.0)
    with pytest.raises(sr.StudyError):
        sr.print_half_ulp("abc")


def test_deck_requests_ccx_totals_and_the_parser_reads_the_print_request_back():
    mesh = sr.reference_mesh("C3D8", 2, 1)
    text = sr.deck_text(mesh, 2.1e11, 0.3, {k: (0.0, 0.0, 0.0) for k in mesh.lateral()}, {}, "t")
    assert "*NODE PRINT, NSET=NPRESC, TOTALS=YES\nRF\n" in text
    prints = sr.parse_deck(text)["node_print"]
    assert {"nset": "NPRESC", "totals": "YES", "variables": ["RF"]} in prints
    assert {"nset": "NALL", "totals": "NO", "variables": ["U"]} in prints


def test_dat_reader_keeps_raw_tokens_and_the_direct_total_block():
    mesh = sr.reference_mesh("C3D8", 2, 1)
    parsed = sr.parse_dat(_dat(mesh, "C3D8", mesh.lateral(), total=(-1.5, 2.0e-3, 4.0e2)))
    assert set(parsed["forces_raw"]) == set(mesh.lateral()) and parsed["forces_raw"][mesh.lateral()[0]][2] == "-1.000000E+00"
    assert parsed["totals"]["NPRESC"]["values"] == [-1.5, 2.0e-3, 4.0e2]
    assert parsed["totals"]["NPRESC"]["raw"] == ["-1.500000E+00", "2.000000E-03", "4.000000E+02"]
    assert len(parsed["forces"]) == len(mesh.lateral())  # the total row is not mistaken for a node row
    with pytest.raises(sr.StudyError) as error:
        sr.parse_dat(" total force (fx,fy,fz) for set NPRESC and time 1.0\n 1.0 2.0\n")
    assert error.value.code == "CCX_OUTPUT_MALFORMED"


def test_classification_never_hides_an_observed_discrepancy():
    assert sr.classify(1e-7, 1e-7, 1e-6) == "pass"
    assert sr.classify(7.58e-5, 1.0e-4, 1e-6) == "unresolved"  # observed 7.58e-5 is explained but not discarded
    assert sr.classify(7.58e-5, 1.0e-6, 1e-6) == "fail"


def _fmt(vec):
    return [format(float(c), ".6E") for c in vec]


def _accounting_case(with_total):
    """Exactly balanced reactions with large self-equilibrated parts, then printed at 7 significant digits."""

    mesh = sr.reference_mesh("C3D8", 4, 2)
    held = mesh.lateral()
    full = sr.skin_forces(mesh, "top", (0.0, 0.0, 1.0e4))
    applied, withheld = sr.split_loads(full, held)
    rng = np.random.default_rng(7)
    noise = rng.normal(scale=3.0e3, size=(len(held), 3))
    noise -= noise.mean(axis=0)
    base = -np.sum(list(applied.values()), axis=0) / len(held)
    exact = {k: base + noise[i] for i, k in enumerate(held)}
    raw = {k: _fmt(v) for k, v in exact.items()}
    printed = {k: [float(t) for t in raw[k]] for k in held}
    exact_sum = np.sum(list(exact.values()), axis=0)
    total = None if not with_total else {"values": [float(t) for t in _fmt(exact_sum)], "raw": _fmt(exact_sum)}
    acc = sr.equilibrium_accounting(mesh.nodes, printed, raw, applied, full, held, total, with_total)
    return mesh, held, full, applied, withheld, exact, printed, acc


def test_rounded_sum_residual_is_inside_its_worst_case_print_bound_and_is_reported():
    mesh, held, full, applied, withheld, exact, printed, acc = _accounting_case(False)
    block = acc["force"]["rounded_sum"]
    exact_res = np.sum(list(exact.values()), axis=0) + np.sum(list(applied.values()), axis=0)
    assert np.all(np.abs(exact_res) < 1e-9)  # the underlying system is balanced
    assert block["norm"] > 0.0  # printed rounding leaves an observed residual ...
    assert block["norm"] <= block["bound_norm"] and np.all(np.abs(block["residual"]) <= np.array(block["resolution_bound"]))
    assert acc["force"]["verdict_source"] == "rounded_sum" and acc["force"]["ccx_total"]["status"] == "not_printed"
    assert block["status"] == sr.classify(block["norm"], block["bound_norm"], 1e-6 * acc["force"]["scale"])


def test_moment_residual_about_the_common_datum_is_bounded_and_labelled():
    mesh, held, full, applied, withheld, exact, printed, acc = _accounting_case(False)
    datum = np.array(sr.DATUM)
    exact_m = sum(np.cross(np.array(mesh.nodes[k]) - datum, exact[k]) for k in held) + sum(
        np.cross(np.array(mesh.nodes[k]) - datum, v) for k, v in applied.items())
    m = acc["moment"]["rounded_sum"]
    assert np.all(np.abs(np.array(m["residual"]) - exact_m) <= np.array(m["resolution_bound"]) + 1e-12)
    assert acc["moment"]["datum"] == list(sr.DATUM) and acc["moment"]["scale"] == pytest.approx(400.0 * sr.LENGTH_SCALE)
    assert acc["moment"]["status"] in ("pass", "fail", "unresolved") and acc["moment"]["uncertainty_rel"] == m["bound_rel"]


def test_direct_ccx_total_resolves_force_far_better_than_the_sum_of_rounded_entries():
    *_, acc = _accounting_case(True)
    force = acc["force"]
    assert force["verdict_source"] == "ccx_total" and force["ccx_total"]["consistent_with_rounded_sum"]
    assert force["ccx_total"]["bound_norm"] < force["rounded_sum"]["bound_norm"]
    assert force["status"] == force["ccx_total"]["status"] == "pass"


def test_withheld_loads_are_reconstructed_and_cancel_in_the_full_support_reaction():
    mesh, held, full, applied, withheld, exact, printed, acc = _accounting_case(False)
    wf = np.sum(list(withheld.values()), axis=0)
    assert acc["withheld"]["nodes"] == len(withheld) > 0 and np.allclose(acc["withheld"]["force"], wf)
    uncorrected, supported = np.array(acc["reaction_uncorrected"]["force"]), np.array(acc["reaction_full_support"]["force"])
    assert np.allclose(uncorrected - supported, wf) and np.linalg.norm(wf) > 0.0  # withholding changes the reaction
    support = acc["reaction_full_support"]
    assert support["defect_vs_deck_accounting_force"] < 1e-9 and support["defect_vs_deck_accounting_moment"] < 1e-9


def test_reference_metrics_archives_direct_total_raw_sum_and_withheld_terms(tmp_path):
    manifest = _manifest_with_decks(tmp_path)
    mesh = sr.reference_mesh("C3D8", 4, 2)
    held = mesh.lateral()
    (tmp_path / "ref_C3D8_4x4x2.dat").write_text(_dat(mesh, "C3D8", held, total=(0.0, 0.0, -float(len(held)))))
    metrics = sr.reference_metrics(tmp_path, manifest, manifest["decks"][0])
    acc = metrics["accounting"]
    assert acc["force"]["ccx_total"]["total_force"] == [0.0, 0.0, -float(len(held))]
    assert acc["force"]["ccx_total"]["consistent_with_rounded_sum"] and acc["reaction_uncorrected"]["force"][2] == -float(len(held))
    assert metrics["equilibrium_rel"] > 1e-6  # the observed discrepancy of this (deliberately wrong) output is kept
    assert len(metrics["interface_moment_centre"]) == 3
    report = sr.assemble(manifest, {"ref_C3D8_4x4x2": metrics}, {})
    names = {c["check"] for c in report["integrity"]}
    assert {"reference_equilibrium ref_C3D8_4x4x2", "reference_equilibrium_rounded_sum ref_C3D8_4x4x2",
            "ccx_total_consistency ref_C3D8_4x4x2", "reference_equilibrium_moment ref_C3D8_4x4x2"} <= names
    assert report["status"] == "incomplete"
    # a deck that asked for totals whose .dat lacks them is a failed integrity check, not silence
    (tmp_path / "ref_C3D8_4x4x2.dat").write_text(_dat(mesh, "C3D8", held))
    missing = sr.reference_metrics(tmp_path, manifest, manifest["decks"][0])
    assert missing["accounting"]["force"]["ccx_total"]["status"] == "absent_though_requested"
    integrity = sr.assemble(manifest, {"ref_C3D8_4x4x2": missing}, {})["integrity"]
    assert any(c["check"].startswith("ccx_total_present") and not c["ok"] for c in integrity)


def test_finest_stress_for_p1_and_p2_children_comes_from_the_c3d8_reference_and_names_are_preserved():
    def metrics(stress):
        return {"status": "ok", "u_end_centre": [0, 0, 1e-3], "u_mid_centre": [0, 0, 5e-4], "interface_force": [0, 0, -400.0],
                "interface_moment": [1.0, 2.0, 0.0], "interface_moment_centre": [0.0, 0.0, 0.0], "applied_force": [0, 0, 400.0],
                "equilibrium_rel": 0.0, "stress_region": stress}

    c8 = metrics({"status": "ok", "average": [1, 2, 3, 4, 5, 6.0], "volume": 1e-4, "aligned": True, "volume_rel_error": 0.0})
    c20 = metrics({"status": "not_computed", "reason": "r"})
    child = dict(metrics({"average": [1, 2, 3, 4, 5, 6.0]}), boundary=None)
    child.pop("boundary")
    manifest = {"preset": "formal", "levels": [[4, 2], [8, 4]], "assumptions": [], "sources": {}, "script_sha256": "x",
                "parent_field_sha256": "y", "runs": [{"element": "C3D20", "n": 4, "layers": 2, "order": 2},
                                                     {"element": "C3D8", "n": 4, "layers": 2, "order": 1}]}
    refs = {"ref_C3D8_4x4x2": c8, "ref_C3D8_8x8x4": c8, "ref_C3D20_4x4x2": c20, "ref_C3D20_8x8x4": c20}
    report = sr.assemble(manifest, refs, {"C3D20_4x2_p2": dict(child, status="ok"), "C3D8_4x2_p1": dict(child, status="ok")})
    by_run = {c["run"]: c for c in report["comparisons"]}
    p2 = by_run["C3D20_4x2_p2"]
    assert p2["vs_finest_reference"]["stress_region"]["reference"] == "C3D8 finest"
    assert p2["vs_finest_reference"]["stress_region"]["abs"] == 0.0 and "stress_region" not in p2["vs_same_level_reference"]
    assert by_run["C3D8_4x2_p1"]["vs_finest_reference"]["stress_region"]["reference"] == "C3D8 finest"
    assert {"vs_same_level_reference", "vs_finest_reference"} <= set(p2)
    assert "rel_physical" in p2["vs_finest_reference"]["interface_moment_centre"]


def test_cli_inventory_lists_sources_levels_assumptions_and_runs_nothing(capsys):
    assert sr.main(["--inventory", "--preset", "formal"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["presets"]["formal"]["levels"] == [[4, 2], [8, 4], [16, 8]] and data["presets"]["formal"]["orders"] == [1, 2]
    assert len(data["script"]["sha256"]) == 64 and "anyfem" in data["sources"] and data["assumptions"]
    rows = {r["deck"]: r for r in data["estimate"] if "deck" in r}
    assert rows["ref_C3D8_16x16x8"]["nodes"] == 17 * 17 * 9 and rows["ref_C3D20_4x4x2"]["elements"] == 32


def test_cli_read_without_a_study_is_a_typed_failure_with_exit_code_2(tmp_path, capsys):
    assert sr.main(["read", "--out", str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "STUDY_MANIFEST_MISSING"
    (tmp_path / "manifest.json").write_text("{not json")
    assert sr.main(["read", "--out", str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "STUDY_MANIFEST_INVALID"


# ----------------------------------------------------------------------
# real stack marker: the smallest preset through the public cut / run / save / reopen route
# ----------------------------------------------------------------------
def _stack_available():
    try:
        sr._stack()
        return True
    except sr.StudyError:
        return False


@pytest.mark.skipif(not _stack_available(), reason="the ANYfem solid stack is not importable")
def test_smoke_preset_builds_decks_and_reads_with_missing_ccx_outputs_as_typed_failures(tmp_path):
    manifest = sr.build_study(tmp_path / "study", "smoke", ccx_exe="ccx")
    out = tmp_path / "study"
    assert {d["inp"] for d in manifest["decks"]} == {"ref_C3D8_4x4x2.inp", "ref_C3D20_4x4x2.inp"}
    assert all((out / d["inp"]).is_file() for d in manifest["decks"]) and not list(out.glob("*.dat"))
    assert manifest["ccx_commands"][0]["argv"][:2] == ["ccx", "-i"]
    code = sr.main(["read", "--out", str(out)])
    report = json.loads((out / "study_report.json").read_text())
    assert code == 3 and report["status"] == "incomplete"
    assert {f["code"] for f in report["failures"]} == {"CCX_OUTPUT_MISSING"}
    child = next(v for k, v in report["children"].items() if k.startswith("C3D8_") and v.get("status") == "ok")
    assert child["boundary"]["ok"], child["boundary"]  # every actual boundary coefficient repeats the independent field
    assert child["artifacts_verified"] and child["staleness"] == []
