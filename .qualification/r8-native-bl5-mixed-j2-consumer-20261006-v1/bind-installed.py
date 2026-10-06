"""Byte-only artifact/checkpoint metadata; no numerical execution."""
from pathlib import Path
import email, hashlib, json, zipfile
ROOT = Path(__file__).resolve().parent; REPO = ROOT.parents[1]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
wheel = next((ROOT/'wheel-v1').glob('*.whl')); site = ROOT/'site-v1'
with zipfile.ZipFile(wheel) as archive:
    payload = {name:hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
        if name.startswith('anyfem/') and not name.endswith('/')}
    assert all(sha(site/name) == digest for name,digest in payload.items())
    python_source = {str(p.relative_to(REPO/'src')).replace('\\','/'):sha(p) for p in (REPO/'src/anyfem').rglob('*.py')}
    assert python_source == {name:digest for name,digest in payload.items() if name.endswith('.py')}
    metadata_name = next(name for name in archive.namelist() if name.endswith('.dist-info/METADATA'))
    metadata = email.message_from_bytes(archive.read(metadata_name))
    assert metadata['Version'] == '0.4.1' and metadata['Requires-Python'] == '>=3.11'
    assert sha(site/metadata_name) == hashlib.sha256(archive.read(metadata_name)).hexdigest()
    metadata_hashes = {name:hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()
        if '.dist-info/' in name and not name.endswith('/')}
record = dict(wheel=str(wheel), wheel_sha256=sha(wheel), installed_target=str(site),
    package_python_file_count=len(python_source), payload=payload, metadata_hashes=metadata_hashes,
    name=metadata['Name'], version=metadata['Version'], requires_python=metadata['Requires-Python'],
    requires_dist=metadata.get_all('Requires-Dist'), source_binding='candidate-source-shas.json',
    snapshot_manifest_sha256=sha(ROOT/'installed-input-snapshot-manifest-v1.json'))
(ROOT/'artifact-provenance-v1.json').write_text(json.dumps(record, indent=2)+'\n')
ledger = json.loads((ROOT/'ledger.json').read_text())
remaining = 600.-ledger['used_seconds']; cap = min(90., remaining-20.)
checkpoint = json.loads((ROOT/'checkpoint.json').read_text())
checkpoint.update(state='built and installed; byte-verified candidate; installed impact check pending',
    artifact_sha256=record['wheel_sha256'], consumed_seconds=ledger['used_seconds'],
    remaining_seconds=remaining, installed_process_cap_seconds=cap, reserve_seconds=20,
    selected_installed_tests=[
        'test_stiffened_shell_native_bl5_mixed_j2_consumer.py::test_project_fixed_translation_material_restart[mixed-biaxial-mixed-continuous_p1_coarse]',
        'test_stiffened_shell_native_bl5_mixed_j2_consumer.py::test_v6_strict_legacy_isolation_and_owned_project_record',
        'test_stiffened_shell_native_bl5_mixed_j2_consumer.py::test_public_failure_cancel_and_checkpoint_atomicity',
        'test_stiffened_shell_native_fibre_bl5_consumer.py::test_strict_v5_mutation_topology_and_project_ownership',
        'test_bl5_material_consumer.py::test_bl5_material_v3_strict_schema_and_default_isolation',
        'test_bl5_coupled_material_consumer.py::test_bl5_coupled_v4_strict_roundtrip_and_legacy_isolation',
        'test_bl5_project.py::test_bl5_payload_is_owned_and_route_cannot_be_relabelled'],
    installed_scope_gap='all-S3 axial complete workflow remains source V1 evidence; excluded from installed selectors',
    timing_uncertainty='metadata replay child unknown; root-adopted 5.2350776 second containing shell debit separately recorded')
(ROOT/'checkpoint.json').write_text(json.dumps(checkpoint, indent=2)+'\n')
print(json.dumps(dict(wheel_sha256=record['wheel_sha256'], package_python_files=len(python_source),
    consumed_seconds=ledger['used_seconds'], remaining_seconds=remaining, installed_cap_seconds=cap)))
