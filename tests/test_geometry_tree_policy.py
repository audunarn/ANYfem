"""Headless ownership policy for the intent-first model tree."""

from __future__ import annotations

from anygeometry import EntityRef, feature_entity_owners

from anyfem import commands as cmd
from anyfem.model.project import Project


def test_generated_cylinder_topology_belongs_to_its_feature():
    project = Project("intent tree")
    stack = cmd.CommandStack(project)
    manual_points = (
        stack.run(cmd.AddPoint(-2.0, 0.0, 0.0)),
        stack.run(cmd.AddPoint(-1.0, 0.0, 0.0)),
    )
    manual_line = stack.run(cmd.AddLine(*manual_points))

    cylinder = stack.run(
        cmd.AddCylinder(1.0, 2.0, circumferential_segments=8)
    )
    owners = feature_entity_owners(project.geometry)

    assert EntityRef("vertex", manual_points[0]) not in owners
    assert EntityRef("vertex", manual_points[1]) not in owners
    assert EntityRef("edge", manual_line) not in owners
    current_outputs = {
        current
        for output in cylinder.outputs.values()
        for current in project.geometry.resolve_ref(output)
    }
    assert current_outputs
    assert {owners[output] for output in current_outputs} == {
        cylinder.feature_id
    }
    assert len([ref for ref in owners if ref.kind == "vertex"]) == 32
    assert len([ref for ref in owners if ref.kind == "edge"]) == 24
    assert len([ref for ref in owners if ref.kind == "face"]) == 8


def test_later_modifier_does_not_steal_original_feature_children():
    project = Project("stable tree owner")
    stack = cmd.CommandStack(project)
    plate = stack.run(
        cmd.AddFeature(
            "generator.plate",
            name="Plate",
            parameters={"length": 2.0, "width": 1.0},
        )
    )
    face = next(
        output for output in plate.outputs.values() if output.kind == "face"
    )

    stack.run(cmd.ReverseEntity(face))
    current_face = project.geometry.resolve_ref(face)[0]

    owners = feature_entity_owners(project.geometry)
    assert owners[current_face] == plate.feature_id
