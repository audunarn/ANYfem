"""Production regressions for the independently reviewed caller boundaries."""
from types import SimpleNamespace,MappingProxyType
from threading import Event,Thread
import json,time
import h5py,numpy as np,pytest
from anyfem.post.access import LiveResultAccess,ShapeProvider,retained_recovery_bytes
from anyfem.post.fields import Field
from anypost.errors import RequestCancelled,WorkingSetError,ClosedReaderError

def source():
    mesh=SimpleNamespace(nodes={17:(),91:()},shells={},beams={})
    return SimpleNamespace(status='completed',fields={'ux':Field('ux','m',node_values={17:1.,91:2.})},built=SimpleNamespace(mesh=mesh))

def artifact(tmp_path,*,unframed=False,repeated=False):
    from anyfem.io.artifacts import LazyResultDataset
    path=tmp_path/'source.h5';frames=[0.,1.,1.,2.] if repeated else [0.,1.,2.,3.]
    with h5py.File(path,'w') as handle:
        handle.attrs.update(schema='anyfem.result',version=1,complete=True,model_hash='m',mesh_hash='s')
        handle.create_dataset('frames/values',data=frames)
        group=handle.create_group('fields/temperature')
        group.create_dataset('values',data=[7.,8.] if unframed else np.arange(8.).reshape(4,2))
        group.attrs.update(location='node',label='Temperature',unit='K',components='[]')
        if not unframed:group.attrs['frames']=json.dumps(frames)
    return LazyResultDataset(path)

def test_failed_extraction_retires_its_recovery_and_retry_remains_owned(monkeypatch):
    import anyfem.post.fields as fields
    import anyfem.post.solver_data as data
    shape=SimpleNamespace(status='completed',_stress=None,built=SimpleNamespace(mesh=SimpleNamespace(nodes={},shells={101:()},beams={})))
    cancel=Event();stop=[True]
    def recover(value):
        value._stress=SimpleNamespace(element_stresses={101:{'axial_stress':np.array([2.])}})
        if stop[0]:cancel.set()
        return value._stress
    monkeypatch.setattr(fields,'recover',recover)
    monkeypatch.setattr(data,'resolve_solution_quantity',lambda *a,**k:SimpleNamespace(descriptor=SimpleNamespace(unit='Pa',basis='local')))
    access=LiveResultAccess()
    with pytest.raises(RequestCancelled):access.field(shape,'axial_stress',cancel_event=cancel)
    assert shape._stress is None and not access._recovery_resident
    stop[0]=False;cancel.clear();access.field(shape,'axial_stress',cancel_event=cancel)
    assert access._recovery_resident[id(shape)][3] and access._recovery_resident[id(shape)][2]>0
    access.release_shape(shape);assert shape._stress is None
    access.close()

def test_native_snapshot_refuses_before_padded_allocation(monkeypatch):
    import anyfem.post.solver_data as data
    shape=SimpleNamespace(built=SimpleNamespace(mesh=SimpleNamespace(nodes={},shells={101:()},beams={})))
    recovered=SimpleNamespace(element_stresses={101:{'axial_stress':np.ones(256)}})
    monkeypatch.setattr(data,'resolve_solution_quantity',lambda *a,**k:SimpleNamespace(descriptor=SimpleNamespace(unit='Pa',basis='local')))
    original=np.full;calls=[]
    monkeypatch.setattr(np,'full',lambda *a,**k:calls.append(a[0]) or original(*a,**k))
    with pytest.raises(WorkingSetError):ShapeProvider(shape,'axial_stress',stresses=recovered,working_bytes=4096)
    assert not calls

def test_recovery_mapping_and_additional_arrays_are_charged():
    values=np.ones(128);other=np.ones(256)
    recovery=SimpleNamespace(element_stresses=MappingProxyType({101:MappingProxyType({'stress':values})}),node_values=other)
    assert retained_recovery_bytes(SimpleNamespace(_stress=recovery))>=values.nbytes+other.nbytes

def test_worker_waits_cancellably_for_short_cache_ownership():
    access=LiveResultAccess();entered=Event();release=Event();output=[]
    def hold():
        with access._cache_operation():entered.set();release.wait(5)
    holder=Thread(target=hold);holder.start();assert entered.wait(5)
    worker=Thread(target=lambda:output.append(access.field(source(),'ux')));worker.start()
    time.sleep(.03);assert worker.is_alive()
    release.set();holder.join(5);worker.join(5)
    assert output[0].values=={17:1.,91:2.}
    access.close()

def test_unframed_vector_reads_one_source_row(tmp_path,monkeypatch):
    dataset=artifact(tmp_path,unframed=True);reader=dataset.result_reader();calls=[];read=reader.read_stored_field
    monkeypatch.setattr(reader,'read_stored_field',lambda *a,**k:calls.append(a) or read(*a,**k))
    np.testing.assert_array_equal(dataset.field('temperature').read(),[7.,8.])
    assert len(calls)==1
    dataset.release_reader();np.testing.assert_array_equal(dataset.field('temperature').read(),[7.,8.])
    dataset.close()
    with pytest.raises(ClosedReaderError):dataset.field('temperature').read()

