"""Headless editable face-sketch workflow."""

from __future__ import annotations

import numpy as np
import pytest
from anygeometry import (
    ConnectionIntent,
    EntityRef,
    ImprintOperation,
    IntersectionDimension,
    IntersectionKind,
    Plane,
    SketchDefinition,
    apply_imprint,
    face_sketch_plane,
    plan_imprint,
    query_intersection,
    to_dict,
)
from anymesher.serialize import mesh_to_dict

from anyfem import Project, steel
from anyfem import commands as cmd
from anyfem.geometry.sketching import FaceSketchTask
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.solve.build import build_fe_model
from anysolver import audit_constraints


def _project() -> tuple[Project, int]:
    project = Project()
    points = project.geometry.add_points(((0, 0, 0), (4, 0, 0), (4, 3, 0), (0, 3, 0)))
    return project, project.geometry.add_plate(points)


def test_face_sketch_task_allows_outside_points_dimensions_and_edge_coincidence():
    project, face = _project()
    plane = face_sketch_plane(project.geometry, face)
    task = FaceSketchTask(plane, snap_tolerance=0.05)
    task.add(plane.world((0.02, 0.01)))
    task.add(plane.world((1.4, 0.2)))
    task.add(plane.world((5.0, 1.0)))  # outside the supporting plate is valid
    task.add_distance(0, 1, 1.0)

    definition = task.definition(2.0)

    assert any(item.kind in ("on_edge", "on_vertex") for item in definition.constraints)
    assert any(item.kind == "distance" for item in definition.constraints)
    assert definition.points["p3"] == pytest.approx((5.0, 1.0))


def test_add_edit_undo_sketch_is_one_atomic_feature_command():
    project, face = _project()
    stack = cmd.CommandStack(project)
    definition = SketchDefinition(
        points={"p1": (0.5, 0.5), "p2": (1.5, 0.5), "p3": (1.5, 1.5)},
        path=("p1", "p2", "p3"),
        extrusion=1.0,
    )

    feature = stack.run(cmd.AddSketch(EntityRef("face", face), definition, "Bracket sketch"))

    assert feature.kind == "geometry.sketch.extrude"
    assert len([key for key in feature.outputs if key.startswith("extrusion/face/")]) == 3
    before_edit = to_dict(project.geometry)
    changed = SketchDefinition(
        points={**definition.points, "p3": (2.0, 1.5)},
        path=definition.path,
        extrusion=2.0,
    )
    stack.run(cmd.EditFeature(feature.feature_id, parameters=changed.to_parameters()))
    current = project.geometry.features.get(feature.feature_id)
    point = current.outputs["point/p3"]
    np.testing.assert_allclose(project.geometry.vertices[point.id].position, (2.0, 1.5, 0.0))

    stack.undo()
    assert to_dict(project.geometry) == before_edit
    stack.undo()
    assert not project.geometry.features.records


def test_sketch_support_survives_unrelated_feature_delete_regeneration():
    """An open sketch may retain the pre-regeneration materialized face ID."""

    project = Project()
    stack = cmd.CommandStack(project)
    vertex_ids = [
        stack.run(cmd.AddPoint(*position))
        for position in ((0, 0, 0), (4, 0, 0), (4, 3, 0), (0, 3, 0))
    ]
    face_id = stack.run(cmd.AddPlate(vertex_ids))
    stale_support = EntityRef("face", face_id)

    # This disposable diagonal mirrors the tree item deleted immediately
    # before Apply in the reported GUI workflow.  Its deletion regenerates
    # every materialized entity and therefore changes the numeric face ID.
    stack.run(cmd.AddLine(vertex_ids[0], vertex_ids[2]))
    diagonal_feature_id = project.geometry.features.records[-1].feature_id
    stack.run(cmd.DeleteFeature(diagonal_feature_id))
    assert stale_support not in tuple(
        EntityRef("face", face_id) for face_id in project.geometry.faces
    )

    definition = SketchDefinition(
        points={"p1": (0.5, 0.5), "p2": (1.5, 0.5), "p3": (1.0, 1.5)},
        path=("p1", "p2", "p3"),
        extrusion=1.0,
    )
    feature = stack.run(cmd.AddSketch(stale_support, definition))

    assert feature.state == "ok"
    assert len(
        [key for key in feature.outputs if key.startswith("extrusion/face/")]
    ) == 3


def test_sketch_intent_round_trips_in_anyfem_project_format():
    project, face = _project()
    definition = SketchDefinition(
        points={"p1": (-1.0, 0.0), "p2": (1.0, 0.0), "p3": (1.0, 1.0)},
        path=("p1", "p2", "p3"),
        extrusion=0.5,
    )
    cmd.CommandStack(project).run(
        cmd.AddSketch(EntityRef("face", face), definition, "Outside profile")
    )

    restored = project_from_dict(project_to_dict(project))

    feature = restored.geometry.features.records[-1]
    assert feature.kind == "geometry.sketch.extrude"
    assert feature.parameters["points"]["p1"] == [-1.0, 0.0]
    assert feature.parameters["extrusion"] == pytest.approx(0.5)


