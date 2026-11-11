"""Mesh and result sidecars of shell-driven solid submodel runs.

The solid mesh uses its own HDF5 schema (``anyfem.solid_mesh``) but the same
store identities, ``meshes/<id>.anymesh.h5`` path, atomic commit, checksum and
size verification as the shell mesh codec; ``ArtifactStore.read_mesh`` refuses
it by schema, so a caller must dispatch to :func:`read_solid_mesh` /
:func:`read_solid_mesh_metadata` (the artifact descriptor is ``kind='mesh'``).

Version 2 of the mesh also stores the exact detached ANYgeometry model the mesh was generated from
(``anygeometry.serialization.to_dict``, checksummed), its ``region_id`` and the boundary tolerance, next to the
exact facet owners.  :func:`read_solid_mesh` rebuilds that model and revalidates the groups, every facet owner and
the orientation against it, so a stored mesh is checked against its own origin, never against a model re-derived
from the current (possibly changed or deleted) parent.  Version 1 meshes carry no source and still read as an
unbound geometric audit (``record.source_geometry is None``).

The result is an ordinary ``ArtifactStore.write_result`` artifact; the typed
solid quantities (displacement, coefficients, true integration-point locations,
weights, strain, stress, interface resultants) are stored as numeric tables
next to a ``solid_fields`` JSON table that declares each one's meaning.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np

from ..model.records import ArtifactRef, ResultQuantityDescriptor
from .artifacts import (
    ArtifactError,
    ArtifactStore,
    _artifact,
    _atomic_destination,
    _dataset,
    _h5py,
    _json,
    _root_attributes,
    _text_attribute,
)

__all__ = [
    "ATTRIBUTION_RULE",
    "BOX_GROUPS",
    "INTERFACE_VERSION",
    "MOMENT_FRAME",
    "SOLID_MESH_SCHEMA",
    "SolidMeshRecord",
    "check_solid_mesh_against_region",
    "group_virtual_work",
    "interface_problems",
    "interface_virtual_work",
    "read_solid_mesh",
    "read_solid_mesh_metadata",
    "read_solid_result",
    "solid_interface_arrays",
    "write_solid_mesh",
    "write_solid_result",
]

SOLID_MESH_SCHEMA = "anyfem.solid_mesh"
_VERSION = 2
#: Mesh versions the reader accepts: 1 has no stored source (unbound audit), 2 stores the detached source model.
_READABLE_VERSIONS = (1, 2)
#: The solver's attribution convention for a DOF owned by several cut groups
#: (``anysolver.solid_submodel.ATTRIBUTION_RULE``; a test pins the two together).
ATTRIBUTION_RULE = "equal_split_among_groups_sharing_the_dof"
#: Moments are about the world origin, components in the world frame (the solver's ``_resultant``).
MOMENT_FRAME = "world"
#: Results carrying the cut-interface tables are version 2; results without ``solid_interface`` are the
#: previous variant and still read (``result["interface"] is None``).
INTERFACE_VERSION = 2
#: The box face groups of a shell-driven detail: the skins and the four cut sides.
BOX_GROUPS = ("start", "end", "side_0", "side_1", "side_2", "side_3")
_REL = 1.0e-9  # recomputation tolerance: summation order differs from the solver's, nothing else
_EPS = float(np.finfo(float).eps)


class SolidMeshRecord(dict):
    """Decoded solid mesh: a dict with attribute access.

    Keys: ``coordinates`` (N,3), ``node_ids``, ``cells`` (C,4 zero-based rows,
    positively oriented Tet4), ``cell_ids``, ``boundary_facets`` (F,3, outward),
    ``boundary_ids``, ``face_owners`` ({boundary id: (source model id, face id)}),
    ``node_groups`` / ``cell_groups`` / ``boundary_groups`` ({name: [ids]}),
    ``units``, ``geometry_order``, ``provenance``, and the stored source: ``source_geometry`` (the detached
    ``GeometryModel``, ``None`` for a version 1 mesh), ``region_id`` and ``boundary_tolerance`` (``None`` likewise).
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


# ----------------------------------------------------------------------
# mesh
# ----------------------------------------------------------------------
def _groups(mapping: Mapping[str, Any]) -> Dict[str, list]:
    return {str(k): [int(v) for v in np.asarray(ids).reshape(-1)] for k, ids in sorted(mapping.items())}


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _mesh_problems(x: np.ndarray, node_ids: np.ndarray, cells: np.ndarray, cell_ids: np.ndarray, facets: np.ndarray,
                   boundary_ids: np.ndarray, owner_faces: np.ndarray, units: str, order: int) -> list:
    """Why decoded (or about to be written) solid mesh arrays are inconsistent; shared by writer and reader."""

    problems = []
    if x.ndim != 2 or x.shape[1] != 3 or not np.isfinite(x).all():
        problems.append("coordinates are not a finite (N,3) array")
    elif node_ids.shape != (len(x),) or len(np.unique(node_ids)) != len(x):
        problems.append("node ids are not unique per coordinate row")
    elif cells.ndim != 2 or cells.shape[1] != 4 or not len(cells) or cells.min() < 0 or cells.max() >= len(x):
        problems.append("cells are not a nonempty (C,4) array of valid rows")
    elif cell_ids.shape != (len(cells),) or len(np.unique(cell_ids)) != len(cells):
        problems.append("cell ids are not unique per cell")
    else:
        d = x[cells[:, 1:]] - x[cells[:, :1]]
        if not np.all(np.linalg.det(d) > 0.0):
            problems.append("a Tet4 is not positively oriented")
    if not problems:
        if (facets.ndim != 2 or facets.shape[1] != 3 or boundary_ids.shape != (len(facets),)
                or owner_faces.shape != boundary_ids.shape or len(np.unique(boundary_ids)) != len(boundary_ids)):
            problems.append("boundary ids or face owners do not match the boundary facets")
        elif not len(facets) or facets.min() < 0 or facets.max() >= len(x):
            problems.append("boundary facets are empty or reference unknown rows")
        else:
            problems += _topology_problems(x, cells, facets)
            if not problems:
                problems += _owner_problems(x, facets, boundary_ids, owner_faces)
    if units != "m" or order != 1:
        problems.append("only SI metre, geometry_order=1 meshes are supported")
    return problems


def _canonical(face: Any) -> tuple:
    """A triangle as its cyclic rotation starting at the smallest row: winding is kept, rotation is not."""

    face = tuple(int(v) for v in face)
    start = face.index(min(face))
    return face[start:] + face[:start]


