"""Recovery / comparison driver for a partially completed loaded shell-to-solid study.

Situation: the registered 16x16x8 stage of ``solid_reference.py`` may have timed out.  Some complete child
HDF5 pairs exist, the parent master project / study manifest may be absent.  Nothing here solves a parent,
launches CalculiX or edits an input folder.  Follows ``solid-recovery-proposal.md`` (independent assessor).

    python partial_solid_reference.py prepare --original ORIG --surrogate SMOKE --out NEW_DIR
    python partial_solid_reference.py child   --prepared NEW_DIR --order 2 --divisions 16 [--layers 8]
    python partial_solid_reference.py read    --prepared NEW_DIR

prepare  Read-only scan of ORIG (any layout, ``*.anyres.h5`` / ``*.anymesh.h5`` found recursively).  Identifies
         the parent shell result + mesh by HDF5 identifiers and codec, compares the whole parent mesh, the
         central-face Q4 lineage and the named 121x6 displacement array with the independently persisted smoke
         parent, and binds every complete original child pair (level and order proven from coordinates, cells
         and coefficient cardinality).  The material, traction and skin side are taken from the smoke study only
         when the parents are physically equivalent.  Writes the six C3D8/C3D20 decks of the formal preset with
         the ``solid_reference`` functions, parses each back and records exact identities.  Status ``ready`` or
         ``blocked``: a blocked preparation keeps its typed blockers and writes no decks.
child    One genuinely missing (order, divisions) on an isolated COPY of the verified smoke project through the
         public cut_solid_submodel / add_submodel / run_submodel / save_project / load_project route.  Refuses
         pairs that exist in the original or already in the ledger.  The project is saved the moment the run ends.
read     Reads the saved original and new child tables through an archive adapter (no claimed project),
         checks pairing, source fields, the 1e-9 parent trace at every held coefficient, true tensor locations,
         adds signed interface work for the tets and for CalculiX (from the bound .dat/decks, with printed
         precision) and assembles with ``solid_reference.assemble``.  Reports are written to new files.

Assumptions that can be wrong (each is reported as an observation or a typed status, never silently relaxed):
* ``mesh/codec_json`` stores ``elements_of_face`` as a mapping (or pair list) face id -> element ids anywhere inside
  the JSON; the first such key found is used.
* Child ``solid_mesh/cells`` and ``boundary_facets`` are ALWAYS zero-based rows into ``coordinates`` (supported codec
  ``anyfem.io.solid_submodel_artifact``); node/cell/boundary ids are separate external labels and are only ever
  mapped through an explicit id -> row table (``recovery_cell_ids``, the ``boundary_groups`` of ``groups_json``).
  Both orders use straight four-corner Tet4 geometry and three-corner boundary facets; the order is read from the
  result provenance, never from the cell width.  P1 coefficients sit on the vertices, P2 coefficients on the
  vertices plus the unique mesh edge midpoints; the public displacement field has 3*vertices entries only.
* P2 recovery uses the eleven-station tetrahedron rule whose centroid weight is legitimately negative: weights are
  checked as signed numbers against the known rule and the actual physical locations, never required positive.
* ``skin_force`` is compared with traction*area as an observation only (its sign / withheld convention is unproven).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

HERE = Path(__file__).resolve().parent


def _load_solid_reference():
    existing = sys.modules.get("solid_reference")
    if existing is not None and hasattr(existing, "reference_metrics"):
        return existing
    spec = importlib.util.spec_from_file_location("solid_reference", HERE / "solid_reference.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["solid_reference"] = module
    spec.loader.exec_module(module)
    return module


sr = _load_solid_reference()
StudyError = sr.StudyError

PARTIAL_KIND = "partial-solid-reference"
PARTIAL_SCHEMA = 1
MANIFEST_NAME = "partial_manifest.json"
LEDGER_NAME = "child_ledger.jsonl"
FORMAL_LEVELS: Tuple[Tuple[int, int], ...] = tuple(tuple(level) for level in sr.PRESETS["formal"]["levels"])
ORDERS: Tuple[int, ...] = tuple(sr.PRESETS["formal"]["orders"])
TRACE_TOL = sr.INTEGRITY["boundary_transfer_rel"]  # 1e-9 parent trace / coverage
CLOSURE_TOL = sr.INTEGRITY["reference_equilibrium_rel"]  # 1e-6 force / moment
PARENT_EQUIV_TOL = 1.0e-9  # relative, parent displacement vs smoke parent (named; recorded in the manifest)
GEOMETRY_TOL = 1.0e-9
CENTRAL_LO, CENTRAL_HI = sr.LINES[1], sr.LINES[2]  # 0.3 .. 0.7
EXPECTED_COMPONENTS = ["ux", "uy", "uz", "rx", "ry", "rz"]
ELEMENT_NAME = {1: "tet4", 2: "tet4"}  # the supported solid mesh codec stores straight Tet4 geometry for both orders
POSITION_TOL = 1.0e-12  # coefficient position vs. vertex / edge-midpoint, metres
STATION_TOL = 1.0e-8  # barycentric location of a stored quadrature point vs. the known rule
CHILD_TABLES = (
    "coefficient_components", "coefficient_coordinates", "coefficients", "displacement_node_ids", "group_dof_count",
    "group_force", "group_moment", "group_shared_dof_count", "interface_force", "interface_moment", "moment_origin",
    "ownership_dofs", "ownership_groups", "prescribed_dofs", "prescribed_values", "reactions", "recovery_cell_ids",
    "recovery_coordinates", "recovery_strain", "recovery_stress", "recovery_weights", "skin_force", "skin_moment")
PARTIAL_ASSUMPTIONS = (
    "P1 original sidecars are opened read-only; nothing in the original or smoke folder is written",
    "P2 the smoke parent is used only after bitwise or <=1e-9 relative equivalence with the original parent",
    "P3 level is proven from the zero-based Tet4 vertex lattice and cell count 6*n*n*L; order comes from the result provenance and is "
    "proven by coefficient count (3*vertices, or 3*(vertices+unique edges)), component sets and sorted physical positions",
    "P4 signed interface work W = sum(reaction_i * prescribed_value_i), reaction = K q - f (child) or RF - withheld load (CCX)",
    "P5 CCX work carries the print half-ulp of every RF entry and the 13-digit deck representation bound",
    "P6 new children are solved from the smoke parent copy; their job identity is distinct and recorded",
)


# ----------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------
def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, Path):
        return str(value)
    return str(value)


def dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=_default)


def write_new(path: Path, text: str) -> None:
    """Exclusive create: evidence is never overwritten."""

    with open(path, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _h5():
    try:
        import h5py
    except ImportError as error:  # pragma: no cover - environment
        raise StudyError("H5PY_MISSING", f"h5py is required: {error}") from error
    return h5py


def _text(value: Any) -> str:
    if isinstance(value, np.ndarray) and value.shape == ():
        value = value.item()
    if isinstance(value, (bytes, np.bytes_)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _attrs(node: Any) -> Dict[str, str]:
    return {str(k): _text(v) for k, v in node.attrs.items()}


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _json_text(node: Any) -> Any:
    raw = _text(node[()])
    try:
        return json.loads(raw)
    except ValueError:
        return {"unparsed": raw}


def array_sha256(array: Any) -> str:
    a = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(f"{a.dtype}|{a.shape}|".encode("ascii"))
    digest.update(a.tobytes())
    return digest.hexdigest()


def _find_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def _index_rows(array: Any, count: int, width: int, what: str) -> np.ndarray:
    """Zero-based rows of a mesh index array.  Shape, integer dtype and range are proven; ids are never guessed."""

    a = np.asarray(array)
    if a.ndim != 2 or a.shape[1] != width or a.shape[0] < 1:
        raise StudyError("MESH_INDEX_SHAPE", f"{what} must be a nonempty (M,{width}) array, found {a.shape}")
    if a.dtype.kind not in "iu":
        raise StudyError("MESH_INDEX_DTYPE", f"{what} must be integer rows, found {a.dtype}")
    a = a.astype(np.int64)
    if int(a.min()) < 0 or int(a.max()) >= count:
        raise StudyError("MESH_INDEX_RANGE", f"{what} are not zero-based rows into {count} coordinates ({a.min()}..{a.max()})")
    return a


def _id_rows(ids: Any, wanted: Any, what: str) -> np.ndarray:
    """Rows of external ids through an explicit id -> row table (unique ids required, every wanted id must exist)."""

    table = np.asarray(ids)
    if table.ndim != 1 or table.dtype.kind not in "iu":
        raise StudyError("ID_TABLE_INVALID", f"the id table for {what} is not a 1-D integer array")
    lookup = {int(v): i for i, v in enumerate(table)}
    if len(lookup) != len(table):
        raise StudyError("ID_TABLE_DUPLICATES", f"the id table for {what} holds repeated ids")
    want = np.asarray(wanted)
    if want.ndim != 1 or want.dtype.kind not in "iu":
        raise StudyError("ID_LIST_INVALID", f"{what} is not a 1-D integer list")
    missing = [int(w) for w in want if int(w) not in lookup]
    if missing:
        raise StudyError("ID_UNRESOLVED", f"{len(missing)} of {len(want)} {what} are not external ids of the mesh", first=missing[:5])
    return np.fromiter((lookup[int(w)] for w in want), dtype=np.int64, count=len(want))


def _rule_p1_four_point() -> Dict[str, Any]:
    a, b = (5.0 + 3.0 * np.sqrt(5.0)) / 20.0, (5.0 - np.sqrt(5.0)) / 20.0
    return {"name": "tet-4-point", "classes": [(np.sort([a, b, b, b]), 0.25, 4)]}


def _rule_keast_eleven() -> Dict[str, Any]:
    """Eleven-station degree-4 tetrahedron rule (weights as fractions of the volume; the centroid weight is negative)."""

    s = np.sqrt(5.0 / 14.0)
    a, b = (1.0 + s) / 4.0, (1.0 - s) / 4.0
    return {"name": "tet-11-station", "classes": [
        (np.sort([0.25, 0.25, 0.25, 0.25]), -148.0 / 1875.0, 1),
        (np.sort([11.0 / 14.0, 1.0 / 14.0, 1.0 / 14.0, 1.0 / 14.0]), 343.0 / 7500.0, 4),
        (np.sort([a, a, b, b]), 56.0 / 375.0, 6)]}


QUADRATURE_RULES = {4: _rule_p1_four_point(), 11: _rule_keast_eleven()}


def check_quadrature(order: int, lam4: np.ndarray, weights: np.ndarray, volume: np.ndarray) -> Tuple[bool, Dict[str, Any]]:
    """Stored quadrature against the known rule and the actual physical locations.

    ``lam4`` (cells, points, 4) are the barycentric coordinates of the *stored* physical points in their own cells,
    ``weights`` the signed physical row weights, ``volume`` the actual positive cell volumes.  Each point must be
    one station of the rule (by its sorted barycentric signature, in whatever order it is stored), every cell must
    hold exactly the rule's station counts at distinct locations, and each signed weight must equal the rule's
    weight times the actual volume.  No weight is required to be positive.
    """

    cells, points = weights.shape
    rule = QUADRATURE_RULES.get(points)
    detail: Dict[str, Any] = {"rule": rule["name"] if rule else None, "points_per_cell": int(points),
                              "negative_weights": int(np.count_nonzero(weights < 0)), "finite": bool(np.isfinite(weights).all())}
    if order == 2 and points != 11:
        return False, {**detail, "reason": "P2 recovery must use the eleven-station tetrahedron rule"}
    distinct = all(len(np.unique(np.round(lam4[i], 7), axis=0)) == points for i in range(cells))
    detail["distinct_points"] = bool(distinct)
    if rule is None:
        return bool(detail["finite"] and distinct), {**detail, "rule_known": False}
    signature = np.sort(lam4, axis=2)
    classes = rule["classes"]
    reference = np.stack([c[0] for c in classes])
    dist = np.abs(signature[:, :, None, :] - reference[None, None]).max(axis=3)  # (cells, points, classes)
    nearest, which = dist.min(axis=2), dist.argmin(axis=2)
    counts = np.stack([(which == i).sum(axis=1) for i in range(len(classes))], axis=1)
    counts_ok = bool(np.all(counts == np.array([c[2] for c in classes])))
    expected = np.array([c[1] for c in classes])[which] * volume[:, None]
    weight_err = float(np.max(np.abs(weights - expected) / volume[:, None]))
    location_err = float(nearest.max())
    ok = bool(detail["finite"] and distinct and counts_ok and location_err <= STATION_TOL and weight_err <= TRACE_TOL)
    return ok, {**detail, "rule_known": True, "station_counts_ok": counts_ok, "max_station_location_error": location_err,
                "max_signed_weight_rel_error": weight_err}


def expected_positions(geo: Mapping[str, Any], order: int) -> np.ndarray:
    """Vertices (P1) or vertices plus the distinct unique-edge midpoints (P2), physical coordinates."""

    coords = geo["coords"]
    if order == 1:
        return coords
    edges = geo["edges"]
    return np.vstack([coords, 0.5 * (coords[edges[:, 0]] + coords[edges[:, 1]])])


def _sorted_positions(points: np.ndarray) -> np.ndarray:
    key = np.round(points, 9)
    return points[np.lexsort((key[:, 2], key[:, 1], key[:, 0]))]


def _result_order(A: Mapping[str, Any]) -> int:
    """The element order from the result's field provenance (the only place the supported codec records it)."""

    try:
        prov = json.loads(A["field_attrs"].get("provenance", "{}"))
    except ValueError as error:
        raise StudyError("PROVENANCE_UNREADABLE", f"field provenance is not JSON: {error}") from error
    order = prov.get("order") if isinstance(prov, dict) else None
    if isinstance(order, bool) or order not in (1, 2):
        raise StudyError("ORDER_UNPROVEN", f"result provenance order is {order!r}, expected 1 or 2")
    return int(order)


