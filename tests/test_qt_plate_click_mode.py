"""Interactive ordered plate construction through the real Qt viewport."""
import os
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from anyfem import commands
from test_qt_geometry_actions import qapp, window


def start(window, qapp, coordinates):
    ids = [window.run(commands.AddPoint(*xyz)) for xyz in coordinates]
    editor = window.panels["Geometry"]
    editor.choice.setCurrentIndex(editor.choice.findData("AddPlate"))
    assert not editor.construct_plate.isHidden()
    window.viewport.set_view("top");window.viewport.fit();qapp.processEvents()
    editor.construct_plate.click();qapp.processEvents()
    draft = window.viewport.plate_draft
    assert draft is not None and draft.ids == []
    return ids, draft


def click(window, qapp, identifier):
    surface = window.viewport.canvas.event_widget
    x, y, _ = window.viewport.canvas.project_point(window.project.geometry.vertex_position(identifier))
    ratio = surface.devicePixelRatioF()
    QTest.mouseClick(surface, Qt.LeftButton, pos=QPoint(round(x / ratio), round(y / ratio)))
    qapp.processEvents()


def test_five_point_concave_preview_close_and_undo(window, qapp, monkeypatch):
    coords = ((0, 0, 0), (4, 0, 0), (4, 3, 0), (2, 1.5, 0), (0, 3, 0))
    ids, draft = start(window, qapp, coords)
    canvas = window.viewport.canvas
    lines = []
    original = canvas.add_line
    def observe(a, b, *args, **kwargs):
        if kwargs.get("layer") == 45:
            lines.append((a.to_tuple(), b.to_tuple()))
        return original(a, b, *args, **kwargs)
    monkeypatch.setattr(canvas, "add_line", observe)
    for index, identifier in enumerate(ids):
        lines.clear()
        click(window, qapp, identifier)
        assert draft.ids == ids[:index + 1]
        assert not window.project.geometry.faces
        if index >= 1:
            assert lines[:index] == list(zip(coords[:index], coords[1:index+1]))
        assert f"{index+1}: vertex {identifier}" in draft.summary.text()
    click(window, qapp, ids[0])
    assert window.viewport.plate_draft is None
    geometry = window.project.geometry
    assert len(geometry.faces) == 1
    face = next(iter(geometry.faces))
    assert tuple(geometry.oriented_start_vertex(edge) for edge in geometry.faces[face].loop) == tuple(ids)
    patch = next(item for item in window.viewport._scene.faces if item.ref.id == face)
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    actual = unary_union([Polygon(polygon[:, :2]) for polygon in patch.polygons])
    assert actual.symmetric_difference(Polygon([point[:2] for point in coords])).area < 1e-10
    assert window.session.undo()
    assert not geometry.faces and len(geometry.vertices) == 5
    assert window.session.redo()
    assert tuple(geometry.oriented_start_vertex(edge) for edge in geometry.faces[face].loop) == tuple(ids)


def test_crossed_order_warns_and_can_be_corrected(window, qapp):
    coords = ((0, 0, 0), (3, 0, 0), (3, 3, 0), (0, 3, 0))
    ids, draft = start(window, qapp, coords)
    for i in (0, 2, 1, 3, 0):click(window, qapp, ids[i])
    assert window.viewport.plate_draft is draft
    assert draft.feedback.text().startswith("Warning:")
    assert not window.project.geometry.faces and not window.project.geometry.edges
    remove = next(button for button in draft.findChildren(QPushButton) if button.text() == "Remove last point")
    for _ in range(3):remove.click()
    assert draft.ids == ids[:1] and not draft.feedback.text()
    for i in (1, 2, 3, 0):click(window, qapp, ids[i])
    assert len(window.project.geometry.faces) == 1


def test_insufficient_points_duplicates_and_cancel(window, qapp):
    ids, draft = start(window, qapp, ((0,0,0), (3,0,0), (3,3,0)))
    click(window, qapp, ids[0]);click(window, qapp, ids[0])
    assert "at least three" in draft.feedback.text() and draft.ids == ids[:1]
    click(window, qapp, ids[1]);click(window, qapp, ids[1])
    assert "already" in draft.feedback.text() and draft.ids == ids[:2]
    QTest.keyClick(window.viewport.canvas.event_widget, Qt.Key_Escape)
    assert window.viewport.plate_draft is None
    assert not window.project.geometry.faces and not window.project.geometry.edges


def test_nonplanar_warning_and_cancel(window, qapp):
    ids, draft = start(window, qapp, ((0,0,0), (3,0,0), (3,3,0), (0,3,1)))
    for identifier in (*ids, ids[0]):click(window, qapp, identifier)
    assert draft.feedback.text().startswith("Warning:")
    assert window.viewport.plate_draft is draft and not window.project.geometry.faces and not window.project.geometry.edges
    draft.reject()


def test_button_only_visible_for_add_plate(window):
    editor = window.panels["Geometry"]
    editor.choice.setCurrentIndex(editor.choice.findData("AddPoint"))
    assert editor.construct_plate.isHidden()
    editor.choice.setCurrentIndex(editor.choice.findData("AddPlate"))
    assert not editor.construct_plate.isHidden()


def test_double_click_does_not_finish_unclosed_boundary(window, qapp):
    ids, draft = start(window, qapp, ((0,0,0), (3,0,0), (3,3,0), (0,3,0)))
    for identifier in ids[:3]:click(window, qapp, identifier)
    surface = window.viewport.canvas.event_widget
    x, y, _ = window.viewport.canvas.project_point(window.project.geometry.vertex_position(ids[2]))
    ratio = surface.devicePixelRatioF()
    QTest.mouseDClick(surface, Qt.LeftButton, pos=QPoint(round(x / ratio), round(y / ratio)))
    qapp.processEvents()
    assert window.viewport.plate_draft is draft and draft.ids == ids[:3]
    assert not window.project.geometry.faces
    click(window, qapp, ids[0])
    assert len(window.project.geometry.faces) == 1
