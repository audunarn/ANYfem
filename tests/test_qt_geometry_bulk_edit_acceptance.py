"""Independent native Edit menus and opt-in bulk structural editing."""
import os
import numpy as np
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':
    pytest.skip('enable Qt checks explicitly',allow_module_level=True)
from PySide6.QtCore import Qt,QPoint,QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QMenu,QScrollArea
from anygeometry import EntityRef
from anyfem import commands
from anyfem.io.project_file import project_to_dict
from test_qt_geometry_actions import qapp,window
from test_qt_generated_member_picking import click_edge
from test_qt_empty_sets_acceptance import tree_select


def active_editor(window):
    return next(dialog for dialog in window.findChildren(QDialog) if dialog.objectName()=='GeometryEditDialog' and dialog.isVisible())


def enable(dialog,qapp,kind,key):
    check=getattr(dialog,kind+'_checks')[key]
    dialog.findChild(QScrollArea).ensureWidgetVisible(check);qapp.processEvents()
    QTest.mouseClick(check,Qt.LeftButton);qapp.processEvents()
    assert check.isChecked()
    return getattr(dialog,kind+'_fields')[key]


def native_canvas_edit(window,qapp,edge):
    canvas=window.viewport.canvas;surface=canvas.event_widget
    world=window.project.geometry.sample_edge(edge,[.37])[0]
    x,y,_=canvas.project_point(world);ratio=surface.devicePixelRatioF()
    selected=tuple(window.selection.items)
    QTest.mouseClick(surface,Qt.RightButton,pos=QPoint(round(x/ratio),round(y/ratio)));qapp.processEvents()
    menu=window.geometry_actions.menu
    assert menu is not None and menu.isVisible()
    assert tuple(window.selection.items)==selected
    action=next(action for action in menu.actions() if action.text()=='Edit')
    assert action.isEnabled();action.trigger();menu.close();qapp.processEvents()
    return active_editor(window)


def native_tree_edit(window,qapp,group,key):
    tree=window.tree;item=tree._rows[group][key][0]
    tree.expand(item.parent().index());tree.scrollTo(item.index());qapp.processEvents()
    position=tree.visualRect(item.index()).center();errors=[]
    selected=tuple(window.selection.items)
    def choose():
        menu=QApplication.activePopupWidget()
        try:
            assert isinstance(menu,QMenu)
            action=next(action for action in menu.actions() if action.text()=='Edit')
            assert action.isEnabled() and tuple(window.selection.items)==selected
            action.trigger()
        except BaseException as error:errors.append(error)
        finally:
            if menu is not None:menu.close()
    QTest.mousePress(tree.viewport(),Qt.RightButton,pos=position)
    QTest.mouseRelease(tree.viewport(),Qt.RightButton,pos=position)
    QTimer.singleShot(0,choose)
    QApplication.sendEvent(tree.viewport(),QContextMenuEvent(QContextMenuEvent.Mouse,position,tree.viewport().mapToGlobal(position)))
    qapp.processEvents();assert not errors,errors
    return active_editor(window)


