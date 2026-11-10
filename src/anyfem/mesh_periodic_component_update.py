"""Whole-component density updates for a closed, source-certified cylindrical sheet."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import math

import numpy as np

from . import regional_mesh as rm
from .mesh_connected_update import _published_mesh_digest
from .mesh_curved_update import _Cyl, _support_record
from .mesh_general_update import GeneralUpdateResult
from .mesh_numbering import MeshNumbering, number_full_mesh, raise_high_water
from .mesh_whole_plate_update import _Cancellation

SCHEMA = "anyfem.periodic-cylinder-component"
VERSION = 1


class PeriodicUpdateUnsupported(rm.RegionalUpdateUnsupported):
    """An atlas, component boundary, density request or output proof is missing."""


def _require(condition, message):
    if not condition:
        raise PeriodicUpdateUnsupported(message)


def periodic_ring_family(project):
    from anygeometry import Cylinder
    g = project.geometry
    if len(g.faces) < 2 or any(type(face.surface) is not Cylinder for face in g.faces.values()):
        return False
    # A full ring has precisely two shared, distinct meridians on each
    # sector. Two half-cylinder sectors may share both with the same neighbor.
    owners = {}
    for face in g.faces.values():
        for use in face.loop:
            owners.setdefault(use.edge, []).append(face.id)
    links = {face: [] for face in g.faces}
    for cells in owners.values():
        if len(cells) == 2 and cells[0] != cells[1]:
            a, b = cells
            links[a].append(b)
            links[b].append(a)
        elif len(cells) != 1:
            return False
    if any(len(neighbors) != 2 for neighbors in links.values()):
        return False
    first = next(iter(links))
    seen, pending = {first}, [first]
    while pending:
        for other in links[pending.pop()]:
            if other not in seen:
                seen.add(other)
                pending.append(other)
    return len(seen) == len(g.faces)

def _source(project, settings, check):
    from anygeometry import CylinderAtlasStatus, query_cylinder_atlas
    from .document import canonical_hash

    g = project.geometry
    _require(periodic_ring_family(project), "Cylindrical faces do not form a closed shared-edge cycle.")
    _require(len(g.sheets) == 1 and not (
        project.read_only_reason or g.members or g.attachments or g.junctions or project.beam_edges
        or project.beam_offsets or project.refinements or project.sheet_join_intent_problems()
        or project._feature_materialization_problems()), "A resolved isolated cylindrical shell sheet is required.")
    _require(rm._strategy(project, settings) == "quad_first" and settings.element_order in ("linear", "quadratic"),
             "The closed-cylinder generator must provide quad-first cylindrical validation.")
    _require(all(len(face.loop) == 4 and not face.holes for face in g.faces.values()),
             "Periodic updates require untrimmed four-edge sectors.")
    sheet, = g.sheets.values()
    uses = tuple(g.handle("face_use", u) for u in sheet.face_use_ids)
    _require(len(uses) == len(g.faces) and len(set(uses)) == len(uses),
             "Every sector must have exactly one occurrence on the cylindrical sheet.")
    atlas = query_cylinder_atlas(g, uses, reference_face_use=uses[0], expected_revision=g.revision,
                                 cancellation_check=lambda phase: check(phase) or False)
    n = len(g.faces)
    _require(atlas.status is CylinderAtlasStatus.QUALIFIED and atlas.certificate.complete
             and len(atlas.sectors) == n and {s.face.id for s in atlas.sectors} == set(g.faces)
             and len(atlas.interfaces) == n and sum(i.is_reference_seam for i in atlas.interfaces) == 1
             and all(not i.external_coedges for i in atlas.interfaces)
             and len(atlas.boundary_cycles) == 2 and all(abs(c.winding) == 1 for c in atlas.boundary_cycles),
             "The live owner atlas did not certify a complete, single-period cylindrical component.")
    edges = {u.edge for face in g.faces.values() for u in face.loop}
    vertices = {v for e in g.edges.values() for v in (e.start, e.end, getattr(e.curve, "via_vertex", None)) if v is not None}
    _require(edges == set(g.edges) and vertices == set(g.vertices)
             and all(e in edges for e, _ in settings.overrides), "Loose or unowned source geometry in the cylinder.")
    model = rm._surface_model_inputs(project)
    model["meshing"] = rm._fixed_meshing_inputs(model["meshing"])
    return atlas, {"atlas": atlas.digest, "model": str(g.model_id), "revision": g.revision,
                   "document": str(project.document_id), "fixed": canonical_hash(model),
                   "settings": replace(settings, overrides=(), regional=None, target_size=1.,
                                       publication_allocation=None, execution_policy=None).input_hash,
                   "target": float(settings.target_size).hex(), "overrides": [list(row) for row in settings.overrides]}


def _shell_boundary(mesh, face, cylinder, tol, check):
    """Prove a consistently oriented complete tiling of one untrimmed normalized chart."""
    ids = mesh.elements_of_face.get(face, ())
    _require(bool(ids) and len(ids) == len(set(ids)), f"Face {face} has no unique ordered shell cells.")
    uv, areas, edges, midsides = {}, [], Counter(), {}
    angle_scale = abs(cylinder.radius * cylinder.sweep)
    uv_tol = (tol / angle_scale, tol / abs(cylinder.height))
    orientation = 0
    for index, e in enumerate(ids):
        if index % 128 == 0:
            check("verify full cylinder shell cells")
        corners = mesh.corners_of(e)
        conn = mesh.quads[e] if e in mesh.quads else mesh.tris[e]
        k = len(corners)
        _require(len(set(conn)) == len(conn) and len(conn) == k * (2 if mesh.order == "quadratic" else 1),
                 f"Shell {e} has invalid order or repeated nodes.")
        for node in conn:
            if node not in uv:
                u, v, gap = cylinder.uv(mesh.nodes[node])
                _require(gap <= tol and -uv_tol[0] <= u <= 1+uv_tol[0] and -uv_tol[1] <= v <= 1+uv_tol[1],
                         f"Node {node} is outside the source sector {face}.")
                uv[node] = (u, v)
        pts = [uv[n] for n in corners]
        turns = [(b[0]-a[0])*(c[1]-b[1]) - (b[1]-a[1])*(c[0]-b[0])
                 for a, b, c in zip(pts, pts[1:]+pts[:1], pts[2:]+pts[:2])]
        sign = 1 if min(turns) > 0 else -1 if max(turns) < 0 else 0
        _require(bool(sign) and orientation in (0, sign), f"Shell {e} folds or reverses on face {face}.")
        orientation = sign
        area = .5*sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(pts, pts[1:]+pts[:1]))
        areas.append(abs(area))
        for i, a in enumerate(corners):
            b = corners[(i+1) % k]
            mid = conn[k+i] if mesh.order == "quadratic" else None
            edges[(a,b)] += 1
            _require(edges[a,b] == 1 and midsides.setdefault((min(a,b),max(a,b)), mid) == mid,
                     f"Face {face} has repeated oriented corners or conflicting midsides.")
    _require(abs(math.fsum(areas)-1.) <= 2*sum(uv_tol) + 16*np.finfo(float).eps,
             f"Source sector {face} does not have one full chart area.")
    free = {}
    for a,b in edges:
        if (b,a) not in edges:
            free[(a,b)] = midsides[min(a,b),max(a,b)]
    return free, set(uv)


def _working_owners(project):
    """Resolve the namespace of the producer's geometry-local certificates."""
    g = project.geometry
    report = project._last_mesh_preparation
    _require(report.get("source_model_id") == str(g.model_id) and report.get("source_revision") == g.revision,
             "The producer lacks the actual source-to-working preparation record.")
    working = (report.get("working_model_id"), report.get("working_revision"))
    _require(isinstance(working[0], str) and type(working[1]) is int,
             "The working geometry identity is missing from the cylinder preparation.")
    maps = {}
    for kind, entities in (("face", g.faces), ("edge", g.edges)):
        inverse = {}
        for source_id in entities:
            made = report.get("source_to_working", {}).get(f"{kind}:{source_id}", ())
            _require(len(made) == 1 and made[0].startswith(kind + ":"),
                     f"The cylinder lacks exact {kind} correspondence to its prepared geometry.")
            work_id = int(made[0].split(":", 1)[1])
            _require(work_id not in inverse, "The cylinder preparation merged geometry owners.")
            inverse[work_id] = source_id
        maps[kind] = inverse
    return working, maps

