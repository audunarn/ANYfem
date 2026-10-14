from concurrent.futures import CancelledError
from threading import Event,RLock,get_ident
from queue import Queue,Empty
from time import monotonic,sleep
from types import SimpleNamespace
import numpy as np
import pytest
import anypost
from any3dview import MeshHandle
from anyfem.post.archive import ArchiveSession


class Reader:
    supports_cancellation=True;working_bytes=1<<20
    def __init__(self):
        self.closed=False;self.calls=[];self.reservations={};self.lock=RLock()
    def metadata_summary(self):return {'model_identity':'m','mesh_identity':'mesh','descriptors':self.components()}
    def components(self,*_):return tuple(dict(quantity_id='s',component=c,location='element') for c in ('x','y'))
    def geometry(self):return dict(positions=np.array([[0.,0,0],[1,0,0],[0,1,0]]),triangles=np.array([[0,1,2]]),triangle_to_element=np.array([0]),element_ids=np.array([7]))
    def associations(self,q,c,selection=None,*,cancel_event=None):
        a=np.array([7,7]);return a if selection is None else a[selection]
    def read_field(self,case,q,c,selection=None,*,cancel_event=None):
        a=np.array([1.,2.]);return a if selection is None else a[selection]
    def set_external_cache_bytes(self,key,size):
        with self.lock:self.reservations[key]=size
    def close(self):self.closed=True


def setup_session(monkeypatch,reader):
    monkeypatch.setattr(anypost,'open_results',lambda *_args,**_kwargs:reader)
    queue=Queue()
    viewer=SimpleNamespace(submit_update=queue.put,add_mesh_arrays=lambda mesh:MeshHandle(mesh))
    return ArchiveSession('unused.h5',viewer),queue


def wait_idle(session,queue):
    deadline=monotonic()+2
    while session.view.io_active or queue.empty():
        assert monotonic()<deadline;sleep(.001)
    while not queue.empty():queue.get_nowait()()


def test_association_cancel_forwarded_no_lock_hold_stale_generation_or_reader_close(monkeypatch):
    reader=Reader();session,queue=setup_session(monkeypatch,reader)
    entered,cancelled=Event(),Event();calls=[]
    original=reader.associations
    def associations(q,c,selection=None,*,cancel_event=None):
        calls.append((c,get_ident(),cancel_event))
        if selection is None and c=='x':
            with reader.lock:
                entered.set();assert cancel_event.wait(2);cancelled.set();raise CancelledError()
        return original(q,c,selection,cancel_event=cancel_event)
    reader.associations=associations
    try:
        session.request_native_sample('a','s','x',2)
        assert entered.wait(1)
        assert session.native_sample_diagnostics['sorts']==0
        with pytest.raises(RuntimeError,match='loading'):session.sample_count('s','y')
        session.request_native_sample('b','s','y',1)
        assert cancelled.wait(1);wait_idle(session,queue)
        assert session._sample_map_key==('s','y') and session._sample_index==1
        assert session.native_sample_diagnostics['sorts']==1
        assert all(thread!=get_ident() and token is not None for c,thread,token in calls)
    finally:session.close()
    assert reader.closed and sum(reader.reservations.values())==0


def test_uncached_sync_selector_refuses_during_field_io_cached_count_nonblocking(monkeypatch):
    reader=Reader();session,queue=setup_session(monkeypatch,reader)
    entered,release=Event(),Event();original=reader.read_field
    def read(*args,**kwargs):
        with reader.lock:entered.set();assert release.wait(2);return original(*args,**kwargs)
    reader.read_field=read
    try:
        session.request_native_sample('a','s','x',1);assert entered.wait(1)
        assert session.sample_count('s','x')==2
        assert session.native_sample_diagnostics['maximum']==2
        with pytest.raises(RuntimeError,match='loading'):session.sample_selection('s','x',2)
        with pytest.raises(RuntimeError,match='loading'):session.sample_count('s','y')
        release.set();wait_idle(session,queue)
        np.testing.assert_array_equal(session.sample_selection('s','x',2),[1])
    finally:release.set();session.close()


def test_close_sets_cancel_without_sample_lock_or_accounting_deadlock(monkeypatch):
    reader=Reader();session,queue=setup_session(monkeypatch,reader);entered=Event();cancelled=Event()
    def associations(*args,cancel_event=None,**kwargs):
        with reader.lock:
            entered.set();assert cancel_event.wait(2);cancelled.set();raise CancelledError()
    reader.associations=associations
    session.request_native_sample('a','s','x',1);assert entered.wait(1)
    session.close();assert cancelled.is_set() and reader.closed
    while not queue.empty():queue.get_nowait()()
    assert session.view.handle.removed and session._sample_associations is None
