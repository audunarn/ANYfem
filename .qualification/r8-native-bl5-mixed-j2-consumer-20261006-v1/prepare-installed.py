"""Preserve the adopted accounting uncertainty and freeze installed check inputs."""
from pathlib import Path
import hashlib, json, shutil
ROOT = Path(__file__).resolve().parent; REPO = ROOT.parents[1]
path = ROOT/'ledger.json'; ledger = json.loads(path.read_text())
assert not ledger.get('conservative_debits')
debit = dict(kind='containing shell envelope; replay child precise timing UNKNOWN',
    seconds=5.2350776, containing_tool_wall_time_seconds=5.2350776,
    includes_already_accounted_source_v2_seconds=4.935120200039819,
    exact_command="& 'C:/Python/Python314/python.exe' .qualification/r8-native-bl5-mixed-j2-consumer-20261006-v1/runner.py source v2; & 'C:/Python/Python314/python.exe' .qualification/r8-native-bl5-mixed-j2-consumer-20261006-v1/replay-source-v1.py",
    authority='root adopted conservative envelope; deliberate V2 double count; no fresh numerical run')
ledger['conservative_debits'] = [debit]
ledger['used_seconds'] += debit['seconds']
assert abs(600.-ledger['used_seconds']-107.63688739994025) < 1e-9
path.write_text(json.dumps(ledger, indent=2)+'\n')
snapshot = ROOT/'installed-input-snapshot-v1'; snapshot.mkdir(exist_ok=False)
files = ('tests/test_stiffened_shell_native_bl5_mixed_j2_consumer.py',
    'tests/test_stiffened_shell_native_fibre_bl5_consumer.py',
    'tests/test_bl5_material_consumer.py', 'tests/test_bl5_coupled_material_consumer.py',
    'tests/test_bl5_project.py', 'tests/test_experimental_stiffened_shell_consumer.py',
    'examples/stiffened_shell_native_fibre_bl5_mixed_j2_consumer.py',
    'examples/stiffened_shell_native_fibre_bl5_consumer.py')
sha = lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
identities = {}
for relative in files:
    dest = snapshot/relative; dest.parent.mkdir(exist_ok=True)
    shutil.copyfile(REPO/relative, dest)
    identities[relative] = sha(dest)
    assert sha(REPO/relative) == identities[relative]
(ROOT/'installed-input-snapshot-manifest-v1.json').write_text(json.dumps(identities, indent=2)+'\n')
print(json.dumps(dict(remaining_seconds=600.-ledger['used_seconds'], snapshot_files=len(identities))))
