"""Update local edge divisions on connected, explicitly mapped plate faces.

The baseline binds a published mesh to its source geometry and effective settings.
ANYgeometry partitions the plates and ANYmesher resolves edge divisions. An edge
shared with a retained face keeps its permanent corner and midside node numbers.
The station order comes from the geometry edge. A division change propagated to
a neighbouring face expands the update to the entire connected component.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import numpy as np

from . import regional_mesh as rm

__all__ = [
    "ConnectedPlan",
    "ConnectedUpdateError",
    "ConnectedUpdateRecord",
    "ConnectedUpdateUnsupported",
    "capture_connected_baseline",
    "explain_connected_ineligibility",
    "generate_connected_update",
    "plan_connected_update",
]

SCHEMA = "anyfem.connected-baseline"
VERSION = 2


class ConnectedUpdateError(RuntimeError):
    """A connected update failed (inconsistent inputs or an owner gate refused)."""


class ConnectedUpdateUnsupported(ConnectedUpdateError):
    """The requested update is outside the proved connected scope."""


class _Refuse(Exception):
    """Internal: no proof available (message becomes the plan description)."""


# ------------------------------------------------------------------ topology


def _face_topology(geometry: Any) -> tuple[dict, dict, dict, dict]:
    """face -> loop edges, face -> endpoint vertices, edge -> faces, vertex -> faces."""

    face_edges: dict[int, list[int]] = {}
    face_vertices: dict[int, list[int]] = {}
    edge_faces: dict[int, set[int]] = {}
    vertex_faces: dict[int, set[int]] = {}
    for face_id in sorted(int(f) for f in geometry.faces):
        edges = [int(use.edge) for use in geometry.faces[face_id].loop]
        vertices = sorted({int(v) for e in edges
                           for v in (geometry.edges[e].start, geometry.edges[e].end)})
        face_edges[face_id] = sorted(set(edges))
        face_vertices[face_id] = vertices
        for e in edges:
            edge_faces.setdefault(e, set()).add(face_id)
        for v in vertices:
            vertex_faces.setdefault(v, set()).add(face_id)
    return face_edges, face_vertices, edge_faces, vertex_faces


def _mid_map(mesh: Any) -> dict[frozenset, int]:
    """corner pair -> mid-side node (quadratic); conflicting mids are refused."""

    out: dict[frozenset, int] = {}
    if mesh.order != "quadratic":
        return out
    for table, count in ((mesh.quads, 4), (mesh.tris, 3)):
        for nodes in table.values():
            for i in range(count):
                key = frozenset((int(nodes[i]), int(nodes[(i + 1) % count])))
                mid = int(nodes[count + i])
                if out.setdefault(key, mid) != mid:
                    raise _Refuse("two elements disagree on a mid-side node of one edge")
    return out


def _edge_stations(mesh: Any, geometry: Any, edge_id: int, mids: Mapping) -> list[int]:
    """Ordered station ids along an edge's own direction: corner [, mid, corner]..."""

    edge = geometry.edges[edge_id]
    start = mesh.node_of_vertex.get(int(edge.start))
    end = mesh.node_of_vertex.get(int(edge.end))
    seq = [int(n) for n in mesh.nodes_of_edge.get(edge_id, ())]
    if start is None or end is None:
        raise _Refuse(f"edge {edge_id} end vertices have no mesh node")
    if len(seq) < 2 or seq[0] != start or seq[-1] != end or len(set(seq)) != len(seq):
        raise _Refuse(f"edge {edge_id} has invalid ordered endpoint/station associations")
    if mesh.order == "quadratic":
        if len(seq) % 2 != 1 or any(
            mids.get(frozenset((seq[i], seq[i + 2]))) != seq[i + 1]
            for i in range(0, len(seq) - 1, 2)
        ):
            raise _Refuse(f"edge {edge_id} has inconsistent quadratic mid-side stations")
    return seq


