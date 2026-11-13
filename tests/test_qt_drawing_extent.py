"""Workbench Extent control and the Qt issue publish honouring the extent (spec S7)."""
import json
import os
import time

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("opt-in Qt workbench", allow_module_level=True)
pytest.importorskip("PySide6")


def test_publish_writes_only_the_selected_parts(tmp_path):
    from PySide6.QtWidgets import QApplication
    from anyfem.application.drawing_assembly import assembly_views
    from anyfem.application.drawing_extent import DrawingExtent
    from anyfem.document import DocumentSession
    from anyfem.ui.qt.drawing_issue import publish
    from test_drawing_thickness_side import _Conformal
    app = QApplication.instance() or QApplication([])
    m = _Conformal()
    m.flange(), m.web("web", 0, .5)
    snap = DocumentSession(m.p).snapshot()
    views = assembly_views(m.p, m.part)
    iso = publish(tmp_path / "iso", snap, views, part_id=m.part, extent=DrawingExtent.isometric_only())
    assert [p.name for p in sorted((iso / "views").iterdir())] == ["View-01.dxf"]
    assert not (iso / "Plate-index.csv").exists() and not (iso / "profiles").exists()
    assert (iso / "Drawings.pdf").is_file()
    basis = json.loads((iso / "Drawing-basis.json").read_text())
    assert basis["extent"]["views"] == ["isometric"] and len(basis["views"]) == 1
    full = publish(tmp_path / "full", snap, views, part_id=m.part)
    assert len(list((full / "views").iterdir())) == 4 and (full / "Plate-index.csv").is_file()
    assert "extent" not in json.loads((full / "Drawing-basis.json").read_text())
    assert app


def test_extent_control_persists_and_invalidates_previews(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QFileDialog
    from anyfem.io import recovery
    from anyfem.ui.qt import app as qt_app, drawing_workspace
    from test_drawing_thickness_side import _Conformal

    ini = str(tmp_path / "settings.ini")
    settings = lambda *_: QSettings(ini, QSettings.IniFormat)
    monkeypatch.setattr(qt_app, "QSettings", settings)
    monkeypatch.setattr(drawing_workspace, "QSettings", settings)
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    m = _Conformal()
    m.flange(), m.web("web", 0, .5)
    app = QApplication.instance() or QApplication([])
    window = qt_app.QtFemWindow(project=m.p, viewer_backend="software")
    window.show()
    pane = window.panels["Drawings"]

    def until(condition):
        end = time.monotonic() + 30
        while not condition() and time.monotonic() < end:
            app.processEvents()
            time.sleep(.02)
        assert condition(), pane.feedback.text()
    try:
        window.workspace_actions["Drawings"].trigger()
        pane.part.setCurrentIndex(pane.part.findData(m.part))
        assert pane.extent_preset.currentData() == "all_views"
        assert all(box.isChecked() for box in pane.extent_checks.values())
        pane.preview_btn.click()
        until(lambda: len(pane.views) == 4)
        pane.extent_preset.setCurrentIndex(pane.extent_preset.findData("isometric_only"))
        assert not pane.views and not pane.export_btn.isEnabled()
        assert [k for k, box in pane.extent_checks.items() if box.isChecked()] == ["isometric"]
        assert pane.extent().views == ("isometric",)
        pane.preview_btn.click()
        until(lambda: len(pane.views) == 1)
        # a single check box makes the preset Custom and invalidates again
        pane.extent_checks["front"].setChecked(True)
        assert pane.extent_preset.currentData() == "custom" and not pane.views
        pane.preview_btn.click()
        until(lambda: [v.projection for v in pane.views] == ["isometric", "front"])
        stored = QSettings(ini, QSettings.IniFormat).value("drawings/extent")
        assert json.loads(stored)["views"] == ["isometric", "front"]
        folder = tmp_path / "issue"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(folder), ""))
        pane.export_btn.click()
        until(lambda: not pane.exporting)
        assert len(list((folder / "views").iterdir())) == 2
        assert not (folder / "Plate-index.csv").exists()
        assert json.loads((folder / "Drawing-basis.json").read_text())["extent"]["views"] == ["isometric", "front"]
        # refusing the empty selection of views for a preview
        for box in pane.extent_checks.values():
            box.setChecked(False)
        pane.preview_btn.click()
        assert "view" in pane.feedback.text().lower() and not pane.views
    finally:
        window.session.mark_saved()
        window.close()
        app.processEvents()
