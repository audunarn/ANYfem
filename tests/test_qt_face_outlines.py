"""Rendered application regressions: tessellation is not a model edge."""
import os

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QDockWidget
from anyfem import commands
from anyfem.presentation.visualization import VisualizationStyle
from anyfem.selection import MeshEntityRef
from anyfem.ui.scene import FacePatch, Scene, build_geometry_scene
from test_qt_geometry_actions import qapp, window


@pytest.fixture(autouse=True)
def isolated_view_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from anyfem.ui.qt import app
    monkeypatch.setattr(app, "QSettings", lambda *_: QSettings(
        str(tmp_path / "workbench.ini"), QSettings.IniFormat))


@pytest.mark.parametrize("kind", ["geometry", "quad", "triangles"])
@pytest.mark.parametrize("selected", [False, True])
def test_rendered_semantic_boundaries(window, qapp, kind, selected, tmp_path):
    ids = [window.run(commands.AddPoint(x, y)) for x, y in
           ((0, 0), (1, 0), (1, 1), (0, 1))]
    face = window.run(commands.AddPlate(ids))
    if kind == "geometry":
        scene = build_geometry_scene(window.project)
        refs = [window.project.geometry.entity_ref("face", face)]
        window.selection.set_mode("face")
    else:
        square = np.array(((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)))
        polygons = [square] if kind == "quad" else [square[[0, 1, 2]], square[[0, 2, 3]]]
        refs = [MeshEntityRef("element", index + 1) for index in range(len(polygons))]
        scene = Scene(faces=[FacePatch(None, polygons, ["#7099b4"] * len(polygons),
                                      outline="#334155", polygon_owners=[(ref,) for ref in refs])])
        window.selection.set_mode("element")
    for dock in window.findChildren(QDockWidget):
        dock.hide()
    window.resize(900, 700)
    qapp.processEvents()
    window.viewport.set_visualization(VisualizationStyle(render_mode="Shaded with edges"))
    window.viewport.show(scene, reset_view=True)
    window.viewport.set_view("top")
    window.viewport.fit()
    window.viewport.canvas.set_mesh_lines(True)
    if selected:
        window.selection.restore(refs)
    qapp.processEvents()
    canvas = window.viewport.canvas
    assert canvas.show_mesh_lines
    assert window.viewport.visualization.render_mode == "Shaded with edges"
    image = canvas.capture_image().convert("RGB")
    image.save(tmp_path / f"{kind}-{'selected' if selected else 'plain'}.png")
    x, y, _ = canvas.project_point((0.5, 0.5, 0))
    pixels = np.asarray(image, dtype=float)
    # The quad fan's artificial diagonal crosses the centre; neighbouring
    # interior pixels have the same fill. A genuine FE diagonal must differ.
    interior = pixels[round(y), round(x) + 9]
    centre = pixels[round(y) - 2:round(y) + 3, round(x) - 2:round(x) + 3]
    difference = np.max(np.abs(centre - interior))
    if kind == "triangles":
        assert difference > 20, "the real shared triangle edge disappeared"
    else:
        assert difference < 8, "a fill-triangulation diagonal is visible"
    a, b, _ = canvas.project_point((0.5, 0, 0))
    boundary = pixels[round(b) - 2:round(b) + 3, round(a) - 2:round(a) + 3]
    assert np.max(np.abs(boundary - interior)) > 20, "the real boundary disappeared"
