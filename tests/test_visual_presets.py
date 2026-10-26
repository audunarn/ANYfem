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
        assert style.antialiasing=='4x'
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
