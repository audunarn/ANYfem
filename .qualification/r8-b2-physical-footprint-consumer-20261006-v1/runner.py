"""Single-job bounded consumer development checks; exact shared wall ledger."""
from pathlib import Path
import hashlib, json, os, subprocess, sys, time
ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
SOLVER_RAW = Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001/.qualification/b3-ge-e4-pl/r8-native-fibre-bl5-mixed-j2-installed-dev-20261006-v1')
CI = SOLVER_RAW.parent/'r8-native-s3-restart-binding-dev-20261005-v1/ci-site-network-v2'
sys.path.insert(0, str(CI))
import psutil
MODE, VERSION = sys.argv[1:3]
ledger_path = ROOT/'ledger.json'
ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else dict(total_seconds=600., inherited_used_seconds=563.0746102000534, used_seconds=563.0746102000534, attempts=[])
remaining = ledger['total_seconds']-ledger['used_seconds']
cap = min(12., remaining-20.) if MODE == 'source' else min(90., remaining-20.)
if cap <= 0: raise RuntimeError('No authorized execution budget remains')
folder = ROOT/(MODE+'-'+VERSION); folder.mkdir(exist_ok=False)
env = os.environ.copy()
env.update(OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', OMP_NUM_THREADS='1',
    NUMEXPR_NUM_THREADS='1', NUMBA_NUM_THREADS='1', NUMBA_DISABLE_JIT='1',
    PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PIP_NO_INDEX='1')
env.pop('OPENBLAS_CORETYPE', None); env.pop('PYTHONOPTIMIZE', None)
fem = ROOT/'site-v1' if MODE == 'installed' else REPO/'src'
env['PYTHONPATH'] = os.pathsep.join((str(fem), str(SOLVER_RAW/'installed-host-v1'), str(CI)))
if MODE == 'build':
    command = [sys.executable, '-m', 'pip', 'wheel', str(REPO), '--no-deps', '--no-build-isolation', '--no-cache-dir', '--no-index', '-w', str(ROOT/'wheel-v1')]
elif MODE == 'install':
    command = [sys.executable, '-m', 'pip', 'install', '--no-deps', '--no-cache-dir', '--no-index', '--target', str(ROOT/'site-v1'), str(next((ROOT/'wheel-v1').glob('*.whl')))]
else:
    command = [sys.executable, str(ROOT/'entry.py'), MODE, VERSION]
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
candidate = {str(p.relative_to(REPO)).replace('\\','/'):sha(p) for p in
    (REPO/'src/anyfem/solve/stiffened_shell_b2.py', REPO/'src/anyfem/solve/stiffened_shell_project.py',
     REPO/'tests/test_stiffened_shell_b2_physical_footprint_consumer.py',
     REPO/'examples/stiffened_shell_b2_physical_footprint_consumer.py')}
failure = None; peak = 0; samples = []; code = None
started = time.perf_counter()
with (folder/'stdout.log').open('x') as out, (folder/'stderr.log').open('x') as err:
    child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=out, stderr=err, creationflags=4)
    parent = psutil.Process(child.pid); tree = [parent]
    try:
        parent.cpu_affinity([0]); assert parent.cpu_affinity() == [0]
        parent.resume()
        while child.poll() is None:
            try:
                tree = [parent]+parent.children(recursive=True)
                rss = sum(p.memory_info().rss for p in tree if p.is_running())
                peak = max(peak, rss)
                samples.append(dict(seconds=time.perf_counter()-started, tree_rss=rss))
                if any(p.cpu_affinity() != [0] for p in tree if p.is_running()): failure = 'affinity changed'
            except psutil.NoSuchProcess: pass
            if time.perf_counter()-started >= cap: failure = 'timeout'
            if peak > 2*1024**3: failure = 'memory ceiling'
            if failure: break
            time.sleep(.02)
    except BaseException as error: failure = type(error).__name__+': '+str(error)
    finally:
        if child.poll() is None:
            for process in reversed(tree):
                try: process.kill()
                except psutil.NoSuchProcess: pass
        code = child.wait(timeout=5)
elapsed = time.perf_counter()-started
ledger['used_seconds'] += elapsed
receipt = dict(status=failure or ('completed' if code == 0 else 'failed'), exit_code=code,
    command=command, cwd=str(ROOT), process_wall_seconds=elapsed, seconds_limit=cap,
    cumulative_used_seconds=ledger['used_seconds'], remaining_seconds=600.-ledger['used_seconds'],
    sampled_peak_tree_rss=peak, memory_limit_bytes=2*1024**3, affinity=[0],
    environment={key:env.get(key) for key in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
        'OMP_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','NUMBA_DISABLE_JIT','OPENBLAS_CORETYPE')},
    candidate=candidate, runner_sha256=sha(__file__), entry_sha256=sha(ROOT/'entry.py'))
ledger['attempts'].append(dict(path=str(folder), **receipt))
(folder/'execution.json').write_text(json.dumps(receipt, indent=2)+'\n')
(folder/'rss-samples.json').write_text(json.dumps(samples)+'\n')
ledger_path.write_text(json.dumps(ledger, indent=2)+'\n')
print(json.dumps({key:receipt[key] for key in ('status','exit_code','process_wall_seconds','cumulative_used_seconds','remaining_seconds','sampled_peak_tree_rss')}))
if failure or code: raise SystemExit(1)
