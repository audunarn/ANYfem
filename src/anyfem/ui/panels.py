"""The stage panels: Geometry, Mesh, Loads & BC, Solve, Results.

Every panel acts through the command stack, never directly on the project, so
everything the user does here is undoable and scriptable by the same calls.
"""

from __future__ import annotations

from dataclasses import replace
from queue import Empty, Queue
from threading import Thread
import tkinter as tk
from tkinter import colorchooser, filedialog, ttk
from typing import Callable, List, Optional, Sequence

import numpy as np
from anymaterial import available_grades
from anygeometry.entities import EntityRef

from .. import commands as cmd
from ..geometry.construction import ConstructionMode, ConstructionTask
from ..geometry.snapping import geometry_snap_data
from anymesher.decomposition import check_mappable
from ..mesh.mapped import ELEMENT_ORDERS
from ..mesh.refinement import refine_around
from ..mesh.seeding import SeedingConflict
from ..model.attributes import (
    DOF_NAMES,
    LineLoad,
    Mass,
    PointLoad,
    Pressure,
    Support,
    SurfaceTraction,
)
from ..model.imperfections import Imperfection
from ..model.materials import Material, dnv_steel_material
from ..model.sections import PROFILES, BeamSection, PlateSection
from ..model.project import ProjectError
from ..model.workplanes import Workplane
from ..post.extract import along_line, envelope, probe
from ..post.fields import available_fields
from ..post.history import history_series
from ..post.report import (
    field_to_csv,
    result_report_context,
    write_csv,
    write_report,
    write_result_report,
)
from ..selection import SELECTION_MODES, mode_label
from .plot import HistoryPlot
from .live_progress import GRAPH_CHOICES, LiveProgressData
from .result_display import (
    DISPLAY_UNIT_SYSTEMS,
    ENGINEERING_DISPLAY,
    converted_series,
    unit_transform,
)
from .result_export import lazy_field_to_csv, save_gif
from .result_summary import (
    nonlinear_path_summary,
    prescribed_path_progress,
    submitted_target_load_factor,
)
from .visualization import RENDER_MODES, VisualizationStyle
from .scene import (
    COLOR_LOAD,
    COLOR_MASS,
    COLOR_MOMENT,
    COLOR_PRESSURE,
    COLOR_ROTATION,
    COLOR_SUPPORT,
    RESULT_COLORMAPS,
)

__all__ = [
    "GeometryPanel",
    "LoadPanel",
    "MeshPanel",
    "ResultsPanel",
    "SectionPanel",
    "SolvePanel",
    "StagePanel",
]


class StagePanel(ttk.Frame):
    """Base class: a titled panel with small form helpers."""

    title = "Stage"

    def __init__(self, master: tk.Misc, app) -> None:
        super().__init__(master, padding=8)
        self.app = app
        self.build()

    # -- construction ---------------------------------------------------
    def build(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def refresh(self) -> None:
        """Called when the model or selection changes."""

    # -- form helpers ---------------------------------------------------
    def section(
        self, text: str, parent: tk.Misc | None = None
    ) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(self if parent is None else parent, text=text, padding=6)
        frame.pack(fill="x", pady=(0, 8))
        return frame

    @staticmethod
    def labelled_entry(
        parent: tk.Misc,
        label: str,
        default: str = "",
        width: int = 10,
        *,
        label_width: int = 16,
    ) -> tuple[ttk.Frame, tk.StringVar]:
        """An entry row, returning its frame so it can be shown or hidden."""

        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=label, width=label_width).pack(side="left")
        variable = tk.StringVar(value=default)
        ttk.Entry(row, textvariable=variable, width=width).pack(
            side="left", fill="x", expand=True
        )
        return row, variable

    @classmethod
    def entry_row(
        cls, parent: tk.Misc, label: str, default: str = "", width: int = 10
    ) -> tk.StringVar:
        return cls.labelled_entry(parent, label, default, width)[1]

    @staticmethod
    def vector_row(
        parent: tk.Misc, label: str, default: Sequence[str] = ("0", "0", "0")
    ) -> List[tk.StringVar]:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=label, width=16).pack(side="left")
        variables = []
        for value in default:
            variable = tk.StringVar(value=value)
            ttk.Entry(row, textvariable=variable, width=7).pack(
                side="left", padx=1
            )
            variables.append(variable)
        return variables

    def button(
        self, parent: tk.Misc, text: str, action: Callable[[], None]
    ) -> ttk.Button:
        widget = ttk.Button(parent, text=text, command=self.guarded(action))
        widget.pack(fill="x", pady=2)
        return widget

    def guarded(self, action: Callable[[], None]) -> Callable[[], None]:
        """Run an action, turning any modelling error into a status message."""

        def wrapped() -> None:
            try:
                action()
            except (
                ValueError,
                KeyError,
                ProjectError,
                SeedingConflict,
            ) as error:
                # GeometryError and SeedingConflict both derive from
                # ValueError, so a modelling refusal reaches the status bar
                # rather than a traceback.
                self.app.set_status(str(error), error=True)

        return wrapped

    # -- parsing --------------------------------------------------------
    @staticmethod
    def number(variable: tk.StringVar, label: str) -> float:
        text = variable.get().strip()
        try:
            return float(text)
        except ValueError:
            raise ValueError(f"{label} must be a number, got {text!r}") from None

    def vector(self, variables: Sequence[tk.StringVar], label: str) -> np.ndarray:
        return np.array(
            [self.number(variable, label) for variable in variables], dtype=float
        )

    # -- selection ------------------------------------------------------
    def require_selection(self, kind: str, count: Optional[int] = None) -> List[EntityRef]:
        selection = self.app.selection
        if selection.mode != kind:
            raise ValueError(
                f"switch to {mode_label(kind)} mode first "
                f"(currently {mode_label(selection.mode)})"
            )
        items = selection.items
        if not items:
            raise ValueError(f"select at least one {mode_label(kind).lower()} first")
        if count is not None and len(items) != count:
            raise ValueError(
                f"select exactly {count} {mode_label(kind).lower()}s "
                f"({len(items)} selected)"
            )
        return items


