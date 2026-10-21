"""Regress modelling after meshing and keyboard submission of operations."""
import os
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from anymesher.mesh import Mesh
from anyfem import commands
from anyfem.selection import MeshEntityRef
from test_qt_geometry_actions import qapp, window


def mesh():
    return Mesh(nodes={1: np.array((0., 0., 0.)), 2: np.array((1., 0., 0.)),
                       3: np.array((0., 1., 0.))}, tris={1: (1, 2, 3)})


def display_mesh(window):
    window.mesh = mesh()
    window.show_mesh()
    window.selection.set_mode("node")
    window.selection.restore((MeshEntityRef("node", 1),))


@pytest.mark.parametrize("workspace", ["Geometry", "Construction", "Generators"])
def test_selecting_model_workspace_clears_active_mesh_and_restores_geometry(window, qapp, workspace):
    point = window.run(commands.AddPoint(0, 0, 0))
    window.notebook.select("Mesh")
    display_mesh(window)
    revision = window.session.revision
    window.notebook.select(workspace); qapp.processEvents()
    assert window._view_mode == "geometry" and window.mesh is None
    assert window.selection.domain.value == "geometry"
    assert not window.selection.items
    assert point in window.project.geometry.vertices
    assert window.session.revision is revision
    assert not window.viewport._scene.faces


def test_model_operation_icon_and_field_resume_same_workspace(window, qapp):
    window.run(commands.AddPoint(0, 0, 0))
    editor = window.panels["Geometry"]
    display_mesh(window)
    index = editor.choice.findData("AddLine")
    editor.option_icons.buttons[index].click(); qapp.processEvents()
    assert window.mesh is None and window._view_mode == "geometry"
    display_mesh(window)
    QTest.mouseClick(editor.fields["start"][0], Qt.LeftButton)
    assert window.mesh is None and window.selection.domain.value == "geometry"


def test_cancel_construction_preserves_model_after_resuming(window, qapp):
    panel = window.panels["Construction"]
    window.notebook.select("Construction")
    display_mesh(window)
    revision = window.session.revision
    panel.start()
    assert window._view_mode == "geometry" and window.mesh is None
    assert window.viewport.construction_active
    window.viewport.cancel_construction()
    assert window.session.revision is revision and not window.viewport.construction_active


def test_mesh_native_document_is_not_discarded_on_workspace_selection(window):
    from types import SimpleNamespace
    original = mesh()
    window.imported = SimpleNamespace(mesh=original)
    window.mesh = original
    window.show_mesh()
    window.notebook.select("Construction")
    assert window.mesh is original and window._view_mode == "mesh"
    window.imported = None


@pytest.mark.parametrize("key", [Qt.Key_Return, Qt.Key_Enter])
def test_enter_creates_line_once_using_current_fields_and_undo(window, key):
    a = window.run(commands.AddPoint(0, 0, 0))
    b = window.run(commands.AddPoint(1, 0, 0))
    editor = window.panels["Geometry"]
    editor.choice.setCurrentIndex(editor.choice.findData("AddLine"))
    editor.fields["start"][0].setText(str(a))
    editor.fields["end"][0].setText(str(b))
    QTest.keyClick(editor.fields["end"][0], key)
    assert len(window.project.geometry.edges) == 1
    assert window.session.undo()
    assert not window.project.geometry.edges


def test_enter_creates_generator_once_and_undo(window):
    window.notebook.select("Generators")
    panel = window.panels["Generators"]
    before = len(window.project.geometry.features)
    QTest.keyClick(panel.form.fields["length"][0], Qt.Key_Return)
    assert len(window.project.geometry.features) == before + 1
    assert window.session.undo()
    assert len(window.project.geometry.features) == before

@pytest.mark.parametrize("key", [Qt.Key_Return, Qt.Key_Enter])
def test_enter_on_canvas_creates_line_from_selected_points(window, key):
    from anygeometry.entities import EntityRef
    a = window.run(commands.AddPoint(0, 0, 0))
    b = window.run(commands.AddPoint(1, 0, 0))
    editor = window.panels["Geometry"]
    editor.choice.setCurrentIndex(editor.choice.findData("AddLine"))
    window.selection.set_mode("vertex")
    window.selection.restore((EntityRef("vertex", a), EntityRef("vertex", b)))
    QTest.keyClick(window.viewport.canvas.event_widget, key)
    assert len(window.project.geometry.edges) == 1
    assert window.session.undo()
    assert not window.project.geometry.edges


def test_enter_on_construction_fields_adds_points_and_applies_line(window):
    window.notebook.select("Construction")
    panel = window.panels["Construction"]
    panel.mode.setCurrentIndex(panel.mode.findData("line"))
    panel.start()
    for coordinates in ("0, 0", "1, 0"):
        panel.coordinates.setText(coordinates)
        QTest.keyClick(panel.coordinates, Qt.Key_Return)
    assert len(panel.app.viewport.construction_task.points) == 2
    QTest.keyClick(panel.offset, Qt.Key_Enter)
    assert len(window.project.geometry.edges) == 1
    assert not window.viewport.construction_active
    assert window.session.undo() and not window.project.geometry.edges


def test_modified_and_repeated_enter_on_canvas_does_not_apply(window):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    a = window.run(commands.AddPoint(0, 0, 0))
    b = window.run(commands.AddPoint(1, 0, 0))
    editor = window.panels["Geometry"]
    editor.choice.setCurrentIndex(editor.choice.findData("AddLine"))
    editor.fields["start"][0].setText(str(a))
    editor.fields["end"][0].setText(str(b))
    target = window.viewport.canvas.event_widget
    QTest.keyClick(target, Qt.Key_Return, Qt.ControlModifier)
    QApplication.sendEvent(target, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier, "\r", True, 2))
    assert not window.project.geometry.edges


