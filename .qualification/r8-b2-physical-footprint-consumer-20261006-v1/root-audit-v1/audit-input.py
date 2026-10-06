"""Independent artifact/evidence audit. Stdlib only; no numerical imports."""
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

candidate = read('candidate-source-shas-v4.json')
for name, expected in candidate.items():
    assert sha(REPO/name) == expected, name
source_v3 = read('candidate-source-shas-v3.json')
for name in ('src/anyfem/solve/stiffened_shell_b2.py', 'src/anyfem/solve/stiffened_shell_project.py'):
    assert source_v3[name] == candidate[name], name

counts = {}
for phase, expectation in (('source-v1', (3, 3)), ('source-v2', (3, 3)),
                            ('source-v3', (3, 2)), ('installed-v1', (3, 0))):
    base, version = phase.rsplit('-', 1)
    tree = ET.parse(RAW/(base+'-junit-'+version+'.xml'))
    cases = tree.findall('.//testcase')
    failures = [c for c in cases if c.find('failure') is not None or c.find('error') is not None]
    assert (len(cases), len(failures)) == expectation, phase
    assert not tree.findall('.//skipped'), phase
    counts[phase] = dict(cases=len(cases), failures=len(failures))

ledger = read('ledger.json')
assert ledger['inherited_used_seconds'] == 563.0746102000534
used = ledger['inherited_used_seconds']
for attempt in ledger['attempts']:
    used += attempt['process_wall_seconds']
    assert abs(attempt['cumulative_used_seconds']-used) <= 1e-8
    assert abs(attempt['remaining_seconds']-(600-used)) <= 1e-8
    assert 0 < attempt['process_wall_seconds'] <= attempt['seconds_limit']
    if 'sampled_peak_tree_rss' in attempt:
        assert attempt['sampled_peak_tree_rss'] <= attempt['memory_limit_bytes'] == 2*1024**3
        assert attempt['affinity'] == [0]
        assert attempt['environment']['NUMBA_DISABLE_JIT'] == '1'
        assert attempt['environment']['OPENBLAS_CORETYPE'] is None
assert abs(used-ledger['used_seconds']) <= 1e-8 and used < 600
for phase in ('build-v1', 'install-v1', 'installed-v1'):
    job = read(phase+'/execution.json')
    assert job['status'] == 'completed' and job['exit_code'] == 0, phase
    for name, expected in job['candidate'].items():
        assert candidate[name] == expected == sha(REPO/name), (phase, name)
assert (read('build-v1/execution.json')['process_wall_seconds']+
        read('install-v1/execution.json')['process_wall_seconds']) <= 6.5
assert read('installed-v1/execution.json')['remaining_seconds'] >= 8

binding = read('installed-binding-v1.json')
modules = binding['fem_modules']
wheel_path = RAW/'wheel-v1/anyfem-0.4.1-py3-none-any.whl'
with zipfile.ZipFile(wheel_path) as wheel:
    wheel_modules = {n:hashlib.sha256(wheel.read(n)).hexdigest() for n in wheel.namelist()
                     if n.startswith('anyfem/') and n.endswith('.py')}
    assert modules == wheel_modules
    for name, expected in modules.items():
        assert sha(REPO/'src'/name) == expected == sha(RAW/'site-v1'/name), name
    metadata = wheel.read('anyfem-0.4.1.dist-info/METADATA').decode().splitlines()
    assert 'Version: 0.4.1' in metadata and 'Requires-Python: >=3.11' in metadata
    consumer_payload = {n:hashlib.sha256(wheel.read(n)).hexdigest() for n in wheel.namelist()
                        if n.startswith('anyfem/') and not n.endswith('/')}
    wheel_metadata = {n:hashlib.sha256(wheel.read(n)).hexdigest() for n in wheel.namelist()
                      if not n.startswith('anyfem/') and not n.endswith('/')}
    for name, expected in consumer_payload.items():
        assert sha(RAW/'site-v1'/name) == expected, name
    for name, expected in wheel_metadata.items():
        if not name.endswith('/RECORD'):
            assert sha(RAW/'site-v1'/name) == expected, name
consumer_prov = read('artifact-provenance-v1.json')
assert sha(wheel_path) == consumer_prov['wheel_sha256']
assert consumer_prov['payload'] == consumer_payload
assert consumer_prov['metadata_hashes'] == wheel_metadata

solver_prov = json.loads((SR/'artifact-provenance-v1.json').read_text())
solver_wheel = SR/solver_prov['wheel']
assert sha(solver_wheel) == solver_prov['wheel_sha256'] == '375e9e4525c93aaa4e1d75a26bbbd8fb506494064ae3a698e5ea67603c9b5a9b'
assert binding['solver_wheel_sha256'] == solver_prov['wheel_sha256']
assert binding['solver_provenance_sha256'] == sha(SR/'artifact-provenance-v1.json')
solver_site = SR/solver_prov['installed_target']
loaded = read('installed-loaded-v1.json')
for name, path in loaded.items():
    prefix = RAW/'site-v1' if name.startswith('anyfem') else solver_site
    assert Path(path).is_relative_to(prefix), (name, path)
    relative = Path(path).relative_to(prefix).as_posix()
    expected = modules[relative] if name.startswith('anyfem') else solver_prov['payload'][relative]
    assert sha(path) == expected, name

