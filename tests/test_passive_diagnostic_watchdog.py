"""Scalar diagnostic lifecycle tests; no model or scientific qualification."""
import importlib
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
import weakref
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]

def observer_module():
    return importlib.import_module('tools._python_stack_watchdog')

def wait_for(predicate):
    end = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < end, 'bounded observer event missing'
        time.sleep(.005)

def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]

def wait_for_snapshot(watcher,path,nodeid,phase,count):
    wait_for(lambda: watcher.snapshots > count and any(
        record.get('nodeid')==nodeid and record.get('phase')==phase for record in rows(path)[count:]))

def test_diagnostic_command_requests_passive_snapshots(monkeypatch, tmp_path):
    from test_test_runner import runner
    seen=[]
    def execute(command,log,environment):
        seen.append(command); log.write_text('failed'); return 1
    monkeypatch.setattr(runner,'execute',execute)
    assert runner.main(['--ci-diagnostics','--out',str(tmp_path/'out')]) == 1
    assert 'anyfem_snapshot_timeout=60' in seen[0]
    assert not any('faulthandler_timeout=' in value for value in seen[0])

def test_passive_snapshot_has_actual_metadata_and_no_locals(tmp_path):
    module=observer_module()
    secret='LOCAL_VALUE_MUST_NOT_APPEAR'
    watcher=module.PassiveWatchdog(tmp_path, interval=.01)
    try:
        watcher.arm('scalar::first','call')
        wait_for(lambda: watcher.snapshots >= 1)
        watcher.disarm()
        data=rows(tmp_path/'python-stack-snapshots.jsonl')
        assert data[0]['nodeid']=='scalar::first' and data[0]['phase']=='call'
        assert any(frame['function']=='test_passive_snapshot_has_actual_metadata_and_no_locals'
                   for thread in data[0]['threads'] for frame in thread['frames'])
        assert secret not in (tmp_path/'python-stack-snapshots.jsonl').read_text()
        assert all(set(frame)=={'filename','function','line'} for thread in data[0]['threads'] for frame in thread['frames'])
        count=watcher.snapshots
        time.sleep(.03)
        assert watcher.snapshots==count
        watcher.arm('scalar::second','setup')
        watcher.phase('scalar::second','teardown')
        wait_for_snapshot(watcher,tmp_path/'python-stack-snapshots.jsonl','scalar::second','teardown',count)
        watcher.disarm()
        assert rows(tmp_path/'python-stack-snapshots.jsonl')[-1]['phase']=='teardown'
    finally:
        watcher.close()
    status=json.loads((tmp_path/'python-stack-watchdog-status.json').read_text())
    assert status['status']=='closed' and status['retirement_verified'] is True

def test_passive_snapshot_is_bounded_and_explicitly_truncated(tmp_path):
    watcher=observer_module().PassiveWatchdog(tmp_path,interval=.01,max_frames=1,max_threads=1,max_bytes=2048)
    try:
        watcher.arm('scalar::bounded','call')
        wait_for(lambda: watcher._exhausted)
    finally:
        watcher.close()
    assert (tmp_path/'python-stack-snapshots.jsonl').stat().st_size <= 2048
    assert any(row['truncated'] for row in rows(tmp_path/'python-stack-snapshots.jsonl'))

def test_observer_error_is_visible_infrastructure_failure(monkeypatch,tmp_path):
    module=observer_module()
    def fail(*args): raise RuntimeError('snapshot sentinel')
    monkeypatch.setattr(module.PassiveWatchdog,'_capture',fail)
    watcher=module.PassiveWatchdog(tmp_path,interval=.01)
    watcher.arm('scalar::error','call')
    wait_for(lambda: watcher.failed)
    with pytest.raises(RuntimeError,match='snapshot sentinel'):
        watcher.close()
    status=json.loads((tmp_path/'python-stack-watchdog-status.json').read_text())
    assert status['status']=='failed' and status['retirement_verified'] is True

def test_long_nodeid_truncation_is_explicit(tmp_path):
    watcher=observer_module().PassiveWatchdog(tmp_path,interval=.01)
    try:
        watcher.arm('x'*2000,'call')
        wait_for(lambda: watcher.snapshots >= 1)
    finally:
        watcher.close()
    record=rows(tmp_path/'python-stack-snapshots.jsonl')[0]
    assert record['nodeid']=='x'*1024
    assert record['nodeid_truncated'] is True and record['truncated'] is True

