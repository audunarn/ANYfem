"""Synthetic real-window run monitoring; no engineering computations."""
import os
from types import SimpleNamespace
import numpy as np
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('set ANYFEM_RUN_QT_TESTS=1',allow_module_level=True)
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication,QToolTip
from PySide6.QtCore import Qt,QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from anyfem.ui.qt.app import QtFemWindow
from anyfem.jobs import JobEvent
from anyfem.mesh_jobs import MeshJobEvent,MeshProgress
from anyfem.model.records import JobRecord,MeshRecord
from anyfem.post.history import Series
from anyfem.presentation.run_progress import PROGRESS_GRAPH
from anysolver import ProgressEvent


@pytest.fixture(scope='module')
def qapp():return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp,tmp_path,monkeypatch):
    from anyfem.io import recovery
    monkeypatch.setattr(recovery,'default_recovery_root',lambda:tmp_path/'recovery')
    app=QtFemWindow(viewer_backend='software');app.show();qapp.processEvents()
    yield app
    app.session.mark_saved();app.close();qapp.processEvents()


def record(window,kind='solve',identifier='run'):
    if kind=='solve':item=JobRecord('',name='Nonlinear plate',id=identifier);window.project.jobs[identifier]=item
    else:item=MeshRecord('Mesh plate',window.session.revision.model_hash,'','',id=identifier);window.project.mesh_records[identifier]=item
    return item


def test_shared_poll_routes_both_run_types_without_consuming_twice(window,qapp,monkeypatch):
    record(window);record(window,'mesh','mesh')
    solve=[JobEvent('run','started','Factorizing'),JobEvent('run','progress','Assembling',ProgressEvent('stage','assembly',completed=2,total=4))]
    mesh=[MeshJobEvent('mesh','started','Meshing'),MeshJobEvent('mesh','progress','Generating',MeshProgress('generation','Generating',.2))]
    def poll(items):result=list(items);items.clear();return result
    monkeypatch.setattr(window.job_manager,'poll',lambda:poll(solve))
    monkeypatch.setattr(window.mesh_task_manager,'poll',lambda:poll(mesh))
    window._poll_jobs();window.run_monitor.refresh();qapp.processEvents()
    assert not solve and not mesh and len(window.run_monitor.catalog.runs)==2
    assert window.run_monitor.bar.value()==200 and window.run_monitor.graph.currentText()==PROGRESS_GRAPH
    assert window.run_dock.isVisible() and window.run_monitor.cancel.isEnabled()
    assert 'progress: Generating' in window.run_monitor.transcript.toPlainText()


def test_residual_live_metrics_graph_pause_resume_and_run_switch(window,qapp):
    record(window);monitor=window.run_monitor
    window._observe_run_event('solve',JobEvent('run','started','Nonlinear'))
    for iteration,residual in [(1,1000),(2,1),(3,.001)]:
        text=f'Increment trial 1 | load factor .5 / 1 | increment .5 | Newton iteration {iteration} | residual {residual}'
        window._observe_run_event('solve',JobEvent('run','progress',text))
    monitor.refresh();qapp.processEvents()
    assert 'Iteration 3' in monitor.metrics.text() and 'Residual 0.001' in monitor.metrics.text()
    assert 'Newton residual' in monitor.graph.currentText()
    np.testing.assert_allclose(monitor.plot.series[0].y,[3,0,-3])
    monitor.pause.setChecked(True)
    window._observe_run_event('solve',JobEvent('run','progress','Increment trial 1 | load factor .5 / 1 | increment .5 | Newton iteration 4 | residual .00001'))
    monitor.refresh();assert len(monitor.plot.series[0].y)==3 and 'Iteration 4' in monitor.metrics.text()
    record(window,'mesh','other');window._observe_run_event('mesh',MeshJobEvent('other','started','Meshing'))
    monitor.refresh();assert not monitor.plot.series
    monitor.pause.setChecked(False);monitor.refresh();assert not monitor.plot.series


def test_dynamic_reactions_and_bounded_display_log(window,qapp):
    record(window);monitor=window.run_monitor
    window._observe_run_event('solve',JobEvent('run','started','Running'))
    for index in range(1,4):
        payload=ProgressEvent('nonlinear_step','nonlinear','accepted',metadata={'step_index':index,'load_factor':index/3,'support_reactions':{'Base':[index,2*index,0,0,0,0]}})
        window._observe_run_event('solve',JobEvent('run','progress','accepted',payload))
    monitor.refresh();monitor.graph.setCurrentText('Reaction: Base | Fx');monitor.choose_graph();monitor.refresh()
    np.testing.assert_array_equal(monitor.plot.series[0].y,[1,2,3])
    panel=window.panels['Solve'];panel.begin_job('Nonlinear','run','inputs')
    panel.append_progress('accepted',payload);panel.update_graph()
    assert panel.graph.findText('Reaction: Base | Fx')>=0
    assert panel.transcript.document().maximumBlockCount()==1500
    panel.reset();assert panel.graph.findText('Reaction: Base | Fx')==-1


