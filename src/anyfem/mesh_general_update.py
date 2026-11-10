"""Regenerate complete published surface owners when no fixed internal cut is proved.

The mesher runs on the complete source closure. An unchanged isolated face may
keep its permanent numbers only after the fresh face's ordered cells, oriented
connectivity, coordinates and source associations all match the original.
"""
from __future__ import annotations

from contextlib import nullcontext
from collections import defaultdict
from dataclasses import dataclass, replace

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
    if len(records) > 1:
        from anymesher.regional_source_faces import capture_reusable_source_faces
        baseline["reusable_faces"] = capture_reusable_source_faces(source_owner, mesh)
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

    @property
    def is_noop(self):
        return not self.regenerated

    @property
    def impact(self):
        return {"regenerated": [[f] for f in self.regenerated], "retained": [[f] for f in self.retained],
                "added": [], "removed": []}


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
    proposals = baseline.get("reusable_faces", {})
    candidates = tuple(f for f in kept if str(f) in proposals and reusable_face_for_request(
        proposals[str(f)], rm._strategy(project, settings), controls.recombine))
    if candidates:
        operation = f"Reuse the stored cells of source faces {list(candidates)} after fresh owner validation."
    else:
        operation = "Regenerate the full source mesh."
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
        for index in active_zones:
            zone = project.refinements[index]
            distances = np.linalg.norm(np.asarray(centers) - np.asarray(zone.center), axis=1)
            # Credit only a cell whose center is inside the actual constant-size
            # core. A missed footprint, including a point-only core, fails closed.
            core = distances <= zone.radius + tol
            if np.any(core):
                _require(np.any(sizes[core] <= zone.size + tol),
                         f"No generated cell in the point zone on face {face} achieves the requested size.")
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
    check("generate complete source surface mesh")
    snapshot = DocumentSession(project).snapshot()
    detached = snapshot.thaw()
    try:
        reuse = nullcontext({"reused_faces": set()})
        if plan.reuse_candidates:
            from anymesher.regional_source_faces import reuse_source_faces
            candidates = {str(f): baseline["reusable_faces"][str(f)] for f in plan.reuse_candidates}
            reuse = reuse_source_faces(detached.geometry, candidates)
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
        kept_nodes, kept_elems = _retained_ids(old, fresh, records, plan)
        allocation = raise_high_water(MeshNumbering(node_high_water, element_high_water), old)
        nodes = {**kept_nodes, **_fresh_ids(fresh.nodes.keys() - kept_nodes.keys(), allocation.node_high_water, False)}
        cells = {**kept_elems, **_fresh_ids(fresh.shells.keys() - kept_elems.keys(), allocation.element_high_water, False)}
        numbered = number_mesh_with_maps(fresh, nodes, cells, source_geometry=source)
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
    except (MeshError, rm._Ineligible, KeyError, ValueError) as error:
        raise GeneralSurfaceUnsupported(f"Full source surface generation or validation refused: {error}") from error
    check("complete general surface update")
    provenance = {"generation_scope": "selected faces" if reused else "full source mesh",
                  "generated_faces": sorted(set(detached.geometry.faces) - reused),
                  "reused_source_faces": sorted(reused), "impact": plan.impact, "sizing": measured, "quality": quality.as_dict()}
    updated["last_update"] = provenance
    updated["digest"] = rm._digest({k: v for k, v in updated.items() if k != "digest"})
    return GeneralUpdateResult(numbered, updated, frozenset(kept_nodes.values()), frozenset(kept_elems.values()),
                              plan, provenance)
