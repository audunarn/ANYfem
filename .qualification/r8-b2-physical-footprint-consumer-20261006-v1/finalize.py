"""Exactly supervised byte-only consumer artifact and truthful development handoff."""
from pathlib import Path
import email, hashlib, json, zipfile
ROOT=Path(__file__).resolve().parent; REPO=ROOT.parents[1]
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
ledger=json.loads((ROOT/'ledger.json').read_text())
receipt=json.loads((ROOT/'installed-v1/execution.json').read_text())
assert receipt['status']=='completed' and receipt['exit_code']==0
wheel=next((ROOT/'wheel-v1').glob('*.whl')); site=ROOT/'site-v1'
with zipfile.ZipFile(wheel) as archive:
    payload={name:hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
        if name.startswith('anyfem/') and not name.endswith('/')}
    assert all(sha(site/name)==digest for name,digest in payload.items())
    source={str(p.relative_to(REPO/'src')).replace('\\','/'):sha(p) for p in (REPO/'src/anyfem').rglob('*.py')}
    assert source=={name:digest for name,digest in payload.items() if name.endswith('.py')}
    metadata_name=next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))
    metadata=email.message_from_bytes(archive.read(metadata_name))
    assert metadata['Version']=='0.4.1' and metadata['Requires-Python']=='>=3.11'
    metadata_hashes={name:hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
        if '.dist-info/' in name and not name.endswith('/')}
    assert sha(site/metadata_name)==metadata_hashes[metadata_name]
artifact=dict(wheel=str(wheel),wheel_sha256=sha(wheel),installed_target=str(site),payload=payload,
    package_python_file_count=len(source),version=metadata['Version'],requires_python=metadata['Requires-Python'],
    metadata_hashes=metadata_hashes,requires_dist=metadata.get_all('Requires-Dist'))
(ROOT/'artifact-provenance-v1.json').write_text(json.dumps(artifact,indent=2)+'\n')
workflow_path=ROOT/'installed-temp-v1/test_real_case_project_static_b0/workflow.json'
workflow=json.loads(workflow_path.read_text())
assert workflow['buckling_status']=='ok' and workflow['selected_mapping_matches_original_definition']
assert workflow['footprint_wrenches_match_original_definition']
evidence=('candidate-source-shas-v4.json','installed-input-snapshot-manifest-v4.json',
    'source-v1/execution.json','source-v2/execution.json','source-v3/execution.json',
    'build-v1/execution.json','install-v1/execution.json','installed-v1/execution.json',
    'installed-junit-v1.xml','installed-binding-v1.json','installed-loaded-v1.json',
    'installed-dependency-bindings-v1.json','installed-runtime-v1.json','installed-post-runtime-v1.json',
    'installed-selected-tests-v1.json')
manifest=dict(state='verified against assigned installed development criteria; root acceptance pending',
    source_identity=json.loads((ROOT/'candidate-source-shas-v4.json').read_text()),
    artifact=artifact,evidence_sha256={name:sha(ROOT/name) for name in evidence},
    workflow=dict(path=str(workflow_path),sha256=sha(workflow_path),status=workflow['status'],
        project_status=workflow['project_status'],buckling_status=workflow['buckling_status'],
        buckling_policy=workflow['buckling_policy'],batch_counts=[b['case_count'] for b in workflow['batches']],
        factorization_counts=[b['factorization_count'] for b in workflow['batches']],
        selected_mapping_matches_original_definition=True,footprint_wrenches_match_original_definition=True),
    check_truth=dict(source_v1='3 codec integration failures before solves',source_v2='3 missing canonical-method failures before solves',
        source_v3='1 passed / 2 incorrect status/prepared-factor test premises failed; genuine full workflow completed',
        installed_v1='3 corrected impact checks passed, including previously unexecuted cancel-prefix/callback checks'),
    timing=dict(inherited_used_seconds=ledger['inherited_used_seconds'],used_seconds_before_final_bookkeeping=ledger['used_seconds'],
        remaining_seconds_before_final_bookkeeping=600.-ledger['used_seconds'],
        precise_final_bookkeeping_receipt='bookkeeping-v1/execution.json',authoritative_final_ledger='ledger.json',root_reserved_seconds=8.),
    open_gates=['root final artifact audit/integration/acceptance','scientific acceptance','required different-family review',
        'full programme/Revision8','release/publication'],production_qualified=False)
