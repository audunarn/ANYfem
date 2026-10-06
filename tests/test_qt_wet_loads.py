"""Opt-in real Qt check that wet surfaces, wet pressures and series are reachable.

The Loads task builds its forms from the public command signatures, so this
drives the same widgets a user does and checks the project that results.
"""
from __future__ import annotations

import os

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


def test_qt_wet_surface_wet_pressure_and_series_are_applied_and_undoable(window, qapp):
    points = [window.run(cmd.AddPoint(x, y, 0)) for x, y in ((0, 0), (2, 0), (2, 1), (0, 1))]
    face = window.run(cmd.AddPlate(points))
    ref = window.project.geometry.entity_ref("face", face)
    window.selection.set_mode("face")
    window.selection.restore((ref,))
    window.run(cmd.AddLoadCase("Service"))
    panel = window.panels["Loads & BC"]
    panel.case.setCurrentText("Service")

    _choose(panel, "AddWetSurface")
    panel.fields["fluid_side"][0].setText("negative_normal")
    panel.apply.click()
    qapp.processEvents()
    assert [s.ref for s in window.project.wet_surfaces] == [ref]
    assert window.project.wet_surfaces[0].fluid_side == "negative_normal"

    _choose(panel, "AddWetPressure")
    panel.case.setCurrentText("Service")
    panel.fields["expression"][0].setText("1000 * max(0, 2 - x)")
    panel.apply.click()
    qapp.processEvents()
    case = window.project.load_cases["Service"]
    assert [w.expression for w in case.wet_pressures] == ["1000 * max(0, 2 - x)"]

    _choose(panel, "AddWetPressure")
    panel.case.setCurrentText("Service")
    panel.fields["expression"][0].setText("")
    panel.fields["value"][0].setText("20 kPa")
    panel.apply.click()
    qapp.processEvents()
    assert case.wet_pressures[-1].value == pytest.approx(20000.0)

    _choose(panel, "AddLoadCaseSeries")
    panel.fields["name"][0].setText("wave")
    panel.fields["times"][0].setText("[0, 1, 2]")
    panel.fields["template"][0].setText("Service")
    panel.apply.click()
    qapp.processEvents()
    assert {"wave_0", "wave_1", "wave_2"} <= set(window.project.load_cases)
    assert window.project.load_cases["wave_2"].time == 2.0
    assert len(window.project.load_cases["wave_2"].wet_pressures) == 2

    window.undo()
    assert not any(name.startswith("wave_") for name in window.project.load_cases)
    window.redo()
    assert "wave_1" in window.project.load_cases


def _group(tree, label):
    items = tree._model.findItems(label)
    return items[0] if items else None


def test_qt_wet_surfaces_appear_in_the_tree_like_sets(window, qapp):
    from PySide6.QtCore import QItemSelectionModel, Qt

    first = [window.run(cmd.AddPoint(x, y, 0)) for x, y in ((0, 0), (2, 0), (2, 1), (0, 1))]
    plate = window.run(cmd.AddPlate(first))
    ref = window.project.geometry.entity_ref("face", plate)
    window.run(cmd.AddLoadCase("Service"))
    surface = window.run(cmd.AddWetSurface(ref, name="Hull"))
    load = window.run(cmd.AddWetPressure(expression="1000*max(0, 1 - z)", case="Service"))
    qapp.processEvents()
    tree = window.tree
    tree.refresh()

    group = _group(tree, "Wet surfaces")
    assert group is not None and group.rowCount() == 1
    row = group.child(0)
    assert row.text() == "Hull"
    assert group.child(0, 1).text() == "1"
    assert row.rowCount() == 1 and row.child(0).data(Qt.UserRole) == ref

    loads = _group(tree, "Loads")
    assert any("Wet pressure (expression)" in loads.child(i).text() for i in range(loads.rowCount()))

    # Selecting the wet surface highlights its plates, as selecting a set does.
    tree.selectionModel().select(row.index(), QItemSelectionModel.Select | QItemSelectionModel.Rows)
    qapp.processEvents()
    assert ref in window.viewport._collection_refs

    # Deleting goes through the tree key and is one undoable edit.
    assert tree.item_key(("wet_surfaces", surface.id)) == f"wet_surface:{surface.id}"
    window._delete_tree_items((f"wet_surface:{surface.id}",))
    qapp.processEvents()
    assert window.project.wet_surfaces == []
    assert _group(tree, "Wet surfaces").rowCount() == 0
    window.undo()
    qapp.processEvents()
    assert [s.id for s in window.project.wet_surfaces] == [surface.id]
    assert _group(tree, "Wet surfaces").rowCount() == 1

    # Edit / inspect loads the record into the Loads task.
    tree.edit_record(("wet_surfaces", surface.id))
    panel = window.panels["Loads & BC"]
    assert panel.choice.currentData() == "EditAttribute"
    panel.apply.click()
    qapp.processEvents()
    assert window.project.wet_surfaces[0].id == surface.id
    assert window.project.wet_surfaces[0].fluid_side == "positive_normal"
    tree.edit_record(("load", load.id))
    assert panel.choice.currentData() == "EditAttribute"
    panel.apply.click()
    qapp.processEvents()
    edited = window.project.load_cases["Service"].wet_pressures[0]
    assert edited.id == load.id and edited.expression == load.expression


