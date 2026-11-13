"""Pure parts of the thread-route runtime bundle: lock parsing, hashing, refusal on any mismatch (no interpreter needed)."""
from __future__ import annotations

import copy
import hashlib
import json
import os

import pytest

from anyfem import runtime_lock as rl

H1 = "a" * 64
H2 = "b" * 64
H3 = "c" * 64


def _input_lock():
    return {
        "schema_version": 1,
        "platform": "win_amd64",
        "python_version": "3.14.10",
        "python_version_full": "3.14.10 free-threading build (x)",
        "numpy": "2.4.6",
        "interpreter": {"upstream_url": "https://www.python.org/ftp/python/3.14.10/python-3.14.10t-amd64.zip",
                        "upstream_sha256": H1, "payload_sha256": H2, "payload_files": 3,
                        "root_files": ["python3.14t.exe"], "trees": ["Lib", "DLLs"],
                        "exclude_dirs": ["__pycache__", "site-packages"]},
        "wheels": [{"name": "numpy", "version": "2.4.6", "filename": "numpy-2.4.6-cp314-cp314t-win_amd64.whl", "sha256": H3},
                   {"name": "scipy", "version": "1.17.1", "filename": "scipy-1.17.1-cp314-cp314t-win_amd64.whl", "sha256": H1}],
    }


def _built_lock():
    data = _input_lock()
    data.update(source_digest=H2, build_date="2026-11-13", bundle_sha256=H3, bundle_files=10)
    return data


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ---------------------------------------------------------------------------------------------- parsing

def test_parse_valid_input_and_built_lock():
    assert rl.parse_lock(_input_lock()).numpy == "2.4.6"
    built = rl.parse_lock(_built_lock(), built=True)
    assert built.source_digest == H2 and built.bundle_files == 10


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("schema_version"),
    lambda d: d.update(schema_version=2),
    lambda d: d.update(schema_version=True),
    lambda d: d.update(platform="linux_x86_64"),
    lambda d: d.update(numpy="2.4.5"),
    lambda d: d.update(python_version="3.15.0"),
    lambda d: d.update(python_version_full=""),
    lambda d: d["interpreter"].update(upstream_sha256="xyz"),
    lambda d: d["interpreter"].update(payload_sha256=H2.upper()),
    lambda d: d["interpreter"].update(payload_files=0),
    lambda d: d["interpreter"].update(trees=["../Lib"]),
    lambda d: d["interpreter"].update(root_files=["sub/python.exe"]),
    lambda d: d["interpreter"].update(trees=[]),
    lambda d: d.update(wheels=[]),
    lambda d: d["wheels"].append(dict(d["wheels"][0])),
    lambda d: d["wheels"][0].update(sha256="short"),
    lambda d: d["wheels"][0].update(filename="numpy-2.4.5-cp314-cp314t-win_amd64.whl"),
    lambda d: d["wheels"][0].update(filename="../numpy-2.4.6-cp314-cp314t-win_amd64.whl"),
    lambda d: d["wheels"][0].update(filename="numpy-2.4.6-cp314-cp314-win_amd64.whl"),
    lambda d: d["wheels"].pop(0),  # numpy missing
    lambda d: d.update(unknown_key=1),
])
def test_parse_refuses_bad_input_lock(mutate):
    data = _input_lock()
    mutate(data)
    with pytest.raises(rl.LockError):
        rl.parse_lock(data)


def test_parse_refuses_non_object_and_bad_json_bytes():
    with pytest.raises(rl.LockError):
        rl.parse_lock([])
    with pytest.raises(rl.LockError):
        rl.load_lock_bytes(b"{not json")
    with pytest.raises(rl.LockError):
        rl.load_lock_bytes(b"\xff\xfe")


@pytest.mark.parametrize("drop", ["source_digest", "build_date", "bundle_sha256", "bundle_files"])
def test_built_lock_needs_output_fields(drop):
    data = _built_lock()
    data.pop(drop)
    with pytest.raises(rl.LockError):
        rl.parse_lock(data, built=True)


def test_input_lock_may_not_carry_output_fields_when_unbuilt():
    data = _input_lock()
    data["source_digest"] = H2
    with pytest.raises(rl.LockError):
        rl.parse_lock(data)


def test_built_lock_field_types():
    for key, bad in (("source_digest", "zz"), ("bundle_sha256", 5), ("bundle_files", 0), ("bundle_files", "3"), ("build_date", "")):
        data = _built_lock()
        data[key] = bad
        with pytest.raises(rl.LockError):
            rl.parse_lock(data, built=True)


