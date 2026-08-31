"""Shared viewport appearance settings.

The values are deliberately independent of Tk and result data.  A Results
task may edit them, but the viewport keeps using the same appearance when the
user returns to geometry or mesh views.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

__all__ = ["GEOMETRY_DETAILS", "RENDER_MODES", "VisualizationStyle"]


RENDER_MODES = ("Shaded", "Shaded with edges", "Wireframe")

# Geometry is often much denser than its engineering appearance suggests: a
# cylinder with closely spaced rings contains a distinct curved plate for
# every ring/segment cell.  This is a display policy only; it never changes
# geometry, mesh controls or solver inputs.
GEOMETRY_DETAILS = ("Auto", "Fine", "Balanced", "Fast")


@dataclass(frozen=True)
class VisualizationStyle:
    background: str = "#fbfbfd"
    render_mode: str = "Shaded with edges"
    surface_opacity: float = 1.0
    edge_color: str = "#4a6572"
    edge_width: int = 1
    show_legend: bool = True
    geometry_detail: str = "Auto"

    def __post_init__(self) -> None:
        if self.render_mode not in RENDER_MODES:
            raise ValueError(
                f"render mode must be one of {', '.join(RENDER_MODES)}"
            )
        if self.geometry_detail not in GEOMETRY_DETAILS:
            raise ValueError(
                "geometry detail must be one of "
                f"{', '.join(GEOMETRY_DETAILS)}"
            )
        opacity = float(self.surface_opacity)
        if not math.isfinite(opacity) or not 0.0 <= opacity <= 1.0:
            raise ValueError("surface opacity must be between 0 and 1")
        width = int(self.edge_width)
        if not 1 <= width <= 8:
            raise ValueError("edge width must be between 1 and 8 pixels")
        if not str(self.background).strip():
            raise ValueError("background colour cannot be empty")
        if not str(self.edge_color).strip():
            raise ValueError("edge colour cannot be empty")
