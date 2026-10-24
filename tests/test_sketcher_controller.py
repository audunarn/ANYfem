"""Exercise sketch editing and real document commands without a GUI toolkit."""
import os
import subprocess
import sys

import numpy as np
import pytest
from anygeometry import SketchDefinition, SketchEntity, SketchWire, plane_sketch_plane
from anyfem import Project, commands
from anyfem.geometry.sketching import FaceSketchTask as LegacyTask
from anyfem.model.coordinates import GLOBAL_COORDINATES
from anyfem.model.workplanes import Workplane
from anyfem.sketcher import FaceSketchTask, SketchController, SketchPorts


PLANE = plane_sketch_plane((0, 0, 0), (0, 0, 1), (1, 0, 0))


def line_task():
    definition = SketchDefinition(points={"a": (1., 1.), "b": (3., 1.)}, version=2, closed=False,
        entities=(SketchEntity("line", "line", ("a", "b")),),
        wires=(SketchWire("profile", ("line",), False),))
    return FaceSketchTask(PLANE, .05, definition)


def harness(task=None):
    events = {key: [] for key in ("begin", "end", "changed", "preview", "accepted", "finish")}
    project = Project()
    stack = commands.CommandStack(project)
    ports = SketchPorts(
        begin_preview=lambda *args: events["begin"].append(args),
        end_preview=lambda: events["end"].append(True),
        changed=lambda *args: events["changed"].append(args),
        preview=lambda: events["preview"].append(True),
        execute=stack.run,
        accepted=lambda value: events["accepted"].append(value),
        finish_requested=lambda: events["finish"].append(True),
    )
    controller = SketchController(ports)
    controller.begin(task or line_task(), Workplane(), {"global": GLOBAL_COORDINATES})
    return controller, events, project, stack


def test_import_is_toolkit_neutral_and_legacy_identity_is_preserved():
    assert FaceSketchTask is LegacyTask
    result = subprocess.run([sys.executable, "-c",
        "import sys; from anyfem.sketcher import SketchController; "
        "assert not any(k.startswith(('PySide6','tkinter')) for k in sys.modules)"],
        capture_output=True, text=True, timeout=20, env=os.environ.copy())
    assert result.returncode == 0, result.stderr


def test_controller_owns_drag_threshold_preview_and_single_undo():
    controller, events, _project, _stack = harness()
    original = controller.task.definition(0).to_parameters()
    controller.press((10, 10), point_key="b")
    changed = len(events["changed"])
    controller.move((4., 2., 0.), position=(12, 12), left_down=True, threshold=10)
    assert not controller.session.dragging
    assert controller.task.definition(0).to_parameters() == original
    controller.move((4., 2., 0.), position=(30, 30), left_down=True, threshold=10)
    controller.move((5., 2., 0.), position=(40, 30), left_down=True, threshold=10)
    assert len(events["changed"]) == changed  # Pointer preview must not rebuild the form.
    assert events["preview"]
    controller.release()
    assert len(controller.task._undo) == 1
    np.testing.assert_allclose(controller.task.points[-1], (5., 2., 0.))
    controller.undo()
    assert controller.task.definition(0).to_parameters() == original
    controller.undo(redo=True)
    np.testing.assert_allclose(controller.task.points[-1], (5., 2., 0.))


def test_escape_rolls_back_drag_without_canceling_the_sketch():
    controller, events, _project, _stack = harness()
    before = controller.task.definition(0).to_parameters()
    controller.press((0, 0), point_key="b")
    controller.move((6., 3., 0.), position=(30, 30), left_down=True, threshold=10)
    controller.escape()
    assert controller.active and controller.task.tool == "select"
    assert controller.task.definition(0).to_parameters() == before
    assert not events["end"]
    assert controller.cancel() and not controller.active
    assert len(events["end"]) == 1
    assert not controller.cancel() and len(events["end"]) == 1


def test_conflicting_dimension_keeps_state_and_undo_history():
    controller, _events, _project, _stack = harness()
    for key in ("a", "b"):
        controller.select(key)
        controller.constraint("fixed")
    controller.select("a")
    controller.select("b", shift=True)
    before = controller.task.definition(0).to_parameters()
    undo_count = len(controller.task._undo)
    with pytest.raises(ValueError):
        controller.constraint("distance", 5.)
    assert controller.task.definition(0).to_parameters() == before
    assert len(controller.task._undo) == undo_count


def test_failed_finish_retains_draft_then_commits_once_and_undoes():
    controller, events, project, stack = harness()
    task = controller.task
    before = task.definition(0).to_parameters()
    def refuse(_command):
        raise ValueError("Rejected by document")
    controller.ports.execute = refuse
    with pytest.raises(ValueError, match="Rejected by document"):
        controller.finish("Line", closed=False)
    assert controller.task is task
    assert task.definition(0).to_parameters() == before
    assert not events["end"] and not events["accepted"]
    controller.ports.execute = stack.run
    feature = controller.finish("Line", closed=False)
    assert not controller.active
    assert len(events["end"]) == len(events["accepted"]) == 1
    assert feature.feature_id == controller.session.last_sketch_feature_id
    assert len(project.geometry.edges) == 1
    stack.undo()
    assert not project.geometry.edges


def test_enter_finishes_tool_before_requesting_document_finish():
    task = FaceSketchTask(PLANE, .05)
    controller, events, _project, _stack = harness(task)
    controller.point(task.plane.world((1., 1.)))
    controller.point(task.plane.world((3., 1.)))
    controller.point(task.plane.world((3., 3.)))
    controller.enter()
    assert task.entities and not task.pending and not events["finish"]
    controller.enter()
    assert events["finish"] == [True]
    assert controller.active


def test_read_only_rejects_edits_without_discarding_the_session():
    controller, events, _project, _stack = harness()
    before = controller.task.definition(0).to_parameters()
    controller.ports.can_edit = lambda: False
    for edit in (lambda: controller.choose_tool("line"),
                 lambda: controller.point((4., 2., 0.)),
                 lambda: controller.finish("Line")):
        with pytest.raises(PermissionError):
            edit()
        assert controller.active
        assert controller.task.definition(0).to_parameters() == before
    assert not events["end"] and not events["accepted"]
    assert controller.cancel()
