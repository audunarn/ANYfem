from pathlib import Path
import hashlib,importlib.util,os,sys
ROOT=Path(__file__).resolve().parent
SOLVER=Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001')
EVIDENCE=SOLVER/'.qualification/b3-ge-e4-pl/r8-native-fibre-bl5-dev-20261005-v1'
CI=EVIDENCE.parent/'r8-native-s3-restart-binding-dev-20261005-v1/ci-site-network-v2'
BASE=ROOT/'inherited-supervisor-v1.py'
assert hashlib.sha256(BASE.read_bytes()).hexdigest()=='ad9947c9268774a980041112004364d4daba6ce884fe339fab9f0d90b265d3ec'
spec=importlib.util.spec_from_file_location('bounded',BASE);s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
s.ROOT=ROOT;s.ENV.pop('OPENBLAS_CORETYPE',None);s.ENV.pop('PYTHONOPTIMIZE',None)
s.ENV.update(NUMEXPR_NUM_THREADS='1',NUMBER_OF_CPUS='1')
s.ENV['PYTHONPATH']=os.pathsep.join((str(ROOT/'site-v1'),str(EVIDENCE/'installed-s3-v1'),str(CI)))
s.supervise([sys.executable,str(ROOT/'default-helper-entry-v1.py')],'default-helper-v1',2*1024**3)
