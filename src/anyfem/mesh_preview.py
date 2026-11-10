"""Viewport owners for an explicit regional update preview."""

from dataclasses import dataclass

from anygeometry import EntityRef

from .selection import MeshEntityRef


@dataclass(frozen=True)
class MeshUpdatePreview:
    description: str
    owners: tuple[EntityRef | MeshEntityRef, ...]
    review_prepared_cells: bool = False


def mesh_update_preview(plan, baseline, parent_mesh, parent_id):
    from .mesh_connected_update import ConnectedPlan
    from .mesh_general_update import GeneralPlan
    from .mesh_curved_update import CurvedPlan
    from .mesh_source_chart_update import SourceChartPlan
    from .mesh_structural_update import StructuralPlan
    from .mesh_surface_update import SurfacePlan
    from .mesh_whole_plate_update import WholePlatePlan
    from .mesh_periodic_component_update import PeriodicPlan
    from .regional_mesh import RegionalPlan

    description = plan.description
    faces, edges, removed_faces = set(), set(), set()
    if not plan.is_noop:
        if isinstance(plan, RegionalPlan):
            for previous_index, group in plan.changed:
                faces.update(group)
                previous = baseline["components"][previous_index]["faces"]
                removed_faces.update(set(previous) - set(group))
            for group in plan.added:
                faces.update(group)
            for group in plan.removed_faces:
                removed_faces.update(group)
        elif isinstance(plan, ConnectedPlan):
            # Includes neighbours selected by the seeding extension.
            faces.update(plan.affected_faces)
        elif isinstance(plan, StructuralPlan):
            for index in plan.changed:
                record = plan.records[index]
                faces.update(record["faces"])
                edges.update(record["members"].values())
        elif isinstance(plan, GeneralPlan):
            faces.update(plan.regenerated)
        elif isinstance(plan, PeriodicPlan):
            faces.update(plan.faces)
        elif isinstance(plan, WholePlatePlan):
            faces.add(plan.face)
        elif isinstance(plan, (SurfacePlan, CurvedPlan, SourceChartPlan)):
            # Local cell membership is established during generation. Only the
            # affected source face has been selected at confirmation time.
            faces.add(baseline["source"]["face"])
            description += "\n\nCell selection occurs during generation. Review the selected parent elements before publication."

    owners = [EntityRef("face", int(fid)) for fid in sorted(faces)]
    owners.extend(EntityRef("edge", int(eid)) for eid in sorted(edges))
    retired = {int(e) for fid in removed_faces for e in parent_mesh.elements_of_face.get(fid, ())}
    owners.extend(MeshEntityRef("element", eid, parent_id) for eid in sorted(retired))
    local = isinstance(plan, (SurfacePlan, CurvedPlan, SourceChartPlan)) and not plan.is_noop
    return MeshUpdatePreview(description, tuple(owners), local)


def prepared_cell_preview(parent_id, old_mesh, new_mesh, retained_ids, provenance):
    from .mesh_numbering import element_numbers

    parent, child = element_numbers(old_mesh), element_numbers(new_mesh)
    retained = set(retained_ids)
    if retained != parent.intersection(child):
        raise ValueError("Prepared patch numbers differ from its retained element receipt")
    replaced = parent - retained
    description = (f"Replace {len(replaced)} highlighted parent elements with {len(child - retained)} new elements. "
                   f"{len(retained)} parent elements keep their IDs.")
    if provenance.get("growth_rings"):
        description += f" The patch expanded by {provenance['growth_rings']} ring(s)."
    return MeshUpdatePreview(description, tuple(MeshEntityRef("element", e, parent_id) for e in sorted(replaced)))