# ----------------------------------------------------------------------
# artifact discovery (read-only)
# ----------------------------------------------------------------------
def _describe_file(path: Path) -> Dict[str, Any]:
    h5py = _h5()
    kind = "result" if path.name.endswith(".anyres.h5") else "mesh"
    record: Dict[str, Any] = {"path": str(path), "name": path.name, "kind": kind, "bytes": path.stat().st_size,
                              "sha256": sr.sha256_file(path)}
    try:
        with h5py.File(path, "r") as handle:
            attrs = _attrs(handle)
            record["attrs"] = attrs
            if kind == "result":
                role, comps = "other", None
                try:
                    comps = json.loads(_text(handle["fields/displacement"].attrs["components"]))
                    prov = json.loads(_text(handle["fields/displacement"].attrs.get("provenance", "{}")))
                    if len(comps) == 6:
                        role = "parent_shell"
                    elif prov.get("child_kind") == "solid" and len(comps) == 3:
                        role = "solid_child"
                except (KeyError, ValueError, TypeError):
                    role = "unclassified"
                record["role"] = role
                record["components"] = comps
            else:
                record["role"] = "solid_mesh" if attrs.get("schema") == "anyfem.solid_mesh" else (
                    "shell_mesh" if attrs.get("schema") == "anyfem.mesh" else "other")
        record["readable"] = True
    except (OSError, ValueError, KeyError) as error:
        record["readable"] = False
        record["role"] = "unreadable"
        record["error"] = f"{type(error).__name__}: {error}"
        record.setdefault("attrs", {})
    return record


def scan_artifacts(root: Path) -> Dict[str, Any]:
    root = Path(root)
    results, meshes = [], []
    for path in sorted(root.rglob("*.h5")):
        if not path.is_file():
            continue
        if path.name.endswith(".anyres.h5"):
            results.append(_describe_file(path))
        elif path.name.endswith(".anymesh.h5"):
            meshes.append(_describe_file(path))
    by_id = {m["attrs"].get("artifact_id"): m for m in meshes if m.get("readable")}
    pairs, orphan_results, used = [], [], set()
    for res in results:
        mesh = by_id.get(res.get("attrs", {}).get("mesh_id")) if res.get("readable") else None
        if mesh is None:
            orphan_results.append(res)
        else:
            pairs.append({"result": res, "mesh": mesh})
            used.add(mesh["path"])
    orphan_meshes = [m for m in meshes if m["path"] not in used]
    return {"root": str(root), "results": results, "meshes": meshes, "pairs": pairs,
            "orphan_results": orphan_results, "orphan_meshes": orphan_meshes}


def _brief(record: Mapping[str, Any]) -> Dict[str, Any]:
    keys = ("path", "name", "kind", "role", "bytes", "sha256", "readable", "error")
    out = {k: record[k] for k in keys if k in record}
    attrs = record.get("attrs", {})
    out["artifact_id"] = attrs.get("artifact_id")
    out["document_id"] = attrs.get("document_id")
    out["complete"] = attrs.get("complete")
    out["partial"] = attrs.get("partial")
    return out


# ----------------------------------------------------------------------
# parent shell artifact
# ----------------------------------------------------------------------
def load_parent(result_path: Path, mesh_path: Path) -> Dict[str, Any]:
    h5py = _h5()
    with h5py.File(result_path, "r") as r:
        out: Dict[str, Any] = {"result_attrs": _attrs(r), "field_attrs": _attrs(r["fields/displacement"])}
        out["values"] = np.asarray(r["fields/displacement/values"], dtype=np.float64)
        out["displacement_node_ids"] = np.asarray(r["tables/displacement_node_ids"], dtype=np.int64)
    with h5py.File(mesh_path, "r") as m:
        out["mesh_attrs"] = _attrs(m)
        out["node_ids"] = np.asarray(m["mesh/nodes/ids"], dtype=np.int64)
        out["xyz"] = np.asarray(m["mesh/nodes/xyz"], dtype=np.float64)
        out["quad_ids"] = np.asarray(m["mesh/elements/quads/ids"], dtype=np.int64)
        out["quad_conn"] = np.asarray(m["mesh/elements/quads/connectivity"], dtype=np.int64)
        codec = np.asarray(m["mesh/codec_json"], dtype=np.uint8)
    out["codec_sha256"] = hashlib.sha256(codec.tobytes()).hexdigest()
    try:
        payload = json.loads(codec.tobytes().decode("utf-8"))
        raw = _find_key(payload, "elements_of_face")
        out["lineage"] = parse_lineage(raw)
        out["lineage_error"] = None
    except (ValueError, StudyError) as error:
        out["lineage"] = None
        out["lineage_error"] = str(error)
    return out


def parse_lineage(raw: Any) -> Dict[int, List[int]]:
    if isinstance(raw, dict):
        return {int(k): sorted(int(x) for x in v) for k, v in raw.items()}
    if isinstance(raw, list):
        return {int(a): sorted(int(x) for x in b) for a, b in raw}
    raise StudyError("PARENT_LINEAGE_UNREADABLE", "mesh codec carries no readable elements_of_face")


def check_parent(P: Mapping[str, Any]) -> Dict[str, Any]:
    ra, ma = P["result_attrs"], P["mesh_attrs"]
    comps = json.loads(P["field_attrs"].get("components", "[]"))
    values, ids = P["values"], P["displacement_node_ids"]
    checks = {
        "result_complete": _truthy(ra.get("complete")) and not _truthy(ra.get("partial")),
        "mesh_complete": _truthy(ma.get("complete")),
        "pairing": ra.get("mesh_id") == ma.get("artifact_id") and ra.get("mesh_hash") == ma.get("mesh_hash")
                   and ra.get("document_id") == ma.get("document_id") and ra.get("model_hash") == ma.get("model_hash"),
        "components": comps == EXPECTED_COMPONENTS,
        "shape": values.ndim == 3 and values.shape[0] == 1 and values.shape[2] == 6 and values.shape[1] == len(ids),
        "node_ids": bool(np.array_equal(np.sort(ids), np.sort(P["node_ids"]))),
        "quads_reference_nodes": bool(np.isin(P["quad_conn"], P["node_ids"]).all()) if P["quad_conn"].size else False,
        "lineage": P["lineage"] is not None,
    }
    return {"checks": checks, "ok": all(checks.values())}


def central_candidates(P: Mapping[str, Any]) -> List[int]:
    lineage = P.get("lineage")
    if not lineage:
        return []
    position = {int(n): i for i, n in enumerate(P["node_ids"])}
    qpos = {int(e): i for i, e in enumerate(P["quad_ids"])}
    found = []
    for face, eids in sorted(lineage.items()):
        try:
            xyz = np.array([[P["xyz"][position[int(n)]] for n in P["quad_conn"][qpos[e]]] for e in eids])
        except KeyError:
            continue
        flat = xyz.reshape(-1, 3)
        box_ok = (abs(flat[:, 0].min() - CENTRAL_LO) < GEOMETRY_TOL and abs(flat[:, 0].max() - CENTRAL_HI) < GEOMETRY_TOL
                  and abs(flat[:, 1].min() - CENTRAL_LO) < GEOMETRY_TOL and abs(flat[:, 1].max() - CENTRAL_HI) < GEOMETRY_TOL
                  and float(np.ptp(flat[:, 2])) < GEOMETRY_TOL and abs(flat[:, 2].mean()) < GEOMETRY_TOL)
        area = float(sum(0.5 * np.linalg.norm(np.cross(q[2] - q[0], q[3] - q[1])) for q in xyz))
        if box_ok and abs(area - (CENTRAL_HI - CENTRAL_LO) ** 2) < GEOMETRY_TOL:
            found.append(int(face))
    return found


