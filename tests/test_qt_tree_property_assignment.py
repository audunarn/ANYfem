import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('enable Qt tests explicitly',allow_module_level=True)
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_qt_geometry_actions import qapp,window,geometry
from anyfem.mesh.refinement import Refinement


def test_property_rightpress_keeps_canvas_selection_and_applies_chosen_row(window,qapp):
    _,refs=geometry(window);face=refs[2]
    window.selection.set_mode('face');window.selection.restore((face,));qapp.processEvents()
    tree=window.tree;section=window.project.plate_sections['plate'];item=tree._rows['Plate sections'][section.id][0]
    tree.expand(item.parent().index());tree.scrollTo(item.index());qapp.processEvents()
    before=tuple(window.selection.ordered_items);highlight=window.viewport.highlighted_tags
    QTest.mousePress(tree.viewport(),Qt.RightButton,pos=tree.visualRect(item.index()).center());qapp.processEvents()
    assert tuple(window.selection.ordered_items)==before and window.viewport.highlighted_tags==highlight
    QTest.mouseRelease(tree.viewport(),Qt.RightButton,pos=tree.visualRect(item.index()).center());qapp.processEvents()
    menu=tree.property_menu(('plate_sections',section.id));action=next(a for a in menu.actions() if a.text()=='Apply to selection')
    assert action.isEnabled();action.trigger();qapp.processEvents()
    assert window.project.face_sections[face.id]=='plate' and tuple(window.selection.ordered_items)==before
    window.undo();assert not window.project.face_sections
    window.redo();assert window.project.face_sections[face.id]=='plate'
    menu.deleteLater()


def test_mesh_size_rows_reuse_existing_policy_and_stale_document_refuses(window,qapp):
    _,refs=geometry(window);source=Refinement(size=.25,radius=.2,ref=refs[1],growth=1.7,name='Fine region')
    window.project.add_refinement(source);window.tree.refresh();qapp.processEvents()
    window.selection.set_mode('face');window.selection.restore((refs[2],))
    tree=window.tree;item=tree._rows['Mesh sizes'][id(source)][0]
    assert 'Fine region' in item.text() and '0.25' in item.text()
    menu=tree.property_menu(('refinements',id(source)));next(a for a in menu.actions() if a.text()=='Apply to selection').trigger();qapp.processEvents()
    assert len(window.project.refinements)==2 and window.project.refinements[0] is source
    assert window.project.refinements[-1].ref==refs[2] and window.project.refinements[-1].growth==1.7
    stale=tree.property_menu(('refinements',id(source)));window.new_project()
    next(a for a in stale.actions() if a.text()=='Apply to selection').trigger();qapp.processEvents()
    assert not window.project.refinements
    menu.deleteLater();stale.deleteLater()


def test_incompatible_selection_has_disabled_apply_explanation(window,qapp):
    _,refs=geometry(window);window.selection.set_mode('vertex');window.selection.restore((refs[0],))
    section=window.project.plate_sections['plate'];menu=window.tree.property_menu(('plate_sections',section.id))
    action=next(a for a in menu.actions() if a.text()=='Apply to selection')
    assert not action.isEnabled() and 'plates' in action.toolTip()
    assert any('Select one or more' in a.text() for a in menu.actions());menu.deleteLater()


def test_section_menu_retains_delete_edit_and_canvas_visibility_actions(window,qapp,monkeypatch):
    section=window.project.plate_sections['plate'];tree=window.tree
    menu=tree.property_menu(('plate_sections',section.id));labels={a.text():a for a in menu.actions()}
    assert {'Apply to selection','Delete','Edit / inspect','Hide selected','Show selected'}<=set(labels)
    deleted=[];monkeypatch.setattr(window,'_delete_tree_items',lambda keys:deleted.append(keys))
    labels['Delete'].trigger();assert deleted==[(f'plate_section:{section.id}',)]
    from dataclasses import replace
    window.project.plate_sections['plate']=replace(section,thickness=.02)
    labels['Delete'].trigger();assert len(deleted)==1
    menu.deleteLater()