def test_repeated_full_frame_axis_keeps_row_identity(tmp_path):
    dataset=artifact(tmp_path,repeated=True);dataset.prepare_metadata();field=dataset.field('temperature')
    assert field.root_frame_index(1)==1 and field.root_frame_index(2)==2
    np.testing.assert_array_equal(field.read(1),[2.,3.]);np.testing.assert_array_equal(field.read(2),[4.,5.])
    dataset.close()

def test_actual_live_inspector_query_adapter_has_typed_field_boundary(monkeypatch):
    import anyfem.post.solver_data as data
    from anyfem.ui.qt.tasks import ResultsTask as QtResultsTask
    payload=MappingProxyType({'source':(1.,2.)})
    monkeypatch.setattr(data,'resolve_solution_quantity',lambda *a,**k:SimpleNamespace(data=payload,descriptor=SimpleNamespace(components=())))
    class Panel:
        inspect_quantity=QtResultsTask.inspect_quantity
        _inspect_quantity_source=staticmethod(QtResultsTask._inspect_quantity_source)
    panel=Panel();panel._quantity_component=None;reports=[];pending=[]
    panel.app=SimpleNamespace(solution=source(),active_job_id=None,result_datasets={},set_status=lambda *a:None,
        _request_result=lambda load,publish,**kw:pending.append((load,publish)))
    panel.quantities=SimpleNamespace(currentData=lambda:('live','stress'))
    panel.frame=SimpleNamespace(value=lambda:0);panel.report=SimpleNamespace(setPlainText=reports.append,appendPlainText=reports.append)
    panel.table=SimpleNamespace(show_rows=lambda *a:None);panel.plot=SimpleNamespace(show_series=lambda *a:None)
    panel.inspect_quantity();load,publish=pending[0];publish(load(Event()))
    assert json.loads(reports[-1])=={'source':[1.,2.]}

def test_non_displacement_query_does_not_pack_all_case_vectors(monkeypatch):
    from anyfem.post.solver_data import _wrapper_carrier
    solution=SimpleNamespace(shapes=[SimpleNamespace(displacements=np.ones(4))],info={'reactions':[2.]})
    monkeypatch.setattr(np,'stack',lambda *a,**k:(_ for _ in ()).throw(AssertionError('unrequested packing')))
    assert _wrapper_carrier(solution,quantity_id='reactions').reactions==[2.]

def test_interactive_controls_stop_cached_playback_but_capture_sync_does_not(monkeypatch):
    from anyfem.ui.qt import tasks
    events=[];scheduled=[]
    canvas=SimpleNamespace(is_playing_animation=True)
    def stop():events.append('stop');canvas.is_playing_animation=False
    canvas.stop_animation=stop
    panel=SimpleNamespace(_selected_entry=None,_syncing_controls=False,_display_pending=False,
        show_results=lambda:events.append('show'))
    panel.app=SimpleNamespace(viewport=SimpleNamespace(canvas=canvas),_view_mode='results',session=object(),
        project=SimpleNamespace(document_id='p'),active_job_id=None,solution=object(),result_datasets={},_closing=False,guarded=lambda callback:callback)
    monkeypatch.setattr(tasks.QTimer,'singleShot',lambda delay,owner,callback:scheduled.append(callback))
    tasks.ResultsTask._controls_changed(panel);assert events==['stop']
    scheduled[0]();assert events==['stop','show']
    panel._syncing_controls=True;canvas.is_playing_animation=True
    tasks.ResultsTask._controls_changed(panel);assert canvas.is_playing_animation and events==['stop','show']

def test_failed_publication_discards_candidate_reservation():
    from anyfem.application.workflow import WorkbenchWorkflow
    access=LiveResultAccess();access.reserve_publication(Field('wrong','m',node_values={17:1.}))
    app=SimpleNamespace(solution=source(),panels={'Results':SimpleNamespace(ensure_compatible_field=lambda:None,field_name=lambda:'ux')},
        current_shape=source,_live_result_access=access)
    with pytest.raises(ValueError,match='requested component'):WorkbenchWorkflow._publish_results_field(app,Field('wrong'))
    assert access._pending_publication_bytes==0
    access.close()