(ROOT/'final-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
checkpoint=json.loads((ROOT/'checkpoint.json').read_text())
checkpoint.update(state=manifest['state'],artifact_sha256=artifact['wheel_sha256'],final_manifest='final-manifest.json',
    next='root byte/import/JUnit audit and integration; no numerical jobs active')
(ROOT/'checkpoint-final.json').write_text(json.dumps(checkpoint,indent=2)+'\n')
text=f'''# B2 physical rigid-footprint development consumer handoff

State: verified against assigned installed development criteria. Root final
artifact audit/integration/acceptance remain pending. Scientific, different-family,
full programme/Revision8, release and publication obligations remain OPEN.

Only the isolated checkout at 94a0b0c56d9494ea2555aab65ed17c086e4d461b was edited.
Owned product paths: stiffened_shell_b2.py and stiffened_shell_project.py, plus
the new test, example and document recorded in candidate-source-shas-v4.json.
model/records.py needed no change. No main/sibling/solver/Git/dependency/version
changes were performed. Historical protected evidence was not restored/discarded.

The strict new ExperimentalB2PhysicalRigidFootprintPanel/schema reconstructs only
the public B2PhysicalRigidFootprintDefinition. Shared B2 input syntax has empty
POINT rows, decoded/encoded via an explicit internal neutral path scoped to the
physical envelope; no POINT owner is created or solved. Historical POINT's
nonempty-attachment guard remains its default and has regression coverage. The
old nodal six-row descriptor, modes, mass/material and other schemas keep their
guards. The new law is the public five-row position/director footprint with
independent normal spin. No mechanical mathematics was duplicated.

The genuine case and existing Project save/reopen workflow completed static,
selected 1/10/100 compatible batches and correctly typed physical-footprint
reference buckling (public success status 'ok'). Exact original-definition versus
reopened Project comparisons passed for selected DOF values/reactions, requested
member IDs/stations/resultant values, complete descriptors/frame, and actual
footprint wrench IDs/contents. Each batch used one public factorization; bounded
RHS chunk and selected recovery counters are retained in workflow.json. The
already executed Project one-case result is reused for the 1-case summary.
Cancelled pre-submission and accepted-prefix semantics plus callback errors passed.
The public owner prepares factorization_count1 before submission; pre-cancel has
zero solved cases/solve_count0, and no false zero-preparation claim is made.

Failures are preserved: source V1, {ledger['attempts'][0]['process_wall_seconds']!r}s,
failed the historical POINT-empty codec integration before owner solves; source
V2, {ledger['attempts'][1]['process_wall_seconds']!r}s, lacked explicit canonical
methods; source V3, {ledger['attempts'][2]['process_wall_seconds']!r}s, completed
the real workflow but had two incorrect test premises ('completed' versus public
'ok', zero versus one prepared factor). V3 had 1PASS/2FAIL. Corrected installed
V1 passed all three impact checks in {receipt['process_wall_seconds']!r} precise
child-wall seconds; sampled peak tree RSS {receipt['sampled_peak_tree_rss']} bytes.
No passed source campaign or historical solver campaign was rerun.

Exactly one offline wheel build and one isolated install were performed, consuming
{ledger['attempts'][3]['process_wall_seconds']!r}s and {ledger['attempts'][4]['process_wall_seconds']!r}s
({ledger['attempts'][3]['process_wall_seconds']+ledger['attempts'][4]['process_wall_seconds']!r}s combined,
within the 6.5s authority). Wheel SHA256 {artifact['wheel_sha256']}. All {len(source)}
source/wheel/installed Python modules and known package payload bytes matched;
metadata remains ANYfem0.4.1/Python>=3.11. Numerical runtime used the unchanged
solver wheel375e9e45, exact CI versions, affinity[0], pools1/Numba1/JIToff and
unforced backend. Actual installed/sibling origins and loaded byte hashes are
recorded in installed runtime/import/dependency maps; no source ANYfem fallback.

Before final exactly supervised stdlib bookkeeping, the inherited shared ledger
records {ledger['used_seconds']!r}s used /{600.-ledger['used_seconds']!r}s remaining.
The final precise bookkeeping duration and resulting authority are in
bookkeeping-v1/execution.json and ledger.json. At least8s remain reserved for root's
final byte/import/JUnit audit. Full diagnostics, failed versions, immutable input
snapshots, source identities and receipts are preserved in this raw directory.
final-manifest.json binds the evidence and explicitly separates development
checks from installed artifact acceptance, scientific qualification and release.
'''
(ROOT/'handoff.md').write_text(text)
print(json.dumps(dict(wheel_sha256=artifact['wheel_sha256'],installed_checks=3,
    package_python_files=len(source),remaining_seconds_before_bookkeeping=600.-ledger['used_seconds'])))
