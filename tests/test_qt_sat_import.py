"""SAT import preserves the open document until a complete result is accepted."""
import os
import time
from types import SimpleNamespace

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("opt-in Qt workbench", allow_module_level=True)
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from anyfem.application.controller import default_project
from anyfem.ui.qt.app import QtFemWindow
from anyfem.ui.qt import sat_import


def wait_finished(app, window):
    deadline = time.monotonic() + 10
    while window._sat_import is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert window._sat_import is None


@pytest.mark.parametrize("outcome", ["success", "failure", "empty_failure", "cancel", "stale", "closing"])
def test_sat_import_is_atomic_and_cancellable(tmp_path, monkeypatch, outcome):
    from anyfem.io import recovery
    from anyfem.ui.qt import app as qt_app
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    monkeypatch.setattr(qt_app, "QSettings", lambda *_: QSettings(str(tmp_path / "settings.ini"),QSettings.IniFormat))
    app = QApplication.instance() or QApplication([])
    window = QtFemWindow(viewer_backend="software")
    window.show()
    original = window.project
    result = default_project()
    result.name = "Imported SAT"
    errors = []
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    monkeypatch.setattr(window.dialogs, "show_error", lambda *args: errors.append(args))

    def read(path, **options):
        if outcome == "closing":
            deadline = time.monotonic() + 5
            while not options["cancelled"]() and time.monotonic() < deadline:
                time.sleep(.01)
            assert options["cancelled"]()
            raise ValueError("Import cancelled while closing")
        if outcome == "failure":
            raise ValueError("SAT entity 4 uses an unsupported surface")
        if outcome == "empty_failure":
            raise ValueError()
        return result, SimpleNamespace()

    monkeypatch.setattr(sat_import, "import_sat_project", read)
    try:
        assert "Import SAT geometry…" in window._actions
        sat_import.open_sat_import(window, tmp_path / "example.sat")
        if outcome == "cancel":
            window._sat_import.cancel()
        if outcome == "closing":
            window.close()
        if outcome == "stale":
            with window.session.transaction("Change project during import", solver_affecting=False):
                window.project.name = "Changed during import"
        wait_finished(app, window)
        assert window.project is (result if outcome == "success" else original)
        assert bool(errors) == (outcome in {"failure", "empty_failure"})
        if outcome == "success":
            assert window.session.dirty
    finally:
        window.session.mark_saved()
        if not getattr(window,"_closing",False):
            window.close()
        app.processEvents()
