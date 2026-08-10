"""The command stack: undo, redo, and ID stability across both."""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import Project, steel
from anyfem import commands as cmd
from anyfem.geometry.entities import EntityRef
from anyfem.geometry.model import GeometryError
from anyfem.mesh import refine_around
from anyfem.model import BeamSection, Mass, plate_mode
from anyfem.model.attributes import Support


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
    project.load_case().add_surface_traction(ref, (10.0, 20.0, 30.0))
    project.add_mass(Mass(ref, 50.0, "payload"))
    project.add_imperfection(plate_mode(ref, amplitude=0.001))
    project.add_refinement(refine_around(ref, size=0.1, radius=0.2))

    stack.run(cmd.DeleteEntity(ref))
    assert face not in project.geometry.faces
    assert face not in project.face_sections
    assert not project.supports
    assert not project.load_case().pressures
    assert not project.load_case().surface_tractions
    assert not project.masses
    assert not project.imperfections
    assert not project.refinements

    stack.undo()
    assert face in project.geometry.faces
    assert project.face_sections[face] == "plate"
    assert len(project.supports) == 1
    assert len(project.load_case().pressures) == 1
    assert len(project.load_case().surface_tractions) == 1
    assert len(project.masses) == 1
    assert len(project.imperfections) == 1
    assert len(project.refinements) == 1

    stack.redo()
    assert face not in project.geometry.faces
    assert not project.face_sections
    assert not project.supports
    assert not project.load_case().pressures
    assert not project.load_case().surface_tractions
    assert not project.masses
    assert not project.imperfections
    assert not project.refinements

    stack.undo()
    assert project.face_sections[face] == "plate"
    assert len(project.supports) == 1
    assert len(project.load_case().pressures) == 1
    assert len(project.load_case().surface_tractions) == 1
    assert len(project.masses) == 1
    assert len(project.imperfections) == 1
    assert len(project.refinements) == 1


def test_delete_undo_restores_groups_tags_and_lineage(stack, project):
    _points, face = square(stack)
    ref = EntityRef("face", face)
    project.geometry.add_to_group("shell", [ref])
    project.geometry.tag(ref, "deck", "primary")

    # Keep a pre-existing lineage entry and current edit log to prove that
    # undo restores more than the deleted entity dictionary entry.
    loose = project.geometry.add_point(10.0, 10.0, 0.0)
    loose_ref = EntityRef("vertex", loose)
    project.geometry.remove_vertex(loose)
    history_before = project.geometry.replacement_history()
    log_before = project.geometry.replacement_log()

    stack.run(cmd.DeleteEntity(ref))

    assert project.geometry.resolve_ref(ref) == ()
    assert project.geometry.group("shell", resolve=False) == ()
    assert project.geometry.tags_for(ref) == ()
    assert project.geometry.replacement_log() == [(ref, ())]
    assert project.geometry.replacement_history()[ref] == ()

    stack.undo()

    assert project.geometry.resolve_ref(ref) == (ref,)
    assert project.geometry.group("shell", resolve=False) == (ref,)
    assert project.geometry.tags_for(ref) == ("deck", "primary")
    assert project.geometry.replacement_history() == history_before
    assert project.geometry.replacement_log() == log_before
    assert project.geometry.resolve_ref(loose_ref) == ()

    stack.redo()
    assert project.geometry.resolve_ref(ref) == ()
    assert project.geometry.group("shell", resolve=False) == ()
    assert project.geometry.replacement_log() == [(ref, ())]


def test_deleting_a_used_line_is_refused(stack, project):
    _points, _face = square(stack)
    edge = next(iter(project.geometry.edges))

    with pytest.raises(GeometryError, match="bounds face"):
        stack.run(cmd.DeleteEntity(EntityRef("edge", edge)))


def test_rejected_delete_restores_the_active_geometry_transaction(stack, project):
    _points, _face = square(stack)
    edge = next(iter(project.geometry.edges))
    ref = EntityRef("edge", edge)
    project.geometry.add_to_group("boundary", [ref])
    project.geometry.tag(ref, "clamped")
    loose = project.geometry.add_point(20.0, 20.0, 0.0)
    project.geometry.remove_vertex(loose)
    history_before = project.geometry.replacement_history()
    log_before = project.geometry.replacement_log()

    with pytest.raises(GeometryError, match="bounds face"):
        stack.run(cmd.DeleteEntity(ref))

    assert project.geometry.group("boundary", resolve=False) == (ref,)
    assert project.geometry.tags_for(ref) == ("clamped",)
    assert project.geometry.replacement_history() == history_before
    assert project.geometry.replacement_log() == log_before


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