def test_qt_overlay_shows_wet_outline_and_arrows_in_the_scene(window, qapp):
    from anyfem.ui.scene import COLOR_WET

    points = [window.run(cmd.AddPoint(x, 0, z)) for x, z in ((0, 0), (2, 0), (2, 2), (0, 2))]
    plate = window.run(cmd.AddPlate(points))
    ref = window.project.geometry.entity_ref("face", plate)
    window.run(cmd.AddLoadCase("default"))
    window.run(cmd.AddWetSurface(ref, name="Hull", fluid_side="negative_normal"))
    window.run(cmd.AddWetPressure(value=5000.0, case="default"))
    qapp.processEvents()
    from anyfem.ui.scene import build_attribute_overlay

    scene = build_attribute_overlay(window.project, case_name="default")
    assert any(line.color == COLOR_WET for line in scene.lines)
    assert any(arrow.color == COLOR_WET for arrow in scene.arrows)


@pytest.fixture
def isolated_trust(tmp_path, monkeypatch):
    monkeypatch.setenv("ANYLOADS_TRUSTED_CODE_FILE", str(tmp_path / "trusted.json"))
    return tmp_path


def _plate_with_wet_surface(window):
    points = [window.run(cmd.AddPoint(x, 0, z)) for x, z in ((0, 0), (2, 0), (2, 3), (0, 3))]
    plate = window.run(cmd.AddPlate(points))
    ref = window.project.geometry.entity_ref("face", plate)
    window.run(cmd.AddLoadCase("Service"))
    window.run(cmd.AddWetSurface(ref, name="Hull", fluid_side="negative_normal"))
    return ref


def test_qt_pressure_code_is_typed_in_a_code_field_and_edited(window, qapp, isolated_trust):
    from anyfem.ui.qt.editors import CodeEdit

    _plate_with_wet_surface(window)
    panel = window.panels["Loads & BC"]
    panel.case.setCurrentText("Service")
    _choose(panel, "AddWetPressure")
    code_field = panel.fields["code"][0]
    assert isinstance(code_field, CodeEdit)
    code = "def pressure(case, x, y, z):\n    return 1000.0 * max(0.0, 2.0 - z)\n"
    code_field.setText(code)
    panel.apply.click()
    qapp.processEvents()
    load = window.project.load_cases["Service"].wet_pressures[0]
    assert load.kind == "code" and load.code.strip() == code.strip()
    from anyloads import is_trusted

    assert is_trusted(load.code)

    window.tree.refresh()
    window.tree.edit_record(("load", load.id))
    edited = "def pressure(case, x, y, z):\n    return 2000.0\n"
    panel.record_fields["replacement"][1]["code"][0].setText(edited)
    panel.apply.click()
    qapp.processEvents()
    assert window.project.load_cases["Service"].wet_pressures[0].code.strip() == edited.strip()
    assert is_trusted(window.project.load_cases["Service"].wet_pressures[0].code)


