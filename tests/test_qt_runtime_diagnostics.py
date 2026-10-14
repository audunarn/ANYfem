"""Headless contracts for the on-demand Qt runtime diagnosis snapshot.

No QApplication, window or GL context is ever created here.  The production
Qt routes are exercised through unbound methods and a ``QtFemWindow`` subclass
whose ``__init__`` never runs, which is enough to prove that the report and
both copy routes consume the snapshot helper without a desktop session.  The
fakes mirror the production shape: a window owns a presentation viewport,
the viewport owns the ANY3dView canvas viewer, and the canvas owns the
``event_widget`` surface plus the optional cached ``diagnostic_snapshot``.
"""
from __future__ import annotations

import ast
import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import anyfem.ui.qt.runtime_diagnostics as runtime_diagnostics
from anyfem import Project
from anyfem.diagnostics import ErrorDiagnostic, build_diagnostic_report
from anyfem.ui.qt.runtime_diagnostics import (
    extend_report_with_qt_runtime,
    qt_runtime_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

_NO_APP_NOTE = "unavailable (RuntimeError: no QApplication instance in this process)"


def _production_viewport(*, event_size=(800, 600), event_dpr=1.25,
                         requested_backend="auto", active_backend="software",
                         diagnostic_snapshot=None, with_canvas=True):
    """Separate viewport/canvas/event_widget objects shaped like production."""
    event_widget = SimpleNamespace(
        width=lambda: event_size[0],
        height=lambda: event_size[1],
        devicePixelRatioF=lambda: event_dpr,
    )
    canvas = SimpleNamespace(event_widget=event_widget)
    if diagnostic_snapshot is not None:
        canvas.diagnostic_snapshot = diagnostic_snapshot
    viewport = SimpleNamespace(
        requested_backend=requested_backend, active_backend=active_backend)
    if with_canvas:
        viewport.canvas = canvas
    return viewport


class _RecordingWindow:
    """Plain fake window: geometry reads are recorded, nothing else exists."""

    def __init__(self, viewport=None):
        self.calls: list[str] = []
        self.viewport = (viewport if viewport is not None
                         else _production_viewport())

    def width(self):
        self.calls.append("width")
        return 1400

    def height(self):
        self.calls.append("height")
        return 900

    def devicePixelRatioF(self):
        self.calls.append("device_pixel_ratio")
        return 1.5

    def isVisible(self):
        self.calls.append("visible")
        return True

    def isMinimized(self):
        self.calls.append("minimized")
        return False


def test_snapshot_covers_pid_versions_uptime_window_viewer_and_workbench_count():
    window = _RecordingWindow(viewport=_production_viewport(
        diagnostic_snapshot=lambda: {"identity": "cached viewer", "lifecycle": "idle"}))
    snapshot = qt_runtime_snapshot(
        window,
        pid_reader=lambda: 4242,
        versions_reader=lambda: {"pyside6": "6.9.0", "qt_runtime": "6.9.0"},
        uptime_reader=lambda: {"uptime_ms": 123456, "source": "GetTickCount64"},
        workbench_counter=lambda _window: 1,
    )
    assert snapshot == {
        "pid": 4242,
        "qt_versions": {"pyside6": "6.9.0", "qt_runtime": "6.9.0"},
        "system_uptime": {"uptime_ms": 123456, "source": "GetTickCount64"},
        "window": {
            "size": [1400, 900],
            "device_pixel_ratio": 1.5,
            "visible": True,
            "minimized": False,
            "event_widget_size": [800, 600],
            "event_widget_device_pixel_ratio": 1.25,
        },
        "viewer": {
            "viewport_class": "SimpleNamespace",
            "viewer_class": "SimpleNamespace",
            "requested_backend": "auto",
            "active_backend": "software",
            "diagnostic_snapshot": {"identity": "cached viewer", "lifecycle": "idle"},
        },
        "workbenches_in_process": 1,
    }


def test_system_uptime_reader_reports_a_value_or_an_explicit_reason():
    uptime = runtime_diagnostics._read_system_uptime()
    if uptime.get("uptime_ms") is not None:
        assert isinstance(uptime["uptime_ms"], int)
        assert 0 <= uptime["uptime_ms"] < 2 ** 64
        assert uptime["source"] in {"GetTickCount64", "CLOCK_BOOTTIME"}
    else:
        assert uptime.get("unavailable"), "unavailable uptime must say why"


def test_windows_uptime_binding_is_explicitly_unsigned_64_bit():
    if sys.platform != "win32":
        pytest.skip("Windows GetTickCount64 binding")
    uptime = runtime_diagnostics._read_system_uptime()
    assert uptime["source"] == "GetTickCount64"
    assert isinstance(uptime["uptime_ms"], int)
    assert 0 <= uptime["uptime_ms"] < 2 ** 64
    binding = ctypes.windll.kernel32.GetTickCount64
    assert binding.restype is ctypes.c_uint64
    assert binding.argtypes == []


def test_windows_uptime_oracle_survives_uptimes_beyond_32_bits(monkeypatch):
    """A host up past ~24.8 days must not truncate or turn negative."""
    if sys.platform != "win32":
        pytest.skip("Windows GetTickCount64 binding")

    class _FakeGetTickCount64:
        def __init__(self, value):
            self.value = value
            self.restype = None
            self.argtypes = None

        def __call__(self):
            return self.value

    fake = _FakeGetTickCount64(2 ** 33 + 123)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(
        kernel32=SimpleNamespace(GetTickCount64=fake)))
    uptime = runtime_diagnostics._read_system_uptime()
    assert uptime == {"uptime_ms": 2 ** 33 + 123, "source": "GetTickCount64"}
    assert fake.restype is ctypes.c_uint64
    assert fake.argtypes == []


