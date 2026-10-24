"""Presenter snapshots coordinates before owner and color callbacks."""
from types import SimpleNamespace

import numpy as np
import pytest

from any3dview.retained_viewer import RetainedViewer
from anyfem.presentation.viewport import SceneViewport


class Capture(RetainedViewer):
    def __init__(self):
        self.outputs = []

    def add_mesh_arrays(self, mesh, **options):
        self.outputs.append((mesh, options))


@pytest.mark.parametrize("callback", ["owner", "color"])
def test_face_batch_preserves_values_before_callbacks(callback):
    # This test also runs against the frozen pre-change presenter: expected
    # values follow its eager polygon.tolist() boundary.
    polygon = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    expected = polygon.copy()

    class Color:
        def __str__(self):
            if callback == "color":
                polygon[1, 0] = 7.
            return "red"

    def binding(*args):
        if callback == "owner":
            polygon[1, 0] = 8.
        return None

    patch = SimpleNamespace(polygons=[polygon], colors=[Color()],
                            polygon_owners=[()], tag="",
                            owners_for_polygon=lambda index: ())
    canvas = Capture()
    view = SimpleNamespace(canvas=canvas, _face_style=lambda patch: ("black", 1., True),
                           _pick_binding=binding, _smooth_options=lambda group: {},
                           _visualization=SimpleNamespace(edge_width=1))
    view._face_pick_binding = SceneViewport._face_pick_binding.__get__(view)
    SceneViewport._draw_face_batches(view, [patch])
    assert polygon[1, 0] != expected[1, 0]
    assert len(canvas.outputs) == 1
    assert canvas.outputs[0][0].positions.tobytes() == expected.tobytes()