def _xyz(mesh: Any, ids: list[int]) -> list[list[str]]:
    return [rm._hex(mesh.nodes[n]) for n in ids]


def _conformity(mesh: Any, stations: Mapping[int, list[int]], edge_faces: Mapping[int, set]) -> None:
    """Each station segment must be an element side of exactly its adjacent faces."""

    element_face = {eid: face for face, ids in mesh.elements_of_face.items() for eid in ids}
    sides: dict[frozenset, list[int]] = {}
    for table, count in ((mesh.quads, 4), (mesh.tris, 3)):
        for eid, nodes in table.items():
            for i in range(count):
                key = frozenset((int(nodes[i]), int(nodes[(i + 1) % count])))
                sides.setdefault(key, []).append(element_face[eid])
    step = 2 if mesh.order == "quadratic" else 1
    for edge, ids in stations.items():
        corners = ids[::step]
        for a, b in zip(corners, corners[1:]):
            if sorted(sides.get(frozenset((a, b)), ())) != sorted(edge_faces[edge]):
                raise _Refuse(
                    f"edge {edge} station segment {a}-{b} is not conforming across its "
                    f"{len(edge_faces[edge])} face(s)")


def _reverse_associations(mesh: Any, geometry: Any) -> None:
    owner: dict[int, int] = {}
    for face, elements in mesh.elements_of_face.items():
        for e in elements:
            if int(e) in owner:
                raise _Refuse(f"element {e} belongs to two faces")
            owner[int(e)] = int(face)
    shells = {int(e) for e in (*mesh.quads, *mesh.tris)}
    if set(owner) != shells:
        raise _Refuse("elements_of_face does not cover exactly the shell elements")
    if {int(f) for f in mesh.elements_of_face} != {int(f) for f in geometry.faces}:
        raise _Refuse("mesh faces differ from geometry faces")
    for e in shells:
        for n in (mesh.quads.get(e) or mesh.tris.get(e)):
            if int(n) not in mesh.nodes:
                raise _Refuse(f"element {e} references a missing node")


def _explain_shared_nodes(mesh: Any, face_nodes: Mapping[int, set], face_edges: Mapping,
                          face_vertices: Mapping, stations: Mapping, vertex_node: Mapping) -> None:
    """Every node shared by two faces must come from a shared edge station or shared vertex."""

    users: dict[int, list[int]] = {}
    for face, nodes in face_nodes.items():
        for n in nodes:
            users.setdefault(n, []).append(face)
    for n, faces in users.items():
        for i, f in enumerate(faces):
            for g in faces[i + 1:]:
                allowed = {s for e in set(face_edges[f]) & set(face_edges[g]) for s in stations[e]}
                allowed |= {vertex_node[v] for v in set(face_vertices[f]) & set(face_vertices[g])}
                if n not in allowed:
                    raise _Refuse(f"node {n} joins faces {f} and {g} without a shared edge/vertex "
                                  "(T-junction or tolerance coincidence)")


# ------------------------------------------------------------------- capture


def _pins() -> dict:
    pins = dict(rm._pins())
    pins["baseline_schema"] = f"{SCHEMA}/{VERSION}"
    return pins


def _eligible_inputs(project: Any, settings: Any) -> None:
    from anygeometry.curves import Straight
    from anygeometry.surfaces import CoonsSurface, Plane
    from .mesh_controls import MeshControls

    geometry = project.geometry
    rm._geometry_gate(geometry)
    if (project.read_only_reason or project.refinements or project.edge_sections
            or settings.automation is not None
            or project.sheet_join_intent_problems() or project._feature_materialization_problems()):
        raise _Refuse("connected update requires resolved plain plates without refinements or automation")
    controls = settings.controls or MeshControls.from_settings(project.native_mesh_settings)
    if controls.certification_mode != "interactive":
        raise _Refuse("connected update has no strict-certification preparation route")
    active_edges = {use.edge for face in geometry.faces.values() for use in face.loop}
    if any(edge not in active_edges for edge, _ in settings.overrides):
        raise _Refuse("edge division override names an edge outside the supported plates")
    for face in geometry.faces.values():
        surface = face.surface
        if (len(face.loop) != 4 or face.parameterization is not None
                or not (type(surface) is Plane or
                        type(surface) is CoonsSurface and surface.bottom is None)
                or any(type(geometry.edges[use.edge].curve) is not Straight for use in face.loop)):
            raise _Refuse("only four straight edges on a plane or topology-backed Coons plate are supported")


