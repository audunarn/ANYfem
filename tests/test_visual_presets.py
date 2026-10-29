"""Presentation presets preserve engineering visibility and scalar settings."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from anyfem.presentation.visualization import VisualizationStyle,visual_preset,VISUAL_PRESETS
from anyfem.presentation.viewport import SceneViewport


def test_presets_preserve_engineering_settings_and_results_are_flat():
    source=replace(VisualizationStyle(),surface_opacity=.4,show_result_loads=False,
        show_beam_sections=False,result_colormap='Viridis',show_mesh_nodes=True)
    for name in VISUAL_PRESETS:
        style=visual_preset(name,source)
        for key in ('surface_opacity','show_result_loads','show_beam_sections','result_colormap','show_mesh_nodes'):
            assert getattr(style,key)==getattr(source,key)
        assert style.antialiasing==('Off' if name=='Simple' else '4x')
    simple=visual_preset('Simple',source)
    assert (simple.antialiasing,simple.lighting,simple.ambient_occlusion)==('Off','Flat','Off')
    assert not simple.background_gradient and not simple.feature_edges
    assert (simple.background,simple.edge_color,simple.render_mode)==('#fbfbfd','#4a6572','Shaded with edges')
    result=visual_preset('Results',source)
    assert result.lighting=='Flat' and not result.feature_edges and not result.background_gradient
    assert result.ambient_occlusion=='Off'
    with pytest.raises(ValueError):visual_preset('Unknown')
    with pytest.raises(ValueError):replace(source,antialiasing='8x')


def test_quality_applies_once_per_canvas_or_policy_change():
    calls=[]
    canvas=lambda:SimpleNamespace(set_render_quality=lambda **kw:calls.append(('quality',kw)),
        set_shading=lambda enabled:calls.append(('shading',enabled)),set_light=lambda **kw:calls.append(('light',kw)))
    view=SimpleNamespace(canvas=canvas());style=visual_preset('Model')
    SceneViewport._apply_render_quality(view,style)
    assert calls[0][1]==dict(samples=4,gradient=True,feature_edges=True,ambient_occlusion='Low',ao_strength=.8,ao_radius=24)
    count=len(calls);SceneViewport._apply_render_quality(view,style);assert len(calls)==count
    SceneViewport._apply_render_quality(view,visual_preset('Results'))
    assert calls[-1]==('shading',False)
    view.canvas=canvas();SceneViewport._apply_render_quality(view,style);assert len(calls)>count


def test_result_contours_bypass_ao_and_geometry_restores_it():
    settings=[]
    view=SimpleNamespace(canvas=SimpleNamespace(set_render_quality=lambda **kw:settings.append(kw)),
        _scene=SimpleNamespace(result_field=object()))
    style=visual_preset('Model')
    SceneViewport._apply_render_quality(view,style);assert settings[-1]['ambient_occlusion']=='Off'
    view._scene=SimpleNamespace(result_field=None)
    SceneViewport._apply_render_quality(view,style);assert settings[-1]['ambient_occlusion']=='Low'


def test_mesh_scene_keeps_element_edges_and_restores_geometry_policy():
    from anyfem.ui.scene import Scene
    from anyfem.presentation.interaction_scene import camera_scene
    from anyfem.presentation.visibility import SelectionVisibility
    settings=[]
    view=SimpleNamespace(canvas=SimpleNamespace(set_render_quality=lambda **kw:settings.append(kw)))
    style=visual_preset('Model')
    geometry=Scene();mesh=Scene(show_element_edges=True)
    assert not geometry.show_element_edges
    assert mesh.copy().show_element_edges
    assert SelectionVisibility().filter_scene(mesh).show_element_edges
    assert camera_scene(mesh).show_element_edges
    assert Scene().merge(mesh).show_element_edges
    for scene,expected in ((geometry,True),(mesh,False),(geometry,True)):
        view._scene=scene
        SceneViewport._apply_render_quality(view,style)
        assert settings[-1]['feature_edges'] is expected
    assert style.feature_edges  # The user's preference remains available in geometry view.


def test_geometry_associated_mesh_scene_marks_its_element_edges():
    from anyfem.model.project import Project
    from anyfem.ui.scene import build_mesh_scene
    from anymesher import Mesh
    import numpy as np
    project=Project()
    vertices=project.geometry.add_points([(0,0,0),(2,0,0),(2,1,0),(0,1,0)])
    face=project.geometry.add_face(project.geometry.add_polyline(vertices,close=True))
    mesh=Mesh(nodes={1:np.array([0.,0.,0.]),2:np.array([1.,0.,0.]),3:np.array([2.,0.,0.]),
                     4:np.array([0.,1.,0.]),5:np.array([1.,1.,0.]),6:np.array([2.,1.,0.])},
              quads={1:(1,2,5,4),2:(2,3,6,5)},elements_of_face={face:[1,2]})
    scene=build_mesh_scene(project,mesh,show_nodes=False)
    assert scene.show_element_edges and len(scene.faces[0].polygons)==2
    assert [tuple(owner.kind for owner in owners) for owners in scene.faces[0].polygon_owners]==[
        ('face','element','element_face'),('face','element','element_face')]
