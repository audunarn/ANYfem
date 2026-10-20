"""Complete mutable polygon identity with bounded bulk hashing."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from anygeometry.entities import EntityRef
from anyfem.presentation.retained_scene import scene_key
from anyfem.presentation.scene import Scene, FacePatch
from anyfem.presentation.visualization import VisualizationStyle


def view():
    return SimpleNamespace(construction_active=False, canvas=SimpleNamespace(
        preselected_key=None, add_mesh_arrays=lambda *a, **k: None),
        visualization=VisualizationStyle(), _commercial_selection=True)


def scene_with(polygons):
    return Scene(faces=[FacePatch(EntityRef("face", 1), polygons,
                                 ["red"] * len(polygons))])


@pytest.mark.parametrize("change", ["coordinate", "dtype", "shape", "order",
                                   "container", "signed_zero", "owners", "colors"])
def test_full_identity_detects_mutations_across_block_boundaries(change):
    polygons = [np.array([[float(index), 0., 0.], [1., 0., 0.], [0., 1., 0.]])
                for index in range(4100)]
    scene = scene_with(polygons)
    viewport = view()
    before = scene_key(viewport, scene)
    assert before is not None and scene_key(viewport, deepcopy(scene)) == before
    if change == "coordinate":
        polygons[4097][1, 2] = .25
    elif change == "dtype":
        polygons[4097] = polygons[4097].astype(np.float32)
    elif change == "shape":
        polygons[4097] = polygons[4097].reshape(1, 9)
    elif change == "order":
        polygons[4097], polygons[4098] = polygons[4098], polygons[4097]
    elif change == "container":
        scene.faces[0].polygons = tuple(polygons)
    elif change == "signed_zero":
        polygons[4097][1, 2] = -0.
    elif change == "owners":
        scene.faces[0].polygon_owners = [(EntityRef("face", 2),)] * len(polygons)
    else:
        scene.faces[0].colors[4097] = "blue"
    assert scene_key(viewport, scene) != before


def test_noncontiguous_mixed_dtype_empty_and_shape_payloads_keep_all_bytes():
    source = np.arange(60., dtype=np.float64).reshape(10, 6)
    scene = scene_with([source[:, ::2], np.empty((0, 3)), np.ones((3, 3), np.float32)])
    viewport = view()
    before = scene_key(viewport, scene)
    assert before is not None and scene_key(viewport, deepcopy(scene)) == before
    source[7, 2] += 1
    assert scene_key(viewport, scene) != before


def test_external_array_subclass_declines_without_calling_reducer():
    class External(np.ndarray):
        def __reduce__(self):
            pytest.fail("external reducers must not be invoked")
    scene = scene_with([np.zeros((3, 3)).view(External)])
    assert scene_key(view(), scene) is None


@pytest.mark.parametrize("mixed_layout", [False, True])
def test_same_shape_fortran_polygons_keep_retention_and_detect_mutation(mixed_layout):
    polygons = [np.asfortranarray(np.arange(12.).reshape(4, 3) + offset)
                for offset in range(3)]
    if mixed_layout:
        polygons[1] = np.ascontiguousarray(polygons[1])
    scene = scene_with(polygons)
    before = scene_key(view(), scene)
    assert before is not None
    equivalent = scene_with([np.ascontiguousarray(polygon) for polygon in polygons])
    assert scene_key(view(), equivalent) == before
    polygons[2][2, 1] += 1.
    assert scene_key(view(), scene) != before


@pytest.mark.parametrize("leading_numeric", [False, True])
def test_padded_structured_polygons_preserve_every_original_byte(leading_numeric):
    dtype = np.dtype({"names": ["x", "y", "z"], "formats": ["f8"] * 3,
                      "offsets": [0, 16, 32], "itemsize": 48})
    polygons = [np.zeros(3, dtype=dtype) for _ in range(2)]
    if leading_numeric:
        polygons.insert(0, np.zeros((3, 3)))
    scene = scene_with(polygons)
    before = scene_key(view(), scene)
    assert before is not None and scene_key(view(), scene) == before
    # Byte 8 is padding, absent from both dtype fields and tolist values.
    values = polygons[-1].tolist()
    polygons[-1].view(np.uint8)[8] = 1
    assert polygons[-1].tolist() == values
    assert scene_key(view(), scene) != before


def test_bulk_copies_are_bounded_and_preserve_complete_large_arrays(monkeypatch):
    import anyfem.presentation.retained_scene as module
    original = module.np.concatenate
    sizes = []

    def concatenate(arrays, *args, **kwargs):
        sizes.append(sum(array.nbytes for array in arrays))
        return original(arrays, *args, **kwargs)

    monkeypatch.setattr(module.np, "concatenate", concatenate)
    scene = scene_with([np.zeros((12000, 3)) for _ in range(40)])
    before = scene_key(view(), scene)
    assert sizes and max(sizes) <= 8 * 1024 * 1024
    scene.faces[0].polygons[-1][11000, 2] = 1
    assert scene_key(view(), scene) != before