snapshot = read('installed-input-snapshot-manifest-v4.json')
assert len(snapshot) == 2
for name, expected in snapshot.items():
    assert sha(REPO/name) == expected == sha(RAW/'installed-input-snapshot-v4'/name), name
assert len(read('installed-selected-tests-v1.json')['selectors']) == 1

before = read('installed-runtime-v1.json')
after = read('installed-post-runtime-v1.json')
assert before['affinity'] == after['affinity'] == [0]
assert before['jit_off'] and after['jit_off'] and after['numba_threads'] == 1
assert all(pool['num_threads'] == 1 for runtime in (before, after) for pool in runtime['pools'])
pins = {'numpy':'2.5.3', 'scipy':'1.18.1', 'numba':'0.68.0', 'llvmlite':'0.50.0'}
ci = SR.parent/'r8-native-s3-restart-binding-dev-20261005-v1/ci-site-network-v2'
for name, version in pins.items():
    assert before['versions'][name] == version and Path(before['imports'][name]).is_relative_to(ci)
deps = read('installed-dependency-bindings-v1.json')
old_deps = json.loads((SR/'installed-imports-after-tests-v1.json').read_text())['readonly_sibling_modules']
shared = 0
for name, info in deps.items():
    assert sha(info['path']) == info['sha256'], name
    if name in old_deps:
        assert info == old_deps[name], name
        shared += 1

workflow = read('installed-temp-v1/test_real_case_project_static_0/workflow.json')
source_workflow = read('source-temp-v3/test_real_case_project_static_0/workflow.json')
assert workflow == source_workflow
assert workflow['status'] == workflow['project_status'] == 'completed'
assert workflow['buckling_status'] == 'ok' and workflow['buckling_failure_reason'] is None
assert workflow['buckling_policy'] == 'B2_PHYSICAL_RIGID_FOOTPRINT_GREEN_BUCKLING_V1_DEV'
assert workflow['schema'] == 'anyfem.experimental-b2-physical-rigid-footprint-panel-v1'
assert workflow['input_bytes_equal'] and workflow['selected_mapping_matches_original_definition']
assert workflow['production_qualified'] is False
descriptor = workflow['physical_descriptor']
assert descriptor['rows_per_node'] == 5 and descriptor['normal_spin'] == 'independent'
assert workflow['requested_member_stations'] == {'beam-right':[0.,.5,1.]}
assert [b['case_count'] for b in workflow['batches']] == [1,10,100]
for batch in workflow['batches']:
    assert batch['status'] == 'completed' and batch['factorization_count'] == 1
    assert batch['solve_count'] == batch['case_count']
    assert batch['selected_footprint_counts'] == [1]*batch['case_count']
assert workflow['batches'][-1]['maximum_rhs_chunk'] < 100

proof = dict(status='scoped installed DEVELOPMENT verified', candidate=candidate,
    consumer_wheel_sha256=sha(wheel_path), solver_wheel_sha256=solver_prov['wheel_sha256'],
    source_wheel_site_modules=len(modules), verified_package_payloads=len(consumer_payload),
    verified_wheel_metadata=len(wheel_metadata), snapshot_inputs=len(snapshot),
    imported_consumer_modules=sum(n.startswith('anyfem') for n in loaded),
    imported_solver_modules=sum(n.startswith('anysolver') for n in loaded),
    loaded_dependency_bytes=len(deps), unchanged_frozen_sibling_modules=shared,
    counts=counts, workflow=workflow, exact_source_installed_workflow_equal=True,
    inherited_ledger_used_before_root_audit=ledger['used_seconds'],
    limitations=['Source V1/V2 failed before owner solves; codec/method repairs retained',
        'Source V3 one pass/two bad public-semantic assertions, not a passing source campaign',
        'Installed3 checks prove actual caller content/refusal/cancellation only on this artifact/host',
        'Prepared owner has one factorization even for zero-solve pre-cancellation',
        'Mass, modes, nonlinear and material requests remain unsupported by this consumer',
        'Non-author OpenAI review is not different-family/scientific acceptance',
        'Full programme, physical/external/scale/platform/release/main obligations remain incomplete'])
(RAW/'root-installed-verification-v1.json').write_text(json.dumps(proof, indent=2)+'\n')
print(json.dumps({k:proof[k] for k in ('status','source_wheel_site_modules',
    'imported_consumer_modules','imported_solver_modules','loaded_dependency_bytes','counts')}))
