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
    real = rl.check_installed
    monkeypatch.setattr(rl, "check_installed", lambda *a, **k: calls.append(1) or real(*a, **k))
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
