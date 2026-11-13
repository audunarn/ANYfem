"""Lock format and verification for the bundled free-threaded runtime (``runtime-314t``) of the thread route.

Standard library only: it is used by ``tools/build_runtime.py`` (assembly), by ``thread_route`` (health check, application
side) and by tests.  Everything here fails closed: any malformed, unknown, missing or mismatching item raises
:class:`LockError` (or is returned as a reason string by :func:`check_installed`), never a partial pass.

Two forms of ``runtime.lock.json`` (schema_version 1):

* the **input lock** (committed under ``tools/``): CPython version, interpreter payload pins, wheel names + sha256;
* the **built lock** (written into the runtime folder by the build): the input lock plus ``source_digest`` (ANYgeometry
  sources + the two ANYfem child files, as computed by ``_batch_prepare.source_digest``), ``build_date``, ``bundle_sha256`` and
  ``bundle_files`` (digest over every file of the runtime folder except the lock itself).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

__all__ = [
    "LOCK_NAME", "LockError", "Lock", "Interpreter", "Wheel", "parse_lock", "load_lock_bytes", "sha256_file", "tree_digest",
    "payload_files", "payload_digest", "verify_wheelhouse", "verify_interpreter_source", "verify_bundle", "check_installed",
]

LOCK_NAME = "runtime.lock.json"
SCHEMA_VERSION = 1
PLATFORM = "win_amd64"
PYTHON_PREFIX = "3.14."
NUMPY_PIN = "2.4.6"
WHEEL_TAIL = "-cp314-cp314t-win_amd64.whl"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[A-Za-z0-9_.]+\Z")
_VERSION = re.compile(r"[0-9][0-9A-Za-z.+!]*\Z")
_TOP_KEYS = {"schema_version", "platform", "python_version", "python_version_full", "numpy", "interpreter", "wheels"}
_OUT_KEYS = {"source_digest", "build_date", "bundle_sha256", "bundle_files"}
_INTERP_KEYS = {"upstream_url", "upstream_sha256", "payload_sha256", "payload_files", "root_files", "trees", "exclude_dirs"}
_WHEEL_KEYS = {"name", "version", "filename", "sha256"}


class LockError(ValueError):
    """The lock, an input archive or the assembled bundle does not match what the lock pins."""


@dataclass(frozen=True)
class Wheel:
    name: str
    version: str
    filename: str
    sha256: str


@dataclass(frozen=True)
class Interpreter:
    upstream_url: str
    upstream_sha256: str
    payload_sha256: str
    payload_files: int
    root_files: tuple
    trees: tuple
    exclude_dirs: tuple


@dataclass(frozen=True)
class Lock:
    python_version: str
    python_version_full: str
    numpy: str
    interpreter: Interpreter
    wheels: tuple
    source_digest: Optional[str] = None
    build_date: Optional[str] = None
    bundle_sha256: Optional[str] = None
    bundle_files: Optional[int] = None


# --------------------------------------------------------------------------------------------------------- parsing

def _hex(value, what: str) -> str:
    if not isinstance(value, str) or not _HEX.match(value):
        raise LockError("%s must be 64 lowercase hex digits" % what)
    return value


def _text(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LockError("%s must be a non-empty string" % what)
    return value


def _build_date(value) -> str:
    import datetime
    try:
        if isinstance(value, str) and datetime.date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    raise LockError("build_date must be a canonical YYYY-MM-DD date")


def _count(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LockError("%s must be a positive integer" % what)
    return value


def _relative_names(value, what: str, *, single_component: bool) -> tuple:
    if not isinstance(value, list) or not value:
        raise LockError("%s must be a non-empty list" % what)
    names = []
    for item in value:
        _text(item, what)
        if "\\" in item or item.startswith("/") or ".." in item.split("/") or ":" in item or (single_component and "/" in item):
            raise LockError("%s entry %r is not a plain relative name" % (what, item))
        names.append(item)
    if len(set(names)) != len(names):
        raise LockError("%s has duplicates" % what)
    return tuple(names)


def parse_lock(data, *, built: bool = False) -> Lock:
    """Strict validation.  ``built=True`` also requires the output fields; an input lock may not carry them."""
    if not isinstance(data, dict):
        raise LockError("lock is not a JSON object")
    version = data.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise LockError("unsupported lock schema_version %r" % (version,))
    keys = set(data)
    required = _TOP_KEYS | (_OUT_KEYS if built else set())
    allowed = required
    if keys - allowed:
        raise LockError("unknown lock keys: %s" % ", ".join(sorted(keys - allowed)))
    if required - keys:
        raise LockError("missing lock keys: %s" % ", ".join(sorted(required - keys)))
    if data["platform"] != PLATFORM:
        raise LockError("platform %r is not supported (only %s)" % (data["platform"], PLATFORM))
    python_version = _text(data["python_version"], "python_version")
    if not python_version.startswith(PYTHON_PREFIX):
        raise LockError("python_version %r is not CPython %sx" % (python_version, PYTHON_PREFIX))
    full = _text(data["python_version_full"], "python_version_full")
    if not full.startswith(python_version):
        raise LockError("python_version_full does not start with python_version")
    if data["numpy"] != NUMPY_PIN:
        raise LockError("numpy pin %r differs from the qualified %s" % (data["numpy"], NUMPY_PIN))
    raw = data["interpreter"]
    if not isinstance(raw, dict) or set(raw) != _INTERP_KEYS:
        raise LockError("interpreter section has the wrong keys")
    interpreter = Interpreter(
        upstream_url=_text(raw["upstream_url"], "upstream_url"),
        upstream_sha256=_hex(raw["upstream_sha256"], "upstream_sha256"),
        payload_sha256=_hex(raw["payload_sha256"], "payload_sha256"),
        payload_files=_count(raw["payload_files"], "payload_files"),
        root_files=_relative_names(raw["root_files"], "root_files", single_component=True),
        trees=_relative_names(raw["trees"], "trees", single_component=True),
        exclude_dirs=_relative_names(raw["exclude_dirs"], "exclude_dirs", single_component=True))
    wheels = []
    if not isinstance(data["wheels"], list) or not data["wheels"]:
        raise LockError("wheels must be a non-empty list")
    for entry in data["wheels"]:
        if not isinstance(entry, dict) or set(entry) != _WHEEL_KEYS:
            raise LockError("wheel entry has the wrong keys")
        name, wheel_version = entry["name"], entry["version"]
        if not isinstance(name, str) or not _NAME.match(name) or not isinstance(wheel_version, str) or not _VERSION.match(wheel_version):
            raise LockError("wheel entry has a malformed name or version")
        filename = entry["filename"]
        if not isinstance(filename, str) or filename != "%s-%s%s" % (name, wheel_version, WHEEL_TAIL) or "/" in filename or "\\" in filename:
            raise LockError("wheel filename %r must be %s-%s%s" % (filename, name, wheel_version, WHEEL_TAIL))
        wheels.append(Wheel(name, wheel_version, filename, _hex(entry["sha256"], "wheel sha256")))
    if len({w.name.lower() for w in wheels}) != len(wheels):
        raise LockError("duplicate wheel name")
    numpy_wheels = [w for w in wheels if w.name.lower() == "numpy"]
    if len(numpy_wheels) != 1 or numpy_wheels[0].version != data["numpy"]:
        raise LockError("the wheel list must contain numpy==%s exactly once" % data["numpy"])
    extra = {}
    if built:
        extra = dict(source_digest=_hex(data["source_digest"], "source_digest"), build_date=_build_date(data["build_date"]),
                     bundle_sha256=_hex(data["bundle_sha256"], "bundle_sha256"), bundle_files=_count(data["bundle_files"], "bundle_files"))
    return Lock(python_version=python_version, python_version_full=full, numpy=data["numpy"], interpreter=interpreter,
                wheels=tuple(wheels), **extra)


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate JSON keys")
    return dict(pairs)


def decode_lock_json(raw: bytes):
    """The ONE decoder for lock text: UTF-8 JSON without duplicate keys; absurd nesting is a LockError, not a crash."""
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise LockError("lock is not valid UTF-8 JSON: %s" % (exc,))


def load_lock_bytes(raw: bytes, *, built: bool = False) -> Lock:
    return parse_lock(decode_lock_json(raw), built=built)


# --------------------------------------------------------------------------------------------------------- hashing

def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _digest_of(entries: Mapping[str, Path]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(entries):
        digest.update(rel.encode("utf-8") + b"\0" + sha256_file(entries[rel]).encode("ascii") + b"\n")
    return digest.hexdigest()


def _is_link(path: Path) -> bool:
    """Symbolic link, directory junction or any other reparse point (invisible to a plain file walk, yet followed by imports)."""
    try:
        if os.path.islink(path) or (hasattr(os.path, "isjunction") and os.path.isjunction(path)):
            return True
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError as exc:
        raise LockError("cannot inspect %s: %r" % (path, exc))


def _raise(exc: OSError):
    raise LockError("cannot enumerate %s: %r" % (getattr(exc, "filename", "?"), exc))


def _walk_tree(root: Path, exclude_dirs=()):
    """Every regular file below ``root`` as (full path, posix relative name).  A link or reparse point anywhere (the root, a
    file, a folder, also an excluded folder itself), or an enumeration error, is an error: nothing may hide from the digest.
    (The contents of an excluded folder are neither walked nor copied.)"""
    if _is_link(Path(root)):
        raise LockError("link or reparse point not allowed: %s" % root)
    for current, dirs, files in os.walk(root, onerror=_raise):
        for name in list(dirs) + files:
            if _is_link(Path(current) / name):
                raise LockError("link or reparse point not allowed: %s" % (Path(current) / name))
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for name in files:
            full = Path(current) / name
            yield full, full.relative_to(root).as_posix()


def _walk(root: Path, exclude_dirs=(), skip_lock: bool = False) -> dict:
    found = {}
    for full, rel in _walk_tree(root, exclude_dirs):
        if skip_lock and rel == LOCK_NAME:
            continue
        found[rel] = full
    return found


def tree_digest(root, *, exclude_dirs=(), with_count: bool = False):
    """sha256 over (relative path, file sha256) of every file under ``root`` except the top-level lock file."""
    entries = _walk(Path(root), exclude_dirs, skip_lock=True)
    digest = _digest_of(entries)
    return (digest, len(entries)) if with_count else digest


def payload_files(base, interpreter: Interpreter) -> dict:
    """Files of the source interpreter install that go into the bundle: the named root files + the named trees."""
    base = Path(base)
    if _is_link(base):
        raise LockError("link or reparse point not allowed: %s" % base)
    found = {}
    for name in interpreter.root_files:
        path = base / name
        if not path.is_file():
            raise LockError("interpreter source lacks root file %s" % name)
        if _is_link(path):
            raise LockError("link or reparse point not allowed: %s" % path)
        found[name] = path
    for tree in interpreter.trees:
        folder = base / tree
        if not folder.is_dir():  # a link as the folder itself is refused by _walk_tree
            raise LockError("interpreter source lacks folder %s" % tree)
        for full, _ in _walk_tree(folder, interpreter.exclude_dirs):
            found[full.relative_to(base).as_posix()] = full
    return found


def payload_digest(base, interpreter: Interpreter):
    entries = payload_files(base, interpreter)
    return _digest_of(entries), len(entries)


# ---------------------------------------------------------------------------------------------------- verification

def verify_wheelhouse(lock: Lock, wheelhouse) -> None:
    """Exactly the locked wheels, byte-identical.  Used before anything is installed."""
    folder = Path(wheelhouse)
    present = {p.name for p in folder.iterdir()} if folder.is_dir() else set()
    wanted = {w.filename for w in lock.wheels}
    for wheel in lock.wheels:
        if wheel.filename not in present:
            raise LockError("wheel missing from the wheelhouse: %s" % wheel.filename)
        actual = sha256_file(folder / wheel.filename)
        if actual != wheel.sha256:
            raise LockError("wheel %s has sha256 %s, the lock pins %s" % (wheel.filename, actual, wheel.sha256))
    if present - wanted:
        raise LockError("unlisted files in the wheelhouse: %s" % ", ".join(sorted(present - wanted)))


def verify_interpreter_source(lock: Lock, base) -> None:
    digest, count = payload_digest(base, lock.interpreter)
    if digest != lock.interpreter.payload_sha256 or count != lock.interpreter.payload_files:
        raise LockError("interpreter payload %s (%d files) differs from the lock (%s, %d files)"
                        % (digest, count, lock.interpreter.payload_sha256, lock.interpreter.payload_files))


def verify_bundle(lock: Lock, root) -> None:
    if lock.bundle_sha256 is None or lock.bundle_files is None:
        raise LockError("lock has no bundle digest (not a built lock)")
    digest, count = tree_digest(root, with_count=True)
    if digest != lock.bundle_sha256 or count != lock.bundle_files:
        raise LockError("bundle contents (%d files, %s) differ from the lock (%d files, %s): changed, missing or extra files"
                        % (count, digest, lock.bundle_files, lock.bundle_sha256))


def check_installed_lock(root, *, expect_numpy: str, expect_source_digest: str):
    """Application-side check of an installed runtime folder.  Returns (reason, lock): reason "" and the parsed lock when the
    folder is sound, else the reason it is unusable and None.  The lock is read once; callers use the returned object."""
    root = Path(root)
    try:
        raw = (root / LOCK_NAME).read_bytes()
    except OSError as exc:
        return "runtime.lock.json missing or unreadable: %r" % (exc,), None
    try:
        lock = load_lock_bytes(raw, built=True)
    except LockError as exc:
        return "runtime.lock.json is invalid: %s" % exc, None
    if lock.numpy != expect_numpy:
        return "runtime.lock.json pins numpy %s, expected %s" % (lock.numpy, expect_numpy), None
    if lock.source_digest != expect_source_digest:
        return "runtime.lock.json source digest differs from this application's", None
    try:
        verify_bundle(lock, root)
    except (LockError, OSError) as exc:
        return "runtime bundle check failed: %s" % exc, None
    return "", lock


def check_installed(root, *, expect_numpy: str, expect_source_digest: str) -> str:
    return check_installed_lock(root, expect_numpy=expect_numpy, expect_source_digest=expect_source_digest)[0]


# ------------------------------------------------------------------------------------------------- wheel extraction

def _record_hash(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def _safe_member(name: str) -> str:
    parts = name.split("/")
    if (not name or "\\" in name or name.startswith("/") or ":" in name or any(p in ("", "..", ".") for p in parts)
            or any(p.endswith(".data") for p in parts)):
        raise LockError("wheel member %r is not a plain relative path" % name)
    return name


def read_verified_wheel(wheel: Wheel, wheelhouse) -> bytes:
    """Read a locked wheel ONCE and return exactly the bytes whose sha256 equals the lock's (no second read can differ)."""
    data = (Path(wheelhouse) / wheel.filename).read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != wheel.sha256:
        raise LockError("wheel %s has sha256 %s, the lock pins %s" % (wheel.filename, actual, wheel.sha256))
    return data