def _junction_segments(stations: Mapping, edge_ids: Any, order: str) -> set[tuple[int, int]]:
    segments = set()
    for edge in edge_ids:
        corners = stations[edge][::2 if order == "quadratic" else 1]
        segments.update(tuple(sorted((a, b))) for a, b in zip(corners, corners[1:]))
    return segments


def _published_mesh_digest(mesh: Any) -> str:
    from anymesher.serialize import mesh_to_dict

    return rm._digest(mesh_to_dict(mesh))


def _capture(project: Any, settings: Any, mesh: Any) -> dict:
    strategy = rm._strategy(project, settings)
    if strategy != "mapped":
        raise _Refuse(f"strategy {strategy!r}: only mapped is supported")
    if not all(hasattr(mesh, n) for n in ("elements_of_face", "quads", "tris", "nodes")):
        raise _Refuse("mesh carries no face provenance")
    if mesh.beams or mesh.couplings:
        raise _Refuse("mesh has beams or couplings")
    if mesh.automatic_beam_connections or mesh.automatic_shell_connections or mesh.tris:
        raise _Refuse("mesh has structural connections")
    if mesh.order != settings.element_order:
        raise _Refuse("mesh order differs from the settings")
    geometry = project.geometry
    _eligible_inputs(project, settings)
    if str(mesh.geometry_model_id) != str(geometry.model_id) or mesh.geometry_revision != geometry.revision:
        raise _Refuse("published mesh and project geometry identities differ")
    document = rm._document(geometry)
    _reverse_associations(mesh, geometry)
    separation = float(settings.target_size)
    groups = rm._partition(geometry, separation, rm._check_fn(None))
    if not any(len(g) > 1 for g in groups):
        raise _Refuse("no multi-face component; the disconnected producer owns this model")
    face_edges, face_vertices, edge_faces, vertex_faces = _face_topology(geometry)
    for e, faces in edge_faces.items():
        if len(faces) > 2:
            raise _Refuse(f"edge {e} is used by {len(faces)} faces (T-junction / non-manifold)")
    group_of = {f: i for i, g in enumerate(groups) for f in g}
    for e, faces in edge_faces.items():
        if len({group_of[f] for f in faces}) > 1:
            raise _Refuse(f"edge {e} joins faces of different components")
    mids = _mid_map(mesh)
    stations = {e: _edge_stations(mesh, geometry, e, mids) for e in sorted(edge_faces)}
    shared = {e for e, faces in edge_faces.items() if len(faces) == 2}
    if not shared:
        raise _Refuse("no shared geometry edge")
    declared = {tuple(sorted(map(int, pair))) for pair in mesh.declared_plate_junction_edges}
    declared_edges = [e for e in sorted(shared)
                      if _junction_segments(stations, [e], mesh.order) <= declared]
    if _junction_segments(stations, declared_edges, mesh.order) != declared:
        raise _Refuse("declared junction is not a complete two-face shared edge")
    if mesh.automatic_intersections > len(declared_edges):
        raise _Refuse("automatic intersections are not accounted for by ordinary shared edges")
    vertex_node = {}
    for v in vertex_faces:
        if v not in mesh.node_of_vertex:
            raise _Refuse(f"vertex {v} has no mesh node")
        vertex_node[v] = int(mesh.node_of_vertex[v])
    face_nodes: dict[int, set] = {}
    faces_out: dict[str, Any] = {}
    for f in sorted(face_edges):
        elements, nodes = rm._component_mesh(mesh, [f])
        face_nodes[f] = nodes
        record = rm._component_record(geometry, document, [f], settings)
        faces_out[str(f)] = {
            "fingerprint": record["fingerprint"], "local_hash": record["local"]["hash"],
            "faces": [f], "edges": face_edges[f], "vertices": face_vertices[f],
            "sheets": record["sheets"], "mesh": rm._binding(mesh, record, elements, nodes),
        }
    if set().union(*face_nodes.values()) != {int(n) for n in mesh.nodes}:
        raise _Refuse("mesh has nodes outside the faces")
    _explain_shared_nodes(mesh, face_nodes, face_edges, face_vertices, stations, vertex_node)
    _conformity(mesh, stations, edge_faces)
    edges_out = {
        str(e): {"faces": sorted(edge_faces[e]), "divisions": (len(ids) - 1) // (2 if mesh.order == "quadratic" else 1),
                 "stations": _xyz(mesh, ids)}
        for e, ids in stations.items()
    }
    rm._closure_gate(mesh, list(faces_out.values()))

    baseline = {
        "schema": SCHEMA, "version": VERSION, "pins": _pins(),
        "global": rm._global_state(project, settings, strategy, document),
        "separation": separation, "order": str(mesh.order),
        "geometry_order": [int(f) for f in geometry.faces],
        "original_geometry": rm._plain(document["raw"]),
        "published_mesh": _published_mesh_digest(mesh),
        "declared_edges": declared_edges,
        "groups": [list(g) for g in groups],
        "faces": faces_out, "edges": edges_out,
        "vertices": {str(v): rm._hex(mesh.nodes[n]) for v, n in sorted(vertex_node.items())},
    }
    baseline["receipt"] = rm._digest(baseline)
    return baseline


