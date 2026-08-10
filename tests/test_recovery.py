"""Project lock decisions and crash-recovery bundles."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket

import pytest

from anyfem.io.recovery import (
    LOCK_SCHEMA,
    LOCK_VERSION,
    LockOwner,
    ProjectLock,
    RecoveryCandidate,
    RecoveryError,
    discard_recovery,
    discover_recoveries,
    load_recovery,
    prune_recoveries,
    write_autosave,
)


UTC = timezone.utc


def _document(name: str = "autosaved") -> dict:
    return {
        "anyfem": {"format": 4, "document_id": "document-1"},
        "name": name,
        "geometry": {},
    }


def _autosave(
    root: Path,
    *,
    sequence: int = 1,
    source: Path | None = None,
    created: datetime | None = None,
    keep: int = 3,
):
    return write_autosave(
        _document(f"revision {sequence}"),
        document_id="document-1",
        revision_id=f"revision-{sequence}",
        revision_sequence=sequence,
        document_hash=f"sha256:document-{sequence}",
        model_hash=f"sha256:model-{sequence}",
        saved_document_hash="sha256:saved",
        source_path=source,
        artifact_refs=({"id": "mesh-1", "uri": "meshes/mesh-1.h5"},),
        root=root,
        created_at=created,
        keep=keep,
    )


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------
def test_a_second_owner_gets_a_read_only_live_decision(tmp_path):
    project = tmp_path / "model.anyfem"
    first = ProjectLock(project)
    acquired = first.acquire()
    assert acquired.acquired
    assert acquired.writable

    second = ProjectLock(project)
    decision = second.acquire()
    assert decision.state == "live"
    assert decision.read_only
    assert not decision.can_take_over
    assert decision.owner is not None
    assert decision.owner.pid == os.getpid()

    assert first.release()
    assert second.inspect().state == "available"


def test_release_never_removes_another_owners_lock(tmp_path):
    lock = ProjectLock(tmp_path / "model.anyfem")
    assert lock.acquire().acquired
    foreign = LockOwner(
        pid=os.getpid(),
        hostname=socket.gethostname(),
        acquired_utc="2026-08-10T10:00:00Z",
        token="foreign-token",
        process_start=None,
    )
    lock.path.write_text(json.dumps(foreign.to_dict()), encoding="utf-8")

    assert not lock.release()
    assert lock.path.exists()
    lock.path.unlink()


def test_a_dead_pid_is_stale_and_takeover_is_explicit(tmp_path):
    lock = ProjectLock(tmp_path / "model.anyfem")
    stale = LockOwner(
        pid=2_147_483_647,
        hostname=socket.gethostname(),
        acquired_utc="2026-08-01T10:00:00Z",
        token="stale-token",
        process_start="old-process",
    )
    lock.path.write_text(json.dumps(stale.to_dict()), encoding="utf-8")

    decision = lock.acquire()
    assert decision.state == "stale"
    assert decision.read_only
    assert decision.can_take_over
    assert lock.path.exists()

    taken = lock.acquire(take_over_stale=True)
    assert taken.state == "acquired"
    assert taken.owner is not None and taken.owner.token == lock.token
    assert lock.release()


def test_a_malformed_lock_is_reported_as_stale_not_silently_deleted(tmp_path):
    lock = ProjectLock(tmp_path / "model.anyfem")
    lock.path.write_text("{not json", encoding="utf-8")

    decision = lock.inspect()
    assert decision.state == "stale"
    assert decision.can_take_over
    assert "invalid" in decision.reason
    assert lock.path.exists()


def test_a_remote_host_lock_is_conservatively_live(tmp_path):
    lock = ProjectLock(tmp_path / "model.anyfem")
    remote = {
        "schema": LOCK_SCHEMA,
        "version": LOCK_VERSION,
        "pid": 123,
        "hostname": "another-host.invalid",
        "acquired_utc": "2026-08-10T10:00:00Z",
        "token": "remote-token",
        "process_start": None,
    }
    lock.path.write_text(json.dumps(remote), encoding="utf-8")

    decision = lock.acquire(take_over_stale=True)
    assert decision.state == "live"
    assert decision.read_only
    assert not decision.can_take_over
    assert "another host" in decision.reason


# ---------------------------------------------------------------------------
# Recovery bundles
# ---------------------------------------------------------------------------
def test_autosave_is_discoverable_and_round_trips_the_document(tmp_path):
    source = tmp_path / "model.anyfem"
    source.write_text('{"saved": true}\n', encoding="utf-8")
    candidate = _autosave(tmp_path / "state", source=source)

    assert candidate.snapshot_path.is_file()
    assert load_recovery(candidate)["name"] == "revision 1"
    assert candidate.source_state == "source_unchanged"
    assert candidate.recommendation == "restore"
    assert candidate.artifact_refs[0]["id"] == "mesh-1"

    found = discover_recoveries(root=tmp_path / "state")
    assert len(found) == 1
    assert found[0].revision_id == "revision-1"


def test_unsaved_and_missing_sources_recover_as_a_new_file(tmp_path):
    unsaved = _autosave(tmp_path / "state", sequence=1)
    missing = _autosave(
        tmp_path / "state",
        sequence=2,
        source=tmp_path / "missing.anyfem",
    )

    assert unsaved.source_state == "unsaved"
    assert unsaved.recommendation == "restore_as"
    assert missing.source_state == "source_missing"
    assert missing.recommendation == "restore_as"


def test_a_source_changed_after_autosave_must_open_as_a_copy(tmp_path):
    source = tmp_path / "model.anyfem"
    source.write_text("original", encoding="utf-8")
    candidate = _autosave(tmp_path / "state", source=source)
    source.write_text("changed externally", encoding="utf-8")

    assert candidate.source_state == "source_changed"
    assert candidate.recommendation == "open_copy"


def test_a_clean_recovery_is_marked_for_discard(tmp_path):
    candidate = write_autosave(
        _document(),
        document_id="document-1",
        revision_id="revision-1",
        revision_sequence=1,
        document_hash="sha256:same",
        model_hash="sha256:model",
        saved_document_hash="sha256:same",
        root=tmp_path / "state",
    )

    assert candidate.clean
    assert candidate.recommendation == "discard_clean"


def test_retention_keeps_the_three_newest_per_document_and_source(tmp_path):
    root = tmp_path / "state"
    start = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    for index in range(5):
        _autosave(
            root,
            sequence=index,
            created=start + timedelta(minutes=index),
            keep=3,
        )

    all_records = discover_recoveries(
        root=root,
        latest_only=False,
        include_expired=True,
        now=start + timedelta(minutes=5),
    )
    assert [item.revision_sequence for item in all_records] == [4, 3, 2]
    latest = discover_recoveries(root=root, now=start + timedelta(minutes=5))
    assert [item.revision_sequence for item in latest] == [4]


def test_pruning_removes_expired_records(tmp_path):
    root = tmp_path / "state"
    old = datetime(2026, 7, 1, tzinfo=UTC)
    candidate = _autosave(root, created=old)

    removed = prune_recoveries(
        root=root,
        now=datetime(2026, 8, 10, tzinfo=UTC),
        max_age=timedelta(days=7),
    )
    assert candidate.bundle_path in removed
    assert not candidate.bundle_path.exists()


def test_retention_is_scoped_by_source_even_for_an_unsaved_document(tmp_path):
    root = tmp_path / "state"
    source = tmp_path / "model.anyfem"
    source.write_text("saved", encoding="utf-8")
    saved = _autosave(root, sequence=1, source=source, keep=1)
    _autosave(root, sequence=2, keep=1)
    unsaved_latest = _autosave(root, sequence=3, keep=1)

    all_records = discover_recoveries(
        root=root, latest_only=False, include_expired=True
    )
    assert {item.bundle_path for item in all_records} == {
        saved.bundle_path,
        unsaved_latest.bundle_path,
    }


def test_discard_is_scoped_to_the_recovery_root(tmp_path):
    root = tmp_path / "state"
    candidate = _autosave(root)
    outside = tmp_path / "valuable"
    outside.mkdir()
    unsafe = RecoveryCandidate(
        **{
            **candidate.__dict__,
            "bundle_path": outside,
            "snapshot_path": outside / "project.anyfem",
        }
    )

    with pytest.raises(RecoveryError, match="outside recovery root"):
        discard_recovery(unsafe, root=root)
    assert outside.exists()

    discard_recovery(candidate, root=root)
    assert not candidate.bundle_path.exists()


def test_malformed_or_path_traversing_metadata_is_not_discovered(tmp_path):
    root = tmp_path / "state"
    candidate = _autosave(root)
    metadata = candidate.bundle_path / "metadata.json"
    data = json.loads(metadata.read_text(encoding="utf-8"))
    data["snapshot_uri"] = "../../outside.anyfem"
    metadata.write_text(json.dumps(data), encoding="utf-8")

    assert discover_recoveries(root=root) == []


def test_a_corrupt_snapshot_is_not_offered_for_recovery(tmp_path):
    root = tmp_path / "state"
    candidate = _autosave(root)
    candidate.snapshot_path.write_text("{}", encoding="utf-8")

    assert discover_recoveries(root=root) == []
    with pytest.raises(RecoveryError, match="checksum"):
        load_recovery(candidate)


def test_failed_bundle_write_never_becomes_discoverable(tmp_path, monkeypatch):
    from anyfem.io import recovery

    original = recovery._atomic_write
    calls = 0

    def fail_on_metadata(path, payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return original(path, payload)

    monkeypatch.setattr(recovery, "_atomic_write", fail_on_metadata)
    root = tmp_path / "state"
    with pytest.raises(OSError, match="disk full"):
        _autosave(root)

    assert discover_recoveries(root=root) == []
    assert not list(root.rglob("*.tmp"))
