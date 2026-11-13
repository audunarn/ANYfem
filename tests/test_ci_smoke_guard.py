"""A CI smoke bundle (interpreter payload not pinned, S3 T1) must never be accepted by the application's shipped-runtime check.

Fixture bundle only (fake interpreter + a lock that describes the folder).  Windows only, like the route itself.
"""
from __future__ import annotations

import json
import os

import pytest

from anyfem import runtime_lock as rl
from anyfem import thread_route as tr

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the thread route is Windows only")

DIGEST = "d" * 64
H = "a" * 64


def write_bundle(root, *, marker=True):
    (root / "python.exe").write_bytes(b"not a real interpreter")
    if marker:
        (root / rl.CI_SMOKE_MARKER).write_text("ci smoke\n", encoding="utf-8")
    digest, count = rl.tree_digest(root, with_count=True)
    data = {
        "schema_version": 1, "platform": "win_amd64", "python_version": "3.14.10",
        "python_version_full": "3.14.10 free-threading build (x)", "numpy": "2.4.6",
        "interpreter": {"upstream_url": "unpinned:test", "upstream_sha256": "0" * 64, "payload_sha256": H, "payload_files": 1,
                        "root_files": ["python3.14t.exe"], "trees": ["Lib"], "exclude_dirs": ["__pycache__"]},
        "wheels": [{"name": "numpy", "version": "2.4.6", "filename": "numpy-2.4.6-cp314-cp314t-win_amd64.whl", "sha256": H}],
        "source_digest": DIGEST, "build_date": "2026-11-13", "bundle_sha256": digest, "bundle_files": count,
    }
    (root / rl.LOCK_NAME).write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "runtime-314t"
    root.mkdir()
    monkeypatch.setattr(tr, "_parent_source_digest", lambda: DIGEST)
    spawned = []

    def fake_run_child(python, job, *args, **kwargs):
        spawned.append(job.get("kind"))
        return tr.ChildOutcome(status=tr.UNAVAILABLE, reason="fixture interpreter cannot run")
    monkeypatch.setattr(tr, "run_child", fake_run_child)
    previous = tr._ci_smoke_allowed  # the T1 job sets the opt-in for the whole session through conftest; restore it
    tr.allow_ci_smoke_bundle(False)
    tr._health_cache.clear()
    yield root, spawned
    tr.allow_ci_smoke_bundle(previous)
    tr._health_cache.clear()


def test_application_check_refuses_a_marked_bundle_by_default(runtime):
    root, _ = runtime
    write_bundle(root)
    problem, lock = rl.check_installed_lock(root, expect_numpy="2.4.6", expect_source_digest=DIGEST)
    assert lock is None and "ci smoke" in problem.lower()
    assert rl.check_installed_lock(root, expect_numpy="2.4.6", expect_source_digest=DIGEST, allow_ci_smoke=True)[0] == ""


def test_health_check_refuses_a_marked_bundle_without_spawning_and_no_environment_variable_opts_in(runtime, monkeypatch):
    root, spawned = runtime
    write_bundle(root)
    for name in ("ANYFEM_CI_SMOKE", "ANYGEOMETRY_CI_SMOKE", "ANYFEM_ALLOW_CI_SMOKE_BUNDLE", "ANYGEOMETRY_ALLOW_CI_SMOKE_BUNDLE", "ANYFEM_TEST_ALLOW_CI_SMOKE_BUNDLE"):
        monkeypatch.setenv(name, "1")
    tr.configure(reset=True)
    outcome = tr.check_health(root / "python.exe")
    assert outcome.status == tr.UNAVAILABLE and "ci smoke" in outcome.reason.lower()
    assert spawned == []


def test_explicit_opt_in_survives_configure_reset_and_reaches_the_child(runtime):
    root, spawned = runtime
    write_bundle(root)
    tr.allow_ci_smoke_bundle(True)
    tr.configure(reset=True)
    tr.check_health(root / "python.exe")
    assert spawned == ["selftest"]


def test_the_opt_in_is_part_of_the_cache_key(runtime):
    root, spawned = runtime
    write_bundle(root)
    tr.allow_ci_smoke_bundle(True)
    tr.check_health(root / "python.exe")
    tr.allow_ci_smoke_bundle(False)
    assert "ci smoke" in tr.check_health(root / "python.exe").reason.lower()  # no cache clear in between


def test_deleting_the_marker_is_a_bundle_change(runtime):
    root, _ = runtime
    write_bundle(root)
    (root / rl.CI_SMOKE_MARKER).unlink()  # the lock still describes a folder WITH the marker
    problem, _ = rl.check_installed_lock(root, expect_numpy="2.4.6", expect_source_digest=DIGEST, allow_ci_smoke=True)
    assert "differ" in problem


def test_an_ordinary_bundle_is_unaffected(runtime):
    root, spawned = runtime
    write_bundle(root, marker=False)
    assert rl.check_installed_lock(root, expect_numpy="2.4.6", expect_source_digest=DIGEST)[0] == ""
    tr.check_health(root / "python.exe")
    assert spawned == ["selftest"]


def test_marker_is_refused_even_when_the_lock_is_missing_or_schemaless(runtime):
    """Codex r1 P2: a copied smoke bundle without runtime.lock.json looked like a developer runtime and skipped the marker check."""
    root, spawned = runtime
    write_bundle(root)
    (root / rl.LOCK_NAME).unlink()
    outcome = tr.check_health(root / "python.exe")
    assert outcome.status == tr.UNAVAILABLE and "ci smoke" in outcome.reason.lower() and spawned == []
    (root / rl.LOCK_NAME).write_text(json.dumps({"numpy": "2.4.6"}), encoding="utf-8")  # legacy lenient lock
    tr._health_cache.clear()
    outcome = tr.check_health(root / "python.exe")
    assert outcome.status == tr.UNAVAILABLE and "ci smoke" in outcome.reason.lower() and spawned == []
    tr.allow_ci_smoke_bundle(True)  # the explicit opt-in is the only way past it
    tr._health_cache.clear()
    tr.check_health(root / "python.exe")
    assert spawned == ["selftest"]
