"""Mesh-node display is an independent, session-scoped visualization option."""
import os
import numpy as np
import pytest
from anymesher.mesh import Mesh
from anyfem import Project
from anyfem.presentation.points import USER_POINT_GROUP
from anyfem.selection import MeshEntityRef
from anyfem.ui.scene import build_mesh_scene
from anyfem.ui.visualization import VisualizationStyle


def small_mesh():
    return Mesh(nodes={1:np.array((0.,0.,0.)), 2:np.array((2.,0.,0.)),
                       3:np.array((0.,1.,0.))}, tris={10:(1,2,3)})


def test_mesh_scene_default_compatibility_and_explicit_visibility():
    project=Project();geometry=project.geometry
    vertex=geometry.add_point(0,0,0)
    geometry.add_to_group(USER_POINT_GROUP, [geometry.entity_ref('vertex',vertex)])
    mesh=small_mesh();mesh.node_of_vertex[vertex]=1
    assert [p.ref.id for p in build_mesh_scene(project,mesh).points] == [1]
    hidden=build_mesh_scene(project,mesh,show_nodes=False)
    shown=build_mesh_scene(project,mesh,show_nodes=True)
    assert hidden.points == []
    assert {point.ref for point in shown.points} == {MeshEntityRef('node',i) for i in (1,2,3)}
    assert hidden.faces[0].polygon_owners == shown.faces[0].polygon_owners
    assert VisualizationStyle().show_mesh_nodes is False
    assert VisualizationStyle().show_result_nodes is False


@pytest.mark.skipif(os.environ.get('ANYFEM_RUN_QT_TESTS')!='1', reason='set ANYFEM_RUN_QT_TESTS=1')
def test_qt_mesh_visibility_toggle_modes_reset_and_selection(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from anyfem.io import recovery
    from anyfem.ui.qt.app import QtFemWindow
    monkeypatch.setattr(recovery, 'default_recovery_root', lambda: tmp_path/'recovery')
    app=QApplication.instance() or QApplication([])
    window=QtFemWindow(viewer_backend='software');window.show();app.processEvents()
    try:
        mesh=small_mesh();window.mesh=mesh;window.show_mesh();app.processEvents()
        panel=window.panels['Visualization'];toggle=panel.appearance['show_mesh_nodes']
        assert not toggle.isChecked() and not window.viewport._scene.points
        assert len(window.viewport._scene.faces)==1
        before=window.viewport.canvas.export_view_state().camera_position
        revision=window.session.revision
        window.selection.set_mode('node');window.selection.restore((MeshEntityRef('node',1),))
        toggle.setChecked(True);app.processEvents()
        assert window.viewport.visualization.show_mesh_nodes
        assert len(window.viewport._scene.points)==3
        x,y,_=window.viewport.canvas.project_point(mesh.nodes[1])
        from anyfem.selection import owner_to_ref
        hits=window.viewport.canvas.query_point(round(x), round(y))
        assert MeshEntityRef('node',1) in [owner_to_ref(hit.owner) for hit in hits]
        assert tuple(window.selection.ordered_items)==(MeshEntityRef('node',1),)
        assert window.mesh is mesh and window.session.revision is revision
        after=window.viewport.canvas.export_view_state().camera_position
        assert (after.x,after.y,after.z)==pytest.approx((before.x,before.y,before.z))
        assert not window.viewport.visualization.show_result_nodes
        window.show_geometry();window.show_mesh()
        assert len(window.viewport._scene.points)==3
        panel.appearance['show_result_nodes'].setChecked(True)
        assert len(window.viewport._scene.points)==3 and toggle.isChecked()
        toggle.setChecked(False)
        assert not window.viewport._scene.points
        assert tuple(window.selection.ordered_items)==(MeshEntityRef('node',1),)
        toggle.setChecked(True)
        path=tmp_path/'mesh-view.anyfem'
        window.save_project(path=str(path));window.new_project();window.open_project(str(path))
        window.mesh=mesh;window.show_mesh()
        assert toggle.isChecked() and len(window.viewport._scene.points)==3
        panel.reset()
        assert not toggle.isChecked() and not window.viewport._scene.points
        assert 'pick' in toggle.toolTip().lower()
    finally:
        window.session.mark_saved();window.close();app.processEvents()


@pytest.mark.skipif(os.environ.get('ANYFEM_RUN_QT_TESTS')!='1', reason='set ANYFEM_RUN_QT_TESTS=1')
def test_qt_inspection_mesh_toggle_and_refresh(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from anyfem.io import recovery
    from anyfem.ui.qt.app import QtFemWindow
    monkeypatch.setattr(recovery, 'default_recovery_root', lambda: tmp_path/'recovery')
    app=QApplication.instance() or QApplication([])
    window=QtFemWindow(viewer_backend='software');window.show();app.processEvents()
    try:
        window._inspection_mesh=small_mesh();window.show_inspection_mesh()
        assert window._view_mode=='inspection_mesh' and not window.viewport._scene.points
        window.panels['Visualization'].appearance['show_mesh_nodes'].setChecked(True)
        assert window._view_mode=='inspection_mesh' and len(window.viewport._scene.points)==3
        window.refresh_views()
        assert window._view_mode=='inspection_mesh' and len(window.viewport._scene.points)==3
    finally:
        window.session.mark_saved();window.close();app.processEvents()


def test_tk_appearance_controls_preserve_mesh_node_setting():
    from types import SimpleNamespace
    from anyfem.ui.panels import VisualizationPanel, ResultsPanel
    class Var:
        def __init__(self, value=None): self.value=value
        def get(self): return self.value
        def set(self, value): self.value=value
    names = ('_render_mode','_surface_opacity','_background_color','_edge_color','_edge_width',
             '_show_legend','_legend_font_size','_legend_width','_result_colormap','_show_mesh_nodes',
             '_show_result_nodes','_show_result_supports','_show_result_loads','_show_result_masses',
             '_show_imperfect_reference','_show_beam_sections','_geometry_detail')
    controls=SimpleNamespace(**{name:Var() for name in names})
    from dataclasses import replace
    style=replace(VisualizationStyle(),show_mesh_nodes=True)
    VisualizationPanel._load_style(controls,style)
    assert VisualizationPanel._style(controls).show_mesh_nodes
    controls._show_mesh_nodes.set(False)
    assert not VisualizationPanel._style(controls).show_mesh_nodes
    controls._show_result_legend=controls._show_legend
    controls._colormap=controls._result_colormap
    controls.app=SimpleNamespace(viewport=SimpleNamespace(visualization=style))
    assert ResultsPanel._visualization_style(controls).show_mesh_nodes
