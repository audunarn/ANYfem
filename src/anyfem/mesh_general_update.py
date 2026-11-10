"""Update published source faces with live cell, interface and numbering proofs.

The mesher validates the complete source closure. Unchanged cells can keep
their permanent numbers after their oriented connectivity, coordinates and
source associations match the original publication.
"""
from __future__ import annotations

from contextlib import nullcontext
from collections import defaultdict
from dataclasses import dataclass, field, replace

import numpy as np

from . import regional_mesh as rm
from .mesh_connected_update import _published_mesh_digest
from .mesh_numbering import MeshNumbering, _fresh_ids, element_numbers, raise_high_water
from .mesh_whole_plate_update import _Cancellation


SCHEMA = "anyfem.regional-source-surfaces"
VERSION = 1


class GeneralSurfaceUnsupported(rm.RegionalUpdateUnsupported):
    """The full source owner, boundary or permanent identity could not be proved."""


def _require(condition, reason):
    if not condition:
        raise GeneralSurfaceUnsupported(reason)


def general_family(project):
    from anygeometry import CoonsSurface, Cone, Cylinder, ExtrudedSurface, RuledSurface
    from anygeometry.nurbs import NurbsSurface
    from .model.project import source_publication_required

    faces = project.geometry.faces
    return bool(faces) and (source_publication_required(project.geometry) or any(
        isinstance(f.surface, (ExtrudedSurface, RuledSurface, NurbsSurface))
        or isinstance(f.surface, CoonsSurface) and f.surface.has_boundaries
        or isinstance(f.surface, (Cylinder, Cone)) and (len(faces) > 1 or f.holes)
        for f in faces.values()))


def _inputs(project, settings):
    from .document import canonical_hash

    g = project.geometry
    _require(general_family(project), "No general authored surface in the selected source.")
    _require(not (g.members or g.junctions or g.attachments or project.beam_edges or project.beam_offsets
                  or project.read_only_reason or project.sheet_join_intent_problems()
                  or project._feature_materialization_problems()), "General surface update requires resolved shell owners.")
    _require(settings.element_order == "linear", "General surface source coverage requires linear shell elements.")
    records = []
    doc = rm._document(g)
    sheets_by_face = defaultdict(list)
    for sheet_id, sheet in g.sheets.items():
        for face in {g.face_uses[use].face_id for use in sheet.face_use_ids}:
            sheets_by_face[face].append(sheet_id)
    for face in g.faces:
        records.append(rm._component_record(g, doc, [face], settings, sheets_in_scope=sheets_by_face[face]))
    for key, owner in (("edges", g.edges), ("vertices", g.vertices)):
        ids = [i for row in records for i in row[key]]
        _require(len(ids) == len(set(ids)) and set(ids) == set(owner),
                 "Source faces must have disjoint complete edge and vertex ownership.")
    _require({e for e, _ in settings.overrides} <= set(g.edges), "A requested division names an unowned edge.")
    model = rm._surface_model_inputs(project)
    model.pop("geometry")
    # The exact serialized face, oriented trim, support, edges and control
    # vertices are bound per face below. Every other geometry input remains global.
    geometry_globals = {k: v for k, v in doc["raw"].items()
                         if k not in ("revision", "faces", "edges", "vertices", "id_state", "checksum")}
    structural = dict(geometry_globals["structural"])
    face_uses = structural.pop("face_uses")
    coedges = structural.pop("coedges")
    geometry_globals["structural"] = structural
    meshing = rm._fixed_meshing_inputs(model.pop("meshing"))
    zones = meshing.pop("refinements")
    fixed = {key: canonical_hash(value) for key, value in model.items()}
    fixed.update({f"geometry.{key}": canonical_hash(value) for key, value in geometry_globals.items()})
    fixed["meshing"] = canonical_hash(meshing)
    fixed["settings"] = replace(settings, regional=None, target_size=1., overrides=(), execution_policy=None).input_hash
    local = {}
    uses_by_face = defaultdict(list)
    face_by_use = {}
    coedges_by_face = defaultdict(list)
    for use in face_uses:
        uses_by_face[use["face_id"]].append(use)
        face_by_use[use["id"]] = use["face_id"]
    for edge in coedges:
        coedges_by_face[face_by_use[edge["face_use_id"]]].append(edge)
    for row in records:
        face, = row["faces"]
        occurrence = {"definition": row["fingerprint"], "face_uses": uses_by_face[face],
                      "coedges": coedges_by_face[face]}
        local[str(face)] = {"definition": canonical_hash(occurrence), "divisions": row["local"]["overrides"], "sizes": []}
    points = []
    for zone in zones:
        ref = zone.get("ref")
        if zone.get("mode") == "override" and ref and ref["kind"] == "face" and str(ref["id"]) in local:
            local[str(ref["id"])]["sizes"].append(zone)
        elif zone.get("mode", "refine") == "refine" and ref is None and zone.get("center") is not None:
            points.append(zone)
        else:
            raise GeneralSurfaceUnsupported("The refinement lacks a supported point or unique face owner.")
    return records, {"fixed": fixed, "document": str(project.document_id), "model": str(g.model_id),
                     "revision": g.revision, "target": float(settings.target_size).hex(), "faces": local,
                     "zones": points}


