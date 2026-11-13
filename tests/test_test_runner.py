"""Execution boundaries and evidence integrity for the development runner."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("test_anyfem_runner", ROOT / "tools/test_anyfem.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

def _completed_receipt(log, environment):
    import xml.etree.ElementTree as ET
    from tools.test_suite_plan import manifest
    if 'ANYFEM_SUITE_PROFILE' not in environment:return
    profile=environment['ANYFEM_SUITE_PROFILE']
    existing=(ET.parse(log.with_name('pytest.xml')).find('testsuite')
              if log.with_name('pytest.xml').exists() else ET.Element('testsuite',tests='1'))
    count=int(existing.get('tests','1'));skipped=int(existing.get('skipped','0'))
    node=('tests/test_qt_workbench.py::test_real_qt_mesh_solve_save_reopen' if profile in {'quick','full','qt'} else 'tests/test_license_policy.py::test_synthetic')
    data=manifest([node] if count else [],'full')
    data.update(profile=profile,suite_root=str(ROOT.resolve()),collection_owner_pid=os.getpid(),collection_errors=0,collection_skips=[])
    log.with_name('suite-collected.json').write_text(json.dumps(data))
    root=ET.Element('testsuites');suite=ET.SubElement(root,'testsuite',tests=str(count),failures='0',errors='0',skipped=str(skipped))
    path,name=node.split('::');case=ET.SubElement(suite,'testcase',classname=path[:-3].replace('/','.'),name=name)
    props=ET.SubElement(case,'properties');ET.SubElement(props,'property',name='anyfem_original_nodeid',value=node)
    if skipped:ET.SubElement(case,'skipped')
    ET.ElementTree(root).write(log.with_name('pytest.xml'))




def test_deadline_retires_real_owned_tree_and_preserves_partial_output(tmp_path):
    log = tmp_path / 'pytest.log'
    command = [sys.executable, '-c',
               "import subprocess,sys,time;print('partial-before-timeout',flush=True);"
               "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);time.sleep(60)"]
    start = time.monotonic()
    with pytest.raises(TimeoutError, match='deadline'):
        runner.execute(command, log, dict(os.environ, ANYFEM_TEST_TIMEOUT_SECONDS='1'))
    assert time.monotonic() - start < 15
    assert 'partial-before-timeout' in log.read_text()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX inherited pipe regression')
def test_exited_posix_parent_retires_descendant_holding_output(tmp_path):
    start = time.monotonic()
    command = [sys.executable, '-c',
               "import subprocess,sys;print('parent-exited',flush=True);"
               "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])"]
    assert runner.execute(command, tmp_path/'pytest.log', dict(os.environ, ANYFEM_TEST_TIMEOUT_SECONDS='10')) == 0
    assert time.monotonic()-start < 5


@pytest.mark.parametrize('damage', ['missing','hash','union','duplicate','qt_skip'])
def test_passing_exit_cannot_hide_incomplete_receipt_or_qt(tmp_path, damage):
    log=tmp_path/'pytest.log';_completed_receipt(log, {'ANYFEM_SUITE_PROFILE':'quick'})
    assert runner.validate_run_receipt(tmp_path,'quick')['executed']==1
    receipt=tmp_path/'suite-collected.json'
    if damage=='missing':receipt.unlink()
    elif damage=='hash':
        data=json.loads(receipt.read_text());data['inventory_sha256']='bad';receipt.write_text(json.dumps(data))
    else:
        import xml.etree.ElementTree as ET
        tree=ET.parse(tmp_path/'pytest.xml');case=next(tree.iter('testcase'))
        if damage=='union':case.find('properties/property').set('value','tests/test_unknown.py::test_other')
        elif damage=='duplicate':
            import copy
            tree.find('testsuite').append(copy.deepcopy(case))
        else:ET.SubElement(case,'skipped')
        tree.write(tmp_path/'pytest.xml')
    with pytest.raises((RuntimeError,OSError)):
        runner.validate_run_receipt(tmp_path,'quick')


def test_associated_subtest_rows_preserve_original_union_and_reject_replays(tmp_path):
    import copy
    import xml.etree.ElementTree as ET
    _completed_receipt(tmp_path/'pytest.log',{'ANYFEM_SUITE_PROFILE':'unit'})
    tree=ET.parse(tmp_path/'pytest.xml');case=next(tree.iter('testcase'))
    child=copy.deepcopy(case);child.set('name',case.get('name')+' (x=1)');tree.find('testsuite').append(child)
    tree.write(tmp_path/'pytest.xml')
    node=case.find('properties/property').get('value')
    (tmp_path/'suite-subtests.json').write_text(json.dumps([dict(nodeid=node,description='(x=1)',ordinal=0,outcome='passed')]))
    assert runner.validate_run_receipt(tmp_path,'unit')['selected']==1
    tree.find('testsuite').append(copy.deepcopy(child));tree.write(tmp_path/'pytest.xml')
    with pytest.raises(RuntimeError,match='duplicate associated subtest'):
        runner.validate_run_receipt(tmp_path,'unit')


def test_owned_windows_spawn_waits_for_assignment_before_go(monkeypatch, tmp_path):
    from tools import _owned_test_process as owned
    calls = []
    class Process:
        _handle = 123
        pid = 456
        stdout = None
        def kill(self): calls.append("kill")
        def wait(self, timeout=None): return 0
    monkeypatch.setattr(owned, "_create_kill_on_close_job", lambda: "job")
    def assign(job, process):
        assert not list(tmp_path.rglob("GO"))
        calls.append((job, process._handle))
    monkeypatch.setattr(owned, "_assign_owned_process", assign)
    monkeypatch.setattr(owned.subprocess, "Popen", lambda argv, **kw: calls.append(argv) or Process())
    replace = owned.os.replace
    def publish(pending, gate):
        # The actual bootstrap argv pins this fixture's one GO destination.
        if not calls or Path(gate) != Path(calls[0][5]):
            return replace(pending, gate)
        assert pending.name == "GO.pending" and pending.read_text() == "GO"
        assert not gate.exists(), "GO must not be visible before its complete contents"
        calls.append("publish complete GO")
        replace(pending, gate)
    monkeypatch.setattr(owned.os, "replace", publish)
    process = owned.start_windows_process([sys.executable, "-c", "pass"],
                                         cwd=ROOT, env=os.environ.copy(), gate_root=tmp_path)
    assert calls[0][1:3] == ["-I", "-S"]
    assert calls[1] == ("job", 123)
    assert calls[2] == "publish complete GO"
    assert process._gate.exists()
    # This unit uses no native Job; disposal is covered by real-child controls.
    process._directory.cleanup()


def test_owned_windows_assignment_failure_never_releases_go(monkeypatch, tmp_path):
    from tools import _owned_test_process as owned
    calls = []
    class Process:
        _handle = 123
        pid = 456
        def kill(self): calls.append("kill")
        def wait(self, timeout=None): calls.append(("wait", timeout)); return 1
    monkeypatch.setattr(owned, "_create_kill_on_close_job", lambda: "job")
    def refuse(*args): raise OSError("assignment refused")
    monkeypatch.setattr(owned, "_assign_owned_process", refuse)
    monkeypatch.setattr(owned, "_retire_job", lambda job: calls.append(("retire", job)))
    monkeypatch.setattr(owned.subprocess, "Popen", lambda argv, **kw: Process())
    with pytest.raises(OSError, match="assignment refused"):
        owned.start_windows_process([sys.executable, "-c", "pass"],
                                    cwd=ROOT, env=os.environ.copy(), gate_root=tmp_path)
    assert "kill" in calls and ("wait", 5) in calls
    assert not list(tmp_path.rglob("GO"))


@pytest.mark.parametrize("exit_code", [0, 7])
def test_owned_bootstrap_preserves_real_exit_environment_and_cwd(tmp_path, exit_code):
    record = tmp_path / "child.json"
    command = [sys.executable, "-c",
               "import os,sys,json;from pathlib import Path;"
               "Path(sys.argv[1]).write_text(json.dumps([os.getcwd(),os.environ['OWNED_SENTINEL']]));"
               "sys.exit(int(sys.argv[2]))", str(record), str(exit_code)]
    env = dict(os.environ, OWNED_SENTINEL="unchanged child environment")
    process = runner.start_owned_process(command, cwd=tmp_path, env=env,
                                         gate_root=tmp_path, start_new_session=os.name != "nt")
    try:
        assert process.wait(timeout=10) == exit_code
        assert json.loads(record.read_text()) == [str(tmp_path), env["OWNED_SENTINEL"]]
    finally:
        runner.stop_owned_process(process)


def test_owned_bootstrap_go_deadline_never_launches_child(tmp_path):
    from tools import _owned_test_process as owned
    marker = tmp_path / "must-not-exist"
    (tmp_path / "GO.pending").write_text("G")
    command = [sys.executable, "-c", "from pathlib import Path;import sys;Path(sys.argv[1]).touch()", str(marker)]
    result = subprocess.run([sys.executable, "-I", "-S", str(Path(owned.__file__)),
                             "--bootstrap", str(tmp_path / "GO"), "0.05", json.dumps(command)],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 125
    assert "GO deadline expired" in result.stderr
    assert not marker.exists()


def test_owned_job_query_failure_is_error_and_close_backstop(monkeypatch):
    from tools import _owned_test_process as owned
    calls = []
    kernel = SimpleNamespace(TerminateJobObject=lambda *args: calls.append("terminate") or True,
                             CloseHandle=lambda *args: calls.append("close") or True)
    monkeypatch.setattr(owned, "_windows_kernel32", lambda: kernel)
    def fail(job): calls.append("query"); raise OSError("query failed")
    monkeypatch.setattr(owned, "_windows_job_active_processes", fail)
    with pytest.raises(OSError, match="query failed"):
        owned._retire_job("owned job")
    assert calls == ["terminate", "query", "close"]


def test_owned_job_checks_empty_before_closing(monkeypatch):
    from tools import _owned_test_process as owned
    calls = []
    counts = iter([2, 1, 0])
    kernel = SimpleNamespace(TerminateJobObject=lambda *args: calls.append("terminate") or True,
                             CloseHandle=lambda *args: calls.append("close") or True)
    monkeypatch.setattr(owned, "_windows_kernel32", lambda: kernel)
    monkeypatch.setattr(owned, "_windows_job_active_processes", lambda job: calls.append("query") or next(counts))
    monkeypatch.setattr(owned.time, "sleep", lambda delay: None)
    owned._retire_job("owned job")
    assert calls == ["terminate", "query", "query", "query", "close"]


def test_owned_job_nonempty_deadline_is_error(monkeypatch):
    from tools import _owned_test_process as owned
    calls = []
    ticks = iter([0, 6])
    monkeypatch.setattr(owned, "_windows_kernel32", lambda: SimpleNamespace(
        TerminateJobObject=lambda *args: True,
        CloseHandle=lambda *args: calls.append("close") or True))
    monkeypatch.setattr(owned, "_windows_job_active_processes", lambda job: 1)
    monkeypatch.setattr(owned.time, "monotonic", lambda: next(ticks))
    with pytest.raises(RuntimeError, match="did not become empty"):
        owned._retire_job("owned job")
    assert calls == ["close"]


def test_owned_job_primitives_match_application_owner_source():
    source = ast.parse((ROOT / "src/anyfem/job_process.py").read_text(encoding="utf-8-sig"))
    helper = ast.parse((ROOT / "tools/_owned_test_process.py").read_text())
    names = {"_windows_kernel32", "_create_kill_on_close_job", "_windows_job_active_processes"}
    original = {node.name: ast.dump(node, include_attributes=False) for node in source.body
                if isinstance(node, ast.FunctionDef) and node.name in names}
    copied = {node.name: ast.dump(node, include_attributes=False) for node in helper.body
              if isinstance(node, ast.FunctionDef) and node.name in names}
    assert original == copied and set(copied) == names


def test_windows_interrupt_retires_job_and_preserves_exception(monkeypatch, tmp_path):
    owner = runner._process_owner()
    calls = []
    original = KeyboardInterrupt("actual interrupted stream")
    class Output:
        def __iter__(self): raise original
        def close(self): calls.append("stdout closed")
    directory = SimpleNamespace(cleanup=lambda: calls.append("gate removed"))
    popen = SimpleNamespace(pid=123, stdout=Output(), poll=lambda: None,
                            wait=lambda timeout: calls.append("wait"))
    process = owner.WindowsOwnedProcess(popen, "retained Job", directory, None, ["bootstrap"])
    monkeypatch.setattr(owner, "_retire_job", lambda job: calls.append(("retire", job)))
    monkeypatch.setattr(runner, "start_owned_process", lambda *args, **kw: process)
    if os.name != "nt":
        # This is a Windows adapter unit with no native resource on POSIX.
        process.stop()
        assert calls == [("retire", "retained Job"), "wait", "gate removed"]
    else:
        with pytest.raises(KeyboardInterrupt) as caught:
            runner.execute(["logical pytest"], tmp_path / "pytest.log", {})
        assert caught.value is original
        assert calls == [("retire", "retained Job"), "wait", "gate removed", "stdout closed"]


def test_owned_bootstrap_real_command_failure_keeps_logical_argv(tmp_path):
    command = [sys.executable, "-c", "print('owned scalar output');raise SystemExit(7)"]
    assert runner.execute(command, tmp_path / "pytest.log", os.environ.copy()) == 7
    assert "owned scalar output" in (tmp_path / "pytest.log").read_text()
    if os.name == "nt":
        record = json.loads((tmp_path / "pytest-process.json").read_text())
        assert record["logical_command"] == command
        actual = record["bootstrap_command"]
        assert actual[:3] == [sys.executable, "-I", "-S"]
        assert json.loads(actual[-1]) == command
        assert record["ownership"] == "retained-kill-on-close-job-before-go"


def test_owned_output_propagates_original_read_exception_after_queued_lines():
    from tools import _owned_test_process as owned
    original = UnicodeError("actual read failure")
    calls = []
    class Output:
        def __iter__(self):
            yield "already read line\n"
            raise original
    process = SimpleNamespace(stdout=Output(), poll=lambda: None,
                              stop=lambda: calls.append("retired"))
    output = owned.owned_output(process)
    assert next(output) == "already read line\n"
    with pytest.raises(UnicodeError) as caught:
        next(output)
    assert caught.value is original
    assert calls == ["retired"]


def test_owned_output_eof_does_not_terminate_running_command():
    from tools import _owned_test_process as owned
    process = SimpleNamespace(stdout=iter(["last line\n"]), poll=lambda: None,
                              stop=lambda: pytest.fail("EOF alone cannot stop the command"))
    assert list(owned.owned_output(process)) == ["last line\n"]


def test_owned_output_uncertain_reader_never_enters_blocking_close():
    from tools import _owned_test_process as owned
    calls = []
    process = SimpleNamespace(_output_reader=SimpleNamespace(is_alive=lambda: True),
                              stdout=SimpleNamespace(close=lambda: pytest.fail("blocking close entered")))
    output = SimpleNamespace(close=lambda: calls.append("generator closed"))
    with pytest.raises(RuntimeError, match="reader remains active"):
        owned.close_owned_output(process, output)
    assert calls == ["generator closed"]


def test_owned_command_exit_retires_descendant_holding_stdout(tmp_path):
    marker = tmp_path / "descendant.pid"
    command = [sys.executable, "-c",
               "import subprocess,sys;from pathlib import Path;"
               "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
               "Path(sys.argv[1]).write_text(str(child.pid));print('retained before exit',flush=True)",
               str(marker)]
    if os.name != "nt":
        # The reader/Job integration is Windows-only; exercise its actual
        # output behavior below without creating an uncontained POSIX child.
        from tools import _owned_test_process as owned
        calls = []
        process = SimpleNamespace(stdout=iter(["retained before exit\n"]), poll=lambda: 0,
                                  stop=lambda: calls.append("retire"))
        assert list(owned.owned_output(process)) == ["retained before exit\n"]
        assert calls
        return
    started = time.monotonic()
    assert runner.execute(command, tmp_path / "pytest.log", os.environ.copy()) == 0
    assert time.monotonic() - started < 10, "descendant's 60-second pipe held the runner"
    assert marker.exists()
    assert "retained before exit" in (tmp_path / "pytest.log").read_text()


@pytest.mark.parametrize("selector", ["--help", "../tests/x.py", "tests/../x.py",
                                      "tests/a/../../x.py", "tests/..\\x.py"])
def test_focused_refuses_outside_paths(selector):
    with pytest.raises(ValueError):
        runner.selectors("focused", [selector])


def test_full_cannot_be_narrowed_and_focused_requires_scope():
    for profile, paths in [("full", ["tests/test_io.py"]), ("focused", [])]:
        with pytest.raises(ValueError):
            runner.selectors(profile, paths)
    assert runner.selectors("focused", ["tests/test_io.py::test_example"]) == ["tests/test_io.py::test_example"]
    assert all((ROOT / path.split("::",1)[0]).is_file() for path in runner.selectors("quick", []))
    assert "tests/test_qt_activation_readiness.py" in runner.selectors("qt", [])


def test_dry_run_neither_imports_owners_nor_executes(monkeypatch, tmp_path, capsys):
    def forbidden(*args):
        pytest.fail("dry run executed work")
    monkeypatch.setattr(runner, "owner_capabilities", forbidden)
    monkeypatch.setattr(runner, "execute", forbidden)
    output = tmp_path / "evidence"
    assert runner.main(["--profile", "full", "--dry-run", "--out", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["acceptance"] == "not established"
    assert not output.exists()


def test_missing_owner_blocks_before_pytest(monkeypatch, tmp_path):
    monkeypatch.setattr(runner.importlib, "import_module", lambda name: SimpleNamespace())
    monkeypatch.setattr(runner, "execute", lambda *args: pytest.fail("pytest must not start"))
    output = tmp_path / "blocked"
    assert runner.main(["--profile", "full", "--out", str(output)]) == 2
    report = json.loads((output / "run.json").read_text())
    assert report["status"] == "blocked"
    assert "anygeometry.plan_intersections" in report["error"]
    assert not (output / "pytest.log").exists()


def test_existing_evidence_is_preserved(tmp_path):
    sentinel = tmp_path / "original.json"
    sentinel.write_text("original")
    with pytest.raises(SystemExit):
        runner.main(["--out", str(tmp_path)])
    assert sentinel.read_text() == "original"


def test_inherited_pytest_selection_cannot_narrow_a_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k only_one_test --collect-only")
    def execute(command, log, environment):
        assert "PYTEST_ADDOPTS" not in environment
        log.write_text("run")
        log.with_name("pytest.xml").write_text('<testsuites><testsuite tests="1"/></testsuites>')
        _completed_receipt(log, environment)
        return 0
    monkeypatch.setattr(runner, "execute", execute)
    output = tmp_path / "run"
    assert runner.main(["--out", str(output)]) == 0
    assert json.loads((output / "run.json").read_text())["ignored_pytest_addopts"] == "-k only_one_test --collect-only"


@pytest.mark.parametrize("mutation", ["empty", "partial", "duplicate", "failure", "counts"])
def test_full_rejects_unusable_scientific_evidence(tmp_path, mutation):
    data = {"status": "passed", "counts": {"total": 2, "passed": 2, "failed": 0},
            "results": [{"case_id": item, "status": "passed"} for item in ("A", "B")]}
    if mutation == "empty":
        data["results"] = []
    elif mutation == "partial":
        data["results"].pop()
    elif mutation == "duplicate":
        data["results"][1]["case_id"] = "A"
    elif mutation == "failure":
        data["results"][1]["status"] = "failed"
    else:
        data["counts"]["passed"] = 1
    path = tmp_path / "verification.json"
    path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError):
        runner.validate_verification(path, {"A", "B"})


def _stub_completed_full_run(monkeypatch, execute):
    """Supply completed pytest artifacts for tests of main's final evidence checks."""
    def completed_caller_phases(command, output, environment, report):
        return execute(command, output / "pytest.log", environment)

    monkeypatch.setattr(runner, "execute", execute)
    monkeypatch.setattr(runner, "_execute_caller_phases", completed_caller_phases)


