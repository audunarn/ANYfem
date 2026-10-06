"""Versioned impact handoff under an exactly timed supervised metadata child."""
from pathlib import Path
import hashlib, json
ROOT = Path(__file__).resolve().parent
sha = lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
protected = json.loads((ROOT/'protected-v2-identities-v3.json').read_text())
assert all(sha(ROOT/name) == digest for name,digest in protected.items())
receipt = json.loads((ROOT/'installed-v3/execution.json').read_text())
assert receipt['status'] == 'completed' and receipt['exit_code'] == 0
axial_path = ROOT/'installed-temp-v3/test_project_fixed_translation0/workflow.json'
axial = json.loads(axial_path.read_text())
assert axial['status'] == 'completed' and axial['shell'] == 's3'
assert axial['history_generation'] == 3 and axial['generation'] == 4
alpha = max(value for record in axial['shell_s3_material'] for value in record['alpha'])
assert alpha == 0. and axial['physical_fibre_alpha_max'] > 0.
assert not axial['beam_generalized_material']
post = json.loads((ROOT/'installed-post-runtime-v3.json').read_text())
assert post['affinity'] == [0] and post['numba_threads'] == 1 and post['jit_off']
assert all(pool['num_threads'] == 1 for pool in post['pools'])
ledger = json.loads((ROOT/'ledger.json').read_text())
manifest = json.loads((ROOT/'final-manifest.json').read_text())
new_evidence = ('installed-v3/execution.json', 'installed-binding-v3.json',
    'installed-loaded-v3.json', 'installed-dependency-bindings-v3.json',
    'installed-runtime-v3.json', 'installed-post-runtime-v3.json', 'installed-junit-v3.xml',
    'installed-selected-tests-v3.json', 'entry-v3.py', 'runner-v3.py',
    'protected-v2-identities-v3.json')
manifest['evidence_sha256'].update({name:sha(ROOT/name) for name in new_evidence})
manifest['prior_manifest_sha256'] = sha(ROOT/'final-manifest.json')
manifest['protected_v2_identities'] = protected
manifest['installed_unique_cases'] = dict(count=8, provenance='7 V2 plus 1 V3 by impact; not one fresh full-eight campaign',
    v3_selector='test_project_fixed_translation_material_restart[s3-axial-fibre-p0]')
manifest['installed_axial_workflow'] = dict(path=str(axial_path), sha256=sha(axial_path),
    history_generation=3, continued_generation=4, s3_alpha_max=alpha,
    physical_fibre_alpha_max=axial['physical_fibre_alpha_max'],
    physical_member_ids=sorted({item['element_id'] for item in axial['beam_fibre_material']}),
    input_bytes_equal=axial['input_bytes_equal'],
    force_seed_restore_bytes_equal=axial['force_seed_restore_bytes_equal'],
    fresh_restore_bytes_equal=axial['fresh_restore_bytes_equal'],
    continued_bytes_equal=axial['continued_bytes_equal'])
manifest['check_truth']['installed_v3'] = '1 corrected all-S3 axial full Project workflow passed; elastic S3/yielded physical fibres'
manifest['shared_resource'] = dict(total_seconds=600., inherited_used_seconds=ledger['inherited_used_seconds'],
    used_seconds_before_final_bookkeeping=ledger['used_seconds'],
    remaining_seconds_before_final_bookkeeping=600.-ledger['used_seconds'],
    conservative_debits=ledger['conservative_debits'],
    final_bookkeeping_exact_child_wall_receipt='bookkeeping-v3/execution.json',
    authoritative_final_ledger='ledger.json', final_reserved_seconds=20.)
(ROOT/'final-manifest-v2.json').write_text(json.dumps(manifest, indent=2)+'\n')
text = (ROOT/'handoff.md').read_text()+f'''

## Versioned installed all-S3 axial completion

The original V2 handoff, manifest, runner, entry, selector and immutable input
snapshot/artifact bindings remain byte-for-byte unchanged; their identities are
recorded in protected-v2-identities-v3.json. Versioned V3 executes only the existing
corrected all-S3 axial/fibre/P0 Project workflow against the same consumer wheel,
installed site, solver wheel and input snapshot. It passed, consuming
{receipt['process_wall_seconds']!r} precise child-wall seconds, sampled peak tree
RSS {receipt['sampled_peak_tree_rss']} bytes. S3 alpha remains 0.0 and actual physical
fibres yield; generalized members are absent. Generation-3 loaded histories and
generation-4 unloading/recovery, case and Project persistence, exact native bytes,
force-seed/fresh restore and byte-identical continuation all passed.

Installed evidence now covers eight UNIQUE existing cases by impact: seven V2
plus one V3. No full-eight campaign was rerun and no new physics/material/load
case was introduced. Source V1's test-premise failure/metadata reuse and installed
V1's pre-test harness failure remain preserved and honestly classified.

Before final bookkeeping, the ledger records {ledger['used_seconds']!r} seconds
used /{600.-ledger['used_seconds']!r} remaining, including the separately adopted
0.3414474-second final-V2 bookkeeping envelope with precise-child timing UNKNOWN.
This final metadata step runs in a supervised child; its precise duration and
updated remaining authority are recorded afterward in bookkeeping-v3/execution.json
and ledger.json. A >=20-second root audit reserve remains protected. Scientific,
different-family, programme, release and final root acceptance obligations stay
OPEN. final-manifest-v2.json binds the versioned evidence without altering V2.
'''
(ROOT/'handoff-v2.md').write_text(text)
print(json.dumps(dict(installed_unique_cases=8, axial_s3_alpha_max=alpha,
    physical_fibre_alpha_max=axial['physical_fibre_alpha_max'],
    remaining_seconds_before_final_bookkeeping=600.-ledger['used_seconds'])))