def explain_connected_ineligibility(project: Any, settings: Any, mesh: Any) -> str | None:
    try:
        _capture(project, settings, mesh)
    except (_Refuse, *rm._refusals()) as error:
        return f"{type(error).__name__}: {error}"
    return None


def capture_connected_baseline(project: Any, settings: Any, mesh: Any) -> dict | None:
    """Capture a serializable baseline of the published, permanently numbered mesh."""

    try:
        return _capture(project, settings, mesh)
    except (_Refuse, *rm._refusals()):
        return None


# ---------------------------------------------------------------------- plan


@dataclass(frozen=True, eq=False)
class ConnectedPlan:
    applicable: bool
    description: str
    affected_faces: tuple = ()
    retained_faces: tuple = ()
    changed_groups: tuple = ()
    grown_groups: tuple = ()
    interface_edges: tuple = ()
    interface_vertices: tuple = ()
    reasons: Mapping = field(default_factory=dict)
    records: Mapping = field(default_factory=dict)
    seeding: Any = None
    noop: bool = False

    @property
    def is_noop(self) -> bool:
        return self.noop


def _solve_group(project: Any, settings: Any, edges: set) -> Any:
    from anymesher.refinement import SizeField
    from anymesher.seeding import solve_seeding

    geometry = project.geometry
    overrides = {int(e): int(n) for e, n in settings.overrides if int(e) in edges}
    return solve_seeding(geometry, size_field=SizeField(geometry, float(settings.target_size), ()),
                         overrides=overrides, edge_ids=sorted(edges))


