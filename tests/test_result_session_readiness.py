"""Production lifecycle/transport regressions; no mechanical qualification."""
from types import SimpleNamespace,MethodType
from threading import Event,get_ident
from pathlib import Path
import json,time,weakref,gc
import numpy as np
import pytest
from anyfem.application.workflow import WorkbenchWorkflow
from anyfem.application.result_requests import ResultRequests,CommittedResult
from anyfem.ui.result_export import write_client_export
from anypost.errors import RequestCancelled

def host():
    queue=[];status=SimpleNamespace(value='')
    status.get=lambda:status.value
    app=SimpleNamespace(solution=object(),project=object(),mesh=object(),active_job_id='a',result_datasets={},_closing=False,
        scheduler=SimpleNamespace(call_later=lambda delay,fn:queue.append(fn)),callbacks=queue,_status=status)
    app.set_status=lambda text,**kw:setattr(status,'value',text)
    for name in ('_request_result','_take_retired_result_datasets','close_result_access','invalidate_result_requests','_set_result_warning'):
        setattr(app,name,MethodType(getattr(WorkbenchWorkflow,name),app))
    return app

def pump(app,condition):
    end=time.monotonic()+3
    while not condition():
        if app.callbacks:app.callbacks.pop(0)()
        assert time.monotonic()<end,'owner completion did not settle'
        time.sleep(.002)

def test_committed_export_reports_success_after_source_invalidation(tmp_path):
    app=host();out=[];path=tmp_path/'source.csv'
    app._request_result(lambda cancel:write_client_export(path,'source',cancel,source='job-a'),out.append,channel='export',ordered=True)
    while app._result_loader._tasks:time.sleep(.002)
    app.invalidate_result_requests(source=True);app.solution=object()
    pump(app,lambda:bool(out))
    assert out==[path] and path.read_text()=='source'
    app.close_result_access();app._result_retirement.result(timeout=2)

def test_precommit_cancel_preserves_previous_destination(tmp_path):
    path=tmp_path/'source.csv';path.write_text('previous');event=Event();event.set()
    with pytest.raises(RequestCancelled):write_client_export(path,'new',event,source='job-a')
    assert path.read_text()=='previous' and list(tmp_path.iterdir())==[path]

def test_cancel_after_atomic_replace_remains_committed(tmp_path,monkeypatch):
    import anyfem.ui.result_export as module
    event=Event();replace=module.os.replace
    def commit(*args):replace(*args);event.set()
    monkeypatch.setattr(module.os,'replace',commit)
    result=write_client_export(tmp_path/'output.csv','actual',event)
    assert isinstance(result,CommittedResult) and result.value.read_text()=='actual'

def test_close_noninterruptible_query_is_nonblocking_then_same_worker_retires():
    app=host();entered=Event();release=Event();closed=[];owner=get_ident()
    dataset=SimpleNamespace(close=lambda:closed.append(get_ident()),release_reader=lambda:None)
    app.result_datasets={'a':dataset}
    def load(cancel):entered.set();release.wait(2);return np.ones(2)
    cleanup=[];app._request_result(load,lambda value:pytest.fail('late publication'),dataset=dataset,on_cancel=lambda:cleanup.append(True))
    assert entered.wait(1)
    started=time.monotonic();app.close_result_access()
    assert time.monotonic()-started<.2 and cleanup==[True] and not closed
    assert not app._result_retirement.done()
    release.set();app._result_retirement.result(timeout=2)
    assert closed and closed[0]!=owner

def test_reactivation_waits_without_starting_second_worker():
    app=host();entered=Event();release=Event();out=[]
    app._request_result(lambda cancel:(entered.set(),release.wait(2))[1],out.append)
    assert entered.wait(1);old=app._result_loader
    app.close_result_access();app._request_result(lambda cancel:'fresh',out.append)
    assert app._result_loader is None
    release.set();pump(app,lambda:'fresh' in out)
    assert app._result_loader is not old
    app.close_result_access();app._result_retirement.result(timeout=2)

def test_job_switch_cancels_inspection_not_ordered_export():
    app=host();entered=Event();release=Event();out=[]
    app._request_result(lambda cancel:(entered.set(),release.wait(2))[1],out.append,channel='inspection',on_cancel=lambda:out.append('cancelled'))
    assert entered.wait(1);app.active_job_id='b';app.invalidate_result_requests(reads=True);release.set()
    pump(app,lambda:bool(out));assert out==['cancelled']
    app.close_result_access();app._result_retirement.result(timeout=2)

