from pathlib import Path
import hashlib,json,subprocess
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[2]
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
paths=['.gitattributes','src/anyfem/solve/stiffened_shell.py','src/anyfem/solve/stiffened_shell_project.py',
 'tests/test_stiffened_shell_ge_finite_distributing_consumer.py',
 'examples/stiffened_shell_ge_finite_distributing_consumer.py',
 'docs/development/r8-ge-finite-distributing-consumer-20261004.md']
raw=[p for p in ROOT.rglob('*') if p.is_file() and not any(part in ('dist','site','__pycache__') or '-pytest-' in part for part in p.relative_to(ROOT).parts)]
record=dict(scope='Experimental finite consumer development, not scientific/release acceptance',
 preceding_commit=subprocess.check_output(['git','-c','safe.directory='+REPO.as_posix(),'rev-parse','HEAD'],cwd=REPO,text=True).strip(),
 source={p:sha(REPO/p) for p in paths},
 raw={p.relative_to(REPO).as_posix():dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted(raw)},
 wheels={p.name:dict(sha256=sha(p),bytes=p.stat().st_size) for p in (ROOT/'dist').glob('*.whl')})
(ROOT/'delivery-inventory-v1.json').open('x',encoding='utf-8').write(json.dumps(record,indent=2)+'\n')
raw.extend((ROOT/'delivery-inventory-v1.json',ROOT/'stage-paths-v1.txt'))
paths.extend(p.relative_to(REPO).as_posix() for p in sorted(raw))
(ROOT/'stage-paths-v1.txt').open('x',encoding='utf-8').write('\n'.join(paths)+'\n')
print(json.dumps(dict(paths=len(paths),evidence_bytes=sum(p.stat().st_size for p in raw),sites_wheels_pytest_excluded=True)))
