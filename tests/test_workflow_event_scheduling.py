"""The shared workflow retains the legacy frontend event/poll protocol."""
from types import SimpleNamespace
from anyfem.application.workflow import WorkbenchWorkflow
from anyfem.jobs import JobEvent


def test_unbounded_frontend_keeps_zero_argument_poll_and_per_event_hooks():
    seen=[];scheduled=[]
    events=[JobEvent("run","progress","first"),JobEvent("run","progress","second")]
    class Manager:
        def poll(self):return events
    class Scheduler:
        def call_later(self,delay,callback):scheduled.append((delay,callback));return "next"
        def cancel_call(self,identifier):raise AssertionError("legacy poll was not cancelled")
    class Frontend(WorkbenchWorkflow):
        def _observe_run_event(self,kind,event):seen.append(("observe",event.message))
        def _on_progress(self,text,payload):seen.append(("progress",text))
        def _on_progress_batch(self,events):raise AssertionError("legacy progress was batched")
        def _poll_result_artifacts(self):pass
        def _poll_job_log_artifacts(self):pass
        def _poll_mesh_jobs(self):pass
        def _poll_recovery_write(self):pass
    app=Frontend()
    app.workbench=SimpleNamespace(project=SimpleNamespace(jobs={"run":object()}),job_manager=Manager())
    app._closing=False;app._job_poll="old";app.scheduler=Scheduler();app.worker=SimpleNamespace(POLL_MS=60)
    app._poll_jobs()
    assert seen==[("observe","first"),("progress","first"),("observe","second"),("progress","second")]
    assert scheduled==[(60,app._poll_jobs)] and app._job_poll=="next"
