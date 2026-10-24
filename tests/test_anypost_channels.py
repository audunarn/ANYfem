"""Production consumer/channel regressions for the final review repairs."""
from types import SimpleNamespace, MethodType
from threading import Event
import time, json
import numpy as np
import pytest
from anyfem.application.workflow import WorkbenchWorkflow
from anyfem.post.fields import Field, evaluate_field
from anyfem.post.access import LiveResultAccess, result_access_scope, evaluate_envelope


def source():
    return SimpleNamespace(status='completed',fields={'ux':Field('ux','m',node_values={1:5.,2:7.}),
        'von_mises':Field('von_mises','Pa',node_values={1:5.,2:7.})},
        built=SimpleNamespace(mesh=SimpleNamespace(nodes={1:(),2:()},shells={1:()},beams={})))


def host():
    callbacks=[];statuses=[]
    app=SimpleNamespace(solution=source(),project=object(),mesh=object(),result_datasets={},
        _closing=False,scheduler=SimpleNamespace(call_later=lambda delay,fn:callbacks.append(fn)),
        set_status=lambda text,**kwargs:statuses.append((text,kwargs)),callbacks=callbacks,statuses=statuses)
    for name in ('_request_result','_take_retired_result_datasets','invalidate_result_requests','close_result_access'):
        setattr(app,name,MethodType(getattr(WorkbenchWorkflow,name),app))
    return app


def drain(app, predicate):
    until=time.monotonic()+3
    while not predicate():
        if app.callbacks:app.callbacks.pop(0)()
        if time.monotonic()>until:raise AssertionError('owner delivery did not settle')
        time.sleep(.002)


def test_production_unrelated_inspection_does_not_cancel_display():
    app=host();entered=Event();release=Event();shown=[]
    def display(cancel):entered.set();release.wait(2);assert not cancel.is_set();return 'field'
    app._request_result(display,shown.append,on_cancel=lambda:shown.append('cancelled'))
    assert entered.wait(2)
    app._request_result(lambda cancel:'inspector',shown.append,channel='inspection')
    release.set();drain(app,lambda:len(shown)==2)
    assert shown==['field','inspector']
    app.close_result_access()


def test_next_heavy_query_waits_for_owner_settlement_without_blocking_owner():
    app=host();out=[]
    app._request_result(lambda cancel:'display',out.append)
    def inspect(cancel):
        assert out==['display']
        return 'inspection'
    app._request_result(inspect,out.append,channel='inspection')
    drain(app,lambda:len(out)==2);assert out==['display','inspection'];app.close_result_access()


def test_dispatch_failure_releases_next_worker_gate(caplog):
    from anyfem.application.result_requests import ResultRequests
    queued=[];calls=[];finished=Event()
    def dispatch(callback):
        calls.append(True)
        if len(calls)==1:raise RuntimeError('dispatch failed')
        queued.append(callback)
    coordinator=ResultRequests(dispatch)
    coordinator.request(lambda cancel:None,lambda result:None)
    coordinator.request(lambda cancel:finished.set(),lambda result:None,channel='inspection')
    assert finished.wait(2)
    coordinator.close()
    assert 'dispatch failed' in caplog.text


def test_ordered_exports_survive_frame_invalidation_and_display(tmp_path):
    app=host();entered=Event();release=Event();out=[]
    def export(cancel):
        entered.set();release.wait(2);assert not cancel.is_set()
        (tmp_path/'actual.csv').write_text('source');return 'exported'
    app._request_result(export,out.append,channel='export',ordered=True)
    assert entered.wait(2)
    app.invalidate_result_requests()  # frame/control change, not source replacement
    app._request_result(lambda cancel:'new field',out.append)
    release.set();drain(app,lambda:len(out)==2)
    assert (tmp_path/'actual.csv').read_text()=='source' and out==['exported','new field']
    app.close_result_access()


