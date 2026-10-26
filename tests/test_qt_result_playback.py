"""Demand-driven playback cancellation and pacing, without numerical jobs."""
from types import SimpleNamespace
from anyfem.ui.qt.playback import ResultPlayback

class Scheduler:
    def __init__(self):self.calls=[];self.cancelled=[]
    def call_later(self,delay,callback):
        token=SimpleNamespace(delay=delay,callback=callback);self.calls.append(token);return token
    def cancel_call(self,token):
        self.cancelled.append(token)
        if token in self.calls:self.calls.remove(token)
    def tick(self):
        token=self.calls.pop(0);token.callback();return token

class Harness:
    def __init__(self,count=600,synchronous=False):
        self.scheduler=Scheduler();self.frame=0;self.owner=0;self.canvas=0
        self.requests=[];self.ready=[];self.cancel=[];self.published=[]
        self.invalidations=0;self.canvas_stops=0;self.failed=[];self.count=count
        self.synchronous=synchronous;self.max_inflight=0
        self.clock_value=0.
        self.play=ResultPlayback(scheduler=self.scheduler,request=self.request,set_frame=self.set_frame,
            context=lambda:(self.owner,self.canvas,self.frame),invalidate=self.invalidate,stop_canvas=self.stop_canvas,failed=self.failed.append,
            clock=lambda:self.clock_value)
    def set_frame(self,index):self.frame=index
    def stop_canvas(self):self.canvas_stops+=1
    def invalidate(self):self.invalidations+=1
    def request(self,*,on_ready,on_cancel):
        self.requests.append(self.frame);self.ready.append(on_ready);self.cancel.append(on_cancel)
        self.max_inflight=max(self.max_inflight,int(self.play.inflight))
        if self.synchronous:self.complete()
    def complete(self):
        callback=self.ready.pop(0);callback()

def test_stop_first_inflight_read_and_late_callbacks_cannot_restart():
    h=Harness();h.play.start(600,20)
    assert h.requests==[0] and h.play.inflight and not h.scheduler.calls
    h.play.stop();assert not h.play.active and h.invalidations==1
    h.complete();h.cancel[0]()
    assert not h.scheduler.calls and h.requests==[0]
    h.play.stop();assert h.invalidations==1

def test_only_one_frame_requested_and_completion_paces_not_eager_cache():
    h=Harness();h.play.start(600,4)
    for index in range(8):
        assert h.requests==list(range(index+1)) and h.play.inflight
        h.complete();assert len(h.scheduler.calls)==1 and h.scheduler.calls[0].delay==250
        assert not h.play.inflight and h.play.completed_frames==index+1
        h.scheduler.tick()
    h.play.stop();assert not h.scheduler.calls and h.max_inflight==1

def test_stop_between_frames_cancels_owned_timer_and_replay_ignores_old_tokens():
    h=Harness();h.play.start(600,20);h.complete();old=h.scheduler.calls[0]
    h.play.stop();assert old in h.scheduler.cancelled
    old.callback();assert h.requests==[0]
    h.play.start(600,8,index=17);assert h.requests==[0,17]
    # Old cancellation arrives after a new run and must not stop the new owner.
    h.cancel[0]();assert h.play.active
    h.complete();assert h.scheduler.calls[0].delay==125
    h.play.stop()

def test_source_canvas_and_manual_frame_change_cancel_before_next_request():
    for attribute in ('owner','canvas','frame'):
        h=Harness();h.play.start(600,20);h.complete()
        setattr(h,attribute,99);h.scheduler.tick()
        assert not h.play.active and h.requests==[0] and not h.scheduler.calls

def test_cancel_error_and_synchronous_request_settle_once():
    h=Harness();h.play.start(600,4);h.cancel[0]();h.complete()
    assert not h.play.active and not h.scheduler.calls
    h=Harness(synchronous=True);h.play.start(600,.5)
    assert h.play.active and not h.play.inflight and h.scheduler.calls[0].delay==2000
    h.play.stop()
    h=Harness()
    def refused(**callbacks):raise ValueError('read refused')
    h.play.request=refused;h.play.start(600,4)
    assert not h.play.active and str(h.failed[0])=='read refused'


