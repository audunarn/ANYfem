"""Child entry of the thread route.  ``python -I -B _thread_child.py JOBDIR`` on the free-threaded interpreter.

Self-contained: it must not import the ``anyfem`` package (anymesher/PySide are absent on the free-threaded runtime); it
loads ``_batch_prepare.py`` from the same folder by path.  Reads ``JOBDIR/job.json`` (written by the parent), sets the public
thread switch ``ANYGEOMETRY_PLANNING_THREADS``, imports, verifies the runtime (GIL off AFTER imports, NumPy pin, source digest,
frozen smoke), runs the job, writes JSONL progress and an atomic result envelope (job id + body sha256).  No pickle crosses the
interpreter boundary.  A crash leaves no result file; the parent classifies that itself.
"""
import hashlib
import importlib.util
import json
import os
import sys
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


def main(job_dir):
    with open(os.path.join(job_dir, "job.json"), encoding="utf-8") as handle:
        job = json.load(handle)
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
        if job["kind"] == "batch_prepare":
            with open(os.path.join(job_dir, job["document"]), "rb") as handle:
                document = json.loads(handle.read())
            _emit(job_dir, "document_read")
            model = from_dict(document)
            _emit(job_dir, "document_loaded", faces=len(model.faces))
            operands = tuple(model.handle(kind, ident) for kind, ident in job["operands"])
            phase = 1
            try:
                t = time.perf_counter()
                state = batch.plan_apply_phase(model, operands)
                _emit(job_dir, "planned_and_applied", seconds=time.perf_counter() - t)
                phase = 2
                rows = batch.report_rows_phase(model, state)
            except GeometryError as exc:  # typed refusal: reported as is, never retried
                _write_result(job_dir, job_id, "refusal", dict(
                    phase=phase, type=type(exc).__name__, message=str(exc)[:4000], health=health))
                return 0
            prepared = to_dict(model)
            _emit(job_dir, "prepared_serialised")
            _write_result(job_dir, job_id, "ok", dict(
                health=health, prepared=prepared, prepared_checksum=prepared["checksum"]["value"],
                rows=batch.rows_to_json(rows), faces_out=len(model.faces)))
            return 0
        return unavailable("unknown job kind %r" % job["kind"])
    except Exception as exc:  # anything untyped: route unavailable, parent runs serially
        return unavailable("child error %s: %s" % (type(exc).__name__, str(exc)[:500]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
