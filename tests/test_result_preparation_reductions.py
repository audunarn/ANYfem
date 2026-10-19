"""Warm result preparation shares means without changing either renderer's data."""
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.post.fields import Field
from anyfem.presentation.retained_results import prepare_warm_field, scalar_values
from anyfem.ui.scene import (
    COLOR_BEAM, COLOR_MESH_FILL, FacePatch, MeshEntityRef, Polyline, Scene,
    _ramp_color,
)
from anypost.errors import WorkingSetError


PALETTE = ((0., '#000000'), (1., '#ffffff'))
OPTIONS = dict(limits=(0., 1.), colormap=PALETTE)


def preparation_case(elements=3, polygons_per_element=4):
    """Small deterministic fixture also usable by an external profiling driver."""
    mesh = SimpleNamespace(shells={}, beams={})
    values = {}
    for eid in range(1, elements + 1):
        nodes = (2 * eid - 1, 2 * eid)
        (mesh.shells if eid % 2 else mesh.beams)[eid] = nodes
        values.update({node: eid / (elements + 1) for node in nodes})
    field = Field('ux', 'm', node_values=values)
    ids = list(range(1, elements + 1))
    polygon_ids = ids * polygons_per_element
    triangle = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    patch = FacePatch(None, [triangle.copy() for _ in polygon_ids],
                      ['#123456'] * len(polygon_ids),
                      polygon_owners=[(MeshEntityRef('element', eid),) for eid in polygon_ids])
    scene = Scene(faces=[patch],
                  lines=[Polyline(MeshEntityRef('element', eid), triangle[:2].copy(), color='#234567') for eid in ids],
                  result_field=field, result_mesh=mesh)
    retained = (b'geometry', [], (np.array(polygon_ids), np.array(ids)), mesh)
    return field, scene, retained


def legacy_color(field, mesh, eid, fallback, palette=PALETTE):
    if field.per_element:
        value = field.element_values.get(eid, np.nan)
    else:
        nodes = mesh.shells[eid] if eid in mesh.shells else mesh.beams.get(eid, ())
        values = [field.node_values.get(node, np.nan) for node in nodes]
        value = float(np.mean(values)) if values and np.isfinite(values).all() else np.nan
    return _ramp_color(float(value), palette) if np.isfinite(value) else fallback


def expected_outputs(field, scene, retained, palette=PALETTE):
    scalars = tuple(scalar_values(field, retained[3], ids) for ids in retained[2])
    faces = [(index, [legacy_color(field, scene.result_mesh, owners[0].id, COLOR_MESH_FILL, palette)
                      for owners in patch.polygon_owners]) for index, patch in enumerate(scene.faces)]
    lines = [(index, legacy_color(field, scene.result_mesh, line.ref.id, COLOR_BEAM, palette))
             for index, line in enumerate(scene.lines)]
    return scalars, (faces, lines)


def assert_outputs(field, expected):
    arrays, colors = expected
    assert field._display_buffers[0] == b'geometry'
    for actual, wanted in zip(field._display_buffers[1], arrays, strict=True):
        np.testing.assert_array_equal(actual, wanted)
        assert actual.dtype == np.float32
    assert field._display_buffers[2] == colors


def estimate(scene, retained):
    count = sum(len(patch.polygons) for patch in scene.faces) + len(scene.lines)
    return count * 128 + sum(len(ids) * 32 for ids in retained[2]) + 16384


def test_one_full_precision_reduction_per_element_across_batches_and_colors(monkeypatch):
    field, scene, retained = preparation_case()
    expected = expected_outputs(field, scene, retained)
    original = np.mean
    calls = []
    monkeypatch.setattr(np, 'mean', lambda samples: (calls.append(tuple(samples)), original(samples))[1])
    prepare_warm_field(field, scene, retained, **OPTIONS)
    assert len(calls) == 3
    assert_outputs(field, expected)
    assert scene.faces[0].colors == ['#123456'] * 12
    assert [line.color for line in scene.lines] == ['#234567'] * 3
    assert retained[3] is scene.result_mesh


def test_colors_use_full_precision_mean_not_float32_gpu_scalar():
    field, scene, retained = preparation_case(elements=1)
    field.node_values = {1: .500000005, 2: .500000005}
    palette = ((0., '#000000'), (.5, '#000000'), (.50000001, '#ffffff'), (1., '#ffffff'))
    expected = expected_outputs(field, scene, retained, palette)
    prepare_warm_field(field, scene, retained, limits=(0., 1.), colormap=palette)
    assert_outputs(field, expected)
    assert field._display_buffers[1][0][0] == .5
    full_precision_color = field._display_buffers[2][0][0][1][0]
    assert full_precision_color != _ramp_color(float(field._display_buffers[1][0][0]), palette)


