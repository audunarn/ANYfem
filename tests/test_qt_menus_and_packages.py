"""Menu grouping (File > Import/Export/Examples) and Help > Loaded packages in Qt."""
import os
import sys
from pathlib import Path

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QLabel, QMenu, QPushButton, QTableWidget
from test_qt_geometry_actions import qapp, window

IMPORT = ["Import SESAM model", "Import SAT geometry…", "Import CalculiX results", "Import SESAM results"]
EXPORT = ["Export CalculiX deck", "Create plate drawings…"]
EXAMPLES = [
    "5 m barge — internal bracket weld fatigue…",
    "Support B — plate drawings…",
    "Floating barge section…",
    "Floating barge section — end moment on a distributing connection…",
    "Floating barge section — end moment on a rigid connection…",
    "Floating barge section — seaway time history (600 load cases)…",
    "Floating barge section — 15 m with bulkhead openings…",
    "Drawing barge — 60 m structural example…",
]


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from anyfem.ui.qt import app
    monkeypatch.setattr(app, "QSettings", lambda *_: QSettings(str(tmp_path / "layout.ini"), QSettings.IniFormat))


def _wait_for_report(dialog, qapp, seconds=30):
    import time
    deadline = time.monotonic() + seconds
    while dialog.report is None and dialog.error is None and time.monotonic() < deadline:
        qapp.processEvents(); time.sleep(0.01)
    assert dialog.error is None and dialog.report is not None
    return dialog.report


def _menu(parent, title):
    return next(menu for menu in parent.findChildren(QMenu) if menu.title() == title)


def test_file_menu_groups_import_export_and_examples(window):
    assert [action.text() for action in window.menuBar().actions()] == ["File", "Edit", "View", "Analysis", "Workspace", "Help"]
    file = _menu(window.menuBar(), "File")
    entries = [action.text() for action in file.actions() if not action.isSeparator()]
    assert entries == ["New", "Open", "Recent projects", "Save", "Save as", "Recover autosave",
                       "Open file inspector", "Import", "Export", "Examples", "Close"]
    for title, names in (("Import", IMPORT), ("Export", EXPORT), ("Examples", EXAMPLES)):
        submenu = _menu(file, title)
        assert submenu.parent() is file
        assert submenu.actions() == [window._actions[name] for name in names]
    moved = set(IMPORT + EXPORT + EXAMPLES)
    elsewhere = [action.text() for menu in window.menuBar().findChildren(QMenu)
                 if menu.title() not in {"Import", "Export", "Examples"}
                 for action in menu.actions() if action.text() in moved]
    assert elsewhere == []
    # Moved, not changed: shortcuts and the menu-bar commands keep theirs.
    assert all(window._actions[name].shortcut().isEmpty() for name in moved)
    assert [window._actions[name].shortcut().toString() for name in ("New", "Open", "Save", "Save as", "Close")] == \
        ["Ctrl+N", "Ctrl+O", "Ctrl+S", "Ctrl+Shift+S", "Ctrl+Q"]
    assert window._actions["Floating barge section…"].toolTip().startswith("Create an S355 barge section")


def test_help_loaded_packages_dialog_lists_loaded_paths_and_copies(window, qapp, monkeypatch):
    import anyfem
    help_menu = _menu(window.menuBar(), "Help")
    assert [action.text() for action in help_menu.actions()] == ["Copy diagnosis", "Loaded packages…"]
    assert not hasattr(window, "_loaded_packages_dialog")  # nothing collected at start-up
    window._actions["Loaded packages…"].trigger(); qapp.processEvents()
    dialog = window._loaded_packages_dialog
    assert dialog.isVisible() and dialog.windowTitle() == "Loaded packages"
    report = _wait_for_report(dialog, qapp)
    assert report.elapsed_ms < 2000  # measured ~70-90 ms warm, on the worker thread
    table = dialog.findChild(QTableWidget, "LoadedPackagesTable")
    assert table.rowCount() == len(report.rows)
    cells = {table.item(index, 0).text(): [table.item(index, column).text() for column in range(table.columnCount())]
             for index in range(table.rowCount())}
    anyfem_row = cells["ANYfem"]
    assert anyfem_row[1].lstrip("⚠ ") not in ("", "—")
    assert anyfem_row[3].splitlines()[0] == str(Path(anyfem.__file__).parent)
    for module_name in ("PySide6", "numpy"):
        row = next(row for row in report.rows if row.module == module_name)
        assert row.loaded and cells[row.name][1].lstrip("⚠ ") == row.version != "unknown"
    not_loaded = [row for row in report.rows if not row.loaded]
    assert all(cells[row.name][1:3] == ["—", "not loaded in this session"] for row in not_loaded)
    copied = []
    monkeypatch.setattr(window.clipboard, "copy_text", copied.append)
    dialog.findChild(QPushButton, "CopyLoadedPackages").click()
    assert copied == [report.text()]
    assert f"ANYfem: {report.rows[0].version}" in copied[0]
    dialog.close(); qapp.processEvents()


