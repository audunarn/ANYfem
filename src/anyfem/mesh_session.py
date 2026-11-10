"""Headless generation and explicit regional updates of published project meshes."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import RLock
from weakref import finalize

from .application.mesh_publication import (
    commit_generated_mesh, publication_ticket, retained_mesh, unchanged_record, verify_published_content,
)
from .document import DocumentSession
from .io.artifacts import ArtifactStore
from .io.project_file import save_project
from .io.recovery import ProjectLock
from .mesh_jobs import MeshSettings, RegionalJobInput, _cancellation_token, run_mesh_job
from .mesh_numbering import number_full_mesh
from .model.records import MeshRecord


class ProjectMeshSession:
    """Publish meshes with permanent IDs. Supply the project path to reopen its artifacts."""

    def __init__(self, project, path=None):
        self.project = project
        self._file_base = getattr(project, "_mesh_file_base", None)
        if path is None and self._file_base is not None:
            path = self._file_base[0]
        self.path = Path(path) if path is not None else None
        self.session = DocumentSession(project, path=self.path)
        self._meshes = {}
        self._file_guard = RLock()
        self._file_lock = None
        self._lock_cleanup = None
        self._closed = False

    def _acquire_file(self, destination):
        lock = ProjectLock(destination)
        if self._file_lock and self._file_lock.project_path == lock.project_path:
            lock = self._file_lock
        if not lock.acquire().acquired:
            raise ValueError(f"another session owns the project lock for {lock.project_path}")
        try:
            if self._file_base and self._file_base[0] == lock.project_path:
                path, expected = self._file_base
                if not path.is_file() or sha256(path.read_bytes()).digest() != expected:
                    raise ValueError("the saved project was superseded; reopen it before publishing a mesh")
            elif lock.project_path.exists():
                raise ValueError("open the existing project before publishing to its numbering ledger")
        except BaseException:
            if lock is not self._file_lock:
                lock.release()
            raise
        return lock

    def _release_file(self):
        if self._lock_cleanup:
            self._lock_cleanup()
        self._lock_cleanup = self._file_lock = None

    def close(self):
        """Release this session's edit lock. Save first to persist its mesh publications."""
        with self._file_guard:
            self._release_file()
            self._closed = True

    @property
    def mesh_record_id(self):
        numbering = self.project.mesh_numbering
        if numbering.generation:
            return numbering.active_mesh_id
        # Legacy documents had no active UUID in a numbering ledger. Restore
        # an unambiguous artifact without assigning identities to other records.
        eligible = [r for r in self.project.mesh_records.values()
                    if r.status == "completed" and r.artifact_id in self.project.artifacts
                    and not r.summary.get("provisional_numbers")]
        marked = [r for r in eligible if r.summary.get("active_mesh")]
        candidates = marked or eligible
        return candidates[0].id if len(candidates) == 1 else None

    @property
    def mesh(self):
        return retained_mesh(self, self.mesh_record_id)

    def generate_mesh(self, settings: MeshSettings, *, cancellation=None, progress=None, repairs=None):
        """Publish a full rebuild. Each successful rebuild allocates fresh numbers."""
        return self._generate(settings, False, cancellation, progress, repairs)

    def update_mesh(self, settings: MeshSettings, *, cancellation=None, progress=None, repairs=None):
        """Generate only the affected owners and publish atomically."""
        return self._generate(settings, True, cancellation, progress, repairs)

    def preview_update(self, settings: MeshSettings):
        from .regional_dispatch import plan_regional_update
        return plan_regional_update(self.project, settings, self._baseline())

    def _baseline(self):
        record = self.project.mesh_records.get(self.mesh_record_id)
        if record is None or "regional_baseline" not in record.summary:
            if record is not None and record.summary.get("regional_refusal"):
                raise ValueError(record.summary["regional_refusal"])
            raise ValueError("Update mesh requires a supported generated baseline")
        return record.summary["regional_baseline"]

    def _generate(self, settings, regional, cancellation, progress, repairs):
        from anymesher.serialize import mesh_to_dict
        from .regional_dispatch import capture_regional_baseline

        if self._closed:
            raise ValueError("mesh session is closed")
        if not isinstance(settings, MeshSettings) or settings.regional is not None:
            raise ValueError("expected MeshSettings without a preselected parent")
        ticket = publication_ticket(self)
        if regional:
            if unchanged_record(self, ticket, settings, self._baseline()):
                return self.mesh
            request = RegionalJobInput(self._baseline(), mesh_to_dict(self.mesh),
                                       ticket.allocation.node_high_water, ticket.allocation.element_high_water)
            settings = replace(settings, regional=request)
        snapshot = self.session.snapshot().thaw()
        token = cancellation if cancellation is not None else _cancellation_token()
        result = run_mesh_job(snapshot, ticket.model_hash, settings, progress or (lambda *_: None), token)
        if result.mesh.hybrid_diagnostics.get("automation", {}).get("status") == "inspection_only":
            raise ValueError("an inspection-only candidate cannot become the active project mesh")
        mesh = result.mesh if regional else number_full_mesh(
            result.mesh, ticket.allocation, source_geometry=self.project.geometry)
        baseline = result.regional_baseline if regional else capture_regional_baseline(self.project, settings, mesh)
        quality = result.quality
        if mesh is not result.mesh:
            from anymesher import verify_mesh_quality
            quality = verify_mesh_quality(mesh).as_dict()
        record = MeshRecord(
            name=f"Mesh {len(self.project.mesh_records) + 1}",
            source_model_hash=ticket.model_hash, mesh_input_hash=result.mesh_input_hash,
            mesh_hash="", structural_preparation=dict(result.structural_preparation),
            summary={"nodes": mesh.num_nodes, "elements": mesh.num_elements, "quality": dict(quality)},
        )
        token.raise_if_cancelled("mesh publication")
        with self._file_guard:
            if self._closed:
                raise ValueError("mesh session is closed")
            lock = self._acquire_file(self.path) if self.path else None
            acquired = lock is not None and self._file_lock is None
            if acquired:
                self._file_lock = lock
                self._lock_cleanup = finalize(self, lock.release)

            def before_publish():
                if self._closed:
                    raise ValueError("mesh session is closed")
                token.raise_if_cancelled("mesh publication")

            try:
                commit_generated_mesh(self, ticket, record, mesh, baseline, retained_nodes=result.retained_node_ids,
                                      retained_elements=result.retained_element_ids, repairs=repairs,
                                      settings_hash=settings.input_hash, before_publish=before_publish,
                                      on_rollback=self._release_file if acquired else None)
            except BaseException:
                if acquired:
                    self._release_file()
                raise
        return mesh

    def save(self, path=None):
        destination = Path(path) if path is not None else self.path
        if destination is None:
            raise ValueError("saving a new mesh project requires a path")
        with self._file_guard:
            if self._closed:
                raise ValueError("mesh session is closed")
            lock = self._acquire_file(destination)
            try:
                result = self._save_artifacts(lock.project_path)
            finally:
                if lock is not self._file_lock:
                    lock.release()
            self._file_base = self.project._mesh_file_base
            self._release_file()
            return result

    def _save_artifacts(self, destination):
        from .io.mesh_number_journal import saved_allocation
        store = ArtifactStore(destination)
        with self.session.transaction("save mesh artifacts", solver_affecting=False,
                                      persist=lambda: save_project(self.project, destination)):
            self.project.mesh_numbering = saved_allocation(self.project, destination)
            if self.path is not None:
                source = ArtifactStore(self.path)
                if source.root.resolve() != store.root.resolve():
                    for artifact in self.project.artifacts.values():
                        store.copy_from(source, artifact)
            for mesh_id, mesh in self._meshes.items():
                record = self.project.mesh_records.get(mesh_id)
                if record is None:
                    continue
                previous = self.project.artifacts.get(record.artifact_id)
                if previous is not None and store.verify(previous):
                    continue
                verify_published_content(record, mesh)
                artifact = store.write_mesh(mesh, mesh_id=mesh_id, document_id=self.project.document_id,
                                            model_hash=record.source_model_hash, mesh_hash=record.mesh_hash,
                                            structural_preparation=record.structural_preparation)
                self.project.artifacts[artifact.id] = artifact
                record.artifact_id = artifact.id
        self.path = destination
        self.session.mark_saved(destination)
        return destination
