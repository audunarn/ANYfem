"""Headless contracts for the live GUI command transcript."""

from anygeometry import EntityRef

from anyfem import Project, steel
from anyfem.command_recording import command_event_to_python, command_to_python
from anyfem.commands import (
    AddCylinder,
    AddLine,
    AddPoint,
    AddMaterial,
    AddPlateSection,
    AssignPlate,
    AddPressure,
    AddSupport,
    CommandEvent,
    CommandStack,
    CompositeCommand,
)
from anyfem.document import DocumentSession
from anyfem.model.attributes import support
from anyfem.model.sections import PlateSection
from anyfem.scripting import ScriptRunner


def test_simple_command_is_replayable_python() -> None:
    assert command_to_python(AddPoint(1.0, 2.0, 3.0)) == (
        "commands.run(commands.AddPoint(x=1.0, y=2.0, z=3.0))"
    )


def test_generator_convenience_command_records_as_replayable_feature() -> None:
    command = AddCylinder(
        0.5,
        2.0,
        circumferential_segments=12,
        longitudinal_spacing=0.5,
        ring_spacing=1.0,
    )
    text = command_to_python(command)

    assert "commands.AddFeature(" in text
    assert "commands.AddCylinder(kind=" not in text
    target = DocumentSession(Project("recorded cylinder"))
    with ScriptRunner(target) as runner:
        outcome = runner.run(text)
    assert outcome.committed
    assert len(target.project.geometry.faces) == 24


def test_legacy_recorded_cylinder_constructor_remains_replayable() -> None:
    source = """
commands.run(commands.AddCylinder(
    kind='generator.cylinder',
    name='Cylinder',
    parameters={
        'radius': 0.5,
        'height': 2.0,
        'circumferential_segments': 12,
        'origin': (0.0, 0.0, 0.0),
        'axis': (0.0, 0.0, 1.0),
        'radial_direction': (1.0, 0.0, 0.0),
        'longitudinal_spacing': 0.5,
        'ring_spacing': 1.0,
    },
    label='add cylinder',
))
"""
    target = DocumentSession(Project("legacy cylinder"))

    with ScriptRunner(target) as runner:
        outcome = runner.run(source)

    assert outcome.committed
    assert len(target.project.geometry.faces) == 24
    assert len(target.project.geometry.members) == 13


def test_nested_public_values_are_replayable() -> None:
    text = command_to_python(AddPressure(EntityRef("face", 7), 12_500.0))
    assert text == (
        "commands.run(commands.AddPressure("
        "ref=commands.EntityRef(kind='face', id=7), value=12500.0))"
    )


def test_ecosystem_owned_constructor_is_explicitly_imported() -> None:
    text = command_to_python(AddMaterial(steel()))
    assert not text.startswith("# Review before replay")
    assert "__import__(" in text
    assert "commands.AddMaterial" in text
    compile(text, "<recorded command>", "exec")

    session = DocumentSession(Project())
    with ScriptRunner(session) as runner:
        outcome = runner.run(text)
    assert outcome.committed
    assert "S355" in session.project.materials


def test_composite_undo_and_redo_have_clear_transcript_lines() -> None:
    command = CompositeCommand((AddPoint(0.0, 0.0), AddPoint(1.0, 0.0)))
    text = command_to_python(command)
    assert text.startswith("commands.run_many([")
    assert "commands.AddPoint" in text
    assert "commands.run(commands.AddPoint" not in text

    session = DocumentSession(Project())
    with ScriptRunner(session) as runner:
        outcome = runner.run(text)
    assert outcome.committed
    assert len(session.project.geometry.vertices) == 2
    assert session.commands.history() == ["run script"]

    assert command_event_to_python(CommandEvent("undo", command)) == (
        "commands.undo()  # batch edit"
    )
    assert command_event_to_python(CommandEvent("redo", command)) == (
        "commands.redo()  # batch edit"
    )


def test_recorded_section_assignment_batch_replays_commands_not_results() -> None:
    project = Project("section replay")
    project.add_material(steel())
    points = project.geometry.add_points(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    face = project.geometry.add_plate(points)
    batch = CompositeCommand(
        (
            AddPlateSection(PlateSection("recorded", 0.012, "S355")),
            AssignPlate(face, "recorded"),
        ),
        label="batch edit",
    )
    text = command_to_python(batch)

    assert "commands.run(commands.AddPlateSection" not in text
    assert "commands.run(commands.AssignPlate" not in text

    session = DocumentSession(project)
    with ScriptRunner(session) as runner:
        outcome = runner.run(text)

    assert outcome.committed
    assert session.project.plate_sections["recorded"].thickness == 0.012
    assert session.project.face_sections[face] == "recorded"


def test_legacy_nested_run_batch_from_gui_transcript_remains_replayable() -> None:
    project = Project("legacy transcript")
    project.add_material(steel())
    points = project.geometry.add_points(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
         (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    face = project.geometry.add_plate(points)
    source = f"""
commands.run_many([
    commands.run(commands.AddPlateSection(
        section=commands.PlateSection(
            name='legacy', thickness=0.01, material='S355'
        )
    )),
    commands.run(commands.AssignPlate(face_id={face}, section='legacy'))
], label='batch edit')
"""

    session = DocumentSession(project)
    with ScriptRunner(session) as runner:
        outcome = runner.run(source)

    assert outcome.committed
    assert session.project.plate_sections["legacy"].thickness == 0.01
    assert session.project.face_sections[face] == "legacy"


def test_recorded_support_recreates_its_hidden_scope_on_replay() -> None:
    source_project = Project("source")
    source_stack = CommandStack(source_project)
    first = source_stack.run(AddPoint(0.0, 0.0, 0.0))
    second = source_stack.run(AddPoint(1.0, 0.0, 0.0))
    edge = source_stack.run(AddLine(first, second))
    add_support = AddSupport(
        support(EntityRef("edge", edge), name="recorded support", ux=0.0)
    )
    source_stack.run(add_support)

    # The command input remains portable; the source project's generated
    # singleton is retained only in the applied result used by undo/redo.
    assert add_support.support.region is None
    recorded = command_to_python(add_support)
    assert "RegionRef" not in recorded

    target = DocumentSession(Project("target"))
    transcript = "\n".join(
        (
            command_to_python(AddPoint(0.0, 0.0, 0.0)),
            command_to_python(AddPoint(1.0, 0.0, 0.0)),
            command_to_python(AddLine(1, 2)),
            recorded,
        )
    )
    with ScriptRunner(target) as runner:
        outcome = runner.run(transcript)

    assert outcome.committed
    assert len(target.project.supports) == 1
    applied = target.project.supports[0]
    assert applied.region is not None
    assert applied.region.id in target.project.regions


def test_stack_emits_only_successful_action_events() -> None:
    stack = CommandStack(Project())
    events = []
    stack.add_action_listener(events.append)

    stack.run(AddPoint(0.0, 0.0))
    stack.undo()
    stack.redo()

    assert [event.action for event in events] == ["run", "undo", "redo"]
    assert all(event.command.label == "add point" for event in events)


def test_recorder_listener_cannot_break_a_committed_command() -> None:
    stack = CommandStack(Project())

    def broken(_event):
        raise RuntimeError("recorder failed")

    stack.add_action_listener(broken)
    identifier = stack.run(AddPoint(0.0, 0.0))

    assert identifier in stack.project.geometry.vertices