# ----------------------------------------------------------------------
class GeometryPanel(StagePanel):
    title = "Geometry"

    def build(self) -> None:
        modes = self.section("Select")
        row = ttk.Frame(modes)
        row.pack(fill="x")
        self._mode = tk.StringVar(value=self.app.selection.mode)
        for mode in SELECTION_MODES:
            ttk.Radiobutton(
                row,
                text=mode_label(mode),
                value=mode,
                variable=self._mode,
                command=self._change_mode,
            ).pack(side="left", expand=True)

        point = self.section("Point")
        self._point = self.vector_row(point, "x, y, z")
        self.button(point, "Add point", self._add_point)

        construction = self.section("Workplane construction")
        row = ttk.Frame(construction)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="coordinates", width=16).pack(side="left")
        self._workplane_coordinates = tk.StringVar(value="Global")
        self._workplane_coordinates_box = ttk.Combobox(
            row,
            textvariable=self._workplane_coordinates,
            state="readonly",
            width=16,
        )
        self._workplane_coordinates_box.pack(side="left", fill="x", expand=True)
        self._workplane_offset = self.entry_row(construction, "plane offset", "0 m")
        self._workplane_grid = self.entry_row(construction, "grid spacing", "1 m")
        self._workplane_tolerance = self.entry_row(
            construction, "snap tolerance", "50 mm"
        )
        snaps = ttk.Frame(construction)
        snaps.pack(fill="x", pady=1)
        self._snap_grid = tk.BooleanVar(value=True)
        self._snap_object = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            snaps, text="Grid/axes", variable=self._snap_grid
        ).pack(side="left")
        ttk.Checkbutton(
            snaps, text="End/mid/intersection", variable=self._snap_object
        ).pack(side="left", padx=(8, 0))
        row = ttk.Frame(construction)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="construct", width=16).pack(side="left")
        self._construction_mode = tk.StringVar(value="Polyline")
        ttk.Combobox(
            row,
            textvariable=self._construction_mode,
            values=("Point", "Line", "Polyline"),
            state="readonly",
            width=12,
        ).pack(side="left", fill="x", expand=True)
        self._construction_close = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            row, text="Close", variable=self._construction_close
        ).pack(side="left", padx=(6, 0))
        self.button(construction, "Start click construction", self._start_construction)
        actions = ttk.Frame(construction)
        actions.pack(fill="x", pady=(2, 0))
        ttk.Button(
            actions, text="Apply / Enter", command=self.guarded(self._apply_construction)
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            actions, text="Cancel / Esc", command=self._cancel_construction
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Label(
            construction,
            text="LMB adds snapped points; numeric x/y/z entry above remains available.",
            foreground="#666666",
            wraplength=270,
        ).pack(anchor="w", pady=(3, 0))

        line = self.section("Line")
        self.button(line, "Line through 2 points", self._add_line)
        self.button(line, "Arc through 3 points", self._add_arc)
        ttk.Label(
            line,
            text="Arc order: start, via, end",
            foreground="#666666",
        ).pack(anchor="w")

        plate = self.section("Plate")
        self.button(plate, "Plate from selected lines", self._add_face)
        self.button(plate, "Plate from selected points", self._add_plate)

        extrude = self.section("Sweep")
        self._extrude = self.vector_row(extrude, "extrude vector", ("0", "0", "1"))
        self.button(extrude, "Extrude selected lines", self._extrude_lines)
        self._axis_point = self.vector_row(extrude, "axis point")
        self._axis_direction = self.vector_row(
            extrude, "axis direction", ("0", "0", "1")
        )
        self._angle = self.entry_row(extrude, "angle [deg]", "360")
        self.button(extrude, "Revolve selected lines", self._revolve_lines)

        self._advanced_open = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self,
            text="Advanced modeling tools",
            variable=self._advanced_open,
            command=self._show_advanced,
        ).pack(anchor="w", pady=(0, 6))
        self._advanced = ttk.Frame(self)

        duplicate = self.section("Copy, mirror and pattern", self._advanced)
        self._copy_offset = self.vector_row(duplicate, "copy offset", ("1", "0", "0"))
        self.button(duplicate, "Copy selected", self._copy_selected)
        self._mirror_point = self.vector_row(duplicate, "plane point")
        self._mirror_normal = self.vector_row(
            duplicate, "plane normal", ("1", "0", "0")
        )
        self.button(duplicate, "Mirror selected", self._mirror_selected)
        self._linear_direction = self.vector_row(
            duplicate, "linear direction", ("1", "0", "0")
        )
        self._linear_spacing = self.entry_row(duplicate, "spacing [m]", "1")
        self._linear_count = self.entry_row(duplicate, "additional copies", "2")
        self.button(duplicate, "Linear pattern", self._linear_pattern)
        self._pattern_axis_point = self.vector_row(duplicate, "axis point")
        self._pattern_axis_direction = self.vector_row(
            duplicate, "axis direction", ("0", "0", "1")
        )
        self._pattern_angle = self.entry_row(duplicate, "angle step [deg]", "90")
        self._pattern_count = self.entry_row(duplicate, "additional copies", "3")
        self.button(duplicate, "Circular pattern", self._circular_pattern)

        generators = self.section("ANYgeometry structural generators", self._advanced)
        row = ttk.Frame(generators)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="type", width=16).pack(side="left")
        self._generator_type = tk.StringVar(value="Stiffened panel")
        ttk.Combobox(
            row,
            textvariable=self._generator_type,
            values=("Stiffened panel", "Cylinder", "Cone"),
            state="readonly",
            width=18,
        ).pack(side="left", fill="x", expand=True)
        self._generator_length = self.entry_row(generators, "length / height [m]", "4")
        self._generator_width = self.entry_row(generators, "panel width [m]", "2")
        self._generator_radius_start = self.entry_row(generators, "radius start [m]", "1")
        self._generator_radius_end = self.entry_row(generators, "radius end [m]", "0.5")
        self._generator_longitudinal = self.entry_row(
            generators, "longitudinal spacing", "0.5"
        )
        self._generator_transverse = self.entry_row(
            generators, "transverse / ring", "1"
        )
        self._generator_segments = self.entry_row(generators, "circumferential", "12")
        self._generator_group = self.entry_row(generators, "semantic group", "shell")
        self._generator_origin = self.vector_row(generators, "origin")
        self._generator_axis = self.vector_row(
            generators, "axis", ("0", "0", "1")
        )
        self.button(generators, "Create generator feature", self._add_generator)
        self._show_advanced()

        divide = self.section("Divide")
        self._fraction = self.entry_row(divide, "fraction", "0.5")
        self.button(divide, "Split selected lines", self._split_lines)
        row = ttk.Frame(divide)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="direction", width=16).pack(side="left")
        self._axis = tk.StringVar(value="0")
        for value, text in (("0", "sides 0-2"), ("1", "sides 1-3")):
            ttk.Radiobutton(
                row, text=text, value=value, variable=self._axis
            ).pack(side="left", expand=True)
        self.button(divide, "Split selected plates", self._split_plates)
        self._strips = self.entry_row(divide, "strips", "3")
        self.button(divide, "Strip selected plates", self._strip_plates)
        self.button(divide, "Three-sided region to plates", self._triangle)

        trim = self.section("Modeling: neutral trim hole")
        self._trim_centre = self.vector_row(trim, "centre")
        self._trim_radius = self.entry_row(trim, "radius [m]", "0.25")
        self.button(trim, "Create trimmed face hole", self._trim_hole)
        ttk.Label(
            trim,
            text="Keeps one structural face; creates an inner boundary.",
            foreground="#666666",
            wraplength=270,
        ).pack(anchor="w")

        hole = self.section("Mesh: butterfly decomposition")
        self._hole_centre = self.vector_row(hole, "centre")
        self._hole_radius = self.entry_row(hole, "radius [m]", "0.25")
        self.button(hole, "Create butterfly patches", self._punch)

        edit = self.section("Edit")
        self._corners = self.entry_row(edit, "corners", "0 1 2 3")
        self.button(edit, "Set plate corners", self._set_corners)
        self.button(edit, "Reverse line / plate orientation", self._reverse)
        row = ttk.Frame(edit)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="measurement", width=16).pack(side="left")
        self._measurement = tk.StringVar(value="Auto")
        ttk.Combobox(
            row,
            textvariable=self._measurement,
            values=(
                "Auto", "Position", "Coordinates", "Distance", "Angle",
                "Radius", "Length", "Area", "Perimeter", "Centroid", "Normal",
            ),
            state="readonly",
            width=14,
        ).pack(side="left", fill="x", expand=True)
        self.button(edit, "Measure selection", self._measure)
        self.button(edit, "Check selected plates", self._check)
        self.button(edit, "Delete selection", self._delete)

    def refresh(self) -> None:
        self._mode.set(self.app.selection.mode)
        systems = sorted(
            self.app.project.coordinate_systems.values(),
            key=lambda item: (item.id != "global", item.name.lower(), item.id),
        )
        values = tuple(item.name for item in systems)
        self._workplane_coordinates_box.configure(values=values)
        if self._workplane_coordinates.get() not in values and values:
            self._workplane_coordinates.set(values[0])

    def _change_mode(self) -> None:
        if self.app.viewport.cancel_construction():
            self.app.set_status(
                "click construction cancelled; geometry selection is active"
            )
        self.app.selection.set_mode(self._mode.get())

    def _show_advanced(self) -> None:
        if self._advanced_open.get():
            self._advanced.pack(fill="x", pady=(0, 8))
        else:
            self._advanced.pack_forget()

    def _geometry_selection(self) -> List[EntityRef]:
        mode = self.app.selection.mode
        if mode not in SELECTION_MODES:
            raise ValueError(
                "this modeling tool needs geometry selection; switch the "
                "selection domain from Mesh to Geometry"
            )
        return self.require_selection(mode)

    @staticmethod
    def _whole(value: float, label: str, *, minimum: int = 1) -> int:
        integer = int(value)
        if float(integer) != float(value) or integer < minimum:
            raise ValueError(f"{label} must be a whole number at least {minimum}")
        return integer

    def _optional_number(self, variable: tk.StringVar, label: str) -> float | None:
        return None if not variable.get().strip() else self.number(variable, label)

    def _copy_selection(self, result, originals: Sequence[EntityRef]) -> None:
        made = [result.entity_map[item] for item in originals if item in result.entity_map]
        if made:
            self.app.selection.restore(made)

    def _copy_selected(self) -> None:
        items = self._geometry_selection()
        result = self.app.run(
            cmd.CopyEntities(items, translation=self.vector(self._copy_offset, "copy offset"))
        )
        self._copy_selection(result, items)
        self.app.set_status(f"copied {len(items)} selected entity(ies)")

    def _mirror_selected(self) -> None:
        items = self._geometry_selection()
        result = self.app.run(
            cmd.MirrorEntities(
                items,
                plane_point=self.vector(self._mirror_point, "mirror plane point"),
                plane_normal=self.vector(self._mirror_normal, "mirror plane normal"),
            )
        )
        self._copy_selection(result, items)
        self.app.set_status(f"mirrored {len(items)} selected entity(ies)")

    def _linear_pattern(self) -> None:
        items = self._geometry_selection()
        count = self._whole(
            self.number(self._linear_count, "additional copies"),
            "additional copies",
        )
        result = self.app.run(
            cmd.LinearPattern(
                items,
                direction=self.vector(self._linear_direction, "pattern direction"),
                spacing=self.number(self._linear_spacing, "pattern spacing"),
                count=count,
            )
        )
        made = [
            instance.entity_map[item]
            for instance in result.instances
            for item in items
            if item in instance.entity_map
        ]
        if made:
            self.app.selection.restore(made)
        self.app.set_status(f"created {count} linear pattern instance(s)")

    def _circular_pattern(self) -> None:
        items = self._geometry_selection()
        count = self._whole(
            self.number(self._pattern_count, "additional copies"),
            "additional copies",
        )
        result = self.app.run(
            cmd.CircularPattern(
                items,
                axis_point=self.vector(self._pattern_axis_point, "pattern axis point"),
                axis_direction=self.vector(
                    self._pattern_axis_direction, "pattern axis direction"
                ),
                angle_step=float(
                    np.deg2rad(self.number(self._pattern_angle, "pattern angle step"))
                ),
                count=count,
            )
        )
        made = [
            instance.entity_map[item]
            for instance in result.instances
            for item in items
            if item in instance.entity_map
        ]
        if made:
            self.app.selection.restore(made)
        self.app.set_status(f"created {count} circular pattern instance(s)")

    def _add_generator(self) -> None:
        kind = self._generator_type.get()
        length = self.number(self._generator_length, "length / height")
        origin = self.vector(self._generator_origin, "generator origin")
        spacing = self._optional_number(
            self._generator_longitudinal, "longitudinal spacing"
        )
        transverse = self._optional_number(
            self._generator_transverse, "transverse / ring spacing"
        )
        if kind == "Stiffened panel":
            if spacing is None:
                raise ValueError("a stiffened panel needs longitudinal spacing")
            command = cmd.AddStiffenedPanel(
                length,
                self.number(self._generator_width, "panel width"),
                spacing,
                transverse,
                origin=origin,
                semantic_group=self._generator_group.get().strip() or "shell",
            )
        else:
            segments = self._whole(
                self.number(self._generator_segments, "circumferential segments"),
                "circumferential segments",
                minimum=3,
            )
            axis = self.vector(self._generator_axis, "generator axis")
            shared = dict(
                height=length,
                circumferential_segments=segments,
                longitudinal_spacing=spacing,
                ring_spacing=transverse,
                origin=origin,
                axis=axis,
            )
            if kind == "Cylinder":
                command = cmd.AddCylinder(
                    self.number(self._generator_radius_start, "radius"), **shared
                )
            elif kind == "Cone":
                command = cmd.AddCone(
                    self.number(self._generator_radius_start, "start radius"),
                    self.number(self._generator_radius_end, "end radius"),
                    **shared,
                )
            else:  # defensive against a scripted variable value
                raise ValueError(f"unknown structural generator {kind!r}")
        feature = self.app.run(command)
        made = len(feature.outputs)
        self.app.set_status(
            f"created {feature.name} as editable feature {feature.feature_id} "
            f"({made} semantic outputs)"
        )

    def _add_point(self) -> None:
        x, y, z = self.vector(self._point, "point coordinate")
        vertex = self.app.run(cmd.AddPoint(float(x), float(y), float(z)))
        self.app.set_status(f"added point {vertex}")

    def _selected_coordinate_system_id(self) -> str:
        name = self._workplane_coordinates.get().strip()
        matches = [
            item.id
            for item in self.app.project.coordinate_systems.values()
            if item.name == name
        ]
        if len(matches) != 1:
            raise ValueError(f"coordinate system {name!r} is unavailable")
        return matches[0]

    def _workplane(self) -> Workplane:
        units = self.app.project.units
        objects = bool(self._snap_object.get())
        return Workplane(
            coordinate_system_id=self._selected_coordinate_system_id(),
            offset=units.parse(self._workplane_offset.get(), "length"),
            grid_spacing=units.parse(self._workplane_grid.get(), "length"),
            snap_tolerance=units.parse(self._workplane_tolerance.get(), "length"),
            snap_grid=bool(self._snap_grid.get()),
            snap_axes=bool(self._snap_grid.get()),
            snap_endpoints=objects,
            snap_midpoints=objects,
            snap_intersections=objects,
        )

    def _start_construction(self) -> None:
        close = bool(self._construction_close.get())
        mode = ConstructionMode(self._construction_mode.get().strip().lower())
        task = ConstructionTask(mode, close=close)
        workplane = self._workplane()
        if self.app.viewport.construction_active:
            self.app.viewport.cancel_construction()
        self.app.session.active_workplane = workplane
        self.app.viewport.begin_construction(
            task,
            workplane,
            self.app.project.coordinate_systems,
            snap_data=lambda: geometry_snap_data(self.app.project.geometry),
            update_handler=self._construction_updated,
            apply_handler=self.guarded(self._apply_construction),
        )
        self.app.set_status(
            f"{mode.value} construction active: click in the viewport; "
            "Enter applies and Escape cancels"
        )

    def _construction_updated(self, task: ConstructionTask, snap) -> None:
        if task.cancelled:
            self.app.set_status("construction cancelled; model unchanged")
            return
        kind = "free"
        if snap is not None and snap.kind is not None:
            kind = snap.kind.value
        ready = "; ready to Apply" if task.ready else ""
        self.app.set_status(
            f"construction point {len(task.points)} ({kind} snap){ready}"
        )

    def _apply_construction(self) -> None:
        task = self.app.viewport.construction_task
        if task is None:
            raise ValueError("start click construction first")
        result = self.app.viewport.finish_construction(self.app.run)
        made = tuple(result.vertices) + tuple(result.edges)
        if made:
            # Prefer newly made lines when the active geometry filter allows
            # them; otherwise keep the new points selected.
            mode = "edge" if result.edges else "vertex"
            self.app.selection.set_mode(mode)
            self.app.selection.restore(result.edges or result.vertices)
        self.app.set_status(
            f"created {len(result.vertices)} point(s) and {len(result.edges)} line(s)"
        )

    def _cancel_construction(self) -> None:
        if not self.app.viewport.cancel_construction():
            self.app.set_status("no construction task is active")

    def _add_line(self) -> None:
        points = self.require_selection("vertex", 2)
        edge = self.app.run(cmd.AddLine(points[0].id, points[1].id))
        self.app.set_status(f"added line {edge}")

    def _add_arc(self) -> None:
        points = self.require_selection("vertex", 3)
        edge = self.app.run(
            cmd.AddArc(points[0].id, points[1].id, points[2].id)
        )
        self.app.set_status(f"added arc {edge}")

    def _add_face(self) -> None:
        edges = self.require_selection("edge")
        face = self.app.run(cmd.AddFace([ref.id for ref in edges]))
        self.app.set_status(f"added plate {face}")

    def _add_plate(self) -> None:
        points = self.require_selection("vertex")
        if len(points) < 4:
            raise ValueError("a plate needs at least four points")
        face = self.app.run(cmd.AddPlate([ref.id for ref in points]))
        self.app.set_status(f"added plate {face}")

    def _extrude_lines(self) -> None:
        edges = self.require_selection("edge")
        vector = self.vector(self._extrude, "extrusion vector")
        faces = self.app.run(cmd.Extrude([ref.id for ref in edges], vector))
        self.app.set_status(f"extruded into {len(faces)} plate(s)")

    def _revolve_lines(self) -> None:
        edges = self.require_selection("edge")
        angle = np.deg2rad(self.number(self._angle, "angle"))
        faces = self.app.run(
            cmd.Revolve(
                edge_ids=[ref.id for ref in edges],
                axis_point=self.vector(self._axis_point, "axis point"),
                axis_direction=self.vector(self._axis_direction, "axis direction"),
                angle=float(angle),
            )
        )
        self.app.set_status(f"revolved into {len(faces)} plate(s)")

    def _split_lines(self) -> None:
        edges = self.require_selection("edge")
        fraction = self.number(self._fraction, "fraction")
        self.app.run_many(cmd.SplitEdge(ref.id, fraction) for ref in edges)
        self.app.set_status(f"split {len(edges)} line(s)")

    def _split_plates(self) -> None:
        faces = self.require_selection("face")
        axis = int(self._axis.get())
        fraction = self.number(self._fraction, "fraction")
        self.app.run_many(
            cmd.SplitFace(ref.id, axis, fraction) for ref in faces
        )
        self.app.set_status(f"split {len(faces)} plate(s)")

    def _strip_plates(self) -> None:
        faces = self.require_selection("face")
        axis = int(self._axis.get())
        count = int(self.number(self._strips, "strips"))
        results = self.app.run_many(
            cmd.StripFace(ref.id, axis, count) for ref in faces
        )
        made = sum(len(strips) for strips, _dividers in results)
        self.app.set_status(f"made {made} strip(s)")

    def _triangle(self) -> None:
        edges = self.require_selection("edge", 3)
        faces = self.app.run(
            cmd.TriangleToQuads(edge_ids=[ref.id for ref in edges])
        )
        self.app.selection.clear()
        self.app.set_status(f"three-sided region became {len(faces)} plates")

    def _trim_hole(self) -> None:
        faces = self.require_selection("face", 1)
        centre = self.vector(self._trim_centre, "trim-hole centre")
        radius = self.number(self._trim_radius, "trim-hole radius")
        _face, arcs = self.app.run(
            cmd.NeutralTrimHole(faces[0].id, tuple(centre), radius)
        )
        self.app.set_status(
            f"created neutral trim hole with {len(arcs)} boundary arc(s); "
            "the structural plate was not decomposed"
        )

    def _punch(self) -> None:
        faces = self.require_selection("face", 1)
        centre = self.vector(self._hole_centre, "hole centre")
        radius = self.number(self._hole_radius, "hole radius")
        patches, _arcs = self.app.run(
            cmd.ButterflyHoleDecomposition(faces[0].id, tuple(centre), radius)
        )
        self.app.set_status(
            f"mesh-decomposition hole punched into {len(patches)} "
            "butterfly patches (not a neutral trim)"
        )

    def _set_corners(self) -> None:
        faces = self.require_selection("face", 1)
        try:
            corners = [int(part) for part in self._corners.get().split()]
        except ValueError:
            raise ValueError(
                "corners must be four whole numbers, e.g. 0 1 2 3"
            ) from None
        self.app.run(cmd.SetFaceCorners(faces[0].id, corners))
        self.app.set_status(f"corners set on plate {faces[0].id}")

    def _reverse(self) -> None:
        if self.app.selection.mode not in ("edge", "face"):
            raise ValueError("select one or more lines or plates to reverse")
        items = self.require_selection(self.app.selection.mode)
        self.app.run_many(cmd.ReverseEntity(item) for item in items)
        noun = "normal" if items[0].kind == "face" else "direction"
        self.app.set_status(f"reversed {noun} on {len(items)} entity(ies)")

    def _measure(self) -> None:
        items = self._geometry_selection()
        quantity = self._measurement.get().strip().lower()
        result = self.app.commands.query(cmd.MeasureGeometry(items, quantity))
        value = result.value
        if isinstance(value, tuple):
            rendered = "(" + ", ".join(f"{item:.7g}" for item in value) + ")"
        else:
            rendered = f"{value:.7g}"
        unit = "" if result.unit == "1" else f" {result.unit}"
        if result.kind == "angle":
            rendered += f" ({np.rad2deg(float(value)):.7g} deg)"
        self.app.set_status(f"{result.kind}: {rendered}{unit}")

    def _check(self) -> None:
        faces = self.require_selection("face")
        reports = [
            check_mappable(self.app.project.geometry, ref.id) for ref in faces
        ]
        bad = [report for report in reports if not report.ok]
        if not bad:
            self.app.set_status(f"{len(reports)} plate(s): all mappable")
        else:
            self.app.set_status(str(bad[0]), error=True)

    def _delete(self) -> None:
        items = self.require_selection(self.app.selection.mode)
        self.app.selection.clear()
        self.app.run_many(cmd.DeleteEntity(ref) for ref in items)
        self.app.set_status(f"deleted {len(items)} entity(ies)")


# ----------------------------------------------------------------------
class MeshPanel(StagePanel):
    title = "Mesh"

    def build(self) -> None:
        controls = self.section("Mapped mesh")
        self._size = self.entry_row(controls, "element size [m]", "0.25")
        row = ttk.Frame(controls)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="element order", width=16).pack(side="left")
        self._order = tk.StringVar(value="linear")
        ttk.Combobox(
            row,
            textvariable=self._order,
            values=list(ELEMENT_ORDERS),
            state="readonly",
            width=12,
        ).pack(side="left", fill="x", expand=True)
        self._generate_button = self.button(controls, "Generate mesh", self._generate)
        self._cancel_button = self.button(controls, "Cancel mesh", self._cancel)
        self.button(controls, "Open ANYmesher...", self._open_mesher)

        seeding = self.section("Seeding")
        self._divisions = self.entry_row(seeding, "divisions", "4")
        self.button(seeding, "Pin selected lines", self._pin)
        self.button(seeding, "Clear pins", self._clear_pins)
        self._pin_label = ttk.Label(seeding, text="no pinned lines", foreground="#666666")
        self._pin_label.pack(anchor="w")

        refine = self.section("Local refinement")
        self._refine_size = self.entry_row(refine, "element size [m]", "0.05")
        self._refine_radius = self.entry_row(refine, "radius [m]", "0.1")
        self.button(refine, "Refine around selection", self._refine)
        self.button(refine, "Clear refinements", self._clear_refinements)
        self._refine_label = ttk.Label(
            refine, text="no refinement zones", foreground="#666666",
            justify="left", wraplength=220,
        )
        self._refine_label.pack(anchor="w")

        self._stats = ttk.Label(self, text="no mesh", justify="left")
        self._stats.pack(anchor="w")

    def refresh(self) -> None:
        pins = self.app.seeding_overrides
        self._pin_label.configure(
            text="no pinned lines"
            if not pins
            else "pinned: "
            + ", ".join(f"{edge}→{count}" for edge, count in sorted(pins.items()))
        )
        if self._order.get() != self.app.project.element_order:
            self._order.set(self.app.project.element_order)

        zones = self.app.project.refinements
        self._refine_label.configure(
            text="no refinement zones"
            if not zones
            else "\n".join(
                f"{zone.size:g} m within {zone.radius:g} m of "
                f"{zone.ref if zone.ref is not None else 'a point'}"
                for zone in zones
            )
        )

        busy = bool(getattr(self.app, "mesh_job_running", False))
        self._generate_button.configure(state="disabled" if busy else "normal")
        self._cancel_button.configure(state="normal" if busy else "disabled")

        record_id = (
            getattr(self.app, "_active_mesh_task_id", None)
            or getattr(self.app, "_mesh_details_record_id", None)
            or getattr(self.app, "mesh_record_id", None)
        )
        record = self.app.project.mesh_records.get(record_id or "")
        if record is None and self.app.project.mesh_records:
            record = next(reversed(self.app.project.mesh_records.values()))
        mesh = self.app.mesh
        lines: list[str] = []
        if record is not None:
            state = self.app.mesh_record_state(record)
            lines.append(f"{record.name}: {state}")
            quality = record.summary.get("quality", {})
            if quality:
                lines.extend(
                    (
                        f"max aspect ratio {float(quality.get('max_aspect_ratio', 0.0)):.4g}",
                        f"mean aspect ratio {float(quality.get('mean_aspect_ratio', 0.0)):.4g}",
                        f"max warp {float(quality.get('max_warp', 0.0)):.4g}",
                    )
                )
                warnings = quality.get("warnings", ())
                lines.extend(f"warning: {warning}" for warning in warnings)
            if record.diagnostics:
                diagnostic = record.diagnostics[-1]
                message = (
                    diagnostic.get("message", str(diagnostic))
                    if isinstance(diagnostic, dict)
                    else str(diagnostic)
                )
                lines.append(f"error: {message}")
        if mesh is not None:
            beams = "3-node" if mesh.is_quadratic else "2-node"
            lines.extend(
                (
                    f"{mesh.num_nodes} nodes",
                    f"{len(mesh.shells)} shell elements",
                    f"{len(mesh.beams)} {beams} beam elements",
                )
            )
        self._stats.configure(text="\n".join(lines) if lines else "no mesh")

    def _generate(self) -> None:
        size = self.number(self._size, "element size")
        if size <= 0:
            raise ValueError("element size must be positive")
        if self._order.get() != self.app.project.element_order:
            self.app.run(cmd.SetElementOrder(order=self._order.get()))
        self.app.generate_mesh_async(size)

    def _cancel(self) -> None:
        self.app.cancel_mesh()

    def _open_mesher(self) -> None:
        """Open ANYmesher as a standalone neutral-mesh tool.

        Its parametric primitives are not associated with this project's BRep,
        so presenting its generic mesh as analysis-ready here would leave
        sections, loads and supports referring to unrelated entity IDs.
        """

        from anymesher.gui import open_mesher

        open_mesher(self.winfo_toplevel())
        self.app.set_status(
            "opened standalone ANYmesher; save its neutral mesh from that window"
        )

    def _refine(self) -> None:
        """Refine around whatever is selected, whatever kind it is."""

        selection = list(self.app.selection)
        if not selection:
            raise ValueError(
                "select a point, line or plate to refine around first"
            )
        size = self.number(self._refine_size, "element size")
        radius = self.number(self._refine_radius, "radius")
        self.app.run_many(
            cmd.AddRefinement(refinement=refine_around(ref, size, radius))
            for ref in selection
        )
        self.app.set_status(
            f"refining to {size:g} m around {len(selection)} entity(ies); "
            "generate the mesh to apply it"
        )
        self.app.refresh_panels()

    def _clear_refinements(self) -> None:
        self.app.project.refinements.clear()
        self.app.set_status("cleared refinement zones")
        self.app.refresh_panels()

    def _pin(self) -> None:
        edges = self.require_selection("edge")
        count = int(self.number(self._divisions, "divisions"))
        if count < 1:
            raise ValueError("divisions must be at least 1")
        for ref in edges:
            self.app.seeding_overrides[ref.id] = count
        self.app.set_status(f"pinned {len(edges)} line(s) to {count} divisions")
        self.app.refresh_panels()

    def _clear_pins(self) -> None:
        self.app.seeding_overrides.clear()
        self.app.set_status("cleared seeding pins")
        self.app.refresh_panels()