def test_interior_sketch_extrusion_meshes_as_connected_shell_t_junction():
    project, face = _project()
    project.add_material(steel())
    project.add_plate_section("plate", 0.01, "S355")
    project.assign_plate(face, "plate")
    definition = SketchDefinition(
        points={"p1": (1.0, 1.0), "p2": (2.0, 1.0), "p3": (2.0, 2.0), "p4": (1.0, 2.0)},
        path=("p1", "p2", "p3", "p4"),
        extrusion=1.0,
    )
    feature = cmd.CommandStack(project).run(
        cmd.AddSketch(EntityRef("face", face), definition, "Welded box")
    )
    extrusion_faces = sorted(
        reference.id for key, reference in feature.outputs.items()
        if key.startswith("extrusion/face/")
    )
    project.assign_plates(extrusion_faces, "plate")

    geometry = project.geometry
    assert all(
        isinstance(geometry.faces[face_id].surface, Plane)
        for face_id in extrusion_faces
    )
    def face_owner(face_id: int) -> tuple[int, int]:
        uses = tuple(
            face_use
            for face_use in geometry.face_uses.values()
            if face_use.face_id == face_id
        )
        assert len(uses) == 1
        face_use = uses[0]
        part_id = geometry.owner_part("face_use", face_use.id)
        assert part_id is not None
        return face_use.sheet_id, part_id

    support_sheet, support_part = face_owner(face)
    wall_owners = {face_owner(face_id) for face_id in extrusion_faces}
    assert len(wall_owners) == 1
    wall_sheet, wall_part = wall_owners.pop()
    assert wall_sheet != support_sheet
    assert wall_part != support_part

    result = query_intersection(
        geometry,
        geometry.handle("face", face),
        geometry.handle("face", extrusion_faces[0]),
    )
    plan = plan_imprint(geometry, result, policy=ConnectionIntent.CONNECT)
    assert result.kind is IntersectionKind.CROSS
    assert result.dimension is IntersectionDimension.CURVE
    assert plan.operation is ImprintOperation.FACE_IMPRINT
    application = apply_imprint(
        geometry, plan, policy=ConnectionIntent.CONNECT
    )
    assert application.face_intersection is not None
    shared_edge = application.face_intersection.edge.id
    face_use_ids = geometry.face_uses_using_edge(shared_edge)
    assert {
        geometry.face_uses[face_use_id].sheet_id
        for face_use_id in face_use_ids
    } == {support_sheet, wall_sheet}
    assert {
        geometry.face_uses[geometry.coedges[coedge_id].face_use_id].sheet_id
        for coedge_id in geometry.coedges_using_edge(shared_edge)
    } == {support_sheet, wall_sheet}
    assert geometry.validate_topology() == ()

    mesh = project.generate_mesh(0.5)
    repeated = project.generate_mesh(0.5)
    assert mesh_to_dict(repeated) == mesh_to_dict(mesh)
    shared_nodes = set(mesh.nodes_of_edge[shared_edge])
    assert shared_nodes
    for sheet_id in (support_sheet, wall_sheet):
        sheet_nodes = {
            node_id
            for element_id in mesh.elements_of_sheet[sheet_id]
            for node_id in mesh.shells[element_id]
        }
        assert shared_nodes <= sheet_nodes
    assert mesh.automatic_shell_connections == 0
    assert not mesh.couplings
    built = build_fe_model(
        project, mesh, load_case=None, require_loads=False, require_supports=False
    )

    assert len(built.fe_model.mesh.elements) == len(mesh.shells)


def test_separately_extruded_adjacent_plate_edges_have_acyclic_shell_mpcs():
    project, face = _project()
    project.add_material(steel())
    project.add_plate_section("plate", 0.01, "S355")
    edges = [item.edge for item in project.geometry.faces[face].loop]
    first_wall = project.geometry.extrude((edges[0],), (0, 0, 1))[0]
    second_wall = project.geometry.extrude((edges[1],), (0, 0, 1))[0]
    project.assign_plates((face, first_wall, second_wall), "plate")

    mesh = project.generate_mesh(0.25)
    built = build_fe_model(
        project, mesh, load_case=None, require_loads=False, require_supports=False
    )
    report = audit_constraints(built.fe_model)

    assert mesh.automatic_shell_connections >= 1
    assert not [issue for issue in report.issues if issue.code == "CONSTRAINT003"]
