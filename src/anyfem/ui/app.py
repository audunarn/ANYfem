"""The ANYfem application window.

Layout: model tree on the left, 3D viewport in the middle, stage panels on the
right, status bar along the bottom.

The app owns the shared state -- project, command stack, selection, current
mesh and solution -- and the panels act on it.  Every model change goes through
the command stack, so the same calls are available from a script.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any, Dict, Iterable, Optional

from ..commands import Command, CommandStack
from ..io.decks import export_calculix_deck
from ..io.project_file import load_project, save_project
from ..io.results import import_calculix_results, import_sesam_results
from ..io.sesam import import_sesam
from ..solve.build import build_fe_model
from ..model.materials import steel
from ..model.project import Project
from ..selection import Selection, mode_label
from ..solve.run import (
    solve_arc_length,
    solve_buckling,
    solve_impact,
    solve_linear_static,
    solve_modal,
    solve_nonlinear_static,
    solve_transient,
)

from .panels import (
    GeometryPanel,
    LoadPanel,
    MeshPanel,
    ResultsPanel,
    SectionPanel,
    SolvePanel,
)
from .scene import (
    build_attribute_overlay,
    build_collision_overlay,
    build_geometry_scene,
    build_mesh_scene,
    build_result_scene,
)
from .tree import ModelTree
from .viewport import Viewport
from .worker import SolveWorker

__all__ = ["ANALYSES", "AnyFemApp", "main"]

# Which function each analysis name on the Solve panel runs.
ANALYSES = {
    "Linear static": solve_linear_static,
    "Modal": solve_modal,
    "Buckling": solve_buckling,
    "Nonlinear static": solve_nonlinear_static,
    "Arc length": solve_arc_length,
    "Transient": solve_transient,
    "Impact": solve_impact,
}


class AnyFemApp(ttk.Frame):
    """The main window."""

    def __init__(self, master: tk.Misc, project: Optional[Project] = None) -> None:
        super().__init__(master)
        self.project = project if project is not None else default_project()
        self.commands = CommandStack(self.project)
        self.selection = Selection(mode="vertex")
        self.mesh = None
        self.solution = None
        self.analysis = "Linear static"
        self.shape_index = 0
        self.imported = None
        self.path: Optional[Path] = None
        self.seeding_overrides: Dict[int, int] = {}
        self._view_mode = "geometry"
        self._closing = False
        self._refresh_suspended = 0

        self._build()
        self._build_menu()

        self.commands.add_listener(self.refresh_all)
        self.selection.add_listener(self._on_selection_changed)
        self.worker = SolveWorker(
            self,
            on_status=self._on_progress,
            on_done=self._on_solved,
            on_error=lambda text: self.set_status(text, error=True),
            on_state_change=self.refresh_panels,
        )

        self.refresh_all()
        self.show_geometry(reset_view=True)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        self.pack(fill="both", expand=True)

        toolbar = ttk.Frame(self, padding=(6, 4))
        toolbar.pack(fill="x")
        self._undo_button = ttk.Button(toolbar, text="Undo", command=self.undo)
        self._undo_button.pack(side="left")
        self._redo_button = ttk.Button(toolbar, text="Redo", command=self.redo)
        self._redo_button.pack(side="left", padx=(4, 12))
        for label, name in (
            ("Iso", "iso"), ("Top", "top"), ("Front", "front"), ("Side", "side")
        ):
            ttk.Button(
                toolbar, text=label, width=6,
                command=lambda n=name: self.viewport.set_view(n),
            ).pack(side="left", padx=1)
        ttk.Button(toolbar, text="Fit", width=6, command=self._fit).pack(
            side="left", padx=(8, 0)
        )
        self._show_attributes = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            toolbar,
            text="Loads & BC",
            variable=self._show_attributes,
            command=self.refresh_views,
        ).pack(side="left", padx=(12, 0))
        self._view_label = ttk.Label(toolbar, text="")
        self._view_label.pack(side="right")

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)

        self.tree = ModelTree(panes, self.project, self.selection)
        panes.add(self.tree, weight=1)

        centre = ttk.Frame(panes)
        panes.add(centre, weight=4)
        self.viewport = Viewport(centre, selection=self.selection)
        self.viewport.pack(fill="both", expand=True)
        self.viewport.set_pick_handler(self._on_pick)

        right = ttk.Frame(panes)
        panes.add(right, weight=2)
        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)

        self.panels = {}
        for panel_class in (
            GeometryPanel, MeshPanel, SectionPanel, LoadPanel, SolvePanel,
            ResultsPanel,
        ):
            panel = panel_class(self.notebook, self)
            self.notebook.add(panel, text=panel_class.title)
            self.panels[panel_class.title] = panel

        status = ttk.Frame(self, padding=(6, 3))
        status.pack(fill="x")
        self._status = ttk.Label(status, text="ready", anchor="w")
        self._status.pack(side="left", fill="x", expand=True)
        self._selection_label = ttk.Label(status, text="")
        self._selection_label.pack(side="right")

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    def run(self, command: Command) -> Any:
        """Run a command; the stack notifies everything that must refresh."""

        return self.commands.run(command)

    def run_many(self, commands: Iterable[Command]) -> list[Any]:
        """Run independent commands with one final expensive GUI refresh.

        Every command remains a separate undo step.  Only rendering, tree and
        panel refreshes are coalesced, which matters when a load or section is
        applied to hundreds of selected entities.
        """

        pending = list(commands)
        if not pending:
            return []
        self._refresh_suspended += 1
        try:
            return [self.commands.run(command) for command in pending]
        finally:
            self._refresh_suspended -= 1
            if self._refresh_suspended == 0:
                self.refresh_all()

    def undo(self) -> None:
        if self.commands.undo():
            self.set_status("undone")
        self._invalidate_downstream()

    def redo(self) -> None:
        if self.commands.redo():
            self.set_status("redone")
        self._invalidate_downstream()

    def _invalidate_downstream(self) -> None:
        """A geometry change makes any existing mesh and result stale."""

        self.mesh = None
        self.solution = None
        self.show_geometry()

    # ------------------------------------------------------------------
    # meshing and solving
    # ------------------------------------------------------------------
    def generate_mesh(self, target_size: float):
        self.mesh = self.project.generate_mesh(
            target_size, overrides=self.seeding_overrides
        )
        self.solution = None
        self.set_status(
            f"meshed: {self.mesh.num_nodes} nodes, "
            f"{self.mesh.num_elements} elements"
        )
        self.show_mesh()
        self.refresh_panels()
        return self.mesh

    def solve(self, analysis: str = "Linear static", **options: Any) -> None:
        """Run one analysis on the worker thread."""

        if self.mesh is None:
            raise ValueError("generate a mesh first")
        try:
            function = ANALYSES[analysis]
        except KeyError:
            raise ValueError(f"unknown analysis {analysis!r}") from None

        self.analysis = analysis
        if self.imported is not None:
            # An imported model is already built and has no geometry to mesh,
            # so it goes straight to the analysis.
            case = self.project.load_cases.get(self.active_case())
            options = {
                key: value
                for key, value in options.items()
                if key not in ("load_case", "combination")
            }
            started = self.worker.start(
                function,
                progress_key="progress",
                built=self.imported.built(case),
                **options,
            )
        else:
            started = self.worker.start(
                function,
                progress_key="progress",
                project=self.project,
                mesh=self.mesh,
                **options,
            )
        if not started:
            self.set_status("a solve is already running", error=True)

    def cancel_solve(self) -> None:
        self.worker.cancel()

    def _on_solved(self, solution) -> None:
        self.solution = solution
        self.shape_index = 0
        self.set_status(solution.summary())
        panel = self.panels["Solve"]
        panel.write(_solution_report(solution))
        panel.show_progress("")
        self.notebook.select(self.panels["Results"])
        self.show_results()
        self.refresh_panels()

    def current_shape(self):
        """The displacement field currently being displayed.

        A static result is its own shape; a modal, buckling or transient
        result is browsed by index.  Everything downstream sees the same
        interface either way.
        """

        solution = self.solution
        if solution is None:
            return None
        shapes = getattr(solution, "shapes", None)
        if not shapes:
            return solution
        index = min(max(self.shape_index, 0), len(shapes) - 1)
        return shapes[index]

    # ------------------------------------------------------------------
    # views
    # ------------------------------------------------------------------
    def show_geometry(self, reset_view: bool = False) -> None:
        self._view_mode = "geometry"
        scene = build_geometry_scene(self.project)
        self.viewport.show(self._with_attributes(scene), reset_view=reset_view)
        self._update_view_label()

    def show_mesh(self) -> None:
        if self.mesh is None:
            self.show_geometry()
            return
        self._view_mode = "mesh"
        scene = build_mesh_scene(self.project, self.mesh)
        self.viewport.show(self._with_attributes(scene))
        self._update_view_label()

    def _with_attributes(self, scene):
        """Overlay supports and the active case's loads, if asked for."""

        if not self._show_attributes.get():
            return scene
        return scene.merge(
            build_attribute_overlay(self.project, case_name=self.active_case())
        )

    def active_case(self) -> str:
        panel = self.panels.get("Loads & BC")
        return "default" if panel is None else panel.case_name()

    def refresh_views(self) -> None:
        """Redraw whichever view is showing."""

        if self._closing:
            return
        if self._view_mode == "results":
            self.show_results()
        elif self._view_mode == "mesh":
            self.show_mesh()
        else:
            self.show_geometry()

    def show_results(self) -> None:
        if self.solution is None:
            self.show_mesh()
            return
        panel = self.panels["Results"]
        shape = self.current_shape()
        self._view_mode = "results"
        scene = build_result_scene(
            shape,
            field=panel.field_name(),
            scale=panel.scale_value(shape),
            limits=panel.colour_limits(),
            values=panel.field_values(),
        )
        scene = self._with_attributes(scene)
        if getattr(self.solution, "sphere_positions", None) is not None:
            scene.merge(build_collision_overlay(self.solution, self.shape_index))
        self.viewport.show(scene)
        self._update_view_label()

    def _fit(self) -> None:
        self.viewport.fit()

    def _update_view_label(self) -> None:
        text = f"showing: {self._view_mode}"
        if self._view_mode == "results":
            shape = self.current_shape()
            if shape is not None and getattr(shape, "label", ""):
                text += f" - {shape.label}"
        self._view_label.configure(text=text)

    # ------------------------------------------------------------------
    # refresh
    # ------------------------------------------------------------------
    def refresh_all(self) -> None:
        if self._closing or self._refresh_suspended:
            return
        self.tree.refresh()
        self.refresh_panels()
        self._refresh_toolbar()
        if self._view_mode == "geometry":
            self.show_geometry()

    def refresh_panels(self) -> None:
        if self._closing:
            return
        for panel in self.panels.values():
            panel.refresh()
        self._refresh_toolbar()

    def _refresh_toolbar(self) -> None:
        self._undo_button.configure(
            state="normal" if self.commands.can_undo else "disabled",
            text=f"Undo {self.commands.undo_label or ''}".strip(),
        )
        self._redo_button.configure(
            state="normal" if self.commands.can_redo else "disabled",
            text=f"Redo {self.commands.redo_label or ''}".strip(),
        )

    def _on_selection_changed(self) -> None:
        if self._closing:
            return
        self._selection_label.configure(
            text=f"{mode_label(self.selection.mode)} mode - "
            f"{self.selection.describe()}"
        )
        self.refresh_panels()

    def _on_progress(self, text: str) -> None:
        self.set_status(text)
        panel = self.panels.get("Solve")
        if panel is not None:
            panel.show_progress(text)

    def _on_pick(self, ref) -> None:
        if ref is None:
            return
        self.set_status(f"selected {ref}")
        # Clicking something while results are showing reads it out: that is
        # what a probe is for, and asking for it twice would be tedious.
        if self._view_mode == "results" and self.solution is not None:
            panel = self.panels.get("Results")
            if panel is not None:
                panel.guarded(panel._probe)()

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # files
    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        root = self.winfo_toplevel()
        menu = tk.Menu(root)
        files = tk.Menu(menu, tearoff=0)
        files.add_command(label="New", command=self.guarded(self.new_project))
        files.add_command(label="Open...", command=self.guarded(self.open_project))
        files.add_command(
            label="Open file inspector...",
            command=self.guarded(self.open_file_inspector),
        )
        files.add_separator()
        files.add_command(label="Save", command=self.guarded(self.save_project))
        files.add_command(
            label="Save As...", command=self.guarded(lambda: self.save_project(True))
        )
        files.add_separator()
        files.add_command(
            label="Import SESAM...", command=self.guarded(self.import_sesam_model)
        )
        files.add_command(
            label="Export CalculiX deck...", command=self.guarded(self.export_deck)
        )
        files.add_separator()
        files.add_command(
            label="Import CalculiX results...",
            command=self.guarded(self.import_calculix_result),
        )
        files.add_command(
            label="Import SESAM results...",
            command=self.guarded(self.import_sesam_result),
        )
        menu.add_cascade(label="File", menu=files)
        try:
            root.configure(menu=menu)
        except tk.TclError:  # pragma: no cover - embedded without a toplevel
            pass
        self._menu = menu

    def guarded(self, action):
        """Turn a refusal into a status message rather than a traceback."""

        def wrapped() -> None:
            try:
                action()
            except (ValueError, KeyError, OSError) as error:
                self.set_status(str(error), error=True)

        return wrapped

    def new_project(self) -> None:
        self.project = default_project()
        self.commands = CommandStack(self.project)
        self.commands.add_listener(self.refresh_all)
        self.tree.project = self.project
        self.imported = None
        self.path = None
        self.mesh = None
        self.solution = None
        self.selection.clear()
        self.set_status("new model")
        self.refresh_all()
        self.show_geometry(reset_view=True)

    def open_file_inspector(self, path: Optional[str] = None) -> None:
        """Open ANYfileio's inspector as a child of this application."""

        from anyfileio.gui import open_inspector

        open_inspector(self.winfo_toplevel(), path=path)

    def open_project(self, path: Optional[str] = None) -> None:
        if path is None:
            path = filedialog.askopenfilename(
                filetypes=[("ANYfem project", "*.anyfem"), ("All files", "*.*")]
            )
            if not path:
                return
        loaded = load_project(path)
        self.project = loaded
        self.commands = CommandStack(self.project)
        self.commands.add_listener(self.refresh_all)
        self.tree.project = self.project
        self.imported = None
        self.path = Path(path)
        self.mesh = None
        self.solution = None
        self.selection.clear()
        self.set_status(f"opened {self.path.name}")
        self.refresh_all()
        self.show_geometry(reset_view=True)

    def save_project(self, ask: bool = False, path: Optional[str] = None) -> None:
        if self.imported is not None:
            raise ValueError(
                "an imported model has no ANYfem geometry to save; export a "
                "CalculiX deck instead"
            )
        if path is None:
            path = str(self.path) if self.path and not ask else ""
        if not path:
            path = filedialog.asksaveasfilename(
                defaultextension=".anyfem",
                filetypes=[("ANYfem project", "*.anyfem"), ("All files", "*.*")],
                initialfile=f"{self.project.name}.anyfem",
            )
            if not path:
                return
        self.path = save_project(self.project, path)
        self.set_status(f"saved {self.path.name}")

    def import_sesam_model(self, path: Optional[str] = None) -> None:
        if path is None:
            path = filedialog.askopenfilename(
                filetypes=[("SESAM FEM", "*.FEM *.fem"), ("All files", "*.*")]
            )
            if not path:
                return
        model = import_sesam(path)
        self.imported = model
        self.project = model.project()
        self.commands = CommandStack(self.project)
        self.commands.add_listener(self.refresh_all)
        self.tree.project = self.project
        self.path = None
        self.mesh = model.mesh
        self.solution = None
        self.selection.clear()
        note = (
            ""
            if not model.diagnostics
            else f"; {len(model.diagnostics)} diagnostic(s)"
        )
        self.set_status(
            f"imported {model.summary()}{note}. Geometry editing is off: an "
            "imported file has a mesh, not plates and lines."
        )
        self.refresh_all()
        self.show_mesh()
        self.viewport.fit()

    def import_calculix_result(self, path: Optional[str] = None) -> None:
        """Read a CalculiX FRD onto the current model."""

        if path is None:
            path = filedialog.askopenfilename(
                filetypes=[
                    ("CalculiX results", "*.frd *.FRD *.dat *.DAT"),
                    ("All files", "*.*"),
                ]
            )
            if not path:
                return
        self._attach_results(import_calculix_results(path))

    def import_sesam_result(self, path: Optional[str] = None) -> None:
        """Read SESAM SIF shell stresses onto the current model."""

        if path is None:
            path = filedialog.askopenfilename(
                filetypes=[("SESAM SIF", "*.SIF *.sif"), ("All files", "*.*")]
            )
            if not path:
                return
        self._attach_results(import_sesam_results(path))

    def _attach_results(self, results) -> None:
        """Bind imported results to whatever model is loaded.

        Matching is by node ID, so this needs a model that has been meshed or
        imported.  A mismatch is reported rather than partially attached.
        """

        built = self.built()
        if built is None:
            raise ValueError(
                "generate or import a mesh first: results are matched to a "
                "model by node ID, so there has to be one to match against"
            )
        self.solution = results.attach(built)
        self.set_status(results.summary())
        self.refresh_all()
        self.show_results()

    def built(self):
        """The built model behind the current mesh, if there is one."""

        if self.imported is not None:
            return self.imported
        if self.mesh is None:
            return None
        from ..solve.build import build_fe_model

        return build_fe_model(
            self.project, self.mesh,
            require_loads=False, require_supports=False,
        )

    def export_deck(self, path: Optional[str] = None) -> None:
        if self.mesh is None:
            raise ValueError("generate or import a mesh first")
        if path is None:
            path = filedialog.asksaveasfilename(
                defaultextension=".inp",
                filetypes=[("CalculiX deck", "*.inp"), ("All files", "*.*")],
                initialfile=f"{self.project.name}.inp",
            )
            if not path:
                return
        built = self.build_current()
        written = export_calculix_deck(built, path)
        self.set_status(
            f"deck written to {Path(path).name}. A generated deck is a handoff, "
            "not evidence: it says nothing until it has been run and compared."
        )
        return written

    def build_current(self):
        """The built model for whatever is loaded, imported or modelled."""

        if self.imported is not None:
            case = self.project.load_cases.get(self.active_case())
            return self.imported.built(case)
        if self.mesh is None:
            raise ValueError("generate a mesh first")
        return build_fe_model(self.project, self.mesh)

    # ------------------------------------------------------------------
    def set_status(self, text: str, error: bool = False) -> None:
        self._status.configure(
            text=text, foreground="#b00020" if error else "#222222"
        )

    def destroy(self) -> None:
        """Unhook everything before the widgets go.

        Tk destroys children in its own order, and a Treeview fires
        ``<<TreeviewSelect>>`` on the way out.  Without unhooking, that
        callback would drive a refresh into panels that no longer exist.
        """

        self._closing = True
        try:
            self.worker.stop()
            self.selection.remove_listener(self._on_selection_changed)
            self.selection.remove_listener(self.tree.sync_from_selection)
            self.selection.remove_listener(self.viewport._apply_highlight)
            self.commands.remove_listener(self.refresh_all)
        finally:
            super().destroy()


def _solution_report(solution) -> str:
    """A text summary that suits whichever analysis produced the result."""

    mesh = solution.built.mesh
    lines = [
        solution.built.project.name,
        "",
        f"nodes                {mesh.num_nodes}",
        f"shell elements       {len(mesh.shells)}",
        f"beam elements        {len(mesh.beams)}",
        "",
        solution.summary(),
    ]

    shapes = getattr(solution, "shapes", None)
    if shapes:
        lines.append("")
        for shape in shapes[:12]:
            lines.append(f"  {shape.label:<16} {shape.value:.6g}")
        if len(shapes) > 12:
            lines.append(f"  ... {len(shapes) - 12} more")

    steps = getattr(solution, "steps", None)
    if steps:
        lines.append("")
        lines.append(f"steps                {len(steps)}")
        lines.append(f"status               {solution.status}")
    return "\n".join(lines)


def default_project() -> Project:
    """A new project with one steel already defined, so nothing is empty."""

    project = Project(name="model")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    return project


def main() -> None:  # pragma: no cover - entry point
    root = tk.Tk()
    root.title("ANYfem")
    root.geometry("1500x900")
    AnyFemApp(root)
    root.mainloop()


if __name__ == "__main__":  # pragma: no cover
    main()
