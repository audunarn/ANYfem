"""Child entry of the thread route.  ``python -I -B _thread_child.py JOBDIR`` on the free-threaded interpreter.

Self-contained: it must not import the ``anyfem`` package (anymesher/PySide are absent on the free-threaded runtime); it
loads ``_batch_prepare.py`` from the same folder by path.  Reads ``JOBDIR/job.json`` (written by the parent), sets the public
thread switch ``ANYGEOMETRY_PLANNING_THREADS``, imports, verifies the runtime (GIL off AFTER imports, NumPy pin, source digest,
frozen smoke), runs the job, writes JSONL progress and an atomic result envelope (job id + body sha256).  No pickle crosses the
interpreter boundary.  A crash leaves no result file; the parent classifies that itself.
"""
import base64
import hashlib
import importlib.util
import json
import os
import sys
import sysconfig
import time

T0 = time.perf_counter()
HERE = os.path.dirname(os.path.abspath(__file__))


def _emit(job_dir, event, **fields):
    line = json.dumps(dict(event=event, t=round(time.perf_counter() - T0, 6), **fields))
    with open(os.path.join(job_dir, "progress.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()


def _write_result(job_dir, job_id, status, body):
    body_bytes = json.dumps(body, sort_keys=True, separators=(",", ":"), default=repr).encode("utf-8")
    envelope = dict(format=1, job_id=job_id, status=status, body_sha256=hashlib.sha256(body_bytes).hexdigest(), body=body)
    tmp = os.path.join(job_dir, "result.json.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(envelope, handle, sort_keys=True, separators=(",", ":"), default=repr)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, os.path.join(job_dir, "result.json"))


def _load_batch_module():
    spec = importlib.util.spec_from_file_location("_anyfem_batch_prepare", os.path.join(HERE, "_batch_prepare.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["_anyfem_batch_prepare"] = module
    spec.loader.exec_module(module)
    return module


def _watch_parent(parent_pid):
    """Exit when the parent is gone (covers the window before the parent's job object contains this process).

    The handle is opened first and kept, so a recycled PID cannot be mistaken for the parent."""
    if os.name != "nt" or not parent_pid:
        return
    import ctypes
    import threading
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    handle = k.OpenProcess(0x00100000, False, int(parent_pid))  # SYNCHRONIZE
    if not handle:
        os._exit(4)  # the parent is already gone

    def watch():
        while k.WaitForSingleObject(handle, 1000) == 0x102:  # WAIT_TIMEOUT: parent still alive
            pass
        os._exit(4)

    threading.Thread(target=watch, daemon=True).start()


def main(job_dir):
    with open(os.path.join(job_dir, "job.json"), encoding="utf-8") as handle:
        job = json.load(handle)
    _watch_parent(job.get("parent_pid"))
    job_id = job["job_id"]
    os.environ["ANYGEOMETRY_PLANNING_THREADS"] = str(job["threads"])
    for path in reversed(job["src_paths"]):
        sys.path.insert(0, path)
    _emit(job_dir, "start", pid=os.getpid(), python=sys.version.split()[0])
    import numpy
    import scipy  # noqa: F401  (loaded now so a GIL re-enabling extension shows up in the check below)
    import anygeometry
    from anygeometry.errors import GeometryError
    from anygeometry.serialization import from_dict, to_dict
    batch = _load_batch_module()
    gil_enabled = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True
    health = dict(numpy=numpy.__version__, gil_enabled=gil_enabled, anygeometry=anygeometry.__file__)
    _emit(job_dir, "imports_done", **health)

    def unavailable(reason):
        _write_result(job_dir, job_id, "unavailable", dict(reason=reason, **health))
        return 0

    if gil_enabled and not job.get("allow_gil"):
        return unavailable("GIL enabled after imports")
    if numpy.__version__ != job["expect_numpy"]:
        return unavailable("NumPy %s != required %s" % (numpy.__version__, job["expect_numpy"]))
    if job.get("source_digest"):
        actual = batch.source_digest(os.path.dirname(os.path.abspath(anygeometry.__file__)),
                                     [os.path.join(HERE, "_batch_prepare.py"), os.path.join(HERE, "_thread_child.py")])
        if actual != job["source_digest"]:
            return unavailable("source digest differs from the parent's")
    try:
        if job["kind"] == "selftest":
            smoke = batch.smoke_digest()
            if job.get("smoke_digest") and smoke != job["smoke_digest"]:
                return unavailable("smoke digest %s != frozen %s" % (smoke, job["smoke_digest"]))
            _write_result(job_dir, job_id, "ok", dict(health=health, smoke=smoke))
            return 0
        if job["kind"] == "plan":
            with open(os.path.join(job_dir, job["document"]), "rb") as handle:
                document = json.loads(handle.read())
            _emit(job_dir, "document_read")
            model = from_dict(document)
            _emit(job_dir, "document_loaded", faces=len(model.faces))
            operands = tuple(model.handle(kind, ident) for kind, ident in job["operands"])
            phase = 1
            try:
                t = time.perf_counter()
                plan = batch.plan_only(model, operands)
                _emit(job_dir, "planned", seconds=time.perf_counter() - t)
            except GeometryError as exc:  # typed refusal: reported as is, never retried
                diagnostic, diagnostic_type, cause, seen = None, None, exc, set()  # phase is always 1: only planning runs here
                while cause is not None and id(cause) not in seen:  # the owner's structured evidence, if any
                    seen.add(id(cause))
                    provider = getattr(cause, "to_diagnostic", None)
                    if callable(provider):
                        try:
                            diagnostic = json.loads(json.dumps(dict(provider()), default=repr))
                            diagnostic_type = type(cause).__name__
                        except Exception:
                            diagnostic = None
                        break
                    cause = cause.__cause__
                _write_result(job_dir, job_id, "refusal", dict(
                    phase=phase, type=type(exc).__name__, message=str(exc), health=health,
                    diagnostic=diagnostic, diagnostic_type=diagnostic_type))
                return 0
            try:
                from anygeometry.plan_adoption import encode_intersection_plan
                packet = encode_intersection_plan(plan, job_id=job_id)
                package_dir = os.path.dirname(os.path.abspath(anygeometry.__file__))
                modules = batch.loaded_modules(package_dir)
                loaded_digest = batch.files_digest(package_dir, modules)
            except Exception as exc:  # a plan the codec cannot carry: the parent plans serially
                return unavailable("plan cannot be encoded: %s: %s" % (type(exc).__name__, str(exc)[:500]))
            _emit(job_dir, "plan_encoded", bytes=len(packet))
            body = dict(
                health=dict(health, version=sys.version.split()[0], cache_tag=sys.implementation.cache_tag,
                            gil_disabled_build=bool(sysconfig.get_config_var("Py_GIL_DISABLED")), executable=sys.executable),
                packet=base64.b64encode(packet).decode("ascii"), packet_sha256=hashlib.sha256(packet).hexdigest(),
                modules=modules, loaded_digest=loaded_digest, faces=len(model.faces))
            if job.get("verify_apply"):  # diagnostic only: the child applies the same plan and reports the checksum
                from anygeometry import apply_intersections
                apply_intersections(model, plan, policy=batch.make_policy(None))
                body["apply_checksum"] = to_dict(model)["checksum"]["value"]
            _write_result(job_dir, job_id, "ok", body)
            return 0
        return unavailable("unknown job kind %r" % job["kind"])
    except Exception as exc:  # anything untyped: route unavailable, parent runs serially
        return unavailable("child error %s: %s" % (type(exc).__name__, str(exc)[:500]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
