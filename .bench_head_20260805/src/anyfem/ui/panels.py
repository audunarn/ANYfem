"""The stage panels: Geometry, Mesh, Loads & BC, Solve, Results.

Every panel acts through the command stack, never directly on the project, so
everything the user does here is undoable and scriptable by the same calls.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, List, Optional, Sequence

import numpy as np

from .. import commands as cmd
from ..geometry.entities import EntityRef
from ..geometry.operations import check_mappable
from ..mesh.mapped import ELEMENT_ORDERS
from ..mesh.refinement import refine_around
from ..mesh.seeding import SeedingConflict
from ..model.attributes import DOF_NAMES, Support
from ..model.imperfections import Imperfection
from ..model.materials import steel
from ..model.sections import PROFILES, BeamSection
from ..model.project import ProjectError
from ..post.extract import along_line, envelope, probe
from ..post.fields import available_fields
from ..post.history import history_series
from ..post.report import field_to_csv, write_csv, write_report
from ..selection import SELECTION_MODES, mode_label
from .plot import HistoryPlot

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
    def section(self, text: str) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(self, text=text, padding=6)
        frame.pack(fill="x", pady=(0, 8))
        return frame

    @staticmethod
    def labelled_entry(
        parent: tk.Misc, label: str, default: str = "", width: int = 10
    ) -> tuple[ttk.Frame, tk.StringVar]:
        """An entry row, returning its frame so it can be shown or hidden."""

        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=label, width=16).pack(side="left")
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

        hole = self.section("Hole")
        self._hole_centre = self.vector_row(hole, "centre")
        self._hole_radius = self.entry_row(hole, "radius [m]", "0.25")
        self.button(hole, "Punch in selected plate", self._punch)

        edit = self.section("Edit")
        self._corners = self.entry_row(edit, "corners", "0 1 2 3")
        self.button(edit, "Set plate corners", self._set_corners)
        self.button(edit, "Check selected plates", self._check)
        self.button(edit, "Delete selection", self._delete)

    def refresh(self) -> None:
        self._mode.set(self.app.selection.mode)

    def _change_mode(self) -> None:
        self.app.selection.set_mode(self._mode.get())

    def _add_point(self) -> None:
        x, y, z = self.vector(self._point, "point coordinate")
        vertex = self.app.run(cmd.AddPoint(float(x), float(y), float(z)))
        self.app.set_status(f"added point {vertex}")

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
        for ref in edges:
            self.app.run(cmd.SplitEdge(ref.id, fraction))
        self.app.selection.clear()
        self.app.set_status(f"split {len(edges)} line(s)")

    def _split_plates(self) -> None:
        faces = self.require_selection("face")
        axis = int(self._axis.get())
        fraction = self.number(self._fraction, "fraction")
        for ref in faces:
            self.app.run(cmd.SplitFace(ref.id, axis, fraction))
        self.app.selection.clear()
        self.app.set_status(f"split {len(faces)} plate(s)")

    def _strip_plates(self) -> None:
        faces = self.require_selection("face")
        axis = int(self._axis.get())
        count = int(self.number(self._strips, "strips"))
        made = 0
        for ref in faces:
            strips, _dividers = self.app.run(cmd.StripFace(ref.id, axis, count))
            made += len(strips)
        self.app.selection.clear()
        self.app.set_status(f"made {made} strip(s)")

    def _triangle(self) -> None:
        edges = self.require_selection("edge", 3)
        faces = self.app.run(
            cmd.TriangleToQuads(edge_ids=[ref.id for ref in edges])
        )
        self.app.selection.clear()
        self.app.set_status(f"three-sided region became {len(faces)} plates")

    def _punch(self) -> None:
        faces = self.require_selection("face", 1)
        centre = self.vector(self._hole_centre, "hole centre")
        radius = self.number(self._hole_radius, "hole radius")
        patches, _arcs = self.app.run(
            cmd.PunchHole(faces[0].id, tuple(centre), radius)
        )
        self.app.selection.clear()
        self.app.set_status(f"hole punched; plate became {len(patches)} patches")

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
        for ref in items:
            self.app.run(cmd.DeleteEntity(ref))
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
        self.button(controls, "Generate mesh", self._generate)

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

        mesh = self.app.mesh
        if mesh is None:
            self._stats.configure(text="no mesh")
        else:
            shells = "8-node" if mesh.is_quadratic else "4-node"
            beams = "3-node" if mesh.is_quadratic else "2-node"
            self._stats.configure(
                text=(
                    f"{mesh.num_nodes} nodes\n"
                    f"{len(mesh.quads)} {shells} shell elements\n"
                    f"{len(mesh.beams)} {beams} beam elements"
                )
            )

    def _generate(self) -> None:
        size = self.number(self._size, "element size")
        if size <= 0:
            raise ValueError("element size must be positive")
        if self._order.get() != self.app.project.element_order:
            self.app.stack.run(cmd.SetElementOrder(order=self._order.get()))
        self.app.generate_mesh(size)

    def _refine(self) -> None:
        """Refine around whatever is selected, whatever kind it is."""

        selection = list(self.app.selection)
        if not selection:
            raise ValueError(
                "select a point, line or plate to refine around first"
            )
        size = self.number(self._refine_size, "element size")
        radius = self.number(self._refine_radius, "radius")
        for ref in selection:
            self.app.stack.run(
                cmd.AddRefinement(refinement=refine_around(ref, size, radius))
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
        material = self.section("Material")
        self._grade = self.entry_row(material, "grade", "S355")
        self._grade_thickness = self.entry_row(material, "thickness [mm]", "10")
        self.button(material, "Add steel", self._add_material)

        plate = self.section("Plate section")
        self._plate_name = self.entry_row(plate, "name", "plate")
        self._plate_thickness = self.entry_row(plate, "thickness [mm]", "10")
        self._plate_material = self.entry_row(plate, "material", "S355")
        self.button(plate, "Add section", self._add_plate_section)
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
        self._beam_material = self.entry_row(beam, "material", "S355")
        self.button(beam, "Add section", self._add_beam_section)
        self.button(beam, "Assign to selected lines", self._assign_beam)

        imperfection = self.section("Imperfection")
        self._imperfection_amplitude = self.entry_row(
            imperfection, "amplitude [mm]", "auto"
        )
        self._waves = self.entry_row(imperfection, "waves", "1 1")
        self.button(imperfection, "Add to selection", self._add_imperfection)
        self._imperfection_label = ttk.Label(self, text="", foreground="#666666")
        self._imperfection_label.pack(anchor="w")

    def refresh(self) -> None:
        count = len(self.app.project.imperfections)
        self._imperfection_label.configure(
            text="no imperfections" if not count else f"{count} imperfection(s)"
        )

    def _add_material(self) -> None:
        thickness = self.number(self._grade_thickness, "thickness") / 1000.0
        material = steel(self._grade.get().strip(), thickness)
        self.app.project.add_material(material)
        self.app.set_status(f"added material {material.name}")
        self.app.refresh_all()

    def _add_plate_section(self) -> None:
        self.app.project.add_plate_section(
            self._plate_name.get().strip(),
            self.number(self._plate_thickness, "thickness") / 1000.0,
            self._plate_material.get().strip(),
        )
        self.app.set_status(f"added plate section {self._plate_name.get()}")
        self.app.refresh_all()

    def _assign_plate(self) -> None:
        faces = self.require_selection("face")
        name = self._plate_name.get().strip()
        for ref in faces:
            self.app.run(cmd.AssignPlate(ref.id, name))
        self.app.set_status(f"assigned {name} to {len(faces)} plate(s)")

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
        for ref in edges:
            self.app.run(cmd.AssignBeam(ref.id, name))
        self.app.set_status(f"assigned {name} to {len(edges)} line(s)")

    def _add_imperfection(self) -> None:
        items = self.require_selection(self.app.selection.mode)
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

        for ref in items:
            self.app.run(
                cmd.AddImperfection(
                    Imperfection(ref=ref, amplitude=amplitude, waves=waves)
                )
            )
        self.app.set_status(f"imperfection on {len(items)} entity(ies)")


class LoadPanel(StagePanel):
    title = "Loads & BC"

    def build(self) -> None:
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
        grid = ttk.Frame(support)
        grid.pack(fill="x")
        self._dofs = {}
        self._values = {}
        for index, dof in enumerate(DOF_NAMES):
            held = tk.BooleanVar(value=dof in ("ux", "uy", "uz"))
            ttk.Checkbutton(grid, text=dof, variable=held).grid(
                row=index // 3, column=index % 3, sticky="w", padx=2
            )
            self._dofs[dof] = held
        self._prescribed = self.entry_row(support, "value [mm]", "0")
        self.button(support, "Apply to selection", self._add_support)

        load = self.section("Load")
        self._pressure = self.entry_row(load, "pressure [Pa]", "10000")
        self.button(load, "Pressure on plates", self._add_pressure)
        self._force = self.vector_row(load, "force [N]", ("0", "0", "-1000"))
        self.button(load, "Point load on points", self._add_point_load)
        self._line = self.vector_row(load, "line load [N/m]", ("0", "0", "-1000"))
        self.button(load, "Line load on lines", self._add_line_load)
        self._traction = self.vector_row(load, "traction [Pa]", ("0", "0", "-1000"))
        self.button(load, "Traction on plates", self._add_traction)

        body = self.section("Body load")
        self._acceleration = self.vector_row(
            body, "accel [m/s2]", ("0", "0", "-9.81")
        )
        self.button(body, "Apply to case", self._set_acceleration)
        self._mass = self.entry_row(body, "mass [kg]", "100")
        self.button(body, "Mass on selection", self._add_mass)

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

    def _add_support(self) -> None:
        items = self.require_selection(self.app.selection.mode)
        value = self.number(self._prescribed, "value") / 1000.0
        constraints = {
            dof: value for dof, held in self._dofs.items() if held.get()
        }
        if not constraints:
            raise ValueError("tick at least one degree of freedom")
        for ref in items:
            self.app.run(
                cmd.AddSupport(
                    Support(
                        name=f"support_{ref}", ref=ref, constraints=dict(constraints)
                    )
                )
            )
        what = "prescribed" if value else "supported"
        self.app.set_status(f"{what} {len(items)} entity(ies)")

    def _add_pressure(self) -> None:
        faces = self.require_selection("face")
        value = self.number(self._pressure, "pressure")
        for ref in faces:
            self.app.run(cmd.AddPressure(ref, value, case=self.case_name()))
        self.app.set_status(f"pressure on {len(faces)} plate(s)")

    def _add_point_load(self) -> None:
        points = self.require_selection("vertex")
        force = self.vector(self._force, "force")
        for ref in points:
            self.app.run(
                cmd.AddPointLoad(ref, tuple(force), case=self.case_name())
            )
        self.app.set_status(f"point load on {len(points)} point(s)")

    def _add_line_load(self) -> None:
        edges = self.require_selection("edge")
        intensity = self.vector(self._line, "line load")
        for ref in edges:
            self.app.run(
                cmd.AddLineLoad(ref, tuple(intensity), case=self.case_name())
            )
        self.app.set_status(f"line load on {len(edges)} line(s)")

    def _add_traction(self) -> None:
        faces = self.require_selection("face")
        traction = self.vector(self._traction, "traction")
        for ref in faces:
            self.app.run(
                cmd.AddSurfaceTraction(ref, tuple(traction), case=self.case_name())
            )
        self.app.set_status(f"traction on {len(faces)} plate(s)")

    def _set_acceleration(self) -> None:
        vector = self.vector(self._acceleration, "acceleration")
        self.app.run(cmd.SetAcceleration(tuple(vector), case=self.case_name()))
        self.app.set_status(f"acceleration on case {self.case_name()}")

    def _add_mass(self) -> None:
        items = self.require_selection(self.app.selection.mode)
        value = self.number(self._mass, "mass")
        for ref in items:
            self.app.run(cmd.AddMass(ref, value, name=f"mass_{ref}"))
        self.app.set_status(f"mass on {len(items)} entity(ies)")

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
        "Modal": ("modes",),
        "Buckling": ("modes",),
        "Nonlinear static": ("steps", "factor"),
        "Arc length": ("arc_steps",),
        "Transient": ("dt", "t_end", "damping"),
        "Impact": ("mass", "radius", "speed", "start", "direction"),
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
        analysis_box.bind("<<ComboboxSelected>>", lambda _e: self._show_options())

        row = ttk.Frame(controls)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text="loads", width=16).pack(side="left")
        self._target = tk.StringVar(value="case: default")
        self._target_box = ttk.Combobox(
            row, textvariable=self._target, values=["case: default"],
            state="readonly", width=16,
        )
        self._target_box.pack(side="left", fill="x", expand=True)

        options = self.section("Options")
        self._options = {}
        self._option_frames = {}
        for name, label, default in (
            ("modes", "modes", "6"),
            ("steps", "load steps", "10"),
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
        ):
            frame, variable = self.labelled_entry(options, label, default)
            self._options[name] = variable
            self._option_frames[name] = frame

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

        self._report = tk.Text(self, height=12, wrap="word", state="disabled")
        self._report.pack(fill="both", expand=True, pady=(6, 0))

        self._show_options()

    # ------------------------------------------------------------------
    def _show_options(self) -> None:
        wanted = set(self.ANALYSES.get(self._analysis.get(), ()))
        for name, frame in self._option_frames.items():
            if name in wanted:
                frame.pack(fill="x", pady=1)
            else:
                frame.pack_forget()

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

    def show_progress(self, text: str) -> None:
        self._progress.configure(text=text)

    def write(self, text: str) -> None:
        self._report.configure(state="normal")
        self._report.delete("1.0", "end")
        self._report.insert("1.0", text)
        self._report.configure(state="disabled")

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
            kwargs = {"num_modes": int(self.number(self._options["modes"], "modes"))}
        elif analysis == "Buckling":
            kwargs["num_modes"] = int(self.number(self._options["modes"], "modes"))
        elif analysis == "Nonlinear static":
            kwargs["num_steps"] = int(self.number(self._options["steps"], "load steps"))
            kwargs["max_load_factor"] = self.number(
                self._options["factor"], "max load factor"
            )
        elif analysis == "Arc length":
            from anysolver import ArcLengthControl

            kwargs["control"] = ArcLengthControl(
                max_steps=int(self.number(self._options["arc_steps"], "max arc steps"))
            )
        elif analysis == "Transient":
            kwargs["dt"] = self.number(self._options["dt"], "time step")
            kwargs["t_end"] = self.number(self._options["t_end"], "duration")
            kwargs["rayleigh_alpha"] = self.number(
                self._options["damping"], "Rayleigh alpha"
            )
        elif analysis == "Impact":
            from ..model.collision import Collision

            # An impact does not need a load case; the sphere is the load.
            kwargs.pop("combination", None)
            kwargs["load_case"] = None
            kwargs["collision"] = Collision(
                mass=self.number(self._options["mass"], "sphere mass"),
                radius=self.number(self._options["radius"], "sphere radius"),
                speed=self.number(self._options["speed"], "speed"),
                start=self._triple(self._options["start"], "start"),
                direction=self._triple(self._options["direction"], "direction"),
            )

        self.app.solve(analysis, **kwargs)


# ----------------------------------------------------------------------
class ResultsPanel(StagePanel):
    title = "Results"

    def build(self) -> None:
        controls = self.section("Display")
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
        self._envelope = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            controls,
            text="envelope over every shape",
            variable=self._envelope,
            command=self.guarded(self._show),
        ).pack(anchor="w")
        self.button(controls, "Show", self._show)
        self.button(controls, "Back to mesh", self.app.show_mesh)

        browse = self.section("Shape")
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
        self.button(browse, "Animate", self._animate)

        query = self.section("Query")
        self.button(query, "Probe selection", self._probe)
        self.button(query, "Along selected line", self._along_line)
        self.button(query, "Export report", self._export_report)
        self.button(query, "Export field as CSV", self._export_field)

        history = self.section("History")
        self.plot = HistoryPlot(history, width=300, height=180)
        self.plot.pack(fill="both", expand=True)
        self.button(history, "Plot at selected point", self._plot_at_selection)

        self._summary = ttk.Label(self, text="no results", justify="left")
        self._summary.pack(anchor="w")

        self._readout = tk.Text(self, height=12, wrap="none", state="disabled")
        self._readout.pack(fill="both", expand=True, pady=(6, 0))

    # ------------------------------------------------------------------
    def field_name(self) -> str:
        return self._component.get()

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

    # ------------------------------------------------------------------
    def _require_solution(self):
        if self.app.solution is None:
            raise ValueError("run a solve first")
        return self.app.current_shape()

    def _probe(self) -> None:
        shape = self._require_solution()
        items = self.require_selection(self.app.selection.mode)
        readouts = [probe(shape, ref).text() for ref in items]
        self.write((chr(10) * 2).join(readouts))
        self.app.set_status(f"probed {len(items)} entity(ies)")

    def _along_line(self) -> None:
        shape = self._require_solution()
        edges = self.require_selection("edge", 1)
        result = along_line(shape, edges[0], self.field_name())
        lines = [
            f"{self.field_name()} along {edges[0]}  ({result.length:.4g} m)",
            f"{'distance':>12}  {'value':>14}",
        ]
        for distance, value in zip(result.distances, result.values):
            lines.append(f"{distance:12.5g}  {value:14.6g}")
        self.write(chr(10).join(lines))
        self.app.set_status(f"sampled {len(result)} points along {edges[0]}")

    def _export_report(self) -> None:
        solution = self.app.solution
        if solution is None:
            raise ValueError("run a solve first")
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("All files", "*.*")],
            initialfile=f"{self.app.project.name}_report.md",
        )
        if not path:
            return
        written = write_report(solution, path)
        self.app.set_status(f"report written to {written}")

    def _export_field(self) -> None:
        shape = self._require_solution()
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
            initialfile=f"{self.app.project.name}_{self.field_name()}.csv",
        )
        if not path:
            return
        written = write_csv(field_to_csv(shape, self.field_name()), path)
        self.app.set_status(f"field written to {written}")

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
        self.plot.show(series)
        self.app.set_status(f"plotting at node {node}")

    def refresh(self) -> None:
        solution = self.app.solution
        if solution is None:
            self._summary.configure(text="no results")
            self._shape_box.configure(values=["-"])
            self._shape.set("-")
            self.plot.clear()
            return

        # The plot follows the result: a transient or an incremental solve has
        # a history, a linear static does not, and an empty plot says so rather
        # than keeping the previous result's curve on screen.
        self.plot.show(history_series(solution))

        # An imported result names its own fields, which are the file's, not
        # ANYfem's list.
        available = getattr(solution, "available_fields", None)
        self._field_box.configure(
            values=available() if callable(available) else available_fields()
        )

        shapes = getattr(solution, "shapes", None)
        if shapes:
            labels = [f"{shape.label}  ({shape.value:.5g})" for shape in shapes]
            self._shape_box.configure(values=labels)
            index = min(self.app.shape_index, len(labels) - 1)
            self._shape.set(labels[index])
        else:
            self._shape_box.configure(values=["static"])
            self._shape.set("static")

        shape = self.app.current_shape()
        node, magnitude = shape.max_translation()
        self._summary.configure(
            text=(
                f"{solution.summary()}\n"
                f"showing {getattr(shape, 'label', 'static')}\n"
                f"max translation {magnitude:.6g} m at node {node}"
            )
        )

    # ------------------------------------------------------------------
    def _shape_count(self) -> int:
        shapes = getattr(self.app.solution, "shapes", None)
        return len(shapes) if shapes else 1

    def _pick_shape(self) -> None:
        values = list(self._shape_box.cget("values"))
        if self._shape.get() in values:
            self.app.shape_index = values.index(self._shape.get())
            self.app.show_results()
            self.app.refresh_panels()

    def _step(self, delta: int) -> None:
        if self.app.solution is None:
            raise ValueError("run a solve first")
        count = self._shape_count()
        self.app.shape_index = (self.app.shape_index + delta) % count
        self.app.show_results()
        self.app.refresh_panels()

    def _previous(self) -> None:
        self._step(-1)

    def _next(self) -> None:
        self._step(1)

    def _animate(self) -> None:
        """Step through every shape once, letting Tk redraw between frames."""

        if self.app.solution is None:
            raise ValueError("run a solve first")
        count = self._shape_count()
        if count < 2:
            raise ValueError("this result has only one shape to show")
        for index in range(count):
            self.app.shape_index = index
            self.app.show_results()
            self.app.update_idletasks()
        self.app.refresh_panels()

    def scale_value(self, solution) -> float:
        text = self._scale.get().strip().lower()
        if text in ("", "auto"):
            _node, magnitude = solution.max_translation()
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
            raise ValueError("run a solve first")
        self.app.show_results()
