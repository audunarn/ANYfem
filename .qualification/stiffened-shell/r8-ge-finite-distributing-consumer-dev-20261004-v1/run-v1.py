from pathlib import Path
import hashlib,importlib.util,os,sys
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[2]
SOLVER=Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001')
SOLVERRAW=SOLVER/'.qualification/b3-ge-e4-pl/r8-distributing-finite-owner-dev-20261004-v1'
p=Path(os.environ['TEMP'])/'r8-bl5-affine-flexible-v1/run.py'
assert hashlib.sha256(p.read_bytes()).hexdigest()=='ad9947c9268774a980041112004364d4daba6ce884fe339fab9f0d90b265d3ec'
s=importlib.util.spec_from_file_location('bounded',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
m.ROOT=ROOT;m.ENV=os.environ.copy();m.ENV.pop('PYTHONOPTIMIZE',None)
m.ENV.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMBA_NUM_THREADS='1',NUMBA_DISABLE_JIT='1',BLIS_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
kind=sys.argv[1]
if kind=='build':
 m.ENV.pop('PYTHONPATH',None);command=[sys.executable,'-m','build','--wheel','--no-isolation','--outdir',str(ROOT/'dist'),str(REPO)]
elif kind=='install':
 m.ENV.pop('PYTHONPATH',None);wheel,=(ROOT/'dist').glob('*.whl');assert not (ROOT/'site').exists()
 command=[sys.executable,'-m','pip','install','--no-deps','--no-index','--target',str(ROOT/'site'),str(wheel)]
elif kind in ('source','installed','example'):
 m.ENV['PYTHONPATH']=os.pathsep.join((str(REPO/'src' if kind=='source' else ROOT/'site'),str(SOLVERRAW/'site'),str(REPO)))
 m.ENV['R8_FINITE_CONSUMER_KIND']=kind;command=[sys.executable,'-u',str(ROOT/'probe-v1.py')]
else:raise ValueError('declared action required')
m.supervise(command,kind+'-v1',2*1024**3)
