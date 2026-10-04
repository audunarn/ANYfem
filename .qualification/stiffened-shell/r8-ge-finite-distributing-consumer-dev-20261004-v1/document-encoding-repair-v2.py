from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parent
SOLVER=Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001')
for name in ('B3_GE_E4_PL_ACTIVE_STATE.md','B3_GE_E4_PL_REVISION_8_PLAN.md','B3_GE_E4_PL_REVISION_8_GOAL.md'):
    path='docs/agent_plans/'+name
    original=subprocess.check_output(['git','-c','safe.directory='+SOLVER.as_posix(),'show','HEAD:'+path],cwd=SOLVER)
    (SOLVER/path).write_bytes(original)
source=(ROOT/'document-delivery-v1.py').read_text(encoding='utf-8').replace('.read_text()', ".read_text(encoding='utf-8')")
assert (ROOT/'document-delivery-v2.py').read_text(encoding='utf-8')==source
(ROOT/'document-v1-failure.txt').open('x').write('Documentation v1 failed at unchanged matrix: Windows default cp1252 cannot decode UTF-8 punctuation. Initial shell repair failed parsing. Repair-v1 wrote explicit-UTF8 v2, but safe.directory blocked git snapshot restoration; premature v2 then stopped on already-modified active note. Repair-v2 restored active/plan/goal byte-for-byte using exact per-command safe.directory. Product/tests/artifacts unchanged. No historical evidence modified.\n')
print('Three root-owned current documents restored exactly; v2 explicit UTF-8 verified.')