def parent_patch(P: Mapping[str, Any], face_id: int) -> "sr.ShellPatch":
    position = {int(n): i for i, n in enumerate(P["node_ids"])}
    qpos = {int(e): i for i, e in enumerate(P["quad_ids"])}
    drow = {int(n): i for i, n in enumerate(P["displacement_node_ids"])}
    ids, X, U = [], [], []
    for eid in sorted(P["lineage"][int(face_id)]):
        conn = [int(n) for n in P["quad_conn"][qpos[eid]]]
        if len(conn) != 4:
            raise StudyError("PARENT_NOT_Q4", f"element {eid} has {len(conn)} nodes")
        ids.append(eid)
        X.append([P["xyz"][position[n]] for n in conn])
        U.append([P["values"][0, drow[n]] for n in conn])
    return sr.ShellPatch(tuple(ids), np.asarray(X, float), np.asarray(U, float))


def patch_normal(patch: "sr.ShellPatch") -> np.ndarray:
    normals = []
    for X in patch.X:
        c = np.cross(X[2] - X[0], X[3] - X[1])
        normals.append(c / np.linalg.norm(c))
    normals = np.asarray(normals)
    if not np.allclose(normals, normals[0], atol=1e-12):
        raise StudyError("PARENT_NORMAL_NONUNIFORM", "central face normals differ")
    return normals[0]


def compare_parents(orig: Mapping[str, Any], surr: Mapping[str, Any]) -> Dict[str, Any]:
    same = lambda a, b: bool(np.array_equal(orig[a], surr[b]))  # noqa: E731
    a, b = orig["values"], surr["values"]
    shape_equal = a.shape == b.shape
    if shape_equal:
        diff = float(np.max(np.abs(a - b))) if a.size else 0.0
        scale = max(float(np.max(np.abs(b))) if b.size else 0.0, 1e-300)
        bitwise = bool(np.array_equal(a, b))
    else:
        diff, scale, bitwise = float("inf"), 1.0, False
    rel = diff / scale
    lineage_equal = orig["lineage"] == surr["lineage"] if (orig["lineage"] is not None and surr["lineage"] is not None) else None
    out = {
        "node_ids_equal": same("node_ids", "node_ids"), "node_xyz_bitwise_equal": same("xyz", "xyz"),
        "quad_ids_equal": same("quad_ids", "quad_ids"), "quad_connectivity_equal": same("quad_conn", "quad_conn"),
        "displacement_node_ids_equal": same("displacement_node_ids", "displacement_node_ids"),
        "lineage_equal": lineage_equal, "codec_sha256_equal": orig["codec_sha256"] == surr["codec_sha256"],
        "displacement_shape_equal": shape_equal, "displacement_bitwise_equal": bitwise,
        "displacement_max_abs_diff": diff, "displacement_scale": scale, "displacement_rel_diff": rel,
        "tolerance_rel": PARENT_EQUIV_TOL,
        "mesh_hash_equal": orig["mesh_attrs"].get("mesh_hash") == surr["mesh_attrs"].get("mesh_hash"),
        "model_hash_equal": orig["mesh_attrs"].get("model_hash") == surr["mesh_attrs"].get("model_hash"),
    }
    out["equivalent"] = bool(out["node_ids_equal"] and out["node_xyz_bitwise_equal"] and out["quad_ids_equal"]
                             and out["quad_connectivity_equal"] and out["displacement_node_ids_equal"]
                             and lineage_equal is True and shape_equal and rel <= PARENT_EQUIV_TOL)
    return out


# ----------------------------------------------------------------------
# solid child pair
# ----------------------------------------------------------------------
def load_child(result_path: Path, mesh_path: Path) -> Dict[str, Any]:
    h5py = _h5()
    A: Dict[str, Any] = {"result_path": str(result_path), "mesh_path": str(mesh_path)}
    with h5py.File(result_path, "r") as r:
        A["result_attrs"] = _attrs(r)
        A["field_attrs"] = _attrs(r["fields/displacement"])
        A["displacement"] = np.asarray(r["fields/displacement/values"], dtype=np.float64)
        for name in CHILD_TABLES:
            A[name] = np.asarray(r["tables/" + name]) if ("tables/" + name) in r else None
        A["texts"] = {}
        for name in ("provenance_json", "summary_json", "diagnostics_json"):
            if "metadata/" + name in r:
                A["texts"][name] = _json_text(r["metadata/" + name])
        for name in ("solid_fields", "solid_interface"):
            if "tables/" + name in r:
                A["texts"][name] = _json_text(r["tables/" + name])
    with h5py.File(mesh_path, "r") as m:
        A["mesh_attrs"] = _attrs(m)
        g = m["solid_mesh"]
        A["mesh_group_attrs"] = _attrs(g)
        for name in ("boundary_facets", "boundary_ids", "cell_ids", "cells", "coordinates", "face_owner_face_ids", "node_ids"):
            A["mesh_" + name] = np.asarray(g[name]) if name in g else None
        for name in ("groups_json", "provenance_json"):
            if name in g:
                A["texts"]["mesh_" + name] = _json_text(g[name])
        if "solid_mesh_source/geometry_json" in m:
            A["texts"]["mesh_geometry_json"] = _json_text(m["solid_mesh_source/geometry_json"])
    return A


def child_hashes(A: Mapping[str, Any]) -> Dict[str, str]:
    out = {}
    for name in ("mesh_coordinates", "mesh_cells", "mesh_node_ids", "mesh_cell_ids", "mesh_boundary_facets", "mesh_boundary_ids", "displacement"):
        if A.get(name) is not None:
            out[name] = array_sha256(A[name])
    for name in CHILD_TABLES:
        if A.get(name) is not None:
            out[name] = array_sha256(A[name])
    return out


def _tet_geometry(A: Mapping[str, Any]) -> Dict[str, Any]:
    """Straight Tet4 geometry of the supported solid mesh codec: ``cells`` (C,4) and ``boundary_facets`` (F,3) are
    zero-based rows into ``coordinates``; node, cell and boundary ids are separate, unique external labels."""

    coords = np.asarray(A["mesh_coordinates"])
    if coords.ndim != 2 or coords.shape[1] != 3 or not np.isfinite(coords).all():
        raise StudyError("MESH_COORDINATES_INVALID", "coordinates are not a finite (N,3) array")
    coords = coords.astype(np.float64)
    nv = len(coords)
    rows = _index_rows(A["mesh_cells"], nv, 4, "cells")
    facets = _index_rows(A["mesh_boundary_facets"], nv, 3, "boundary_facets")
    for name, ids, count in (("node_ids", A["mesh_node_ids"], nv), ("cell_ids", A["mesh_cell_ids"], len(rows)),
                             ("boundary_ids", A["mesh_boundary_ids"], len(facets))):
        arr = None if ids is None else np.asarray(ids)
        if arr is None or arr.shape != (count,) or arr.dtype.kind not in "iu" or len(np.unique(arr)) != count:
            raise StudyError("MESH_IDS_INVALID", f"{name} are not {count} unique integer labels")
    if len(np.unique(rows)) != nv:
        raise StudyError("MESH_VERTEX_UNUSED", "some coordinate rows are not a corner of any cell")
    corners = coords[rows]  # (M, 4, 3)
    T = np.stack([corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0], corners[:, 3] - corners[:, 0]], axis=2)
    volume = np.linalg.det(T) / 6.0
    if not bool(np.all(volume > 0.0)):
        raise StudyError("MESH_CELL_NOT_POSITIVE", "a Tet4 has a non-positive actual volume")
    pairs = np.concatenate([rows[:, [a, b]] for a, b in ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))])
    edges = np.unique(np.sort(pairs, axis=1), axis=0)
    return {"coords": coords, "nv": nv, "rows": rows, "facets": facets, "corners": corners, "T": T, "volume": volume, "edges": edges}


