from pathlib import Path
import hashlib, json, os, sys, zipfile
ROOT = Path(__file__).resolve().parent; REPO = ROOT.parents[1]
SOLVER_RAW = Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001/.qualification/b3-ge-e4-pl/r8-native-fibre-bl5-mixed-j2-installed-dev-20261006-v1')
CI = SOLVER_RAW.parent/'r8-native-s3-restart-binding-dev-20261005-v1/ci-site-network-v2'
mode, version = sys.argv[1:3]
fem = ROOT/'site-v1' if mode == 'installed' else REPO/'src'
solver = SOLVER_RAW/'installed-host-v1'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
import anyfem, anysolver, anymaterial, numpy, scipy.linalg, numba, llvmlite, psutil
from threadpoolctl import threadpool_info
runtime = dict(imports={m.__name__:m.__file__ for m in (anyfem, anysolver, anymaterial, numpy, scipy, numba, llvmlite)},
    versions={m.__name__:getattr(m,'__version__',None) for m in (anyfem, anysolver, anymaterial, numpy, scipy, numba, llvmlite)},
    affinity=psutil.Process().cpu_affinity(), pools=threadpool_info(), jit_off=bool(numba.config.DISABLE_JIT))
(ROOT/(mode+'-runtime-'+version+'.json')).write_text(json.dumps(runtime, indent=2)+'\n')
assert Path(anyfem.__file__).resolve().is_relative_to(fem.resolve())
assert Path(anysolver.__file__).resolve().is_relative_to(solver.resolve())
assert Path(anymaterial.__file__).resolve() == Path('C:/Github/ANYmaterial/src/anymaterial/__init__.py').resolve()
assert sha(anymaterial.__file__) == '46d9c80639435e40c8533d78d89364c51f1cef65969b1c418eab4f36a7512900'
for module, expected in ((numpy,'2.5.3'), (scipy,'1.18.1'), (numba,'0.68.0'), (llvmlite,'0.50.0')):
    assert module.__version__ == expected and Path(module.__file__).resolve().is_relative_to(CI.resolve())
assert runtime['affinity'] == [0] and runtime['jit_off']
assert all(pool['num_threads'] == 1 for pool in runtime['pools'])
assert os.environ.get('OPENBLAS_CORETYPE') is None
provenance = json.loads((SOLVER_RAW/'artifact-provenance-v1.json').read_text())
wheel = SOLVER_RAW/provenance['wheel']
assert sha(wheel) == provenance['wheel_sha256'] == '375e9e4525c93aaa4e1d75a26bbbd8fb506494064ae3a698e5ea67603c9b5a9b'
with zipfile.ZipFile(wheel) as archive:
    assert provenance['payload'] == {name:hashlib.sha256(archive.read(name)).hexdigest()
        for name in archive.namelist() if name.startswith('anysolver/') and not name.endswith('/')}
fem_modules = {str(path.relative_to(REPO/'src')).replace('\\','/'):sha(path)
    for path in (REPO/'src/anyfem').rglob('*.py')}
if mode == 'installed':
    assert fem_modules == {str(p.relative_to(fem)).replace('\\','/'):sha(p) for p in (fem/'anyfem').rglob('*.py')}
    with zipfile.ZipFile(next((ROOT/'wheel-v1').glob('*.whl'))) as archive:
        assert fem_modules == {name:hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
            if name.startswith('anyfem/') and name.endswith('.py')}
(ROOT/(mode+'-binding-'+version+'.json')).write_text(json.dumps(dict(fem_modules=fem_modules,
    solver_wheel_sha256=sha(wheel), solver_provenance_sha256=sha(SOLVER_RAW/'artifact-provenance-v1.json'),
    entry_sha256=sha(__file__)), indent=2)+'\n')
