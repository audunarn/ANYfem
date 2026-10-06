"""Versioned axial-only installed impact overlay; preserve V2 artifacts."""
from pathlib import Path
import hashlib, json
ROOT = Path(__file__).resolve().parent
sha = lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
protected = ('entry.py','runner.py','final-manifest.json','handoff.md',
    'installed-input-snapshot-manifest-v1.json','artifact-provenance-v1.json',
    'installed-selected-tests-v1.json')
identities = {name:sha(ROOT/name) for name in protected}
(ROOT/'protected-v2-identities-v3.json').write_text(json.dumps(identities, indent=2)+'\n')
runner = (ROOT/'runner.py').read_text()
runner = runner.replace("ROOT/'entry.py'", "ROOT/'entry-v3.py'")
runner = runner.replace("else:\n    command = [sys.executable, str(ROOT/'entry-v3.py'), MODE, VERSION]",
    "elif MODE == 'bookkeeping':\n    command = [sys.executable, str(ROOT/'finalize-v3.py')]\nelse:\n    command = [sys.executable, str(ROOT/'entry-v3.py'), MODE, VERSION]")
runner = runner.replace("remaining = ledger['total_seconds']-ledger['used_seconds']", """if not any(item.get('label') == 'final-v2-bookkeeping-envelope' for item in ledger['conservative_debits']):
    debit = dict(label='final-v2-bookkeeping-envelope', seconds=.3414474,
        kind='containing tool envelope; final bookkeeping child precise duration UNKNOWN',
        exact_command=\"& 'C:/Python/Python314/python.exe' .qualification/r8-native-bl5-mixed-j2-consumer-20261006-v1/finalize-handoff.py; git status --short --untracked-files=normal\",
        authority='root adopted conservative .3414474 second envelope; no rerun')
    ledger['conservative_debits'].append(debit); ledger['used_seconds'] += debit['seconds']
    ledger_path.write_text(json.dumps(ledger, indent=2)+'\\n')
remaining = ledger['total_seconds']-ledger['used_seconds']""")
(ROOT/'runner-v3.py').write_text(runner)
entry = (ROOT/'entry.py').read_text()
start = entry.index("if mode == 'installed':\n    tests = (")
end = entry.index("test_root = ", start)
entry = entry[:start]+"if mode == 'installed':\n    tests = ('test_stiffened_shell_native_bl5_mixed_j2_consumer.py::test_project_fixed_translation_material_restart[s3-axial-fibre-p0]',)\n"+entry[end:]
entry = entry.replace('installed-selected-tests-v1.json','installed-selected-tests-v3.json')
(ROOT/'entry-v3.py').write_text(entry)
assert all(sha(ROOT/name) == digest for name,digest in identities.items())
print(json.dumps(dict(protected_files=len(identities), runner_sha256=sha(ROOT/'runner-v3.py'),
    entry_sha256=sha(ROOT/'entry-v3.py'))))
