"""tools/ci_compare_bundles.py: two builds must be identical file for file (the CI reproducibility check)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "ci_compare_bundles.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("ci_compare_bundles_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make(root, lock=b'{"a": 1}'):
    (root / "Lib").mkdir(parents=True)
    (root / "Lib" / "x.py").write_bytes(b"x")
    (root / "python.exe").write_bytes(b"exe")
    (root / "runtime.lock.json").write_bytes(lock)
    return root


def test_identical_trees_pass(tool, tmp_path):
    assert tool.main([str(_make(tmp_path / "a")), str(_make(tmp_path / "b"))]) == 0


def test_changed_file_fails(tool, tmp_path):
    a, b = _make(tmp_path / "a"), _make(tmp_path / "b")
    (b / "Lib" / "x.py").write_bytes(b"y")
    assert tool.main([str(a), str(b)]) == 1


def test_extra_and_missing_files_fail(tool, tmp_path):
    a, b = _make(tmp_path / "a"), _make(tmp_path / "b")
    (b / "extra.txt").write_bytes(b"")
    assert tool.main([str(a), str(b)]) == 1
    (b / "extra.txt").unlink()
    (b / "python.exe").unlink()
    assert tool.main([str(a), str(b)]) == 1


def test_lock_difference_fails_although_the_bundle_digest_ignores_the_lock(tool, tmp_path):
    a, b = _make(tmp_path / "a"), _make(tmp_path / "b", lock=b'{"a": 2}')
    assert tool.main([str(a), str(b)]) == 1


def test_missing_folder_fails(tool, tmp_path):
    assert tool.main([str(_make(tmp_path / "a")), str(tmp_path / "nope")]) == 1