def test_invalid_fps_and_timer_overflow_refuse_before_stopping_existing_run():
    import pytest
    h=Harness();h.play.start(600,4)
    before=(h.play.generation,h.play.active,h.play.inflight,h.invalidations,list(h.requests))
    for fps in (0,-1,float('nan'),float('inf'),1e-20,1e-320):
        with pytest.raises(ValueError):h.play.start(600,fps)
        assert (h.play.generation,h.play.active,h.play.inflight,h.invalidations,list(h.requests))==before
    h.play.stop()


def test_scheduler_failure_stops_once_and_reports_without_zombie():
    h=Harness();h.play.start(600,4)
    def refused(*args):raise RuntimeError('scheduler closed')
    h.scheduler.call_later=refused
    h.complete()
    assert not h.play.active and not h.play.inflight and h.play.timer is None
    assert len(h.failed)==1 and str(h.failed[0])=='scheduler closed'
    h.cancel[0]()
    assert len(h.failed)==1 and h.invalidations==1

def test_live_frames_stream_through_production_request_and_access_without_cache():
    """Synthetic solved-source fields use the real asynchronous display service."""
    from types import MethodType
    from test_anypost_channels import host,source,drain
    from anyfem.application.workflow import WorkbenchWorkflow
    from anyfem.post.access import LiveResultAccess
    app=host();app.solution=SimpleNamespace(shapes=tuple(source() for _ in range(600)))
    frame=[0];published=[];requested=[];pacer=Scheduler()
    app.current_shape=lambda:app.solution.shapes[frame[0]]
    app.panels={'Results':SimpleNamespace(ensure_compatible_field=lambda:None,field_name=lambda:'ux')}
    app._live_result_access=LiveResultAccess()
    app._result_scale_text=MethodType(WorkbenchWorkflow._result_scale_text,app)
    app._publish_results_field=lambda field:published.append(field) or True
    def request(**callbacks):
        requested.append(frame[0]);WorkbenchWorkflow.show_results(app,**callbacks)
    playback=ResultPlayback(scheduler=pacer,request=request,set_frame=lambda value:frame.__setitem__(0,value),
        context=lambda:(id(app.solution),frame[0],getattr(app,'_result_revision',0)),
        invalidate=app.invalidate_result_requests,stop_canvas=lambda:None)
    try:
        playback.start(600,20)
        for completed in range(1,5):
            drain(app,lambda:playback.completed_frames==completed)
            assert len(published)==completed and len(pacer.calls)==1 and not playback.inflight
            assert 1<=pacer.calls[0].delay<=50
            if completed<4:pacer.tick()
        playback.stop();assert not pacer.calls and requested==[0,1,2,3]
        assert published[0].node_values=={1:5.,2:7.}
        assert app.solution.shapes[0].fields['ux'].node_values=={1:5.,2:7.}
    finally:
        playback.stop();app.close_result_access()


def test_pacing_subtracts_frame_work_and_yields_without_catchup_queue():
    h=Harness();h.play.start(600,4)
    h.clock_value=.1;h.complete()
    assert h.scheduler.calls[0].delay==150
    h.scheduler.tick();h.clock_value=.8;h.complete()
    assert len(h.scheduler.calls)==1 and h.scheduler.calls[0].delay==1
    assert h.requests==[0,1]
    h.play.stop()


def test_streamed_frame_notification_does_not_refresh_document_but_edits_do():
    from anyfem.ui.qt.app import QtFemWindow
    refreshed=[]
    host=SimpleNamespace(panels={'Results':SimpleNamespace(playback_active=True)},
                         _queue_refresh=lambda:refreshed.append(True))
    QtFemWindow._on_workbench_event(host,SimpleNamespace(kind='state_changed',field='shape_index'))
    assert not refreshed
    QtFemWindow._on_workbench_event(host,SimpleNamespace(kind='state_changed',field='project'))
    assert refreshed==[True]
    host.panels['Results'].playback_active=False
    QtFemWindow._on_workbench_event(host,SimpleNamespace(kind='state_changed',field='shape_index'))
    assert refreshed==[True,True]


