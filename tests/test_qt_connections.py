"""Opt-in real Qt check that connections are reachable from the Loads task."""
from __future__ import annotations

import os

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets", allow_module_level=True)
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from anyfem import commands as cmd
from anyfem.ui.qt.app import QtFemWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from anyfem.io import recovery

    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    window = QtFemWindow(viewer_backend=os.environ.get("ANYFEM_QT_TEST_BACKEND", "software"))
    window.show()
    qapp.processEvents()
    yield window
    window.session.mark_saved()
    window.close()
    qapp.processEvents()


def _choose(panel, operation):
    panel.choice.setCurrentIndex(panel.choice.findData(operation))


def test_qt_connection_and_connection_load_are_applied_drawn_and_undoable(window, qapp):
    points = [window.run(cmd.AddPoint(x, y, z)) for x, y, z in ((2, 0, 0), (2, 1, 0), (2, 0, 1), (2, 1, 1))]
    lower = window.run(cmd.AddLine(points[0], points[1]))
    upper = window.run(cmd.AddLine(points[2], points[3]))
    refs = tuple(window.project.geometry.entity_ref("edge", item) for item in (lower, upper))
    window.selection.set_mode("edge")
    window.selection.restore(refs)
    panel = window.panels["Loads & BC"]

    _choose(panel, "AddConnection")
    panel.fields["name"][0].setText("Section end")
    panel.fields["kind"][0].setText("distributing")
    panel.apply.click()
    qapp.processEvents()
    assert window.project.connections, panel.feedback.text() + " | " + window.statusBar().currentMessage()
    (connection,) = window.project.connections
    assert connection.name == "Section end" and connection.kind == "distributing"
    assert len(window.project.connection_targets(connection)) == 2
    np.testing.assert_allclose(
        window.project.connection_reference_point(connection), (2.0, 0.5, 0.5))

    _choose(panel, "AddConnectionLoad")
    panel.fields["connection"][0].setText("Section end")
    panel.fields["moment"][0].setText("0, 5e4, 0")
    panel.apply.click()
    qapp.processEvents()
    (load,) = window.project.load_case().connection_loads
    np.testing.assert_allclose(load.moment, (0, 5e4, 0))
    assert load.connection == connection.id

    window.tree.refresh()
    qapp.processEvents()
    labels = [window.tree._model.item(row).text() for row in range(window.tree._model.rowCount())]
    assert any(label.startswith("Connections") for label in labels)

    from anyfem.ui.scene import build_attribute_overlay

    scene = build_attribute_overlay(window.project, case_name="default")
    assert any(abs(np.asarray(m.position) - (2.0, 0.5, 0.5)).max() < 1e-9 for m in scene.points)
    # A moment arrow is centred on the reference point, along the moment axis.
    assert any(
        abs(0.5 * (np.asarray(a.start) + np.asarray(a.end)) - (2.0, 0.5, 0.5)).max() < 1e-9
        and abs(np.cross(np.asarray(a.end) - np.asarray(a.start), (0, 1, 0))).max() < 1e-9
        for a in scene.arrows
    )

    # Opening the tree row loads the connection into the edit form; applying
    # replaces it in place and keeps its identity.
    window.tree.edit_record(("connections", connection.id))
    qapp.processEvents()
    assert panel.choice.currentData() == "EditAttribute"
    panel.record_fields["replacement"][1]["kind"][0].setText("rigid")
    panel.apply.click()
    qapp.processEvents()
    (edited,) = window.project.connections
    assert edited.id == connection.id and edited.kind == "rigid"
    assert edited.dofs == connection.dofs and edited.region == connection.region

    window.undo()
    window.undo()
    window.undo()
    assert not window.project.connections


def test_qt_examples_menu_creates_the_seaway_barge_with_steps_grouped_under_the_series(window, qapp, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Ok)
    label = "Floating barge section — seaway time history (600 load cases)…"
    assert label in window._actions
    window._actions[label].trigger()
    qapp.processEvents()
    project = window.project
    assert len(project.series_children("Seaway")) == 600 and project.load_cases["Seaway"].is_series
    # The active case must never be the series definition or a template: it cannot be solved.
    panel = window.panels["Loads & BC"]
    panel.refresh()
    assert not project.load_cases[panel.case_name()].is_template

    window.tree.refresh()
    qapp.processEvents()
    model = window.tree._model
    group = next(model.item(row) for row in range(model.rowCount()) if model.item(row).text().startswith("Load cases"))
    assert group.rowCount() == 1  # one series row, not 601 cases
    series = group.child(0)
    assert series.text() == "Seaway"
    assert series.rowCount() == 201  # 200 steps listed, then "... 400 more"
    assert series.child(0).text() == "Seaway_000" and series.child(200).text() == "... 400 more"
    assert series.child(0).data(Qt.UserRole)[0] == "load_cases"
