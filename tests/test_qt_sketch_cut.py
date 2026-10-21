"""Preview and cancel must not mutate the accepted plate."""
import os
import numpy as np
import pytest
if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)
from anygeometry import EntityRef
from anyfem import commands
from anyfem.ui.qt.sketch_cut import SketchCutDialog
from test_qt_geometry_actions import qapp, window
from test_sketch_plate_cut import circle_definition


def test_cut_preview_cancel_and_accept(window, qapp):
    vertices = [window.run(commands.AddPoint(*point)) for point in
                ((0, 0, 0), (8, 0, 0), (8, 6, 0), (0, 6, 0))]
    face = window.run(commands.AddPlate(vertices))
    source = window.run(commands.AddPlaneSketch(circle_definition(), (0, 0, 0), (0, 0, 1), (1, 0, 0)))
    window.selection.set_mode("face")
    window.selection.restore((EntityRef("face", face),))
    original = window.viewport._scene
    dialog = SketchCutDialog(window, source.feature_id)
    dialog.preview()
    assert dialog.apply_button.isEnabled(), dialog.status.text()
    assert not window.project.geometry.faces[face].holes
    assert window.viewport._scene is not original
    dialog.reject()
    restored = window.viewport._scene
    assert len(restored.faces) == len(original.faces)
    for before_face, after_face in zip(original.faces, restored.faces):
        assert len(before_face.polygons) == len(after_face.polygons)
        for before, after in zip(before_face.polygons, after_face.polygons):
            np.testing.assert_array_equal(before, after)
    assert not window.project.geometry.faces[face].holes
    window.run(dialog.command)
    assert len(window.project.geometry.faces[face].holes) == 1
    dialog.deleteLater()
