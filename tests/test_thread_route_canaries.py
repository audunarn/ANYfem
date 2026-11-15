"""Tamper canaries (CI job T4): a bundled runtime that fails its lock must leave the route unavailable and the serial result unchanged.

These run end to end through ``prepare_structural_connectivity`` with the route ON, against a FIXTURE bundle (a fake ``python.exe`` plus a
lock that really describes the folder), so they need no real runtime and run on ordinary Python.  They prove the decline path and the
serial identity; they do not prove that a REAL bundle passes (that needs the bundle, CI job T1).  Windows only, like the route itself.
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from anygeometry import GeometryModel, to_dict
from anyfem import runtime_lock as rl
from anyfem import thread_route as tr
from anyfem.structural_preparation import prepare_structural_connectivity

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the thread route is Windows only")

DIGEST = "d" * 64
H = "a" * 64
MODEL_ID = "12345678-1234-5678-1234-567812345678"
SOUND_REASON_WORDS = ("lock", "bundle", "source digest", "numpy", "runtime missing")


def crossing() -> GeometryModel:
    model = GeometryModel(model_id=MODEL_ID)
    model.add_plate(model.add_points(((0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0))))
    model.add_plate(model.add_points(((2, 0, -1), (2, 4, -1), (2, 4, 1), (2, 0, 1))))
    return model


def result_of(model, report) -> str:
    blob = json.dumps([to_dict(model), report.to_dict()], sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def write_bundle(root, *, numpy="2.4.6", source=DIGEST, bundle_sha=None):
    (root / "python.exe").write_bytes(b"not a real interpreter")
    digest, count = rl.tree_digest(root, with_count=True)
    data = {
        "schema_version": 1, "platform": "win_amd64", "python_version": "3.14.10",
        "python_version_full": "3.14.10 free-threading build (x)", "numpy": numpy,
        "interpreter": {"upstream_url": "https://example.invalid/p.zip", "upstream_sha256": H, "payload_sha256": H, "payload_files": 1,
                        "root_files": ["python3.14t.exe"], "trees": ["Lib"], "exclude_dirs": ["__pycache__"]},
        "wheels": [{"name": "numpy", "version": numpy, "filename": "numpy-%s-cp314-cp314t-win_amd64.whl" % numpy, "sha256": H}],
        "source_digest": source, "build_date": "2026-11-13", "bundle_sha256": bundle_sha or digest, "bundle_files": count,
    }
    (root / rl.LOCK_NAME).write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def app(tmp_path, monkeypatch):
    for name in (tr.ENV_ENABLE, tr.ENV_RUNTIME, tr.ENV_THREADS, tr.ENV_MIN_FACES):
        monkeypatch.delenv(name, raising=False)
    runtime = tmp_path / "runtime-314t"
    runtime.mkdir()
    monkeypatch.setenv(tr.ENV_APP_DIR, str(tmp_path))
    monkeypatch.setattr(tr, "_parent_source_digest", lambda: DIGEST)
    monkeypatch.setattr(tr, "_INJECT", {})
    monkeypatch.setattr(tr, "last_outcome", None)
    calls = []

    def fake_run_child(python, job, *args, **kwargs):  # records every spawn; the fixture interpreter cannot run anyway
        calls.append(job.get("kind"))
        return tr.ChildOutcome(status=tr.UNAVAILABLE, reason="fixture interpreter cannot run")

    monkeypatch.setattr(tr, "run_child", fake_run_child)
    monkeypatch.setattr(tr, "_spawned", calls, raising=False)
    tr.configure(reset=True)
    tr._health_cache.clear()
    yield runtime
    tr.configure(reset=True)
    tr._health_cache.clear()


@pytest.fixture
def spawned(app):
    return tr._spawned


def serial_reference():
    tr.configure(enabled=False)
    model = crossing()
    report = prepare_structural_connectivity(model)
    return result_of(model, report)


def route_on_result():
    tr._health_cache.clear()
    tr.configure(enabled=True, min_faces=0)  # runtime: the default locator (<app>\runtime-314t), never PATH
    model = crossing()
    report = prepare_structural_connectivity(model)
    return result_of(model, report)


def assert_declined(reason_word):
    assert tr.last_outcome is not None and tr.last_outcome.status == tr.UNAVAILABLE
    assert reason_word in tr.last_outcome.reason, tr.last_outcome.reason


def test_tampered_lock_pin_declines_and_serial_result_is_identical(app, spawned):
    expected = serial_reference()
    write_bundle(app, numpy="2.4.5")  # lock claims another NumPy than the application's pin
    assert route_on_result() == expected
    assert_declined("numpy")
    assert spawned == []  # rejected before any child was started


def test_tampered_lock_source_digest_declines_and_serial_result_is_identical(app, spawned):
    expected = serial_reference()
    write_bundle(app, source="e" * 64)  # lock built for other geometry source
    assert route_on_result() == expected
    assert_declined("source digest")
    assert spawned == []  # rejected before any child was started


def test_wrong_bundle_digest_declines_and_serial_result_is_identical(app, spawned):
    expected = serial_reference()
    write_bundle(app, bundle_sha="f" * 64)
    assert route_on_result() == expected
    assert_declined("bundle")
    assert spawned == []  # rejected before any child was started


def test_planted_file_declines_and_serial_result_is_identical(app, spawned):
    expected = serial_reference()
    write_bundle(app)
    (app / "planted.pyd").write_bytes(b"x")  # added after the lock was made
    assert route_on_result() == expected
    assert_declined("bundle")
    assert spawned == []  # rejected before any child was started


def test_changed_file_declines_and_serial_result_is_identical(app, spawned):
    expected = serial_reference()
    write_bundle(app)
    (app / "python.exe").write_bytes(b"swapped interpreter")
    assert route_on_result() == expected
    assert_declined("bundle")
    assert spawned == []  # rejected before any child was started


def test_missing_runtime_folder_declines_and_serial_result_is_identical(app, spawned):
    expected = serial_reference()
    app.rmdir()
    assert route_on_result() == expected
    assert_declined("runtime missing")
    assert spawned == []  # rejected before any child was started


def test_control_sound_fixture_bundle_passes_the_lock_stage(app, spawned):
    """The canaries above are not vacuous: an untampered fixture reaches the health child (spawn recorded) and is refused only there (its interpreter is fake)."""
    expected = serial_reference()
    write_bundle(app)
    assert route_on_result() == expected
    assert tr.last_outcome.status == tr.UNAVAILABLE
    assert spawned == ["selftest"], spawned  # only the sound fixture got past the lock stage to the health child
    assert not any(word in tr.last_outcome.reason for word in SOUND_REASON_WORDS), tr.last_outcome.reason