def test_routine_supersession_does_not_replace_unrelated_success_status():
    app=host();app._request_result(lambda cancel:'old',lambda value:None)
    while app._result_loader._tasks:time.sleep(.002)
    app.invalidate_result_requests(source=True);app.set_status('Mesh ready')
    for _ in range(3):
        if app.callbacks:app.callbacks.pop(0)()
    assert app._status.get()=='Mesh ready'
    app.close_result_access();app._result_retirement.result(timeout=2)

def test_result_warning_clears_only_its_own_status():
    app=host();app._set_result_warning('Partial result',error=True);app._set_result_warning()
    assert app._status.get()=='Results ready'
    app._set_result_warning('Stale result',error=True);app.set_status('Export completed');app._set_result_warning()
    assert app._status.get()=='Export completed'

def artifact(tmp_path,*,nan=False):
    import h5py
    from anyfem.io.artifacts import LazyResultDataset
    path=tmp_path/'source.h5'
    with h5py.File(path,'w') as h:
        h.attrs.update(schema='anyfem.result',version=1,complete=True,model_hash=np.bytes_(b'm'),mesh_hash='s')
        f=h.create_group('frames');f.attrs['kind']='load';f.create_dataset('values',data=[np.nan,np.nan] if nan else [0.,1.])
        g=h.create_group('fields/x');g.attrs.update(label='x',location='node',unit='Pa',components='["xx"]',frames='[NaN,NaN]' if nan else '[0.0,1.0]',provenance='{}')
        g.create_dataset('values',data=[[[-5.]],[[-7.]]]);h.create_dataset('tables/x_node_ids',data=[17])
    return LazyResultDataset(path)

def test_artifact_warm_cold_identity_errors_and_nan_full_axes_match(tmp_path):
    from anyfem.io.artifacts import ArtifactError
    dataset=artifact(tmp_path);identity=dataset.identity
    assert dataset.field('x').root_frame_index(1)==1
    for warm in (False,True):
        if warm:dataset.prepare_metadata()
        assert dataset.identity==identity and identity['frame_kind']=='load'
        for query in (lambda:dataset.metadata('absent'),lambda:dataset.history_shape('absent'),lambda:dataset.table_shape('absent')):
            with pytest.raises(ArtifactError):query()
    dataset.close()

def test_legacy_singleton_absolute_is_preserved_and_labelled(tmp_path):
    from anyfem.ui.scene import read_persisted_display_field,persisted_display_metadata
    dataset=artifact(tmp_path)
    field,descriptor=read_persisted_display_field(dataset,'x',frame=0)
    assert field.node_values=={17:5.}
    assert 'absolute scalar' in persisted_display_metadata(field,descriptor)[2]['title']
    raw=dataset.field('x').read(0);np.testing.assert_array_equal(raw,[[-5.]])
    dataset.close()

def test_tk_and_qt_use_same_descriptor_and_live_outcome_has_no_dataset(monkeypatch):
    from anyfem.ui import app,panels
    import anyfem.application.workflow as workflow
    assert app._WorkbenchField is workflow._WorkbenchField
    monkeypatch.setattr(panels,'nonlinear_path_summary',lambda *a,**kw:None)
    seen={};widget=SimpleNamespace(configure=lambda **kw:seen.update(kw))
    panel=SimpleNamespace(app=SimpleNamespace(active_job_id=None,submitted_input_reports={}),_outcome_status=widget,
        _outcome_values={},_outcome_progress=widget,_outcome_progress_text=widget,_outcome_reason=widget)
    panels.ResultsPanel._update_outcome(panel,SimpleNamespace(status='completed'))
    assert seen['value']==100.

def test_qt_gif_cancel_releases_images_and_nonspatial_play_stops():
    from anyfem.ui.qt.tasks import ResultsTask
    images=[object()];panel=SimpleNamespace(_gif_capture={'images':images})
    ResultsTask._cancel_gif_capture(panel);assert panel._gif_capture is None and images==[]
    stopped=[];canvas=SimpleNamespace(begin_animation_cache=lambda:None,stop_animation=lambda:stopped.append(True))
    app=SimpleNamespace(result_datasets={},active_job_id='a',shape_index=0,session=object(),_closing=False,viewport=SimpleNamespace(canvas=canvas))
    panel=SimpleNamespace(app=app,playback_fps=lambda:10.,frame_count=lambda:1,_set_frame_index=lambda value:None,
        show_results=lambda **kw:kw['on_cancel']())
    ResultsTask.play(panel);assert stopped==[True]


