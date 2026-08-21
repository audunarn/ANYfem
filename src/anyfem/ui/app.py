"""The ANYfem application window.

Layout: model tree on the left, 3D viewport in the middle, stage panels on the
right, status bar along the bottom.

The app owns the shared state -- project, command stack, selection, current
mesh and solution -- and the panels act on it.  Every model change goes through
the command stack, so the same calls are available from a script.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Dict, Iterable, Optional

from ..commands import Command, CommandStack
from ..document import DocumentSession, canonical_hash
from ..jobs import JobManager, analysis_hash
from ..mesh_jobs import (
    MeshJobResult,
    MeshSettings,
    MeshTaskManager,
    mesh_semantic_hash,
)
from ..io.decks import export_calculix_deck
from ..io.project_file import (
    load_project, project_from_dict, project_to_dict, save_project,
)
from ..io.recovery import (
    ProjectLock, discover_recoveries, load_recovery, write_autosave,
)
from ..io.results import import_calculix_results, import_sesam_results
from ..io.result_artifact import write_solution_artifact
from ..io.sesam import import_sesam
from ..solve.build import build_fe_model
from ..model.materials import steel
from ..model.project import Project, ProjectError
from ..model.records import AnalysisDefinition, MeshRecord
from ..selection import MeshEntityRef, Selection, mode_label
from ..solve.run import (
    solve_arc_length,
    solve_buckling,
    solve_capacity,
    solve_impact,
    solve_linear_static,
    solve_linear_static_many,
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
from .definitions import DefinitionsPanel
from .scene import (
    build_attribute_overlay,
    build_collision_overlay,
    build_geometry_scene,
    build_mesh_scene,
    build_persisted_result_scene,
    build_result_scene,
)
from .tree import ModelTree
from .viewport import Viewport
from .worker import JobWorkerFacade
from .workspace import DetailsWorkspace, JobStatusView, SelectionStrip
from .scripting import ScriptingPanel

__all__ = ["ANALYSES", "AnyFemApp", "main"]

# Which function each analysis name on the Solve panel runs.
ANALYSES = {
    "Linear static": solve_linear_static,
    "Batch linear static": solve_linear_static_many,
    "Modal": solve_modal,
    "Buckling": solve_buckling,
    "Nonlinear static": solve_nonlinear_static,
    "Arc length": solve_arc_length,
    "Transient": solve_transient,
    "Impact": solve_impact,
    "Capacity": solve_capacity,
}

_RENDERER_LABELS = {
    "auto": "Automatic",
    "gpu": "GPU",
    "software": "Tk",
}
_RENDERER_BACKENDS = {label: backend for backend, label in _RENDERER_LABELS.items()}


class AnyFemApp(ttk.Frame):
    """The main window."""

    def __init__(
        self,
        master: tk.Misc,
        project: Optional[Project] = None,
        *,
        viewer_backend: str = "auto",
    ) -> None:
        super().__init__(master)
        normalized_backend = str(viewer_backend).strip().casefold()
        normalized_backend = {"automatic": "auto", "tk": "software"}.get(
            normalized_backend, normalized_backend
        )
        if normalized_backend not in _RENDERER_LABELS:
            raise ValueError("viewer_backend must be 'auto', 'gpu', or 'software'")
        self._viewer_backend = normalized_backend
        self.project = project if project is not None else default_project()
        self.selection = Selection(mode="vertex")
        self.session = DocumentSession(self.project, selection=self.selection)
        self.commands = self.session.commands
        self._active_model_hash = self.session.revision.model_hash
        self.job_manager = JobManager(self.project)
        self.mesh_task_manager = MeshTaskManager()
        self.mesh = None
        self._meshes: Dict[str, Any] = {}
        self.solution = None
        self.solutions: Dict[str, Any] = {}
        self.result_datasets: Dict[str, Any] = {}
        self.submitted_input_reports: Dict[str, str] = {}
        self._artifact_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="anyfem-artifact"
        )
        self._recovery_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="anyfem-recovery"
        )
        self._artifact_futures: Dict[str, Future] = {}
        self._artifact_destinations: Dict[str, Path] = {}
        self._log_futures: Dict[str, Future] = {}
        self._log_destinations: Dict[str, Path] = {}
        self._active_mesh_task_id: Optional[str] = None
        self._mesh_details_record_id: Optional[str] = None
        self.active_job_id: Optional[str] = None
        self.analysis = "Linear static"
        self.shape_index = 0
        self.imported = None
        self.path: Optional[Path] = None
        self.seeding_overrides: Dict[int, int] = {}
        self._view_mode = "geometry"
        self._geometry_selection_mode = "vertex"
        self._closing = False
        self._refresh_suspended = 0
        self._project_lock: ProjectLock | None = None
        self._root_bindings: list[tuple[str, str]] = []
        self._autosave_after = None
        self._autosave_hard_after = None
        self._recovery_future: Future | None = None
        self._recovery_future_epoch = 0
        self._recovery_pending: tuple[int, dict[str, Any], dict[str, Any]] | None = None
        self._recovery_epoch = 0
        self._recent_paths = self._load_recent_paths()

        self._build()
        self._build_menu()

        self.commands.add_listener(self.refresh_all)
        self.session.add_listener(self._on_revision_changed)
        self.selection.add_listener(self._on_selection_changed)
        self.worker = JobWorkerFacade(self.job_manager)
        self._job_poll = self.after(self.worker.POLL_MS, self._poll_jobs)

        self.refresh_all()
        self.show_geometry(reset_view=True)
        self._update_window_title()
        try:
            self.winfo_toplevel().protocol("WM_DELETE_WINDOW", self.request_close)
            self.winfo_toplevel().bind("<Control-p>", self.show_command_palette)
            self.winfo_toplevel().bind("<Control-P>", self.show_command_palette)
            self._bind_root_shortcut("<Control-z>", self._undo_shortcut)
            self._bind_root_shortcut("<Control-Z>", self._undo_shortcut)
            self._bind_root_shortcut("<Control-y>", self._redo_shortcut)
            self._bind_root_shortcut("<Control-Y>", self._redo_shortcut)
        except tk.TclError:  # pragma: no cover - embedded frame
            pass

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
        ttk.Label(toolbar, text="Renderer").pack(side="left", padx=(12, 3))
        self._renderer_choice = tk.StringVar(
            value=_RENDERER_LABELS[self._viewer_backend]
        )
        self._renderer_selector = ttk.Combobox(
            toolbar,
            textvariable=self._renderer_choice,
            values=tuple(_RENDERER_BACKENDS),
            width=10,
            state="readonly",
        )
        self._renderer_selector.pack(side="left")
        self._renderer_selector.bind(
            "<<ComboboxSelected>>", self._on_renderer_selected, add="+"
        )
        self._renderer_active_label = ttk.Label(toolbar, text="")
        self._renderer_active_label.pack(side="left", padx=(3, 0))
        self._show_attributes = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            toolbar,
            text="Attributes / imperfections",
            variable=self._show_attributes,
            command=self.refresh_views,
        ).pack(side="left", padx=(12, 0))
        ttk.Separator(toolbar, orient="vertical").pack(
            side="left", fill="y", padx=8
        )
        for label, page in (
            ("Model", "Geometry"), ("Define", "Definitions"), ("Mesh", "Mesh"),
            ("Assign", "Sections"), ("Load/BC", "Loads & BC"),
            ("Run", "Solve"), ("Inspect", "Results"),
            ("Script", "Scripting"),
        ):
            ttk.Button(
                toolbar,
                text=label,
                command=lambda value=page: self.details.select(value),
            ).pack(side="left", padx=1)
        ttk.Button(
            toolbar, text="Commands...", command=self.show_command_palette
        ).pack(side="left", padx=(8, 0))
        self._view_label = ttk.Label(toolbar, text="")
        self._view_label.pack(side="right")

        self.selection_strip = SelectionStrip(self, self)
        self.selection_strip.pack(fill="x")

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)

        self.tree = ModelTree(
            panes,
            self.project,
            self.selection,
            job_is_stale=self._job_is_stale,
            mesh_is_stale=lambda record: self.mesh_record_state(record) == "stale",
        )
        self.tree.set_action_handler(self._tree_action)
        panes.add(self.tree, weight=1)

        centre = ttk.Frame(panes)
        panes.add(centre, weight=4)
        self.viewport = Viewport(
            centre,
            selection=self.selection,
            backend=self._viewer_backend,
        )
        self.viewport.pack(fill="both", expand=True)
        self.viewport.set_pick_handler(self._on_pick)
        self.viewport.bind_event("<Control-a>", self._select_all)
        self.viewport.bind_event("<Control-A>", self._select_all)
        self.viewport.bind_event(
            "<KeyPress-f>", lambda _event: self.viewport.frame_selection()
        )
        self.viewport.bind_event(
            "<KeyPress-F>", lambda _event: self.viewport.frame_selection()
        )
        self.viewport.bind_event("<Delete>", self._delete_selection)
        self.viewport.bind_event("<Escape>", lambda _event: self.selection.clear())
        self._update_renderer_label()

        right = ttk.Frame(panes)
        panes.add(right, weight=2)
        self.details = DetailsWorkspace(right)
        self.details.pack(fill="both", expand=True)
        # Backward-compatible attribute for integrations that selected a
        # notebook page.  The object now implements the same ``select`` call
        # while using a persistent Details task workspace.
        self.notebook = self.details

        self.panels = {}
        for panel_class in (
            GeometryPanel, DefinitionsPanel, MeshPanel, SectionPanel, LoadPanel, SolvePanel,
            ResultsPanel, ScriptingPanel,
        ):
            panel = panel_class(self.details._content, self)
            self.details.add(panel, text=panel_class.title)
            self.panels[panel_class.title] = panel
        self.details.set_select_handler(self._on_details_page_selected)

        self.job_status = JobStatusView(centre, self)
        self.job_status.pack(fill="x")

        status = ttk.Frame(self, padding=(6, 3))
        status.pack(fill="x")
        self._status = ttk.Label(status, text="ready", anchor="w")
        self._status.pack(side="left", fill="x", expand=True)
        self._selection_label = ttk.Label(status, text="")
        self._selection_label.pack(side="right")
        if self.viewport.backend_diagnostics:
            self.set_status(
                "renderer: Tk fallback; "
                + "; ".join(self.viewport.backend_diagnostics)
            )

    @property
    def requested_viewer_backend(self) -> str:
        return self.viewport.requested_backend

    @property
    def active_viewer_backend(self) -> str:
        return self.viewport.active_backend

    @property
    def viewer_backend_diagnostics(self) -> tuple[str, ...]:
        return self.viewport.backend_diagnostics

    def _update_renderer_label(self) -> None:
        active = "GPU" if self.viewport.active_backend == "gpu" else "Tk"
        self._renderer_active_label.configure(text=f"({active})")

    def switch_viewer_backend(self, backend: str) -> str:
        """Switch renderers without changing application or project state."""

        active = self.viewport.switch_backend(backend)
        self._viewer_backend = self.viewport.requested_backend
        self._renderer_choice.set(_RENDERER_LABELS[self._viewer_backend])
        self._update_renderer_label()
        diagnostics = self.viewport.backend_diagnostics
        detail = f"; {'; '.join(diagnostics)}" if diagnostics else ""
        self.set_status(f"renderer: {'GPU' if active == 'gpu' else 'Tk'}{detail}")
        return active

    def _on_renderer_selected(self, _event: tk.Event | None = None) -> None:
        previous = self.viewport.requested_backend
        requested = _RENDERER_BACKENDS[self._renderer_choice.get()]
        try:
            self.switch_viewer_backend(requested)
        except Exception as error:
            self._renderer_choice.set(_RENDERER_LABELS[previous])
            diagnostics = tuple(getattr(error, "diagnostics", ()))
            detail = "; ".join(str(item) for item in diagnostics if item)
            message = str(error) + (f"\n\n{detail}" if detail else "")
            self.set_status(f"renderer switch failed: {str(error)}", error=True)
            messagebox.showerror(
                "Renderer unavailable",
                message,
                parent=self.winfo_toplevel(),
            )

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    def run(self, command: Command) -> Any:
        """Run a command; the stack notifies everything that must refresh."""

        return self.session.execute(command)

    def run_many(self, commands: Iterable[Command]) -> list[Any]:
        """Run independent commands with one final expensive GUI refresh.

        In the desktop session the commands form one atomic undo item.  A
        lightweight legacy harness without a DocumentSession retains the old
        individual-stack behavior for API compatibility.
        """

        pending = list(commands)
        if not pending:
            return []
        if hasattr(self, "session"):
            return self.session.execute_many(pending, label="batch edit")
        self._refresh_suspended += 1
        try:
            return [self.commands.run(command) for command in pending]
        finally:
            self._refresh_suspended -= 1
            if self._refresh_suspended == 0:
                self.refresh_all()

    def undo(self) -> None:
        if self.session.undo():
            self.set_status("undone")

    def redo(self) -> None:
        if self.session.redo():
            self.set_status("redone")

    def _bind_root_shortcut(self, sequence: str, callback) -> None:
        """Bind one application shortcut and retain its teardown token."""

        identifier = self.winfo_toplevel().bind(sequence, callback, add="+")
        if identifier:
            self._root_bindings.append((sequence, identifier))

    def _undo_shortcut(self, _event: tk.Event | None = None) -> str:
        self.guarded(self.undo)()
        return "break"

    def _redo_shortcut(self, _event: tk.Event | None = None) -> str:
        self.guarded(self.redo)()
        return "break"

    def _job_is_stale(self, job: object) -> bool:
        """Compare a retained job's immutable inputs with the active inputs."""

        if getattr(job, "model_hash", "") != self.session.revision.model_hash:
            return True

        mesh_hash = ""
        mesh_record = self.project.mesh_records.get(
            getattr(self, "mesh_record_id", "")
        )
        if mesh_record is not None:
            mesh_hash = mesh_record.mesh_hash
        submitted_mesh_hash = str(getattr(job, "mesh_hash", ""))
        if submitted_mesh_hash and mesh_hash != submitted_mesh_hash:
            return True

        definition = self.project.analyses.get(
            str(getattr(job, "analysis_id", ""))
        )
        if definition is not None:
            return analysis_hash(
                definition,
                getattr(self.project, "output_requests", None),
                document=self.session.snapshot().document,
            ) != str(
                getattr(job, "analysis_hash", "")
            )
        return False

    def _on_revision_changed(self, revision) -> None:
        """Invalidate derived data and expose dirty/stale state immediately."""

        model_changed = revision.model_hash != self._active_model_hash
        self._active_model_hash = revision.model_hash
        if model_changed:
            self.mesh = None
            self.solution = None
            self.shape_index = 0
            # Derived views belong to the previous immutable revision.  Move
            # back to editable geometry immediately instead of leaving a
            # result/mesh label around with no corresponding data.
            if hasattr(self, "viewport"):
                self._view_mode = "geometry"
                self.show_geometry()
        if hasattr(self, "job_status"):
            self.job_status.refresh()
        if hasattr(self, "tree"):
            # CommandStack notifies before DocumentSession publishes its new
            # revision. Update only retained-job badges against the committed
            # hash; rebuilding the whole large tree here would duplicate work.
            self.tree.refresh_job_states()
            self.tree.refresh_mesh_states()
        self._update_window_title()
        if self.session.dirty:
            self._schedule_autosave()

    def _invalidate_downstream(self) -> None:
        """A geometry change makes any existing mesh and result stale."""

        self.mesh = None
        self.solution = None
        self.show_geometry()

    # ------------------------------------------------------------------
    # meshing and solving
    # ------------------------------------------------------------------
    @staticmethod
    def _triangulation_backend_summary(mesh) -> dict[str, dict[str, Any]]:
        diagnostics = getattr(mesh, "hybrid_diagnostics", {})
        if not isinstance(diagnostics, Mapping):
            return {}
        by_face = diagnostics.get("triangulation_backend_by_face", {})
        if not isinstance(by_face, Mapping):
            return {}
        return {
            str(face_id): dict(values)
            for face_id, values in sorted(by_face.items(), key=lambda item: int(item[0]))
            if isinstance(values, Mapping)
        }

    @staticmethod
    def _meshing_strategy_summary(mesh) -> dict[str, str]:
        diagnostics = getattr(mesh, "hybrid_diagnostics", {})
        if not isinstance(diagnostics, Mapping):
            return {}
        by_face = diagnostics.get("strategy_by_face", {})
        if not isinstance(by_face, Mapping):
            return {}
        return {
            str(face_id): str(strategy)
            for face_id, strategy in sorted(
                by_face.items(), key=lambda item: int(item[0])
            )
        }

    @staticmethod
    def _mesh_quality_optimization_summary(mesh) -> dict[str, dict[str, Any]]:
        """Retain ANYmesher 0.2.3 per-face optimization provenance."""

        diagnostics = getattr(mesh, "hybrid_diagnostics", {})
        if not isinstance(diagnostics, Mapping):
            return {}
        direct = diagnostics.get("quality_optimization_by_face", {})
        if isinstance(direct, Mapping) and direct:
            return {
                str(face_id): dict(values)
                for face_id, values in sorted(
                    direct.items(), key=lambda item: int(item[0])
                )
                if isinstance(values, Mapping)
            }
        by_face = diagnostics.get("triangulation_backend_by_face", {})
        if not isinstance(by_face, Mapping):
            return {}
        return {
            str(face_id): dict(values["quality_optimization"])
            for face_id, values in sorted(
                by_face.items(), key=lambda item: int(item[0])
            )
            if isinstance(values, Mapping)
            and isinstance(values.get("quality_optimization"), Mapping)
        }

    @staticmethod
    def _project_mesh_strategy(project: Project, requested: str | None) -> str:
        """Resolve the public hybrid strategy while preserving legacy auto."""

        from anymesher.hybrid import MeshingStrategy

        if requested is None:
            settings = project.native_mesh_settings
            backend = (
                "automatic"
                if settings is None
                else str(getattr(settings.backend, "value", settings.backend))
            )
            requested = {
                "automatic": "auto",
                "auto": "auto",
                "mapped": "mapped",
                "native": "native",
            }.get(backend, backend)
        try:
            return MeshingStrategy(str(requested).strip().lower()).value
        except ValueError as error:
            choices = ", ".join(item.value for item in MeshingStrategy)
            raise ValueError(
                f"unknown meshing strategy {requested!r}; expected one of {choices}"
            ) from error

    def _store_mesh_strategy(
        self, strategy: str, *, target_size: float, element_order: str
    ) -> None:
        """Persist a UI strategy through the existing native-settings schema."""

        from ..native_meshing import NativeMeshSettings

        current = self.project.native_mesh_settings
        settings = NativeMeshSettings.create(
            target_size,
            element_order=element_order,
            backend="automatic" if strategy == "auto" else strategy,
            certification_mode=(
                "interactive" if current is None else current.certification_mode
            ),
            controls=() if current is None else current.controls,
            parameters={} if current is None else dict(current.parameters),
        )
        self.project.set_native_mesh_settings(settings)

    def generate_mesh(
        self,
        target_size: float,
        *,
        native_backend: str | None = None,
        strategy: str | None = None,
    ):
        """Generate a mesh synchronously for scripts and legacy integrations.

        The desktop Mesh task uses :meth:`generate_mesh_async`; keeping this
        method synchronous preserves the established headless/test contract.
        """

        resolved_strategy = self._project_mesh_strategy(self.project, strategy)
        with self.session.transaction("mesh settings"):
            self.project.target_size = float(target_size)
            self.project.seeding_overrides = dict(self.seeding_overrides)
            if native_backend is not None:
                self.project.set_native_triangulation_backend(native_backend)
            if strategy is not None:
                self._store_mesh_strategy(
                    resolved_strategy,
                    target_size=float(target_size),
                    element_order=self.project.element_order,
                )
        requested_backend = self.project.native_triangulation_backend
        effective_native_backend = (
            None if resolved_strategy == "mapped" else requested_backend
        )
        self.mesh = self.project.generate_mesh(
            target_size,
            overrides=self.seeding_overrides,
            strategy=resolved_strategy,
        )
        self.solution = None
        from anymesher import verify_mesh_quality

        mesh_input_hash = canonical_hash(
            {
                "target_size": float(target_size),
                "overrides": dict(self.seeding_overrides),
                "element_order": self.project.element_order,
                "strategy": resolved_strategy,
                "native_backend": effective_native_backend,
            }
        )
        preparation = dict(self.project._last_mesh_preparation)
        mesh_hash = mesh_semantic_hash(
            self.mesh,
            model_hash=self.session.revision.model_hash,
            mesh_input_hash=mesh_input_hash,
            structural_preparation=preparation,
        )
        quality = verify_mesh_quality(self.mesh).as_dict()
        record = MeshRecord(
            name=f"Mesh {len(self.project.mesh_records) + 1}",
            source_model_hash=self.session.revision.model_hash,
            mesh_input_hash=mesh_input_hash,
            mesh_hash=mesh_hash,
            structural_preparation=preparation,
            summary={
                "nodes": self.mesh.num_nodes,
                "elements": self.mesh.num_elements,
                "native_backend_requested": effective_native_backend,
                "strategy_requested": resolved_strategy,
                "strategy_by_face": self._meshing_strategy_summary(self.mesh),
                "quality_optimization_by_face": (
                    self._mesh_quality_optimization_summary(self.mesh)
                ),
                "triangulation_backend_by_face": (
                    self._triangulation_backend_summary(self.mesh)
                ),
                "automatic_intersections": int(
                    getattr(self.mesh, "automatic_intersections", 0)
                ),
                "automatic_beam_connections": int(
                    getattr(self.mesh, "automatic_beam_connections", 0)
                ),
                "automatic_shell_connections": int(
                    getattr(self.mesh, "automatic_shell_connections", 0)
                ),
                "quality": quality,
            },
        )
        with self.session.transaction("record mesh", solver_affecting=False):
            self.project.mesh_records[record.id] = record
        self.mesh_record_id = record.id
        self._mesh_details_record_id = record.id
        self._meshes[record.id] = self.mesh
        self.set_status(
            f"meshed: {self.mesh.num_nodes} nodes, "
            f"{self.mesh.num_elements} elements; "
            f"{getattr(self.mesh, 'automatic_intersections', 0)} plate "
            "intersection(s) imprinted; "
            f"{getattr(self.mesh, 'automatic_beam_connections', 0)} beam "
            "connection(s) created; "
            f"{getattr(self.mesh, 'automatic_shell_connections', 0)} shell "
            "T-junction tie(s) created; "
            f"max aspect {quality['max_aspect_ratio']:.3g}, "
            f"warp {quality['max_warp']:.3g}"
        )
        self.show_mesh()
        self.refresh_panels()
        return self.mesh

    def generate_mesh_async(
        self,
        target_size: float,
        *,
        native_backend: str | None = None,
        strategy: str | None = None,
    ) -> MeshRecord:
        """Submit meshing from an immutable snapshot and return immediately."""

        if self.mesh_task_manager.busy:
            raise ValueError("a mesh is already being generated")
        resolved_strategy = self._project_mesh_strategy(self.project, strategy)
        settings = MeshSettings.create(
            target_size,
            element_order=self.project.element_order,
            overrides=self.seeding_overrides,
            strategy=resolved_strategy,
        )
        with self.session.transaction("mesh settings"):
            self.project.target_size = settings.target_size
            self.project.seeding_overrides = dict(settings.overrides)
            if native_backend is not None:
                self.project.set_native_triangulation_backend(native_backend)
            if strategy is not None:
                self._store_mesh_strategy(
                    resolved_strategy,
                    target_size=settings.target_size,
                    element_order=settings.element_order,
                )
        requested_backend = self.project.native_triangulation_backend
        effective_native_backend = (
            None if settings.strategy == "mapped" else requested_backend
        )
        snapshot = self.session.snapshot()
        record = MeshRecord(
            name=f"Mesh {len(self.project.mesh_records) + 1}",
            source_model_hash=snapshot.revision.model_hash,
            mesh_input_hash=canonical_hash(
                {
                    "mesh_settings": settings.input_hash,
                    "native_backend": effective_native_backend,
                }
            ),
            mesh_hash="",
            status="running",
            summary={
                "target_size": settings.target_size,
                "element_order": settings.element_order,
                "native_backend_requested": effective_native_backend,
                "strategy_requested": settings.strategy,
                "status": "running",
            },
        )
        with self.session.transaction("queue mesh", solver_affecting=False):
            self.project.mesh_records[record.id] = record
        try:
            self.mesh_task_manager.submit(record.id, snapshot, settings)
        except BaseException:
            with self.session.transaction("remove failed mesh submission", solver_affecting=False):
                self.project.mesh_records.pop(record.id, None)
            raise
        self._active_mesh_task_id = record.id
        self._mesh_details_record_id = record.id
        self.set_status("meshing in background from immutable model snapshot")
        self.refresh_panels()
        return record

    @property
    def mesh_job_running(self) -> bool:
        return self.mesh_task_manager.busy

    def cancel_mesh(self) -> bool:
        """Request cancellation at the next safe meshing phase."""

        job_id = self.mesh_task_manager.active_job_id
        if job_id is None or not self.mesh_task_manager.cancel(job_id):
            return False
        record = self.project.mesh_records.get(job_id)
        if record is not None:
            with self.session.transaction("cancel mesh", solver_affecting=False):
                record.status = "cancelling"
                record.summary["status"] = "cancelling"
        self.set_status("cancelling mesh; waiting for the current safe phase")
        self.refresh_panels()
        return True

    def mesh_record_state(self, record: MeshRecord) -> str:
        """Return persisted state with revision-based staleness applied."""

        if record.status in ("completed", "stale") and (
            record.source_model_hash
            and record.source_model_hash != self.session.revision.model_hash
        ):
            return "stale"
        return str(record.status)

    def _poll_mesh_jobs(self) -> None:
        for event in self.mesh_task_manager.poll():
            record = self.project.mesh_records.get(event.job_id)
            if record is None:
                continue
            if event.kind in ("started", "progress"):
                self.set_status(event.message)
                continue
            if event.kind == "cancelling":
                self.set_status(event.message)
                continue

            if event.kind == "completed":
                result = event.payload
                if not isinstance(result, MeshJobResult):
                    continue
                current = record.source_model_hash == self.session.revision.model_hash
                with self.session.transaction(
                    "record completed mesh", solver_affecting=False
                ):
                    record.mesh_hash = result.mesh_hash
                    record.structural_preparation = dict(
                        result.structural_preparation
                    )
                    record.status = "completed" if current else "stale"
                    record.summary.update(
                        {
                            "status": record.status,
                            "nodes": result.mesh.num_nodes,
                            "elements": result.mesh.num_elements,
                            "strategy_by_face": self._meshing_strategy_summary(
                                result.mesh
                            ),
                            "quality_optimization_by_face": (
                                self._mesh_quality_optimization_summary(
                                    result.mesh
                                )
                            ),
                            "triangulation_backend_by_face": (
                                self._triangulation_backend_summary(result.mesh)
                            ),
                            "automatic_intersections": int(
                                getattr(result.mesh, "automatic_intersections", 0)
                            ),
                            "automatic_beam_connections": int(
                                getattr(result.mesh, "automatic_beam_connections", 0)
                            ),
                            "automatic_shell_connections": int(
                                getattr(result.mesh, "automatic_shell_connections", 0)
                            ),
                            "quality": dict(result.quality),
                        }
                    )
                self._meshes[record.id] = result.mesh
                if current:
                    self.mesh = result.mesh
                    self.mesh_record_id = record.id
                    self.solution = None
                    self.show_mesh()
                    quality = result.quality
                    self.set_status(
                        f"meshed: {result.mesh.num_nodes} nodes, "
                        f"{result.mesh.num_elements} elements; "
                        f"{getattr(result.mesh, 'automatic_intersections', 0)} "
                        "plate intersection(s) imprinted; "
                        f"{getattr(result.mesh, 'automatic_beam_connections', 0)} "
                        "beam connection(s) created; "
                        f"{getattr(result.mesh, 'automatic_shell_connections', 0)} "
                        "shell T-junction tie(s) created; "
                        f"max aspect {float(quality['max_aspect_ratio']):.3g}, "
                        f"warp {float(quality['max_warp']):.3g}"
                    )
                else:
                    self.set_status(
                        "mesh completed for an older model revision and was retained as stale"
                    )
            elif event.kind == "cancelled":
                with self.session.transaction(
                    "record cancelled mesh", solver_affecting=False
                ):
                    record.status = "cancelled"
                    record.summary["status"] = "cancelled"
                self.set_status("mesh generation cancelled")
            elif event.kind == "failed":
                with self.session.transaction("record failed mesh", solver_affecting=False):
                    record.status = "failed"
                    record.summary["status"] = "failed"
                    if event.payload:
                        record.diagnostics.append(event.payload)
                self.set_status(f"mesh generation failed: {event.message}", error=True)
            self._active_mesh_task_id = None
            self.refresh_all()

    def solve(self, analysis: str = "Linear static", **options: Any) -> None:
        """Queue an analysis against an immutable document/mesh snapshot."""

        if self.mesh is None:
            raise ValueError("generate a mesh first")
        try:
            function = ANALYSES[analysis]
        except KeyError:
            raise ValueError(f"unknown analysis {analysis!r}") from None

        self.analysis = analysis
        submitted_options = dict(options)
        target_kind = "none" if analysis == "Modal" else "load_case"
        target_id = str(options.get("load_case", "default"))
        if options.get("combination") is not None:
            target_kind = "combination"
            target_id = str(options["combination"])
        definition = AnalysisDefinition(
            name=f"{analysis} {len(self.project.analyses) + 1}",
            type=analysis.lower().replace(" ", "_"),
            target_kind=target_kind,
            target_id=target_id,
            settings=_record_settings(options),
        )
        with self.session.transaction("create analysis", solver_affecting=False):
            self.project.analyses[definition.id] = definition

        job_options = dict(options)
        if self.imported is not None:
            # An imported model is already built and has no geometry to mesh,
            # so it goes straight to the analysis.
            case = self.project.load_cases.get(self.active_case())
            options = {
                key: value
                for key, value in options.items()
                if key not in ("load_case", "combination")
            }
            built = self.imported.built(case, project=self.project)
            job_options = {
                key: value
                for key, value in job_options.items()
                if key not in ("load_case", "combination")
            }
            job_options["built"] = built
        else:
            job_options["mesh"] = deepcopy(self.mesh)

        mesh_hash = ""
        mesh_record = self.project.mesh_records.get(
            getattr(self, "mesh_record_id", "")
        )
        if mesh_record is not None:
            mesh_hash = mesh_record.mesh_hash
        record = self.job_manager.submit(
            definition,
            self.session.snapshot(),
            _execute_analysis_job,
            mesh_hash=mesh_hash,
            kwargs={
                "solver_function": function,
                "analysis_name": analysis,
                "options": job_options,
            },
            name=definition.name,
            project_override=self.project if self.imported is not None else None,
        )
        self.active_job_id = record.id
        input_report = _submitted_input_report(
            self.project,
            definition,
            submitted_options,
            self.mesh,
            revision=self.session.revision.sequence,
            model_hash=self.session.revision.model_hash,
            mesh_hash=mesh_hash,
        )
        self.submitted_input_reports[record.id] = input_report
        solve_panel = self.panels.get("Solve")
        if solve_panel is not None:
            solve_panel.begin_job(
                definition.name,
                record.id,
                input_report,
            )
        self.set_status(f"queued {definition.name}")
        self.refresh_panels()
        self.job_status.refresh()

    def cancel_solve(self) -> None:
        self.worker.cancel()

    def _on_solved(
        self,
        solution,
        job_id: str | None = None,
        completion: str = "completed",
    ) -> None:
        self.solution = solution
        if job_id is not None:
            self.solutions[job_id] = solution
            self.active_job_id = job_id
        shapes = getattr(solution, "shapes", None)
        # Nonlinear snapshots are a path to the submitted result; open on the
        # last converged state while retaining every real increment for the
        # navigator and playback controls.
        self.shape_index = (
            len(shapes) - 1 if shapes and hasattr(solution, "steps") else 0
        )
        self.set_status(solution.summary())
        panel = self.panels["Solve"]
        panel.append_progress(completion)
        panel.append_report(_solution_report(solution))
        panel.show_progress("")
        self.notebook.select(self.panels["Results"])
        self.refresh_panels()
        self.show_results()

    def _poll_jobs(self) -> None:
        if self._closing:
            return
        for event in self.job_manager.poll():
            if event.kind in ("queued", "started", "progress", "status"):
                self._on_progress(event.message, event.payload)
            elif event.kind == "completed":
                try:
                    self._on_solved(event.payload, event.job_id)
                except Exception as error:
                    # A display/export adapter must never turn a successfully
                    # completed numerical job into an unhandled Tk callback.
                    self.set_status(
                        f"job completed, but opening Results failed: {error}",
                        error=True,
                    )
                    panel = self.panels.get("Solve")
                    if panel is not None:
                        panel.append_progress(f"Results display failed: {error}")
                    try:
                        self.refresh_panels()
                    except Exception as refresh_error:
                        if panel is not None:
                            panel.append_progress(
                                f"Results controls refresh failed: {refresh_error}"
                            )
                if self.path is not None:
                    self._schedule_result_artifact(event.job_id, self.path)
                    self._schedule_job_log_artifact(event.job_id, self.path)
                if self.session.dirty:
                    self._write_recovery()
            elif event.kind == "partial":
                try:
                    self._on_solved(
                        event.payload,
                        event.job_id,
                        completion=f"partial: {event.message}",
                    )
                except Exception as error:
                    self.set_status(
                        f"partial job retained, but opening Results failed: {error}",
                        error=True,
                    )
                if self.path is not None:
                    self._schedule_result_artifact(event.job_id, self.path)
                    self._schedule_job_log_artifact(event.job_id, self.path)
                if self.session.dirty:
                    self._write_recovery()
            elif event.kind == "cancelled":
                self.set_status(f"job {event.job_id[:8]} cancelled")
                if self.path is not None:
                    self._schedule_job_log_artifact(event.job_id, self.path)
                self.refresh_panels()
            elif event.kind == "failed":
                self.set_status(
                    f"job {event.job_id[:8]} failed: {event.message}", error=True
                )
                panel = self.panels.get("Solve")
                if panel is not None:
                    panel.append_progress(f"failed: {event.message}")
                if self.path is not None:
                    self._schedule_job_log_artifact(event.job_id, self.path)
                self.refresh_panels()
        if hasattr(self, "job_status"):
            self.job_status.refresh()
        self._poll_result_artifacts()
        self._poll_job_log_artifacts()
        self._poll_mesh_jobs()
        self._poll_recovery_write()
        self._job_poll = self.after(self.worker.POLL_MS, self._poll_jobs)

    def _schedule_result_artifact(self, job_id: str, destination: Path) -> None:
        """Write a completed result off the Tk event thread."""

        if self.session.read_only or job_id in self._artifact_futures:
            return
        solution = self.solutions.get(job_id)
        record = self.project.jobs.get(job_id)
        if solution is None or record is None:
            return
        mesh_id = str(getattr(self, "mesh_record_id", "") or "active-mesh")
        from ..io.artifacts import ArtifactStore

        store = ArtifactStore(destination)
        submitted_inputs = self.submitted_input_reports.get(job_id)
        artifact_provenance = {}
        if submitted_inputs:
            try:
                artifact_provenance["submitted_inputs"] = json.loads(submitted_inputs)
            except json.JSONDecodeError:
                artifact_provenance["submitted_inputs_text"] = submitted_inputs
        future = self._artifact_executor.submit(
            write_solution_artifact,
            store,
            solution,
            job_id=record.id,
            document_id=self.project.document_id,
            mesh_id=mesh_id,
            model_hash=record.model_hash,
            mesh_hash=record.mesh_hash,
            analysis_hash=record.analysis_hash,
            provenance=artifact_provenance,
            summary=dict(record.summary),
            diagnostics=tuple(record.diagnostics),
            partial=bool(record.partial),
        )
        self._artifact_futures[job_id] = future
        self._artifact_destinations[job_id] = Path(destination)

    def _poll_result_artifacts(self) -> None:
        for job_id, future in tuple(self._artifact_futures.items()):
            if not future.done():
                continue
            self._artifact_futures.pop(job_id, None)
            destination = self._artifact_destinations.pop(job_id, None)
            try:
                artifact = future.result()
            except BaseException as error:  # persisted as a job diagnostic
                record = self.project.jobs.get(job_id)
                if record is not None:
                    record.diagnostics.append(
                        {"type": type(error).__name__, "message": str(error)}
                    )
                self.set_status(
                    f"result artifact for {job_id[:8]} failed: {error}", error=True
                )
                continue
            if job_id not in self.project.jobs or destination is None:
                continue
            self._record_result_artifact(job_id, artifact, destination)
            if self.session.dirty:
                self._write_recovery()

    def _record_result_artifact(self, job_id: str, artifact, destination: Path) -> None:
        from ..io.artifacts import ArtifactStore

        with self.session.transaction(
            "record result artifact", solver_affecting=False
        ):
            self.project.jobs[job_id].result_artifact_id = artifact.id
            self.project.artifacts[artifact.id] = artifact
        try:
            self.result_datasets[job_id] = ArtifactStore(destination).open_result(
                artifact
            )
        except (OSError, ValueError):
            pass

    def _schedule_job_log_artifact(self, job_id: str, destination: Path) -> None:
        """Persist one terminal numerical-job log outside the Tk thread."""

        if self.session.read_only or job_id in self._log_futures:
            return
        record = self.project.jobs.get(job_id)
        if record is None:
            return
        try:
            entries = self.job_manager.log(job_id)
        except KeyError:
            return
        from ..io.artifacts import ArtifactStore

        store = ArtifactStore(destination)
        self._log_futures[job_id] = self._artifact_executor.submit(
            store.write_log, job_id, entries
        )
        self._log_destinations[job_id] = Path(destination)

    def _poll_job_log_artifacts(self) -> None:
        for job_id, future in tuple(self._log_futures.items()):
            if not future.done():
                continue
            self._log_futures.pop(job_id, None)
            self._log_destinations.pop(job_id, None)
            try:
                artifact = future.result()
            except BaseException as error:
                record = self.project.jobs.get(job_id)
                if record is not None:
                    with self.session.transaction(
                        "record job log failure", solver_affecting=False
                    ):
                        record.diagnostics.append(
                            {"type": type(error).__name__, "message": str(error)}
                        )
                self.set_status(
                    f"job log for {job_id[:8]} failed: {error}", error=True
                )
                continue
            if job_id not in self.project.jobs:
                continue
            self._record_job_log_artifact(job_id, artifact)
            if self.session.dirty:
                self._write_recovery()

    def _record_job_log_artifact(self, job_id: str, artifact) -> None:
        with self.session.transaction(
            "record job log artifact", solver_affecting=False
        ):
            self.project.jobs[job_id].log_artifact_id = artifact.id
            self.project.artifacts[artifact.id] = artifact

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

    def _on_details_page_selected(self, page: str) -> None:
        """Keep task navigation and the viewport in the same workflow context."""

        if page == "Geometry":
            self.show_geometry()
            self.selection_strip.set_context(
                self._geometry_selection_mode,
                "Model geometry • select Point, Line or Plate",
            )
            self.details.set_hint("Model geometry")
            self.set_status("model geometry shown; Point/Line/Plate selection is active")
            return
        if page == "Loads & BC":
            self.show_geometry()
            kind = (
                self.selection.mode
                if self.selection.mode in ("vertex", "edge", "face")
                else "edge"
            )
            self.selection_strip.set_context(
                kind,
                "Geometry scope • choose Point, Line or Plate",
            )
            self.details.set_hint("Scope on model geometry")
            self.set_status(
                "model geometry shown; select points, lines or plates for loads and BCs"
            )
            return
        if page == "Sections":
            self.show_geometry()
            self.details.set_hint("Assign on model geometry")
            return
        if page in ("Mesh", "Solve") and self.mesh is not None:
            self.show_mesh()
            self.details.set_hint("Mesh view")

    def show_persisted_result(
        self,
        field_key: str,
        *,
        frame: int = 0,
        component: str | None = None,
        scale: float = 1.0,
        limits=None,
    ) -> None:
        if self.mesh is None or self.active_job_id is None:
            raise ValueError("this persisted result has no available mesh")
        dataset = self.result_datasets.get(self.active_job_id)
        if dataset is None:
            raise ValueError("the result artifact is unavailable")
        scene = build_persisted_result_scene(
            self.project,
            self.mesh,
            dataset,
            field_key,
            frame=frame,
            component=component,
            scale=scale,
            limits=limits,
            colormap=self.panels["Results"].colormap(),
            display_units=self.panels["Results"].display_units(),
            show_nodes=self.panels["Results"].show_result_nodes(),
        )
        panel = self.panels["Results"]
        scene = self._with_attributes(
            scene,
            show_supports=panel.show_result_supports(),
            show_loads=panel.show_result_loads(),
            show_masses=panel.show_result_masses(),
            show_imperfections=panel.show_imperfect_reference(),
        )
        self._view_mode = "results"
        self.viewport.show(scene)
        self._update_view_label()

    def _with_attributes(
        self,
        scene,
        *,
        show_supports: bool = True,
        show_loads: bool = True,
        show_masses: bool = True,
        show_imperfections: bool = True,
    ):
        """Overlay supports and the active case's loads, if asked for."""

        if not self._show_attributes.get():
            return scene
        return scene.merge(
            build_attribute_overlay(
                self.project,
                case_name=self.active_case(),
                mesh=self.mesh,
                show_supports=show_supports,
                show_loads=show_loads,
                show_masses=show_masses,
                show_imperfections=show_imperfections,
            )
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
        panel.ensure_compatible_field()
        shape = self.current_shape()
        self._view_mode = "results"
        scene = build_result_scene(
            shape,
            field=panel.field_name(),
            scale=panel.scale_value(shape),
            limits=panel.colour_limits(),
            values=panel.field_values(),
            colormap=panel.colormap(),
            display_units=panel.display_units(),
            show_nodes=panel.show_result_nodes(),
        )
        scene = self._with_attributes(
            scene,
            show_supports=panel.show_result_supports(),
            show_loads=panel.show_result_loads(),
            show_masses=panel.show_result_masses(),
            show_imperfections=panel.show_imperfect_reference(),
        )
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
        if getattr(self.selection.domain, "value", "geometry") == "geometry":
            self._geometry_selection_mode = self.selection.mode
        self._selection_label.configure(
            text=f"{mode_label(self.selection.mode)} mode - "
            f"{self.selection.describe()}"
        )
        self.refresh_panels()

    def _on_progress(self, text: str, payload: Any = None) -> None:
        line = _job_progress_text(text, payload)
        self.set_status(line)
        panel = self.panels.get("Solve")
        if panel is not None:
            panel.show_progress(line)
            panel.append_progress(line, payload=payload)

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

    def _selection_universe(self):
        mode = self.selection.mode
        if mode == "vertex":
            return [self.project.geometry.entity_ref("vertex", key) for key in self.project.geometry.vertices]
        if mode == "edge":
            return [self.project.geometry.entity_ref("edge", key) for key in self.project.geometry.edges]
        if mode == "face":
            return [self.project.geometry.entity_ref("face", key) for key in self.project.geometry.faces]
        if self.mesh is None:
            return []
        if mode == "node":
            return [MeshEntityRef("node", key) for key in self.mesh.nodes]
        if mode == "element":
            identifiers = (
                *self.mesh.shells.keys(),
                *self.mesh.beams.keys(),
                *self.mesh.couplings.keys(),
            )
            return [MeshEntityRef("element", key) for key in identifiers]
        if mode == "element_face":
            return [MeshEntityRef("element_face", (key, 0)) for key in self.mesh.shells]
        return []

    def _select_all(self, _event=None):
        self.selection.select_all(self._selection_universe())
        return "break"

    def _delete_selection(self, _event=None):
        from ..commands import DeleteEntity

        geometry = [
            ref for ref in self.selection.items
            if getattr(ref, "kind", "") in ("vertex", "edge", "face")
            and not isinstance(ref, MeshEntityRef)
        ]
        if geometry:
            self.run_many(DeleteEntity(ref) for ref in geometry)
        else:
            self.set_status("mesh topology is immutable; delete its owning geometry or scope")
        return "break"

    # ------------------------------------------------------------------
    # workspace commands, recovery and document ownership
    # ------------------------------------------------------------------
    def _tree_action(self, action: str, keys: tuple[str, ...]) -> None:
        if not keys:
            return
        if action == "delete":
            self._delete_tree_items(keys)
            return
        key = keys[0]
        prefix = key.split(":", 1)[0]
        page = {
            "feature": "Geometry",
            "material": "Sections",
            "plate_section": "Sections",
            "beam_section": "Sections",
            "imperfection": "Sections",
            "coordinate": "Definitions",
            "region": "Definitions",
            "unit": "Definitions",
            "mesh": "Mesh",
            "case": "Loads & BC",
            "load": "Loads & BC",
            "support": "Loads & BC",
            "mass": "Loads & BC",
            "analysis": "Solve",
            "job": "Solve",
            "result": "Results",
        }.get(prefix, "Geometry")
        if action == "edit":
            self.details.select(page)
            if prefix == "feature":
                geometry_panel = self.panels.get("Geometry")
                feature_id = int(key.split(":", 1)[1])
                edit_sketch = getattr(geometry_panel, "edit_sketch", None)
                if (
                    callable(edit_sketch)
                    and edit_sketch(feature_id)
                ):
                    self.details.set_hint(
                        f"Editing constrained sketch {self.tree.tree.item(key, 'text')}"
                    )
                    self.set_status("editable sketch loaded on its support plate")
                    return
            if prefix == "imperfection":
                section_panel = self.panels.get("Sections")
                identifier = key.split(":", 1)[1]
                if (
                    section_panel is not None
                    and section_panel.edit_imperfection(identifier)
                ):
                    self.details.set_hint(
                        f"Editing actual values of {self.tree.tree.item(key, 'text')}"
                    )
                    self.set_status("selected imperfection loaded into Details")
                    return
            if prefix in {"support", "mass", "load"}:
                load_panel = self.panels.get("Loads & BC")
                if load_panel is not None and load_panel.edit_tree_item(key):
                    self.details.set_hint(
                        f"Editing actual values of {self.tree.tree.item(key, 'text')}"
                    )
                    self.set_status("selected attribute loaded into Details")
                    return
            self.details.set_hint(f"Editing {self.tree.tree.item(key, 'text')}")
            return
        if action == "suppress" and prefix == "feature":
            feature_id = int(key.split(":", 1)[1])
            with self.session.transaction("suppress feature"):
                record = self.project.geometry.features.get(feature_id)
                self.project.geometry.features.set_suppressed(
                    feature_id, not record.suppressed
                )
                report = self.project.regenerate_geometry_features()
                if not report.success:
                    raise ValueError(report.diagnostic or "feature regeneration failed")
                self.selection.apply_replacements(report.replacements)
            self.set_status(
                f"feature {feature_id} "
                f"{'suppressed' if record.suppressed else 'resumed'}"
            )
            return
        if action == "rename" and prefix == "feature":
            feature_id = int(key.split(":", 1)[1])
            record = self.project.geometry.features.get(feature_id)
            name = simpledialog.askstring(
                "Rename feature", "Name", initialvalue=record.name,
                parent=self.winfo_toplevel(),
            )
            if name:
                with self.session.transaction("rename feature", solver_affecting=False):
                    self.project.geometry.features.update(feature_id, name=name)
            return
        if action == "dependencies" and prefix == "feature":
            feature_id = int(key.split(":", 1)[1])
            dependencies = self.project.geometry.features.dependents(
                feature_id, transitive=True
            )
            self.set_status(
                f"feature {feature_id} dependents: "
                + (", ".join(map(str, dependencies)) or "none")
            )
            return
        if action == "isolate":
            isolate = getattr(self.viewport, "isolate", None)
            if callable(isolate):
                isolate(keys)
            else:
                self.set_status("isolate is available from the viewport display groups")
            return
        self.set_status(f"{action} is not available for this item")

    def _delete_tree_items(self, keys: tuple[str, ...]) -> None:
        """Delete every highlighted leaf as one dependency-audited undo item."""

        from ..commands import (
            DeleteAttribute,
            DeleteEntity,
            DeleteFeature,
            DeleteLoadCase,
            DeleteOutputRequest,
            DeleteProjectRecord,
        )
        from ..selection import parse_entity_tag

        selected = tuple(dict.fromkeys(str(key) for key in keys))
        categories: set[str] = set()
        commands = []
        mesh_ids: set[str] = set()
        job_ids: set[str] = set()
        result_ids: set[str] = set()
        cascaded_job_ids: set[str] = set()
        explicitly_selected_job_ids = {
            key.split(":", 1)[1]
            for key in selected
            if key.startswith("job:")
        }
        selected_analysis_ids = {
            key.split(":", 1)[1]
            for key in selected
            if key.startswith("analysis:")
        }
        analysis_dependent_job_ids = {
            job.id
            for job in self.project.jobs.values()
            if job.analysis_id in selected_analysis_ids
        }

        entity_refs = [parse_entity_tag(key) for key in selected]
        if any(ref is not None for ref in entity_refs):
            if not all(ref is not None for ref in entity_refs):
                self.set_status(
                    "Delete geometry separately from other tree item types",
                    error=True,
                )
                return
            categories.add("geometry")
            # Delete owners before their topology dependencies.  The whole
            # sequence remains one CompositeCommand and rolls back on failure.
            order = {"face": 0, "edge": 1, "vertex": 2}
            for ref in sorted(entity_refs, key=lambda item: order.get(item.kind, 9)):
                commands.append(DeleteEntity(ref))
        else:
            for key in selected:
                if ":" not in key:
                    self.set_status(
                        "Select individual tree items, not a branch heading",
                        error=True,
                    )
                    return
                prefix, identifier = key.split(":", 1)
                if prefix in {"support", "mass", "imperfection", "load"}:
                    categories.add("attribute")
                    if key.endswith(":gravity"):
                        self.set_status(
                            "Gravity/acceleration is deleted by editing its load case",
                            error=True,
                        )
                        return
                    commands.append(DeleteAttribute(key.rsplit(":", 1)[1]))
                elif prefix == "feature":
                    categories.add("feature")
                    commands.append(DeleteFeature(int(identifier)))
                elif prefix == "case":
                    categories.add("case")
                    case = next(
                        (
                            (name, value)
                            for name, value in self.project.load_cases.items()
                            if str(getattr(value, "id", name)) == identifier
                        ),
                        None,
                    )
                    if case is None:
                        self.set_status(f"Load case {identifier!r} no longer exists", error=True)
                        return
                    commands.append(DeleteLoadCase(case[0]))
                elif prefix == "output_request":
                    categories.add("output request")
                    commands.append(DeleteOutputRequest(identifier))
                elif prefix in {"plate_section", "beam_section"}:
                    categories.add("section definition")
                    commands.append(DeleteProjectRecord(prefix, identifier))
                elif prefix in {
                    "mesh", "analysis", "job", "result", "material",
                    "coordinate", "region",
                }:
                    # A submitted analysis owns its retained job/result
                    # history.  Deleting only the definition would orphan
                    # those records, which the command layer correctly
                    # refuses.  At the tree/workflow level, explicit analysis
                    # deletion therefore removes finished dependants first in
                    # the same atomic, undoable edit.  Active jobs still fail
                    # closed in DeleteProjectRecord("job", ...).
                    if prefix == "analysis":
                        categories.add(prefix)
                        dependants = [
                            job
                            for job in self.project.jobs.values()
                            if job.analysis_id == identifier
                        ]
                        for job in dependants:
                            if job.id not in cascaded_job_ids:
                                commands.append(DeleteProjectRecord("job", job.id))
                                cascaded_job_ids.add(job.id)
                                job_ids.add(job.id)
                                if job.result_artifact_id is not None:
                                    result_ids.add(job.result_artifact_id)
                        commands.append(DeleteProjectRecord(prefix, identifier))
                        continue
                    if prefix == "job" and identifier in analysis_dependent_job_ids:
                        # The user selected both an analysis and its child job;
                        # the cascade already contains it exactly once.
                        continue
                    categories.add(prefix)
                    commands.append(DeleteProjectRecord(prefix, identifier))
                    if prefix == "mesh":
                        mesh_ids.add(identifier)
                    elif prefix == "job":
                        job_ids.add(identifier)
                    elif prefix == "result":
                        result_ids.add(identifier)
                else:
                    self.set_status(
                        f"Delete is not available for {prefix.replace('_', ' ')}",
                        error=True,
                    )
                    return

        # Homogeneous bulk edits are predictable.  Attributes and plate/beam
        # sections are intentionally normalized above so related sibling
        # types may be removed together.
        if len(categories) > 1:
            self.set_status(
                "Delete one kind of tree item at a time; nothing was changed",
                error=True,
            )
            return
        if not commands:
            return
        # Feature dependencies point from later monotonic IDs to earlier IDs.
        # Removing dependants first lets a multi-feature selection succeed
        # without implicit cascade deletion.
        if categories == {"feature"}:
            commands.sort(key=lambda command: int(command.feature_id), reverse=True)
        try:
            self.session.execute_many(
                commands,
                label=f"delete {len(commands)} tree item(s)",
                solver_affecting=categories not in ({"mesh"}, {"job"}, {"result"}),
            )
        except (ValueError, KeyError, ProjectError) as error:
            self.set_status(str(error), error=True)
            return

        if getattr(self, "mesh_record_id", None) in mesh_ids:
            self.mesh = None
            self.mesh_record_id = None
            self._mesh_details_record_id = None
            self.solution = None
            self.show_geometry()
        active_result_removed = self.active_job_id in job_ids
        if self.active_job_id is not None and result_ids:
            old_job = next(
                (
                    value for value in self.project.jobs.values()
                    if value.id == self.active_job_id
                ),
                None,
            )
            active_result_removed = active_result_removed or (
                old_job is not None and old_job.result_artifact_id is None
            )
        if active_result_removed:
            self.active_job_id = None
            self.solution = None
            self.shape_index = 0
            self.show_mesh()
        self.set_status(
            f"deleted {len(selected)} selected {next(iter(categories))} item(s)"
            + (
                f" and {len(cascaded_job_ids - explicitly_selected_job_ids)} "
                "dependent job(s)/result(s)"
                if cascaded_job_ids - explicitly_selected_job_ids
                else ""
            )
            + "; Undo restores all"
        )

    def show_command_palette(self, _event=None):
        commands = {
            "New project": lambda: self.new_project(confirm=True),
            "Open project": lambda: self.open_project(confirm=True),
            "Save project": self.save_project,
            "Undo": self.undo,
            "Redo": self.redo,
            "Model geometry": lambda: self.details.select("Geometry"),
            "Regions, coordinates and units": lambda: self.details.select("Definitions"),
            "Mesh controls": lambda: self.details.select("Mesh"),
            "Materials and sections": lambda: self.details.select("Sections"),
            "Loads and boundary conditions": lambda: self.details.select("Loads & BC"),
            "Analysis and jobs": lambda: self.details.select("Solve"),
            "Results explorer": lambda: self.details.select("Results"),
            "Python scripting console": lambda: self.details.select("Scripting"),
            "Frame model": self._fit,
            "Isometric view": lambda: self.viewport.set_view("iso"),
            "Top view": lambda: self.viewport.set_view("top"),
        }
        dialog = tk.Toplevel(self)
        dialog.title("ANYfem commands")
        dialog.transient(self.winfo_toplevel())
        dialog.geometry("520x360")
        query = tk.StringVar(value="")
        entry = ttk.Entry(dialog, textvariable=query)
        entry.pack(fill="x", padx=10, pady=10)
        listing = tk.Listbox(dialog, activestyle="dotbox")
        listing.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        def refresh(*_args) -> None:
            needle = query.get().strip().casefold()
            listing.delete(0, "end")
            for label in commands:
                if needle in label.casefold():
                    listing.insert("end", label)
            if listing.size():
                listing.selection_set(0)

        def run_selected(_event=None) -> None:
            selected = listing.curselection()
            if not selected:
                return
            label = listing.get(selected[0])
            dialog.destroy()
            self.guarded(commands[label])()

        query.trace_add("write", refresh)
        listing.bind("<Double-1>", run_selected)
        listing.bind("<Return>", run_selected)
        entry.bind("<Return>", run_selected)
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        refresh()
        entry.focus_set()
        return "break"

    def _schedule_autosave(self) -> None:
        if self._autosave_after is not None:
            try:
                self.after_cancel(self._autosave_after)
            except tk.TclError:
                pass
        self._autosave_after = self.after(30_000, self._write_recovery)
        if self._autosave_hard_after is None:
            self._autosave_hard_after = self.after(300_000, self._write_recovery)

    def _write_recovery(self) -> None:
        """Capture recovery state on Tk, then queue all file I/O off-thread."""

        for attribute in ("_autosave_after", "_autosave_hard_after"):
            identifier = getattr(self, attribute, None)
            if identifier is not None:
                try:
                    self.after_cancel(identifier)
                except tk.TclError:
                    pass
                setattr(self, attribute, None)
        if not getattr(self, "session", None) or not self.session.dirty:
            return
        snapshot = self.session.snapshot()
        request = (
            self._recovery_epoch,
            dict(snapshot.document),
            {
                "document_id": self.project.document_id,
                "revision_id": snapshot.revision.id,
                "revision_sequence": snapshot.revision.sequence,
                "document_hash": snapshot.revision.document_hash,
                "model_hash": snapshot.revision.model_hash,
                "saved_document_hash": self.session.saved_document_hash,
                "source_path": self.path,
                # Materialize on the UI thread; generators over a live project
                # are not immutable worker inputs.
                "artifact_refs": tuple(
                    artifact.to_dict() for artifact in self.project.artifacts.values()
                ),
            },
        )
        if self._recovery_future is None:
            self._start_recovery_write(request)
        else:
            # Only the newest dirty revision matters while the preceding
            # atomic bundle finishes.
            self._recovery_pending = request
        if self.session.dirty:
            self._autosave_hard_after = self.after(300_000, self._write_recovery)

    def _start_recovery_write(
        self, request: tuple[int, dict[str, Any], dict[str, Any]]
    ) -> None:
        epoch, document, keywords = request
        self._recovery_future_epoch = epoch
        self._recovery_future = self._recovery_executor.submit(
            write_autosave, document, **keywords
        )

    def _poll_recovery_write(self) -> None:
        future = self._recovery_future
        if future is None or not future.done():
            return
        epoch = self._recovery_future_epoch
        self._recovery_future = None
        try:
            future.result()
        except BaseException as error:  # noqa: BLE001 - surface worker failures
            if epoch == self._recovery_epoch:
                self.set_status(f"autosave failed: {error}", error=True)
        else:
            if epoch == self._recovery_epoch:
                self.set_status("autosaved recovery snapshot")

        pending = self._recovery_pending
        self._recovery_pending = None
        if pending is not None and pending[0] == self._recovery_epoch:
            self._start_recovery_write(pending)

    def recover_autosave(self) -> None:
        candidates = discover_recoveries(latest_only=True)
        if not candidates:
            raise ValueError("no recoverable ANYfem autosaves were found")
        candidate = candidates[0]
        if not messagebox.askyesno(
            "Recover ANYfem autosave",
            f"Recover snapshot from {candidate.created_utc}?\n"
            f"Recommended action: {candidate.recommendation.replace('_', ' ')}",
            parent=self.winfo_toplevel(),
        ):
            return
        project = project_from_dict(load_recovery(candidate))
        self._set_project(project)
        self.session.dirty = True
        self.set_status("recovered autosave; use Save As to keep it")
        self.refresh_all()
        self.show_geometry(reset_view=True)

    def request_close(self) -> None:
        if not self._confirm_discard():
            return
        root = self.winfo_toplevel()
        self.destroy()
        try:
            root.destroy()
        except tk.TclError:
            pass

    def _load_recent_paths(self) -> list[str]:
        try:
            from platformdirs import user_config_path

            path = Path(user_config_path("ANYfem", appauthor=False)) / "recent.json"
            values = json.loads(path.read_text(encoding="utf-8"))
            return [str(value) for value in values if Path(value).suffix == ".anyfem"][:10]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []

    def _remember_recent(self, path: Path) -> None:
        resolved = str(path.resolve())
        self._recent_paths = [
            resolved,
            *(item for item in self._recent_paths if item.casefold() != resolved.casefold()),
        ][:10]
        self._refresh_recent_menu()
        try:
            from platformdirs import user_config_path

            destination = Path(user_config_path("ANYfem", appauthor=False)) / "recent.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
            temporary.write_text(
                json.dumps(self._recent_paths, indent=2) + "\n", encoding="utf-8"
            )
            os.replace(temporary, destination)
        except OSError:
            pass

    def _refresh_recent_menu(self) -> None:
        menu = getattr(self, "_recent_menu", None)
        if menu is None:
            return
        menu.delete(0, "end")
        if not self._recent_paths:
            menu.add_command(label="(none)", state="disabled")
            return
        for path in self._recent_paths:
            menu.add_command(
                label=path,
                command=self.guarded(
                    lambda value=path: self.open_project(value, confirm=True)
                ),
            )

    def _acquire_destination_lock(self, destination: Path) -> ProjectLock:
        current = self._project_lock
        if current is not None and current.project_path == destination.resolve(False):
            return current
        lock = ProjectLock(destination)
        decision = lock.acquire()
        if not decision.acquired:
            raise PermissionError(
                decision.reason or "project is locked by another ANYfem process"
            )
        return lock

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # files
    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        root = self.winfo_toplevel()
        menu = tk.Menu(root)
        files = tk.Menu(menu, tearoff=0)
        files.add_command(
            label="New",
            command=self.guarded(lambda: self.new_project(confirm=True)),
        )
        files.add_command(
            label="Open...",
            command=self.guarded(lambda: self.open_project(confirm=True)),
        )
        self._recent_menu = tk.Menu(files, tearoff=0)
        files.add_cascade(label="Recent projects", menu=self._recent_menu)
        self._refresh_recent_menu()
        files.add_command(
            label="Recover autosave...",
            command=self.guarded(self.recover_autosave),
        )
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
        edit = tk.Menu(menu, tearoff=0)
        edit.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo)
        edit.add_command(label="Redo", accelerator="Ctrl+Y", command=self.redo)
        edit.add_separator()
        edit.add_command(
            label="Command palette...",
            accelerator="Ctrl+P",
            command=self.show_command_palette,
        )
        edit.add_command(
            label="Python scripting console...",
            command=lambda: self.details.select("Scripting"),
        )
        menu.add_cascade(label="Edit", menu=edit)
        view = tk.Menu(menu, tearoff=0)
        for label, name in (
            ("Isometric", "iso"), ("Top", "top"),
            ("Front", "front"), ("Side", "side"),
        ):
            view.add_command(
                label=label, command=lambda value=name: self.viewport.set_view(value)
            )
        view.add_command(label="Frame all", accelerator="F", command=self._fit)
        menu.add_cascade(label="View", menu=view)
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
            except (ValueError, KeyError, OSError, PermissionError) as error:
                self.set_status(str(error), error=True)

        return wrapped

    def new_project(self, *, confirm: bool = False) -> None:
        """Start a document.

        Direct callers keep the historical noninteractive API.  Commands
        reached through the File menu pass ``confirm=True`` and therefore
        protect unsaved work.
        """

        if confirm and not self._confirm_discard():
            return
        self._set_project(default_project())
        self.set_status("new model")
        self.refresh_all()
        self.show_geometry(reset_view=True)

    def open_file_inspector(self, path: Optional[str] = None) -> None:
        """Open ANYfileio's inspector as a child of this application."""

        from anyfileio.gui import open_inspector

        open_inspector(self.winfo_toplevel(), path=path)

    def open_project(
        self, path: Optional[str] = None, *, confirm: bool = False
    ) -> None:
        if confirm and not self._confirm_discard():
            return
        if path is None:
            path = filedialog.askopenfilename(
                filetypes=[("ANYfem project", "*.anyfem"), ("All files", "*.*")]
            )
            if not path:
                return
        source = Path(path)
        lock = ProjectLock(source)
        decision = lock.acquire()
        if decision.can_take_over:
            answer = messagebox.askyesnocancel(
                "Stale ANYfem project lock",
                "The previous ANYfem process no longer owns this project.\n\n"
                "Yes: take over the stale lock and edit the project.\n"
                "No: open the project read-only.\n"
                "Cancel: leave the current project open.",
                parent=self.winfo_toplevel(),
            )
            if answer is None:
                return
            if answer:
                decision = lock.acquire(take_over_stale=True)
        held_lock = lock if decision.acquired else None
        try:
            loaded = load_project(source)
        except BaseException:
            if held_lock is not None:
                held_lock.release()
            raise
        self._set_project(
            loaded,
            path=source,
            project_lock=held_lock,
            read_only=decision.read_only,
        )
        # Generated and imported meshes are optional artifacts; failure to
        # locate one never prevents the editable document from opening.
        try:
            from ..io.artifacts import ArtifactStore

            store = ArtifactStore(path)
            if loaded.mesh_records:
                latest = list(loaded.mesh_records.values())[-1]
                self._mesh_details_record_id = latest.id
                artifact = loaded.artifacts.get(latest.artifact_id or "")
                if artifact is not None:
                    loaded_mesh = store.read_mesh(artifact)
                    self.mesh_record_id = latest.id
                    self._meshes[latest.id] = loaded_mesh
                    if latest.kind == "imported" or self.mesh_record_state(latest) != "stale":
                        self.mesh = loaded_mesh
                    if loaded.mesh_only and loaded.imported_format == "sesam_fem":
                        from ..io.sesam import import_sesam_artifact

                        self.imported = import_sesam_artifact(store, artifact)
                        # Use the verified sidecar association map so mesh
                        # regions and result IDs are byte-for-byte those saved.
                        self.imported.mesh = loaded_mesh
        except (OSError, ValueError):
            self.mesh = None
        try:
            from ..io.artifacts import ArtifactStore

            store = ArtifactStore(path)
            for job_id, record in loaded.jobs.items():
                artifact = loaded.artifacts.get(record.result_artifact_id or "")
                if artifact is None:
                    continue
                try:
                    self.result_datasets[job_id] = store.open_result(artifact)
                except (OSError, ValueError) as error:
                    record.diagnostics.append(
                        {
                            "type": type(error).__name__,
                            "message": f"result artifact unavailable: {error}",
                        }
                    )
            if self.result_datasets:
                self.active_job_id = next(reversed(self.result_datasets))
        except (OSError, ValueError):
            pass
        self._remember_recent(source)
        self.set_status(
            f"opened {self.path.name}"
            + (f" read-only: {decision.reason}" if decision.read_only else "")
        )
        self.refresh_all()
        if loaded.mesh_only and self.mesh is not None:
            self.show_mesh()
            self.viewport.fit()
        else:
            self.show_geometry(reset_view=True)

    def save_project(self, ask: bool = False, path: Optional[str] = None) -> None:
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
        destination = Path(path)
        if not destination.suffix:
            destination = destination.with_suffix(".anyfem")
        if (
            self.session.read_only
            and self.path is not None
            and destination.resolve(False) == self.path.resolve(False)
        ):
            raise PermissionError("this project is locked; use Save As")
        destination_lock = self._acquire_destination_lock(destination)
        previous_lock = self._project_lock
        owns_new_lock = destination_lock is not previous_lock
        try:
            self._save_project_contents(destination)
            saved_path = save_project(self.project, destination)
        except BaseException:
            if owns_new_lock:
                destination_lock.release()
            raise
        if previous_lock is not None and previous_lock is not destination_lock:
            previous_lock.release()
        self._project_lock = destination_lock
        self.session.read_only = False
        self.path = saved_path
        self.session.mark_saved(self.path)
        self._remember_recent(self.path)
        self._update_window_title()
        self.set_status(f"saved {self.path.name}")

    def _save_project_contents(self, destination: Path) -> None:
        """Commit sidecars before the project index JSON."""

        saved_mesh_ids: set[str] = set()
        if self.mesh is not None:
            from ..io.artifacts import ArtifactStore
            from anymesher.serialize import mesh_to_dict

            store = ArtifactStore(destination)
            mesh_record = self.project.mesh_records.get(
                getattr(self, "mesh_record_id", "")
            )
            if mesh_record is None:
                mesh_record = MeshRecord(
                    name="Imported mesh" if self.imported is not None else "Mesh",
                    kind="imported" if self.imported is not None else "generated",
                    source_model_hash=self.session.revision.model_hash,
                    mesh_input_hash="",
                    mesh_hash=canonical_hash(mesh_to_dict(self.mesh)),
                    summary={
                        "nodes": self.mesh.num_nodes,
                        "elements": self.mesh.num_elements,
                    },
                )
            imported_metadata = None
            embedded_source = None
            if self.imported is not None:
                imported_metadata, embedded_source = self.imported.artifact_embedding()
            artifact = store.write_mesh(
                self.mesh,
                mesh_id=mesh_record.id,
                document_id=self.project.document_id,
                model_hash=mesh_record.source_model_hash,
                mesh_hash=mesh_record.mesh_hash,
                structural_preparation=(
                    mesh_record.structural_preparation
                ),
                imported_model=imported_metadata,
                embedded_source=embedded_source,
            )
            mesh_record.artifact_id = artifact.id
            with self.session.transaction("record saved mesh", solver_affecting=False):
                self.project.mesh_records[mesh_record.id] = mesh_record
                self.project.artifacts[artifact.id] = artifact
                if self.imported is not None:
                    self.project.imported_semantics_artifact_id = artifact.id
            self.mesh_record_id = mesh_record.id
            self._meshes[mesh_record.id] = self.mesh
            saved_mesh_ids.add(mesh_record.id)
        self._persist_cached_mesh_artifacts(destination, exclude=saved_mesh_ids)
        self._persist_result_artifacts(destination)

    def _persist_cached_mesh_artifacts(
        self, destination: Path, *, exclude: set[str]
    ) -> None:
        """Persist retained stale generated meshes so they remain inspectable."""

        if not self._meshes:
            return
        from ..io.artifacts import ArtifactStore

        store = ArtifactStore(destination)
        for mesh_id, mesh in tuple(self._meshes.items()):
            if mesh_id in exclude:
                continue
            record = self.project.mesh_records.get(mesh_id)
            if record is None or record.kind == "imported":
                continue
            artifact = store.write_mesh(
                mesh,
                mesh_id=record.id,
                document_id=self.project.document_id,
                model_hash=record.source_model_hash,
                mesh_hash=record.mesh_hash,
            )
            with self.session.transaction(
                "record retained mesh artifact", solver_affecting=False
            ):
                record.artifact_id = artifact.id
                self.project.artifacts[artifact.id] = artifact

    def _persist_result_artifacts(self, destination: Path) -> None:
        """Write in-memory results and copy unopened sidecars for Save As."""

        from ..io.artifacts import ArtifactStore

        # Do not race an automatic post-job write with the explicit save.
        for job_id, future in tuple(self._artifact_futures.items()):
            try:
                artifact = future.result()
            except BaseException as error:
                record = self.project.jobs.get(job_id)
                if record is not None:
                    record.diagnostics.append(
                        {"type": type(error).__name__, "message": str(error)}
                    )
            else:
                source_destination = self._artifact_destinations.get(job_id)
                if job_id in self.project.jobs and source_destination is not None:
                    self._record_result_artifact(
                        job_id, artifact, source_destination
                    )
            self._artifact_futures.pop(job_id, None)
            self._artifact_destinations.pop(job_id, None)

        store = ArtifactStore(destination)
        mesh_id = str(getattr(self, "mesh_record_id", "") or "active-mesh")
        written: set[str] = set()
        for job_id, solution in tuple(self.solutions.items()):
            record = self.project.jobs.get(job_id)
            if record is None:
                continue
            artifact = write_solution_artifact(
                store,
                solution,
                job_id=record.id,
                document_id=self.project.document_id,
                mesh_id=mesh_id,
                model_hash=record.model_hash,
                mesh_hash=record.mesh_hash,
                analysis_hash=record.analysis_hash,
                summary=dict(record.summary),
                diagnostics=tuple(record.diagnostics),
                partial=bool(record.partial),
            )
            self._record_result_artifact(job_id, artifact, destination)
            written.add(artifact.id)

        written.update(self._persist_job_log_artifacts(destination))

        if self.path is None or self.path.resolve(False) == destination.resolve(False):
            return
        source = ArtifactStore(self.path)
        for record in self.project.jobs.values():
            for artifact_id, label in (
                (record.result_artifact_id, "result"),
                (record.log_artifact_id, "job log"),
            ):
                if not artifact_id or artifact_id in written:
                    continue
                artifact = self.project.artifacts.get(artifact_id)
                if artifact is None:
                    continue
                try:
                    copied = store.copy_from(source, artifact)
                except (OSError, ValueError) as error:
                    record.diagnostics.append(
                        {
                            "type": type(error).__name__,
                            "message": f"{label} artifact unavailable during Save As: {error}",
                        }
                    )
                    continue
                self.project.artifacts[copied.id] = copied

    def _persist_job_log_artifacts(self, destination: Path) -> set[str]:
        """Finish automatic log writes and reproduce current logs on save."""

        from ..io.artifacts import ArtifactStore

        written: set[str] = set()
        for job_id, future in tuple(self._log_futures.items()):
            try:
                artifact = future.result()
            except BaseException as error:
                record = self.project.jobs.get(job_id)
                if record is not None:
                    record.diagnostics.append(
                        {"type": type(error).__name__, "message": str(error)}
                    )
            else:
                if job_id in self.project.jobs:
                    self._record_job_log_artifact(job_id, artifact)
                    written.add(artifact.id)
            self._log_futures.pop(job_id, None)
            self._log_destinations.pop(job_id, None)

        store = ArtifactStore(destination)
        terminal = {"completed", "cancelled", "failed", "partial", "interrupted"}
        for job_id, record in self.project.jobs.items():
            status = getattr(record.status, "value", str(record.status))
            if status not in terminal:
                continue
            try:
                entries = self.job_manager.log(job_id)
            except KeyError:
                continue
            artifact = store.write_log(job_id, entries)
            self._record_job_log_artifact(job_id, artifact)
            written.add(artifact.id)
        return written

    def import_sesam_model(self, path: Optional[str] = None) -> None:
        if path is None:
            path = filedialog.askopenfilename(
                filetypes=[("SESAM FEM", "*.FEM *.fem"), ("All files", "*.*")]
            )
            if not path:
                return
        model = import_sesam(path)
        if not self._confirm_discard():
            return
        project = model.project()
        project.mesh_only = True
        project.imported_format = "sesam_fem"
        self._set_project(project, imported=model)
        self.mesh = model.mesh
        # Imported meshes receive their persistent record on save; retaining
        # the object here lets that path treat them like generated meshes.
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

    def _set_project(
        self,
        project: Project,
        *,
        path: Path | None = None,
        imported=None,
        project_lock: ProjectLock | None = None,
        read_only: bool = False,
    ) -> None:
        try:
            self.viewport.cancel_construction()
            self.worker.cancel()
            self.mesh_task_manager.shutdown()
            self.commands.remove_listener(self.refresh_all)
            self.session.remove_listener(self._on_revision_changed)
        except (AttributeError, ValueError):
            pass
        if self._project_lock is not None and self._project_lock is not project_lock:
            self._project_lock.release()
        self._project_lock = project_lock
        self._recovery_epoch += 1
        self._recovery_pending = None
        self.project = project
        self.session = DocumentSession(project, selection=self.selection, path=path)
        self.commands = self.session.commands
        self.session.read_only = bool(
            read_only or getattr(project, "read_only_reason", None)
        )
        self.commands.add_listener(self.refresh_all)
        self.session.add_listener(self._on_revision_changed)
        self._active_model_hash = self.session.revision.model_hash
        self.job_manager = JobManager(project)
        self.mesh_task_manager = MeshTaskManager()
        self.worker = JobWorkerFacade(self.job_manager)
        self.tree.project = project
        self.imported = imported
        self.path = path
        self.mesh = None
        self._meshes = {}
        self.mesh_record_id = ""
        self.solution = None
        self.solutions = {}
        self.result_datasets = {}
        self._artifact_futures.clear()
        self._artifact_destinations.clear()
        self._log_futures.clear()
        self._log_destinations.clear()
        self._active_mesh_task_id = None
        self._mesh_details_record_id = None
        self.active_job_id = None
        self.selection.clear()
        self._update_window_title()

    def _confirm_discard(self) -> bool:
        if not getattr(self, "session", None) or not self.session.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved ANYfem project",
            "Save the current project before continuing?",
            parent=self.winfo_toplevel(),
        )
        if answer is None:
            return False
        if answer:
            self.save_project()
            return not self.session.dirty
        return True

    def _update_window_title(self) -> None:
        root = self.winfo_toplevel()
        name = self.path.name if self.path is not None else self.project.name
        dirty = " *" if getattr(self, "session", None) and self.session.dirty else ""
        try:
            root.title(f"{name}{dirty} - ANYfem")
        except tk.TclError:  # pragma: no cover - embedded frame
            pass

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
            return self.imported.built(case, project=self.project)
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
            for attribute in ("_autosave_after", "_autosave_hard_after"):
                identifier = getattr(self, attribute, None)
                if identifier is not None:
                    try:
                        self.after_cancel(identifier)
                    except tk.TclError:
                        pass
                    setattr(self, attribute, None)
            if getattr(self, "_job_poll", None) is not None:
                try:
                    self.after_cancel(self._job_poll)
                except tk.TclError:
                    pass
                self._job_poll = None
            self.worker.stop()
            self.mesh_task_manager.shutdown()
            self.selection.remove_listener(self._on_selection_changed)
            self.selection.remove_listener(self.tree.sync_from_selection)
            self.selection.remove_listener(self.viewport._apply_highlight)
            self.commands.remove_listener(self.refresh_all)
            self.session.remove_listener(self._on_revision_changed)
            root = self.winfo_toplevel()
            for sequence, identifier in self._root_bindings:
                try:
                    root.unbind(sequence, identifier)
                except tk.TclError:
                    pass
            self._root_bindings.clear()
            if self._project_lock is not None:
                self._project_lock.release()
                self._project_lock = None
            self._recovery_pending = None
            self._recovery_executor.shutdown(wait=False, cancel_futures=True)
            self._artifact_executor.shutdown(wait=False, cancel_futures=True)
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


