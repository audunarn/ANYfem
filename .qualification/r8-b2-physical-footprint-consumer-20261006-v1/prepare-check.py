"""Freeze the B2 impact inputs and adapt the established precise supervisor."""
from pathlib import Path
import hashlib, json
ROOT = Path(__file__).resolve().parent; REPO = ROOT.parents[1]
PREVIOUS = ROOT.parent/'r8-native-bl5-mixed-j2-consumer-20261006-v1'
sha = lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
remaining = 36.92538979994663
runner = (PREVIOUS/'runner.py').read_text().replace('402.72211770003196', repr(600.-remaining))
runner = runner.replace('min(90., remaining-60.)', 'min(12., remaining-20.)')
runner = runner.replace("REPO/'src/anyfem/solve/stiffened_shell.py'", "REPO/'src/anyfem/solve/stiffened_shell_b2.py'")
runner = runner.replace('test_stiffened_shell_native_bl5_mixed_j2_consumer.py', 'test_stiffened_shell_b2_physical_footprint_consumer.py')
runner = runner.replace('stiffened_shell_native_fibre_bl5_mixed_j2_consumer.py', 'stiffened_shell_b2_physical_footprint_consumer.py')
(ROOT/'runner.py').write_text(runner)
entry = (PREVIOUS/'entry.py').read_text()
start = entry.index('tests = ('); end = entry.index('code = pytest.main(', start)
entry = entry[:start]+"""tests = ('test_stiffened_shell_b2_physical_footprint_consumer.py',)
import pytest
test_root = ROOT/'installed-input-snapshot-v1'
manifest = json.loads((ROOT/'installed-input-snapshot-manifest-v1.json').read_text())
assert all(sha(test_root/name) == digest for name,digest in manifest.items())
(ROOT/(mode+'-selected-tests-'+version+'.json')).write_text(json.dumps(dict(selectors=list(tests),
    snapshot_manifest_sha256=sha(ROOT/'installed-input-snapshot-manifest-v1.json'),
    pyproject_sha256=sha(REPO/'pyproject.toml')), indent=2)+'\\n')
"""+entry[end:]
entry = entry.replace("if mode == 'installed':\n    assert all(sha(test_root/name) == digest for name,digest in manifest.items())", "assert all(sha(test_root/name) == digest for name,digest in manifest.items())")
(ROOT/'entry.py').write_text(entry)
snapshot = ROOT/'installed-input-snapshot-v1'; snapshot.mkdir(exist_ok=False)
files = ('tests/test_stiffened_shell_b2_physical_footprint_consumer.py',
    'examples/stiffened_shell_b2_physical_footprint_consumer.py')
identities = {}
for name in files:
    dest = snapshot/name; dest.parent.mkdir(exist_ok=True); dest.write_bytes((REPO/name).read_bytes())
    identities[name] = sha(dest)
(ROOT/'installed-input-snapshot-manifest-v1.json').write_text(json.dumps(identities, indent=2)+'\n')
owned = ('src/anyfem/solve/stiffened_shell_b2.py','src/anyfem/solve/stiffened_shell_project.py',
    *files,'docs/experimental_b2_physical_rigid_footprint.md')
identities = {name:sha(REPO/name) for name in owned}
(ROOT/'candidate-source-shas.json').write_text(json.dumps(identities, indent=2)+'\n')
checkpoint = json.loads((ROOT/'checkpoint.json').read_text())
checkpoint.update(state='implemented; AST parsed; source impact child ready under root-reviewed contract',
    source_process_cap_seconds=min(12.,remaining-20.), inherited_used_seconds=600.-remaining,
    immutable_input_manifest='installed-input-snapshot-manifest-v1.json',
    exact_source_command="C:/Python/Python314/python.exe runner.py source v1", source_identity=identities)
(ROOT/'checkpoint.json').write_text(json.dumps(checkpoint, indent=2)+'\n')
print(json.dumps(dict(state=checkpoint['state'], cap_seconds=12., remaining_seconds=remaining)))
