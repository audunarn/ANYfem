"""Real-widget checks for sets, right-click actions and vector validation."""
import os
import pytest
import numpy as np

if os.environ.get("ANYFEM_RUN_QT_TESTS")!="1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1",allow_module_level=True)
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt,QPoint,QItemSelectionModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QPlainTextEdit
from anygeometry.entities import EntityRef
from anyfem import commands as cmd
from anyfem.ui.qt.app import QtFemWindow


@pytest.fixture(scope="module")
def qapp():
    app=QApplication.instance() or QApplication([])
    # Some Windows offscreen runtimes omit Qt's bundled fonts. Use an
    # installed desktop font so row hit tests use real, nonzero text metrics.
    from PySide6.QtGui import QFontDatabase,QFont
    from pathlib import Path
    if os.environ.get("QT_QPA_PLATFORM")=="offscreen":
        font=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"/"segoeui.ttf"
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font));app.setFont(QFont("Segoe UI",9))
    return app


@pytest.fixture
def window(qapp,tmp_path,monkeypatch):
    from anyfem.io import recovery
    monkeypatch.setattr(recovery,"default_recovery_root",lambda:tmp_path/"recovery")
    window=QtFemWindow(viewer_backend=os.environ.get("ANYFEM_QT_TEST_BACKEND","software"))
    window.show();qapp.processEvents()
    yield window
    window.session.mark_saved();window.close();qapp.processEvents()


def geometry(window):
    vertices=[window.run(cmd.AddPoint(x,y)) for x,y in ((0,0),(1,0),(1,1),(0,1))]
    face=window.run(cmd.AddPlate(vertices))
    edge=window.project.geometry.faces[face].loop[0].edge
    return vertices,(EntityRef("vertex",vertices[0]),EntityRef("edge",edge),EntityRef("face",face))


def choose_set(window,qapp,identifier):
    tree=window.tree
    item=tree._rows["Sets"][identifier][0]
    tree.expand(item.parent().index());tree.scrollTo(item.index());qapp.processEvents()
    QTest.mouseClick(tree.viewport(),Qt.LeftButton,pos=tree.visualRect(item.index()).center())
    qapp.processEvents()
    return item


def test_mixed_set_highlight_reopen_delete_undo(window,qapp,tmp_path):
    vertices,refs=geometry(window)
    region=window.geometry_actions.new_set(refs,"Frame")
    item=choose_set(window,qapp,region.id)
    assert window.viewport._collection_refs==frozenset(refs)
    expected={entry.tag for group in (window.viewport._scene.points,window.viewport._scene.lines,window.viewport._scene.faces)
              for entry in group if any(ref in refs for ref in (entry.owners or (entry.ref,)))}
    assert expected and set(window.viewport.canvas._highlighted_tags)==expected
    assert item.rowCount()==3
    path=tmp_path/"frame.anyfem";window.save_project(path=str(path));window.new_project();window.open_project(str(path))
    choose_set(window,qapp,region.id)
    assert set(window.viewport.canvas._highlighted_tags)==expected
    window._delete_tree_items((f"region:{region.id}",))
    assert region.id not in window.project.regions and not window.viewport._collection_refs
    window.undo();choose_set(window,qapp,region.id)
    assert window.viewport._collection_refs==frozenset(refs)
    QTest.keyClick(window.viewport.canvas.event_widget,Qt.Key_Escape);qapp.processEvents()
    assert not window.viewport.canvas._highlighted_tags


def test_vector_units_validation_cancel_and_replacement(window,qapp):
    vertices,refs=geometry(window)
    from anyfem.model.units import UnitProfile
    window.project.units=UnitProfile("Millimetres",{**window.project.units.units,"length":"mm"})
    dialog=window.geometry_actions.vector_dialog("Move",refs)
    before=window.commands.history()
    dialog.fields["X"].setText("bad");dialog.apply()
    assert dialog.isVisible() and dialog.feedback.text() and window.commands.history()==before
    dialog.fields["X"].setText("1000");dialog.fields["Y"].setText("20 cm")
    dialog.apply();qapp.processEvents()
    np.testing.assert_allclose(window.project.geometry.vertex_position(vertices[0]),(1,.2,0))
    window.undo()
    np.testing.assert_allclose(window.project.geometry.vertex_position(vertices[0]),(0,0,0))
    dialog=window.geometry_actions.vector_dialog("Copy",refs);dialog.reject();qapp.processEvents()
    assert len(window.project.geometry.faces)==1
    dialog=window.geometry_actions.vector_dialog("Move",refs)
    window.run(cmd.AddPoint(3,3,3));history=window.commands.history()
    dialog.apply();assert "changed" in dialog.feedback.text() and window.commands.history()==history
    window.new_project();qapp.processEvents();assert not window.geometry_actions.dialogs


