"""Explicit refined direct quad source: polygons, conservation, settings/recipe identity and refusal (no native work)."""
from dataclasses import asdict
import types

import numpy as np
import pytest

from anyfem.application import floating_apply as fa, floating_prepare as fp, floating_source as fs
from anyfem.application.floating_job import _settings
from anygeometry import floating_overlap as ov
from anyhydro import floating_radiation_fields as ff
from anymesher.floating_quad_source import PROFILE_NAME, build_wet_quad_source
from anyfesolver import floating_response as fr

L, B, T = 6., 3., 1.
RESOLUTION = [6, 4, 2]
PROFILE = dict(name=PROFILE_NAME, resolution=RESOLUTION, full_panel_limit=2500)
WAVE = dict(wave_type='regular', period=8., height=2., heading=0., phase=0., depth=50., dt=1., duration=2.)
POSE = fs.MeanPose(np.eye(3), np.zeros(3))


def plates():
    a, b, y0, y1 = 0., L, -B / 2, B / 2
    return [dict(label='bottom', polygon=[(a, y0, -T), (a, y1, -T), (b, y1, -T), (b, y0, -T)]),
            dict(label='port', polygon=[(a, y0, -T), (b, y0, -T), (b, y0, 0.), (a, y0, 0.)]),
            dict(label='starboard', polygon=[(a, y1, -T), (a, y1, 0.), (b, y1, 0.), (b, y1, -T)]),
            dict(label='aft', polygon=[(a, y0, -T), (a, y0, 0.), (a, y1, 0.), (a, y1, -T)]),
            dict(label='fore', polygon=[(b, y0, -T), (b, y1, -T), (b, y1, 0.), (b, y0, 0.)])]


@pytest.fixture(scope='module')
def source():
    return fs.quad_manifest(build_wet_quad_source(plates(), PROFILE))


def target_cells(nx=5, ny=3, nz=3):
    """Independent wet cells on another grid, one target polygon set per plate."""
    polygons = []
    for plate in plates():
        p = np.array(plate['polygon'], float)
        for s in range(nx):
            for t in range(nz if plate['label'] != 'bottom' else ny):
                n = nz if plate['label'] != 'bottom' else ny
                a, b, c, d = s / nx, (s + 1) / nx, t / n, (t + 1) / n
                e0, e1 = p[1] - p[0], p[3] - p[0]
                polygons.append(np.array([p[0] + a * e0 + c * e1, p[0] + b * e0 + c * e1, p[0] + b * e0 + d * e1, p[0] + a * e0 + d * e1]))
    areas = np.array([ov.polygon_area(q) for q in polygons])
    return types.SimpleNamespace(polygons_wet=tuple(polygons), area_wet=areas)


def test_manifest_keeps_one_native_polygon_per_face(source):
    assert len(source.triangles) == len(source.faces) == 6 * 4 + 2 * 6 * 2 + 2 * 4 * 2
    assert all(q.shape == (4, 3) for q in source.triangles)
    assert source.areas.sum() == pytest.approx(L * B + 2 * L * T + 2 * B * T, rel=1e-13)
    outward = np.array([ov.polygon_plane(q)[0] for q in source.triangles])
    np.testing.assert_array_equal(outward, source.normals)
    assert source.provenance['native_panel'] == 'quad' and source.provenance['full_panels'] == len(source.faces)


@pytest.mark.parametrize('field,value', [('recipe', ff.RECIPE), ('geometry', 'another-source'), ('heading', 0.7),
                                         ('omega', 1.7)])
def test_assembly_refuses_mismatched_retained_family_before_fe_work(source, field, value):
    settings = fp.FloatingSettings(components=(fp.FloatingComponent(1.2, .01, 0., 0.),), times=(0., 1.),
                                   water_depth=50., panel_size=0., source_profile=PROFILE)
    pose_record = dict(rotation_body_to_world=np.eye(3).tolist(), cg_world_m=[3., 0., -.5])
    request = fp.family_requests(settings, source, pose_record)[0]
    snap = types.SimpleNamespace(request=dict(request), omega=1.2)
    if field == 'omega':
        snap.omega = value
    else:
        snap.request[field] = value
    with pytest.raises(fp.FloatingPrepareError, match='identity or frequency') as error:
        fp.assemble_prepared(None, None, settings, pose_record, source, [snap], built=None, props=None)
    assert error.value.stage == 'family'


