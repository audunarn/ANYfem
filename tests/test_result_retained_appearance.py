"""Appearance changes use the published snapshot and live Qt control routing."""
from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import pytest

from anyfem.post.fields import Field
from anyfem.presentation.viewport import SceneViewport
from anyfem.presentation.retained_results import snapshot_appearance_field
from anyfem.presentation.scene import Scene, FacePatch, RESULT_COLORMAPS
from anyfem.presentation.visualization import VisualizationStyle
from anyfem.presentation.visibility import SelectionVisibility
from anyfem.ui.scene import MeshEntityRef


class Handle:
    removed=False
    def __init__(self,value=2.):
        self.mesh=SimpleNamespace(element_scalars=np.array([value],dtype=np.float32))
        self.appearance=[];self.visible=True
    def set_visible(self,value):self.visible=value
    def update_appearance(self,**values):self.appearance.append(values)
    def update_scalar_fields(self,**values):pytest.fail('appearance replaced scalars')


class Canvas:
    backend_name='gpu';event_widget=SimpleNamespace();is_playing_animation=False
    def __init__(self):self.styles=[];self.draws=0;self.fail=None
    def set_mesh_style(self,handle,**values):
        if handle is self.fail:raise RuntimeError('refused')
        self.styles.append((handle,values))
    def set_background(self,color):self.background=color
    def redraw(self):self.draws+=1


def view():
    result=SceneViewport.__new__(SceneViewport);result.canvas=Canvas()
    result._visualization=VisualizationStyle();result.visibility=SelectionVisibility()
    field=Field('ux','m',element_values={1:2.})
    result._scene=Scene(faces=[FacePatch(MeshEntityRef('element',1),
        [np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]])],['red'])],
        result_field=field,result_mesh=SimpleNamespace(shells={1:(1,2,3)},beams={}),
        result_scalar_range=(2.,3.),result_colormap=RESULT_COLORMAPS['Cool-warm'],
        legend={'title':'Displayed UX','unit':'m'})
    result._scene._appearance_field=snapshot_appearance_field(field)
    surface,line,edge=Handle(),Handle(),Handle()
    result._retained_result=(b'identity',[surface,line],(np.array([1]),np.array([1])),result._scene.result_mesh)
    result._retained_result_canvas=result.canvas
    result._retained_styles=(result._retained_result,(surface,),(line,),(edge,),True)
    result._visibility_scene=result._scene
    result._draw_legend=lambda scene:None
    result._refresh_visualization_scene=lambda:pytest.fail('appearance rebuilt geometry')
    return result,surface,line,edge


@pytest.mark.parametrize('changes',[
    {'background':'#101010','edge_color':'#ffffff'}, {'edge_width':3},
    {'render_mode':'Wireframe'}, {'render_mode':'Shaded'},
    {'show_legend':False}, {'legend_font_size':18,'legend_width':300},
    {'show_model_lines':False}, {'surface_opacity':.25}, {'result_colormap':'Viridis'}])
def test_style_preserves_current_handles_and_scalar_storage(changes):
    result,surface,line,edge=view();retained=result._retained_result
    scalars=surface.mesh.element_scalars
    result.set_visualization(replace(result.visualization,**changes))
    assert result._retained_result is retained and surface.mesh.element_scalars is scalars
    assert edge.visible==(result.visualization.render_mode!='Shaded')
    assert line.visible==result.visualization.show_model_lines


def test_palette_limits_units_use_frozen_full_precision_snapshot():
    result,surface,line,edge=view();field=result._scene.result_field
    field.element_values[1]=99.  # Editing source does not edit displayed snapshot.
    scalar=surface.mesh.element_scalars
    assert result.update_result_appearance(limits=(1000.,4000.),display_units='Engineering (mm / MPa)',
                                           colormap=RESULT_COLORMAPS['Viridis'])
    assert surface.mesh.element_scalars is scalar and scalar[0]==2.
    assert result._scene.result_scalar_range==(1.,4.)
    assert result._scene.legend['unit']=='mm'
    assert result._scene.legend['title']=='Displayed UX'
    from anyfem.ui.scene import _ramp_color
    assert result._scene.faces[0].colors==[_ramp_color(1/3,RESULT_COLORMAPS['Viridis'])]
    assert result._visibility_scene is result._scene


