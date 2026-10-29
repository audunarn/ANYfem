"""Build the pinned ecosystem and install resolved wheels in a clean Mac venv.

No editable installs or --no-deps installation: missing requirements are errors.
The JSON manifest uses the revisions already exercised by the existing CI lanes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import shutil
import shlex
import subprocess
import sys
import sysconfig
import time

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name("macos-ecosystem.json")


def source_pins(path=MANIFEST):
    pins = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(pins, dict) or not pins:
        raise ValueError("ecosystem manifest must be a nonempty object")
    for name, sha in pins.items():
        if not re.fullmatch(r"ANY[A-Za-z0-9]+", name) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError(f"invalid ecosystem pin: {name!r}")
    return pins


def fresh_work_directory(path, source=ROOT):
    path = path.resolve()
    if path == source.resolve() or source.resolve() in path.parents:
        raise ValueError("work directory must be outside the ANYfem source tree")
    path.mkdir(parents=True, exist_ok=False)
    return path


class Commands:
    def __init__(self, root, timeout=600):
        self.root, self.timeout = root, timeout
        self.records = []

    def run(self, argv, *, cwd=None, env=None):
        index = len(self.records)
        row = {"command": list(map(str, argv)), "cwd": str(cwd or self.root), "exit_code": None,
               "timeout_seconds": self.timeout,
               "started_utc": datetime.now(timezone.utc).isoformat(), "status": "running"}
        self.records.append(row)
        start = time.perf_counter()
        process = None
        try:
            effective_env = dict(os.environ if env is None else env)
            # A source PYTHONPATH can make pip skip a wheel as "installed"
            # even though isolated Python cannot import it in the fresh venv.
            for key in ("PYTHONPATH", "PYTHONHOME"):
                effective_env.pop(key, None)
            process = subprocess.Popen(row["command"], cwd=cwd or self.root, env=effective_env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            try:
                output, _ = process.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                row["status"] = "timeout"
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                output, _ = process.communicate(timeout=10)
            (self.root / f"command-{index:02d}.log").write_bytes(output)
            row["exit_code"] = process.returncode
            if row["status"] != "timeout":
                row["status"] = "passed" if process.returncode == 0 else "failed"
            if row["status"] != "passed":
                raise RuntimeError(f"command {index} {row['status']}; see {self.root}/command-{index:02d}.log")
            return output.decode("utf-8", errors="replace").strip()
        except BaseException as error:
            if row["status"] == "running":
                row["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "startup_or_process_error"
            row["error"] = f"{type(error).__name__}: {error}"
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                output, _ = process.communicate(timeout=10)
                (self.root / f"command-{index:02d}.log").write_bytes(output)
                row["exit_code"] = process.returncode
            raise
        finally:
            row["process_wall_seconds"] = time.perf_counter() - start
            (self.root / "commands.json").write_text(json.dumps(self.records, indent=2) + "\n", encoding="utf-8")


def copy_application_source(destination, source=ROOT):
    """Build from current candidate sources without reusing in-tree build caches."""
    destination.mkdir(exist_ok=False)
    files = ("pyproject.toml", "README.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md",
             "dependency-licenses.json", "docs/LICENSE.md")
    for relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    shutil.copytree(source / "src/anyfem", destination / "src/anyfem",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.egg-info"))
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path, help="new directory outside source; never reused")
    parser.add_argument("--linux-reference", action="store_true", help="CI-only Linux build for cross-platform numerical comparison")
    options = parser.parse_args(argv)
    mac = sys.platform == "darwin" and platform.machine() == "arm64"
    reference = options.linux_reference and sys.platform == "linux"
    if not (mac or reference):
        parser.error("this bootstrap targets a native Apple Silicon macOS Python")
    pins = source_pins()
    work = fresh_work_directory(options.work_dir)
    runner = Commands(work)
    result = {"status": "running", "platform": platform.platform(), "machine": platform.machine(),
              "python": sys.version, "executable": sys.executable, "pins": pins}
    try:
        sources = work / "sources"
        sources.mkdir()
        for name, sha in pins.items():
            destination = sources / name
            runner.run(["git", "init", str(destination)])
            runner.run(["git", "remote", "add", "origin", f"https://github.com/audunarn/{name}.git"], cwd=destination)
            runner.run(["git", "fetch", "--depth", "1", "origin", sha], cwd=destination)
            runner.run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=destination)
            actual = runner.run(["git", "rev-parse", "HEAD"], cwd=destination)
            if actual != sha:
                raise RuntimeError(f"{name}: checked out {actual}, expected {sha}")
        result["anyfem_head"] = runner.run(["git", "rev-parse", "HEAD"], cwd=ROOT)
        result["anyfem_status"] = runner.run(["git", "status", "--porcelain=v1"], cwd=ROOT)
        if os.environ.get("GITHUB_ACTIONS") == "true" and result["anyfem_status"]:
            raise RuntimeError("CI requires a clean ANYfem checkout before building")
        result["python_build_config"] = {key: sysconfig.get_config_var(key) for key in (
            "CC", "CXX", "CFLAGS", "CCSHARED", "LDSHARED", "MACOSX_DEPLOYMENT_TARGET")}
        compiler = shlex.split(os.environ.get("CXX") or sysconfig.get_config_var("CXX") or "c++")
        result["compiler"] = runner.run([*compiler, "--version"])
        result["sdk"] = runner.run(["xcrun", "--show-sdk-version"]) if mac else "Linux reference"
        build = work / "build-env"
        runner.run([sys.executable, "-m", "venv", str(build)])
        python = build / "bin/python"
        runner.run([python, "-m", "pip", "install", "--upgrade", "pip", "build"])
        wheels = work / "wheels"
        wheels.mkdir()
        environment = dict(os.environ)
        # Native geometry remains the owner's default; native mesher is required.
        environment.update(ANYMESHER_REQUIRE_NATIVE="1", ANYMESHER_DISABLE_NATIVE="0")
        for variable in ("ANYGEOMETRY_BUILD_NATIVE", "ANYGEOMETRY_REQUIRE_NATIVE", "ANYGEOMETRY_DISABLE_NATIVE"):
            environment.pop(variable, None)
        result["build_environment"] = {key: environment.get(key) for key in (
            "ANYMESHER_REQUIRE_NATIVE", "ANYMESHER_DISABLE_NATIVE", "CFLAGS", "CXXFLAGS",
            "LDFLAGS", "MACOSX_DEPLOYMENT_TARGET", "ARCHFLAGS")}
        packages = [sources / name for name in pins]
        packages += [sources / "ANYsolver/packages/results", copy_application_source(work / "application-source")]
        for package in packages:
            runner.run([python, "-m", "build", "--wheel", "--outdir", str(wheels), str(package)], env=environment)
        result["wheels_sha256"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(wheels.glob("*.whl"))}
        installed = work / "venv"
        runner.run([sys.executable, "-m", "venv", str(installed)])
        python = installed / "bin/python"
        runner.run([python, "-m", "pip", "install", "--upgrade", "pip"])
        # All declared extras are installed because the existing licence gate
        # inventories optional dependencies too. Floating execution is not qualified.
        runner.run([python, "-m", "pip", "install", "--only-binary=:all:", "--report", str(work / "install-report.json"),
                    "--find-links", str(wheels), *map(str, sorted(wheels.glob("*.whl"))),
                    "ANYfem[dev,gui,qt,tk,sections,floating,excel]==" +
                    __import__("tomllib").loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
                    "xarray>=2023.1"])
        runner.run([python, "-m", "pip", "check"])
        runner.run([python, "-m", "pip", "freeze", "--all"])
        runner.run([python, "-I", str(ROOT / "tools/macos_runtime.py"), "--out", str(work / "runtime.json"),
                    "--forbid-source-root", str(ROOT), "--forbid-source-root", str(sources),
                    "--forbid-source-root", str(work / "application-source")])
        result.update(status="passed", installed_python=str(python))
        print(f"Installed: {python}\nEvidence: {work}")
        return 0
    except BaseException as error:
        result.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
                      error=f"{type(error).__name__}: {error}")
        raise
    finally:
        (work / "bootstrap.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