def inspect_child(A: Mapping[str, Any], expected: Mapping[str, Any]) -> Dict[str, Any]:
    """Binding verdict of one result/mesh pair.  Every check is evaluated; failures are kept, not raised."""

    checks: Dict[str, Dict[str, Any]] = {}
    derived: Dict[str, Any] = {}
    gating = set()

    def guard(name: str, fn, gate: bool = True) -> None:
        if gate:
            gating.add(name)
        try:
            ok, detail = fn()
            checks[name] = {"ok": bool(ok), **detail}
        except Exception as error:  # noqa: BLE001 - a damaged pair must become a typed record
            checks[name] = {"ok": False, "error": f"{type(error).__name__}: {error}"}

    ra, ma = A["result_attrs"], A["mesh_attrs"]
    guard("complete", lambda: (_truthy(ra.get("complete")) and not _truthy(ra.get("partial")) and _truthy(ma.get("complete")),
                               {"result_complete": ra.get("complete"), "result_partial": ra.get("partial"), "mesh_complete": ma.get("complete")}))
    guard("pairing", lambda: (
        ra.get("schema") == "anyfem.result" and ma.get("schema") == "anyfem.solid_mesh"
        and ra.get("mesh_id") == ma.get("artifact_id") and ra.get("mesh_hash") == ma.get("mesh_hash")
        and ra.get("document_id") == ma.get("document_id") and ra.get("model_hash") == ma.get("model_hash")
        and (not expected.get("document_id") or ra.get("document_id") == expected["document_id"]),
        {"result_mesh_id": ra.get("mesh_id"), "mesh_artifact_id": ma.get("artifact_id"), "document_id": ra.get("document_id"),
         "expected_document_id": expected.get("document_id")}))
    guard("node_order", lambda: (np.array_equal(A["displacement_node_ids"], A["mesh_node_ids"]), {}))

    def geometry() -> Dict[str, Any]:
        if "geometry" not in derived:
            derived["geometry"] = _tet_geometry(A)
        return derived["geometry"]

    def mesh_arrays():
        geo = geometry()
        group = A["mesh_group_attrs"]
        ok = group.get("geometry_order") == "1" and group.get("units") == "m" and ma.get("element_type") == "tet4"
        return ok, {"vertices": geo["nv"], "cells": len(geo["rows"]), "boundary_facets": len(geo["facets"]), "unique_edges": len(geo["edges"]),
                    "geometry_order": group.get("geometry_order"), "units": group.get("units"), "element_type_attr": ma.get("element_type"),
                    "index_convention": "cells and boundary_facets are zero-based rows; ids are external labels"}

    guard("mesh_arrays", mesh_arrays)

    def shapes():
        geo = geometry()
        order = _result_order(A)
        n = geo["nv"]
        npos = n + (len(geo["edges"]) if order == 2 else 0)
        d = np.asarray(A["displacement"])
        q, xyz = np.asarray(A["coefficients"]), np.asarray(A["coefficient_coordinates"])
        comp = np.asarray(A["coefficient_components"])
        info: Dict[str, Any] = {"nodes": n, "order": order, "coefficients": int(q.size), "coefficients_expected": 3 * npos,
                                "public_displacement_entries": int(d.size)}
        good = (d.shape == (1, n, 3) and d.size == 3 * n and q.shape == (3 * npos,) and xyz.shape == (3 * npos, 3)
                and comp.shape == (3 * npos,) and comp.dtype.kind in "iu" and np.asarray(A["displacement_node_ids"]).shape == (n,)
                and bool(np.isfinite(xyz).all()))
        if not good or int(comp.min()) < 0 or int(comp.max()) > 2:
            return False, info
        counts = np.bincount(comp.astype(np.int64), minlength=3)
        info["component_counts"] = counts.tolist()
        wanted = expected_positions(geo, order)
        distinct = len(np.unique(np.round(wanted, 9), axis=0)) == len(wanted)
        reference = _sorted_positions(wanted)
        err = 0.0
        for c in range(3):
            got = xyz[comp == c]
            if got.shape != reference.shape:
                err = float("inf")
                break
            err = max(err, float(np.max(np.abs(_sorted_positions(got) - reference))))
        info.update({"expected_positions": int(npos), "positions_distinct": bool(distinct), "max_position_error": err})
        return bool(np.all(counts == npos) and distinct and err <= POSITION_TOL), info

    guard("cardinality", shapes)

    def lattice():
        geo = geometry()
        coords = geo["coords"]
        axes = [np.unique(np.round(coords[:, a], 12)) for a in range(3)]
        n, layers = len(axes[0]) - 1, len(axes[2]) - 1
        order = _result_order(A)
        prov = json.loads(A["field_attrs"].get("provenance", "{}"))
        ok = (n >= 1 and layers >= 1 and len(axes[1]) - 1 == n
              and np.allclose(np.diff(axes[0]), 0.2 / n, atol=1e-12) and np.allclose(np.diff(axes[1]), 0.2 / n, atol=1e-12)
              and np.allclose(np.diff(axes[2]), sr.THICKNESS / layers, atol=1e-12)
              and abs(axes[0][0] - sr.DETAIL_BOX[0]) < 1e-12 and abs(axes[0][-1] - sr.DETAIL_BOX[3]) < 1e-12
              and abs(axes[1][0] - sr.DETAIL_BOX[1]) < 1e-12 and abs(axes[1][-1] - sr.DETAIL_BOX[4]) < 1e-12
              and abs(axes[2][0] - sr.DETAIL_BOX[2]) < 1e-12 and abs(axes[2][-1] - sr.DETAIL_BOX[5]) < 1e-12)
        derived.update({"divisions": n, "layers": layers, "order": order})
        cells_ok = int(len(geo["rows"])) == 6 * n * n * layers
        return bool(ok and cells_ok and ma.get("element_type") == ELEMENT_NAME[order]), {
            "divisions": n, "layers": layers, "order_from_provenance": prov.get("order"),
            "element_type_attr": ma.get("element_type"), "element_type_expected": ELEMENT_NAME[order],
            "cells": int(len(geo["rows"])), "cells_expected": 6 * n * n * layers}

    guard("level_order", lattice)

    def prescribed():
        dofs = np.asarray(A["prescribed_dofs"], dtype=np.int64)
        vals = np.asarray(A["prescribed_values"], float)
        react = np.asarray(A["reactions"], float)
        ok = (dofs.ndim == 1 and dofs.size > 0 and len(np.unique(dofs)) == dofs.size and dofs.min() >= 0
              and dofs.max() < len(A["coefficients"]) and vals.shape == dofs.shape and react.shape == dofs.shape)
        return ok, {"held": int(dofs.size)}

    guard("held_coefficients", prescribed)

    def recovery():
        geo = geometry()
        order = _result_order(A)
        w = np.asarray(A["recovery_weights"], float)
        xyz = np.asarray(A["recovery_coordinates"], float)
        stress = np.asarray(A["recovery_stress"], float)
        if w.ndim != 2:
            return False, {"reason": f"recovery_weights has shape {w.shape}, expected (cells, points)"}
        k, q = w.shape
        shape_ok = (xyz.shape == (k, q, 3) and stress.shape == (k, q, 3, 3)
                    and bool(np.isfinite(w).all() and np.isfinite(xyz).all() and np.isfinite(stress).all()))
        if not shape_ok:
            return False, {"cells": k, "points_per_cell": q, "reason": "recovery tables are not finite arrays of matching shape"}
        # external recovery cell ids -> rows through the explicit id table of the mesh
        cell_rows = _id_rows(A["mesh_cell_ids"], np.asarray(A["recovery_cell_ids"]), "recovery_cell_ids")
        if len(cell_rows) != k:
            return False, {"cells": k, "reason": "recovery_cell_ids and recovery_weights differ in length"}
        cover = bool(len(np.unique(cell_rows)) == len(geo["rows"]) == k)
        volume = geo["volume"][cell_rows]
        corners, T = geo["corners"][cell_rows], geo["T"][cell_rows]
        lam = np.linalg.solve(T, (xyz - corners[:, None, 0]).transpose(0, 2, 1)).transpose(0, 2, 1)  # (k,q,3)
        lam4 = np.concatenate([1.0 - lam.sum(axis=2, keepdims=True), lam], axis=2)
        inside = float(lam4.min())
        wsum_rel = float(np.max(np.abs(w.sum(axis=1) - volume) / volume))
        total_rel = abs(float(w.sum()) - (0.2 * 0.2 * sr.THICKNESS)) / (0.2 * 0.2 * sr.THICKNESS)
        quad_ok, quad = check_quadrature(order, lam4, w, volume)
        derived["recovery_rows"] = cell_rows
        ok = (shape_ok and cover and inside >= -GEOMETRY_TOL and wsum_rel <= TRACE_TOL and total_rel <= TRACE_TOL and quad_ok)
        return ok, {"cells": k, "points_per_cell": q, "cell_coverage": cover, "min_barycentric_slack": inside,
                    "cell_weight_rel_error": wsum_rel, "total_volume_rel_error": total_rel, "min_cell_volume": float(geo["volume"].min()),
                    "quadrature": quad}

    guard("tensor_locations_and_coverage", recovery)

    def source():
        provenance = A["texts"].get("provenance_json", {})
        actual = provenance.get("submodel", {}).get("source_job_id")
        return bool(actual) and actual == expected["source_job_id"], {
            "expected_source_job_id": expected["source_job_id"], "actual_source_job_id": actual}

    guard("source_job", source)

    def groups():
        geo = geometry()
        parsed = A["texts"].get("mesh_groups_json", {})
        table = parsed.get("boundary_groups") if isinstance(parsed, dict) else None
        end = table.get("end") if isinstance(table, dict) else None
        if not isinstance(end, list) or not end:
            raise StudyError("END_GROUP_MISSING", "groups_json has no non-empty boundary_groups['end']")
        sel = _id_rows(A["mesh_boundary_ids"], np.asarray(end), "end group boundary ids")  # external boundary ids -> facet rows
        rows = geo["facets"][sel]  # (m, 3) vertex rows
        z = geo["coords"][np.unique(rows), 2]
        planar = float(np.ptp(z)) < 1e-9
        derived["z_end"] = float(z.mean())
        derived["end_rows"] = rows
        return planar, {"z_end": float(z.mean()), "planar": planar, "end_facets": len(sel)}

    guard("end_plane", groups)
    status_ok = all(checks[name]["ok"] for name in gating)
    failed = [name for name in gating if not checks[name]["ok"]]
    return {"status": "bound" if status_ok else "unbound", "failed_checks": sorted(failed), "checks": checks,
            "divisions": derived.get("divisions"), "layers": derived.get("layers"), "order": derived.get("order"),
            "z_end": derived.get("z_end"), "derived": {"geometry": derived.get("geometry"), "end_rows": derived.get("end_rows")}}


def public_binding(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in record.items() if k != "derived"}


def combo_key(order: int, n: int, layers: int) -> str:
    return f"{sr.ORDER_ELEMENT[order]}_{n}x{layers}_p{order}"


