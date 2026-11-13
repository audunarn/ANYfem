"""Application-side health check of the bundled runtime: the full lock + bundle check runs BEFORE any child is spawned."""
from __future__ import annotations

import json

import pytest

from anyfem import runtime_lock as rl
from anyfem import thread_route as tr

DIGEST = "d" * 64
H = "a" * 64


def _lock(root, *, numpy="2.4.6", source=DIGEST, tamper_after=None):
    (root / "python.exe").write_bytes(b"not a real interpreter")
    digest, count = rl.tree_digest(root, with_count=True)
    data = {
        "schema_version": 1, "platform": "win_amd64", "python_version": "3.14.10",
        "python_version_full": "3.14.10 free-threading build (x)", "numpy": numpy,
        "interpreter": {"upstream_url": "https://example.invalid/p.zip", "upstream_sha256": H, "payload_sha256": H, "payload_files": 1,
                        "root_files": ["python3.14t.exe"], "trees": ["Lib"], "exclude_dirs": ["__pycache__"]},
        "wheels": [{"name": "numpy", "version": numpy, "filename": "numpy-%s-cp314-cp314t-win_amd64.whl" % numpy, "sha256": H}],
        "source_digest": source, "build_date": "2026-11-13", "bundle_sha256": digest, "bundle_files": count,
    }
    (root / rl.LOCK_NAME).write_text(json.dumps(data), encoding="utf-8")
    if tamper_after:
        tamper_after(root)


