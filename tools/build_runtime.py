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
  python tools/build_runtime.py build --lock tools/runtime.lock.json --base-install <dir> --wheelhouse <dir> --out <runtime-314t>
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
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


def _run(command, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in command], capture_output=True, text=True, timeout=600, **kwargs)


def _stage_interpreter(lock: rl.Lock, base: Path, stage: Path) -> None:
    for rel, source in sorted(rl.payload_files(base, lock.interpreter).items()):
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    shutil.copyfile(stage / "python3.14t.exe", stage / "python.exe")  # the application looks for <runtime>\python.exe


def _install_wheels(lock: rl.Lock, wheelhouse: Path, stage: Path) -> None:
    """Copy, never recompile: each wheel (already sha256-verified against the lock) is unpacked member by member and every member
    is checked against the wheel's own RECORD.  No pip, no launcher scripts, no installer metadata: the result is reproducible."""
    for wheel in lock.wheels:
        rl.install_wheel(wheelhouse / wheel.filename, stage / "Lib" / "site-packages")


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


def build(lock_path: Path, base: Path, wheelhouse: Path, out: Path) -> dict:
    lock = rl.load_lock_bytes(lock_path.read_bytes())
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
        source_digest = _source_digest()
        data = json.loads(lock_path.read_text(encoding="utf-8"))
        bundle_sha256, bundle_files = rl.tree_digest(stage, with_count=True)
        data.update(source_digest=source_digest, build_date=datetime.date.today().isoformat(), bundle_sha256=bundle_sha256,
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
    args = parser.parse_args(argv)
    try:
        if args.command == "pin-interpreter":
            data = json.loads(args.lock.read_text(encoding="utf-8"))
            data["interpreter"].setdefault("payload_sha256", "0" * 64)
            data["interpreter"].setdefault("payload_files", 1)
            interpreter = rl.parse_lock(data).interpreter
            digest, count = rl.payload_digest(args.base_install, interpreter)
            print(json.dumps(dict(payload_sha256=digest, payload_files=count), indent=2))
            return 0
        result = build(args.lock, args.base_install, args.wheelhouse, args.out)
    except (rl.LockError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
