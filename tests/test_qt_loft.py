"""A preview is disposable and only a current validated preview may commit."""
import os
import pytest
if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

from anygeometry import EntityRef
from anyfem import commands
from anyfem.ui.qt.loft import LoftDialog
from test_qt_geometry_actions import qapp, window
from test_loft_workflow import profiles


def prepared(window):
    first, second = profiles(window.project)
    window.show_geometry()
    dialog = LoftDialog(window)
    for index, edge in enumerate((first, second)):
        window.selection.restore((EntityRef("edge", edge),))
        dialog.capture(index)
    return dialog


def test_preview_cancel_is_disposable(window, qapp):
    dialog = prepared(window)
    before = window.project.geometry.revision
    dialog.preview()
    assert dialog.apply_button.isEnabled(), dialog.status.text()
    assert not window.project.geometry.faces
    assert window.project.geometry.revision == before
    assert window.viewport._scene.faces
    assert any(line.annotation for line in window.viewport._scene.lines)
    assert window.viewport._scene.arrows
    dialog.reject()
    assert not window.project.geometry.faces
    assert not window.viewport._scene.faces
    dialog.deleteLater()


def test_apply_undo_redo_and_edit(window, qapp):
    dialog = prepared(window)
    dialog.preview()
    assert dialog.apply_button.isEnabled(), dialog.status.text()
    dialog.apply()
    assert window.project.geometry.faces
    feature_id = window.project.geometry.features.records[-1].feature_id
    window.undo()
    assert not window.project.geometry.faces
    window.redo()
    assert window.project.geometry.faces
    editor = LoftDialog(window)
    editor.feature.setCurrentIndex(editor.feature.findData(feature_id))
    editor.flip.setChecked(True)
    editor.preview()
    assert editor.apply_button.isEnabled(), editor.status.text()
    editor.apply()
    assert window.project.geometry.features.get(feature_id).parameters["flip_normal"]
    dialog.deleteLater()
    editor.deleteLater()


def test_changed_document_and_changed_controls_invalidate(window, qapp):
    dialog = prepared(window)
    dialog.preview()
    assert dialog.apply_button.isEnabled(), dialog.status.text()
    window.run(commands.AddPoint(20, 20, 20))
    dialog.apply()
    assert not window.project.geometry.faces
    assert "document changed" in dialog.status.text()
    dialog.preview()
    dialog.flip.setChecked(True)
    assert dialog.command is None
    assert not dialog.apply_button.isEnabled()
    dialog.reject()
    dialog.deleteLater()


def test_geometry_navigation_constructs_selection_once(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    first, second = profiles(window.project)
    window.selection.restore((EntityRef("edge", first), EntityRef("edge", second)))
    panel = window.panels["Geometry"]
    panel.choice.setCurrentIndex(panel.choice.findData("Loft"))
    assert panel.fields == {} and panel.apply.text() == "Construct"
    QTest.mouseClick(panel.apply, Qt.LeftButton)
    qapp.processEvents()
    assert window.project.geometry.faces
    assert not getattr(window, "_loft_dialog", None)
    window.undo()
    assert not window.project.geometry.faces


def test_read_only_preview_refuses(window, qapp):
    from types import SimpleNamespace
    dialog = prepared(window)
    original = dialog.app
    dialog.app = SimpleNamespace(session=SimpleNamespace(read_only=True))
    dialog.preview()
    assert not dialog.apply_button.isEnabled()
    assert "read-only" in dialog.status.text()
    dialog.app = original
    dialog.reject()
    dialog.deleteLater()


def test_replaced_project_cannot_commit_old_preview(window, qapp):
    dialog = prepared(window)
    dialog.preview()
    assert dialog.apply_button.isEnabled(), dialog.status.text()
    window.new_project()
    dialog.apply()
    assert not window.project.geometry.faces
    assert "document changed" in dialog.status.text()
    dialog.reject()
    dialog.deleteLater()


def test_automatic_direction_is_frozen_and_can_be_overridden(window, qapp):
    geometry = window.project.geometry
    ids = [geometry.add_point(*p) for p in ((0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0))]
    a, b = geometry.add_line(*ids[:2]), geometry.add_line(*ids[2:])
    window.show_geometry()
    dialog = LoftDialog(window)
    dialog.profiles[0].setText(str(a))
    dialog.profiles[1].setText(str(b))
    dialog.preview()
    assert dialog.apply_button.isEnabled(), dialog.status.text()
    assert dialog.command.reverse_second is True
    assert dialog.reversals[1].isChecked()
    dialog.reversals[1].setChecked(False)
    assert not dialog.auto_match.isChecked()
    assert dialog.command is None
    dialog.reject()
    dialog.deleteLater()


@pytest.mark.parametrize("closed", [False, True])
def test_sketch_chain_selection(window, qapp, closed):
    from anygeometry import SketchDefinition
    points = {"p1": (0, 0), "p2": (2, 0)}
    if closed:
        points.update(p3=(2, 2), p4=(0, 2))
    definition = SketchDefinition(points=points, path=tuple(points), closed=closed)
    for z in (0, 1):
        window.run(commands.AddPlaneSketch(definition, (0, 0, z), (0, 0, 1), (1, 0, 0)))
    dialog = LoftDialog(window)
    assert dialog.sketches[0].count() == 3
    dialog.sketches[0].setCurrentIndex(1)
    dialog.sketches[1].setCurrentIndex(2)
    dialog.preview()
    assert dialog.apply_button.isEnabled(), dialog.status.text()
    dialog.apply()
    assert window.project.geometry.features.records[-1].kind == "geometry.loft"
    dialog.deleteLater()


def test_raw_vertex_edit_cannot_commit_old_preview(window, qapp):
    import numpy as np
    dialog = prepared(window)
    dialog.preview()
    assert dialog.apply_button.isEnabled(), dialog.status.text()
    geometry = window.project.geometry
    revision = geometry.revision
    vertex = next(iter(geometry.vertices.values()))
    object.__setattr__(vertex, "position", np.asarray(vertex.position) + (0, 0, .1))
    assert geometry.revision == revision
    dialog.apply()
    assert not geometry.faces
    assert not dialog.apply_button.isEnabled()
    dialog.reject()
    dialog.deleteLater()


def test_view_callback_cannot_replace_accepted_preview_inputs(window, qapp, monkeypatch):
    dialog = prepared(window)
    original = window.viewport.show
    fired = False
    def show(scene, **kwargs):
        nonlocal fired
        original(scene, **kwargs)
        if not fired:
            fired = True
            dialog.flip.setChecked(True)
    monkeypatch.setattr(window.viewport, "show", show)
    dialog.preview()
    assert fired
    assert dialog.command is None
    assert not dialog.apply_button.isEnabled()
    assert not window.project.geometry.faces
    dialog.reject()
    dialog.deleteLater()


def test_apply_callback_change_is_checked_before_command(window, qapp, monkeypatch):
    dialog = prepared(window)
    dialog.preview()
    assert dialog.apply_button.isEnabled(), dialog.status.text()
    monkeypatch.setattr(window, "_begin_modelling", lambda: window.project.geometry.add_point(9, 8, 7))
    dialog.apply()
    assert not window.project.geometry.faces
    assert not window.project.geometry.features.records
    assert "document changed" in dialog.status.text()
    dialog.reject()
    dialog.deleteLater()
