"""Independent byte/JUnit/runtime audit; stdlib only, no numerical imports."""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile

RAW = Path(__file__).resolve().parent
REPO = RAW.parents[1]
SOLVER = Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001')
SR = SOLVER/'.qualification/b3-ge-e4-pl/r8-native-fibre-bl5-mixed-j2-installed-dev-20261006-v1'

def read(name):
    return json.loads((RAW/name).read_text(encoding='utf-8'))

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

candidate = read('candidate-source-shas.json')
for name, expected in candidate.items():
    assert sha(REPO/name) == expected, name
for phase in ('source-v1', 'source-v2', 'build-v1', 'install-v1', 'installed-v1', 'installed-v2', 'installed-v3'):
    job = read(phase+'/execution.json')
    assert 0 < job['process_wall_seconds'] <= job['seconds_limit']
    assert job['sampled_peak_tree_rss'] <= job['memory_limit_bytes'] == 2*1024**3
    assert job['affinity'] == [0]
    assert job['environment']['NUMBA_DISABLE_JIT'] == '1'
    assert job['environment']['OPENBLAS_CORETYPE'] is None
    assert job['exit_code'] == (1 if phase in ('source-v1', 'installed-v1') else 0)

counts = {}
for phase, expected in (('source-v1', (8, 1)), ('source-v2', (1, 0)), ('installed-v2', (7, 0)), ('installed-v3', (1, 0))):
    base, version = phase.rsplit('-', 1)
    tree = ET.parse(RAW/(base+'-junit-'+version+'.xml'))
    cases = tree.findall('.//testcase')
    failed = [c for c in cases if c.find('failure') is not None or c.find('error') is not None]
    assert (len(cases), len(failed)) == expected
    assert not tree.findall('.//skipped')
    if phase == 'source-v1':
        assert failed[0].attrib['name'].endswith('[s3-axial-fibre-p0]')
    counts[phase] = dict(cases=len(cases), failed=len(failed))

modules = read('installed-binding-v2.json')['fem_modules']
assert len(modules) == 99
wheel_path = RAW/'wheel-v1/anyfem-0.4.1-py3-none-any.whl'
assert sha(wheel_path) == '0e1ae7b3324410cfd64c4ba1dfd8810e1b6e239de22ba8bfeedcf9bb55cb0315'
with zipfile.ZipFile(wheel_path) as wheel:
    for name, expected in modules.items():
        assert sha(REPO/'src'/name) == expected == sha(RAW/'site-v1'/name)
        assert hashlib.sha256(wheel.read(name)).hexdigest() == expected
    metadata = wheel.read('anyfem-0.4.1.dist-info/METADATA').decode()
    assert '\nVersion: 0.4.1\n' in metadata and '\nRequires-Python: >=3.11\n' in metadata
    consumer_prov = read('artifact-provenance-v1.json')
    for name, expected in consumer_prov['payload'].items():
        assert hashlib.sha256(wheel.read(name)).hexdigest() == expected == sha(RAW/'site-v1'/name)
    for name, expected in consumer_prov['metadata_hashes'].items():
        assert hashlib.sha256(wheel.read(name)).hexdigest() == expected
        if not name.endswith('/RECORD'):
            assert sha(RAW/'site-v1'/name) == expected

solver_prov = json.loads((SR/'artifact-provenance-v1.json').read_text())
assert sha(SR/solver_prov['wheel']) == solver_prov['wheel_sha256'] == '375e9e4525c93aaa4e1d75a26bbbd8fb506494064ae3a698e5ea67603c9b5a9b'
solver_site = SR/solver_prov['installed_target']
loaded = read('installed-loaded-v2.json')
for name, path in loaded.items():
    prefix = RAW/'site-v1' if name.startswith('anyfem') else solver_site
    assert Path(path).is_relative_to(prefix)
    relative = Path(path).relative_to(prefix).as_posix()
    expected = modules[relative] if name.startswith('anyfem') else solver_prov['payload'][relative]
    assert sha(path) == expected

snapshot = read('installed-input-snapshot-manifest-v1.json')
assert len(snapshot) == 8
for name, expected in snapshot.items():
    assert sha(REPO/name) == expected == sha(RAW/'installed-input-snapshot-v1'/name)
assert len(read('installed-selected-tests-v1.json')['selectors']) == 7
assert len(read('installed-selected-tests-v3.json')['selectors']) == 1

before = read('installed-runtime-v2.json')
after = read('installed-post-runtime-v2.json')
assert before['affinity'] == after['affinity'] == [0]
assert before['jit_off'] and after['jit_off'] and after['numba_threads'] == 1
assert all(p['num_threads'] == 1 for r in (before, after) for p in r['pools'])
pins = {'numpy': '2.5.3', 'scipy': '1.18.1', 'numba': '0.68.0', 'llvmlite': '0.50.0'}
ci = SR.parent/'r8-native-s3-restart-binding-dev-20261005-v1/ci-site-network-v2'
for name, version in pins.items():
    assert before['versions'][name] == version and Path(before['imports'][name]).is_relative_to(ci)