def _job_progress_text(message: str, payload: Any = None) -> str:
    """Format strings and ANYsolver ProgressEvents for the live transcript."""

    base = str(message or "").strip()
    if payload is None or isinstance(payload, str):
        return base or "working"

    def value(name: str, default=None):
        attribute = getattr(payload, name, None)
        if attribute is not None:
            return attribute
        getter = getattr(payload, "get", None)
        return getter(name, default) if callable(getter) else default

    stage = str(value("stage", "")).strip().replace("_", " ")
    detail = str(value("message", base) or base).strip()
    qualifiers = []
    completed = value("completed")
    total = value("total")
    fraction = value("fraction")
    iteration = value("iteration")
    if completed is not None and total not in (None, 0, 0.0):
        qualifiers.append(f"{float(completed):g}/{float(total):g}")
    elif fraction is not None:
        qualifiers.append(f"{100.0 * float(fraction):.0f}%")
    if iteration is not None:
        qualifiers.append(f"iteration {int(iteration)}")
    for key, label in (
        ("load_factor", "load"),
        ("time_s", "t"),
        ("residual_norm", "residual"),
    ):
        item = value(key)
        if item is not None:
            suffix = " s" if key == "time_s" else ""
            qualifiers.append(f"{label} {float(item):.5g}{suffix}")
    prefix = stage or "solver"
    line = f"{prefix}: {detail}" if detail and detail != stage else prefix
    return line + (f"  [{' | '.join(qualifiers)}]" if qualifiers else "")


