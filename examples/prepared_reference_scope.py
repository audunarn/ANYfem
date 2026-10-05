"""Portable child-pressure counterexample, without meshing or solving.

Run against the coordinated installed candidates. This fixture demonstrates
input scope visibility only and provides no cell-membership qualification.
"""
import json

from anygeometry import GeometryModel, apply_intersections, plan_intersections
from anygeometry.entities import EntityRef
from anyfem import Project
from anyfem.model.regions import ManualRegion, Region, RegionDomain, RegionRef
from anyfem.prepared_reference_scope import (
    PreparedReferenceScopeError, assert_authored_root_face_scope,
    query_prepared_project_reference_scope, validate_prepared_project_reference_scope,
)


def build_child_pressure_fixture():
    geometry = GeometryModel()
    root = geometry.add_plate(geometry.add_points(
        ((0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0))))
    cutter = geometry.add_plate(geometry.add_points(
        ((1, -1, -1), (1, 2, -1), (1, 2, 1), (1, -1, 1))))
    apply_intersections(geometry,
        plan_intersections(geometry, (root, cutter), policy="connect"), policy="connect")
    children = tuple(ref.id for ref in geometry.resolve_ref(EntityRef("face", root)))
    # Coordinates choose the synthetic fixture's left side, never map cells.
    left = next(face for face in children if all(
        geometry.vertex_position(geometry.edges[use.edge].start)[0] <= 1
        for use in geometry.faces[face].loop))
    project = Project(geometry=geometry)
    region = Region("Left child pressure", RegionDomain.GEOMETRY, "face",
                    ManualRegion((EntityRef("face", left),)))
    project.regions.add(region)
    project.load_case().add_pressure(EntityRef("face", left), 1200,
                                    region=RegionRef(region.id))
    return project, root, children, left


if __name__ == "__main__":
    project, root, children, left = build_child_pressure_fixture()
    manifest = query_prepared_project_reference_scope(project, project.geometry)
    validate_prepared_project_reference_scope(project, project.geometry, manifest)
    try:
        assert_authored_root_face_scope(manifest, root, children)
    except PreparedReferenceScopeError as error:
        print(json.dumps({"root": root, "children": children, "pressure_child": left,
            "required_boundary_edges": manifest.required_boundary_edge_ids,
            "root_only_refusal": str(error)}, sort_keys=True))
    else:
        raise AssertionError("child-local pressure incorrectly allowed root-only associations")
