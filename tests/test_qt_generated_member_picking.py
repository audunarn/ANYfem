"""Independent native-event regression for generated member selection."""
import os

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from anygeometry import EntityRef
from anyfem import commands as cmd
from test_qt_geometry_actions import qapp, window


def click_edge(window, qapp, edge_id, modifiers=Qt.NoModifier):
    canvas = window.viewport.canvas
    surface = canvas.event_widget
    world = window.project.geometry.sample_edge(edge_id, [0.37])[0]
    x, y, _ = canvas.project_point(world)
    ratio = surface.devicePixelRatioF()
    QTest.mouseClick(surface, Qt.LeftButton, modifiers,
                     pos=QPoint(round(x / ratio), round(y / ratio)))
    qapp.processEvents()


def test_collapsed_panel_members_pick_and_shift_select_without_sections(window, qapp):
    window.run(cmd.AddStiffenedPanel(5, 4, 1))
    geometry = window.project.geometry
    edges = [edge for edge in geometry.edges if geometry.members_using_edge(edge)]
    assert len(edges) >= 2 and not window.project.edge_sections
    window.viewport.set_view("top")
    window.viewport.fit()
    window.selection.set_mode("edge")
    qapp.processEvents()
    click_edge(window, qapp, edges[0])
    assert window.selection.items == [EntityRef("edge", edges[0])]
    click_edge(window, qapp, edges[1], Qt.ShiftModifier)
    assert set(window.selection.items) == {EntityRef("edge", edge) for edge in edges[:2]}
    # Incidental plate perimeter topology remains collapsed.
    visible = {line.ref.id for line in window.viewport._scene.lines if line.ref}
    assert set(edges) <= visible
    assert not (set(geometry.edges) - set(edges)) & visible


def test_existing_geometry_only_panel_members_pick_after_reopen(window, qapp, tmp_path):
    window.run(cmd.AddStiffenedPanel(5, 4, 1))
    path = tmp_path / "geometry-only-panel.anyfem"
    window.save_project(path=str(path))
    window.open_project(str(path))
    edge = next(edge for edge in window.project.geometry.edges
                if window.project.geometry.members_using_edge(edge))
    window.viewport.set_view("top")
    window.viewport.fit()
    window.selection.set_mode("edge")
    qapp.processEvents()
    click_edge(window, qapp, edge)
    assert window.selection.items == [EntityRef("edge", edge)]


def test_members_from_two_collapsed_panels_remain_independently_pickable(window, qapp):
    window.run(cmd.AddStiffenedPanel(5, 4, 1))
    first_edges = set(window.project.geometry.edges)
    window.run(cmd.AddStiffenedPanel(5, 4, 1, origin=(0, 6, 0)))
    geometry = window.project.geometry
    first = next(edge for edge in sorted(first_edges) if geometry.members_using_edge(edge))
    second = next(edge for edge in sorted(set(geometry.edges) - first_edges)
                  if geometry.members_using_edge(edge))
    window.viewport.set_view("top")
    window.viewport.fit()
    window.selection.set_mode("edge")
    qapp.processEvents()
    click_edge(window, qapp, first)
    click_edge(window, qapp, second, Qt.ShiftModifier)
    assert set(window.selection.items) == {EntityRef("edge", first), EntityRef("edge", second)}