def test_paired_nan_full_axes_are_ordinal_without_inventing_values(tmp_path):
    dataset=artifact(tmp_path,nan=True)
    assert dataset.field('x').root_frame_index(1)==1
    dataset.prepare_metadata()
    assert dataset.field('x').root_frame_index(1)==1
    assert np.array_equal(dataset.field('x').read(frame=1),np.array([[-7.]]))
    dataset.close()


def test_transient_buffers_do_not_pin_scene_and_cpu_copy_stays_truthful():
    from anyfem.presentation.retained_results import prepare_warm_field,discard_prepared_field,prepared_publication_bytes
    from anyfem.presentation.viewport import SceneViewport
    from anyfem.ui.scene import Scene,Polyline
    from anyfem.post.fields import Field
    from anyfem.ui.scene import MeshEntityRef
    owner=MeshEntityRef('element',1)
    mesh=SimpleNamespace(nodes={1:(0,0,0),2:(1,0,0)},beams={1:(1,2)},shells={})
    old=Field('ux','m',element_values={1:0.})
    scene=Scene(lines=[Polyline(owner,np.array([[0.,0.,0.],[1.,0.,0.]]),color='#0000ff')],result_field=old,result_mesh=mesh)
    class Handle:
        def update_scalar_fields(self,**kw):self.values=kw['element_scalars']
        def update_appearance(self,**kw):pass
    handle=Handle();retained=(b'geometry', [handle],(np.array([1]),),mesh)
    field=Field('ux','m',element_values={1:1.})
    prepare_warm_field(field,scene,retained,limits=(0.,1.),colormap=((0.,'#0000ff'),(1.,'#ff0000')))
    assert field._display_buffers[0]==b'geometry' and field._display_buffers[2][1]==[(0,'#ff0000')]
    charge=prepared_publication_bytes(field,scene,4)
    view=SimpleNamespace(_scene=scene,_retained_result=retained,active_backend='gpu',_draw_legend=lambda scene:None,canvas=SimpleNamespace(redraw=lambda:None))
    view._retained_result_canvas = view.canvas
    assert SceneViewport.update_result_field(view,field,scalar_range=(0.,1.),colormap=((0.,'#0000ff'),(1.,'#ff0000')),legend={})
    assert scene.lines[0].color=='#0000ff' and view._scene.lines[0].color=='#ff0000'
    assert not hasattr(field,'_display_buffers') and prepared_publication_bytes(field,view._scene,4)==charge

def test_retirement_attempts_all_closes_and_can_reactivate_after_failure():
    app=host();closed=[]
    def broken():closed.append('broken');raise RuntimeError('close failed')
    app._request_result(lambda cancel:1,lambda value:None)
    while app._result_loader._tasks:time.sleep(.002)
    app.result_datasets={'a':SimpleNamespace(close=broken),'b':SimpleNamespace(close=lambda:closed.append('healthy'))}
    app.close_result_access();app._result_retirement.result(timeout=2)
    assert closed==['broken','healthy'] and app._result_retirement_errors==['RuntimeError: close failed']
    app.result_datasets={};out=[];app._request_result(lambda cancel:'fresh',out.append)
    pump(app,lambda:bool(out));assert out==['fresh']
    app.close_result_access();app._result_retirement.result(timeout=2)

def test_repeated_close_queues_new_dataset_on_same_retired_executor():
    app=host();out=[];entered=Event();release=Event()
    app._request_result(lambda cancel:(entered.set(),release.wait(2))[1],lambda value:None)
    assert entered.wait(1)
    app.result_datasets={'a':SimpleNamespace(close=lambda:out.append(('a',get_ident())))}
    app.close_result_access()
    app.result_datasets={'b':SimpleNamespace(close=lambda:out.append(('b',get_ident())))}
    app.close_result_access();release.set();app._result_retirement.result(timeout=2)
    assert [n for n,_ in out]==['a','b'] and len({thread for _,thread in out})==1
    app.result_datasets={'c':SimpleNamespace(close=lambda:out.append(('c',get_ident())))}
    app.close_result_access();app._result_retirement.result(timeout=2)
    assert out[-1]==('c',out[0][1])
    app._retired_result_loader.shutdown()