def _topology_problems(x: np.ndarray, cells: np.ndarray, facets: np.ndarray) -> list:
    """Run the mesher's own exhaustive volume validator and require the stored facets to be exactly its exterior.

    ``validate_cells`` proves positive, conforming, non-overlapping, face-connected Tet4 cells and returns the
    outward exterior triangles.  The stored boundary must be that set with the same winding: a missing, extra,
    duplicated, reversed or nonsensical facet is a difference between the two sets.
    """

    try:
        from anymesher._volume_validation import validate_cells
    except ImportError as error:  # fail closed: an unverifiable mesh is not admitted
        return [f"the mesh volume validator is unavailable ({error})"]
    try:
        exterior = validate_cells(x, cells)
    except Exception as error:  # the validator's typed refusals and cancellation both mean "not admitted"
        return [f"cells are not a valid conforming volume mesh: {error}"]
    stored = [_canonical(f) for f in facets]
    wanted = {_canonical(f) for f in exterior}
    if len(set(stored)) != len(stored):
        return ["boundary facets contain duplicates"]
    have = set(stored)
    if have == wanted:
        return []
    reversed_ = {_canonical(f[::-1]) for f in have - wanted} & (wanted - have)
    parts = []
    if reversed_:
        parts.append(f"{len(reversed_)} facets have inward winding")
    missing, extra = len(wanted - have) - len(reversed_), len(have - wanted) - len(reversed_)
    if missing:
        parts.append(f"{missing} exterior facets are missing")
    if extra:
        parts.append(f"{extra} stored facets are not on the exterior")
    return ["boundary facets differ from the cells' exterior: " + ", ".join(parts)]


def _owner_problems(x: np.ndarray, facets: np.ndarray, boundary_ids: np.ndarray, owner_faces: np.ndarray) -> list:
    """Without a region snapshot: each owner face must be one plane and no two owners may share a plane.

    A swapped or nonsensical owner assignment that mixes planes under one face id (or splits one plane
    over several ids) is refused here; which *name* a face carries needs the region snapshot
    (:func:`check_solid_mesh_against_region`).
    """

    if np.any(owner_faces < 0):
        return ["face owner ids must be non-negative"]
    tri = x[facets]
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    size = np.linalg.norm(cross, axis=1)
    diagonal = float(np.linalg.norm(x.max(axis=0) - x.min(axis=0)))
    if not np.all(size > 1.0e-14 * max(diagonal, 1.0e-300) ** 2):
        return ["a boundary facet is degenerate"]
    normal = cross / size[:, None]
    offset = np.einsum("ij,ij->i", normal, tri[:, 0])
    tolerance = 1.0e-9 * diagonal
    planes = []
    for face in np.unique(owner_faces):
        rows = np.flatnonzero(owner_faces == face)
        n0, d0 = normal[rows[0]], offset[rows[0]]
        if (float(np.min(normal[rows] @ n0)) < 1.0 - 1.0e-9
                or float(np.max(np.abs(tri[rows].reshape(-1, 3) @ n0 - d0))) > tolerance):
            return [f"the facets owned by face {int(face)} do not lie on one plane"]
        planes.append((n0, d0, int(face)))
    for i, (ni, di, fi) in enumerate(planes):
        for nj, dj, fj in planes[i + 1:]:
            if float(ni @ nj) > 1.0 - 1.0e-9 and abs(di - dj) <= tolerance:
                return [f"faces {fi} and {fj} own the same plane"]
    return []


def _owner_pair(owner: Any) -> tuple:
    """``(model id, face id)`` of an ``EntityHandle`` or of an already decoded pair."""

    if isinstance(owner, (tuple, list)):
        model, face = owner
    else:
        if getattr(owner, "kind", "face") != "face":
            raise ArtifactError("a solid mesh face owner must be a face")
        model, face = owner.model_id, owner.id
    return str(model), int(face)


def _field(mesh: Any, name: str) -> Any:
    return mesh[name] if isinstance(mesh, Mapping) else getattr(mesh, name)


def check_solid_mesh_against_region(mesh: Any, region: Any, *, boundary_tolerance: float,
                                    group_names: Sequence[str] = BOX_GROUPS, cancel: Any = None) -> None:
    """Refuse unless ``mesh`` (a decoded record or an admitted mesh) is exactly the mesh of ``region``.

    ``region`` is the oriented ``solid_region_snapshot`` the mesh was made from.  The mesher's own
    ``validate_source`` / ``validate_geometry`` are run on the mesh arrays (convex containment, exact
    geometric ownership of every boundary triangle, projected coverage, protected edges, volume), and then:

    * every facet's stored owner handle (model id and face id) equals the geometric owner;
    * each named group is owned by exactly the one handle the snapshot assigns to that name;
    * the groups partition the boundary and no two groups share a handle.

    So a complete swap of ``start``/``end`` groups or handles, or a mesh of another model, is refused.
    """

    try:
        from anymesher._volume_validation import validate_geometry, validate_source
    except ImportError as error:
        raise ArtifactError(f"the mesh volume validator is unavailable ({error})") from None
    try:
        x = np.asarray(_field(mesh, "coordinates"), dtype=np.float64)
        cells = np.asarray(_field(mesh, "cells"), dtype=np.int64)
        facets = np.asarray(_field(mesh, "boundary_facets"), dtype=np.int64).reshape(-1, 3)
        boundary_ids = [int(b) for b in np.asarray(_field(mesh, "boundary_ids"))]
        owners = _field(mesh, "face_owners")
        groups = _field(mesh, "boundary_groups")
        stored = [_owner_pair(owners[b]) for b in boundary_ids]
        source = validate_source(region, boundary_tolerance)
        geometric, _report = validate_geometry(x, cells, facets, source, cancel=cancel)
    except ArtifactError:
        raise
    except Exception as error:
        raise ArtifactError(f"solid mesh does not match its region: {type(error).__name__}: {error}") from None
    found = [(str(h.model_id), int(h.id)) for h in geometric]
    if found != stored:
        wrong = sum(1 for a, b in zip(found, stored) if a != b)
        raise ArtifactError(f"{wrong} of {len(stored)} boundary facets name a face owner that is not their geometric owner")
    position = {b: i for i, b in enumerate(boundary_ids)}
    seen: Dict[int, str] = {}
    handles: Dict[str, tuple] = {}
    for name in group_names:
        wanted = region.face_groups.get(name) if isinstance(region.face_groups, Mapping) else None
        if wanted is None or len(wanted) != 1:
            raise ArtifactError(f"the region has no single face handle for group {name!r}")
        expected = (str(wanted[0].model_id), int(wanted[0].id))
        members = [int(b) for b in groups.get(name, ())]
        if not members:
            raise ArtifactError(f"boundary group {name!r} is empty")
        for b in members:
            if b not in position:
                raise ArtifactError(f"boundary group {name!r} names unknown boundary id {b}")
            if b in seen:
                raise ArtifactError(f"boundary facet {b} is in both {seen[b]!r} and {name!r}")
            seen[b] = name
            if stored[position[b]] != expected:
                raise ArtifactError(f"boundary group {name!r} facet {b} is owned by {stored[position[b]]}, "
                                    f"the region assigns {expected} to {name!r}")
        handles[name] = expected
    if set(seen) != set(boundary_ids):
        raise ArtifactError("the named box groups do not cover the whole boundary")
    if len(set(handles.values())) != len(handles):
        raise ArtifactError("two box groups resolve to the same face handle")


