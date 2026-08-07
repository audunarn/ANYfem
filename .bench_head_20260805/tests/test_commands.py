"""The command stack: undo, redo, and ID stability across both."""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import Project, steel
from anyfem import commands as cmd
from anyfem.geometry.entities import EntityRef
from anyfem.geometry.model import GeometryError
from anyfem.model.attributes import Support
from anyfem.model.sections import BeamSection


@pytest.fixture
def project():
    project = Project(name="test")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    return project


@pytest.fixture
def stack(project):
    return cmd.CommandStack(project)


def square(stack) -> tuple[list[int], int]:
    points = [stack.run(cmd.AddPoint(x, y)) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))]
    face = stack.run(cmd.AddPlate(points))
    return points, face


def test_undo_and_redo_round_trip(stack, project):
    points, face = square(stack)
    assert len(project.geometry.faces) == 1

    stack.undo()
    assert not project.geometry.faces
    assert not project.geometry.edges

    stack.redo()
    assert list(project.geometry.faces) == [face]


def test_redo_restores_exactly_the_same_ids(stack, project):
    """Attributes reference entities by ID, so undo must not renumber."""

    square(stack)
    before = sorted(project.geometry.entity_keys())

    stack.undo()
    stack.redo()
    assert sorted(project.geometry.entity_keys()) == before


def test_extrude_undo_redo_keeps_ids(stack, project):
    start = stack.run(cmd.AddPoint(0, 0))
    end = stack.run(cmd.AddPoint(1, 0))
    edge = stack.run(cmd.AddLine(start, end))
    stack.run(cmd.Extrude([edge], (0, 0, 1)))
    before = sorted(project.geometry.entity_keys())

    stack.undo()
    assert not project.geometry.faces
    stack.redo()
    assert sorted(project.geometry.entity_keys()) == before


def test_a_new_action_clears_the_redo_branch(stack):
    stack.run(cmd.AddPoint(0, 0))
    stack.undo()
    assert stack.can_redo

    stack.run(cmd.AddPoint(5, 5))
    assert not stack.can_redo


def test_undo_on_an_empty_stack_is_harmless(stack):
    assert stack.undo() is False
    assert stack.redo() is False


def test_labels_describe_the_next_action(stack):
    stack.run(cmd.AddPoint(0, 0))
    assert stack.undo_label == "add point"
    stack.undo()
    assert stack.redo_label == "add point"
    assert stack.undo_label is None


def test_listeners_fire_on_every_change(stack):
    seen = []
    stack.add_listener(lambda: seen.append(1))
    stack.run(cmd.AddPoint(0, 0))
    stack.undo()
    stack.redo()
    assert len(seen) == 3


def test_move_point_is_reversible(stack, project):
    vertex = stack.run(cmd.AddPoint(1.0, 2.0, 3.0))
    stack.run(cmd.MovePoint(vertex, 9.0, 9.0, 9.0))
    assert project.geometry.vertex_position(vertex) == pytest.approx([9, 9, 9])

    stack.undo()
    assert project.geometry.vertex_position(vertex) == pytest.approx([1, 2, 3])


def test_assign_plate_undo_restores_the_previous_section(stack, project):
    _points, face = square(stack)
    project.add_plate_section("thick", thickness=0.020, material="S355")

    stack.run(cmd.AssignPlate(face, "plate"))
    stack.run(cmd.AssignPlate(face, "thick"))
    assert project.face_sections[face] == "thick"

    stack.undo()
    assert project.face_sections[face] == "plate"
    stack.undo()
    assert face not in project.face_sections


def test_assign_beam_and_undo(stack, project):
    project.add_beam_section(
        BeamSection(
            name="fb", profile="Flatbar", material="S355",
            flange_width=0.05, flange_thickness=0.02,
        )
    )
    start = stack.run(cmd.AddPoint(0, 0))
    end = stack.run(cmd.AddPoint(1, 0))
    edge = stack.run(cmd.AddLine(start, end))

    stack.run(cmd.AssignBeam(edge, "fb"))
    assert project.beam_edges == [edge]
    stack.undo()
    assert project.beam_edges == []


def test_supports_and_loads_undo(stack, project):
    _points, face = square(stack)
    ref = EntityRef("face", face)

    stack.run(cmd.AddSupport(Support("s", ref, {"uz": 0.0})))
    stack.run(cmd.AddPressure(ref, 5000.0))
    assert len(project.supports) == 1
    assert len(project.load_case().pressures) == 1

    stack.undo()
    assert not project.load_case().pressures
    stack.undo()
    assert not project.supports


def test_deleting_an_entity_takes_its_attributes_with_it(stack, project):
    _points, face = square(stack)
    ref = EntityRef("face", face)
    stack.run(cmd.AssignPlate(face, "plate"))
    stack.run(cmd.AddSupport(Support("s", ref, {"uz": 0.0})))
    stack.run(cmd.AddPressure(ref, 5000.0))

    stack.run(cmd.DeleteEntity(ref))
    assert face not in project.geometry.faces
    assert face not in project.face_sections
    assert not project.supports
    assert not project.load_case().pressures

    stack.undo()
    assert face in project.geometry.faces
    assert project.face_sections[face] == "plate"
    assert len(project.supports) == 1
    assert len(project.load_case().pressures) == 1


def test_deleting_a_used_line_is_refused(stack, project):
    _points, _face = square(stack)
    edge = next(iter(project.geometry.edges))

    with pytest.raises(GeometryError, match="bounds face"):
        stack.run(cmd.DeleteEntity(EntityRef("edge", edge)))


def test_deleting_a_used_point_is_refused(stack, project):
    start = stack.run(cmd.AddPoint(0, 0))
    end = stack.run(cmd.AddPoint(1, 0))
    stack.run(cmd.AddLine(start, end))

    with pytest.raises(GeometryError, match="used by edge"):
        stack.run(cmd.DeleteEntity(EntityRef("vertex", start)))


def test_arc_command_round_trips(stack, project):
    start = stack.run(cmd.AddPoint(1, 0, 0))
    via = stack.run(cmd.AddPoint(0, 1, 0))
    end = stack.run(cmd.AddPoint(-1, 0, 0))
    edge = stack.run(cmd.AddArc(start, via, end))
    assert project.geometry.edge_length(edge) == pytest.approx(np.pi)

    stack.undo()
    assert not project.geometry.edges
    stack.redo()
    assert project.geometry.edge_length(edge) == pytest.approx(np.pi)


def test_a_failed_command_leaves_the_model_untouched(stack, project):
    stack.run(cmd.AddPoint(0, 0))
    before = sorted(project.geometry.entity_keys())

    with pytest.raises(GeometryError):
        stack.run(cmd.AddLine(1, 999))

    assert sorted(project.geometry.entity_keys()) == before
    assert stack.history() == ["add point"]
