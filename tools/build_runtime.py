"""Assemble the bundled free-threaded runtime (``runtime-314t``) for the ANYfem thread route, reproducibly, from a locked manifest.

Windows x64 only.  Inputs, all hash-pinned in ``tools/runtime.lock.json`` (see ``anyfem.runtime_lock``):

* a CPython 3.14.x free-threaded install tree (the python.org ``python-3.14.10t-amd64.zip`` layout, e.g. unpacked by ``pymanager``):
  the selected files are verified against ``interpreter.payload_sha256`` BEFORE anything is copied;
* a wheelhouse holding exactly the locked cp314t wheels (whole-file sha256 verified against the lock before anything is unpacked;
  then unpacked member by member, each member checked against the wheel RECORD: files are copied, nothing is compiled, no ``.pyc``
  is written, no pip-generated launcher (those embed absolute paths and made two builds differ)).

Output: ``<out>`` = interpreter payload + ``python.exe`` + ``Lib/site-packages`` + the BUILT ``runtime.lock.json`` (input lock plus
``source_digest``, ``build_date``, ``bundle_sha256``, ``bundle_files``).  The build refuses (non-zero exit, nothing left behind) on any mismatch
and never overwrites an existing output folder.

  python tools/build_runtime.py pin-interpreter --base-install <dir> --lock tools/runtime.lock.json   # prints the payload pins
  python tools/build_runtime.py requirements --lock tools/runtime.lock.json                          # pip --require-hashes lines for the wheels
  python tools/build_runtime.py check-interpreter --lock tools/runtime.lock.json                      # THIS interpreter == the lock's build (CI)
  python tools/build_runtime.py build --lock tools/runtime.lock.json --base-install <dir> --wheelhouse <dir> --out <runtime-314t>

``build --ci-smoke-unpinned-interpreter`` (CI build smoke only, never the shipped path): the interpreter payload digest is MEASURED instead of
compared with the lock (the CI interpreter comes from actions/setup-python, not from the pinned python.org zip).  The built lock records the
measured values (``upstream_url`` says "unpinned", ``upstream_sha256`` is zeros) and the bundle gets ``CI-SMOKE-UNPINNED-INTERPRETER.txt``, which is
covered by the bundle digest; the application refuses a runtime containing it.  Wheels, root files, trees, the full Python version text
(``check-interpreter``), the import check and the reproducibility of the output are still enforced.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anyfem import runtime_lock as rl  # noqa: E402

IMPORT_CHECK = (
    "import sys;"
    "import numpy, scipy, shapely;"
    "print(sys.version);print(numpy.__version__);print(scipy.__version__);print(shapely.__version__);"
    "print(sys._is_gil_enabled())"
)


SMOKE_MARKER_TEXT = (b"CI smoke bundle: the interpreter payload was measured, not pinned (python from actions/setup-python).\n"
                     b"This is NOT a shipped runtime; the application refuses it.\n")


def check_interpreter(lock_path: Path, version=None, gil_disabled="auto") -> int:
    """0 only when the running interpreter's full version text equals the lock's python_version_full AND it is a free-threaded build."""
    try:
        lock = rl.load_lock_bytes(Path(lock_path).read_bytes())
    except (rl.LockError, OSError) as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 2
    version = sys.version if version is None else version
    if gil_disabled == "auto":
        flag = sysconfig.get_config_var("Py_GIL_DISABLED")
        gil_disabled = True if flag == 1 else (False if flag == 0 else None)
    if version != lock.python_version_full:
        print("REFUSED: interpreter %r differs from the lock's %r" % (version, lock.python_version_full), file=sys.stderr)
        return 2
    if gil_disabled is not True:
        print("REFUSED: not a free-threaded build (Py_GIL_DISABLED=%r)" % (gil_disabled,), file=sys.stderr)
        return 2
    print("interpreter matches the lock: %s" % version)
    return 0


def _run(command, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in command], capture_output=True, text=True, timeout=600, **kwargs)


def _stage_interpreter(lock: rl.Lock, base: Path, stage: Path) -> None:
    for rel, source in sorted(rl.payload_files(base, lock.interpreter).items()):
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    # Check the STAGED bytes (not the source tree we hashed earlier, which could have changed meanwhile) before anything runs.
    rl.verify_interpreter_source(lock, stage)
    shutil.copyfile(stage / "python3.14t.exe", stage / "python.exe")  # the application looks for <runtime>\python.exe
    if rl.sha256_file(stage / "python.exe") != rl.sha256_file(stage / "python3.14t.exe"):
        raise rl.LockError("python.exe copy differs from python3.14t.exe")


def _install_wheels(lock: rl.Lock, wheelhouse: Path, stage: Path) -> None:
    """Copy, never recompile: each wheel (already sha256-verified against the lock) is unpacked member by member and every member
    is checked against the wheel's own RECORD.  No pip, no launcher scripts, no installer metadata: the result is reproducible."""
    for wheel in lock.wheels:  # each wheel is read once; the hash-verified bytes are the bytes that get unpacked
        rl.install_wheel(rl.read_verified_wheel(wheel, wheelhouse), stage / "Lib" / "site-packages")


def _import_check(lock: rl.Lock, stage: Path) -> None:
    result = _run([stage / "python.exe", "-I", "-B", "-c", IMPORT_CHECK], cwd=str(stage))
    if result.returncode != 0:
        raise rl.LockError("import check failed in the assembled runtime:\n%s" % result.stderr[-2000:])
    lines = result.stdout.splitlines()
    if len(lines) != 5:
        raise rl.LockError("import check printed an unexpected answer")
    if lines[0] != lock.python_version_full:
        raise rl.LockError("sys.version %r differs from python_version_full in the lock" % lines[0])
    versions = {w.name: w.version for w in lock.wheels}
    for name, version in zip(("numpy", "scipy", "shapely"), lines[1:4]):
        if versions.get(name) != version:
            raise rl.LockError("%s %s was imported, the lock pins %s" % (name, version, versions.get(name)))
    if lines[4] != "False":
        raise rl.LockError("the GIL is enabled after importing numpy/scipy/shapely in the assembled runtime")


