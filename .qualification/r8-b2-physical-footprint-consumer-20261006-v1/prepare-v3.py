from pathlib import Path
ROOT=Path(__file__).resolve().parent
source=(ROOT/'prepare-v2.py').read_text()
source=source.replace('entry-v2.py','entry-v3.py').replace('runner-v2.py','runner-v3.py')
source=source.replace('installed-input-snapshot-v2','installed-input-snapshot-v3').replace('installed-input-snapshot-manifest-v2','installed-input-snapshot-manifest-v3')
source=source.replace('candidate-source-shas-v2.json','candidate-source-shas-v3.json')
exec(compile(source, str(ROOT/'prepare-v2.py'), 'exec'))
