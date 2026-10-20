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


@pytest.mark.parametrize('per_element', [False, True])
@pytest.mark.parametrize('headroom', [None, 0, 1024 + 512])
def test_color_reuse_is_call_local_and_shares_the_working_cap(monkeypatch, per_element, headroom):
    import anyfem.ui.scene as scene_module
    field, scene, retained = preparation_case()
    if per_element:
        field.element_values = {1: .25, 2: .5, 3: .75}
    expected = expected_outputs(field, scene, retained)
    original = scene_module._ramp_color
    calls = []
    monkeypatch.setattr(scene_module, '_ramp_color',
                        lambda *args: (calls.append(args), original(*args))[1])
    budget = {} if headroom is None else {'working_bytes': estimate(scene, retained) + headroom}
    prepare_warm_field(field, scene, retained, **OPTIONS, **budget)
    assert_outputs(field, expected)
    # Five legend colors plus one conversion per element when space permits.
    expected_calls = 8 if headroom is None else 12 if per_element and headroom else 20
    assert len(calls) == expected_calls
    # Reusing the field with a new palette must never reuse old color strings.
    palette = ((0., '#ff0000'), (1., '#0000ff'))
    expected = expected_outputs(field, scene, retained, palette)
    calls.clear()
    prepare_warm_field(field, scene, retained, limits=(0., 1.), colormap=palette, **budget)
    assert_outputs(field, expected)
    assert len(calls) == expected_calls


def test_cached_invalid_element_preserves_distinct_primitive_fallbacks():
    field, scene, retained = preparation_case(elements=1)
    field.element_values = {1: np.nan}
    prepare_warm_field(field, scene, retained, **OPTIONS)
    assert_outputs(field, expected_outputs(field, scene, retained))
    assert field._display_buffers[2][0][0][1] == [COLOR_MESH_FILL] * 4
    assert field._display_buffers[2][1] == [(0, COLOR_BEAM)]
    field.element_values[1] = .75
    prepare_warm_field(field, scene, retained, **OPTIONS)
    assert_outputs(field, expected_outputs(field, scene, retained))


@pytest.mark.parametrize('tight_cap', [False, True])
def test_native_mesh_avoids_rebuilding_shells_preserving_precedence_and_mutation(tight_cap):
    import cProfile
    from anymesher import Mesh
    field, scene, retained = preparation_case()
    field.node_values = {node: node / 10. for node in range(1, 7)}
    mesh = Mesh(quads={1: (1, 2), 2: (1,)}, tris={2: (3, 4)},
                beams={1: (6,), 2: (6,), 3: (5, 6)})
    scene.result_mesh = mesh
    retained = (*retained[:3], mesh)
    expected = expected_outputs(field, scene, retained)
    budget = {'working_bytes': estimate(scene, retained)} if tight_cap else {}
    profile = cProfile.Profile()
    profile.runcall(prepare_warm_field, field, scene, retained, **OPTIONS, **budget)
    assert_outputs(field, expected)
    assert not any(entry.code is Mesh.shells.fget.__code__ for entry in profile.getstats())
    # Attribute reassignment and subsequent in-place edits both remain live.
    mesh.quads = {1: (6,), 2: (1,)}
    mesh.tris = {2: (6,)}
    mesh.beams = {3: (1,)}
    prepare_warm_field(field, scene, retained, **OPTIONS, **budget)
    assert_outputs(field, expected_outputs(field, scene, retained))
    mesh.quads[1] = (2,)
    mesh.tris[2] = (1,)
    mesh.beams[3] = (3,)
    prepare_warm_field(field, scene, retained, **OPTIONS, **budget)
    assert_outputs(field, expected_outputs(field, scene, retained))


def test_custom_mesh_shell_property_keeps_original_access_behavior():
    from anymesher import Mesh
    class CustomMesh(Mesh):
        shell_reads = 0
        @property
        def shells(self):
            self.shell_reads += 1
            return super().shells
    field, scene, retained = preparation_case()
    mesh = CustomMesh(quads=dict(scene.result_mesh.shells), beams=dict(scene.result_mesh.beams))
    scene.result_mesh = mesh
    retained = (*retained[:3], mesh)
    expected = expected_outputs(field, scene, retained)
    mesh.shell_reads = 0
    prepare_warm_field(field, scene, retained, **OPTIONS)
    assert mesh.shell_reads == 5
    assert_outputs(field, expected)


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


