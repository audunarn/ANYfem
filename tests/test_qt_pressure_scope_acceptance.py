"""The pressure assignment and its highlight describe the same plate bays."""
import os

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("enable native Qt checks explicitly", allow_module_level=True)

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from anygeometry import EntityRef
from anyfem import commands
from anyfem.model.attributes import Support
from anyfem.ui.scene import COLOR_PLATE
from test_qt_geometry_actions import qapp, window
from test_qt_tree_direct_edit_acceptance import click_row


def panel_model(window, qapp, view):
    window.run(commands.AddStiffenedPanel(5, 10, .5, beam_section="stiffener", plate_section="plate"))
    geometry = window.project.geometry
    edge = next(iter(geometry.edges))
    window.run(commands.AddSupport(Support("Edge support", EntityRef("edge", edge), {"ux": 0., "uy": 0., "uz": 0.})))
    window.resize(1900, 1000)
    qapp.processEvents()
    window.show_geometry(reset_view=True)
    window.viewport.set_view(view); window.viewport.fit()
    window.notebook.select("Loads & BC")
    panel = window.panels["Loads & BC"]
    panel.choice.setCurrentIndex(panel.choice.findData("AddPressure"))
    window._quick_select("face")
    qapp.processEvents()
    return {EntityRef("face", face) for face in geometry.faces}


def canvas_click(window, qapp, point):
    canvas = window.viewport.canvas
    x, y, _ = canvas.project_point(point)
    ratio = canvas.event_widget.devicePixelRatioF()
    QTest.mouseClick(canvas.event_widget, Qt.LeftButton, pos=QPoint(round(x/ratio), round(y/ratio)))
    QTest.mouseMove(canvas.event_widget, QPoint(8, 8))
    qapp.processEvents()


def bay_pixels(window, qapp):
    canvas = window.viewport.canvas
    qapp.processEvents()
    image = np.asarray(canvas.capture_image().convert("RGB"), dtype=float)
    samples = []
    for bay in range(20):
        for x in (.6, 1.1, 1.7, 2.1, 3.1, 3.8, 4.3):
            for dy in (.11, .16, .23, .29, .37, .41):
                sx, sy, _ = canvas.project_point((x, bay*.5+dy, 0))
                samples.append((bay, image[round(sy), round(sx)]))
    return samples


@pytest.mark.parametrize("view", ["top", "iso"])
def test_native_collapsed_plate_pressure_reaches_all_bays_and_uniform_fill(window, qapp, view):
    faces = panel_model(window, qapp, view)
    canvas_click(window, qapp, (2.1, .25, 0))
    assert set(window.selection.items) == faces
    panel = window.panels["Loads & BC"]
    panel.scalar.setText("1 kPa")
    QTest.mouseClick(panel.apply, Qt.LeftButton); qapp.processEvents()
    pressures = window.project.load_case("default").pressures
    assert len(pressures) == 20 and {load.ref for load in pressures} == faces
    assert all(load.value == 1000 for load in pressures)
    # Derive visible plate pixels from the unselected image with the SAME
    # beam/support/pressure overlays. A beam flange or glyph can legitimately
    # obscure a projected point on the underlying plate.
    window.selection.clear(); qapp.processEvents()
    before = bay_pixels(window, qapp)
    plate_color = np.array(QColor(COLOR_PLATE).getRgb()[:3])
    visible = [index for index, (_, color) in enumerate(before)
               if np.max(np.abs(color-plate_color)) < 2]
    assert len(visible) >= 60
    visible_bays = {before[index][0] for index in visible}
    if view == "top":
        assert visible_bays == set(range(20))
    else:
        # At oblique angles the physical section flanges obscure some bays.
        assert len(visible_bays) >= 10
    window.selection.restore(tuple(faces)); qapp.processEvents()
    after = bay_pixels(window, qapp)
    colors = np.array([after[index][1] for index in visible])
    if os.environ.get("ANYFEM_PRESSURE_REVIEW_IMAGES") == "1":
        canvas = window.viewport.canvas
        canvas.capture_image().save("C:/ANYworkspaceWork/pressure-all-bays-"+view+"-20261022.png")
    assert np.max(np.ptp(colors, axis=0)) < 12, "unhighlighted fill wedges remain across selected plate bays"
    window.undo(); assert not window.project.load_case("default").pressures
    assert len(window.project.supports) == 1
    window.redo(); assert len(window.project.load_case("default").pressures) == 20


def test_existing_partial_pressure_highlights_only_its_real_bay(window, qapp):
    faces = panel_model(window, qapp, "top")
    target = min(faces, key=lambda ref: ref.id)
    load = window.run(commands.AddPressure(target, 1000))
    window.selection.clear(); qapp.processEvents()
    before = bay_pixels(window, qapp)
    click_row(window, qapp, "Loads", load.id)
    after = bay_pixels(window, qapp)
    plate_color = np.array(QColor(COLOR_PLATE).getRgb()[:3])
    changed = {bay for (bay, old), (_, new) in zip(before, after)
               if np.max(np.abs(old-plate_color)) < 2 and np.max(np.abs(old-new)) > 20}
    assert len(changed) == 1, "one-bay pressure must not visually highlight the whole collapsed plate"
    assert window.panels["Loads & BC"]._editing.record.id == load.id
    assert len(window.project.load_case("default").pressures) == 1


def test_hover_preview_matches_the_whole_collapsed_plate_scope(window, qapp):
    panel_model(window, qapp, "iso")
    window.selection.clear(); qapp.processEvents()
    before = bay_pixels(window, qapp)
    plate_color = np.array(QColor(COLOR_PLATE).getRgb()[:3])
    visible = [index for index, (_, color) in enumerate(before)
               if np.max(np.abs(color-plate_color)) < 2]
    assert len(visible) >= 60
    canvas = window.viewport.canvas
    sx, sy, _ = canvas.project_point((2.1, .25, 0))
    ratio = canvas.event_widget.devicePixelRatioF()
    QTest.mouseMove(canvas.event_widget, QPoint(round(sx/ratio), round(sy/ratio)))
    # Software hit testing intentionally debounces hover for 120 ms.
    QTest.qWait(160)
    qapp.processEvents()
    assert not window.selection.items
    after = bay_pixels(window, qapp)
    colors = np.array([after[index][1] for index in visible])
    assert np.max(np.ptp(colors, axis=0)) < 12, "hover must preview the same complete plate scope as a click"
    assert np.max(np.abs(colors[0]-plate_color)) > 40
    if os.environ.get("ANYFEM_PRESSURE_REVIEW_IMAGES") == "1":
        canvas.capture_image().save("C:/ANYworkspaceWork/pressure-hover-all-bays-20261022.png")