def test_canvas_edit_many_beams_only_opted_in_fields_and_cancel(window,qapp):
    window.run(commands.AddStiffenedPanel(4,4,1,beam_section='stiffener',plate_section='plate'))
    edges=sorted(window.project.edge_sections)
    window.viewport.set_view('top');window.viewport.fit();window.selection.set_mode('edge');qapp.processEvents()
    click_edge(window,qapp,edges[0]);click_edge(window,qapp,edges[1],Qt.ShiftModifier)
    expected={EntityRef('edge',edge) for edge in edges[:2]}
    assert set(window.selection.items)==expected
    def solid(edge):
        arrays=[np.asarray(polygon).reshape(-1,3) for patch in window.viewport._scene.faces
                if patch.ref==EntityRef('edge',edge) for polygon in patch.polygons]
        assert arrays
        return np.concatenate(arrays).copy()
    initial_solids={edge:solid(edge) for edge in edges}
    original=window.project.beam_sections['stiffener'];history=len(window.commands.history())
    dialog=native_canvas_edit(window,qapp,edges[0])
    assert not any(check.isChecked() for check in dialog.beam_checks.values())
    rotation=enable(dialog,qapp,'beam','rotation_deg');rotation.setText('45 deg')
    side=enable(dialog,qapp,'beam','attachment_side');side.setCurrentIndex(side.findData('back'))
    mode=enable(dialog,qapp,'beam','offset_mode');mode.setCurrentIndex(mode.findData('manual'))
    offset=enable(dialog,qapp,'beam','eccentricity');offset.setText('20 mm')
    dialog.grab().save('C:/ANYworkspaceWork/bulk-beam-edit-dialog-20261022.png')
    QTest.mouseClick(dialog.apply,Qt.LeftButton);qapp.processEvents()
    assert len(window.commands.history())==history+1
    assert window.project.beam_sections['stiffener']==original
    for edge in edges[:2]:
        section=window.project.beam_section_of(edge)
        assert section.rotation_deg==pytest.approx(45.) and section.eccentricity==pytest.approx(.02)
        assert section.attachment_side=='back' and section.offset_mode=='manual'
        assert section.web_height==original.web_height and section.material==original.material
    assert window.project.edge_sections[edges[2]]=='stiffener'
    assert not np.array_equal(solid(edges[0]),initial_solids[edges[0]])
    np.testing.assert_array_equal(solid(edges[2]),initial_solids[edges[2]])
    window.undo();qapp.processEvents()
    assert all(window.project.edge_sections[edge]=='stiffener' for edge in edges)
    for edge in edges:np.testing.assert_allclose(solid(edge),initial_solids[edge],atol=1e-12)
    dialog=native_canvas_edit(window,qapp,edges[0]);enable(dialog,qapp,'beam','rotation_deg').setText('60')
    history=window.commands.history();QTest.mouseClick(dialog.cancel,Qt.LeftButton);qapp.processEvents()
    assert window.commands.history()==history


def test_worktree_multi_plate_edit_and_invalid_preserves_accepted_model(window,qapp,tmp_path):
    window.run(commands.AddStiffenedPanel(4,4,1,beam_section='stiffener',plate_section='plate'))
    faces=sorted(window.project.face_sections)
    # Generated faces are exposed as children of their source feature.
    feature=window.project.geometry.features.records[-1]
    window.tree.toggle_feature_topology((feature.feature_id,));qapp.processEvents()
    # Native tree rows may be under Features; use the indexed semantic reference rows.
    def choose_face(face,modifier=Qt.NoModifier):
        item=window.tree._ref_rows[EntityRef('face',face)][0]
        ancestor=item.parent()
        while ancestor is not None:window.tree.expand(ancestor.index());ancestor=ancestor.parent()
        window.tree.scrollTo(item.index());qapp.processEvents()
        QTest.mouseClick(window.tree.viewport(),Qt.LeftButton,modifier,pos=window.tree.visualRect(item.index()).center());qapp.processEvents()
        return item
    first=choose_face(faces[0]);choose_face(faces[1],Qt.ControlModifier)
    assert set(window.selection.items)=={EntityRef('face',face) for face in faces[:2]}
    # Context Edit is shared by exposed geometry and ordinary tree rows.
    tree=window.tree;position=tree.visualRect(first.index()).center();errors=[]
    def choose():
        menu=QApplication.activePopupWidget()
        try:next(action for action in menu.actions() if action.text()=='Edit').trigger()
        except BaseException as error:errors.append(error)
        finally:
            if menu is not None:menu.close()
    QTest.mousePress(tree.viewport(),Qt.RightButton,pos=position);QTest.mouseRelease(tree.viewport(),Qt.RightButton,pos=position)
    QTimer.singleShot(0,choose)
    QApplication.sendEvent(tree.viewport(),QContextMenuEvent(QContextMenuEvent.Mouse,position,tree.viewport().mapToGlobal(position)))
    qapp.processEvents();assert not errors,errors
    dialog=active_editor(window);thickness=enable(dialog,qapp,'plate','thickness');thickness.setText('-1 mm')
    before=project_to_dict(window.project);history=window.commands.history()
    QTest.mouseClick(dialog.apply,Qt.LeftButton);qapp.processEvents()
    assert dialog.isVisible() and dialog.feedback.text()
    assert project_to_dict(window.project)==before and window.commands.history()==history
    thickness.setText('25 mm');QTest.mouseClick(dialog.apply,Qt.LeftButton);qapp.processEvents()
    assert window.project.plate_sections['plate'].thickness==.01
    for face in faces[:2]:assert window.project.plate_sections[window.project.face_sections[face]].thickness==.025
    assert all(window.project.face_sections[face]=='plate' for face in faces[2:])
    path=tmp_path/'bulk-edit.anyfem';window.save_project(path=str(path));window.open_project(str(path));qapp.processEvents()
    for face in faces[:2]:assert window.project.plate_sections[window.project.face_sections[face]].thickness==.025


