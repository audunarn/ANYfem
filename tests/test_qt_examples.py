"""Exercise example activation and unsaved-document protection in Qt."""
import os
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QMessageBox
from test_qt_geometry_actions import qapp, window


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from anyfem.ui.qt import app
    monkeypatch.setattr(app, "QSettings", lambda *_: QSettings(str(tmp_path/"layout.ini"), QSettings.IniFormat))


def test_examples_menu_creates_section(window, qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Ok)
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    assert "Examples" in [action.text() for action in window.menuBar().actions()]
    action = window._actions["Floating barge section…"]
    assert action.toolTip()
    action.trigger(); qapp.processEvents()
    assert len(window.project.geometry.faces) == 5
    assert len(window.project.edge_sections) == 188
    assert len([point for point in window.viewport._scene.points
                if point.ref is not None and point.ref.kind == "vertex"]) == 8
    assert len(window.tree._rows["Points"]) == 8
    assert window.session.dirty and window.viewport._scene.faces
    path = tmp_path/"barge.anyfem"
    window.save_project(path=str(path))
    window.new_project(); window.open_project(str(path))
    assert len(window.project.geometry.faces) == 5
    assert len(window.project.edge_sections) == 188
    from anyfem import commands
    from anygeometry.entities import EntityRef
    identifier = next(iter(window.project.geometry.vertices))
    before = window.project.geometry.vertex_position(identifier).copy()
    window.run(commands.MoveEntities((EntityRef("vertex", identifier),), (0,0,.05)))
    window.undo()
    import numpy as np
    np.testing.assert_allclose(window.project.geometry.vertex_position(identifier), before)
    assert len(window.project.edge_sections) == 188


def test_example_cancel_and_discard_declined(window, monkeypatch):
    original = window.project
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Cancel)
    window.open_barge_example()
    assert window.project is original
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Ok)
    monkeypatch.setattr(window, "_confirm_discard", lambda: False)
    window.open_barge_example()
    assert window.project is original


def test_examples_menu_creates_large_barge_with_bulkhead_openings(window, qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Ok)
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    action = window._actions["Floating barge section — 15 m with bulkhead openings…"]
    assert action.toolTip()
    action.trigger(); qapp.processEvents()
    assert len(window.project.geometry.faces) == 6
    assert sum(len(face.holes) for face in window.project.geometry.faces.values()) == 6
    assert len(window.project.edge_sections) == 436
    assert window.project.target_size == .25
    assert window.session.dirty and window.viewport._scene.faces
    path = tmp_path/"barge15.anyfem"
    window.save_project(path=str(path))
    window.new_project(); window.open_project(str(path))
    assert len(window.project.geometry.faces) == 6
    assert sum(len(face.holes) for face in window.project.geometry.faces.values()) == 6
    assert len(window.project.edge_sections) == 436