def test_retirement_reentrant_request_settles_cancel_callback():
    app=host();app._retiring_result_access=True;cancelled=[]
    assert app._request_result(lambda cancel:None,lambda value:None,on_cancel=lambda:cancelled.append(True)) is None
    assert cancelled==[True]

@pytest.mark.parametrize('newer',[False,True])
def test_ordered_precommit_cancel_is_visible_without_overwriting_new_status(newer):
    app=host();entered=Event();release=Event();logs=[]
    app.log=SimpleNamespace(appendPlainText=logs.append)
    app._request_result(lambda cancel:(entered.set(),release.wait(2))[1],lambda value:pytest.fail('cancelled publication'),channel='export',ordered=True)
    assert entered.wait(1)
    if newer:app.set_status('New solve completed')
    app.invalidate_result_requests(source=True);release.set()
    pump(app,lambda:bool(getattr(app,'_result_export_outcomes',[])))
    if newer:assert app._status.get()=='New solve completed' and logs==['Export cancelled before publication']
    else:assert app._status.get()=='Export cancelled before publication'
    app.close_result_access();app._result_retirement.result(timeout=2)

def test_conversion_refusal_before_gpu_mutation_preserves_cpu_and_legend(monkeypatch):
    from anyfem.presentation.viewport import SceneViewport
    from anyfem.presentation import retained_results
    from anyfem.ui.scene import Scene
    from anyfem.post.fields import Field
    from anypost.errors import WorkingSetError
    mutations=[];old=Scene(legend={'title':'old'})
    handle=SimpleNamespace(update_scalar_fields=lambda **kw:mutations.append('scalar'),update_appearance=lambda **kw:mutations.append('appearance'))
    field=Field('ux',node_values={1:1.});field._display_buffers=(b'obsolete',(),())
    view=SimpleNamespace(_scene=old,_retained_result=(b'current',[handle],(np.array([1]),),object()),active_backend='gpu')
    view.canvas = view._retained_result_canvas = SimpleNamespace()
    def refuse(*args,**kwargs):raise WorkingSetError('cap')
    monkeypatch.setattr(retained_results,'prepare_warm_field',refuse)
    with pytest.raises(WorkingSetError):SceneViewport.update_result_field(view,field,scalar_range=(0,1),colormap=(),legend={'title':'new'})
    assert mutations==[] and view._scene is old and old.legend=={'title':'old'}

def test_tk_export_binds_captured_job_even_if_worker_runs_after_switch(tmp_path,monkeypatch):
    from anyfem.ui import panels
    loads=[];written=[]
    app=SimpleNamespace(solution=object(),active_job_id='a',shape_index=0,project=SimpleNamespace(name='p'),
        _request_result=lambda load,publish,**kw:loads.append(load),set_status=lambda *a:None)
    panel=SimpleNamespace(app=app,_require_solution=lambda:object(),field_name=lambda:'ux')
    monkeypatch.setattr(panels.filedialog,'asksaveasfilename',lambda **kw:str(tmp_path/'source.csv'))
    monkeypatch.setattr(panels,'field_to_csv',lambda *a:'values')
    from anyfem.ui import result_export
    original=result_export.write_client_export
    monkeypatch.setattr(result_export,'write_client_export',lambda *a,**kw:(written.append(kw['source']),original(*a,**kw))[1])
    panels.ResultsPanel._export_field(panel);app.active_job_id='b';loads[0](Event())
    assert written==['a']

def test_real_tk_status_port_cget_clears_only_own_warning():
    app=host();text={'value':'Partial analysis'}
    app._status=SimpleNamespace(cget=lambda key:text['value'])
    app.set_status=lambda value,**kw:text.update(value=value)
    app._set_result_warning('Partial analysis')
    app._set_result_warning();assert text['value']=='Results ready'

def test_terminal_retirement_exception_is_cleared_before_new_query():
    from concurrent.futures import Future
    app=host();retirement=Future();retirement.set_exception(RuntimeError('terminal cleanup'))
    app._result_retirement=retirement;out=[]
    app._request_result(lambda cancel:'fresh',out.append)
    pump(app,lambda:bool(out));assert out==['fresh'] and app._result_retirement is None
    app.close_result_access();app._result_retirement.result(timeout=2)