@pytest.mark.parametrize("data", [[], {"results": [None]}, {"results": [{"case_id": []}]},
                                  {"results": None}])
def test_malformed_evidence_is_recorded_as_failure(monkeypatch, tmp_path, data):
    from anyfem import verification
    monkeypatch.setattr(runner, "owner_capabilities", lambda: None)
    monkeypatch.setattr(verification, "cases", lambda: [SimpleNamespace(case_id="A")])
    def execute(command, log, environment):
        log.write_text("pytest passed")
        log.with_name("pytest.xml").write_text('<testsuites><testsuite tests="1"/></testsuites>')
        report_dir = Path(environment["ANYFEM_VERIFICATION_OUT"])
        report_dir.mkdir()
        (report_dir / "verification.json").write_text(json.dumps(data))
        _completed_receipt(log, environment)
        return 0
    _stub_completed_full_run(monkeypatch, execute)
    output = tmp_path / "run"
    assert runner.main(["--profile", "qualification", "--out", str(output)]) == 2
    report = json.loads((output / "run.json").read_text())
    assert report["status"] == "failed"
    assert report["exit_code"] == 2
    assert report["error"].startswith("RuntimeError: verification ")


def test_interruption_cleanup_escalates_with_bounded_wait(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(runner.signal, "SIGKILL", 9, raising=False)
    class Output:
        def __iter__(self):
            raise KeyboardInterrupt
        def close(self):
            calls.append("close")
    class Process:
        pid = 123456
        stdout = Output()
        def kill(self):
            calls.append("kill")
        def wait(self, timeout):
            calls.append(("wait", timeout))
            if "kill" not in calls:
                raise runner.subprocess.TimeoutExpired("owned pytest", timeout)
            return -9
    monkeypatch.setattr(runner, "start_owned_process", lambda *args, **kwargs: Process())
    monkeypatch.setattr(runner.os, "killpg", lambda pid, sig: calls.append(("signal", pid, sig)), raising=False)
    stop = runner.stop_owned_process
    monkeypatch.setattr(runner, "stop_owned_process", lambda process: stop(process, windows=False))
    with pytest.raises(KeyboardInterrupt):
        runner.execute(["owned pytest"], tmp_path / "pytest.log", {})
    assert calls == [("signal", 123456, runner.signal.SIGTERM), ("wait", 5),
                     ("signal", 123456, runner.signal.SIGKILL), "kill", ("wait", 5),
                     ("signal", 123456, runner.signal.SIGKILL), "close"]


def test_windows_cleanup_targets_only_owned_tree(monkeypatch):
    calls = []
    owner = runner._process_owner()
    process = owner.WindowsOwnedProcess(None, None, None, None, [])
    monkeypatch.setattr(process, "stop", lambda: calls.append("retire owned Job"))
    runner.stop_owned_process(process, windows=True)
    assert calls == ["retire owned Job"]
    with pytest.raises(RuntimeError, match="retained owned Job"):
        runner.stop_owned_process(SimpleNamespace(pid=123456), windows=True)


def test_cleanup_stops_real_owned_descendant(tmp_path):
    pid_file = tmp_path / "child.pid"
    script = ("import subprocess,sys,time;from pathlib import Path;"
              "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
              "Path(sys.argv[1]).write_text(str(child.pid));time.sleep(60)")
    parent = runner.start_owned_process([sys.executable, "-c", script, str(pid_file)],
                                       cwd=ROOT, env=os.environ.copy(), gate_root=tmp_path,
                                       start_new_session=os.name != "nt")
    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_file.exists(), "owned test child did not start"
        pid = int(pid_file.read_text())
        runner.stop_owned_process(parent)
        assert parent.poll() is not None
        if os.name == "nt":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if handle:
                try:
                    assert kernel.WaitForSingleObject(handle, 1000) == 0
                finally:
                    kernel.CloseHandle(handle)
            else:
                assert ctypes.get_last_error() == 87, "could not inspect owned child's exit"
        else:
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                state = Path(f"/proc/{pid}/stat")
                try:
                    stopped = state.read_text().split(") ", 1)[1].startswith("Z")
                except FileNotFoundError:
                    stopped = True
                if stopped:
                    break
                time.sleep(0.01)
            else:
                pytest.fail("owned descendant remains running")
    finally:
        if parent.poll() is None:
            runner.stop_owned_process(parent)


def test_interruption_retains_nonzero_record(monkeypatch, tmp_path):
    def execute(*args):
        raise KeyboardInterrupt
    monkeypatch.setattr(runner, "execute", execute)
    output = tmp_path / "run"
    assert runner.main(["--out", str(output)]) == 130
    assert json.loads((output / "run.json").read_text())["status"] == "interrupted"


@pytest.mark.parametrize("exit_code, skipped, expected", [(1, 0, 1), (0, 1, 2), (0, 0, 0)])
def test_runner_propagates_failures_and_refuses_all_skipped(monkeypatch, tmp_path, exit_code, skipped, expected):
    def execute(command, log, environment):
        log.write_text("retained failure diagnostics")
        log.with_name("pytest.xml").write_text(
            f'<testsuites><testsuite tests="1" failures="{exit_code}" errors="0" skipped="{skipped}"/></testsuites>')
        if exit_code == 0:_completed_receipt(log,environment)
        return exit_code
    monkeypatch.setattr(runner, "execute", execute)
    output = tmp_path / "run"
    assert runner.main(["--out", str(output)]) == expected
    assert json.loads((output / "run.json").read_text())["counts"]["tests"] == 1
    assert (output / "pytest.log").read_text() == "retained failure diagnostics"


def test_fixture_reuses_original_measured_report_including_failure(monkeypatch, tmp_path):
    from anyfem import verification
    measured = object()
    calls = []
    monkeypatch.setattr(verification, "run_verification", lambda: calls.append("solve") or measured)
    monkeypatch.setattr(verification, "write_verification_report",
                        lambda report, path: calls.append((report, path)))
    monkeypatch.setenv("ANYFEM_VERIFICATION_OUT", str(tmp_path))
    fixture = runpy.run_path(str(ROOT / "diagnostics/test_verification.py"))["report"]
    assert fixture.__wrapped__() is measured
    assert calls == ["solve", (measured, str(tmp_path))]


@pytest.mark.parametrize("computed, code", [(1.0, 0), (2.0, 1)])
def test_verification_cli_still_writes_reports_and_propagates_status(monkeypatch, tmp_path, computed, code):
    from anyfem import verification
    measured = verification.VerificationReport(results=[verification.VerificationResult(
        "CLI-TEST", "synthetic CLI wiring check", "test only", computed, 1.0, 0.01)])
    selections = []
    monkeypatch.setattr(verification, "run_verification", lambda selected: selections.append(selected) or measured)
    assert verification.main(["--case", "CLI-TEST", "--out", str(tmp_path)]) == code
    assert selections == [["CLI-TEST"]]
    assert json.loads((tmp_path / "verification.json").read_text()) == measured.to_dict()
    assert (tmp_path / "verification.md").read_text() == measured.to_markdown()


def test_default_pytest_argv_is_byte_for_byte_compatible(monkeypatch,tmp_path):
    seen=[]
    def execute(command,log,environment):
        seen.append(list(command))
        log.write_text('passed')
        log.with_name('pytest.xml').write_text('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>')
        _completed_receipt(log, environment)
        return 0
    monkeypatch.setattr(runner,'execute',execute)
    output=tmp_path/'default'
    assert runner.main(['--out',str(output)])==0
    assert seen==[[sys.executable,'-m','pytest',*runner.QUICK,'-q','--junitxml='+str(output/'pytest.xml')]]


def test_diagnostic_execute_surfaces_failure_before_suite_completion(monkeypatch,tmp_path,capsys):
    class Output(list):
        def close(self):pass
    process=SimpleNamespace(stdout=Output(['current node sentinel\n','first failure sentinel\n']),wait=lambda:1)
    monkeypatch.setattr(runner,'start_owned_process',lambda *args,**kwargs:process)
    assert runner.execute(['owned pytest'],tmp_path/'pytest.log',{'ANYFEM_CI_DIAGNOSTICS_DIR':str(tmp_path)})==1
    output=capsys.readouterr().out
    assert 'current node sentinel' in output and 'first failure sentinel' in output
    assert (tmp_path/'pytest.log').read_text()=='current node sentinel\nfirst failure sentinel\n'


def test_diagnostics_and_fail_fast_are_explicit_flags_with_owned_output(monkeypatch,tmp_path):
    seen=[]
    def execute(command,log,environment):
        seen.append((command,dict(environment)))
        log.write_text('failed')
        return 1
    monkeypatch.setattr(runner,'execute',execute)
    monkeypatch.setenv('ANYFEM_RUN_GUI_TESTS','0')
    monkeypatch.setenv('ANYFEM_RUN_QT_TESTS','0')
    monkeypatch.setenv('ANYFEM_RUN_SCALE_GATES','0')
    output=tmp_path/'diagnostics'
    assert runner.main(['--ci-diagnostics','--fail-fast','--out',str(output)])==1
    command,environment=seen[0]
    assert command==[sys.executable,'-m','pytest',*runner.QUICK,'-q',
        '-p','tools.pytest_anyfem_progress','-o','anyfem_snapshot_timeout=60',
        '--maxfail=1','--junitxml='+str(output/'pytest.xml')]
    assert environment['ANYFEM_CI_DIAGNOSTICS_DIR']==str(output)
    assert {key:environment[key] for key in ('ANYFEM_RUN_GUI_TESTS','ANYFEM_RUN_QT_TESTS','ANYFEM_RUN_SCALE_GATES')}=={
        'ANYFEM_RUN_GUI_TESTS':'0','ANYFEM_RUN_QT_TESTS':'1','ANYFEM_RUN_SCALE_GATES':'0'}


def test_inherited_diagnostics_environment_cannot_enable_default(monkeypatch,tmp_path,capsys):
    monkeypatch.setenv('ANYFEM_CI_DIAGNOSTICS_DIR','inherited-must-not-activate')
    def execute(command,log,environment):
        assert 'ANYFEM_CI_DIAGNOSTICS_DIR' not in environment
        assert 'tools.pytest_anyfem_progress' not in command
        log.write_text('passed')
        log.with_name('pytest.xml').write_text('<testsuites><testsuite tests="1"/></testsuites>')
        _completed_receipt(log, environment)
        return 0
    monkeypatch.setattr(runner,'execute',execute)
    assert runner.main(['--out',str(tmp_path/'default')])==0


def test_fail_fast_diagnostics_do_not_waive_full_verification(monkeypatch,tmp_path):
    from anyfem import verification
    monkeypatch.setattr(runner,'owner_capabilities',lambda:None)
    monkeypatch.setattr(verification,'cases',lambda:[SimpleNamespace(case_id='A')])
    def execute(command,log,environment):
        assert '--maxfail=1' in command
        log.write_text('pytest passed, verification incomplete')
        log.with_name('pytest.xml').write_text('<testsuites><testsuite tests="1"/></testsuites>')
        directory=Path(environment['ANYFEM_VERIFICATION_OUT']);directory.mkdir()
        (directory/'verification.json').write_text(json.dumps({'status':'passed','counts':{'total':0,'passed':0,'failed':0},'results':[]}))
        _completed_receipt(log, environment)
        return 0
    _stub_completed_full_run(monkeypatch, execute)
    output=tmp_path/'full'
    assert runner.main(['--profile','qualification','--ci-diagnostics','--fail-fast','--out',str(output)])==2
    report=json.loads((output/'run.json').read_text())
    assert report['status']=='failed' and report['exit_code']==2
    assert 'incomplete' in report['error']


def _scalar_diagnostic_pytest(tmp_path, body, *extra):
    script=tmp_path/'test_scalar_child.py';script.write_text(body)
    config=tmp_path/'pytest.ini';config.write_text('[pytest]\n')
    output=tmp_path/'diagnostics';output.mkdir()
    environment=os.environ.copy()
    environment.pop('PYTEST_ADDOPTS',None)
    environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    environment['ANYFEM_CI_DIAGNOSTICS_DIR']=str(output)
    command=[sys.executable,'-m','pytest','-q','-c',str(config),'--confcutdir',str(tmp_path),
             '-p','tools.pytest_anyfem_progress',str(script),*extra]
    result=subprocess.run(command,cwd=ROOT,env=environment,capture_output=True,text=True,timeout=15)
    return result,output


def test_plugin_named_phases_are_current_before_body_and_preserve_capture(tmp_path):
    body="""import json,os
from pathlib import Path
import pytest

def phase():return json.loads((Path(os.environ['ANYFEM_CI_DIAGNOSTICS_DIR'])/'pytest-current.json').read_text())
@pytest.fixture
def resource():
 assert phase()['event']=='phase_start' and phase()['phase']=='setup'
 yield
 assert phase()['event']=='phase_start' and phase()['phase']=='teardown'
def test_sample(resource,capsys):
 assert phase()['event']=='phase_start' and phase()['phase']=='call'
 assert phase()['nodeid'].endswith('::test_sample')
 print('body only')
 assert capsys.readouterr().out=='body only\\n'
"""
    result,output=_scalar_diagnostic_pytest(tmp_path,body)
    assert result.returncode==0,result.stdout+result.stderr
    rows=[json.loads(line) for line in (output/'pytest-progress.jsonl').read_text().splitlines()]
    assert [row['phase'] for row in rows if row['event']=='phase_start']==['setup','call','teardown']
    assert [row['sequence'] for row in rows]==list(range(1,len(rows)+1))
    assert rows[-1]['event']=='session_finish' and rows[-1]['exit_status']==0
    assert json.loads((output/'pytest-collection.json').read_text())['nodes']==['test_scalar_child.py::test_sample']


def test_plugin_failure_is_visible_before_the_next_node(tmp_path):
    result,output=_scalar_diagnostic_pytest(tmp_path,
        "def test_first():assert False,'visible first failure sentinel'\ndef test_second():pass\n")
    assert result.returncode==1
    text=result.stdout+result.stderr
    assert text.index('visible first failure sentinel')<text.index('"event": "node_start", "nodeid": "test_scalar_child.py::test_second"')
    rows=[json.loads(line) for line in (output/'pytest-progress.jsonl').read_text().splitlines()]
    failed=[row for row in rows if row['event']=='failure']
    assert len(failed)==1 and failed[0]['phase']=='call'
    assert 'visible first failure sentinel' in failed[0]['longrepr']


@pytest.mark.parametrize('phase', ['setup','teardown'])
def test_plugin_fixture_errors_are_immediate_and_named(tmp_path,phase):
    setup="assert False,'setup sentinel'" if phase=='setup' else 'pass'
    teardown="assert False,'teardown sentinel'" if phase=='teardown' else 'pass'
    body=f"import pytest\n@pytest.fixture\ndef resource():\n {setup}\n yield\n {teardown}\ndef test_sample(resource):pass\n"
    result,output=_scalar_diagnostic_pytest(tmp_path,body)
    assert result.returncode==1
    rows=[json.loads(line) for line in (output/'pytest-progress.jsonl').read_text().splitlines()]
    failed=[row for row in rows if row['event']=='failure']
    assert len(failed)==1 and failed[0]['phase']==phase
    assert f'{phase} sentinel' in failed[0]['longrepr']
    assert '[anyfem-ci failure] test_scalar_child.py::test_sample' in result.stdout


def test_plugin_fail_fast_keeps_failure_and_never_runs_second_body(tmp_path):
    marker=tmp_path/'second-ran'
    body=f"from pathlib import Path\ndef test_first():assert False,'first sentinel'\ndef test_second():Path({str(marker)!r}).write_text('ran')\n"
    result,output=_scalar_diagnostic_pytest(tmp_path,body,'--maxfail=1')
    assert result.returncode==1 and not marker.exists()
    rows=[json.loads(line) for line in (output/'pytest-progress.jsonl').read_text().splitlines()]
    assert len([row for row in rows if row['event']=='node_start'])==1
    assert rows[-1]['exit_status']==1 and rows[-1]['collected_count']==2


def test_plugin_collection_only_never_executes_a_body(tmp_path):
    marker=tmp_path/'body-ran'
    result,output=_scalar_diagnostic_pytest(tmp_path,
        f"from pathlib import Path\ndef test_sample():Path({str(marker)!r}).write_text('ran')\n",'--collect-only')
    assert result.returncode==0 and not marker.exists()
    rows=[json.loads(line) for line in (output/'pytest-progress.jsonl').read_text().splitlines()]
    assert not any(row['event'] in {'node_start','phase_start'} for row in rows)
    assert json.loads((output/'pytest-collection.json').read_text())['nodes']==['test_scalar_child.py::test_sample']


def test_standard_faulthandler_dump_does_not_abort_the_scalar_test(tmp_path):
    result,output=_scalar_diagnostic_pytest(tmp_path,
        "import time\ndef test_sample():time.sleep(.3)\n",'-o','faulthandler_timeout=0.05')
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Timeout (' in result.stdout+result.stderr
    rows=[json.loads(line) for line in (output/'pytest-progress.jsonl').read_text().splitlines()]
    assert rows[-1]['exit_status']==0


def test_reusable_lane_retains_named_diagnostics():
    text=(ROOT/'.github/workflows/application-lane.yml').read_text()
    assert '--ci-diagnostics' in text and '--out reports/application' in text
    assert 'timeout-minutes: 45' in text


def test_shared_lane_keeps_exact_physical_solver_and_results_pins():
    text=(ROOT/'.github/workflows/application-lane.yml').read_text()
    assert text.count('ref: f54e3cde5abf70cb8c989eb106119eedd3e33025') == 2
    assert 'ref: 9eb9713b92c1b3a476301150b5e89e350d515844' in text
    assert 'python -m build' in text and 'check_release_artifacts.py dist' in text


def test_canonical_results_runtime_install_does_not_share_editable_source_roots():
    text=(ROOT/'.github/workflows/application-lane.yml').read_text()
    assert '-e .ecosystem/ANYfeSolver ' in text
    assert '-e .ecosystem/ANYfeSolver-results/packages/results' not in text
    assert 'python -m pip install --no-deps .ecosystem/ANYfeSolver-results/packages/results' in text
    assert 'anyfesolver_results.principals' in text
