"""Opt-in thread route for structural preparation: run plan + apply in a free-threaded child process.

OFF by default.  When enabled (``ANYFEM_THREAD_ROUTE=1`` or ``configure(enabled=True)``) and eligible, the batch plan/apply of
``structural_preparation.prepare_structural_connectivity`` runs in a child interpreter on a free-threaded CPython
(``ANYGEOMETRY_PLANNING_THREADS=<n>`` is the only ANYgeometry interface used).  Every failure of the route ends in the
ordinary serial in-process path on the untouched geometry; a typed geometry refusal is reported as is and never retried.

Contract with the child (``_thread_child.py``): the document goes in as ``to_dict`` JSON (checksum verified by the child's
``from_dict``); the child runs the PLAN stage only and returns an atomic envelope (job id + body sha256) holding the encoded
plan packet of ``anygeometry.plan_adoption``.  The parent adopts the plan with ``adopt_intersection_plan`` (every check against
the parent's own model, operands, policy and job) and runs the ordinary ``apply_intersections`` with one policy built in the
parent, so the owner's preparation receipts are minted by the owner's own code in this process.  After the apply the
route checks the genuine proof (``has_current_intersection_preparation`` AND the edge-subcurve / face-preimage bindings);
a failure there cannot be rolled back soundly, so it raises ``AdoptedPreparationFailed`` and disables the route for the session.
Every refusal BEFORE the apply (adoption, packet, runtime, child) falls back to the serial path on the untouched geometry.  Guarantee: fresh owner application of a TRUSTED
child plan (same source digest, runtime lock, NumPy pin, GIL off, smoke digest); a self-consistent wrong plan is not detected.
Outcomes: ``ok`` / ``refusal`` / ``route_unavailable`` / ``cancelled``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from anygeometry.errors import GeometryError

from . import _batch_prepare

__all__ = [
    "ChildOutcome",
    "configure",
    "locate_runtime",
    "check_health",
    "route_enabled",
    "try_prepare",
]

LOG = logging.getLogger("anyfem.thread_route")
EXPECT_NUMPY = "2.4.6"
EXPECT_CACHE_TAG = "cpython-314"  # CPython 3.14 (the free-threaded build reports the same tag; Py_GIL_DISABLED is checked separately)
EXPECT_VERSION_PREFIX = "3.14."
ENV_ENABLE = "ANYFEM_THREAD_ROUTE"
ENV_RUNTIME = "ANYGEOMETRY_THREAD_RUNTIME"
ENV_THREADS = "ANYFEM_THREAD_COUNT"
ENV_MIN_FACES = "ANYFEM_THREAD_MIN_FACES"
ENV_APP_DIR = "ANYFEM_APP_DIR"
DEFAULT_MIN_FACES = 40
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000
OK, REFUSAL, UNAVAILABLE, CANCELLED = "ok", "refusal", "route_unavailable", "cancelled"
HERE = Path(__file__).resolve().parent
CHILD_MAIN = HERE / "_thread_child.py"
CHILD_SOURCES = (HERE / "_batch_prepare.py", HERE / "_thread_child.py")

_override: dict = {}
_session: dict = {"disabled": ""}  # reason, once the route has been switched off for the rest of the session
last_adoption: dict = {}
_health_cache: dict = {}
_digest_cache: dict = {}
last_outcome: Optional["ChildOutcome"] = None
_INJECT: dict = {}  # tests only: extra keyword arguments merged into every try_prepare call


class AdoptedPreparationFailed(GeometryError):
    """The parent's own apply of an adopted plan did not leave the genuine owner proof (should never happen)."""


class RoutedRefusal(GeometryError):
    """A child refusal; carries the owner's structured diagnostic so ``to_diagnostic()`` chains keep their evidence."""

    diagnostic: Optional[dict] = None

    def to_diagnostic(self):
        return dict(self.diagnostic or {})


