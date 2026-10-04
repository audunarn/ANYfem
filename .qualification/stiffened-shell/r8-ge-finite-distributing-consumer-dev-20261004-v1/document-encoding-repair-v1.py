from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parent
source=(ROOT/'document-delivery-v1.py').read_text(encoding='utf-8')
source=source.replace('.read_text()', ".read_text(encoding='utf-8')")
(ROOT/'document-delivery-v2.py').open('x',encoding='utf-8').write(source)
SOLVER=Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001')
for name in ('B3_GE_E4_PL_ACTIVE_STATE.md','B3_GE_E4_PL_REVISION_8_PLAN.md','B3_GE_E4_PL_REVISION_8_GOAL.md'):
    path='docs/agent_plans/'+name
    (SOLVER/path).write_bytes(subprocess.check_output(['git','show','HEAD:'+path],cwd=SOLVER))
(ROOT/'document-v1-failure.txt').open('x').write('Document update failed at unchanged acceptance matrix: Windows default cp1252 cannot decode UTF-8 punctuation. Active/plan/goal restored byte-for-byte from scoped current HEAD before v2 explicit UTF-8 update. Product/tests/artifacts unchanged. An initial shell repair failed parsing before executing; this script performs the repair.\n')
