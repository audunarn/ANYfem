"""Publication of generated meshes through the project command history."""

from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

from ..document import _model_payload, canonical_hash
from ..io.project_file import project_to_dict
from ..io.mesh_number_journal import allocation_floor, journal_path, persist_allocation
from ..mesh_jobs import mesh_semantic_hash
from ..mesh_numbering import MeshNumbering, PublishMesh, published_state, raise_high_water


_commit_lock = RLock()


def _model_hash(project):
    return canonical_hash(_model_payload(project_to_dict(project)))


def mesh_source_digests(project):
    """Mesh-affecting document inputs, also available after reopening the project."""
    model = _model_payload(project_to_dict(project))
    return {key: canonical_hash(model[key]) for key in (
        "geometry", "meshing", "ownership", "face_sections", "edge_sections",
        "imperfections", "geometry_attachment_regions",
    ) if key in model}


def changed_mesh_sources(project, record):
    prior = record.summary.get("mesh_sources")
    if prior is None:
        return ()
    current = mesh_source_digests(project)
    return tuple(key for key in sorted(set(prior) | set(current))
                 if prior.get(key) != current.get(key))


def verify_published_content(record, mesh):
    if record is None or "mesh_sources" not in record.summary:
        return
    from ..mesh_jobs import matches_mesh_hash
    if not matches_mesh_hash(mesh, record.mesh_hash, model_hash=record.source_model_hash,
                             mesh_input_hash=record.mesh_input_hash, structural_preparation=record.structural_preparation):
        raise ValueError(f"mesh content differs from publication {record.id}")


def retained_mesh(workflow, mesh_id):
    if mesh_id is None:
        return None
    record = workflow.project.mesh_records.get(mesh_id)
    cached = workflow._meshes.get(mesh_id)
    if cached is not None:
        producing_id = getattr(cached, "_anyfem_mesh_record_id", None)
        if producing_id is not None and producing_id != mesh_id:
            raise ValueError("cached mesh belongs to another publication")
        return cached
    if record is not None and workflow.path:
        artifact = workflow.project.artifacts.get(record.artifact_id)
        if artifact is not None:
            from ..io.artifacts import ArtifactStore
            mesh = ArtifactStore(workflow.path).read_mesh(
                artifact, mesh_id=record.id, document_id=workflow.project.document_id,
                mesh_hash=record.mesh_hash or None, legacy_document="mesh_sources" not in record.summary)
            verify_published_content(record, mesh)
            workflow._meshes[mesh_id] = mesh
            return mesh
    raise ValueError(f"restore the retained mesh artifact for {mesh_id} before remeshing")


@dataclass(frozen=True)
class PublicationTicket:
    expected: MeshNumbering
    allocation: MeshNumbering
    parent_id: str | None
    document_id: str
    model_hash: str
    geometry_revision: int
    session: object = field(repr=False, compare=False)
    geometry: object = field(repr=False, compare=False)
    journal: Path | None = field(repr=False, compare=False)


def _number_journal(workflow):
    path = getattr(workflow, "path", None) or workflow.session.path
    return journal_path(path) if path is not None else None


def publication_ticket(workflow):
    project = workflow.project
    expected = project.mesh_numbering
    allocation = expected
    parent_id = expected.active_mesh_id
    if not expected.generation:
        parent_id = getattr(workflow, "mesh_record_id", None)
        # Older documents have no ledger. Include the maxima of every retained
        # published mesh, even if its result or geometry is currently stale.
        for record in project.mesh_records.values():
            if (record.mesh_hash and not record.summary.get("provisional_numbers")
                    and record.status in ("completed", "stale", "inspection_only")):
                allocation = raise_high_water(allocation, retained_mesh(workflow, record.id))
    journal = _number_journal(workflow)
    if journal is not None:
        allocation = allocation_floor(journal, project.document_id, allocation)
    return PublicationTicket(expected, allocation, parent_id, project.document_id,
                             _model_hash(project), project.geometry.revision, workflow.session, project.geometry, journal)


def check_ticket(workflow, ticket):
    project = workflow.project
    journal = _number_journal(workflow)
    if (workflow.session is not ticket.session or project.geometry is not ticket.geometry
            or project.mesh_numbering != ticket.expected or project.document_id != ticket.document_id
            or project.geometry.revision != ticket.geometry_revision
            or _model_hash(project) != ticket.model_hash or journal != ticket.journal
            or (journal is not None and allocation_floor(journal, project.document_id, ticket.allocation) != ticket.allocation)):
        raise ValueError("mesh candidate was superseded by a model edit or another publication")


