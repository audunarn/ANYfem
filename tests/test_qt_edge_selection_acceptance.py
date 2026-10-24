"""Independent cylinder rim and large native edge selection acceptance."""
import os
import time
import json
from pathlib import Path
import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("Qt opt-in", allow_module_level=True)

from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from anygeometry import EntityRef
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.selection import plate_boundary_edge_ids
from test_qt_geometry_actions import qapp, window


def screen(window, world):
    x, y, _ = window.viewport.canvas.project_point(world)
    ratio = window.viewport.canvas.event_widget.devicePixelRatioF()
    return QPoint(round(x/ratio), round(y/ratio))


def box_everything(window, qapp):
    surface = window.viewport.canvas.event_widget
    window.viewport.configure_selection(tool="box", depth="through", operation="replace")
    started = time.perf_counter()
    QTest.mousePress(surface, Qt.LeftButton, pos=QPoint(3, 3))
    QTest.mouseMove(surface, QPoint(surface.width()-3, surface.height()-3), 20)
    QTest.mouseRelease(surface, Qt.LeftButton, pos=QPoint(surface.width()-3, surface.height()-3))
    qapp.processEvents()
    return time.perf_counter()-started


@pytest.mark.parametrize("view,axis,radial,component", [
    ("top", (0, 0, 1), (1, 0, 0), 2),
    ("side", (1, 0, 0), (0, 1, 0), 0),
])
def test_cylinder_open_rims_click_and_through_window_use_canonical_edges(window, qapp, view, axis, radial, component):
    window._set_project(default_project())
    window.run(commands.AddCylinder(2, 4, circumferential_segments=12, longitudinal_spacing=1,
                                    axis=axis, radial_direction=radial,
                                    beam_section="stiffener", plate_section="plate"))
    window.viewport.set_view(view); window.viewport.fit(); window._quick_select("plate_edge")
    qapp.processEvents()
    geometry = window.project.geometry
    expected = plate_boundary_edge_ids(window.project)
    upper = [edge for edge in sorted(expected) if np.allclose(geometry.sample_edge(edge, [.37])[:, component], 4)]
    assert len(upper) >= 12
    surface = window.viewport.canvas.event_widget
    for edge in upper[::max(1, len(upper)//3)]:
        QTest.mouseClick(surface, Qt.LeftButton, pos=screen(window, geometry.sample_edge(edge, [.37])[0]))
        qapp.processEvents()
        assert window.selection.items == [EntityRef("edge", edge)]
    history = window.commands.history()
    box_everything(window, qapp)
    assert set(window.selection.items) == {EntityRef("edge", edge) for edge in expected}
    assert window.commands.history() == history


def test_large_edge_window_keeps_selection_only_and_all_targets(window, qapp, tmp_path):
    window._set_project(default_project())
    for origin, u, v in (((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                         ((0, 0, 0), (1, 0, 0), (0, 0, 1)),
                         ((0, 20, 0), (1, 0, 0), (0, 0, 1))):
        window.run(commands.AddStiffenedPanel(20, 20, .5, origin=origin, u_direction=u, v_direction=v,
                                             beam_section="stiffener", plate_section="plate"))
    window.notebook.select("Loads & BC")
    panel = window.panels["Loads & BC"]
    panel.choice.setCurrentIndex(panel.choice.findData("AddConnection"))
    window.viewport.set_view("iso"); window.viewport.fit(); window._quick_select("plate_edge")
    qapp.processEvents()
    expected = {EntityRef("edge", edge) for edge in plate_boundary_edge_ids(window.project)}
    assert len(expected) > 150
    history = window.commands.history()
    revision = window.session.revision
    elapsed = box_everything(window, qapp)
    assert set(window.selection.items) == expected
    assert window.commands.history() == history and window.session.revision == revision
    evidence = {"selected": len(expected), "seconds": elapsed, "backend": window.viewport.active_backend,
                "scale": window.viewport.canvas.event_widget.devicePixelRatioF()}
    (tmp_path/"selection-timing.json").write_text(json.dumps(evidence), encoding="utf-8")
    print("SELECTION_TIMING", json.dumps(evidence))
