"""N2c full-model identity gate (S3 T6): compare a serial run and a thread-route-ON run with each other and with frozen constants.

Usage: ci_n2c_gate.py SERIAL_DIR ON_DIR --constants tools/n2c_identity_constants.json [--origin anygeometry=DIR --origin anyfem=DIR]
Each DIR holds out/{prepared-model.json, plan.json, counters-all.json, child-result.json} (the full-model child's output).

Exit codes (every non-zero exit is red; nothing is ever refreshed automatically):
  0 PASS
  1 FINDING   a prepared-model / plan digest differs (from the constants or serial vs ON), the counters differ between serial and ON,
              the face count or NumPy version differs, or a run was not what it claims (child's counters claim, ON not ON, serial threaded)
  2 INPUT     a file or folder is missing / empty / unreadable / malformed, or the constants are unusable (fail closed)
  3 REFRESH   ONLY the non-time counters (sha or key count) differ from the constants while both runs agree with each other and the
              model and plan digests are unchanged: a reviewed refresh of the constants is needed (docs/N2C_IDENTITY_GATE.md)
Findings are collected and all printed; 1 outranks 3.  UUIDs and 64-hex strings are masked before hashing (they differ run to run).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

EXIT_PASS, EXIT_FINDING, EXIT_INPUT, EXIT_REFRESH = 0, 1, 2, 3
FILES = ("prepared-model.json", "plan.json", "counters-all.json", "child-result.json")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA64 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class InputError(Exception):
    pass


def mask(text: str) -> str:
    return _SHA64.sub("<SHA64>", _UUID.sub("<UUID>", text))


def load_constants(path) -> dict:
    try:
        c = json.loads(Path(path).read_text(encoding="utf-8"))
        for group in ("prepared_model", "plan"):
            if not _HEX64.fullmatch(c[group]["masked_sha256"]) or int(c[group]["masked_chars"]) <= 0:
                raise ValueError(group)
        if not _HEX64.fullmatch(c["counters_deterministic_sha256"]):
            raise ValueError("counters_deterministic_sha256")
        for k in ("counters_nontime_keys", "faces_in", "working_faces_out"):
            if int(c[k]) <= 0:
                raise ValueError(k)
        if not isinstance(c["numpy_version"], str) or not c["numpy_version"]:
            raise ValueError("numpy_version")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InputError("constants unusable (%s): %r" % (path, exc))
    return c


def _read(path: Path) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InputError("cannot read %s: %r" % (path, exc))
    if not data:
        raise InputError("empty file %s" % path)
    return data


def counters_sha(counters: dict) -> str:
    # same recipe as the full-model child (s1_child_n2c.py): non-time counters only
    kept = {k: v for k, v in sorted(counters.items()) if not k.endswith("_time_ns")}
    return hashlib.sha256(json.dumps(kept, sort_keys=True, default=repr).encode()).hexdigest()


def load_run(folder) -> dict:
    folder = Path(folder)
    out = folder / "out"
    if not out.is_dir():
        raise InputError("missing folder %s" % out)
    raw = {name: _read(out / name) for name in FILES}
    run = {}
    for name in ("prepared-model.json", "plan.json"):
        try:
            text = mask(raw[name].decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise InputError("%s not UTF-8: %r" % (out / name, exc))
        try:
            json.loads(text)
        except ValueError as exc:
            raise InputError("%s is not valid JSON: %r" % (out / name, exc))
        run[name] = (hashlib.sha256(text.encode()).hexdigest(), len(text))
    try:
        counters = json.loads(raw["counters-all.json"])
        child = json.loads(raw["child-result.json"])
        run["claimed_counters_sha"] = child["identity"]["counters_deterministic_sha256"]
        run["faces_in"] = child["identity"]["faces_in"]
        run["faces_out"] = child["identity"]["working_faces_out"]
        run["numpy"] = child["versions"]["numpy"]
        run["gil"] = child["gil"]
        run["threads"] = child["threads_env"]
        run["origins"] = {k: str(v) for k, v in child["origins"].items()}
        if not isinstance(counters, dict) or not counters:
            raise ValueError("counters-all.json is not a non-empty object")
        if not isinstance(run["faces_out"], int) or isinstance(run["faces_out"], bool):
            raise ValueError("working_faces_out is not an integer")
    except (ValueError, KeyError, TypeError) as exc:
        raise InputError("malformed result in %s: %r" % (out, exc))
    run["counters_sha"] = counters_sha(counters)
    run["nontime_keys"] = len([k for k in counters if not k.endswith("_time_ns")])
    return run


def _threads(value):
    """Return the planning thread count as an int, 0 for none, or None when it is not a clean count."""
    if value in (None, "", "0", 0):
        return 0
    if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).isdigit():
        return int(value)
    return None


def _under(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def compare(serial: dict, on: dict, c: dict, origins: dict | None = None):
    findings, refresh = [], []
    for label, run in (("serial", serial), ("ON", on)):
        for pkg, root in (origins or {}).items():
            where = run["origins"].get(pkg)
            if where is None or not _under(where, Path(root)):
                findings.append("%s: package %s was imported from %r, not from the checkout %s" % (label, pkg, where, root))
        for name, key in (("prepared-model.json", "prepared_model"), ("plan.json", "plan")):
            digest, chars = run[name]
            ref = c[key]
            if digest != ref["masked_sha256"]:
                findings.append("%s: %s masked digest %s (%d chars) != frozen %s (%d chars)" % (
                    label, name.replace(".json", ""), digest[:12], chars, ref["masked_sha256"][:12], int(ref["masked_chars"])))
        if run["numpy"] != c["numpy_version"]:
            findings.append("%s: NumPy %s != pinned %s" % (label, run["numpy"], c["numpy_version"]))
        if run["faces_out"] != int(c["working_faces_out"]):
            findings.append("%s: working_faces_out %d != frozen %d" % (label, run["faces_out"], int(c["working_faces_out"])))
        if run["faces_in"] != int(c["faces_in"]):
            findings.append("%s: faces_in %r != frozen %d" % (label, run["faces_in"], int(c["faces_in"])))
        if run["claimed_counters_sha"] != run["counters_sha"]:
            findings.append("%s: child claims counters sha %s but counters-all.json gives %s" % (
                label, str(run["claimed_counters_sha"])[:12], run["counters_sha"][:12]))
    for name, what in (("prepared-model.json", "prepared-model"), ("plan.json", "plan")):
        if serial[name] != on[name]:
            findings.append("serial vs ON: %s masked digest differs (%s vs %s)" % (what, serial[name][0][:12], on[name][0][:12]))
    if serial["counters_sha"] != on["counters_sha"] or serial["nontime_keys"] != on["nontime_keys"]:
        findings.append("serial vs ON: non-time counters differ (%s vs %s)" % (serial["counters_sha"][:12], on["counters_sha"][:12]))
    if serial["faces_out"] != on["faces_out"]:
        findings.append("serial vs ON: working_faces_out %r vs %r" % (serial["faces_out"], on["faces_out"]))
    if serial["numpy"] != on["numpy"]:
        findings.append("serial vs ON: NumPy %s vs %s" % (serial["numpy"], on["numpy"]))
    if _threads(serial["threads"]) != 0:
        findings.append("serial run is not serial: ANYGEOMETRY_PLANNING_THREADS=%r" % (serial["threads"],))
    t = _threads(on["threads"])
    if not t:
        findings.append("ON run is not ON: ANYGEOMETRY_PLANNING_THREADS=%r" % (on["threads"],))
    if on["gil"] is not False:
        findings.append("ON run is not on a free-threaded interpreter (gil=%r)" % (on["gil"],))
    if not findings:
        for label, run in (("serial", serial), ("ON", on)):
            if run["counters_sha"] != c["counters_deterministic_sha256"] or run["nontime_keys"] != int(c["counters_nontime_keys"]):
                refresh.append("%s: non-time counters sha %s (%d keys) != frozen %s (%d keys)" % (
                    label, run["counters_sha"][:12], run["nontime_keys"], c["counters_deterministic_sha256"][:12], int(c["counters_nontime_keys"])))
    return findings, refresh


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("serial")
    parser.add_argument("on")
    parser.add_argument("--constants", required=True)
    parser.add_argument("--origin", action="append", default=[], metavar="PKG=DIR",
                        help="require child-result origins[PKG] to lie under DIR in both runs (binds the run to the checkout); repeatable")
    args = parser.parse_args(argv)
    origins = {}
    for item in args.origin:
        pkg, sep, root = item.partition("=")
        if not sep or not pkg or not root:
            print("N2C GATE INPUT ERROR (fail closed): bad --origin %r" % item)
            return EXIT_INPUT
        origins[pkg] = root
    try:
        if Path(args.serial).resolve() == Path(args.on).resolve():
            raise InputError("serial and ON folders are the same folder")
        c = load_constants(args.constants)
        serial, on = load_run(args.serial), load_run(args.on)
    except InputError as exc:
        print("N2C GATE INPUT ERROR (fail closed):", exc)
        return EXIT_INPUT
    findings, refresh = compare(serial, on, c, origins)
    for line in findings:
        print("N2C GATE FINDING:", line)
    for line in refresh:
        print("N2C GATE REFRESH REQUIRED (counters only; digests unchanged and serial == ON):", line)
    if findings:
        return EXIT_FINDING
    if refresh:
        return EXIT_REFRESH
    print("N2C GATE PASS: prepared-model %s plan %s counters %s faces %d, serial == ON" % (
        serial["prepared-model.json"][0][:12], serial["plan.json"][0][:12], serial["counters_sha"][:12], serial["faces_out"]))
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
