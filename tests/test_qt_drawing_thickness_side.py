"""Workbench representation selector and per-plate thickness side editor."""
import csv
import json
import os
import time

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("opt-in Qt workbench", allow_module_level=True)
pytest.importorskip("PySide6")


def test_representation_and_plate_side_invalidate_previews_and_reach_the_export(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QFileDialog
    from anyfem.io import recovery
    from anyfem.ui.qt import app as qt_app, drawing_workspace
    from test_drawing_thickness_side import _Conformal

    settings = lambda *_: QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(qt_app, "QSettings", settings)
    monkeypatch.setattr(drawing_workspace, "QSettings", settings)
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    m = _Conformal()
    flange, web = m.flange(), m.web("web", 0, .5)
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
        until(lambda: pane.sides.rowCount() == 2)
        assert pane.side_choice(flange).currentData() == "auto"
        assert "front" in pane.sides.item(pane.side_row(flange), 2).text()
        pane.preview_btn.click()
        until(lambda: len(pane.views) == 4)
        solid_front = pane.views[2].paths
        pane.keep.click()
        assert pane.saved_views and pane.export_btn.isEnabled()
        pane.representation.setCurrentIndex(pane.representation.findData("midsurface_2d"))
        assert not pane.views and not pane.saved_views and not pane.export_btn.isEnabled()
        assert QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat).value(
            "drawings/representation") == "midsurface_2d"
        pane.preview_btn.click()
        until(lambda: len(pane.views) == 4)
        assert any("2D mid-surface" in note for note in pane.views[0].notes)
        assert pane.views[2].paths != solid_front
        pane.side_choice(flange).setCurrentIndex(pane.side_choice(flange).findData("back"))
        until(lambda: not pane.views)
        assert window.project.drawing_thickness_sides == {"flange": "back"}
        assert not pane.export_btn.isEnabled()
        assert pane.sides.item(pane.side_row(flange), 2).text().startswith("back")
        pane.preview_btn.click()
        until(lambda: len(pane.views) == 4)
        folder = tmp_path / "issue"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(folder), ""))
        pane.export_btn.click()
        until(lambda: not pane.exporting)
        with (folder / "Plate-index.csv").open(encoding="utf-8") as stream:
            rows = {row["Plate"]: row for row in csv.DictReader(stream)}
        assert rows["flange"]["Thickness side"] == "back (+n = +Z)"
        assert rows["flange"]["Side source"] == "manual: override"
        basis = json.loads((folder / "Drawing-basis.json").read_text())
        assert basis["representation"] == "midsurface_2d"
        window._actions["Undo"].trigger()
        until(lambda: pane.side_choice(flange).currentData() == "auto")
        assert window.project.drawing_thickness_sides == {}
    finally:
        window.session.mark_saved()
        window.close()
        app.processEvents()
