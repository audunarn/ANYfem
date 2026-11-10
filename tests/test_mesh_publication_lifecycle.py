"""Publication lifecycle through document transactions, headless sessions and workbench file I/O."""

from types import SimpleNamespace

import pytest

from anyfem.application.controller import WorkbenchController
from anyfem.application.mesh_publication import check_ticket, publication_ticket
from anyfem.commands import DeleteProjectRecord
from anyfem.io.artifacts import ArtifactError, ArtifactStore
from anyfem.io.project_file import load_project, project_from_dict, project_to_dict, save_project
from anyfem.mesh_jobs import MeshSettings

from test_mesh_publication_numbers import Workbench, plates


SETTINGS = MeshSettings.create(.5, strategy="mapped", element_order="linear")


def test_two_saved_sessions_arbitrate_publication_and_reopen(tmp_path):
    from dataclasses import replace
    from anyfem.io.recovery import ProjectLock

    app, _, boundaries = plates()
    first = app.project.mesh_session()
    original = first.generate_mesh(SETTINGS)
    path = first.save(tmp_path / "shared.anyfem")
    second = load_project(path).mesh_session()
    prior = second.project.mesh_numbering
    changed = replace(SETTINGS, overrides=tuple((edge, 4) for edge in boundaries[0]))
    updated = first.update_mesh(changed)
    assert updated is not original and ProjectLock(path).inspect().state == "live"
    try:
        with pytest.raises(ValueError, match="owns the project lock"):
            second.update_mesh(changed)
        assert second.project.mesh_numbering == prior
        assert set(second.project.mesh_records) == {prior.active_mesh_id}
        first.save()
        assert ProjectLock(path).inspect().state == "available"
        with pytest.raises(ValueError, match="superseded"):
            second.update_mesh(changed)
        with pytest.raises(ValueError, match="superseded"):
            second.save()
        assert ProjectLock(path).inspect().state == "available"
        assert second.project.mesh_numbering == prior
        current = load_project(path).mesh_session()
        assert current.mesh_record_id == first.mesh_record_id
        rebuilt = current.generate_mesh(changed)
        assert min(rebuilt.quads) > first.project.mesh_numbering.element_high_water
        fork = current.save(tmp_path / "copy.anyfem")
        disk = load_project(path).mesh_numbering
        assert disk.active_mesh_id == first.mesh_record_id
        assert disk.element_high_water == current.project.mesh_numbering.element_high_water
        assert load_project(fork).mesh_numbering == current.project.mesh_numbering
        assert ProjectLock(path).inspect().state == ProjectLock(fork).inspect().state == "available"
        current.close()
    finally:
        first.close()
        second.close()


def test_saved_session_releases_lock_on_refusal_rollback_and_close(tmp_path, monkeypatch):
    from anyfem.io.recovery import ProjectLock

    app, _, _ = plates()
    editor = app.project.mesh_session()
    parent = editor.generate_mesh(SETTINGS)
    path = editor.save(tmp_path / "rollback.anyfem")
    prior = editor.project.mesh_numbering

    class CancelAtCommit:
        is_cancelled = False

        def raise_if_cancelled(self, stage):
            if stage == "mesh publication":
                raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        editor.generate_mesh(SETTINGS, cancellation=CancelAtCommit())
    assert ProjectLock(path).inspect().state == "available"
    with monkeypatch.context() as patch:
        def reject_revision(**kwargs):
            raise ValueError("reject the revision")
        patch.setattr(editor.session, "_make_revision", reject_revision)
        with pytest.raises(ValueError, match="reject the revision"):
            with editor.session.transaction("outer bulk edit"):
                editor.generate_mesh(SETTINGS)
    assert editor.mesh is parent and editor.project.mesh_numbering == prior
    assert ProjectLock(path).inspect().state == "available"
    assert editor.project.mesh_session().path == path
    editor.generate_mesh(SETTINGS)
    assert ProjectLock(path).inspect().state == "live"
    discarded = editor.project.mesh_numbering
    before_bytes = path.read_bytes()
    with editor.session.transaction("unfinished edit"):
        with pytest.raises(ValueError, match="before saving"):
            editor.save()
    with monkeypatch.context() as patch:
        patch.setattr(editor.session, "_make_revision", reject_revision)
        with pytest.raises(ValueError, match="reject the revision"):
            editor.save()
    assert path.read_bytes() == before_bytes
    assert editor.project.mesh_records[editor.mesh_record_id].artifact_id is None
    editor.close()
    assert ProjectLock(path).inspect().state == "available"
    with pytest.raises(ValueError, match="closed"):
        editor.save()
    reopened = load_project(path).mesh_session()
    assert reopened.mesh_record_id == prior.active_mesh_id
    assert reopened.project.mesh_numbering.element_high_water == discarded.element_high_water
    try:
        rebuilt = reopened.generate_mesh(SETTINGS)
        assert min(rebuilt.nodes) > discarded.node_high_water
        assert min(rebuilt.quads) > discarded.element_high_water
    finally:
        reopened.close()