def _refuse_bytecode(stage: Path) -> None:
    found = [p for p in stage.rglob("*") if p.is_dir() and p.name == "__pycache__" or p.suffix in (".pyc", ".pyo")]
    if found:
        raise rl.LockError("bytecode was written into the pinned tree: %s" % ", ".join(str(p.relative_to(stage)) for p in found[:5]))


def _source_digest() -> str:
    from anyfem import thread_route  # the application's own computation (anygeometry sources + the two ANYfem child files)
    return thread_route._parent_source_digest()


def build(lock_path: Path, base: Path, wheelhouse: Path, out: Path, build_date: str = "", ci_smoke: bool = False) -> dict:
    lock_bytes = lock_path.read_bytes()  # ONE snapshot: verification and the output lock both come from these bytes
    lock = rl.load_lock_bytes(lock_bytes)
    if ci_smoke:  # measure, do not compare: the lock stored below then describes what was really used (and says it is unpinned)
        digest, count = rl.payload_digest(base, lock.interpreter)
        data = rl.decode_lock_json(lock_bytes)
        data["interpreter"].update(payload_sha256=digest, payload_files=count, upstream_sha256="0" * 64,
                                   upstream_url="unpinned: CI smoke build, interpreter payload measured, not pinned")
        lock_bytes = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
        lock = rl.load_lock_bytes(lock_bytes)
    build_date = build_date or datetime.date.today().isoformat()
    try:
        if datetime.date.fromisoformat(build_date).isoformat() != build_date:
            raise ValueError(build_date)
    except ValueError:
        raise rl.LockError("build date %r is not a canonical YYYY-MM-DD date" % build_date)
    rl.verify_wheelhouse(lock, wheelhouse)
    rl.verify_interpreter_source(lock, base)
    if out.exists():
        raise rl.LockError("output %s exists; the build never overwrites" % out)
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=out.name + ".building-", dir=str(out.parent)))
    try:
        _stage_interpreter(lock, base, stage)
        _install_wheels(lock, wheelhouse, stage)
        _import_check(lock, stage)
        _refuse_bytecode(stage)
        if ci_smoke:
            (stage / rl.CI_SMOKE_MARKER).write_bytes(SMOKE_MARKER_TEXT)
        source_digest = _source_digest()
        data = rl.decode_lock_json(lock_bytes)
        bundle_sha256, bundle_files = rl.tree_digest(stage, with_count=True)
        data.update(source_digest=source_digest, build_date=build_date, bundle_sha256=bundle_sha256,
                    bundle_files=bundle_files)
        text = json.dumps(data, indent=2, sort_keys=True) + "\n"
        (stage / rl.LOCK_NAME).write_bytes(text.encode("utf-8"))
        rl.verify_bundle(rl.load_lock_bytes(text.encode("utf-8"), built=True), stage)
        os.replace(stage, out)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    return dict(files=bundle_files + 1, bytes=size, source_digest=source_digest, bundle_sha256=bundle_sha256)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pin = sub.add_parser("pin-interpreter", help="print payload_sha256 / payload_files of a base install for the input lock")
    pin.add_argument("--lock", type=Path, required=True)
    pin.add_argument("--base-install", type=Path, required=True)
    make = sub.add_parser("build", help="assemble the runtime folder")
    make.add_argument("--lock", type=Path, required=True)
    make.add_argument("--base-install", type=Path, required=True)
    make.add_argument("--wheelhouse", type=Path, required=True)
    make.add_argument("--out", type=Path, required=True)
    make.add_argument("--build-date", default="", help="YYYY-MM-DD written to the lock (default: today); fix it for byte-identical folders")
    make.add_argument("--ci-smoke-unpinned-interpreter", action="store_true",
                      help="CI build smoke ONLY: measure the interpreter payload instead of comparing it; the bundle is marked and the application refuses it")
    req = sub.add_parser("requirements", help="print pip --require-hashes lines for the locked wheels")
    req.add_argument("--lock", type=Path, required=True)
    chk = sub.add_parser("check-interpreter", help="exit 0 only if this interpreter is the lock's free-threaded build")
    chk.add_argument("--lock", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "check-interpreter":
        return check_interpreter(args.lock)
    try:
        if args.command == "requirements":
            for wheel in rl.load_lock_bytes(args.lock.read_bytes()).wheels:
                print("%s==%s --hash=sha256:%s" % (wheel.name, wheel.version, wheel.sha256))
            return 0
        if args.command == "pin-interpreter":
            data = rl.decode_lock_json(args.lock.read_bytes())
            if not isinstance(data, dict) or not isinstance(data.get("interpreter"), dict):
                raise rl.LockError("lock has no interpreter section")
            data["interpreter"].setdefault("payload_sha256", "0" * 64)
            data["interpreter"].setdefault("payload_files", 1)
            interpreter = rl.parse_lock(data).interpreter
            digest, count = rl.payload_digest(args.base_install, interpreter)
            print(json.dumps(dict(payload_sha256=digest, payload_files=count), indent=2))
            return 0
        result = build(args.lock, args.base_install, args.wheelhouse, args.out, args.build_date, args.ci_smoke_unpinned_interpreter)
    except (rl.LockError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
