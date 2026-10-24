"""Create and reuse named sizes through the native tree workflow."""
import os
from dataclasses import replace
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("enable Qt checks explicitly", allow_module_level=True)

from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QMenu
from anyfem import commands
from anyfem.mesh_sizes import NamedRefinement
from anyfem.selection import SelectionFilter
from test_qt_geometry_actions import qapp,window,geometry
from test_qt_tree_apply_selection_acceptance import apply_property


def test_native_create_apply_mixed_selection_edit_and_reopen(window,qapp,monkeypatch,tmp_path):
    _,refs=geometry(window)
    window.run(commands.AssignBeam(refs[1].id,"stiffener"))
    window.selection.set_filter(SelectionFilter("geometry",frozenset({"edge","face"})))
    window.selection.restore((refs[1],refs[2]));qapp.processEvents()
    selected=tuple(window.selection.items)
    tree=window.tree;group=tree._groups["Mesh sizes"]
    tree.scrollTo(group.index());qapp.processEvents()
    point=tree.visualRect(group.index()).center()
    errors=[];calls=[]
    def fill():
        dialog=QApplication.activeModalWidget()
        try:
            assert dialog is not None and dialog.windowTitle()=="New mesh size"
            dialog.name.setText("Fine plates and beams");dialog.size.setText("250 mm")
            QTest.mouseClick(dialog.primary,Qt.LeftButton);calls.append("created")
        except BaseException as error:
            errors.append(error)
            if dialog is not None:dialog.reject()
    def choose():
        menu=QApplication.activePopupWidget()
        try:
            assert isinstance(menu,QMenu)
            action=next(action for action in menu.actions() if action.text()=="New mesh size…")
            QTimer.singleShot(0,fill);action.trigger()
        except BaseException as error:errors.append(error)
        finally:
            if menu is not None:menu.close()
    QTest.mousePress(tree.viewport(),Qt.RightButton,pos=point)
    assert tuple(window.selection.items)==selected
    QTest.mouseRelease(tree.viewport(),Qt.RightButton,pos=point)
    QTimer.singleShot(0,choose)
    QApplication.sendEvent(tree.viewport(),QContextMenuEvent(QContextMenuEvent.Mouse,point,tree.viewport().mapToGlobal(point)))
    qapp.processEvents()
    assert not errors,errors
    assert calls==["created"] and len(window.project.mesh_size_properties)==1
    definition=next(iter(window.project.mesh_size_properties.values()))
    assert definition.size==.25 and not window.project.refinements
    assert tuple(window.selection.items)==selected
    with monkeypatch.context() as scoped:
        apply_property(window,qapp,scoped,"Mesh sizes",definition.id)
    applied=[item for item in window.project.refinements if isinstance(item,NamedRefinement)]
    assert {item.ref for item in applied}==set(selected)
    assert all(item.property_id==definition.id and item.size==.25 for item in applied)
    window.undo();assert not window.project.refinements
    window.redo();assert len(window.project.refinements)==2
    window.run(commands.SetMeshSizeProperty(replace(definition,size=.125)))
    assert all(item.size==.125 for item in window.project.refinements)
    path=tmp_path/"named-sizes.anyfem";window.save_project(path=str(path));window.open_project(str(path))
    assert window.project.mesh_size_properties[definition.id].size==.125
    assert len(window.project.refinements)==2 and all(isinstance(item,NamedRefinement) for item in window.project.refinements)