def _group_problems(groups: Any, node_ids: np.ndarray, cell_ids: np.ndarray, boundary_ids: np.ndarray) -> list:
    """Group tables must be ``{kind: {name: [int]}}``; boundary groups must name known boundary ids.

    Node and cell groups are checked for structure only: the stored tables do not
    say whether their members are external ids or rows, and the mesher owns that.
    """

    if not isinstance(groups, Mapping):
        return ["groups are not an object"]
    problems = []
    known = set(map(int, boundary_ids))
    for kind in ("node_groups", "cell_groups", "boundary_groups"):
        table = groups.get(kind, {})
        if not isinstance(table, Mapping):
            problems.append(f"{kind} is not an object")
            continue
        for name, ids in table.items():
            if not isinstance(ids, (list, tuple)) or any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
                problems.append(f"{kind}[{name!r}] is not a list of integers")
            elif kind == "boundary_groups" and not set(ids) <= known:
                problems.append(f"{kind}[{name!r}] names unknown boundary ids")
    return problems


def _source_text(detail: Any, region_id: Any, boundary_tolerance: Any) -> tuple:
    """``(geometry json, region id, tolerance)`` of a detached source model, as stored in a version 2 mesh."""

    try:
        from anygeometry.serialization import to_dict

        tolerance = float(boundary_tolerance)
        if isinstance(boundary_tolerance, bool) or not (math.isfinite(tolerance) and tolerance > 0.0):
            raise ArtifactError("the boundary tolerance must be a positive finite number")
        if not isinstance(region_id, str) or not region_id:
            raise ArtifactError("the region id must be a non-empty string")
        text = json.dumps(to_dict(detail), allow_nan=False, sort_keys=True, separators=(",", ":"))
    except ArtifactError:
        raise
    except Exception as error:
        raise ArtifactError(f"the source geometry cannot be serialized: {type(error).__name__}: {error}") from None
    return text, region_id, tolerance


def _load_source(text: Any, region_id: Any, boundary_tolerance: Any) -> tuple:
    """Rebuild ``(model, oriented region snapshot, tolerance)`` from stored source metadata, or refuse.

    The geometry document is checksum-verified by ``anygeometry.serialization.from_dict``.  The region must be a
    shell-detail box: eight vertices and exactly the six single-face box groups.
    """

    try:
        tolerance = float(boundary_tolerance)
    except (TypeError, ValueError):
        raise ArtifactError("the stored boundary tolerance is malformed") from None
    if isinstance(boundary_tolerance, bool) or not (math.isfinite(tolerance) and tolerance > 0.0):
        raise ArtifactError("the stored boundary tolerance is not a positive finite number")
    if not isinstance(region_id, str) or not region_id:
        raise ArtifactError("the stored region id is malformed")
    if not isinstance(text, str):
        raise ArtifactError("the stored source geometry is not text")
    try:
        from anygeometry.serialization import from_dict

        model = from_dict(json.loads(text))
        region = model.solid_region_snapshot(region_id)
    except Exception as error:  # a damaged, unsupported or mismatched document is never admitted
        raise ArtifactError(f"the stored source geometry is not readable: {type(error).__name__}: {error}") from None
    try:
        problems = []
        if str(model.units) != "m":
            problems.append("the source model is not in metres")
        if np.asarray(region.coordinates).shape != (8, 3) or len(region.face_refs) != 6:
            problems.append("the source region is not an 8-vertex, 6-face box")
        for name in BOX_GROUPS:
            if len(region.face_groups.get(name, ())) != 1:
                problems.append(f"the source region has no single face for group {name!r}")
    except Exception as error:
        raise ArtifactError(f"the stored source region is malformed: {type(error).__name__}: {error}") from None
    if problems:
        raise ArtifactError("; ".join(problems))
    return model, region, tolerance


def _check_against_source(record: Mapping[str, Any], model: Any, region: Any, tolerance: float,
                          group_names: Sequence[str] = BOX_GROUPS) -> None:
    """Every facet owner must belong to the stored source model, then the full region check runs."""

    models = {str(owner[0]) for owner in record["face_owners"].values()}
    if models != {str(model.model_id)}:
        raise ArtifactError("the facet owners name a model other than the stored source geometry")
    check_solid_mesh_against_region(record, region, boundary_tolerance=tolerance, group_names=group_names)


