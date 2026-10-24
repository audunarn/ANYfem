import os
from dataclasses import replace
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('enable Qt tests explicitly',allow_module_level=True)
from test_qt_geometry_actions import qapp,window,geometry
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from anyfem import commands


def test_colour_legend_is_transparent_bounded_units_and_cleared_for_results(window,qapp):
    window.run(commands.AddStiffenedPanel(4,3,1,beam_section='stiffener',plate_thickness=.02,material='S355'))
    window.show_geometry();window.set_model_colour_mode('section');qapp.processEvents()
    label=window.viewport._colour_legend
    assert label.isVisible() and 'stiffener' in label.text()
    assert label.testAttribute(Qt.WA_TransparentForMouseEvents)
    assert label.x()+label.width()<=window.viewport.widget.width()
    window.viewport.set_visualization(replace(window.viewport.visualization,show_legend=False))
    assert not label.isVisible()
    window.viewport.set_visualization(replace(window.viewport.visualization,show_legend=True))
    window.show_geometry();assert label.isVisible()
    scene=window.viewport._scene.copy();scene.result_field=object();scene.legend={'title':'Stress','levels':[1.], 'unit':'MPa'}
    window.viewport._draw_legend(scene)
    assert not label.isVisible() and window.viewport.canvas._thickness_legend['title']=='Stress'
    window._view_mode='results';window._sync_view_mode_buttons()
    assert all(not a.isEnabled() for a in window._model_colour_actions.values())


def test_actual_toolbar_click_keeps_selection_and_default_cache(window,qapp):
    _,refs=geometry(window);window.selection.restore((refs[2],));window.show_geometry()
    original=[tuple(f.colors) for f in window._geometry_scene_cache[1].faces]
    action=window._model_colour_actions['thickness'];button=next(widget for widget in window.findChildren(__import__('PySide6.QtWidgets',fromlist=['QToolButton']).QToolButton) if widget.defaultAction() is action)
    QTest.mouseClick(button,Qt.LeftButton);qapp.processEvents()
    assert window._model_colour_mode=='thickness' and window.viewport._scene.legend['entries']
    assert tuple(window.selection)==(refs[2],)
    assert [tuple(f.colors) for f in window._geometry_scene_cache[1].faces]==original
    window._model_colour_actions['default'].trigger();qapp.processEvents()
    assert window.viewport._scene.legend is None
    assert [tuple(f.colors) for f in window.viewport._scene.faces]==original


def test_mesh_only_workflow_uses_available_section_information_without_geometry_frame(window,qapp):
    import numpy as np
    from anymesher.mesh import Mesh
    project=window.project
    project.geometry=type(project.geometry)();project.mesh_only=True
    project.edge_sections={90:'stiffener'}
    window.mesh=Mesh(nodes={1:np.array([0.,0.,0.]),2:np.array([2.,0.,0.])},beams={12:(1,2)},elements_of_edge={90:[12]})
    window._model_colour_mode='section';window.show_mesh();qapp.processEvents()
    assert window._view_mode=='mesh'
    assert any(text=='stiffener' for text,_ in window.viewport._scene.legend['entries'])
    window.set_model_colour_mode('density')
    assert any(text=='2 m' for text,_ in window.viewport._scene.legend['entries'])
    window.viewport.set_visualization(replace(window.viewport.visualization,legend_font_size=28))
    window.viewport._draw_legend(window.viewport._scene)
    assert window.viewport._colour_legend.height()<=max(24,window.viewport.widget.height()-24)


@pytest.mark.parametrize('font_size',[10,18,28])
def test_wrapped_colour_legend_includes_coarsest_level_without_clipping(window,qapp,font_size):
    from anyfem.ui.scene import Scene
    viewport=window.viewport
    viewport.set_visualization(replace(viewport.visualization,legend_width=190,legend_font_size=font_size))
    viewport.widget.resize(400,1800)
    scene=Scene(legend={'title':'Actual element size with a longer wrapped engineering title',
        'entries':[(text,'#c83232') for text in ['0.2 m','0.25 m','0.3 m','0.35 m','0.4 m','0.45 m','0.5 m']],
        'note':'Shell √area; beam length'})
    viewport._draw_legend(scene)
    label=viewport._colour_legend
    assert label.width()==190
    visible=label.property('visible_colour_entries')
    if font_size<28:
        assert visible==7 and '0.5 m' in label.text()
    else:
        assert visible<7 and ('+'+str(7-visible)+' more') in label.text()
        assert '0.5 m' in label.toolTip()
    assert label.height()>=label.heightForWidth(label.width())
    assert label.height()<=viewport.widget.height()-24
    # Smaller canvases remove complete rows and report the actual omitted count.
    viewport.widget.resize(300,140);viewport._position_colour_legend()
    count=label.property('visible_colour_entries')
    assert count<7 and ('+'+str(7-count)+' more') in label.text()
    assert label.height()<=116