# ----------------------------------------------------------------------
class SectionPanel(StagePanel):
    title = "Sections"

    def build(self) -> None:
        self._editing_imperfection = None
        material = self.section("Material definition")
        self._material_name = self.entry_row(material, "material name", "auto")
        grade_row = ttk.Frame(material)
        grade_row.pack(fill="x", pady=1)
        ttk.Label(grade_row, text="DNV RP-C208 grade", width=16).pack(side="left")
        self._grade = tk.StringVar(value="S355")
        self._grade_box = ttk.Combobox(
            grade_row,
            textvariable=self._grade,
            values=tuple(available_grades()),
            state="readonly",
            width=12,
        )
        self._grade_box.pack(side="left", fill="x", expand=True)
        self._grade_thickness = self.entry_row(material, "thickness [mm]", "10")
        self.button(material, "Add DNV steel", self._add_material)
        ttk.Label(
            material,
            text=(
                "Name is an editable project label (for example S355_NL). "
                "Grade selects the DNV properties and is kept separate."
            ),
            foreground="#666666",
            wraplength=430,
            justify="left",
        ).pack(anchor="w", pady=(1, 3))
        self.button(material, "Open ANYmaterial...", self._open_material_editor)
        library_row = ttk.Frame(material)
        library_row.pack(fill="x", pady=(4, 1))
        ttk.Label(library_row, text="inspect material", width=16).pack(side="left")
        self._material_choice = tk.StringVar(value="S355")
        self._material_choice_box = ttk.Combobox(
            library_row,
            textvariable=self._material_choice,
            values=(),
            state="readonly",
            width=24,
        )
        self._material_choice_box.pack(side="left", fill="x", expand=True)
        self._material_choice_box.bind(
            "<<ComboboxSelected>>", lambda _event: self._refresh_material_details()
        )
        self._material_details = ttk.Label(
            material, text="", justify="left", foreground="#333333"
        )
        self._material_details.pack(anchor="w", fill="x", pady=(2, 4))

        plate = self.section("Plate section")
        self._plate_name = self.entry_row(plate, "name", "plate")
        self._plate_thickness = self.entry_row(plate, "thickness [mm]", "10")
        self._plate_material, self._plate_material_box = self._material_row(plate)
        self._auto_dnv_plate = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            plate,
            text="Auto DNV nonlinear material from thickness",
            variable=self._auto_dnv_plate,
            command=self._update_plate_material_mode,
        ).pack(anchor="w", pady=(2, 0))
        ttk.Label(
            plate,
            text=(
                "Uses the DNV grade above and this plate thickness; an automatic "
                "thickness-qualified material name is generated and reused."
            ),
            foreground="#666666",
            wraplength=430,
            justify="left",
        ).pack(anchor="w", pady=(0, 2))
        self.button(plate, "Create / update section", self._add_plate_section)
        self.button(plate, "Assign to selected plates", self._assign_plate)

        beam = self.section("Beam section")
        self._beam_name = self.entry_row(beam, "name", "stiffener")
        row = ttk.Frame(beam)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="profile", width=16).pack(side="left")
        self._profile = tk.StringVar(value="T-bar")
        ttk.Combobox(
            row, textvariable=self._profile, values=list(PROFILES),
            state="readonly", width=12,
        ).pack(side="left", fill="x", expand=True)
        self._beam_dims = self.vector_row(beam, "hw, tw, b [mm]", ("200", "10", "100"))
        self._beam_flange = self.entry_row(beam, "tf [mm]", "12")
        self._beam_material, self._beam_material_box = self._material_row(beam)
        self.button(beam, "Add section", self._add_beam_section)
        self.button(beam, "Assign to selected lines", self._assign_beam)

        usage = self.section("Section definitions and assigned model entities")
        self._section_usage = ttk.Treeview(
            usage,
            columns=("kind", "material", "assigned"),
            show="tree headings",
            height=6,
        )
        self._section_usage.heading("#0", text="Definition")
        self._section_usage.heading("kind", text="Type")
        self._section_usage.heading("material", text="Material")
        self._section_usage.heading("assigned", text="Assigned model entities")
        self._section_usage.column("#0", width=100, stretch=True)
        self._section_usage.column("kind", width=55, stretch=False)
        self._section_usage.column("material", width=165, stretch=True)
        self._section_usage.column("assigned", width=190, stretch=True)
        self._section_usage.pack(fill="x")

        imperfection = self.section("Imperfection")
        self._imperfection_amplitude = self.entry_row(
            imperfection, "amplitude [mm]", "auto"
        )
        self._waves = self.entry_row(imperfection, "waves", "1 1")
        self._imperfection_apply = self.button(
            imperfection, "Add to selection", self._add_imperfection
        )
        ttk.Label(
            imperfection,
            text=(
                "Purple wire shows the stress-free imperfect shape. Small "
                "amplitudes are automatically exaggerated for visibility; "
                "the tree and solver retain the stated physical amplitude."
            ),
            foreground="#6a1b9a",
            wraplength=430,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))
        self._imperfection_label = ttk.Label(self, text="", foreground="#666666")
        self._imperfection_label.pack(anchor="w")
        self._update_plate_material_mode()

    def refresh(self) -> None:
        names = tuple(sorted(self.app.project.materials))
        self._material_choice_box.configure(values=names)
        if names and self._material_choice.get() not in names:
            self._material_choice.set(names[0])
        for variable, box in (
            (self._plate_material, self._plate_material_box),
            (self._beam_material, self._beam_material_box),
        ):
            box.configure(values=names)
            if names and variable.get() not in names:
                variable.set(names[0])
        count = len(self.app.project.imperfections)
        self._imperfection_label.configure(
            text="no imperfections" if not count else f"{count} imperfection(s)"
        )
        self._update_plate_material_mode()
        self._refresh_material_details()
        self._refresh_section_usage()

    def _refresh_material_details(self) -> None:
        material = self.app.project.materials.get(self._material_choice.get())
        if material is None:
            self._material_details.configure(text="No material selected")
            return
        lines = [f"{material.name}"]
        if material.symmetry == "isotropic":
            lines.append(
                f"Elastic: E = {material.elastic_modulus / 1e9:.6g} GPa, "
                f"nu = {material.poisson_ratio:.5g}"
            )
        else:
            lines.append(f"Elastic symmetry: {material.symmetry}")
        lines.append(
            f"Density = {material.density:.6g} kg/m3; "
            f"nominal yield = {material.yield_stress / 1e6:.6g} MPa"
        )
        hardening = material.hardening
        if hardening is None:
            lines.append("Response: ELASTIC ONLY (no plastic hardening curve)")
        else:
            kind = str(hardening.get("kind", "custom"))
            lines.append(f"Response: NONLINEAR PLASTICITY ACTIVE ({kind})")
            if kind == "dnv_c208":
                lines.append(
                    f"DNV grade {hardening.get('grade', '?')}; "
                    f"product thickness = "
                    f"{float(hardening.get('thickness', 0.0)) * 1000:.6g} mm"
                )
            try:
                curve = material.build().hardening_curve
                plastic_strain = np.array((0.0, 0.01, 0.10), dtype=float)
                flow = np.asarray(curve.flow_stress(plastic_strain), dtype=float) / 1e6
                lines.append(
                    "Flow stress [MPa] at plastic strain "
                    f"0/1%/10% = {flow[0]:.4g} / {flow[1]:.4g} / {flow[2]:.4g}"
                )
            except (AttributeError, TypeError, ValueError):
                lines.append("Hardening curve is stored and validated by ANYmaterial")
        self._material_details.configure(text="\n".join(lines))

    @staticmethod
    def _assigned_label(prefix: str, identifiers: Sequence[int]) -> str:
        ordered = sorted(int(value) for value in identifiers)
        shown = ", ".join(str(value) for value in ordered[:12])
        if len(ordered) > 12:
            shown += f", +{len(ordered) - 12} more"
        return "unassigned" if not ordered else f"{prefix} {shown}"

    def _refresh_section_usage(self) -> None:
        self.app.project.resolve_section_assignments(strict=False)
        self._section_usage.delete(*self._section_usage.get_children())
        faces_by_section: dict[str, list[int]] = {}
        for identifier, assigned in self.app.project.face_sections.items():
            faces_by_section.setdefault(assigned, []).append(identifier)
        edges_by_section: dict[str, list[int]] = {}
        for identifier, assigned in self.app.project.edge_sections.items():
            edges_by_section.setdefault(assigned, []).append(identifier)
        for name, section in sorted(self.app.project.plate_sections.items()):
            faces = faces_by_section.get(name, ())
            self._section_usage.insert(
                "", "end", iid=f"plate:{section.id}", text=name,
                values=("Plate", section.material, self._assigned_label("Plates", faces)),
            )
        for name, section in sorted(self.app.project.beam_sections.items()):
            edges = edges_by_section.get(name, ())
            self._section_usage.insert(
                "", "end", iid=f"beam:{section.id}", text=name,
                values=("Beam", section.material, self._assigned_label("Lines", edges)),
            )

    def _update_plate_material_mode(self) -> None:
        self._plate_material_box.configure(
            state="disabled" if self._auto_dnv_plate.get() else "readonly"
        )

    def _add_material(self) -> None:
        thickness = self.number(self._grade_thickness, "thickness") / 1000.0
        requested_name = self._material_name.get().strip()
        custom_name = None if requested_name.lower() in ("", "auto") else requested_name
        material = dnv_steel_material(
            self._grade.get().strip(),
            thickness,
            nonlinear=True,
            name=custom_name,
        )
        self.app.run(cmd.AddMaterial(material, label="add DNV material"))
        self._material_choice.set(material.name)
        self._plate_material.set(material.name)
        self._beam_material.set(material.name)
        self.app.set_status(
            f"added nonlinear DNV material {material.name}: "
            f"grade {material.hardening['grade']}, "
            f"product thickness {thickness * 1000:g} mm"
        )
        self.app.refresh_all()

    @staticmethod
    def _material_row(parent: tk.Misc) -> tuple[tk.StringVar, ttk.Combobox]:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="material", width=16).pack(side="left")
        variable = tk.StringVar(value="S355")
        box = ttk.Combobox(
            row, textvariable=variable, values=(), state="readonly", width=12
        )
        box.pack(side="left", fill="x", expand=True)
        return variable, box

    def _open_material_editor(self) -> None:
        from anymaterial.gui import open_material_editor

        selected = self._plate_material.get() or self._beam_material.get()
        initial = self.app.project.materials.get(selected)
        open_material_editor(
            self.winfo_toplevel(), initial_spec=initial, on_apply=self._use_material
        )

    def _use_material(self, material) -> None:
        """Add an editor result and select it for both new section types."""

        if not isinstance(material, Material):
            # Keep ANYfem's compatibility properties (elastic_modulus and
            # poisson_ratio) while storing the complete ANYmaterial schema.
            material = Material.from_dict(material.to_dict())
        self.app.run(cmd.AddMaterial(material, label="use ANYmaterial specification"))
        self._material_choice.set(material.name)
        self._auto_dnv_plate.set(False)
        self._update_plate_material_mode()
        self._plate_material.set(material.name)
        self._beam_material.set(material.name)
        self.app.set_status(f"using material {material.name}")
        self.app.refresh_all()

    def _plate_section_command(self) -> tuple[cmd.AddPlateSection, str]:
        name = self._plate_name.get().strip()
        thickness = self.number(self._plate_thickness, "thickness") / 1000.0
        automatic = self._auto_dnv_plate.get()
        material_name = self._plate_material.get().strip()
        material = None
        if automatic:
            material = dnv_steel_material(
                self._grade.get().strip(), thickness, nonlinear=True
            )
            material_name = material.name
        existing = self.app.project.plate_sections.get(name)
        section = PlateSection(
            name=name,
            thickness=thickness,
            material=material_name,
            **({"id": existing.id} if existing is not None else {}),
        )
        return cmd.AddPlateSection(section, material), material_name

    def _add_plate_section(self) -> None:
        command, material_name = self._plate_section_command()
        self.app.run(command)
        self._plate_material.set(material_name)
        self._material_choice.set(material_name)
        name = command.section.name
        detail = (
            "automatic DNV nonlinear"
            if command.material is not None
            else "selected"
        )
        self.app.set_status(
            f"added plate section {name} using {material_name} ({detail} material)"
        )
        self.app.refresh_all()

    def _assign_plate(self) -> None:
        faces = self.require_selection("face")
        name = self._plate_name.get().strip()
        definition, material_name = self._plate_section_command()
        self.app.run_many(
            (definition, *(cmd.AssignPlate(ref.id, name) for ref in faces))
        )
        self._plate_material.set(material_name)
        self._material_choice.set(material_name)
        self.app.set_status(
            f"updated {name} and assigned it to {len(faces)} plate(s); "
            f"material {material_name}"
        )

    def _add_beam_section(self) -> None:
        web_height, web_thickness, flange_width = (
            value / 1000.0 for value in self.vector(self._beam_dims, "beam dimension")
        )
        section = BeamSection(
            name=self._beam_name.get().strip(),
            profile=self._profile.get(),
            material=self._beam_material.get().strip(),
            web_height=web_height,
            web_thickness=web_thickness,
            flange_width=flange_width,
            flange_thickness=self.number(self._beam_flange, "tf") / 1000.0,
        )
        self.app.project.add_beam_section(section)
        self.app.set_status(f"added beam section {section.name}")
        self.app.refresh_all()

    def _assign_beam(self) -> None:
        edges = self.require_selection("edge")
        name = self._beam_name.get().strip()
        self.app.run_many(cmd.AssignBeam(ref.id, name) for ref in edges)
        self.app.set_status(f"assigned {name} to {len(edges)} line(s)")

    def _add_imperfection(self) -> None:
        text = self._imperfection_amplitude.get().strip().lower()
        amplitude = None if text in ("", "auto") else (
            self.number(self._imperfection_amplitude, "amplitude") / 1000.0
        )
        try:
            waves = tuple(int(part) for part in self._waves.get().split())
        except ValueError:
            raise ValueError("waves must be two whole numbers, e.g. 1 1") from None
        if len(waves) != 2:
            raise ValueError("waves must be two whole numbers, e.g. 1 1")

        if isinstance(self._editing_imperfection, Imperfection):
            current = self._editing_imperfection
            self.app.run(
                cmd.EditAttribute(
                    replace(current, amplitude=amplitude, waves=waves)
                )
            )
            self._editing_imperfection = None
            self._imperfection_apply.configure(text="Add to selection")
            self.app.set_status(f"updated imperfection {current.name}")
            return

        items = self.require_selection(self.app.selection.mode)

        self.app.run_many(
            cmd.AddImperfection(
                Imperfection(ref=ref, amplitude=amplitude, waves=waves)
            )
            for ref in items
        )
        self.app.set_status(f"imperfection on {len(items)} entity(ies)")

    def edit_imperfection(self, identifier: str) -> bool:
        for item in self.app.project.imperfections:
            if getattr(item, "id", None) != identifier:
                continue
            self._editing_imperfection = item
            self._imperfection_amplitude.set(
                "auto" if item.amplitude is None else f"{item.amplitude * 1000:.12g}"
            )
            self._waves.set(f"{item.waves[0]} {item.waves[1]}")
            self._imperfection_apply.configure(text="Update selected imperfection")
            self.app.show_geometry()
            self.app.selection.set_mode(item.ref.kind)
            self.app.selection.select(item.ref)
            self.app.refresh_views()
            return True
        return False