@pytest.mark.parametrize('reason',['canvas','removed','edge_removed','hidden','interaction'])
def test_appearance_refuses_noncurrent_or_filtered_scene(reason):
    result,surface,line,edge=view()
    if reason=='canvas':result._retained_result_canvas=object()
    elif reason=='removed':surface.removed=True
    elif reason=='edge_removed':edge.removed=True
    elif reason=='hidden':result.visibility.hidden.add(('element',1))
    else:result._interaction_scene_active=True
    assert not result.update_result_appearance(colormap=RESULT_COLORMAPS['Viridis'])
    assert not surface.appearance


def test_refusal_rolls_back_style_and_appearance():
    result,surface,line,edge=view();old=result.visualization;result.canvas.fail=edge
    with pytest.raises(RuntimeError,match='refused'):
        result.set_visualization(replace(old,background='#111111',edge_color='#ffffff'))
    assert result.visualization is old
    assert result.canvas.styles[-1][1]['opacity']==1.
    assert result._retained_result is None  # Persistent rollback refusal invalidates cache.


def test_actual_qt_visualization_refuses_invalid_colour(qt_panel):
    from anyfem.ui.qt.tasks import VisualizationTask
    panel,qapp=qt_panel;result=panel.app.viewport;old=result.visualization
    visual=VisualizationTask(panel.app)
    visual.appearance['edge_color'].setText('not a colour')
    with pytest.raises(ValueError,match='invalid surface edge'):
        visual.apply()
    assert result.visualization is old and not result.canvas.styles
    visual.deleteLater();qapp.processEvents()


@pytest.mark.parametrize('limits',[(1.,1.),(3.,1.),(0.,float('nan'))])
def test_invalid_range_does_not_mutate_view(limits):
    result,surface,line,edge=view();scene=result._scene
    with pytest.raises(ValueError):result.update_result_appearance(limits=limits)
    assert result._scene is scene and not surface.appearance


@pytest.fixture
def qt_panel():
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    from anyfem.ui.qt.tasks import ResultsTask
    appqt=QApplication.instance() or QApplication([])
    viewport,*_=view()
    app=SimpleNamespace(session=object(),project=SimpleNamespace(document_id='document'),
        active_job_id='job',solution=object(),result_datasets={},_result_revision=1,
        _result_source_epoch=2,_view_mode='results',_closing=False,viewport=viewport,
        guarded=lambda fn:fn)
    panel=ResultsTask(app)
    app.panels={'Results':panel}
    panel._displayed_selection=panel._display_selection()
    panel.show_results=lambda:pytest.fail('appearance loaded a field')
    panel.inspect_quantity=lambda:pytest.fail('appearance loaded an inspection')
    yield panel,appqt
    panel.deleteLater();appqt.processEvents()


@pytest.mark.parametrize('control',['palette','units','limits','inspection_palette'])
def test_actual_qt_controls_recolour_without_result_requests(qt_panel,control):
    panel,appqt=qt_panel
    if control=='inspection_palette':
        panel._selected_entry=('live','stress',None)
        panel._displayed_selection=panel._display_selection()
    if control in {'palette','inspection_palette'}:panel.colors.setCurrentText('Viridis')
    elif control=='units':panel.units.setCurrentText('Engineering (mm / MPa)')
    else:
        panel.minimum.setText('1');panel.maximum.setText('4');panel.maximum.editingFinished.emit()
    appqt.processEvents()
    assert panel.app.viewport.canvas.draws==1


def test_source_epoch_change_requires_new_result_request(qt_panel):
    panel,appqt=qt_panel;requests=[]
    panel.show_results=lambda:requests.append('load')
    panel.app._result_source_epoch+=1
    panel.colors.setCurrentText('Viridis');appqt.processEvents()
    assert requests==['load'] and panel.app.viewport.canvas.draws==0


