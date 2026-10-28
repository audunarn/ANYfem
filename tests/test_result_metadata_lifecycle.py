"""Result metadata preparation is owned by its dataset, not by the display selection."""
from __future__ import annotations

import time
from concurrent.futures import Future
from threading import Event
from types import SimpleNamespace

import pytest

from anyfem.application.workflow import WorkbenchWorkflow, _WorkbenchField


class _Scheduler:
    def __init__(self):self.calls=[]
    def call_later(self,_delay,callback):self.calls.append(callback)


class _Dataset:
    def __init__(self,gate=None):
        self.metadata_ready=False;self.prepared=0;self.gate=gate
    def prepare_metadata(self):
        if self.gate is not None:assert self.gate.wait(10)
        self.prepared+=1;self.metadata_ready=True
        return {}
    def release_reader(self):pass
    def close(self):pass


class _Host:
    """Only the workflow state that result requests consult."""
    _request_result=WorkbenchWorkflow._request_result
    prepare_result_dataset=WorkbenchWorkflow.prepare_result_dataset
    _take_retired_result_datasets=WorkbenchWorkflow._take_retired_result_datasets

    def __init__(self,dataset,*,retiring):
        self._closing=False;self.scheduler=_Scheduler()
        self.project=SimpleNamespace(jobs={});self.solution=None;self.mesh=None
        self.active_job_id=None;self.result_datasets={"job":dataset}
        self._status=SimpleNamespace(value="",get=lambda:self._status.value)
        self.refreshed=0
        self._result_retirement=Future() if retiring else None
    def set_status(self,text,*,error=False):self._status.value=text
    def refresh_panels(self):self.refreshed+=1
    def pump(self,predicate,timeout=10):
        end=time.monotonic()+timeout
        while not predicate() and time.monotonic()<end:
            calls,self.scheduler.calls=self.scheduler.calls,[]
            for call in calls:call()
            time.sleep(.005)
        return predicate()
    def close(self):
        loader=getattr(self,"_result_loader",None)
        if loader is not None:loader.close()


@pytest.fixture
def hosts():
    created=[]
    yield created
    for host in created:host.close()


def _prepare(host,dataset):
    outcome=[]
    assert host.prepare_result_dataset(dataset,on_ready=lambda:outcome.append("ready"),on_cancel=lambda:outcome.append("cancel"))
    assert host._status.get()=="Preparing result metadata..."
    return outcome


def test_activation_during_retirement_does_not_strand_metadata(hosts):
    # Reopen then activate: the job selection and mesh change while the previous
    # source is still retiring. The deferred header request must still run.
    dataset=_Dataset();host=_Host(dataset,retiring=True);hosts.append(host)
    outcome=_prepare(host,dataset)
    host.active_job_id="job";host.mesh=object()
    host.pump(lambda:False,timeout=.05)
    assert not dataset.prepared and not outcome
    host._result_retirement.set_result(None)
    assert host.pump(lambda:outcome)
    assert outcome==["ready"] and dataset.metadata_ready and dataset.prepared==1
    assert host.refreshed==1 and not host._result_metadata_preparations


def test_display_change_during_header_read_still_publishes(hosts):
    gate=Event();dataset=_Dataset(gate);host=_Host(dataset,retiring=False);hosts.append(host)
    outcome=_prepare(host,dataset)
    host.active_job_id="job";host.mesh=object();host.solution=object()
    gate.set()
    assert host.pump(lambda:outcome)
    assert outcome==["ready"] and dataset.metadata_ready and host.refreshed==1


@pytest.mark.parametrize("change",["dataset","project"])
def test_replaced_source_still_cancels_deferred_metadata(hosts,change):
    dataset=_Dataset();host=_Host(dataset,retiring=True);hosts.append(host)
    outcome=_prepare(host,dataset)
    if change=="dataset":host.result_datasets={"job":_Dataset()}
    else:host.project=SimpleNamespace(jobs={})
    host._result_retirement.set_result(None)
    assert host.pump(lambda:outcome)
    assert outcome==["cancel"] and not dataset.prepared and not dataset.metadata_ready
    assert not host._result_metadata_preparations


def test_replaced_source_during_header_read_is_not_published(hosts):
    gate=Event();dataset=_Dataset(gate);host=_Host(dataset,retiring=False);hosts.append(host)
    outcome=_prepare(host,dataset)
    host.result_datasets={}
    gate.set()
    assert host.pump(lambda:outcome)
    assert outcome==["cancel"] and host.refreshed==0


class _Workbench(SimpleNamespace):
    def update(self,field,value):setattr(self,field,value)