class LoadPanel(StagePanel):
    title = "Loads & BC"

    def build(self) -> None:
        self._editing_attribute = None
        self._editing_case_name: str | None = None
        scope = self.section("Scope on model")
        ttk.Label(
            scope,
            text="Select model geometry for loads and boundary conditions.",
            foreground="#666666",
        ).pack(anchor="w")
        scope_row = ttk.Frame(scope)
        scope_row.pack(fill="x", pady=(3, 0))
        for text, kind in (
            ("Points", "vertex"),
            ("Lines", "edge"),
            ("Plates", "face"),
        ):
            ttk.Button(
                scope_row,
                text=text,
                command=lambda value=kind: self._scope_geometry(value),
            ).pack(side="left", fill="x", expand=True, padx=1)

        key = self.section("Viewport key")
        for text, colour in (
            ("blue arrow  pressure", COLOR_PRESSURE),
            ("red arrow  force / prescribed translation", COLOR_LOAD),
            ("orange arrow  moment", COLOR_MOMENT),
            ("green axes  translational restraint", COLOR_SUPPORT),
            ("teal axes  rotational restraint", COLOR_ROTATION),
            ("purple marker / arrow  mass / acceleration", COLOR_MASS),
        ):
            ttk.Label(key, text=text, foreground=colour).pack(anchor="w")

        cases = self.section("Load case")
        row = ttk.Frame(cases)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="case", width=16).pack(side="left")
        self._case = tk.StringVar(value="default")
        self._case_box = ttk.Combobox(
            row, textvariable=self._case, values=["default"], width=12
        )
        self._case_box.pack(side="left", fill="x", expand=True)
        self._case_box.bind(
            "<<ComboboxSelected>>", lambda _event: self.app.refresh_views()
        )
        coordinate_row = ttk.Frame(cases)
        coordinate_row.pack(fill="x", pady=1)
        ttk.Label(coordinate_row, text="coordinates", width=16).pack(side="left")
        self._coordinates = tk.StringVar(value="Global")
        self._coordinate_box = ttk.Combobox(
            coordinate_row,
            textvariable=self._coordinates,
            values=("Global",),
            state="readonly",
            width=18,
        )
        self._coordinate_box.pack(side="left", fill="x", expand=True)
        self.button(cases, "New case", self._add_case)
        self.button(cases, "Delete case", self._delete_case)
        self._follower = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            cases,
            text="pressure follows the deformed shape",
            variable=self._follower,
            command=self.guarded(self._set_follower),
        ).pack(anchor="w")

        support = self.section("Support")
        preset_row = ttk.Frame(support)
        preset_row.pack(fill="x", pady=1)
        ttk.Label(preset_row, text="preset", width=16).pack(side="left")
        self._support_preset = tk.StringVar(value="Pinned")
        preset = ttk.Combobox(
            preset_row,
            textvariable=self._support_preset,
            values=(
                "Fixed", "Pinned", "Simply supported Z", "Prescribed",
                "X symmetry", "Y symmetry", "Z symmetry",
                "X antisymmetry", "Y antisymmetry", "Z antisymmetry",
            ),
            state="readonly",
            width=20,
        )
        preset.pack(side="left", fill="x", expand=True)
        preset.bind("<<ComboboxSelected>>", self._apply_support_preset)
        grid = ttk.Frame(support)
        grid.pack(fill="x")
        self._dofs = {}
        self._values = {}
        self._component_values = {}
        for index, dof in enumerate(DOF_NAMES):
            held = tk.BooleanVar(value=dof in ("ux", "uy", "uz"))
            value = tk.StringVar(value="0")
            ttk.Checkbutton(grid, text=dof, variable=held).grid(
                row=index, column=0, sticky="w", padx=2
            )
            ttk.Entry(grid, textvariable=value, width=12).grid(
                row=index, column=1, sticky="ew", padx=2, pady=1
            )
            ttk.Label(
                grid, text="mm" if dof.startswith("u") else "mrad"
            ).grid(row=index, column=2, sticky="w")
            self._dofs[dof] = held
            self._component_values[dof] = value
        grid.columnconfigure(1, weight=1)
        # Historical integrations set this one value.  Keep it as an explicit
        # convenience while the six component boxes above are authoritative.
        self._prescribed = self.entry_row(
            support, "set checked [mm/mrad]", "0"
        )
        self._prescribed.trace_add("write", self._broadcast_prescribed)
        self._support_apply = self.button(
            support, "Apply to selection", self._add_support
        )

        load = self.section("Load")
        self._pressure = self.entry_row(load, "pressure [Pa]", "10000")
        self._pressure_apply = self.button(
            load, "Pressure on plates", self._add_pressure
        )
        self._force = self.vector_row(load, "force [N]", ("0", "0", "-1000"))
        self._moment = self.vector_row(load, "moment [Nm]", ("0", "0", "0"))
        policy_row = ttk.Frame(load)
        policy_row.pack(fill="x", pady=1)
        ttk.Label(policy_row, text="multi-point policy", width=16).pack(side="left")
        self._point_policy = tk.StringVar(value="Per target")
        ttk.Combobox(
            policy_row,
            textvariable=self._point_policy,
            values=("Per target", "Total distributed"),
            state="readonly",
            width=18,
        ).pack(side="left", fill="x", expand=True)
        self._point_apply = self.button(
            load, "Point load on points", self._add_point_load
        )
        self._line = self.vector_row(load, "line load [N/m]", ("0", "0", "-1000"))
        self._line_apply = self.button(
            load, "Line load on lines", self._add_line_load
        )
        self._traction = self.vector_row(load, "traction [Pa]", ("0", "0", "-1000"))
        self._traction_apply = self.button(
            load, "Traction on plates", self._add_traction
        )

        body = self.section("Body load")
        self._acceleration = self.vector_row(
            body, "accel [m/s2]", ("0", "0", "-9.81")
        )
        self.button(body, "Apply to case", self._set_acceleration)
        self._mass = self.entry_row(body, "mass [kg]", "100")
        self._mass_apply = self.button(
            body, "Mass on selection", self._add_mass
        )

        combination = self.section("Combination")
        self._combination_name = self.entry_row(combination, "name", "ULS")
        self._factors = self.entry_row(combination, "factors", "default 1.0")
        ttk.Label(
            combination,
            text="factors: case factor, case factor, ...",
            foreground="#666666",
        ).pack(anchor="w")
        self.button(combination, "Define", self._add_combination)
        self._combination_label = ttk.Label(self, text="", justify="left")
        self._combination_label.pack(anchor="w")

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        names = sorted(self.app.project.load_cases) or ["default"]
        self._case_box.configure(values=names)
        if self._case.get() not in names:
            self._case.set(names[0])
        case = self.app.project.load_cases.get(self._case.get())
        if case is not None:
            self._follower.set(case.follower_pressure)
        coordinate_names = [
            item.name
            for item in sorted(
                self.app.project.coordinate_systems.values(),
                key=lambda item: (item.id != "global", item.name.lower()),
            )
        ]
        self._coordinate_box.configure(values=coordinate_names)
        if self._coordinates.get() not in coordinate_names:
            self._coordinates.set(coordinate_names[0] if coordinate_names else "Global")
        combinations = self.app.project.combinations
        self._combination_label.configure(
            text="no combinations"
            if not combinations
            else "\n".join(
                f"{name}: "
                + ", ".join(
                    f"{case_name} x {factor:g}"
                    for case_name, factor in sorted(item.factors.items())
                )
                for name, item in sorted(combinations.items())
            )
        )

    def case_name(self) -> str:
        return self._case.get().strip() or "default"

    def _scope_geometry(self, kind: str) -> None:
        labels = {"vertex": "points", "edge": "lines", "face": "plates"}
        self.app.show_geometry()
        self.app.selection_strip.set_context(
            kind,
            f"Geometry scope • select {labels[kind]}",
        )
        self.app.details.set_hint(f"Select model {labels[kind]}")
        self.app.set_status(
            f"model geometry shown; select {labels[kind]} for loads and BCs"
        )

    def coordinate_system_id(self) -> str:
        selected = self._coordinates.get()
        for identifier, system in self.app.project.coordinate_systems.items():
            if system.name == selected or identifier == selected:
                return identifier
        raise ValueError(f"coordinate system {selected!r} does not exist")

    # ------------------------------------------------------------------
    def _add_case(self) -> None:
        name = self._case.get().strip()
        if not name:
            raise ValueError("give the load case a name")
        self.app.run(cmd.AddLoadCase(name))
        self.app.set_status(f"load case {name}")
        self.app.refresh_all()

    def _delete_case(self) -> None:
        name = self.case_name()
        self.app.run(cmd.DeleteLoadCase(name))
        self.app.set_status(f"deleted load case {name}")
        self.app.refresh_all()

    def _set_follower(self) -> None:
        self.app.run(
            cmd.SetFollowerPressure(self._follower.get(), case=self.case_name())
        )
        self.app.set_status(
            "pressure follows the deformed shape"
            if self._follower.get()
            else "pressure acts on the reference shape"
        )

    @staticmethod
    def _set_vector(variables: Sequence[tk.StringVar], values: Sequence[float]) -> None:
        for variable, value in zip(variables, values):
            variable.set(f"{float(value):.12g}")

    def _set_coordinate_system(self, identifier: str) -> None:
        system = self.app.project.coordinate_systems.get(identifier)
        self._coordinates.set(system.name if system is not None else identifier)

    def _begin_attribute_edit(self, item, case_name: str | None = None) -> None:
        """Hydrate the form from the UUID-selected tree object."""

        self._editing_attribute = item
        self._editing_case_name = case_name
        if case_name is not None:
            self._case.set(case_name)
        if hasattr(item, "coordinate_system_id"):
            self._set_coordinate_system(item.coordinate_system_id)
        self.app.show_geometry()
        self.app.selection.set_mode(item.ref.kind)
        self.app.selection.select(item.ref)

        if isinstance(item, Support):
            self._support_preset.set("Prescribed")
            for dof in DOF_NAMES:
                active = dof in item.constraints
                self._dofs[dof].set(active)
                self._component_values[dof].set(
                    f"{1000.0 * float(item.constraints.get(dof, 0.0)):.12g}"
                )
            self._support_apply.configure(text="Update selected support")
        elif isinstance(item, PointLoad):
            self._set_vector(self._force, item.force)
            self._set_vector(self._moment, item.moment)
            self._point_policy.set(
                "Per target"
                if item.distribution_policy == "per_target"
                else "Total distributed"
            )
            self._point_apply.configure(text="Update selected point load")
        elif isinstance(item, Pressure):
            self._pressure.set(f"{float(item.value):.12g}")
            self._pressure_apply.configure(text="Update selected pressure")
        elif isinstance(item, LineLoad):
            self._set_vector(self._line, item.force_per_length)
            self._line_apply.configure(text="Update selected line load")
        elif isinstance(item, SurfaceTraction):
            self._set_vector(self._traction, item.traction)
            self._traction_apply.configure(text="Update selected traction")
        elif isinstance(item, Mass):
            self._mass.set(f"{float(item.value):.12g}")
            self._point_policy.set(
                "Per target"
                if item.distribution_policy == "per_target"
                else "Total distributed"
            )
            self._mass_apply.configure(text="Update selected mass")
        self.app.refresh_views()

    def edit_tree_item(self, key: str) -> bool:
        """Open the exact support/load/mass selected in the model tree."""

        if key.startswith("support:"):
            identifier = key.split(":", 1)[1]
            for item in self.app.project.supports:
                if item.id == identifier:
                    self._begin_attribute_edit(item)
                    return True
        if key.startswith("mass:"):
            identifier = key.split(":", 1)[1]
            for item in self.app.project.masses:
                if item.id == identifier:
                    self._begin_attribute_edit(item)
                    return True
        if key.startswith("load:") and not key.endswith(":gravity"):
            identifier = key.rsplit(":", 1)[1]
            for case_name, case in self.app.project.load_cases.items():
                for attribute in (
                    "point_loads",
                    "pressures",
                    "line_loads",
                    "surface_tractions",
                ):
                    for item in getattr(case, attribute):
                        if item.id == identifier:
                            self._begin_attribute_edit(item, case_name)
                            return True
        return False

    def _finish_attribute_edit(self) -> None:
        self._editing_attribute = None
        self._editing_case_name = None
        self._support_apply.configure(text="Apply to selection")
        self._pressure_apply.configure(text="Pressure on plates")
        self._point_apply.configure(text="Point load on points")
        self._line_apply.configure(text="Line load on lines")
        self._traction_apply.configure(text="Traction on plates")
        self._mass_apply.configure(text="Mass on selection")

    def _add_support(self) -> None:
        constraints = {
            dof: self.number(self._component_values[dof], dof) / 1000.0
            for dof, held in self._dofs.items()
            if held.get()
        }
        if not constraints:
            raise ValueError("tick at least one degree of freedom")
        if isinstance(self._editing_attribute, Support):
            current = self._editing_attribute
            updated = replace(
                current,
                constraints=dict(constraints),
                coordinate_system_id=self.coordinate_system_id(),
            )
            self.app.run(cmd.EditAttribute(updated))
            self._finish_attribute_edit()
            self.app.set_status(f"updated {current.name}")
            return
        items = self.require_selection(self.app.selection.mode)
        self.app.run_many(
            cmd.AddSupport(
                Support(
                    name=f"support_{ref}",
                    ref=ref,
                    constraints=dict(constraints),
                    coordinate_system_id=self.coordinate_system_id(),
                )
            )
            for ref in items
        )
        what = "prescribed" if any(constraints.values()) else "supported"
        self.app.set_status(f"{what} {len(items)} entity(ies)")

    def _broadcast_prescribed(self, *_args) -> None:
        value = self._prescribed.get()
        for dof, held in self._dofs.items():
            if held.get():
                self._component_values[dof].set(value)

    def _apply_support_preset(self, _event=None) -> None:
        name = self._support_preset.get()
        mapping = {
            "Fixed": set(DOF_NAMES),
            "Pinned": {"ux", "uy", "uz"},
            "Simply supported Z": {"uz"},
            "X symmetry": {"ux", "ry", "rz"},
            "Y symmetry": {"uy", "rx", "rz"},
            "Z symmetry": {"uz", "rx", "ry"},
            "X antisymmetry": {"uy", "uz", "rx"},
            "Y antisymmetry": {"ux", "uz", "ry"},
            "Z antisymmetry": {"ux", "uy", "rz"},
        }
        if name == "Prescribed":
            return
        selected = mapping.get(name, set())
        for dof, held in self._dofs.items():
            held.set(dof in selected)
            if dof in selected:
                self._component_values[dof].set("0")

    def _add_pressure(self) -> None:
        value = self.number(self._pressure, "pressure")
        if isinstance(self._editing_attribute, Pressure):
            current = self._editing_attribute
            self.app.run(cmd.EditAttribute(replace(current, value=value)))
            self._finish_attribute_edit()
            self.app.set_status("updated pressure")
            return
        faces = self.require_selection("face")
        self.app.run_many(
            cmd.AddPressure(ref, value, case=self.case_name())
            for ref in faces
        )
        self.app.set_status(f"pressure on {len(faces)} plate(s)")

    def _add_point_load(self) -> None:
        force = self.vector(self._force, "force")
        moment = self.vector(self._moment, "moment")
        policy = (
            "per_target"
            if self._point_policy.get() == "Per target"
            else "total_distributed"
        )
        if isinstance(self._editing_attribute, PointLoad):
            current = self._editing_attribute
            self.app.run(
                cmd.EditAttribute(
                    replace(
                        current,
                        force=force,
                        moment=moment,
                        coordinate_system_id=self.coordinate_system_id(),
                        distribution_policy=policy,
                    )
                )
            )
            self._finish_attribute_edit()
            self.app.set_status("updated point force/moment")
            return
        points = self.require_selection("vertex")
        scale = 1.0 if policy == "per_target" else 1.0 / len(points)
        self.app.run_many(
            cmd.AddPointLoad(
                ref,
                tuple(value * scale for value in force),
                tuple(value * scale for value in moment),
                case=self.case_name(),
                coordinate_system_id=self.coordinate_system_id(),
                distribution_policy=policy,
            )
            for ref in points
        )
        self.app.set_status(
            f"point force/moment on {len(points)} point(s), "
            f"{self._point_policy.get().lower()}"
        )

    def _add_line_load(self) -> None:
        intensity = self.vector(self._line, "line load")
        if isinstance(self._editing_attribute, LineLoad):
            current = self._editing_attribute
            self.app.run(
                cmd.EditAttribute(
                    replace(
                        current,
                        force_per_length=intensity,
                        coordinate_system_id=self.coordinate_system_id(),
                    )
                )
            )
            self._finish_attribute_edit()
            self.app.set_status("updated line load")
            return
        edges = self.require_selection("edge")
        self.app.run_many(
            cmd.AddLineLoad(
                ref,
                tuple(intensity),
                case=self.case_name(),
                coordinate_system_id=self.coordinate_system_id(),
            )
            for ref in edges
        )
        self.app.set_status(f"line load on {len(edges)} line(s)")

    def _add_traction(self) -> None:
        traction = self.vector(self._traction, "traction")
        if isinstance(self._editing_attribute, SurfaceTraction):
            current = self._editing_attribute
            self.app.run(
                cmd.EditAttribute(
                    replace(
                        current,
                        traction=traction,
                        coordinate_system_id=self.coordinate_system_id(),
                    )
                )
            )
            self._finish_attribute_edit()
            self.app.set_status("updated surface traction")
            return
        faces = self.require_selection("face")
        self.app.run_many(
            cmd.AddSurfaceTraction(
                ref,
                tuple(traction),
                case=self.case_name(),
                coordinate_system_id=self.coordinate_system_id(),
            )
            for ref in faces
        )
        self.app.set_status(f"traction on {len(faces)} plate(s)")

    def _set_acceleration(self) -> None:
        vector = self.vector(self._acceleration, "acceleration")
        self.app.run(
            cmd.SetAcceleration(
                tuple(vector),
                case=self.case_name(),
                coordinate_system_id=self.coordinate_system_id(),
            )
        )
        self.app.set_status(f"acceleration on case {self.case_name()}")

    def _add_mass(self) -> None:
        value = self.number(self._mass, "mass")
        policy = (
            "per_target"
            if self._point_policy.get() == "Per target"
            else "total_distributed"
        )
        if isinstance(self._editing_attribute, Mass):
            current = self._editing_attribute
            self.app.run(
                cmd.EditAttribute(
                    replace(current, value=value, distribution_policy=policy)
                )
            )
            self._finish_attribute_edit()
            self.app.set_status(f"updated {current.name}")
            return
        items = self.require_selection(self.app.selection.mode)
        scale = 1.0 if policy == "per_target" else 1.0 / len(items)
        self.app.run_many(
            cmd.AddMass(
                ref,
                value * scale,
                name=f"mass_{ref}",
                distribution_policy=policy,
            )
            for ref in items
        )
        self.app.set_status(
            f"mass on {len(items)} entity(ies), {self._point_policy.get().lower()}"
        )

    def _add_combination(self) -> None:
        name = self._combination_name.get().strip()
        if not name:
            raise ValueError("give the combination a name")
        parts = self._factors.get().replace(",", " ").split()
        if not parts or len(parts) % 2:
            raise ValueError(
                "factors must be pairs of case name and factor, e.g. "
                "'dead 1.2 live 1.5'"
            )
        factors = {}
        for case_name, factor in zip(parts[0::2], parts[1::2]):
            try:
                factors[case_name] = float(factor)
            except ValueError:
                raise ValueError(f"{factor!r} is not a number") from None
        self.app.run(cmd.AddCombination(name, factors))
        self.app.set_status(f"combination {name}")
        self.app.refresh_all()