def test_results_palette_then_visualization_reset_recolours_once_each(qt_panel,monkeypatch):
    from anyfem.ui.qt.tasks import VisualizationTask
    from anyfem.presentation import retained_results
    panel,qapp=qt_panel;visual=VisualizationTask(panel.app)
    panel.app.panels['Visualization']=visual
    calls=[];original=retained_results.prepare_warm_field
    def prepare(*args,**kw):calls.append(kw['include_scalars']);return original(*args,**kw)
    monkeypatch.setattr(retained_results,'prepare_warm_field',prepare)
    panel.colors.setCurrentText('Viridis');qapp.processEvents()
    assert panel.app.viewport.visualization.result_colormap=='Viridis'
    assert visual.appearance['result_colormap'].currentText()=='Viridis'
    assert panel.app.viewport._scene.result_colormap==RESULT_COLORMAPS['Viridis']
    visual.reset();qapp.processEvents()
    assert panel.colors.currentText()=='Cool-warm'
    assert panel.app.viewport.visualization.result_colormap=='Cool-warm'
    assert panel.app.viewport._scene.result_colormap==RESULT_COLORMAPS['Cool-warm']
    assert calls==[False,False]
    visual.deleteLater();qapp.processEvents()


def test_snapshot_storage_is_in_publication_admission():
    import sys
    from anyfem.presentation.retained_results import prepared_publication_bytes,cpu_color_bytes
    result,*_=view();field=result._scene.result_field
    resident=prepared_publication_bytes(field,result._scene,4)
    assert resident>=4+cpu_color_bytes(result._scene)+sys.getsizeof(field.element_values)+sys.getsizeof(field.node_values)


def test_default_ownerless_attribute_lines_allow_retained_style(monkeypatch):
    from anyfem.ui.scene import Polyline
    from anyfem.presentation import retained_scene
    result,*_=view();scene=result._scene
    scene.lines.append(Polyline(None,np.array([[0.,0.,0.],[1.,1.,1.]]),'blue'))
    monkeypatch.setattr(retained_scene,'scene_key',lambda *a:None)
    result._pick_binding=lambda *a:None
    result._clear_hover_state=result._draw_construction_overlay=result._apply_highlight=lambda:None
    result._draw=lambda remaining:None
    result.canvas.clear=lambda **kw:None
    def add(mesh,**options):
        handle=Handle();handle.mesh=mesh;return handle
    result.canvas.add_mesh_arrays=add
    result._retained_result=None
    result._render_scene(scene)
    assert result._retained_styles[4]
    retained=result._retained_result
    result.set_visualization(replace(result.visualization,background='#111111',edge_color='#ffffff'))
    assert result._retained_result is retained


@pytest.mark.parametrize('change',['palette','units','limits','selection','none'])
def test_async_publication_preserves_captured_selection_and_appearance(qt_panel,change):
    from anyfem.application.workflow import WorkbenchWorkflow
    from anyfem.application.result_requests import Completion
    panel,qapp=qt_panel;app=panel.app;callbacks=[];published=[];replacements=[];cancelled=[]
    app.mesh=None
    app._result_loader=SimpleNamespace(request=lambda run,done,**kw:callbacks.append(done))
    app.set_status=lambda *a,**kw:pytest.fail('publication error')
    app._status_text='ready'
    panel.show_results=lambda:replacements.append('request')
    captured=panel._display_selection()
    WorkbenchWorkflow._request_result(app,lambda cancel:None,published.append,
        on_cancel=lambda:cancelled.append(True),_status_stamp=None)
    panel._syncing_controls=True
    if change=='palette':panel.colors.setCurrentText('Viridis')
    elif change=='units':panel.units.setCurrentText('Engineering (mm / MPa)')
    elif change=='limits':panel.minimum.setText('1');panel.maximum.setText('4')
    elif change=='selection':app._result_revision+=1
    panel._syncing_controls=False
    callbacks[0](Completion(value='candidate'))
    if change in {'palette','units','limits'}:
        assert replacements==['request'] and not published and cancelled
    else:
        assert published==['candidate'] and not replacements
        assert panel._displayed_selection==captured
        if change=='selection':assert panel._displayed_selection!=panel._display_selection()
