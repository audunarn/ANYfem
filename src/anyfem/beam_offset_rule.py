"""Which automatic stiffener offset rule a document's saved meshes were made with.

ANYfem 2026-11-24 moved the automatic beam offset from the plate mid-surface to
the plate surface (the stiffener foot sits on the plate).  A mesh stores the
offsets it was generated with, so meshes saved before that change keep the old
offsets until they are generated again.

The project file names the rule in its header (:data:`HEADER_KEY`), written
whenever the project defines a beam section.  When a document that defines a
beam section is read without the current rule, every saved generated mesh is
marked stale (:func:`migrate_pre_rule_meshes`): it is not adopted, reused as a
regional-update parent or admitted for solving until the mesh is generated
again with a full rebuild.

Scope (owner decision B, 2026-11-25): the guarantee covers meshes saved in a
document.  A mesh that reaches assembly or a regional update without its
record tag is still recognised when it reproduces a stale record's semantic
mesh hash.  A hand-assembled ``BuiltModel``, and a mesh dictionary of another
document, are not checked.  Legacy pressure recovery on open still reads a
stale record's shell cells: plate cells do not depend on the beam offset rule.
"""
from __future__ import annotations

from typing import Any, Mapping

HEADER_KEY = "beam_offset_rule"
#: The current automatic offset rule: neutral axis measured from the plate surface.
CURRENT_RULE = "foot_on_plate_surface"
#: Mesh-record summary flag set by the migration.
STALE_FLAG = "beam_offset_rule_stale"
REFUSAL = (
    "this mesh was saved before the automatic stiffener offset rule changed "
    "(stiffener foot on the plate surface); generate the mesh again (full rebuild)"
)
_ADMITTED = ("completed", "inspection_only", "stale")


def needs_rule(project: Any) -> bool:
    """Whether saved meshes of ``project`` can depend on the offset rule."""

    return bool(getattr(project, "beam_sections", None))


def header_rule(data: Mapping[str, Any]) -> str | None:
    header = data.get("anyfem") if isinstance(data, Mapping) else None
    value = header.get(HEADER_KEY) if isinstance(header, Mapping) else None
    return value if isinstance(value, str) else None


def is_rule_stale(record: Any) -> bool:
    summary = getattr(record, "summary", None)
    return isinstance(summary, Mapping) and summary.get(STALE_FLAG) is True


def migrate_pre_rule_meshes(project: Any) -> tuple[str, ...]:
    """Mark every saved generated mesh of a pre-rule document stale.

    Returns the IDs of the records marked.  Imported meshes are mesh-native
    (their offsets come from the imported data) and are left alone.
    """

    if not needs_rule(project):
        return ()
    marked = []
    for record in project.mesh_records.values():
        if record.kind == "imported" or record.status not in _ADMITTED:
            continue
        record.status = "stale"
        summary = dict(record.summary)
        summary[STALE_FLAG] = True
        summary.pop("regional_baseline", None)
        summary["regional_refusal"] = REFUSAL
        if "status" in summary:
            summary["status"] = "stale"
        record.summary = summary
        marked.append(record.id)
    return tuple(marked)


def has_rule_stale_records(project: Any) -> bool:
    return any(is_rule_stale(record) for record in project.mesh_records.values())


def rule_stale_record_for(project: Any, mesh: Any) -> Any:
    """The rule-stale record ``mesh`` belongs to, or ``None``.

    A mesh belongs to the active published record of a numbered document, or
    to the record (or artifact) its read tag names.  A mesh without a tag (a
    decoded copy, a worker transport, a read by URI string) belongs to a
    rule-stale record whose semantic mesh hash it reproduces.  A mesh that
    :meth:`Project.generate_mesh` produced in this process is current.
    """

    records = project.mesh_records
    stale = [record for record in records.values() if is_rule_stale(record)]
    if not stale:
        return None
    numbering = project.mesh_numbering
    if numbering.generation:
        active = records.get(numbering.active_mesh_id)
        if active is not None and is_rule_stale(active):
            return active
    if getattr(mesh, "_anyfem_rule_fresh", False):
        return None
    tag = getattr(mesh, "_anyfem_mesh_record_id", None)
    if tag is not None:
        for record in records.values():
            if record.id == tag or record.artifact_id == tag:
                return record if is_rule_stale(record) else None
    from .mesh_jobs import matches_mesh_hash
    for record in stale:
        if record.mesh_hash and matches_mesh_hash(
                mesh, record.mesh_hash, model_hash=record.source_model_hash,
                mesh_input_hash=record.mesh_input_hash,
                structural_preparation=record.structural_preparation):
            return record
    return None


def is_pre_rule_document(document: Mapping[str, Any]) -> bool:
    """Whether a raw project document needs the migration when it is read."""

    return bool(document.get("beam_sections")) and header_rule(document) != CURRENT_RULE