def test_decoded_native_ramps_match_reference_channels_at_boundaries_and_random_values():
    from anyfem.presentation.retained_results import _decoded_ramp, _decoded_color
    from anyfem.ui.scene import RESULT_COLORMAPS
    rng = np.random.default_rng(117)
    palettes = list(RESULT_COLORMAPS.values()) + [
        ((0., '#000000'), (.5, '#000000'), (.50000001, '#ffffff'), (1., '#ffffff')),
        ((0., '#FE0180'), (.000001, '#01ff00'), (1., '#aA03F7')),
    ]
    for palette in palettes:
        decoded = _decoded_ramp(palette)
        assert decoded is not None
        boundaries = [value for stop, _ in palette
                      for value in (stop, np.nextafter(stop, -np.inf), np.nextafter(stop, np.inf))]
        for position in [*boundaries, *rng.uniform(-1., 2., 1000)]:
            assert _decoded_color(position, decoded) == _ramp_color(position, palette)


@pytest.mark.parametrize('palette', [
    ((0, '#000000'), (1, '#ffffff')),
    ([0., '#000000'], [1., '#ffffff']),
    ((0., '#000000'), (.5, '#ffffff'), (.5, '#ff0000'), (1., '#ffffff')),
    ((0., '#000000'), (float('nan'), '#ffffff'), (1., '#ffffff')),
    ((.1, '#000000'), (1., '#ffffff')),
    ((0., '#bad'), (1., '#ffffff')),
])
def test_unusual_ramps_keep_reference_fallback(palette):
    from anyfem.presentation.retained_results import _decoded_ramp
    assert _decoded_ramp(palette) is None


def test_decoded_workspace_is_bounded_and_custom_blender_keeps_fallback(monkeypatch):
    from anyfem.presentation.retained_results import _decoded_ramp, _decoded_color
    import anyfem.ui.scene as scene_module
    palette = tuple((float(i / 31), '#ffffff' if i % 2 else '#000000') for i in range(32))
    decoded = _decoded_ramp(palette)
    assert decoded is not None and len(decoded) == 31
    for position in np.linspace(0., 1., 301):
        assert _decoded_color(position, decoded) == _ramp_color(position, palette)
    assert _decoded_ramp(tuple((float(i / 32), '#123456') for i in range(33))) is None
    monkeypatch.setattr(scene_module, '_blend', lambda *args: '#123456')
    assert _decoded_ramp(palette) is None


def test_custom_scene_numpy_binding_keeps_reference_fallback(monkeypatch):
    from anyfem.presentation.retained_results import _decoded_ramp
    import anyfem.ui.scene as scene_module
    monkeypatch.setattr(scene_module, 'np', SimpleNamespace(array=np.array, clip=np.clip))
    assert _decoded_ramp(PALETTE) is None


def test_full_preparation_uses_native_ramp_and_preserves_current_colors(monkeypatch):
    import anyfem.presentation.retained_results as module
    from anyfem.ui.scene import RESULT_COLORMAPS
    field, scene, retained = preparation_case(elements=50)
    values = np.random.default_rng(112).uniform(-1., 2., 50)
    values[:3] = [np.nan, np.inf, .500000005]
    field.element_values = {eid: value for eid, value in enumerate(values, 1)}
    original = module._decoded_color
    calls = []
    monkeypatch.setattr(module, '_decoded_color', lambda *a: (calls.append(a), original(*a))[1])
    for palette in [PALETTE, RESULT_COLORMAPS['Cool-warm'], RESULT_COLORMAPS['Viridis']]:
        prepare_warm_field(field, scene, retained, limits=(0., 1.), colormap=palette)
        assert_outputs(field, expected_outputs(field, scene, retained, palette))
        field.element_values[4] = .001
    assert len(calls) == 48 * 3
    calls.clear()
    prepare_warm_field(field, scene, retained, working_bytes=estimate(scene, retained), **OPTIONS)
    assert_outputs(field, expected_outputs(field, scene, retained))
    assert not calls


def test_owner_lookup_preserves_lazy_first_match_and_exception():
    from anyfem.presentation.retained_results import element_id
    observed = []
    def owners():
        observed.append('node')
        yield SimpleNamespace(kind='node', id=1)
        observed.append('element')
        yield SimpleNamespace(kind='element', id='17')
        raise AssertionError('consumed beyond first element')
    assert element_id(owners()) == 17
    assert observed == ['node', 'element']
    with pytest.raises(ValueError):
        element_id([SimpleNamespace(kind='element', id='bad')])
