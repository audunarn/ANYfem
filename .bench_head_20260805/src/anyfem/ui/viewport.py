"""The 3D viewport: draws a Scene and turns clicks back into entities.

This is the only module that touches ANYtk3D.  It imports it lazily so the rest
of ANYfem stays usable without the GUI extra installed.
"""

from __future__ import annotations

from typing import Callable, List, Optional

import numpy as np

from ..selection import TAG_PREFIX, Selection
from .scene import PointMarker, Scene

__all__ = ["Viewport", "require_canvas"]


def require_canvas():
    """Import ANYtk3D, with a message that says how to get it."""

    try:
        from anytk3d import Point3D, Tkinter3DCanvas
    except ImportError as error:  # pragma: no cover - depends on the install
        raise ImportError(
            "the ANYfem viewport needs ANYtk3D. Install it with:\n"
            "    python -m pip install ANYfem[gui]"
        ) from error
    return Point3D, Tkinter3DCanvas


class Viewport:
    """A 3D canvas that knows how to draw a Scene and report picks."""

    def __init__(
        self,
        master,
        selection: Optional[Selection] = None,
        width: int = 900,
        height: int = 640,
        background: str = "#fbfbfd",
    ) -> None:
        self._point3d, canvas_class = require_canvas()
        self.canvas = canvas_class(
            master, width=width, height=height, bg=background
        )
        # Not ``selection or Selection()``: Selection defines __len__, so an
        # empty one is falsy and that would quietly make a second, unshared
        # selection object.
        self.selection = Selection() if selection is None else selection
        self._scene: Optional[Scene] = None
        self._on_pick: Optional[Callable[[Optional[object]], None]] = None
        self._marker_size = 0.0

        self.canvas.set_pick_callback(self._handle_pick, prefix=TAG_PREFIX)
        self.selection.add_listener(self._apply_highlight)

    # ------------------------------------------------------------------
    def pack(self, **kwargs):
        return self.canvas.pack(**kwargs)

    def grid(self, **kwargs):
        return self.canvas.grid(**kwargs)

    # ------------------------------------------------------------------
    def show(self, scene: Scene, *, reset_view: bool = False) -> None:
        """Replace what is drawn."""

        self._scene = scene
        self._marker_size = 0.012 * scene.characteristic_size()
        self.canvas.clear(keep_canvas=True)
        self._draw(scene)
        if reset_view:
            self.canvas.fit_to_scene()
        self._apply_highlight()
        self.canvas.redraw()

    def clear(self) -> None:
        self._scene = None
        self.canvas.clear(keep_canvas=True)
        self.canvas.redraw()

    def fit(self) -> None:
        self.canvas.fit_to_scene()
        self.canvas.redraw()

    # ------------------------------------------------------------------
    def _draw(self, scene: Scene) -> None:
        point3d = self._point3d

        for patch in scene.faces:
            if not patch.polygons:
                continue
            self.canvas.add_faces(
                [polygon.tolist() for polygon in patch.polygons],
                colors=patch.colors,
                outline=patch.outline,
                tags=patch.tag,
            )

        for line in scene.lines:
            points = np.asarray(line.points, dtype=float)
            for start, end in zip(points, points[1:]):
                self.canvas.add_line(
                    point3d(*start),
                    point3d(*end),
                    color=line.color,
                    width=line.width,
                    tags=line.tag,
                )

        for marker in scene.points:
            self._draw_point(marker)

        for sphere in scene.spheres:
            self.canvas.add_sphere(
                sphere.radius,
                center=point3d(*sphere.centre),
                color=sphere.color,
                opacity=sphere.opacity,
            )

        for arrow in scene.arrows:
            self.canvas.add_arrow(
                point3d(*arrow.start), point3d(*arrow.end), color=arrow.color
            )

        if scene.legend:
            self.canvas.set_thickness_legend(
                scene.legend.get("levels", []),
                unit=str(scene.legend.get("unit", "")),
                title=str(scene.legend.get("title", "")),
            )
        else:
            self.canvas.clear_thickness_legend()

    def _draw_point(self, marker: PointMarker) -> None:
        """Points are drawn as small boxes so they can be picked and shaded."""

        size = marker.size or self._marker_size
        if size <= 0.0:
            size = 0.01
        self.canvas.add_box(
            size,
            size,
            size,
            center=self._point3d(*marker.position),
            color=marker.color,
            tags=marker.tag,
        )

    # ------------------------------------------------------------------
    def set_pick_handler(
        self, handler: Optional[Callable[[Optional[object]], None]]
    ) -> None:
        """Called after a pick has updated the selection."""

        self._on_pick = handler

    def _handle_pick(self, pick) -> None:
        ref = self.selection.handle_tag(pick.tag, extend=pick.shift)
        if self._on_pick is not None:
            self._on_pick(ref)

    def _apply_highlight(self) -> None:
        self.canvas.set_highlight(self.selection.tags())

    # ------------------------------------------------------------------
    def selectable_tags(self) -> List[str]:
        return [] if self._scene is None else self._scene.tags()

    def set_view(self, name: str) -> None:
        actions = {
            "iso": self.canvas.set_iso_view,
            "top": self.canvas.set_top_view,
            "front": self.canvas.set_front_view,
            "side": self.canvas.set_side_view,
        }
        action = actions.get(name)
        if action is None:
            raise ValueError(f"unknown view {name!r}")
        action()
        self.canvas.redraw()
