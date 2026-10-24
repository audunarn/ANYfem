"""Native tree selection and in-place engineering edit acceptance."""
import os

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("enable Qt tests explicitly", allow_module_level=True)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from anyfem import commands
from anyfem.model.attributes import Support
from test_qt_geometry_actions import qapp, window, geometry


def click_row(window, qapp, group, key):
    tree = window.tree
    item = tree._rows[group][key][0]
    parent = item.parent()
    while parent is not None:
        tree.expand(parent.index())
        parent = parent.parent()
    # A tree row should be actionable immediately after branch expansion.
    qapp.processEvents()
    tree.scrollTo(item.index())
    qapp.processEvents()
    rect = tree.visualRect(item.index())
    assert rect.isValid() and tree.viewport().rect().contains(rect.center())
    assert tree.indexAt(rect.center()).siblingAtColumn(0).data(Qt.UserRole) == item.data(Qt.UserRole)
    assert not tree._refreshing
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=rect.center())
    qapp.processEvents()
    assert tree.selectionModel().isSelected(item.index()), (
        tree.selectionBehavior(), tree.selectionModel().selectedIndexes(), item.flags())
    return item


def highlighted(window, refs):
    scene = window.viewport._scene
    expected = {entry.tag for entries in (scene.points, scene.lines, scene.faces)
                for entry in entries if any(ref in refs for ref in (entry.owners or (entry.ref,)))}
    assert expected
    assert expected <= set(window.viewport.canvas._highlighted_tags)


@pytest.mark.parametrize("group,index", [("Points", 0), ("Lines", 1), ("Plates", 2)])
def test_geometry_single_tree_click_highlights_with_other_pick_filter(window, qapp, group, index):
    _, refs = geometry(window)
    window._quick_select("plate_edge" if index == 0 else "vertex")
    click_row(window, qapp, group, refs[index].id)
    highlighted(window, {refs[index]})


def test_generated_beam_and_feature_rows_highlight_without_editing(window, qapp):
    window.run(commands.AddStiffenedPanel(4, 6, 1, beam_section="stiffener", plate_section="plate"))
    project = window.project
    feature = project.geometry.features.records[-1]
    beam = next(iter(project.geometry.members))
    revision, history = project.geometry.revision, len(window.commands.history())
    window._quick_select("face")
    click_row(window, qapp, "Beams", beam)
    highlighted(window, set(window.tree.beam_references(beam)))
    click_row(window, qapp, "Features", feature.feature_id)
    assert feature.feature_id in window.viewport._feature_ids
    refs = {resolved for ref in feature.outputs.values() for resolved in project.geometry.resolve_ref(ref)}
    highlighted(window, refs)
    assert project.geometry.revision == revision and len(window.commands.history()) == history


def test_support_single_click_edit_updates_same_record_and_undo(window, qapp, tmp_path):
    _, refs = geometry(window)
    support = window.run(commands.AddSupport(Support("Edge restraint", refs[1], {"ux": .002, "uz": 0.})))
    window._quick_select("face")
    click_row(window, qapp, "Supports", support.id)
    highlighted(window, {refs[1]})
    panel = window.panels["Loads & BC"]
    assert panel.choice.currentData() == "AddSupport"
    assert panel.name.text() == "Edge restraint"
    assert panel.dofs["ux"].isChecked() and panel.dofs["uz"].isChecked()
    assert not panel.dofs["uy"].isChecked()
    assert window.project.units.parse(panel.prescribed["ux"].text(), "length") == .002
    if os.environ.get("ANYFEM_TREE_REVIEW_IMAGES") == "1":
        window.resize(1900, 1000); qapp.processEvents()
        window.grab().save("C:/ANYworkspaceWork/anyfem-tree-edit-support-20261022.png")
    panel.name.setText("Edited restraint")
    QTest.mouseClick(panel.dofs["uy"], Qt.LeftButton)
    before = len(window.commands.history())
    QTest.mouseClick(panel.apply, Qt.LeftButton)
    qapp.processEvents()
    assert len(window.project.supports) == 1
    edited = window.project.supports[0]
    assert edited.id == support.id and edited.region == support.region
    assert edited.name == "Edited restraint" and edited.constraints == {"ux": .002, "uy": 0., "uz": 0.}
    assert len(window.commands.history()) == before + 1
    window.undo()
    assert window.project.supports[0].name == "Edge restraint"
    window.redo()
    assert window.project.supports[0].name == "Edited restraint"
    path = tmp_path / "edited-support.anyfem"
    window.save_project(path=str(path)); window.open_project(str(path))
    assert window.project.supports[0].id == support.id
    assert window.project.supports[0].constraints == {"ux": .002, "uy": 0., "uz": 0.}


def test_load_single_click_edit_preserves_target_identity_and_history(window, qapp):
    _, refs = geometry(window)
    load = window.run(commands.AddLineLoad(refs[1], force_per_length=(10., 20., -2000.)))
    window._quick_select("face")
    click_row(window, qapp, "Loads", load.id)
    highlighted(window, {refs[1]})
    panel = window.panels["Loads & BC"]
    assert panel.choice.currentData() == "AddLineLoad"
    assert panel.vector.value() == pytest.approx((10., 20., -2000.))
    if os.environ.get("ANYFEM_TREE_REVIEW_IMAGES") == "1":
        window.resize(1900, 1000); qapp.processEvents()
        window.grab().save("C:/ANYworkspaceWork/anyfem-tree-edit-load-20261022.png")
    panel.vector.components[2].setText("-3 kN/m")
    QTest.keyClick(panel.vector.components[2], Qt.Key_Return)
    qapp.processEvents()
    loads = window.project.load_case("default").line_loads
    assert len(loads) == 1 and loads[0].id == load.id and loads[0].region == load.region
    np.testing.assert_allclose(loads[0].force_per_length, (10., 20., -3000.))
    window.undo()
    np.testing.assert_allclose(loads[0].force_per_length, (10., 20., -2000.))
