from __future__ import annotations

import ast
from pathlib import Path

import pytest

from anyfem import Project, WorkbenchController
from anyfem.application import StatusMessage, TaskPresenter
from anyfem.commands import AddPoint
from anyfem.presentation import Scene, VisualizationStyle
from anyfem.ui.tk_adapters import (
    CallbackStatusPort,
    TkClipboardPort,
    TkDialogPort,
    TkSchedulerPort,
)


class _JobManager:
    running = False
    queued = ()
    active_job_id = None

    def __init__(self, project) -> None:
        self.project = project


class _MeshTaskManager:
    busy = False

    def __init__(self) -> None:
        self.closed = False

    def shutdown(self) -> None:
        self.closed = True


class _Status:
    def __init__(self) -> None:
        self.messages: list[StatusMessage] = []

    def publish(self, message: StatusMessage) -> None:
        self.messages.append(message)


def _controller(project: Project | None = None) -> WorkbenchController:
    return WorkbenchController(
        project,
        job_manager_factory=_JobManager,
        mesh_task_manager_factory=_MeshTaskManager,
    )


def test_workbench_owns_commands_state_and_coarse_events() -> None:
    controller = _controller(Project("headless workbench"))
    events = []
    controller.add_listener(events.append)

    vertex = controller.execute(AddPoint(1.0, 2.0, 3.0))
    controller.set_view_mode("mesh")

    assert vertex == 1
    assert controller.project.geometry.vertices[vertex].position.tolist() == [
        1.0,
        2.0,
        3.0,
    ]
    assert controller.snapshot().view_mode == "mesh"
    assert [event.kind for event in events] == [
        "command_executed",
        "state_changed",
    ]
    assert events[-1].field == "view_mode"


def test_project_replacement_is_atomic_to_workbench_observers() -> None:
    controller = _controller(Project("first"))
    old_manager = controller.mesh_task_manager
    replacement = Project("replacement")
    events = []
    controller.add_listener(events.append)

    controller.replace_project(replacement, path=Path("replacement.anyfem"))

    assert controller.project is replacement
    assert controller.session.project is replacement
    assert controller.job_manager.project is replacement
    assert controller.mesh_task_manager is not old_manager
    assert old_manager.closed
    assert controller.snapshot().project_path == Path("replacement.anyfem")
    assert [(event.kind, event.field) for event in events] == [
        ("project_replaced", None)
    ]


def test_structural_owners_cannot_be_replaced_as_loose_state() -> None:
    controller = _controller(Project("owned"))

    with pytest.raises(AttributeError, match="lifecycle methods"):
        controller.update("project", Project("bypass"))

    assert controller.project.name == "owned"
    assert controller.session.project is controller.project


def test_task_presenter_reuses_headless_selection_and_status_contracts() -> None:
    controller = _controller(Project("presenter"))
    vertex = controller.execute(AddPoint(0.0, 0.0, 0.0))
    controller.selection.select(
        controller.project.geometry.entity_ref("vertex", vertex)
    )
    status = _Status()
    presenter = TaskPresenter(controller, "Geometry", status=status)

    assert presenter.require_selection("vertex", 1)[0].id == vertex
    assert presenter.view_model().selected_count == 1
    with pytest.raises(ValueError, match="Line mode"):
        presenter.require_selection("edge")
    try:
        raise RuntimeError("sample failure")
    except RuntimeError as error:
        presenter.report_error(error)
    assert status.messages[-1].error
    assert status.messages[-1].diagnostic["type"] == "RuntimeError"


def test_presentation_api_is_plain_data_and_imports_no_toolkit() -> None:
    assert Scene().faces == []
    assert VisualizationStyle().render_mode == "Shaded with edges"
    root = Path(__file__).resolve().parents[1] / "src" / "anyfem" / "presentation"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(
            name.split(".")[0] in {"tkinter", "PySide6", "PyQt6"}
            for name in imported
        ), path


def test_only_tk_adapter_knows_tk_scheduling_method_names() -> None:
    application = Path(__file__).resolve().parents[1] / "src" / "anyfem" / "application"
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in application.rglob("*.py")
    )
    assert ".after(" not in sources
    assert ".after_cancel(" not in sources


class _Widget:
    def __init__(self) -> None:
        self.calls = []
        self.clipboard = ""

    def after(self, delay, callback):
        self.calls.append(("after", delay, callback))
        return "scheduled"

    def after_cancel(self, identifier):
        self.calls.append(("cancel", identifier))

    def clipboard_clear(self):
        self.clipboard = ""

    def clipboard_append(self, text):
        self.clipboard += text

    def update_idletasks(self):
        self.calls.append(("idle",))


class _MessageBox:
    def __init__(self) -> None:
        self.calls = []

    def showerror(self, *args, **kwargs):
        self.calls.append(("error", args, kwargs))

    def askyesno(self, *args, **kwargs):
        self.calls.append(("confirm", args, kwargs))
        return True

    def askyesnocancel(self, *args, **kwargs):
        self.calls.append(("save", args, kwargs))
        return None


class _FileDialog:
    @staticmethod
    def askopenfilename(**_options):
        return "opened.anyfem"

    @staticmethod
    def asksaveasfilename(**_options):
        return "saved.anyfem"


class _SimpleDialog:
    @staticmethod
    def askstring(*_args, **_kwargs):
        return "renamed"


def test_tk_ports_translate_toolkit_calls_without_application_logic() -> None:
    widget = _Widget()
    scheduler = TkSchedulerPort(widget)
    callback = lambda: None
    assert scheduler.call_later(25, callback) == "scheduled"
    scheduler.cancel_call("scheduled")
    assert widget.calls[:2] == [
        ("after", 25, callback),
        ("cancel", "scheduled"),
    ]

    clipboard = TkClipboardPort(widget)
    clipboard.copy_text("diagnosis")
    assert widget.clipboard == "diagnosis"

    owner = object()
    messagebox = _MessageBox()
    dialogs = TkDialogPort(
        lambda: owner,
        messagebox=messagebox,
        filedialog=_FileDialog,
        simpledialog=_SimpleDialog,
    )
    dialogs.show_error("Failure", "details")
    assert dialogs.confirm("Continue", "question") is True
    assert dialogs.confirm_save("Save", "question") is None
    assert dialogs.ask_text("Rename", "Name", initial="old") == "renamed"
    assert dialogs.open_file() == "opened.anyfem"
    assert dialogs.save_file() == "saved.anyfem"
    assert all(call[2]["parent"] is owner for call in messagebox.calls)

    messages = []
    status = CallbackStatusPort(
        lambda text, **options: messages.append((text, options))
    )
    status.publish(StatusMessage("bad", error=True, diagnostic={"id": 1}))
    assert messages == [
        ("bad", {"error": True, "diagnostic": {"id": 1}})
    ]