def test_one_dimensional_singleton_preserves_signed_scalar_and_legend(tmp_path):
    import h5py
    from anyfem.ui.scene import read_persisted_display_field,persisted_display_metadata
    dataset=artifact(tmp_path)
    with h5py.File(dataset.path,'r+') as h:
        del h['fields/x/values'];h['fields/x'].create_dataset('values',data=[[-5.],[-7.]])
    dataset.prepare_metadata()
    assert dataset._header['descriptors']['x']['component_shape']==[1]
    field,descriptor=read_persisted_display_field(dataset,'x',frame=0)
    assert field.node_values=={17:-5.} and 'absolute' not in persisted_display_metadata(field,descriptor)[2]['title']
    np.testing.assert_array_equal(dataset.field('x').read(0),[-5.])
    dataset.close()

@pytest.mark.parametrize('closing',[False,True])
def test_running_atomic_replace_retirement_reports_actual_commit_only(tmp_path,monkeypatch,closing):
    import anyfem.ui.result_export as module
    app=host();entered=Event();release=Event();owner=get_ident();published=[];cleanup=[]
    original=module.os.replace
    def blocked_replace(*args):entered.set();assert release.wait(3);original(*args)
    monkeypatch.setattr(module.os,'replace',blocked_replace)
    path=tmp_path/'source.csv'
    app._request_result(lambda cancel:write_client_export(path,'source',cancel,source='job-a'),
        lambda value:published.append((value,get_ident())),channel='export',ordered=True,on_cancel=lambda:cleanup.append(True))
    assert entered.wait(2);app._closing=closing
    app.close_result_access()
    assert cleanup==[True] and not published and 'cancelled' not in app._status.get().lower()
    release.set();app._result_retirement.result(timeout=3)
    assert path.read_text()=='source' and app._result_completed_exports[-1].committed
    if closing:
        assert not published and app._retired_result_loader.retirement_reports.empty()
    else:
        pump(app,lambda:bool(published))
        assert published==[(path,owner)] and 'Export completed from job-a' in app._status.get()
        assert cleanup==[True]
    app._retired_result_loader.shutdown()

def test_retiring_ordered_export_reports_confirmed_precommit_cancel_on_owner(tmp_path):
    app=host();entered=Event();release=Event();cleanup=[]
    def load(cancel):entered.set();assert release.wait(3);return write_client_export(tmp_path/'source.csv','source',cancel)
    app._request_result(load,lambda value:pytest.fail('cancelled write'),channel='export',ordered=True,on_cancel=lambda:cleanup.append(True))
    assert entered.wait(2);app.close_result_access()
    assert not getattr(app,'_result_export_outcomes',[]) and cleanup==[True]
    release.set();app._result_retirement.result(timeout=3)
    pump(app,lambda:bool(getattr(app,'_result_export_outcomes',[])))
    assert app._status.get()=='Export cancelled before publication' and not (tmp_path/'source.csv').exists() and cleanup==[True]
    app._retired_result_loader.shutdown()

def actual_status_setter(app,frontend):
    if frontend=='tk':
        from anyfem.ui.app import AnyFemApp
        text={'value':''}
        app._status=SimpleNamespace(cget=lambda key:text['value'],configure=lambda **kw:text.update(value=kw['text']))
        app.set_status=MethodType(AnyFemApp.set_status,app)
    else:
        from anyfem.ui.qt.app import QtFemWindow,_Value
        app._status=_Value('');app.statusBar=lambda:SimpleNamespace(showMessage=lambda text:None)
        app.set_status=MethodType(QtFemWindow.set_status,app)

@pytest.mark.parametrize('frontend',['tk','qt'])
def test_identical_newer_frontend_status_owns_warning_and_cancel(frontend):
    app=host();actual_status_setter(app,frontend)
    app._error_diagnostics=[];app._view_mode='results'
    app._set_result_warning('Same text')
    first=app._status_revision;app.set_status('Same text');assert app._status_revision>first
    app._set_result_warning();assert WorkbenchWorkflow._result_status_text(app)=='Same text'
    entered=Event();release=Event();reports=[]
    app.log=SimpleNamespace(appendPlainText=reports.append)
    app._request_result(lambda cancel:(entered.set(),release.wait(3))[1],lambda value:pytest.fail('cancelled publication'),channel='export',ordered=True)
    assert entered.wait(2);revision=app._status_revision;app.set_status('Same text')
    app.invalidate_result_requests(source=True);release.set()
    pump(app,lambda:bool(getattr(app,'_result_export_outcomes',[])))
    assert app._status_revision==revision+1 and WorkbenchWorkflow._result_status_text(app)=='Same text'
    assert reports==['Same text','Export cancelled before publication'] if frontend=='qt' else reports==['Export cancelled before publication']
    app.close_result_access();app._result_retirement.result(timeout=2);app._retired_result_loader.shutdown()

