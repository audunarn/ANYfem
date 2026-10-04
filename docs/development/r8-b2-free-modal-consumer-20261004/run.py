from pathlib import Path
import json,os,subprocess,sys,time
import psutil
root=Path(__file__).resolve().parent;mode=sys.argv[1]
repo=Path('C:/Github/ANYfem/.worktrees/experimental-ge-panel-consumer')
solver=Path('C:/Users/AudunArnesenNyhus/AppData/Local/Temp/r8-bl5-projected-joint32-v2/site') if mode=='historical' else Path('C:/Users/AudunArnesenNyhus/AppData/Local/Temp/r8-b2-free-reference-modes-v3/site')
consumer=repo/'src' if mode.startswith('source') else root/'site'
env=os.environ.copy();env.update(PYTHONPATH=str(consumer)+';'+str(solver),OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMBA_NUM_THREADS='1',NUMBA_DISABLE_JIT='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',PYTEST_ADDOPTS='',ANYFEM_TEST_THREADS='1')
started=time.monotonic();peak=0;failure=None
command=[sys.executable,str(root/('examples.py' if mode=='examples' else 'compatibility.py' if mode=='compatibility' else 'check-consumer.py'))]
if mode not in ('examples','compatibility'):command.append(mode)
with (root/(mode+'.log')).open('x') as out:
    child=subprocess.Popen(command,cwd=root,env=env,stdout=out,stderr=subprocess.STDOUT);proc=psutil.Process(child.pid)
    while child.poll() is None:
        try:peak=max(peak,sum(p.memory_info().rss for p in [proc]+proc.children(recursive=True) if p.is_running()))
        except psutil.NoSuchProcess:pass
        if peak>2*1024**3 or time.monotonic()-started>180:
            failure='memory' if peak>2*1024**3 else 'timeout';subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False);child.wait();break
        time.sleep(.1)
record=dict(command=command,status='failed_limit' if failure else 'terminal',failure=failure,exit_code=child.wait(),peak_sampled_bytes=peak,elapsed_seconds=time.monotonic()-started,limit_bytes=2*1024**3,timeout_seconds=180)
(root/(mode+'-resource.json')).write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record));print((root/(mode+'.log')).read_text()[-7000:]);raise SystemExit(record['exit_code'] or bool(failure))


