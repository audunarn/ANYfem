from pathlib import Path
ROOT=Path(__file__).resolve().parent
source=(ROOT/'prepare-v2.py').read_text()
source=source.replace('entry-v2.py','entry-v4.py').replace('runner-v2.py','runner-v4.py')
source=source.replace('installed-input-snapshot-v2','installed-input-snapshot-v4').replace('installed-input-snapshot-manifest-v2','installed-input-snapshot-manifest-v4')
source=source.replace('candidate-source-shas-v2.json','candidate-source-shas-v4.json')
exec(compile(source, str(ROOT/'prepare-v2.py'), 'exec'))
runner=(ROOT/'runner-v4.py').read_text()
start=runner.index('cap = '); end=runner.index('if cap <= 0:', start)
runner=runner[:start]+"""if MODE in ('build','install'):
    prior=[attempt for attempt in ledger['attempts'] if Path(attempt['path']).name.startswith(('build-','install-'))]
    spent=sum(attempt['process_wall_seconds'] for attempt in prior)
    assert not any(Path(attempt['path']).name.startswith(MODE+'-') for attempt in prior), 'one build/install only'
    cap=min(6.5-spent, remaining-8.)
elif MODE == 'installed':
    cap=min(8., remaining-8.)
else:
    cap=min(12., remaining-20.)
"""+runner[end:]
(ROOT/'runner-v4.py').write_text(runner)