def _mesh_owners(mesh, records):
    _require(not (mesh.beams or mesh.couplings or mesh.automatic_intersections or mesh.automatic_beam_connections
                  or mesh.automatic_shell_connections or mesh.declared_plate_junction_edges or mesh.offset_nodes_of_edge),
             "Structural connections or offset nodes require a distinct generation receipt.")
    rm._closure_gate(mesh, records)
    claimed_nodes, claimed_cells = set(), set()
    for row in records:
        cells, nodes = rm._component_mesh(mesh, row["faces"])
        _require(claimed_nodes.isdisjoint(nodes) and claimed_cells.isdisjoint(cells),
                 "Different source faces share generated cells or nodes.")
        _require(all(set(mesh.nodes_of_edge.get(edge, ())) <= nodes for edge in row["edges"])
                 and all(mesh.nodes_of_edge.get(edge) for edge in row["edges"]),
                 "A source edge has missing or foreign shell stations.")
        claimed_nodes.update(nodes)
        claimed_cells.update(cells)
    _require(claimed_nodes == set(mesh.nodes) and claimed_cells == element_numbers(mesh),
             "A generated node or cell has no unambiguous source face.")


def _source_proof(project, mesh):
    from anygeometry import to_dict
    from anymesher import requires_material_source_transfer

    g = project.geometry
    _require(str(mesh.geometry_model_id) == str(g.model_id) and mesh.geometry_revision == g.revision,
             "Mesh model/revision differs from the current source geometry.")
    _require(requires_material_source_transfer(g, mesh),
             "This ordinary full generator supplies no live material-cell proof for this surface or trim.")
    owner = getattr(getattr(getattr(mesh, "boundary_registry", None), "view", None), "source", None)
    _require(owner is not None and (owner is g or rm._digest(to_dict(owner)) == rm._digest(to_dict(g))),
             "General surface capture requires live source-cell publication for the exact source geometry.")
    try:
        from anymesher import validate_numbered_source_mesh
        validate_numbered_source_mesh(mesh, source_geometry=owner)
    except (ImportError, ValueError, RuntimeError) as error:
        raise GeneralSurfaceUnsupported(f"Live source-cell publication could not be verified: {error}") from error
    return owner


def _capture(project, settings, mesh):
    records, source = _inputs(project, settings)
    source_owner = _source_proof(project, mesh)
    _mesh_owners(mesh, records)
    baseline = {"schema": SCHEMA, "version": VERSION, "pins": rm._pins(), "source": source,
                "mesh": _published_mesh_digest(mesh)}
    from anymesher.regional_source_faces import capture_reusable_source_faces
    proposals = capture_reusable_source_faces(source_owner, mesh)
    baseline["reusable_faces"] = proposals
    if len(records) == 1 and proposals:
        from anymesher.regional_source_faces import capture_source_face_identity_rows
        face, = records[0]["faces"]
        if str(face) in proposals:
            identities = capture_source_face_identity_rows(source_owner, mesh, face)
            _validate_parent_rows(mesh, face, proposals[str(face)]["data"], identities)
            baseline["point_patch_parent"] = identities
    return {**baseline, "digest": rm._digest(baseline)}


