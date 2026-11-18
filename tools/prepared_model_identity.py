"""Certify that two prepared models (serial vs thread-route ON) are equal apart from the fields derived from the random ``model_id``.

Unlike ``ci_n2c_gate.py`` (which masks every UUID / 64-hex string), this uses an explicit ALLOWLIST of five field paths and fails closed:
  * any difference outside the allowlist                                  -> ``non-allowlisted-difference``
  * an allowlisted field differing although ``model_id`` is equal, or a
    field NOT differing although ``model_id`` differs                      -> ``allowlist-not-model-id-derived``
  * a model whose own ``checksum`` is not the sha256 of its canonical
    payload                                                               -> ``model-checksum-invalid``
  * a ``route_checksum`` that is not the sha256 of its own route fields    -> ``route-checksum-not-derived``
  * anything else unexpected in the shape (not exactly one publication)   -> ``unsupported-structure``

What each allowlisted field is: ``model_id`` is a fresh random uuid per run; ``checksum.value`` and ``route_checksum`` are recomputed here
from the document (so they are pure functions of the other fields); ``source_checksum`` and ``content_checksum`` are produced by the planner
and cannot be recomputed from the prepared model alone, so they are certified only as co-varying with ``model_id`` (all or none).

CLI: ``prepared_model_identity.py A.json B.json``; exit 0 certified, 1 refused, 2 unreadable input.
"""
from __future__ import annotations

import hashlib
import json
import sys

PLANNING = "anygeometry:intersection_planning"
_PUB = ("extensions", PLANNING, "publications", 0)
ALLOWLIST = (
    ("model_id",),
    ("checksum", "value"),
    _PUB + ("route_checksum",),
    _PUB + ("source_checksum",),
    _PUB + ("content_checksum",),
)
_ROUTE_FIELDS = ("content_checksum", "planning_mode", "certificate_version", "r_max", "seam_policy", "seam_policy_version")


class IdentityRefusal(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def _structure(doc, name):
    try:
        if not isinstance(doc, dict) or not isinstance(doc.get("model_id"), str):
            raise KeyError("model_id")
        if not isinstance(doc["checksum"], dict) or not isinstance(doc["checksum"].get("value"), str):
            raise KeyError("checksum.value")
        pubs = doc["extensions"][PLANNING]["publications"]
        if not isinstance(pubs, list) or len(pubs) != 1 or not isinstance(pubs[0], dict):
            raise KeyError("exactly one publication")
        for key in _ROUTE_FIELDS + ("route_checksum", "source_checksum"):
            if key not in pubs[0]:
                raise KeyError(key)
    except (KeyError, TypeError) as error:
        raise IdentityRefusal("unsupported-structure", f"{name}: {error!r}") from None


def _model_checksum(doc) -> str:
    body = {k: v for k, v in doc.items() if k != "checksum"}
    text = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _route_checksum(pub) -> str:
    text = json.dumps([pub[k] for k in _ROUTE_FIELDS], sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _differences(x, y, path, out):
    if type(x) is not type(y):
        out.append(path)
    elif isinstance(x, dict):
        for key in sorted(set(x) | set(y), key=str):
            if key not in x or key not in y:
                out.append(path + (key,))
            else:
                _differences(x[key], y[key], path + (key,), out)
    elif isinstance(x, list):
        if len(x) != len(y):
            out.append(path)
        else:
            for i, (u, v) in enumerate(zip(x, y)):
                _differences(u, v, path + (i,), out)
    elif x != y:
        out.append(path)


def certify(a, b) -> dict:
    """Return a certificate, or raise ``IdentityRefusal`` with a typed code."""
    for name, doc in (("A", a), ("B", b)):
        _structure(doc, name)
        if _model_checksum(doc) != doc["checksum"]["value"]:
            raise IdentityRefusal("model-checksum-invalid", f"{name}: checksum is not the sha256 of the canonical payload")
        pub = doc["extensions"][PLANNING]["publications"][0]
        if _route_checksum(pub) != pub["route_checksum"]:
            raise IdentityRefusal("route-checksum-not-derived", f"{name}: route_checksum is not the hash of its route fields")
    found: list = []
    _differences(a, b, (), found)
    outside = [p for p in found if p not in ALLOWLIST]
    if outside:
        raise IdentityRefusal("non-allowlisted-difference", f"{len(outside)} field(s), first: {list(outside[:5])}")
    same_id = a["model_id"] == b["model_id"]
    if (same_id and found) or (not same_id and sorted(map(str, found)) != sorted(map(str, ALLOWLIST))):
        raise IdentityRefusal("allowlist-not-model-id-derived",
                              f"model_id {'equal' if same_id else 'differs'} but differing allowlisted fields are {list(found)}")
    return {"certified": True, "differing_allowlisted_fields": list(found), "allowlist": list(ALLOWLIST),
            "model_checksums": [a["checksum"]["value"], b["checksum"]["value"]]}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: prepared_model_identity.py A.json B.json", file=sys.stderr)
        return 2
    try:
        docs = [json.loads(open(p, encoding="utf-8").read()) for p in argv]
    except (OSError, ValueError) as error:
        print(f"INPUT: {error}", file=sys.stderr)
        return 2
    try:
        print(json.dumps(certify(*docs), default=list))
    except IdentityRefusal as refusal:
        print(f"REFUSED {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