def _verify(project, mesh, settings, atlas, check):
    from anymesher import verify_mesh_quality
    from .mesh_general_update import _source_proof
    from anymesher import requires_material_source_transfer

    g = project.geometry
    _require(str(mesh.geometry_model_id) == str(g.model_id) and mesh.geometry_revision == g.revision
             and mesh.order == settings.element_order, "Cylinder mesh model, revision or order differs.")
    if requires_material_source_transfer(g, mesh):
        _source_proof(project, mesh)
    _require(not (mesh.beams or mesh.couplings or mesh.automatic_intersections or mesh.automatic_beam_connections
                  or mesh.automatic_shell_connections or mesh.declared_plate_junction_edges),
             "The isolated cylinder acquired additional structural connections.")
    _require(set(mesh.elements_of_face) == set(g.faces) and set(mesh.nodes_of_edge) == set(g.edges)
             and len(mesh.elements_of_sheet) == 1 and set(mesh.elements_of_sheet) == set(g.sheets),
             "Cylinder mesh source face, sheet or edge associations differ.")
    tol = atlas.certificate.tolerance_surface
    working, maps = _working_owners(project)
    diagnostics = mesh.hybrid_diagnostics
    _require(diagnostics.get("route") == "quad-first-cylindrical" and diagnostics.get("certifiable") is True
             and {int(k) for k in diagnostics.get("validation", {}).get("faces", {})} == set(maps["face"]),
             "The full generator lacks its cylindrical physical validation record.")
    claimed_nodes, claimed_cells = set(), set()
    traces = {}
    for sector in atlas.sectors:
        f = sector.face.id
        chart = _Cyl(_support_record(g.faces[f].surface), tol)
        free, local_nodes = _shell_boundary(mesh, f, chart, tol, check)
        cells = set(mesh.elements_of_face[f])
        _require(claimed_cells.isdisjoint(cells), "A cylinder cell belongs to more than one source face.")
        claimed_cells.update(cells)
        claimed_nodes.update(local_nodes)
        for occurrence in g.faces[f].loop:
            edge = occurrence.edge
            chain = tuple(mesh.nodes_of_edge[edge])
            stride = 2 if mesh.order == "quadratic" else 1
            _require(len(chain) >= stride+1 and (len(chain)-1) % stride == 0 and len(chain) == len(set(chain)),
                     f"Invalid cylinder source station chain on edge {edge}.")
            _require((chain[0], chain[-1]) == (mesh.node_of_vertex.get(g.edges[edge].start),
                                                mesh.node_of_vertex.get(g.edges[edge].end)),
                     f"Edge {edge} endpoints differ from their source vertices.")
            if edge not in traces:
                prev = -1.
                for node in chain:
                    _, t, residual = g.closest_edge_point(edge, mesh.nodes[node])
                    _require(residual <= tol and t > prev, f"Source edge {edge} stations are off-curve or unordered.")
                    prev = t
            oriented = chain if occurrence.forward else chain[::-1]
            directions = set()
            for i in range(0, len(oriented)-1, stride):
                a, b = oriented[i], oriented[i+stride]
                directions.add((a,b) in free)
                endpoints = (a,b) if (a,b) in free else (b,a)
                _require(endpoints in free and free.pop(endpoints) == (oriented[i+1] if stride == 2 else None),
                         f"Edge {edge} is not the exact corner/midside shell boundary of face {f}.")
            _require(len(directions) == 1, f"Edge {edge} changes shell traversal on face {f}.")
            follows_loop = directions.pop()
            _require(follows_loop == (sector.face_use_orientation == 1),
                     f"Shell boundary orientation on face {f} contradicts its source FaceUse.")
            actual = oriented if follows_loop else oriented[::-1]
            traces.setdefault(edge, []).append((f, actual))
        _require(not free, f"Source face {f} has an unowned internal rim.")
    _require(claimed_nodes == set(mesh.nodes) and claimed_cells == set(mesh.shells)
             and len(next(iter(mesh.elements_of_sheet.values()))) == len(claimed_cells)
             and set(next(iter(mesh.elements_of_sheet.values()))) == claimed_cells,
             "Cylinder has unowned or missing shell cells or nodes.")
    interfaces = {i.edge.id for i in atlas.interfaces}
    for edge, sides in traces.items():
        if edge in interfaces:
            _require(len(sides) == 2 and sides[0][0] != sides[1][0] and sides[0][1] == sides[1][1][::-1],
                     f"The two sides of periodic interface {edge} do not have identical opposite chains.")
        else:
            _require(len(sides) == 1, f"External cylinder rim {edge} has incorrect incidence.")
    if mesh.order == "quadratic":
        certificate = diagnostics.get("high_order_geometry", {})
        reports = certificate.get("reports", ())
        _require((certificate.get("model_id"), certificate.get("revision")) == working
                 and certificate.get("status") == "CERTIFIED_POSITIVE" and certificate.get("q8_count") == len(mesh.quads)
                 and certificate.get("t6_count") == len(mesh.tris)
                 and {int(row["face_id"]) for row in reports} == set(maps["face"])
                 and all((row["model_id"], row["revision"]) == working and row["validity_status"] == "CERTIFIED_POSITIVE"
                         and row["invalid_elements"] == 0 and row["certified_elements"] == len(
                             mesh.elements_of_face[maps["face"][int(row["face_id"])]]) for row in reports),
                 "Quadratic ring lacks a complete positive certificate bound to its actual prepared geometry.")
        station_nodes = {(e, n) for e, chain in mesh.nodes_of_edge.items() for n in chain[1::2]}
        reported = set()
        for row in certificate["boundary_midsides"]:
            edge, node = maps["edge"][int(row["source_edge"])], int(row["midpoint_node"])
            expected = g.sample_edge(edge, [row["midpoint_parameter"]])[0]
            _require((edge, node) not in reported and np.linalg.norm(mesh.nodes[node] - expected) <= tol,
                     "Certified midside differs from its original source-edge parameter.")
            reported.add((edge, node))
        _require(reported == station_nodes, "High-order ring certificate lacks a unique source station midside.")
    for edge, count in settings.overrides:
        _require(len(mesh.nodes_of_edge[edge]) == count*(2 if mesh.order == "quadratic" else 1)+1,
                 f"The full cylinder did not achieve requested divisions for edge {edge}.")
    quality = verify_mesh_quality(mesh)
    _require(quality.num_shell_elements == len(claimed_cells) and quality.min_scaled_jacobian > 0
             and np.isfinite(quality.max_aspect_ratio), "Cylinder whole-mesh shell validity failed.")
    rm._validate_quality(project, mesh, settings, check)
    return {"quality": quality.as_dict(), "interfaces": len(interfaces),
            "source_model": str(g.model_id), "source_revision": g.revision,
            "working_model": working[0], "working_revision": working[1],
            "seam": next(i.edge.id for i in atlas.interfaces if i.is_reference_seam)}


