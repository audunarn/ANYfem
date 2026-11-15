"""T7 NumPy bit-change canary: does the cheap sector60 identity measurement notice a NumPy other than the pinned one?

``measure OUT``   run the sector60 closure through ``prepare_structural_connectivity`` SERIALLY (route OFF) in THIS interpreter and write
                  OUT (JSON record) and OUT.blob (UUID/64-hex masked canonical result).  Needs anygeometry and anyfem importable.
``compare A B``   A = record under the pinned NumPy, B = record under the canary NumPy (2.5.x).  The canary only has teeth if the two
                  differ: exit 0 DETECTED (digests differ), 1 NOT DETECTED (identical: the cheap measurement cannot see this NumPy),
                  2 input problem (fail closed: wrong NumPy versions, any other difference between the two environments, missing/unbound blobs,
                  a difference that is not purely floating point).
The pinned NumPy is read from ``anyfem.runtime_lock.NUMPY_PIN``.  Manual procedure and workflow: docs/NUMPY_CANARY.md.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOSURE = ROOT / "tests" / "data" / "sector60_closure.json.gz"
_MASK = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-f]{64}")
_STR_KEYS = ("numpy", "python", "scipy", "shapely", "anygeometry", "anyfem", "masked_sha256")
_ENV = ("OPENBLAS_CORETYPE", "NPY_DISABLE_CPU_FEATURES", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "NUMPY_MADVISE_HUGEPAGE")
DETECTED, NOT_DETECTED, INPUT = 0, 1, 2


def mask(text: str) -> str:
    return _MASK.sub("X", text)


def measure(out: Path) -> dict:
    import numpy
    import anygeometry
    from dataclasses import replace
    from anygeometry import to_dict
    from anyfem import thread_route as tr
    from anyfem.structural_preparation import prepare_structural_connectivity
    tr.configure(reset=True, enabled=False)
    with gzip.open(CLOSURE, "rb") as handle:
        closure = anygeometry.model_closure_from_dict(json.loads(handle.read().decode("utf-8")))
    work = closure.working_model.clone(preserve_identity=True)
    fresh = replace(closure, working_model=work)
    report = prepare_structural_connectivity(work, source_model_id=str(fresh.source_model_id), source_revision=fresh.source_revision,
                                             member_ids=tuple(work.members))
    blob = mask(json.dumps([to_dict(work), report.to_dict()], sort_keys=True, separators=(",", ":")))
    import anyfem
    import scipy
    import shapely
    env = {name: os.environ.get(name, "") for name in _ENV}
    record = {"numpy": numpy.__version__, "python": sys.version.split()[0], "scipy": scipy.__version__, "shapely": shapely.__version__,
              "masked_sha256": hashlib.sha256(blob.encode()).hexdigest(), "faces": len(work.faces), "env": env,
              "anygeometry": str(Path(anygeometry.__file__).resolve().parent), "anyfem": str(Path(anyfem.__file__).resolve().parent)}
    out.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    Path(str(out) + ".blob").write_bytes(blob.encode())
    return record


def _load(path: Path) -> dict:
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("cannot read %s: %s" % (path, exc))
    if not isinstance(record, dict) or any(not isinstance(record.get(key), str) for key in _STR_KEYS) or not isinstance(record.get("env"), dict):
        raise ValueError("%s is not a measurement record" % path)
    if not re.fullmatch(r"[0-9a-f]{64}", record["masked_sha256"]) or type(record.get("faces")) is not int:
        raise ValueError("%s holds a malformed digest or face count" % path)
    return record


def _blob(path: Path, record: dict) -> object:
    """The masked result next to a record; it must be the one the record's digest names (the verdict is bound to the measured bytes)."""
    try:
        raw = Path(str(path) + ".blob").read_bytes()
    except OSError as exc:
        raise ValueError("blob of %s missing or unreadable: %s" % (path, exc))
    if hashlib.sha256(raw).hexdigest() != record["masked_sha256"]:
        raise ValueError("blob of %s does not match its recorded digest" % path)
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ValueError("blob of %s is not JSON: %s" % (path, exc))


def _walk(a, b, counts: list) -> None:
    """counts = [float leaves that differ, any other difference (type, key, length, string, int, bool, null)]."""
    if isinstance(a, dict) and isinstance(b, dict):
        counts[1] += len(a.keys() ^ b.keys())
        for key in a.keys() & b.keys():
            _walk(a[key], b[key], counts)
    elif isinstance(a, list) and isinstance(b, list):
        counts[1] += abs(len(a) - len(b))
        for x, y in zip(a, b):
            _walk(x, y, counts)
    elif type(a) is float and type(b) is float:
        counts[0] += a != b
    elif type(a) is not type(b) or a != b:
        counts[1] += 1


def verdict(pinned: dict, canary: dict, expect_pin: str) -> tuple:
    """(exit code, message) from the two records only (digest equality).  Pure: no numpy 2.5 needed to test it."""
    if pinned["numpy"] != expect_pin:
        return INPUT, "reference record is NumPy %s, not the pin %s" % (pinned["numpy"], expect_pin)
    if not re.fullmatch(r"2\.5\.\d+", canary["numpy"]):
        return INPUT, "canary record is NumPy %s, not 2.5.x" % canary["numpy"]
    for key in _STR_KEYS + ("env",):
        if key not in ("numpy", "masked_sha256") and pinned[key] != canary[key]:
            return INPUT, "records differ in %s (%r vs %r): the comparison would not isolate NumPy" % (key, pinned[key], canary[key])
    if pinned["masked_sha256"] == canary["masked_sha256"] and pinned["faces"] == canary["faces"]:
        return NOT_DETECTED, "NOT DETECTED: sector60 result is bit-identical under NumPy %s and %s" % (pinned["numpy"], canary["numpy"])
    return DETECTED, "DETECTED: sector60 differs (%s vs %s, faces %s vs %s)" % (
        pinned["masked_sha256"][:12], canary["masked_sha256"][:12], pinned["faces"], canary["faces"])


def compare(a: Path, b: Path, expect_pin: str) -> int:
    """DETECTED only when the digests differ AND the bound blobs differ in float leaves alone (same structure, same strings and integers)."""
    try:
        pinned, canary = _load(a), _load(b)
        code, message = verdict(pinned, canary, expect_pin)
        if code != INPUT:
            blobs = (_blob(a, pinned), _blob(b, canary))
            counts = [0, 0]
            _walk(blobs[0], blobs[1], counts)
            if code == DETECTED:
                if counts[1] or not counts[0]:
                    raise ValueError("the results differ in more than floating-point values (%d float leaves, %d other differences): "
                                     "not a clean numeric canary, investigate" % tuple(counts))
                message += "; %d differing float leaves, nothing else" % counts[0]
    except ValueError as exc:
        print("INPUT PROBLEM:", exc, file=sys.stderr)
        return INPUT
    (sys.stdout if code == DETECTED else sys.stderr).write(message + "\n")
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("out", type=Path)
    c = sub.add_parser("compare")
    c.add_argument("pinned", type=Path)
    c.add_argument("canary", type=Path)
    args = parser.parse_args(argv)
    if args.cmd == "measure":
        print(json.dumps(measure(args.out)))
        return 0
    from anyfem import runtime_lock
    return compare(args.pinned, args.canary, runtime_lock.NUMPY_PIN)


if __name__ == "__main__":
    sys.exit(main())
