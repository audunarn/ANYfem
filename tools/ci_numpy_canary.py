"""T7 NumPy bit-change canary: does the cheap sector60 identity measurement notice a NumPy other than the pinned one?

``measure OUT``   run the sector60 closure through ``prepare_structural_connectivity`` SERIALLY (route OFF) in THIS interpreter and write
                  OUT (JSON record) and OUT.blob (UUID/64-hex masked canonical result).  Needs anygeometry and anyfem importable.
``compare A B``   A = record under the pinned NumPy, B = record under the canary NumPy (2.5.x).  The canary only has teeth if the two
                  differ: exit 0 DETECTED (digests differ), 1 NOT DETECTED (identical: the cheap measurement cannot see this NumPy),
                  2 input problem (fail closed: wrong NumPy versions, missing keys, different structure that is not a float change).
The pinned NumPy is read from ``anyfem.runtime_lock.NUMPY_PIN``.  Manual procedure and workflow: docs/NUMPY_CANARY.md.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOSURE = ROOT / "tests" / "data" / "sector60_closure.json.gz"
_MASK = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-f]{64}")
_KEYS = ("numpy", "python", "masked_sha256", "faces")
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
    record = {"numpy": numpy.__version__, "python": sys.version.split()[0], "masked_sha256": hashlib.sha256(blob.encode()).hexdigest(),
              "faces": len(work.faces), "anygeometry": str(Path(anygeometry.__file__).resolve().parent)}
    out.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    Path(str(out) + ".blob").write_text(blob, encoding="utf-8")
    return record


def _load(path: Path) -> dict:
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("cannot read %s: %s" % (path, exc))
    if not isinstance(record, dict) or any(not isinstance(record.get(key), (str, int)) for key in _KEYS):
        raise ValueError("%s is not a measurement record" % path)
    if not re.fullmatch(r"[0-9a-f]{64}", str(record["masked_sha256"])) or isinstance(record["faces"], bool):
        raise ValueError("%s holds a malformed digest or face count" % path)
    return record


def _float_diffs(a, b) -> int:
    if isinstance(a, dict) and isinstance(b, dict):
        return sum(_float_diffs(a[k], b[k]) for k in a.keys() & b.keys()) + len(a.keys() ^ b.keys())
    if isinstance(a, list) and isinstance(b, list):
        return sum(_float_diffs(x, y) for x, y in zip(a, b)) + abs(len(a) - len(b))
    return 0 if a == b else 1


def verdict(pinned: dict, canary: dict, expect_pin: str) -> tuple:
    """(exit code, message).  Pure: no numpy 2.5 needed to test it."""
    if pinned["numpy"] != expect_pin:
        return INPUT, "reference record is NumPy %s, not the pin %s" % (pinned["numpy"], expect_pin)
    if not re.fullmatch(r"2\.5\.\d+", str(canary["numpy"])):
        return INPUT, "canary record is NumPy %s, not 2.5.x" % canary["numpy"]
    if pinned["python"] != canary["python"]:
        return INPUT, "interpreters differ (%s vs %s): the comparison would not isolate NumPy" % (pinned["python"], canary["python"])
    if pinned["masked_sha256"] == canary["masked_sha256"] and pinned["faces"] == canary["faces"]:
        return NOT_DETECTED, "NOT DETECTED: sector60 result is bit-identical under NumPy %s and %s" % (pinned["numpy"], canary["numpy"])
    return DETECTED, "DETECTED: sector60 differs (%s vs %s, faces %s vs %s)" % (
        pinned["masked_sha256"][:12], canary["masked_sha256"][:12], pinned["faces"], canary["faces"])


def compare(a: Path, b: Path, expect_pin: str) -> int:
    try:
        pinned, canary = _load(a), _load(b)
    except ValueError as exc:
        print("INPUT PROBLEM:", exc, file=sys.stderr)
        return INPUT
    code, message = verdict(pinned, canary, expect_pin)
    extra = ""
    blobs = [Path(str(p) + ".blob") for p in (a, b)]
    if code == DETECTED and all(p.is_file() for p in blobs):
        try:
            extra = "; %d differing leaf values" % _float_diffs(*(json.loads(p.read_text(encoding="utf-8")) for p in blobs))
        except ValueError:
            extra = ""
    (sys.stdout if code == DETECTED else sys.stderr).write(message + extra + "\n")
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
