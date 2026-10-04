"""Owned serial source/build/install/example checks, fixed installed solver."""
from pathlib import Path
import hashlib, importlib.util, os, sys

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[2]
SOLVER=Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001/.qualification/b3-ge-e4-pl/r8-distributing-ge-owner-dev-20261004-v1')
wheel=SOLVER/'dist/anysolver-0.4.7-py3-none-any.whl'
if hashlib.sha256(wheel.read_bytes()).hexdigest()!='f0267b0f5803ce69c1a181bfd7aa22bde06fa1aafdb65cbd56bba4d550745b4e':
    raise ValueError('preceding verified installed solver required')
p=Path(os.environ['TEMP'])/'r8-bl5-affine-flexible-v1/run.py'
if hashlib.sha256(p.read_bytes()).hexdigest()!='ad9947c9268774a980041112004364d4daba6ce884fe339fab9f0d90b265d3ec':
    raise ValueError('pinned bounded supervisor required')
spec=importlib.util.spec_from_file_location('bounded',p)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.ROOT=ROOT;m.ENV=os.environ.copy();m.ENV.pop('PYTHONOPTIMIZE',None)
for key,value in {'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
    'NUMBA_NUM_THREADS':'1','NUMBA_DISABLE_JIT':'1','VECLIB_MAXIMUM_THREADS':'1',
    'BLIS_NUM_THREADS':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}.items():m.ENV[key]=value
action=sys.argv[1] if len(sys.argv)==2 else None
if action in ('source','installed','example'):
    m.ENV['R8_DISTRIBUTING_CONSUMER_KIND']=action
    m.ENV['PYTHONPATH']=os.pathsep.join((str(REPO/'src' if action=='source' else ROOT/'site'),str(SOLVER/'site')))
    m.supervise([sys.executable,'-u',str(ROOT/'probe-v1.py')],action+'-v1',2*1024**3)
elif action=='build':
    m.ENV.pop('PYTHONPATH',None)
    m.supervise([sys.executable,'-m','build','--wheel','--no-isolation','--outdir',str(ROOT/'dist'),str(REPO)],'build-v1',2*1024**3)
elif action=='install':
    m.ENV.pop('PYTHONPATH',None)
    wheels=list((ROOT/'dist').glob('*.whl'))
    if len(wheels)!=1 or (ROOT/'site').exists():raise ValueError('one fresh consumer wheel/site required')
    m.supervise([sys.executable,'-m','pip','install','--no-deps','--no-index','--target',str(ROOT/'site'),str(wheels[0])],'install-v1',2*1024**3)
else:raise ValueError('source/build/install/installed/example action required')