# ---------------------------------------------------------------------------------------------- hashing

def test_sha256_file(tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"abc")
    assert rl.sha256_file(path) == hashlib.sha256(b"abc").hexdigest()


def test_tree_digest_depends_on_names_and_bytes(tmp_path):
    a = tmp_path / "a"
    _write(a / "p" / "f.txt", b"one")
    _write(a / "g.txt", b"two")
    base = rl.tree_digest(a)
    assert base == rl.tree_digest(a)  # stable
    _write(a / "g.txt", b"twO")
    assert rl.tree_digest(a) != base
    _write(a / "g.txt", b"two")
    assert rl.tree_digest(a) == base
    (a / "g.txt").rename(a / "h.txt")
    assert rl.tree_digest(a) != base  # a renamed file changes the digest
    (a / "h.txt").rename(a / "g.txt")
    _write(a / "extra.pyc", b"")  # an extra file (even empty / bytecode) changes the digest
    assert rl.tree_digest(a) != base


def test_tree_digest_skips_the_lock_and_excluded_dirs(tmp_path):
    a = tmp_path / "a"
    _write(a / "f", b"1")
    base, count = rl.tree_digest(a, with_count=True)
    _write(a / rl.LOCK_NAME, b"{}")
    assert rl.tree_digest(a) == base
    _write(a / "Lib" / "__pycache__" / "m.pyc", b"x")
    assert rl.tree_digest(a) != base  # no exclusion unless asked
    assert rl.tree_digest(a, exclude_dirs=("__pycache__",)) != None  # noqa: E711
    assert count == 1


def test_payload_digest_selects_root_files_and_trees_and_excludes(tmp_path):
    src = tmp_path / "base"
    _write(src / "python3.14t.exe", b"EXE")
    _write(src / "other.txt", b"not selected")
    _write(src / "Lib" / "os.py", b"os")
    _write(src / "Lib" / "__pycache__" / "os.pyc", b"pyc")
    _write(src / "Lib" / "site-packages" / "pip" / "x.py", b"pip")
    _write(src / "DLLs" / "a.pyd", b"d")
    spec = _input_lock()["interpreter"]
    files = rl.payload_files(src, rl.parse_lock(_input_lock()).interpreter)
    assert sorted(files) == ["DLLs/a.pyd", "Lib/os.py", "python3.14t.exe"]
    digest, count = rl.payload_digest(src, rl.parse_lock(_input_lock()).interpreter)
    assert count == 3 and len(digest) == 64
    _write(src / "other.txt", b"changed")  # unselected files do not matter
    _write(src / "Lib" / "site-packages" / "pip" / "x.py", b"changed")
    assert rl.payload_digest(src, rl.parse_lock(_input_lock()).interpreter)[0] == digest
    _write(src / "Lib" / "os.py", b"OS")
    assert rl.payload_digest(src, rl.parse_lock(_input_lock()).interpreter)[0] != digest
    assert spec["trees"] == ["Lib", "DLLs"]


def test_payload_files_refuses_missing_selection(tmp_path):
    src = tmp_path / "base"
    _write(src / "Lib" / "os.py", b"os")
    with pytest.raises(rl.LockError):
        rl.payload_files(src, rl.parse_lock(_input_lock()).interpreter)  # root file and DLLs tree absent


# ---------------------------------------------------------------------------------------------- verification

def _real_input_lock(tmp_path):
    """A lock whose wheel and payload hashes are real for tiny fake inputs."""
    wheels = tmp_path / "wheels"
    data = _input_lock()
    for entry in data["wheels"]:
        _write(wheels / entry["filename"], entry["name"].encode())
        entry["sha256"] = hashlib.sha256(entry["name"].encode()).hexdigest()
    base = tmp_path / "base"
    _write(base / "python3.14t.exe", b"EXE")
    _write(base / "Lib" / "os.py", b"os")
    _write(base / "DLLs" / "a.pyd", b"d")
    data["interpreter"]["payload_sha256"], data["interpreter"]["payload_files"] = rl.payload_digest(
        base, rl.parse_lock(data).interpreter)
    return rl.parse_lock(data), wheels, base


def test_verify_wheelhouse_accepts_exact_set(tmp_path):
    lock, wheels, _ = _real_input_lock(tmp_path)
    rl.verify_wheelhouse(lock, wheels)


