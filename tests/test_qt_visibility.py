"""Hide/show shortcuts, tree feedback and viewport lifecycle in real Qt widgets."""
import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':
    pytest.skip('set ANYFEM_RUN_QT_TESTS=1',allow_module_level=True)
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt,QItemSelectionModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from anygeometry.entities import EntityRef
from anyfem import commands as cmd
from anyfem.ui.qt.app import QtFemWindow


@pytest.fixture(scope='module')
def qapp():return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp,tmp_path,monkeypatch):
    from anyfem.io import recovery
    monkeypatch.setattr(recovery,'default_recovery_root',lambda:tmp_path/'recovery')
    window=QtFemWindow(viewer_backend=os.environ.get('ANYFEM_QT_TEST_BACKEND','software'))
    window.show();window.activateWindow();qapp.processEvents()
    yield window
    window.session.mark_saved();window.close();qapp.processEvents()


def geometry(window):
    vertices=[window.run(cmd.AddPoint(x,y)) for x,y in ((0,0),(1,0),(1,1),(0,1))]
    face=window.run(cmd.AddPlate(vertices));edge=window.project.geometry.faces[face].loop[0].edge
    return tuple(EntityRef(kind,identifier) for kind,identifier in
                 (('vertex',vertices[0]),('edge',edge),('face',face)))


def shortcut(window,qapp,key,modifiers=Qt.ControlModifier):
    window.raise_();window.activateWindow()
    assert QTest.qWaitForWindowActive(window,2000)
    widget=window.viewport.canvas.event_widget;widget.setFocus();qapp.processEvents()
    assert widget.hasFocus()
    QTest.keyClick(widget,key,modifiers);qapp.processEvents()
    if window.viewport._requested_backend=='gpu':
        QTest.qWait(20)
        assert window.viewport.active_backend=='gpu'


@pytest.mark.parametrize('index,group',[(0,'Points'),(1,'Lines'),(2,'Plates')])
def test_shortcut_retains_selection_and_dims_each_entity(window,qapp,index,group):
    ref=geometry(window)[index];window.selection.set_mode(ref.kind);window.selection.select(ref)
    item=window.tree._rows[group][ref.id][0]
    history=window.commands.history();revision=window.project.geometry.revision
    shortcut(window,qapp,Qt.Key_H)
    assert window.viewport.visibility.is_hidden(ref) and ref in window.selection.items
    assert item.toolTip()=='Hidden in viewport' and item.foreground().color().name()=='#8794a6'
    assert not any(ref in (entry.owners or (entry.ref,)) for entries in
                   (window.viewport._scene.faces,window.viewport._scene.lines,window.viewport._scene.points) for entry in entries)
    shortcut(window,qapp,Qt.Key_H,Qt.ControlModifier|Qt.ShiftModifier)
    assert not window.viewport.visibility.is_hidden(ref) and item.toolTip()==''
    assert window.commands.history()==history and window.project.geometry.revision==revision
    assert window._actions['Save'].shortcut().toString()=='Ctrl+S'
    assert window._actions['Show selected'].shortcut().toString()=='Ctrl+Shift+H'
    assert window._actions['Save as'].shortcut().toString()=='Ctrl+Shift+S'


def test_mixed_set_children_hide_show_and_partial_feedback(window,qapp):
    refs=geometry(window);region=window.geometry_actions.new_set(refs,'Frame')
    item=window.tree._rows['Sets'][region.id][0]
    window.tree.selectionModel().select(item.index(),QItemSelectionModel.ClearAndSelect|QItemSelectionModel.Rows)
    shortcut(window,qapp,Qt.Key_H)
    assert all(window.viewport.visibility.is_hidden(ref) for ref in refs)
    assert item.toolTip()=='Hidden in viewport'
    assert all(item.child(i).toolTip()=='Hidden in viewport' for i in range(item.rowCount()))
    shortcut(window,qapp,Qt.Key_H,Qt.ControlModifier|Qt.ShiftModifier)
    assert not window.viewport.visibility.hidden and item.toolTip()==''
    window.viewport.visibility.set_hidden([refs[0]]);window.tree.refresh_visibility()
    assert item.toolTip()=='Partly hidden in viewport'
    assert item.foreground().style()==Qt.NoBrush