def write_solid_mesh(store: ArtifactStore, mesh: Any, *, mesh_id: str, document_id: str, model_hash: str,
                     mesh_hash: str, detail: Any = None, region_id: Optional[str] = None,
                     boundary_tolerance: Optional[float] = None) -> ArtifactRef:
    """Atomically write an admitted ``anymesher`` VolumeMesh; raises ``ArtifactError`` on malformed input.

    The arrays are checked with the same rules the reader applies, so nothing is
    written that ``read_solid_mesh`` would refuse.

    ``detail`` (the detached ANYgeometry model the mesh was generated from), ``region_id`` and
    ``boundary_tolerance`` are given together.  They are stored with the facet owners (version 2), and the mesh is
    checked against the *reconstructed* stored model before anything is written.  Without them the previous
    unbound variant (version 1) is written.
    """

    given = [detail is not None, region_id is not None, boundary_tolerance is not None]
    if any(given) and not all(given):
        raise ArtifactError("detail, region_id and boundary_tolerance must be given together")
    bound = all(given)
    source = _source_text(detail, region_id, boundary_tolerance) if bound else None
    try:
        boundary_ids = np.asarray([int(b) for b in np.asarray(mesh.boundary_ids)], dtype=np.int64)
        owners = {int(k): v for k, v in mesh.face_owners.items()}
        if set(owners) != set(map(int, boundary_ids)):
            raise ArtifactError("solid mesh needs exactly one face owner per boundary id")
        owner_models = sorted({str(h.model_id) for h in owners.values()})
        if len(owner_models) != 1 or any(h.kind != "face" for h in owners.values()):
            raise ArtifactError("solid mesh face owners must be faces of one source model")
        owner_faces = np.asarray([int(owners[int(b)].id) for b in boundary_ids], dtype=np.int64)
        x = np.asarray(mesh.coordinates, dtype=np.float64)
        node_ids = np.asarray(mesh.node_ids, dtype=np.int64)
        cells = np.asarray(mesh.cells, dtype=np.int64)
        cell_ids = np.asarray(mesh.cell_ids, dtype=np.int64)
        facets = np.asarray(mesh.boundary_facets, dtype=np.int64).reshape(-1, 3)
        groups = {"node_groups": _groups(mesh.node_groups), "cell_groups": _groups(mesh.cell_groups),
                  "boundary_groups": _groups(mesh.boundary_groups)}
        provenance = _plain(mesh.provenance)
        units, order = str(mesh.units), int(mesh.geometry_order)
    except ArtifactError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise ArtifactError(f"solid mesh is malformed: {error}") from None
    problems = _mesh_problems(x, node_ids, cells, cell_ids, facets, boundary_ids, owner_faces, units, order)
    problems += [] if problems else _group_problems(groups, node_ids, cell_ids, boundary_ids)
    if problems:
        raise ArtifactError("solid mesh is inconsistent: " + "; ".join(problems))
    if bound:
        model, region, tolerance = _load_source(*source)
        candidate = {"coordinates": x, "cells": cells, "boundary_facets": facets, "boundary_ids": boundary_ids,
                     "face_owners": {int(b): (owner_models[0], int(f)) for b, f in zip(boundary_ids, owner_faces)},
                     "boundary_groups": groups["boundary_groups"]}
        _check_against_source(candidate, model, region, tolerance)
    boundary_ids = boundary_ids.tolist()
    identifier = str(mesh_id)
    uri = f"meshes/{identifier}.anymesh.h5"
    destination = store.resolve(uri)
    store.ensure()
    h5py = _h5py()
    with _atomic_destination(destination) as temporary:
        with h5py.File(temporary, "w") as handle:
            _root_attributes(handle, schema=SOLID_MESH_SCHEMA, artifact_id=identifier, document_id=document_id)
            handle.attrs["version"] = _VERSION if bound else 1
            handle.attrs["model_hash"] = model_hash
            handle.attrs["mesh_hash"] = mesh_hash
            handle.attrs["element_type"] = "tet4"
            group = handle.create_group("solid_mesh")
            _dataset(group, "coordinates", x)
            _dataset(group, "node_ids", node_ids)
            _dataset(group, "cells", cells)
            _dataset(group, "cell_ids", cell_ids)
            _dataset(group, "boundary_facets", facets)
            _dataset(group, "boundary_ids", np.asarray(boundary_ids, dtype=np.int64))
            _dataset(group, "face_owner_face_ids", owner_faces)
            string = h5py.string_dtype("utf-8")
            group.create_dataset("face_owner_model_id", data=owner_models[0], dtype=string)
            group.create_dataset("groups_json", dtype=string, data=_json(groups))
            group.create_dataset("provenance_json", data=_json(provenance), dtype=string)
            group.attrs["units"] = units
            group.attrs["geometry_order"] = order
            if bound:
                origin = handle.create_group("solid_mesh_source")
                origin.create_dataset("geometry_json", data=source[0], dtype=string)
                origin.attrs["region_id"] = source[1]
                origin.attrs["boundary_tolerance"] = source[2]
            handle.attrs["complete"] = True
            handle.flush()
    return _artifact(uri, identifier, "mesh", destination)


def _open_mesh(store: ArtifactStore, artifact: Any):
    path = store._path_for_read(artifact, "mesh")
    h5py = _h5py()
    handle = h5py.File(path, "r")
    try:
        store._validate_root(handle, SOLID_MESH_SCHEMA, max_version=_VERSION)
        store._validate_artifact_identity(handle, artifact)
        if "solid_mesh" not in handle:
            raise ArtifactError("solid mesh group is missing")
    except BaseException:
        handle.close()
        raise
    return path, handle


def read_solid_mesh_metadata(store: ArtifactStore, artifact: Any) -> Dict[str, Any]:
    """Verified identity and counts of a solid mesh artifact without reading its arrays."""

    try:
        path, handle = _open_mesh(store, artifact)
        with handle:
            group = handle["solid_mesh"]
            return {
                "schema": _text_attribute(handle.attrs.get("schema", "")),
                "version": int(handle.attrs.get("version", 0)),
                "artifact_id": _text_attribute(handle.attrs.get("artifact_id", "")),
                "document_id": _text_attribute(handle.attrs.get("document_id", "")),
                "created_utc": _text_attribute(handle.attrs.get("created_utc", "")),
                "model_hash": _text_attribute(handle.attrs.get("model_hash", "")),
                "mesh_hash": _text_attribute(handle.attrs.get("mesh_hash", "")),
                "element_type": _text_attribute(handle.attrs.get("element_type", "")),
                "byte_size": path.stat().st_size,
                "nodes": int(group["coordinates"].shape[0]),
                "cells": int(group["cells"].shape[0]),
                "boundary_facets": int(group["boundary_ids"].shape[0]),
                "bound": "solid_mesh_source" in handle,
                "region_id": (_text_attribute(handle["solid_mesh_source"].attrs.get("region_id", ""))
                              if "solid_mesh_source" in handle else None),
            }
    except ArtifactError:
        raise
    except (OSError, KeyError, TypeError, ValueError, AttributeError, UnicodeError) as error:
        raise ArtifactError(f"cannot read solid mesh metadata: {error}") from None


def read_solid_mesh(store: ArtifactStore, artifact: Any, *, region: Any = None,
                    boundary_tolerance: Optional[float] = None,
                    group_names: Sequence[str] = BOX_GROUPS) -> SolidMeshRecord:
    """Decode and re-check a solid mesh: shapes, ids, ranges, the cells' full volume validation, the exact
    exterior facets (winding included) and the owner planes.

    A version 2 mesh also carries its detached source model: it is rebuilt and the groups, every facet owner and
    the orientation are revalidated against it (:func:`check_solid_mesh_against_region`), whatever has become of
    the parent since.  A version 1 mesh has no source and gets only the self-consistency checks
    (``record.source_geometry is None``).

    ``region`` (an oriented region snapshot) and ``boundary_tolerance`` are an *additional* check for a caller that
    holds its own expectation; they never replace the stored source, so a mesh of another model is refused.
    """

    record = _read_solid_mesh(store, artifact)
    if (region is None) != (boundary_tolerance is None):
        raise ArtifactError("region and boundary_tolerance must be given together")
    if region is not None:
        check_solid_mesh_against_region(record, region, boundary_tolerance=float(boundary_tolerance),
                                        group_names=group_names)
    return record


