"""Offline tests of ``partial_solid_reference`` (artifact binding, refusals, methodology).

Everything runs on small synthetic HDF5 archives written with h5py in tmp_path: a 10x10 unit-plate parent (a
"original" and an independently written "smoke" copy) and Tet4 child pairs (P1 and P2) on a 2x2x2 lattice.  No ANYfem
solve, no CalculiX and no ANYfem stack is used; the child phase is covered only up to its stack-free refusals.

The synthetic child follows the supported codec ``anyfem.io.solid_submodel_artifact``: ``cells`` (C,4) and
``boundary_facets`` (F,3) are zero-based ROWS into ``coordinates``; ``node_ids`` / ``cell_ids`` / ``boundary_ids`` are
separate external labels whose range deliberately overlaps the row range (a reader that guessed "ids or rows" from the
integer range would silently bind the wrong vertices); ``groups_json`` is ``{node_groups, cell_groups,
boundary_groups}``; both orders store straight four-corner Tet4 geometry; P1 coefficients sit on the vertices, P2
coefficients on the vertices plus the unique mesh edges; the public displacement field has 3*vertices entries only;
P2 recovery uses the eleven-station rule whose centroid weight is negative.  The parent codec JSON key
``elements_of_face`` follows archive-schema.json where explicit.

``test_original_development_*`` reads the recorded development archives (formal-details/solid, the smoke study) and the
independent observations in formal-details/assessed-complete-child-{fields,traces}.json; it is skipped when those
evidence folders are absent and writes only below tmp_path.

    python docs/handoff/submodelling-20261009/s4a/run_tests.py \
        docs/handoff/submodelling-20261009/s6/test_partial_solid_reference.py --basetemp=<dir>
"""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules and hasattr(sys.modules[name], "__file__") and Path(sys.modules[name].__file__).resolve() == HERE / filename:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sr = _load("solid_reference", "solid_reference.py")
psp = _load("partial_solid_reference", "partial_solid_reference.py")

DOC_O, DOC_S = "doc-original", "doc-smoke"
JOB_O, JOB_S = "job-original-parent", "job-smoke-parent"
LEVEL = (2, 2)
STR = h5py.string_dtype("utf-8")
#: External labels start at these values so that they overlap the zero-based row range.
NODE_OFFSET, CELL_OFFSET, BOUNDARY_OFFSET = 3, 7, 5

EVIDENCE = HERE.parents[4] / "evidence"
EVIDENCE_ORIGINAL = EVIDENCE / "formal-details" / "solid"
EVIDENCE_SMOKE = EVIDENCE / "solid-total-dev-input" / "study"
EVIDENCE_DETAILS = EVIDENCE / "formal-details"


@pytest.fixture(autouse=True)
def small_levels(monkeypatch):
    monkeypatch.setattr(psp, "FORMAL_LEVELS", (LEVEL,))


# ----------------------------------------------------------------------
# synthetic archives
# ----------------------------------------------------------------------
def _root(handle, **attrs):
    for key, value in attrs.items():
        handle.attrs[key] = value


def parent_field(perturb: float = 0.0) -> np.ndarray:
    U = np.random.default_rng(7).normal(size=(121, 6)) * 1e-4
    U[17, 0] += perturb
    return U


def make_parent(root: Path, doc: str, job: str, perturb: float = 0.0, lineage: bool = True) -> None:
    meshes, results = root / "parent.anyfem-data" / "meshes", root / "parent.anyfem-data" / "results"
    meshes.mkdir(parents=True)
    results.mkdir(parents=True)
    mesh_id = f"mesh-{job}"
    nid = lambda i, j: 1 + i + 11 * j  # noqa: E731
    ids = np.arange(1, 122, dtype=np.int64)
    xyz = np.array([[i / 10, j / 10, 0.0] for j in range(11) for i in range(11)])
    qids = np.arange(1, 101, dtype=np.int64)
    conn = np.array([[nid(i, j), nid(i + 1, j), nid(i + 1, j + 1), nid(i, j + 1)] for j in range(10) for i in range(10)], dtype=np.int64)
    central = [1 + i + 10 * j for j in range(3, 7) for i in range(3, 7)]
    others = [int(q) for q in qids if int(q) not in central]
    payload = {"mesh": {"elements_of_face": {"5": central, "1": others}}} if lineage else {"mesh": {}}
    codec = np.frombuffer(json.dumps(payload).encode("utf-8"), dtype=np.uint8)
    with h5py.File(meshes / f"{mesh_id}.anymesh.h5", "w") as m:
        _root(m, schema="anyfem.mesh", version=1, artifact_id=mesh_id, document_id=doc, model_hash="sha256:model", mesh_hash="sha256:mesh", complete=True)
        m.create_dataset("mesh/nodes/ids", data=ids)
        m.create_dataset("mesh/nodes/xyz", data=xyz)
        m.create_dataset("mesh/elements/quads/ids", data=qids)
        m.create_dataset("mesh/elements/quads/connectivity", data=conn)
        m.create_dataset("mesh/codec_json", data=codec)
    with h5py.File(results / f"{job}.anyres.h5", "w") as r:
        _root(r, schema="anyfem.result", version=1, artifact_id=job, document_id=doc, model_hash="sha256:model", mesh_hash="sha256:mesh",
              analysis_hash="sha256:a", mesh_id=mesh_id, partial=False, complete=True)
        r.create_dataset("fields/displacement/values", data=parent_field(perturb)[None])
        r["fields/displacement"].attrs["components"] = '["ux","uy","uz","rx","ry","rz"]'
        r["fields/displacement"].attrs["provenance"] = "{}"
        r.create_dataset("tables/displacement_node_ids", data=ids)


def patch_of(root: Path, job: str):
    pair = next(p for p in psp.scan_artifacts(root)["pairs"] if p["result"]["role"] == "parent_shell")
    P = psp.load_parent(Path(pair["result"]["path"]), Path(pair["mesh"]["path"]))
    return psp.parent_patch(P, 5)