tests = ('test_stiffened_shell_native_bl5_mixed_j2_consumer.py',
    'test_stiffened_shell_native_fibre_bl5_consumer.py::test_strict_v5_mutation_topology_and_project_ownership',
    'test_bl5_material_consumer.py::test_bl5_material_v3_strict_schema_and_default_isolation',
    'test_bl5_coupled_material_consumer.py::test_bl5_coupled_v4_strict_roundtrip_and_legacy_isolation',
    'test_bl5_project.py::test_bl5_payload_is_owned_and_route_cannot_be_relabelled')
import pytest
if mode == 'source' and version == 'v2':
    tests = ('test_stiffened_shell_native_bl5_mixed_j2_consumer.py::test_v6_strict_legacy_isolation_and_owned_project_record',)
if mode == 'installed':
    tests = ('test_stiffened_shell_native_bl5_mixed_j2_consumer.py::test_project_fixed_translation_material_restart[mixed-biaxial-mixed-continuous_p1_coarse]',
        'test_stiffened_shell_native_bl5_mixed_j2_consumer.py::test_v6_strict_legacy_isolation_and_owned_project_record',
        'test_stiffened_shell_native_bl5_mixed_j2_consumer.py::test_public_failure_cancel_and_checkpoint_atomicity',
        *tests[1:])
test_root = ROOT/'installed-input-snapshot-v1' if mode == 'installed' else REPO
if mode == 'installed':
    manifest = json.loads((ROOT/'installed-input-snapshot-manifest-v1.json').read_text())
    assert all(sha(test_root/name) == digest for name,digest in manifest.items())
    (ROOT/'installed-selected-tests-v1.json').write_text(json.dumps(dict(selectors=list(tests),
        snapshot_manifest_sha256=sha(ROOT/'installed-input-snapshot-manifest-v1.json'),
        pyproject_sha256=sha(REPO/'pyproject.toml')), indent=2)+'\n')
code = pytest.main(['-q', '-c', str(REPO/'pyproject.toml'), '-o', 'pythonpath=',
    '--basetemp', str(ROOT/(mode+'-temp-'+version)), '--junitxml='+str(ROOT/(mode+'-junit-'+version+'.xml')),
    *[str(test_root/'tests'/test) for test in tests]])
loaded = {name:module.__file__ for name,module in sys.modules.items() if
    (name == 'anyfem' or name.startswith('anyfem.') or name == 'anysolver' or name.startswith('anysolver.'))
    and getattr(module,'__file__',None)}
assert all(Path(path).resolve().is_relative_to((fem if name.startswith('anyfem') else solver).resolve()) for name,path in loaded.items())
for name, path in loaded.items():
    if name.startswith('anysolver'):
        relative = str(Path(path).relative_to(solver)).replace('\\','/')
        assert sha(path) == provenance['payload'][relative]
(ROOT/(mode+'-loaded-'+version+'.json')).write_text(json.dumps(loaded, indent=2)+'\n')
dependencies = {name:dict(path=module.__file__, sha256=sha(module.__file__))
    for name,module in sys.modules.items() if
    ((name.lower().startswith('any') and not name.startswith(('anyfem','anysolver')))
        or name.startswith(('numpy', 'scipy', 'numba', 'llvmlite')))
    and getattr(module,'__file__',None) and Path(module.__file__).is_file()}
(ROOT/(mode+'-dependency-bindings-'+version+'.json')).write_text(json.dumps(dependencies, indent=2)+'\n')
post_runtime = dict(affinity=psutil.Process().cpu_affinity(), pools=threadpool_info(),
    numba_threads=numba.get_num_threads(), jit_off=bool(numba.config.DISABLE_JIT))
(ROOT/(mode+'-post-runtime-'+version+'.json')).write_text(json.dumps(post_runtime, indent=2)+'\n')
assert post_runtime['affinity'] == [0] and post_runtime['numba_threads'] == 1
assert post_runtime['jit_off'] and all(pool['num_threads'] == 1 for pool in post_runtime['pools'])
if mode == 'installed':
    assert all(sha(test_root/name) == digest for name,digest in manifest.items())
raise SystemExit(code)
