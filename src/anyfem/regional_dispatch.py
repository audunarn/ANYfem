"""Choose the owner of a regional baseline when its numbered mesh is published."""

from . import mesh_connected_update as connected
from . import mesh_general_update as general
from . import mesh_periodic_component_update as periodic
from . import mesh_curved_update as curved
from . import mesh_source_chart_update as chart
from . import mesh_surface_update as surface
from . import mesh_whole_plate_update as whole_plate
from . import mesh_structural_update as structural
from . import regional_mesh as component


def regional_refusal(project):
    """Explain an owner requirement that the regional splice cannot reissue."""
    from .model.project import source_publication_required
    faces = project.geometry.faces.values()
    families = {type(face.surface).__name__ for face in faces}
    if families and source_publication_required(project.geometry):
        return (f"Regional update unsupported for this {', '.join(sorted(families))} baseline. "
                "It requires live source-cell publication and complete verified surface ownership.")
    unsupported = families & {"RuledSurface", "ExtrudedSurface", "NurbsSurface"}
    if unsupported:
        return f"Regional update unsupported. No verified regional adapter for {', '.join(sorted(unsupported))}."
    if any(type(face.surface).__name__ == "CoonsSurface" and face.surface.has_boundaries for face in faces):
        return "Regional update unsupported for an explicit Coons chart."
    if families & {"Cylinder", "Cone"} and (len(faces) != 1 or any(face.holes for face in faces)):
        return "Regional update unsupported for split or trimmed cylindrical and conical components."
    return None


def capture_regional_baseline(project, settings, mesh):
    if periodic.periodic_ring_family(project):
        baseline = periodic.capture_periodic_baseline(project, settings, mesh)
        if baseline is not None:
            return baseline
    if general.general_family(project):
        return general.capture_general_baseline(project, settings, mesh)
    if regional_refusal(project):
        return None
    if project.beam_edges or (mesh.declared_plate_junction_edges and mesh.automatic_intersections):
        return structural.capture_structural_baseline(project, settings, mesh)
    faces = project.geometry.faces
    if len(faces) == 1:
        from anygeometry import Cone, Cylinder
        face = next(iter(faces.values()))
        capture = {Cylinder: curved.capture_curved_baseline, Cone: chart.capture_source_chart_baseline}
        baseline = capture.get(type(face.surface), surface.capture_surface_baseline)(project, settings, mesh)
        if baseline is not None:
            return baseline
    elif len(faces) > 1:
        seen = set()
        for face in faces.values():
            edges = {use.edge for use in face.loop}
            if seen.intersection(edges):
                baseline = connected.capture_connected_baseline(project, settings, mesh)
                if baseline is not None:
                    return baseline
                break
            seen.update(edges)
    return component.capture_regional_baseline(project, settings, mesh)


def plan_regional_update(project, settings, baseline, **kwargs):
    if baseline.get('schema') == periodic.SCHEMA:
        return periodic.plan_periodic_update(project, settings, baseline, **kwargs)
    if baseline.get('schema') == general.SCHEMA:
        return general.plan_general_update(project, settings, baseline, **kwargs)
    refusal = regional_refusal(project)
    if refusal:
        raise component.RegionalUpdateUnsupported(refusal)
    schema = baseline.get("schema")
    if schema == structural.SCHEMA:
        return structural.plan_structural_update(project, settings, baseline, **kwargs)
    if schema == connected.SCHEMA:
        plan = connected.plan_connected_update(project, settings, baseline, **kwargs)
    elif schema in (curved.SCHEMA, chart.SCHEMA):
        plan = curved.plan_curved_update(project, settings, baseline, **kwargs)
    elif schema == surface.SCHEMA:
        plan = whole_plate.plan_planar_update(project, settings, baseline, **kwargs)
    else:
        return component.plan_regional_update(project, settings, baseline, **kwargs)
    if not plan.is_noop and not plan.applicable:
        raise component.RegionalUpdateUnsupported(plan.description)
    return plan


def generate_regional_update(project, settings, baseline, old_mesh, *, mesh_cancellation=None, **kwargs):
    if baseline.get('schema') == periodic.SCHEMA:
        return periodic.generate_periodic_update(project, settings, baseline, old_mesh, **kwargs)
    if baseline.get('schema') == general.SCHEMA:
        return general.generate_general_update(project, settings, baseline, old_mesh,
                                              mesh_cancellation=mesh_cancellation, **kwargs)
    refusal = regional_refusal(project)
    if refusal:
        raise component.RegionalUpdateUnsupported(refusal)
    schema = baseline.get("schema")
    if schema == structural.SCHEMA:
        return structural.generate_structural_update(project, settings, baseline, old_mesh, **kwargs)
    if schema == connected.SCHEMA:
        return connected.generate_connected_update(project, settings, baseline, old_mesh, **kwargs)
    if schema in (curved.SCHEMA, chart.SCHEMA):
        return curved.generate_curved_update(project, settings, baseline, old_mesh, **kwargs)
    if schema == surface.SCHEMA:
        return whole_plate.generate_planar_update(project, settings, baseline, old_mesh, **kwargs)
    return component.generate_regional_update(project, settings, baseline, old_mesh, **kwargs)