deps = read('installed-dependency-bindings-v2.json')
old_deps = json.loads((SR/'installed-imports-after-tests-v1.json').read_text())['readonly_sibling_modules']
shared = 0
for name, info in deps.items():
    assert sha(info['path']) == info['sha256']
    if name in old_deps:
        assert info == old_deps[name], name
        shared += 1

axial_source = RAW/'source-temp-v1/test_project_fixed_translation0/workflow.json'
axial_path = RAW/'installed-temp-v3/test_project_fixed_translation0/workflow.json'
axial = json.loads(axial_path.read_text())
assert sha(axial_source) == read('source-v1-axial-metadata-replay.json')['sha256']
assert axial == json.loads(axial_source.read_text())
assert max(v for r in axial['shell_s3_material'] for v in r['alpha']) == 0
assert axial['physical_fibre_alpha_max'] > 0
mixed = read('installed-temp-v2/test_project_fixed_translation0/workflow.json')
assert max(v for r in mixed['shell_s3_material'] for v in r['alpha']) > 0
assert mixed['physical_fibre_alpha_max'] > 0
physical = {r['element_id'] for r in mixed['beam_fibre_material']}
assert physical == {r['element_id'] for r in mixed['physical_fibre_members']}
assert physical.isdisjoint(r['element_id'] for r in mixed['beam_generalized_material'])
assert mixed['history_generation'] == 3 and mixed['generation'] == 4
assert mixed['continued_bytes_equal'] and mixed['fresh_restore_bytes_equal']
assert mixed['free_equilibrium_ratio'] <= 1e-8 and mixed['constraint_max'] <= 1e-9
v3_loaded = read('installed-loaded-v3.json')
assert read('installed-binding-v3.json')['fem_modules'] == modules
for name, path in v3_loaded.items():
    prefix = RAW/'site-v1' if name.startswith('anyfem') else solver_site
    assert Path(path).is_relative_to(prefix)
    relative = Path(path).relative_to(prefix).as_posix()
    assert sha(path) == (modules[relative] if name.startswith('anyfem') else solver_prov['payload'][relative])
for name in ('installed-runtime-v3.json', 'installed-post-runtime-v3.json'):
    runtime = read(name)
    assert runtime['affinity'] == [0] and runtime['jit_off']
    assert all(p['num_threads'] == 1 for p in runtime['pools'])
assert read('installed-post-runtime-v3.json')['numba_threads'] == 1
for name, info in read('installed-dependency-bindings-v3.json').items():
    assert sha(info['path']) == info['sha256']
    if name in old_deps:
        assert info == old_deps[name], name
assert sha(RAW/'independent-consumer-review-v1.md') == '696f66caf5aa7fb7ea32212e3a2c760612eade64db188adbef509109652bb033'

ledger = read('ledger.json')
assert ledger['used_seconds'] < 600
proof = dict(status='scoped installed DEVELOPMENT verified', consumer_wheel_sha256=sha(wheel_path),
    solver_wheel_sha256=solver_prov['wheel_sha256'], candidate=candidate, source_wheel_site_modules=len(modules),
    verified_package_payloads=len(consumer_prov['payload']), verified_wheel_metadata=len(consumer_prov['metadata_hashes']),
    imported_consumer_modules=sum(n.startswith('anyfem') for n in loaded),
    imported_solver_modules=sum(n.startswith('anysolver') for n in loaded), readonly_loaded_dependencies=len(deps),
    unchanged_frozen_sibling_modules=shared, check_counts=counts, snapshot_inputs=len(snapshot),
    installed_mixed_workflow=mixed, inherited_ledger_used_before_root_audit=ledger['used_seconds'],
    installed_axial_workflow=axial, installed_unique_cases_by_impact=8,
    limitations=['Source axial correction reuses complete saved V1 numerical evidence; no fresh axial pytest pass',
        'Installed8 unique cases by impact (7V2+1V3), not a single full-suite invocation',
        'Replay-child duration unknown; containing tool envelope conservatively debited',
        'InstalledV1 harness-origin assertion failed before numerical execution; unchanged artifacts used in V2',
        'Non-author OpenAI code review does not meet different-family/scientific acceptance',
        'G0-G6, physical/external/scale/platform/production-release and main reconciliation remain incomplete'])
(RAW/'root-installed-verification-v1.json').write_text(json.dumps(proof, indent=2)+'\n')
print(json.dumps({k: proof[k] for k in ('status', 'source_wheel_site_modules', 'imported_consumer_modules',
    'imported_solver_modules', 'readonly_loaded_dependencies', 'unchanged_frozen_sibling_modules', 'check_counts')}))
