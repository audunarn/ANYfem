"""Headless editable face-sketch workflow."""

from __future__ import annotations

import numpy as np
import pytest
from anygeometry import EntityRef, SketchDefinition, face_sketch_plane, to_dict

from anyfem import Project, steel
from anyfem import commands as cmd
from anyfem.geometry.sketching import FaceSketchTask
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.solve.build import build_fe_model


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
    extrusion_faces = [
        reference.id for key, reference in feature.outputs.items()
        if key.startswith("extrusion/face/")
    ]
    project.assign_plates(extrusion_faces, "plate")

    mesh = project.generate_mesh(0.5)
    built = build_fe_model(
        project, mesh, load_case=None, require_loads=False, require_supports=False
    )

    assert mesh.automatic_shell_connections == 8
    assert len(mesh.couplings) == 8
    assert len(built.fe_model.mesh.elements) == (
        len(mesh.shells) + len(mesh.couplings)
    )