def test_hydrostatic_triangulation_has_no_half_area_error(source):
    v, f = fs.source_triangles_closed(source)
    tri = v[f]
    area = .5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    assert len(f) == 2 * len(source.faces) and area.sum() == pytest.approx(source.areas.sum(), rel=1e-13)
    mass = 1025. * L * B * T
    rest = fr.hydrostatic_restoring_world(v, f, rho=1025., gravity=9.81, mass=mass, cg_world=(L / 2, 0., -T / 2))
    assert rest['buoyancy_force'] == pytest.approx(mass * 9.81, rel=1e-12)
    assert rest['force_residual_rel'] < 1e-12 and rest['moment_arm_horizontal'] < 1e-12
    np.testing.assert_allclose(rest['buoyancy_centre_relative_cg'], (0., 0., 0.), atol=1e-12)
    C = fr.restoring_body_from_source(v, f, Q0=np.eye(3), cg_world=(L / 2, 0., -T / 2), rho=1025., gravity=9.81, mass=mass)
    assert C[2, 2] == pytest.approx(1025. * 9.81 * L * B, rel=1e-12)


def test_legacy_triangle_faces_are_returned_untouched():
    built = build_wet_quad_source(plates(), PROFILE)
    tris = [t for q in built['polygons'] for t in ov.fan_triangles(q)]
    legacy = fs._bem_arrays(tris, ['p'] * len(tris), dict(generator='anymesher.floating_source_mesh.v2'))
    v, f = fs.source_triangles_closed(legacy)
    assert v is legacy.vertices and np.array_equal(f, legacy.faces[:, :3])


def test_overlap_covers_polygons_and_preserves_force_and_moment(source):
    target = target_cells()
    omap = fs.build_overlap_map(source, target, POSE)
    assert omap.coverage['source_uncovered_rel'] < 1e-12 and omap.coverage['target_unmatched_rel'] < 1e-12
    pressure = source.centers @ np.array([.3, -.2, .7]) + 5.        # one value per native panel
    panel_force = -(pressure[:, None] * source.normals * source.areas[:, None])
    force, moment = np.zeros(3), np.zeros(3)
    panels = set()
    for tris, owners in omap.regions.values():
        panels.update(owners.tolist())
        for tri, i in zip(tris, owners):
            n, c, a = ov.polygon_plane(tri)
            f = -pressure[i] * n * a
            force += f; moment += np.cross(c, f)
    assert panels == set(range(len(source.faces)))
    np.testing.assert_allclose(force, panel_force.sum(axis=0), rtol=0, atol=1e-11)
    np.testing.assert_allclose(moment, np.cross(source.centers, panel_force).sum(axis=0), rtol=0, atol=1e-10)


def test_mutated_manifest_is_refused(source):
    fields = dict(vertices=source.vertices, faces=source.faces, surface_ids=source.surface_ids, triangles=source.triangles,
                  areas=source.areas, centers=source.centers, normals=source.normals, provenance=source.provenance)
    with pytest.raises(fs.FloatingSourceError, match='polygons'):
        fs.SourceManifest(**dict(fields, triangles=(source.triangles[0] + 1., *source.triangles[1:])))
    with pytest.raises(fs.FloatingSourceError, match='counts'):
        fs.SourceManifest(**dict(fields, faces=source.faces[:-1]))
    with pytest.raises(fs.FloatingSourceError, match='provenance'):
        fs.SourceManifest(**dict(fields, provenance=dict(source.provenance, profile=dict(PROFILE, extra=1))))
    back = fs.SourceManifest(**dict(fields, triangles=tuple(np.array(t) for t in np.array(source.triangles))))
    assert back.digest() == source.digest()