# ----------------------------------------------------------------------
# decks (solid_reference functions, parsed back)
# ----------------------------------------------------------------------
def deck_readback(text: str, mesh: Any, young: float, poisson: float, prescribed: Mapping[int, Sequence[float]],
                  traction: Sequence[float], side: str) -> Dict[str, Any]:
    info = sr.parse_deck(text)
    forces = sr.skin_forces(mesh, side, traction)
    lateral = mesh.lateral()
    applied, withheld = sr.split_loads(forces, lateral)
    rel = sr.INTEGRITY["deck_value_rel"]

    def close(a: Sequence[float], b: Sequence[float]) -> bool:
        a, b = np.asarray(a, float), np.asarray(b, float)
        return bool(np.all(np.abs(a - b) <= rel * np.abs(b)))  # per-entry: the deck prints 13 significant digits

    section = text.split("*BOUNDARY", 1)[1].split("*CLOAD", 1)[0] if "*BOUNDARY" in text else ""
    boundary_lines = [ln for ln in section.splitlines() if ln.strip() and not ln.startswith("*")]
    area = (mesh.box[3] - mesh.box[0]) * (mesh.box[4] - mesh.box[1])
    expected_total = np.asarray(traction, float) * area
    applied_total = np.sum(list(info["cload"].values()), axis=0) if info["cload"] else np.zeros(3)
    withheld_total = np.sum([np.asarray(v, float) for v in withheld.values()], axis=0) if withheld else np.zeros(3)
    total = applied_total + withheld_total
    scale = max(float(np.linalg.norm(expected_total)), 1e-300)
    printed = {p["nset"]: p for p in info["node_print"]}
    checks = {
        "nodes": set(info["nodes"]) == set(mesh.nodes) and all(close(info["nodes"][k], mesh.nodes[k]) for k in mesh.nodes),
        "elements": {e[0]: tuple(e[1]) for e in mesh.elements} == info["elements"],
        "element_type": info["element_type"] == mesh.element,
        "material": info["elastic"] is not None and close(info["elastic"], (young, poisson)),
        "support_set": set(info["boundary"]) == set(lateral) and len(boundary_lines) == 3 * len(lateral),
        "support_values": all(close(info["boundary"][k], prescribed[k]) for k in prescribed),
        "applied_loads": set(info["cload"]) == {k for k, v in applied.items() if any(v)}
                         and all(close(info["cload"][k], applied[k]) for k in info["cload"]),
        "withheld_not_in_deck": not (set(withheld) & set(info["cload"])),
        "pressure_resultant": float(np.linalg.norm(total - expected_total)) <= 1e-12 * scale,
        "signed_gauge": bool(np.sign(expected_total[2]) == np.sign(total[2]) and all(
            abs(mesh.nodes[k][2] - (mesh.box[5] if side == "top" else mesh.box[2])) < 1e-12 for k in list(info["cload"]) + list(withheld))),
        "prints": "NALL" in printed and "U" in printed["NALL"]["variables"] and "NPRESC" in printed
                  and printed["NPRESC"]["totals"] == "YES" and "RF" in printed["NPRESC"]["variables"],
    }
    return {"checks": checks, "ok": all(checks.values()), "applied_total": applied_total.tolist(),
            "withheld_total": withheld_total.tolist(), "expected_total": expected_total.tolist(),
            "held_nodes": len(lateral), "loaded_nodes": len(info["cload"]), "withheld_nodes": len(withheld),
            "material_in_deck": info["elastic"]}


def write_decks(out: Path, patch: "sr.ShellPatch", young: float, poisson: float, traction: Sequence[float], side: str,
                levels: Sequence[Tuple[int, int]]) -> List[Dict[str, Any]]:
    rows = []
    for n, layers in levels:
        for element in ("C3D8", "C3D20"):
            mesh = sr.reference_mesh(element, n, layers)
            lateral = mesh.lateral()
            values, spread = sr.evaluate_patch(patch, [mesh.nodes[k] for k in lateral])
            prescribed = {k: tuple(float(c) for c in v) for k, v in zip(lateral, values)}
            applied, withheld = sr.split_loads(sr.skin_forces(mesh, side, traction), lateral)
            name = sr.deck_name(element, n, layers)
            text = sr.deck_text(mesh, young, poisson, prescribed, applied, f"{sr.STUDY_ID} {name} (partial recovery)")
            write_new(out / f"{name}.inp", text)
            back = deck_readback(text, mesh, young, poisson, prescribed, traction, side)
            rows.append({"name": name, "element": element, "n": n, "layers": layers, "inp": f"{name}.inp", "dat": f"{name}.dat",
                         "sha256": hashlib.sha256(text.encode("ascii")).hexdigest(), "nodes": len(mesh.nodes),
                         "elements": len(mesh.elements), "prescribed_nodes": len(lateral), "loaded_nodes": len(applied),
                         "withheld_nodes": len(withheld), "hit_spread": spread, "readback": back})
    return rows


# ----------------------------------------------------------------------
# prepare
# ----------------------------------------------------------------------
def _top_level_files(root: Path) -> List[Dict[str, Any]]:
    return [{"name": p.name, "bytes": p.stat().st_size, "sha256": sr.sha256_file(p)} for p in sorted(root.iterdir()) if p.is_file()]


def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        return {"unreadable": str(error)}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _pick_parent(scan: Mapping[str, Any], hint: Optional[str], label: str, blockers: List[Dict[str, Any]]):
    parents = [p for p in scan["pairs"] if p["result"].get("role") == "parent_shell"]
    if hint:
        chosen = [p for p in parents if p["result"]["attrs"].get("artifact_id") == hint]
    else:
        chosen = parents
    if len(chosen) == 1:
        return chosen[0]
    code = "PARENT_ABSENT" if not chosen else "PARENT_AMBIGUOUS"
    blockers.append({"code": code, "where": label, "message": f"{len(chosen)} parent shell result/mesh pairs match",
                     "candidates": [p["result"]["attrs"].get("artifact_id") for p in chosen],
                     "orphan_results": [_brief(r) for r in scan["orphan_results"] if r.get("role") == "parent_shell"]})
    return None


def prepare(original: Path, surrogate: Path, out: Path) -> Dict[str, Any]:
    original, surrogate, out = Path(original), Path(surrogate), Path(out)
    for label, p in (("original", original), ("surrogate", surrogate)):
        if not p.is_dir():
            raise StudyError("INPUT_MISSING", f"{label} folder {p} does not exist")
    if _inside(out, original) or _inside(out, surrogate) or _inside(original, out) or _inside(surrogate, out):
        raise StudyError("OUT_OVERLAPS_INPUT", "the output path must be separate from both input folders")
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise StudyError("OUT_EXISTS", f"{out} exists; evidence is never overwritten") from error
    try:
        pm = _prepare(original, surrogate, out)
    except StudyError as error:
        write_new(out / "prepare_failure.json", dumps(error.as_dict()))
        raise
    except Exception as error:  # noqa: BLE001
        write_new(out / "prepare_failure.json", dumps({"status": "failed", "code": "UNEXPECTED", "message": f"{type(error).__name__}: {error}",
                                                        "traceback": traceback.format_exc()}))
        raise
    return pm


def _bound_files(pm_parts: Sequence[Mapping[str, Any]]) -> List[Tuple[str, str]]:
    return [(r["path"], r["sha256"]) for r in pm_parts]