def test_uncertain_thread_retirement_is_not_reported_success(tmp_path):
    watcher=observer_module().PassiveWatchdog(tmp_path)
    watcher._thread=SimpleNamespace(join=lambda timeout:None,is_alive=lambda:True)
    with pytest.raises(RuntimeError,match='retire'):
        watcher.close()
    status=json.loads((tmp_path/'python-stack-watchdog-status.json').read_text())
    assert status['retirement_verified'] is False and status['status']=='failed'

def test_phase_transitions_preserve_node_deadline(tmp_path):
    watcher=observer_module().PassiveWatchdog(tmp_path,interval=60)
    try:
        watcher.arm('scalar::combined','setup')
        deadline=watcher._due
        watcher.phase('scalar::combined','call')
        assert watcher._due == deadline
        watcher.phase('scalar::combined','teardown')
        assert watcher._due == deadline
    finally:
        watcher.close()

def test_running_status_does_not_claim_idle_retirement(tmp_path):
    watcher=observer_module().PassiveWatchdog(tmp_path,interval=60)
    try:
        watcher.arm('scalar::waiting','setup')
        status=json.loads((tmp_path/'python-stack-watchdog-status.json').read_text())
        assert status['status']=='running'
        assert status['thread_started'] is True
        assert status['retirement_verified'] is False
    finally:
        watcher.close()

def test_main_thread_is_prioritized_before_thread_truncation(monkeypatch,tmp_path):
    module=observer_module()
    main=threading.main_thread().ident
    frame=sys._getframe()
    monkeypatch.setattr(module.sys,'_current_frames',lambda:{main-1:frame,main:frame})
    watcher=module.PassiveWatchdog(tmp_path,max_threads=1)
    try:
        captured,truncated=watcher._capture()
        assert [row['thread_id'] for row in captured]==[main]
        assert truncated is True
    finally:
        watcher.close()

def test_frame_references_are_released_before_publication(monkeypatch,tmp_path):
    module=observer_module()
    entered,release=threading.Event(),threading.Event()
    original=module.PassiveWatchdog._publish
    def blocked(self,data):
        entered.set()
        assert release.wait(3)
        original(self,data)
    monkeypatch.setattr(module.PassiveWatchdog,'_publish',blocked)
    watcher=module.PassiveWatchdog(tmp_path,interval=.01)
    class Payload: pass
    def scope():
        value=Payload()
        reference=weakref.ref(value)
        watcher.arm('scalar::references','call')
        assert entered.wait(2)
        # Disarm must not wait for file publication.
        watcher.disarm()
        return reference
    try:
        reference=scope()
        gc.collect()
        assert reference() is None
    finally:
        release.set()
        watcher.close()

def test_thread_startup_failure_is_recorded(monkeypatch,tmp_path):
    module=observer_module()
    def fail(*args,**kwargs): raise RuntimeError('thread startup sentinel')
    monkeypatch.setattr(module.threading,'Thread',fail)
    watcher=module.PassiveWatchdog(tmp_path)
    with pytest.raises(RuntimeError,match='thread startup sentinel'):
        watcher.arm('scalar::startup','setup')
    with pytest.raises(RuntimeError,match='thread startup sentinel'):
        watcher.close()
    assert json.loads((tmp_path/'python-stack-watchdog-status.json').read_text())['status']=='failed'

@pytest.mark.parametrize('launched',(False,True),ids=('before-launch','after-real-start'))
def test_start_interruption_keeps_owned_thread_and_original_exception(monkeypatch,tmp_path,launched):
    module=observer_module()
    original=module.threading.Thread.start
    seen=[]
    interruption=KeyboardInterrupt('owned startup interruption')
    def interrupt(thread):
        seen.append(thread)
        if launched: original(thread)
        raise interruption
    monkeypatch.setattr(module.threading.Thread,'start',interrupt)
    watcher=module.PassiveWatchdog(tmp_path)
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            watcher.arm('scalar::interrupted','setup')
        assert caught.value is interruption
        assert watcher._thread is seen[0]
    finally:
        with pytest.raises(RuntimeError):
            watcher.close()
        # Retire the exact real thread even against the intentionally broken
        # baseline, which discarded its handle. No unrelated thread queries.
        if launched:
            seen[0].join(timeout=2)
            assert not seen[0].is_alive()
    status=json.loads((tmp_path/'python-stack-watchdog-status.json').read_text())
    assert status['status']=='failed'
    assert status['retirement_verified'] is launched
    if not launched: assert status['thread_started'] is None

