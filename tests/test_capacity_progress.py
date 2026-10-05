"""Capacity phase disclosure and worker liveness do not fabricate progress."""
import threading
from types import SimpleNamespace
from anyfem.solve.run import _capacity_reporter
from anyfem.presentation.run_progress import RunProgressCatalog
from anyfem.jobs import JobManager,JobEvent
from anyfem.document import DocumentSession
from anyfem.model import Project,AnalysisDefinition


def test_initial_convergence_is_not_full_capacity_completion():
    events=[]
    report=_capacity_reporter(events.append)
    report({"stage":"linear_static.complete","status":"converged","completed":1,"total":1})
    assert 'initial linear' in str(events[0])
    assert 'still active' in str(events[-1])
    catalog=RunProgressCatalog(clock=lambda:10.)
    run=catalog.ingest('solve',JobEvent('a','started','Capacity'),'Capacity')
    for event in events:catalog.ingest('solve',JobEvent('a','progress',str(event),event),'Capacity')
    assert run.state=='running' and run.fraction is None
    assert run.stage=='Prestress recovery and buckling modes'
    report({"stage":"buckling.complete","status":"converged","completed":3,"total":3})
    assert 'preparing nonlinear' in str(events[-1])


def test_heartbeat_preserves_true_update_time_and_graph():
    clock=SimpleNamespace(now=0.)
    catalog=RunProgressCatalog(clock=lambda:clock.now)
    run=catalog.ingest('solve',JobEvent('a','started','Assembling'),'Capacity')
    catalog.ingest('solve',JobEvent('a','progress','Assembling',{'stage':'assembly','fraction':.5}),'Capacity')
    revision=run.graph_revision
    clock.now=100.
    catalog.ingest('solve',JobEvent('a','heartbeat','Worker active',{'heartbeat':True}),'Capacity')
    assert run.updated_at==0. and run.fraction==.5 and run.graph_revision==revision
    assert run.stage=='assembly' and not run.terminal
    catalog.ingest('solve',JobEvent('a','completed','Completed'),'Capacity')
    catalog.ingest('solve',JobEvent('a','heartbeat','Late heartbeat'),'Capacity')
    assert run.message=='Completed' and run.terminal


def test_job_heartbeat_during_silent_call_and_cancellation():
    project=Project('slow job');manager=JobManager(project)
    entered=threading.Event();release=threading.Event()
    def slow(progress, **kwargs):
        progress('Recovering prestress and solving buckling modes')
        entered.set();assert release.wait(5)
        return None
    record=manager.submit(AnalysisDefinition('capacity'),DocumentSession(project).snapshot(),slow)
    assert entered.wait(5)
    try:
        initial=manager.poll()
        assert initial[-1].kind=='progress'
        manager.heartbeat_interval=0.
        event=manager.poll()[-1]
        assert event.kind=='heartbeat' and 'not completion' in event.message
        assert manager.cancel(record.id)
        assert 'Cancellation pending' in manager.poll()[-1].message
    finally:release.set();manager.wait(record.id,timeout=5)
    terminal=manager.poll()
    assert terminal[-1].kind=='cancelled'
    assert not any(e.kind=='heartbeat' for e in terminal)
    assert not manager.poll()


def test_capacity_scope_covers_prerequisites_and_restores_native_threads(monkeypatch):
    import numpy as np
    import scipy.linalg
    import anysolver
    from anysolver import ResourceConfig
    from threadpoolctl import threadpool_info
    from anyfem.solve import run
    from anyfem.post import results
    before=[p['num_threads'] for p in threadpool_info()]
    observed=[]
    def owner(*args, **kwargs):
        observed.extend(p['num_threads'] for p in threadpool_info())
        kwargs['progress_callback']({'stage':'linear_static.complete','status':'converged'})
        raise RuntimeError('intentional stop after prerequisite check')
    monkeypatch.setattr(anysolver,'run_nonlinear_capacity_workflow',owner)
    monkeypatch.setattr(run,'_resolve_built',lambda *a,**k:SimpleNamespace(fe_model=object(),load_case=object()))
    import pytest
    with pytest.raises(RuntimeError,match='intentional stop'):
        run.solve_capacity(built=object(),resources=ResourceConfig(solver_threads=2),progress=lambda v:None)
    assert observed and all(n==2 for n in observed)
    assert [p['num_threads'] for p in threadpool_info()]==before


def test_terminal_summary_cannot_be_changed_back_to_cancelling():
    project=Project('completion');manager=JobManager(project)
    entered=threading.Event();release=threading.Event()
    def summary():
        entered.set();assert release.wait(5)
        return 'complete'
    def solve(**kwargs):return SimpleNamespace(summary=summary)
    record=manager.submit(AnalysisDefinition('case'),DocumentSession(project).snapshot(),solve)
    assert entered.wait(5)
    try:
        assert not manager.cancel(record.id)
        manager.heartbeat_interval=0.
        assert not any(e.kind=='heartbeat' for e in manager.poll())
    finally:release.set();manager.wait(record.id,timeout=5)
    assert manager.poll()[-1].kind=='completed'
    assert record.id not in manager._activity