def test_verify_wheelhouse_refuses_wrong_hash_missing_and_extra(tmp_path):
    lock, wheels, _ = _real_input_lock(tmp_path)
    target = wheels / lock.wheels[0].filename
    original = target.read_bytes()
    target.write_bytes(original + b"!")
    with pytest.raises(rl.LockError, match="sha256"):
        rl.verify_wheelhouse(lock, wheels)
    target.write_bytes(original)
    target.unlink()
    with pytest.raises(rl.LockError, match="missing"):
        rl.verify_wheelhouse(lock, wheels)
    target.write_bytes(original)
    _write(wheels / "evil-1.0-py3-none-any.whl", b"x")
    with pytest.raises(rl.LockError, match="unlisted"):
        rl.verify_wheelhouse(lock, wheels)


def test_verify_interpreter_source_accepts_and_refuses(tmp_path):
    lock, _, base = _real_input_lock(tmp_path)
    rl.verify_interpreter_source(lock, base)
    _write(base / "Lib" / "os.py", b"tampered")
    with pytest.raises(rl.LockError, match="payload"):
        rl.verify_interpreter_source(lock, base)
    _write(base / "Lib" / "os.py", b"os")
    _write(base / "Lib" / "extra.py", b"x")
    with pytest.raises(rl.LockError, match="payload"):
        rl.verify_interpreter_source(lock, base)


def test_verify_bundle_matches_built_lock(tmp_path):
    root = tmp_path / "rt"
    _write(root / "python.exe", b"EXE")
    _write(root / "Lib" / "site-packages" / "numpy" / "__init__.py", b"n")
    digest, count = rl.tree_digest(root, with_count=True)
    data = _built_lock()
    data.update(bundle_sha256=digest, bundle_files=count)
    lock = rl.parse_lock(data, built=True)
    rl.verify_bundle(lock, root)
    _write(root / "Lib" / "site-packages" / "numpy" / "__init__.py", b"N")
    with pytest.raises(rl.LockError, match="bundle"):
        rl.verify_bundle(lock, root)
    _write(root / "Lib" / "site-packages" / "numpy" / "__init__.py", b"n")
    rl.verify_bundle(lock, root)
    _write(root / "Lib" / "site-packages" / "numpy" / "__pycache__" / "x.pyc", b"\0")
    with pytest.raises(rl.LockError, match="bundle"):
        rl.verify_bundle(lock, root)


def test_check_installed_lock_for_health(tmp_path):
    """Everything the application side needs in one call: full lock, bundle tree, pins and the application's source digest."""
    root = tmp_path / "rt"
    _write(root / "python.exe", b"EXE")
    digest, count = rl.tree_digest(root, with_count=True)
    data = _built_lock()
    data.update(bundle_sha256=digest, bundle_files=count)
    _write(root / rl.LOCK_NAME, json.dumps(data).encode())
    assert rl.check_installed(root, expect_numpy="2.4.6", expect_source_digest=H2) == ""
    assert "numpy" in rl.check_installed(root, expect_numpy="2.4.7", expect_source_digest=H2)
    assert "source digest" in rl.check_installed(root, expect_numpy="2.4.6", expect_source_digest=H1)
    (root / rl.LOCK_NAME).unlink()
    assert "lock" in rl.check_installed(root, expect_numpy="2.4.6", expect_source_digest=H2)
    bad = copy.deepcopy(data)
    bad["wheels"][0]["sha256"] = "nothex"
    _write(root / rl.LOCK_NAME, json.dumps(bad).encode())
    assert "lock" in rl.check_installed(root, expect_numpy="2.4.6", expect_source_digest=H2)
    _write(root / rl.LOCK_NAME, json.dumps(data).encode())
    _write(root / "planted.pyd", b"x")
    assert "bundle" in rl.check_installed(root, expect_numpy="2.4.6", expect_source_digest=H2)


# ---------------------------------------------------------------------------------------------- wheel extraction

def _make_wheel(path, members, *, record=True, tamper=None):
    import base64
    import zipfile
    lines = []
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            lines.append("%s,sha256=%s,%d" % (name, digest, len(data)))
        lines.append("pkg-1.0.dist-info/RECORD,,")
        if record:
            body = "\n".join(lines) + "\n"
            if tamper:
                body = tamper(body)
            archive.writestr("pkg-1.0.dist-info/RECORD", body)