def test_cancelled_display_settles_capture_once_even_after_queued_completion():
    app=host();out=[]
    app._request_result(lambda cancel:'old',out.append,on_cancel=lambda:out.append('cleanup'))
    time.sleep(.02)
    app._request_result(lambda cancel:'new',out.append)
    drain(app,lambda:len(out)==2)
    assert out==['cleanup','new']
    app.close_result_access()


def test_source_invalidation_explicitly_settles_ordered_export():
    app=host();entered=Event();release=Event();out=[]
    def read(cancel):entered.set();release.wait(2);return 'stale'
    app._request_result(read,out.append,channel='export',ordered=True,on_cancel=lambda:out.append('cleanup'))
    assert entered.wait(2);app.invalidate_result_requests(source=True);release.set()
    drain(app,lambda:bool(out));assert out==['cleanup'];app.close_result_access()


def test_accepted_publication_bytes_transfer_across_reader_mode_replacement():
    app=host();old=LiveResultAccess();old.commit_publication(12345);app._live_result_access=old
    app._live_access_revision=0;observed=[]
    app._request_result(lambda cancel:app._live_result_access._publication_bytes,observed.append,dataset=object())
    drain(app,lambda:bool(observed));assert observed==[12345];app.close_result_access()


def test_imported_nodal_stress_envelope_keeps_nodes_even_when_eid_collides():
    result=evaluate_envelope(source(),'von_mises')
    assert result.field.node_values=={1:5.,2:7.} and result.field.element_values=={}


def test_scoped_reduction_matches_public_refusal():
    access=LiveResultAccess()
    with result_access_scope(access),pytest.raises(ValueError):evaluate_field(source(),'ux',reduction='bogus')
    access.close()


def test_numpy_integer_local_frame_matches_python_integer(tmp_path):
    dataset=sidecar(tmp_path)
    np.testing.assert_array_equal(dataset.field('stress').read(np.int64(1)),dataset.field('stress').read(1))
    dataset.close()


def test_deferred_validator_governs_every_cold_header_and_retains_returned_path(tmp_path):
    from anyfem.io.artifacts import LazyResultDataset,ArtifactError
    def refuse():raise ArtifactError('validator refused')
    dataset=LazyResultDataset(tmp_path/'unadmitted.h5',validator=refuse)
    for read in (lambda:dataset.field_keys,lambda:dataset.frames,lambda:dataset.field('x').shape,lambda:dataset.identity,lambda:dataset.metadata("inputs")):
        with pytest.raises(ArtifactError,match='validator refused'):read()
    actual=sidecar(tmp_path);path=actual.path;actual.close()
    dataset=LazyResultDataset(tmp_path/'wrong.h5',validator=lambda:path)
    assert dataset.field_keys==('stress',) and dataset.path==path
    dataset.close()


def test_reader_open_failure_retires_only_session_created_recovery(monkeypatch):
    import anyfem.post.access as access_module
    shape=source();shape._stress=None
    provider=access_module.ShapeProvider
    def create(*args,**kwargs):
        value=provider(*args,**kwargs);shape._stress=SimpleNamespace(values=np.ones(8));return value
    monkeypatch.setattr(access_module,'ShapeProvider',create)
    monkeypatch.setattr(access_module,'open_memory_results',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('open refused')))
    access=LiveResultAccess()
    with pytest.raises(RuntimeError,match='open refused'):access.field(shape,'ux')
    assert shape._stress is None and not access._recovery_resident;access.close()


def test_native_sample_magnitude_uses_named_translations_not_first_three():
    from anyfem.ui.scene import _artifact_scalar_rows
    labels=('rx','ry','rz','ux','uy','uz')
    values=np.array([[[100.,200.,300.,3.,4.,0.],[9.,8.,7.,0.,0.,2.]]])
    np.testing.assert_array_equal(_artifact_scalar_rows(values,labels,None,(1,)),[5.])


