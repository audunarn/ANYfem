"""Discriminating production regressions for the reviewed default callers."""
import json,threading
from types import SimpleNamespace,MappingProxyType
import numpy as np
import pytest
from anyfem.presentation import VisualizationStyle
from anyfem.post.fields import Field
from anyfem.post.access import LiveResultAccess,ShapeProvider,evaluate_envelope,source_case_metadata
from anypost.errors import WorkingSetError,UnavailableFieldError,FailedCaseError

def shape(value=1.,status="completed"):
    mesh=SimpleNamespace(nodes={17:(),91:()},shells={},beams={})
    return SimpleNamespace(status=status,fields={"ux":Field("ux","m",node_values={17:value,91:-value})},built=SimpleNamespace(mesh=mesh))

def sidecar(tmp_path,partial=False):
    import h5py
    from anyfem.io.artifacts import LazyResultDataset
    path=tmp_path/'source.h5'
    with h5py.File(path,'w') as h:
        h.attrs.update(schema="anyfem.result",version=1,complete=True,partial=partial,model_hash="m",mesh_hash="s")
        h.create_dataset('frames/values',data=[0.,1.,2.,3.])
        for key,frames,values in [('stress',[1.,3.],[[[10.]],[[30.]]]),('displacement',[0.,1.,2.,3.],np.arange(12.).reshape(4,1,3))]:
            g=h.create_group('fields/'+key);g.create_dataset('values',data=values)
            g.attrs.update(label=key,location="node",unit="m",components=json.dumps(['ux','uy','uz'] if key=='displacement' else ['xx']),frames=json.dumps(frames),provenance='{}')
            h.create_dataset('tables/'+key+'_node_ids',data=[17])
    return LazyResultDataset(path)

def test_original_headers_without_optional_key_and_root_frame_mapping(tmp_path):
    from anyfem.ui.scene import read_persisted_display_field
    dataset=sidecar(tmp_path)
    before=dataset.field('stress').descriptor
    dataset.prepare_metadata()
    assert dataset.field('stress').descriptor==before
    assert dataset.field('displacement').descriptor.label=='displacement'
    stored=dataset.field('stress')
    np.testing.assert_array_equal(stored.read(1),[[30.]])  # raw API remains local
    assert stored.root_frame_index(1)==0 and stored.root_frame_index(3)==1
    assert read_persisted_display_field(dataset,'stress',frame=1,component='xx')[0].values=={17:10.}
    with pytest.raises(UnavailableFieldError):read_persisted_display_field(dataset,'stress',frame=2,component='xx')
    dataset.close()

def test_completed_source_data_readiness_and_unknown_generic_envelope():
    from anyfem.post.results import LinearSolution,ImportedSolution
    for cls in (LinearSolution,ImportedSolution):
        value=cls.__new__(cls)
        meta=source_case_metadata(value)
        assert meta['status']=='completed' and not meta['certified']
        assert meta['source_status']=='unknown'
    unknown=shape();del unknown.status
    assert ShapeProvider(unknown,'ux').metadata()['cases'][0]['status']=='unknown'
    with pytest.raises(FailedCaseError):evaluate_envelope(unknown,'ux')

def test_envelope_reports_failed_coverage_without_changing_submitted_count():
    result=evaluate_envelope(SimpleNamespace(shapes=[shape(1),shape(100,'failed')]),'ux')
    assert result.shape_count==2 and result.admitted_count==1 and result.failed_count==1
    assert result.admitted_case_ids==('0',) and result.failed_case_ids==('1',)
    assert result.field.values[17]==1

def test_envelope_releases_only_owned_recovery(monkeypatch):
    import anyfem.post.fields as fields
    import anyfem.post.solver_data as solver_data
    monkeypatch.setattr(solver_data,'resolve_solution_quantity',lambda *a,**k:SimpleNamespace(descriptor=SimpleNamespace(unit='Pa',basis='local')))
    mesh=SimpleNamespace(nodes={},shells={101:()},beams={})
    shapes=[SimpleNamespace(status='completed',built=SimpleNamespace(mesh=mesh),_stress=None) for _ in range(3)]
    supplied=SimpleNamespace(element_stresses={101:{'axial_stress':np.array([2.])}})
    shapes[-1]._stress=supplied
    def recover(value):
        if value._stress is None:value._stress=SimpleNamespace(element_stresses={101:{'axial_stress':np.array([1.])}})
        return value._stress
    monkeypatch.setattr(fields,'recover',recover)
    result=evaluate_envelope(SimpleNamespace(shapes=shapes),'axial_stress')
    assert result.field.values[101]==2
    assert shapes[0]._stress is None and shapes[1]._stress is None and shapes[2]._stress is supplied