def _read_solid_mesh(store: ArtifactStore, artifact: Any) -> SolidMeshRecord:
    try:
        _path, handle = _open_mesh(store, artifact)
        with handle:
            group = handle["solid_mesh"]
            x = np.asarray(group["coordinates"], dtype=np.float64)
            node_ids = np.asarray(group["node_ids"], dtype=np.int64)
            cells = np.asarray(group["cells"], dtype=np.int64)
            cell_ids = np.asarray(group["cell_ids"], dtype=np.int64)
            facets = np.asarray(group["boundary_facets"], dtype=np.int64).reshape(-1, 3)
            boundary_ids = np.asarray(group["boundary_ids"], dtype=np.int64)
            owner_faces = np.asarray(group["face_owner_face_ids"], dtype=np.int64)
            model_id = _text_attribute(group["face_owner_model_id"][()])
            groups = json.loads(_text_attribute(group["groups_json"][()]))
            provenance = json.loads(_text_attribute(group["provenance_json"][()]))
            units = _text_attribute(group.attrs.get("units", ""))
            order = int(group.attrs.get("geometry_order", 0))
            version = int(handle.attrs.get("version", 0))
            stored = "solid_mesh_source" in handle
            source = None
            if stored:
                origin = handle["solid_mesh_source"]
                source = (_text_attribute(origin["geometry_json"][()]),
                          _text_attribute(origin.attrs["region_id"]), float(origin.attrs["boundary_tolerance"]))
    except ArtifactError:
        raise
    except (OSError, KeyError, TypeError, ValueError, AttributeError, UnicodeError) as error:
        raise ArtifactError(f"cannot read solid mesh: {error}") from None
    problems = _mesh_problems(x, node_ids, cells, cell_ids, facets, boundary_ids, owner_faces, units, order)
    problems += [] if problems else _group_problems(groups, node_ids, cell_ids, boundary_ids)
    if not isinstance(provenance, Mapping):
        problems.append("provenance is not an object")
    if version not in _READABLE_VERSIONS:
        problems.append(f"solid mesh version {version} is not supported (supported: {_READABLE_VERSIONS})")
    elif (version == 2) != stored:
        problems.append(f"a version {version} solid mesh {'must' if version == 2 else 'cannot'} carry its source geometry")
    if problems:
        raise ArtifactError("solid mesh is inconsistent: " + "; ".join(problems))
    owners = {int(b): (model_id, int(f)) for b, f in zip(boundary_ids, owner_faces)}
    record = SolidMeshRecord(
        coordinates=x, node_ids=node_ids, cells=cells, cell_ids=cell_ids, boundary_facets=facets,
        boundary_ids=boundary_ids, face_owners=owners, node_groups=groups.get("node_groups", {}),
        cell_groups=groups.get("cell_groups", {}), boundary_groups=groups.get("boundary_groups", {}),
        units=units, geometry_order=order, provenance=provenance,
        source_geometry=None, region_id=None, boundary_tolerance=None)
    if source is not None:
        model, region, tolerance = _load_source(*source)
        _check_against_source(record, model, region, tolerance)
        record.update(source_geometry=model, region_id=source[1], boundary_tolerance=tolerance)
    return record


# ----------------------------------------------------------------------
# result
# ----------------------------------------------------------------------
_TABLES = {
    # key: (location, unit, components, meaning)
    "coefficients": ("dof", "m", ("value",), "P1/P2 displacement coefficients, one per prepared DOF"),
    "coefficient_coordinates": ("dof", "m", ("x", "y", "z"), "physical location of each coefficient DOF"),
    "coefficient_components": ("dof", "", ("axis",), "displacement axis (0,1,2) of each coefficient DOF"),
    "recovery_coordinates": ("integration_point", "m", ("x", "y", "z"),
                             "physical location of each quadrature point, shape (cells, points, 3)"),
    "recovery_weights": ("integration_point", "m3", ("weight",),
                         "physical quadrature weight (detJ-scaled), shape (cells, points)"),
    "recovery_strain": ("integration_point", "1", ("xx", "xy", "xz", "yx", "yy", "yz", "zx", "zy", "zz"),
                        "physical strain tensor, shape (cells, points, 3, 3)"),
    "recovery_stress": ("integration_point", "Pa", ("xx", "xy", "xz", "yx", "yy", "yz", "zx", "zy", "zz"),
                        "physical stress tensor, shape (cells, points, 3, 3)"),
    "recovery_cell_ids": ("cell", "", ("cell_id",), "external cell id of each recovery row"),
    "interface_force": ("interface", "N", ("x", "y", "z"), "resultant force on the driven cut groups"),
    "interface_moment": ("interface", "N*m", ("x", "y", "z"), "resultant moment on the driven cut groups"),
}


def _result_problems(tables: Mapping[str, np.ndarray], displacement: np.ndarray, node_ids: np.ndarray) -> list:
    """Why solid result tables are inconsistent; shared by writer and reader."""

    problems = []
    for key, array in {**tables, "displacement": displacement}.items():
        if array.dtype.kind == "f" and not np.isfinite(array).all():
            problems.append(f"{key} is not finite")
    ndof = tables["coefficients"].shape[0] if tables["coefficients"].ndim == 1 else -1
    if ndof < 1:
        problems.append("coefficients are not a nonempty vector")
    for key, shape in (("coefficient_coordinates", (ndof, 3)), ("coefficient_components", (ndof,)),
                       ("interface_force", (3,)), ("interface_moment", (3,))):
        if tables[key].shape != shape:
            problems.append(f"{key} has shape {tables[key].shape}, expected {shape}")
    if displacement.ndim != 2 or displacement.shape[1] != 3 or node_ids.shape != (len(displacement),):
        problems.append("displacement and its node ids do not match")
    weights = tables["recovery_weights"]
    cells = tables["recovery_cell_ids"]
    if weights.ndim != 2 or cells.shape != (len(weights),):
        problems.append("recovery weights and cell ids do not match")
    else:
        for key, tail in (("recovery_coordinates", (3,)), ("recovery_strain", (3, 3)), ("recovery_stress", (3, 3))):
            if tables[key].shape != weights.shape + tail:
                problems.append(f"{key} has shape {tables[key].shape}, expected {weights.shape + tail}")
    return problems


# ----------------------------------------------------------------------
# the cut interface: prescribed DOFs, reactions, group ownership, resultants
# ----------------------------------------------------------------------
_INTERFACE_TABLES = {
    # key: (location, unit, components, meaning)
    "prescribed_dofs": ("dof", "", ("dof",), "prepared DOF index of every driven cut coefficient, each once"),
    "prescribed_values": ("dof", "m", ("value",), "imposed shell displacement on each prescribed DOF"),
    "reactions": ("dof", "N", ("value",), "signed constraint reaction r = K q - f on each prescribed DOF"),
    "ownership_dofs": ("ownership", "", ("dof",), "DOF of every (DOF, cut group) ownership pair"),
    "ownership_groups": ("ownership", "", ("group",), "index into group_names of every ownership pair"),
    "group_force": ("group", "N", ("x", "y", "z"), "attributed reaction force per cut group (world frame)"),
    "group_moment": ("group", "N*m", ("x", "y", "z"), "attributed reaction moment per cut group about moment_origin"),
    "group_dof_count": ("group", "", ("count",), "DOFs listed by each cut group"),
    "group_shared_dof_count": ("group", "", ("count",), "DOFs of each group that another group also owns"),
    "skin_force": ("interface", "N", ("x", "y", "z"), "signed resultant force of the skin load (world frame)"),
    "skin_moment": ("interface", "N*m", ("x", "y", "z"), "signed resultant moment of the skin load about moment_origin"),
    "moment_origin": ("interface", "m", ("x", "y", "z"), "world point every stored moment is taken about"),
}
_INT_TABLES = ("prescribed_dofs", "ownership_dofs", "ownership_groups", "group_dof_count", "group_shared_dof_count")


