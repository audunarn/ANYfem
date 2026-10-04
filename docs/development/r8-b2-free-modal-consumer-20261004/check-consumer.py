from pathlib import Path
from collections import Counter
import hashlib,json,sys,zipfile
root=Path(__file__).resolve().parent
repo=Path('C:/Github/ANYfem/.worktrees/experimental-ge-panel-consumer')
mode=sys.argv[1]
solver=Path('C:/Users/AudunArnesenNyhus/AppData/Local/Temp/r8-bl5-projected-joint32-v2/site') if mode=='historical' else Path('C:/Users/AudunArnesenNyhus/AppData/Local/Temp/r8-b2-free-reference-modes-v3/site')
consumer=repo/'src' if mode.startswith('source') else root/'site'
import pytest,anyfem,anysolver
assert Path(anyfem.__file__).is_relative_to(consumer)
assert Path(anysolver.__file__).is_relative_to(solver)
files=['test_experimental_b2_free_reference_modal_consumer.py','test_experimental_b2_mixed_reference_modal_consumer.py','test_stiffened_shell_b2_reference_modal_consumer.py','test_stiffened_shell_project.py']
if mode=='source-final':files=files[:1]
if mode=='historical':files=files[1:2]
sha=lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
binding=dict(solver_site=str(solver),consumer_root=str(consumer),tests={name:sha(repo/'tests'/name) for name in files})
if mode=='installed':
    wheel=next((root/'dist').glob('*.whl'))
    binding['wheel_sha256']=sha(wheel)
    binding['modules']={}
    with zipfile.ZipFile(wheel) as archive:
        for relative in ('anyfem/solve/stiffened_shell_b2.py','anyfem/solve/stiffened_shell_project.py'):
            blobs=((repo/'src'/relative).read_bytes(),(consumer/relative).read_bytes(),archive.read(relative))
            assert blobs[0]==blobs[1]==blobs[2]
            binding['modules'][relative]=hashlib.sha256(blobs[0]).hexdigest()
(root/(mode+'-binding.json')).write_text(json.dumps(binding,indent=2)+'\n')
class Runtime:
    def __init__(self):self.outcomes=Counter()
    def pytest_runtest_logreport(self,report):
        if report.when=='call' or report.outcome=='skipped':self.outcomes[report.outcome]+=1
    def pytest_sessionfinish(self,session,exitstatus):
        from threadpoolctl import threadpool_info
        modules={name:str(Path(module.__file__).resolve()) for name,module in tuple(sys.modules.items()) if name.startswith(('anysolver','anyfem')) and getattr(module,'__file__',None)}
        assert all(Path(path).is_relative_to(solver if name.startswith('anysolver') else consumer) for name,path in modules.items())
        pools=threadpool_info();assert pools and all(p['num_threads']==1 for p in pools)
        import numba
        assert numba.get_num_threads()==1 and numba.config.DISABLE_JIT
        (root/(mode+'-runtime.json')).write_text(json.dumps(dict(imports=modules,pools=pools,numba_threads=numba.get_num_threads(),jit_disabled=bool(numba.config.DISABLE_JIT),collected=session.testscollected,failed=session.testsfailed,exit_status=int(exitstatus),outcomes=dict(self.outcomes)),indent=2)+'\n')
sys.path.insert(0,str(repo/'tests'))
raise SystemExit(pytest.main(['-q','-c',str(root/'pytest.ini'),*(str(repo/'tests'/name) for name in files)],plugins=[Runtime()]))


