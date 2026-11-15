"""N2c full-model identity gate (S3 T6): compare a serial run and a thread-route-ON run with each other and with frozen constants.

Usage: ci_n2c_gate.py SERIAL_DIR ON_DIR --constants tools/n2c_identity_constants.json [--origin anygeometry=DIR --origin anyfem=DIR]
Each DIR holds out/{prepared-model.json, plan.json, counters-all.json, child-result.json} (the full-model child's output).

Exit codes (every non-zero exit is red; nothing is ever refreshed automatically):
  0 PASS
  1 FINDING   a prepared-model / plan digest differs (from the constants or serial vs ON), the counters differ between serial and ON,
              the face count or NumPy version differs, or a run was not what it claims (child's counters claim, ON not ON, serial threaded)
  2 INPUT     a file or folder is missing / empty / unreadable / malformed, or the constants are unusable (fail closed)
  4 KERNEL    the recorded BLAS kernel is not the one the constants were frozen on (the full model is kernel dependent), the record is
              missing or malformed, or the requested OPENBLAS_CORETYPE differs from the effective OpenBLAS architecture (e.g. SKYLAKEX
              requested on a CPU without AVX-512: OpenBLAS may fall back silently).  Outranks 1 and 3; all findings are still printed.
  3 REFRESH   ONLY the non-time counters (sha or key count) differ from the constants while both runs agree with each other and the
              model and plan digests are unchanged: a reviewed refresh of the constants is needed (docs/N2C_IDENTITY_GATE.md)
Findings are collected and all printed; 1 outranks 3.  UUIDs and 64-hex strings are masked before hashing (they differ run to run).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

EXIT_PASS, EXIT_FINDING, EXIT_INPUT, EXIT_REFRESH, EXIT_KERNEL = 0, 1, 2, 3, 4
FILES = ("prepared-model.json", "plan.json", "counters-all.json", "child-result.json")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA64 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class InputError(Exception):
    pass


def mask(text: str) -> str:
    return _SHA64.sub("<SHA64>", _UUID.sub("<UUID>", text))


def _count(value, what: str) -> int:
    """A strict positive integer (bool, float, str are refused): counts are compared with ==, which would accept 1269.0."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("%s is not a positive integer: %r" % (what, value))
    return value


def load_constants(path) -> dict:
    try:
        c = json.loads(Path(path).read_text(encoding="utf-8"))
        for group in ("prepared_model", "plan"):
            if not _HEX64.fullmatch(c[group]["masked_sha256"]):
                raise ValueError(group)
            _count(c[group]["masked_chars"], group + ".masked_chars")
        if not _HEX64.fullmatch(c["counters_deterministic_sha256"]):
            raise ValueError("counters_deterministic_sha256")
        for k in ("counters_nontime_keys", "faces_in", "working_faces_out"):
            _count(c[k], k)
        if not isinstance(c["numpy_version"], str) or not c["numpy_version"]:
            raise ValueError("numpy_version")
        k = c["blas_kernel"]
        if not isinstance(k, dict):
            raise ValueError("blas_kernel is not an object")
        for key in ("openblas_architecture", "openblas_coretype"):
            if not isinstance(k[key], str) or not k[key] or k[key] != k[key].strip():
                raise ValueError("blas_kernel.%s" % key)
        if k["openblas_architecture"].casefold() != k["openblas_coretype"].casefold():
            raise ValueError("blas_kernel coretype %r does not name architecture %r" % (k["openblas_coretype"], k["openblas_architecture"]))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InputError("constants unusable (%s): %r" % (path, exc))
    return c


# threadpoolctl's internal_api values; any other value is an unclassified pool and fails closed
_POOL_APIS = frozenset({"openblas", "mkl", "blis", "flexiblas", "openmp"})