def test_resume_after_real_mesh_generation(window, qapp):
    vertices = [window.run(commands.AddPoint(x, y, 0)) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))]
    window.run(commands.AddPlate(vertices))
    window.notebook.select("Mesh")
    window.generate_mesh(0.5, strategy="auto", native_backend="python")
    assert window.mesh is not None and window._view_mode == "mesh"
    mesh_id = window.mesh_record_id
    window.notebook.select("Geometry"); qapp.processEvents()
    assert window.mesh is None and window._view_mode == "geometry"
    assert mesh_id in window.project.mesh_records
    assert len(window.project.geometry.faces) == 1


def test_construction_canvas_ignores_modified_and_repeated_enter(window):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    window.notebook.select("Construction")
    panel = window.panels["Construction"]
    panel.start()
    panel.coordinates.setText("0, 0")
    panel.add_point()
    target = window.viewport.canvas.event_widget
    QTest.keyClick(target, Qt.Key_Return, Qt.ControlModifier)
    QApplication.sendEvent(target, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier, "\r", True, 2))
    assert window.viewport.construction_active and not window.project.geometry.vertices
    QTest.keyClick(target, Qt.Key_Return)
    assert len(window.project.geometry.vertices) == 1 and not window.viewport.construction_active


def test_reselecting_active_model_workspace_resumes_modelling(window):
    assert window.details.tabText(window.details.currentIndex()) == "Geometry"
    display_mesh(window)
    window.notebook.select("Geometry")
    assert window.mesh is None and window._view_mode == "geometry"
    assert window.selection.domain.value == "geometry"


def _canvas_position(window, vertex):
    from PySide6.QtCore import QPoint
    x, y, _ = window.viewport.canvas.project_point(window.project.geometry.vertex_position(vertex))
    ratio = window.viewport.canvas.event_widget.devicePixelRatioF()
    return QPoint(round(x / ratio), round(y / ratio))


def test_clicking_two_points_constructs_one_line_without_preselection(window, qapp):
    a = window.run(commands.AddPoint(0, 0, 0))
    b = window.run(commands.AddPoint(2, 0, 0))
    editor = window.panels["Geometry"]
    editor.choice.setCurrentIndex(editor.choice.findData("AddLine"))
    window.viewport.set_view("top"); window.viewport.fit(); qapp.processEvents()
    surface = window.viewport.canvas.event_widget
    QTest.mouseClick(surface, Qt.LeftButton, pos=_canvas_position(window, a)); qapp.processEvents()
    assert window.viewport.line_start.id == a
    assert not window.project.geometry.edges
    QTest.mouseClick(surface, Qt.LeftButton, pos=_canvas_position(window, b)); qapp.processEvents()
    assert window.viewport.line_start is None
    edge, = window.project.geometry.edges
    assert (window.project.geometry.edges[edge].start, window.project.geometry.edges[edge].end) == (a, b)
    assert window.selection.mode == "edge"
    assert window.session.undo() and not window.project.geometry.edges


def test_escape_cancels_pending_line_endpoint(window, qapp):
    a = window.run(commands.AddPoint(0, 0, 0))
    b = window.run(commands.AddPoint(2, 0, 0))
    editor = window.panels["Geometry"]
    editor.choice.setCurrentIndex(editor.choice.findData("AddLine"))
    window.viewport.set_view("top"); window.viewport.fit(); qapp.processEvents()
    surface = window.viewport.canvas.event_widget
    QTest.mouseClick(surface, Qt.LeftButton, pos=_canvas_position(window, a)); qapp.processEvents()
    QTest.keyClick(surface, Qt.Key_Escape)
    assert window.viewport.line_start is None and not window.project.geometry.edges
    QTest.mouseClick(surface, Qt.LeftButton, pos=_canvas_position(window, b)); qapp.processEvents()
    assert window.viewport.line_start.id == b


def test_geometry_workspace_uses_words_and_keeps_operation_icon_strip(window):
    panel = window.panels["Geometry"]
    choices = [panel.choice.itemData(index) for index in range(panel.choice.count())]
    assert "AddFeature" not in choices and "AddSketch" in choices
    assert panel.choice.itemText(panel.choice.findData("AddLine")) == "Add line / beam axis"
    assert "Choose an operation" in panel.operation_heading.text()
    assert panel.option_icons.isVisible()


def test_construction_plane_supports_saved_reopenable_sketch(window, qapp):
    window.notebook.select("Construction")
    panel = window.panels["Construction"]
    panel.plane_name.setText("Deck plane")
    panel.plane_origin.setText("2, 3, 4")
    panel.plane_normal.setText("0, 0, 1")
    panel.plane_x_axis.setText("1, 0, 0")
    panel.create_plane()
    assert panel.system.currentText() == "Deck plane"
    panel.offset.setText("0.5")
    panel.sketch_on_plane()
    for point in ("0, 0", "1, 0", "0, 1"):
        panel.coordinates.setText(point)
        panel.add_point()
    panel.extrusion.setText("0.2")
    panel.apply();qapp.processEvents()
    record = window.project.geometry.features.records[-1]
    assert record.kind == "geometry.sketch.plane"
    assert record.parameters["plane"]["origin"] == [2.0, 3.0, 4.5]
    vertex = record.outputs["point/p1"]
    assert tuple(window.project.geometry.vertex_position(vertex.id)) == (2.0, 3.0, 4.5)
    assert panel.edit_sketch(record.feature_id)
    panel.apply();qapp.processEvents()
    edited = window.project.geometry.features.get(record.feature_id)
    assert edited.parameters["plane"] == record.parameters["plane"]
