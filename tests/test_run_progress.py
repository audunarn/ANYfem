"""Notification display contracts, without solving or meshing."""
from types import SimpleNamespace
import numpy as np
import pytest
from anyfem.presentation.run_progress import RunProgressCatalog, PROGRESS_GRAPH
from anyfem.jobs import JobEvent
from anyfem.mesh_jobs import MeshJobEvent,MeshProgress
from anysolver import ProgressEvent


@pytest.fixture
def catalog():
    clock=SimpleNamespace(now=0.)
    return RunProgressCatalog(clock=lambda:clock.now,max_finished=2),clock


def test_worker_fraction_work_units_and_measured_elapsed(catalog):
    monitor,clock=catalog
    run=monitor.ingest('solve',JobEvent('a','queued','queued'),'Linear')
    clock.now=3.;assert run.elapsed(clock.now)==3.
    monitor.ingest('solve',JobEvent('a','started','building snapshot'),'Linear')
    clock.now=7.
    payload=ProgressEvent('stage','assembly','Assembling',completed=2,total=4)
    monitor.ingest('solve',JobEvent('a','progress','Assembling',payload),'Linear')
    assert run.elapsed(clock.now)==4. and run.fraction==.5 and run.work=='2 / 4 work units'
    series=run.series(PROGRESS_GRAPH)
    np.testing.assert_array_equal(series.x,[4.]);np.testing.assert_array_equal(series.y,[50.])


def test_unknown_progress_never_invents_a_percentage(catalog):
    monitor,clock=catalog
    run=monitor.ingest('solve',JobEvent('a','started','Factorizing matrix'),'Linear')
    clock.now=400.
    assert run.fraction is None and run.series(PROGRESS_GRAPH) is None and run.elapsed(clock.now)==400.
    monitor.ingest('solve',JobEvent('a','progress','Recovering stresses'),'Linear')
    assert run.fraction is None


def test_different_solver_work_stages_are_not_joined_as_one_completion_path(catalog):
    monitor,clock=catalog
    run=monitor.ingest('solve',JobEvent('a','progress','Assembly',ProgressEvent('stage','assembly',completed=2,total=4)),'Linear')
    clock.now=5.
    monitor.ingest('solve',JobEvent('a','progress','Factorizing',ProgressEvent('stage','factorization',completed=1,total=5)),'Linear')
    assert run.progress_values==[20.] and 'factorization' in run.series(PROGRESS_GRAPH).name
    clock.now=10.
    monitor.ingest('solve',JobEvent('a','completed','done'),'Linear')
    assert run.stage=='factorization' and run.progress_values==[20.]


@pytest.mark.parametrize('state',['completed','partial','cancelled','failed'])
def test_terminal_elapsed_and_late_packets_cannot_revive_run(catalog,state):
    monitor,clock=catalog
    run=monitor.ingest('mesh',MeshJobEvent('a','started','Preparing'),'Mesh')
    clock.now=4.;monitor.ingest('mesh',MeshJobEvent('a',state,state),'Mesh',stale=True)
    clock.now=20.;monitor.ingest('mesh',MeshJobEvent('a','progress','late',MeshProgress('generation','late',.4)),'Mesh')
    assert run.state==state and run.elapsed(clock.now)==4. and run.stale
    assert run.message==state


def test_newton_trials_and_accepted_paths_are_separate(catalog):
    monitor,clock=catalog
    run=monitor.ingest('solve',JobEvent('a','started','Nonlinear'),'Nonlinear')
    for iteration,residual in [(1,100.),(2,.01)]:
        monitor.ingest('solve',JobEvent('a','progress',f'Increment trial 1 | load factor 0.5 / 1 | increment 0.5 | Newton iteration {iteration} | residual {residual}'),'Nonlinear')
    event=ProgressEvent('nonlinear_step','nonlinear','accepted',metadata={'step_index':1,'load_factor':.5,'max_translation':.003,'max_equivalent_plastic_strain':.02,'support_reactions':{'Base':[3,4,0,0,0,0]}})
    monitor.ingest('solve',JobEvent('a','progress','accepted',event),'Nonlinear')
    assert run.data.load_factors==[.5] and run.data.trial_residuals==[100.,.01]
    assert 'Accepted load factor 0.5' in run.metric_text() and 'Max |u| 0.003 m' in run.metric_text()
    assert 'Reaction: Base | Fx' in run.graph_choices
    monitor.ingest('solve',JobEvent('a','progress','Increment trial 2 | load factor 1 / 1 | increment 0.5 | Newton iteration 1 | residual 20'),'Nonlinear')
    assert run.data.trial_residuals==[20.] and run.data.load_factors==[.5]


def test_logs_and_finished_runs_are_bounded_without_evicting_active(catalog):
    monitor,clock=catalog
    active=monitor.ingest('mesh',MeshJobEvent('a','started','Mesh'),'Mesh');active.max_events=3
    for index in range(10):
        clock.now=index;monitor.ingest('mesh',MeshJobEvent('a','progress','Mesh',MeshProgress('generation','Mesh',index/10)),'Mesh')
    assert len(active.events)==len(active.progress_values)==3 and active.sequence==11
    for index in range(5):monitor.ingest('solve',JobEvent(str(index),'completed','done'),'Old')
    assert ('mesh','a') in monitor.runs and len(monitor.runs)==3


@pytest.mark.parametrize('fraction',[float('nan'),float('inf'),2.,-1.])
def test_invalid_reported_fraction_is_not_shown_as_valid_progress(catalog,fraction):
    monitor,_=catalog
    run=monitor.ingest('solve',JobEvent('a','progress','Working',{'fraction':fraction}),'Linear')
    assert run.fraction is None and run.series(PROGRESS_GRAPH) is None