def capture_general_baseline(project, settings, mesh):
    try:
        return _capture(project, settings, mesh)
    except (GeneralSurfaceUnsupported, rm._Ineligible, KeyError, ImportError, ValueError):
        return None


@dataclass(frozen=True)
class GeneralPlan:
    description: str
    regenerated: tuple[int, ...]
    retained: tuple[int, ...]
    reuse_candidates: tuple[int, ...] = ()
    applicable: bool = True
    patched_face: int | None = None
    replaced_elements: tuple[int, ...] = ()
    interface_nodes: tuple[int, ...] = ()
    _patch: dict | None = field(default=None, repr=False, compare=False)

    @property
    def is_noop(self):
        return not self.regenerated and self.patched_face is None

    @property
    def impact(self):
        impact = {"regenerated": [[f] for f in self.regenerated], "retained": [[f] for f in self.retained],
                  "added": [], "removed": []}
        if self.patched_face is not None:
            impact.update(patched_face=self.patched_face, replaced_elements=list(self.replaced_elements),
                          interface_nodes=list(self.interface_nodes))
        return impact


def _validate_parent_rows(mesh, face, data, identities):
    """Check owner row correspondence against every oriented cell on the real parent."""
    nodes, cells = identities["nodes"], identities["cells"]
    shells = mesh.shells
    face_cells, face_nodes = rm._component_mesh(mesh, [face])
    _require(len(nodes) == len(data["node_rows"]) == len(face_nodes) and set(nodes) == face_nodes
             and len(cells) == len(data["cells"]) == len(face_cells) and set(cells) == set(face_cells),
             "Point patch parent rows do not bijectively cover the published face.")
    _require(all(np.array_equal(mesh.nodes[node], row[1]) for node, row in zip(nodes, data["node_rows"]))
             and all(tuple(nodes[n] for n in conn) == shells[e] for conn, e in zip(data["cells"], cells)),
             "Point patch parent rows differ from the oriented source cells or their coordinates.")


def _point_plan(project, settings, baseline, current, controls, check):
    from anymesher.errors import MeshError
    from anymesher.regional_source_patch import plan_source_face_point_patch

    previous = baseline["source"]
    if (len(current["faces"]) != 1 or previous["zones"] or len(current["zones"]) != 1
            or any(previous[k] != current[k] for k in ("faces", "revision", "target"))
            or len(project.refinements) != 1 or "point_patch_parent" not in baseline):
        return None
    face, = project.geometry.faces
    identities = baseline["point_patch_parent"]
    try:
        proposal = baseline["reusable_faces"][str(face)]
        patch = plan_source_face_point_patch(
            project.geometry, proposal, project.refinements[0], target_size=settings.target_size,
            strategy=rm._strategy(project, settings), recombine=controls.recombine, cancellation_check=check)
        if patch is None:
            return None
        _require(patch["face"] == face, "Point patch belongs to a different source face.")
        retired = tuple(sorted(identities["cells"][r] for r in patch["replaced_cell_rows"]))
        loop = tuple(identities["nodes"][r] for r in patch["interface_rows"])
        _require(0 < len(retired) == len(set(retired)) < len(identities["cells"])
                 and len(loop) == len(set(loop)) >= 3, "Point patch has an invalid parent cut or interface.")
    except (MeshError, IndexError, KeyError, ValueError, TypeError) as error:
        raise GeneralSurfaceUnsupported(f"Point patch source or parent interface refused: {error}") from error
    return GeneralPlan(f"Replace {len(retired)} parent cells on face {face} for the point sizing change. "
                       f"Keep the {len(loop)} interface nodes and all cells outside the cut.",
                       (), (), patched_face=face, replaced_elements=retired, interface_nodes=loop, _patch=patch)


