from pathlib import Path
import hashlib,importlib.metadata,json,os,sys,time
import numpy as np,pytest
from threadpoolctl import threadpool_info
from anysolver import stiffened_shell as ss
import anyfem.solve.stiffened_shell as consumer
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[2]
SOLVER=Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001/.qualification/b3-ge-e4-pl/r8-distributing-material-dev-20261004-v1/site')
SITE=ROOT/'site'
assert Path(consumer.__file__).resolve()==SITE/'anyfem/solve/stiffened_shell.py'
for path in (SITE/'anyfem').rglob('*.py'):
    assert path.read_bytes()==(REPO/'src'/path.relative_to(SITE)).read_bytes(),path
assert Path(ss.__file__).resolve().is_relative_to(SOLVER)
assert ss.GE_FINITE_DISTRIBUTING_COUPLED_J2_MATERIAL_V1=='GE_FINITE_DISTRIBUTING_COUPLED_J2_MATERIAL_V1_DEV'
pools=threadpool_info();assert pools and all(p['num_threads']==1 for p in pools)
assert os.environ['NUMBA_DISABLE_JIT']=='1' and not sys.flags.optimize
files=['src/anyfem/solve/stiffened_shell.py','src/anyfem/solve/stiffened_shell_project.py',
    'src/anyfem/solve/stiffened_shell_ge_distributing_material.py',
    'tests/test_stiffened_shell_ge_distributing_material_consumer.py',
    'examples/stiffened_shell_ge_distributing_material_consumer.py']
tests=['tests/test_stiffened_shell_ge_distributing_material_consumer.py',
    'tests/test_stiffened_shell_ge_finite_distributing_consumer.py',
    'tests/test_stiffened_shell_ge_distributing_consumer.py',
    'tests/test_stiffened_shell_ge_reference_modal_consumer.py::test_save_reopen_owns_exact_modal_definition_without_owner_assembly']
record=dict(scope='material-aware consumer development, not scientific qualification',pools=pools,
    imports=[str(consumer.__file__),str(ss.__file__)],tests=tests,
    sources={f:hashlib.sha256((REPO/f).read_bytes()).hexdigest() for f in files})
out=ROOT/'installed-runtime-v3.json';out.open('x',encoding='utf-8').write(json.dumps(record,indent=2)+'\n')
start=time.monotonic();status=pytest.main(['-q',*[str(REPO/p) for p in tests],'-p','no:cacheprovider','--basetemp',str(ROOT/'pytest-v3')])
record.update(exit_code=int(status),seconds=time.monotonic()-start)
record['wheel_sha256']=hashlib.sha256((ROOT/'dist-v3/anyfem-0.4.1-py3-none-any.whl').read_bytes()).hexdigest()
record['imported_paths']={name:module.__file__ for name,module in sys.modules.items() if name.startswith(('anyfem.','anysolver.')) and getattr(module,'__file__',None)}
assert all(Path(path).resolve().is_relative_to(SITE if name.startswith('anyfem.') else SOLVER) for name,path in record['imported_paths'].items())
out.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8');raise SystemExit(status)

