"""Independent native plate-edge selection and boundary-condition workflow."""
import os

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolBar
from anyfem import commands
from anyfem.application.controller import default_project
from anygeometry import EntityRef
from test_qt_geometry_actions import qapp, window


def plate(window, qapp):
    project = default_project()
    commands.AddStiffenedPanel(
        4, 6, 1, u_direction=(0, 1, 0), v_direction=(1, 0, 0),
        beam_section="stiffener", plate_section="plate",
    ).do(project)
    window._set_project(project)
    window.resize(1900, 1000)
    window.show_geometry(reset_view=True)
    window.viewport.set_view("top")
    window.viewport.fit()
    window.notebook.select("Loads & BC")
    qapp.processEvents()
    panel = window.panels["Loads & BC"]
    if panel.rebind.isVisible():
        QTest.mouseClick(panel.rebind, Qt.LeftButton)
    return project


def screen(window, world):
    x, y, _ = window.viewport.canvas.project_point(world)
    ratio = window.viewport.canvas.event_widget.devicePixelRatioF()
    return QPoint(round(x/ratio), round(y/ratio))


def select_bottom_edges(window, qapp):
    action = window.quick_selection_actions["plate_edge"]
    toolbar = next(bar for bar in window.findChildren(QToolBar) if action in bar.actions())
    QTest.mouseClick(toolbar.widgetForAction(action), Qt.LeftButton)
    window.selection_tool.set_value("box")
    qapp.processEvents()
    surface = window.viewport.canvas.event_widget
    start, end = screen(window, (-.3, .3, 0)), screen(window, (6.3, -.3, 0))
    assert start.x() < end.x()
    QTest.mousePress(surface, Qt.LeftButton, pos=start)
    QTest.mouseMove(surface, end, 25)
    QTest.mouseRelease(surface, Qt.LeftButton, pos=end)
    qapp.processEvents()
    expected = {EntityRef("edge", identifier) for identifier in window.project.geometry.edges
                if np.allclose(window.project.geometry.sample_edge(identifier, [0, 1])[:, 1], 0)}
    assert len(expected) == 6 and set(window.selection.items) == expected
    return expected


def test_window_select_plate_edges_and_apply_checked_support_to_all(window, qapp, tmp_path):
    project = plate(window, qapp)
    expected = select_bottom_edges(window, qapp)
    panel = window.panels["Loads & BC"]
    panel.choice.setCurrentIndex(panel.choice.findData("AddSupport"))
    assert "6" in panel.target_summary.text()
    for dof, check in panel.dofs.items():
        wanted = dof in {"ux", "uy", "uz"}
        if check.isChecked() != wanted:
            QTest.mouseClick(check, Qt.LeftButton)
    history = len(window.commands.history())
    assert panel.apply.isEnabled(), panel.target_summary.text()
    QTest.mouseClick(panel.apply, Qt.LeftButton)
    qapp.processEvents()
    assert len(project.supports) == 6, panel.feedback.text()
    assert {item.ref for item in project.supports} == expected
    assert all(item.constraints == {"ux": 0., "uy": 0., "uz": 0.}
               and item.coordinate_system_id == "global" for item in project.supports)
    if os.environ.get("ANYFEM_LOADS_REVIEW_IMAGES") == "1":
        window.grab().save("C:/ANYworkspaceWork/anyfem-support-edges-20261022.png")
    assert len(window.commands.history()) == history+1
    window.undo()
    assert not project.supports
    window.redo()
    assert len(project.supports) == 6
    path = tmp_path / "supported-panel.anyfem"
    window.save_project(path=str(path))
    window.open_project(str(path))
    assert len(window.project.supports) == 6


def test_edge_line_load_units_and_pressure_selection_preserve_draft(window, qapp):
    project = plate(window, qapp)
    expected = select_bottom_edges(window, qapp)
    panel = window.panels["Loads & BC"]
    panel.choice.setCurrentIndex(panel.choice.findData("AddLineLoad"))
    panel.vector.components[2].setText("-2 kN/m")
    window.selection.clear()
    assert panel.vector.components[2].text() == "-2 kN/m"
    window.selection.restore(tuple(expected))
    assert panel.apply.isEnabled(), panel.target_summary.text()
    QTest.mouseClick(panel.apply, Qt.LeftButton)
    qapp.processEvents()
    loads = project.load_case().line_loads
    assert len(loads) == 6 and {load.ref for load in loads} == expected, panel.feedback.text()
    assert all(tuple(load.force_per_length) == (0., 0., -2000.) for load in loads)
    if os.environ.get("ANYFEM_LOADS_REVIEW_IMAGES") == "1":
        window.grab().save("C:/ANYworkspaceWork/anyfem-line-loads-20261022.png")
    panel.choice.setCurrentIndex(panel.choice.findData("AddPressure"))
    window._quick_select("face")
    faces = tuple(EntityRef("face", face) for face in project.geometry.faces)
    window.selection.restore(faces)
    panel.scalar.setText("3 kPa")
    QTest.keyClick(panel.scalar, Qt.Key_Return)
    qapp.processEvents()
    assert len(project.load_case().pressures) == len(faces)
    assert all(load.value == 3000 for load in project.load_case().pressures)
