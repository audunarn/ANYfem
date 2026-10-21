"""Request-local quantity inventory and component publication notifications."""
from types import SimpleNamespace
from threading import Event
from queue import Queue

import numpy as np
import pytest

from anyfem.application.workflow import WorkbenchWorkflow
from anyfem.application.result_requests import ResultRequests
from anyfem.post.fields import Field


def request_app():
    shape = SimpleNamespace(displacements=np.zeros(6), status='ok')
    updates = []
    panel = SimpleNamespace(ensure_compatible_field=lambda: None,
        field_name=lambda: 'ux', refresh_quantities=lambda **kw: updates.append(kw['descriptors']))
    timers=[]
    app = SimpleNamespace(solution=shape, panels={'Results': panel}, _closing=False,
        timers=timers, scheduler=SimpleNamespace(call_later=lambda delay,fn:timers.append(fn)),
        current_shape=lambda: shape, _result_scale_text=lambda panel: None,
        _live_result_access=SimpleNamespace(field=lambda *a, **kw: Field('ux', 'm', node_values={1: 0.})),
        _publish_results_field=lambda field: True,
        result_browser=SimpleNamespace(refresh=lambda: None))
    requests=[]
    def request(load,publish,**kw):
        if kw.get('channel','display')=='display':app._result_inventory_token=object()
        requests.append((load,publish))
    app._request_result=request
    return app, shape, updates, requests


def load_and_publish(app, requests):
    WorkbenchWorkflow.show_results(app)
    load,publish=requests.pop()
    value=load(Event())
    publish(value)
    if app.timers:
        app.timers.pop(0)()
        inventory_load,inventory_publish=requests.pop()
        inventory_publish(inventory_load(Event()))
    return value


@pytest.mark.parametrize('mutation', ['introduced', 'replaced', 'nan', 'excluded', 'new_component'])
def test_show_results_discovers_current_mutable_capabilities(mutation):
    app,shape,updates,requests=request_app()
    if mutation!='introduced':
        shape._stress=SimpleNamespace(element_stresses={1: {'von_mises': 1.}})
    load_and_publish(app,requests)
    if mutation in {'introduced','replaced'}:
        shape._stress=SimpleNamespace(element_stresses={1: {'von_mises_top': 2.}})
    elif mutation=='nan':shape._stress.element_stresses[1]['von_mises']=np.nan
    elif mutation=='excluded':shape._stress.element_stresses[1]['physical_stress_available']=False
    else:shape._stress.element_stresses[1]['von_mises_bot']=3.
    load_and_publish(app,requests)
    stress=next((d for d in updates[-1] if d.quantity_id=='stress'),None)
    if mutation in {'nan','excluded'}:assert stress is None
    elif mutation in {'introduced','replaced'}:assert stress.components==('von_mises_top',)
    else:assert stress.components==('von_mises','von_mises_bot')


def test_refused_publication_does_not_update_inventory():
    app,shape,updates,requests=request_app()
    app._publish_results_field=lambda field: False
    app.show_results=lambda **kw: None
    load_and_publish(app,requests)
    assert not updates


def test_cancelled_request_does_not_publish_inventory():
    app,shape,updates,requests=request_app()
    dispatched=Queue();loader=ResultRequests(dispatched.put)
    WorkbenchWorkflow.show_results(app)
    load,publish=requests.pop()
    loader.request(load,lambda done: None if done.cancelled else publish(done.value))
    deliver=dispatched.get(timeout=5)
    loader.invalidate()
    deliver()
    loader.shutdown()
    assert not updates


def test_current_results_mode_emits_no_workbench_event():
    # Stop after mode handling to isolate the production notification boundary.
    from anyfem.application.workflow import _WorkbenchField
    class App:
        _view_mode=_WorkbenchField('view_mode')
        def current_shape(self):return SimpleNamespace(status='ok')
        def _result_scale_text(self,panel):raise StopIteration
    events=[]
    state=SimpleNamespace(view_mode='results')
    def update(field,value):
        setattr(state,field,value);events.append((field,value))
    state.update=update
    app=App();app.workbench=state;app.solution=object()
    app.panels={'Results':SimpleNamespace(ensure_compatible_field=lambda:None,field_name=lambda:'ux')}
    app.set_status=lambda *a,**kw:None
    for initial,expected in [('results',[]),('mesh',[('view_mode','results')])]:
        state.view_mode=initial;events.clear()
        with pytest.raises(StopIteration):WorkbenchWorkflow._apply_results_field(app,Field('ux','m'))
        assert events==expected


