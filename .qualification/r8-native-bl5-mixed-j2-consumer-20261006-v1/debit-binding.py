from pathlib import Path
import json
ROOT = Path(__file__).resolve().parent
path = ROOT/'ledger.json'; ledger = json.loads(path.read_text())
assert not any(item.get('label') == 'pre-installed-byte-binding' for item in ledger['conservative_debits'])
debit = dict(label='pre-installed-byte-binding', seconds=.221211,
    kind='containing tool envelope; exact byte-only child duration UNKNOWN',
    exact_command="& 'C:/Python/Python314/python.exe' .qualification/r8-native-bl5-mixed-j2-consumer-20261006-v1/bind-installed.py",
    authority='root adopted .221211 second conservative envelope; no rerun')
ledger['conservative_debits'].append(debit); ledger['used_seconds'] += debit['seconds']
path.write_text(json.dumps(ledger, indent=2)+'\n')
print(json.dumps(dict(used_seconds=ledger['used_seconds'], remaining_seconds=600.-ledger['used_seconds'],
    installed_continuation_cap_seconds=min(90., 600.-ledger['used_seconds']-20.))))