def test_journal_advances_only_on_outer_commit(tmp_path, monkeypatch):
    from anyfem.io.mesh_number_journal import journal_path
    from anyfem.io import mesh_number_journal

    app, _, _ = plates()
    editor = app.project.mesh_session()
    editor.generate_mesh(SETTINGS)
    path = editor.save(tmp_path / "journal.anyfem")
    other = load_project(path).mesh_session()
    old_ticket = publication_ticket(other)
    prior = editor.project.mesh_numbering
    journal = journal_path(path)
    try:
        def refuse_write(*args):
            raise OSError("disk unavailable")
        with monkeypatch.context() as patch:
            patch.setattr(mesh_number_journal, "_atomic_write", refuse_write)
            with pytest.raises(OSError, match="disk unavailable"):
                editor.generate_mesh(SETTINGS)
        assert editor.project.mesh_numbering == prior and not journal.exists()
        with editor.session.transaction("two accepted updates"):
            editor.generate_mesh(SETTINGS)
            final = editor.generate_mesh(SETTINGS)
            assert not journal.exists()
        editor.close()
        with pytest.raises(ValueError, match="superseded"):
            check_ticket(other, old_ticket)
        fork = load_project(other.save(tmp_path / "copied.anyfem"))
        assert fork.mesh_numbering.active_mesh_id == prior.active_mesh_id
        assert fork.mesh_numbering.element_high_water == max(final.quads)
        rebuilt = other.generate_mesh(SETTINGS)
        assert min(rebuilt.nodes) > max(final.nodes)
        assert min(rebuilt.quads) > max(final.quads)
    finally:
        editor.close()
        other.close()


class FileWorkbench(Workbench):
    """Use the real open/save commands with widget plumbing omitted."""

    def __init__(self, project, **options):
        super().__init__(project, **options)
        self._project_lock = None
        self._artifact_futures = {}
        self._log_futures = {}

    def _set_project(self, project, *, path, project_lock, read_only):
        self.workbench = WorkbenchController(project, path=path)
        self._project_lock = project_lock
        self.session.read_only = read_only
        self._mesh_details_record_id = None

    def _remember_recent(self, path):
        pass

    def _update_window_title(self):
        pass

    def show_geometry(self, **options):
        pass


def test_workbench_and_headless_session_share_one_file_allocator(tmp_path):
    app, _, _ = plates()
    creator = app.project.mesh_session()
    creator.generate_mesh(SETTINGS)
    path = creator.save(tmp_path / "cross-client.anyfem")
    original_id = creator.mesh_record_id
    editor = FileWorkbench(app.project)
    editor.open_project(str(path))
    second = load_project(path).mesh_session()
    try:
        published = editor.generate_mesh(.5, strategy="mapped")
        with pytest.raises(ValueError, match="owns the project lock"):
            second.generate_mesh(SETTINGS)
    finally:
        editor._project_lock.release()
    try:
        assert second.mesh_record_id == original_id
        rebuilt = second.generate_mesh(SETTINGS)
        assert min(rebuilt.quads) > max(published.quads)
        assert min(rebuilt.nodes) > max(published.nodes)
    finally:
        second.close()


def test_published_mesh_is_available_to_observers_and_observer_failure_is_logged(caplog):
    app, _, _ = plates()
    session = app.project.mesh_session()
    observed = []

    def broken_observer(revision):
        observed.append(session.mesh)
        raise RuntimeError("observer sentinel")

    session.session.add_listener(broken_observer)
    session.session.add_listener(lambda revision: observed.append(session.mesh))
    mesh = session.generate_mesh(SETTINGS)
    assert observed == [mesh, mesh]
    assert session.mesh_record_id == mesh._anyfem_mesh_record_id
    assert "observer sentinel" in caplog.text
    assert session.session.commands.can_undo


def test_outer_validation_rollback_restores_mesh_cache_and_ledger(monkeypatch):
    app, _, _ = plates()
    session = app.project.mesh_session()
    original = session.generate_mesh(SETTINGS)
    state = session.project.mesh_numbering
    revision = session.session.revision

    def invalid_revision(**kwargs):
        raise ValueError("validation sentinel")

    monkeypatch.setattr(session.session, "_make_revision", invalid_revision)
    with pytest.raises(ValueError, match="validation sentinel"):
        with session.session.transaction("outer edit"):
            candidate = session.generate_mesh(SETTINGS)
            assert session.mesh is candidate
    assert session.mesh is original
    assert set(session._meshes) == {state.active_mesh_id}
    assert not hasattr(candidate, "_anyfem_mesh_record_id")
    assert session.project.mesh_numbering == state
    assert session.session.revision is revision
    assert set(session.project.mesh_records) == {state.active_mesh_id}