def _prepare(original: Path, surrogate: Path, out: Path) -> Dict[str, Any]:
    blockers: List[Dict[str, Any]] = []
    smanifest = _read_json_file(surrogate / "manifest.json")
    if smanifest is None or "unreadable" in smanifest:
        raise StudyError("SURROGATE_MANIFEST_MISSING", "the smoke study has no readable manifest.json", path=str(surrogate / "manifest.json"))
    if smanifest.get("study") != sr.STUDY_ID or smanifest.get("schema") != sr.SCHEMA:
        raise StudyError("SURROGATE_MANIFEST_INVALID", "the smoke manifest is not of the solid reference study")
    if list(smanifest.get("box", [])) != list(sr.DETAIL_BOX):
        raise StudyError("SURROGATE_FIXTURE_DIFFERS", "the smoke detail box differs from the registered fixture")
    omanifest = _read_json_file(original / "manifest.json")  # claimed data only; may be absent
    oscan, sscan = scan_artifacts(original), scan_artifacts(surrogate)
    hint_o = omanifest.get("job_id") if isinstance(omanifest, dict) else None
    opair = _pick_parent(oscan, hint_o, "original", blockers)
    spair = _pick_parent(sscan, smanifest.get("job_id"), "surrogate", blockers)

    pm: Dict[str, Any] = {
        "kind": PARTIAL_KIND, "schema": PARTIAL_SCHEMA, "created_utc": _utc(), "status": "blocked", "blockers": blockers,
        "script": {"partial_sha256": sr.sha256_file(Path(__file__).resolve()), "solid_reference_sha256": sr.sha256_file(Path(sr.__file__).resolve())},
        "tolerances": {"parent_equivalence_rel": PARENT_EQUIV_TOL, "trace_rel": TRACE_TOL, "force_moment_rel": CLOSURE_TOL,
                       "geometry_abs": GEOMETRY_TOL, "integrity": sr.INTEGRITY},
        "assumptions": list(PARTIAL_ASSUMPTIONS) + list(sr.ASSUMPTIONS),
        "original": {"root": str(original.resolve()), "claimed_manifest": omanifest, "top_level_files": _top_level_files(original),
                     "results": [_brief(r) for r in oscan["results"]], "meshes": [_brief(m) for m in oscan["meshes"]],
                     "orphan_results": [_brief(r) for r in oscan["orphan_results"]], "orphan_meshes": [_brief(m) for m in oscan["orphan_meshes"]]},
        "surrogate": {"root": str(surrogate.resolve()), "manifest": smanifest, "job_id": smanifest.get("job_id"),
                      "project": smanifest.get("project"), "results": [_brief(r) for r in sscan["results"]],
                      "meshes": [_brief(m) for m in sscan["meshes"]], "top_level_files": _top_level_files(surrogate)},
        "children": {"bound": [], "unbound": [], "duplicates": [], "missing": []},
    }
    hashed = [r for r in oscan["results"] + oscan["meshes"] + sscan["results"] + sscan["meshes"]]
    pm["inputs_sha256"] = {r["path"]: r["sha256"] for r in hashed}

    patch = None
    if opair and spair:
        P_o = load_parent(Path(opair["result"]["path"]), Path(opair["mesh"]["path"]))
        P_s = load_parent(Path(spair["result"]["path"]), Path(spair["mesh"]["path"]))
        co, cs = check_parent(P_o), check_parent(P_s)
        eq = compare_parents(P_o, P_s) if (co["ok"] and cs["ok"]) else {"equivalent": False}
        cand_o, cand_s = central_candidates(P_o), central_candidates(P_s)
        pm["parents"] = {
            "original": {"result": _brief(opair["result"]), "mesh": _brief(opair["mesh"]), "checks": co, "codec_sha256": P_o["codec_sha256"],
                         "lineage_error": P_o["lineage_error"], "central_face_candidates": cand_o},
            "surrogate": {"result": _brief(spair["result"]), "mesh": _brief(spair["mesh"]), "checks": cs, "codec_sha256": P_s["codec_sha256"],
                          "lineage_error": P_s["lineage_error"], "central_face_candidates": cand_s},
            "equivalence": eq}
        if not co["ok"]:
            blockers.append({"code": "PARENT_ORIGINAL_INVALID", "checks": co["checks"]})
        if not cs["ok"]:
            blockers.append({"code": "PARENT_SURROGATE_INVALID", "checks": cs["checks"]})
        if co["ok"] and cs["ok"] and not eq["equivalent"]:
            blockers.append({"code": "PARENT_NOT_EQUIVALENT", "message": "the smoke parent is not physically equivalent to the original parent", "comparison": eq})
        if co["ok"] and len(cand_o) != 1:
            blockers.append({"code": "CENTRAL_FACE_UNPROVEN", "message": f"{len(cand_o)} original faces match the 0.3-0.7 m central plane", "candidates": cand_o})
        if co["ok"] and cs["ok"] and len(cand_o) == 1 and len(cand_s) == 1 and not blockers:
            face_o, face_s = cand_o[0], cand_s[0]
            patch_o, patch_s = parent_patch(P_o, face_o), parent_patch(P_s, face_s)
            normal = patch_normal(patch_o)
            traction = sr.PRESSURE * normal
            claimed = np.asarray(smanifest["traction"], float)
            physical = {
                "face_id_original": face_o, "face_id_surrogate": face_s, "face_id_manifest_surrogate": smanifest.get("face_id"),
                "lineage_elements_equal": patch_o.ids == patch_s.ids and bool(np.array_equal(patch_o.X, patch_s.X)),
                "patch_displacement_bitwise_equal": bool(np.array_equal(patch_o.U, patch_s.U)),
                "normal": normal.tolist(), "traction_from_original_geometry": traction.tolist(), "traction_smoke_manifest": claimed.tolist(),
                "traction_agrees": bool(np.allclose(traction, claimed, rtol=1e-12, atol=1e-12 * sr.PRESSURE)),
                "pressure": sr.PRESSURE, "thickness": sr.THICKNESS, "thickness_source": "frozen fixture constant, confirmed on child meshes below",
                "material": smanifest["material"], "material_source": "smoke manifest (parent section material), not in the parent artifacts",
                "z_end_smoke": smanifest["z_end"], "skin_side_smoke": smanifest["skin_side"]}
            pm["physical"] = physical
            if not (physical["lineage_elements_equal"] and physical["traction_agrees"]):
                blockers.append({"code": "PHYSICAL_INPUTS_UNPROVEN", "physical": physical})
            else:
                patch = patch_o
                pm["parent_field_sha256"] = hashlib.sha256(np.ascontiguousarray(patch.U).tobytes()).hexdigest()
                pm["physical"]["parent_field_matches_claimed"] = {
                    "original_manifest": (omanifest or {}).get("parent_field_sha256") == pm["parent_field_sha256"] if omanifest else None,
                    "smoke_manifest": smanifest.get("parent_field_sha256") == pm["parent_field_sha256"]}

    # --- children (bound by HDF5 identifiers; nothing depends on a project)
    parent_job = opair["result"]["attrs"].get("artifact_id") if opair else hint_o
    parent_doc = opair["result"]["attrs"].get("document_id") if opair else None
    expected = {"document_id": parent_doc, "source_job_id": parent_job}
    seen: Dict[str, str] = {}
    end_planes = []
    for pair in oscan["pairs"]:
        if pair["result"].get("role") != "solid_child":
            continue
        try:
            A = load_child(Path(pair["result"]["path"]), Path(pair["mesh"]["path"]))
            rec = inspect_child(A, expected)
            row = {"result": _brief(pair["result"]), "mesh": _brief(pair["mesh"]), "numerical_hashes": child_hashes(A), "binding": public_binding(rec),
                   "order": rec["order"], "divisions": rec["divisions"], "layers": rec["layers"], "z_end": rec["z_end"]}
        except Exception as error:  # noqa: BLE001
            row = {"result": _brief(pair["result"]), "mesh": _brief(pair["mesh"]), "binding": {"status": "unbound", "error": f"{type(error).__name__}: {error}"}}
            rec = {"status": "unbound"}
        if rec["status"] == "bound":
            key = combo_key(rec["order"], rec["divisions"], rec["layers"])
            row["key"] = key
            row["registered_level"] = (rec["divisions"], rec["layers"]) in FORMAL_LEVELS
            if key in seen:
                row["duplicate_of"] = seen[key]
                pm["children"]["duplicates"].append(row)
            else:
                seen[key] = pair["result"]["attrs"].get("artifact_id")
                pm["children"]["bound"].append(row)
                end_planes.append(rec["z_end"])
        else:
            pm["children"]["unbound"].append(row)
    pm["children"]["orphan_results"] = [_brief(r) for r in oscan["orphan_results"] if r.get("role") in ("solid_child", "other", "unclassified", "unreadable")]
    pm["children"]["orphan_meshes"] = [_brief(m) for m in oscan["orphan_meshes"]]
    registered = [(o, n, l) for n, l in FORMAL_LEVELS for o in ORDERS]
    pm["children"]["missing"] = [{"order": o, "divisions": n, "layers": l, "key": combo_key(o, n, l)}
                                 for o, n, l in registered if combo_key(o, n, l) not in seen]
    if "physical" in pm and end_planes:
        agree = all(abs(z - pm["physical"]["z_end_smoke"]) < 1e-12 for z in end_planes)
        pm["physical"]["end_plane_agrees_with_original_children"] = agree
        if not agree:
            blockers.append({"code": "END_SKIN_DIFFERS", "original_end_planes": end_planes, "smoke": pm["physical"]["z_end_smoke"]})
    pm["children"]["note"] = ("no original child pair could be bound; decks are still written, nothing can be compared"
                              if not pm["children"]["bound"] else "bound pairs listed; missing levels/orders are candidates for the child phase")
    # --- decks
    pm["decks"] = []
    if patch is not None and not blockers:
        rows = write_decks(out, patch, float(smanifest["material"]["elastic_modulus"]), float(smanifest["material"]["poisson_ratio"]),
                           traction, smanifest["skin_side"], FORMAL_LEVELS)
        pm["decks"] = rows
        claimed_decks = {d["name"]: d["sha256"] for d in (omanifest or {}).get("decks", [])} if isinstance(omanifest, dict) else {}
        for row in rows:
            row["matches_original_manifest_sha256"] = (claimed_decks.get(row["name"]) == row["sha256"]) if claimed_decks else None
            existing = original / row["inp"]
            row["matches_original_deck_file"] = (sr.sha256_file(existing) == row["sha256"]) if existing.is_file() else None
            if not row["readback"]["ok"]:
                blockers.append({"code": "DECK_READBACK_FAILED", "deck": row["name"], "checks": row["readback"]["checks"]})
        pm["reference"] = {
            "study": sr.STUDY_ID, "schema": sr.SCHEMA, "preset": "formal", "levels": [list(l) for l in FORMAL_LEVELS], "orders": list(ORDERS),
            "face_id": pm["physical"]["face_id_original"], "material": smanifest["material"], "traction": traction.tolist(),
            "z_end": smanifest["z_end"], "skin_side": smanifest["skin_side"], "box": list(sr.DETAIL_BOX), "region_half": sr.REGION_HALF,
            "parent_field_sha256": pm["parent_field_sha256"], "runs": [],
            "decks": [{k: v for k, v in r.items() if k != "readback"} for r in rows], "integrity_tolerances": sr.INTEGRITY,
            "assumptions": pm["assumptions"], "sources": sr.module_sources(), "script_sha256": pm["script"]["solid_reference_sha256"]}
    # --- inputs untouched?
    changed = [p for p, digest in pm["inputs_sha256"].items() if sr.sha256_file(Path(p)) != digest]
    pm["originals_unchanged_after_prepare"] = not changed
    if changed:
        blockers.append({"code": "INPUT_CHANGED_DURING_PREPARE", "files": changed})
    pm["status"] = "ready" if not blockers and pm["decks"] else "blocked"
    write_new(out / MANIFEST_NAME, dumps(pm))
    return pm


def load_prepared(prepared: Path, require_ready: bool = False) -> Dict[str, Any]:
    path = Path(prepared) / MANIFEST_NAME
    if not path.is_file():
        raise StudyError("PREPARED_MISSING", f"{path} is absent: run prepare first")
    try:
        pm = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise StudyError("PREPARED_INVALID", str(error)) from error
    if pm.get("kind") != PARTIAL_KIND or pm.get("schema") != PARTIAL_SCHEMA:
        raise StudyError("PREPARED_INVALID", "not a manifest of this driver")
    if require_ready and pm.get("status") != "ready":
        raise StudyError("PREPARED_BLOCKED", "the preparation is blocked; its typed blockers are in the manifest", blockers=pm.get("blockers"))
    return pm


# ----------------------------------------------------------------------
# child phase
# ----------------------------------------------------------------------
def read_ledger(prepared: Path) -> List[Dict[str, Any]]:
    path = Path(prepared) / LEDGER_NAME
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_ledger(prepared: Path, entry: Mapping[str, Any]) -> None:
    with open(Path(prepared) / LEDGER_NAME, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({**entry, "utc": _utc()}, sort_keys=True, default=_default) + "\n")
        handle.flush()


def plan_child(prepared: Path, order: int, divisions: int, layers: Optional[int] = None) -> Dict[str, Any]:
    """All refusals of the child phase that need no ANYfem stack."""

    pm = load_prepared(prepared, require_ready=True)
    layers = int(layers) if layers is not None else int(divisions) // 2
    level = (int(divisions), layers)
    if int(order) not in ORDERS:
        raise StudyError("ORDER_INVALID", f"order {order} not in {list(ORDERS)}")
    if level not in FORMAL_LEVELS:
        raise StudyError("LEVEL_UNREGISTERED", f"{level} is not one of the registered levels {list(FORMAL_LEVELS)}")
    key = combo_key(int(order), *level)
    for row in pm["children"]["bound"] + pm["children"]["duplicates"]:
        if row.get("key") == key:
            raise StudyError("ORIGINAL_PAIR_EXISTS", f"{key} already exists as a complete original pair; not repeated", artifact=row["result"].get("artifact_id"))
    if key not in {m["key"] for m in pm["children"]["missing"]}:
        raise StudyError("NOT_MISSING", f"{key} is not a missing original level/order")
    for entry in read_ledger(prepared):
        if entry.get("key") == key and entry.get("state") in ("started", "completed", "run_finished_unsaved"):
            raise StudyError("DUPLICATE_CHILD", f"{key} has a ledger entry in state {entry['state']!r}; inspect it, it is never repeated silently", entry=entry)
    sm = pm["surrogate"]["manifest"]
    for run in sm.get("runs", []):
        if (run.get("order"), run.get("n"), run.get("layers")) == (int(order), level[0], level[1]):
            raise StudyError("DUPLICATE_PAIR_IN_COPY", f"the smoke copy already holds {key}; a second pair is refused")
    return {"key": key, "order": int(order), "divisions": level[0], "layers": level[1], "prepared": pm}