def test_selected_inclusive_interval_wraps_without_outside_frames():
    h=Harness();h.play.start(600,20,index=4,first=4,last=8)
    for _ in range(11):h.complete();h.scheduler.tick()
    assert h.requests==[4,5,6,7,8,4,5,6,7,8,4,5]
    h.play.stop()


def test_invalid_interval_refuses_before_mutating_an_existing_run():
    import pytest
    h=Harness();h.play.start(600,20)
    old=(h.play.generation,h.play.active,h.invalidations,list(h.requests))
    for first,last in ((-1,4),(8,4),(0,600)):
        with pytest.raises(ValueError):h.play.start(600,20,first=first,last=last)
        assert (h.play.generation,h.play.active,h.invalidations,list(h.requests))==old
    h.play.stop()


def test_gif_uses_selected_source_indices_and_waits_for_publication():
    from anyfem.ui.qt.tasks import ResultsTask
    frames=[];callbacks=[];session=object()
    app=SimpleNamespace(_closing=False,session=session,active_job_id='job',
        viewport=SimpleNamespace(canvas=SimpleNamespace(capture_image=lambda:'actual')),
        scheduler=SimpleNamespace(call_later=lambda *args:None))
    state=dict(session=session,job='job',count=2,index=0,frames=(4,8),images=[])
    panel=SimpleNamespace(_gif_capture=state,app=app,_set_frame_index=frames.append,
        show_results=lambda **kw:callbacks.append(kw['on_ready']),_capture_gif_frame=lambda:None)
    ResultsTask._capture_gif_frame(panel);assert frames==[4] and state['images']==[]
    callbacks.pop()();ResultsTask._capture_gif_frame(panel)
    assert frames==[4,8] and state['images']==['actual']


def test_preprocessing_context_change_before_launch_cancels_without_a_child():
    from anyfem.ui.qt.preprocessing import FramePreparation
    context=[0];statuses=[]
    job=FramePreparation(Scheduler(),lambda:context[0],statuses.append,lambda cache:None,lambda error:None)
    token,directory=job.begin();context[0]=1
    job.launch(token,'must-not-be-opened',None,(0,1))
    assert not job.active and job.process is None and job.directory is None
    assert statuses[-1]=='Preprocessing stopped'


def test_prepared_playback_refuses_changed_context_before_reading():
    from anyfem.ui.qt.tasks import ResultsTask
    cancelled=[]
    panel=SimpleNamespace(_cached_playback=SimpleNamespace(context=('old',)),frame=SimpleNamespace(value=lambda:4),
        _preprocess_context=lambda:('new',))
    ResultsTask._stream_frame(panel,on_ready=lambda:None,on_cancel=lambda:cancelled.append(True))
    assert cancelled==[True]


def test_preprocessing_reports_snapshot_counts_and_completion(monkeypatch):
    import json
    import anyfem.ui.qt.preprocessing as module
    scheduler=Scheduler();counts=[];messages=[];ready=[]
    child=SimpleNamespace(poll=lambda:None,wait=lambda:None,terminate=lambda:None)
    monkeypatch.setattr(module.subprocess,'Popen',lambda *args,**kwargs:child)
    monkeypatch.setattr(module,'PreparedFrames',lambda *args:SimpleNamespace())
    job=module.FramePreparation(scheduler,lambda:'same',messages.append,ready.append,
        lambda error:(_ for _ in ()).throw(error),progress=lambda *args:counts.append(args))
    token,directory=job.begin()
    assert counts==[(0,0)] and messages[-1]=='Preparing snapshot'
    job.launch(token,'unused-request',None,(4,5,6,7,8))
    (directory/'progress.json').write_text(json.dumps({'completed':2}))
    scheduler.tick();assert counts[-1]==(2,5)
    child.poll=lambda:0
    (directory/'progress.json').write_text(json.dumps({'completed':5,'ready':True}))
    scheduler.tick()
    assert counts[-1]==(5,5) and messages[-1]=='Frames prepared' and len(ready)==1
    assert not job.active and not scheduler.calls
    module.remove_directory(directory)
