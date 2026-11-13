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


def _window(tmp_path, monkeypatch):
    from anyfem.io import recovery
    from anyfem.ui.qt import app as qt_app
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    monkeypatch.setattr(qt_app, "QSettings", lambda *_: QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    app = QApplication.instance() or QApplication([])
    window = QtFemWindow(viewer_backend="software")
    window.show()
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    return app, window


def _close(app, window):
    window.session.mark_saved()
    if not getattr(window, "_closing", False):
        window.close()
    app.processEvents()


def test_import_option_fields_are_empty_by_default_and_parse(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = sat_import.SatImportOptionsDialog()
    assert dialog.vertex_relocation.text() == "" and dialog.approximate_curve.text() == ""
    assert dialog.options() == {}
    dialog.vertex_relocation.setText("2e-6")
    assert dialog.options() == {"vertex_relocation_tolerance_m": 2e-6}
    dialog.approximate_curve.setText(" 5e-6 ")
    assert dialog.options() == {"vertex_relocation_tolerance_m": 2e-6, "approximate_curve_tolerance_m": 5e-6}
    dialog.approximate_curve.setText("abc")
    with pytest.raises(ValueError, match="Approximate curve tolerance"):
        dialog.options()
    dialog.accept()
    assert dialog.result() == 0  # an unparsable field keeps the dialog open
    dialog.approximate_curve.setText("")
    dialog.accept()
    assert dialog.result() == 1


@pytest.mark.parametrize("given", [None, {"vertex_relocation_tolerance_m": 2e-6, "approximate_curve_tolerance_m": 5e-6}])
def test_opt_ins_reach_the_application_import_only_when_given(tmp_path, monkeypatch, given):
    app, window = _window(tmp_path, monkeypatch)
    seen = []

    def read(path, **options):
        seen.append(options)
        return default_project(), SimpleNamespace()

    monkeypatch.setattr(sat_import, "import_sat_project", read)
    try:
        if given is None:
            sat_import.open_sat_import(window, tmp_path / "example.sat")
        else:
            sat_import.open_sat_import(window, tmp_path / "example.sat", options=given)
        wait_finished(app, window)
        assert len(seen) == 1
        assert {k: v for k, v in seen[0].items() if k != "cancelled"} == (given or {})
    finally:
        _close(app, window)


def test_refusal_naming_an_opt_in_is_shown_plainly_with_its_field(tmp_path, monkeypatch):
    app, window = _window(tmp_path, monkeypatch)
    errors = []
    monkeypatch.setattr(window.dialogs, "show_error", lambda *args: errors.append(args))
    message = "edge 3: approximate curve cache declared to 5e-06 m exceeds the source resolution 1e-09; approximate_curve_tolerance_m opts in"

    def read(path, **options):
        raise ValueError(message)

    monkeypatch.setattr(sat_import, "import_sat_project", read)
    try:
        sat_import.open_sat_import(window, tmp_path / "example.sat")
        wait_finished(app, window)
        assert len(errors) == 1
        shown = errors[0][1]
        assert message in shown
        assert "Approximate curve tolerance (m)" in shown
    finally:
        _close(app, window)


def test_refusal_without_an_opt_in_name_gets_no_field_hint(tmp_path, monkeypatch):
    app, window = _window(tmp_path, monkeypatch)
    errors = []
    monkeypatch.setattr(window.dialogs, "show_error", lambda *args: errors.append(args))

    def read(path, **options):
        raise ValueError("SAT entity 4 uses an unsupported surface")

    monkeypatch.setattr(sat_import, "import_sat_project", read)
    try:
        sat_import.open_sat_import(window, tmp_path / "example.sat")
        wait_finished(app, window)
        assert errors[0][1] == "SAT entity 4 uses an unsupported surface"
    finally:
        _close(app, window)
