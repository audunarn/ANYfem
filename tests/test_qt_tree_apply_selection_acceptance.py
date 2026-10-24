"""Native tree property clicks preserve the engineer's canvas selection."""
import os

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from anygeometry import EntityRef
from anyfem import commands
from anyfem.model.sections import PlateSection
from anyfem.mesh.refinement import Refinement
from anyfem.selection import SelectionFilter
from test_qt_geometry_actions import qapp, window


def selected_faces(window, qapp):
    faces = []
    for offset in (0, 2):
        points = [window.run(commands.AddPoint(offset+x, y))
                  for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))]
        faces.append(window.run(commands.AddPlate(points)))
    window.viewport.set_view("top")
    window.viewport.fit()
    window.selection.set_mode("face")
    qapp.processEvents()
    surface = window.viewport.canvas.event_widget
    ratio = surface.devicePixelRatioF()
    for index, offset in enumerate((0, 2)):
        x, y, _ = window.viewport.canvas.project_point((offset+.5, .5, 0))
        QTest.mouseClick(surface, Qt.LeftButton,
                         Qt.ShiftModifier if index else Qt.NoModifier,
                         pos=QPoint(round(x/ratio), round(y/ratio)))
        qapp.processEvents()
    assert set(window.selection.items) == {EntityRef("face", face) for face in faces}
    return faces


def apply_property(window, qapp, monkeypatch, group, key):
    tree = window.tree
    item = tree._rows[group][key][0]
    tree.expand(item.parent().index())
    tree.scrollTo(item.index())
    qapp.processEvents()
    position = tree.visualRect(item.index()).center()
    selected = window.selection.items
    highlighted = window.viewport.highlighted_tags
    calls = []
    errors = []

    def execute(menu, *args):
        actions = [action for action in menu.actions() if action.text() == "Apply to selection"]
        assert len(actions) == 1 and actions[0].isEnabled()
        assert window.selection.items == selected
        assert window.viewport.highlighted_tags == highlighted
        calls.append(1)
        actions[0].trigger()

    original_menu = tree.property_menu
    def property_menu(value):
        menu = original_menu(value)
        def choose():
            try:
                execute(menu)
            except BaseException as error:
                errors.append(error)
            finally:
                menu.close()
        QTimer.singleShot(0, choose)
        return menu
    monkeypatch.setattr(tree, "property_menu", property_menu)
    QTest.mousePress(tree.viewport(), Qt.RightButton, pos=position)
    assert window.selection.items == selected
    QTest.mouseRelease(tree.viewport(), Qt.RightButton, pos=position)
    if not calls:
        QApplication.sendEvent(tree.viewport(), QContextMenuEvent(
            QContextMenuEvent.Mouse, position, tree.viewport().mapToGlobal(position)))
    assert calls == [1]
    if errors:
        raise errors[0]
    qapp.processEvents()


def test_apply_plate_section_to_canvas_selection_preserves_mixed_items_and_undo(window, qapp, monkeypatch):
    faces = selected_faces(window, qapp)
    section = PlateSection("Thick", .025, "S355")
    window.run(commands.AddPlateSection(section))
    edge = window.project.geometry.faces[faces[0]].loop[0].edge
    window.selection.set_filter(SelectionFilter("geometry", frozenset({"edge", "face"})))
    window.selection.select(EntityRef("edge", edge), extend=True)
    original = window.selection.items
    history = len(window.commands.history())
    apply_property(window, qapp, monkeypatch, "Plate sections", section.id)
    assert window.selection.items == original
    assert all(window.project.face_sections[face] == "Thick" for face in faces)
    assert edge not in window.project.edge_sections
    assert len(window.commands.history()) == history+1
    window.undo()
    assert not window.project.face_sections
    window.redo()
    assert all(window.project.face_sections[face] == "Thick" for face in faces)


def test_apply_beam_section_skips_selected_faces_and_undo_restores_members(window, qapp, monkeypatch):
    faces = selected_faces(window, qapp)
    edges = [window.project.geometry.faces[face].loop[0].edge for face in faces]
    window.selection.set_filter(SelectionFilter("geometry", frozenset({"edge", "face"})))
    for edge in edges:
        window.selection.select(EntityRef("edge", edge), extend=True)
    original = window.selection.items
    before_members = set(window.project.geometry.members)
    section = window.project.beam_sections["stiffener"]
    apply_property(window, qapp, monkeypatch, "Beam sections", section.id)
    assert window.selection.items == original
    assert set(window.project.edge_sections) == set(edges)
    assert not window.project.face_sections
    window.undo()
    assert not window.project.edge_sections
    assert set(window.project.geometry.members) == before_members
    window.redo()
    assert set(window.project.edge_sections) == set(edges)


def test_apply_mesh_size_from_tree_preserves_source_and_roundtrips(window, qapp, monkeypatch, tmp_path):
    faces = selected_faces(window, qapp)
    source = Refinement(.2, radius=.15, center=(0, 0, 0), growth=1.8, name="Fine")
    window.run(commands.AddRefinement(source))
    history = len(window.commands.history())
    original = window.selection.items
    apply_property(window, qapp, monkeypatch, "Mesh sizes", id(source))
    assert window.selection.items == original
    applied = window.project.refinements
    assert applied[0] is source and len(applied) == 3
    assert {item.ref for item in applied[1:]} == {EntityRef("face", face) for face in faces}
    assert all(item.center is None and item.size == .2 and item.radius == .15
               and item.growth == 1.8 for item in applied[1:])
    assert len(window.commands.history()) == history+1
    window.undo()
    assert window.project.refinements == [source]
    window.redo()
    path = tmp_path / "tree-mesh-sizes.anyfem"
    window.save_project(path=str(path))
    window.open_project(str(path))
    assert len(window.project.refinements) == 3