def plan_general_update(project, settings, baseline, *, cancellation_check=None):
    check = rm._check_fn(cancellation_check)
    check("validate general surface update scope")
    _require(baseline.get("schema") == SCHEMA and baseline.get("version") == VERSION
             and baseline.get("digest") == rm._digest({k: v for k, v in baseline.items() if k != "digest"})
             and baseline.get("pins") == rm._pins(), "General surface baseline or owner build differs.")
    try:
        _, current = _inputs(project, settings)
    except (rm._Ineligible, KeyError, ValueError) as error:
        raise GeneralSurfaceUnsupported(f"General surface scope is unproved: {error}") from error
    prior = baseline["source"]
    differences = sorted(k for k in set(current["fixed"]) | set(prior["fixed"])
                         if current["fixed"].get(k) != prior["fixed"].get(k))
    _require(not differences, f"General surface update encountered changed global inputs {differences}.")
    fixed = ("document", "model")
    _require(all(current[k] == prior[k] for k in fixed) and current["faces"].keys() == prior["faces"].keys(),
             "General surface update requires the same named face owners, structural ownership and global settings.")
    global_change = any(current[k] != prior[k] for k in ("target", "zones"))
    changed = tuple(sorted(int(f) for f in current["faces"] if global_change or current["faces"][f] != prior["faces"][f]))
    definitions_changed = any(current["faces"][f]["definition"] != prior["faces"][f]["definition"]
                              for f in current["faces"])
    _require(not definitions_changed or current["revision"] != prior["revision"],
             "Authored geometry changed without a new source revision.")
    if current["revision"] != prior["revision"] and not definitions_changed:
        # A new revision with no localized geometric delta still needs current
        # generation authority. Its old mesh cannot be returned as a no-op.
        changed = tuple(sorted(project.geometry.faces))
    kept = tuple(sorted(set(project.geometry.faces) - set(changed)))
    if not changed:
        return GeneralPlan("No source or sizing change.", (), kept)
    from anymesher.regional_source_faces import reusable_face_for_request
    from .mesh_controls import MeshControls
    controls = settings.controls or MeshControls.from_settings(project.native_mesh_settings)
    points_changed = current["zones"] != prior["zones"]
    if points_changed:
        point_plan = _point_plan(project, settings, baseline, current, controls, check)
        if point_plan is not None:
            return point_plan
    proposals = baseline.get("reusable_faces", {})
    candidates = tuple(f for f in kept if str(f) in proposals and reusable_face_for_request(
        proposals[str(f)], rm._strategy(project, settings), controls.recombine))
    if candidates:
        operation = f"Reuse the stored cells of source faces {list(candidates)} after fresh owner validation."
    else:
        operation = "Regenerate the full source mesh."
    if points_changed:
        operation += (" A fixed cut requires one added point zone with certified size and influence "
                      "inside an unchanged source face's interior Q4 neighbourhood.")
    return GeneralPlan(f"{operation} Replace whole faces {list(changed)} because no fixed interior cut is proved. "
                       f"Retain exact IDs on faces {list(kept)} after their cells and associations match.",
                       changed, kept, candidates)

def _retained_ids(old, candidate, records, plan):
    nodes, elements = {}, {}
    old_shells, fresh_shells = old.shells, candidate.shells
    for record in records:
        face, = record["faces"]
        if face not in plan.retained:
            continue
        before = old.elements_of_face[face]
        after = candidate.elements_of_face[face]
        _require(len(before) == len(after), f"Retained face {face} changed cell count.")
        for new, previous in zip(after, before, strict=True):
            _require((previous in old.quads) == (new in candidate.quads), f"Retained face {face} changed cell type.")
            prev_conn = old_shells[previous]
            new_conn = fresh_shells[new]
            _require(len(prev_conn) == len(new_conn), f"Retained face {face} changed connectivity.")
            elements[new] = previous
            for a, b in zip(new_conn, prev_conn, strict=True):
                _require(nodes.setdefault(a, b) == b and np.array_equal(candidate.nodes[a], old.nodes[b]),
                         f"Retained face {face} changed oriented connectivity or coordinates.")
    _require(len(nodes) == len(set(nodes.values())), "Retained face nodes have ambiguous identity.")
    return nodes, elements