def test_install_wheel_extracts_verified_members(tmp_path):
    wheel = tmp_path / "pkg.whl"
    _make_wheel(wheel, {"pkg/__init__.py": b"x=1", "pkg/ext.pyd": b"bin", "pkg-1.0.dist-info/METADATA": b"m", "pkg/empty.whl": b""})
    site = tmp_path / "site"
    names = rl.install_wheel(wheel, site)
    assert (site / "pkg" / "ext.pyd").read_bytes() == b"bin"
    assert (site / "pkg" / "empty.whl").read_bytes() == b""
    assert "pkg-1.0.dist-info/RECORD" in names and not list(site.rglob("__pycache__"))


@pytest.mark.parametrize("members", [
    {"../evil.py": b"x"},
    {"/abs.py": b"x"},
    {"pkg/../../evil.py": b"x"},
    {"C:/evil.py": b"x"},
    {"pkg-1.0.data/scripts/run.exe": b"x"},
    {"pkg\\evil.py": b"x"},
])
def test_install_wheel_refuses_unsafe_members(tmp_path, members):
    wheel = tmp_path / "pkg.whl"
    _make_wheel(wheel, members)
    with pytest.raises(rl.LockError):
        rl.install_wheel(wheel, tmp_path / "site")
    assert not (tmp_path / "evil.py").exists()


def test_install_wheel_refuses_record_problems(tmp_path):
    members = {"pkg/__init__.py": b"x=1"}
    wheel = tmp_path / "a.whl"
    _make_wheel(wheel, members, record=False)
    with pytest.raises(rl.LockError, match="RECORD"):
        rl.install_wheel(wheel, tmp_path / "s1")
    _make_wheel(wheel, members, tamper=lambda body: body.replace("sha256=", "sha256=A"))
    with pytest.raises(rl.LockError, match="RECORD"):
        rl.install_wheel(wheel, tmp_path / "s2")
    wheel2 = tmp_path / "b.whl"
    import zipfile
    _make_wheel(wheel2, members)
    with zipfile.ZipFile(wheel2, "a") as archive:  # a member the RECORD does not list
        archive.writestr("pkg/sneaky.py", b"y")
    with pytest.raises(rl.LockError, match="RECORD"):
        rl.install_wheel(wheel2, tmp_path / "s3")


def test_install_wheel_refuses_existing_target_file(tmp_path):
    wheel = tmp_path / "pkg.whl"
    _make_wheel(wheel, {"pkg/__init__.py": b"x=1"})
    site = tmp_path / "site"
    rl.install_wheel(wheel, site)
    with pytest.raises(rl.LockError, match="exists"):
        rl.install_wheel(wheel, site)


def test_parse_refuses_consistently_edited_pins():
    """Changing the numpy pin together with its wheel, or the CPython line together with its full version, is still refused."""
    data = _input_lock()
    data["numpy"] = "2.4.5"
    data["wheels"][0].update(version="2.4.5", filename="numpy-2.4.5-cp314-cp314t-win_amd64.whl")
    with pytest.raises(rl.LockError, match="numpy"):
        rl.parse_lock(data)
    data = _input_lock()
    data.update(python_version="3.15.0", python_version_full="3.15.0 free-threading build (x)")
    with pytest.raises(rl.LockError, match="3.14"):
        rl.parse_lock(data)


def test_install_wheel_refuses_record_entry_without_member(tmp_path):
    import zipfile
    wheel = tmp_path / "pkg.whl"
    members = {"pkg/__init__.py": b"x=1", "pkg/gone.py": b"y"}
    _make_wheel(wheel, members)
    # rebuild the archive without pkg/gone.py but keep its RECORD row
    kept = {}
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            if info.filename != "pkg/gone.py":
                kept[info.filename] = archive.read(info)
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in kept.items():
            archive.writestr(name, data)
    with pytest.raises(rl.LockError, match="RECORD"):
        rl.install_wheel(wheel, tmp_path / "site")


def test_install_wheel_refuses_duplicate_members(tmp_path):
    import zipfile
    import warnings
    wheel = tmp_path / "pkg.whl"
    _make_wheel(wheel, {"pkg/__init__.py": b"x=1"})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr("pkg/__init__.py", b"x=1")
    with pytest.raises(rl.LockError, match="duplicate"):
        rl.install_wheel(wheel, tmp_path / "site")


# ------------------------------------------------------------------------------------------ review round 1 (Codex r1)