@pytest.fixture
def app(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime-314t"
    runtime.mkdir()
    monkeypatch.setenv(tr.ENV_APP_DIR, str(tmp_path))
    monkeypatch.setattr(tr, "_parent_source_digest", lambda: DIGEST)
    tr._health_cache.clear()
    yield runtime
    tr._health_cache.clear()


def _health(runtime):
    return tr.check_health(runtime / "python.exe", timeout=5, temp_root=str(runtime.parent))


def test_bundled_runtime_without_lock_is_unavailable(app):
    (app / "python.exe").write_bytes(b"x")
    outcome = _health(app)
    assert outcome.status == tr.UNAVAILABLE and "lock" in outcome.reason


@pytest.mark.parametrize("kwargs, word", [
    (dict(numpy="2.4.5"), "numpy"),
    (dict(source="e" * 64), "source digest"),
])
def test_bundled_runtime_with_wrong_pins_is_unavailable(app, kwargs, word):
    _lock(app, **kwargs)
    outcome = _health(app)
    assert outcome.status == tr.UNAVAILABLE and word in outcome.reason


@pytest.mark.parametrize("tamper", [
    lambda r: (r / "python.exe").write_bytes(b"swapped interpreter"),
    lambda r: (r / "planted.pyd").write_bytes(b"x"),
    lambda r: (r / "__pycache__").mkdir() or (r / "__pycache__" / "m.pyc").write_bytes(b"x"),
])
def test_bundled_runtime_with_changed_or_extra_files_is_unavailable(app, tamper):
    _lock(app, tamper_after=tamper)
    outcome = _health(app)
    assert outcome.status == tr.UNAVAILABLE and "bundle" in outcome.reason


def test_bundled_runtime_with_malformed_lock_is_unavailable(app):
    _lock(app)
    (app / rl.LOCK_NAME).write_text('{"numpy": "2.4.6"}', encoding="utf-8")  # the old lenient format is not accepted when bundled
    outcome = _health(app)
    assert outcome.status == tr.UNAVAILABLE and "invalid" in outcome.reason


def test_sound_bundle_passes_the_lock_stage_and_only_then_reaches_the_child(app):
    _lock(app)
    outcome = _health(app)  # the fake interpreter cannot run: unavailable, but NOT for a lock or bundle reason
    assert outcome.status == tr.UNAVAILABLE
    assert "lock" not in outcome.reason and "bundle" not in outcome.reason and "source digest" not in outcome.reason


def test_override_runtime_with_legacy_lock_keeps_the_lenient_check(tmp_path, monkeypatch):
    monkeypatch.setenv(tr.ENV_APP_DIR, str(tmp_path / "elsewhere"))
    monkeypatch.setattr(tr, "_parent_source_digest", lambda: DIGEST)
    tr._health_cache.clear()
    dev = tmp_path / "dev-runtime"
    dev.mkdir()
    (dev / "python.exe").write_bytes(b"x")
    (dev / "runtime.lock.json").write_text(json.dumps({"numpy": "2.4.6", "source_digest": DIGEST}), encoding="utf-8")
    outcome = tr.check_health(dev / "python.exe", timeout=5, temp_root=str(tmp_path))
    assert "bundle" not in (outcome.reason or "") and "invalid" not in (outcome.reason or "")
    (dev / "runtime.lock.json").write_text(json.dumps({"numpy": "9.9.9"}), encoding="utf-8")
    tr._health_cache.clear()
    assert "NumPy" in tr.check_health(dev / "python.exe", timeout=5, temp_root=str(tmp_path)).reason
    tr._health_cache.clear()


def test_failed_bundle_check_is_cached_for_the_process(app, monkeypatch):
    _lock(app, tamper_after=lambda r: (r / "planted.pyd").write_bytes(b"x"))
    calls = []
    real = rl.check_installed_lock
    monkeypatch.setattr(rl, "check_installed_lock", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert _health(app).status == tr.UNAVAILABLE and _health(app).status == tr.UNAVAILABLE
    assert len(calls) == 1


def test_override_runtime_that_declares_a_schema_gets_the_full_check(tmp_path, monkeypatch):
    monkeypatch.setenv(tr.ENV_APP_DIR, str(tmp_path / "elsewhere"))
    monkeypatch.setattr(tr, "_parent_source_digest", lambda: DIGEST)
    tr._health_cache.clear()
    dev = tmp_path / "dev-runtime"
    dev.mkdir()
    _lock(dev, tamper_after=lambda r: (r / "planted.pyd").write_bytes(b"x"))
    outcome = tr.check_health(dev / "python.exe", timeout=5, temp_root=str(tmp_path))
    assert outcome.status == tr.UNAVAILABLE and "bundle" in outcome.reason
    tr._health_cache.clear()


# ------------------------------------------------------------------------------------------ review round 1 (Codex r1)

def _spy_run_child(monkeypatch):
    calls = []
    real = tr.run_child
    monkeypatch.setattr(tr, "run_child", lambda *a, **k: calls.append(1) or real(*a, **k))
    return calls


def test_rejected_bundle_never_spawns_a_child(app, monkeypatch):
    calls = _spy_run_child(monkeypatch)
    _lock(app, tamper_after=lambda r: (r / "planted.pyd").write_bytes(b"x"))
    assert _health(app).status == tr.UNAVAILABLE
    assert calls == []


def test_sound_bundle_does_reach_the_child_stage(app, monkeypatch):
    calls = _spy_run_child(monkeypatch)
    _lock(app)
    _health(app)
    assert calls == [1]


def test_explicit_lock_path_cannot_switch_the_full_check_off(app, monkeypatch):
    _lock(app, tamper_after=lambda r: (r / "planted.pyd").write_bytes(b"x"))
    legacy = app.parent / "legacy.lock.json"
    legacy.write_text(json.dumps({"numpy": "2.4.6", "source_digest": DIGEST}), encoding="utf-8")
    outcome = tr.check_health(app / "python.exe", timeout=5, temp_root=str(app.parent), lock_path=legacy)
    assert outcome.status == tr.UNAVAILABLE and "bundle" in outcome.reason
    # and the weaker answer is not cached for the default call
    assert "bundle" in _health(app).reason


def test_nested_json_lock_is_unavailable_not_an_exception(app):
    (app / "python.exe").write_bytes(b"x")
    (app / rl.LOCK_NAME).write_bytes(b"[" * 100000)
    outcome = _health(app)
    assert outcome.status == tr.UNAVAILABLE and "lock" in outcome.reason
    legacy = app.parent / "legacy.json"
    legacy.write_bytes(b"[" * 100000)
    tr._health_cache.clear()
    assert tr.check_health(app / "python.exe", timeout=5, temp_root=str(app.parent), lock_path=legacy).status == tr.UNAVAILABLE


def test_explicit_legacy_lock_is_still_checked_next_to_the_full_check(app):
    _lock(app)  # a sound bundle
    legacy = app.parent / "legacy.lock.json"
    legacy.write_text(json.dumps({"numpy": "9.9.9"}), encoding="utf-8")
    outcome = tr.check_health(app / "python.exe", timeout=5, temp_root=str(app.parent), lock_path=legacy)
    assert outcome.status == tr.UNAVAILABLE and "NumPy" in outcome.reason


def test_nested_json_lock_of_a_developer_runtime_is_unavailable_not_an_exception(tmp_path, monkeypatch):
    monkeypatch.setenv(tr.ENV_APP_DIR, str(tmp_path / "elsewhere"))
    monkeypatch.setattr(tr, "_parent_source_digest", lambda: DIGEST)
    tr._health_cache.clear()
    dev = tmp_path / "dev-runtime"
    dev.mkdir()
    (dev / "python.exe").write_bytes(b"x")
    (dev / "runtime.lock.json").write_bytes(b"[" * 100000)
    outcome = tr.check_health(dev / "python.exe", timeout=5, temp_root=str(tmp_path))
    assert outcome.status == tr.UNAVAILABLE and "lock" in outcome.reason
    tr._health_cache.clear()


# ------------------------------------------------------------------------------------------ review round 2 (Codex r2)

def _ok_child(monkeypatch, version_full="3.14.10 free-threading build (x)", on_run=None):
    def fake(python, job, document, **kwargs):
        if on_run:
            on_run()
        return tr.ChildOutcome(status=tr.OK, body={"health": {"version_full": version_full}, "smoke": tr._batch_prepare.SMOKE_DIGEST})
    monkeypatch.setattr(tr, "run_child", fake)


def test_interpreter_pin_of_the_root_lock_survives_an_explicit_legacy_lock(app, monkeypatch):
    _lock(app)  # root lock pins "3.14.10 free-threading build (x)"
    _ok_child(monkeypatch, version_full="3.14.10 some other build")
    legacy = app.parent / "legacy.lock.json"
    legacy.write_text(json.dumps({"numpy": "2.4.6", "source_digest": DIGEST}), encoding="utf-8")
    outcome = tr.check_health(app / "python.exe", timeout=5, temp_root=str(app.parent), lock_path=legacy)
    assert outcome.status == tr.UNAVAILABLE and "different interpreter build" in outcome.reason


def test_dev_runtime_root_lock_pin_survives_an_explicit_lock(tmp_path, monkeypatch):
    monkeypatch.setenv(tr.ENV_APP_DIR, str(tmp_path / "elsewhere"))
    monkeypatch.setattr(tr, "_parent_source_digest", lambda: DIGEST)
    tr._health_cache.clear()
    dev = tmp_path / "dev"
    dev.mkdir()
    (dev / "python.exe").write_bytes(b"x")
    (dev / "runtime.lock.json").write_text(json.dumps({"python_version_full": "A"}), encoding="utf-8")
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"numpy": "2.4.6"}), encoding="utf-8")
    _ok_child(monkeypatch, version_full="B")
    outcome = tr.check_health(dev / "python.exe", timeout=5, temp_root=str(tmp_path), lock_path=other)
    assert outcome.status == tr.UNAVAILABLE and "different interpreter" in outcome.reason
    tr._health_cache.clear()


