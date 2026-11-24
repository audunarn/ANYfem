"""Name the working face, location, source face and builder groups on a mesher face refusal.

ANYmesher records ``quad_first_face`` / ``quad_first_location`` on a per-face quad-first
refusal without changing its message (its own recovery stores ``str(error)`` in successful
fallback results).  This module renders them, with the source lineage and the geometry
groups, at ANYfem's outer meshing boundary only, after any recovery has run.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, TypeVar

from anygeometry import EntityRef
from anymesher.errors import MeshError

__all__ = ["call_naming_source_faces", "name_source_of_face_refusal"]

T = TypeVar("T")


def _source_faces(working: int, source_to_working: Mapping[str, Iterable[str]]) -> tuple[int, ...]:
    label = f"face:{int(working)}"
    return tuple(sorted(
        int(key.split(":", 1)[1]) for key, values in source_to_working.items()
        if key.startswith("face:") and label in tuple(values)))


def _groups(sources: tuple[int, ...], source_geometry: Any, source_revision: Any) -> tuple[str, ...] | None:
    """Groups holding any source face, read only at the revision the lineage came from (else ``None``)."""
    if source_geometry is None or not sources:
        return ()
    def current() -> bool:
        return source_revision is None or int(getattr(source_geometry, "revision", -1)) == int(source_revision)

    if not current():
        return None
    refs = {EntityRef("face", item) for item in sources}
    members = source_geometry.groups  # refusal path only: one membership view per refusal
    names = tuple(sorted(name for name, items in members.items() if refs.intersection(items)))
    # A committed edit between the check and the read would mix revisions: check again.
    return names if current() else None


def name_source_of_face_refusal(error: BaseException, source_to_working: Mapping[str, Iterable[str]],
                                source_geometry: Any, source_revision: Any = None) -> None:
    """Render a mesher face refusal: ``[quad-first face W near (x, y, z); source face S; groups: ...]``.

    Only a :class:`MeshError` carrying ``quad_first_face`` is touched; it keeps its type
    and cause and gains ``source_face_ids`` and ``builder_groups`` (``None`` when the
    source geometry changed since the lineage was prepared).  The suffix is rebuilt from
    the original message each time, so a re-raised refusal shows its current face.
    Diagnostics never replace the refusal.
    """
    if not isinstance(error, MeshError):
        return
    try:
        working = error.__dict__.get("quad_first_face")
        if working is None or not error.args or not isinstance(error.args[0], str):
            return
        original = error.__dict__.setdefault("refusal_message", error.args[0])
        if error.args[0] != original and not error.args[0].startswith(original + " [quad-first face "):
            original = error.__dict__["refusal_message"] = error.args[0]
        sources = _source_faces(int(working), source_to_working)
        try:
            groups = _groups(sources, source_geometry, source_revision)
        except Exception:
            groups = None
        location = error.__dict__.get("quad_first_location")
        where = f"quad-first face {int(working)}"
        if location is not None:
            where += " near (%.4g, %.4g, %.4g)" % tuple(location)
        where += "; source face " + (", ".join(str(item) for item in sources) or "unknown")
        if groups:
            where += "; groups: " + "; ".join(groups)
        elif groups is None and sources:
            where += "; groups: unavailable (geometry changed)"
        error.source_face_ids = sources
        error.builder_groups = groups
        error.args = (f"{original} [{where}]", *error.args[1:])
    except Exception:  # diagnostics never replace the refusal
        return


def call_naming_source_faces(action: Callable[..., T], source_to_working: Mapping[str, Iterable[str]],
                             source_geometry: Any, source_revision: Any, *args: Any, **kwargs: Any) -> T:
    """Call ``action``; a mesher refusal naming a working face also names its source face(s).

    Exceptions raised by the caller's own cancellation callback (``cancellation_check``),
    or raised from one, propagate untouched; a typed cancellation signal is passed on
    unwrapped (owners type-check it, and it reports by return value).
    """
    raised: list[BaseException] = []
    check = kwargs.get("cancellation_check")
    if check is not None and callable(check):
        from anygeometry import is_cancellation_signal

        if not is_cancellation_signal(check):
            def tracked(*inner: Any, **named: Any) -> Any:
                try:
                    return check(*inner, **named)
                except BaseException as error:
                    raised.append(error)
                    raise

            kwargs["cancellation_check"] = tracked
    try:
        return action(*args, **kwargs)
    except MeshError as error:
        if not _raised_from(error, raised):
            name_source_of_face_refusal(error, source_to_working, source_geometry, source_revision)
        raise


def _raised_from(error: BaseException, raised: list[BaseException]) -> bool:
    """Whether ``error`` or an exception in its cause/context chain is one of ``raised`` (identity only)."""
    if not raised:
        return False
    seen: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and not any(current is item for item in seen):
        seen.append(current)
        if any(current is item for item in raised):
            return True
        following = current.__cause__
        current = current.__context__ if following is None else following
    return False
