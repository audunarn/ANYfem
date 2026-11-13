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