def _refusal_error(base, body: dict) -> GeometryError:
    name = body.get("diagnostic_type") or body.get("type") or "GeometryError"
    kind = type(str(name), (RoutedRefusal, base), {})  # same class name as the owner's exception in cause records
    error = kind(str(body["message"]))
    if isinstance(body.get("diagnostic"), dict):
        error.diagnostic = body["diagnostic"]
    else:
        error.to_diagnostic = None  # type: ignore[assignment]  # no evidence was sent: do not pretend to have any
    return error


@dataclass
class ChildOutcome:
    status: str
    reason: str = ""
    body: Optional[dict] = None
    timings: dict = field(default_factory=dict)
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    job_id: str = ""
    temp_dir: str = ""
    temp_removed: bool = False
    stderr_tail: str = ""
    sizes: dict = field(default_factory=dict)


def configure(*, enabled: Optional[bool] = None, threads: Optional[int] = None, min_faces: Optional[int] = None,
              runtime: Optional[str] = None, verify_apply: Optional[bool] = None, reset: bool = False) -> None:
    """Programmatic setting (an application checkbox would call this); ``reset=True`` drops all overrides."""
    if reset:
        _override.clear()
        _session["disabled"] = ""
    for key, value in (("enabled", enabled), ("threads", threads), ("min_faces", min_faces), ("runtime", runtime),
                       ("verify_apply", verify_apply)):
        if value is not None:
            _override[key] = value


def route_enabled() -> bool:
    if "enabled" in _override:
        return bool(_override["enabled"])
    return os.environ.get(ENV_ENABLE, "").strip().lower() in ("1", "on", "true", "yes")


def _min_faces() -> int:
    if "min_faces" in _override:
        return int(_override["min_faces"])
    try:
        return int(os.environ.get(ENV_MIN_FACES, DEFAULT_MIN_FACES))
    except ValueError:
        return DEFAULT_MIN_FACES