def test_snapshot_field_failures_degrade_to_bounded_unavailable_notes():
    def explode():
        raise RuntimeError("versions exploded")

    snapshot = qt_runtime_snapshot(
        _RecordingWindow(), pid_reader=lambda: 11, versions_reader=explode,
        workbench_counter=lambda _window: 1)
    assert snapshot["pid"] == 11
    assert snapshot["qt_versions"] == "unavailable (RuntimeError: versions exploded)"
    assert snapshot["window"]["size"] == [1400, 900]


def test_window_read_failures_are_bounded_per_field():
    class _BrokenWindow:
        viewport = None

        def width(self):
            raise OSError("no window")

        def height(self):
            return 900

        def devicePixelRatioF(self):
            return 2.0

        def isVisible(self):
            return True

        def isMinimized(self):
            return False

    snapshot = qt_runtime_snapshot(_BrokenWindow(), workbench_counter=lambda _w: 1)
    window = snapshot["window"]
    assert window["size"] == "unavailable (OSError: no window)"
    assert window["event_widget_size"] == "unavailable (window has no canvas event_widget surface)"
    assert window["event_widget_device_pixel_ratio"] == "unavailable (window has no canvas event_widget surface)"
    assert window["device_pixel_ratio"] == 2.0


def test_fractional_device_pixel_ratios_are_preserved_on_window_and_event_widget():
    window = _RecordingWindow(viewport=_production_viewport(
        event_dpr=2.75, diagnostic_snapshot=lambda: {}))
    snapshot = qt_runtime_snapshot(window, workbench_counter=lambda _w: 1)
    assert snapshot["window"]["device_pixel_ratio"] == 1.5
    assert snapshot["window"]["event_widget_device_pixel_ratio"] == 2.75


def test_missing_or_raising_viewer_snapshots_are_both_explicit():
    missing = qt_runtime_snapshot(
        _RecordingWindow(viewport=_production_viewport()),
        workbench_counter=lambda _w: 1)
    assert missing["viewer"]["diagnostic_snapshot"] == (
        "unavailable (viewer provides no diagnostic_snapshot)")

    def explode():
        raise ValueError("viewer snapshot failed")

    raising = qt_runtime_snapshot(
        _RecordingWindow(viewport=_production_viewport(
            requested_backend="gpu", active_backend="gpu",
            diagnostic_snapshot=explode)),
        workbench_counter=lambda _w: 1)
    assert raising["viewer"]["diagnostic_snapshot"] == (
        "unavailable (ValueError: viewer snapshot failed)")
    assert raising["viewer"]["active_backend"] == "gpu"

    no_canvas = qt_runtime_snapshot(
        _RecordingWindow(viewport=_production_viewport(with_canvas=False)),
        workbench_counter=lambda _w: 1)
    assert no_canvas["viewer"]["viewer_class"] == "unavailable (viewport has no canvas viewer)"
    assert no_canvas["viewer"]["diagnostic_snapshot"] == "unavailable (viewport has no canvas viewer)"
    assert no_canvas["viewer"]["requested_backend"] == "auto"