def test_retired_report_empty_race_still_delivers_committed_export(tmp_path,monkeypatch):
    import anyfem.ui.result_export as module
    from queue import Empty,SimpleQueue
    app=host();entered=Event();release=Event();published=[];replace=module.os.replace
    def blocked(*args):entered.set();assert release.wait(4);replace(*args)
    monkeypatch.setattr(module.os,'replace',blocked)
    path=tmp_path/'source.csv'
    app._request_result(lambda cancel:write_client_export(path,'actual',cancel,source='a'),published.append,channel='export',ordered=True)
    assert entered.wait(2)
    class EmptyRace:
        def __init__(self):self.queue=SimpleQueue();self.fired=False
        def put(self,value):self.queue.put(value)
        def get_nowait(self):
            try:return self.queue.get_nowait()
            except Empty:
                if not self.fired:
                    self.fired=True;release.set();app._result_retirement.result(timeout=3)
                raise
    queue=EmptyRace();app._result_loader.retirement_reports=queue
    app.close_result_access();assert queue.fired and path.read_text()=='actual'
    pump(app,lambda:bool(published));assert published==[path]
    app._retired_result_loader.shutdown()

@pytest.mark.parametrize('retire',[True,False])
def test_ordered_replace_error_is_failure_after_retire_or_source_invalidation(tmp_path,monkeypatch,retire):
    import anyfem.ui.result_export as module
    app=host();entered=Event();release=Event();errors=[];cleanup=[]
    original_status=app.set_status
    def status(text,**kw):errors.append((text,kw.get('error',False)));original_status(text,**kw)
    app.set_status=status
    def fail(*args):entered.set();assert release.wait(4);raise PermissionError('destination busy')
    monkeypatch.setattr(module.os,'replace',fail)
    path=tmp_path/'source.csv';path.write_text('previous')
    app._request_result(lambda cancel:write_client_export(path,'new',cancel),lambda value:pytest.fail('failed write publication'),
        channel='export',ordered=True,on_cancel=lambda:cleanup.append(True))
    assert entered.wait(2);loader=app._result_loader
    if retire:app.close_result_access()
    else:app.invalidate_result_requests(source=True);app.solution=object()
    release.set()
    if retire:app._result_retirement.result(timeout=3)
    pump(app,lambda:bool(getattr(app,'_result_export_outcomes',[])))
    assert 'Export failed: PermissionError: destination busy' in app._status.get()
    assert 'cancelled' not in app._status.get().lower() and errors[-1][1] and cleanup==[True]
    assert loader.failures==[{'type':'PermissionError','message':'destination busy'}]
    assert path.read_text()=='previous' and list(tmp_path.iterdir())==[path]
    if not retire:app.close_result_access();app._result_retirement.result(timeout=2)
    app._retired_result_loader.shutdown()

def test_late_settled_dispatch_does_not_pin_transient_field_after_close():
    from anyfem.post.fields import Field
    app=host();load_entered=Event();load_release=Event();dispatch_entered=Event();dispatch_release=Event();references=[]
    def load(cancel):
        field=Field('ux',node_values={1:1.});field._display_buffers=(b'g',(np.ones(64,dtype=np.float32),),([],[]))
        references.append(weakref.ref(field));load_entered.set();assert load_release.wait(4);return field
    app._request_result(load,lambda value:pytest.fail('retired publication'))
    assert load_entered.wait(2);loader=app._result_loader;queue=app._result_queue
    def delayed_dispatch(callback):dispatch_entered.set();assert dispatch_release.wait(4);queue.put(callback)
    loader._dispatch=delayed_dispatch;load_release.set();assert dispatch_entered.wait(2)
    started=time.monotonic();app.close_result_access();assert time.monotonic()-started<.2
    dispatch_release.set();app._result_retirement.result(timeout=3);gc.collect()
    assert references[0]() is None and not queue.empty() and app._result_loader is None
    loader.shutdown()

