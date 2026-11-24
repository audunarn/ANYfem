"""Name the source face and builder groups on a mesher refusal that names a working face."""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, TypeVar

from anygeometry import EntityRef

__all__ = ["call_naming_source_faces", "name_source_of_face_refusal"]

T = TypeVar("T")


def name_source_of_face_refusal(error: BaseException, source_to_working: Mapping[str, Iterable[str]],
                                source_geometry: Any) -> None:
    """Add the source face(s) and builder groups to a mesher refusal that names a working face.

    Diagnostics only: the exception keeps its type; its message gains a
    ``[source face S; groups ...]`` suffix and the attributes ``source_face_ids``
    and ``builder_groups``.  Refusals without ``quad_first_face`` are untouched.
    """
    working = getattr(error, "quad_first_face", None)
    if working is None or hasattr(error, "source_face_ids"):
        return
    label = f"face:{int(working)}"
    sources = tuple(sorted(
        int(key.split(":", 1)[1]) for key, values in source_to_working.items()
        if key.startswith("face:") and label in tuple(values)))
    groups: tuple[str, ...] = ()
    try:
        members = getattr(source_geometry, "groups", None) or {}
        refs = {EntityRef("face", item) for item in sources}
        groups = tuple(sorted(name for name, items in members.items() if refs.intersection(items)))
    except Exception:  # diagnostics never replace the refusal
        groups = ()
    error.source_face_ids = sources
    error.builder_groups = groups
    named = ", ".join(str(item) for item in sources) or "unknown"
    where = f"[source face {named}" + (f"; groups: {'; '.join(groups)}]" if groups else "]")
    if error.args and isinstance(error.args[0], str):
        error.args = (f"{error.args[0]} {where}", *error.args[1:])


def call_naming_source_faces(action: Callable[..., T], source_to_working: Mapping[str, Iterable[str]],
                             source_geometry: Any, *args: Any, **kwargs: Any) -> T:
    """Call ``action``; a refusal naming a working face also names its source face(s)."""
    try:
        return action(*args, **kwargs)
    except Exception as error:
        name_source_of_face_refusal(error, source_to_working, source_geometry)
        raise