def test_event_burst_batches_selector_and_plot_updates(window,qapp,monkeypatch):
    record(window,'mesh','mesh');monitor=window.run_monitor
    draws=[];original=monitor.plot.show_series
    monkeypatch.setattr(monitor.plot,'show_series',lambda series:draws.append(series) or original(series))
    window._observe_run_event('mesh',MeshJobEvent('mesh','started','Mesh'))
    for index in range(200):window._observe_run_event('mesh',MeshJobEvent('mesh','progress','Generation',MeshProgress('generation','Generation',index/200)))
    assert not draws and monitor.jobs.count()==0
    monitor.refresh();assert monitor.jobs.count()==1 and len(draws)==2
    assert monitor.plot.y_limits==(0.,100.) and len(monitor.plot.series[0].x)==200
    monitor.refresh();assert len(draws)==2


@pytest.mark.parametrize('state',['completed','partial','cancelled','failed'])
def test_terminal_state_is_explicit_and_stops_activity(window,qapp,state):
    record(window);monitor=window.run_monitor
    window._observe_run_event('solve',JobEvent('run','started','Factorizing'))
    monitor.refresh();assert monitor.bar.minimum()==monitor.bar.maximum()==0
    window._observe_run_event('solve',JobEvent('run',state,state));monitor.refresh()
    assert monitor.state.text().startswith(state.capitalize()) and not monitor.cancel.isEnabled()
    assert monitor.bar.maximum()==1000
    assert monitor.bar.value()==(1000 if state=='completed' else 0)


def test_cancel_routes_exact_selected_job_and_resets_on_project_replacement(window,qapp,monkeypatch):
    record(window,'mesh','mesh');monitor=window.run_monitor
    window._observe_run_event('mesh',MeshJobEvent('mesh','started','Mesh'))
    calls=[];monkeypatch.setattr(window.mesh_task_manager,'cancel',lambda identifier:calls.append(identifier) or True)
    monitor.refresh();monitor.cancel.click();assert calls==['mesh'] and not monitor.cancel.isEnabled()
    monitor.refresh();assert not monitor.cancel.isEnabled()
    window.new_project();qapp.processEvents()
    assert not monitor.catalog.runs and monitor.jobs.count()==0 and not monitor.transcript.toPlainText()
    assert not monitor.plot.series and not monitor.cancel.isEnabled()
    assert monitor.state.text()=='No runs yet'


def test_monitor_failure_does_not_stop_existing_poll_workflow(window,qapp,monkeypatch):
    record(window);events=[JobEvent('run','progress','Factorizing')]
    monkeypatch.setattr(window.job_manager,'poll',lambda:events.copy() if events else [])
    monkeypatch.setattr(window.run_monitor,'observe',lambda *a,**k:(_ for _ in ()).throw(ValueError('monitor defect')))
    window._poll_jobs();events.clear()
    assert 'Factorizing' in window.panels['Solve'].transcript.toPlainText()


def test_replacement_with_same_saved_document_id_clears_old_live_state(window,qapp,monkeypatch):
    from anyfem.application import workflow
    record(window);monitor=window.run_monitor
    window._observe_run_event('solve',JobEvent('run','started','Running'));monitor.refresh()
    assert monitor.catalog.runs
    original_id=window.project.document_id
    replacement=workflow.default_project();replacement.document_id=original_id
    monkeypatch.setattr(workflow,'default_project',lambda:replacement)
    window.new_project();qapp.processEvents()
    assert window.project.document_id==original_id and not monitor.catalog.runs
    assert monitor.bar.format()=='No run' and monitor.graph.count()==0


def test_plot_keeps_gaps_and_point_values_and_handles_missing_data(window,qapp):
    from anyfem.ui.qt.plot import HistoryPlot
    plot=HistoryPlot();plot.resize(650,260);plot.show()
    plot.raise_();plot.activateWindow();assert QTest.qWaitForWindowActive(plot,2000)
    plot.show_series([Series('Missing middle',np.array([0.,1.,2.]),np.array([0.,np.nan,2.]),'Step','Response','','m')])
    qapp.processEvents();image=plot.grab().toImage()
    # Original renderer joined the two endpoints through the missing middle.
    assert not any(image.pixelColor(x,y).blue()>160 and image.pixelColor(x,y).red()<90
                   for x in range(315,345) for y in range(105,145))
    assert len(plot._samples)==2
    # Native QTest cursor movement emits no MouseMove in this Windows host.
    # Deliver the same Qt event deterministically (retained tooltip_probe.py).
    point=plot._samples[-1][0]
    event=QMouseEvent(QEvent.MouseMove,point,plot.mapToGlobal(point.toPoint()),Qt.NoButton,Qt.NoButton,Qt.NoModifier)
    qapp.sendEvent(plot,event);qapp.processEvents()
    assert 'Response (m): 2' in QToolTip.text()
    plot.show_series([Series('Absent',np.array([0.]),np.array([np.nan]))]);qapp.processEvents()
    assert not plot._samples
    plot.close()


def test_layout_reset_and_shutdown_stop_monitor_timer(window,qapp):
    window.reset_workspace_layout();assert window.run_monitor.timer.isActive()
    window.session.mark_saved();window.close();qapp.processEvents()
    assert not window.run_monitor.timer.isActive()
    assert not window.panels['Solve'].graph_timer.isActive()
