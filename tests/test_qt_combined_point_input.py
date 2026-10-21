"""Combined and separate Add point inputs share the normal command path."""
import os
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from anyfem import commands
from anyfem.model.units import UNIT_PROFILES
from test_qt_geometry_actions import qapp, window


@pytest.mark.parametrize("text", ["1,2,3", "1 2 3", "(1,2,3)", "[1,2,3]", "[1 2 3]"])
def test_combined_point_apply_enter_and_undo(window, qapp, text):
    panel = window.panels["Geometry"]
    panel.choice.setCurrentIndex(panel.choice.findData("AddPoint"))
    assert not panel.point_input_mode.isChecked()
    panel.point_input_mode.setChecked(True)
    panel.point_coordinates.setText(text)
    panel.point_coordinates.setFocus()
    QTest.keyClick(panel.point_coordinates, Qt.Key_Return)
    qapp.processEvents()
    identifier, = window.project.geometry.vertices
    assert tuple(window.project.geometry.vertex_position(identifier)) == pytest.approx((1, 2, 3))
    window.undo()
    assert not window.project.geometry.vertices


def test_combined_point_units_and_mode_switching(window):
    window.run(commands.SetUnitProfile(UNIT_PROFILES["SI-mm-N-MPa"]))
    panel = window.panels["Geometry"]
    panel.choice.setCurrentIndex(panel.choice.findData("AddPoint"))
    for name, value in zip(("x", "y", "z"), ("1000", "2 cm", "3")):
        panel.fields[name][0].setText(value)
    panel.point_input_mode.setChecked(True)
    form = panel.scroll.widget().layout()
    assert form.isRowVisible(panel.point_coordinates)
    assert not form.isRowVisible(panel.fields["x"][0])
    assert panel.command_options() == pytest.approx({"x": 1, "y": .02, "z": .003})
    panel.point_coordinates.setText("[4 mm 5 cm 6]")
    panel.point_input_mode.setChecked(False)
    assert not form.isRowVisible(panel.point_coordinates)
    assert form.isRowVisible(panel.fields["x"][0])
    assert panel.command_options() == pytest.approx({"x": .004, "y": .05, "z": .006})
    panel.point_input_mode.setChecked(True)
    assert panel.command_options() == pytest.approx({"x": .004, "y": .05, "z": .006})


@pytest.mark.parametrize("text", ["1 2", "1 2 3 4", "1,,3", "1 mm 2 s 3 mm", "nan 2 3"])
def test_invalid_combined_point_is_refused_without_mutation(window, qapp, text):
    panel = window.panels["Geometry"]
    panel.choice.setCurrentIndex(panel.choice.findData("AddPoint"))
    panel.point_input_mode.setChecked(True)
    panel.point_coordinates.setText(text)
    before = window.commands.history()
    QTest.keyClick(panel.point_coordinates, Qt.Key_Return)
    qapp.processEvents()
    assert window.commands.history() == before
    assert not window.project.geometry.vertices
    assert "Error" in window.statusBar().currentMessage()
    panel.point_input_mode.setChecked(False)
    assert panel.point_input_mode.isChecked()
    assert panel.point_coordinates.text() == text
    assert "Cannot switch" in panel.feedback.text()


def test_mode_is_available_again_after_changing_operation(window):
    panel = window.panels["Geometry"]
    panel.point_input_mode.setChecked(True)
    panel.choice.setCurrentIndex(panel.choice.findData("Extrude"))
    panel.choice.setCurrentIndex(panel.choice.findData("AddPoint"))
    assert panel.point_input_mode.isChecked()
    assert panel.scroll.widget().layout().isRowVisible(panel.point_coordinates)


def test_combined_coordinates_also_move_a_point_and_undo(window, qapp):
    identifier = window.run(commands.AddPoint(0, 0, 0))
    panel = window.panels["Geometry"]
    panel.choice.setCurrentIndex(panel.choice.findData("MovePoint"))
    panel.fields["vertex_id"][0].setText(str(identifier))
    panel.point_input_mode.setChecked(True)
    panel.point_coordinates.setText("(4 5 6)")
    QTest.keyClick(panel.point_coordinates, Qt.Key_Return)
    qapp.processEvents()
    assert tuple(window.project.geometry.vertex_position(identifier)) == pytest.approx((4, 5, 6))
    window.undo()
    assert tuple(window.project.geometry.vertex_position(identifier)) == pytest.approx((0, 0, 0))