@pytest.mark.parametrize('frontend',['tk','qt'])
def test_late_committed_export_keeps_new_solve_status_and_reports_success(tmp_path,monkeypatch,frontend):
    import anyfem.ui.result_export as module
    app=host();actual_status_setter(app,frontend);logs=[];app.log=SimpleNamespace(appendPlainText=logs.append)
    app.set_status('Waiting for export');entered=Event();release=Event();published=[];replace=module.os.replace
    def blocked(*args):entered.set();assert release.wait(4);replace(*args)
    monkeypatch.setattr(module.os,'replace',blocked)
    path=tmp_path/'source.csv'
    app._request_result(lambda cancel:write_client_export(path,'actual',cancel,source='old-job'),published.append,channel='export',ordered=True)
    assert entered.wait(2);app.close_result_access();app.set_status('New solve completed');revision=app._status_revision
    release.set();app._result_retirement.result(timeout=3);pump(app,lambda:bool(published))
    assert published==[path] and path.read_text()=='actual'
    assert WorkbenchWorkflow._result_status_text(app)=='New solve completed' and app._status_revision==revision
    assert any(f'Export completed from old-job: {path}'==message for message in logs)
    app._retired_result_loader.shutdown()

def test_recent_committed_ledger_is_bounded_but_all_twenty_files_remain(tmp_path):
    app=host();published=[]
    for index in range(20):
        path=tmp_path/f'{index}.csv'
        app._request_result(lambda cancel,path=path,index=index:write_client_export(path,str(index),cancel,source='source'),published.append,channel='export',ordered=True)
        pump(app,lambda:len(published)==index+1)
    assert len(app._result_loader.outcomes)==10 and app._result_loader.committed_count==20
    assert [result.value for result in app._result_loader.outcomes]==published[-10:]
    assert all((tmp_path/f'{index}.csv').read_text()==str(index) for index in range(20))
    app.close_result_access();app._result_retirement.result(timeout=2);app._retired_result_loader.shutdown()

def test_late_export_diagnostics_do_not_trim_unrelated_diagnostics():
    from anyfem.diagnostics import ErrorDiagnostic
    app=host();original=[ErrorDiagnostic.capture(f'solver diagnostic {i}') for i in range(10)]
    app._error_diagnostics=list(original);stamp=WorkbenchWorkflow._result_status_stamp(app)
    app.set_status('New solve');app.log=SimpleNamespace(appendPlainText=lambda message:None)
    for index in range(20):WorkbenchWorkflow._result_export_notice(app,f'Export failure {index}',stamp,error=True)
    assert app._error_diagnostics==original and len(app._result_export_diagnostics)==10 and len(app._result_export_outcomes)==10

def test_already_committed_closing_settlement_cleans_capture_once(tmp_path):
    app=host();cleanup=[];path=tmp_path/'source.csv'
    app._request_result(lambda cancel:write_client_export(path,'actual',cancel),lambda value:pytest.fail('closed publication'),
        channel='export',ordered=True,on_cancel=lambda:cleanup.append(True))
    while app._result_loader._tasks:time.sleep(.002)
    app._closing=True;app.close_result_access();app._result_retirement.result(timeout=2)
    assert cleanup==[True] and path.read_text()=='actual' and app._retired_result_loader.committed_count==1
    app._retired_result_loader.shutdown()

@pytest.mark.parametrize('outcome',['commit','error','cancel'])
def test_deferred_ordered_export_preserves_original_status_ownership(tmp_path,outcome):
    from concurrent.futures import Future
    app=host();actual_status_setter(app,'tk');app._error_diagnostics=[];app._view_mode='results'
    reports=[];app.log=SimpleNamespace(appendPlainText=reports.append);app.set_status('Waiting')
    retirement=Future();app._result_retirement=retirement;published=[]
    path=tmp_path/'source.csv'
    def load(cancel):
        if outcome=='error':raise PermissionError('destination busy')
        if outcome=='cancel':raise RequestCancelled('explicit cancellation')
        return write_client_export(path,'actual',cancel,source='original-source')
    app._request_result(load,published.append,channel='export',ordered=True)
    app.set_status('New solve completed');revision=app._status_revision;retirement.set_result(None)
    pump(app,lambda:bool(getattr(app,'_result_export_outcomes',[])))
    assert WorkbenchWorkflow._result_status_text(app)=='New solve completed' and app._status_revision==revision
    assert reports and ('completed' if outcome=='commit' else 'failed' if outcome=='error' else 'cancelled') in reports[-1]
    if outcome=='commit':assert published==[path] and path.read_text()=='actual'
    else:assert published==[] and not path.exists()
    app.close_result_access();app._result_retirement.result(timeout=2);app._retired_result_loader.shutdown()