def test_ticket_binds_the_live_document_session_and_geometry_instance():
    app, _, _ = plates()
    session = app.project.mesh_session()
    ticket = publication_ticket(session)
    duplicate = project_from_dict(project_to_dict(app.project)).mesh_session()
    with pytest.raises(ValueError, match="superseded"):
        check_ticket(duplicate, ticket)
    session.project.geometry = duplicate.project.geometry
    with pytest.raises(ValueError, match="superseded"):
        check_ticket(session, ticket)


@pytest.mark.parametrize("foreign", ["artifact_id", "document_id", "mesh_hash"])
def test_retained_mesh_rejects_a_readable_foreign_sidecar(tmp_path, foreign):
    app, _, _ = plates()
    session = app.project.mesh_session()
    mesh = session.generate_mesh(SETTINGS)
    path = session.save(tmp_path / "source.anyfem")
    project = load_project(path)
    record = project.mesh_records[session.mesh_record_id]
    store = ArtifactStore(path)
    fields = dict(mesh_id=record.id, document_id=project.document_id, mesh_hash=record.mesh_hash)
    fields["mesh_id" if foreign == "artifact_id" else foreign] = "foreign"
    artifact = store.write_mesh(mesh, **fields)
    project.artifacts[artifact.id] = artifact
    record.artifact_id = artifact.id
    restored = project.mesh_session(path)
    with pytest.raises(ArtifactError, match=foreign):
        _ = restored.mesh
    assert not restored._meshes
    save_project(project, path)
    window = FileWorkbench(project)
    window.open_project(str(path))
    try:
        assert not window._meshes and window.mesh is None
        assert foreign in window.status
    finally:
        window._project_lock.release()


def test_workbench_save_as_copies_cold_history_and_resolves_the_submitted_mesh(tmp_path):
    app, _, _ = plates()
    session = app.project.mesh_session()
    first = session.generate_mesh(SETTINGS)
    first_id = session.mesh_record_id
    session.generate_mesh(SETTINGS)
    source = session.save(tmp_path / "source.anyfem")
    window = FileWorkbench(app.project)
    window.open_project(str(source))
    try:
        assert set(window._meshes) == {session.mesh_record_id}
        destination = tmp_path / "fork.anyfem"
        window.save_project(path=str(destination))
        copied = load_project(destination)
        first_record = copied.mesh_records[first_id]
        artifact = copied.artifacts[first_record.artifact_id]
        assert ArtifactStore(destination).verify(artifact)
        window.result_datasets["history"] = SimpleNamespace(identity={
            "mesh_id": first_id, "mesh_hash": first_record.mesh_hash, "document_id": copied.document_id,
        })
        assert window.retained_result_mesh("history").quads == first.quads
        # The archived result can still resolve its explicit sidecar after
        # deleting its tree record and dropping the in-memory mesh cache.
        with window.session.transaction("delete historical row"):
            window.session.commands.run(DeleteProjectRecord("mesh", first_id))
        window._meshes.pop(first_id)
        assert window.retained_result_mesh("history").quads == first.quads
        window.result_datasets["history"] = SimpleNamespace(identity={"mesh_id": "missing"})
        with pytest.raises(ValueError, match="unavailable"):
            window.retained_result_mesh("history")
    finally:
        window._project_lock.release()


def test_headless_save_after_deletion_and_workbench_reopen_keep_active_mesh_empty(tmp_path):
    app, _, _ = plates()
    session = app.project.mesh_session()
    session.generate_mesh(SETTINGS)
    session.generate_mesh(SETTINGS)
    deleted = session.mesh_record_id
    source = session.save(tmp_path / "deleted.anyfem")
    with session.session.transaction("delete active mesh"):
        session.session.commands.run(DeleteProjectRecord("mesh", deleted))
    session.save()
    reopened = load_project(source)
    assert deleted not in reopened.mesh_records and reopened.mesh_records
    assert reopened.mesh_numbering.active_mesh_id is None
    window = FileWorkbench(reopened)
    window.open_project(str(source))
    try:
        assert window.mesh is None and window.mesh_record_id is None
        assert window._mesh_details_record_id is None and not window._meshes
    finally:
        window._project_lock.release()