def _analyze(project: Any, settings: Any, baseline: Mapping, check: Callable) -> ConnectedPlan:
    if not isinstance(baseline, Mapping) or baseline.get("schema") != SCHEMA:
        raise _Refuse("not a connected baseline")
    if baseline.get("version") != VERSION:
        raise _Refuse("unsupported connected baseline version")
    if baseline.get("receipt") != rm._digest({k: v for k, v in baseline.items() if k != "receipt"}):
        raise _Refuse("connected baseline failed its content check")
    if dict(baseline.get("pins", {})) != _pins():
        raise _Refuse("build pins changed; full rebuild required")
    strategy = rm._strategy(project, settings)
    if strategy != "mapped":
        raise _Refuse(f"strategy {strategy!r} is not supported")
    geometry = project.geometry
    _eligible_inputs(project, settings)
    document = rm._document(geometry)
    if document["raw"]["checksum"] != baseline["original_geometry"]["checksum"]:
        raise _Refuse("geometry changed; shared-edge and intersection provenance requires a full rebuild")
    now, before = rm._global_state(project, settings, strategy, document), dict(baseline["global"])
    differing = sorted(k for k in set(now) | set(before) if now.get(k) != before.get(k))
    if differing:
        raise _Refuse("global dependencies changed (" + ", ".join(differing) + "); not a regional edit")
    if str(settings.element_order) != baseline["order"]:
        raise _Refuse("element order changed")
    check("connected component partition")
    groups = rm._partition(geometry, float(baseline["separation"]), check)
    if groups != sorted(sorted(int(f) for f in g) for g in baseline["groups"]):
        raise _Refuse("component structure changed (plate added/removed or components merged/split); "
                      "no owner proof for added/removed intersections")
    face_edges, face_vertices, edge_faces, vertex_faces = _face_topology(geometry)
    if {str(e): sorted(f) for e, f in edge_faces.items()} != \
            {e: list(v["faces"]) for e, v in baseline["edges"].items()}:
        raise _Refuse("shared-edge structure changed (T-junction or added/removed intersection)")
    records = {f: rm._component_record(geometry, document, [f], settings) for f in face_edges}
    base_faces = baseline["faces"]
    changed0: dict[int, str] = {}
    for f, rec in records.items():
        old = base_faces[str(f)]
        if rec["fingerprint"] != old["fingerprint"]:
            raise _Refuse(f"face {f} differs from its bound geometry")
        if rec["local"]["hash"] != old["local_hash"]:
            changed0[f] = "local edge divisions changed"
    if not changed0:
        return ConnectedPlan(False, f"{len(groups)} component(s); nothing changed", noop=True,
                             retained_faces=tuple(sorted(face_edges)))
    affected: set[int] = set()
    grown: list[tuple] = []
    reasons = dict(changed0)
    seedings = []
    changed_groups = []
    for group in groups:
        hits = [f for f in group if f in changed0]
        if not hits:
            continue
        check("connected seeding")
        changed_groups.append(tuple(group))
        edges = {e for f in group for e in face_edges[f]}
        seeding = _solve_group(project, settings, edges)
        divisions = {int(k): int(v) for k, v in seeding.divisions.items()}
        stale = [f for f in group if f not in changed0 and any(
            divisions.get(e) != baseline["edges"][str(e)]["divisions"] for e in face_edges[f])]
        if stale:
            grown.append(tuple(group))
            for f in group:
                reasons.setdefault(f, f"component expanded: seed of unchanged face(s) {stale} changes")
            affected |= set(group)
        else:
            affected |= set(hits)
        seedings.append(seeding)
    retained = sorted(set(face_edges) - affected)
    interface_edges = sorted(e for e, fs in edge_faces.items() if fs & affected and fs - affected)
    interface_vertices = sorted(v for v, fs in vertex_faces.items() if fs & affected and fs - affected)
    active = sorted({e for f in affected for e in face_edges[f]})
    merged: dict[int, int] = {}
    classes: dict[int, int] = {}
    for s in seedings:
        merged.update({int(k): int(v) for k, v in s.divisions.items()})
        classes.update({int(k): int(v) for k, v in (s.classes or {}).items()})
    # Interface seeds are frozen: proven equal to the old counts above.
    for e in interface_edges:
        if merged.get(e) != baseline["edges"][str(e)]["divisions"]:
            raise _Refuse(f"interface edge {e} seed cannot be frozen")
    from anymesher.seeding import Seeding

    sub = Seeding({e: merged[e] for e in active}, max(int(s.sweeps) for s in seedings),
                  {e: classes[e] for e in active if e in classes}, seedings[0].size_field)
    parts = [f"Regenerate faces {sorted(affected)}"]
    parts += [f"Face {f} ({reasons[f]})" for f in sorted(affected)]
    if grown:
        parts.append(f"One whole-component expansion covers {[list(g) for g in grown]}")
    parts.append(f"Keep frozen interface edges {interface_edges} and vertices {interface_vertices}")
    parts.append(f"Retain faces {retained}")
    return ConnectedPlan(True, ". ".join(parts), tuple(sorted(affected)), tuple(retained),
                         tuple(changed_groups), tuple(grown), tuple(interface_edges),
                         tuple(interface_vertices), reasons, records, sub)


