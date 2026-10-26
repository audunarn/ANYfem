"""Production entry-point contracts for the default result engine."""
import threading
import time
from types import SimpleNamespace
import numpy as np
import pytest
from anyfem.presentation import VisualizationStyle
from anyfem.post.fields import Field, evaluate_field
from anyfem.post.access import LiveResultAccess


def imported_shape(value=3.0):
    mesh = SimpleNamespace(nodes={17:(0,0,0), 91:(1,0,0)}, shells={}, beams={})
    return SimpleNamespace(status="completed",fields={"ux": Field("ux", "m", node_values={17:value,91:-value})},
                           built=SimpleNamespace(mesh=mesh, project=None))


def test_public_field_queries_engine_and_observes_mutable_import(monkeypatch):
    from anyfem.post import access
    calls = []
    original = access.open_memory_results
    def tracked(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(access, "open_memory_results", tracked)
    shape = imported_shape()
    assert evaluate_field(shape,"ux").values == {17:3.0,91:-3.0}
    shape.fields["ux"].node_values[17] = 7.0
    assert evaluate_field(shape,"ux").values[17] == 7.0
    assert len(calls) == 2


def test_live_result_reuses_engine_cache_and_closes():
    shape = imported_shape()
    access = LiveResultAccess(cache_bytes=2**20)
    assert access.field(shape,"ux").values[91] == -3
    reader = next(iter(access._readers.values()))[2]
    before = reader.diagnostics
    assert access.field(shape,"ux").values[17] == 3
    assert reader.diagnostics["cache_hits"] > before["cache_hits"]
    assert sum(item[2].diagnostics["cache_resident_bytes"] for item in access._readers.values()) <= 2**20
    access.close()
    with pytest.raises(RuntimeError):access.field(shape,"ux")


def test_workflow_actual_show_results_publishes_owner_and_revision_refuses(monkeypatch):
    from anyfem.application.workflow import WorkbenchWorkflow
    import anyfem.application.workflow as workflow
    class Scheduler:
        def __init__(self):self.callbacks=[]
        def call_later(self, delay, callback):self.callbacks.append(callback)
        def step(self):
            pending,self.callbacks=self.callbacks,[]
            for callback in pending:callback()
    class App(WorkbenchWorkflow):
        solution = None
        project = None
        mesh = None
        def current_shape(self):return self.solution
        def _publish_results_field(self, field):self.published.append((threading.get_ident(),field))
        def show_mesh(self):pass
        def set_status(self,text,**kwargs):self.errors.append(text)
    app=App();app.solution=imported_shape();app.project=object();app.mesh=app.solution.built.mesh
    app._closing=False;app.scheduler=Scheduler();app.published=[];app.errors=[]
    panel=SimpleNamespace(ensure_compatible_field=lambda:None,field_name=lambda:"ux",_envelope=SimpleNamespace(get=lambda:False))
    app.panels={"Results":panel}
    owner=threading.get_ident()
    app.show_results()
    deadline=time.monotonic()+5
    while not app.published and time.monotonic()<deadline:
        app.scheduler.step();time.sleep(.005)
    assert app.published and app.published[0][0]==owner
    assert app.published[0][1].values[17]==3
    app.show_results();app.invalidate_result_requests()
    time.sleep(.02);app.scheduler.step()
    assert len(app.published)==1
    app.close_result_access();app._closing=True
    app.scheduler.step()
    assert not app.errors  # routine supersession preserves unrelated status


def test_native_ragged_empty_missing_surface_and_nan(monkeypatch):
    import anyfem.post.solver_data as solver_data
    monkeypatch.setattr(solver_data,"resolve_solution_quantity",lambda *args,**kwargs:SimpleNamespace(descriptor=SimpleNamespace(unit="Pa",basis="element_local")))
    mesh=SimpleNamespace(nodes={},shells={101:(),305:(),900:(),901:()},beams={})
    records={101:{"membrane_xx":np.array([1.,5.]),"bending_xx":np.array([2.,-1.])},
             305:{"membrane_xx":np.array([]),"bending_xx":np.array([])},
             901:{"membrane_xx":np.array([np.nan]),"bending_xx":np.array([1.])}}
    shape=SimpleNamespace(built=SimpleNamespace(mesh=mesh),_stress=SimpleNamespace(element_stresses=records))
    result=evaluate_field(shape,"top_xx",reduction="max_abs")
    assert result.values[101]==4 and result.values[305]==0
    assert np.isnan(result.values[901]) and result.missing==(900,)


def test_production_scalar_switch_reuses_cpu_scene(monkeypatch):
    from anyfem.application.workflow import WorkbenchWorkflow
    import anyfem.application.workflow as workflow
    from anyfem.ui.scene import Scene
    class App(WorkbenchWorkflow):
        solution=None;project=None;mesh=None;_view_mode=None
        def current_shape(self):return self.solution
        def _result_case_name(self,shape):return "case"
        def _update_view_label(self):pass
        def _with_attributes(self,scene,**kwargs):return scene
    app=App();app.solution=imported_shape();app.project=object();app.mesh=app.solution.built.mesh
    app._show_attributes=SimpleNamespace(get=lambda:False)
    calls=[];updates=[]
    def build(shape,**kwargs):calls.append(shape);return Scene(result_field=kwargs["values"],result_mesh=shape.built.mesh)
    monkeypatch.setattr(workflow,"build_result_scene",build)
    app.viewport=SimpleNamespace(visualization=VisualizationStyle(show_beam_sections=False),show=lambda scene:None,
                                 update_result_field=lambda field,**kwargs:updates.append(field) or True)
    selected=["ux"]
    panel=SimpleNamespace(ensure_compatible_field=lambda:None,field_name=lambda:selected[0],scale_value=lambda shape:0,
         colour_limits=lambda:None,colormap=lambda:((0.,"#000000"),(1.,"#ffffff")),display_units=lambda:"SI (m / Pa)",
         show_result_nodes=lambda:False,show_result_supports=lambda:False,show_result_loads=lambda:False,
         show_result_masses=lambda:False,show_imperfect_reference=lambda:False)
    app.panels={"Results":panel}
    app._persisted_geometry_key=object()
    app._publish_results_field(Field("ux","m",node_values={17:1.,91:2.}))
    assert app._persisted_geometry_key is None
    selected[0]="uy"
    app._publish_results_field(Field("uy","m",node_values={17:3.,91:4.}))
    assert len(calls)==1 and len(updates)==1


def test_displacement_then_recovery_is_charged_and_only_owned_cache_evicted(monkeypatch):
    import anyfem.post.fields as fields
    import anyfem.post.solver_data as solver_data
    monkeypatch.setattr(solver_data,"resolve_solution_quantity",lambda *a,**k:SimpleNamespace(descriptor=SimpleNamespace(unit="Pa",basis="local")))
    mesh=SimpleNamespace(nodes={17:(),91:()},shells={101:()},beams={})
    shape=SimpleNamespace(built=SimpleNamespace(mesh=mesh),component=lambda name:np.array([1.,2.]),_stress=None)
    recovery=SimpleNamespace(element_stresses={101:{"axial_stress":np.array([3.,4.])}})
    def recover(value):value._stress=recovery;return recovery
    monkeypatch.setattr(fields,"recover",recover)
    access=LiveResultAccess(cache_bytes=2**20)
    access.field(shape,"ux")
    assert access._recovery_resident[id(shape)][2]==0
    access.field(shape,"axial_stress")
    assert access._recovery_resident[id(shape)][2]>recovery.element_stresses[101]["axial_stress"].nbytes
    assert access._recovery_resident[id(shape)][3]
    access.close();assert shape._stress is None
    shape._stress=recovery
    access=LiveResultAccess(cache_bytes=2**20);access.field(shape,"axial_stress");access.close()
    assert shape._stress is recovery


def test_reader_lru_releases_snapshot_owners_and_global_budget():
    access=LiveResultAccess(cache_bytes=150000)
    for index in range(6):
        shape=imported_shape()
        shape.fields["ux"].node_values={i:float(index) for i in range(3000)}
        access.field(shape,"ux")
        assert sum(v[2].diagnostics["cache_resident_bytes"] for v in access._readers.values()) <= 150000
    assert len(access._readers)==1 and len(access._recovery_resident)==1
    access.close()


def test_envelope_cancel_and_absent_entity_differ_from_true_nan():
    from anyfem.post.access import evaluate_envelope
    from anypost.errors import RequestCancelled
    one=imported_shape();two=imported_shape()
    one.fields["ux"].node_values={17:np.nan}
    two.fields["ux"].node_values={17:7.,91:9.}
    result=evaluate_envelope(SimpleNamespace(shapes=[one,two]),"ux")
    assert result.field.values[91]==9 and result.shape_of[91]==1
    assert np.isnan(result.field.values[17])
    cancel=threading.Event();cancel.set()
    with pytest.raises(RequestCancelled):evaluate_envelope(SimpleNamespace(shapes=[one,two]),"ux",cancel_event=cancel)


def test_failed_source_snapshot_explicit_stored_keeps_failed_status():
    from anyfem.post.access import ShapeProvider
    from anypost import open_memory_results
    from anypost.errors import FailedCaseError
    shape=imported_shape();shape.status="failed"
    provider=ShapeProvider(shape,"ux")
    with open_memory_results(provider) as reader:
        assert reader.cases()[0]["status"]=="failed"
        assert not reader.cases()[0]["certified"]
        assert reader.cases()[0]["source_frame_availability"]=="stored_source_no_commitment_claim"
        with pytest.raises(FailedCaseError):reader.read_field("0","ux","ux")
        np.testing.assert_array_equal(reader.read_stored_field("0","ux","ux"),[3.,-3.])


def test_publication_reservation_refuses_without_replacing_previous():
    from anypost.errors import WorkingSetError
    access=LiveResultAccess(cache_bytes=5000,working_bytes=100000)
    previous=Field("ux","m",node_values={17:1.})
    token=access.reserve_publication(previous,100)
    access.commit_publication(token)
    with pytest.raises(WorkingSetError):
        access.reserve_publication(Field("uy","m",node_values={i:float(i) for i in range(100)}),200)
    assert access._publication_bytes==token
    access.close()


def test_production_qt_gif_capture_waits_for_field_publication():
    from anyfem.ui.qt.tasks import ResultsTask
    callbacks=[];images=[];queued=[];session=object()
    app=SimpleNamespace(_closing=False,session=session,active_job_id="job",
        viewport=SimpleNamespace(canvas=SimpleNamespace(capture_image=lambda:images.append("actual") or "frame")),
        scheduler=SimpleNamespace(call_later=lambda delay,callback:queued.append(callback)))
    state=dict(session=session,job="job",count=2,index=0,frames=(0,1),images=[])
    panel=SimpleNamespace(_gif_capture=state,app=app,_set_frame_index=lambda index:None,
        show_results=lambda **kwargs:callbacks.append(kwargs["on_ready"]),_capture_gif_frame=lambda:None)
    ResultsTask._capture_gif_frame(panel)
    assert images==[] and state["index"]==0
    callbacks[0]()
    assert images==["actual"] and state["images"]==["frame"] and state["index"]==1


def test_source_extraction_admits_before_values_and_precancel_before_recovery(monkeypatch):
    from anyfem.post.access import ShapeProvider
    from anypost.errors import WorkingSetError,RequestCancelled
    class Counted(dict):
        reads=0
        def __getitem__(self,key):self.reads+=1;return super().__getitem__(key)
    shape=imported_shape();data=Counted({i:1. for i in range(1000)})
    shape.fields["ux"].node_values=data
    with pytest.raises(WorkingSetError):ShapeProvider(shape,"ux",working_bytes=4000)
    assert data.reads==0
    import anyfem.post.fields as fields
    called=[];monkeypatch.setattr(fields,"recover",lambda value:called.append(value))
    cancel=threading.Event();cancel.set()
    with pytest.raises(RequestCancelled):ShapeProvider(shape,"axial_stress",cancel_event=cancel)
    assert called==[]


def test_surface_extraction_refuses_before_owner_allocation_and_checks_each_row(monkeypatch):
    from anyfem.post.access import ShapeProvider
    from anypost.errors import WorkingSetError,RequestCancelled
    import anyfem.post.solver_data as solver_data
    import anysolver_results
    monkeypatch.setattr(solver_data,"resolve_solution_quantity",lambda *a,**k:SimpleNamespace(descriptor=SimpleNamespace(unit="Pa",basis="local")))
    rows={i:{"membrane_xx":np.ones(1000),"bending_xx":np.ones(1000)} for i in (101,305)}
    recovery=SimpleNamespace(element_stresses=rows)
    shape=SimpleNamespace(built=SimpleNamespace(mesh=SimpleNamespace(nodes={},shells={101:(),305:()},beams={})),_stress=recovery)
    owner=anysolver_results.shell_surface_component;calls=[];cancel=threading.Event()
    def tracked(*args):calls.append(args);cancel.set();return owner(*args)
    monkeypatch.setattr(anysolver_results,"shell_surface_component",tracked)
    with pytest.raises(WorkingSetError):ShapeProvider(shape,"top_xx",working_bytes=6000)
    assert calls==[]
    with pytest.raises(RequestCancelled):ShapeProvider(shape,"top_xx",cancel_event=cancel)
    assert len(calls)==1


def test_workflow_capture_callback_waits_through_publication_retry():
    from anyfem.application.workflow import WorkbenchWorkflow
    class Scheduler:
        def __init__(self):self.callbacks=[]
        def call_later(self,delay,callback):self.callbacks.append(callback)
        def step(self):
            pending,self.callbacks=self.callbacks,[]
            for callback in pending:callback()
    class App(WorkbenchWorkflow):
        solution=None;project=None;mesh=None
        def current_shape(self):return self.solution
        def _publish_results_field(self,field):
            self.attempts+=1
            assert self.done==[]
            return self.attempts>1
        def set_status(self,text):raise AssertionError(text)
    app=App();app.solution=imported_shape();app.mesh=app.solution.built.mesh;app.project=object()
    app._closing=False;app.scheduler=Scheduler();app.attempts=0;app.done=[]
    app.panels={"Results":SimpleNamespace(ensure_compatible_field=lambda:None,field_name=lambda:"ux",_envelope=SimpleNamespace(get=lambda:False))}
    app.show_results(on_ready=lambda:app.done.append(threading.get_ident()))
    deadline=time.monotonic()+5
    while not app.done and time.monotonic()<deadline:app.scheduler.step();time.sleep(.005)
    assert app.attempts==2 and app.done==[threading.get_ident()]
    app.close_result_access();app._closing=True


def test_retained_quadratic_shell_mean_preserves_all_source_nodes():
    from anyfem.presentation.retained_results import scalar_values
    mesh=SimpleNamespace(shells={101:(1,2,3,4,5,6)},beams={})
    field=Field("ux","m",node_values={1:1.,2:1.,3:1.,4:100.,5:100.,6:100.})
    np.testing.assert_array_equal(scalar_values(field,mesh,[101]),np.array([50.5],dtype=np.float32))
    field.node_values.pop(6)
    assert np.isnan(scalar_values(field,mesh,[101])[0])


def test_production_request_retires_live_and_inactive_stored_owners():
    from anyfem.application.workflow import WorkbenchWorkflow
    from anyfem.post.fields import evaluate_field
    class Scheduler:
        def __init__(self):self.callbacks=[]
        def call_later(self,delay,callback):self.callbacks.append(callback)
        def step(self):
            pending,self.callbacks=self.callbacks,[]
            for callback in pending:callback()
    class Dataset:
        def __init__(self):self.closes=[]
        def close(self):self.closes.append(threading.get_ident())
    class App(WorkbenchWorkflow):
        solution=None;project=None;mesh=None;result_datasets={}
        def set_status(self,text):raise AssertionError(text)
    app=App();app.solution=imported_shape();app.mesh=app.solution.built.mesh;app.project=object()
    app._closing=False;app.scheduler=Scheduler()
    one,two=Dataset(),Dataset();app.result_datasets={"one":one,"two":two}
    published=[]
    def request(dataset=None):
        target=len(published)+1
        load=(lambda cancel:evaluate_field(app.solution,"ux")) if dataset is None else (lambda cancel:1)
        app._request_result(load,published.append,dataset=dataset)
        deadline=time.monotonic()+5
        while len(published)<target and time.monotonic()<deadline:app.scheduler.step();time.sleep(.005)
        assert len(published)==target
    request();original=app._live_result_access
    assert original._readers
    request(one);request(one)
    assert original._closed and not original._readers
    assert len(one.closes)==1 and len(two.closes)==3
    request()
    assert len(one.closes)==2 and len(two.closes)==4
    assert all(thread!=threading.get_ident() for thread in one.closes+two.closes)
    app.close_result_access();app._closing=True
