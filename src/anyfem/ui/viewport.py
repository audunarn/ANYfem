"""The 3D viewport: draws a Scene and turns clicks back into entities.

This is the only module that touches ANYtk3D.  It imports it lazily so the rest
of ANYfem stays usable without the GUI extra installed.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional

import numpy as np

from ..geometry.construction import ConstructionTask
from ..geometry.snapping import GeometrySnapData, SnapEngine, SnapResult
from ..model.coordinates import CoordinateSystem
from ..model.workplanes import Workplane, WorkplaneFrame
from ..selection import (
    TAG_PREFIX,
    Selection,
    entity_tag,
    owner_to_ref,
    parse_entity_tag,
)
from .scene import PointMarker, Scene
from .visualization import VisualizationStyle

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


def _commercial_selection_api() -> Optional[dict[str, Any]]:
    """Return the rich ANYtk3D selection types when the installed canvas has them.

    ANYtk3D is an optional dependency and older compatible installations only
    expose tag-based picking.  Keeping this import lazy preserves that useful
    degradation path for scripts and for downstream embedders.
    """

    try:
        import anytk3d

        names = (
            "PickBinding",
            "PickOwner",
            "SelectionConfig",
            "SelectionDepth",
            "SelectionFilter",
            "SelectionGesture",
            "SelectionOperation",
            "SelectionTool",
        )
        return {name: getattr(anytk3d, name) for name in names}
    except (ImportError, AttributeError):  # pragma: no cover - install dependent
        return None


class Viewport:
    """A 3D canvas that knows how to draw a Scene and report picks."""

    def __init__(
        self,
        master,
        selection: Optional[Selection] = None,
        width: int = 900,
        height: int = 640,
        background: str = "#fbfbfd",
        commercial_interaction: bool = True,
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
        self._on_hover: Optional[Callable[[Optional[object]], None]] = None
        self._on_frame_selection: Optional[Callable[[List[object]], Any]] = None
        self._hovered: Optional[object] = None
        self._hover_key: Optional[str] = None
        self._marker_size = 0.0
        self._visualization = VisualizationStyle(background=background)
        self._selection_api = _commercial_selection_api()
        self._selection_tool = "box"
        self._selection_depth = "visible"
        self._selection_operation = "replace"
        self._canvas_filter_kinds: frozenset[str] = frozenset()
        self._construction_task: ConstructionTask | None = None
        self._construction_workplane: Workplane | None = None
        self._construction_coordinate_systems: Mapping[
            str, CoordinateSystem
        ] | None = None
        self._construction_snap_engine: SnapEngine | None = None
        self._construction_snap_data: (
            GeometrySnapData | Callable[[], GeometrySnapData] | None
        ) = None
        self._on_construction_update: Optional[
            Callable[[ConstructionTask, SnapResult | None], None]
        ] = None
        self._on_construction_apply: Optional[Callable[[], Any]] = None
        self._construction_grid_extent: tuple[float, float, float, float] | None = None
        self._construction_length_formatter: Callable[[float], str] | None = None
        self._commercial_selection = bool(
            commercial_interaction
            and self._selection_api is not None
            and hasattr(self.canvas, "set_interaction_profile")
            and hasattr(self.canvas, "configure_selection")
            and hasattr(self.canvas, "update_selection_config")
        )

        if self._commercial_selection:
            self.canvas.set_interaction_profile("commercial")
            config = self._selection_config()
            self.canvas.configure_selection(
                self._handle_selection_event,
                hover_callback=self._handle_hover,
                config=config,
            )
            self._canvas_filter_kinds = config.filter.kinds
        else:
            # The original tag callback remains the compatibility contract for
            # older ANYtk3D versions and for callers opting out of CAD controls.
            self.canvas.set_pick_callback(self._handle_pick, prefix=TAG_PREFIX)
        inner_canvas = getattr(self.canvas, "canvas", None)
        if inner_canvas is not None and hasattr(inner_canvas, "bind"):
            inner_canvas.bind(
                "<Escape>", self._handle_construction_escape, add="+"
            )
            inner_canvas.bind(
                "<Return>", self._handle_construction_enter, add="+"
            )
            inner_canvas.bind(
                "<KP_Enter>", self._handle_construction_enter, add="+"
            )
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
        self._clear_hover_state()
        self._draw(scene)
        self._draw_construction_overlay()
        if reset_view:
            self.canvas.fit_to_scene()
        self._apply_highlight()
        self.canvas.redraw()

    def clear(self) -> None:
        self._scene = None
        self.canvas.clear(keep_canvas=True)
        self._clear_hover_state()
        self.canvas.redraw()

    def fit(self) -> None:
        self.canvas.fit_to_scene()
        self.canvas.redraw()

    @property
    def visualization(self) -> VisualizationStyle:
        return self._visualization

    def set_visualization(self, style: VisualizationStyle) -> None:
        """Apply one validated appearance to every viewport scene."""

        if not isinstance(style, VisualizationStyle):
            raise TypeError("style must be a VisualizationStyle")
        widget = getattr(self.canvas, "canvas", self.canvas)
        colour_check = getattr(widget, "winfo_rgb", None)
        if callable(colour_check):
            for label, colour in (
                ("background", style.background),
                ("edge", style.edge_color),
            ):
                try:
                    colour_check(colour)
                except Exception as error:
                    raise ValueError(f"invalid {label} colour {colour!r}") from error
        self._visualization = style
        setter = getattr(self.canvas, "set_background", None)
        if callable(setter):
            setter(style.background)
        elif hasattr(widget, "configure"):
            widget.configure(background=style.background)
        if getattr(self, "_scene", None) is not None:
            self.show(self._scene)

    # ------------------------------------------------------------------
    # workplane projection and click construction
    # ------------------------------------------------------------------
    def project_to_workplane(
        self,
        x: float,
        y: float,
        workplane: Workplane,
        coordinate_systems: Mapping[str, CoordinateSystem],
    ) -> tuple[float, float, float] | None:
        """Project one viewport pixel onto a named structural workplane.

        ANYtk3D owns the camera ray and ray/plane intersection.  ANYfem only
        resolves its session workplane into a world-space origin and normal.
        ``None`` is returned for a parallel ray or an intersection behind the
        camera, matching the lower-level canvas contract.
        """

        frame = workplane.resolve(coordinate_systems)
        unproject = getattr(self.canvas, "unproject_to_plane", None)
        if not callable(unproject):
            raise RuntimeError(
                "workplane construction needs an ANYtk3D release with "
                "unproject_to_plane()"
            )
        point = unproject(
            float(x),
            float(y),
            tuple(float(item) for item in frame.origin),
            tuple(float(item) for item in frame.normal),
        )
        if point is None:
            return None
        values = np.asarray(tuple(point), dtype=float)
        if values.shape != (3,) or not np.all(np.isfinite(values)):
            raise RuntimeError("ANYtk3D returned an invalid workplane point")
        # Remove tiny ray-intersection drift along the normal before snapping.
        return tuple(float(item) for item in frame.project(values))

    @property
    def construction_active(self) -> bool:
        return self._construction_task is not None

    @property
    def construction_task(self) -> ConstructionTask | None:
        return self._construction_task

    def begin_construction(
        self,
        task: ConstructionTask,
        workplane: Workplane,
        coordinate_systems: Mapping[str, CoordinateSystem],
        *,
        snap_engine: SnapEngine | None = None,
        snap_data: GeometrySnapData | Callable[[], GeometrySnapData] | None = None,
        update_handler: Optional[
            Callable[[ConstructionTask, SnapResult | None], None]
        ] = None,
        apply_handler: Optional[Callable[[], Any]] = None,
        grid_extent: tuple[float, float, float, float] | None = None,
        length_formatter: Callable[[float], str] | None = None,
    ) -> None:
        """Route subsequent LMB click events into a working-copy task.

        No geometry is mutated here.  The Details task later calls
        :meth:`ConstructionTask.apply` with the application's command runner.
        """

        if not isinstance(task, ConstructionTask):
            raise TypeError("task must be a ConstructionTask")
        # Resolve now so a missing/deleted coordinate system fails before the
        # pointer enters a mode that appears active.
        workplane.resolve(coordinate_systems)
        self._construction_task = task
        self._construction_workplane = workplane
        self._construction_coordinate_systems = coordinate_systems
        self._construction_snap_engine = snap_engine or SnapEngine()
        self._construction_snap_data = snap_data
        self._on_construction_update = update_handler
        self._on_construction_apply = apply_handler
        self._construction_grid_extent = grid_extent
        self._construction_length_formatter = length_formatter
        if getattr(self, "_scene", None) is not None:
            self.show(self._scene)

    def _draw_construction_overlay(self) -> None:
        """Draw a bounded face/workplane grid and the uncommitted sketch."""

        task = self._construction_task
        workplane = self._construction_workplane
        systems = self._construction_coordinate_systems
        if task is None or workplane is None or systems is None:
            return
        frame = workplane.resolve(systems)
        extent = self._construction_grid_extent or (-5.0, 5.0, -5.0, 5.0)
        u0, u1, v0, v1 = (float(item) for item in extent)
        spacing = float(workplane.grid_spacing)
        # At most 101 grid lines per direction; an overly fine entry must not
        # freeze the Tk renderer.
        def grid_values(lower: float, upper: float) -> np.ndarray:
            first = int(np.floor(lower / spacing))
            last = int(np.ceil(upper / spacing))
            count = max(last - first + 1, 0)
            if count <= 101:
                indices = np.arange(first, last + 1, dtype=float)
            else:
                indices = np.linspace(first, last, 101)
            return indices * spacing

        u_values = grid_values(u0, u1)
        v_values = grid_values(v0, v1)
        point3d = self._point3d
        for value in u_values:
            start = frame.world_position((value, v0))
            end = frame.world_position((value, v1))
            self.canvas.add_line(
                point3d(*start), point3d(*end), color="#c8d7e6", width=1,
                layer=40, draw_overlay=True,
            )
        for value in v_values:
            start = frame.world_position((u0, value))
            end = frame.world_position((u1, value))
            self.canvas.add_line(
                point3d(*start), point3d(*end), color="#c8d7e6", width=1,
                layer=40, draw_overlay=True,
            )
        for start, end in task.preview_segments:
            self.canvas.add_line(
                point3d(*start), point3d(*end), color="#1565c0", width=3,
                layer=45, draw_overlay=True,
            )
        point_keys = list(getattr(task, "point_keys", ()))
        point_index = {key: index for index, key in enumerate(point_keys)}
        for constraint in getattr(task, "constraints", ()):
            if getattr(constraint, "kind", "") != "distance":
                continue
            first = point_index.get(getattr(constraint, "first", ""))
            second = point_index.get(getattr(constraint, "second", ""))
            if first is None or second is None:
                continue
            midpoint = 0.5 * (
                np.asarray(task.points[first], dtype=float)
                + np.asarray(task.points[second], dtype=float)
            )
            formatter = getattr(self, "_construction_length_formatter", None)
            label = (
                formatter(float(constraint.value))
                if callable(formatter)
                else f"{float(constraint.value):.6g} m"
            )
            self.canvas.add_text(
                point3d(*midpoint),
                label,
                color="#0d47a1",
                layer=47,
                draw_overlay=True,
            )
        if task.points:
            if hasattr(self.canvas, "add_markers"):
                self.canvas.add_markers(
                    [point3d(*point) for point in task.points],
                    colors=["#ff8f00"] * len(task.points),
                    size=[8] * len(task.points),
                    layer=46,
                )
            else:
                for point in task.points:
                    self.canvas.add_sphere(
                        max(self._marker_size, spacing * 0.015),
                        center=point3d(*point), color="#ff8f00",
                    )

    def construction_click(self, x: float, y: float) -> SnapResult | None:
        """Feed one screen click into the active task and return its snap."""

        task = self._construction_task
        workplane = self._construction_workplane
        systems = self._construction_coordinate_systems
        engine = self._construction_snap_engine
        if task is None or workplane is None or systems is None or engine is None:
            return None
        if not task.accepts_more:
            return None
        point = self.project_to_workplane(x, y, workplane, systems)
        if point is None:
            return None
        frame: WorkplaneFrame = workplane.resolve(systems)
        source = self._construction_snap_data
        data = source() if callable(source) else source
        result = engine.snap(point, workplane, frame, data)
        task.add(result)
        if getattr(self, "_scene", None) is not None:
            self.show(self._scene)
        if self._on_construction_update is not None:
            self._on_construction_update(task, result)
        return result

    def refresh_construction_overlay(self) -> None:
        """Redraw a changed working-copy task without mutating the model."""

        if getattr(self, "_scene", None) is not None and self._construction_task is not None:
            self.show(self._scene)

    def finish_construction(self, execute: Callable[[Any], Any]) -> Any:
        """Apply one task atomically; leave it active if validation fails."""

        if self._construction_task is None:
            raise RuntimeError("no construction task is active")
        result = self._construction_task.apply(execute)
        self.end_construction()
        return result

    def end_construction(self) -> None:
        self._construction_task = None
        self._construction_workplane = None
        self._construction_coordinate_systems = None
        self._construction_snap_engine = None
        self._construction_snap_data = None
        self._on_construction_update = None
        self._on_construction_apply = None
        self._construction_grid_extent = None
        self._construction_length_formatter = None
        if getattr(self, "_scene", None) is not None:
            self.show(self._scene)

    def cancel_construction(self) -> bool:
        """Cancel the working preview.  The live model remains untouched."""

        task = self._construction_task
        if task is None:
            return False
        handler = self._on_construction_update
        task.cancel()
        if handler is not None:
            handler(task, None)
        self.end_construction()
        return True

    def _handle_construction_escape(self, _event=None) -> None:
        self.cancel_construction()

    def _handle_construction_enter(self, _event=None) -> None:
        task = self._construction_task
        handler = self._on_construction_apply
        if task is not None and task.ready and handler is not None:
            handler()

    # ------------------------------------------------------------------
    # capture and optional section-plane capabilities
    # ------------------------------------------------------------------
    @property
    def capture_available(self) -> bool:
        """Whether this desktop can turn the visible viewport into an image."""

        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            return False
        return any(
            callable(getattr(self.canvas, name, None))
            for name in ("capture_image", "snapshot_image", "to_image")
        ) or hasattr(self.canvas, "canvas")

    def capture_image(self):
        """Capture the current fully rendered viewport as a Pillow image.

        Newer ANYtk3D releases may provide a renderer-native capture method.
        The desktop fallback captures precisely the inner Tk canvas rectangle;
        it does not include surrounding dialogs, toolbars or window chrome.
        """

        try:
            from PIL import Image, ImageGrab
        except ImportError as error:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "viewport image export needs Pillow; install ANYfem[gui]"
            ) from error

        for name in ("capture_image", "snapshot_image", "to_image"):
            capture = getattr(self.canvas, name, None)
            if not callable(capture):
                continue
            result = capture()
            if isinstance(result, Image.Image):
                return result.copy()
            if isinstance(result, (bytes, bytearray, memoryview)):
                with Image.open(io.BytesIO(bytes(result))) as loaded:
                    return loaded.convert("RGBA")
            if isinstance(result, (str, Path)):
                with Image.open(result) as loaded:
                    return loaded.convert("RGBA")
            if result is not None and hasattr(result, "convert"):
                return result.convert("RGBA")
            raise RuntimeError(
                f"ANYtk3D {name}() returned no usable image"
            )

        widget = getattr(self.canvas, "canvas", self.canvas)
        try:
            widget.update_idletasks()
            x0 = int(widget.winfo_rootx())
            y0 = int(widget.winfo_rooty())
            width = int(widget.winfo_width())
            height = int(widget.winfo_height())
        except (AttributeError, TypeError, ValueError) as error:
            raise RuntimeError(
                "this ANYtk3D canvas does not expose image capture"
            ) from error
        if width <= 1 or height <= 1:
            raise RuntimeError("the viewport must be visible before it can be captured")
        try:
            return ImageGrab.grab(bbox=(x0, y0, x0 + width, y0 + height)).convert(
                "RGBA"
            )
        except (OSError, RuntimeError) as error:
            raise RuntimeError(
                "the operating system could not capture the visible viewport"
            ) from error

    def capture_png(self, path: str | Path) -> Path:
        """Save the visible viewport to a PNG file."""

        from .result_export import save_png

        return save_png(self.capture_image(), path)

    @property
    def supports_section_planes(self) -> bool:
        """True only when the installed ANYtk3D exposes clipping controls."""

        return any(
            callable(getattr(self.canvas, name, None))
            for name in ("set_section_plane", "set_clip_plane", "configure_clipping")
        )

    def set_section_plane(
        self,
        normal=(1.0, 0.0, 0.0),
        offset: float = 0.0,
        *,
        enabled: bool = True,
    ) -> None:
        """Forward one world-space clipping plane to capable ANYtk3D builds."""

        vector = np.asarray(normal, dtype=float).reshape(-1)
        if vector.shape != (3,) or not np.isfinite(vector).all():
            raise ValueError("section-plane normal must contain three finite values")
        length = float(np.linalg.norm(vector))
        if length <= 0.0:
            raise ValueError("section-plane normal cannot be zero")
        distance = float(offset)
        if not np.isfinite(distance):
            raise ValueError("section-plane offset must be finite")
        unit_normal = tuple(float(value) for value in vector / length)

        method = next(
            (
                getattr(self.canvas, name)
                for name in ("set_section_plane", "set_clip_plane", "configure_clipping")
                if callable(getattr(self.canvas, name, None))
            ),
            None,
        )
        if method is None:
            raise RuntimeError(
                "section planes need a newer ANYtk3D clipping-capable release"
            )
        method(normal=unit_normal, offset=distance, enabled=bool(enabled))
        self.canvas.redraw()

    def clear_section_plane(self) -> None:
        """Disable clipping while preserving the current scene."""

        for name in ("clear_section_plane", "clear_clip_plane", "clear_clipping"):
            method = getattr(self.canvas, name, None)
            if callable(method):
                method()
                self.canvas.redraw()
                return
        self.set_section_plane(enabled=False)

    # ------------------------------------------------------------------
    def _draw(self, scene: Scene) -> None:
        point3d = self._point3d

        for patch in scene.faces:
            if not patch.polygons:
                continue
            style = self._visualization
            outline = patch.outline
            opacity = style.surface_opacity
            lit = True
            if style.render_mode == "Shaded":
                outline = ""
            elif style.render_mode == "Shaded with edges":
                outline = style.edge_color
            else:
                outline = style.edge_color
                opacity = 0.0
                lit = False
            options: dict[str, Any] = {
                "colors": patch.colors,
                "outline": outline,
                "width": style.edge_width,
                "opacity": opacity,
                "lit": lit,
                "tags": patch.tag,
            }
            if patch.polygon_owners is not None:
                # A single retained mesh batch keeps one binding per element
                # polygon.  Each binding may simultaneously expose its parent
                # geometry plate, FE element and element face.
                options["bindings"] = [
                    self._pick_binding(
                        patch.owners_for_polygon(index), patch.tag
                    )
                    for index in range(len(patch.polygons))
                ]
            else:
                binding = self._pick_binding(
                    patch.owners_for_polygon(0), patch.tag
                )
                if binding is not None:
                    # One geometry plate owns every tessellation polygon in
                    # this batch.  ANYtk3D broadcasts a scalar binding.
                    options["bindings"] = binding
            self.canvas.add_faces(
                [polygon.tolist() for polygon in patch.polygons],
                **options,
            )

        for line in scene.lines:
            points = np.asarray(line.points, dtype=float)
            binding = self._pick_binding(line.pick_owners, line.tag)
            for start, end in zip(points, points[1:]):
                options = {
                    "color": line.color,
                    "width": line.width,
                    "tags": line.tag,
                }
                if binding is not None:
                    options["binding"] = binding
                self.canvas.add_line(
                    point3d(*start),
                    point3d(*end),
                    **options,
                )

        if (
            self._commercial_selection
            and scene.points
            and hasattr(self.canvas, "add_markers")
        ):
            # Geometry points are one retained batch but keep per-marker
            # owners.  Screen-space markers remain usable at every zoom level.
            self.canvas.add_markers(
                [point3d(*marker.position) for marker in scene.points],
                colors=[marker.color for marker in scene.points],
                size=[7 for _marker in scene.points],
                bindings=[
                    self._pick_binding(marker.pick_owners, marker.tag)
                    for marker in scene.points
                ],
            )
        else:
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

        if scene.legend and self._visualization.show_legend:
            self.canvas.set_thickness_legend(
                scene.legend.get("levels", []),
                unit=str(scene.legend.get("unit", "")),
                title=str(scene.legend.get("title", "")),
                colors=scene.legend.get("colors"),
            )
        else:
            self.canvas.clear_thickness_legend()

    def _draw_point(self, marker: PointMarker) -> None:
        """Points are drawn as small boxes so they can be picked and shaded."""

        size = marker.size or self._marker_size
        if size <= 0.0:
            size = 0.01
        options: dict[str, Any] = {
            "center": self._point3d(*marker.position),
            "color": marker.color,
            "tags": marker.tag,
        }
        binding = self._pick_binding(marker.pick_owners, marker.tag)
        if binding is not None:
            # add_box forwards this scalar binding to its batched faces.
            options["bindings"] = binding
        self.canvas.add_box(size, size, size, **options)

    def _pick_binding(self, refs: object, tag: str = ""):
        """Attach one or more stable semantic owners to a primitive."""

        if not self._commercial_selection:
            return None
        if refs is None:
            candidates: List[object] = []
        elif isinstance(refs, (tuple, list, set, frozenset)):
            candidates = [ref for ref in refs if ref is not None]
        else:
            candidates = [refs]

        if not candidates and tag:
            parsed = parse_entity_tag(tag)
            if parsed is not None:
                candidates.append(parsed)

        owners = []
        keys: set[str] = set()
        for ref in candidates:
            kind = str(getattr(ref, "kind", ""))
            if not kind:
                continue
            domain_value = getattr(ref, "domain", "geometry")
            domain = str(getattr(domain_value, "value", domain_value)).lower()
            if domain not in {"geometry", "mesh"}:
                domain = "geometry"
            try:
                key = entity_tag(ref)
            except (AttributeError, TypeError, ValueError):
                continue
            if not key or key in keys:
                continue
            keys.add(key)
            priority = {
                "vertex": 30,
                "node": 30,
                "edge": 20,
                "element_face": 15,
                "face": 10,
                "element": 10,
            }.get(kind, 0)
            owners.append(
                self._selection_api["PickOwner"](
                    key, f"{domain}.{kind}", priority
                )
            )
        if not owners:
            return None
        return self._selection_api["PickBinding"](tuple(owners))

    # ------------------------------------------------------------------
    def set_pick_handler(
        self, handler: Optional[Callable[[Optional[object]], None]]
    ) -> None:
        """Called after a pick has updated the selection."""

        self._on_pick = handler

    def set_hover_handler(
        self, handler: Optional[Callable[[Optional[object]], None]]
    ) -> None:
        """Receive the geometry entity currently prehighlighted by hover."""

        self._on_hover = handler

    @property
    def hovered(self) -> Optional[object]:
        """The geometry reference under the pointer, if any."""

        return self._hovered

    def _handle_pick(self, pick) -> None:
        if self.construction_active:
            self.construction_click(pick.x, pick.y)
            return
        ref = self.selection.handle_tag(pick.tag, extend=pick.shift)
        if self._on_pick is not None:
            self._on_pick(ref)

    @staticmethod
    def _enum_value(value: object) -> str:
        return str(getattr(value, "value", value)).strip().lower()

    def _event_refs(self, event) -> List[object]:
        refs: List[object] = []
        for hit in getattr(event, "hits", ()):
            ref = owner_to_ref(getattr(hit, "owner", hit))
            if ref is None:
                # Compatibility with SelectionHit-like fakes that expose only
                # the original canvas tag.
                ref = parse_entity_tag(str(getattr(hit, "key", "")))
            selection_filter = getattr(self.selection, "filter", None)
            accepted = (
                selection_filter.accepts(ref)
                if ref is not None and selection_filter is not None
                else ref is not None and ref.kind == self.selection.mode
            )
            if accepted and ref not in refs:
                refs.append(ref)
        return refs

    def _handle_selection_event(self, event) -> None:
        """Translate rich canvas hits into the existing Selection service."""

        gesture = self._enum_value(getattr(event, "gesture", "click"))
        if self.construction_active and gesture == "click":
            point = getattr(event, "end", (0, 0))
            self.construction_click(point[0], point[1])
            return
        if self._selection_tool == "single" and gesture != "click":
            return

        refs = self._event_refs(event)
        owners = [
            getattr(hit, "owner", hit) for hit in getattr(event, "hits", ())
        ]
        event_operation = self._enum_value(
            getattr(event, "operation", "replace")
        )
        # No modifier arrives as REPLACE.  In that case the operation chosen
        # on SelectionStrip is the base policy; Shift/Ctrl/Alt operations from
        # ANYtk3D deliberately override it for the current gesture.
        operation = (
            self._selection_operation
            if event_operation == "replace"
            else event_operation
        )
        if hasattr(self.selection, "apply_owners"):
            # The headless selection service owns filtering, rejections and
            # ordered-set semantics.  Passing owners (not just tags) keeps
            # qualified geometry and mesh kinds available to it.
            change = self.selection.apply_owners(owners, operation)
            refs = list(change.accepted)
        else:  # pragma: no cover - compatibility with ANYfem < 0.0.1
            current = self.selection.items
            if operation == "replace":
                updated = refs
            elif operation == "add":
                updated = current + [ref for ref in refs if ref not in current]
            elif operation == "remove":
                updated = [ref for ref in current if ref not in refs]
            elif operation == "toggle":
                updated = list(current)
                for ref in refs:
                    if ref in updated:
                        updated.remove(ref)
                    else:
                        updated.append(ref)
            else:
                raise ValueError(f"unknown selection operation {operation!r}")
            self.selection.restore(updated)
        if self._on_pick is not None:
            self._on_pick(refs[0] if len(refs) == 1 else None)

    def _handle_hover(self, hit) -> None:
        ref = None
        if hit is not None:
            candidate = owner_to_ref(getattr(hit, "owner", hit))
            if candidate is None:
                candidate = parse_entity_tag(str(getattr(hit, "key", "")))
            selection_filter = getattr(self.selection, "filter", None)
            accepted = (
                selection_filter.accepts(candidate)
                if candidate is not None and selection_filter is not None
                else candidate is not None and candidate.kind == self.selection.mode
            )
            if accepted:
                ref = candidate
        self._hovered = ref
        self._hover_key = (
            None if ref is None else str(getattr(hit, "key", "")) or None
        )
        if hasattr(self.canvas, "set_preselection"):
            self.canvas.set_preselection(self._hover_key)
        if self._on_hover is not None:
            self._on_hover(ref)

    def _clear_hover_state(self) -> None:
        had_hover = self._hovered is not None or self._hover_key is not None
        self._hovered = None
        self._hover_key = None
        if had_hover and self._on_hover is not None:
            self._on_hover(None)

    def _selection_config(self):
        api = self._selection_api
        tool_enum = api["SelectionTool"]
        if self._selection_tool == "lasso":
            tool = tool_enum.LASSO
        elif self._selection_tool == "single":
            # SINGLE is present in the commercial-selection ANYtk3D floor.
            # The fallback keeps source compatibility with an earlier 0.2.x
            # build while ANYfem's own event guard still rejects region picks.
            tool = getattr(tool_enum, "SINGLE", tool_enum.BOX)
        else:
            tool = tool_enum.BOX
        depth = api["SelectionDepth"](self._selection_depth)
        model_filter = getattr(self.selection, "filter", None)
        qualified_kinds = getattr(model_filter, "qualified_kinds", None)
        if qualified_kinds is None:
            qualified_kinds = frozenset({f"geometry.{self.selection.mode}"})
        selection_filter = api["SelectionFilter"](kinds=qualified_kinds)
        options = {
            "filter": selection_filter,
            "depth": depth,
            "tool": tool,
        }
        if "click_on_press" in getattr(
            api["SelectionConfig"], "__dataclass_fields__", {}
        ):
            # Commit the front hit on button press.  This matches desktop CAD
            # interaction and avoids depending on a ButtonRelease that Tk can
            # lose on Windows after focus/menu transitions.  Box/lasso still
            # complete their region on release; their final operation is
            # idempotent except for toggle, which ANYtk3D de-duplicates.
            options["click_on_press"] = True
        return api["SelectionConfig"](**options)

    def configure_selection(
        self,
        *,
        tool: str = "box",
        depth: str = "visible",
        operation: str = "replace",
    ):
        """Apply SelectionStrip policy to the commercial canvas controls.

        ``single`` retains click selection but suppresses completed region
        gestures.  Box uses ANYtk3D's left-to-right window and right-to-left
        crossing semantics; lasso uses the same modifier operations.
        """

        tool = str(tool).strip().lower()
        depth = str(depth).strip().lower()
        operation = str(operation).strip().lower()
        if tool not in {"single", "box", "lasso"}:
            raise ValueError("selection tool must be 'single', 'box', or 'lasso'")
        if depth not in {"visible", "through"}:
            raise ValueError("selection depth must be 'visible' or 'through'")
        if operation not in {"replace", "add", "remove", "toggle"}:
            raise ValueError(
                "selection operation must be replace, add, remove, or toggle"
            )

        self._selection_tool = tool
        self._selection_depth = depth
        self._selection_operation = operation
        if not self._commercial_selection:
            return None
        config = self._selection_config()
        updated = self.canvas.update_selection_config(
            filter=config.filter,
            depth=config.depth,
            tool=config.tool,
        )
        self._canvas_filter_kinds = config.filter.kinds
        # ANYtk3D clears preselection while replacing query policy.  The
        # current owner is still valid when only tool/depth/operation changed.
        if self._hover_key is not None and hasattr(
            self.canvas, "set_preselection"
        ):
            self.canvas.set_preselection(self._hover_key)
        return updated

    def set_frame_selection_handler(
        self, handler: Optional[Callable[[List[object]], Any]]
    ) -> None:
        """Install an application-specific selected-entity framing hook."""

        self._on_frame_selection = handler

    def frame_selection(self) -> None:
        """Frame selected entities, falling back to fitting the whole scene."""

        if self._on_frame_selection is not None:
            handled = self._on_frame_selection(self.selection.items)
            if handled is not False:
                self.canvas.redraw()
                return
        self.fit()

    def _apply_highlight(self) -> None:
        self.canvas.set_highlight(self.selection.tags())
        if self._commercial_selection:
            # The filter follows geometry Point/Line/Plate mode immediately.
            config = self._selection_config()
            if config.filter.kinds != self._canvas_filter_kinds:
                self.canvas.update_selection_config(filter=config.filter)
                self._canvas_filter_kinds = config.filter.kinds
                self._clear_hover_state()

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
