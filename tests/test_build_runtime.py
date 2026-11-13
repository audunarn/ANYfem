"""build_runtime.py refuses before it copies anything when an input does not match the lock (no interpreter needed)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from anyfem import runtime_lock as rl

TOOL = Path(__file__).resolve().parents[1] / "tools" / "build_runtime.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("build_runtime_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _inputs(tmp_path):
    base = tmp_path / "base"
    for rel, data in (("python3.14t.exe", b"EXE"), ("Lib/os.py", b"os"), ("DLLs/a.pyd", b"d")):
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_bytes(data)
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel = wheels / "numpy-2.4.6-cp314-cp314t-win_amd64.whl"
    wheel.write_bytes(b"not really a wheel")
    interpreter = dict(upstream_url="https://example.invalid/p.zip", upstream_sha256="a" * 64, payload_sha256="b" * 64, payload_files=3,
                       root_files=["python3.14t.exe"], trees=["Lib", "DLLs"], exclude_dirs=["__pycache__"])
    lock = {"schema_version": 1, "platform": "win_amd64", "python_version": "3.14.10", "python_version_full": "3.14.10 x",
            "numpy": "2.4.6", "interpreter": interpreter,
            "wheels": [{"name": "numpy", "version": "2.4.6", "filename": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}]}
    digest, count = rl.payload_digest(base, rl.parse_lock(lock).interpreter)
    interpreter.update(payload_sha256=digest, payload_files=count)
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    return path, base, wheels


def _args(lock, base, wheels, out):
    return ["build", "--lock", str(lock), "--base-install", str(base), "--wheelhouse", str(wheels), "--out", str(out)]


def test_refuses_wrong_wheel_hash(tool, tmp_path, capsys):
    lock, base, wheels = _inputs(tmp_path)
    (wheels / "numpy-2.4.6-cp314-cp314t-win_amd64.whl").write_bytes(b"swapped")
    out = tmp_path / "out" / "runtime-314t"
    assert tool.main(_args(lock, base, wheels, out)) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert not out.exists() and not (tmp_path / "out").exists()  # refused before any staging folder was created


def test_refuses_changed_interpreter_payload(tool, tmp_path, capsys):
    lock, base, wheels = _inputs(tmp_path)
    (base / "Lib" / "os.py").write_bytes(b"tampered")
    out = tmp_path / "out" / "runtime-314t"
    assert tool.main(_args(lock, base, wheels, out)) == 2
    assert "payload" in capsys.readouterr().err and not out.exists()


def test_never_overwrites_an_existing_output(tool, tmp_path, capsys):
    lock, base, wheels = _inputs(tmp_path)
    out = tmp_path / "runtime-314t"
    out.mkdir()
    (out / "keep.txt").write_text("keep")
    assert tool.main(_args(lock, base, wheels, out)) == 2
    assert "exists" in capsys.readouterr().err and (out / "keep.txt").read_text() == "keep"


def test_refuses_a_malformed_lock(tool, tmp_path, capsys):
    lock, base, wheels = _inputs(tmp_path)
    lock.write_text("{", encoding="utf-8")
    assert tool.main(_args(lock, base, wheels, tmp_path / "o")) == 2
    assert not (tmp_path / "o").exists()


def test_repository_lock_parses(tool):
    lock = rl.load_lock_bytes((TOOL.parent / "runtime.lock.json").read_bytes())
    assert lock.numpy == "2.4.6" and lock.interpreter.payload_files == 809


# ------------------------------------------------------------------------------------------ review round 1 (Codex r1)

def _runnable_inputs(tmp_path):
    """Inputs for a successful assembly with the interpreter-running steps replaced (no real Python in the fake base install)."""
    import base64
    import zipfile
    lock, base, wheels = _inputs(tmp_path)
    wheel = wheels / "numpy-2.4.6-cp314-cp314t-win_amd64.whl"
    body = b"VALUE = 1\n"
    digest = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("numpy/__init__.py", body)
        archive.writestr("numpy-2.4.6.dist-info/RECORD", "numpy/__init__.py,sha256=%s,%d\nnumpy-2.4.6.dist-info/RECORD,,\n" % (digest, len(body)))
    data = json.loads(lock.read_text(encoding="utf-8"))
    data["wheels"][0]["sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    lock.write_text(json.dumps(data), encoding="utf-8")
    return lock, base, wheels


def test_successful_assembly_publishes_a_verifiable_reproducible_folder(tool, tmp_path, monkeypatch):
    lock, base, wheels = _runnable_inputs(tmp_path)
    monkeypatch.setattr(tool, "_import_check", lambda *a, **k: None)
    monkeypatch.setattr(tool, "_source_digest", lambda: "c" * 64)
    first = tool.build(lock, base, wheels, tmp_path / "o1" / "runtime-314t", "2026-11-13")
    second = tool.build(lock, base, wheels, tmp_path / "o2" / "runtime-314t", "2026-11-13")
    assert first["bundle_sha256"] == second["bundle_sha256"]
    out = tmp_path / "o1" / "runtime-314t"
    built = rl.load_lock_bytes((out / rl.LOCK_NAME).read_bytes(), built=True)
    rl.verify_bundle(built, out)
    assert built.build_date == "2026-11-13" and built.source_digest == "c" * 64
    assert (out / "python.exe").read_bytes() == b"EXE" and (out / "Lib" / "site-packages" / "numpy" / "__init__.py").exists()
    assert [p.name for p in (tmp_path / "o1").iterdir()] == ["runtime-314t"]  # no staging folder left behind


def test_failed_assembly_leaves_nothing_behind(tool, tmp_path, monkeypatch):
    lock, base, wheels = _runnable_inputs(tmp_path)

    def boom(*args, **kwargs):
        raise rl.LockError("import check failed")
    monkeypatch.setattr(tool, "_import_check", boom)
    out = tmp_path / "o" / "runtime-314t"
    with pytest.raises(rl.LockError):
        tool.build(lock, base, wheels, out)
    assert not out.exists() and list((tmp_path / "o").iterdir()) == []


def test_wheel_replaced_after_verification_is_not_used(tool, tmp_path, monkeypatch):
    lock, base, wheels = _runnable_inputs(tmp_path)
    monkeypatch.setattr(tool, "_import_check", lambda *a, **k: None)
    monkeypatch.setattr(tool, "_source_digest", lambda: "c" * 64)
    real = rl.verify_wheelhouse

    def swap_after(lock_, folder):
        real(lock_, folder)
        (Path(folder) / lock_.wheels[0].filename).write_bytes(b"replaced after the directory check")
    monkeypatch.setattr(rl, "verify_wheelhouse", swap_after)
    with pytest.raises(rl.LockError, match="sha256"):
        tool.build(lock, base, wheels, tmp_path / "o" / "runtime-314t")
    assert not (tmp_path / "o" / "runtime-314t").exists()


def test_interpreter_file_replaced_after_verification_is_not_staged(tool, tmp_path, monkeypatch):
    lock, base, wheels = _runnable_inputs(tmp_path)
    monkeypatch.setattr(tool, "_import_check", lambda *a, **k: None)
    monkeypatch.setattr(tool, "_source_digest", lambda: "c" * 64)
    real = rl.verify_interpreter_source
    state = {"first": True}

    def swap_after(lock_, folder):
        real(lock_, folder)
        if state["first"]:
            state["first"] = False
            (Path(folder) / "Lib" / "os.py").write_bytes(b"replaced after the first check")
    monkeypatch.setattr(rl, "verify_interpreter_source", swap_after)
    with pytest.raises(rl.LockError, match="payload"):
        tool.build(lock, base, wheels, tmp_path / "o" / "runtime-314t")
    assert not (tmp_path / "o" / "runtime-314t").exists()


def test_bad_build_date_is_refused(tool, tmp_path, monkeypatch):
    lock, base, wheels = _runnable_inputs(tmp_path)
    with pytest.raises(rl.LockError, match="date"):
        tool.build(lock, base, wheels, tmp_path / "o" / "runtime-314t", "yesterday")


# ------------------------------------------------------------------------------------------ review round 2 (Codex r2)

def test_pin_interpreter_refuses_malformed_lock_input(tool, tmp_path, capsys):
    lock, base, _ = _inputs(tmp_path)
    good = json.loads(lock.read_text(encoding="utf-8"))
    for text in ('{"schema_version": 1, "schema_version": 1}', "[" * 100000, "[]", '"x"', json.dumps({"interpreter": 3})):
        lock.write_text(text, encoding="utf-8")
        assert tool.main(["pin-interpreter", "--lock", str(lock), "--base-install", str(base)]) == 2
    lock.write_text(json.dumps(good), encoding="utf-8")
    assert tool.main(["pin-interpreter", "--lock", str(lock), "--base-install", str(base)]) == 0


def test_noncanonical_build_date_is_refused(tool, tmp_path):
    lock, base, wheels = _runnable_inputs(tmp_path)
    with pytest.raises(rl.LockError, match="date"):
        tool.build(lock, base, wheels, tmp_path / "o" / "runtime-314t", "20261113")


def test_swap_after_check_cases_never_reach_the_import_check(tool, tmp_path, monkeypatch):
    lock, base, wheels = _runnable_inputs(tmp_path)
    reached = []
    monkeypatch.setattr(tool, "_import_check", lambda *a, **k: reached.append(1))
    monkeypatch.setattr(tool, "_source_digest", lambda: "c" * 64)
    real = rl.verify_wheelhouse
    monkeypatch.setattr(rl, "verify_wheelhouse", lambda l, f: (real(l, f), (Path(f) / l.wheels[0].filename).write_bytes(b"swapped")))
    with pytest.raises(rl.LockError):
        tool.build(lock, base, wheels, tmp_path / "o" / "runtime-314t")
    assert reached == []


def test_two_builds_are_identical_as_whole_trees_including_the_lock(tool, tmp_path, monkeypatch):
    lock, base, wheels = _runnable_inputs(tmp_path)
    monkeypatch.setattr(tool, "_import_check", lambda *a, **k: None)
    monkeypatch.setattr(tool, "_source_digest", lambda: "c" * 64)
    tool.build(lock, base, wheels, tmp_path / "o1" / "runtime-314t", "2026-11-13")
    tool.build(lock, base, wheels, tmp_path / "o2" / "runtime-314t", "2026-11-13")

    def snapshot(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert snapshot(tmp_path / "o1" / "runtime-314t") == snapshot(tmp_path / "o2" / "runtime-314t")