def _cut_boundary(mesh, elements):
    """Return the oriented edges of a cell patch and reject non-manifold interiors."""
    edges = defaultdict(list)
    shells = mesh.shells
    for element in elements:
        cell = shells[element]
        for a, b in zip(cell, cell[1:] + cell[:1]):
            edges[min(a, b), max(a, b)].append((a, b))
    boundary = set()
    for sides in edges.values():
        if len(sides) == 1:
            boundary.add(sides[0])
        else:
            _require(len(sides) == 2 and sides[0] == sides[1][::-1],
                     "Point patch has overlapping or inconsistently oriented interior edges.")
    return boundary


def _patch_ids(old, fresh, baseline, plan, stats):
    """Translate receipt rows only after proving both sides of the advertised fixed cut."""
    face = plan.patched_face
    identity = baseline["point_patch_parent"]
    old_nodes, old_cells = identity["nodes"], identity["cells"]
    data = baseline["reusable_faces"][str(face)]["data"]
    _require(set(stats.get("patched_faces", ())) == {face},
             "Point patch received no current final owner proof for the selected face.")
    receipt = stats["patched_faces"][face]
    cut = set(plan._patch["replaced_cell_rows"])
    _require(cut == set(receipt["replaced_cell_rows"])
             and tuple(receipt["interface_rows"]) == tuple(plan._patch["interface_rows"]),
             "Issued point patch differs from the previewed cells or interface.")
    kept = set(range(len(old_cells))) - cut
    rows, surviving = receipt["kept_cell_rows"], receipt["old_node_rows"]
    # A station solely inside the retired cells gets a fresh permanent number
    # even when the patch re-emits its exact source position.
    shared = {n for r in kept for n in data["cells"][r]}
    _require(set(rows) == kept and shared <= set(surviving) <= set(range(len(old_nodes)))
             and len(set(rows.values())) == len(rows) and len(set(surviving.values())) == len(surviving),
             "Point patch receipt lacks a unique record for every retained row.")
    nodes = {surviving[r]: old_nodes[r] for r in shared}
    cells = {new: old_cells[r] for r, new in rows.items()}
    old_shells, new_shells = old.shells, fresh.shells
    fresh_elements = set(fresh.elements_of_face[face])
    _require(set(cells) <= fresh_elements and all(np.array_equal(fresh.nodes[a], old.nodes[b])
             for a, b in nodes.items()), "Point patch moved a retained source node or cell to another owner.")
    _require(all(tuple(nodes[n] for n in new_shells[a]) == old_shells[b]
                 and fresh.activity.get(a) == old.activity.get(b) for a, b in cells.items()),
             "Point patch changed a retained oriented source cell or its activity.")
    before = _cut_boundary(old, plan.replaced_elements)
    after = {(nodes[a], nodes[b]) for a, b in _cut_boundary(fresh, fresh_elements - set(cells))}
    loop = plan.interface_nodes
    ring = set(zip(loop, loop[1:] + loop[:1]))
    _require((before == ring or before == {(b, a) for a, b in ring}) and after == before
             and {(b, a) for a, b in before} <= _cut_boundary(old, list(cells.values())),
             "Point patch did not close against the complete oriented retained interface.")
    return nodes, cells


