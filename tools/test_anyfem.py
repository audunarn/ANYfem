"""Bounded development checks; full acceptance remains an explicit separate scope."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
QUICK = (
    "tests/test_license_policy.py", "tests/test_application_frontend_boundary.py",
    "tests/test_output_requests.py", "tests/test_command_recording.py",
    "tests/test_document_geometry_snapshot.py", "tests/test_selection_state.py",
    "tests/test_test_runner.py",
)


def selectors(profile, requested):
    if profile == "focused":
        if not requested:
            raise ValueError("focused requires explicit test files or node IDs")
        if any(not value.startswith("tests/") or "\\" in value or
               ".." in value.split("::", 1)[0].split("/") for value in requested):
            raise ValueError("focused selectors must be repository tests/ paths or node IDs")
        return list(requested)
    if requested:
        raise ValueError("selectors are allowed only with --profile focused")
    if profile == "quick":
        return list(QUICK)
    if profile == "qt":
        return ["tests/test_qt_workbench.py", "tests/test_application_frontend_boundary.py"]
    return ["tests"]


def owner_capabilities():
    missing = []
    for name, symbols in {
        "anygeometry": ("IntersectionBatchPolicy", "plan_intersections", "apply_intersections"),
        "anymesher": ("MeshAutomationOptions", "MeshRecoveryIncomplete"),
    }.items():
        module = importlib.import_module(name)  # A broken installed owner must fail.
        missing.extend(f"{name}.{symbol}" for symbol in symbols if not callable(getattr(module, symbol, None)))
    if missing:
        raise RuntimeError("required owner capabilities unavailable: " + ", ".join(missing))


def validate_verification(path, expected):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("verification evidence must be an object")
    results = data.get("results", [])
    if not isinstance(results, list) or any(not isinstance(row, dict) for row in results):
        raise RuntimeError("verification results must be a list of objects")
    identifiers = [row.get("case_id") for row in results]
    if any(not isinstance(identifier, str) for identifier in identifiers):
        raise RuntimeError("verification case IDs must be strings")
    if not expected or len(identifiers) != len(expected) or set(identifiers) != set(expected):
        raise RuntimeError("verification evidence is missing, duplicated or incomplete")
    if data.get("status") != "passed" or any(row.get("status") != "passed" for row in results):
        raise RuntimeError("verification evidence records failure")
    if data.get("counts") != {"total": len(expected), "passed": len(expected), "failed": 0}:
        raise RuntimeError("verification counts do not match the complete case inventory")


def stop_owned_process(process, windows=None):
    """Stop the owned pytest tree, including scientific subprocesses."""
    windows = os.name == "nt" if windows is None else windows
    try:
        if windows:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, check=True)
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if not windows:
                os.killpg(process.pid, signal.SIGKILL)
            process.kill()
            process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError) as error:
        # Never claim interrupted work stopped if tree cleanup could not be confirmed.
        process.kill()
        raise RuntimeError("could not confirm stopping the owned pytest process tree") from error
    finally:
        if not windows:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def execute(command, log, environment):
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                   start_new_session=os.name != "nt")
        try:
            for line in process.stdout:
                stream.write(line)
                stream.flush()
                if "%]" in line:
                    print(line.strip(), flush=True)
            return process.wait()
        except KeyboardInterrupt:
            stop_owned_process(process)
            raise
        finally:
            process.stdout.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("quick", "focused", "qt", "full"), default="quick")
    parser.add_argument("--backend", choices=("software", "gpu"), default="software")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", type=Path, help="new evidence directory; existing paths are refused")
    parser.add_argument("tests", nargs="*")
    options = parser.parse_args(argv)
    try:
        selected = selectors(options.profile, options.tests)
    except ValueError as error:
        parser.error(str(error))
    command = [sys.executable, "-m", "pytest", *selected, "-q"]
    if options.dry_run:
        print(json.dumps({"profile": options.profile, "command": command,
                          "acceptance": "not established"}, indent=2))
        return 0
    output = options.out or ROOT / "reports" / "tests" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = output.resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        parser.error(str(error))
    environment = os.environ.copy()
    # An inherited -k/--ignore/--collect-only must not silently narrow a profile.
    inherited_options = environment.pop("PYTEST_ADDOPTS", None)
    if options.profile == "qt":
        environment["ANYFEM_RUN_QT_TESTS"] = "1"
        environment["ANYFEM_QT_TEST_BACKEND"] = options.backend
    if options.profile == "full":
        environment["ANYFEM_VERIFICATION_OUT"] = str(output / "verification")
    command += ["--junitxml=" + str(output / "pytest.xml")]
    report = {"profile": options.profile, "command": command, "python": sys.executable,
              "python_version": sys.version, "ignored_pytest_addopts": inherited_options,
              "environment": {key: environment.get(key) for key in (
                  "ANYFEM_RUN_QT_TESTS", "ANYFEM_QT_TEST_BACKEND", "ANYFEM_RUN_GUI_TESTS",
                  "ANYFEM_RUN_SCALE_GATES", "ANYFEM_RUN_HARDWARE_GATES", "ANYFEM_TEST_THREADS")},
              "acceptance": "not established", "status": "running"}
    start = time.monotonic()
    code = 2
    try:
        if options.profile in {"full", "qt"}:
            owner_capabilities()
        if options.profile == "qt":
            importlib.import_module("PySide6.QtWidgets")
        code = execute(command, output / "pytest.log", environment)
        report["status"] = "passed" if code == 0 else "failed"
        if (output / "pytest.xml").exists():
            suites = ET.parse(output / "pytest.xml").getroot().findall("testsuite")
            report["counts"] = {key: sum(int(suite.get(key, "0")) for suite in suites)
                                for key in ("tests", "failures", "errors", "skipped")}
        if code == 0:
            counts = report.get("counts", {})
            if counts.get("tests", 0) <= counts.get("skipped", 0):
                raise RuntimeError("no executed tests were recorded")
            if options.profile == "qt":
                rows = ET.parse(output / "pytest.xml").getroot().iter("testcase")
                if not any("test_qt_workbench" in row.get("classname", "") and
                           row.find("skipped") is None for row in rows):
                    raise RuntimeError("no Qt workbench tests executed")
        if code == 0 and options.profile == "full":
            from anyfem.verification import cases
            validate_verification(output / "verification" / "verification.json", {case.case_id for case in cases()})
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        code = 130
    except (ImportError, RuntimeError, OSError, ValueError, ET.ParseError) as error:
        report["status"] = "blocked" if not (output / "pytest.log").exists() else "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        code = 2
    finally:
        report.update(exit_code=code, seconds=time.monotonic() - start)
        (output / "run.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report.get("counts", {})), flush=True)
        print(f"{report['status']}: {output / 'run.json'}", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
