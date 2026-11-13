"""What one drawing generation produces: assembly views, sections, plate profiles, index and takeoff."""
from __future__ import annotations

from dataclasses import dataclass

VIEW_NAMES = ("isometric", "plan", "front", "right")
_ALIASES = {"iso": "isometric", "isometric": "isometric", "plan": "plan", "front": "front", "right": "right"}


def normalize_views(views) -> tuple[str, ...]:
    """Canonical assembly view subset: duplicates dropped, order isometric, plan, front, right."""
    if isinstance(views, str):
        raise ValueError("Give the assembly views as a sequence of names")
    chosen = set()
    for name in views:
        key = _ALIASES.get(name.strip().lower()) if isinstance(name, str) else None
        if key is None:
            raise ValueError(f"Unknown assembly view {name!r}; choose from {', '.join(VIEW_NAMES)}")
        chosen.add(key)
    return tuple(name for name in VIEW_NAMES if name in chosen)


def parse_views(text: str) -> tuple[str, ...]:
    """Parse ``all`` or a comma list such as ``iso,plan,front,right``."""
    items = [item.strip() for item in text.split(",")]
    if items == ["all"]:
        return VIEW_NAMES
    if not text.strip() or any(not item for item in items):
        raise ValueError("Give 'all' or a comma-separated list of views: iso, plan, front, right")
    return normalize_views(items)


@dataclass(frozen=True)
class DrawingExtent:
    """The default selects everything, i.e. the historical output."""

    views: tuple = VIEW_NAMES
    sections: bool = True
    plate_profiles: bool = True
    index_and_takeoff: bool = True

    def __post_init__(self):
        object.__setattr__(self, "views", normalize_views(self.views))
        for name in ("sections", "plate_profiles", "index_and_takeoff"):
            object.__setattr__(self, name, bool(getattr(self, name)))
        if not (self.views or self.sections or self.plate_profiles or self.index_and_takeoff):
            raise ValueError("Select at least one view, sections, plate profiles or the index: "
                             "there is nothing to generate")

    @classmethod
    def all_views(cls):
        return cls()

    @classmethod
    def isometric_only(cls):
        return cls(views=("isometric",), sections=False, plate_profiles=False, index_and_takeoff=False)

    @property
    def is_default(self) -> bool:
        return self == DrawingExtent()

    def to_dict(self) -> dict:
        return {"views": list(self.views), "sections": self.sections,
                "plate_profiles": self.plate_profiles, "index_and_takeoff": self.index_and_takeoff}

    @classmethod
    def from_dict(cls, data):
        return cls(views=tuple(data.get("views", VIEW_NAMES)), sections=data.get("sections", True),
                   plate_profiles=data.get("plate_profiles", True),
                   index_and_takeoff=data.get("index_and_takeoff", True))
