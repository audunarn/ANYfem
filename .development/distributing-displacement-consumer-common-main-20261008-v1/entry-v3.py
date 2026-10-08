"""Single approved source-check attempt. No authority is conferred by this file.

The enclosing supervisor owns cumulative accounting, deadlines and final cleanup.
This entry independently refuses unapproved or changed inputs and records native
process identities so the supervisor can clean up only this attempt's tree.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.metadata
import json
import io
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import threading
import time
import traceback
import zipfile


ROOT = Path(__file__).resolve().parents[2]
HOME = Path(__file__).resolve().parent
SITE = HOME/"site"
SOLVER_SITE = Path("C:/Github/ANYsolver/.perf2-worktrees/common-main-20261008/.development/common-main-20261008/site-v5")
CONSUMER_ROOT = SITE/"anyfem"
BASE_DEPENDENCIES = Path("C:/Python/Python314/Lib/site-packages")
DEPENDENCIES = Path("C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001/.qualification/b3-ge-e4-pl/r8-native-s3-restart-binding-dev-20261005-v1/ci-site-network-v2")
PYTHON = Path("C:/Python/Python314/python.exe")
CAP_BYTES = 2*1024**3
ATOMIC_DEADLINE_NS = None
ENVIRONMENT = {
    "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1", "NUMBER_OF_CPUS": "1",
    "NUMBA_DISABLE_JIT": "1", "NUMBA_NUM_THREADS": "1",
    "ANYFEM_TEST_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1", "BLIS_NUM_THREADS": "1",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PIP_NO_INDEX": "1",
}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            value.update(block)
    return value.hexdigest()


def atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    retry_deadline = time.perf_counter_ns()+250_000_000
    if ATOMIC_DEADLINE_NS is not None:
        retry_deadline = min(retry_deadline, ATOMIC_DEADLINE_NS)
    while True:
        try:
            os.replace(temporary, path)
            break
        except OSError as error:
            # A reader without FILE_SHARE_DELETE can briefly hold a Windows
            # registry snapshot open. Retry only these native sharing/access
            # results, retaining the flushed temporary file and total deadline.
            remaining = (retry_deadline-time.perf_counter_ns())/1_000_000_000.
            if getattr(error, "winerror", None) not in (5, 32, 33) or remaining <= 0.:
                raise
            time.sleep(min(.01, remaining))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def contained(path, parent):
    path = Path(path).resolve()
    return path == parent.resolve() or parent.resolve() in path.parents


def checked_relative(value):
    path = (ROOT/value).resolve()
    require(contained(path, ROOT), "manifest path escapes source checkout")
    return path


def source_inventory(manifest):
    result = []
    current = {path.relative_to(ROOT).as_posix(): digest(path)
               for path in sorted((ROOT/"src/anyfem").rglob("*.py")) if path.is_file()}
    require(current == manifest["source_files"], "full consumer source catalog changed")
    for name, expected in sorted({**manifest["source_files"], **manifest["test_files"],
                                 **manifest.get("extra_test_files", {}),
                                 **manifest["docs_files"], **manifest["example_files"],
                                 **manifest["metadata_files"]}.items()):
        path = checked_relative(name)
        actual = digest(path)
        require(actual == expected, "frozen source mismatch: "+name)
        result.append({"path": name, "sha256": actual})
    require(bool(result), "empty frozen source manifest")
    require(digest(__file__) == manifest["entry_sha256"], "entry source changed after manifest freeze")
    require(digest(HOME/"supervise-v3.ps1") == manifest["supervisor_sha256"], "supervisor source changed after manifest freeze")
    # Frozen dependency catalogs remain in the manifest; actual imported files
    # are verified before and after each check by dependency_module_inventory.
    return result


def module_inventory(manifest):
    result = []
    for name, module in sorted(tuple(sys.modules.items())):
        consumer = name == "anyfem" or name.startswith("anyfem.")
        solver = name == "anysolver" or name.startswith("anysolver.")
        if not consumer and not solver:
            continue
        location = getattr(module, "__file__", None)
        if location is None:
            continue
        path = Path(location).resolve()
        root = CONSUMER_ROOT if consumer else SOLVER_SITE/"anysolver"
        require(contained(path, root), "source fallback or foreign product import: "+name)
        require(path.suffix == ".py", "non-source product module: "+name)
        relative = "src/"+root.name+"/"+path.relative_to(root).as_posix()
        expected = (manifest["source_files"] if consumer else manifest["solver_artifact"]["python_files"]).get(relative)
        require(expected is not None, "loaded product source is absent from frozen manifest: "+relative)
        actual = digest(path)
        require(actual == expected, "loaded product source does not match frozen manifest: "+relative)
        result.append({"module": name, "path": str(path), "sha256": actual})
    return result


def dependency_module_inventory(manifest):
    result = []
    frozen = {str(Path(path).resolve()).casefold(): expected
              for path, expected in manifest.get("dependency_source_files", {}).items()}
    for name, module in sorted(tuple(sys.modules.items())):
        location = getattr(module, "__file__", None)
        if location is None and (name == "docs" or name.startswith("docs.")):
            paths = tuple(getattr(module, "__path__", ()))
            require(bool(paths) and all(contained(Path(path), ROOT/"docs") for path in paths),
                    "docs namespace outside controlled repository root: "+name)
        if (location is None or name == "anysolver" or name.startswith("anysolver.") or
                name == "anyfem" or name.startswith("anyfem.")):
            continue
        path = Path(location).resolve()
        allowed = contained(path, DEPENDENCIES) or contained(path, BASE_DEPENDENCIES)
        allowed = allowed or contained(path, Path("C:/Python/Python314/Lib")) or contained(path, Path("C:/Python/Python314/DLLs"))
        allowed = allowed or path == Path(__file__).resolve() or contained(path, ROOT/"tests") or contained(path, ROOT/"examples")
        if contained(path, ROOT/"examples"):
            relative = path.relative_to(ROOT).as_posix()
            require(path.suffix == ".py" and manifest["example_files"].get(relative) == digest(path),
                    "example import outside frozen source: "+name)
        if contained(path, ROOT/"tests"):
            relative = path.relative_to(ROOT).as_posix()
            require(path.suffix == ".py" and manifest["test_files"].get(relative) == digest(path),
                    "test import outside frozen source: "+name)
        if name == "docs" or name.startswith("docs.") or contained(path, ROOT/"docs"):
            require(contained(path, ROOT/"docs") and path.suffix == ".py", "foreign/non-source reference module: "+name)
            relative = path.relative_to(ROOT).as_posix()
            expected = manifest.get("docs_files", {}).get(relative)
            require(expected is not None and digest(path) == expected,
                    "loaded reference source is absent or differs from frozen docs_files: "+relative)
            allowed = True
        declared = [(prefix, roots) for prefix, roots in manifest.get("allowed_module_roots", {}).items()
                    if name == prefix or name.startswith(prefix+".")]
        if declared:
            prefix, roots = max(declared, key=lambda row: len(row[0]))
            require(type(roots) is list and bool(roots), "empty module-origin declaration")
            allowed = any(contained(path, Path(root)) for root in roots)
            require(allowed, "declared dependency loaded from wrong root: "+name)
            if path.suffix.lower() == ".py":
                expected = frozen.get(str(path).casefold())
                require(expected is not None and digest(path) == expected,
                        "loaded declared dependency source does not match freeze: "+name)
        require(allowed, "module outside declared source/dependency graph: "+name+" at "+str(path))
        require(path.is_file(), "imported module has no inspectable file: "+name)
        if contained(path, BASE_DEPENDENCIES) or contained(path, DEPENDENCIES):
            expected = manifest["dependency_file_hashes"].get(str(path).replace("\\", "/"))
            require(expected is not None and digest(path) == expected,
                    "loaded installed dependency absent from frozen catalog: "+name)
        result.append({"module": name, "path": str(path), "sha256": digest(path)})
    return result


def dependency_inventory(manifest, attempt, stage):
    # Development-phase identity check. The full 4,309-file dependency catalog
    # was frozen at registration and is retained for final artifact acceptance.
    # Runtime checks verify pinned roots, versions, package metadata, entry modules,
    # and every dependency module actually loaded by the phase.
    result = []
    origins = []
    for name, expected_version in sorted(manifest["dependency_versions"].items()):
        approved = Path(manifest["dependency_roots"][name]).resolve()
        expected_root = BASE_DEPENDENCIES if name in ("psutil", "pytest") else DEPENDENCIES if name in ("numpy", "scipy", "numba", "llvmlite") else approved
        cached = sys.modules.get(name)
        origin = {"module": name, "approved_root": str(approved),
                  "expected_root": str(expected_root.resolve()),
                  "cached_before_import": cached is not None,
                  "cached_file": getattr(cached, "__file__", None)}
        origins.append(origin)
        try:
            module = importlib.import_module(name)
            path = Path(module.__file__).resolve()
            origin.update(path=str(path), approved_root_is_pinned=approved in (
                DEPENDENCIES.resolve(), BASE_DEPENDENCIES.resolve()),
                approved_root_matches_expected=approved == expected_root.resolve(),
                actual_path_within_approved=contained(path, approved))
        except BaseException as error:
            origin.update(import_error=str(error), traceback=traceback.format_exc())
            atomic(attempt/("dependency-origins-"+stage+".json"),
                   {"stage": stage, "sys_path": list(sys.path), "origins": origins})
            raise
        atomic(attempt/("dependency-origins-"+stage+".json"),
               {"stage": stage, "sys_path": list(sys.path), "origins": origins})
        require(origin["approved_root_is_pinned"] and origin["approved_root_matches_expected"] and
                origin["actual_path_within_approved"], "dependency outside pinned installation: "+name+
                "; actual="+str(path)+"; approved="+str(approved)+"; expected="+str(expected_root.resolve()))
        distribution = importlib.metadata.distribution(name)
        require(distribution.version == expected_version, "dependency version mismatch: "+name)
        frozen = manifest["dependency_file_hashes"]
        actual = digest(path)
        require(frozen.get(str(path).replace("\\", "/")) == actual,
                "loaded dependency entry source differs from frozen catalog: "+name)
        files = []
        for filename in distribution.files or ():
            if Path(str(filename)).name not in ("METADATA", "RECORD"):
                continue
            selected = Path(distribution.locate_file(filename)).resolve()
            require(contained(selected, approved), "dependency metadata escapes pinned installation: "+name)
            file_hash = digest(selected)
            require(frozen.get(str(selected).replace("\\", "/")) == file_hash,
                    "dependency metadata differs from frozen catalog: "+name)
            files.append({"path": str(selected), "sha256": file_hash})
        require(bool(files), "dependency has no verifiable installed metadata: "+name)
        result.append({"module": name, "distribution": name,
                       "version": distribution.version, "path": str(path), "sha256": actual, "files": files})
    require(bool(result), "empty dependency manifest")
    return result

class TreeMonitor:
    def __init__(self, psutil, attempt, deadline_ns):
        self.psutil = psutil
        self.process = psutil.Process()
        self.attempt = attempt
        self.stop_event = threading.Event()
        self.error = None
        self.peak = 0
        self.identities = {}
        self.registry_lock = threading.RLock()
        self.finished = False
        self.capture_errors = []
        self.deadline_ns = deadline_ns
        self.pinned_descendants = set()
        self.affinity_audit = {}
        self.thread = threading.Thread(target=self.loop, name="attempt-native-accounting", daemon=True)

    def capture(self):
        """Durably capture ownership before any fatal resource interrogation."""
        with self.registry_lock:
            try:
                processes = [self.process]+self.process.children(recursive=True)
            except BaseException as error:
                self.capture_errors.append("tree discovery: "+str(error))
                processes = [self.process]
            captured = []
            for process in processes:
                try:
                    identity = (process.pid, process.create_time())
                except self.psutil.NoSuchProcess:
                    continue  # This process has disappeared; no live ownership remains.
                except BaseException as error:
                    self.capture_errors.append("creation identity: "+str(error))
                    continue
                if process.pid != self.process.pid:
                    self.identities[identity] = {"pid": identity[0], "creation_time": identity[1]}
                captured.append((process, identity))
            try:
                atomic(self.attempt/"owned-processes.json",
                       sorted(self.identities.values(), key=lambda row: (row["pid"], row["creation_time"])))
            except BaseException as error:
                self.capture_errors.append("registry persistence: "+str(error))
                raise
            require(not self.capture_errors, "live ownership capture/persistence uncertainty")
            return captured

    def pin_new_descendant(self, process, identity):
        """Pin one newly captured child once, after verifying current ancestry."""
        if process.pid == self.process.pid or identity in self.pinned_descendants:
            return
        current = self.psutil.Process(identity[0])
        require(current.create_time() == identity[1], "captured descendant PID was reused before affinity pin")
        parent = current.parent()
        root_identity = (self.process.pid, self.process.create_time())
        ancestry = set()
        while parent is not None:
            parent_identity = (parent.pid, parent.create_time())
            require(parent_identity not in ancestry, "cyclic process ancestry during ownership verification")
            ancestry.add(parent_identity)
            if parent_identity == root_identity:
                break
            parent = parent.parent()
        require(parent is not None, "captured process is no longer a creation-matched owned descendant")
        row = {"pid": identity[0], "creation_time": identity[1], "status": "IDENTITY_VERIFIED"}
        self.affinity_audit[identity] = row
        with self.registry_lock:
            atomic(self.attempt/"descendant-affinity.json", list(self.affinity_audit.values()))
        try:
            row.update(name=current.name(), exe=current.exe(), before_affinity=current.cpu_affinity(),
                       status="PIN_PENDING")
            with self.registry_lock:
                atomic(self.attempt/"descendant-affinity.json", list(self.affinity_audit.values()))
            self.pinned_descendants.add(identity)
            current.cpu_affinity([0])
            row["after_affinity"] = current.cpu_affinity()
            require(row["after_affinity"] == [0], "new owned descendant affinity pin was ineffective")
            row["status"] = "PINNED_ONCE"
        except BaseException as error:
            row["status"] = "PIN_FAILED"
            row["error"] = str(error)
            raise
        finally:
            with self.registry_lock:
                atomic(self.attempt/"descendant-affinity.json", list(self.affinity_audit.values()))

    def sample(self):
        captured = self.capture()
        require(time.perf_counter_ns() < self.deadline_ns-1_000_000_000,
                "owned workload reached parent deadline cleanup reserve")
        total = 0
        peak_sum = 0
        for process, identity in captured:
            try:
                require(process.create_time() == identity[1], "process identity changed during accounting")
                self.pin_new_descendant(process, identity)
                affinity = process.cpu_affinity()
                require(affinity == [0], "process affinity changed after initial pin: pid="+
                        str(identity[0])+" creation="+str(identity[1])+" affinity="+repr(affinity))
                memory = process.memory_info()
            except self.psutil.NoSuchProcess:
                continue
            total += memory.rss
            peak_sum += getattr(memory, "peak_wset", memory.rss)
            self.peak = max(self.peak, total)
            require(getattr(memory, "peak_wset", memory.rss) <= CAP_BYTES, "individual process exceeded 2 GiB peak RSS")
        self.peak = max(self.peak, total)
        require(total <= CAP_BYTES, "process tree exceeded 2 GiB RSS")
        require(peak_sum <= CAP_BYTES, "conservative sum of live process peak RSS exceeded 2 GiB")
        require(len(self.identities) <= 64, "owned descendant registry bound exceeded")

    def fatal(self, error):
        """One total cleanup interval; only creation-matched captured children."""
        self.error = str(error)
        self.stop_event.set()
        deadline_ns = min(time.perf_counter_ns()+1_000_000_000, self.deadline_ns)
        uncertainty = list(self.capture_errors)
        waiting = []
        try:
            try:
                self.capture()
            except BaseException as capture_error:
                uncertainty.append("final ownership capture: "+str(capture_error))
            with self.registry_lock:
                identities = tuple(self.identities)
            for pid, creation in identities:
                if time.perf_counter_ns() >= deadline_ns:
                    uncertainty.append("total cleanup interval exhausted")
                    break
                try:
                    process = self.psutil.Process(pid)
                    if process.create_time() != creation:
                        continue  # Recorded owner exited; reused PID belongs to somebody else.
                    process.kill()
                    waiting.append(process)
                except self.psutil.NoSuchProcess:
                    continue
                except BaseException as cleanup_error:
                    uncertainty.append("matched child cleanup: "+str(cleanup_error))
            remaining = max(0., (deadline_ns-time.perf_counter_ns())/1_000_000_000.)
            if waiting:
                _, alive = self.psutil.wait_procs(waiting, timeout=remaining)
                if alive:
                    uncertainty.append("matched descendants remain live after total cleanup interval")
            try:
                captured = self.capture()
                if any(process.pid != self.process.pid for process, identity in captured):
                    uncertainty.append("live descendants remain or appeared during cleanup")
            except BaseException as capture_error:
                uncertainty.append("post-cleanup ownership uncertainty: "+str(capture_error))
        except BaseException as cleanup_error:
            uncertainty.append("cleanup accounting failed: "+str(cleanup_error))
        try:
            if uncertainty:
                atomic(self.attempt/"cleanup-uncertainty.json", {
                    "status": "CLEANUP_UNCERTAIN", "errors": uncertainty,
                    "action": "null remaining authority; refuse another launch",
                })
            atomic(self.attempt/"resource-failure.json", {
                "status": "RESOURCE_ACCOUNTING_FAILURE", "error": self.error,
                "peak_tree_rss_bytes": self.peak, "cleanup_deadline_perf_counter_ns": deadline_ns,
                "parent_deadline_perf_counter_ns": self.deadline_ns,
                "cleanup_uncertain": bool(uncertainty),
            })
        finally:
            os._exit(125)

    def loop(self):
        while not self.stop_event.wait(.05):
            try:
                self.sample()
            except BaseException as error:
                self.fatal(error)

    def start(self):
        try:
            self.sample()
        except BaseException as error:
            self.fatal(error)
        self.thread.start()

    def finish(self):
        try:
            self.stop_event.set()
            self.thread.join(timeout=2.)
            require(not self.thread.is_alive(), "resource monitor did not stop")
            self.sample()
            deferred = []
            for process, identity in self.capture():
                if process.pid == self.process.pid:
                    continue
                try:
                    current = self.psutil.Process(identity[0])
                    require(current.create_time() == identity[1], "terminal descendant identity changed")
                    exe = current.exe()
                    require(identity in self.pinned_descendants and
                            Path(exe).resolve() == Path("C:/Windows/System32/conhost.exe").resolve(),
                            "attempt left live computational descendant: pid="+str(identity[0])+" exe="+exe)
                    require(current.cpu_affinity() == [0], "deferred console affinity changed")
                    deferred.append({"pid": identity[0], "creation_time": identity[1],
                                     "name": current.name(), "exe": exe,
                                     "terminal_cleanup_owner": "root supervisor"})
                except self.psutil.NoSuchProcess:
                    continue
            atomic(self.attempt/"deferred-console-cleanup.json", deferred)
            require(self.error is None, "resource monitor failed")
            self.finished = True
        except BaseException as error:
            self.fatal(error)


class Results:
    def __init__(self):
        self.collected = 0
        self.counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report):
        if report.skipped:
            self.counts["skipped"] += 1
        elif report.when == "call":
            self.counts[report.outcome] += 1
        elif report.failed:
            self.counts["errors"] += 1


def solver_installation_inventory(manifest):
    binding = manifest["solver_artifact"]
    require(Path(binding["site"]).resolve() == SOLVER_SITE.resolve(), "solver site binding differs")
    require(digest(binding["installation_receipt"]) == binding["installation_receipt_sha256"],
            "immutable solver installation receipt changed")
    require(digest(binding["wheel"]) == binding["wheel_sha256"], "immutable solver wheel changed")
    expected = {name.removeprefix("src/"): value for name, value in binding["python_files"].items()}
    require(len(expected) == 627, "immutable solver source inventory is not627")
    with zipfile.ZipFile(binding["wheel"]) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "solver wheel duplicate members")
        actual = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in names if not name.endswith("/")}
        require(actual == binding["wheel_files"], "immutable full solver wheel catalog changed")
        require({name: value for name, value in actual.items() if name.endswith(".py")} == expected,
                "solver wheel Python bytes differ from frozen source")
    actual_site = {path.relative_to(SOLVER_SITE).as_posix(): digest(path)
                   for path in sorted(SOLVER_SITE.rglob("*")) if path.is_file()}
    require(actual_site == binding["site_files"], "immutable full solver site catalog changed")
    require({name: value for name, value in actual_site.items() if name.endswith(".py")} == expected,
            "solver installed source bytes differ")
    return {"receipt_sha256": binding["installation_receipt_sha256"],
            "wheel_sha256": binding["wheel_sha256"], "python_files": len(expected), "site_files": actual_site}


def installation_inventory(manifest):
    record = read_json(HOME/"installation-v3.json")
    require(record["manifest_sha256"] == digest(HOME/"manifest-v3.json") and
            record["candidate_commit"] == manifest["candidate_commit"], "consumer installation source binding differs")
    wheel = Path(record["wheel"]).resolve()
    require(contained(wheel, HOME) and digest(wheel) == record["wheel_sha256"], "consumer wheel identity changed")
    expected = {name.removeprefix("src/"): value for name, value in manifest["source_files"].items()}
    require(len(expected) == manifest["consumer_python_files"], "consumer frozen source count differs")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "consumer wheel contains duplicate members")
        actual = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in names if not name.endswith("/")}
        require(actual == record["wheel_files"], "full consumer wheel catalog changed")
        require({name: value for name, value in actual.items() if name.endswith(".py")} == expected,
                "consumer wheel Python bytes differ from frozen source")
    site_files = {path.relative_to(SITE).as_posix(): digest(path)
                  for path in sorted(SITE.rglob("*")) if path.is_file()}
    require(site_files == record["site_files"], "full consumer installed file inventory changed")
    require({name: value for name, value in site_files.items() if name.endswith(".py")} == expected,
            "consumer installed Python bytes differ from frozen source")
    return {"receipt_sha256": digest(HOME/"installation-v3.json"), "wheel_sha256": record["wheel_sha256"],
            "python_files": len(expected), "site_files": site_files}

def build_install(manifest, attempt):
    require(not SITE.exists() and not (HOME/"installation-v3.json").exists(), "build/install is one-shot; preserve existing artifacts")
    staging = attempt/"build-source"
    staging.mkdir()
    for relative, expected in {**manifest["source_files"], **manifest["metadata_files"]}.items():
        source = checked_relative(relative)
        require(digest(source) == expected, "build input changed: "+relative)
        destination = staging/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    wheels = attempt/"wheel"
    commands = [[str(PYTHON), "-I", "-B", "-m", "build", "--wheel", "--no-isolation", "--outdir", str(wheels), str(staging)]]
    executions = []
    for index, command in enumerate(commands):
        atomic(attempt/"build-install-commands.json", commands)
        started_ns = time.perf_counter_ns()
        with (attempt/("build-"+str(index)+".stdout.log")).open("wb") as out, (attempt/("build-"+str(index)+".stderr.log")).open("wb") as err:
            completed = subprocess.run(command, cwd=staging, stdout=out, stderr=err, check=False)
        executions.append({"command": command, "cwd": str(staging), "exit_code": completed.returncode,
                           "process_wall_ns": time.perf_counter_ns()-started_ns})
        atomic(attempt/"build-install-executions.json", executions)
        require(completed.returncode == 0, "isolated wheel build failed; inspect command logs")
    selected = list(wheels.glob("*.whl"))
    require(len(selected) == 1, "build must produce exactly one wheel")
    wheel = selected[0]
    expected = {name.removeprefix("src/"): value for name, value in manifest["source_files"].items()}
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        wheel_files = {name: hashlib.sha256(archive.read(name)).hexdigest()
                       for name in names if not name.endswith("/")}
        require(len(names) == len(set(names)) and
                {name: hashlib.sha256(archive.read(name)).hexdigest() for name in names if name.endswith(".py")} == expected,
                "wheel source-byte identity failed before install")
    command = [str(PYTHON), "-I", "-B", "-m", "pip", "--disable-pip-version-check", "--no-input", "install", "--no-deps", "--no-index", "--no-compile", "--target", str(SITE), str(wheel)]
    commands.append(command)
    atomic(attempt/"build-install-commands.json", commands)
    started_ns = time.perf_counter_ns()
    with (attempt/"install.stdout.log").open("wb") as out, (attempt/"install.stderr.log").open("wb") as err:
        completed = subprocess.run(command, cwd=attempt, stdout=out, stderr=err, check=False)
    executions.append({"command": command, "cwd": str(attempt), "exit_code": completed.returncode,
                       "process_wall_ns": time.perf_counter_ns()-started_ns})
    atomic(attempt/"build-install-executions.json", executions)
    require(completed.returncode == 0, "isolated target install failed; inspect command logs")
    atomic(HOME/"installation-v3.json", {"manifest_sha256": digest(HOME/"manifest-v3.json"),
           "candidate_commit": manifest["candidate_commit"], "wheel": str(wheel), "wheel_sha256": digest(wheel),
           "wheel_files": wheel_files,
           "site_files": {path.relative_to(SITE).as_posix(): digest(path) for path in sorted(SITE.rglob("*")) if path.is_file()}})
    return installation_inventory(manifest)


def main():
    global ATOMIC_DEADLINE_NS, CONSUMER_ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True,
                        choices=("source-controls", "build-install", "installed-controls", "public-example", "affected-legacy"))
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", args.attempt), "invalid unique attempt identity")
    authority = read_json(HOME/"authority-v3.json")
    require(authority["status"] == "APPROVED", "fresh development authority is not APPROVED")
    require(bool(authority["approval_evidence"]) and authority["total_seconds"] == 240 and
            authority["reserve_seconds"] == 60, "approval evidence or approved budget mismatch")
    manifest_path = HOME/"manifest-v3.json"
    manifest = read_json(manifest_path)
    require(re.fullmatch(r"[0-9a-f]{40}", manifest["candidate_commit"]), "full frozen product commit identity required")
    require(digest(manifest_path) == authority["manifest_sha256"], "authority/manifest binding mismatch")
    proposal_path = HOME/"verification-proposal-v3.json"
    require(digest(proposal_path) == authority["proposal_sha256"] == manifest["proposal_sha256"],
            "authority/proposal binding mismatch")
    require(Path(manifest["repository"]).resolve() == ROOT, "manifest repository mismatch")
    require(args.phase in manifest["phases"], "phase not declared in frozen manifest")
    phase = manifest["phases"][args.phase]
    require(Path(sys.executable).resolve() == PYTHON.resolve(), "unapproved Python executable")
    require(sys.flags.isolated == 1 and sys.flags.dont_write_bytecode == 1 and sys.flags.optimize == 0,
            "entry requires Python -I -B with optimize 0")
    require(Path.cwd().resolve() == HOME, "entry must launch in the supervised runner directory")
    attempt = HOME/args.attempt
    require(attempt.is_dir(), "supervisor has not prepared this unique attempt")
    receipt = read_json(attempt/"launch-inputs.json")
    require(receipt["attempt"] == args.attempt and receipt["phase"] == args.phase and
            receipt["manifest_sha256"] == authority["manifest_sha256"], "launch receipt does not bind this attempt")
    require(receipt["monotonic_clock"] == "WINDOWS_QUERY_PERFORMANCE_COUNTER" and
            "queryperformancecounter" in time.get_clock_info("perf_counter").implementation.lower(),
            "parent/entry monotonic clock binding mismatch")
    deadline_ns = receipt["monotonic_deadline_ns"]
    require(type(deadline_ns) is int and time.perf_counter_ns() < deadline_ns < 2**63,
            "parent monotonic deadline is not a finite future integer")
    ATOMIC_DEADLINE_NS = deadline_ns
    require(not any((attempt/name).exists() for name in
                    ("result.json", "runtime-before.json", "junit.xml", "owned-processes.json")),
            "attempt already contains entry evidence")
    os.chdir(ROOT)
    before_ns = time.perf_counter_ns()
    result = {"phase": args.phase, "attempt": args.attempt, "status": "STARTED",
              "pid": os.getpid(), "command": [sys.executable]+sys.orig_argv[1:],
              "parent_deadline_perf_counter_ns": deadline_ns,
              "perf_counter_implementation": time.get_clock_info("perf_counter").implementation}
    monitor = None
    try:
        for name, expected in ENVIRONMENT.items():
            require(os.environ.get(name) == expected, "supervisor runtime setting mismatch: "+name)
        require("PYTEST_ADDOPTS" not in os.environ and "PYTEST_PLUGINS" not in os.environ,
                "ambient pytest plugin/options injection")
        CONSUMER_ROOT = ROOT/"src/anyfem" if args.phase == "source-controls" else SITE/"anyfem"
        preserved = [path for path in sys.path if path and (
            contained(Path(path), BASE_DEPENDENCIES) or
            contained(Path(path), Path("C:/Python/Python314/Lib")) or
            contained(Path(path), Path("C:/Python/Python314/DLLs")) or
            Path(path).resolve() == Path("C:/Python/Python314/python314.zip").resolve())]
        source_dependencies = [str(Path(root).parent) for roots in manifest["allowed_module_roots"].values()
                               for root in roots]
        sys.path[:] = list(dict.fromkeys([str(CONSUMER_ROOT.parent), str(SOLVER_SITE),
            str(ROOT/"tests"), str(ROOT/"examples"), str(ROOT), str(DEPENDENCIES)] +
            source_dependencies + preserved))
        psutil = importlib.import_module("psutil")
        process = psutil.Process()
        process.cpu_affinity([0])
        require(process.cpu_affinity() == [0], "effective affinity must be exactly [0]")
        monitor = TreeMonitor(psutil, attempt, deadline_ns)
        monitor.start()
        source_before = source_inventory(manifest)
        result["source_before"] = source_before
        dependencies_before = dependency_inventory(manifest, attempt, "before")
        result["dependencies_before"] = dependencies_before
        numpy = importlib.import_module("numpy")
        scipy = importlib.import_module("scipy.linalg")
        numba = importlib.import_module("numba")
        require(numba.config.DISABLE_JIT == 1 and numba.config.NUMBA_NUM_THREADS == 1 and
                numba.get_num_threads() == 1,
                "effective Numba configuration mismatch")
        threadpoolctl = importlib.import_module("threadpoolctl")
        initial_pools = threadpoolctl.threadpool_info()
        result["threadpools_initial"] = initial_pools
        require(bool(initial_pools) and all(row["num_threads"] == 1 for row in initial_pools),
                "initial BLAS thread count mismatch before enforcement")
        with threadpoolctl.threadpool_limits(limits=1):
            pools = threadpoolctl.threadpool_info()
            require(bool(pools) and all(row["num_threads"] == 1 for row in pools), "effective BLAS thread count mismatch")
            result["solver_installation_before"] = solver_installation_inventory(manifest)
            if args.phase not in ("source-controls", "build-install"):
                result["installation_before"] = installation_inventory(manifest)
            importlib.import_module("anysolver")
            if args.phase != "build-install":
                importlib.import_module("anyfem")
            observed_modules = []
            for observed_name, observed_module in sorted(tuple(sys.modules.items())):
                observed_location = getattr(observed_module, "__file__", None)
                if observed_location is not None:
                    observed_modules.append({"module": observed_name, "path": str(Path(observed_location).resolve())})
            atomic(attempt/"dependency-modules-observed-before.json", observed_modules)
            result.update(source_before=source_before, dependencies_before=dependencies_before,
                          solver_modules_before=module_inventory(manifest), affinity=process.cpu_affinity(),
                          dependency_modules_before=dependency_module_inventory(manifest),
                          python_flags={"isolated": sys.flags.isolated, "dont_write_bytecode": sys.flags.dont_write_bytecode,
                                        "optimize": sys.flags.optimize},
                          numba_before={"disable_jit": numba.config.DISABLE_JIT,
                                        "threads": numba.config.NUMBA_NUM_THREADS,
                                        "actual_threads": numba.get_num_threads()},
                          threadpools_initial=initial_pools,
                          environment={name: os.environ[name] for name in ENVIRONMENT}, threadpools_before=pools)
            atomic(attempt/"runtime-before.json", result)
            if args.phase == "build-install":
                result["installation_after"] = build_install(manifest, attempt)
                result.update(tests=1, passed=1, failed=0, errors=0, skipped=0)
            else:
                pytest = importlib.import_module("pytest")
                results = Results()
                selections = phase["tests"]
                require(type(selections) is list and bool(selections), "empty approved phase selection")
                for selection in selections:
                    require(type(selection) is str and selection.startswith("tests/"), "non-test selection refused")
                    checked_relative(selection.split("::", 1)[0])
                arguments = ["-x", "-q", "-p", "no:cacheprovider", "-o", "addopts=", "-o", "pythonpath=",
                             "--basetemp="+str(attempt/"pytest-temp"),
                             "--junitxml="+str(attempt/"junit.xml")]+selections
                if phase.get("exclude_expression"):
                    require(phase["exclude_expression"] == "not public_checkpoint_example",
                            "unexpected control exclusion expression")
                    arguments += ["-k", phase["exclude_expression"]]
                exit_code = int(pytest.main(arguments, plugins=[results]))
                result.update(pytest_arguments=arguments, pytest_exit_code=exit_code,
                              collected=results.collected, counts=results.counts, tests=results.collected,
                              **results.counts)
                require(exit_code == 0, "pytest phase failed")
                require(results.counts["failed"] == results.counts["errors"] == results.counts["skipped"] == 0,
                        "phase has failures/errors/skips")
                require(results.collected > 0 and results.counts["passed"] == results.collected,
                        "phase must collect and pass every selected case")
                require(phase["expected_cases"] is None or results.collected == phase["expected_cases"],
                        "collected test count differs from frozen phase")
            if args.phase != "source-controls":
                result["installation_after"] = installation_inventory(manifest)
            if "installation_before" in result:
                require(result["installation_after"] == result["installation_before"], "wheel/site changed during installed verification")
            if args.phase != "source-controls":
                require(not any(contained(Path(path), ROOT/"src") for path in sys.path if path),
                        "consumer source fallback appeared in installed verification sys.path")
            result["solver_installation_after"] = solver_installation_inventory(manifest)
            require(result["solver_installation_after"] == result["solver_installation_before"],
                    "immutable solver artifact changed during consumer attempt")
            observed_modules_after = []
            for observed_name, observed_module in sorted(tuple(sys.modules.items())):
                observed_location = getattr(observed_module, "__file__", None)
                if observed_location is not None:
                    observed_modules_after.append({"module": observed_name, "path": str(Path(observed_location).resolve())})
            atomic(attempt/"dependency-modules-observed-after.json", observed_modules_after)
            result.update(source_after=source_inventory(manifest), dependencies_after=dependency_inventory(manifest, attempt, "after"),
                          solver_modules_after=module_inventory(manifest), dependency_modules_after=dependency_module_inventory(manifest),
                          threadpools_after=threadpoolctl.threadpool_info())
            require(result["source_after"] == source_before and result["dependencies_after"] == dependencies_before,
                    "source/dependency changed during attempt")
            require(all(row["num_threads"] == 1 for row in result["threadpools_after"]), "BLAS runtime changed")
            require(numba.config.DISABLE_JIT == 1 and numba.config.NUMBA_NUM_THREADS == 1 and
                    numba.get_num_threads() == 1 and
                    process.cpu_affinity() == [0] and sys.flags.optimize == 0 and
                    all(os.environ.get(name) == expected for name, expected in ENVIRONMENT.items()),
                    "effective runtime changed during attempt")
            result["numba_after"] = {"disable_jit": numba.config.DISABLE_JIT,
                                     "threads": numba.config.NUMBA_NUM_THREADS,
                                     "actual_threads": numba.get_num_threads()}
            atomic(attempt/"runtime-after.json", {key: value for key, value in result.items()
                   if key.endswith("_after") or key in ("phase", "attempt", "affinity", "environment", "python_flags")})
        monitor.finish()
        result.update(status="PASSED", peak_tree_rss_bytes=monitor.peak)
        return 0
    except BaseException as error:
        result.update(status="FAILED", error=str(error), traceback=traceback.format_exc())
        # Save the triggering failure before preservation or terminal cleanup
        # can itself fail or terminate this owned process.
        atomic(attempt/"primary-failure.json", {
            "status": "FAILED", "error": result["error"], "traceback": result["traceback"],
            "phase": args.phase, "attempt": args.attempt,
            "process_wall_ns_at_failure": time.perf_counter_ns()-before_ns,
        })
        if monitor is not None:
            result["peak_tree_rss_bytes"] = monitor.peak
        return 1
    finally:
        preservation_error = None
        try:
            if "source_before" in result and "source_after" not in result:
                result["source_after"] = source_inventory(manifest)
                require(result["source_after"] == result["source_before"], "source preservation check failed")
            if "dependencies_before" in result and "dependencies_after" not in result:
                result["dependencies_after"] = dependency_inventory(manifest, attempt, "preservation")
                require(result["dependencies_after"] == result["dependencies_before"], "dependency preservation check failed")
            if "solver_installation_before" in result and "solver_installation_after" not in result:
                result["solver_installation_after"] = solver_installation_inventory(manifest)
                require(result["solver_installation_after"] == result["solver_installation_before"],
                        "immutable solver artifact preservation failed")
                result["dependency_modules_after"] = dependency_module_inventory(manifest)
            if "installation_before" in result and "installation_after" not in result:
                result["installation_after"] = installation_inventory(manifest)
                require(result["installation_after"] == result["installation_before"], "installed artifact preservation check failed")
            if "solver_modules_after" not in result:
                result["solver_modules_after"] = module_inventory(manifest)
        except BaseException as error:
            preservation_error = str(error)
            result.update(status="FAILED", preservation_error=preservation_error)
        if monitor is not None and not monitor.finished:
            try:
                monitor.finish()
                result["peak_tree_rss_bytes"] = monitor.peak
            except BaseException as error:
                preservation_error = str(error)
                result.update(status="FAILED", resource_cleanup_error=preservation_error)
        result["process_wall_ns"] = time.perf_counter_ns()-before_ns
        atomic(attempt/"result.json", result)
        if preservation_error is not None:
            raise RuntimeError("post-attempt preservation/accounting failed: "+preservation_error)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("REFUSED: "+str(error), file=sys.stderr)
        raise SystemExit(126)