def solid_interface_arrays(solved: Any, prepared: Any) -> tuple:
    """``(arrays, meta)`` of the solver's cut interface, re-derived from its actual fields and checked.

    Group membership is re-read from ``prepared.boundary_dofs(name)`` (the solver does not return the
    unshared DOFs) and must agree with the solver's own counts and ``shared_attribution``.  Raises
    ``ArtifactError`` for anything :func:`interface_problems` refuses.
    """

    try:
        names = [str(n) for n in solved.group_resultants]
        pairs = []
        for index, name in enumerate(names):
            dofs = np.unique(np.asarray(prepared.boundary_dofs(name), dtype=np.int64))
            pairs += [(int(d), index) for d in dofs]
        order = np.asarray(sorted(pairs), dtype=np.int64).reshape(-1, 2)
        arrays = {
            "prescribed_dofs": np.asarray(solved.prescribed_dofs, dtype=np.int64),
            "prescribed_values": np.asarray(solved.prescribed_values, dtype=np.float64),
            "reactions": np.asarray(solved.reactions, dtype=np.float64),
            "ownership_dofs": order[:, 0].copy(),
            "ownership_groups": order[:, 1].copy(),
            "group_force": np.asarray([solved.group_resultants[n].force for n in names], dtype=np.float64).reshape(-1, 3),
            "group_moment": np.asarray([solved.group_resultants[n].moment for n in names], dtype=np.float64).reshape(-1, 3),
            "group_dof_count": np.asarray([solved.group_resultants[n].dof_count for n in names], dtype=np.int64),
            "group_shared_dof_count": np.asarray([solved.group_resultants[n].shared_dof_count for n in names],
                                                 dtype=np.int64),
            "skin_force": np.asarray(solved.skin_force, dtype=np.float64).reshape(3),
            "skin_moment": np.asarray(solved.skin_moment, dtype=np.float64).reshape(3),
            "moment_origin": np.zeros(3),
            "interface_force": np.asarray(solved.interface_force, dtype=np.float64).reshape(3),
            "interface_moment": np.asarray(solved.interface_moment, dtype=np.float64).reshape(3),
        }
        meta = {"version": INTERFACE_VERSION, "group_names": names, "attribution_rule": str(solved.attribution_rule),
                "moment_frame": MOMENT_FRAME}
        shared = {int(d): tuple(str(g) for g in groups) for d, groups in solved.shared_attribution.items()}
        coefficients = np.asarray(solved.coefficients, dtype=np.float64)
        coordinates = np.asarray(prepared.coefficient_coordinates, dtype=np.float64)
        components = np.asarray(prepared.coefficient_components, dtype=np.int64)
    except (AttributeError, TypeError, ValueError, KeyError, IndexError) as error:
        raise ArtifactError(f"solid interface is malformed: {error}") from None
    problems = interface_problems(arrays, meta, coefficients, coordinates, components)
    owners: Dict[int, list] = {}
    for dof, index in order:
        owners.setdefault(int(dof), []).append(names[int(index)])
    if not problems and {d: tuple(g) for d, g in owners.items() if len(g) > 1} != shared:
        problems.append("the solver's shared-DOF attribution differs from the groups' DOF lists")
    if problems:
        raise ArtifactError("solid interface is inconsistent: " + "; ".join(problems))
    return arrays, meta


