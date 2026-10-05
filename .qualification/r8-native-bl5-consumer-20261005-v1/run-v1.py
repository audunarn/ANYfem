from pathlib import Path
import hashlib, importlib.util, json, os, sys
ROOT=Path(__file__).resolve().parent; REPO=ROOT.parents[1]
SOLVER=Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001')
EVIDENCE=SOLVER/'.qualification/b3-ge-e4-pl/r8-native-fibre-bl5-dev-20261005-v1'
CI=EVIDENCE.parent/'r8-native-s3-restart-binding-dev-20261005-v1/ci-site-network-v2'
BASE=Path(os.environ['TEMP'])/'r8-bl5-affine-flexible-v1/run.py'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert sha(BASE)=='ad9947c9268774a980041112004364d4daba6ce884fe339fab9f0d90b265d3ec'
spec=importlib.util.spec_from_file_location('bounded',BASE); bounded=importlib.util.module_from_spec(spec);spec.loader.exec_module(bounded)
bounded.ROOT=ROOT; bounded.ENV.pop('OPENBLAS_CORETYPE',None); bounded.ENV.pop('PYTHONOPTIMIZE',None)
bounded.ENV.update(NUMEXPR_NUM_THREADS='1',NUMBER_OF_CPUS='1',PIP_NO_INDEX='1')
mode=sys.argv[1]; version=sys.argv[2]
fem=ROOT/'site-v1' if mode=='installed' else REPO/'src'
bounded.ENV['PYTHONPATH']=os.pathsep.join((str(fem),str(EVIDENCE/'installed-s3-v1'),str(CI)))
if mode=='build':
    bounded.supervise([sys.executable,'-m','pip','wheel',str(REPO),'--no-deps','--no-build-isolation','--no-cache-dir','--no-index','-w',str(ROOT/'wheel-v1')],'build-'+version,2*1024**3)
elif mode=='install':
    bounded.supervise([sys.executable,'-m','pip','install','--no-deps','--no-cache-dir','--no-index','--target',str(ROOT/'site-v1'),str(next((ROOT/'wheel-v1').glob('*.whl')))],'install-'+version,2*1024**3)
else:
    bounded.supervise([sys.executable,str(ROOT/'entry-v1.py'),mode,version],mode+'-'+version,2*1024**3)
