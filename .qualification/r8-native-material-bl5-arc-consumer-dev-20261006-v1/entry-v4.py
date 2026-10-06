"""Installed consumer impact entry; only an approved native supervisor may launch."""
from pathlib import Path
import hashlib, json, os, sys, threading, time

RAW = Path(__file__).resolve().parent
authority = json.loads((RAW/'authority.json').read_text())
if authority['status'] != 'APPROVED' or not authority.get('approval_evidence'):
    raise SystemExit('PENDING_USER_APPROVAL: interpreter entry refuses execution')
ATTEMPT = sys.argv[1]
if not ATTEMPT or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in ATTEMPT):
    raise SystemExit('Invalid attempt identity')
FOLDER = RAW/ATTEMPT
if not FOLDER.is_dir() or not (RAW/'active.lock').is_file():
    raise SystemExit('Native supervised attempt required')
MANIFEST = json.loads((RAW/'frozen-manifest-v4.json').read_text())
SNAPSHOT = RAW/'snapshot-v4'
CI = Path(MANIFEST['ci'])
sys.dont_write_bytecode = True
sys.path[:0] = [str(CI)]
import psutil
process = psutil.Process()
initial_affinity = process.cpu_affinity()
process.cpu_affinity([0])
assert process.cpu_affinity() == [0]
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(name, value):
    (FOLDER/name).write_text(json.dumps(value, indent=2)+'\n')
write('startup.json', dict(pid=process.pid, initial_affinity=initial_affinity,
    pre_numerical_affinity=process.cpu_affinity(), executable=sys.executable,
    psutil_path=psutil.__file__, psutil_sha256=sha(psutil.__file__)))
ready = threading.Event()
stop = threading.Event()
started = time.perf_counter()
samples = []; transitions = []
def monitor():
    while not stop.is_set():
        try:
            tree = [process]+process.children(recursive=True)
            live = [p for p in tree if p.is_running()]
            sample = dict(seconds=time.perf_counter()-started,
                rss=sum(p.memory_info().rss for p in live),
                processes=[dict(pid=p.pid, name=p.name(), executable=p.exe(), affinity=p.cpu_affinity()) for p in live],
                numerical_ready=ready.is_set())
            samples.append(sample)
            with (FOLDER/'resource-samples.jsonl').open('a') as stream:
                stream.write(json.dumps(sample)+'\n'); stream.flush()
            for p in live:
                before = p.cpu_affinity()
                if before != [0]:
                    event = dict(pid=p.pid, name=p.name(), executable=p.exe(), before=before,
                        seconds=sample['seconds'], numerical_ready=ready.is_set())
                    transitions.append(event); write('affinity-transitions.json', transitions)
                    # Only startup children may be pinned; after imports begin,
                    # conhost is the explicitly recorded nonnumerical exception.
                    if ready.is_set() and p.name().lower() != 'conhost.exe':
                        raise RuntimeError('Numerical process affinity changed')
                    p.cpu_affinity([0])
            if sample['rss'] > authority['memory_bytes']:
                raise RuntimeError('2GiB process-tree RSS ceiling exceeded')
        except psutil.NoSuchProcess:
            pass
        except BaseException as error:
            write('watchdog-failure.json', dict(error=repr(error), samples=len(samples)))
            kill_errors = []
            try:
                try:
                    children = process.children(recursive=True)
                except BaseException as failure:
                    children = []; kill_errors.append(dict(action='enumerate', error=repr(failure)))
                for p in reversed(children):
                    try: p.kill()
                    except BaseException as failure:
                        kill_errors.append(dict(action='child_kill', pid=p.pid, error=repr(failure)))
            finally:
                # Child disappearance/access errors must never skip parent kill.
                try: write('watchdog-kill-errors.json', kill_errors)
                finally:
                    try: process.kill()
                    except BaseException as failure:
                        kill_errors.append(dict(action='parent_kill', error=repr(failure)))
                        write('watchdog-kill-errors.json', kill_errors)
        stop.wait(.05)
thread = threading.Thread(target=monitor, daemon=True); thread.start()

def validate_inputs():
    for name, row in MANIFEST['inputs'].items():
        assert sha(SNAPSHOT/name) == row['sha256'], name
        assert sha(row['source']) == row['sha256'], 'Source drift: '+name
    for row in MANIFEST['artifacts'].values():
        assert sha(row['wheel']) == row['sha256']
        for name, digest in row['payload'].items():
            assert sha(Path(row['site'])/name) == digest, name
    for name, row in MANIFEST['dependencies'].items():
        assert sha(row['path']) == row['sha256'], name

def loaded():
    return {name:dict(path=str(Path(m.__file__).resolve()), sha256=sha(m.__file__))
        for name,m in list(sys.modules.items())
        if getattr(m,'__file__',None) and Path(m.__file__).is_file()}