def interface_problems(a: Mapping[str, np.ndarray], meta: Any, q: np.ndarray, coords: np.ndarray,
                       comps: np.ndarray) -> list:
    """Why a cut-interface record is inconsistent with the coefficients and the DOF table (empty when sound).

    Checks lengths, dtypes, finiteness, ranges, uniqueness, that the imposed values are the coefficients,
    that every prescribed DOF has an owner and every owner is prescribed, the per-group counts, the equal
    attribution allocation (each shared DOF split equally among its owners, recomputed from the stored
    reactions), the group resultants, their total against the interface resultant and the interface
    resultant recomputed from the reactions, and the balance against the signed skin resultant.
    """

    if not isinstance(meta, Mapping):
        return ["interface metadata is not an object"]
    names = meta.get("group_names")
    if (not isinstance(names, (list, tuple)) or not names
            or any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names)):
        return ["interface group names are not distinct non-empty strings"]
    problems = []
    if meta.get("attribution_rule") != ATTRIBUTION_RULE:
        problems.append(f"attribution rule is {meta.get('attribution_rule')!r}, expected {ATTRIBUTION_RULE!r}")
    if meta.get("moment_frame") != MOMENT_FRAME:
        problems.append(f"moment frame is {meta.get('moment_frame')!r}, expected {MOMENT_FRAME!r}")
    groups = len(names)
    try:
        t = {key: np.asarray(a[key]) for key in (*_INTERFACE_TABLES, "interface_force", "interface_moment")}
    except KeyError as error:
        return problems + [f"interface table {error} is missing"]
    ndof = len(q)
    count = t["prescribed_dofs"].shape[0] if t["prescribed_dofs"].ndim == 1 else -1
    owned = t["ownership_dofs"].shape[0] if t["ownership_dofs"].ndim == 1 else -1
    expect = {"prescribed_dofs": (count,), "prescribed_values": (count,), "reactions": (count,),
              "ownership_dofs": (owned,), "ownership_groups": (owned,), "group_force": (groups, 3),
              "group_moment": (groups, 3), "group_dof_count": (groups,), "group_shared_dof_count": (groups,),
              "skin_force": (3,), "skin_moment": (3,), "moment_origin": (3,), "interface_force": (3,),
              "interface_moment": (3,)}
    if count < 1 or owned < 1:
        problems.append("interface has no prescribed DOFs or no ownership pairs")
    for key, shape in expect.items():
        if t[key].shape != shape:
            problems.append(f"{key} has shape {t[key].shape}, expected {shape}")
        elif key in _INT_TABLES and t[key].dtype.kind not in "iu":
            problems.append(f"{key} is not an integer table")
        elif key not in _INT_TABLES and (t[key].dtype.kind != "f" or not np.isfinite(t[key]).all()):
            problems.append(f"{key} is not a finite float table")
    if q.ndim != 1 or coords.shape != (ndof, 3) or comps.shape != (ndof,):
        problems.append("coefficient tables do not match each other")
    if problems:
        return problems
    dofs, owned_dofs, owned_groups = (t[k].astype(np.int64) for k in ("prescribed_dofs", "ownership_dofs",
                                                                      "ownership_groups"))
    if dofs.min() < 0 or dofs.max() >= ndof or len(np.unique(dofs)) != count:
        return ["prescribed DOFs are out of range or repeated"]
    if owned_dofs.min() < 0 or owned_dofs.max() >= ndof or owned_groups.min() < 0 or owned_groups.max() >= groups:
        return ["ownership pairs reference DOFs or groups out of range"]
    if len(np.unique(np.column_stack([owned_dofs, owned_groups]), axis=0)) != owned:
        return ["a DOF is listed twice for the same group"]
    if not np.array_equal(t["prescribed_values"], q[dofs]):
        problems.append("imposed values differ from the stored coefficients on the prescribed DOFs")
    if not np.array_equal(np.unique(owned_dofs), np.sort(dofs)):
        return problems + ["the owned DOFs are not exactly the prescribed DOFs"]
    if not np.all(t["moment_origin"] == 0.0):
        problems.append("moments must be about the world origin")
    # allocation: every DOF's reaction is split equally among the groups that list it
    where = np.searchsorted(np.sort(dofs), owned_dofs)
    reactions = t["reactions"][np.argsort(dofs)]
    multiplicity = np.bincount(where, minlength=count)
    weighted = reactions[where] / multiplicity[where]
    if np.any(t["group_dof_count"] != np.bincount(owned_groups, minlength=groups)):
        problems.append("group DOF counts differ from the ownership pairs")
    if np.any(t["group_shared_dof_count"] != np.bincount(owned_groups[multiplicity[where] > 1], minlength=groups)):
        problems.append("group shared-DOF counts differ from the ownership pairs")
    for g in range(groups):
        points = coords[owned_dofs[owned_groups == g]]
        if len(points) and np.any(np.unique(points, axis=0, return_counts=True)[1] != 3):
            problems.append(f"group {names[g]!r} does not carry all three components at every location")
    vectors = np.zeros((owned, 3))
    vectors[np.arange(owned), comps[owned_dofs]] = weighted
    moments = np.cross(coords[owned_dofs] - t["moment_origin"], vectors)
    magnitude = np.abs(vectors).sum(axis=1)
    arm = np.linalg.norm(coords[owned_dofs] - t["moment_origin"], axis=1)

    def close(found: np.ndarray, wanted: np.ndarray, scale: float) -> bool:
        return bool(np.linalg.norm(found - wanted) <= (_REL + 64.0 * _EPS) * scale + 1.0e-300)

    for g in range(groups):
        mask = owned_groups == g
        if not close(t["group_force"][g], vectors[mask].sum(axis=0), float(magnitude[mask].sum())):
            problems.append(f"group {names[g]!r} force is not its attributed reactions")
        if not close(t["group_moment"][g], moments[mask].sum(axis=0), float((arm[mask] * magnitude[mask]).sum())):
            problems.append(f"group {names[g]!r} moment is not its attributed reactions")
    # total: the groups sum to the interface, which is the reactions' own resultant
    unit = np.zeros((count, 3))
    unit[np.arange(count), comps[np.sort(dofs)]] = reactions
    force = unit.sum(axis=0)
    moment = np.cross(coords[np.sort(dofs)] - t["moment_origin"], unit).sum(axis=0)
    total = float(np.abs(unit).sum())
    lever = float((np.linalg.norm(coords[np.sort(dofs)] - t["moment_origin"], axis=1) * np.abs(unit).sum(axis=1)).sum())
    if not close(t["interface_force"], force, total) or not close(t["interface_moment"], moment, lever):
        problems.append("the interface resultant is not the resultant of the stored reactions")
    if not close(t["group_force"].sum(axis=0), t["interface_force"], total) or not close(
            t["group_moment"].sum(axis=0), t["interface_moment"], lever):
        problems.append("the group resultants do not add up to the interface resultant")
    skin = float(np.abs(t["skin_force"]).sum())
    if (np.linalg.norm(t["interface_force"] + t["skin_force"]) > 1.0e-6 * (total + skin) + 1.0e-300
            or np.linalg.norm(t["interface_moment"] + t["skin_moment"]) > 1.0e-6 * (lever + skin * float(
                np.max(arm, initial=0.0))) + 1.0e-300):
        problems.append("the interface reactions do not balance the signed skin resultant")
    return problems


def _interface_pairs(result: Mapping[str, Any]) -> tuple:
    """``(dofs sorted, reactions, coefficients on them)`` of a ``read_solid_result`` dict, each DOF once."""

    interface = result.get("interface")
    if interface is None:
        raise ArtifactError("this solid result predates the cut-interface record")
    order = np.argsort(interface["prescribed_dofs"])
    dofs = np.asarray(interface["prescribed_dofs"])[order]
    return dofs, np.asarray(interface["reactions"])[order], np.asarray(result["coefficients"])[dofs]


def interface_virtual_work(result: Mapping[str, Any]) -> float:
    """Unique virtual work ``sum r_i q_i`` over the prescribed DOFs (a shared DOF counts once)."""

    _dofs, reactions, values = _interface_pairs(result)
    return float(np.dot(reactions, values))


def group_virtual_work(result: Mapping[str, Any]) -> Dict[str, float]:
    """Virtual work attributed to each cut group by the equal split; the groups add up to the unique total."""

    interface = result["interface"]
    dofs, reactions, values = _interface_pairs(result)
    owned, groups = np.asarray(interface["ownership_dofs"]), np.asarray(interface["ownership_groups"])
    where = np.searchsorted(dofs, owned)
    share = reactions[where] * values[where] / np.bincount(where, minlength=len(dofs))[where]
    return {name: float(share[groups == g].sum()) for g, name in enumerate(interface["group_names"])}