def test_cache_overlap_refuses_without_waiting_or_mutating(monkeypatch):
    import anyfem.post.access as access
    entered=threading.Event();release=threading.Event();errors=[]
    original=access.ShapeProvider
    def blocked(*a,**k):entered.set();assert release.wait(5);return original(*a,**k)
    monkeypatch.setattr(access,'ShapeProvider',blocked)
    service=LiveResultAccess(cache_bytes=2**20)
    def work():
        try:service.field(shape(),'ux')
        except BaseException as error:errors.append(error)
    thread=threading.Thread(target=work);thread.start();assert entered.wait(5)
    try:
        with pytest.raises(WorkingSetError):service.reserve_publication(Field('ux','m',node_values={17:2.}))
    finally:release.set();thread.join(5)
    assert not thread.is_alive() and not errors and service._publication_bytes==0
    service.close()

def test_warm_refusal_tracks_or_closes_reader():
    service=LiveResultAccess(cache_bytes=2**20);value=shape()
    service.field(value,'ux');reader=next(iter(service._readers.values()))[2]
    service._publication_bytes=service.cache_bytes
    with pytest.raises(WorkingSetError):service.field(value,'ux')
    assert not service._readers
    from anypost.errors import ClosedReaderError
    with pytest.raises(ClosedReaderError):reader.read_stored_field('0','ux','ux')
    service.close()

def test_owner_inspector_submits_persisted_and_json_mapping(monkeypatch):
    from anyfem.ui.qt.tasks import ResultsTask
    from anyfem.post import access
    class Panel:
        inspect_quantity=ResultsTask.inspect_quantity
        _inspect_quantity_source=ResultsTask._inspect_quantity_source
    pending=[];reports=[]
    panel=Panel();panel._quantity_component=None
    panel.app=SimpleNamespace(solution=object(),active_job_id='job',result_datasets={'job':object()},_request_result=lambda load,publish,**kw:pending.append((load,publish,kw)),set_status=lambda *a:None)
    panel.quantities=SimpleNamespace(currentData=lambda:('field','displacement'))
    panel.frame=SimpleNamespace(value=lambda:0);panel.report=SimpleNamespace(setPlainText=reports.append,appendPlainText=reports.append)
    panel.plot=SimpleNamespace(show_series=lambda *a:None);panel.table=SimpleNamespace(show_rows=lambda *a:None)
    panel.inspect_quantity();assert pending[-1][2]['dataset'] is panel.app.result_datasets['job']
    panel.quantities.currentData=lambda:('live','plasticity')
    monkeypatch.setattr(access,'query_solution_quantity',lambda *a:SimpleNamespace(data=MappingProxyType({17:MappingProxyType({'strain':(1.,2.)})}),descriptor=SimpleNamespace(components=())))
    panel.inspect_quantity();load,publish,_=pending[-1];publish(load(threading.Event()))
    assert json.loads(reports[-1])=={'17':{'strain':[1.,2.]}}

def test_partial_stored_warning_and_cold_warm_legend(tmp_path):
    from anyfem.application.workflow import WorkbenchWorkflow
    from anyfem.ui.scene import persisted_display_metadata
    dataset=sidecar(tmp_path,partial=True);dataset.prepare_metadata()
    app=SimpleNamespace(set_status=lambda text,**kw:messages.append((text,kw)));messages=[]
    WorkbenchWorkflow._warn_persisted_status(app,dataset)
    assert 'Partial analysis' in messages[0][0] and messages[0][1]['error']
    descriptor=dataset.field('stress').descriptor
    field=Field('stress','m',node_values={17:2.})
    one=persisted_display_metadata(field,descriptor,'xx')[2]
    two=persisted_display_metadata(field,descriptor,'xx')[2]
    assert one==two and 'stress · xx' in one['title']
    dataset.close()