def test_workbench_counter_reads_only_the_lazy_qt_toplevel_widget_list(monkeypatch):
    class _Workbench:
        pass

    class _UnrelatedSceneObject:
        pass

    window = _Workbench()
    other = _Workbench()
    monkeypatch.setattr(runtime_diagnostics, "_qt_toplevel_widgets", lambda: [
        window, other, _UnrelatedSceneObject(), SimpleNamespace()])
    snapshot = qt_runtime_snapshot(window, pid_reader=lambda: 5)
    assert snapshot["workbenches_in_process"] == 2


def test_workbench_count_is_unavailable_without_a_qt_application(monkeypatch):
    monkeypatch.setattr(runtime_diagnostics, "_qt_toplevel_widgets", lambda: None)
    snapshot = qt_runtime_snapshot(_RecordingWindow(), pid_reader=lambda: 5)
    assert snapshot["workbenches_in_process"] == _NO_APP_NOTE


def test_window_counting_never_scans_gc_or_constructs_a_qt_application(monkeypatch):
    """The counter avoids gc and QApplication construction, proven on the AST
    call/import graph and by running the real counter against forbidding
    fakes.  Docstrings may legitimately name what is avoided, so the raw
    source text is never searched."""
    source = Path(runtime_diagnostics.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called.add(ast.unparse(node.func))
            if isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                called.add(node.func.id)
        elif isinstance(node, ast.Import):
            assert all(alias.name != "gc" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "gc"
    assert "QApplication" not in called, "no QApplication may be constructed"
    assert "get_objects" not in called, "no gc scan of every Python object"
    assert "topLevelWidgets" in called

    class _Workbench:
        pass

    class _UnrelatedSceneObject:
        pass

    class _FakeQApplication:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("QApplication constructed")

        @staticmethod
        def instance():
            workbench, other = _Workbench(), _Workbench()
            return SimpleNamespace(topLevelWidgets=lambda: [
                workbench, other, _UnrelatedSceneObject(), SimpleNamespace()])

    def _forbidden_get_objects():
        raise AssertionError("gc.get_objects scanned")

    import gc

    monkeypatch.setattr(gc, "get_objects", _forbidden_get_objects)
    fake_widgets = ModuleType("PySide6.QtWidgets")
    fake_widgets.QApplication = _FakeQApplication
    real_widgets = sys.modules.get("PySide6.QtWidgets")
    if real_widgets is not None:
        monkeypatch.setattr(real_widgets, "QApplication", _FakeQApplication)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", fake_widgets)

    window = _Workbench()
    assert runtime_diagnostics._count_workbenches(window) == 2


def test_snapshot_never_touches_the_live_renderer_or_mutates_the_window():
    class _Canvas:
        def __init__(self):
            self.event_widget = SimpleNamespace(
                width=lambda: 800, height=lambda: 600,
                devicePixelRatioF=lambda: 1.25)

        @property
        def ctx(self):
            raise AssertionError("live renderer context read")

        def redraw(self):
            raise AssertionError("renderer redraw requested")

        def query_point(self, *_point):
            raise AssertionError("live picking query")

    window = _RecordingWindow(viewport=SimpleNamespace(
        requested_backend="auto", active_backend="software", canvas=_Canvas()))
    snapshot = qt_runtime_snapshot(window, workbench_counter=lambda _w: 1)
    assert snapshot["viewer"]["requested_backend"] == "auto"
    assert snapshot["viewer"]["diagnostic_snapshot"] == (
        "unavailable (viewer provides no diagnostic_snapshot)")
    assert snapshot["window"]["event_widget_size"] == [800, 600]
    assert window.calls == ["width", "height", "device_pixel_ratio",
                           "visible", "minimized"]


def test_json_safe_truncates_long_strings_and_bounds_collections_lazily():
    long_text = "x" * 5000
    assert runtime_diagnostics._json_safe(long_text) == long_text[:197] + "..."

    class _HugeRepr:
        def __repr__(self):
            return "r" * 5000

    assert runtime_diagnostics._json_safe(_HugeRepr()) == "r" * 197 + "..."

    class _TrackingList(list):
        consumed = 0

        def __iter__(self):
            for index in range(len(self)):
                self.consumed += 1
                yield list.__getitem__(self, index)

    tracking_list = _TrackingList(range(5000))
    assert len(runtime_diagnostics._json_safe(tracking_list)) == 100
    assert tracking_list.consumed == 100, "no eager whole-list copy before the bound"

    class _TrackingDict(dict):
        consumed = 0

        def items(self):
            for key in dict.keys(self):
                self.consumed += 1
                yield key, dict.__getitem__(self, key)

    tracking_dict = _TrackingDict(
        (f"key{index}", index) for index in range(5000))
    bounded = runtime_diagnostics._json_safe(tracking_dict)
    assert len(bounded) == 100
    assert tracking_dict.consumed == 100, "no eager whole-dict copy before the bound"

    nested = current = {}
    for _ in range(20):
        current["child"] = {}
        current = current["child"]
    safe = runtime_diagnostics._json_safe(nested)
    for _ in range(9):
        safe = safe["child"]
    assert safe == "<maximum diagnostic depth reached>"


def test_module_starts_no_timers_threads_tracing_or_environment_reads():
    source = Path(runtime_diagnostics.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {alias.name for alias in node.names} & {
                "threading", "trace", "atexit", "tracemalloc", "gc"}, (
                "no background machinery or gc scans")
        if isinstance(node, ast.ImportFrom) and node.module in {
                "threading", "trace", "atexit", "tracemalloc", "gc"}:
            raise AssertionError("no background machinery or gc scans")
        if isinstance(node, ast.Attribute) and node.attr == "environ":
            raise AssertionError("the snapshot must not read environment variables")
        if isinstance(node, ast.Name) and node.id in {"QTimer", "QThread", "QTimerEvent"}:
            raise AssertionError("no Qt background machinery")


def test_extend_report_preserves_inherited_report_and_adds_qt_runtime():
    project = Project("qt lag")
    error = ErrorDiagnostic.capture("boom", details={"traceback": "full worker trace"})
    inherited = build_diagnostic_report(
        project, errors=(error,), context={"details_page": "Scripts"},
        recent_commands=("commands.run(commands.AddPoint(0, 0, 0))",))
    extended = extend_report_with_qt_runtime(inherited, _RecordingWindow())
    payload = json.loads(extended)
    original = json.loads(inherited)
    for key, value in original.items():
        assert payload[key] == value, f"inherited field {key} changed"
    assert set(payload) == set(original) | {"qt_runtime"}
    assert payload["qt_runtime"]["window"]["size"] == [1400, 900]
    assert extended.endswith("\n")


def test_snapshot_failure_never_breaks_the_report(monkeypatch):
    def explode(_window):
        raise RuntimeError("snapshot subsystem failed")

    monkeypatch.setattr(runtime_diagnostics, "qt_runtime_snapshot", explode)
    inherited = build_diagnostic_report(Project("kept"))
    extended = extend_report_with_qt_runtime(inherited, _RecordingWindow())
    payload = json.loads(extended)
    assert payload["qt_runtime"] == {"unavailable": "RuntimeError: snapshot subsystem failed"}
    assert payload["project"]["name"] == "kept"


def test_non_json_report_is_returned_unchanged():
    assert extend_report_with_qt_runtime("not a report", _RecordingWindow()) == "not a report"


class _FakeWorkbench:
    """Smallest controller stand-in that satisfies the workflow field descriptors."""

    def __init__(self):
        self._fields: dict[str, object] = {}

    def update(self, field, value):
        self._fields[field] = value

    def __getattr__(self, name):
        try:
            return self.__dict__["_fields"][name]
        except KeyError:
            raise AttributeError(name) from None


def _headless_qt_window(copied):
    """A QtFemWindow instance whose QMainWindow side was never constructed."""
    pytest.importorskip("PySide6")
    from anyfem.ui.qt.app import QtFemWindow

    class _HeadlessWindow(QtFemWindow):
        def __init__(self):
            pass  # no QMainWindow, no QApplication, no viewer

        def width(self):
            return 1400

        def height(self):
            return 900

        def devicePixelRatioF(self):
            return 1.5

        def isVisible(self):
            return True

        def isMinimized(self):
            return False

    window = _HeadlessWindow()
    window.workbench = _FakeWorkbench()
    window.project = Project("qt lag diagnosis")
    window.path = None
    window.selection = SimpleNamespace(
        domain=SimpleNamespace(value="geometry"), mode="vertex",
        allowed_kinds=frozenset({"vertex"}), ordered_items=())
    window.details = SimpleNamespace(_current="Scripts")
    window._view_mode = "geometry"
    window.viewport = _production_viewport(
        diagnostic_snapshot=lambda: {"identity": "cached viewer", "lifecycle": "idle"})
    window._status = SimpleNamespace(cget=lambda _key: "Ready")
    window.session = SimpleNamespace(revision=SimpleNamespace(
        sequence=7, document_hash="doc", model_hash="model"))
    window.mesh_record_id = ""
    window._error_diagnostics = [ErrorDiagnostic.capture("solve failed")]
    window.clipboard = SimpleNamespace(copy_text=copied.append)
    return window


def test_qtfemwindow_report_route_consumes_the_snapshot_helper():
    window = _headless_qt_window([])
    report = window.diagnostic_report(
        recent_commands=("commands.run(commands.AddPoint(1, 0, 0))",))
    payload = json.loads(report)
    assert payload["report"] == "ANYfem diagnostic"
    assert payload["errors"][0]["message"] == "solve failed"
    assert payload["project"]["name"] == "qt lag diagnosis"
    assert payload["application"]["renderer_requested"] == "auto"
    assert payload["application"]["details_page"] == "Scripts"
    assert payload["recent_gui_commands"] == ["commands.run(commands.AddPoint(1, 0, 0))"]
    qt = payload["qt_runtime"]
    assert qt["pid"] == os.getpid()
    assert qt["window"]["size"] == [1400, 900]
    assert qt["window"]["device_pixel_ratio"] == 1.5
    assert qt["window"]["event_widget_size"] == [800, 600]
    assert qt["window"]["event_widget_device_pixel_ratio"] == 1.25
    assert qt["window"]["visible"] is True
    assert qt["viewer"]["viewport_class"] == "SimpleNamespace"
    assert qt["viewer"]["requested_backend"] == "auto"
    assert qt["viewer"]["active_backend"] == "software"
    assert qt["viewer"]["diagnostic_snapshot"] == {"identity": "cached viewer", "lifecycle": "idle"}
    assert qt["workbenches_in_process"] == _NO_APP_NOTE
    assert isinstance(qt["qt_versions"]["pyside6"], str)


def test_scripts_copy_diagnosis_route_copies_the_extended_report():
    pytest.importorskip("PySide6")
    from anyfem.ui.qt.app import ScriptTask

    copied: list[str] = []
    window = _headless_qt_window(copied)
    panel = SimpleNamespace(
        app=window,
        command_stream=SimpleNamespace(
            toPlainText=lambda: "commands.run(commands.AddPoint(2, 0, 0))"))
    ScriptTask.copy_diagnosis(panel)
    assert len(copied) == 1
    payload = json.loads(copied[0])
    assert "qt_runtime" in payload
    assert payload["recent_gui_commands"] == ["commands.run(commands.AddPoint(2, 0, 0))"]


def test_help_copy_diagnosis_action_routes_through_the_scripts_panel():
    pytest.importorskip("PySide6")
    from anyfem.ui.qt.app import QtFemWindow

    calls: list[str] = []
    window = SimpleNamespace(panels={"Scripts": SimpleNamespace(
        copy_diagnosis=lambda: calls.append("copied"))})
    QtFemWindow.copy_diagnosis(window)
    assert calls == ["copied"]


def test_help_menu_registers_copy_diagnosis_with_a_tooltip():
    source = (REPO_ROOT / "src/anyfem/ui/qt/app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    menus = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name == "_menus")
    segment = ast.get_source_segment(source, menus)
    assert 'addMenu("Help")' in segment
    registrations = [node for node in ast.walk(menus)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                     and node.func.attr == "_action"
                     and any(isinstance(arg, ast.Constant) and arg.value == "Copy diagnosis"
                             for arg in node.args)]
    assert len(registrations) == 1
    assert any(isinstance(arg, ast.Attribute) and arg.attr == "copy_diagnosis"
               for arg in registrations[0].args)
    assert 'action.setToolTip("Copy the support report' in segment


def test_headless_import_of_the_snapshot_module_loads_neither_qt_nor_tk():
    child = r"""
import json, runpy, sys
from pathlib import Path
root = Path(sys.argv[1])
runpy.run_path(str(root / "run_gui.py"), run_name="anyfem_gui_bootstrap")
sys.path.insert(0, str(root / "src"))
import anyfem
import anyfem.ui.qt.runtime_diagnostics as module
print(json.dumps({
    "anyfem_origin": anyfem.__file__,
    "module_origin": module.__file__,
    "pyside6_loaded": "PySide6" in sys.modules,
    "tkinter_loaded": "tkinter" in sys.modules,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", child, str(REPO_ROOT)],
        capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT))
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["pyside6_loaded"] is False
    assert result["tkinter_loaded"] is False
    assert Path(result["module_origin"]).is_relative_to(REPO_ROOT)
    assert Path(result["anyfem_origin"]).is_relative_to(REPO_ROOT)