def write_solid_result(store: ArtifactStore, solved: Any, prepared: Any, mesh: Any, *, run_id: str,
                       document_id: str, mesh_id: str, model_hash: str, mesh_hash: str, analysis_hash: str,
                       provenance: Optional[Mapping[str, Any]] = None, summary: Optional[Mapping[str, Any]] = None,
                       diagnostics: Any = (), require_interface: bool = True) -> ArtifactRef:
    """Persist a solved solid with the store's own ``write_result`` (identity, atomic commit, integrity).

    The cut interface (prescribed DOFs, imposed values, signed reactions, group ownership, per-group and
    total resultants, the signed skin resultant, the moment origin and frame) is validated and stored.
    ``require_interface=False`` writes the previous variant, without those tables, for a solver result
    that does not carry them; the default refuses such a result.
    """

    legacy = False
    if not require_interface:
        legacy = not any(hasattr(solved, n) for n in ("prescribed_dofs", "reactions", "group_resultants"))
    try:
        displacement = np.asarray(solved.nodal_displacements, dtype=np.float64)
        recovery = solved.recovery
        arrays = {
            "coefficients": np.asarray(solved.coefficients, dtype=np.float64),
            "coefficient_coordinates": np.asarray(prepared.coefficient_coordinates, dtype=np.float64),
            "coefficient_components": np.asarray(prepared.coefficient_components, dtype=np.int64),
            "recovery_coordinates": np.asarray(recovery.coordinates, dtype=np.float64),
            "recovery_weights": np.asarray(recovery.weights, dtype=np.float64),
            "recovery_strain": np.asarray(recovery.strain, dtype=np.float64),
            "recovery_stress": np.asarray(recovery.stress, dtype=np.float64),
            "recovery_cell_ids": np.asarray(recovery.cell_ids, dtype=np.int64),
            "interface_force": np.asarray(solved.interface_force, dtype=np.float64).reshape(3),
            "interface_moment": np.asarray(solved.interface_moment, dtype=np.float64).reshape(3),
        }
        node_ids = np.asarray(mesh.node_ids, dtype=np.int64)
        ndof = int(prepared.ndof)
    except (AttributeError, TypeError, ValueError) as error:
        raise ArtifactError(f"solid result is malformed: {error}") from None
    problems = _result_problems(arrays, displacement, node_ids)
    if arrays["coefficients"].shape != (ndof,):
        problems.append("coefficients do not match the prepared DOF count")
    if displacement.shape != (len(mesh.node_ids), 3):
        problems.append("nodal displacements do not match the mesh nodes")
    if problems:
        raise ArtifactError("solid result is inconsistent: " + "; ".join(problems))
    declared = {key: {"location": spec[0], "unit": spec[1], "components": list(spec[2]), "meaning": spec[3],
                      "shape": list(arrays[key].shape), "dtype": arrays[key].dtype.str}
                for key, spec in _TABLES.items()}
    declared["recovery_sampling"] = {"sampling": str(recovery.sampling),
                                     "tensor_convention": str(recovery.tensor_convention)}
    tables: Dict[str, Any] = {"displacement_node_ids": np.asarray(mesh.node_ids, dtype=np.int64), **arrays,
                              "solid_fields": declared}
    if not legacy:
        interface, meta = solid_interface_arrays(solved, prepared)
        if not (np.array_equal(interface["interface_force"], arrays["interface_force"])
                and np.array_equal(interface["interface_moment"], arrays["interface_moment"])):
            raise ArtifactError("solid interface resultant differs from the result's")
        for key, spec in _INTERFACE_TABLES.items():
            declared[key] = {"location": spec[0], "unit": spec[1], "components": list(spec[2]), "meaning": spec[3],
                             "shape": list(interface[key].shape), "dtype": interface[key].dtype.str}
            tables[key] = interface[key]
        tables["solid_interface"] = meta
    descriptor = ResultQuantityDescriptor(
        key="displacement", label="Displacement", location="node", unit="m", components=("ux", "uy", "uz"),
        basis="global", frames=(0.0,), recovery="native", reduction="none", deformation_required=False,
        provenance={"element_type": "tet4", "order": int(prepared.order), "child_kind": "solid"})
    return store.write_result(
        job_id=run_id, document_id=document_id, mesh_id=mesh_id, model_hash=model_hash, mesh_hash=mesh_hash,
        analysis_hash=analysis_hash, fields={"displacement": (descriptor, displacement[None, :, :])},
        frames=(0.0,), frame_kind="static", tables=tables, provenance=dict(provenance or {}),
        summary=dict(summary or {}), diagnostics=list(diagnostics))


def _close(dataset: Any) -> None:
    """Release a result dataset that owns resources (a callable ``close``); nothing else is touched."""

    close = getattr(dataset, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # releasing a read-only handle must not mask the read's own outcome
            pass


def read_solid_result(store: ArtifactStore, artifact: Any) -> Dict[str, Any]:
    """Verified read of a solid result: identity, displacement, every typed table and the declaration.

    The opened result dataset is closed when the read ends; a damaged result raises ``ArtifactError`` and the
    read changes nothing (it never touches the project or the published artifacts).
    """

    path = store._path_for_read(artifact, "result")
    dataset = store.open_result(artifact)
    h5py = _h5py()
    try:
        with h5py.File(path, "r") as handle:
            out: Dict[str, Any] = {"identity": dataset.identity,
                                   "displacement": np.asarray(handle["fields/displacement/values"])[0]}
            for key in ("displacement_node_ids", *_TABLES):
                out[key] = np.asarray(handle[f"tables/{key}"])
            out["solid_fields"] = json.loads(_text_attribute(handle["tables/solid_fields"][()]))
            out["interface"] = None  # the previous variant has no interface record
            if "solid_interface" in handle["tables"]:
                meta = json.loads(_text_attribute(handle["tables/solid_interface"][()]))
                out["interface"] = {key: np.asarray(handle[f"tables/{key}"]) for key in _INTERFACE_TABLES}
                out["interface"].update(group_names=meta.get("group_names") if isinstance(meta, Mapping) else None,
                                        meta=meta)
    except ArtifactError:
        raise
    except (OSError, KeyError, IndexError, TypeError, ValueError, AttributeError, UnicodeError) as error:
        raise ArtifactError(f"cannot read solid result: {error}") from None
    finally:
        _close(dataset)
    declared = out["solid_fields"]
    if not isinstance(declared, Mapping) or any(
            not isinstance(declared.get(key), Mapping) or list(declared[key].get("shape", ())) != list(out[key].shape)
            for key in _TABLES):
        raise ArtifactError("solid result declares tables that differ from the stored ones")
    problems = _result_problems({key: out[key] for key in _TABLES}, np.asarray(out["displacement"]),
                                np.asarray(out["displacement_node_ids"]))
    interface = out["interface"]
    if interface is not None:
        if any(not isinstance(declared.get(key), Mapping)
               or list(declared[key].get("shape", ())) != list(interface[key].shape) for key in _INTERFACE_TABLES):
            raise ArtifactError("solid result declares interface tables that differ from the stored ones")
        problems += interface_problems(
            {**interface, "interface_force": out["interface_force"], "interface_moment": out["interface_moment"]},
            interface["meta"], np.asarray(out["coefficients"]), np.asarray(out["coefficient_coordinates"]),
            np.asarray(out["coefficient_components"]))
    if problems:
        raise ArtifactError("solid result is inconsistent: " + "; ".join(problems))
    return out
