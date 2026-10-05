from pathlib import Path
import hashlib,json,sys,os,zipfile
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[1]
SOLVER=Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001')
EVIDENCE=SOLVER/'.qualification/b3-ge-e4-pl/r8-native-fibre-bl5-dev-20261005-v1'
CI=EVIDENCE.parent/'r8-native-s3-restart-binding-dev-20261005-v1/ci-site-network-v2'
mode,version=sys.argv[1:];sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
import anyfem,anysolver,numpy,scipy.linalg,numba,llvmlite,psutil
from threadpoolctl import threadpool_info
fem=ROOT/'site-v1' if mode=='installed' else REPO/'src'
solver=EVIDENCE/'installed-s3-v1'
record=dict(imports={m.__name__:m.__file__ for m in (anyfem,anysolver,numpy,scipy,numba,llvmlite)},
 versions={m.__name__:getattr(m,'__version__',None) for m in (anyfem,anysolver,numpy,scipy,numba,llvmlite)},
 pools=threadpool_info(),affinity=psutil.Process().cpu_affinity(),jit_off=bool(numba.config.DISABLE_JIT),
 environment={k:os.environ.get(k) for k in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','NUMBA_DISABLE_JIT','OPENBLAS_CORETYPE')})
(ROOT/(mode+'-runtime-'+version+'.json')).write_text(json.dumps(record,indent=2)+'\n')
assert Path(anyfem.__file__).resolve().is_relative_to(fem.resolve())
assert Path(anysolver.__file__).resolve().is_relative_to(solver.resolve())
for module,expected in ((numpy,'2.5.3'),(scipy,'1.18.1'),(numba,'0.68.0'),(llvmlite,'0.50.0')):
 assert module.__version__==expected and Path(module.__file__).resolve().is_relative_to(CI.resolve()),record
assert len(record['affinity'])==1 and record['jit_off'] and all(p['num_threads']==1 for p in record['pools']),record
assert record['environment']['OPENBLAS_CORETYPE'] is None and all(v=='1' for k,v in record['environment'].items() if k!='OPENBLAS_CORETYPE'),record
wheel=EVIDENCE/'wheel-s3-v1/anysolver-0.4.7-py3-none-any.whl'
assert sha(wheel)=='b3d5f572797f3c2954d1bf9dc1c6b4293da14c5812e543dbde7f314a60207291'
with zipfile.ZipFile(wheel) as z:
 solver_modules={name:hashlib.sha256(z.read(name)).hexdigest() for name in z.namelist() if name.startswith('anysolver/') and name.endswith('.py')}
assert solver_modules=={str(p.relative_to(solver)).replace('\\','/'):sha(p) for p in (solver/'anysolver').rglob('*.py')}
fem_modules={str(p.relative_to(REPO/'src')).replace('\\','/'):sha(p) for p in (REPO/'src/anyfem').rglob('*.py')}
if mode=='installed':
 assert fem_modules=={str(p.relative_to(fem)).replace('\\','/'):sha(p) for p in (fem/'anyfem').rglob('*.py')}
 with zipfile.ZipFile(next((ROOT/'wheel-v1').glob('*.whl'))) as z:
  assert fem_modules=={name:hashlib.sha256(z.read(name)).hexdigest() for name in z.namelist() if name.startswith('anyfem/') and name.endswith('.py')}
files=('src/anyfem/solve/stiffened_shell.py','src/anyfem/solve/stiffened_shell_project.py','tests/test_stiffened_shell_native_fibre_bl5_consumer.py','examples/stiffened_shell_native_fibre_bl5_consumer.py','docs/experimental_stiffened_shell_native_fibre_bl5.md')
(ROOT/(mode+'-bindings-'+version+'.json')).write_text(json.dumps(dict(files={p:sha(REPO/p) for p in files},solver_wheel_sha256=sha(wheel),solver_modules=solver_modules,fem_modules=fem_modules,entry_sha256=sha(__file__),runner_sha256=sha(ROOT/'run-v1.py')),indent=2)+'\n')
import pytest
tests=('test_stiffened_shell_native_fibre_bl5_consumer.py','test_bl5_project.py','test_bl5_material_consumer.py','test_bl5_coupled_material_consumer.py','test_bl5_distributed_project.py')
code=pytest.main(['-q','-c',str(REPO/'pyproject.toml'),'-o','pythonpath=', '--basetemp',str(ROOT/(mode+'-temp-'+version)),'--junitxml='+str(ROOT/(mode+'-junit-'+version+'.xml')),*[str(REPO/'tests'/p) for p in tests]])
loaded={name:module.__file__ for name,module in sys.modules.items() if (name=='anyfem' or name.startswith('anyfem.') or name=='anysolver' or name.startswith('anysolver.')) and getattr(module,'__file__',None)}
assert all(Path(p).resolve().is_relative_to((fem if name.startswith('anyfem') else solver).resolve()) for name,p in loaded.items()),loaded
(ROOT/(mode+'-loaded-'+version+'.json')).write_text(json.dumps(loaded,indent=2)+'\n')
assert code==0,'focused consumer checks failed'
print(json.dumps(dict(status='completed',fem_modules=len(fem_modules),solver_modules=len(solver_modules),loaded_modules=len(loaded),solver_wheel_sha256=sha(wheel)),indent=2))