class _FieldHost(_Host):
    """Same host, but state goes through the real controller descriptors."""
    project=_WorkbenchField("project")
    solution=_WorkbenchField("solution")
    mesh=_WorkbenchField("mesh")
    result_datasets=_WorkbenchField("result_datasets")
    active_job_id=_WorkbenchField("active_job_id")
    shape_index=_WorkbenchField("shape_index")
    invalidate_result_requests=WorkbenchWorkflow.invalidate_result_requests

    def __init__(self,datasets,*,retiring):
        self.__dict__["workbench"]=_Workbench()
        super().__init__(None,retiring=retiring)
        self.result_datasets=dict(datasets);self.shape_index=0
        self._take_retired_result_datasets()  # drop the base host's placeholder


def _activate(host,job_id):
    # tasks.activate_job order: selection, then (possibly None->None) solution.
    host.active_job_id=job_id;host.solution=None;host.shape_index=0
    host.mesh=object()


@pytest.mark.parametrize("retiring",[True,False])
def test_real_activation_invalidation_keeps_dataset_metadata(hosts,retiring):
    gate=None if retiring else Event()
    dataset=_Dataset(gate);host=_FieldHost({"job":dataset},retiring=retiring);hosts.append(host)
    outcome=_prepare(host,dataset)
    epoch=getattr(host,"_result_source_epoch",0)
    _activate(host,"job")
    # The descriptor really did invalidate reads and the source epoch.
    assert host._result_source_epoch>epoch
    if retiring:host._result_retirement.set_result(None)
    else:gate.set()
    assert host.pump(lambda:outcome)
    assert outcome==["ready"] and dataset.metadata_ready and dataset.prepared==1
    assert host.refreshed==1 and not host._result_metadata_preparations


def test_distinct_dataset_metadata_requests_do_not_cancel_each_other(hosts):
    gate=Event();first,second=_Dataset(gate),_Dataset()
    host=_FieldHost({"a":first,"b":second},retiring=False);hosts.append(host)
    outcomes=_prepare(host,first),_prepare(host,second)
    _activate(host,"b")
    gate.set()
    assert host.pump(lambda:all(outcomes))
    assert outcomes==(["ready"],["ready"]) and first.metadata_ready and second.metadata_ready


@pytest.mark.parametrize("retiring",[True,False])
def test_real_dataset_replacement_cancels_metadata(hosts,retiring):
    gate=None if retiring else Event()
    dataset=_Dataset(gate);host=_FieldHost({"job":dataset},retiring=retiring);hosts.append(host)
    outcome=_prepare(host,dataset)
    host.result_datasets={"job":_Dataset()}
    if retiring:host._result_retirement.set_result(None)
    else:gate.set()
    assert host.pump(lambda:outcome)
    assert outcome==["cancel"] and host.refreshed==0
    if retiring:assert not dataset.prepared


def test_real_project_replacement_cancels_deferred_metadata(hosts):
    dataset=_Dataset();host=_FieldHost({"job":dataset},retiring=True);hosts.append(host)
    outcome=_prepare(host,dataset)
    host.project=SimpleNamespace(jobs={})
    host._result_retirement.set_result(None)
    assert host.pump(lambda:outcome)
    assert outcome==["cancel"] and not dataset.prepared


def test_deferred_display_request_keeps_display_ownership(hosts):
    dataset=_Dataset();host=_Host(dataset,retiring=True);hosts.append(host)
    loads=[];cancelled=[]
    host._request_result(lambda cancel:loads.append(True),lambda value:None,dataset=dataset,on_cancel=lambda:cancelled.append(True))
    host.active_job_id="job"
    host._result_retirement.set_result(None)
    assert host.pump(lambda:cancelled)
    assert not loads


@pytest.mark.parametrize("route",["workflow","panel"])
def test_late_metadata_display_continuation_does_not_redraw_new_job(route):
    first,second=object(),object();callbacks=[];cancelled=[];draws=[]
    def prepare(dataset,**kwargs):
        assert dataset is first
        callbacks.append(kwargs["on_ready"])
        return True
    app=SimpleNamespace(active_job_id="a",result_datasets={"a":first,"b":second},prepare_result_dataset=prepare,
        show_persisted_result=lambda *args,**kwargs:draws.append((args,kwargs)))
    cancel=lambda:cancelled.append(True)
    if route=="workflow":
        WorkbenchWorkflow.show_persisted_result(app,"only_a",frame=4,on_cancel=cancel)
    else:
        from anyfem.ui.qt.tasks import ResultsTask
        panel=SimpleNamespace(app=app,_display_selection=lambda:app.active_job_id,use_retained=lambda:True,
            show_results=lambda **kwargs:draws.append(kwargs))
        ResultsTask.show_results(panel,on_cancel=cancel)
    app.active_job_id="b"
    callbacks[0]()
    assert cancelled==[True] and not draws
