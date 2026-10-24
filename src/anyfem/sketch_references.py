"""ANYfem facade for fixed sketch references and their optional source links.

All geometry (plane intersections, snapshots, solving, source signatures) is
owned by ANYgeometry. A reference is a fixed snapshot stored with the sketch.
The optional source link (feature parameter ``reference_links``) is separate
metadata: the moment the source changes, is deleted or becomes ambiguous the
link is dropped and the snapshot stays exactly where it was. Nothing here
follows, re-solves or blocks source edits; there is no dependency tracking.
"""
from __future__ import annotations

from typing import Mapping, Sequence

from anygeometry import (
    ReferenceLink, SketchReference, SketchReferenceError, capture_reference_link,
    capture_sketch_reference, reference_links_from_parameters, surviving_reference_links,
)
from anygeometry.entities import EntityRef

__all__ = [
    "LINKS_KEY", "ReferenceLink", "SketchReference", "SketchReferenceError", "capture_links",
    "capture_link", "capture_reference", "describe_reference_error", "detach_stale_links", "link_state",
    "linked_reference_ids", "next_reference_id", "restore_links",
]

LINKS_KEY = "reference_links"

_ADVICE = {
    "missing": "The source geometry was deleted or replaced.",
    "ambiguous": "The source geometry does not resolve to one vertex or edge.",
    "no_intersection": "The source does not meet the sketch plane.",
    "unsupported": "This kind of geometry cannot be used as a sketch reference. Select vertices or edges.",
    "invalid": "The reference definition is inconsistent. Remove it and add it again.",
}


def describe_reference_error(error: Exception) -> str:
    reason = getattr(error, "reason", None)
    advice = _ADVICE.get(reason, "")
    return f"{error}. {advice}".strip() if advice else str(error)


def next_reference_id(existing: Sequence[str]) -> str:
    used = set(existing)
    index = 1
    while f"r{index}" in used:
        index += 1
    return f"r{index}"


def capture_reference(geometry, plane, source: EntityRef, reference_id: str) -> SketchReference:
    """Owner snapshot of ``source`` against ``plane`` (explicit user action only)."""

    current = geometry.resolve_ref(source)
    if len(current) != 1:
        raise SketchReferenceError(
            "missing", f"source {source.kind} {source.id} no longer exists or is ambiguous", reference_id)
    return capture_sketch_reference(geometry, plane, current[0], reference_id)


def capture_link(geometry, source: EntityRef, reference_id: str) -> ReferenceLink | None:
    """Optional source link for an explicit capture; ``None`` when the source cannot be linked."""

    from .commands import _feature_anchor

    try:
        return capture_reference_link(geometry, _feature_anchor(geometry, source), reference_id)
    except SketchReferenceError:
        return None


def capture_links(geometry, sources: Mapping[str, EntityRef]) -> list[dict]:
    """Source-link metadata for ``reference id -> entity``; empty when unlinked."""

    from .commands import _feature_anchor

    links = []
    for reference_id, source in sorted(sources.items()):
        anchor = _feature_anchor(geometry, source)
        links.append(capture_reference_link(geometry, anchor, reference_id).to_dict())
    return links


def linked_reference_ids(parameters: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(item.reference_id for item in reference_links_from_parameters(parameters))


# ------------------------------------------------------------- invalidation


def _history(project):
    return getattr(getattr(project, "geometry", None), "features", None)


def link_state(project) -> dict[int, list]:
    """feature id -> raw link list for every feature that carries links (cheap scan)."""

    history = _history(project)
    scan = getattr(history, "parameter_values", None)
    return {} if scan is None else dict(scan(LINKS_KEY))


def detach_stale_links(project, state: Mapping[int, list] | None = None) -> tuple[int, ...]:
    """Drop links whose source moved, vanished or became ambiguous.

    Only linked sources are compared. Sketch geometry and snapshots are never
    touched. Returns the feature ids that lost at least one link.
    """

    state = link_state(project) if state is None else state
    changed = []
    history = _history(project)
    for feature_id, raw in state.items():
        try:
            links = reference_links_from_parameters({LINKS_KEY: raw})
            alive = surviving_reference_links(project.geometry, links)
            wanted = [item.to_dict() for item in alive]
            stale = len(alive) != len(links)
        except Exception:  # noqa: BLE001 - malformed optional metadata is simply detached
            wanted, stale = [], True
        if not stale:
            continue
        try:
            history.set_metadata_parameter(feature_id, LINKS_KEY, wanted)
        except Exception:  # noqa: BLE001 - never veto a legitimate source edit
            continue
        changed.append(int(feature_id))
    return tuple(changed)


def restore_links(project, state: Mapping[int, list]) -> None:
    """Put exact link metadata back (undo/redo of a detachment)."""

    history = _history(project)
    for feature_id, raw in state.items():
        try:
            history.set_metadata_parameter(feature_id, LINKS_KEY, raw)
        except KeyError:
            continue