def verify_loaded(rows):
    for name, row in rows.items():
        path = Path(row['path'])
        package = name.split('.')[0]
        if package in ('anyfem','anysolver'):
            binding = MANIFEST['artifacts'][package]; site = Path(binding['site']).resolve()
            assert path.is_relative_to(site), 'Forbidden product source origin: '+name
            assert row['sha256'] == binding['payload'][path.relative_to(site).as_posix()], name
        elif name in MANIFEST['dependencies']:
            binding = MANIFEST['dependencies'][name]
            assert path == Path(binding['path']).resolve() and row['sha256'] == binding['sha256'], name
        elif package.lower().startswith('any'):
            raise AssertionError('Unbound sibling module: '+name)
        elif package in ('numpy','scipy','numba','llvmlite','h5py','pytest','_pytest','psutil','threadpoolctl'):
            assert path.is_relative_to(CI.resolve()), name

try:
    validate_inputs()
    write('preflight-bindings.json', dict(manifest_sha256=sha(RAW/'frozen-manifest-v4.json'),
        inputs=MANIFEST['inputs'], artifacts=MANIFEST['artifacts'], dependency_count=len(MANIFEST['dependencies'])))
    sys.path[:0] = [MANIFEST['artifacts'][p]['site'] for p in ('anyfem','anysolver')]+MANIFEST['readonly_source_roots']+[str(SNAPSHOT)]
    # Set readiness before ANY numerical/product import: no watchdog repinning
    # can conceal effective affinity changes during imported numerical work.
    ready.set()
    import anyfem, anysolver, anymaterial, numpy, scipy.linalg, numba, llvmlite
    from threadpoolctl import threadpool_info
    def runtime():
        return dict(affinity=process.cpu_affinity(), pools=threadpool_info(),
            numba_threads=numba.get_num_threads(), jit_off=bool(numba.config.DISABLE_JIT),
            versions={m.__name__:getattr(m,'__version__',None) for m in (anyfem,anysolver,numpy,scipy,numba,llvmlite)},
            environment={k:os.environ.get(k) for k in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','OMP_NUM_THREADS','NUMEXPR_NUM_THREADS','NUMBA_NUM_THREADS','NUMBA_DISABLE_JIT','OPENBLAS_CORETYPE')})
    def verify_runtime(value):
        assert value['affinity']==[0] and value['numba_threads']==1 and value['jit_off']
        assert all(p['num_threads']==1 for p in value['pools'])
        assert value['environment']['OPENBLAS_CORETYPE'] is None
        assert all(value['environment'][k]=='1' for k in value['environment'] if k!='OPENBLAS_CORETYPE')
        assert value['versions']==dict(anyfem='0.4.1',anysolver='0.4.7',numpy='2.5.3',scipy='1.18.1',numba='0.68.0',llvmlite='0.50.0')
    before=runtime();write('runtime-before.json',before);verify_runtime(before)
    imports=loaded();write('loaded-before.json',imports);verify_loaded(imports)
    selectors=json.loads((RAW/'selectors.json').read_text())
    expected=['tests/test_stiffened_shell_native_material_bl5_arc_consumer.py::test_project_native_material_arc_prefix_cancel_restore_continue',
        'tests/test_stiffened_shell_native_material_bl5_arc_consumer.py::test_existing_v6_project_ownership_and_strict_input_refusals']
    assert selectors==expected
    import pytest
    arguments=['-q','-c',str(SNAPSHOT/'pyproject.toml'),'-o','pythonpath=',
        '--basetemp',str(FOLDER/'temp'),'--junitxml='+str(FOLDER/'junit.xml')]
    arguments += [str(SNAPSHOT/s.split('::')[0])+'::'+s.split('::')[1] for s in selectors]
    write('pytest-command.json',dict(arguments=arguments, selectors=selectors, config_sha256=sha(SNAPSHOT/'pyproject.toml')))
    class Reports:
        def pytest_runtest_logreport(self,report):
            with (FOLDER/'case-reports.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(nodeid=report.nodeid, when=report.when,
                    outcome=report.outcome, duration=report.duration,
                    detail=str(report.longrepr) if report.failed else None))+'\n');stream.flush()
    code=pytest.main(arguments,plugins=[Reports()])
    after=runtime();write('runtime-after.json',after);verify_runtime(after)
    imports=loaded();write('loaded-after.json',imports);verify_loaded(imports)
    validate_inputs()
    write('post-audit.json',dict(status='passed', pytest_exit_code=code,
        input_count=len(MANIFEST['inputs']), loaded_files=len(imports),
        full_diagnostics='workflow JSON, actual solver checkpoints and Project are inside temp/'))
    raise SystemExit(code)
finally:
    stop.set();thread.join(timeout=1)
    write('resource-summary.json',dict(peak_tree_rss=max((s['rss'] for s in samples),default=None),
        samples=len(samples), affinity_transitions=transitions,
        timeout_evidence='Partial JSONL survives tree kill; reported peak is sampled, not continuous'))