# ----------------------------------------------------------------------
class SolvePanel(StagePanel):
    title = "Solve"

    # Each analysis names the option rows it uses, so the panel only shows
    # what the chosen one actually takes.
    ANALYSES = {
        "Linear static": (),
        "Batch linear static": ("batch_cases",),
        "Modal": ("modes",),
        "Buckling": ("modes",),
        "Nonlinear static": ("steps", "factor"),
        "Arc length": ("arc_steps",),
        "Transient": ("dt", "t_end", "damping"),
        "Impact": ("mass", "radius", "speed", "start", "direction"),
        "Capacity": ("modes", "steps", "factor", "imperfection"),
    }

    def build(self) -> None:
        controls = self.section("Analysis")
        row = ttk.Frame(controls)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="analysis", width=16).pack(side="left")
        self._analysis = tk.StringVar(value="Linear static")
        analysis_box = ttk.Combobox(
            row, textvariable=self._analysis, values=list(self.ANALYSES),
            state="readonly", width=16,
        )
        analysis_box.pack(side="left", fill="x", expand=True)
        analysis_box.bind(
            "<<ComboboxSelected>>",
            lambda _e: (self._show_options(), self._refresh_material_response()),
        )

        row = ttk.Frame(controls)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="loads", width=16).pack(side="left")
        self._target = tk.StringVar(value="case: default")
        self._target_box = ttk.Combobox(
            row, textvariable=self._target, values=["case: default"],
            state="readonly", width=16,
        )
        self._target_box.pack(side="left", fill="x", expand=True)
        self._material_response = ttk.Label(
            controls, text="", justify="left", foreground="#555555"
        )
        self._material_response.pack(anchor="w", fill="x", pady=(3, 1))

        options = self.section("Options")
        # tkinter.Misc owns a callable named ``_options`` which pack/grid use.
        # Shadowing it with this dictionary makes opening the Solve page fail.
        self._analysis_options = {}
        self._option_frames = {}
        for name, label, default in (
            ("batch_cases", "case names", "*"),
            ("modes", "modes", "6"),
            ("steps", "initial load increments", "10"),
            ("factor", "max load factor", "1.0"),
            ("arc_steps", "max arc steps", "60"),
            ("dt", "time step [s]", "0.0002"),
            ("t_end", "duration [s]", "0.02"),
            ("damping", "Rayleigh alpha", "0.0"),
            ("mass", "sphere mass [kg]", "500"),
            ("radius", "sphere radius [m]", "0.15"),
            ("speed", "speed [m/s]", "4.0"),
            ("start", "start x y z [m]", "0 0 1"),
            ("direction", "direction x y z", "0 0 -1"),
            ("imperfection", "imperfection [mm]", "5.0"),
        ):
            frame, variable = self.labelled_entry(
                options,
                label,
                default,
                label_width=20 if name == "steps" else 16,
            )
            self._analysis_options[name] = variable
            self._option_frames[name] = frame
        self._nominal_steps_hint = ttk.Label(
            options,
            text=(
                "Starting partition only: adaptive cutbacks and growth determine "
                "the actual number of converged increments."
            ),
            justify="left",
            foreground="#555555",
            wraplength=560,
        )

        self._advanced_open = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self,
            text="Advanced solver controls",
            variable=self._advanced_open,
            command=self._show_advanced,
        ).pack(anchor="w", pady=(5, 1))
        self._advanced = ttk.Frame(self)
        self._kinematics = self.entry_row(
            self._advanced, "kinematics", "von_karman"
        )
        self._corotational_tangent = self.entry_row(
            self._advanced, "corotational tangent", "auto"
        )
        self._record_snapshots = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self._advanced,
            text="save converged nonlinear increments",
            variable=self._record_snapshots,
        ).pack(anchor="w")

        self._run = ttk.Button(
            self, text="Run", command=self.guarded(self._solve)
        )
        self._run.pack(fill="x", pady=2)
        self._cancel = ttk.Button(
            self, text="Cancel", command=self.app.cancel_solve, state="disabled"
        )
        self._cancel.pack(fill="x", pady=2)

        self._progress = ttk.Label(self, text="", foreground="#555555")
        self._progress.pack(anchor="w", pady=(4, 0))

        self._transcript_tabs = ttk.Notebook(self)
        self._transcript_tabs.pack(fill="both", expand=True, pady=(6, 0))
        monitor_page = ttk.Frame(self._transcript_tabs)
        inputs_page = ttk.Frame(self._transcript_tabs)
        self._transcript_tabs.add(monitor_page, text="Run monitor")
        self._transcript_tabs.add(inputs_page, text="Submitted inputs")

        monitor = ttk.Panedwindow(monitor_page, orient=tk.VERTICAL)
        monitor.pack(fill="both", expand=True)
        graph_page = ttk.Frame(monitor, padding=(2, 2))
        log_page = ttk.Frame(monitor, padding=(2, 2))
        monitor.add(graph_page, weight=1)
        monitor.add(log_page, weight=1)

        graph_header = ttk.Frame(graph_page)
        graph_header.pack(fill="x")
        ttk.Label(graph_header, text="live graph", width=11).pack(side="left")
        self._live_graph_choice = tk.StringVar(value=GRAPH_CHOICES[0])
        graph_box = ttk.Combobox(
            graph_header,
            textvariable=self._live_graph_choice,
            values=GRAPH_CHOICES,
            state="readonly",
            width=30,
        )
        graph_box.pack(side="left", fill="x", expand=True)
        self._live_graph_choice_box = graph_box
        graph_box.bind(
            "<<ComboboxSelected>>", lambda _event: self._refresh_live_plot()
        )
        self._live_caption = ttk.Label(
            graph_page, text="Waiting for solver progress…", foreground="#555555"
        )
        self._live_caption.pack(fill="x", anchor="w")
        self._live_plot = HistoryPlot(graph_page, width=360, height=150)
        self._live_plot.pack(fill="both", expand=True)

        ttk.Label(log_page, text="Text log", foreground="#555555").pack(anchor="w")
        self._report = tk.Text(log_page, height=8, wrap="word", state="disabled")
        self._report.pack(fill="both", expand=True)
        self._submitted_inputs = tk.Text(
            inputs_page, height=12, wrap="none", state="disabled"
        )
        self._submitted_inputs.pack(fill="both", expand=True)
        self._live_line_count = 0
        self._last_live_line = ""
        self._live_data = LiveProgressData()
        self._live_plot_after = None

        self._show_options()
        self._show_advanced()

    # ------------------------------------------------------------------
    def _show_options(self) -> None:
        wanted = set(self.ANALYSES.get(self._analysis.get(), ()))
        for name, frame in self._option_frames.items():
            if name in wanted:
                frame.pack(fill="x", pady=1)
            else:
                frame.pack_forget()
        if self._analysis.get() in ("Nonlinear static", "Capacity"):
            self._nominal_steps_hint.pack(fill="x", anchor="w", pady=(3, 0))
        else:
            self._nominal_steps_hint.pack_forget()

    def _show_advanced(self) -> None:
        if self._advanced_open.get():
            self._advanced.pack(fill="x", pady=(0, 4), before=self._run)
        else:
            self._advanced.pack_forget()

    def refresh(self) -> None:
        running = self.app.worker.running
        self._run.configure(state="disabled" if running else "normal")
        self._cancel.configure(state="normal" if running else "disabled")

        project = self.app.project
        options = [f"case: {name}" for name in sorted(project.load_cases)]
        options += [
            f"combination: {name}" for name in sorted(project.combinations)
        ]
        self._target_box.configure(values=options or ["case: default"])
        if self._target.get() not in options and options:
            self._target.set(options[0])
        self._refresh_material_response()

    def _refresh_material_response(self) -> None:
        project = self.app.project
        material_names = {
            project.plate_sections[section_name].material
            for section_name in project.face_sections.values()
            if section_name in project.plate_sections
        }
        plastic = sorted(
            name
            for name in material_names
            if name in project.materials
            and project.materials[name].hardening is not None
        )
        elastic = sorted(material_names - set(plastic))
        analysis = self._analysis.get()
        nonlinear = analysis in ("Nonlinear static", "Arc length", "Capacity")
        if plastic:
            text = "Shell plasticity active: " + ", ".join(plastic)
            if elastic:
                text += "; elastic shell materials: " + ", ".join(elastic)
            colour = "#1b5e20"
        elif material_names:
            text = "Shell response is elastic"
            if nonlinear:
                text += " (analysis is geometrically nonlinear only)"
            text += ": " + ", ".join(sorted(material_names))
            colour = "#b23a00" if nonlinear else "#555555"
        else:
            text = "No assigned shell section/material"
            colour = "#b00020"
        if nonlinear and any(
            abs(float(value)) > 0.0
            for support in project.supports
            for value in support.constraints.values()
        ) and not project.imperfections:
            text += (
                "\nBuckling-path warning: the model is geometrically perfect. "
                "Add an out-of-plane imperfection; a symmetric flat model can "
                "remain on the flat equilibrium path."
            )
            colour = "#b23a00"
        self._material_response.configure(text=text, foreground=colour)

    def show_progress(self, text: str) -> None:
        self._progress.configure(text=text)

    def begin_job(self, name: str, job_id: str, submitted_inputs: str = "") -> None:
        """Start a bounded live transcript for a newly submitted job."""

        self._report.configure(state="normal")
        self._report.delete("1.0", "end")
        self._report.insert("end", f"{name}  [{job_id[:8]}]\n")
        self._report.configure(state="disabled")
        self._live_line_count = 1
        self._last_live_line = ""
        self._live_data.clear()
        self._refresh_live_plot()
        self.set_submitted_inputs(submitted_inputs)
        self._transcript_tabs.select(0)
        self.append_progress("queued")

    def set_submitted_inputs(self, text: str) -> None:
        self._submitted_inputs.configure(state="normal")
        self._submitted_inputs.delete("1.0", "end")
        self._submitted_inputs.insert("1.0", str(text).strip() or "No input record available")
        self._submitted_inputs.configure(state="disabled")

    def append_progress(self, text: str, payload=None) -> None:
        """Append one live solver message without allowing unbounded growth."""

        line = str(text).strip()
        if self._live_data.ingest(line, payload):
            self._schedule_live_plot()
        if not line or line == self._last_live_line:
            return
        self._last_live_line = line
        self._report.configure(state="normal")
        self._report.insert("end", line + "\n")
        self._live_line_count += 1
        if self._live_line_count > 1000:
            excess = self._live_line_count - 1000
            self._report.delete("1.0", f"{excess + 1}.0")
            self._live_line_count -= excess
        self._report.see("end")
        self._report.configure(state="disabled")

    def _schedule_live_plot(self) -> None:
        """Coalesce worker bursts so graph redraw never floods Tk."""

        if self._live_plot_after is None:
            self._live_plot_after = self.after(50, self._refresh_live_plot)

    def _refresh_live_plot(self) -> None:
        self._live_plot_after = None
        self._live_graph_choice_box.configure(values=self._live_data.graph_choices)
        choice = self._live_graph_choice.get()
        series = self._live_data.series(choice)
        self._live_plot.show([] if series is None else [series])
        self._live_caption.configure(text=self._live_data.caption(choice))

    def append_report(self, text: str) -> None:
        report = str(text).strip()
        if not report:
            return
        self._report.configure(state="normal")
        self._report.insert("end", "\nResult summary\n--------------\n" + report + "\n")
        self._report.see("end")
        self._report.configure(state="disabled")

    def write(self, text: str) -> None:
        self._report.configure(state="normal")
        self._report.delete("1.0", "end")
        self._report.insert("1.0", text)
        self._report.configure(state="disabled")
        self._live_line_count = int(self._report.index("end-1c").split(".")[0])
        self._last_live_line = ""

    def destroy(self) -> None:
        if self._live_plot_after is not None:
            try:
                self.after_cancel(self._live_plot_after)
            except tk.TclError:
                pass
            self._live_plot_after = None
        super().destroy()

    # ------------------------------------------------------------------
    @staticmethod
    def _triple(variable: tk.StringVar, label: str) -> tuple:
        parts = variable.get().replace(",", " ").split()
        if len(parts) != 3:
            raise ValueError(f"{label} needs three numbers, e.g. '0 0 -1'")
        try:
            return tuple(float(part) for part in parts)
        except ValueError:
            raise ValueError(f"{label} must be three numbers") from None

    def _target_kwargs(self) -> dict:
        choice = self._target.get()
        if choice.startswith("combination: "):
            return {"combination": choice.split(": ", 1)[1]}
        name = choice.split(": ", 1)[-1] if ": " in choice else choice
        return {"load_case": name or "default"}

    def _solve(self) -> None:
        analysis = self._analysis.get()
        kwargs = self._target_kwargs()

        if analysis == "Modal":
            kwargs = {"num_modes": int(self.number(self._analysis_options["modes"], "modes"))}
        elif analysis == "Batch linear static":
            value = self._analysis_options["batch_cases"].get().strip()
            names = (
                tuple(sorted(self.app.project.load_cases))
                if value in ("", "*")
                else tuple(value.replace(",", " ").split())
            )
            if not names:
                raise ValueError("a batch solve needs at least one load case")
            unknown = sorted(set(names) - set(self.app.project.load_cases))
            if unknown:
                raise ValueError(f"unknown batch load case(s): {unknown}")
            kwargs = {"load_cases": names}
        elif analysis == "Buckling":
            kwargs["num_modes"] = int(self.number(self._analysis_options["modes"], "modes"))
        elif analysis == "Nonlinear static":
            kwargs["num_steps"] = int(
                self.number(
                    self._analysis_options["steps"], "initial load increments"
                )
            )
            kwargs["max_load_factor"] = self.number(
                self._analysis_options["factor"], "max load factor"
            )
        elif analysis == "Arc length":
            from anysolver import ArcLengthControl

            kwargs["control"] = ArcLengthControl(
                max_steps=int(
                    self.number(self._analysis_options["arc_steps"], "max arc steps")
                )
            )
        elif analysis == "Transient":
            kwargs["dt"] = self.number(self._analysis_options["dt"], "time step")
            kwargs["t_end"] = self.number(self._analysis_options["t_end"], "duration")
            kwargs["rayleigh_alpha"] = self.number(
                self._analysis_options["damping"], "Rayleigh alpha"
            )
        elif analysis == "Impact":
            from ..model.collision import Collision

            # An impact does not need a load case; the sphere is the load.
            kwargs.pop("combination", None)
            kwargs["load_case"] = None
            kwargs["collision"] = Collision(
                mass=self.number(self._analysis_options["mass"], "sphere mass"),
                radius=self.number(self._analysis_options["radius"], "sphere radius"),
                speed=self.number(self._analysis_options["speed"], "speed"),
                start=self._triple(self._analysis_options["start"], "start"),
                direction=self._triple(
                    self._analysis_options["direction"], "direction"
                ),
            )
        elif analysis == "Capacity":
            kwargs["num_buckling_modes"] = int(
                self.number(self._analysis_options["modes"], "buckling modes")
            )
            kwargs["num_steps"] = int(
                self.number(
                    self._analysis_options["steps"], "initial load increments"
                )
            )
            kwargs["max_load_factor"] = self.number(
                self._analysis_options["factor"], "max load factor"
            )
            kwargs["imperfection_amplitude"] = self.number(
                self._analysis_options["imperfection"], "imperfection"
            ) / 1000.0

        if analysis in ("Nonlinear static", "Arc length"):
            kwargs["kinematics"] = self._kinematics.get().strip() or "von_karman"
            kwargs["corotational_tangent"] = (
                self._corotational_tangent.get().strip() or "auto"
            )
        if analysis in ("Nonlinear static", "Arc length", "Capacity"):
            kwargs["record_increment_snapshots"] = self._record_snapshots.get()

        self.app.solve(analysis, **kwargs)