def kuhn_mesh(n: int, layers: int):
    """Positively oriented Kuhn Tet4 lattice over the detail box: zero-based rows, outward end facets (z = +0.005)."""

    row = lambda i, j, k: i + (n + 1) * (j + (n + 1) * k)  # noqa: E731
    coords = np.zeros(((n + 1) ** 2 * (layers + 1), 3))
    for k in range(layers + 1):
        for j in range(n + 1):
            for i in range(n + 1):
                coords[row(i, j, k)] = (0.4 + 0.2 * i / n, 0.4 + 0.2 * j / n, -0.005 + 0.01 * k / layers)
    cells, E = [], np.eye(3, dtype=int)
    for k in range(layers):
        for j in range(n):
            for i in range(n):
                for perm in itertools.permutations(range(3)):
                    v, path = np.zeros(3, int), [np.zeros(3, int)]
                    for a in perm:
                        v = v + E[a]
                        path.append(v.copy())
                    quad = [row(i + p[0], j + p[1], k + p[2]) for p in path]
                    if np.linalg.det(np.stack([coords[quad[1]] - coords[quad[0]], coords[quad[2]] - coords[quad[0]],
                                               coords[quad[3]] - coords[quad[0]]])) < 0:
                        quad[2], quad[3] = quad[3], quad[2]
                    cells.append(quad)
    tris = []
    for j in range(n):
        for i in range(n):
            a, b, c, d = row(i, j, layers), row(i + 1, j, layers), row(i + 1, j + 1, layers), row(i, j + 1, layers)
            tris += [[a, b, c], [a, c, d]]
    return coords, np.asarray(cells, dtype=np.int64), np.asarray(tris, dtype=np.int64)


def unique_edges(cells: np.ndarray) -> np.ndarray:
    pairs = np.concatenate([cells[:, [a, b]] for a, b in itertools.combinations(range(4), 2)])
    return np.unique(np.sort(pairs, axis=1), axis=0)


def kuhn_edge_count(n: int, layers: int) -> int:
    """Independent closed form: axis edges, face diagonals and body diagonals of the Kuhn lattice."""

    return (2 * n * (n + 1) * (layers + 1) + (n + 1) ** 2 * layers + n * n * (layers + 1)
            + 2 * n * (n + 1) * layers + n * n * layers)


def stations(order: int):
    """Barycentric stations and volume-fraction weights of the rule the solver uses for each order."""

    if order == 1:
        a, b = (5 + 3 * 5 ** 0.5) / 20, (5 - 5 ** 0.5) / 20
        rows = [[b if c != v else a for c in range(4)] for v in range(4)]
        return np.array(rows), np.full(4, 0.25)
    s = (5 / 14) ** 0.5
    a, b = (1 + s) / 4, (1 - s) / 4
    rows, frac = [[0.25] * 4], [-148 / 1875]
    for v in range(4):
        rows.append([11 / 14 if c == v else 1 / 14 for c in range(4)])
        frac.append(343 / 7500)
    for i, j in itertools.combinations(range(4), 2):
        rows.append([a if c in (i, j) else b for c in range(4)])
        frac.append(56 / 375)
    return np.array(rows), np.array(frac)


def lateral_positions(pos: np.ndarray):
    near = lambda col, value: np.abs(pos[:, col] - value) < 1e-12  # noqa: E731
    mask = near(0, 0.4) | near(0, 0.6) | near(1, 0.4) | near(1, 0.6)
    return [int(p) for p in np.flatnonzero(mask)]