def _copy_surrogate(prepared: Path, pm: Mapping[str, Any]) -> Path:
    root = Path(pm["surrogate"]["root"])
    project = root / pm["surrogate"]["project"]
    data = project.with_name(project.name + "-data")
    target = Path(prepared) / "child_project"
    path = target / project.name
    if target.exists():
        return path
    if not project.is_file() or not data.is_dir():
        raise StudyError("SURROGATE_PROJECT_MISSING", "the smoke project file or its sidecar directory is absent", project=str(project))
    for rec in pm["surrogate"]["results"] + pm["surrogate"]["meshes"]:
        if sr.sha256_file(Path(rec["path"])) != rec["sha256"]:
            raise StudyError("SURROGATE_CHANGED", f"{rec['name']} no longer matches its prepared hash")
    target.mkdir(parents=True, exist_ok=False)
    shutil.copy2(project, path)
    shutil.copytree(data, path.with_name(path.name + "-data"))
    for top in pm["surrogate"]["top_level_files"]:
        if top["name"] == project.name and sr.sha256_file(path) != top["sha256"]:
            raise StudyError("COPY_MISMATCH", "the copied project differs from the smoke project")
    return path


def run_child(prepared: Path, order: int, divisions: int, layers: Optional[int] = None) -> Dict[str, Any]:
    plan = plan_child(prepared, order, divisions, layers)
    pm, key = plan["prepared"], plan["key"]
    S = sr._stack()
    path = _copy_surrogate(prepared, pm)
    baseline = {r["name"]: r["sha256"] for r in pm["surrogate"]["results"] + pm["surrogate"]["meshes"]}
    for rec in scan_artifacts(path.with_name(path.name + "-data"))["results"] + scan_artifacts(path.with_name(path.name + "-data"))["meshes"]:
        if rec["name"] in baseline and rec["sha256"] != baseline[rec["name"]]:
            raise StudyError("COPY_MISMATCH", f"{rec['name']} in the copy differs from the smoke sidecar")
    job_id = pm["surrogate"]["job_id"]
    face_id = int(pm["surrogate"]["manifest"]["face_id"])
    n, nl, o = plan["divisions"], plan["layers"], plan["order"]
    append_ledger(prepared, {"state": "started", "key": key, "order": o, "divisions": n, "layers": nl, "source_job_id": job_id, "copy": str(path)})
    try:
        project = S["load_project"](path)
        store = S["ArtifactStore"](path)
        session = S["DocumentSession"](project)
        cut = S["cut_solid_submodel"](session.snapshot(), source=S["SourceReference"](job_id, "default"), face_id=face_id,
                                      footprint=sr.FOOTPRINT, target_size=(0.2 / n) * (1.0 + 1.0e-9), layers=nl, order=o,
                                      pressure_skin="end", name=f"partial recovery n{n} l{nl} p{o}")
        with session.transaction("add solid submodel", solver_affecting=False):
            S["add_submodel"](project, cut.definition)
        published = S["run_submodel"](session, cut.definition.id, store)
        run = published.run
    except Exception as error:  # noqa: BLE001 - keep the evidence of a failed attempt
        append_ledger(prepared, {"state": "failed", "key": key, "error": f"{type(error).__name__}: {error}", "traceback": traceback.format_exc()})
        raise StudyError("CHILD_RUN_FAILED", f"{type(error).__name__}: {error}", key=key) from error
    try:
        S["save_project"](project, path)  # persisted immediately after the run
    except Exception as error:  # noqa: BLE001
        append_ledger(prepared, {"state": "run_finished_unsaved", "key": key, "run_id": run.id, "error": str(error)})
        raise StudyError("PROJECT_SAVE_FAILED", str(error), key=key) from error
    reopened = S["load_project"](path)
    store2 = S["ArtifactStore"](path)
    mesh_id, result_id = run.child["mesh_artifact"]["id"], run.child["result_artifact"]["id"]
    verified = run.id in reopened.submodel_runs and bool(store2.verify(reopened.artifacts[mesh_id])) and bool(store2.verify(reopened.artifacts[result_id]))
    scan = scan_artifacts(path.with_name(path.name + "-data"))
    files = {r["attrs"].get("artifact_id"): r for r in scan["results"] + scan["meshes"]}
    original_ids = {r["result"]["artifact_id"] for r in pm["children"]["bound"]}
    entry = {"state": "completed", "key": key, "order": o, "divisions": n, "layers": nl, "run_id": run.id, "identity": run.identity,
             "definition_id": cut.definition.id, "definition_hash": run.definition_hash, "source_job_id": job_id,
             "mesh_artifact": mesh_id, "result_artifact": result_id, "reopened_and_verified": verified,
             "distinct_from_original": result_id not in original_ids and run.id != job_id,
             "result_path": files.get(result_id, {}).get("path"), "result_sha256": files.get(result_id, {}).get("sha256"),
             "mesh_path": files.get(mesh_id, {}).get("path"), "mesh_sha256": files.get(mesh_id, {}).get("sha256"),
             "notification_error": None if published.notification_error is None else str(published.notification_error)}
    append_ledger(prepared, entry)
    return entry


# ----------------------------------------------------------------------
# read: archived child metrics (read-only adapter, not a claimed project)
# ----------------------------------------------------------------------
def archived_child_metrics(A: Mapping[str, Any], rec: Mapping[str, Any], patch: "sr.ShellPatch", ref_manifest: Mapping[str, Any],
                           file_hashes_ok: bool, identity: str) -> Dict[str, Any]:
    coords = np.asarray(A["mesh_coordinates"], float)
    dofs = np.asarray(A["prescribed_dofs"], dtype=np.int64)
    values = np.asarray(A["prescribed_values"], float)
    xyz = np.asarray(A["coefficient_coordinates"], float)[dofs]
    comp = np.asarray(A["coefficient_components"], dtype=np.int64)[dofs]
    unique, inverse = np.unique(np.round(xyz, 12), axis=0, return_inverse=True)
    field, spread = sr.evaluate_patch(patch, unique)
    diff = np.abs(field[inverse.reshape(-1), comp] - values)
    scale = max(float(np.max(np.abs(values))), 1e-300)
    worst = np.argsort(diff)[::-1][:10]
    boundary = {"coefficients": int(len(dofs)), "locations": int(len(unique)), "max_abs": float(diff.max()),
                "max_rel": float(diff.max() / scale), "field_scale": scale, "hit_spread": spread,
                "ok": bool(diff.max() / scale <= TRACE_TOL),
                "failing_coefficients": int(np.count_nonzero(diff / scale > TRACE_TOL)),
                "distribution_rel": {f"p{q}": float(np.percentile(diff, q) / scale) for q in (50, 90, 99, 100)},
                "worst": [{"coefficient": int(dofs[i]), "component": int(comp[i]), "xyz": xyz[i].tolist(), "abs": float(diff[i])} for i in worst],
                "abs_diff_sha256": array_sha256(diff)}
    cx, cy = sr.CENTRE
    w, S6 = np.asarray(A["recovery_weights"], float), np.asarray(A["recovery_stress"], float)
    centroid = np.asarray(A["recovery_coordinates"], float).mean(axis=1)
    half = ref_manifest["region_half"]
    sel = (np.abs(centroid[:, 0] - cx) < half) & (np.abs(centroid[:, 1] - cy) < half)
    if not bool(sel.any()):
        raise StudyError("REGION_EMPTY", "no child cell has its centroid inside the stress region")
    volume = float(w[sel].sum())
    expected_volume = 2 * half * 2 * half * (sr.DETAIL_BOX[5] - sr.DETAIL_BOX[2])
    average = [float((w[sel] * S6[sel][:, :, a, b]).sum() / volume) for a, b in sr.VOIGT]
    mesh_like = SimpleNamespace(coordinates=coords)
    ground = np.asarray(A["displacement"], float)[0]
    z_end = ref_manifest["z_end"]
    # signed interface work and reaction closure
    react = np.asarray(A["reactions"], float)
    work = float(np.sum(react * values))
    by_comp = [float(np.sum((react * values)[comp == c])) for c in range(3)]
    origin = np.asarray(A["moment_origin"], float) if A.get("moment_origin") is not None else np.zeros(3)
    unit = np.eye(3)[comp]
    force_sum = np.sum(react[:, None] * unit, axis=0)
    moment_sum = np.sum(np.cross(xyz - origin, react[:, None] * unit), axis=0)
    f_stored, m_stored = np.asarray(A["interface_force"], float), np.asarray(A["interface_moment"], float)
    f_scale = max(float(np.linalg.norm(ref_manifest["traction"])) * 0.04, 1e-300)
    force_closure = float(np.linalg.norm(force_sum - f_stored)) / f_scale
    moment_closure = float(np.linalg.norm(moment_sum - m_stored)) / (f_scale * sr.LENGTH_SCALE)
    end_rows = rec["derived"]["end_rows"]
    tri = coords[end_rows]
    area = float(np.sum(0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)))
    expected_skin = np.asarray(ref_manifest["traction"], float) * area
    skin = np.asarray(A["skin_force"], float)
    return {
        "boundary": boundary,
        "u_end_centre": sr._child_point(mesh_like, ground, (cx, cy, z_end)),
        "u_mid_centre": sr._child_point(mesh_like, ground, (cx, cy, 0.0)),
        "interface_force": f_stored.tolist(), "interface_moment": m_stored.tolist(),
        "skin_force": skin.tolist(), "skin_moment": np.asarray(A["skin_moment"], float).tolist(),
        "stress_region": {"components": ["xx", "yy", "zz", "xy", "xz", "yz"], "average": average, "volume": volume,
                          "volume_rel_error": abs(volume - expected_volume) / expected_volume,
                          "aligned": bool(abs(volume - expected_volume) / expected_volume <= sr.INTEGRITY["region_volume_rel"])},
        "interface_work": {"total": work, "by_component": by_comp, "definition": "sum(reaction_i * prescribed_value_i), reaction = K q - f"},
        "closure": {"force_rel": force_closure, "moment_rel": moment_closure, "moment_origin": origin.tolist(), "tolerance": CLOSURE_TOL,
                    "status": "pass" if max(force_closure, moment_closure) <= CLOSURE_TOL else "fail", "gating": False},
        "skin_vector_observation": {"end_area": area, "expected_traction_times_area": expected_skin.tolist(), "measured": skin.tolist(),
                                    "abs_diff": float(np.linalg.norm(skin - expected_skin)),
                                    "abs_diff_if_negated": float(np.linalg.norm(skin + expected_skin)), "gating": False},
        "tensor_locations": rec["checks"]["tensor_locations_and_coverage"],
        "binding": public_binding(rec),
        "artifacts_verified": bool(file_hashes_ok),
        "run_identity": identity,
    }