@pytest.mark.parametrize('invalid', ['missing-node', 'nan', 'inf', 'missing-element', 'empty'])
def test_invalid_nodal_samples_keep_gpu_nan_and_software_fallback(invalid):
    field, scene, retained = preparation_case(elements=1)
    if invalid == 'missing-node':
        del field.node_values[2]
    elif invalid in ('nan', 'inf'):
        field.node_values[2] = float(invalid)
    elif invalid == 'missing-element':
        del scene.result_mesh.shells[1]
    else:
        scene.result_mesh.shells[1] = ()
    expected = expected_outputs(field, scene, retained)
    prepare_warm_field(field, scene, retained, **OPTIONS)
    assert_outputs(field, expected)
    assert np.isnan(field._display_buffers[1][0]).all()
    assert field._display_buffers[2][0][0][1] == [COLOR_MESH_FILL] * 4
    assert field._display_buffers[2][1] == [(0, COLOR_BEAM)]


def test_distinct_source_meshes_do_not_share_means(monkeypatch):
    field, scene, retained = preparation_case(elements=1)
    field.node_values = {1: .1, 2: .2, 3: .8, 4: .9}
    gpu_mesh = SimpleNamespace(shells={1: (3, 4)}, beams={})
    retained = (*retained[:3], gpu_mesh)
    expected = expected_outputs(field, scene, retained)
    original = np.mean
    calls = []
    monkeypatch.setattr(np, 'mean', lambda samples: (calls.append(tuple(samples)), original(samples))[1])
    prepare_warm_field(field, scene, retained, **OPTIONS)
    assert len(calls) == 2
    assert_outputs(field, expected)
    assert retained[3] is gpu_mesh and scene.result_mesh is not gpu_mesh


def test_per_element_field_never_reduces_nodes_and_preserves_missing_values(monkeypatch):
    field, scene, retained = preparation_case()
    field.element_values = {1: .7, 2: np.inf}
    expected = expected_outputs(field, scene, retained)
    monkeypatch.setattr(np, 'mean', lambda samples: pytest.fail('per-element field reduced nodes'))
    prepare_warm_field(field, scene, retained, **OPTIONS)
    assert_outputs(field, expected)


def test_no_reuse_across_mutated_fields_or_mesh_connectivity():
    field, scene, retained = preparation_case(elements=1)
    prepare_warm_field(field, scene, retained, **OPTIONS)
    field.node_values[1] = .1
    field.node_values[2] = .3
    prepare_warm_field(field, scene, retained, **OPTIONS)
    assert_outputs(field, expected_outputs(field, scene, retained))
    scene.result_mesh.shells[1] = (1,)
    prepare_warm_field(field, scene, retained, **OPTIONS)
    assert_outputs(field, expected_outputs(field, scene, retained))
    assert field._display_buffers[1][0][0] == np.float32(.1)


@pytest.mark.parametrize('headroom,expected_calls', [(0, 30), (1024 + 256, 21)])
def test_tight_caps_limit_reuse_without_new_refusal(monkeypatch, headroom, expected_calls):
    field, scene, retained = preparation_case()
    expected = expected_outputs(field, scene, retained)
    original = np.mean
    calls = []
    monkeypatch.setattr(np, 'mean', lambda samples: (calls.append(tuple(samples)), original(samples))[1])
    prepare_warm_field(field, scene, retained, working_bytes=estimate(scene, retained) + headroom, **OPTIONS)
    assert len(calls) == expected_calls
    assert_outputs(field, expected)


def test_refusal_and_failed_conversion_do_not_publish_partial_buffers(monkeypatch):
    import anyfem.ui.scene as scene_module
    field, scene, retained = preparation_case()
    old_buffers = (b'previous', (), ())
    field._display_buffers = old_buffers
    field._display_buffer_bytes = 17
    with pytest.raises(WorkingSetError):
        prepare_warm_field(field, scene, retained, working_bytes=estimate(scene, retained) - 1, **OPTIONS)
    assert field._display_buffers is old_buffers and field._display_buffer_bytes == 17
    original = scene_module._ramp_color
    calls = []
    def failing_color(*args):
        calls.append(None)
        if len(calls) == 7:  # Metadata and one software primitive succeeded.
            raise RuntimeError('color conversion failed')
        return original(*args)
    monkeypatch.setattr(scene_module, '_ramp_color', failing_color)
    with pytest.raises(RuntimeError, match='color conversion failed'):
        prepare_warm_field(field, scene, retained, **OPTIONS)
    assert field._display_buffers is old_buffers and field._display_buffer_bytes == 17