def test_loaded_packages_dialog_opens_before_collection_finishes(window, qapp, monkeypatch):
    """Provenance reads can stall on a network share; the GUI thread must not wait."""
    import threading
    from anyfem.ui.qt import loaded_packages as lp
    release, started = threading.Event(), threading.Event()
    real = lp.collect_loaded_packages

    def slow(modules=None, qt_runtime=None):
        started.set()
        release.wait(10)
        return real(modules, qt_runtime)

    monkeypatch.setattr(lp, "collect_loaded_packages", slow)
    window._actions["Loaded packages…"].trigger(); qapp.processEvents()
    dialog = window._loaded_packages_dialog
    try:
        assert dialog.isVisible() and dialog.report is None
        assert started.wait(5)
        copy = dialog.findChild(QPushButton, "CopyLoadedPackages")
        assert not copy.isEnabled()
        assert "Collecting" in dialog.findChild(QLabel, "LoadedPackagesSummary").text()
    finally:
        release.set()
    report = _wait_for_report(dialog, qapp)
    assert copy.isEnabled() and dialog.findChild(QTableWidget, "LoadedPackagesTable").rowCount() == len(report.rows)
    dialog.close(); qapp.processEvents()


def test_closing_the_dialog_early_drops_the_late_result(window, qapp, monkeypatch):
    import threading
    import time
    from anyfem.ui.qt import loaded_packages as lp
    release, done = threading.Event(), threading.Event()
    real = lp.collect_loaded_packages

    def slow(modules=None, qt_runtime=None):
        release.wait(10)
        try:
            return real(modules, qt_runtime)
        finally:
            done.set()

    import shiboken6
    from PySide6.QtCore import QEvent, QTimer
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *info: errors.append(info))  # Qt slot exceptions land here
    monkeypatch.setattr(lp, "collect_loaded_packages", slow)
    window._actions["Loaded packages…"].trigger(); qapp.processEvents()
    dialog = window._loaded_packages_dialog
    poller = dialog.findChild(QTimer, "LoadedPackagesPoller")
    assert poller.isActive()
    dialog.close(); qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not shiboken6.isValid(dialog) and not shiboken6.isValid(poller)  # the poller died with it
    release.set()
    assert done.wait(10)
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        qapp.processEvents(); time.sleep(0.01)  # nothing is left to deliver the late result to
    assert errors == []


def test_repeated_opens_do_not_retain_closed_dialogs(window, qapp):
    import gc
    import weakref
    from PySide6.QtCore import QEvent
    references = []
    for _ in range(3):
        window._actions["Loaded packages…"].trigger(); qapp.processEvents()
        dialog = window._loaded_packages_dialog
        _wait_for_report(dialog, qapp)
        references.append(weakref.ref(dialog))
        dialog.close(); qapp.processEvents()
        qapp.sendPostedEvents(None, QEvent.DeferredDelete)
        del dialog
    window._loaded_packages_dialog = None
    gc.collect()
    assert [reference() for reference in references] == [None, None, None]


def test_a_failing_qt_version_call_does_not_stop_the_dialog(window, qapp, monkeypatch):
    from PySide6 import QtCore

    def broken():
        raise RuntimeError("qVersion replaced")

    monkeypatch.setattr(QtCore, "qVersion", broken)
    window._actions["Loaded packages…"].trigger(); qapp.processEvents()
    dialog = window._loaded_packages_dialog
    report = _wait_for_report(dialog, qapp)
    assert report.qt_runtime == "unknown"
    dialog.close(); qapp.processEvents()