def test_schema_version_must_be_an_integer_and_keys_unique():
    text = json.dumps(_input_lock()).replace('"schema_version": 1,', '"schema_version": 1.0,')
    with pytest.raises(rl.LockError):
        rl.load_lock_bytes(text.encode())
    duplicate = json.dumps(_input_lock())[:-1] + ', "numpy": "2.4.6"}'
    with pytest.raises(rl.LockError, match="JSON"):
        rl.load_lock_bytes(duplicate.encode())
    rl.load_lock_bytes(json.dumps(_input_lock()).encode())  # the unmodified lock is fine


def test_absurdly_nested_json_is_a_lock_error_not_a_crash():
    with pytest.raises(rl.LockError):
        rl.load_lock_bytes(b"[" * 100000)


def test_install_wheel_refuses_duplicate_record_rows(tmp_path):
    wheel = tmp_path / "pkg.whl"
    _make_wheel(wheel, {"pkg/__init__.py": b"x=1"},
                tamper=lambda body: "pkg/__init__.py,sha256=WRONG,3\n" + body)  # a wrong row first, the right one after
    with pytest.raises(rl.LockError, match="twice|RECORD"):
        rl.install_wheel(wheel, tmp_path / "site")


def test_read_verified_wheel_returns_the_hashed_bytes_only(tmp_path):
    lock, wheels, _ = _real_input_lock(tmp_path)
    assert rl.read_verified_wheel(lock.wheels[0], wheels) == b"numpy"
    (wheels / lock.wheels[0].filename).write_bytes(b"other")
    with pytest.raises(rl.LockError, match="sha256"):
        rl.read_verified_wheel(lock.wheels[0], wheels)


def test_install_wheel_accepts_bytes(tmp_path):
    wheel = tmp_path / "pkg.whl"
    _make_wheel(wheel, {"pkg/__init__.py": b"x=1"})
    rl.install_wheel(wheel.read_bytes(), tmp_path / "site")
    assert (tmp_path / "site" / "pkg" / "__init__.py").read_bytes() == b"x=1"


def _junction(link, target):
    import subprocess
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip("cannot create a junction here: %s" % result.stdout)


def test_directory_junction_in_the_bundle_is_refused_not_ignored(tmp_path):
    root = tmp_path / "rt"
    _write(root / "python.exe", b"EXE")
    outside = tmp_path / "outside"
    _write(outside / "sitecustomize.py", b"print('planted')")
    digest, count = rl.tree_digest(root, with_count=True)
    _junction(root / "Lib", outside)
    with pytest.raises(rl.LockError, match="link or reparse"):
        rl.tree_digest(root)
    data = _built_lock()
    data.update(bundle_sha256=digest, bundle_files=count)
    _write(root / rl.LOCK_NAME, json.dumps(data).encode())
    assert "bundle" in rl.check_installed(root, expect_numpy="2.4.6", expect_source_digest=H2)


def test_junction_in_the_interpreter_source_is_refused(tmp_path):
    lock, _, base = _real_input_lock(tmp_path)
    outside = tmp_path / "outside"
    _write(outside / "os.py", b"os")
    import shutil
    shutil.rmtree(base / "Lib")
    _junction(base / "Lib", outside)
    with pytest.raises(rl.LockError, match="link|lacks"):
        rl.verify_interpreter_source(lock, base)


def test_exclusion_is_exact(tmp_path):
    a = tmp_path / "a"
    _write(a / "f", b"1")
    _write(a / "Lib" / "m.py", b"2")
    base = rl.tree_digest(a, exclude_dirs=("__pycache__",), with_count=True)
    _write(a / "Lib" / "__pycache__" / "m.pyc", b"x")
    assert rl.tree_digest(a, exclude_dirs=("__pycache__",), with_count=True) == base  # an excluded folder (at any depth) changes nothing
    assert rl.tree_digest(a, with_count=True) != base  # without the exclusion it counts
    _write(a / "Lib" / "other" / "m.pyc", b"x")
    assert rl.tree_digest(a, exclude_dirs=("__pycache__",), with_count=True) != base  # only the named folder is excluded


def test_enumeration_errors_are_refused_not_skipped(tmp_path, monkeypatch):
    root = tmp_path / "rt"
    _write(root / "python.exe", b"EXE")
    real = os.walk

    def failing(top, topdown=True, onerror=None, followlinks=False):
        if onerror is not None:
            onerror(PermissionError(13, "denied", str(top)))
        return iter(())
    monkeypatch.setattr(rl.os, "walk", failing)
    with pytest.raises(rl.LockError, match="cannot enumerate"):
        rl.tree_digest(root)
    monkeypatch.setattr(rl.os, "walk", real)
    rl.tree_digest(root)