def settings(**extra):
    return _settings(dict(wave=WAVE, floating=extra))


def test_profile_settings_are_explicit_and_default_identity_is_unchanged():
    plain = settings()
    assert plain.source_profile is None
    fields = asdict(plain); del fields['source_profile']
    import hashlib, json
    assert plain.digest() == hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode()).hexdigest()
    assert fp._family_route(plain) == {}
    chosen = settings(source_profile=PROFILE)
    fp._check_components(chosen)
    assert chosen.digest() != plain.digest() and chosen.realization_digest() == plain.realization_digest()
    assert fp._family_route(chosen) == dict(recipe=ff.DIRECT_RECIPE, source_capacity=PROFILE_NAME)


@pytest.mark.parametrize('bad', [dict(PROFILE, extra=1), dict(PROFILE, name='other'), dict(PROFILE, full_panel_limit=2501),
                                 dict(PROFILE, resolution=[48, 32, 8])])
def test_unknown_or_excess_profile_controls_refuse(bad):
    with pytest.raises(fp.FloatingPrepareError) as caught:
        fp._check_components(settings(source_profile=bad))
    assert caught.value.stage == 'settings'


@pytest.mark.parametrize('extra', [dict(panel_size=.5), dict(max_panels=300)])
def test_profile_is_the_only_source_control(extra):
    with pytest.raises(fp.FloatingPrepareError, match='controlled by its profile'):
        fp._check_components(settings(source_profile=PROFILE, **extra))


def test_default_budget_stays_600():
    with pytest.raises(fp.FloatingPrepareError, match='outside 4..600'):
        fp._check_components(settings(max_panels=601))


def test_family_requests_separate_route_and_keep_default_identity(source):
    pose = dict(rotation_body_to_world=np.eye(3).tolist(), cg_world_m=[3., 0., -.4])
    default = fp.family_requests(settings(), source, pose)[0]
    assert default['recipe'] == ff.RECIPE and 'source_capacity' not in default
    direct = fp.family_requests(settings(source_profile=PROFILE), source, pose)[0]
    assert direct['recipe'] == ff.DIRECT_RECIPE and direct['source_capacity'] == PROFILE_NAME
    assert ff.radiation_key(default) != ff.radiation_key(direct)


def test_changed_profile_is_a_stale_source():
    chosen = settings(source_profile=PROFILE)
    keys = dict(mass_numeric=dict(mass=1., cg=[0., 0., 0.], inertia=np.eye(3).tolist()), mesh='m',
                realization=chosen.realization_digest(), restoring_gate=fp.RESTORING_GATE_MARKER)
    prepared = types.SimpleNamespace(stage_keys=keys, settings=asdict(chosen))
    props = types.SimpleNamespace(mass=1., cg=np.zeros(3), inertia_cg=np.eye(3))
    stale = lambda s: fa.stale_stage(prepared, props=props, mesh_id='m', settings=s)
    assert stale(chosen) in (None, 'gates')
    assert stale(settings()) == 'source'
    assert stale(settings(source_profile=dict(PROFILE, resolution=[6, 4, 3]))) == 'source'
    legacy = types.SimpleNamespace(stage_keys=keys, settings={k: v for k, v in asdict(settings()).items() if k != 'source_profile'})
    assert fa.stale_stage(legacy, props=props, mesh_id='m', settings=settings()) in (None, 'gates')


def test_saved_profile_is_restored_for_the_dialog(monkeypatch):
    chosen = settings(source_profile=PROFILE)
    saved = asdict(chosen)
    monkeypatch.setattr(fa, 'recover_prepared', lambda payload: types.SimpleNamespace(settings=saved))
    history = types.SimpleNamespace(payload=b'')
    assert fa.restore_dialog_settings(history)['floating']['source_profile'] == PROFILE
    saved = asdict(settings())
    assert 'source_profile' not in fa.restore_dialog_settings(history)['floating']