def test_worktree_single_beam_edit_uses_degrees_and_preserves_other_properties(window,qapp):
    window.run(commands.AddStiffenedPanel(4,4,1,beam_section='stiffener',plate_section='plate'))
    member=next(iter(window.project.geometry.members))
    refs=window.tree.beam_references(member);assert refs
    original=window.project.beam_section_of(refs[0].id)
    tree_select(window,qapp,'Beams',member)
    dialog=native_tree_edit(window,qapp,'Beams',member)
    enable(dialog,qapp,'beam','rotation_deg').setText('30')
    history=len(window.commands.history())
    QTest.mouseClick(dialog.apply,Qt.LeftButton);qapp.processEvents()
    assert len(window.commands.history())==history+1
    for ref in refs:
        section=window.project.beam_section_of(ref.id)
        assert section.rotation_deg==30.
        assert section.offset_mode==original.offset_mode and section.eccentricity==original.eccentricity
        assert section.attachment_side==original.attachment_side and section.material==original.material


def test_bulk_edit_one_refresh_with_existing_mesh_controls(window,qapp,monkeypatch):
    import json
    from pathlib import Path
    from time import perf_counter
    from anyfem.application.controller import default_project
    from anyfem.mesh_sizes import MeshSizeProperty,SetMeshSizeProperty,AssignMeshSize
    p=default_project()
    commands.AddStiffenedPanel(10,30,.3,beam_section='stiffener',plate_section='plate').do(p)
    refs=tuple(EntityRef('edge',edge) for edge in p.edge_sections)
    definition=MeshSizeProperty('Fine',.2);SetMeshSizeProperty(definition).do(p)
    AssignMeshSize(definition.id,refs).do(p)
    window._set_project(p);window.show_geometry(reset_view=True);qapp.processEvents()
    calls=[];original=window.show_geometry
    def counted(*args,**kwargs):calls.append(True);return original(*args,**kwargs)
    monkeypatch.setattr(window,'show_geometry',counted)
    started=perf_counter();dialog=window.geometry_actions.edit(refs);qapp.processEvents()
    opened=(perf_counter()-started)*1000
    assert not calls
    enable(dialog,qapp,'beam','rotation_deg').setText('30')
    history=len(window.commands.history());started=perf_counter()
    QTest.mouseClick(dialog.apply,Qt.LeftButton);qapp.processEvents()
    elapsed=(perf_counter()-started)*1000
    assert len(window.commands.history())==history+1
    assert len(calls)==1
    assert all(p.beam_section_of(ref.id).rotation_deg==30. for ref in refs)
    assert len(p.beam_sections)==2
    Path('C:/ANYworkspaceWork/bulk-edit-performance-20261022.json').write_text(json.dumps({
        'beams':len(refs),'mesh_controls':len(p.refinements),'backend':'software',
        'open_ms':opened,'apply_ms':elapsed,'scene_refreshes':len(calls),'undo_steps':1},indent=2))