def test_first_deferred_display_preserves_requested_continuation(tmp_path,monkeypatch):
    import anyfem.application.workflow as workflow
    from anyfem.ui.scene import Scene
    dataset=artifact(tmp_path);events=[]
    mesh=SimpleNamespace(nodes={17:(),91:()},shells={},beams={})
    monkeypatch.setattr(workflow,'build_persisted_result_scene',lambda *a,**k:Scene(result_field=Field('temperature','K',node_values={17:1.}),result_mesh=mesh))
    class App(workflow.WorkbenchWorkflow):
        project=None;solution=None;mesh=None;active_job_id=None;result_datasets={};_view_mode=None
        def _request_result(self,load,publish,**kwargs):publish(load(Event()))
        def retained_result_mesh(self,job):return mesh
        def refresh_panels(self):events.append('metadata')
        def _with_attributes(self,scene,**kwargs):return scene
        def _update_view_label(self):pass
        def set_status(self,*a,**k):pass
    app=App();app.active_job_id='job';app.result_datasets={'job':dataset};app.project=SimpleNamespace(jobs={})
    app._show_attributes=SimpleNamespace(get=lambda:False)
    app.viewport=SimpleNamespace(visualization=SimpleNamespace(show_beam_sections=False),show=lambda scene:events.append('shown'))
    app.panels={'Results':SimpleNamespace(colormap=lambda:((0.,'#000000'),(1.,'#ffffff')),display_units=lambda:'SI (m / Pa)',
        show_result_nodes=lambda:False,show_result_supports=lambda:False,show_result_loads=lambda:False,
        show_result_masses=lambda:False,show_imperfect_reference=lambda:False)}
    app.show_persisted_result('temperature',scale=0,on_ready=lambda:events.append('ready'))
    assert events==['metadata','shown','ready'] and dataset.metadata_ready
    dataset.close()

def test_publication_render_error_clears_pending_and_keeps_accepted_charge(monkeypatch):
    import anyfem.application.workflow as workflow
    from anyfem.ui.scene import Scene
    access=LiveResultAccess();old=access.reserve_publication(Field('ux','m',node_values={17:1.}));access.commit_publication(old)
    value=source();panel=SimpleNamespace(ensure_compatible_field=lambda:None,field_name=lambda:'ux',scale_value=lambda s:0,
        show_result_nodes=lambda:False,show_result_supports=lambda:False,show_result_loads=lambda:False,
        show_result_masses=lambda:False,show_imperfect_reference=lambda:False,colour_limits=lambda:None,colormap=lambda:(),display_units=lambda:'SI (m / Pa)')
    prior=Scene();viewport=SimpleNamespace(_scene=prior,active_backend='software',visualization=SimpleNamespace(show_beam_sections=False),
        show=lambda scene:(_ for _ in ()).throw(RuntimeError('render refused')))
    app=SimpleNamespace(solution=value,project=None,panels={'Results':panel},current_shape=lambda:value,_live_result_access=access,
        viewport=viewport,_show_attributes=SimpleNamespace(get=lambda:False),_result_scale_text=lambda p:None,
        _result_case_name=lambda s:'source',_with_attributes=lambda scene,**k:scene)
    monkeypatch.setattr(workflow,'build_result_scene',lambda *a,**k:Scene(result_field=k['values'],result_mesh=value.built.mesh))
    with pytest.raises(RuntimeError,match='render refused'):workflow.WorkbenchWorkflow._publish_results_field(app,Field('ux','m',node_values={17:2.}))
    assert access._pending_publication_bytes==0 and access._publication_bytes==old and viewport._scene is prior
    access.close()

def test_envelope_declares_nested_callback_allowance_and_uses_it(monkeypatch):
    from anyfem.post.access import EnvelopeProvider,WORKING_BYTES
    from anypost import open_memory_results
    access=LiveResultAccess();provider=EnvelopeProvider([source()],'ux','mean',access=access)
    assert provider.provider_work_bytes==provider.inner_working+provider.conversion_reserved
    assert provider.provider_work_bytes+provider.outer_reserved==WORKING_BYTES
    assert provider.metadata()['descriptors'][0]['provider_work_bytes']==provider.provider_work_bytes
    field=access.field;observed=[]
    monkeypatch.setattr(access,'field',lambda *a,**k:observed.append(access.working_bytes) or field(*a,**k))
    with open_memory_results(provider,working_bytes=WORKING_BYTES) as reader:
        np.testing.assert_array_equal(reader.read_field('0','ux','ux'),[1.,2.])
    assert observed==[provider.inner_working] and access.working_bytes==WORKING_BYTES
    access.close()

def test_envelope_insufficient_callback_allowance_refuses_before_nested_query(monkeypatch):
    import anyfem.post.access as module
    from anypost import open_memory_results
    monkeypatch.setattr(module,'WORKING_BYTES',128*1024)
    value=source();value.built.mesh.nodes={i:() for i in range(300)}
    access=LiveResultAccess();provider=module.EnvelopeProvider([value],'ux','mean',access=access)
    monkeypatch.setattr(access,'field',lambda *a,**k:pytest.fail('nested callback allocated after refused allowance'))
    with open_memory_results(provider,working_bytes=128*1024) as reader:
        with pytest.raises(WorkingSetError):reader.read_field('0','ux','ux')
    access.close()

def test_qt_frame_selector_uses_root_axis_for_subset_quantity():
    from anyfem.ui.qt.tasks import ResultsTask
    dataset=SimpleNamespace(frames=(0.,1.,2.,3.),field_keys=('stress',),
        field=lambda key:SimpleNamespace(descriptor=SimpleNamespace(frames=(1.,3.))))
    panel=SimpleNamespace(app=SimpleNamespace(result_datasets={'job':dataset},active_job_id='job'),
        use_retained=lambda:True,field_name=lambda:'stress')
    assert ResultsTask.frame_count(panel)==4
