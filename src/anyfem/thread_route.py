"""Opt-in thread route for structural preparation: run plan + apply in a free-threaded child process.

OFF by default.  When enabled (``ANYFEM_THREAD_ROUTE=1`` or ``configure(enabled=True)``) and eligible, the batch plan/apply of
``structural_preparation.prepare_structural_connectivity`` runs in a child interpreter on a free-threaded CPython
(``ANYGEOMETRY_PLANNING_THREADS=<n>`` is the only ANYgeometry interface used).  Every failure of the route ends in the
ordinary serial in-process path on the untouched geometry; a typed geometry refusal is reported as is and never retried.

Contract with the child (``_thread_child.py``): the document goes in as ``to_dict`` JSON (checksum verified by the child's
``from_dict``), the result comes back as an atomic envelope (job id + body sha256) holding the prepared topology (``to_dict``)
and the report rows.  The parent re-validates the prepared document (``from_dict`` checksum, claimed checksum, model id) and
re-checks the checksum after ``restore_topology``; a mismatch restores the previous topology and falls back to serial.
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
    "PhaseTwoRefusal",
    "configure",
    "locate_runtime",
    "check_health",
    "route_enabled",
    "try_prepare",
]

LOG = logging.getLogger("anyfem.thread_route")
EXPECT_NUMPY = "2.4.6"
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
_health_cache: dict = {}
_digest_cache: dict = {}
last_outcome: Optional["ChildOutcome"] = None
_INJECT: dict = {}  # tests only: extra keyword arguments merged into every try_prepare call


class PhaseTwoRefusal(GeometryError):
    """The child refused while deriving report rows; serial raises the same error unwrapped."""


@dataclass
class ChildOutcome:
    status: str
    reason: str = ""
    body: Optional[dict] = None
    timings: dict = field(default_factory=dict)
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    temp_dir: str = ""
    temp_removed: bool = False
    stderr_tail: str = ""
    sizes: dict = field(default_factory=dict)


def configure(*, enabled: Optional[bool] = None, threads: Optional[int] = None, min_faces: Optional[int] = None,
              runtime: Optional[str] = None, reset: bool = False) -> None:
    """Programmatic setting (an application checkbox would call this); ``reset=True`` drops all overrides."""
    if reset:
        _override.clear()
    for key, value in (("enabled", enabled), ("threads", threads), ("min_faces", min_faces), ("runtime", runtime)):
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
        try:
            folder = Path(tempfile.mkdtemp(prefix="anyfem-thread-", dir=temp_root))
            outcome.temp_dir = str(folder)
            (folder / "tmp").mkdir()
            job_id = uuid.uuid4().hex
            spec = dict(job, job_id=job_id, threads=threads, expect_numpy=EXPECT_NUMPY, src_paths=_src_paths(),
                        document=None)
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
        job_object.assign(process.pid)
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
                drain()
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
        drain()
        outcome.exit_code = process.returncode
        outcome.timings["child_wall_s"] = time.perf_counter() - t_spawned
        result_path = folder / "result.json"
        if not result_path.is_file():
            outcome.reason = "child exited with code %s and no result" % process.returncode
            outcome.stderr_tail = (folder / "stderr.txt").read_text("utf-8", "replace")[-1500:]
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
            outcome.status, outcome.reason = REFUSAL, "%s: %s" % (body.get("type"), body.get("message"))
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


def check_health(python: Path, *, timeout: float = 120.0, lock_path: Optional[Path] = None, **injection) -> ChildOutcome:
    """Selftest child: GIL off after imports, NumPy pin, source digest, frozen smoke.  Cached by interpreter + lock + sources."""
    lock = Path(lock_path) if lock_path else Path(python).parent.parent / "runtime.lock.json"
    problem = _check_lock(lock)
    if problem:
        return ChildOutcome(status=UNAVAILABLE, reason=problem)
    lock_hash = hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else ""
    stat = Path(python).stat()
    key = (str(python), stat.st_mtime_ns, stat.st_size, lock_hash, _parent_source_digest(), _batch_prepare.SMOKE_DIGEST,
           EXPECT_NUMPY, tuple(sorted((k, repr(v)) for k, v in injection.items())))
    cached = _health_cache.get(key)
    if cached is not None:
        return cached
    job = dict(kind="selftest", source_digest=_parent_source_digest(), smoke_digest=_batch_prepare.SMOKE_DIGEST)
    outcome = run_child(python, job, None, threads=1, timeout=timeout, **injection)
    if outcome.status == OK and (outcome.body or {}).get("smoke") != _batch_prepare.SMOKE_DIGEST:
        outcome.status, outcome.reason = UNAVAILABLE, "smoke digest mismatch"
    if outcome.status in (OK, UNAVAILABLE):
        _health_cache[key] = outcome
    return outcome


# ------------------------------------------------------------------------------------------------ the hook

def _unavailable(reason: str, outcome: Optional[ChildOutcome] = None) -> None:
    global last_outcome
    last_outcome = outcome or ChildOutcome(status=UNAVAILABLE, reason=reason)
    last_outcome.status, last_outcome.reason = UNAVAILABLE, reason
    LOG.warning("thread route unavailable, running serially in-process: %s", reason)
    return None


def try_prepare(geometry, operands, *, cancellation_check: Optional[Callable[[str], None]] = None, **injection):
    """Return the report rows after applying the child's prepared topology to ``geometry``, or None for the serial path.

    ``injection`` is for tests only (``temp_root``, ``child_main``, ``python_args``, ``extra_env``, ``timeout``, ``python``).
    """
    global last_outcome
    if not route_enabled():
        return None
    injection = {**_INJECT, **injection}
    from ._preparation_context import _preparation_evidence
    if _preparation_evidence.get():
        return None  # an evidence collector needs the live plan object, which cannot cross the process boundary
    faces = len(geometry.faces)
    if faces < _min_faces():
        return None
    python = injection.pop("python", None) or locate_runtime()
    if python is None:
        return _unavailable("runtime missing (set %s or install runtime-314t beside the application)" % ENV_RUNTIME)
    timeout = injection.pop("timeout", max(120.0, 3.0 * faces))
    health_kwargs = {k: v for k, v in injection.items() if k in ("temp_root", "python_args_health", "extra_env", "lock_path")}
    if "python_args_health" in health_kwargs:
        health_kwargs["python_args"] = health_kwargs.pop("python_args_health")
    health = check_health(python, **health_kwargs)
    if health.status != OK:
        return _unavailable("health check failed: %s" % (health.reason or health.status), health)
    from anygeometry.errors import GeometryError as _GeometryError
    from anygeometry.serialization import from_dict, to_dict
    try:
        document = json.dumps(to_dict(geometry), sort_keys=True, separators=(",", ":")).encode("utf-8")
        job = dict(kind="batch_prepare", operands=[[h.kind, h.id] for h in operands],
                   source_digest=_parent_source_digest())
    except (_GeometryError, TypeError, ValueError) as exc:
        return _unavailable("document cannot be serialised: %s" % exc)
    run_kwargs = {k: v for k, v in injection.items() if k not in ("python_args_health", "lock_path")}
    outcome = run_child(python, job, document, threads=thread_count(), timeout=timeout,
                        cancellation_check=cancellation_check, **run_kwargs)
    last_outcome = outcome
    if outcome.status == REFUSAL:
        body = outcome.body or {}
        message = str(body.get("message", outcome.reason))
        raise (PhaseTwoRefusal if body.get("phase") == 2 else _GeometryError)(message)
    if outcome.status != OK:
        return _unavailable(outcome.reason or outcome.status, outcome)
    body = outcome.body or {}
    try:
        prepared = body["prepared"]
        claimed = str(body["prepared_checksum"])
        rows = _batch_prepare.rows_from_json(body["rows"])
        candidate = from_dict(prepared)  # verifies the document checksum
        if to_dict(candidate)["checksum"]["value"] != claimed or str(candidate.model_id) != str(geometry.model_id):
            raise ValueError("prepared document does not match its claimed checksum or model")
        snapshot = candidate.topology_snapshot()
    except (KeyError, TypeError, ValueError, _GeometryError) as exc:
        return _unavailable("invalid child result: %s" % exc, outcome)
    before = geometry.topology_snapshot()
    try:
        geometry.restore_topology(snapshot)
        if to_dict(geometry)["checksum"]["value"] != claimed:
            raise ValueError("restored topology differs from the child's prepared document")
    except (ValueError, _GeometryError) as exc:
        geometry.restore_topology(before)
        return _unavailable("applying the child result failed: %s" % exc, outcome)
    return rows