def test_quantity_menu_uses_request_inventory_without_second_scan(monkeypatch):
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication,QComboBox
    from anyfem.ui.qt.tasks import ResultsTask
    from anyfem.post import solver_data
    qapp=QApplication.instance() or QApplication([])
    shape=SimpleNamespace(displacements=np.zeros(6))
    panel=SimpleNamespace(app=SimpleNamespace(solution=shape,result_datasets={},active_job_id=None),quantities=QComboBox())
    descriptors=solver_data.solution_quantity_descriptors(shape)
    calls=[]
    original=solver_data.solution_quantity_descriptors
    monkeypatch.setattr(solver_data,'solution_quantity_descriptors',lambda value:(calls.append(value) or original(value)))
    ResultsTask.refresh_quantities(panel,descriptors=descriptors)
    assert not calls and panel.quantities.count()==1
    shape.displacements[0]=np.nan
    ResultsTask.refresh_quantities(panel)
    assert calls==[shape] and panel.quantities.count()==0


def test_inventory_is_request_local_and_discovery_runs_on_worker(monkeypatch):
    from threading import get_ident
    from anyfem.post import solver_data
    app,shape,updates,requests=request_app()
    shared=Field('ux','m',node_values={1:0.})
    app._live_result_access.field=lambda *a,**kw:shared
    before=dict(vars(shared));threads=[];owner=get_ident()
    original=solver_data.solution_quantity_descriptors
    def discover(value):
        threads.append(get_ident())
        return original(value)
    monkeypatch.setattr(solver_data,'solution_quantity_descriptors',discover)
    dispatched=Queue();loader=ResultRequests(dispatched.put)
    WorkbenchWorkflow.show_results(app)
    load,publish=requests.pop()
    loader.request(load,lambda done:publish(done.value))
    dispatched.get(timeout=5)()
    assert not threads and not updates
    app.timers.pop(0)()
    load,publish=requests.pop()
    loader.request(load,lambda done:publish(done.value),channel='inventory')
    dispatched.get(timeout=5)()
    loader.shutdown()
    assert threads and all(thread!=owner for thread in threads)
    assert vars(shared)==before
    assert len(updates)==1


def test_field_ready_precedes_eventual_inventory(monkeypatch):
    from anyfem.post import solver_data
    app,shape,updates,requests=request_app()
    sequence=[]
    original=solver_data.solution_quantity_descriptors
    monkeypatch.setattr(solver_data,'solution_quantity_descriptors',lambda source:(sequence.append('inventory') or original(source)))
    WorkbenchWorkflow.show_results(app,on_ready=lambda:sequence.append('ready'))
    load,publish=requests.pop();publish(load(Event()))
    assert sequence==['ready'] and not updates
    app.timers.pop(0)()
    load,publish=requests.pop();publish(load(Event()))
    assert sequence==['ready','inventory'] and len(updates)==1


@pytest.mark.parametrize('change',['field','source_epoch','revision','solution','closing'])
def test_stale_inventory_timer_and_request_cannot_publish(change,monkeypatch):
    from anyfem.post import solver_data
    app,shape,updates,requests=request_app()
    calls=[]
    original=solver_data.solution_quantity_descriptors
    monkeypatch.setattr(solver_data,'solution_quantity_descriptors',lambda source:(calls.append(source) or original(source)))
    WorkbenchWorkflow.show_results(app)
    load,publish=requests.pop();publish(load(Event()))
    timer=app.timers.pop()
    timer();inventory_load,inventory_publish=requests.pop()
    descriptors=inventory_load(Event())
    assert calls==[shape]
    if change=='field':
        WorkbenchWorkflow.show_results(app);requests.clear()
    elif change=='source_epoch':app._result_source_epoch=1
    elif change=='revision':app._result_revision=1
    elif change=='solution':app.solution=SimpleNamespace(displacements=np.ones(6))
    else:app._closing=True
    timer()
    assert not requests
    assert inventory_load(Event()) is None
    inventory_publish(descriptors)
    assert not updates and calls==[shape]


def test_rapid_changes_only_schedule_latest_inventory(monkeypatch):
    from anyfem.post import solver_data
    app,shape,updates,requests=request_app();calls=[]
    original=solver_data.solution_quantity_descriptors
    monkeypatch.setattr(solver_data,'solution_quantity_descriptors',lambda source:(calls.append(source) or original(source)))
    for _ in range(3):
        WorkbenchWorkflow.show_results(app)
        load,publish=requests.pop();publish(load(Event()))
    for timer in app.timers:timer()
    assert len(requests)==1 and not calls
    load,publish=requests.pop();publish(load(Event()))
    assert calls==[shape] and len(updates)==1
