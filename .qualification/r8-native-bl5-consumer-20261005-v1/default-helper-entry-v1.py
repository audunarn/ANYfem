"""One installed public default-helper smoke; retain actual results/refusals."""
from pathlib import Path
import hashlib,json,runpy,sys,os
import anyfem,anysolver,numpy,scipy.linalg,numba,llvmlite,psutil
from threadpoolctl import threadpool_info
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell_project import solve_bl5_panel
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[1]
SOLVER=Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001')
EVIDENCE=SOLVER/'.qualification/b3-ge-e4-pl/r8-native-fibre-bl5-dev-20261005-v1'
CI=EVIDENCE.parent/'r8-native-s3-restart-binding-dev-20261005-v1/ci-site-network-v2'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
record=dict(imports={m.__name__:m.__file__ for m in (anyfem,anysolver,numpy,scipy,numba,llvmlite)},
 pools=threadpool_info(),affinity=psutil.Process().cpu_affinity(),jit_off=bool(numba.config.DISABLE_JIT),
 environment={k:os.environ.get(k) for k in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','NUMBA_DISABLE_JIT','OPENBLAS_CORETYPE')})
(ROOT/'default-helper-runtime-v1.json').write_text(json.dumps(record,indent=2)+'\n')
assert Path(anyfem.__file__).resolve().is_relative_to((ROOT/'site-v1').resolve())
assert Path(anysolver.__file__).resolve().is_relative_to((EVIDENCE/'installed-s3-v1').resolve())
for module,version in ((numpy,'2.5.3'),(scipy,'1.18.1'),(numba,'0.68.0'),(llvmlite,'0.50.0')):
 assert module.__version__==version and Path(module.__file__).resolve().is_relative_to(CI.resolve())
assert len(record['affinity'])==1 and record['jit_off'] and all(p['num_threads']==1 for p in record['pools'])
assert record['environment']['OPENBLAS_CORETYPE'] is None and all(v=='1' for k,v in record['environment'].items() if k!='OPENBLAS_CORETYPE')
binding=json.loads((ROOT/'installed-bindings-v1.json').read_text())
assert binding['fem_modules']=={str(p.relative_to(ROOT/'site-v1')).replace('\\','/'):sha(p) for p in (ROOT/'site-v1/anyfem').rglob('*.py')}
assert binding['solver_modules']=={str(p.relative_to(EVIDENCE/'installed-s3-v1')).replace('\\','/'):sha(p) for p in (EVIDENCE/'installed-s3-v1/anysolver').rglob('*.py')}
for p,digest in binding['files'].items(): assert sha(REPO/p)==digest,p
example=runpy.run_path(str(REPO/'examples/stiffened_shell_native_fibre_bl5_consumer.py'))
results={}
for shell in ('q4','s3','mixed'):
 panel=example['panel'](shell=shell)
 analysis=AnalysisDefinition.experimental_bl5_panel('native default '+shell,panel)
 try:
  result=solve_bl5_panel(analysis)
 except BaseException as error:
  results[shell]=dict(outcome='refused',error_type=type(error).__module__+'.'+type(error).__name__,message=str(error))
 else:
  results[shell]=dict(outcome='result',result_type=type(result).__module__+'.'+type(result).__name__,status=result.status,
   free_equilibrium_ratio=result.free_equilibrium_ratio,constraint_max=result.constraint_max,
   shell_displacement_finite=bool(numpy.isfinite(result.shell_displacement).all()),
   fields=list(getattr(result,'__dataclass_fields__',{})))
 (ROOT/'default-helper-results-v1.json').write_text(json.dumps(results,indent=2,allow_nan=False)+'\n')
 print(json.dumps({shell:results[shell]},indent=2,allow_nan=False),flush=True)
loaded={name:module.__file__ for name,module in sys.modules.items() if (name=='anyfem' or name.startswith('anyfem.') or name=='anysolver' or name.startswith('anysolver.')) and getattr(module,'__file__',None)}
assert all(Path(p).resolve().is_relative_to((ROOT/'site-v1' if name.startswith('anyfem') else EVIDENCE/'installed-s3-v1').resolve()) for name,p in loaded.items()),loaded
(ROOT/'default-helper-loaded-v1.json').write_text(json.dumps(loaded,indent=2)+'\n')
(ROOT/'default-helper-binding-v1.json').write_text(json.dumps(dict(entry_sha256=sha(__file__),runner_sha256=sha(ROOT/'default-helper-run-v1.py'),reused_binding_sha256=sha(ROOT/'installed-bindings-v1.json'),loaded_modules=len(loaded)),indent=2)+'\n')