def test_completed_snapshot_does_not_retain_scalar_references(tmp_path):
    payload=object()
    before=sys.getrefcount(payload)
    watcher=observer_module().PassiveWatchdog(tmp_path,interval=.01)
    try:
        watcher.arm('scalar::refcount','call')
        wait_for(lambda: watcher.snapshots >= 1)
        watcher.disarm()
    finally:
        watcher.close()
    assert sys.getrefcount(payload)==before

def child(tmp_path,body,*extra):
    script=tmp_path/'test_scalar.py';script.write_text(body)
    config=tmp_path/'pytest.ini';config.write_text('[pytest]\n')
    output=tmp_path/'out';output.mkdir()
    env=os.environ.copy();env.pop('PYTEST_ADDOPTS',None)
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1';env['ANYFEM_CI_DIAGNOSTICS_DIR']=str(output)
    result=subprocess.run([sys.executable,'-m','pytest','-q','-c',str(config),'--confcutdir',str(tmp_path),
        '-p','tools.pytest_anyfem_progress','-o','anyfem_snapshot_timeout=.02',str(script),*extra],
        cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
    return result,output

def test_real_plugin_snapshots_and_retires_without_disabling_fatal_handler(tmp_path):
    result,out=child(tmp_path,'import faulthandler,time\ndef test_scalar():\n assert faulthandler.is_enabled()\n time.sleep(.1)\n')
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Timeout (' not in result.stdout+result.stderr
    assert any(row['nodeid'].endswith('::test_scalar') and row['phase']=='call'
               for row in rows(out/'python-stack-snapshots.jsonl'))
    assert json.loads((out/'python-stack-watchdog-status.json').read_text())['retirement_verified'] is True

def test_real_plugin_observer_failure_cannot_be_a_scientific_pass(tmp_path):
    result,out=child(tmp_path,
        'import time\ndef test_scalar(request):\n'
        ' watcher=request.config.pluginmanager.get_plugin("anyfem-ci-progress").watchdog\n'
        ' def fail():raise RuntimeError("observer infrastructure sentinel")\n'
        ' watcher._capture=fail\n time.sleep(.08)\n')
    assert result.returncode != 0
    assert 'observer infrastructure sentinel' in result.stdout+result.stderr
    status=json.loads((out/'python-stack-watchdog-status.json').read_text())
    assert status['status']=='failed' and status['retirement_verified'] is True

def test_scientific_failure_survives_simultaneous_observer_failure(tmp_path):
    result,out=child(tmp_path,
        'import time\ndef test_scalar(request):\n'
        ' watcher=request.config.pluginmanager.get_plugin("anyfem-ci-progress").watchdog\n'
        ' def fail():raise RuntimeError("observer simultaneous sentinel")\n'
        ' watcher._capture=fail\n time.sleep(.08)\n'
        ' assert False,"original scientific sentinel"\n')
    assert result.returncode != 0
    failures=[row for row in rows(out/'pytest-progress.jsonl') if row['event']=='failure']
    assert any('original scientific sentinel' in row['longrepr'] for row in failures)
    assert 'observer simultaneous sentinel' in result.stdout+result.stderr

@pytest.mark.parametrize('alive',(False,True),ids=('retired','still-live'))
def test_close_publication_failure_preserves_all_attribution(monkeypatch,tmp_path,alive):
    watcher=observer_module().PassiveWatchdog(tmp_path)
    primary='RuntimeError: original observer sentinel'
    watcher._error=primary
    watcher._thread=SimpleNamespace(ident=123,join=lambda timeout:None,is_alive=lambda:alive)
    publication=OSError('status publication sentinel')
    def fail(*args): raise publication
    monkeypatch.setattr(watcher,'_status',fail)
    with pytest.raises(RuntimeError) as caught:
        watcher.close()
    assert watcher._error == primary
    assert watcher._retirement_verified is not alive
    assert watcher._status_error == 'OSError: status publication sentinel'
    assert (watcher._retirement_error is not None) is alive
    assert 'original observer sentinel' in str(caught.value)
    assert 'status publication sentinel' in str(caught.value)
    assert ('retirement_verified='+str(not alive)) in str(caught.value)
    assert caught.value.__cause__ is publication

def test_close_retirement_failure_is_independent_of_primary_error(tmp_path):
    watcher=observer_module().PassiveWatchdog(tmp_path)
    watcher._error='RuntimeError: earlier observer sentinel'
    watcher._thread=SimpleNamespace(ident=123,join=lambda timeout:None,is_alive=lambda:True)
    with pytest.raises(RuntimeError,match='earlier observer sentinel'):
        watcher.close()
    status=json.loads((tmp_path/'python-stack-watchdog-status.json').read_text())
    assert status['retirement_verified'] is False
    assert status['retirement_error'] is not None
    assert status['error']=='RuntimeError: earlier observer sentinel'

@pytest.mark.parametrize('interruption',(KeyboardInterrupt('publication interrupted'),SystemExit('publication exited')))
def test_close_publication_interrupt_identity_is_preserved(monkeypatch,tmp_path,interruption):
    watcher=observer_module().PassiveWatchdog(tmp_path)
    def fail(*args): raise interruption
    monkeypatch.setattr(watcher,'_status',fail)
    with pytest.raises(type(interruption)) as caught:
        watcher.close()
    assert caught.value is interruption
    assert watcher._retirement_verified is True
    assert type(interruption).__name__ in watcher._status_error

def test_close_status_failure_without_prior_error_cannot_pass(monkeypatch,tmp_path):
    watcher=observer_module().PassiveWatchdog(tmp_path)
    def fail(*args): raise OSError('only publication failed')
    monkeypatch.setattr(watcher,'_status',fail)
    with pytest.raises(RuntimeError,match='only publication failed'):
        watcher.close()
    assert watcher.failed and watcher._retirement_verified is True

def test_error_prefix_truncation_is_explicit(monkeypatch,tmp_path):
    module=observer_module()
    def fail(*args): raise RuntimeError('x'*700)
    monkeypatch.setattr(module.PassiveWatchdog,'_capture',fail)
    watcher=module.PassiveWatchdog(tmp_path,interval=.01)
    watcher.arm('scalar::long-error','call')
    wait_for(lambda: watcher.failed)
    with pytest.raises(RuntimeError): watcher.close()
    status=json.loads((tmp_path/'python-stack-watchdog-status.json').read_text())
    assert status['error']=='RuntimeError: '+'x'*512
    assert status['error_truncated'] is True
    assert status['status_error_truncated'] is False
    assert status['retirement_error_truncated'] is False

@pytest.mark.parametrize('mode',('collect','error','failed'))
def test_collection_error_and_failure_shutdown(tmp_path,mode):
    body='def test_scalar():assert False,"scientific sentinel"\n' if mode=='failed' else (
        'raise RuntimeError("collection sentinel")\n' if mode=='error' else 'def test_scalar():raise AssertionError("must not run")\n')
    result,out=child(tmp_path,body,*(['--collect-only'] if mode=='collect' else []))
    assert result.returncode=={'collect':0,'error':2,'failed':1}[mode],result.stdout+result.stderr
    status=json.loads((out/'python-stack-watchdog-status.json').read_text())
    assert status['status']=='closed' and status['retirement_verified'] is True
    if mode!='failed': assert status['thread_started'] is False


def test_snapshot_wait_rejects_premature_setup_even_when_count_advanced(tmp_path,monkeypatch):
    """A count increment alone does not prove the requested phase was sampled."""
    path=tmp_path/'snapshots.jsonl'
    path.write_text(json.dumps(dict(nodeid='scalar::second',phase='setup'))+'\n')
    watcher=SimpleNamespace(snapshots=1)
    transitions=[]
    def publish_matching_phase(predicate):
        assert not predicate(), 'premature setup must not satisfy the teardown wait'
        transitions.append('teardown')
        with path.open('a') as stream:
            stream.write(json.dumps(dict(nodeid='scalar::second',phase='teardown'))+'\n')
        watcher.snapshots+=1
        assert predicate(), 'the actual matching phase must settle the wait'
    monkeypatch.setitem(globals(),'wait_for',publish_matching_phase)
    wait_for_snapshot(watcher,path,'scalar::second','teardown',0)
    assert transitions==['teardown']


def test_snapshot_wait_rejects_stale_phase_before_baseline_count(tmp_path,monkeypatch):
    path=tmp_path/'snapshots.jsonl'
    history=[dict(nodeid='scalar::second',phase='teardown'),dict(nodeid='scalar::second',phase='setup')]
    path.write_text(''.join(json.dumps(row)+'\n' for row in history))
    watcher=SimpleNamespace(snapshots=2)
    transitions=[]
    def publish_new_matching_phase(predicate):
        assert not predicate(), 'old teardown plus new setup must not settle a new teardown wait'
        transitions.append('new teardown')
        with path.open('a') as stream:
            stream.write(json.dumps(dict(nodeid='scalar::second',phase='teardown'))+'\n')
        watcher.snapshots+=1
        assert predicate()
    monkeypatch.setitem(globals(),'wait_for',publish_new_matching_phase)
    wait_for_snapshot(watcher,path,'scalar::second','teardown',1)
    assert transitions==['new teardown']
