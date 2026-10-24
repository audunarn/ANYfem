import os
from dataclasses import replace
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('enable Qt tests explicitly',allow_module_level=True)
from test_qt_geometry_actions import qapp,window,geometry
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from anyfem.mesh_sizes import MeshSizeProperty,SetMeshSizeProperty,NamedRefinement
from anyfem.ui.qt.mesh_size_editor import MeshSizeDialog
from anyfem.document import canonical_hash,_model_payload
from anyfem.io.project_file import project_to_dict


def test_create_cancel_unit_aware_unused_property_and_tree_dialog_edit(window,qapp):
    from anyfem.model.units import unit_profile
    window.project.units=unit_profile('SI-mm-N-MPa');before=canonical_hash(_model_payload(project_to_dict(window.project)))
    cancelled=MeshSizeDialog(window);cancelled.name.setText('Cancelled');cancelled.size.setText('20 mm');cancelled.reject()
    assert not window.project.mesh_size_properties
    dialog=MeshSizeDialog(window);dialog.name.setText('Fine');dialog.size.setText('20 mm');definition=dialog.save();qapp.processEvents()
    assert definition.size==.02 and not window.project.refinements
    assert canonical_hash(_model_payload(project_to_dict(window.project)))==before
    row=window.tree._rows['Mesh sizes'][definition.id][0];assert row.data(Qt.UserRole)==('mesh_size_properties',definition.id)
    edit=MeshSizeDialog(window,definition);edit.size.setText('30 mm');updated=edit.save();assert updated.id==definition.id and updated.size==.03


def test_rightpress_apply_named_property_to_selected_face_and_line_preserves_selection(window,qapp):
    _,refs=geometry(window);definition=window.run(SetMeshSizeProperty(MeshSizeProperty('Fine',.2)));window.selection.restore((refs[1],refs[2]));window.tree.expand(window.tree._groups['Mesh sizes'].index());qapp.processEvents()
    item=window.tree._rows['Mesh sizes'][definition.id][0];window.tree.scrollTo(item.index());qapp.processEvents()
    QTest.mousePress(window.tree.viewport(),Qt.RightButton,pos=window.tree.visualRect(item.index()).center());QTest.mouseRelease(window.tree.viewport(),Qt.RightButton,pos=window.tree.visualRect(item.index()).center())
    assert set(window.selection.items)=={refs[1],refs[2]}
    menu=window.tree.property_menu(('mesh_size_properties',definition.id));next(action for action in menu.actions() if action.text()=='Apply to selection').trigger();qapp.processEvents()
    assert {item.ref for item in window.project.refinements}=={refs[1],refs[2]}
    assert all(isinstance(item,NamedRefinement) for item in window.project.refinements)
    assert set(window.selection.items)=={refs[1],refs[2]}
    window.undo();assert not window.project.refinements
    window.redo();assert len(window.project.refinements)==2


def test_property_dialog_refuses_stale_project_record_and_invalid_input(window,qapp):
    definition=window.run(SetMeshSizeProperty(MeshSizeProperty('Fine',.2)));dialog=MeshSizeDialog(window,definition)
    window.run(SetMeshSizeProperty(replace(definition,size=.3)))
    with pytest.raises(ValueError,match='changed'):dialog.save()
    create=MeshSizeDialog(window);create.size.setText('-2')
    with pytest.raises(ValueError):create.save()
    create.size.setText('1');window.new_project()
    with pytest.raises(ValueError,match='changed'):create.save()


def test_category_rightpress_does_not_clear_canvas_selection(window,qapp):
    _,refs=geometry(window);window.selection.restore((refs[2],));tree=window.tree;group=tree._groups['Mesh sizes'];tree.scrollTo(group.index());qapp.processEvents()
    QTest.mousePress(tree.viewport(),Qt.RightButton,pos=tree.visualRect(group.index()).center());QTest.mouseRelease(tree.viewport(),Qt.RightButton,pos=tree.visualRect(group.index()).center())
    assert tuple(window.selection.items)==(refs[2],)
    assert tree.category_name(group.index())=='Mesh sizes'


def test_named_menu_filters_points_and_refuses_stale_and_read_only(window,qapp):
    _,refs=geometry(window);definition=window.run(SetMeshSizeProperty(MeshSizeProperty('Fine',.2)));window.selection.restore((refs[0],))
    menu=window.tree.property_menu(('mesh_size_properties',definition.id));action=next(a for a in menu.actions() if a.text()=='Apply to selection');assert not action.isEnabled()
    from anyfem.property_assignment import SelectionAssignment
    captured=SelectionAssignment.capture(window.project,'mesh_size_properties',definition.id,(refs[1],refs[2]))
    with pytest.raises(PermissionError):captured.command(window.project,read_only=True)
    window.run(SetMeshSizeProperty(replace(definition,size=.3)))
    with pytest.raises(ValueError,match='changed'):captured.command(window.project)
