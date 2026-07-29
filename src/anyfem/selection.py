"""Selection state, and the tag encoding that carries it to the viewport.

The viewport draws every entity with a tag; a click hands the tag back; this
module turns it into an ``EntityRef``.  Keeping the encoding here rather than
in the UI means selection can be driven and tested without a display, and the
scripting API can select things too.
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional

from .geometry.entities import EntityRef

__all__ = [
    "SELECTION_MODES",
    "Selection",
    "entity_tag",
    "parse_entity_tag",
]

# Prefix so a pick can tell our tags from anything else drawn on the canvas.
TAG_PREFIX = "ent_"

SELECTION_MODES: tuple[str, ...] = ("vertex", "edge", "face")

_MODE_LABELS = {"vertex": "Point", "edge": "Line", "face": "Plate"}


def entity_tag(ref: EntityRef) -> str:
    """The canvas tag for one entity, e.g. ``ent_face3``."""

    return f"{TAG_PREFIX}{ref.kind}{ref.id}"


def parse_entity_tag(tag: str) -> Optional[EntityRef]:
    """Turn a canvas tag back into an entity reference, or None."""

    if not tag or not tag.startswith(TAG_PREFIX):
        return None
    body = tag[len(TAG_PREFIX) :]
    for kind in SELECTION_MODES:
        if body.startswith(kind):
            digits = body[len(kind) :]
            if digits.isdigit():
                return EntityRef(kind, int(digits))  # type: ignore[arg-type]
    return None


def mode_label(mode: str) -> str:
    return _MODE_LABELS.get(mode, mode)


class Selection:
    """What the user currently has selected, and in which entity mode."""

    def __init__(self, mode: str = "face") -> None:
        self._mode = self._check_mode(mode)
        self._items: List[EntityRef] = []
        self._listeners: List[Callable[[], None]] = []

    # ------------------------------------------------------------------
    @staticmethod
    def _check_mode(mode: str) -> str:
        if mode not in SELECTION_MODES:
            raise ValueError(
                f"unknown selection mode {mode!r}; expected one of "
                f"{', '.join(SELECTION_MODES)}"
            )
        return mode

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch entity mode, dropping anything of the wrong kind."""

        mode = self._check_mode(mode)
        if mode == self._mode:
            return
        self._mode = mode
        # Anything of the previous kind can no longer be acted on.
        self._items = [ref for ref in self._items if ref.kind == mode]
        self._notify()

    # ------------------------------------------------------------------
    @property
    def items(self) -> List[EntityRef]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        # A Selection is a live service object, not a container: an empty one
        # must stay truthy so ``selection or Selection()`` cannot quietly
        # replace a shared selection with a fresh, unshared one.  Ask
        # ``is_empty()`` or ``len()`` about the contents.
        return True

    def __contains__(self, ref: object) -> bool:
        return ref in self._items

    def __iter__(self):
        return iter(list(self._items))

    @property
    def first(self) -> Optional[EntityRef]:
        return self._items[0] if self._items else None

    def is_empty(self) -> bool:
        return not self._items

    # ------------------------------------------------------------------
    def select(self, ref: EntityRef, extend: bool = False) -> None:
        """Select an entity.  ``extend`` toggles it into the current set."""

        if ref.kind != self._mode:
            raise ValueError(
                f"cannot select a {ref.kind} while in {self._mode} mode"
            )
        if extend:
            if ref in self._items:
                self._items.remove(ref)
            else:
                self._items.append(ref)
        else:
            if self._items == [ref]:
                return
            self._items = [ref]
        self._notify()

    def select_many(self, refs: Iterable[EntityRef], extend: bool = False) -> None:
        wanted = [ref for ref in refs if ref.kind == self._mode]
        updated = list(self._items) if extend else []
        for ref in wanted:
            if ref not in updated:
                updated.append(ref)
        # Notifying when nothing changed would let a view that echoes the
        # selection back drive an endless round trip.
        if updated == self._items:
            return
        self._items = updated
        self._notify()

    def clear(self) -> None:
        if not self._items:
            return
        self._items = []
        self._notify()

    def handle_tag(self, tag: str, extend: bool = False) -> Optional[EntityRef]:
        """Apply a pick result.  An unrecognised tag clears the selection."""

        ref = parse_entity_tag(tag)
        if ref is None or ref.kind != self._mode:
            if not extend:
                self.clear()
            return None
        self.select(ref, extend=extend)
        return ref

    # ------------------------------------------------------------------
    def tags(self) -> List[str]:
        """Canvas tags for everything selected, for highlighting."""

        return [entity_tag(ref) for ref in self._items]

    def describe(self) -> str:
        if not self._items:
            return f"No {mode_label(self._mode).lower()} selected"
        if len(self._items) == 1:
            return f"{mode_label(self._mode)} {self._items[0].id}"
        return f"{len(self._items)} {mode_label(self._mode).lower()}s selected"

    # ------------------------------------------------------------------
    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        """Detach a listener, e.g. when its widget is being destroyed."""

        while callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self) -> None:
        for callback in list(self._listeners):
            callback()

    def __repr__(self) -> str:  # pragma: no cover - display only
        return f"Selection(mode={self._mode!r}, items={self._items!r})"
