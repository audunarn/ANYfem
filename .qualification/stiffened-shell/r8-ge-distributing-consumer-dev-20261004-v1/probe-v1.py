"""Actual consumer/solver import and runtime identities before focused checks."""
from pathlib import Path
import hashlib, importlib, importlib.metadata, json, os, runpy, sys, time
import numpy as np
import pytest
from threadpoolctl import threadpool_info

ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[2]
SOLVER=Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001/.qualification/b3-ge-e4-pl/r8-distributing-ge-owner-dev-20261004-v1')
kind=os.environ['R8_DISTRIBUTING_CONSUMER_KIND']
base=REPO/'src' if kind=='source' else ROOT/'site'
names=('solve.stiffened_shell','solve.stiffened_shell_project','model.records','io.project_file')
modules=[importlib.import_module('anyfem.'+name) for name in names]
paths=[base/'anyfem'/(name.replace('.','/')+'.py') for name in names]
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
for module,path in zip(modules,paths):
    if Path(module.__file__).resolve()!=path.resolve():raise ValueError('actual consumer import differs')
    if sha(path)!=sha(REPO/'src/anyfem'/path.relative_to(base/'anyfem')):raise ValueError('artifact/source consumer differs')
from anysolver import stiffened_shell as api
solver_path=SOLVER/'site/anysolver/stiffened_shell.py'
if Path(api.__file__).resolve()!=solver_path.resolve() or 'distributing_footprints' not in api.LinearDefinition.__dataclass_fields__:
    raise ValueError('verified installed distributing solver required')
pools=threadpool_info()
if not pools or any(p['num_threads']!=1 for p in pools) or sys.flags.optimize or os.environ.get('NUMBA_DISABLE_JIT')!='1':
    raise ValueError('actual pools1/unoptimized/JIT-off required')
tests=[str(REPO/'tests/test_stiffened_shell_ge_distributing_consumer.py'),
    str(REPO/'tests/test_stiffened_shell_nodal_footprint_consumer.py')+'::test_strict_input_and_project_roundtrip_preserve_groups_without_assembly',
    str(REPO/'tests/test_stiffened_shell_nodal_footprint_consumer.py')+'::test_legacy_point_schema_is_unchanged_and_rejects_footprint_fields',
    str(REPO/'tests/test_stiffened_shell_nodal_footprint_consumer.py')+'::test_project_dispatch_preserves_explicit_linear_and_finite_selection',
    str(REPO/'tests/test_stiffened_shell_ge_reference_modal_consumer.py')+'::test_from_definition_rejects_unverified_attachment_and_material_scope']
record=dict(scope='Experimental reference-linear distributing consumer; not scientific/release acceptance',kind=kind,
    imports=[str(p) for p in paths],consumer_sha256={p.name:sha(p) for p in paths},
    solver_import=str(solver_path),solver_sha256=sha(solver_path),
    solver_wheel_sha256=sha(SOLVER/'dist/anysolver-0.4.7-py3-none-any.whl'),
    tests_sha256={Path(p.split('::')[0]).name:sha(Path(p.split('::')[0])) for p in tests},
    effective_pools=pools,jit_disabled=True,versions={name:importlib.metadata.version(name) for name in
        ('numpy','scipy','pytest','ANYmaterial','ANYmesher','ANYfileio')})
if kind!='source':
    wheel,=(ROOT/'dist').glob('*.whl');record['wheel']=dict(path=str(wheel),sha256=sha(wheel),bytes=wheel.stat().st_size)
path=ROOT/(kind+'-runtime-v1.json');path.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
started=time.monotonic()
if kind=='example':
    example=REPO/'examples/stiffened_shell_ge_distributing_consumer.py';record['example_sha256']=sha(example)
    sys.argv=[str(example),str(ROOT/'example-v1'/'workflow')]
    runpy.run_path(str(example),run_name='__main__');status=0
else:
    status=pytest.main(['-q',*tests,'-p','no:cacheprovider','--basetemp',str(ROOT/(kind+'-pytest-v1'))])
record.update(exit_code=int(status),checks_seconds=time.monotonic()-started)
path.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
raise SystemExit(status)
