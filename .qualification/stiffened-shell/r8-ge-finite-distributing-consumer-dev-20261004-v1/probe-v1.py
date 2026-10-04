from pathlib import Path
import hashlib,importlib,json,os,runpy,sys,time
import pytest
from threadpoolctl import threadpool_info
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[2]
SOLVER=Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001')
SOLVERRAW=SOLVER/'.qualification/b3-ge-e4-pl/r8-distributing-finite-owner-dev-20261004-v1'
kind=os.environ['R8_FINITE_CONSUMER_KIND'];sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
imports={}
bindings=((('anyfem.solve.stiffened_shell','anyfem.solve.stiffened_shell_project','anyfem.model.records','anyfem.io.project_file'),REPO/'src' if kind=='source' else ROOT/'site',REPO),
 (tuple('anysolver.'+n for n in ('stiffened_shell','_stiffened_shell_ge_experimental','_stiffened_shell_ge_finite_experimental','_stiffened_shell_ge_distributing_finite','_stiffened_shell_ge_checkpoint_binary','_stiffened_shell_distributing_pose')),SOLVERRAW/'site',SOLVER))
for names,site,source in bindings:
 for name in names:
  p=Path(importlib.import_module(name).__file__).resolve();relative=Path(name.replace('.','/')+'.py')
  assert p==site/relative and sha(p)==sha(source/'src'/relative)
  imports[str(p)]=sha(p)
pools=threadpool_info();assert pools and all(p['num_threads']==1 for p in pools)
assert not sys.flags.optimize and os.environ['NUMBA_DISABLE_JIT']=='1'
tests=[str(REPO/'tests'/n) for n in ('test_stiffened_shell_ge_finite_distributing_consumer.py','test_stiffened_shell_ge_distributing_consumer.py')]
tests.extend(str(REPO/'tests/test_stiffened_shell_ge_reference_modal_consumer.py')+'::'+n for n in ('test_from_definition_rejects_unverified_attachment_and_material_scope',))
record=dict(scope='Experimental finite consumer installed workflow, not physical/scientific/release acceptance',kind=kind,imports=imports,pools=pools,
 solver_wheel_sha256=sha(next((SOLVERRAW/'dist').glob('*.whl'))),consumer_wheel_sha256=None if kind=='source' else sha(next((ROOT/'dist').glob('*.whl'))),tests={p:sha(Path(p.split('::')[0])) for p in tests})
out=ROOT/(kind+'-runtime-v1.json');out.open('x').write(json.dumps(record,indent=2)+'\n');start=time.monotonic()
if kind=='example':
 example=REPO/'examples/stiffened_shell_ge_finite_distributing_consumer.py';sys.path.insert(0,str(example.parent))
 spec=importlib.util.spec_from_file_location('finite_consumer_example',example);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 record['example_sha256']=sha(example);record['observations']=module.workflow(ROOT/'example-output-v1');print(json.dumps(record['observations'],sort_keys=True));status=0
else:status=pytest.main(['-q',*tests,'-p','no:cacheprovider','--basetemp',str(ROOT/(kind+'-pytest-v1'))])
record.update(exit_code=int(status),checks_seconds=time.monotonic()-start);out.write_text(json.dumps(record,indent=2)+'\n');raise SystemExit(status)