@pytest.mark.parametrize('pending',[False,True])
def test_shutdown_commit_is_logged_exactly_once_with_source_and_value(tmp_path,monkeypatch,caplog,pending):
    import logging
    import anyfem.ui.result_export as module
    caplog.set_level(logging.INFO)
    app=host();cleanup=[];path=tmp_path/'source.csv';entered=Event();release=Event();replace=module.os.replace
    if pending:
        def blocked(*args):entered.set();assert release.wait(3);replace(*args)
        monkeypatch.setattr(module.os,'replace',blocked)
    app._request_result(lambda cancel:write_client_export(path,'actual',cancel,source='captured-source'),
        lambda value:pytest.fail('closed publication'),channel='export',ordered=True,on_cancel=lambda:cleanup.append(True))
    if pending:assert entered.wait(2)
    else:
        while app._result_loader._tasks:time.sleep(.002)
    app._closing=True;app.close_result_access();release.set();app._result_retirement.result(timeout=2)
    messages=[record.getMessage() for record in caplog.records if 'Export completed from captured-source:' in record.getMessage()]
    assert messages==[f'Export completed from captured-source: {path}'] and cleanup==[True] and path.read_text()=='actual'
    app._retired_result_loader.shutdown()

@pytest.mark.parametrize('closing',[False,True])
def test_deferred_ordered_owner_change_reports_cancel_without_loading_stale_source(tmp_path,closing):
    from concurrent.futures import Future
    app=host();actual_status_setter(app,'tk');app._error_diagnostics=[];app._view_mode='results'
    logs=[];app.log=SimpleNamespace(appendPlainText=logs.append);app.set_status('Waiting')
    retirement=Future();app._result_retirement=retirement;loads=[];cleanup=[]
    path=tmp_path/'source.csv';path.write_text('previous')
    def load(cancel):loads.append(True);return write_client_export(path,'stale',cancel)
    app._request_result(load,lambda value:pytest.fail('stale publication'),channel='export',ordered=True,on_cancel=lambda:cleanup.append(True))
    app.solution=object();app.set_status('New solve completed');revision=app._status_revision
    app._closing=closing;retirement.set_result(None)
    pump(app,lambda:bool(cleanup))
    assert cleanup==[True] and loads==[] and path.read_text()=='previous'
    assert WorkbenchWorkflow._result_status_text(app)=='New solve completed' and app._status_revision==revision
    if closing:assert not getattr(app,'_result_export_outcomes',[]) and logs==[]
    else:assert app._result_export_outcomes==['Export cancelled before publication'] and logs==['Export cancelled before publication']

@pytest.mark.parametrize('invalidation',['source','display','reads'])
def test_deferred_ordered_source_epoch_survives_repeated_waits_and_display_changes(tmp_path,invalidation):
    from concurrent.futures import Future
    app=host();app.solution=None;actual_status_setter(app,'tk');app._error_diagnostics=[];app._view_mode='results'
    logs=[];app.log=SimpleNamespace(appendPlainText=logs.append);app.set_status('Waiting')
    retirement=Future();app._result_retirement=retirement;loads=[];cleanup=[];published=[]
    path=tmp_path/'source.csv';path.write_text('previous')
    def load(cancel):loads.append(True);return write_client_export(path,'actual',cancel,source='stored source')
    owners=(app.solution,app.project,app.mesh)
    app._request_result(load,published.append,channel='export',ordered=True,on_cancel=lambda:cleanup.append(True))
    # Repeated waits must carry the original epoch rather than capture it again.
    app.callbacks.pop(0)();app.callbacks.pop(0)()
    app.invalidate_result_requests(source=invalidation=='source',reads=invalidation=='reads')
    assert all(a is b for a,b in zip(owners,(app.solution,app.project,app.mesh)))
    app.set_status('New solve completed');revision=app._status_revision;retirement.set_result(None)
    pump(app,lambda:bool(getattr(app,'_result_export_outcomes',[])))
    assert WorkbenchWorkflow._result_status_text(app)=='New solve completed' and app._status_revision==revision
    if invalidation=='source':
        assert cleanup==[True] and loads==[] and published==[] and path.read_text()=='previous'
        assert app._result_export_outcomes==['Export cancelled before publication'] and logs==['Export cancelled before publication']
    else:
        assert loads==[True] and cleanup==[] and published==[path] and path.read_text()=='actual'
        assert 'Export completed from stored source:' in logs[-1]
        app.close_result_access();app._result_retirement.result(timeout=2);app._retired_result_loader.shutdown()