def test_qt_pressure_table_commands_and_edit_round_trip(window, qapp, isolated_trust):
    _plate_with_wet_surface(window)
    path = isolated_trust / "p.csv"
    rows = ["time,x,y,z,pressure"]
    for time, value in ((0.0, 1000.0), (1.0, 2000.0)):
        for x in (0.5, 1.5):
            for z in (0.5, 1.5, 2.5):
                rows.append(f"{time},{x},0,{z},{value}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    panel = window.panels["Loads & BC"]
    _choose(panel, "AddLoadCasesFromTable")
    panel.fields["name"][0].setText("run")
    panel.fields["path"][0].setText(str(path))
    panel.fields["key"][0].setText("time")
    panel.apply.click()
    qapp.processEvents()
    assert {"run_0", "run_1"} <= set(window.project.load_cases)
    assert window.project.load_cases["run_1"].time == 1.0

    _choose(panel, "AddWetPressureTable")
    panel.case.setCurrentText("Service")
    panel.fields["path"][0].setText(str(path))
    panel.fields["key"][0].setText("time")
    panel.fields["case"][0].setText("Service")
    panel.apply.click()
    qapp.processEvents()
    load = window.project.load_cases["Service"].wet_pressures[0]
    assert load.kind == "table" and load.table.key == "time"

    window.tree.refresh()
    window.tree.edit_record(("load", load.id))
    panel.apply.click()  # editing a table pressure must round-trip the table
    qapp.processEvents()
    again = window.project.load_cases["Service"].wet_pressures[0]
    assert again.table == load.table and again.id == load.id

    window.undo()
    qapp.processEvents()
    window.redo()


def test_qt_solve_asks_before_running_code_from_a_file(window, qapp, isolated_trust, monkeypatch):
    from anyloads import is_trusted

    _plate_with_wet_surface(window)
    code = "def pressure(z):\n    return 1000.0\n"
    window.run(cmd.AddWetPressure(code=code, case="Service"))
    (isolated_trust / "trusted.json").unlink()
    assert not is_trusted(code)

    asked = []
    monkeypatch.setattr(window, "confirm_untrusted_code", lambda items: asked.append(items) or False)
    window.generate_mesh(0.5)
    with pytest.raises(ValueError, match="has not been approved"):
        window.solve("Linear static", load_case="Service")
    assert asked and asked[0][0][2] == code
    assert not is_trusted(code)

    monkeypatch.setattr(window, "confirm_untrusted_code", lambda items: True)
    window._require_trusted_pressure_code()
    assert is_trusted(code)


def test_qt_table_series_and_code_solve_in_the_worker_process(window, qapp, isolated_trust):
    """The worker process must see the trusted code and read the table file."""
    import time

    from anyfem import steel
    from anyfem.model import PlateSection
    from anyfem.model.attributes import Support

    material = steel("S355", 0.010)
    window.run(cmd.AddMaterial(material))
    window.run(cmd.AddPlateSection(PlateSection("plate", 0.010, material.name), material))
    points = [window.run(cmd.AddPoint(x, y, 0)) for x, y in ((0, 0), (2, 0), (2, 1), (0, 1))]
    edges = [window.run(cmd.AddLine(a, b)) for a, b in zip(points, points[1:] + points[:1])]
    face = window.run(cmd.AddFace(edges))
    window.run(cmd.AssignPlate(face, "plate"))
    for edge in edges:
        window.run(cmd.AddSupport(Support(
            f"fixed-{edge}", window.project.geometry.entity_ref("edge", edge),
            dict.fromkeys(["ux", "uy", "uz", "rx", "ry", "rz"], 0))))
    ref = window.project.geometry.entity_ref("face", face)
    window.run(cmd.AddWetSurface(ref, name="Hull", fluid_side="negative_normal"))

    path = isolated_trust / "p.csv"
    lines = ["time,x,y,z,pressure"]
    for time_value, value in ((0.0, 1000.0), (1.0, 4000.0)):
        for x in (0.5, 1.5):
            for y in (0.25, 0.75):
                lines.append(f"{time_value},{x},{y},0,{value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    window.run(cmd.AddLoadCasesFromTable("tab", str(path), key="time"))
    window.run(cmd.AddLoadCase("code"))
    window.run(cmd.AddWetPressure(
        code="def pressure(case, x, y, z):\n    return 2500.0\n", case="code"))

    window.generate_mesh(0.25)
    window.solve("Batch linear static", load_cases=["tab_0", "tab_1", "code"])
    end = time.monotonic() + 120
    while window.solution is None and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.02)
    assert window.solution is not None, window._status.get()
    peaks = {name: shape.max_translation()[1]
             for name, shape in zip(window.solution.case_names, window.solution.shapes)}
    assert peaks["tab_1"] == pytest.approx(4.0 * peaks["tab_0"], rel=1e-6)
    assert peaks["code"] == pytest.approx(2.5 * peaks["tab_0"], rel=1e-6)
