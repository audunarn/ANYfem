"""Pre-reported whole-plate expansion of a planar boundary mismatch."""

from dataclasses import dataclass, replace
import math

import numpy as np

from . import mesh_surface_update as surface
from . import regional_mesh as regional
from .mesh_numbering import MeshNumbering, number_full_mesh, raise_high_water


@dataclass(frozen=True)
class WholePlatePlan:
    description: str
    reason: str
    face: int
    applicable: bool = True
    is_noop: bool = False


def plan_planar_update(project, settings, baseline, *, cancellation_check=None):
    patch = surface.plan_surface_update(project, settings, baseline, cancellation_check=cancellation_check)
    if not patch.fallback_allowed or {code for code, _ in patch.blockers} != {"SURFACE_BOUNDARY_MISMATCH"}:
        return patch

    current = surface._source(project, settings)
    old = baseline["source"]
    loops = [current["outer"], *current["holes"]]
    edges = {e for loop in loops for e, _ in loop["edges"]}
    vertices = {v for loop in loops for v in loop["vertices"]}
    if (project.beam_edges or project.beam_offsets or set(project.geometry.edges) != edges
            or set(project.geometry.vertices) != vertices or any(e not in edges for e, _ in settings.overrides)):
        raise surface.SurfaceUpdateUnsupported("Whole-plate expansion requires one fully owned planar component.")
    if len(project.geometry.sheets) > 1 or any(len(s.face_use_ids) != 1 for s in project.geometry.sheets.values()):
        raise surface.SurfaceUpdateUnsupported("Whole-plate expansion requires an unambiguous source sheet occurrence.")
    if project.read_only_reason or project.sheet_join_intent_problems() or project._feature_materialization_problems():
        raise surface.SurfaceUpdateUnsupported("Whole-plate expansion requires an editable, resolved plate.")
    if current["support"] != old["support"] or current["outer"] != old["outer"]:
        # The surface planner reports outer changes before it examines edited
        # hole/size footprints. Combined edits cannot use that early result.
        if any(old[k] != current[k] for k in ("holes", "refinements", "order", "target_size")):
            raise surface.SurfaceUpdateUnsupported("The changed outer boundary also has independent interior edits.")
    if any(r["mode"] != "refine" or r["source"]["kind"] != "point" for r in current["refinements"]):
        raise surface.SurfaceUpdateUnsupported("Whole-plate expansion supports point refinement zones.")
    reason = ". ".join(message for _, message in patch.blockers)
    face = int(current["face"])
    return WholePlatePlan(f"Expand once to the whole planar plate (face {face}) because {reason}. "
                          "All mesh nodes and elements will receive new permanent IDs.", reason, face)


class _Cancellation:
    def __init__(self, check):
        self.raise_if_cancelled = check


def _check_requested_sizes(project, mesh, source, check):
    from anymesher.refinement import SizeField

    factor = 2 if mesh.order == "quadratic" else 1
    for edge, count in source["overrides"]:
        if len(mesh.nodes_of_edge.get(edge, ())) != factor * count + 1:
            raise surface.SurfaceUpdateError(f"The generated plate does not honor the divisions of edge {edge}.",
                                            code="SURFACE_EDGE_DIVISIONS_UNMET")
    # The full owner can choose any admitted all-quad strategy. Apply the
    # patch route's achieved-size check to every point zone on the new mesh.
    chart = surface._Chart(source)
    target = float.fromhex(source["target_size"])
    field = SizeField(project.geometry, target, project.refinements, active_faces=[source["face"]]).face_view(source["face"])
    tolerance = float.fromhex(source["support"]["tolerance"])
    for r in source["refinements"]:
        check("verify whole plate point refinement")
        if float.fromhex(r["size"]) >= target:
            continue
        center = np.array([float.fromhex(v) for v in r["source"]["xyz"]])
        cx, cy = chart.to2(center)
        radius = surface._influence_radius(r, target)
        gap = abs(chart.height(center))
        if gap >= radius:
            continue
        radius = math.sqrt(radius * radius - gap * gap) / math.cos(math.pi / surface._DISC_SIDES)
        polygon = [(cx + radius * math.cos(a), cy + radius * math.sin(a))
                   for a in np.linspace(0, 2 * math.pi, surface._DISC_SIDES, endpoint=False)]
        wanted = float(field.size_at(np.asarray([chart.to3((cx, cy))]))[0])
        for conn in mesh.quads.values():
            xyz = [mesh.nodes[n] for n in surface._corners(conn)]
            if (surface._overlap([chart.to2(p) for p in xyz], polygon)
                    and max(np.linalg.norm(a - b) for a, b in zip(xyz, xyz[1:] + xyz[:1])) <= wanted + tolerance):
                break
        else:
            raise surface.SurfaceUpdateError(f"No generated quad in the edited size zone meets its {wanted:g} edge target.",
                                            code="SURFACE_LOCAL_SIZE_UNMET")


def generate_planar_update(project, settings, baseline, old_mesh, *, node_high_water, element_high_water,
                           cancellation_check=None):
    from .document import DocumentSession
    from .mesh_jobs import run_mesh_job

    check = regional._check_fn(cancellation_check)
    plan = plan_planar_update(project, settings, baseline, cancellation_check=check)
    if not isinstance(plan, WholePlatePlan):
        return surface.generate_surface_update(project, settings, baseline, old_mesh, node_high_water=node_high_water,
                                               element_high_water=element_high_water, cancellation_check=check)
    old = regional._decode(old_mesh)
    if (str(old.geometry_model_id) != str(project.geometry.model_id)
            or surface._binding(old, plan.face) != baseline["mesh"]
            or surface._coverage(old, baseline["source"]) or surface._associations(old, baseline["source"])):
        raise surface.SurfaceUpdateError("The parent mesh does not match the planar baseline.",
                                        code="SURFACE_OLD_MESH_MISMATCH")
    check("whole plate expansion")
    snapshot = DocumentSession(project).snapshot()
    detached = snapshot.thaw()
    full = run_mesh_job(detached, snapshot.revision.model_hash, replace(settings, regional=None),
                        lambda *_: None, _Cancellation(check))
    candidate = full.mesh
    if (str(candidate.geometry_model_id) != str(project.geometry.model_id)
            or candidate.geometry_revision != project.geometry.revision
            or candidate.hybrid_diagnostics.get("automation", {}).get("status") == "inspection_only"):
        raise surface.SurfaceUpdateError("The generated plate cannot be published against the current source revision.")
    check("verify whole plate expansion")
    allocation = raise_high_water(MeshNumbering(node_high_water, element_high_water), old)
    numbered = number_full_mesh(candidate, allocation, source_geometry=project.geometry)
    regional._validate_quality(project, numbered, settings, check)
    updated = surface.capture_surface_baseline(project, settings, numbered)
    if updated is None:
        raise surface.SurfaceUpdateError("The generated component failed planar all-quad source coverage or associations.")
    _check_requested_sizes(project, numbered, updated["source"], check)
    provenance = {"operation": "anyfem.whole_plate_update", "face": plan.face, "reason": plan.reason,
                  "retired_nodes": len(old.nodes), "retired_elements": len(old.quads),
                  "retained_nodes": 0, "retained_elements": 0}
    updated["last_update"] = provenance
    check("complete whole plate expansion")
    return surface.SurfaceUpdateResult(numbered, updated, frozenset(), frozenset(), plan, provenance)
