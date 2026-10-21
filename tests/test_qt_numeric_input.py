"""Verify flexible input reaches actual Qt operations without model corruption."""
import os
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from anyfem import commands
from anyfem.model.units import UNIT_PROFILES
from anyfem.ui.qt.editors import CommandEditor
from test_qt_geometry_actions import qapp, window


@pytest.mark.parametrize("vector", ["0,0,2", "0 0 2", "(0,0,2)", "[0,0,2]", "[0 0 2]"])
def test_extrude_accepts_vectors_and_enter_applies(window, qapp, vector):
    points = [window.run(commands.AddPoint(*p)) for p in ((0, 0, 0), (1, 0, 0))]
    edge = window.run(commands.AddLine(*points))
    editor = window.panels["Geometry"]
    editor.choice.setCurrentIndex(editor.choice.findData("Extrude"))
    editor.fields["edge_ids"][0].setText(f"[{edge}]")
    field = editor.fields["vector"][0]
    field.setText(vector)
    field.setFocus()
    QTest.keyClick(field, Qt.Key_Return)
    qapp.processEvents()
    geometry = window.project.geometry
    assert len(geometry.faces) == 1
    positions = [tuple(geometry.vertex_position(key)) for key in geometry.vertices]
    assert any(position == pytest.approx((0, 0, 2)) for position in positions)
    assert any(position == pytest.approx((1, 0, 2)) for position in positions)
    window.undo()
    assert not window.project.geometry.faces


def test_ids_units_and_invalid_input_in_real_command_editor(window, qapp):
    window.run(commands.SetUnitProfile(UNIT_PROFILES["SI-mm-N-MPa"]))
    editor = window.panels["Geometry"]
    editor.choice.setCurrentIndex(editor.choice.findData("Extrude"))
    editor.fields["edge_ids"][0].setText("(1 2 3)")
    editor.fields["vector"][0].setText("1000 mm 2 cm 3")
    options = editor.command_options()
    assert options["edge_ids"] == (1, 2, 3)
    assert options["vector"] == pytest.approx((1, .02, .003))
    before = window.commands.history()
    editor.fields["vector"][0].setText("0,,2")
    editor.apply.click()
    qapp.processEvents()
    assert "ValueError" in window.statusBar().currentMessage()
    assert window.commands.history() == before
    assert not window.project.geometry.faces


def test_structured_and_string_fields_keep_their_existing_interpretation(window):
    editor = window.panels["Geometry"]
    field = QLineEdit('[[1, 0], [0, 1]]')
    assert editor.value(field, "Sequence[Sequence[float]] | None", None) == [[1, 0], [0, 1]]
    field.setText('alpha,beta')
    assert editor.value(field, "Sequence[str]", None) == ("alpha", "beta")
    field.setText('{"x": 2}')
    assert editor.value(field, "dict", None) == {"x": 2}


def test_ordered_plate_draft_accepts_space_separated_ids(window, qapp):
    ids = [window.run(commands.AddPoint(*point)) for point in ((0,0,0), (2,0,0), (0,2,0))]
    editor = window.panels["Geometry"]
    editor.choice.setCurrentIndex(editor.choice.findData("AddPlate"))
    editor.construct_plate.click()
    draft = window.viewport.plate_draft
    text = "[" + " ".join(map(str, ids)) + "]"
    draft.order.setText(text)
    draft.edit_order(text)
    assert draft.ids == ids
    draft.commit()
    assert len(window.project.geometry.faces) == 1


def test_construction_coordinates_use_the_same_parser(window):
    window.run(commands.SetUnitProfile(UNIT_PROFILES["SI-mm-N-MPa"]))
    panel = window.panels["Construction"]
    panel.start()
    panel.coordinates.setText("(1 m 20 mm)")
    panel.add_point()
    panel.apply()
    identifier, = window.project.geometry.vertices
    assert tuple(window.project.geometry.vertex_position(identifier)) == pytest.approx((1, .02, 0))


def test_section_plane_accepts_a_bracketed_space_separated_vector(window):
    panel = window.panels["Visualization"]
    panel.plane.setText("[0 0 1]")
    panel.offset.setText("2")
    captured = {}
    window.viewport.set_section_plane = lambda **kwargs: captured.update(kwargs)
    panel.section()
    assert captured == {"normal": (0., 0., 1.), "offset": 2.}
