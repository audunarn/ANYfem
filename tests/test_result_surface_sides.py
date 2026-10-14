"""Result shells keep their scalar colours and picking from either side."""
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.post.fields import Field
from anyfem.presentation.viewport import SceneViewport
from anyfem.presentation.visualization import VisualizationStyle
from anyfem.selection import MeshEntityRef, entity_tag
from anyfem.ui.scene import FacePatch, Scene


class Canvas:
    backend_name = "gpu"

    def __init__(self):
        self.submissions = []

    def add_mesh_arrays(self, mesh, **options):
        handle = SimpleNamespace(
            removed=False,
            element_scalars=mesh.element_scalars.copy(),
            update_appearance=lambda **values: options.update(values),
        )
        handle.update_scalar_fields = lambda **values: setattr(handle, "element_scalars", values["element_scalars"].copy())
        self.submissions.append((mesh, options, handle))
        return handle

    def clear(self, **options): pass
    def redraw(self): pass


class View(SceneViewport):
    def __init__(self, scene, render_mode):
        from any3dview import PickBinding, PickOwner

        self.canvas = Canvas()
        self._visualization = VisualizationStyle(render_mode=render_mode)
        self._commercial_selection = True
        self._selection_api = {"PickBinding": PickBinding, "PickOwner": PickOwner}
        self._retained_result = self._retained_result_canvas = None
        self._scene = scene

    def _clear_hover_state(self): pass
    def _draw(self, scene): pass
    def _draw_construction_overlay(self): pass
    def _draw_legend(self, scene): pass
    def _apply_highlight(self): pass


def result_scene(reverse):
    # Mixed S3/S4 surfaces, with deliberately opposite normals.
    triangle = np.array([(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)])
    quad = np.array([(2., 0., 0.), (2., 1., 0.), (3., 1., 0.), (3., 0., 0.)])
    polygons = [triangle, quad]
    if reverse:
        polygons = [polygon[::-1] for polygon in polygons]
    return Scene(
        faces=[FacePatch(None, polygons, ["#ff0000", "#0000ff"],
                         polygon_owners=[(MeshEntityRef("element", 17),),
                                         (MeshEntityRef("element", 93),)])],
        result_field=Field("von_mises", "Pa", element_values={17: 200e6, 93: 80e6}),
        result_mesh=SimpleNamespace(shells={17: (1, 2, 3), 93: (4, 5, 6, 7)}, beams={}),
        result_scalar_range=(0., 200e6),
        result_colormap=((0., "#0000ff"), (1., "#ff0000")),
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("render_mode", ["Shaded", "Shaded with edges"])
def test_production_result_upload_is_two_sided_with_original_stress_and_owners(reverse, render_mode):
    scene = result_scene(reverse)
    original_polygons = [polygon.copy() for polygon in scene.faces[0].polygons]
    view = View(scene, render_mode)
    view._render_scene(scene)
    mesh, options, handle = view.canvas.submissions[0]
    assert options["cull_backface"] is False
    assert not options.get("back_color")  # Rear side uses the selected contour, too.
    assert options["scalar_range"] == scene.result_scalar_range
    assert options["colormap"] == scene.result_colormap
    np.testing.assert_array_equal(mesh.element_ids, [17, 93])
    np.testing.assert_array_equal(mesh.element_scalars, [200e6, 80e6])
    np.testing.assert_array_equal(mesh.triangle_to_element, [0, 1, 1])
    for triangle_index, eid in enumerate([17, 93, 93]):
        owners = options["owners"].owners_for("triangle", triangle_index)
        assert len(owners) == 1
        assert owners[0].key == entity_tag(MeshEntityRef("element", eid))
    assert scene.result_field.element_values == {17: 200e6, 93: 80e6}
    for original, polygon in zip(original_polygons, scene.faces[0].polygons):
        np.testing.assert_array_equal(original, polygon)

    # A later field switch reuses the same geometry and two-sided policy.
    changed = Field("sigma_x_bottom", "Pa", element_values={17: -100e6, 93: 120e6})
    assert view.update_result_field(changed, scalar_range=(-100e6, 120e6),
                                    colormap=scene.result_colormap, legend={})
    assert len(view.canvas.submissions) == 1
    assert options["cull_backface"] is False
    np.testing.assert_array_equal(handle.element_scalars, [-100e6, 120e6])