def kernel_problems(label: str, blas, kernel: dict) -> list:
    """Why a run's recorded BLAS kernel is not acceptable ([] = it is the frozen kernel, requested explicitly and really in effect).

    `blas` is child-result["blas"]: {"requested_coretype": <OPENBLAS_CORETYPE or None>, "pools": [{"internal_api", "architecture", ...}]}
    (threadpoolctl's `architecture` is the kernel OpenBLAS actually selected, so a silent fallback shows up as requested != effective).
    """
    if not isinstance(blas, dict):
        return ["%s: KERNEL record missing (child-result has no BLAS kernel record; a run without one is never accepted)" % label]
    problems = []
    req, pools = blas.get("requested_coretype"), blas.get("pools")
    req_ok = isinstance(req, str) and bool(req)
    if not req_ok:
        problems.append("%s: KERNEL not requested (OPENBLAS_CORETYPE recorded as %r; the pin is %s)" % (label, req, kernel["openblas_coretype"]))
    if not isinstance(pools, list):
        return problems + ["%s: KERNEL record malformed (pools is %r)" % (label, type(pools).__name__)]
    effective = []
    for pool in pools:
        threads = pool.get("num_threads") if isinstance(pool, dict) else None
        if (not isinstance(pool, dict) or not isinstance(pool.get("internal_api"), str) or pool["internal_api"] not in _POOL_APIS
                or not isinstance(threads, int) or isinstance(threads, bool) or threads < 1):
            problems.append("%s: KERNEL record malformed (pool entry %r: needs a threadpoolctl internal_api %s and a positive integer num_threads)"
                            % (label, pool, sorted(_POOL_APIS)))
        elif pool.get("internal_api") == "openblas":
            arch = pool.get("architecture")
            if isinstance(arch, str) and arch:
                effective.append(arch)
            else:
                problems.append("%s: KERNEL record malformed (OpenBLAS pool without an architecture: %r)" % (label, pool))
    if not effective and not problems:
        problems.append("%s: KERNEL record has no OpenBLAS pool (nothing proves which kernel ran)" % label)
    for arch in dict.fromkeys(effective):
        if req_ok and req.casefold() != arch.casefold():
            problems.append("%s: KERNEL requested %s but effective %s (OpenBLAS fell back silently; the CPU may lack the instruction set, "
                            "e.g. AVX-512 for SKYLAKEX)" % (label, req, arch))
        if arch != kernel["openblas_architecture"]:
            problems.append("%s: KERNEL effective %s != frozen %s (constants are valid for one kernel only)" % (label, arch, kernel["openblas_architecture"]))
    if req_ok and req != kernel["openblas_coretype"]:
        problems.append("%s: KERNEL requested OPENBLAS_CORETYPE=%s != pinned %s" % (label, req, kernel["openblas_coretype"]))
    return problems


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
        if not isinstance(child["origins"], dict):
            raise ValueError("origins is not an object")
        for k, v in child["origins"].items():
            if not isinstance(v, str) or not os.path.isabs(v):
                raise ValueError("origins[%r] is not an absolute path string: %r" % (k, v))
        run["origins"] = dict(child["origins"])
        if not isinstance(counters, dict) or not counters:
            raise ValueError("counters-all.json is not a non-empty object")
        _count(run["faces_out"], "working_faces_out")
        _count(run["faces_in"], "faces_in")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise InputError("malformed result in %s: %r" % (out, exc))
    run["blas"] = child.get("blas")           # checked by kernel_problems (a missing record is a KERNEL failure, not an input error)
    run["counters_sha"] = counters_sha(counters)
    run["nontime_keys"] = len([k for k in counters if not k.endswith("_time_ns")])
    return run


def _threads(value):
    """Return the planning thread count as an int, 0 for none, or None when it is not a clean count."""
    if value is None or (isinstance(value, str) and value in ("", "0")):
        return 0
    if isinstance(value, str) and value.isascii() and value.isdigit():      # an environment value, so a string only
        return int(value)
    return None


def _under(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def compare(serial: dict, on: dict, c: dict, origins: dict | None = None):
    findings, refresh, kernel = [], [], []
    for label, run in (("serial", serial), ("ON", on)):
        kernel.extend(kernel_problems(label, run["blas"], c["blas_kernel"]))
        for pkg, root in (origins or {}).items():
            where = run["origins"].get(pkg)
            if where is None or not _under(where, Path(root)):
                findings.append("%s: package %s was imported from %r, not from the checkout %s" % (label, pkg, where, root))
        for name, key in (("prepared-model.json", "prepared_model"), ("plan.json", "plan")):
            digest, chars = run[name]
            ref = c[key]
            if digest != ref["masked_sha256"]:
                findings.append("%s: %s masked digest %s (%d chars) != frozen %s (%d chars)" % (
                    label, name.replace(".json", ""), digest[:12], chars, ref["masked_sha256"][:12], ref["masked_chars"]))
        if run["numpy"] != c["numpy_version"]:
            findings.append("%s: NumPy %s != pinned %s" % (label, run["numpy"], c["numpy_version"]))
        if run["faces_out"] != c["working_faces_out"]:
            findings.append("%s: working_faces_out %d != frozen %d" % (label, run["faces_out"], c["working_faces_out"]))
        if run["faces_in"] != c["faces_in"]:
            findings.append("%s: faces_in %r != frozen %d" % (label, run["faces_in"], c["faces_in"]))
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
    if not t or t > 8:
        findings.append("ON run is not ON: ANYGEOMETRY_PLANNING_THREADS=%r" % (on["threads"],))
    if on["gil"] is not False:
        findings.append("ON run is not on a free-threaded interpreter (gil=%r)" % (on["gil"],))
    if not findings and not kernel:
        for label, run in (("serial", serial), ("ON", on)):
            if run["counters_sha"] != c["counters_deterministic_sha256"] or run["nontime_keys"] != c["counters_nontime_keys"]:
                refresh.append("%s: non-time counters sha %s (%d keys) != frozen %s (%d keys)" % (
                    label, run["counters_sha"][:12], run["nontime_keys"], c["counters_deterministic_sha256"][:12], c["counters_nontime_keys"]))
    return findings, refresh, kernel


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
    findings, refresh, kernel = compare(serial, on, c, origins)
    for line in kernel:
        print("N2C GATE KERNEL MISMATCH:", line)
    for line in findings:
        print("N2C GATE FINDING:", line)
    for line in refresh:
        print("N2C GATE REFRESH REQUIRED (counters only; digests unchanged and serial == ON):", line)
    if kernel:
        return EXIT_KERNEL
    if findings:
        return EXIT_FINDING
    if refresh:
        return EXIT_REFRESH
    print("N2C GATE PASS: prepared-model %s plan %s counters %s faces %d, serial == ON, kernel %s" % (
        serial["prepared-model.json"][0][:12], serial["plan.json"][0][:12], serial["counters_sha"][:12], serial["faces_out"], c["blas_kernel"]["openblas_architecture"]))
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