def _record_settings(value: Any) -> Any:
    """Make analysis settings deterministic and JSON-safe for provenance."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _record_settings(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_record_settings(item) for item in value]
    if hasattr(value, "tolist"):
        return _record_settings(value.tolist())
    if hasattr(value, "to_dict"):
        return _record_settings(value.to_dict())
    if is_dataclass(value):
        return _record_settings(asdict(value))
    return {"type": type(value).__name__, "repr": repr(value)}


def _submitted_input_report(
    project: Project,
    definition: AnalysisDefinition,
    options: Dict[str, Any],
    mesh: Any,
    *,
    revision: int,
    model_hash: str,
    mesh_hash: str,
) -> str:
    """Readable, deterministic solver-input record without dumping mesh arrays."""

    load_cases = []
    for case in sorted(project.load_cases.values(), key=lambda item: item.name):
        load_cases.append(
            {
                "id": case.id,
                "name": case.name,
                "follower_pressure": case.follower_pressure,
                "gravity_or_acceleration_m_per_s2": _record_settings(case.gravity),
                "gravity_coordinate_system_id": case.gravity_coordinate_system_id,
                "point_loads": _record_settings(case.point_loads),
                "pressures": _record_settings(case.pressures),
                "line_loads": _record_settings(case.line_loads),
                "surface_tractions": _record_settings(case.surface_tractions),
            }
        )
    support_inputs = []
    for support in project.supports:
        engineering = {
            dof: {
                "value": 1000.0 * float(value),
                "unit": "mm" if dof.startswith("u") else "mrad",
            }
            for dof, value in support.constraints.items()
        }
        support_inputs.append(
            {
                "id": support.id,
                "name": support.name,
                "ref": _record_settings(support.ref),
                "region": _record_settings(support.region),
                "coordinate_system_id": support.coordinate_system_id,
                "constraints_SI": dict(support.constraints),
                "constraints_engineering": engineering,
            }
        )
    mesh_summary = {
        "uuid": getattr(mesh, "id", None),
        "nodes": getattr(mesh, "num_nodes", None),
        "elements": getattr(mesh, "num_elements", None),
        "shell_elements": len(getattr(mesh, "shells", {})) if mesh is not None else 0,
        "beam_elements": len(getattr(mesh, "beams", {})) if mesh is not None else 0,
        "mesh_hash": mesh_hash,
    }
    payload = {
        "record": {
            "project": project.name,
            "revision": int(revision),
            "model_hash": model_hash,
            "note": (
                "Numerical values are SI unless a key states another unit. "
                "Mesh connectivity is identified by mesh_hash and omitted from this UI view."
            ),
        },
        "analysis": definition.to_dict(),
        "submitted_options": _record_settings(options),
        "mesh": mesh_summary,
        "units": project.units.to_dict(),
        "coordinate_systems": [
            item.to_dict()
            for item in sorted(project.coordinate_systems.values(), key=lambda item: item.id)
        ],
        "materials": [
            item.to_dict()
            for item in sorted(project.materials.values(), key=lambda item: item.name)
        ],
        "plate_sections": _record_settings(
            sorted(project.plate_sections.values(), key=lambda item: item.name)
        ),
        "beam_sections": _record_settings(
            sorted(project.beam_sections.values(), key=lambda item: item.name)
        ),
        "section_assignments": [
            item.to_dict()
            for item in sorted(
                project.section_assignments.values(), key=lambda item: item.id
            )
        ],
        "supports": support_inputs,
        "masses": _record_settings(project.masses),
        "load_cases": load_cases,
        "combinations": _record_settings(
            sorted(project.combinations.values(), key=lambda item: item.name)
        ),
        "imperfections": _record_settings(project.imperfections),
        "regions": project.regions.to_list(),
    }
    return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)


def _execute_analysis_job(
    *,
    project: Project,
    solver_function,
    analysis_name: str,
    options: Dict[str, Any],
    progress,
    cancellation_token=None,
):
    """Build, preflight and solve one immutable JobManager request."""

    from ..solve.run import preflight

    resolved = dict(options)
    built = resolved.pop("built", None)
    mesh = resolved.pop("mesh", None)
    if built is None:
        build_options: Dict[str, Any] = {}
        if "combination" in resolved:
            build_options["combination"] = resolved["combination"]
        elif "load_case" in resolved:
            build_options["load_case"] = resolved["load_case"]
        if analysis_name == "Modal":
            build_options.update(require_loads=False, require_supports=False)
        elif analysis_name == "Impact" and resolved.get("load_case") is None:
            build_options.update(require_loads=False, require_supports=True)
        progress("building immutable model snapshot")
        built = build_fe_model(project, mesh, **build_options)

    kinematics = str(resolved.get("kinematics", "von_karman"))
    report = preflight(
        built,
        analysis_type=analysis_name,
        kinematics=kinematics,
        corotational_tangent=str(resolved.get("corotational_tangent", "auto")),
    )
    if report.errors:
        details = "\n".join(
            f"[{issue.code}] {issue.message}"
            + (f" Suggestion: {issue.suggestion}" if issue.suggestion else "")
            for issue in report.errors
        )
        if (
            any(issue.code == "CONSTRAINT003" for issue in report.errors)
            and int(getattr(built.mesh, "automatic_shell_connections", 0)) > 0
        ):
            details += (
                "\n[AUTOMESH001] The submitted mesh contains cyclic automatic "
                "shell-interface ties. Regenerate the mesh with the current "
                "mesher before rerunning; the supports do not need to be changed."
            )
        raise ValueError(f"preflight blocked {analysis_name}:\n{details}")
    for issue in report.warnings:
        progress(f"warning [{issue.code}]: {issue.message}")
    progress("preflight passed")

    resolved["built"] = built
    resolved["progress"] = progress
    # New wrappers advertise this argument explicitly.  Older coordinated
    # packages remain usable; cancellation then stays in the visible
    # 'cancelling' state until their library call returns.
    import inspect

    if cancellation_token is not None and "cancellation_token" in inspect.signature(solver_function).parameters:
        resolved["cancellation_token"] = cancellation_token
    return solver_function(**resolved)


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