def test_exact_overlay_digest_detects_interior_and_last_bit_changes():
    from anyfem.presentation.retained_results import _digest_value
    import hashlib
    values=np.zeros((600,3));changed=values.copy();changed[500,0]=np.nextafter(0.,1.)
    a=hashlib.sha256();b=hashlib.sha256();_digest_value(a,values);_digest_value(b,changed)
    assert a.digest()!=b.digest()


def test_partial_and_stale_warning_preserves_both_conditions():
    calls=[];app=SimpleNamespace(set_status=lambda text,**kw:calls.append((text,kw)))
    WorkbenchWorkflow._warn_persisted_status(app,SimpleNamespace(identity={'partial':True}),True)
    assert 'Partial' in calls[0][0] and 'Stale' in calls[0][0] and calls[0][1]['error']


def sidecar(tmp_path):
    import h5py
    from anyfem.io.artifacts import LazyResultDataset
    path=tmp_path/'source.h5'
    with h5py.File(path,'w') as h:
        h.attrs.update(schema='anyfem.result',version=1,complete=True,model_hash='m',mesh_hash='s')
        h.create_dataset('frames/values',data=[0.,1.,2.,3.])
        g=h.create_group('fields/stress');g.create_dataset('values',data=[[[10.]],[[30.]]])
        g.attrs.update(label='stress',location='node',unit='Pa',components='["xx"]',frames='[1.0,3.0]',provenance='{}')
        h.create_dataset('tables/stress_node_ids',data=[1])
    return LazyResultDataset(path)


@pytest.mark.parametrize('channel,source_invalidation,expected', [('display',False,'cancelled'),('export',True,'cancelled'),('export',False,'value')])
def test_invalidation_after_worker_completion_before_owner_drain(channel,source_invalidation,expected):
    app=host();out=[]
    app._request_result(lambda cancel:'value',out.append,channel=channel,ordered=channel=='export',on_cancel=lambda:out.append('cancelled'))
    until=time.monotonic()+2
    while app._result_loader._tasks:
        assert time.monotonic()<until;time.sleep(.002)
    app.invalidate_result_requests(source=source_invalidation)
    drain(app,lambda:bool(out));assert out==[expected];app.close_result_access()


def test_tk_retained_report_uses_captured_owner_context(monkeypatch,tmp_path):
    import anyfem.ui.panels as panels
    captured=[]
    monkeypatch.setattr(panels.filedialog,'asksaveasfilename',lambda **kw:str(tmp_path/'report.md'))
    monkeypatch.setattr(panels,'result_report_context',lambda dataset,**kw:captured.append(kw) or kw)
    monkeypatch.setattr('anyfem.post.report.result_report_markdown',lambda dataset,**kw:'source report')
    app=SimpleNamespace(active_job_id='j',result_datasets={'j':object()},solution=None,project=SimpleNamespace(name='p',jobs={'j':object()}),
        session=SimpleNamespace(revision=SimpleNamespace(document_hash='captured')),set_status=lambda *a,**kw:None,_job_is_stale=lambda job:True)
    def execute(load,publish,**kw):
        app.session=SimpleNamespace(revision=SimpleNamespace(document_hash='later'));publish(load(Event()))
    app._request_result=execute
    panels.ResultsPanel._export_report(SimpleNamespace(app=app))
    assert captured[0]['current_document_hash']=='captured' and captured[0]['stale'] is True


def test_retirement_snapshot_is_published_before_enqueue():
    from anyfem.application.workflow import _WorkbenchField
    old=object();new=object();observed=[]
    workbench=SimpleNamespace(result_datasets={'old':old},update=lambda key,value:setattr(workbench,key,value))
    app=SimpleNamespace(workbench=workbench,invalidate_result_requests=lambda **kw:None)
    app._retired_result_datasets=SimpleNamespace(put=lambda value:observed.append(app._result_dataset_snapshot))
    _WorkbenchField('result_datasets').__set__(app,{'new':new})
    assert observed==[(new,)]