def unchanged_record(workflow, ticket, settings, baseline):
    from ..regional_dispatch import plan_regional_update
    parent = workflow.project.mesh_records.get(ticket.parent_id)
    if (parent and (parent.source_model_hash == ticket.model_hash or
                    ("mesh_sources" in parent.summary and not changed_mesh_sources(workflow.project, parent)))
            and parent.summary.get("regional_settings_hash") == settings.input_hash
            and plan_regional_update(workflow.project, settings, baseline).is_noop):
        check_ticket(workflow, ticket)
        verify_published_content(parent, retained_mesh(workflow, ticket.parent_id))
        return parent
    return None


def commit_generated_mesh(workflow, ticket, record, mesh, baseline, *,
                          retained_nodes=(), retained_elements=(), repairs=None, settings_hash=None,
                          on_rollback=None, before_publish=None):
    """Commit the active artifact, selected references and both high-water marks together."""
    from ..mesh_reference_update import stage_mesh_references

    project = workflow.project
    with _commit_lock:
        check_ticket(workflow, ticket)
        state = published_state(ticket.allocation, mesh, record.id, retained_nodes, retained_elements)
        selection = getattr(workflow, "selection", None)
        old = retained_mesh(workflow, ticket.parent_id) if ticket.parent_id else None
        if old is not None:
            verify_published_content(project.mesh_records.get(ticket.parent_id), old)

        def stage(replacements):
            return stage_mesh_references(
                project, parent_id=ticket.parent_id, child_id=record.id, old_mesh=old,
                new_mesh=mesh, retained_node_ids=retained_nodes, retained_element_ids=retained_elements,
                selection=getattr(selection, "items", ()), repairs=replacements)

        references = stage(None if callable(repairs) else repairs)
        if references.diagnostics and callable(repairs):
            replacements = repairs(parent_id=ticket.parent_id, child_id=record.id,
                                   old_mesh=old, new_mesh=mesh, diagnostics=references.diagnostics)
            check_ticket(workflow, ticket)
            if replacements is not None:
                references = stage(replacements)
        if references.diagnostics:
            raise ValueError("mesh references require re-selection: " + "; ".join(references.diagnostics))
        # The generator report also carries geometry-domain closure information.
        # Replace its mesh-domain copies with the already translated owner records.
        preparation = dict(record.structural_preparation)
        for key, source in (("qualified_s3", mesh.structural_preparation),
                            ("automation", mesh.hybrid_diagnostics), ("structured_layout", mesh.hybrid_diagnostics)):
            if key in preparation and key in source:
                preparation[key] = source[key]
        record.structural_preparation = preparation
        if baseline is not None:
            record.summary["regional_baseline"] = baseline
            record.summary["regional_settings_hash"] = settings_hash
        else:
            from ..regional_dispatch import regional_refusal
            refusal = regional_refusal(project)
            if refusal:
                record.summary["regional_refusal"] = refusal
        record.summary["mesh_sources"] = mesh_source_digests(project)
        command = PublishMesh(ticket.expected, state, ticket.parent_id, record, references)
        if references is not None and selection is not None:
            command._selection_before = list(selection.items)
            command._selection_after = list(references.selection_after)
        missing = object()
        previous_mesh = workflow._meshes.get(record.id, missing)
        previous_tag = getattr(mesh, "_anyfem_mesh_record_id", missing)

        def restore_cache():
            if previous_mesh is missing:
                workflow._meshes.pop(record.id, None)
            else:
                workflow._meshes[record.id] = previous_mesh
            if previous_tag is missing:
                if hasattr(mesh, "_anyfem_mesh_record_id"):
                    del mesh._anyfem_mesh_record_id
            else:
                mesh._anyfem_mesh_record_id = previous_tag

        with workflow.session.transaction(command.label) as transaction:
            transaction.on_rollback(restore_cache)
            if on_rollback is not None:
                transaction.on_rollback(on_rollback)
            if before_publish is not None:
                before_publish()
            if ticket.journal is not None:
                def write_numbers():
                    lock = getattr(workflow, "_file_lock", None) or getattr(workflow, "_project_lock", None)
                    if lock is None or lock.inspect().state != "owned" or journal_path(lock.project_path) != ticket.journal:
                        raise ValueError("mesh publication requires the current project's edit lock")
                    persist_allocation(ticket.journal, project.document_id, project.mesh_numbering)

                transaction.persist_on_commit(ticket.journal, write_numbers)
            mesh._anyfem_mesh_record_id = record.id
            workflow._meshes[record.id] = mesh
            workflow.session.commands.run(command)
            if hasattr(command, "_selection_after"):
                selection.restore(command._selection_after)
            # Scoped references may now name the child mesh. Hash the final
            # model just as reopening the document will, before publishing it.
            record.source_model_hash = _model_hash(project)
            record.mesh_hash = mesh_semantic_hash(
                mesh, model_hash=record.source_model_hash, mesh_input_hash=record.mesh_input_hash,
                structural_preparation=record.structural_preparation)