def make_child(root: Path, child_id: str, patch, *, doc: str = DOC_O, source_job: str = JOB_O, n: int = 2, layers: int = 2, order: int = 1,
               order_attr=None, partial: bool = False, mesh_hash: str = "hash-child", bad_point: bool = False, shift: float = 0.0,
               drop_mesh: bool = False, tweak=None) -> None:
    """Write a result/mesh pair of the supported codec.  ``tweak(arrays)`` may damage any table before it is written."""

    results = root / "children" / "results"
    meshes = root / "children" / "meshes"
    results.mkdir(parents=True, exist_ok=True)
    meshes.mkdir(parents=True, exist_ok=True)
    coords, cells, tris = kuhn_mesh(n, layers)
    nv = len(coords)
    pos = coords if order == 1 else np.vstack([coords, 0.5 * (coords[unique_edges(cells)[:, 0]] + coords[unique_edges(cells)[:, 1]])])
    comp = np.tile(np.arange(3), len(pos))
    ccoords = np.repeat(pos, 3, axis=0)
    lateral = lateral_positions(pos)
    dofs = np.array([3 * p + c for p in lateral for c in range(3)], dtype=np.int64)
    field, _ = sr.evaluate_patch(patch, pos[lateral])
    row_of = {p: i for i, p in enumerate(lateral)}
    values = np.array([field[row_of[int(d) // 3], int(d) % 3] for d in dofs]) + shift
    react = np.random.default_rng(3).normal(size=dofs.size) * 10.0
    unit = np.eye(3)[comp[dofs]]
    force = (react[:, None] * unit).sum(axis=0)
    moment = np.cross(ccoords[dofs], react[:, None] * unit).sum(axis=0)
    bary, frac = stations(order)
    corner_xyz = coords[cells]
    rcoords = np.einsum("qv,mvd->mqd", bary, corner_xyz)
    if bad_point:
        rcoords[0, 0] = (10.0, 10.0, 10.0)
    T = np.stack([corner_xyz[:, 1] - corner_xyz[:, 0], corner_xyz[:, 2] - corner_xyz[:, 0], corner_xyz[:, 3] - corner_xyz[:, 0]], axis=2)
    vol = np.linalg.det(T) / 6.0
    weights = frac[None, :] * vol[:, None]
    S0 = np.array([[1.0, 0.2, 0.0], [0.2, 2.0, 0.1], [0.0, 0.1, 3.0]]) * 1e6
    stress = np.broadcast_to(S0, (len(cells), len(frac), 3, 3)).copy()
    node_ids = np.arange(nv, dtype=np.int64) + NODE_OFFSET
    cell_ids = np.arange(len(cells), dtype=np.int64) + CELL_OFFSET
    bids = np.arange(len(tris), dtype=np.int64) + BOUNDARY_OFFSET
    arr = {"coords": coords, "cells": cells, "facets": tris, "node_ids": node_ids, "cell_ids": cell_ids, "bids": bids,
           "coefficients": pos.reshape(-1).copy(), "ccoords": ccoords, "comp": comp, "dofs": dofs, "values": values, "react": react,
           "rcoords": rcoords, "weights": weights, "stress": stress, "strain": stress * 1e-11, "rcell_ids": cell_ids.copy(),
           "displacement": (coords * 1e-3)[None], "end": [int(b) for b in bids]}
    if tweak is not None:
        tweak(arr)
    mesh_id = f"{child_id}-mesh"
    with h5py.File(results / f"{child_id}.anyres.h5", "w") as r:
        _root(r, schema="anyfem.result", version=1, artifact_id=child_id, document_id=doc, model_hash="sha256:child-model", mesh_hash=mesh_hash,
              analysis_hash="sha256:b", mesh_id=mesh_id, partial=partial, complete=not partial)
        r.create_dataset("fields/displacement/values", data=arr["displacement"])
        r["fields/displacement"].attrs["components"] = '["ux","uy","uz"]'
        r["fields/displacement"].attrs["provenance"] = json.dumps({"child_kind": "solid", "element_type": "tet4",
                                                                  "order": order if order_attr is None else order_attr})
        t = r.create_group("tables")
        t.create_dataset("coefficients", data=arr["coefficients"])
        t.create_dataset("coefficient_components", data=arr["comp"])
        t.create_dataset("coefficient_coordinates", data=arr["ccoords"])
        t.create_dataset("displacement_node_ids", data=arr["node_ids"])
        t.create_dataset("prescribed_dofs", data=arr["dofs"])
        t.create_dataset("prescribed_values", data=arr["values"])
        t.create_dataset("reactions", data=arr["react"])
        t.create_dataset("interface_force", data=force)
        t.create_dataset("interface_moment", data=moment)
        t.create_dataset("moment_origin", data=np.zeros(3))
        t.create_dataset("skin_force", data=np.array([0.0, 0.0, 400.0]))
        t.create_dataset("skin_moment", data=np.zeros(3))
        t.create_dataset("recovery_cell_ids", data=arr["rcell_ids"])
        t.create_dataset("recovery_coordinates", data=arr["rcoords"])
        t.create_dataset("recovery_weights", data=arr["weights"])
        t.create_dataset("recovery_stress", data=arr["stress"])
        t.create_dataset("recovery_strain", data=arr["strain"])
        t.create_dataset("solid_fields", data=json.dumps({"fields": ["displacement"]}), dtype=STR)
        t.create_dataset("solid_interface", data=json.dumps({"source_job": source_job}), dtype=STR)
        r.create_dataset("metadata/provenance_json", data=json.dumps({"submodel": {"source_job_id": source_job}}), dtype=STR)
        r.create_dataset("metadata/summary_json", data="{}", dtype=STR)
        r.create_dataset("metadata/diagnostics_json", data="[]", dtype=STR)
    if drop_mesh:
        return
    with h5py.File(meshes / f"{mesh_id}.anymesh.h5", "w") as m:
        _root(m, schema="anyfem.solid_mesh", version=2, artifact_id=mesh_id, document_id=doc, model_hash="sha256:child-model", mesh_hash=mesh_hash,
              element_type="tet4", complete=True)
        g = m.create_group("solid_mesh")
        g.attrs["geometry_order"] = 1
        g.attrs["units"] = "m"
        g.create_dataset("cells", data=arr["cells"])
        g.create_dataset("coordinates", data=arr["coords"])
        g.create_dataset("node_ids", data=arr["node_ids"])
        g.create_dataset("cell_ids", data=arr["cell_ids"])
        g.create_dataset("boundary_facets", data=arr["facets"])
        g.create_dataset("boundary_ids", data=arr["bids"])
        g.create_dataset("face_owner_face_ids", data=np.zeros(len(arr["facets"]), dtype=np.int64))
        g.create_dataset("groups_json", data=json.dumps({"node_groups": {}, "cell_groups": {},
                                                         "boundary_groups": {"end": arr["end"]}}), dtype=STR)
        g.create_dataset("provenance_json", data="{}", dtype=STR)


def surrogate_manifest(root: Path, runs=()) -> None:
    manifest = {"study": sr.STUDY_ID, "schema": sr.SCHEMA, "job_id": JOB_S, "face_id": 5, "project": "parent.anyfem",
                "material": {"elastic_modulus": 2.1e11, "poisson_ratio": 0.3}, "traction": [0.0, 0.0, 1.0e4], "z_end": 0.005,
                "skin_side": "top", "box": list(sr.DETAIL_BOX), "runs": list(runs)}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def world(tmp_path: Path, *, child: bool = True, smoke_perturb: float = 0.0, original_parent: bool = True, **child_kwargs):
    orig, surr = tmp_path / "original", tmp_path / "smoke"
    orig.mkdir()
    surr.mkdir()
    if original_parent:
        make_parent(orig, DOC_O, JOB_O)
    make_parent(surr, DOC_S, JOB_S, perturb=smoke_perturb)
    surrogate_manifest(surr)
    if child:
        name = f"child-p{child_kwargs.get('order', 1)}-2x2"
        make_child(orig, name, patch_of(surr, JOB_S) if not original_parent else patch_of(orig, JOB_O), **child_kwargs)
    return orig, surr


def tree_digest(root: Path):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


# ----------------------------------------------------------------------
# prepare: binding
# ----------------------------------------------------------------------
def test_prepare_binds_pair_proves_level_and_order_and_writes_decks(tmp_path):
    orig, surr = world(tmp_path)
    pm = psp.prepare(orig, surr, tmp_path / "out")
    assert pm["status"] == "ready", pm["blockers"]
    (row,) = pm["children"]["bound"]
    assert row["key"] == "C3D8_2x2_p1" and (row["order"], row["divisions"], row["layers"]) == (1, 2, 2)
    assert row["binding"]["checks"]["level_order"]["cells"] == 6 * 2 * 2 * 2
    assert row["z_end"] == pytest.approx(0.005, abs=1e-12)  # the +z skin, found through the explicit boundary id -> facet row table
    assert row["numerical_hashes"]["prescribed_values"] and row["result"]["sha256"] and row["mesh"]["sha256"]
    assert [m["key"] for m in pm["children"]["missing"]] == ["C3D20_2x2_p2"]
    assert pm["parents"]["equivalence"]["equivalent"] and pm["parents"]["equivalence"]["displacement_bitwise_equal"]
    assert pm["physical"]["face_id_original"] == 5 and pm["physical"]["traction_agrees"]
    names = sorted(d["inp"] for d in pm["decks"])
    assert names == ["ref_C3D20_2x2x2.inp", "ref_C3D8_2x2x2.inp"]
    for deck in pm["decks"]:
        assert (tmp_path / "out" / deck["inp"]).is_file() and deck["readback"]["ok"], deck["readback"]["checks"]
        assert hashlib.sha256((tmp_path / "out" / deck["inp"]).read_bytes()).hexdigest() == deck["sha256"]
    assert pm["originals_unchanged_after_prepare"] is True


def test_p2_child_binds_with_the_eleven_station_rule_and_a_negative_centroid_weight(tmp_path):
    orig, surr = world(tmp_path, order=2)
    pm = psp.prepare(orig, surr, tmp_path / "out")
    assert pm["status"] == "ready", pm["blockers"]
    (row,) = pm["children"]["bound"]
    assert row["key"] == "C3D20_2x2_p2" and row["order"] == 2 and row["z_end"] == pytest.approx(0.005, abs=1e-12)
    assert [m["key"] for m in pm["children"]["missing"]] == ["C3D8_2x2_p1"]
    card = row["binding"]["checks"]["cardinality"]
    nv = 27
    ne = kuhn_edge_count(2, 2)
    assert (card["nodes"], card["order"]) == (nv, 2)
    assert card["coefficients"] == card["coefficients_expected"] == 3 * (nv + ne)  # vertices plus unique mesh edges, 3 components
    assert card["public_displacement_entries"] == 3 * nv  # the public field has vertex entries only
    assert card["component_counts"] == [nv + ne] * 3 and card["positions_distinct"] and card["max_position_error"] <= 1e-12
    assert row["binding"]["checks"]["mesh_arrays"]["unique_edges"] == ne == len(unique_edges(kuhn_mesh(2, 2)[1]))
    quad = row["binding"]["checks"]["tensor_locations_and_coverage"]["quadrature"]
    assert quad["rule"] == "tet-11-station" and quad["points_per_cell"] == 11
    assert quad["negative_weights"] == 6 * 2 * 2 * 2  # exactly the centroid station of every cell
    assert quad["station_counts_ok"] and quad["max_signed_weight_rel_error"] <= 1e-9


def test_ids_overlapping_the_row_range_are_never_taken_for_rows(tmp_path):
    # NODE_OFFSET/CELL_OFFSET/BOUNDARY_OFFSET make every external id also a valid row: the end skin must still be z=+0.005
    orig, surr = world(tmp_path)
    pm = psp.prepare(orig, surr, tmp_path / "out")
    (row,) = pm["children"]["bound"]
    assert row["z_end"] == pytest.approx(0.005, abs=1e-12)
    assert row["binding"]["checks"]["end_plane"]["end_facets"] == 8
    # cells given as external node ids (shifted past the last row) are refused, not reinterpreted
    orig2, surr2 = tmp_path / "w2" / "o", tmp_path / "w2" / "s"
    (tmp_path / "w2").mkdir()
    orig2.mkdir()
    surr2.mkdir()
    make_parent(orig2, DOC_O, JOB_O)
    make_parent(surr2, DOC_S, JOB_S)
    surrogate_manifest(surr2)
    make_child(orig2, "child-ids", patch_of(orig2, JOB_O), tweak=lambda a: a.__setitem__("cells", a["cells"] + NODE_OFFSET))
    pm2 = psp.prepare(orig2, surr2, tmp_path / "out2")
    assert pm2["children"]["bound"] == []
    (bad,) = pm2["children"]["unbound"]
    assert "mesh_arrays" in bad["binding"]["failed_checks"]
    assert bad["binding"]["checks"]["mesh_arrays"]["error"].startswith("StudyError")


def test_recovery_rows_in_any_order_are_mapped_through_the_explicit_cell_id_table(tmp_path):
    def reorder(a):
        perm = np.random.default_rng(5).permutation(len(a["weights"]))
        for key in ("rcell_ids", "rcoords", "weights", "stress", "strain"):
            a[key] = a[key][perm]

    orig, surr = world(tmp_path, order=2, tweak=reorder)
    pm = psp.prepare(orig, surr, tmp_path / "out")
    assert [r["key"] for r in pm["children"]["bound"]] == ["C3D20_2x2_p2"], pm["children"]["unbound"]


def test_prepare_never_writes_into_the_inputs(tmp_path):
    orig, surr = world(tmp_path)
    before = (tree_digest(orig), tree_digest(surr))
    psp.prepare(orig, surr, tmp_path / "out")
    assert (tree_digest(orig), tree_digest(surr)) == before


def test_prepare_refuses_existing_or_overlapping_output(tmp_path):
    orig, surr = world(tmp_path)
    (tmp_path / "out").mkdir()
    with pytest.raises(sr.StudyError) as exc:
        psp.prepare(orig, surr, tmp_path / "out")
    assert exc.value.code == "OUT_EXISTS"
    with pytest.raises(sr.StudyError) as exc:
        psp.prepare(orig, surr, orig / "inside")
    assert exc.value.code == "OUT_OVERLAPS_INPUT"


def test_inequivalent_smoke_parent_blocks_decks_and_keeps_the_evidence(tmp_path):
    orig, surr = world(tmp_path, smoke_perturb=1e-6)
    pm = psp.prepare(orig, surr, tmp_path / "out")
    assert pm["status"] == "blocked"
    assert [b["code"] for b in pm["blockers"]] == ["PARENT_NOT_EQUIVALENT"]
    assert pm["parents"]["equivalence"]["displacement_rel_diff"] > psp.PARENT_EQUIV_TOL
    assert pm["decks"] == [] and not list((tmp_path / "out").glob("*.inp"))
    assert (tmp_path / "out" / psp.MANIFEST_NAME).is_file()
    with pytest.raises(sr.StudyError) as exc:
        psp.plan_child(tmp_path / "out", 2, 2, 2)
    assert exc.value.code == "PREPARED_BLOCKED"


def test_missing_original_parent_is_a_typed_blocker_not_a_fabricated_project(tmp_path):
    orig, surr = world(tmp_path, original_parent=False)
    pm = psp.prepare(orig, surr, tmp_path / "out")
    assert pm["status"] == "blocked" and pm["blockers"][0]["code"] == "PARENT_ABSENT"
    assert pm["decks"] == [] and "parents" not in pm
    # the child sidecars stay visible; without the parent they cannot be bound to a source job
    assert len(pm["children"]["bound"]) + len(pm["children"]["unbound"]) == 1


def _weights_centroid_positive(a):
    a["weights"] = np.abs(a["weights"])


def _drop_one_midpoint(a):
    keep = np.ones(len(a["comp"]), dtype=bool)
    keep[-3:] = False  # the last edge midpoint, all three components
    a["coefficients"], a["ccoords"], a["comp"] = a["coefficients"][keep], a["ccoords"][keep], a["comp"][keep]


def _move_one_midpoint(a):
    a["ccoords"] = a["ccoords"].copy()
    a["ccoords"][-3:] += 1e-3


def _scale_one_weight(a):
    a["weights"] = a["weights"].copy()
    a["weights"][4, 3] *= 1.01


def _unknown_recovery_id(a):
    a["rcell_ids"] = a["rcell_ids"].copy()
    a["rcell_ids"][0] = 10 ** 6


def _public_field_with_midpoints(a):
    a["displacement"] = np.zeros((1, len(a["coefficients"]) // 3, 3))


def _two_cells_share_a_recovery_row(a):
    a["rcell_ids"] = a["rcell_ids"].copy()
    a["rcell_ids"][1] = a["rcell_ids"][0]


@pytest.mark.parametrize("kwargs,failed", [
    ({"partial": True}, "complete"),
    ({"source_job": "some-other-job"}, "source_job"),
    ({"order_attr": 2}, "cardinality"),  # P1 geometry claiming P2: coefficient count and positions disagree
    ({"order_attr": 3}, "level_order"),  # order only comes from provenance and must be 1 or 2
    ({"order": 2, "order_attr": 1}, "cardinality"),  # P2 coefficients claiming P1
    ({"bad_point": True}, "tensor_locations_and_coverage"),
    ({"order": 2, "tweak": _weights_centroid_positive}, "tensor_locations_and_coverage"),  # the signed rule is required
    ({"order": 2, "tweak": _scale_one_weight}, "tensor_locations_and_coverage"),
    ({"order": 2, "tweak": _drop_one_midpoint}, "cardinality"),
    ({"order": 2, "tweak": _move_one_midpoint}, "cardinality"),
    ({"order": 2, "tweak": _public_field_with_midpoints}, "cardinality"),
    ({"tweak": _unknown_recovery_id}, "tensor_locations_and_coverage"),
    ({"tweak": _two_cells_share_a_recovery_row}, "tensor_locations_and_coverage"),
])
def test_damaged_or_unbindable_children_stay_unavailable(tmp_path, kwargs, failed):
    orig, surr = world(tmp_path, **kwargs)
    pm = psp.prepare(orig, surr, tmp_path / "out")
    assert pm["children"]["bound"] == []
    (row,) = pm["children"]["unbound"]
    assert failed in row["binding"]["failed_checks"]
    assert {m["key"] for m in pm["children"]["missing"]} == {"C3D8_2x2_p1", "C3D20_2x2_p2"}


def test_unresolved_recovery_id_is_a_typed_error_not_a_guess(tmp_path):
    orig, surr = world(tmp_path, tweak=_unknown_recovery_id)
    pm = psp.prepare(orig, surr, tmp_path / "out")
    (row,) = pm["children"]["unbound"]
    assert "ID_UNRESOLVED" in repr(row["binding"]["checks"]["tensor_locations_and_coverage"]["error"]) or \
        "external ids" in row["binding"]["checks"]["tensor_locations_and_coverage"]["error"]


def test_pairing_requires_matching_mesh_hash_and_an_existing_mesh(tmp_path):
    orig, surr = world(tmp_path)
    mesh_file = next((orig / "children" / "meshes").glob("*.h5"))
    with h5py.File(mesh_file, "r+") as m:
        m.attrs["mesh_hash"] = "tampered"
    pm = psp.prepare(orig, surr, tmp_path / "out")
    assert "pairing" in pm["children"]["unbound"][0]["binding"]["failed_checks"]
    orig2 = tmp_path / "second"
    orig2.mkdir()
    make_parent(orig2, DOC_O, JOB_O)
    make_child(orig2, "orphan", patch_of(orig2, JOB_O), drop_mesh=True)
    scan = psp.scan_artifacts(orig2)
    assert [r["attrs"]["artifact_id"] for r in scan["orphan_results"]] == ["orphan"]
    assert all(p["result"]["attrs"]["artifact_id"] != "orphan" for p in scan["pairs"])


def test_duplicate_pairs_are_kept_visible_not_double_counted(tmp_path):
    orig, surr = world(tmp_path)
    make_child(orig, "child-p1-copy", patch_of(orig, JOB_O))
    pm = psp.prepare(orig, surr, tmp_path / "out")
    assert len(pm["children"]["bound"]) == 1 and len(pm["children"]["duplicates"]) == 1
    assert pm["children"]["duplicates"][0]["duplicate_of"] == pm["children"]["bound"][0]["result"]["artifact_id"]


def test_deck_readback_detects_a_changed_load_and_a_changed_support(tmp_path):
    orig, surr = world(tmp_path)
    pm = psp.prepare(orig, surr, tmp_path / "out")
    deck = next(d for d in pm["decks"] if d["element"] == "C3D8")
    text = (tmp_path / "out" / deck["inp"]).read_text(encoding="ascii")
    mesh = sr.reference_mesh("C3D8", deck["n"], deck["layers"])
    lateral = mesh.lateral()
    patch = patch_of(orig, JOB_O)
    values, _ = sr.evaluate_patch(patch, [mesh.nodes[k] for k in lateral])
    prescribed = {k: tuple(v) for k, v in zip(lateral, values)}
    traction = pm["reference"]["traction"]
    good = psp.deck_readback(text, mesh, 2.1e11, 0.3, prescribed, traction, "top")
    assert good["ok"] and abs(good["expected_total"][2] - 400.0) < 1e-9
    lines = text.splitlines()
    index = next(i for i, ln in enumerate(lines) if ln == "*CLOAD") + 1
    node, dof, value = lines[index].split(", ")
    lines[index] = f"{node}, {dof}, {float(value) * 1.001:.12e}"
    assert not psp.deck_readback("\n".join(lines) + "\n", mesh, 2.1e11, 0.3, prescribed, traction, "top")["checks"]["applied_loads"]
    assert not psp.deck_readback(text, mesh, 2.0e11, 0.3, prescribed, traction, "top")["checks"]["material"]
    assert not psp.deck_readback(text, mesh, 2.1e11, 0.3, prescribed, traction, "bottom")["checks"]["signed_gauge"]


# ----------------------------------------------------------------------
# quadrature and index helpers
# ----------------------------------------------------------------------
@pytest.mark.parametrize("order,points,negative", [(1, 4, 0), (2, 11, 1)])
def test_known_rules_reproduce_the_volume_and_degree_two_moments(order, points, negative):
    bary, frac = stations(order)
    rule = psp.QUADRATURE_RULES[points]
    assert len(bary) == points and int(np.count_nonzero(frac < 0)) == negative
    assert frac.sum() == pytest.approx(1.0, rel=1e-14)
    assert sum(c[2] for c in rule["classes"]) == points and sum(c[1] * c[2] for c in rule["classes"]) == pytest.approx(1.0, rel=1e-14)
    # exact barycentric moments over a tetrahedron of volume V: int(l_i l_j) = V/10 for i == j and V/20 otherwise
    for i in range(4):
        for j in range(4):
            exact = (2.0 if i == j else 1.0) / 20.0
            assert float(np.sum(frac * bary[:, i] * bary[:, j])) == pytest.approx(exact, rel=1e-12)


def test_check_quadrature_accepts_signed_weights_and_refuses_wrong_ones():
    bary, frac = stations(2)
    volume = np.array([1.0, 2.0])
    weights = frac[None, :] * volume[:, None]
    lam = np.broadcast_to(bary, (2,) + bary.shape).copy()
    ok, info = psp.check_quadrature(2, lam, weights, volume)
    assert ok and info["negative_weights"] == 2 and info["rule"] == "tet-11-station"
    shuffled = np.random.default_rng(1).permutation(11)
    assert psp.check_quadrature(2, lam[:, shuffled], weights[:, shuffled], volume)[0]  # station order is irrelevant
    assert not psp.check_quadrature(2, lam, np.abs(weights), volume)[0]  # an all-positive "fix" breaks the rule
    assert not psp.check_quadrature(2, lam[:, :4], weights[:, :4], volume)[0]  # P2 needs the eleven stations
    moved = lam.copy()
    moved[0, 3] = bary[4]  # two stations coincide: not distinct physical locations
    assert not psp.check_quadrature(2, moved, weights, volume)[0]
    nan = weights.copy()
    nan[0, 0] = np.nan
    assert not psp.check_quadrature(2, lam, nan, volume)[0]


def test_index_helpers_demand_rows_and_explicit_ids():
    rows = np.array([[0, 1, 2, 3]])
    assert np.array_equal(psp._index_rows(rows, 4, 4, "cells"), rows)
    for bad in (rows + 1, rows - 1, rows.astype(float), rows[:, :3]):
        with pytest.raises(sr.StudyError):
            psp._index_rows(bad, 4, 4, "cells")
    ids = np.array([10, 11, 12])
    assert psp._id_rows(ids, np.array([12, 10]), "x").tolist() == [2, 0]
    with pytest.raises(sr.StudyError) as exc:
        psp._id_rows(ids, np.array([0, 1]), "x")  # row-like integers are not ids
    assert exc.value.code == "ID_UNRESOLVED"
    with pytest.raises(sr.StudyError):
        psp._id_rows(np.array([1, 1, 2]), np.array([1]), "x")


# ----------------------------------------------------------------------
# child phase: refusals that need no ANYfem stack
# ----------------------------------------------------------------------
def test_child_plan_enforces_missing_only_registered_and_no_duplicate(tmp_path):
    orig, surr = world(tmp_path)
    psp.prepare(orig, surr, tmp_path / "out")
    prepared = tmp_path / "out"
    with pytest.raises(sr.StudyError) as exc:
        psp.plan_child(prepared, 1, 2, 2)
    assert exc.value.code == "ORIGINAL_PAIR_EXISTS"
    with pytest.raises(sr.StudyError) as exc:
        psp.plan_child(prepared, 2, 3, 2)
    assert exc.value.code == "LEVEL_UNREGISTERED"
    with pytest.raises(sr.StudyError) as exc:
        psp.plan_child(prepared, 3, 2, 2)
    assert exc.value.code == "ORDER_INVALID"
    plan = psp.plan_child(prepared, 2, 2, 2)
    assert plan["key"] == "C3D20_2x2_p2"
    psp.append_ledger(prepared, {"state": "started", "key": plan["key"]})
    with pytest.raises(sr.StudyError) as exc:
        psp.plan_child(prepared, 2, 2, 2)
    assert exc.value.code == "DUPLICATE_CHILD"


# ----------------------------------------------------------------------
# read: methodology
# ----------------------------------------------------------------------
def test_read_checks_trace_work_closure_and_types_the_missing_pieces(tmp_path):
    orig, surr = world(tmp_path)
    psp.prepare(orig, surr, tmp_path / "out")
    report = psp.read_prepared(tmp_path / "out")
    child = report["children"]["C3D8_2x2_p1"]
    assert child["status"] == "ok"
    assert child["boundary"]["ok"] and child["boundary"]["max_rel"] <= 1e-9
    assert child["boundary"]["coefficients"] == 3 * 24  # every held coefficient (24 lateral nodes x 3)
    assert child["boundary"]["failing_coefficients"] == 0
    # signed work from the stored tables, recomputed here independently
    pair = next(p for p in psp.scan_artifacts(orig)["pairs"] if p["result"]["role"] == "solid_child")
    with h5py.File(pair["result"]["path"], "r") as r:
        expected = float(np.sum(np.asarray(r["tables/reactions"]) * np.asarray(r["tables/prescribed_values"])))
    assert child["interface_work"]["total"] == pytest.approx(expected, rel=1e-12)
    assert child["closure"]["status"] == "pass" and child["closure"]["gating"] is False
    assert child["tensor_locations"]["ok"] and child["tensor_locations"]["min_barycentric_slack"] >= 0.0
    assert child["skin_vector_observation"]["end_area"] == pytest.approx(0.04, rel=1e-12)
    assert child["run_identity"] == "child-p1-2x2"
    # the CCX decks were not run: typed, preserved, not guessed
    assert report["reference"]["ref_C3D8_2x2x2"]["code"] == "CCX_OUTPUT_MISSING"
    assert report["children"]["C3D20_2x2_p2"]["code"] == "CHILD_ABSENT"
    assert report["status"] == "incomplete" and report["partial"]["bound_inputs_unchanged"] is True
    assert report["partial"]["child_origin"] == {"C3D8_2x2_p1": "original", "C3D20_2x2_p2": "absent"}
    entry = next(c for c in report["comparisons"] if c["run"] == "C3D20_2x2_p2")
    assert entry["status"] == "child_unavailable"
    again = psp.read_prepared(tmp_path / "out")  # a second read writes a second report
    assert again["status"] == "incomplete"
    assert sorted(p.name for p in (tmp_path / "out" / "reports").iterdir()) == ["read_0001.json", "read_0002.json"]


@pytest.mark.parametrize("doc,ledger_job,valid", [(DOC_S, JOB_S, True), (DOC_O, JOB_S, False), (DOC_S, JOB_O, False)])
def test_read_binds_completed_child_to_its_surrogate_document_and_job(tmp_path, doc, ledger_job, valid):
    orig, surr = world(tmp_path)
    prepared = tmp_path / "out"
    psp.prepare(orig, surr, prepared)
    child_dir = tmp_path / "new"
    child_dir.mkdir()
    make_child(child_dir, "fine", patch_of(surr, JOB_S), order=2, doc=doc, source_job=JOB_S)
    pair, = psp.scan_artifacts(child_dir)["pairs"]
    psp.append_ledger(prepared, {"state": "completed", "key": "C3D20_2x2_p2",
                                "source_job_id": ledger_job, "result_artifact": "fine",
                                **{f"{which}_{field}": pair[which][field] for which in ("result", "mesh")
                                   for field in ("path", "sha256")}})
    row = psp.read_prepared(prepared)["children"]["C3D20_2x2_p2"]
    if valid:
        assert row["status"] == "ok"
    else:
        assert row["code"] == "CHILD_UNBOUND"


def test_read_of_a_p2_child_traces_vertex_and_edge_midpoint_coefficients(tmp_path):
    orig, surr = world(tmp_path, order=2)
    psp.prepare(orig, surr, tmp_path / "out")
    report = psp.read_prepared(tmp_path / "out")
    child = report["children"]["C3D20_2x2_p2"]
    assert child["status"] == "ok", child
    coords, cells, _ = kuhn_mesh(2, 2)
    edges = unique_edges(cells)
    pos = np.vstack([coords, 0.5 * (coords[edges[:, 0]] + coords[edges[:, 1]])])
    assert child["boundary"]["coefficients"] == 3 * len(lateral_positions(pos)) and child["boundary"]["ok"]
    assert child["tensor_locations"]["ok"] and child["tensor_locations"]["quadrature"]["negative_weights"] == 48
    # signed weights are used as signed numbers: the region volume is still positive although every centroid weight is negative
    assert child["stress_region"]["volume"] > 0.0


def test_a_trace_error_above_1e9_is_reported_per_coefficient_and_fails_integrity(tmp_path):
    orig, surr = world(tmp_path, shift=1e-6)
    psp.prepare(orig, surr, tmp_path / "out")
    report = psp.read_prepared(tmp_path / "out")
    child = report["children"]["C3D8_2x2_p1"]
    assert child["status"] == "ok" and not child["boundary"]["ok"]
    assert child["boundary"]["failing_coefficients"] == child["boundary"]["coefficients"]
    assert len(child["boundary"]["worst"]) == 10 and child["boundary"]["abs_diff_sha256"]
    failed = [c for c in report["integrity"] if c["check"].startswith("boundary_transfer") and not c["ok"]]
    assert failed and report["status"] == "incomplete"


def test_changed_original_bytes_are_refused_at_read_time(tmp_path):
    orig, surr = world(tmp_path)
    psp.prepare(orig, surr, tmp_path / "out")
    result_file = next((orig / "children" / "results").glob("*.h5"))
    with h5py.File(result_file, "r+") as r:
        r.attrs["note"] = "edited after binding"
    report = psp.read_prepared(tmp_path / "out")
    assert report["children"]["C3D8_2x2_p1"]["code"] == "ARTIFACT_CHANGED"
    assert report["partial"]["bound_inputs_unchanged"] is False and report["status"] == "incomplete"


def test_ccx_interface_work_uses_rf_minus_withheld_load_and_print_precision(tmp_path):
    orig, surr = world(tmp_path)
    pm = psp.prepare(orig, surr, tmp_path / "out")
    out = tmp_path / "out"
    deck = next(d for d in pm["reference"]["decks"] if d["element"] == "C3D8")
    info = sr.parse_deck((out / deck["inp"]).read_text(encoding="ascii"))
    mesh = sr.reference_mesh("C3D8", deck["n"], deck["layers"])
    forces = sr.skin_forces(mesh, "top", pm["reference"]["traction"])
    rows = ["", " forces (fx,fy,fz) for set NPRESC and time  0.1000000E+01", ""]
    rng = np.random.default_rng(11)
    expected, printed = 0.0, {}
    for k in sorted(info["boundary"]):
        react = rng.normal(size=3) * 5.0
        rf = react + np.asarray(forces.get(k, (0.0, 0.0, 0.0)))
        tokens = [f"{v:.6E}" for v in rf]
        printed[k] = tokens
        rows.append(f"{k:8d} " + " ".join(tokens))
        expected += float(np.sum((np.array([float(t) for t in tokens]) - np.asarray(forces.get(k, (0.0, 0.0, 0.0)))) * np.asarray(info["boundary"][k])))
    (out / deck["dat"]).write_text("\n".join(rows) + "\n", encoding="utf-8")
    work = psp.reference_interface_work(out, pm["reference"], deck)
    assert work["total"] == pytest.approx(expected, rel=1e-12, abs=1e-18)
    assert work["held_nodes"] == len(info["boundary"]) and work["print_bound"] > 0.0
    child_like = {"interface_work": {"total": work["total"] * 1.5}}
    cmp = psp.compare_work(child_like, {"interface_work": work})
    assert cmp["same_sign"] and cmp["rel"] == pytest.approx(0.5, rel=1e-9) and cmp["difference_exceeds_print_bound"]


def test_the_driver_reuses_the_public_contracts_of_solid_reference():
    assert psp.sr is sr
    assert psp.TRACE_TOL == sr.INTEGRITY["boundary_transfer_rel"] == 1e-9
    assert psp.CLOSURE_TOL == sr.INTEGRITY["reference_equilibrium_rel"] == 1e-6
    assert psp.FORMAL_LEVELS == (LEVEL,)  # monkeypatched for speed; the module default is the registered formal preset
    assert tuple(tuple(l) for l in sr.PRESETS["formal"]["levels"]) == ((4, 2), (8, 4), (16, 8))


# ----------------------------------------------------------------------
# recorded development archives (skipped when the evidence folders are not present)
# ----------------------------------------------------------------------
needs_evidence = pytest.mark.skipif(
    not (EVIDENCE_ORIGINAL.is_dir() and EVIDENCE_SMOKE.is_dir() and (EVIDENCE_DETAILS / "assessed-complete-child-fields.json").is_file()
         and (EVIDENCE_DETAILS / "assessed-complete-child-traces.json").is_file()),
    reason="recorded development evidence folders are not available")

ACCEPTED_KEYS = {"C3D8_4x2_p1", "C3D20_4x2_p2", "C3D8_8x4_p1", "C3D20_8x4_p2", "C3D8_16x8_p1"}


@needs_evidence
def test_original_development_prepare_binds_all_five_children_and_all_six_decks(tmp_path, monkeypatch):
    monkeypatch.setattr(psp, "FORMAL_LEVELS", tuple(tuple(l) for l in sr.PRESETS["formal"]["levels"]))
    pm = psp.prepare(EVIDENCE_ORIGINAL, EVIDENCE_SMOKE, tmp_path / "out")
    assert pm["status"] == "ready", pm["blockers"]
    assert not [b for b in pm["blockers"] if b["code"] == "END_SKIN_DIFFERS"]
    assert {r["key"] for r in pm["children"]["bound"]} == ACCEPTED_KEYS
    assert pm["children"]["unbound"] == [] and pm["children"]["duplicates"] == []
    assert [m["key"] for m in pm["children"]["missing"]] == ["C3D20_16x8_p2"]
    for row in pm["children"]["bound"]:
        assert row["z_end"] == pytest.approx(0.005, abs=1e-12), row["key"]  # the earlier run read 0.0025 / 0.0 / 0.00375
        assert row["binding"]["failed_checks"] == []
    assert pm["physical"]["end_plane_agrees_with_original_children"] is True
    assert len(pm["decks"]) == 6 and all(d["readback"]["ok"] for d in pm["decks"])
    assert all((tmp_path / "out" / d["inp"]).is_file() for d in pm["decks"])
    assert pm["originals_unchanged_after_prepare"] is True
    p2 = [r for r in pm["children"]["bound"] if r["order"] == 2]
    assert len(p2) == 2 and all(r["binding"]["checks"]["tensor_locations_and_coverage"]["quadrature"]["rule"] == "tet-11-station" for r in p2)


@needs_evidence
def test_original_development_children_match_the_independently_observed_values(tmp_path, monkeypatch):
    monkeypatch.setattr(psp, "FORMAL_LEVELS", tuple(tuple(l) for l in sr.PRESETS["formal"]["levels"]))
    pm = psp.prepare(EVIDENCE_ORIGINAL, EVIDENCE_SMOKE, tmp_path / "out")
    assert pm["status"] == "ready", pm["blockers"]
    report = psp.read_prepared(tmp_path / "out")
    assert report["partial"]["bound_inputs_unchanged"] is True
    bound = {r["key"]: r for r in pm["children"]["bound"]}
    fields = json.loads((EVIDENCE_DETAILS / "assessed-complete-child-fields.json").read_text(encoding="utf-8"))
    traces = json.loads((EVIDENCE_DETAILS / "assessed-complete-child-traces.json").read_text(encoding="utf-8"))
    assert {psp.combo_key(f["order"], f["n"][0], f["n"][2]) for f in fields} == ACCEPTED_KEYS
    centre = np.array([sr.CENTRE[0], sr.CENTRE[1], 0.0])
    for item in fields:
        key = psp.combo_key(item["order"], item["n"][0], item["n"][2])
        row, child = bound[key], report["children"][key]
        assert child["status"] == "ok", (key, child)
        assert row["result"]["artifact_id"] == item["case_id"] and row["result"]["sha256"] == item["result_sha256"]
        assert row["mesh"]["sha256"] == item["mesh_sha256"]
        assert row["z_end"] == pytest.approx(item["z_end_m"], abs=1e-12)
        assert child["binding"]["checks"]["cardinality"]["coefficients"] == item["dofs"]
        assert child["boundary"]["coefficients"] == item["prescribed_count"]
        np.testing.assert_allclose(child["u_mid_centre"], item["u_mid_m"], rtol=1e-9, atol=1e-14, err_msg=key)
        np.testing.assert_allclose(child["u_end_centre"], item["u_end_m"], rtol=1e-9, atol=1e-14, err_msg=key)
        tensor = np.asarray(item["stress_tensor_Pa"], float)
        want = [tensor[a][b] for a, b in sr.VOIGT]
        np.testing.assert_allclose(child["stress_region"]["average"], want, rtol=1e-8, atol=1e-8 * float(np.abs(tensor).max()), err_msg=key)
        assert child["stress_region"]["volume"] == pytest.approx(item["region_m3"], rel=1e-9)
        assert child["interface_work"]["total"] == pytest.approx(item["work_J"], rel=1e-9)
        np.testing.assert_allclose(child["interface_force"], item["interface_force_N"], rtol=1e-9, atol=1e-10, err_msg=key)
        np.testing.assert_allclose(child["skin_force"], item["skin_force_N"], rtol=1e-9, atol=1e-10, err_msg=key)
        # the observation is about the plate centre; the stored moment is about the world origin (z of the centre is irrelevant at 1e-9)
        about_centre = np.asarray(child["interface_moment"]) - np.cross(centre, np.asarray(child["interface_force"]))
        np.testing.assert_allclose(about_centre, item["interface_moment_at_centre_Nm"], rtol=0.0, atol=1e-6, err_msg=key)
        assert child["closure"]["force_rel"] <= 1e-6 and child["closure"]["moment_rel"] <= 1e-6
    assert traces["parent_result_sha256"] == pm["parents"]["original"]["result"]["sha256"]
    assert traces["limit"] == psp.TRACE_TOL
    for item in traces["rows"]:
        key = psp.combo_key(item["order"], item["n"][0], item["n"][2])
        boundary = report["children"][key]["boundary"]
        assert item["status"] == "pass" and boundary["ok"], key
        assert boundary["coefficients"] == item["ndof"] and boundary["locations"] == item["locations"], key
        assert boundary["max_rel"] <= traces["limit"], key
