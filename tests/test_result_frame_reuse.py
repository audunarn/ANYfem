"""Canonical frame preparation retains topology without retaining stale geometry."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from any3dview.retained import MeshHandle
from any3dview.selection import PickOwner,PickBinding
from anyfem.post.fields import Field
from anyfem.presentation.frame_updates import snapshot,prepare_frame
from anyfem.presentation.retained_results import prepare
from anyfem.presentation.viewport import SceneViewport
from anyfem.presentation.visualization import VisualizationStyle
from anyfem.ui.scene import Scene,FacePatch,MeshEntityRef,Polyline
from test_retained_viewer_ownership import View


class Canvas:
    backend_name='gpu'
    def __init__(self):self.handles=[]
    def add_mesh_arrays(self,mesh,**options):
        handle=MeshHandle(mesh);self.handles.append(handle);return handle
    def clear(self,**options):
        for handle in self.handles:handle.remove()
    def redraw(self):pass
    def set_mesh_style(self,*args,**options):pass


def setup():
    view=View(Canvas());view._visualization=VisualizationStyle()
    view._commercial_selection=True;view._selection_api={'PickOwner':PickOwner,'PickBinding':PickBinding}
    mesh=SimpleNamespace(shells={1:(1,2,3,4)},beams={})
    return view,mesh


def scene(mesh,z=0,value=2):
    points=np.array([[0.,0.,z],[1.,0.,z],[1.,1.,z],[0.,1.,z]])
    return Scene(faces=[FacePatch(MeshEntityRef('element',1),[points],['red'])],
        result_field=Field('sx','Pa',element_values={1:value}),result_mesh=mesh,
        result_scalar_range=(0.,10.),result_colormap=((0.,'#0000ff'),(1.,'#ff0000')))


def test_exact_canonical_arrays_and_owner_tables_then_position_only_publication():
    view,mesh=setup();first=scene(mesh)
    canonical=prepare(view,first);prepare_frame(snapshot(view),first)
    for (actual,options),(reference,expected) in zip(first._prepared_result_batches[1],canonical[1]):
        np.testing.assert_array_equal(actual.positions,reference.positions)
        np.testing.assert_array_equal(actual.triangles,reference.triangles)
        for index in range(len(actual.triangles)):
            assert options['owners'].owners_for('triangle',index)==expected['owners'].owners_for('triangle',index)
    view._render_scene(first)
    handles=tuple(view._retained_result[1]);indices=[handle.mesh.triangles for handle in handles]
    second=scene(mesh,.25,7)
    with patch('anyfem.presentation.frame_updates.prepare',side_effect=AssertionError('topology rebuilt')):
        prepare_frame(snapshot(view),second,first._retained_frame_layout)
    view._render_scene(second)
    assert tuple(view._retained_result[1])==handles
    for handle,triangles in zip(handles,indices):
        assert handle.mesh.triangles is triangles
        np.testing.assert_array_equal(handle.mesh.positions[:,2],.25)
        np.testing.assert_array_equal(handle.mesh.element_scalars,[7])
    for handle in view._retained_styles[3]:np.testing.assert_array_equal(handle.mesh.positions[:,2],.25)
    assert view._retained_styles[0] is view._retained_result


def test_source_ownership_styles_topology_and_overlay_changes_refuse_reuse():
    for change in ('source','owner','shape','style','overlay'):
        view,mesh=setup();first=scene(mesh);prepare_frame(snapshot(view),first)
        second=scene(mesh,.1)
        if change=='source':second.result_mesh=SimpleNamespace(shells=mesh.shells,beams={})
        if change=='owner':second.faces[0]=replace(second.faces[0],ref=MeshEntityRef('element',2))
        if change=='shape':second.faces[0].polygons[0]=second.faces[0].polygons[0][:3]
        if change=='style':view._visualization=replace(view._visualization,surface_opacity=.5)
        if change=='overlay':second.lines.append(Polyline(None,np.array([(0,0,0),(1,1,1)]),color='red',annotation=True))
        prepare_frame(snapshot(view),second,first._retained_frame_layout)
        assert second._retained_topology_key!=first._retained_topology_key,change


def test_custom_preparation_methods_stay_on_conservative_path():
    view,mesh=setup();view._face_style=lambda value:('',1.,False)
    assert snapshot(view) is None
