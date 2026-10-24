"""Selected generated plates stay free of fill-triangulation stripes."""
import os

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDockWidget
from anyfem import commands
from test_qt_geometry_actions import qapp, window


@pytest.mark.parametrize("view", ["top", "iso"])
@pytest.mark.parametrize("transverse", [None, 1.])
@pytest.mark.parametrize("smooth", [False, True])
def test_generated_panel_selection_has_no_artificial_diagonal(window, qapp, view, transverse, smooth, monkeypatch):
    if smooth:
        # Exercise the GPU's normal expansion through the real software paint
        # route, allowing raster verification without an OpenGL context.
        monkeypatch.setattr(window.viewport, "_smooth_options", lambda patches: {"smooth_angle": 35})
    window.run(commands.AddStiffenedPanel(5, 4, .5, transverse))
    for dock in window.findChildren(QDockWidget):
        dock.hide()
    window.resize(1000, 800)
    window.show_geometry(reset_view=True)
    window.viewport.set_view(view)
    window.viewport.fit()
    window.selection.set_mode("face")
    qapp.processEvents()
    canvas = window.viewport.canvas
    surface = canvas.event_widget
    x, y, _ = canvas.project_point((2.5, .25, 0))
    ratio = surface.devicePixelRatioF()
    QTest.mouseClick(surface, Qt.LeftButton,
                     pos=QPoint(round(x / ratio), round(y / ratio)))
    qapp.processEvents()
    assert window.selection.items and all(ref.kind == "face" for ref in window.selection.items)
    pixels = np.asarray(canvas.capture_image().convert("RGB"), dtype=float)
    sx, sy, _ = canvas.project_point((2.5, .12, 0))
    fill = pixels[round(sy), round(sx)]
    centre = pixels[round(y)-1:round(y)+2, round(x)-1:round(x)+2]
    assert np.max(np.abs(centre-fill)) < 8, "artificial plate diagonal is highlighted"
    # The generated stiffeners remain visible as independent true members.
    mx, my, _ = canvas.project_point((2.5, .5, 0))
    member = pixels[round(my)-2:round(my)+3, round(mx)-2:round(mx)+3]
    assert np.max(np.abs(member-fill)) > 20