def thread_count() -> int:
    value = _override.get("threads", os.environ.get(ENV_THREADS))
    try:
        if value is not None:
            return max(1, min(8, int(value)))
    except ValueError:
        pass
    return max(1, min(8, (os.cpu_count() or 4) // 4))


def _app_dir() -> Path:
    if os.environ.get(ENV_APP_DIR):
        return Path(os.environ[ENV_APP_DIR])
    return HERE.parent.parent  # <app>\src\anyfem -> <app>


def _runtime_root(python: Path) -> Path:
    """Folder holding ``runtime.lock.json``: the runtime root for both ``<root>/python.exe`` and ``<root>/Scripts/python.exe``."""
    return python.parent.parent if python.parent.name in ("Scripts", "bin") else python.parent


def _runtime_python(root: Path) -> Optional[Path]:
    for candidate in (root / "python.exe", root / "Scripts" / "python.exe", root / "bin" / "python"):
        if candidate.is_file():
            return candidate
    return None


def locate_runtime() -> Optional[Path]:
    """Override, then ``<app>\\runtime-314t``.  Never PATH.  Returns the interpreter, or None."""
    override = _override.get("runtime") or os.environ.get(ENV_RUNTIME)
    if override:
        return _runtime_python(Path(override))
    return _runtime_python(_app_dir() / "runtime-314t")


def _parent_source_digest() -> str:
    key = "digest"
    if key not in _digest_cache:
        import anygeometry
        _digest_cache[key] = _batch_prepare.source_digest(Path(anygeometry.__file__).resolve().parent, CHILD_SOURCES)
    return _digest_cache[key]


def _src_paths() -> list:
    import anygeometry
    return [str(Path(anygeometry.__file__).resolve().parent.parent)]


# --------------------------------------------------------------------------------------------- process plumbing

class _KillOnCloseJob:
    """Windows job object with KILL_ON_JOB_CLOSE: the child (and descendants) die when the parent dies."""

    def __init__(self) -> None:
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes as W

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = W.HANDLE
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
        k.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
        k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        k.OpenProcess.restype = W.HANDLE
        k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        k.CloseHandle.argtypes = [W.HANDLE]
        k.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]
        self.k, self.W = k, W

        class BASIC(ctypes.Structure):
            _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("flags", W.DWORD), ("minws", ctypes.c_size_t),
                        ("maxws", ctypes.c_size_t), ("aff", ctypes.c_size_t), ("pc", W.DWORD), ("sc", ctypes.c_int),
                        ("sid", W.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [("v", ctypes.c_uint64 * 6)]

        class EXT(ctypes.Structure):
            _fields_ = [("basic", BASIC), ("io", IO), ("pm", ctypes.c_size_t), ("jm", ctypes.c_size_t),
                        ("ppm", ctypes.c_size_t), ("jpm", ctypes.c_size_t)]

        handle = k.CreateJobObjectW(None, None)
        if not handle:
            return
        info = EXT()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            k.CloseHandle(handle)
            return
        self.handle = handle

    def assign(self, pid: int) -> bool:
        if not self.handle:
            return False
        process = self.k.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE
        if not process:
            return False
        try:
            return bool(self.k.AssignProcessToJobObject(self.handle, process))
        finally:
            self.k.CloseHandle(process)

    def close(self) -> None:
        if self.handle:
            self.k.TerminateJobObject(self.handle, 1)
            self.k.CloseHandle(self.handle)
            self.handle = None


FOLDER_PREFIX = "anyfem-thread-"
STALE_SECONDS = 24 * 3600


def _sweep_stale(temp_root: Optional[str]) -> None:
    """Remove job folders an abruptly killed parent could not clean (older than a day, our prefix only)."""
    try:
        base = Path(temp_root or tempfile.gettempdir())
        now = time.time()
        for entry in base.iterdir():
            if entry.name.startswith(FOLDER_PREFIX) and entry.is_dir() and now - entry.stat().st_mtime > STALE_SECONDS:
                shutil.rmtree(entry, ignore_errors=True)
    except OSError:
        pass


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _remove_tree(folder: Path) -> bool:
    for _ in range(20):
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            return True
        time.sleep(0.1)
    return not folder.exists()


def _child_env(folder: Path, threads: int) -> dict:
    keys = ("SYSTEMROOT", "WINDIR", "COMSPEC", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "LANG")
    keep = {k: v for k, v in os.environ.items() if k.upper() in keys}
    keep.update(TMP=str(folder / "tmp"), TEMP=str(folder / "tmp"), TMPDIR=str(folder / "tmp"),
                PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", ANYGEOMETRY_PLANNING_THREADS=str(threads),
                OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    return keep  # no PATH, no PYTHONPATH, no other ANYGEOMETRY_* from the parent


def _stop(process: subprocess.Popen, job: _KillOnCloseJob) -> None:
    """Kill only our own child: the Popen handle pins the PID against reuse; the job object takes descendants."""
    try:
        process.kill()
    except OSError:
        pass
    job.close()
    try:
        process.wait(30)
    except subprocess.TimeoutExpired:
        pass


def run_child(python: Path, job: dict, document: Optional[bytes], *, threads: int, timeout: float,
              cancellation_check: Optional[Callable[[str], None]] = None,
              progress_cb: Optional[Callable[[dict], None]] = None, temp_root: Optional[str] = None,
              child_main: Optional[Path] = None, python_args: tuple = (), extra_env: Optional[dict] = None,
              poll_seconds: float = 0.02) -> ChildOutcome:
    """Run one child job and classify it.  The child is dead and the temp folder removed when this returns."""
    t_start = time.perf_counter()
    outcome = ChildOutcome(status=UNAVAILABLE)
    folder: Optional[Path] = None
    process: Optional[subprocess.Popen] = None
    job_object = _KillOnCloseJob()
    try:
        _sweep_stale(temp_root)
        try:
            folder = Path(tempfile.mkdtemp(prefix=FOLDER_PREFIX, dir=temp_root))
            outcome.temp_dir = str(folder)
            (folder / "tmp").mkdir()
            job_id = uuid.uuid4().hex
            outcome.job_id = job_id
            spec = dict(job, job_id=job_id, threads=threads, expect_numpy=EXPECT_NUMPY, src_paths=_src_paths(),
                        document=None, parent_pid=os.getpid())
            if document is not None:
                _atomic_write(folder / "document.json", document)
                spec["document"] = "document.json"
                outcome.sizes["document_bytes"] = len(document)
            _atomic_write(folder / "job.json", json.dumps(spec).encode("utf-8"))
            with open(folder / "stdout.txt", "wb") as out, open(folder / "stderr.txt", "wb") as err:
                flags = (BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW) if os.name == "nt" else 0
                process = subprocess.Popen(
                    [str(python), "-I", "-B", *python_args, str(child_main or CHILD_MAIN), str(folder)],
                    stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=str(folder), close_fds=True,
                    env={**_child_env(folder, threads), **(extra_env or {})}, creationflags=flags,
                    preexec_fn=(lambda: os.nice(10)) if os.name != "nt" else None)
        except OSError as exc:  # missing/unstartable interpreter, disk full on the temp folder, ...
            outcome.reason = "cannot start child: %r" % (exc,)
            return outcome
        outcome.pid = process.pid
        if os.name == "nt" and not job_object.assign(process.pid):
            # Without kill-on-close containment the child could outlive a dying parent: fail closed.
            _stop(process, job_object)
            outcome.exit_code = process.poll()
            outcome.reason = "child could not be placed in a kill-on-close job object"
            return outcome
        t_spawned = time.perf_counter()
        deadline = t_spawned + timeout
        state = dict(offset=0, pending="")
        progress_path = folder / "progress.jsonl"

        def drain() -> None:
            if not progress_path.exists():
                return
            with open(progress_path, "rb") as handle:
                handle.seek(state["offset"])
                chunk = handle.read()
            state["offset"] += len(chunk)
            *lines, state["pending"] = (state["pending"] + chunk.decode("utf-8", "replace")).split("\n")
            for line in lines:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if progress_cb is not None:
                    progress_cb(event)

        forced = None
        try:
            while True:
                try:
                    drain()
                except OSError as exc:  # progress file unreadable: the route is unavailable, not cancelled
                    forced = "progress file unreadable: %r" % (exc,)
                    break
                if cancellation_check is not None:
                    cancellation_check("structural intersection batch")  # raises the caller's cancellation
                code = process.poll()
                if code is not None:
                    break
                if time.perf_counter() > deadline:
                    forced = "timeout after %.1f s" % timeout
                    break
                time.sleep(poll_seconds)
        except BaseException:
            _stop(process, job_object)
            outcome.status = CANCELLED
            raise
        if forced:
            _stop(process, job_object)
            outcome.exit_code = process.poll()
            outcome.reason = forced
            return outcome
        try:
            drain()
        except OSError:
            pass  # the result envelope is authoritative; progress is advisory
        outcome.exit_code = process.returncode
        outcome.timings["child_wall_s"] = time.perf_counter() - t_spawned
        result_path = folder / "result.json"
        if not result_path.is_file():
            outcome.reason = "child exited with code %s and no result" % process.returncode
            try:
                outcome.stderr_tail = (folder / "stderr.txt").read_text("utf-8", "replace")[-1500:]
            except OSError:
                pass
            return outcome
        try:
            raw = result_path.read_bytes()
            outcome.sizes["result_bytes"] = len(raw)
            envelope = json.loads(raw)
            body = envelope["body"]
            body_bytes = json.dumps(body, sort_keys=True, separators=(",", ":"), default=repr).encode("utf-8")
            if envelope.get("format") != 1 or envelope.get("job_id") != job_id:
                raise ValueError("result envelope does not belong to this job")
            if envelope.get("body_sha256") != hashlib.sha256(body_bytes).hexdigest():
                raise ValueError("result checksum mismatch")
            status = envelope["status"]
            if not isinstance(body, dict):
                raise ValueError("result body is not an object")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            outcome.reason = "malformed result: %s" % exc
            return outcome
        if process.returncode != 0:
            outcome.reason = "child exit code %s despite a result" % process.returncode
            return outcome
        outcome.body = body
        if status == "ok":
            outcome.status = OK
        elif status == "refusal":
            if (body.get("phase") not in (1, 2) or not isinstance(body.get("type"), str)
                    or not isinstance(body.get("message"), str) or not body["message"]):
                outcome.reason = "malformed refusal body"
                return outcome
            outcome.status, outcome.reason = REFUSAL, "%s: %s" % (body["type"], body["message"])
        else:
            outcome.status, outcome.reason = UNAVAILABLE, str(body.get("reason", status))
        return outcome
    finally:
        if process is not None and process.poll() is None:
            _stop(process, job_object)
        job_object.close()
        if folder is not None:
            outcome.temp_removed = _remove_tree(folder)
        outcome.timings["total_s"] = time.perf_counter() - t_start


# ------------------------------------------------------------------------------------------------ health check

def _check_lock(lock: Path) -> str:
    """Optional ``runtime.lock.json`` beside the runtime: when present it must parse and may not contradict the pins."""
    if not lock.is_file():
        return ""
    try:
        data = json.loads(lock.read_bytes())
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except ValueError as exc:
        return "runtime.lock.json is malformed: %s" % exc
    if "numpy" in data and data["numpy"] != EXPECT_NUMPY:
        return "runtime.lock.json pins NumPy %s, expected %s" % (data["numpy"], EXPECT_NUMPY)
    if "source_digest" in data and data["source_digest"] != _parent_source_digest():
        return "runtime.lock.json source digest differs from this application's"
    return ""


def check_health(python: Path, *, timeout: float = 120.0, lock_path: Optional[Path] = None,
                 cancellation_check: Optional[Callable[[str], None]] = None, **injection) -> ChildOutcome:
    """Selftest child: GIL off after imports, NumPy pin, source digest, frozen smoke.  Cached by interpreter + lock + sources."""
    try:
        lock = Path(lock_path) if lock_path else _runtime_root(Path(python)) / "runtime.lock.json"
        problem = _check_lock(lock)
        if problem:
            return ChildOutcome(status=UNAVAILABLE, reason=problem)
        lock_hash = hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else ""
        stat = Path(python).stat()
    except OSError as exc:  # unreadable lock, interpreter removed after discovery
        return ChildOutcome(status=UNAVAILABLE, reason="runtime cannot be inspected: %r" % (exc,))
    key = (str(python), stat.st_mtime_ns, stat.st_size, lock_hash, _parent_source_digest(), _batch_prepare.SMOKE_DIGEST,
           EXPECT_NUMPY, tuple(sorted((k, repr(v)) for k, v in injection.items())))
    cached = _health_cache.get(key)
    if cached is not None:
        return cached
    job = dict(kind="selftest", source_digest=_parent_source_digest(), smoke_digest=_batch_prepare.SMOKE_DIGEST)
    outcome = run_child(python, job, None, threads=1, timeout=timeout, cancellation_check=cancellation_check, **injection)
    if outcome.status == OK and lock.is_file():
        try:
            pinned = json.loads(lock.read_bytes()).get("python_version_full")
        except (OSError, ValueError):
            pinned = None
        if pinned is not None and pinned != ((outcome.body or {}).get("health") or {}).get("version_full"):
            outcome.status, outcome.reason = UNAVAILABLE, "runtime.lock.json pins a different interpreter build"
    if outcome.status == OK and (outcome.body or {}).get("smoke") != _batch_prepare.SMOKE_DIGEST:
        outcome.status, outcome.reason = UNAVAILABLE, "smoke digest mismatch"
    if outcome.status in (OK, UNAVAILABLE):
        _health_cache[key] = outcome
    return outcome


# ------------------------------------------------------------------------------------------------ the hook

def _adoption_api():
    """The owner's plan adoption module, or None when this ANYgeometry does not offer it."""
    try:
        from anygeometry import plan_adoption
    except ImportError:
        return None
    if not all(hasattr(plan_adoption, name) for name in ("adopt_intersection_plan", "PlanAdoptionRefused")):
        return None
    return plan_adoption


def _unavailable(reason: str, outcome: Optional[ChildOutcome] = None) -> None:
    global last_outcome
    last_outcome = outcome or ChildOutcome(status=UNAVAILABLE, reason=reason)
    last_outcome.status, last_outcome.reason = UNAVAILABLE, reason
    LOG.warning("thread route unavailable, running serially in-process: %s", reason)
    return None


def disable_for_session(reason: str) -> None:
    _session["disabled"] = reason
    LOG.error("thread route disabled for this session: %s", reason)


def binding_fingerprint(geometry) -> str:
    """sha256 over the owner's authenticated bindings of the CURRENT preparation (edge-subcurve ancestry and face
    preimages), without ids that differ per run.  Raises GeometryError when the geometry carries no usable proof."""
    import anygeometry
    edges = anygeometry.query_prepared_edge_subcurve_preimages(geometry)
    faces = anygeometry.query_prepared_face_preimages(geometry)

    def rows(records):
        return tuple((row.edge_id, row.ancestor.definition.edge_id, tuple(row.interval)) for row in records)

    material = repr((rows(edges.records), rows(edges.alias_records), tuple(edges.unavailable_edge_ids),
                     tuple(edges.coverage), tuple(faces.face_descendants), tuple(faces.coverage)))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _genuine_proof(geometry) -> str:
    """Empty string when the geometry carries the owner's proof AND usable bindings; otherwise the reason."""
    import anygeometry
    if not anygeometry.has_current_intersection_preparation(geometry):
        return "no current owner preparation proof after the apply"
    try:
        binding_fingerprint(geometry)
    except GeometryError as exc:
        return "owner bindings unusable after the apply: %s" % exc
    return ""


def _declined_before_spawn(adoption, geometry, operands) -> str:
    """Owner-side refusals that depend only on the parent's model and operands (journaled, epoch, existing-material or
    bounded routes): the owner adopt call decides them before it looks at the packet, so an empty probe packet is enough
    and no child is wasted on a plan that could never be adopted."""
    try:
        adoption.adopt_intersection_plan(geometry, b"", policy=_batch_prepare.make_policy(None), operands=operands,
                                         job_id="probe", expected_source_checksum="0" * 64)
    except adoption.PlanAdoptionRefused as exc:
        reason = getattr(exc, "reason", "")
        if reason in ("journaled_model", "epoch_model", "registry_route"):
            return reason
    except GeometryError:
        pass
    return ""


def _same_file(first, second) -> bool:
    try:
        return os.path.normcase(str(Path(first).resolve())) == os.path.normcase(str(Path(second).resolve()))
    except (OSError, TypeError, ValueError):
        return False


def _identity_of(health_body: Optional[dict]) -> Optional[dict]:
    """Interpreter identity the health check measured for this runtime (full ``sys.version`` incl. build/compiler)."""
    health = (health_body or {}).get("health")
    if not isinstance(health, dict):
        return None
    keys = ("version_full", "cache_tag", "executable")
    return {key: health.get(key) for key in keys} if all(isinstance(health.get(key), str) for key in keys) else None


def _check_runtime(body: dict, python: Optional[Path] = None, route: Optional[list] = None,
                   identity: Optional[dict] = None) -> str:
    """Per-job wrong-runtime exclusion for EVERY planning answer (plan or refusal): GIL off, NumPy pin, free-threaded
    build, interpreter identity, and a digest over the anygeometry files the child actually loaded.  Returns a reason or ""."""
    import anygeometry
    health = body.get("health")
    if not isinstance(health, dict) or health.get("gil_enabled") is not False:
        return "child did not report a GIL-disabled runtime"
    if health.get("numpy") != EXPECT_NUMPY:
        return "child NumPy %s != %s" % (health.get("numpy"), EXPECT_NUMPY)
    if health.get("gil_disabled_build") is not True:
        return "child is not a free-threaded build"
    version, tag, executable = health.get("version"), health.get("cache_tag"), health.get("executable")
    if (not isinstance(version, str) or not version.startswith(EXPECT_VERSION_PREFIX) or tag != EXPECT_CACHE_TAG
            or not isinstance(executable, str) or (python is not None and not _same_file(executable, python))):
        return "unexpected interpreter identity (version %r, cache tag %r, executable %r)" % (version, tag, executable)
    if identity is not None and {key: health.get(key) for key in identity} != identity:
        return "unexpected interpreter identity: not the build that passed the health check"
    if body.get("gil_final") is not False:
        return "the GIL was enabled (or not reported) after planning"
    if route is not None and body.get("route") != route:
        return "planning configuration differs from this application's (child %r, parent %r)" % (body.get("route"), route)
    modules = body.get("modules")
    if (not isinstance(modules, list) or not modules or not all(isinstance(name, str) for name in modules)):
        return "child reported no loaded-module list"
    try:
        digest = _batch_prepare.files_digest(Path(anygeometry.__file__).resolve().parent, modules)
    except (OSError, ValueError) as exc:
        return "loaded-module digest could not be recomputed: %s" % exc
    if digest != body.get("loaded_digest"):
        return "child loaded different anygeometry code than this application"
    return ""


def _check_plan_body(body: dict, python: Optional[Path] = None, route: Optional[list] = None,
                     identity: Optional[dict] = None) -> tuple:
    """Validate the child's ok body.  Returns (packet bytes, "") or (None, reason)."""
    import base64
    import binascii
    problem = _check_runtime(body, python, route, identity)
    if problem:
        return None, problem
    try:
        packet = base64.b64decode(str(body["packet"]), validate=True)
    except (KeyError, binascii.Error, ValueError):
        return None, "child packet is not valid base64"
    if hashlib.sha256(packet).hexdigest() != body.get("packet_sha256"):
        return None, "child packet checksum mismatch"
    return packet, ""


def try_prepare(geometry, operands, *, cancellation_check: Optional[Callable[[str], None]] = None, **injection):
    """Run plan in the child, adopt + apply in this process.  Returns the applied ``BatchState`` or None (serial path).

    ``injection`` is for tests only (``temp_root``, ``child_main``, ``python_args``, ``extra_env``, ``timeout``, ``python``).
    Contract: nobody else edits the geometry while the call runs (as for the serial path); a change that is noticed
    (stale checksum or revision) never lets a child answer win - the ordinary serial path runs with the caller's operands,
    exactly as it would without the route.
    """
    global last_outcome, last_adoption
    if not route_enabled():
        return None
    if _session["disabled"]:
        return None
    injection = {**_INJECT, **injection}
    from ._preparation_context import _preparation_evidence
    if _preparation_evidence.get():
        return None  # an evidence collector needs the live plan object of this process
    faces = len(geometry.faces)
    if faces < _min_faces():
        return None
    adoption = _adoption_api()
    if adoption is None:
        return _unavailable("this ANYgeometry offers no verified plan adoption API; running serially")
    try:
        parent_route = _batch_prepare.resolved_route()
    except GeometryError as exc:  # the serial path reports the invalid planning configuration itself
        return _unavailable("planning configuration is invalid in this process: %s" % exc)
    declined = _declined_before_spawn(adoption, geometry, operands)
    if declined:
        return _unavailable("plan adoption declines this model before any child is spawned (%s)" % declined)
    python = injection.pop("python", None) or locate_runtime()
    if python is None:
        return _unavailable("runtime missing (set %s or install runtime-314t beside the application)" % ENV_RUNTIME)
    timeout = injection.pop("timeout", max(120.0, 3.0 * faces))
    health_kwargs = {k: v for k, v in injection.items() if k in ("temp_root", "python_args_health", "extra_env", "lock_path")}
    if "python_args_health" in health_kwargs:
        health_kwargs["python_args"] = health_kwargs.pop("python_args_health")
    if "child_main_health" in injection:
        health_kwargs["child_main"] = injection["child_main_health"]
    try:
        health = check_health(python, cancellation_check=cancellation_check, **health_kwargs)
    except OSError as exc:  # e.g. an unreadable source file while computing the source digest
        return _unavailable("health check could not read the runtime or sources: %r" % (exc,))
    if health.status != OK:
        return _unavailable("health check failed: %s" % (health.reason or health.status), health)
    from anygeometry.errors import GeometryError as _GeometryError
    from anygeometry.serialization import to_dict
    verify = bool(_override.get("verify_apply"))
    try:
        source = to_dict(geometry)
        expected_checksum = str(source["checksum"]["value"])
        document = json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
        job = dict(kind="plan", operands=[[h.kind, h.id] for h in operands], source_digest=_parent_source_digest(),
                   verify_apply=verify)
    except (_GeometryError, TypeError, ValueError, OSError) as exc:
        return _unavailable("document cannot be serialised: %s" % exc)
    run_kwargs = {k: v for k, v in injection.items() if k not in ("python_args_health", "lock_path", "child_main_health")}
    outcome = run_child(python, job, document, threads=thread_count(), timeout=timeout,
                        cancellation_check=cancellation_check, **run_kwargs)
    last_outcome = outcome
    last_adoption = {}
    if outcome.status == REFUSAL:
        problem = _check_runtime(outcome.body or {}, python, parent_route, _identity_of(health.body))
        if problem:  # a planning answer from a runtime we do not trust is not final either
            return _unavailable("invalid child refusal: %s" % problem, outcome)
        # A refusal is final only for the geometry it was computed on: if the parent changed meanwhile, the serial
        # path decides on the current geometry instead of the child's stale answer.
        try:
            unchanged = to_dict(geometry)["checksum"]["value"] == expected_checksum
        except (_GeometryError, TypeError, ValueError, OSError):
            unchanged = False
        if not unchanged:
            return _unavailable("stale child refusal: the geometry changed while the child ran", outcome)
        raise _refusal_error(_GeometryError, outcome.body or {})
    if outcome.status != OK:
        return _unavailable(outcome.reason or outcome.status, outcome)
    body = outcome.body or {}
    packet, problem = _check_plan_body(body, python, parent_route, _identity_of(health.body))
    if packet is None:
        return _unavailable("invalid child result: %s" % problem, outcome)
    adopted = {"plan": None}

    def plan_source(policy):
        try:
            adopted["plan"] = adoption.adopt_intersection_plan(
                geometry, packet, policy=policy, operands=operands, job_id=outcome.job_id,
                expected_source_checksum=expected_checksum)
        except adoption.PlanAdoptionRefused as exc:
            _unavailable("plan adoption refused (%s): %s" % (getattr(exc, "reason", "?"), exc), outcome)
        return adopted["plan"]  # None -> plan_apply_phase plans serially on the untouched geometry

    state = _batch_prepare.plan_apply_phase(geometry, operands, cancellation_check=cancellation_check,
                                            plan_source=plan_source)
    if adopted["plan"] is None:
        return state  # adoption refused: the ordinary serial plan + apply has just run
    failure = _genuine_proof(geometry)
    if not failure and verify and body.get("apply_checksum") != to_dict(geometry)["checksum"]["value"]:
        failure = "application checksum differs between child and parent"
    if failure:
        # The owner's own apply has already run here.  Rolling back with restore_topology is NOT sound (measured: it
        # leaves the previous receipts behind and a later serial apply then publishes a preparation receipt whose edge
        # bindings are stale), so this should-never-happen case is a hard, typed failure and the route is switched off
        # for the rest of the session; the caller reports it as a failed preparation and discards the working geometry.
        disable_for_session(failure)
        _unavailable("adopted result failed the owner proof check: %s" % failure, outcome)
        raise AdoptedPreparationFailed("adopted child plan failed the owner proof check after the apply: %s" % failure)
    last_adoption = dict(adopted=True, fingerprint=binding_fingerprint(geometry))
    return state
