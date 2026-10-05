"""Qt remains idle between real updates and discloses unfinished capacity stages."""
import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('set ANYFEM_RUN_QT_TESTS=1',allow_module_level=True)
pytest.importorskip('PySide6')
from test_qt_geometry_actions import qapp,window
from anyfem.jobs import JobEvent
from anyfem.model.records import JobRecord
from anyfem.solve.run import _capacity_reporter


def test_completed_stage_remains_busy_until_terminal_job(window):
    window.project.jobs['capacity']=JobRecord('',name='Capacity',id='capacity')
    monitor=window.run_monitor
    monitor.observe('solve',JobEvent('capacity','started','Capacity'),'Capacity')
    events=[];report=_capacity_reporter(events.append)
    report({'stage':'linear_static.complete','status':'converged','completed':1,'total':1})
    for event in events:monitor.observe('solve',JobEvent('capacity','progress',str(event),event),'Capacity')
    monitor.refresh()
    assert monitor.state.text()=='Running'
    assert monitor.bar.minimum()==monitor.bar.maximum()==0
    assert 'still active' in monitor.message.text()
    monitor.observe('solve',JobEvent('capacity','completed','Full capacity run finished'),'Capacity');monitor.refresh()
    assert monitor.state.text()=='Completed' and monitor.bar.value()==1000


def test_gui_default_limit_and_advanced_resource_override(window):
    task=window.panels['Solve'];task.analysis.setCurrentText('Capacity')
    assert task.use_resources.isChecked()
    assert task.settings()['resources'].solver_threads in (1,2)
    task.options.setPlainText('{"resources":{"solver_threads":3}}')
    assert task.settings()['resources'].solver_threads==3
    task.options.setPlainText('{}');task.use_resources.setChecked(False)
    assert 'resources' not in task.settings()


def test_real_window_idle_after_barge_refresh(window,qapp,monkeypatch):
    from anyfem.application.examples import floating_barge_section
    window._set_project(floating_barge_section())
    for _ in range(4):qapp.processEvents()
    calls=[];real=window.refresh_all
    def counted():calls.append(1);real()
    monkeypatch.setattr(window,'refresh_all',counted)
    for _ in range(20):qapp.processEvents()
    assert not calls
    window.workbench._publish('state_changed','analysis')
    for _ in range(4):qapp.processEvents()
    assert len(calls)==1


def test_unchanged_job_table_does_not_rehash_or_resize(window,monkeypatch):
    window.project.jobs['job']=JobRecord('',name='Capacity',id='job')
    window.job_status.refresh()
    calls=[]
    monkeypatch.setattr(window,'_job_is_stale',lambda job:calls.append(job.id) or False)
    for _ in range(20):window.job_status.refresh()
    assert not calls


def test_barge_ui_timers_and_navigation_continue_during_silent_job(window,qapp):
    import threading
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from anyfem.application.examples import floating_barge_section
    from anyfem.model.records import AnalysisDefinition
    window._set_project(floating_barge_section())
    entered=threading.Event();release=threading.Event()
    def silent(progress, **kwargs):
        progress('Recovering prestress and solving buckling modes')
        entered.set();assert release.wait(5)
        return None
    manager=window.job_manager
    record=manager.submit(AnalysisDefinition('Capacity'),window.session.snapshot(),silent)
    assert entered.wait(5)
    ticks=[];timer=QTimer(window);timer.setInterval(30);timer.timeout.connect(lambda:ticks.append(1))
    timer.start()
    try:
        QTest.qWait(250)
        assert len(ticks)>=4
        window.workspace_actions['Results'].trigger()
        assert window.workspace_actions['Results'].isChecked()
        manager.heartbeat_interval=0.
        window._poll_jobs();window.run_monitor.refresh()
        assert 'Worker still active' in window.run_monitor.message.text()
        assert window.run_monitor.cancel.isEnabled()
    finally:
        timer.stop();release.set();manager.wait(record.id,timeout=5)