def test_close_settles_queued_capture_and_releases_payload_without_publish():
    import weakref,gc
    app=host();payload=np.ones(100);reference=weakref.ref(payload);events=[]
    app._request_result(lambda cancel,value=payload:value,lambda value:events.append('published'),on_cancel=lambda:events.append('cleanup'))
    del payload
    until=time.monotonic()+2
    while app._result_loader._tasks:
        assert time.monotonic()<until;time.sleep(.002)
    app.close_result_access();gc.collect()
    assert events==['cleanup'] and reference() is None and app._result_queue.empty()


def test_actual_tk_csv_export_resolves_subset_root_frame(monkeypatch,tmp_path):
    import anyfem.ui.panels as panels
    dataset=sidecar(tmp_path);dataset.prepare_metadata();destination=tmp_path/'field.csv'
    monkeypatch.setattr(panels.filedialog,'asksaveasfilename',lambda **kw:str(destination))
    app=SimpleNamespace(solution=None,active_job_id='j',result_datasets={'j':dataset},shape_index=1,
        project=SimpleNamespace(name='p'),set_status=lambda *a,**kw:None,
        _request_result=lambda load,publish,**kw:publish(load(Event())))
    panels.ResultsPanel._export_field(SimpleNamespace(app=app,field_name=lambda:'stress'))
    assert '10' in destination.read_text() and '30' not in destination.read_text();dataset.close()


def test_production_envelope_publication_reports_failed_coverage():
    app=host();first=app.solution;failed=source();failed.status='failed'
    app.solution=SimpleNamespace(shapes=[first,failed])
    app.current_shape=lambda:first
    app._result_scale_text=lambda panel:None
    app.panels={'Results':SimpleNamespace(ensure_compatible_field=lambda:None,field_name=lambda:'ux',envelope=SimpleNamespace(isChecked=lambda:True))}
    app._publish_results_field=lambda field:True
    done=[];WorkbenchWorkflow.show_results(app,on_ready=lambda:done.append(True))
    drain(app,lambda:bool(done))
    assert any('1/2 admitted; 1 failed' in text and flags.get('error') for text,flags in app.statuses)
    app.close_result_access()


@pytest.mark.parametrize('backend',['qt','tk'])
def test_result_display_defers_any_cold_header_until_worker_preparation(backend):
    dataset=object();pending=[]
    app=SimpleNamespace(solution=None,active_job_id='j',result_datasets={'j':dataset},
        prepare_result_dataset=lambda value,**kw:pending.append((value,kw)) or True)
    panel=SimpleNamespace(app=app,use_retained=lambda:True,_display_selection=lambda:('stress',),
        _component=SimpleNamespace(get=lambda:'stress'),
        ensure_compatible_field=lambda:pytest.fail('cold headers touched on owner'))
    if backend=='qt':
        from anyfem.ui.qt.tasks import ResultsTask
        ResultsTask.show_results(panel)
    else:
        from anyfem.ui.panels import ResultsPanel
        ResultsPanel._show(panel)
    assert pending[0][0] is dataset and callable(pending[0][1]['on_ready'])


def test_explicit_draw_cancels_only_its_queued_duplicate_controls_draw(monkeypatch):
    from anyfem.ui.qt import tasks
    queued=[];draws=[]
    monkeypatch.setattr(tasks.QTimer,'singleShot',lambda delay,panel,callback:queued.append(callback))
    app=SimpleNamespace(session=object(),project=SimpleNamespace(document_id='p'),active_job_id=None,solution=object(),result_datasets={},
        _view_mode='results',_closing=False,viewport=SimpleNamespace(canvas=SimpleNamespace(is_playing_animation=False)),guarded=lambda fn:fn)
    panel=SimpleNamespace(app=app,_selected_entry=None,_syncing_controls=False,_display_pending=False,show_results=lambda:draws.append(True))
    tasks.ResultsTask._controls_changed(panel)
    tasks.ResultsTask.cancel_pending_display(panel)
    queued.pop()()
    assert draws==[]