def _verify_sizes(project, old_mesh, mesh, settings, records, prior, check):
    from anymesher.refinement import SizeField

    g = project.geometry
    for edge, count in settings.overrides:
        _require(len(mesh.nodes_of_edge.get(edge, ())) == count + 1,
                 f"Requested divisions for edge {edge} were not achieved.")
    field = SizeField(g, settings.target_size, project.refinements, active_faces=tuple(g.faces))
    # Measure actual physical edge lengths. The target is nominal for unconstrained
    # triangles; certify its achieved local scale, in addition to each exact override.
    measured = {}
    met_zones = set()
    active_zones = {i for i, zone in enumerate(project.refinements)
                    if zone.center is not None and zone.size < settings.target_size}
    for record in records:
        face, = record["faces"]
        check("verify achieved general surface sizing")
        view = field.face_view(face)
        centers, edges = [], []
        for eid in mesh.elements_of_face[face]:
            xyz = np.asarray([mesh.nodes[n] for n in mesh.corners_of(eid)])
            centers.append(np.mean(xyz, axis=0))
            edges.append(np.max(np.linalg.norm(xyz - np.roll(xyz, 1, axis=0), axis=1)))
        sizes = np.asarray(edges)
        tol = g.tolerance.effective_length(float(max(edges)))
        _require(np.any(sizes <= view.target_size + tol),
                 f"No generated cell on face {face} meets its requested {view.target_size:g} size.")
        previous = prior["faces"][str(face)]["sizes"]
        previous_target = (min(float(r["size"]) for r in previous) if previous else float.fromhex(prior["target"]))
        if view.target_size < previous_target and max(sizes) > view.target_size + tol:
            before = []
            for e in old_mesh.elements_of_face[face]:
                xyz = np.asarray([old_mesh.nodes[n] for n in old_mesh.corners_of(e)])
                before.append(float(np.max(np.linalg.norm(xyz - np.roll(xyz, 1, axis=0), axis=1))))
            _require(np.mean(sizes) + tol < np.mean(before),
                     f"Face {face} did not get finer after reducing its nominal target.")
        measured[str(face)] = {"target": view.target_size, "cells": len(sizes),
                               "mean_longest_edge": float(np.mean(sizes)), "max_edge": float(np.max(sizes))}
        for index in sorted(active_zones):
            zone = project.refinements[index]
            distances = np.linalg.norm(np.asarray(centers) - np.asarray(zone.center), axis=1)
            # Credit only a cell whose center is inside the actual constant-size
            # core. A missed footprint, including a point-only core, fails closed.
            core = distances <= zone.radius + tol
            if np.any(core):
                achieved = float(np.min(sizes[core]))
                _require(achieved <= zone.size + tol,
                         f"No generated cell in the point zone on face {face} achieves the requested size.")
                measured[str(face)].setdefault("point_zones", []).append({
                    "index": index, "requested_size": zone.size, "radius": zone.radius,
                    "cells_in_core": int(np.count_nonzero(core)), "min_longest_edge": achieved,
                })
                met_zones.add(index)
    _require(met_zones == active_zones, "No generated cell meets the requested point-zone footprint and size.")
    return measured


@dataclass(frozen=True)
class GeneralUpdateResult:
    mesh: object
    baseline: dict
    retained_nodes: frozenset[int]
    retained_elements: frozenset[int]
    plan: GeneralPlan
    provenance: dict