def _capture(project, settings, mesh, check):
    atlas, source = _source(project, settings, check)
    geometry_proof = _verify(project, mesh, settings, atlas, check)
    row = {"schema": SCHEMA, "version": VERSION, "source": source, "mesh": _published_mesh_digest(mesh),
           "pins": rm._pins(), "coverage": geometry_proof}
    return {**row, "digest": rm._digest(row)}


def capture_periodic_baseline(project, settings, mesh):
    try:
        return _capture(project, settings, mesh, rm._check_fn(None))
    except (PeriodicUpdateUnsupported, rm.RegionalUpdateError, rm._Ineligible, KeyError, ValueError):
        return None


@dataclass(frozen=True)
class PeriodicPlan:
    faces: tuple[int, ...]
    description: str
    is_noop: bool
    applicable: bool = True

    @property
    def impact(self):
        return {"retained": [list(self.faces)] if self.is_noop else [],
                "regenerated": [] if self.is_noop else [list(self.faces)], "removed": [], "added": []}


def plan_periodic_update(project, settings, baseline, *, cancellation_check=None):
    check = rm._check_fn(cancellation_check)
    _require(baseline.get("schema") == SCHEMA and baseline.get("version") == VERSION
             and baseline.get("pins") == rm._pins() and baseline.get("digest") == rm._digest(
                 {k:v for k,v in baseline.items() if k != "digest"}), "The published cylinder baseline is stale.")
    try:
        _, source = _source(project, settings, check)
    except (ValueError, KeyError) as error:
        raise PeriodicUpdateUnsupported(f"The current cylinder atlas could not be bound: {error}") from error
    prior = baseline["source"]
    _require(all(prior.get(k) == source[k] for k in source if k not in ("overrides", "target")),
             "Periodic update requires the original source atlas, model settings and shell assignments.")
    noop = source == prior
    return PeriodicPlan(tuple(sorted(project.geometry.faces)),
        "No change to the cylinder density." if noop else "Regenerate the entire periodic cylindrical component. "
        "All shell and node IDs will change, and both sides of every sector seam must agree.", noop)


