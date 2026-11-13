"""Real-bundle acceptance (CI job T1): a genuinely built runtime passes the application's lock stage and its selftest child, and each
tamper of a COPY of it makes the route unavailable BEFORE any child is spawned.  Needs ``ANYGEOMETRY_THREAD_RUNTIME`` to name a built bundle
(``tools/build_runtime.py``); skips otherwise (the T2 job).  The tamper cases complement the fixture-only canaries (T4).
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from anyfem import runtime_lock as rl
from anyfem import thread_route as tr

ROOT = Path(os.environ.get("ANYGEOMETRY_THREAD_RUNTIME", ""))
BUILT = bool(os.environ.get("ANYGEOMETRY_THREAD_RUNTIME")) and (ROOT / "python.exe").is_file() and (ROOT / rl.LOCK_NAME).is_file()
pytestmark = [pytest.mark.skipif(not BUILT, reason="no built runtime bundle in ANYGEOMETRY_THREAD_RUNTIME"),
              pytest.mark.skipif(os.name != "nt", reason="the thread route is Windows only")]


@pytest.fixture(autouse=True)
def fresh_cache():
    tr._health_cache.clear()
    yield
    tr._health_cache.clear()


@pytest.fixture(scope="module")
def copy(tmp_path_factory):
    target = tmp_path_factory.mktemp("bundle-copy") / "runtime-314t"
    shutil.copytree(ROOT, target)
    return target


@pytest.fixture
def no_spawn(monkeypatch):
    spawned = []
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: spawned.append(a) or pytest.fail("a child was spawned for a tampered bundle"))
    return spawned


def _marked(root: Path) -> bool:
    return os.path.lexists(root / rl.CI_SMOKE_MARKER)


def test_genuine_bundle_passes_the_lock_stage_and_the_selftest_child():
    outcome = tr.check_health(ROOT / "python.exe", timeout=300)
    assert outcome.status == tr.OK, outcome.reason
    built = rl.load_lock_bytes((ROOT / rl.LOCK_NAME).read_bytes(), built=True)
    assert (outcome.body or {}).get("health", {}).get("version_full") == built.python_version_full


def test_a_marked_bundle_is_refused_without_the_explicit_opt_in(monkeypatch, no_spawn):
    if not _marked(ROOT):
        pytest.skip("not a CI smoke bundle")
    previous = tr._ci_smoke_allowed
    tr.allow_ci_smoke_bundle(False)
    try:
        outcome = tr.check_health(ROOT / "python.exe")
    finally:
        tr.allow_ci_smoke_bundle(previous)
    assert outcome.status == tr.UNAVAILABLE and "ci smoke" in outcome.reason.lower()


def _expect_unavailable(root: Path, word: str):
    tr._health_cache.clear()
    outcome = tr.check_health(root / "python.exe")
    assert outcome.status == tr.UNAVAILABLE and word in outcome.reason.lower(), (outcome.status, outcome.reason)


def _lock_edit(root: Path, **changes):
    path = root / rl.LOCK_NAME
    original = path.read_bytes()
    data = json.loads(original.decode("utf-8"))
    data.update(changes)
    path.write_bytes(json.dumps(data).encode("utf-8"))
    return original


def test_planted_file_is_refused(copy, no_spawn):
    planted = copy / "Lib" / "site-packages" / "numpy" / "planted.py"
    planted.write_bytes(b"x = 1\n")
    try:
        _expect_unavailable(copy, "bundle")
    finally:
        planted.unlink()


def test_changed_file_is_refused(copy, no_spawn):
    target = copy / "Lib" / "site-packages" / "numpy" / "version.py"
    original = target.read_bytes()
    target.write_bytes(original + b"\n# tampered\n")
    try:
        _expect_unavailable(copy, "bundle")
    finally:
        target.write_bytes(original)


def test_missing_file_is_refused(copy, no_spawn):
    target = copy / "Lib" / "site-packages" / "numpy" / "version.py"
    original = target.read_bytes()
    target.unlink()
    try:
        _expect_unavailable(copy, "bundle")
    finally:
        target.write_bytes(original)


def test_wrong_source_digest_in_the_lock_is_refused(copy, no_spawn):
    original = _lock_edit(copy, source_digest="e" * 64)
    try:
        _expect_unavailable(copy, "source digest")
    finally:
        (copy / rl.LOCK_NAME).write_bytes(original)


def test_wrong_numpy_pin_in_the_lock_is_refused(copy, no_spawn):
    original = _lock_edit(copy, numpy="2.4.5")
    try:
        _expect_unavailable(copy, "numpy")
    finally:
        (copy / rl.LOCK_NAME).write_bytes(original)


def test_wrong_bundle_digest_in_the_lock_is_refused(copy, no_spawn):
    original = _lock_edit(copy, bundle_sha256="f" * 64)
    try:
        _expect_unavailable(copy, "bundle")
    finally:
        (copy / rl.LOCK_NAME).write_bytes(original)


def test_deleting_the_smoke_marker_does_not_turn_a_smoke_bundle_into_a_shipped_one(copy, no_spawn):
    if not _marked(copy):
        pytest.skip("not a CI smoke bundle")
    marker = copy / rl.CI_SMOKE_MARKER
    original = marker.read_bytes()
    marker.unlink()
    try:
        _expect_unavailable(copy, "bundle")  # the marker is covered by the bundle digest
    finally:
        marker.write_bytes(original)


def test_an_untouched_copy_still_passes_the_lock_stage(copy, monkeypatch):
    problem, lock = rl.check_installed_lock(copy, expect_numpy=tr.EXPECT_NUMPY, expect_source_digest=tr._parent_source_digest(),
                                            allow_ci_smoke=True)
    assert problem == "" and lock is not None  # every tamper above was reverted byte for byte