def plan_connected_update(project: Any, settings: Any, baseline: Mapping,
                          *, cancellation_check: Callable | None = None) -> ConnectedPlan:
    """Visible plan; ``applicable`` is true only for a proved connected change."""

    from anymesher.seeding import SeedingConflict

    try:
        return _analyze(project, settings, baseline, rm._check_fn(cancellation_check))
    except (_Refuse, SeedingConflict, *rm._refusals()) as error:
        return ConnectedPlan(False, f"connected update not applicable: {error}")


# ------------------------------------------------------------------ generate


@dataclass(frozen=True, eq=False)
class ConnectedUpdateRecord:
    mesh: Any
    baseline: dict | None
    retained_nodes: frozenset
    retained_elements: frozenset
    plan: ConnectedPlan


def _guard_old(old: Any, geometry: Any, baseline: Mapping) -> dict:
    if (str(old.geometry_model_id) != str(geometry.model_id)
            or old.geometry_revision != geometry.revision):
        raise ConnectedUpdateError("old mesh geometry identity differs from the baseline")
    if baseline.get("published_mesh") != _published_mesh_digest(old):
        raise ConnectedUpdateError("old mesh content differs from the published baseline")
    mids = _mid_map(old) if old.order == "quadratic" else {}
    if old.order != baseline["order"]:
        raise ConnectedUpdateError("old mesh order differs from the baseline")
    try:
        _reverse_associations(old, geometry)
        for f, item in baseline["faces"].items():
            elements, nodes = rm._component_mesh(old, [int(f)])
            if rm._binding(old, item, elements, nodes) != item["mesh"]:
                raise _Refuse(f"old mesh does not match the baseline for face {f}")
        stations = {int(e): _edge_stations(old, geometry, int(e), mids) for e in baseline["edges"]}
        for e, ids in stations.items():
            if _xyz(old, ids) != baseline["edges"][str(e)]["stations"]:
                raise _Refuse(f"old mesh stations of edge {e} differ from the baseline")
    except (_Refuse, rm._Ineligible, KeyError) as error:
        raise ConnectedUpdateError(f"old mesh does not match the baseline: {error}") from error
    return stations