def generate_periodic_update(project, settings, baseline, old_mesh, *, node_high_water, element_high_water,
                             cancellation_check=None):
    from .document import DocumentSession
    from .mesh_jobs import run_mesh_job

    check = rm._check_fn(cancellation_check)
    plan = plan_periodic_update(project, settings, baseline, cancellation_check=check)
    old = rm._decode(old_mesh)
    _require(_published_mesh_digest(old) == baseline["mesh"], "The old cylindrical shell differs from its baseline.")
    if plan.is_noop:
        return GeneralUpdateResult(old, dict(baseline), frozenset(old.nodes), frozenset(old.shells), plan, {})
    snapshot = DocumentSession(project).snapshot()
    detached = snapshot.thaw()
    check("generate complete periodic cylinder")
    try:
        candidate = run_mesh_job(detached, snapshot.revision.model_hash,
            replace(settings, regional=None, publication_allocation=None), lambda *_: None, _Cancellation(check)).mesh
        _require(candidate.hybrid_diagnostics.get("automation", {}).get("status") != "inspection_only",
                 "The full cylinder was returned for inspection only.")
        allocation = raise_high_water(MeshNumbering(node_high_water, element_high_water), old)
        numbered = number_full_mesh(candidate, allocation, source_geometry=detached.geometry)
        updated = _capture(detached, settings, numbered, check)
        if float.fromhex(updated["source"]["target"]) < float.fromhex(baseline["source"]["target"]):
            _require(len(numbered.shells) > len(old.shells), "The smaller target did not refine the complete ring.")
    except (ValueError, KeyError, rm._Ineligible) as error:
        raise PeriodicUpdateUnsupported(f"The full cylinder generator or validation refused: {error}") from error
    check("complete cylindrical component update")
    evidence = {"generation_scope": "entire periodic cylinder", **updated["coverage"], "impact": plan.impact}
    updated["last_update"] = {"generation_scope": evidence["generation_scope"], "impact": plan.impact}
    updated["digest"] = rm._digest({key: value for key, value in updated.items() if key != "digest"})
    return GeneralUpdateResult(numbered, updated, frozenset(), frozenset(), plan, evidence)