def test_feature_visibility_survives_refresh_and_renderer_change_but_not_new_project(window,qapp):
    feature=window.run(cmd.AddFeature('generator.plate',parameters={'length':2,'width':1}))
    item=window.tree._rows['Features'][feature.feature_id][0]
    window.tree.selectionModel().select(item.index(),QItemSelectionModel.ClearAndSelect|QItemSelectionModel.Rows)
    shortcut(window,qapp,Qt.Key_H)
    assert item.toolTip()=='Hidden in viewport' and not window.viewport._scene.faces
    window.show_geometry();window.tree.refresh();window.viewport.switch_backend('software')
    assert not window.viewport._scene.faces and window.viewport.visibility.hidden
    shortcut(window,qapp,Qt.Key_H,Qt.ControlModifier|Qt.ShiftModifier)
    assert window.viewport._scene.faces
    shortcut(window,qapp,Qt.Key_H);window.new_project();qapp.processEvents()
    assert not window.viewport.visibility.hidden
    geometry(window);assert window.viewport._scene.faces


def test_empty_selection_and_show_all_are_safe(window,qapp):
    refs=geometry(window);window.selection.clear()
    shortcut(window,qapp,Qt.Key_H);assert not window.viewport.visibility.hidden
    window.viewport.visibility.set_hidden(refs);window.viewport.refresh_visibility();window.tree.refresh_visibility()
    window._actions['Show all'].trigger();qapp.processEvents()
    assert not window.viewport.visibility.hidden and window.viewport._scene.faces


def test_partial_collapsed_cylinder_retains_visible_siblings_and_restores(window,qapp):
    from anygeometry import feature_entity_owners
    feature=window.run(cmd.AddCylinder(1,2,circumferential_segments=4))
    refs=[ref for ref,owner in feature_entity_owners(window.project.geometry).items()
          if owner==feature.feature_id and ref.kind=='face']
    assert len(refs)>1
    source=window.viewport._visibility_scene
    before=sum(len(patch.polygons) for patch in source.faces)
    window.selection.set_mode('face');window.selection.select(refs[0])
    shortcut(window,qapp,Qt.Key_H)
    after=sum(len(patch.polygons) for patch in window.viewport._scene.faces)
    assert 0<after<before
    assert all(refs[0] not in patch.owners for patch in window.viewport._scene.faces)
    window.viewport._begin_interaction_scene();window.viewport._restore_full_scene()
    assert sum(len(patch.polygons) for patch in window.viewport._scene.faces)==after
    shortcut(window,qapp,Qt.Key_H,Qt.ControlModifier|Qt.ShiftModifier)
    assert sum(len(patch.polygons) for patch in window.viewport._scene.faces)==before
    assert sum(len(patch.polygons) for patch in source.faces)==before


def test_mesh_visibility_does_not_leak_to_reused_node_ids(window,qapp):
    import numpy as np
    from anymesher import Mesh
    from anyfem.selection import MeshEntityRef
    from anyfem.ui.scene import Scene,PointMarker
    node=MeshEntityRef('node',1);geometry_ref=EntityRef('vertex',99)
    source=Scene(points=[PointMarker(node,np.zeros(3))])
    window.mesh=Mesh(nodes={1:np.zeros(3)})
    window.viewport.show(source)
    window.selection.set_mode('node');window.selection.select(node)
    shortcut(window,qapp,Qt.Key_H)
    assert not window.viewport._scene.points
    window.viewport.show(source);assert not window.viewport._scene.points
    window.viewport.visibility.set_hidden([geometry_ref])
    window.mesh=Mesh(nodes={1:np.ones(3)})
    window.viewport.show(source)
    assert window.viewport._scene.points and not window.viewport.visibility.is_hidden(node)
    assert window.viewport.visibility.is_hidden(geometry_ref)
    window.selection.select(node);shortcut(window,qapp,Qt.Key_H)
    window.active_job_id='another-result';window.viewport.show(source)
    assert window.viewport._scene.points and not window.viewport.visibility.is_hidden(node)
