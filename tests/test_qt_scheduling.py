"""Typed search and verbose worker events yield without dropping ordered data."""
import os
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)
pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from anyfem import Project
from anyfem.jobs import JobEvent
from anyfem.model.records import JobRecord
from test_qt_geometry_actions import qapp, window


def test_only_typing_debounces_and_programmatic_search_supersedes(window,monkeypatch):
    tree=window.tree;calls=[]
    original=tree.refresh
    def refresh():calls.append(tree.query);original()
    monkeypatch.setattr(tree,"refresh",refresh)
    QTest.keyClicks(tree.search_edit,"123")
    assert calls==[] and tree.query==""
    QTest.qWait(tree._search_timer.interval()+60)
    assert calls==["123"]
    QTest.keyClicks(tree.search_edit,"4")
    tree.search("1")
    assert calls==["123","1"] and not tree._search_timer.isActive()
    QTest.qWait(tree._search_timer.interval()+30)
    assert calls==["123","1"]


def test_pending_search_is_cancelled_on_project_replace_and_close(window,monkeypatch):
    tree=window.tree
    QTest.keyClicks(tree.search_edit,"987")
    window._set_project(Project("Replacement"))
    assert tree._pending_query is None and not tree._search_timer.isActive()
    assert tree.search_edit.text()==tree.query==""
    QTest.qWait(tree._search_timer.interval()+30)
    assert tree.query==""
    QTest.keyClicks(tree.search_edit,"6")
    window.session.mark_saved();window.close()
    assert tree._pending_query is None and not tree._search_timer.isActive()


def queue_progress(window,count,identifier="run"):
    window.project.jobs[identifier]=JobRecord("",id=identifier,name="Synthetic",status="running")
    messages=[f"ordered progress {index}" for index in range(count)]
    for index,text in enumerate(messages):
        window.job_manager._events.put(JobEvent(identifier,"progress",text,{"index":index}))
    return ["solver: "+text for text in messages]


def test_verbose_burst_yields_and_flushes_complete_order_before_completion(window,qapp,monkeypatch):
    limit=window.JOB_EVENT_BATCH_LIMIT
    messages=queue_progress(window,2*limit+3)
    window.job_manager._events.put(JobEvent("run","completed","done",object()))
    panel=window.panels["Solve"];ingested=[];writes=[];completed=[];yielded=[]
    original_ingest=panel.progress_data.ingest
    def ingest(text,payload):
        ingested.append((text,payload));return original_ingest(text,payload)
    monkeypatch.setattr(panel.progress_data,"ingest",ingest)
    original_append=panel.transcript.appendPlainText
    def append(text):writes.append(text);original_append(text)
    monkeypatch.setattr(panel.transcript,"appendPlainText",append)
    def solved(*args):
        assert [text for text,_ in ingested]==messages
        assert panel.transcript.toPlainText().splitlines()==messages
        assert panel.progress.text()==messages[-1]
        assert window.log.toPlainText().splitlines()[-len(messages):]==messages
        completed.append(True)
    monkeypatch.setattr(window,"_on_solved",solved)
    QTimer.singleShot(0,lambda:yielded.append(len(ingested)))
    window._poll_jobs()
    assert len(ingested)==limit and not completed
    assert window._job_poll.interval()==0
    QTest.qWait(100)
    assert yielded==[limit] and completed==[True]
    assert [payload["index"] for _,payload in ingested]==list(range(len(messages)))
    assert len(writes)==3
    assert window._job_poll.interval()==window.worker.POLL_MS


@pytest.mark.parametrize("terminal",["failed","cancelled"])
def test_terminal_status_follows_buffered_progress(window,monkeypatch,terminal):
    messages=queue_progress(window,window.JOB_EVENT_BATCH_LIMIT+3)
    window.job_manager._events.put(JobEvent("run",terminal,"terminal diagnostic"))
    window._poll_jobs()
    assert window._status.get()==messages[window.JOB_EVENT_BATCH_LIMIT-1]
    QTest.qWait(100)
    transcript=window.panels["Solve"].transcript.toPlainText().splitlines()
    assert transcript[:len(messages)]==messages
    log=window.log.toPlainText().splitlines()
    assert log[-len(messages)-1:-1]==messages
    assert terminal in log[-1] and terminal in window._status.get()
    if terminal=="failed":assert transcript[-1]=="failed: terminal diagnostic"


def test_pending_burst_and_cancel_do_not_leak_across_project_switch(window,monkeypatch):
    limit=window.JOB_EVENT_BATCH_LIMIT
    queue_progress(window,limit+7)
    old_manager=window.job_manager
    old_manager._events.put(JobEvent("run","cancelled","old cancellation"))
    window._poll_jobs()
    assert old_manager._events.qsize()==8
    window._set_project(Project("Replacement"))
    # Even a reused run ID belongs to the new manager/document.
    window.project.jobs["run"]=JobRecord("",id="run",name="New run")
    window.job_manager._events.put(JobEvent("run","progress","new document progress"))
    QTest.qWait(100)
    assert window.panels["Solve"].transcript.toPlainText()=="new document progress"
    assert window._status.get()=="new document progress"


def test_close_cancels_pending_burst_continuation(window):
    queue_progress(window,window.JOB_EVENT_BATCH_LIMIT+7)
    window._poll_jobs()
    window.session.mark_saved();window.close()
    assert not window.scheduler.pending
    QTest.qWait(30)
    assert not window.scheduler.pending


def test_batched_progress_preserves_all_structured_graph_points(window):
    window.project.jobs["run"]=JobRecord("",id="run",name="Synthetic")
    for index in range(1,5):
        payload={"type":"nonlinear_step","step_index":index,"load_factor":index/4,
                 "max_translation":index*.01,"support_reactions":{"Base":[index,0,0,0,0,0]}}
        window.job_manager._events.put(JobEvent("run","progress","accepted",payload))
    window._poll_jobs()
    data=window.panels["Solve"].progress_data
    assert data.increments==[1,2,3,4]
    assert data.load_factors==[.25,.5,.75,1.]
    assert data.max_displacements==[.01,.02,.03,.04]
    assert [row[0] for row in data.support_reactions["Base"]]==[1,2,3,4]


def test_terminal_document_switch_discards_remaining_old_batch(window,monkeypatch):
    queue_progress(window,1)
    manager=window.job_manager
    manager._events.put(JobEvent("run","completed","done",object()))
    manager._events.put(JobEvent("run","progress","old tail"))
    def replace(*args):window._set_project(Project("Replacement during completion"))
    monkeypatch.setattr(window,"_on_solved",replace)
    window._poll_jobs()
    assert window.project.name=="Replacement during completion"
    assert not window.panels["Solve"].transcript.toPlainText()
    assert "old tail" not in window.log.toPlainText()