def test_policy_change_for_the_same_interpreter_is_not_served_from_the_cache(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime-314t"
    runtime.mkdir()
    (runtime / "python.exe").write_bytes(b"x")
    monkeypatch.setattr(tr, "_parent_source_digest", lambda: DIGEST)
    _ok_child(monkeypatch)
    tr._health_cache.clear()
    monkeypatch.setenv(tr.ENV_APP_DIR, str(tmp_path / "elsewhere"))  # 1st: a developer runtime, lenient check
    assert tr.check_health(runtime / "python.exe", timeout=5, temp_root=str(tmp_path)).status == tr.OK
    monkeypatch.setenv(tr.ENV_APP_DIR, str(tmp_path))  # 2nd: the very same folder is now the shipped runtime, no built lock
    outcome = tr.check_health(runtime / "python.exe", timeout=5, temp_root=str(tmp_path))
    assert outcome.status == tr.UNAVAILABLE and "lock" in outcome.reason
    tr._health_cache.clear()


def test_lock_changed_or_corrupted_while_the_child_runs_cannot_crash_the_check(app, monkeypatch):
    _lock(app)
    for corrupt in (b"[" * 100000, b"[]", b"\xff\xfe"):
        tr._health_cache.clear()
        _ok_child(monkeypatch, on_run=lambda c=corrupt: (app / rl.LOCK_NAME).write_bytes(c))
        _lock(app)
        outcome = _health(app)
        assert outcome.status in (tr.OK, tr.UNAVAILABLE)
