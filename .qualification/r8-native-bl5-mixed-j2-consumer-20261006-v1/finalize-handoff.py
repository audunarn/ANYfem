"""Read exact recorded artifacts and publish the owned development handoff."""
from pathlib import Path
import hashlib, json
ROOT = Path(__file__).resolve().parent
sha = lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
ledger = json.loads((ROOT/'ledger.json').read_text())
artifact = json.loads((ROOT/'artifact-provenance-v1.json').read_text())
execution = json.loads((ROOT/'installed-v2/execution.json').read_text())
assert execution['status'] == 'completed' and execution['exit_code'] == 0
workflow_path = ROOT/'installed-temp-v2/test_project_fixed_translation0/workflow.json'
workflow = json.loads(workflow_path.read_text())
assert workflow['status'] == 'completed' and workflow['history_generation'] == 3 and workflow['generation'] == 4
post_runtime = json.loads((ROOT/'installed-post-runtime-v2.json').read_text())
assert post_runtime['affinity'] == [0] and post_runtime['numba_threads'] == 1 and post_runtime['jit_off']
receipts = ['source-v1/execution.json', 'source-v2/execution.json', 'build-v1/execution.json',
    'install-v1/execution.json', 'installed-v1/execution.json', 'installed-v2/execution.json']
evidence = receipts + ['candidate-source-shas.json', 'artifact-provenance-v1.json',
    'installed-input-snapshot-manifest-v1.json', 'installed-selected-tests-v1.json',
    'installed-binding-v2.json', 'installed-loaded-v2.json', 'installed-dependency-bindings-v2.json',
    'installed-runtime-v2.json', 'installed-post-runtime-v2.json', 'installed-junit-v2.xml',
    'source-v1-axial-metadata-replay.json', 'source-junit-v1.xml', 'source-junit-v2.xml']
manifest = dict(state='verified against assigned development consumer criteria; root acceptance pending',
    artifact_sha256=artifact['wheel_sha256'], package_python_file_count=artifact['package_python_file_count'],
    evidence_sha256={name:sha(ROOT/name) for name in evidence},
    installed_workflow=dict(path=str(workflow_path), sha256=sha(workflow_path), status=workflow['status'],
        history_generation=workflow['history_generation'], continued_generation=workflow['generation'],
        schema=workflow['schema'], policy=workflow['policy'], target=workflow['target'],
        shell_s3_alpha_max=max(value for record in workflow['shell_s3_material'] for value in record['alpha']),
        physical_fibre_alpha_max=workflow['physical_fibre_alpha_max'],
        physical_member_ids=sorted({item['element_id'] for item in workflow['beam_fibre_material']}),
        generalized_member_ids=sorted({item['element_id'] for item in workflow['beam_generalized_material']})),
    check_truth=dict(source_v1='7 passed / 1 bad axial-yield premise failed; exact complete saved result replayed after correction',
        source_v2='1 strict-input metadata check passed', installed_v1='harness preflight origin assertion failed; zero pytest/numerical cases',
        installed_v2='7 explicit installed impact cases passed; all-S3 axial full workflow excluded'),
    shared_resource=dict(total_seconds=600., inherited_used_seconds=ledger['inherited_used_seconds'],
        cumulative_used_seconds=ledger['used_seconds'], remaining_seconds=600.-ledger['used_seconds'],
        conservative_debits=ledger['conservative_debits'], final_reserved_seconds=20.),
    scientific_qualified=False, release_ready=False,
    open_gates=['root final byte/import/JUnit audit and artifact acceptance', 'scientific acceptance',
        'required different-family review', 'full programme and Revision8', 'release/publication'],
    ownership='isolated df9d1d78 checkout only; product unchanged since source V1; no main/sibling/Git/config/dependency/version edits')
(ROOT/'final-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
checkpoint = json.loads((ROOT/'checkpoint.json').read_text())
checkpoint.update(state=manifest['state'], consumed_seconds=ledger['used_seconds'],
    remaining_seconds=600.-ledger['used_seconds'], final_manifest='final-manifest.json',
    installed_check='7 passed; scoped impact verification, not installed eight or scientific acceptance',
    next='root final audit/acceptance and integration; no numerical jobs active')
(ROOT/'checkpoint.json').write_text(json.dumps(checkpoint, indent=2)+'\n')
print(json.dumps(dict(state=manifest['state'], history_generation=3, continued_generation=4,
    used_seconds=ledger['used_seconds'], remaining_seconds=600.-ledger['used_seconds'])))
