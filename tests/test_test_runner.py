"""Execution boundaries and evidence integrity for the development runner."""
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
    assert all((ROOT / path).is_file() for path in runner.selectors("quick", []))


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
        return 0
    monkeypatch.setattr(runner, "execute", execute)
    output = tmp_path / "run"
    assert runner.main(["--profile", "full", "--out", str(output)]) == 2
    report = json.loads((output / "run.json").read_text())
    assert report["status"] == "failed"
    assert report["exit_code"] == 2


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
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: Process())
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
    process = SimpleNamespace(pid=123456, wait=lambda timeout: calls.append(("wait", timeout)))
    monkeypatch.setattr(runner.subprocess, "run", lambda command, **options: calls.append((command, options)))
    runner.stop_owned_process(process, windows=True)
    assert calls[0][0] == ["taskkill", "/PID", "123456", "/T", "/F"]
    assert calls[0][1]["timeout"] == 5 and calls[0][1]["check"] is True
    assert calls[1] == ("wait", 5)


def test_cleanup_stops_real_owned_descendant(tmp_path):
    pid_file = tmp_path / "child.pid"
    script = ("import subprocess,sys,time;from pathlib import Path;"
              "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
              "Path(sys.argv[1]).write_text(str(child.pid));time.sleep(60)")
    parent = subprocess.Popen([sys.executable, "-c", script, str(pid_file)],
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
    fixture = runpy.run_path(str(ROOT / "tests/test_verification.py"))["report"]
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
