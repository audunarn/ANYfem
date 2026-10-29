"""Portable checks for the Mac installation's refusal and provenance contracts."""
import json
from pathlib import Path
import re

import pytest

from tools.bootstrap_macos import fresh_work_directory, source_pins


def test_bootstrap_refuses_reusing_outputs_and_building_inside_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="outside"):
        fresh_work_directory(source / "build", source)
    work = tmp_path / "work"
    assert fresh_work_directory(work, source) == work
    with pytest.raises(FileExistsError):
        fresh_work_directory(work, source)


@pytest.mark.parametrize("name,sha", [("../outside", "a" * 40), ("ANYmesh", "main"), ("ANYmesh", "a" * 39)])
def test_manifest_refuses_path_escape_or_unpinned_ref(tmp_path, name, sha):
    path = tmp_path / "pins.json"
    path.write_text(json.dumps({name: sha}))
    with pytest.raises(ValueError, match="invalid ecosystem pin"):
        source_pins(path)


def test_macos_owner_pins_match_existing_application_ci():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    pins = dict(re.findall(r"repository: audunarn/(ANY\w+)\s+(?:#[^\n]*\n\s*)?ref: ([a-f0-9]{40})", workflow))
    assert source_pins() == pins, "adopt a coordinated pin update instead of drifting Mac dependencies"


def test_installed_license_gate_ignores_inactive_platform_requirement(monkeypatch):
    from tools import check_licenses
    calls = []
    monkeypatch.setattr(check_licenses.importlib_metadata, "distribution", lambda name: calls.append(name))
    check_licenses._check_installed([{"scope": "runtime", "name": "absent-platform-only-package",
        "requirement": "absent-platform-only-package; python_version < '1'"}])
    assert calls == []


def test_cross_platform_comparison_refuses_mismatch_and_missing_reference(tmp_path):
    from tools.macos_parity import compare
    cases = {name: {"nodes": [[0, 0, 0]]} for name in ("linear", "quadratic", "beams")}
    paths = []
    for system in ("macOS", "Linux"):
        for python in ("3.11", "3.14"):
            path = tmp_path / f"{system}-{python}.json"
            path.write_text(json.dumps({"machine": "arm64" if system == "macOS" else "x86_64",
                "platform_key": system, "python": python, "cases": cases}))
            paths.append(path)
    assert compare(paths)["status"] == "passed"
    with pytest.raises(ValueError, match="complete.*matrix"):
        compare(paths[:2])
    with pytest.raises(ValueError, match="without duplicates"):
        compare([paths[0], paths[0], *paths[2:]])
    changed = json.loads(paths[-1].read_text())
    changed["cases"]["linear"]["nodes"] = [[0.1, 0, 0]]
    paths[-1].write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="parity mismatch"):
        compare(paths)


def test_bootstrap_records_command_startup_failure(tmp_path):
    from tools.bootstrap_macos import Commands
    with pytest.raises(FileNotFoundError):
        Commands(tmp_path).run([str(tmp_path / "missing-executable")])
    record = json.loads((tmp_path / "commands.json").read_text())[0]
    assert record["status"] == "startup_or_process_error"
    assert record["exit_code"] is None and record["process_wall_seconds"] >= 0


@pytest.mark.parametrize("interrupt", [True, False], ids=["interrupt", "timeout"])
def test_bootstrap_stops_owned_command_and_retains_output(monkeypatch, tmp_path, interrupt):
    from tools import bootstrap_macos as bootstrap

    class Child:
        pid = 4321
        returncode = None
        calls = 0

        def communicate(self, timeout):
            self.calls += 1
            if self.calls == 1:
                if interrupt:
                    raise KeyboardInterrupt()
                raise bootstrap.subprocess.TimeoutExpired(["owned-command"], timeout)
            self.returncode = -9
            return b"retained partial output", None

        def poll(self):
            return self.returncode

    stopped = []
    environments = []
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "source-shadow"))
    def launch(*args, **kwargs):
        environments.append(kwargs["env"])
        return Child()
    monkeypatch.setattr(bootstrap.subprocess, "Popen", launch)
    monkeypatch.setattr(bootstrap.os, "killpg", lambda pid, sig: stopped.append(pid), raising=False)
    monkeypatch.setattr(bootstrap.signal, "SIGKILL", 9, raising=False)
    with pytest.raises(KeyboardInterrupt if interrupt else RuntimeError):
        bootstrap.Commands(tmp_path).run(["owned-command"])
    record = json.loads((tmp_path / "commands.json").read_text())[0]
    assert stopped == [4321]
    assert "PYTHONPATH" not in environments[0]
    assert record["status"] == ("interrupted" if interrupt else "timeout")
    assert (tmp_path / "command-00.log").read_text() == "retained partial output"


def test_isolated_application_source_excludes_old_build_artifacts(tmp_path):
    from tools.bootstrap_macos import copy_application_source
    source = tmp_path / "source"
    for relative in ("pyproject.toml", "README.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md",
                     "dependency-licenses.json", "docs/LICENSE.md", "src/anyfem/__init__.py",
                     "build/lib/anyfem/deleted.py", "src/anyfem/__pycache__/old.pyc"):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("candidate")
    destination = copy_application_source(tmp_path / "copy", source)
    assert (destination / "src/anyfem/__init__.py").read_text() == "candidate"
    assert not (destination / "build").exists()
    assert not (destination / "src/anyfem/__pycache__").exists()