# ----------------------------------------------------------------------
class ResultsPanel(StagePanel):
    title = "Results"

    def build(self) -> None:
        retained = self.section("Result set")
        row = ttk.Frame(retained)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="job", width=16).pack(side="left")
        self._job = tk.StringVar(value="-")
        self._job_ids = {}
        self._job_box = ttk.Combobox(
            row, textvariable=self._job, values=("-",), state="readonly", width=22
        )
        self._job_box.pack(side="left", fill="x", expand=True)
        self._job_box.bind("<<ComboboxSelected>>", lambda _event: self._pick_job())

        outcome = self.section("Analysis outcome")
        outcome_head = ttk.Frame(outcome)
        outcome_head.pack(fill="x", pady=(1, 3))
        self._outcome_status = ttk.Label(
            outcome_head, text="NO RESULT", font=("TkDefaultFont", 9, "bold")
        )
        self._outcome_status.pack(side="left")
        self._outcome_progress_text = ttk.Label(outcome_head, text="")
        self._outcome_progress_text.pack(side="right")
        metrics = ttk.Frame(outcome)
        metrics.pack(fill="x")
        self._outcome_values = {}
        for column, name in enumerate(("Start", "First converged", "Peak", "Last / target")):
            cell = ttk.Frame(metrics)
            cell.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 5, 0))
            metrics.columnconfigure(column, weight=1)
            ttk.Label(cell, text=name, foreground="#666666").pack(anchor="w")
            value = tk.StringVar(value="—")
            ttk.Label(cell, textvariable=value, font=("TkDefaultFont", 8, "bold")).pack(anchor="w")
            self._outcome_values[name] = value
        self._outcome_progress = ttk.Progressbar(
            outcome, orient="horizontal", mode="determinate", maximum=100.0
        )
        self._outcome_progress.pack(fill="x", pady=(4, 2))
        self._outcome_reason = ttk.Label(
            outcome, text="Run an analysis to see its outcome.", justify="left",
            wraplength=560,
        )
        self._outcome_reason.pack(fill="x", anchor="w")

        # Bound the task controls so adding visualization options cannot
        # collapse the lower path/probe workspace on typical laptop screens.
        setup_tabs = ttk.Notebook(self, height=220)
        setup_tabs.pack(fill="x", pady=(0, 6))
        display_page = ttk.Frame(setup_tabs, padding=4)
        visualization_page = ttk.Frame(setup_tabs, padding=4)
        increments_page = ttk.Frame(setup_tabs, padding=4)
        quantities_page = ttk.Frame(setup_tabs, padding=4)
        setup_tabs.add(display_page, text="Display")
        setup_tabs.add(visualization_page, text="Visualization")
        setup_tabs.add(increments_page, text="Increments")
        setup_tabs.add(quantities_page, text="Quantities & tools")

        controls = self.section("Contour display", parent=display_page)
        row = ttk.Frame(controls)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="field", width=16).pack(side="left")
        self._component = tk.StringVar(value="magnitude")
        self._field_box = ttk.Combobox(
            row, textvariable=self._component, values=available_fields(),
            state="readonly", width=14,
        )
        self._field_box.pack(side="left", fill="x", expand=True)
        self._field_box.bind("<<ComboboxSelected>>", lambda _event: self._show())

        self._scale = self.entry_row(controls, "deform scale", "auto")
        self._limits = self.entry_row(controls, "colour range", "auto")
        units_row = ttk.Frame(controls)
        units_row.pack(fill="x", pady=1)
        ttk.Label(units_row, text="display units", width=16).pack(side="left")
        self._display_units = tk.StringVar(value=DISPLAY_UNIT_SYSTEMS[0])
        ttk.Combobox(
            units_row,
            textvariable=self._display_units,
            values=DISPLAY_UNIT_SYSTEMS,
            state="readonly",
            width=22,
        ).pack(side="left", fill="x", expand=True)
        self._display_units.trace_add("write", lambda *_args: self._display_changed())
        self._envelope = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            controls,
            text="envelope over every shape",
            variable=self._envelope,
            command=self.guarded(self._show),
        ).pack(anchor="w")
        display_actions = ttk.Frame(controls)
        display_actions.pack(fill="x", pady=(3, 0))
        ttk.Button(display_actions, text="Update view", command=self.guarded(self._show)).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(
            display_actions,
            text="Model view",
            command=self._model_view,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(display_actions, text="Mesh view", command=self.app.show_mesh).pack(
            side="left", fill="x", expand=True, padx=(4, 0)
        )

        visual_columns = ttk.Frame(visualization_page)
        visual_columns.pack(fill="both", expand=True)
        visual_columns.columnconfigure(0, weight=3)
        visual_columns.columnconfigure(1, weight=2)
        appearance = ttk.LabelFrame(
            visual_columns, text="Viewport appearance", padding=6
        )
        appearance.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        initial_style = self.app.viewport.visualization
        render_row = ttk.Frame(appearance)
        render_row.pack(fill="x", pady=1)
        ttk.Label(render_row, text="render", width=16).pack(side="left")
        self._render_mode = tk.StringVar(value=initial_style.render_mode)
        ttk.Combobox(
            render_row,
            textvariable=self._render_mode,
            values=RENDER_MODES,
            state="readonly",
            width=22,
        ).pack(side="left", fill="x", expand=True)
        self._surface_opacity = self.entry_row(
            appearance,
            "surface opacity [%]",
            f"{100.0 * initial_style.surface_opacity:g}",
        )
        self._edge_width = self.entry_row(
            appearance, "edge width [px]", str(initial_style.edge_width)
        )
        self._background_color = self._colour_row(
            appearance, "background", initial_style.background
        )
        self._edge_color = self._colour_row(
            appearance, "element edges", initial_style.edge_color
        )
        palette_row = ttk.Frame(appearance)
        palette_row.pack(fill="x", pady=1)
        ttk.Label(palette_row, text="contour colours", width=16).pack(side="left")
        self._colormap = tk.StringVar(value="Cool-warm")
        ttk.Combobox(
            palette_row,
            textvariable=self._colormap,
            values=tuple(RESULT_COLORMAPS),
            state="readonly",
            width=22,
        ).pack(side="left", fill="x", expand=True)
        self._colormap.trace_add("write", lambda *_args: self._show_if_available())

        layers = ttk.LabelFrame(
            visual_columns, text="Visible result layers", padding=6
        )
        layers.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        self._show_result_nodes = tk.BooleanVar(value=False)
        self._show_result_legend = tk.BooleanVar(value=initial_style.show_legend)
        self._show_result_supports = tk.BooleanVar(value=True)
        self._show_result_loads = tk.BooleanVar(value=True)
        self._show_result_masses = tk.BooleanVar(value=True)
        # The purple stress-free reference is useful while defining an
        # imperfection, but obscures contours and modes.  Results default to a
        # clean view and let the engineer opt it back in.
        self._show_imperfect_reference = tk.BooleanVar(value=False)
        for index, (text, variable) in enumerate((
            ("FE nodes", self._show_result_nodes),
            ("contour legend", self._show_result_legend),
            ("supports", self._show_result_supports),
            ("loads", self._show_result_loads),
            ("masses", self._show_result_masses),
            ("initial imperfect reference (purple wire)", self._show_imperfect_reference),
        )):
            ttk.Checkbutton(
                layers,
                text=text,
                variable=variable,
                command=self.guarded(self._apply_visualization),
            ).grid(row=index, column=0, sticky="w")
        actions = ttk.Frame(visualization_page)
        actions.pack(fill="x", pady=(3, 0))
        ttk.Button(
            actions,
            text="Apply visualization",
            command=self.guarded(self._apply_visualization),
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            actions,
            text="Reset",
            command=self.guarded(self._reset_visualization),
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        if getattr(self.app.viewport, "supports_section_planes", False):
            clipping_page = ttk.Frame(setup_tabs, padding=4)
            setup_tabs.add(clipping_page, text="Section plane")
            clipping = self.section("Section plane", parent=clipping_page)
            self._clip_normal = self.vector_row(
                clipping, "normal", ("1", "0", "0")
            )
            self._clip_offset = self.entry_row(clipping, "offset [m]", "0")
            self.button(clipping, "Apply clipping", self._apply_section_plane)
            self.button(clipping, "Clear clipping", self._clear_section_plane)

        quantities = self.section("Available quantities", parent=quantities_page)
        self._quantities = ttk.Treeview(
            quantities, columns=("location", "unit"), show="tree headings", height=4
        )
        self._quantities.heading("#0", text="Quantity")
        self._quantities.heading("location", text="Location")
        self._quantities.heading("unit", text="Unit")
        self._quantities.column("#0", width=125, stretch=True)
        self._quantities.column("location", width=70, stretch=False)
        self._quantities.column("unit", width=70, stretch=False)
        self._quantities.pack(fill="x")
        self._quantities.bind("<Double-1>", lambda _event: self._activate_quantity())

        browse = self.section("Increment / shape", parent=increments_page)
        row = ttk.Frame(browse)
        row.pack(fill="x", pady=1)
        ttk.Button(row, text="<", width=3, command=self.guarded(self._previous)).pack(
            side="left"
        )
        self._shape = tk.StringVar(value="-")
        self._shape_box = ttk.Combobox(
            row, textvariable=self._shape, values=["-"], state="readonly", width=18
        )
        self._shape_box.pack(side="left", fill="x", expand=True, padx=2)
        self._shape_box.bind("<<ComboboxSelected>>", lambda _e: self._pick_shape())
        ttk.Button(row, text=">", width=3, command=self.guarded(self._next)).pack(
            side="left"
        )
        self._play_button = ttk.Button(
            row, text="▶ Play", width=8, command=self.guarded(self._animate)
        )
        self._play_button.pack(side="left", padx=(3, 0))
        ttk.Label(row, text="speed").pack(side="left", padx=(8, 2))
        self._playback_fps = tk.StringVar(value="4")
        self._playback_speed_box = ttk.Combobox(
            row,
            textvariable=self._playback_fps,
            values=("0.5", "1", "2", "4", "8", "12", "20", "30"),
            state="readonly",
            width=4,
        )
        self._playback_speed_box.pack(side="left")
        ttk.Label(row, text="fps").pack(side="left", padx=(2, 0))
        self._frame_details = ttk.Label(
            browse, text="No saved increments.", foreground="#555555", wraplength=560
        )
        self._frame_details.pack(fill="x", anchor="w", pady=(2, 0))

        query = self.section("Tools", parent=quantities_page)
        tool_specs = (
            ("Probe", self._probe),
            ("Along line", self._along_line),
            ("Report", self._export_report),
            ("CSV", self._export_field),
            ("PNG", self._export_png),
            ("GIF", self._export_gif),
        )
        tool_buttons = {}
        for index, (label, command) in enumerate(tool_specs):
            button = ttk.Button(query, text=label, command=self.guarded(command))
            button.grid(row=0, column=index, sticky="ew", padx=2, pady=2)
            query.columnconfigure(index, weight=1)
            tool_buttons[label] = button
        png_button = tool_buttons["PNG"]
        gif_button = tool_buttons["GIF"]
        if not getattr(self.app.viewport, "capture_available", False):
            png_button.configure(state="disabled")
            gif_button.configure(state="disabled")

        # The lower workspace owns the remaining height.  Path, detailed probe
        # text and submitted inputs no longer compete vertically in three tiny
        # canvases; each gets the full area when selected.
        self._readout_tabs = ttk.Notebook(self)
        self._readout_tabs.pack(fill="both", expand=True, pady=(6, 0))
        path_page = ttk.Frame(self._readout_tabs)
        readout_page = ttk.Frame(self._readout_tabs)
        inputs_page = ttk.Frame(self._readout_tabs)
        self._readout_tabs.add(path_page, text="Path / increments")
        self._readout_tabs.add(readout_page, text="Probe / details")
        self._readout_tabs.add(inputs_page, text="Submitted inputs")
        path_actions = ttk.Frame(path_page)
        path_actions.pack(fill="x")
        ttk.Label(
            path_actions,
            text=(
                "Green = unloaded start   Orange = peak   Purple = last converged   "
                "Cyan = displayed increment"
            ),
            foreground="#555555",
        ).pack(side="left")
        ttk.Button(
            path_actions, text="Plot selected point", command=self.guarded(self._plot_at_selection)
        ).pack(side="right")
        self.plot = HistoryPlot(path_page, width=380, height=260)
        self.plot.pack(fill="both", expand=True, pady=(3, 0))

        self._summary = ttk.Label(
            readout_page, text="no results", justify="left", wraplength=600
        )
        self._summary.pack(fill="x", anchor="w", padx=4, pady=4)
        self._readout = tk.Text(
            readout_page, height=16, wrap="none", state="disabled"
        )
        self._readout.pack(fill="both", expand=True)
        self._result_inputs = tk.Text(
            inputs_page, height=16, wrap="none", state="disabled"
        )
        self._result_inputs.pack(fill="both", expand=True)
        self._animation_after = None
        self._animation_index = 0
        self._gif_export = None
        self._gif_poll_after = None
        self._gif_results: Queue = Queue()

    # ------------------------------------------------------------------
    def field_name(self) -> str:
        return self._component.get()

    def ensure_compatible_field(self) -> str:
        """Keep artifact field keys from leaking into live result display.

        Persisted artifacts store the vector quantity as ``displacement``;
        live ShapeView contours expose its scalar magnitude as ``magnitude``.
        Switching result sources must therefore validate the retained choice
        before the viewport evaluates it.
        """

        solution = self.app.solution
        if solution is None:
            dataset = self.app.result_datasets.get(self.app.active_job_id)
            choices = tuple(dataset.field_keys) if dataset is not None else ()
        else:
            available = getattr(solution, "available_fields", None)
            choices = tuple(
                available() if callable(available) else available_fields()
            )
        if choices:
            self._field_box.configure(values=choices)
            current = self._component.get()
            aliases = {
                "displacement": "magnitude",
                "mode_shape": "magnitude",
                "stress": "von_mises",
            }
            replacement = aliases.get(current, current)
            if replacement not in choices:
                replacement = "magnitude" if "magnitude" in choices else choices[0]
            if replacement != current:
                self._component.set(replacement)
        return self._component.get()

    def display_units(self) -> str:
        return self._display_units.get()

    def colormap(self):
        return RESULT_COLORMAPS.get(
            self._colormap.get(), RESULT_COLORMAPS["Cool-warm"]
        )

    def show_result_nodes(self) -> bool:
        return bool(self._show_result_nodes.get())

    def show_result_supports(self) -> bool:
        return bool(self._show_result_supports.get())

    def show_result_loads(self) -> bool:
        return bool(self._show_result_loads.get())

    def show_result_masses(self) -> bool:
        return bool(self._show_result_masses.get())

    def show_imperfect_reference(self) -> bool:
        return bool(self._show_imperfect_reference.get())

    def _colour_row(self, parent, label: str, value: str) -> tk.StringVar:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=label, width=16).pack(side="left")
        variable = tk.StringVar(value=value)
        ttk.Entry(row, textvariable=variable, width=14).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(
            row,
            text="Choose…",
            width=9,
            command=lambda: self._choose_colour(variable),
        ).pack(side="left", padx=(4, 0))
        return variable

    def _choose_colour(self, variable: tk.StringVar) -> None:
        _rgb, colour = colorchooser.askcolor(
            color=variable.get(), parent=self, title="Choose viewport colour"
        )
        if colour:
            variable.set(colour)

    def _visualization_style(self) -> VisualizationStyle:
        try:
            opacity = float(self._surface_opacity.get()) / 100.0
        except ValueError:
            raise ValueError("surface opacity must be a percentage from 0 to 100") from None
        try:
            edge_width = int(self._edge_width.get())
        except ValueError:
            raise ValueError("edge width must be an integer from 1 to 8") from None
        return VisualizationStyle(
            background=self._background_color.get().strip(),
            render_mode=self._render_mode.get(),
            surface_opacity=opacity,
            edge_color=self._edge_color.get().strip(),
            edge_width=edge_width,
            show_legend=bool(self._show_result_legend.get()),
        )

    def _apply_visualization(self) -> None:
        self.app.viewport.set_visualization(self._visualization_style())
        self._show_if_available()

    def _reset_visualization(self) -> None:
        style = VisualizationStyle()
        self._render_mode.set(style.render_mode)
        self._surface_opacity.set(f"{100.0 * style.surface_opacity:g}")
        self._edge_width.set(str(style.edge_width))
        self._background_color.set(style.background)
        self._edge_color.set(style.edge_color)
        self._colormap.set("Cool-warm")
        self._show_result_nodes.set(False)
        self._show_result_legend.set(True)
        self._show_result_supports.set(True)
        self._show_result_loads.set(True)
        self._show_result_masses.set(True)
        self._show_imperfect_reference.set(False)
        self._apply_visualization()

    def _show_if_available(self) -> None:
        if self.app.solution is not None or (
            self.app.active_job_id in self.app.result_datasets
        ):
            self.guarded(self._show)()

    def _display_changed(self) -> None:
        self._show_if_available()
        self.refresh()

    def colour_limits(self):
        text = self._limits.get().strip().lower()
        if text in ("", "auto"):
            return None
        parts = text.replace(",", " ").split()
        if len(parts) != 2:
            raise ValueError(
                "colour range must be 'auto' or two numbers, e.g. '0 30e6'"
            )
        try:
            low, high = float(parts[0]), float(parts[1])
        except ValueError:
            raise ValueError(f"colour range {text!r} is not two numbers") from None
        return low, high

    def field_values(self):
        """The Field to colour by: this shape, or the envelope of all of them."""

        if not self._envelope.get():
            return None
        solution = self.app.solution
        if solution is None:
            return None
        return envelope(solution, self.field_name()).field

    def write(self, text: str) -> None:
        self._readout.configure(state="normal")
        self._readout.delete("1.0", "end")
        self._readout.insert("1.0", text)
        self._readout.configure(state="disabled")
        self._readout_tabs.select(1)

    @staticmethod
    def _factor(value) -> str:
        return "—" if value is None else f"λ = {float(value):.5g}"

    def _clear_outcome(self, message: str = "Run an analysis to see its outcome.") -> None:
        self._outcome_status.configure(text="NO RESULT", foreground="#666666")
        for value in self._outcome_values.values():
            value.set("—")
        self._outcome_progress.configure(value=0.0)
        self._outcome_progress_text.configure(text="")
        self._outcome_reason.configure(text=message, foreground="#555555")

    def _update_outcome(self, solution) -> None:
        job_id = getattr(self.app, "active_job_id", None)
        submitted = getattr(self.app, "submitted_input_reports", {}).get(job_id)
        submitted_target = submitted_target_load_factor(submitted)
        path = nonlinear_path_summary(
            solution, target_load_factor=submitted_target
        )
        if path is None:
            status = str(getattr(solution, "status", "available"))
            self._outcome_status.configure(
                text=status.replace("_", " ").upper(), foreground="#2e7d32"
            )
            for value in self._outcome_values.values():
                value.set("—")
            self._outcome_progress.configure(value=100.0)
            self._outcome_progress_text.configure(text="")
            self._outcome_reason.configure(
                text="This analysis has no nonlinear equilibrium path.",
                foreground="#555555",
            )
            return
        colour = {
            "success": "#2e7d32",
            "warning": "#b26a00",
            "error": "#b00020",
        }[path.severity]
        self._outcome_status.configure(
            text=path.status.replace("_", " ").upper(), foreground=colour
        )
        self._outcome_values["Start"].set("λ = 0 (unloaded)")
        self._outcome_values["First converged"].set(
            self._factor(path.first_converged_load_factor)
        )
        self._outcome_values["Peak"].set(self._factor(path.peak_load_factor))
        last = self._factor(path.last_converged_load_factor)
        target = self._factor(path.target_load_factor)
        self._outcome_values["Last / target"].set(f"{last} / {target}")
        fraction = path.progress_fraction
        self._outcome_progress.configure(value=0.0 if fraction is None else 100.0 * fraction)
        self._outcome_progress_text.configure(
            text="" if fraction is None else f"{100.0 * fraction:.1f}% of target"
        )
        details = [
            path.stop_reason,
            f"{path.converged_steps} converged increments",
            f"{path.total_iterations} Newton iterations",
        ]
        if path.status.casefold() == "stopped_at_limit":
            details.append(
                "numerical path stop; not by itself a verified capacity point"
            )
        if path.first_failed_load_factor is not None:
            details.append(f"first failed trial λ={path.first_failed_load_factor:.5g}")
        if path.failed_iteration_reason:
            details.append(path.failed_iteration_reason)
        if path.max_peeq is not None:
            details.append(f"max PEEQ={path.max_peeq:.5g}")
        details.extend(
            prescribed_path_progress(
                submitted,
                last_load_factor=path.last_converged_load_factor,
                target_load_factor=path.target_load_factor,
            )
        )
        built = getattr(solution, "built", None)
        project = getattr(built, "project", None)
        if (
            path.status.casefold() == "stopped_at_limit"
            and project is not None
            and not (getattr(project, "imperfections", ()) or ())
        ):
            details.append(
                "perfect geometry submitted (no imperfection); a buckling branch "
                "can be difficult to follow"
            )
        details.append(
            f"{path.saved_increments} committed contour frame(s) saved"
            if path.saved_increments
            else "increment contours were not saved"
        )
        self._outcome_reason.configure(text="  •  ".join(details), foreground=colour)

    def _update_frame_details(self) -> None:
        solution = self.app.solution
        if solution is None:
            dataset = self.app.result_datasets.get(self.app.active_job_id)
            count = 0 if dataset is None else len(dataset.frames)
            self._frame_details.configure(
                text=(
                    f"Persisted frame {self.app.shape_index + 1} of {count}."
                    if count else "Static result; no frame sequence."
                )
            )
            return
        shapes = getattr(solution, "shapes", None)
        if not shapes:
            self.plot.set_active_index(None)
            if nonlinear_path_summary(solution) is not None:
                self._frame_details.configure(
                    text=(
                        "Final converged state only. To animate real load increments, "
                        "enable ‘Save converged increment snapshots’ in Solve and rerun."
                    ),
                    foreground="#9a5b00",
                )
            else:
                self._frame_details.configure(text="Single static shape.", foreground="#555555")
            return
        index = min(max(self.app.shape_index, 0), len(shapes) - 1)
        shape = shapes[index]
        step = getattr(shape, "step", None)
        if step is None:
            self.plot.set_active_index(None)
            self._frame_details.configure(
                text=f"Shape {index + 1} of {len(shapes)} • value {float(shape.value):.5g}",
                foreground="#555555",
            )
            return
        # The nonlinear history adds the unloaded origin before converged
        # increments, hence the +1 correspondence to the saved frame index.
        self.plot.set_active_index(index + 1)
        pieces = [
            f"Saved increment {index + 1} of {len(shapes)}",
            f"λ={float(shape.value):.5g}",
            f"{int(getattr(step, 'iterations', 0))} iterations",
            f"residual={float(getattr(step, 'residual_norm', 0.0)):.3g}",
            f"|u|={float(getattr(step, 'displacement_norm', 0.0)):.5g} m",
            f"PEEQ={float(getattr(step, 'max_equivalent_plastic_strain', 0.0)):.5g}",
        ]
        self._frame_details.configure(text="  •  ".join(pieces), foreground="#333333")

    def _refresh_submitted_inputs(self) -> None:
        job_id = getattr(self.app, "active_job_id", None)
        report = getattr(self.app, "submitted_input_reports", {}).get(job_id)
        if report is None:
            dataset = self.app.result_datasets.get(job_id)
            if dataset is not None:
                provenance = dataset.metadata("provenance")
                submitted = provenance.get("submitted_inputs")
                if submitted is not None:
                    import json

                    report = json.dumps(
                        submitted, indent=2, sort_keys=True, allow_nan=False
                    )
                elif provenance.get("submitted_inputs_text"):
                    report = str(provenance["submitted_inputs_text"])
        if report is None:
            report = (
                "Submitted input text is unavailable for this reopened/legacy result. "
                "Its hashes and provenance remain in the result artifact."
            )
        self._result_inputs.configure(state="normal")
        self._result_inputs.delete("1.0", "end")
        self._result_inputs.insert("1.0", report)
        self._result_inputs.configure(state="disabled")

    # ------------------------------------------------------------------
    def _require_solution(self):
        if self.app.solution is None:
            raise ValueError("run a solve first")
        return self.app.current_shape()

    def _model_view(self) -> None:
        """Show selectable design geometry while retaining this result task."""

        kind = self.app.selection.mode
        if kind not in ("vertex", "edge"):
            kind = "vertex"
        self.app.show_geometry()
        self.app.selection_strip.set_context(
            kind,
            "Result probing • select model Points or Lines, then use Probe / Along line",
        )
        self.app.details.set_hint("Model view for result probes")
        self.app.set_status(
            "model geometry shown; select points or lines, then use Probe or Along line"
        )

    def _probe(self) -> None:
        shape = self._require_solution()
        items = self.require_selection(self.app.selection.mode)
        engineering = self.display_units() == ENGINEERING_DISPLAY
        readouts = [
            probe(shape, ref).text(
                length_scale=1000.0 if engineering else 1.0,
                length_unit="mm" if engineering else "m",
                stress_scale=1.0e-6 if engineering else 1.0,
                stress_unit="MPa" if engineering else "Pa",
            )
            for ref in items
        ]
        self.write((chr(10) * 2).join(readouts))
        self.app.set_status(f"probed {len(items)} entity(ies)")

    def _along_line(self) -> None:
        shape = self._require_solution()
        edges = self.require_selection("edge", 1)
        result = along_line(shape, edges[0], self.field_name())
        distance_scale, distance_unit = unit_transform("m", self.display_units())
        value_scale, value_unit = unit_transform(result.unit, self.display_units())
        lines = [
            f"{self.field_name()} along {edges[0]}  "
            f"({result.length * distance_scale:.4g} {distance_unit})",
            f"{'distance [' + distance_unit + ']':>16}  "
            f"{('value [' + value_unit + ']') if value_unit else 'value':>18}",
        ]
        for distance, value in zip(result.distances, result.values):
            lines.append(
                f"{distance * distance_scale:16.5g}  {value * value_scale:18.6g}"
            )
        self.write(chr(10).join(lines))
        self.app.set_status(f"sampled {len(result)} points along {edges[0]}")

    def _export_report(self) -> None:
        active_job_id = getattr(self.app, "active_job_id", None)
        dataset = getattr(self.app, "result_datasets", {}).get(active_job_id)
        solution = self.app.solution
        if dataset is None and solution is None:
            raise ValueError("run or open a result first")
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=(
                [
                    ("Markdown", "*.md"),
                    ("HTML", "*.html"),
                    ("All files", "*.*"),
                ]
                if dataset is not None
                else [("Markdown", "*.md"), ("All files", "*.*")]
            ),
            initialfile=f"{self.app.project.name}_report.md",
        )
        if not path:
            return
        if dataset is None:
            if str(path).casefold().endswith((".html", ".htm")):
                raise ValueError(
                    "HTML reporting requires the retained result artifact; "
                    "save the project and retry when result persistence completes"
                )
            written = write_report(solution, path)
        else:
            job = getattr(self.app.project, "jobs", {}).get(active_job_id)
            stale_method = getattr(self.app, "_job_is_stale", None)
            stale = stale_method(job) if callable(stale_method) and job is not None else None
            session = getattr(self.app, "session", None)
            revision = getattr(session, "revision", None)
            context = result_report_context(
                dataset,
                project=self.app.project,
                job=job,
                current_document_hash=str(
                    getattr(revision, "document_hash", "")
                ),
                stale=stale,
            )
            written = write_result_report(dataset, path, context=context)
        self.app.set_status(f"report written to {written}")

    def _export_field(self) -> None:
        dataset = None
        if self.app.solution is None and self.app.active_job_id is not None:
            dataset = self.app.result_datasets.get(self.app.active_job_id)
        if dataset is None:
            shape = self._require_solution()
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            initialfile=f"{self.app.project.name}_{self.field_name()}.csv",
        )
        if not path:
            return
        if dataset is not None:
            key = self.field_name()
            if key not in dataset.field_keys:
                raise ValueError(f"retained result has no field {key!r}")
            text = lazy_field_to_csv(dataset, key, frame=self.app.shape_index)
        else:
            text = field_to_csv(shape, self.field_name())
        written = write_csv(text, path)
        self.app.set_status(f"field written to {written}")

    def _require_result_set(self) -> None:
        if self.app.solution is not None:
            return
        if (
            self.app.active_job_id is not None
            and self.app.active_job_id in self.app.result_datasets
        ):
            return
        raise ValueError("run or open a result first")

    def _require_capturable_result(self) -> None:
        """Reject table/history quantities instead of capturing stale geometry."""

        self._require_result_set()
        if self.app.solution is not None:
            return
        dataset = self.app.result_datasets[self.app.active_job_id]
        key = self.field_name()
        if key not in dataset.field_keys:
            raise ValueError(f"retained result has no field {key!r}")
        location = dataset.field(key).descriptor.location
        if location not in (
            "node", "element", "element_face", "integration_point"
        ):
            raise ValueError(
                f"{key!r} is a {location} table, not a spatial field; "
                "export it as CSV instead"
            )

    def _render_result_frame(self, index: int) -> None:
        self.app.shape_index = int(index)
        if self.app.solution is None:
            self._show()
        else:
            self.app.show_results()

    def _export_png(self) -> None:
        self._require_capturable_result()
        if not getattr(self.app.viewport, "capture_available", False):
            raise ValueError("viewport PNG export is unavailable on this desktop")
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")],
            initialfile=f"{self.app.project.name}_{self.field_name()}.png",
        )
        if not path:
            return
        self._render_result_frame(self.app.shape_index)
        try:
            written = self.app.viewport.capture_png(path)
        except RuntimeError as error:
            raise ValueError(str(error)) from None
        self.app.set_status(f"viewport image written to {written}")

    def _export_gif(self) -> None:
        """Capture result shapes incrementally so Tk remains responsive."""

        self._require_capturable_result()
        if not getattr(self.app.viewport, "capture_available", False):
            raise ValueError("viewport GIF export is unavailable on this desktop")
        count = self._shape_count()
        if count < 2:
            raise ValueError("this result has only one shape to export")
        if self._gif_export is not None or self._gif_poll_after is not None:
            raise ValueError("a GIF export is already in progress")
        path = filedialog.asksaveasfilename(
            defaultextension=".gif",
            filetypes=[("GIF animation", "*.gif"), ("All files", "*.*")],
            initialfile=f"{self.app.project.name}_{self.field_name()}.gif",
        )
        if not path:
            return
        if self._animation_after is not None:
            self.after_cancel(self._animation_after)
            self._animation_after = None
        self._gif_export = {
            "path": path,
            "index": 0,
            "count": count,
            "original": self.app.shape_index,
            "images": [],
        }
        self.app.set_status(f"capturing GIF frame 1 of {count}")
        self.after_idle(self._capture_gif_frame)

    def _capture_gif_frame(self) -> None:
        state = self._gif_export
        if state is None:
            return
        try:
            index = int(state["index"])
            count = int(state["count"])
            if index >= count:
                self._finish_gif_capture()
                return
            self._render_result_frame(index)
            self.update_idletasks()
            state["images"].append(self.app.viewport.capture_image())
            state["index"] = index + 1
            if index + 1 >= count:
                self.after_idle(self._capture_gif_frame)
            else:
                self.app.set_status(
                    f"capturing GIF frame {index + 2} of {count}"
                )
                self.after(1, self._capture_gif_frame)
        except Exception as error:  # asynchronous callbacks cannot use guarded()
            self._abort_gif_capture(error)

    def _restore_gif_shape(self, state) -> None:
        try:
            self._render_result_frame(int(state["original"]))
            self.app.refresh_panels()
        except Exception:
            # Preserve the export diagnostic if a closing window cannot redraw.
            pass

    def _finish_gif_capture(self) -> None:
        state = self._gif_export
        if state is None:
            return
        self._gif_export = None
        self._restore_gif_shape(state)
        path = state["path"]
        images = list(state["images"])

        def encode() -> None:
            try:
                self._gif_results.put((save_gif(images, path), None))
            except Exception as error:
                self._gif_results.put((None, error))

        Thread(target=encode, name="anyfem-gif-export", daemon=True).start()
        self.app.set_status("encoding GIF animation")
        self._gif_poll_after = self.after(50, self._poll_gif_export)

    def _poll_gif_export(self) -> None:
        try:
            written, error = self._gif_results.get_nowait()
        except Empty:
            self._gif_poll_after = self.after(50, self._poll_gif_export)
            return
        self._gif_poll_after = None
        if error is not None:
            self.app.set_status(f"GIF export failed: {error}", error=True)
        else:
            self.app.set_status(f"GIF animation written to {written}")

    def _abort_gif_capture(self, error: Exception) -> None:
        state = self._gif_export
        self._gif_export = None
        if state is not None:
            self._restore_gif_shape(state)
        self.app.set_status(f"GIF export failed: {error}", error=True)

    def _apply_section_plane(self) -> None:
        normal = self.vector(self._clip_normal, "section-plane normal")
        offset = self.number(self._clip_offset, "section-plane offset")
        self.app.viewport.set_section_plane(normal, offset, enabled=True)
        self.app.set_status("section-plane clipping enabled")

    def _clear_section_plane(self) -> None:
        self.app.viewport.clear_section_plane()
        self.app.set_status("section-plane clipping cleared")

    def _plot_at_selection(self) -> None:
        """Trace the history at a selected point rather than the peak node."""

        solution = self.app.solution
        if solution is None:
            raise ValueError("run a solve first")
        points = self.require_selection("vertex", count=1)
        node = self.app.mesh.node_of_vertex.get(points[0].id)
        if node is None:
            raise ValueError(f"{points[0]} has no node in the mesh")
        series = history_series(solution, probe=node, component=self.field_name()
                                if self.field_name() in DOF_NAMES else "uz")
        if not series:
            raise ValueError("this result has no history to plot")
        self.plot.show(converted_series(series, self.display_units()))
        self._readout_tabs.select(0)
        self.app.set_status(f"plotting at node {node}")

    def refresh(self) -> None:
        records = list(getattr(self.app.project, "jobs", {}).values())
        labels = []
        self._job_ids = {}
        for record in reversed(records):
            stale = self.app._job_is_stale(record)
            status = getattr(record.status, "value", str(record.status))
            retained = getattr(self.app, "solutions", {}).get(record.id)
            analysis_status = str(getattr(retained, "status", "")).strip()
            if status == "completed" and analysis_status and analysis_status not in {
                "completed", "converged", "ok"
            }:
                status = (
                    f"job completed; analysis {analysis_status.replace('_', ' ')}"
                )
            label = f"{record.name} [{status}{', stale' if stale else ''}]"
            labels.append(label)
            self._job_ids[label] = record.id
        self._job_box.configure(values=labels or ["-"])
        active_id = getattr(self.app, "active_job_id", None)
        active_label = next(
            (label for label, identifier in self._job_ids.items() if identifier == active_id),
            None,
        )
        if active_label is not None:
            self._job.set(active_label)
        elif self._job.get() not in self._job_ids:
            self._job.set(labels[0] if labels else "-")
        self._refresh_submitted_inputs()

        solution = self.app.solution
        if solution is None:
            dataset = self.app.result_datasets.get(active_id)
            if dataset is not None:
                keys = dataset.field_keys
                self._field_box.configure(values=keys)
                if keys and self._component.get() not in keys:
                    self._component.set(keys[0])
                self._refresh_quantities(dataset)
                frames = dataset.frames
                labels = (
                    [
                        f"frame {index + 1} ({value:.5g})"
                        for index, value in enumerate(frames)
                    ]
                    if len(frames)
                    else ["static"]
                )
                self._shape_box.configure(values=labels)
                self.app.shape_index = min(self.app.shape_index, len(labels) - 1)
                self._shape.set(labels[self.app.shape_index])
                summary = dataset.metadata("summary")
                text = (
                    summary.get("text")
                    or summary.get("solution_type")
                    or "Persisted result"
                )
                deformation = (
                    "deformation available"
                    if "displacement" in keys
                    else "deformation unavailable (undeformed display)"
                )
                self._summary.configure(text=f"{text}\n{deformation}")
                status = str(summary.get("status", "persisted result"))
                self._outcome_status.configure(
                    text=status.replace("_", " ").upper(), foreground="#2e7d32"
                )
                for value in self._outcome_values.values():
                    value.set("—")
                self._outcome_progress.configure(value=100.0)
                self._outcome_progress_text.configure(
                    text=f"{len(frames)} saved frame(s)" if len(frames) else "static"
                )
                self._outcome_reason.configure(
                    text=(
                        "Loaded from retained result artifact. Detailed nonlinear "
                        "termination metadata is shown when present in the report/inputs."
                    ),
                    foreground="#555555",
                )
                self._update_frame_details()
                self.plot.clear()
                return
            self._summary.configure(text="no results")
            self._shape_box.configure(values=["-"])
            self._shape.set("-")
            self.plot.clear()
            self._quantities.delete(*self._quantities.get_children())
            self._clear_outcome()
            self._update_frame_details()
            return

        # The plot follows the result: a transient or an incremental solve has
        # a history, a linear static does not, and an empty plot says so rather
        # than keeping the previous result's curve on screen.
        self.plot.show(
            converted_series(history_series(solution), self.display_units())
        )
        self._update_outcome(solution)

        # An imported result names its own fields, which are the file's, not
        # ANYfem's list.
        self.ensure_compatible_field()
        self._refresh_quantities(solution)

        shapes = getattr(solution, "shapes", None)
        if shapes:
            labels = [shape.label for shape in shapes]
            self._shape_box.configure(values=labels)
            index = min(self.app.shape_index, len(labels) - 1)
            self._shape.set(labels[index])
        else:
            self._shape_box.configure(values=["static"])
            self._shape.set("static")
        self._update_frame_details()

        shape = self.app.current_shape()
        try:
            node, magnitude = shape.max_translation()
            scale, unit = unit_transform("m", self.display_units())
            displacement_text = (
                f"max translation {magnitude * scale:.6g} {unit} at node {node}"
            )
        except KeyError:
            displacement_text = "deformation unavailable (undeformed display)"
        constitutive = self._constitutive_summary(shape)
        self._summary.configure(
            text=(
                f"{solution.summary()}\n"
                f"showing {getattr(shape, 'label', 'static')}\n"
                f"{displacement_text}\n{constitutive}"
            )
        )

    @staticmethod
    def _constitutive_summary(shape) -> str:
        model = shape.built.fe_model
        shell_materials = {
            element.material_name
            for element in model.mesh.elements.values()
            if hasattr(element, "thickness")
        }
        plastic = sorted(
            name
            for name in shell_materials
            if getattr(model.get_material(name), "hardening_curve", None) is not None
        )
        if not plastic:
            return "Constitutive response: ELASTIC shells (geometric nonlinearity only)"
        raw = getattr(shape, "raw_result", None)
        states = getattr(raw, "element_states", {}) or {}
        maxima = [
            float(np.max(np.asarray(state.get("alpha", (0.0,)), dtype=float)))
            for state in states.values()
            if isinstance(state, dict) and len(state.get("alpha", ()))
        ]
        yielded = sum(value > 1.0e-12 for value in maxima)
        maximum = max(maxima, default=0.0)
        return (
            f"Constitutive response: NONLINEAR PLASTICITY ACTIVE "
            f"({', '.join(plastic)}); yielded elements {yielded}/{len(maxima)}, "
            f"max alpha {maximum:.5g}"
        )

    def _pick_job(self) -> None:
        job_id = self._job_ids.get(self._job.get())
        if job_id is None:
            return
        solution = self.app.solutions.get(job_id)
        if solution is None:
            try:
                solution = self.app.job_manager.result(job_id)
            except KeyError:
                dataset = self.app.result_datasets.get(job_id)
                if dataset is None:
                    raise ValueError(
                        "this retained job has no in-memory result and its "
                        "artifact is unavailable"
                    )
                self.app.active_job_id = job_id
                self.app.solution = None
                self.app.shape_index = 0
                self.app.refresh_panels()
                if dataset.field_keys and self.app.mesh is not None:
                    self._component.set(dataset.field_keys[0])
                    self._show()
                return
        self.app.active_job_id = job_id
        self.app.solution = solution
        self.app.shape_index = 0
        self.app.show_results()
        self.app.refresh_panels()

    def _refresh_quantities(self, solution) -> None:
        self._quantities.delete(*self._quantities.get_children())
        if hasattr(solution, "field_keys") and hasattr(solution, "field"):
            for key in solution.field_keys:
                descriptor = solution.field(key).descriptor
                self._quantities.insert(
                    "",
                    "end",
                    iid=f"artifact:{key}",
                    text=descriptor.label,
                    values=(descriptor.location, descriptor.unit),
                )
            return
        raw = getattr(solution, "info", {}).get("raw")
        candidates = [raw, getattr(raw, "nonlinear_result", None)]
        descriptors = ()
        for candidate in candidates:
            describe = getattr(candidate, "quantity_metadata", None)
            if callable(describe):
                descriptors = describe()
                if descriptors:
                    break
        if not descriptors:
            fields = getattr(solution, "available_fields", None)
            values = fields() if callable(fields) else available_fields()
            for key in values:
                self._quantities.insert(
                    "", "end", iid=f"field:{key}", text=str(key), values=("derived", "")
                )
            return
        for descriptor in descriptors:
            identifier = str(getattr(descriptor, "quantity_id", "quantity"))
            key = identifier
            suffix = 2
            while self._quantities.exists(key):
                key = f"{identifier}:{suffix}"
                suffix += 1
            self._quantities.insert(
                "", "end", iid=key,
                text=str(getattr(descriptor, "label", identifier)),
                values=(
                    str(getattr(descriptor, "location", "")),
                    str(getattr(descriptor, "unit", "")),
                ),
            )
        available = getattr(solution, "available_fields", None)
        dynamic = tuple(available()) if callable(available) else ()
        if "equivalent_plastic_strain" in dynamic:
            self._quantities.insert(
                "", "end", iid="field:equivalent_plastic_strain",
                text="Equivalent plastic strain (PEEQ)",
                values=("element", "1"),
            )

    def _activate_quantity(self) -> None:
        selected = self._quantities.selection()
        if not selected:
            return
        quantity = selected[0].split(":", 1)[0]
        if quantity == "artifact":
            self._component.set(selected[0].split(":", 1)[1])
            self._show()
            return
        mapping = {
            "displacement": "magnitude",
            "mode_shape": "magnitude",
            "stress": "von_mises",
            "field": selected[0].split(":", 1)[-1],
        }
        field = mapping.get(quantity)
        values = tuple(self._field_box.cget("values"))
        if field in values:
            self._component.set(field)
            self._show()

    # ------------------------------------------------------------------
    def _shape_count(self) -> int:
        if self.app.solution is None and self.app.active_job_id is not None:
            dataset = self.app.result_datasets.get(self.app.active_job_id)
            if dataset is not None:
                return max(1, len(dataset.frames))
        shapes = getattr(self.app.solution, "shapes", None)
        return len(shapes) if shapes else 1

    def _pick_shape(self) -> None:
        values = list(self._shape_box.cget("values"))
        if self._shape.get() in values:
            self.app.shape_index = values.index(self._shape.get())
            if self.app.solution is None:
                self._show()
            else:
                self.app.show_results()
            self.app.refresh_panels()

    def _step(self, delta: int) -> None:
        if (
            self.app.solution is None
            and self.app.active_job_id not in self.app.result_datasets
        ):
            raise ValueError("run a solve first")
        count = self._shape_count()
        self.app.shape_index = (self.app.shape_index + delta) % count
        if self.app.solution is None:
            self._show()
        else:
            self.app.show_results()
        self.app.refresh_panels()

    def _previous(self) -> None:
        self._step(-1)

    def _next(self) -> None:
        self._step(1)

    def _animate(self) -> None:
        """Play available frames with Tk timers; unavailable frames stay absent."""

        if (
            self.app.solution is None
            and self.app.active_job_id not in self.app.result_datasets
        ):
            raise ValueError("run a solve first")
        count = self._shape_count()
        if count < 2:
            raise ValueError("this result has only one shape to show")
        if self._animation_after is not None:
            self.after_cancel(self._animation_after)
            self._animation_after = None
            self._play_button.configure(text="▶ Play")
            self.app.set_status("playback stopped")
            return
        self._animation_index = (
            0 if self.app.shape_index + 1 >= count else self.app.shape_index + 1
        )
        self._play_button.configure(text="■ Stop")
        self.app.set_status(f"playback started at {self.playback_fps():g} fps")
        self._animation_step()

    def playback_fps(self) -> float:
        """Selected visual playback rate; result time/load values stay unchanged."""

        value = float(self._playback_fps.get())
        if not np.isfinite(value) or value <= 0.0:
            raise ValueError("playback speed must be a positive number of frames/s")
        return value

    def _playback_delay_ms(self) -> int:
        return max(1, int(round(1000.0 / self.playback_fps())))

    def _animation_step(self) -> None:
        count = self._shape_count()
        if self._animation_index >= count:
            self._animation_after = None
            self._play_button.configure(text="▶ Play")
            self.app.refresh_panels()
            return
        self.app.shape_index = self._animation_index
        self._animation_index += 1
        values = list(self._shape_box.cget("values"))
        if self.app.shape_index < len(values):
            self._shape.set(values[self.app.shape_index])
        self._update_frame_details()
        if self.app.solution is None:
            self._show()
        else:
            self.app.show_results()
        self._animation_after = self.after(
            self._playback_delay_ms(), self._animation_step
        )

    def scale_value(self, solution) -> float:
        text = self._scale.get().strip().lower()
        if text in ("", "auto"):
            try:
                _node, magnitude = solution.max_translation()
            except KeyError:
                return 0.0
            if magnitude <= 0.0:
                return 1.0
            from .scene import build_mesh_scene

            span = build_mesh_scene(
                self.app.project, solution.built.mesh
            ).characteristic_size()
            return 0.08 * span / magnitude
        return self.number(self._scale, "deform scale")

    def component(self) -> str:
        return self._component.get()

    def _show(self) -> None:
        if self.app.solution is None:
            if self.app.active_job_id not in self.app.result_datasets:
                raise ValueError("run a solve first")
            dataset = self.app.result_datasets[self.app.active_job_id]
            key = self._component.get()
            stored = dataset.field(key)
            descriptor = stored.descriptor
            if descriptor.location in ("global", "history"):
                frame = min(max(self.app.shape_index, 0), stored.shape[0] - 1)
                value_scale, display_unit = unit_transform(
                    descriptor.unit, self.display_units()
                )
                values = np.asarray(stored.read(frame)) * value_scale
                self.write(
                    f"{descriptor.label} [{display_unit}]\n"
                    + np.array2string(values, precision=7, threshold=200)
                )
                self.app.set_status(f"showing {descriptor.label} as a result table")
                return
            scale_text = self._scale.get().strip().lower()
            scale = (
                1.0
                if scale_text in ("", "auto")
                else self.number(self._scale, "deform scale")
            )
            self.app.show_persisted_result(
                key,
                frame=self.app.shape_index,
                scale=scale,
                limits=self.colour_limits(),
            )
            self.app.details.set_hint("Result contour")
            return
        self.app.show_results()
        self.app.details.set_hint("Result contour")

    def destroy(self) -> None:
        """Cancel panel-owned timers before Tcl discards their callbacks."""

        self._gif_export = None
        for attribute in ("_animation_after", "_gif_poll_after"):
            identifier = getattr(self, attribute, None)
            if identifier is not None:
                try:
                    self.after_cancel(identifier)
                except tk.TclError:
                    pass
                setattr(self, attribute, None)
        super().destroy()
