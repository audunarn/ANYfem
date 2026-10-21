"""Workbench presentation contracts: readable controls, grouping and no lost operations."""
from __future__ import annotations

import os
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets", allow_module_level=True)
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QToolBar

from anyfem import commands as cmd
from anyfem.ui.qt.app import QtFemWindow

# The Geometry operations offered before the regrouping; none may be lost or added.
GEOMETRY_OPERATIONS = {
    "AddPoint", "MovePoint", "AddLine", "AddPlate", "AddFace", "AddArc", "AddPolyline", "AddCylinder",
    "AddCone", "AddStiffenedPanel", "AddConstructionPlane", "AddSketch", "EditFeature", "SuppressFeature",
    "DeleteFeature", "DeleteEntity", "CopyEntities", "LinearPattern", "CircularPattern", "MirrorEntities",
    "Extrude", "Revolve", "ReverseEntity", "SplitEdge", "SplitFace", "StripFace", "SetFaceCorners",
    "JoinSheet", "FragmentPlateOverlaps", "TriangleToQuads", "ButterflyHoleDecomposition",
    "NeutralTrimHole", "MeasureGeometry",
}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from anyfem.io import recovery
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    window = QtFemWindow(viewer_backend=os.environ.get("ANYFEM_QT_TEST_BACKEND", "software"))
    window.show(); qapp.processEvents()
    yield window
    window.session.mark_saved(); window.close(); qapp.processEvents()


def test_standard_save_shortcut_and_show_selected_moved(window):
    assert window._actions["Save"].shortcut().toString() == "Ctrl+S"
    assert window._actions["Show selected"].shortcut().toString() == "Ctrl+Shift+H"
    shortcuts = [action.shortcut().toString() for action in window.findChildren(type(window._actions["Save"]))
                 if not action.shortcut().isEmpty()]
    assert shortcuts.count("Ctrl+S") == 1
    assert "Ctrl+S" in window._actions["Save"].toolTip()


def test_selection_controls_show_words_and_keep_internal_values(window, qapp):
    assert [window.selection_mode.itemText(i) for i in range(window.selection_mode.count())] == [
        "Points", "Lines", "Faces", "Mesh nodes", "Mesh elements"]
    window.selection_mode.set_value("edge")
    assert window.selection.mode == "edge"
    window.selection_tool.set_value("lasso"); window.selection_depth.set_value("through")
    window.selection_operation.set_value("add")
    assert (window.viewport._selection_tool, window.viewport._selection_depth,
            window.viewport._selection_operation) == ("lasso", "through", "add")
    assert window.selection_tool.currentText() == "Lasso"
    with pytest.raises(ValueError):
        window.selection_tool.set_value("polygon")
    assert window.viewport._selection_tool == "lasso"


def test_renderer_lives_in_status_bar_with_readable_names(window):
    assert window.renderer.parent() is not None
    assert window.statusBar().isAncestorOf(window.renderer)
    assert window.renderer.value() == "software" and window.renderer.currentText() == "Software"


def test_workspace_buttons_have_text_shortcuts_and_heading(window, qapp):
    toolbar = next(bar for bar in window.findChildren(QToolBar) if bar.objectName() == "Workspaces")
    for name, action in window.workspace_actions.items():
        button = toolbar.widgetForAction(action)
        assert button.toolButtonStyle() == Qt.ToolButtonTextUnderIcon
        assert action.text().replace("&&", "&") == name
    assert window.workspace_actions["Loads & BC"].iconText() == "Loads & BC"
    before = window.commands.history()
    window.workspace_actions["Mesh"].trigger(); qapp.processEvents()
    assert window.workspace_title.text() == "Mesh" and "mesh" in window.workspace_summary.text().lower()
    assert window.workspace_actions["Geometry"].shortcut().toString() == "Ctrl+1"
    window.workspace_actions["Geometry"].trigger(); qapp.processEvents()
    assert window.details.currentIndex() == 0 and window.workspace_title.text() == "Geometry"
    assert window.commands.history() == before


def test_geometry_operations_are_grouped_without_losing_any(window):
    panel = window.panels["Geometry"]
    choices = [panel.choice.itemData(i) for i in range(panel.choice.count())]
    assert set(choices) == GEOMETRY_OPERATIONS and len(choices) == len(GEOMETRY_OPERATIONS)
    assert choices == [name for _, names in window.GEOMETRY_GROUPS for name in names]
    strip = panel.option_icons
    assert len(strip.buttons) == len(choices)
    assert [heading.text() for heading in strip.headings] == [title.upper() for title, _ in window.GEOMETRY_GROUPS]
    QTest.mouseClick(strip.buttons[choices.index("AddSketch")], Qt.LeftButton)
    assert panel.choice.currentData() == "AddSketch"
    panel.option_palette.set_expanded(False)
    assert strip.isHidden() is False and not strip.isVisibleTo(panel)
    panel.option_palette.set_expanded(True)