def generate_general_update(project, settings, baseline, old_mesh, *, node_high_water, element_high_water,
                            cancellation_check=None):
    from anymesher import verify_mesh_quality
    from anymesher.errors import MeshError
    from .document import DocumentSession
    from .mesh_jobs import run_mesh_job
    from .mesh_numbering import number_mesh_with_maps

    check = rm._check_fn(cancellation_check)
    plan = plan_general_update(project, settings, baseline, cancellation_check=check)
    old = rm._decode(old_mesh)
    _require(_published_mesh_digest(old) == baseline["mesh"], "Parent content differs from its captured surface mesh.")
    if plan.is_noop:
        # Reopening the existing publication is handled by the session. A newly
        # published result still requires live material authority.
        _source_proof(project, old)
        return GeneralUpdateResult(old, dict(baseline), frozenset(old.nodes), frozenset(old.shells), plan, {})
    check("generate selected source cells")
    snapshot = DocumentSession(project).snapshot()
    detached = snapshot.thaw()
    try:
        if plan.patched_face is not None:
            proposal = baseline["reusable_faces"][str(plan.patched_face)]
            _validate_parent_rows(old, plan.patched_face, proposal["data"], baseline["point_patch_parent"])
        reuse = nullcontext({"reused_faces": set()})
        if plan.reuse_candidates or plan.patched_face is not None:
            from anymesher.regional_source_faces import reuse_source_faces
            candidates = {str(f): baseline["reusable_faces"][str(f)] for f in plan.reuse_candidates}
            options = {}
            if plan.patched_face is not None:
                key = str(plan.patched_face)
                candidates[key] = baseline["reusable_faces"][key]
                options["point_patches"] = {key: plan._patch}
            reuse = reuse_source_faces(detached.geometry, candidates, **options)
        with reuse as stats:
            fresh = run_mesh_job(detached, snapshot.revision.model_hash, replace(settings, regional=None),
                                 lambda *_: None, _Cancellation(check)).mesh
        reused = set(stats["reused_faces"])
        _require(reused == set(plan.reuse_candidates), "The owner could not reuse every planned unchanged source face.")
        _require(fresh.hybrid_diagnostics.get("automation", {}).get("status") != "inspection_only",
                 "The full surface generator produced an inspection-only mesh.")
        records, _ = _inputs(detached, settings)
        _mesh_owners(fresh, records)
        source = _source_proof(detached, fresh)
        if plan.patched_face is None:
            kept_nodes, kept_elems = _retained_ids(old, fresh, records, plan)
        else:
            kept_nodes, kept_elems = _patch_ids(old, fresh, baseline, plan, stats)
        allocation = raise_high_water(MeshNumbering(node_high_water, element_high_water), old)
        nodes = {**kept_nodes, **_fresh_ids(fresh.nodes.keys() - kept_nodes.keys(), allocation.node_high_water, False)}
        cells = {**kept_elems, **_fresh_ids(fresh.shells.keys() - kept_elems.keys(), allocation.element_high_water, False)}
        numbered = number_mesh_with_maps(fresh, nodes, cells, source_geometry=source)
        if plan.patched_face is not None:
            _require(set(old.shells) - set(numbered.shells) == set(plan.replaced_elements)
                     and set(old.nodes) & set(numbered.nodes) == set(kept_nodes.values())
                     and old.nodes_of_edge == numbered.nodes_of_edge
                     and old.elements_of_edge == numbered.elements_of_edge
                     and old.node_of_vertex == numbered.node_of_vertex
                     and old.thickness_of_face == numbered.thickness_of_face,
                     "Numbered point patch changed a parent cell, boundary or association outside its preview.")
        for record in records:
            if record["faces"][0] in plan.retained:
                old_ids = rm._component_mesh(old, record["faces"])
                new_ids = rm._component_mesh(numbered, record["faces"])
                _require(rm._binding(old, record, *old_ids) == rm._binding(numbered, record, *new_ids)
                         and old.thickness_of_face.get(record["faces"][0]) == numbered.thickness_of_face.get(record["faces"][0]),
                         "A retained face changed mesh ownership, activity, stations, grid or thickness.")
        measured = _verify_sizes(detached, old, numbered, settings, records, baseline["source"], check)
        quality = verify_mesh_quality(numbered)
        _require(quality.num_shell_elements == len(numbered.shells) and quality.min_scaled_jacobian > 0
                 and np.isfinite(quality.max_aspect_ratio), "Whole-mesh shell validity failed.")
        rm._validate_quality(detached, numbered, settings, check)
        updated = _capture(detached, settings, numbered)
    except (MeshError, rm._Ineligible, KeyError, IndexError, ValueError, TypeError) as error:
        raise GeneralSurfaceUnsupported(f"Source surface generation or validation refused: {error}") from error
    check("complete general surface update")
    provenance = {"generation_scope": "selected faces" if reused else "full source mesh",
                  "generated_faces": sorted(set(detached.geometry.faces) - reused),
                  "reused_source_faces": sorted(reused), "impact": plan.impact, "sizing": measured, "quality": quality.as_dict()}
    if plan.patched_face is not None:
        provenance.update(generation_scope="fixed interior cell patch", generated_faces=[],
                          patched_face=plan.patched_face, new_elements=sorted(numbered.shells.keys() - old.shells.keys()))
    updated["last_update"] = provenance
    updated["digest"] = rm._digest({k: v for k, v in updated.items() if k != "digest"})
    return GeneralUpdateResult(numbered, updated, frozenset(kept_nodes.values()), frozenset(kept_elems.values()),
                              plan, provenance)
