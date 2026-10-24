"""Independent real mouse workflow for empty sets and selected membership."""
import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':
    pytest.skip('enable Qt checks explicitly',allow_module_level=True)

from PySide6.QtCore import Qt,QTimer,QPoint
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QInputDialog,QMenu
from anygeometry import EntityRef
from anyfem import commands
from anyfem.io.project_file import project_to_dict
from test_qt_geometry_actions import qapp,window
from test_qt_tree_apply_selection_acceptance import selected_faces


def create_from_table(window,qapp,name=None):
    table=window.tree.open_table('Sets');qapp.processEvents();errors=[]
    def fill():
        dialog=QApplication.activeModalWidget()
        try:
            assert isinstance(dialog,QInputDialog)
            if name is None:QTest.keyClick(dialog,Qt.Key_Escape)
            else:
                dialog.setTextValue(name);QTest.keyClick(dialog,Qt.Key_Return)
        except BaseException as error:
            errors.append(error)
            if dialog is not None:dialog.reject()
    QTimer.singleShot(0,fill);QTest.mouseClick(table.add_button,Qt.LeftButton);qapp.processEvents()
    assert not errors,errors
    table.close();qapp.processEvents()


def context_membership(window,qapp,set_id,label,require_enabled=True,before_trigger=None):
    tree=window.tree;item=tree._rows['Sets'][set_id][0]
    tree.expand(item.parent().index());tree.scrollTo(item.index());qapp.processEvents()
    position=tree.visualRect(item.index()).center();selected=tuple(window.selection.items)
    errors=[];called=[]
    def choose():
        menu=QApplication.activePopupWidget()
        try:
            assert isinstance(menu,QMenu)
            action=next(action for action in menu.actions() if action.text()==label)
            assert tuple(window.selection.items)==selected
            if require_enabled:assert action.isEnabled(),action.toolTip()
            if before_trigger:before_trigger()
            action.trigger();called.append(True)
        except BaseException as error:errors.append(error)
        finally:
            if menu is not None:menu.close()
    QTest.mousePress(tree.viewport(),Qt.RightButton,pos=position)
    assert tuple(window.selection.items)==selected
    QTest.mouseRelease(tree.viewport(),Qt.RightButton,pos=position)
    QTimer.singleShot(0,choose)
    QApplication.sendEvent(tree.viewport(),QContextMenuEvent(QContextMenuEvent.Mouse,position,tree.viewport().mapToGlobal(position)))
    qapp.processEvents();assert not errors,errors
    assert called


def resolved(window,identifier):
    return set(window.project.regions.resolve(identifier,geometry=window.project.geometry,
        feature_resolver=lambda anchor:window.project.geometry.features.resolve(anchor,window.project.geometry)))


def tree_select(window,qapp,group,key,modifier=Qt.NoModifier):
    tree=window.tree;item=tree._rows[group][key][0]
    tree.expand(item.parent().index());tree.scrollTo(item.index());qapp.processEvents()
    QTest.mouseClick(tree.viewport(),Qt.LeftButton,modifier,pos=tree.visualRect(item.index()).center());qapp.processEvents()


def test_empty_set_canvas_add_tree_mixed_remove_undo_and_reopen(window,qapp,tmp_path):
    faces=selected_faces(window,qapp)
    edge=window.project.geometry.faces[faces[0]].loop[0].edge
    window.run(commands.AssignBeam(edge,'stiffener'))
    window.selection.clear();qapp.processEvents();history=len(window.commands.history())
    create_from_table(window,qapp,'Working set')
    region=next(region for region in window.project.regions if region.name=='Working set')
    identifier=region.id
    assert not resolved(window,identifier) and len(window.commands.history())==history+1
    window.viewport.set_view('top');window.viewport.fit();window.selection.set_mode('face');qapp.processEvents()
    surface=window.viewport.canvas.event_widget;ratio=surface.devicePixelRatioF()
    for index,offset in enumerate((0,2)):
        x,y,_=window.viewport.canvas.project_point((offset+.5,.5,0))
        QTest.mouseClick(surface,Qt.LeftButton,Qt.ShiftModifier if index else Qt.NoModifier,pos=QPoint(round(x/ratio),round(y/ratio)))
        qapp.processEvents()
    expected={EntityRef('face',face) for face in faces}
    assert set(window.selection.items)==expected
    context_membership(window,qapp,identifier,'Add to set')
    assert resolved(window,identifier)==expected
    history=len(window.commands.history())
    context_membership(window,qapp,identifier,'Add to set',require_enabled=False)
    assert len(window.commands.history())==history and resolved(window,identifier)==expected
    # Select a plate and a beam from separate tree branches using Ctrl.
    beam=next(member_id for member_id in window.project.geometry.members if EntityRef('edge',edge) in window.tree.beam_references(member_id))
    tree_select(window,qapp,'Plates',faces[0])
    tree_select(window,qapp,'Beams',beam,Qt.ControlModifier)
    selected={EntityRef('face',faces[0]),EntityRef('edge',edge)}
    assert set(window.selection.items)==selected
    context_membership(window,qapp,identifier,'Remove from set')
    assert resolved(window,identifier)=={EntityRef('face',faces[1])}
    window.undo();qapp.processEvents();assert resolved(window,identifier)==expected
    window.redo();qapp.processEvents();assert resolved(window,identifier)=={EntityRef('face',faces[1])}
    tree_select(window,qapp,'Plates',faces[1])
    context_membership(window,qapp,identifier,'Remove from set')
    assert not resolved(window,identifier) and window.project.regions[identifier].id==identifier
    window.undo();qapp.processEvents()
    tree_select(window,qapp,'Beams',beam)
    context_membership(window,qapp,identifier,'Add to set')
    assert resolved(window,identifier)=={EntityRef('face',faces[1]),EntityRef('edge',edge)}
    path=tmp_path/'membership.anyfem';window.save_project(path=str(path));window.open_project(str(path));qapp.processEvents()
    assert resolved(window,identifier)=={EntityRef('face',faces[1]),EntityRef('edge',edge)}
    assert window.project.edge_sections[edge]=='stiffener'


def test_empty_set_creation_cancel_and_stale_membership_refuse(window,qapp):
    before=project_to_dict(window.project);history=window.commands.history()
    create_from_table(window,qapp)
    assert project_to_dict(window.project)==before and window.commands.history()==history
    create_from_table(window,qapp,'Empty')
    identifier=next(region.id for region in window.project.regions if region.name=='Empty')
    vertex=window.run(commands.AddPoint(0,0));window.selection.restore((EntityRef('vertex',vertex),));qapp.processEvents()
    context_membership(window,qapp,identifier,'Add to set',before_trigger=lambda:window.run(commands.AddPoint(5,5)))
    assert not resolved(window,identifier)
