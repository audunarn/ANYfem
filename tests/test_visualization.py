"""Focused contracts for app-wide viewport visualization."""

from __future__ import annotations

import numpy as np
import pytest

from anyfem.ui import viewport as viewport_module
from anyfem.ui.scene import FacePatch, Scene
from anyfem.ui.visualization import VisualizationStyle


class _Canvas:
    def __init__(self, _master, **_options):
        self.face_calls = []
        self.background = None
        self.legend_calls = 0

    def set_pick_callback(self, *_args, **_kwargs):
        pass

    def set_highlight(self, *_args, **_kwargs):
        pass

    def clear(self, **_kwargs):
        self.face_calls.clear()

    def add_faces(self, polygons, **options):
        self.face_calls.append((polygons, options))

    def set_thickness_legend(self, *_args, **_kwargs):
        self.legend_calls += 1

    def clear_thickness_legend(self):
        pass

    def set_background(self, colour):
        self.background = colour

    def redraw(self):
        pass

    def pack(self, **kwargs):
        return kwargs

    def grid(self, **kwargs):
        return kwargs


def _viewport(monkeypatch):
    monkeypatch.setattr(
        viewport_module,
        "require_canvas",
        lambda: (lambda *values: values, _Canvas),
    )
    return viewport_module.Viewport(object(), commercial_interaction=False)


def _face_scene():
    return Scene(
        faces=[
            FacePatch(
                ref=None,
                polygons=[np.asarray([(0, 0, 0), (1, 0, 0), (0, 1, 0)])],
                colors=["#ff0000"],
            )
        ],
        legend={"levels": [0, 1], "colors": ["#000000", "#ffffff"]},
    )


def test_wireframe_background_edges_and_legend_are_shared_viewport_settings(
    monkeypatch,
):
    viewport = _viewport(monkeypatch)
    viewport.show(_face_scene())
    viewport.set_visualization(
        VisualizationStyle(
            background="#20242a",
            render_mode="Wireframe",
            surface_opacity=0.4,
            edge_color="#f0f0f0",
            edge_width=3,
            show_legend=False,
        )
    )

    _polygons, options = viewport.canvas.face_calls[-1]
    assert viewport.canvas.background == "#20242a"
    assert options["outline"] == "#f0f0f0"
    assert options["width"] == 3
    assert options["opacity"] == 0.0
    assert options["lit"] is False
    assert viewport.canvas.legend_calls == 1  # only the view before legend hiding


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"surface_opacity": 1.1}, "opacity"),
        ({"edge_width": 0}, "edge width"),
        ({"render_mode": "X-ray"}, "render mode"),
        ({"geometry_detail": "Unlimited"}, "geometry detail"),
    ],
)
def test_invalid_visualization_settings_fail_closed(changes, message):
    with pytest.raises(ValueError, match=message):
        VisualizationStyle(**changes)


def test_results_hide_and_can_restore_the_imperfect_reference():
    """Exercise the actual Results layer switch through a real Tk viewport."""

    import time
    import tkinter as tk

    from anygeometry.entities import EntityRef

    from anyfem import commands as cmd
    from anyfem.model import plate_mode
    from anyfem.model.attributes import Support
    from anyfem.ui.app import AnyFemApp
    from anyfem.ui.scene import COLOR_IMPERFECTION

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for tkinter")
    app = AnyFemApp(root)
    try:
        points = [
            app.run(cmd.AddPoint(x, y))
            for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))
        ]
        face = app.run(cmd.AddPlate(points))
        app.run(cmd.AssignPlate(face, "plate"))
        app.run(
            cmd.AddImperfection(
                plate_mode(app.project.face(face), amplitude=0.001)
            )
        )
        for edge in app.project.geometry.edges:
            app.run(
                cmd.AddSupport(
                    Support(
                        f"s{edge}",
                        EntityRef("edge", edge),
                        {"ux": 0.0, "uy": 0.0, "uz": 0.0},
                    )
                )
            )
        app.run(cmd.AddPressure(EntityRef("face", face), 1_000.0))
        app.generate_mesh(0.5)
        app.solve("Linear static")
        deadline = time.time() + 20.0
        while app.solution is None and time.time() < deadline:
            root.update()
            time.sleep(0.01)
        assert app.solution is not None

        results = app.panels["Results"]
        packed = list(results.pack_slaves())
        assert packed.index(results._setup_tabs) < packed.index(
            results._result_set_section
        )
        assert results.show_imperfect_reference() is False
        assert not any(
            line.color == COLOR_IMPERFECTION for line in app.viewport._scene.lines
        )

        results._show_imperfect_reference.set(True)
        results._apply_visualization()
        root.update()
        assert any(
            line.color == COLOR_IMPERFECTION for line in app.viewport._scene.lines
        )

        results._show_result_nodes.set(True)
        results._apply_visualization()
        root.update()
        mesh_nodes = [
            marker
            for marker in app.viewport._scene.points
            if getattr(marker.ref, "kind", "") == "node"
        ]
        assert len(mesh_nodes) == len(app.mesh.nodes)
    finally:
        app.destroy()
        root.update()
        root.destroy()
