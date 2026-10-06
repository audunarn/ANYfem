from pathlib import Path
ROOT=Path(__file__).resolve().parent
runner=(ROOT/'runner-v4.py').read_text()
runner=runner.replace("else:\n    command = [sys.executable, str(ROOT/'entry-v4.py'), MODE, VERSION]",
    "elif MODE == 'bookkeeping':\n    command = [sys.executable, str(ROOT/'finalize.py')]\nelse:\n    command = [sys.executable, str(ROOT/'entry-v4.py'), MODE, VERSION]")
runner=runner.replace("elif MODE == 'installed':", "elif MODE == 'bookkeeping':\n    cap=remaining-8.\nelif MODE == 'installed':")
(ROOT/'runner-bookkeeping.py').write_text(runner)
