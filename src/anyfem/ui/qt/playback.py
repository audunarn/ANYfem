"""Demand-driven result playback; result ownership/publication stays in workflow."""
from __future__ import annotations
import math
import time


class ResultPlayback:
    """One request and one owned pacing timer, never a queue of frame snapshots."""
    def __init__(self, *, scheduler, request, set_frame, context, invalidate, stop_canvas,
                 changed=lambda:None, failed=lambda error:None, clock=time.monotonic):
        self.scheduler=scheduler;self.request=request;self.set_frame=set_frame;self.context=context
        self.invalidate=invalidate;self.stop_canvas=stop_canvas;self.changed=changed;self.failed=failed;self.clock=clock
        self.active=False;self.inflight=False;self.timer=None;self.generation=0
        self.index=0;self.count=0;self.interval_ms=250;self.expected=None
        self.completed_frames=0

    def start(self,count,fps,index=0,*,first=0,last=None):
        count=int(count);fps=float(fps)
        if count<=0:raise ValueError('Select a result before playback')
        first=int(first);last=count-1 if last is None else int(last)
        if not 0<=first<=last<count:raise ValueError('Choose From frame <= To frame within the result')
        if not math.isfinite(fps) or fps<=0:raise ValueError('playback speed must be positive and finite')
        interval=1000/fps
        if not math.isfinite(interval) or round(interval)>2147483647:
            raise ValueError('playback interval exceeds the Qt timer limit')
        interval=max(1,round(interval))
        self.stop()
        self.count=int(count);self.first=first;self.last=last
        self.index=int(index) if first<=int(index)<=last else first
        self.interval_ms=interval
        self.active=True;self.completed_frames=0;self.expected=self.context()
        self.changed();self._request_next(self.generation)

    def stop(self):
        owned=self.active or self.inflight or self.timer is not None
        self.generation+=1;self.active=False;self.inflight=False;self.expected=None
        timer,self.timer=self.timer,None
        if timer is not None:self.scheduler.cancel_call(timer)
        # Cancel only the display generation; ordered exports are independent.
        if owned:self.invalidate()
        self.stop_canvas();self.changed()

    def _current(self,token):
        if not self.active or token!=self.generation:return False
        try:valid=self.context()==self.expected
        except (AttributeError,RuntimeError):valid=False
        if not valid:self.stop()
        return valid

    def _request_next(self,token):
        self.timer=None
        if not self._current(token):return
        started=self.clock()
        self.set_frame(self.index)
        self.inflight=True;self.expected=None;settled=False;waiting=True;completed=False
        def cancelled():
            nonlocal settled
            if settled:return
            settled=True
            if self.active and token==self.generation:self.stop()
        def ready():
            nonlocal settled,completed
            if settled:return
            if waiting:completed=True;return
            settled=True
            if not self._current(token):return
            self.inflight=False;self.completed_frames+=1
            self.index=self.first if self.index>=self.last else self.index+1
            self.changed()
            # Start-to-start pacing, with a UI yield even when this frame is late.
            # The next deadline belongs to its own start: no catch-up queue/skips.
            delay=max(1,math.ceil(self.interval_ms-1000*(self.clock()-started)))
            try:self.timer=self.scheduler.call_later(delay,lambda:self._request_next(token))
            except Exception as error:
                self.stop();self.failed(error)
        try:
            self.request(on_ready=ready,on_cancel=cancelled)
            waiting=False
            if self.active and token==self.generation:
                self.expected=self.context()
                if completed:ready()
        except Exception as error:
            waiting=False
            if self.active and token==self.generation:self.stop()
            self.failed(error)