def test_publication_refusal_keeps_queue_running_for_next_valid_request():
    import time
    from anyfem.application.workflow import WorkbenchWorkflow
    class Scheduler:
        def __init__(self):self.callbacks=[]
        def call_later(self,delay,callback):self.callbacks.append(callback)
        def step(self):
            pending,self.callbacks=self.callbacks,[]
            for callback in pending:callback()
    class App(WorkbenchWorkflow):
        solution=None;project=None;mesh=None;result_datasets={}
        def set_status(self,text,**options):self.messages.append((text,options))
    app=App();app.solution=shape();app.project=object();app.mesh=app.solution.built.mesh
    app._closing=False;app.scheduler=Scheduler();app.messages=[];accepted=[]
    def refuse(value):raise WorkingSetError('test bounded publication refusal')
    app._request_result(lambda cancel:1,refuse)
    deadline=time.monotonic()+5
    while not app.messages and time.monotonic()<deadline:app.scheduler.step();time.sleep(.005)
    assert app.messages and app.messages[-1][1]['error'] and app.scheduler.callbacks
    app._request_result(lambda cancel:2,accepted.append)
    deadline=time.monotonic()+5
    while not accepted and time.monotonic()<deadline:app.scheduler.step();time.sleep(.005)
    assert accepted==[2]
    app.close_result_access();app._closing=True

def test_dataset_replacement_after_retirement_snapshot_remains_close_owned():
    from anyfem.application.workflow import WorkbenchWorkflow
    closed=[]
    class Dataset:
        def close(self):closed.append(self)
    one,two=Dataset(),Dataset()
    workbench=SimpleNamespace(result_datasets={'one':one})
    workbench.update=lambda name,value:setattr(workbench,name,value)
    app=WorkbenchWorkflow();app.workbench=workbench
    app.result_datasets={}
    queue=app.__dict__['_retired_result_datasets']
    retired=app._take_retired_result_datasets()
    assert retired==(one,)
    app.result_datasets={'two':two};app.result_datasets={}
    assert app.__dict__['_retired_result_datasets'] is queue
    # The sole worker owns its captured batch; close owns later replacements.
    for dataset in retired:dataset.close()
    app.close_result_access()
    assert closed==[one,two] and app._take_retired_result_datasets()==()

def test_persisted_overlay_change_rebuilds_and_warm_legend_keeps_component(tmp_path,monkeypatch):
    import anyfem.application.workflow as workflow
    from anyfem.application.workflow import WorkbenchWorkflow
    from anyfem.ui.scene import Scene,Polyline,MeshEntityRef
    dataset=sidecar(tmp_path);dataset.prepare_metadata();calls=[];legends=[];shown=[]
    mesh=SimpleNamespace(nodes={17:(0.,0.,0.),91:(1.,0.,0.)},beams={1:(17,91)},shells={})
    def build(*args,**kwargs):
        calls.append(kwargs)
        return Scene(lines=[Polyline(MeshEntityRef('element',1),np.array([[0.,0.,0.],[1.,0.,0.]]))],
            result_field=Field('stress','m',node_values={17:10.}),result_mesh=mesh)
    monkeypatch.setattr(workflow,'build_persisted_result_scene',build)
    class App(WorkbenchWorkflow):
        solution=None;project=None;mesh=None;result_datasets={};active_job_id=None;_view_mode=None
        def _request_result(self,load,publish,**kwargs):publish(load(threading.Event()))
        def retained_result_mesh(self,job):return mesh
        def _with_attributes(self,scene,**kwargs):return scene
        def _update_view_label(self):pass
        def set_status(self,*args,**kwargs):pass
    app=App();app.project=SimpleNamespace(jobs={});app.mesh=mesh;app.active_job_id='job';app.result_datasets={'job':dataset}
    app._show_attributes=SimpleNamespace(get=lambda:False)
    def show(scene):
        shown.append(scene);app.viewport._scene=scene
        handle=SimpleNamespace(mesh=SimpleNamespace(element_scalars=np.zeros(1,dtype=np.float32)))
        app.viewport._retained_result=(b'geometry',[handle],(np.array([1]),),mesh)
    app.viewport=SimpleNamespace(visualization=VisualizationStyle(show_beam_sections=False),show=show,
        active_backend='gpu',_retained_result=None,_scene=None,
        update_result_field=lambda field,**kwargs:legends.append(kwargs['legend']) or True)
    flag=[False]
    panel=SimpleNamespace(colormap=lambda:((0.,'#000000'),(1.,'#ffffff')),display_units=lambda:'SI (m / Pa)',
        show_result_nodes=lambda:flag[0],show_result_supports=lambda:False,show_result_loads=lambda:False,
        show_result_masses=lambda:False,show_imperfect_reference=lambda:False)
    app.panels={'Results':panel}
    app.show_persisted_result('stress',frame=1,component='xx',scale=0)
    app.show_persisted_result('stress',frame=1,component='xx',scale=0)
    assert len(calls)==1 and 'stress · xx' in legends[-1]['title']
    flag[0]=True
    app.show_persisted_result('stress',frame=1,component='xx',scale=0)
    assert len(calls)==2 and calls[-1]['show_nodes']
    dataset.close()