def test_geometry_fields_and_apply_are_visible_without_scrolling(window, qapp):
    window.resize(1400, 900); window.reset_workspace_layout()
    window.notebook.select("Geometry"); qapp.processEvents()
    panel = window.panels["Geometry"]
    panel.choice.setCurrentIndex(panel.choice.findData("AddPoint")); qapp.processEvents()
    scroll = window.details.currentWidget()
    assert isinstance(scroll, QScrollArea) and scroll.verticalScrollBar().value() == 0
    viewport = scroll.viewport().rect()
    for widget in (panel.fields["x"][0], panel.apply):
        top_left = widget.mapTo(scroll.viewport(), widget.rect().topLeft())
        bottom = top_left.y() + widget.height()
        assert viewport.contains(top_left) and bottom <= viewport.bottom(), (widget, top_left, viewport)
    assert panel.option_icons.isVisible()


def test_unit_hint_follows_project_unit_profile(window, qapp):
    from anyfem.model.units import UNIT_PROFILES
    panel = window.panels["Geometry"]
    panel.choice.setCurrentIndex(panel.choice.findData("AddPoint")); qapp.processEvents()
    assert panel.guidance.text().startswith("Units: m,")
    window.run(cmd.SetUnitProfile(UNIT_PROFILES["SI-mm-N-MPa"])); qapp.processEvents()
    assert panel.guidance.text().startswith("Units: mm,") and "MPa" in panel.guidance.text()
    assert "0.25 m" in panel.guidance.text()


def test_workspace_forms_scroll_once(window, qapp):
    for name, panel in window.panels.items():
        window.notebook.select(name); qapp.processEvents()
        assert not panel.findChildren(QScrollArea), name


def test_choice_fields_submit_internal_values(window, qapp):
    vertex = window.run(cmd.AddPoint(0, 0, 0))
    panel = window.panels["Loads & BC"]
    panel.choice.setCurrentIndex(panel.choice.findData("AddPointLoad"))
    policy = panel.fields["distribution_policy"][0]
    system = panel.fields["coordinate_system_id"][0]
    assert policy.currentText() == "Full value on each target" and policy.text() == "per_target"
    assert system.text() == "global"
    policy.setText("total_distributed")
    panel.fields["ref"][0].setFocus()
    window.selection.set_mode("vertex"); window.selection.select(window.project.geometry.entity_ref("vertex", vertex))
    panel.fields["force"][0].setText("0, 0, -10")
    panel.apply.click(); qapp.processEvents()
    load, = window.project.load_case("default").point_loads
    assert load.distribution_policy == "total_distributed"
    # A coordinate system created later becomes selectable without rebuilding the form.
    created = window.run(cmd.AddConstructionPlane("Deck", origin=(0, 0, 2)))
    qapp.processEvents()
    identifier = getattr(created, "id", created)
    assert system.findData(identifier) >= 0 and system.text() == "global"


def test_mesh_and_solve_keep_advanced_options_folded_but_working(window, qapp):
    mesh = window.panels["Mesh"]
    assert mesh.generate.property("primary") is True
    assert all(not section.is_expanded() for section in mesh.sections.values())
    mesh.strategy.setCurrentText("quad_first"); mesh.t3_minimum_angle.setText("20")
    assert not mesh.t3_minimum_angle.isVisibleTo(mesh)
    assert mesh.settings()["quad_t3_minimum_angle"] == 20.0
    mesh.sections["advanced"].set_expanded(True)
    assert mesh.options.isVisibleTo(mesh)
    solve = window.panels["Solve"]
    assert solve.submit.property("primary") is True
    assert not solve.submit.isEnabled() and solve.submit_hint.isVisibleTo(solve)
    assert solve.output_section.toggle.text() == "Output requests (none defined)"
    assert solve.settings()["output_request_ids"] == ()


def test_empty_jobs_view_explains_next_step(window):
    assert not window.job_status.model().rowCount()
    assert "Generate a mesh" in window.job_status.EMPTY_TEXT
    window._update_window_title()
    assert window.windowTitle().endswith(" — ANYfem")