def generate_connected_update(project: Any, settings: Any, baseline: Mapping, old_mesh: Any,
                              *, node_high_water: int, element_high_water: int,
                              cancellation_check: Callable | None = None) -> ConnectedUpdateRecord:
    from anymesher.errors import MeshError
    from anymesher.regional_mapped import remesh_mapped_region
    from anymesher.regional_update import RegionalQualityLimits

    check = rm._check_fn(cancellation_check)
    plan = plan_connected_update(project, settings, baseline, cancellation_check=cancellation_check)
    if not plan.applicable:
        raise ConnectedUpdateUnsupported(plan.description)
    geometry = project.geometry
    old = rm._decode(old_mesh)
    old_stations = _guard_old(old, geometry, baseline)
    _, _, edge_faces, _ = _face_topology(geometry)
    affected = set(plan.affected_faces)
    if affected & set(old.thickness_of_face):
        raise ConnectedUpdateUnsupported("affected plate has mesh-owned thickness")
    try:
        assembled = remesh_mapped_region(
            old, geometry, affected_faces=plan.affected_faces,
            interface_stations={e: old_stations[e] for e in plan.interface_edges},
            interface_vertices={v: old.node_of_vertex[v] for v in plan.interface_vertices},
            seeding=plan.seeding, target_size=float(settings.target_size),
            node_high_water=node_high_water, element_high_water=element_high_water,
            quality=RegionalQualityLimits(), cancellation_check=check,
        )
    except MeshError as error:
        raise ConnectedUpdateError(f"ANYmesher refused the connected splice: {error}") from error
    final = assembled.mesh

    # -- verification of the full assembly
    if (str(final.geometry_model_id) != str(geometry.model_id)
            or final.geometry_revision != geometry.revision):
        raise ConnectedUpdateError("assembled mesh does not carry the source geometry model/revision")
    retained_nodes = frozenset(int(n) for n in old.nodes) & frozenset(int(n) for n in final.nodes)
    for n in retained_nodes:
        if not np.array_equal(np.asarray(old.nodes[n]), np.asarray(final.nodes[n])):
            raise ConnectedUpdateError(f"retained node {n} moved")
    old_elements = {int(e) for e in (*old.quads, *old.tris)}
    retained_elements = frozenset(old_elements & {int(e) for e in (*final.quads, *final.tris)})
    for e in retained_elements:
        if tuple((old.quads.get(e) or old.tris.get(e))) != tuple((final.quads.get(e) or final.tris.get(e))):
            raise ConnectedUpdateError(f"retained element {e} changed connectivity")
    if retained_nodes != set(assembled.retained_node_ids) or retained_elements != set(assembled.retained_element_ids):
        raise ConnectedUpdateError("owner retention report differs from the numbered assembly")
    expected = {e for f in plan.retained_faces for e in old.elements_of_face[f]}
    if expected != set(retained_elements):
        raise ConnectedUpdateError("retained elements differ from the planned retained faces")
    try:
        _reverse_associations(final, geometry)
        fmids = _mid_map(final) if final.order == "quadratic" else {}
        stations = {e: _edge_stations(final, geometry, e, fmids) for e in sorted(edge_faces)}
        _conformity(final, stations, edge_faces)
        expected_junctions = _junction_segments(stations, baseline["declared_edges"], final.order)
        if {tuple(sorted(p)) for p in final.declared_plate_junction_edges} != expected_junctions:
            raise _Refuse("the assembled junction segments differ from the source shared edges")
        for e in sorted(edge_faces):
            if not set(edge_faces[e]) & affected and stations[e] != old_stations[e]:
                raise _Refuse(f"retained edge {e} stations changed")
    except _Refuse as error:
        raise ConnectedUpdateError(f"assembled mesh is not a valid connected mesh: {error}") from error
    if old.seeding is not None:
        if final.seeding is None:
            raise ConnectedUpdateError("assembled mesh lost its mapped seeding")
        for edge, ids in stations.items():
            actual = (len(ids) - 1) // (2 if final.order == "quadratic" else 1)
            if final.seeding.divisions.get(edge) != actual:
                raise ConnectedUpdateError(f"recomputed seeding differs from the assembled stations of edge {edge}")
    check("connected quality validation")
    rm._validate_quality(project, final, settings, cancellation_check)

    updated = capture_connected_baseline(project, settings, final)
    if updated is None:
        raise ConnectedUpdateError("the updated mesh is no longer connected-eligible: "
                                   f"{explain_connected_ineligibility(project, settings, final)}")
    return ConnectedUpdateRecord(final, updated, retained_nodes, retained_elements, plan)