# ----------------------------------------------------------------------
# read: CCX interface work (bound .dat and deck, with printed precision)
# ----------------------------------------------------------------------
def reference_interface_work(out: Path, ref_manifest: Mapping[str, Any], deck: Mapping[str, Any]) -> Dict[str, Any]:
    """W = sum over held nodes of (RF - withheld skin load) . prescribed value, with its worst-case print bound."""

    info = sr.parse_deck((Path(out) / deck["inp"]).read_text(encoding="ascii"))
    result = sr.parse_dat((Path(out) / deck["dat"]).read_text(encoding="utf-8", errors="replace"))
    mesh = sr.reference_mesh(deck["element"], deck["n"], deck["layers"], ref_manifest["box"])
    forces = sr.skin_forces(mesh, ref_manifest["skin_side"], ref_manifest["traction"])
    total, bound = 0.0, 0.0
    by_comp = np.zeros(3)
    for k in sorted(info["boundary"]):
        bc = np.asarray(info["boundary"][k], float)
        rf = np.asarray(result["forces"][k], float)
        half = np.array([sr.print_half_ulp(t) for t in result["forces_raw"][k]])
        react = rf - np.asarray(forces.get(k, (0.0, 0.0, 0.0)), float)
        terms = react * bc
        by_comp += terms
        total += float(terms.sum())
        bound += float(np.sum(np.abs(bc) * half) + sr.DECK_REPR_REL * np.sum(np.abs(bc) * np.abs(react)))
    return {"total": total, "by_component": by_comp.tolist(), "print_bound": bound, "held_nodes": len(info["boundary"]),
            "definition": "sum((RF - withheld skin load) * prescribed value); bound = sum|u|*half-ulp(RF) + 13-digit deck representation"}


def compare_work(child: Mapping[str, Any], ref: Mapping[str, Any]) -> Dict[str, Any]:
    w, r = child["interface_work"]["total"], ref["interface_work"]["total"]
    diff = abs(w - r)
    bound = ref["interface_work"]["print_bound"]
    return {"child": w, "reference": r, "abs": diff, "rel": diff / max(abs(r), 1e-300), "reference_print_bound": bound,
            "same_sign": bool(np.sign(w) == np.sign(r)),
            "difference_exceeds_print_bound": bool(diff > bound)}


# ----------------------------------------------------------------------
# read
# ----------------------------------------------------------------------
def _next_report(prepared: Path) -> Path:
    folder = Path(prepared) / "reports"
    folder.mkdir(exist_ok=True)
    index = 1
    while (folder / f"read_{index:04d}.json").exists():
        index += 1
    return folder / f"read_{index:04d}.json"


def _failure(code: str, message: str, **details: Any) -> Dict[str, Any]:
    return {"status": "failed", "code": code, "message": message, "details": details}


def read_prepared(prepared: Path) -> Dict[str, Any]:
    prepared = Path(prepared)
    pm = load_prepared(prepared, require_ready=True)
    ref = pm["reference"]
    # the bound originals must still be the bound bytes
    changed = [p for p, digest in pm["inputs_sha256"].items() if not Path(p).is_file() or sr.sha256_file(Path(p)) != digest]
    parent = pm["parents"]["original"]
    P_o = load_parent(Path(parent["result"]["path"]), Path(parent["mesh"]["path"]))
    patch = parent_patch(P_o, pm["physical"]["face_id_original"])
    if hashlib.sha256(np.ascontiguousarray(patch.U).tobytes()).hexdigest() != pm["parent_field_sha256"]:
        raise StudyError("PARENT_FIELD_CHANGED", "the original parent field no longer matches the prepared hash")
    entries: Dict[str, Dict[str, Any]] = {}
    origin: Dict[str, str] = {}

    def one(path_r: str, path_m: str, expected_hashes: Tuple[Optional[str], Optional[str]],
            source_parent: Dict[str, Any], identity: str) -> Dict[str, Any]:
        try:
            hashes_ok = (sr.sha256_file(Path(path_r)) == expected_hashes[0]) and (sr.sha256_file(Path(path_m)) == expected_hashes[1])
            if not hashes_ok:
                return _failure("ARTIFACT_CHANGED", "sidecar bytes differ from the bound hashes", result=path_r, mesh=path_m)
            A = load_child(Path(path_r), Path(path_m))
            rec = inspect_child(A, {"document_id": source_parent["result"]["document_id"],
                                    "source_job_id": source_parent["result"]["artifact_id"]})
            if rec["status"] != "bound":
                return _failure("CHILD_UNBOUND", "pair failed binding checks: " + ", ".join(rec["failed_checks"]), binding=public_binding(rec))
            metrics = archived_child_metrics(A, rec, patch, ref, hashes_ok, identity)
            metrics["status"] = "ok"
            return metrics
        except StudyError as error:
            return error.as_dict()
        except (OSError, KeyError, ValueError, np.linalg.LinAlgError) as error:
            return _failure("CHILD_UNREADABLE", f"{type(error).__name__}: {error}")

    for row in pm["children"]["bound"]:
        key = row["key"]
        entries[key] = one(row["result"]["path"], row["mesh"]["path"], (row["result"]["sha256"], row["mesh"]["sha256"]),
                           parent, row["result"]["artifact_id"])
        origin[key] = "original"
    for row in pm["children"]["unbound"] + pm["children"]["duplicates"]:
        key = row.get("key") or f"unbound_{row['result'].get('artifact_id')}"
        entries.setdefault(key, _failure("CHILD_UNBOUND", "original pair could not be bound or duplicates a bound pair", row=row))
        origin.setdefault(key, "original_unavailable")
    for led in read_ledger(prepared):
        if led.get("state") != "completed":
            if led.get("state") in ("failed", "started", "run_finished_unsaved"):
                entries.setdefault(led["key"], _failure("NEW_CHILD_NOT_COMPLETED", f"ledger state {led['state']}", entry=led))
                origin.setdefault(led["key"], "new_unavailable")
            continue
        surrogate = pm["parents"]["surrogate"]
        if led.get("source_job_id") != surrogate["result"]["artifact_id"]:
            entries[led["key"]] = _failure("CHILD_UNBOUND", "ledger source job differs from the bound surrogate parent")
        else:
            entries[led["key"]] = one(led["result_path"], led["mesh_path"], (led["result_sha256"], led["mesh_sha256"]),
                                      surrogate, led["result_artifact"])
        origin[led["key"]] = "new"
    for miss in pm["children"]["missing"]:
        entries.setdefault(miss["key"], _failure("CHILD_ABSENT", f"no complete child pair for {miss['key']}", **miss))
        origin.setdefault(miss["key"], "absent")

    runs = [{"element": sr.ORDER_ELEMENT[o], "n": n, "layers": l, "order": o} for n, l in FORMAL_LEVELS for o in ORDERS]
    manifest = {**ref, "runs": runs}
    refs = sr.read_reference_outputs(prepared, manifest)
    dat_hashes = {}
    for deck in manifest["decks"]:
        if refs[deck["name"]].get("status") == "ok":
            try:
                refs[deck["name"]]["interface_work"] = reference_interface_work(prepared, manifest, deck)
            except (StudyError, KeyError, OSError) as error:
                refs[deck["name"]]["interface_work_failure"] = str(error)
        dat = prepared / deck["dat"]
        dat_hashes[deck["name"]] = {"inp_sha256": sr.sha256_file(prepared / deck["inp"]) if (prepared / deck["inp"]).is_file() else None,
                                    "dat_sha256": sr.sha256_file(dat) if dat.is_file() else None}
    report = sr.assemble(manifest, refs, entries)
    finest = FORMAL_LEVELS[-1]
    work = {}
    for comp in report["comparisons"]:
        child = entries.get(comp["run"])
        if not child or child.get("status") != "ok":
            continue
        row = {}
        for label, (n, l) in (("same_level", tuple(comp["level"])), ("finest", finest)):
            r = refs.get(sr.deck_name(sr.ORDER_ELEMENT[comp["order"]], n, l))
            row[label] = compare_work(child, r) if r and r.get("status") == "ok" and "interface_work" in r else {"status": "reference_unavailable"}
        work[comp["run"]] = row
    report["partial"] = {
        "kind": PARTIAL_KIND, "prepared_manifest_sha256": sr.sha256_file(prepared / MANIFEST_NAME), "child_origin": origin,
        "interface_work_comparison": work, "bound_inputs_unchanged": not changed, "changed_inputs": changed,
        "reference_files": dat_hashes,
        "closure_observations": {k: v["closure"] for k, v in entries.items() if v.get("status") == "ok"},
        "skin_vector_observations": {k: v["skin_vector_observation"] for k, v in entries.items() if v.get("status") == "ok"},
        "script_sha256": sr.sha256_file(Path(__file__).resolve()),
        "note": "children were read from archived files through a read-only adapter; no project is claimed or reconstructed"}
    if changed:
        report["status"] = "incomplete"
    write_new(_next_report(prepared), dumps(report))
    return report


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--original", required=True)
    p.add_argument("--surrogate", required=True)
    p.add_argument("--out", required=True)
    c = sub.add_parser("child")
    c.add_argument("--prepared", required=True)
    c.add_argument("--order", type=int, required=True)
    c.add_argument("--divisions", type=int, required=True)
    c.add_argument("--layers", type=int, default=None)
    r = sub.add_parser("read")
    r.add_argument("--prepared", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            pm = prepare(Path(args.original), Path(args.surrogate), Path(args.out))
            print(dumps({"status": pm["status"], "blockers": pm["blockers"],
                         "bound_children": [c["key"] for c in pm["children"]["bound"]],
                         "missing": [m["key"] for m in pm["children"]["missing"]], "decks": [d["inp"] for d in pm["decks"]]}))
            return 0 if pm["status"] == "ready" else 3
        if args.command == "child":
            print(dumps(run_child(Path(args.prepared), args.order, args.divisions, args.layers)))
            return 0
        report = read_prepared(Path(args.prepared))
        print(dumps({"status": report["status"], "failures": report["failures"], "unresolved": report["unresolved"],
                     "comparisons": report["comparisons"], "interface_work": report["partial"]["interface_work_comparison"]}))
        return 0 if report["status"] == "complete" else 3
    except StudyError as error:
        print(dumps(error.as_dict()))
        return 2
    except Exception as error:  # noqa: BLE001
        print(dumps({"status": "failed", "code": "UNEXPECTED", "message": f"{type(error).__name__}: {error}", "traceback": traceback.format_exc()}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