def test_copy_selected_set_closure_and_properties(window,qapp):
    vertices,refs=geometry(window)
    region=window.geometry_actions.new_set(refs,"Frame");choose_set(window,qapp,region.id)
    dialog=window.geometry_actions.vector_dialog("Copy")
    dialog.fields["Z"].setText("2");dialog.apply();qapp.processEvents()
    assert len(window.project.geometry.faces)==2 and len(window.project.geometry.vertices)==8
    assert sum(window.project.geometry.vertex_position(key)[2]==2 for key in window.project.geometry.vertices)==4
    properties=window.geometry_actions.properties(refs)
    text=properties.findChild(QPlainTextEdit).toPlainText()
    assert "Position:" in text and "Length:" in text and "Boundary edges:" in text
    assert properties.findChild(QPlainTextEdit).isReadOnly()
    properties.reject()


def test_real_right_click_menu_and_left_selection(window,qapp):
    vertices,refs=geometry(window)
    window.viewport.set_view("top");window.viewport.fit();qapp.processEvents()
    window.selection.set_mode("vertex")
    canvas=window.viewport.canvas;surface=canvas.event_widget
    x,y,_=canvas.project_point(window.project.geometry.vertex_position(vertices[0]))
    ratio=surface.devicePixelRatioF();position=QPoint(round(x/ratio),round(y/ratio))
    QTest.mouseClick(surface,Qt.LeftButton,pos=position);qapp.processEvents()
    assert window.geometry_actions.menu is None
    assert refs[0] in window.selection.items
    window.selection.clear()
    qapp.processEvents()
    x,y,_=canvas.project_point(window.project.geometry.vertex_position(vertices[0]))
    position=QPoint(round(x/ratio),round(y/ratio))
    QTest.mouseClick(surface,Qt.RightButton,pos=position);qapp.processEvents()
    menu=window.geometry_actions.menu
    assert menu is not None and menu.isVisible()
    assert [action.text() for action in menu.actions()][:4]==["Copy","Move","Edit","Properties"]
    assert not menu.actions()[2].isEnabled()  # Point-only selection has no structural inputs.
    menu.actions()[1].trigger();menu.close();qapp.processEvents()
    assert window.geometry_actions.dialogs[-1].operation=="Move"
    window.geometry_actions.dialogs[-1].reject();window.geometry_actions.close();qapp.processEvents()
    QTest.mouseClick(surface,Qt.LeftButton,Qt.ShiftModifier,pos=position);qapp.processEvents()
    assert window.geometry_actions.menu is None
    window.canvas_action_menu.setChecked(False)
    QTest.mouseClick(surface,Qt.RightButton,pos=position);qapp.processEvents()
    assert window.geometry_actions.menu is None


def test_right_drag_orbits_without_opening_menu(window,qapp):
    vertices,_=geometry(window)
    window.viewport.set_view("top");window.viewport.fit();qapp.processEvents()
    surface=window.viewport.canvas.event_widget
    x,y,_=window.viewport.canvas.project_point(window.project.geometry.vertex_position(vertices[0]))
    ratio=surface.devicePixelRatioF();position=QPoint(round(x/ratio),round(y/ratio))
    QTest.mousePress(surface,Qt.RightButton,pos=position)
    QTest.mouseMove(surface,position+QPoint(30,30))
    QTest.mouseRelease(surface,Qt.RightButton,pos=position+QPoint(30,30))
    qapp.processEvents()
    assert window.geometry_actions.menu is None


@pytest.mark.parametrize("operation",["new","add","rename"])
def test_set_prompt_refuses_project_replacement(window,qapp,monkeypatch,operation):
    from PySide6.QtWidgets import QInputDialog
    _,refs=geometry(window)
    region=window.geometry_actions.new_set(refs,"Frame")
    def replace_document(*args,**kwargs):
        window.new_project();geometry(window)
        return ("Frame" if operation=="add" else "Other",True)
    monkeypatch.setattr(QInputDialog,"getText",replace_document)
    monkeypatch.setattr(QInputDialog,"getItem",replace_document)
    callback={"new":lambda:window.geometry_actions.new_set(refs),
              "add":lambda:window.geometry_actions.add_to_set(refs),
              "rename":lambda:window.geometry_actions.rename_set(region.id)}[operation]
    with pytest.raises(ValueError,match="changed"):callback()
    assert not list(window.project.regions)