def install_wheel(wheel, site_packages) -> list:
    """Copy a (hash-verified) wheel's members into ``site_packages`` byte for byte: nothing is compiled, no installer
    metadata or launcher is generated.  Every member must be listed in the wheel's RECORD with a matching sha256 and size,
    every hashed RECORD entry must be present, paths must be plain relative names, ``*.data`` folders are refused, and an
    existing target file is never overwritten.  Returns the installed member names."""
    import csv
    import io
    import zipfile
    site = Path(site_packages)
    source = io.BytesIO(wheel) if isinstance(wheel, (bytes, bytearray)) else wheel
    with zipfile.ZipFile(source) as archive:
        members = [i for i in archive.infolist() if not i.is_dir()]
        names = [_safe_member(i.filename) for i in members]
        if len(set(names)) != len(names):
            raise LockError("wheel has duplicate members")
        records = [n for n in names if n.endswith(".dist-info/RECORD") and n.count("/") == 1]
        if len(records) != 1:
            raise LockError("wheel has no unique RECORD")
        listed = {}
        for row in csv.reader(io.StringIO(archive.read(records[0]).decode("utf-8"))):
            if len(row) != 3:
                raise LockError("wheel RECORD has a malformed row")
            if row[0] in listed:
                raise LockError("wheel RECORD lists %s twice" % row[0])
            listed[row[0]] = row
        if records[0] not in listed:
            raise LockError("wheel RECORD does not list itself")
        for info, name in zip(members, names):
            if name == records[0]:
                continue
            row = listed.get(name)
            data = archive.read(info)
            if row is None or row[1] != "sha256=" + _record_hash(data) or row[2] != str(len(data)):
                raise LockError("wheel member %s does not match the wheel RECORD" % name)
        for name, row in listed.items():
            if row[1] and name not in names:
                raise LockError("wheel RECORD lists %s which is not in the wheel" % name)
        # all checks passed: now write
        for info, name in zip(members, names):
            target = site / name
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(target, "xb") as handle:
                    handle.write(archive.read(info))
            except FileExistsError:
                raise LockError("target file exists: %s" % target)
    return names
