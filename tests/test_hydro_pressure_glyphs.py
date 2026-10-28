"""Source-hydropanel pressure arrows (display only).

The pressure fields below are SYNTHETIC: a one-component response chosen so that
signs, exact zeros and magnitudes are known in closed form on the actual
admitted 236-panel source geometry.  No BEM, mapping or structural solve runs.
"""
from dataclasses import replace
import json
import numpy as np
import pytest

from anyfem import Project, steel
from anyfem.model import WetPressure, WetSurface
from anyfem.application import pressure_transfer as bridge
from anyfem.application.hydro_wave_pressure import _source_mesh, target_hull_cells
from anyfem.document import canonical_hash
from anyfem.presentation import hydro_pressure_glyphs as glyphs
from anyfem.ui.scene import Arrow, Scene, _draw_wet_pressures, build_attribute_overlay
from anyhydro.pressure_bank import PressureResponseBank
from anyloads.marine import AiryComponent
from anyloads.sea_state import WaveRealization
from anyloads.spectral_pressure_history import (
    SpectralMappedPressureHistory, build_pressure_mapping_plan, map_pressure_bank)
from anymesher import Mesh
from anymesher.serialize import mesh_to_dict

OMEGA = 0.7
FIRST_TIME = 0.3  # first AVAILABLE time, deliberately not zero
PANELS = 236


def project_and_meshes():
    p = Project(); p.add_material(steel()); p.add_plate_section('plate', .01, 'S355')
    rectangles = {
        'bottom': ((0, -2.5, 0), (0, 5, 0), (60, 0, 0)),
        'port': ((0, -2.5, 0), (60, 0, 0), (0, 0, 1.5)),
        'starboard': ((0, 2.5, 0), (0, 0, 1.5), (60, 0, 0)),
        'end0': ((0, -2.5, 0), (0, 0, 1.5), (0, 5, 0)),
        'end60': ((60, -2.5, 0), (0, 5, 0), (0, 0, 1.5))}
    faces = {}
    for name, (origin, u, v) in rectangles.items():
        origin, u, v = map(np.array, (origin, u, v))
        vertices = p.geometry.add_points([origin, origin+u, origin+u+v, origin+v])
        face = p.geometry.add_face(p.geometry.add_polyline(vertices, close=True)); faces[name] = face
        p.assign_plate(face, 'plate')
        p.add_wet_surface(WetSurface(ref=p.face(face), name=name, fluid_side='away_from_point',
                                     interior_point=(30, 0, 1)))

    def mesh(divisions, start):
        m = Mesh(); nid = start; eid = start
        for name, (origin, u, v) in rectangles.items():
            origin, u, v = map(np.array, (origin, u, v)); elements = []
            for i in range(divisions):
                points = [origin+u*s+v*t for s, t in ((i/divisions, 0), ((i+1)/divisions, 0),
                                                       ((i+1)/divisions, 1), (i/divisions, 1))]
                ids = tuple(range(nid, nid+4)); nid += 4
                for node, point in zip(ids, points): m.nodes[node] = point.astype(float)
                m.quads[eid] = ids; elements.append(eid); eid += 1
            m.elements_of_face[faces[name]] = elements
        return m
    return p, mesh(1, 10), mesh(2, 100)


def synthetic_response(centers):
    """Real, signed, with exact zeros near mid-length (SYNTHETIC)."""
    return np.round((centers[:, 0] - 30.)/10.) + 0j


def synthetic_history(project, mesh, *, scale_amplitude=1., times=None):
    v, f, labels = _source_mesh(); polygons = v[f]
    av = np.cross(polygons[:, 1]-polygons[:, 0], polygons[:, 2]-polygons[:, 0])/2
    av += np.cross(polygons[:, 2]-polygons[:, 0], polygons[:, 3]-polygons[:, 0])/2
    areas = np.linalg.norm(av, axis=1); centers = polygons.mean(axis=1)
    bank = PressureResponseBank(v, f, labels, centers, av/areas[:, None], areas, [OMEGA],
                                synthetic_response(centers)[None, :])
    realization = WaveRealization((AiryComponent(scale_amplitude, OMEGA, phase=OMEGA*FIRST_TIME),))
    exterior = {s.name: s.ref.id for s in project.wet_surfaces}
    cells = target_hull_cells(project, mesh, exterior); mesh_hash = canonical_hash(mesh_to_dict(mesh))
    plan = build_pressure_mapping_plan(bank, *cells, mesh_content_sha256=mesh_hash)
    fields, _ = map_pressure_bank(bank, plan, *cells[1:4], surface_ids=cells[4], mesh_content_sha256=mesh_hash)
    times = tuple(FIRST_TIME + np.arange(11)/10) if times is None else times
    mapped = SpectralMappedPressureHistory(times, tuple(cells[0]), tuple(bank.omegas), fields.real, fields.imag,
        bank.heading, mesh_hash, bank.bank_id, json.dumps(realization.to_dict()))
    return bridge.retain_pressure_source(mapped, bank, realization, project), bank


@pytest.fixture
def world():
    glyphs.clear_snapshot_cache()
    project, old, new = project_and_meshes()
    history, bank = synthetic_history(project, old)
    load = WetPressure(history=history, mapping_key='element', name='wave', scale=2.)
    owner = project.add_load_series('wave', history.times); owner.wet_pressures.append(load)
    return project, old, new, history, bank, load


def arrows_of(project, mesh=None):
    return build_attribute_overlay(project, case_name='wave', mesh=mesh)


def test_one_arrow_per_source_panel_with_first_time_signed_values(world):
    project, old, new, history, bank, load = world
    scene = arrows_of(project)
    assert len(scene.arrows) == PANELS == len(bank.panel_ids)
    # t0 = history.times[0] = 0.3; phase chosen so p = scale * response there.
    expected = 2. * synthetic_response(bank.centers).real
    snapshot = glyphs.source_pressure_snapshot(project, load, 1.0)
    assert snapshot.time == FIRST_TIME and snapshot.panel_ids == bank.panel_ids
    np.testing.assert_allclose(snapshot.pressures, expected, atol=1e-10)
    np.testing.assert_allclose(snapshot.centers, bank.centers + [0., 0., 1.5], atol=0)
    np.testing.assert_array_equal(snapshot.normals, bank.normals)
    made = glyphs.build_source_glyphs(snapshot, 6.)
    lengths = np.linalg.norm(made.ends - made.starts, axis=1)
    for i, arrow in enumerate(scene.arrows):
        np.testing.assert_allclose(arrow.start, made.starts[i]); np.testing.assert_allclose(arrow.end, made.ends[i])
        assert arrow.color == made.colors[i]
        c, n = snapshot.centers[i], snapshot.normals[i]
        if expected[i] >= 0:   # inward: tip on the panel, tail outside along the outward normal
            np.testing.assert_allclose(arrow.end, c); assert np.dot(arrow.start-arrow.end, n) > 0
        else:                  # outward (suction): tail on the panel, tip away
            np.testing.assert_allclose(arrow.start, c); assert np.dot(arrow.end-arrow.start, n) > 0
    assert (expected > 0).any() and (expected < 0).any() and (expected == 0).any()
    assert len(set(made.colors[i] for i in np.flatnonzero(expected > 0))) > 1
    assert set(made.colors[i] for i in np.flatnonzero(expected > 0)).isdisjoint(
        made.colors[i] for i in np.flatnonzero(expected < 0))
    assert {made.colors[i] for i in np.flatnonzero(expected == 0)} == {glyphs.NEUTRAL_COLOR}
    assert lengths.max() <= 3. and lengths.min() > 0.  # modest, no model-spanning vectors


def test_distinct_target_meshes_give_identical_source_arrows_without_mapping(world, monkeypatch):
    project, old, new, history, *_ = world

    def forbidden(*a, **k): raise AssertionError('mapping/target evaluation is forbidden while drawing')
    import anyloads.spectral_pressure_history as spectral
    import anyfem.application.hydro_wave_pressure as hwp
    import anyfem.solve.build as build
    monkeypatch.setattr(bridge, 'effective_wave_pressure', forbidden)
    monkeypatch.setattr(spectral, 'build_pressure_mapping_plan', forbidden)
    monkeypatch.setattr(spectral, 'map_pressure_bank', forbidden)
    monkeypatch.setattr(hwp, 'target_hull_cells', forbidden)
    monkeypatch.setattr(build, 'wet_element_pressures', forbidden)
    monkeypatch.setattr(SpectralMappedPressureHistory, 'evaluate', forbidden)
    before_mesh, on_old, on_new = arrows_of(project), arrows_of(project, old), arrows_of(project, new)
    assert len(before_mesh.arrows) == len(on_old.arrows) == len(on_new.arrows) == PANELS
    for a, b, c in zip(before_mesh.arrows, on_old.arrows, on_new.arrows):
        np.testing.assert_array_equal(a.start, b.start); np.testing.assert_array_equal(a.end, c.end)
        assert a.color == b.color == c.color
    assert canonical_hash(mesh_to_dict(old)) != canonical_hash(mesh_to_dict(new))


def test_zero_pressure_is_neutral_and_finite(world):
    project, *_ , load = world
    case = project.load_cases['wave']
    case.wet_pressures[0] = replace(load, scale=0.)
    scene = arrows_of(project)
    assert len(scene.arrows) == PANELS
    assert {a.color for a in scene.arrows} == {glyphs.NEUTRAL_COLOR}
    assert np.isfinite([np.r_[a.start, a.end] for a in scene.arrows]).all()
    assert np.isfinite(scene.legend['levels']).all()
    assert glyphs.pressure_color(0., 0.) == glyphs.NEUTRAL_COLOR == glyphs.pressure_color(float('nan'), 5.)


def test_legend_signed_scale_and_result_legend_is_not_overridden(world):
    project, old, new, history, bank, load = world
    scene = arrows_of(project)
    peak = 2. * np.abs(synthetic_response(bank.centers)).max()
    assert scene.legend['unit'] == 'Pa' and 't = 0.3' in scene.legend['title']
    np.testing.assert_allclose(scene.legend['levels'], np.linspace(-peak, peak, 5), atol=1e-10)
    assert len(scene.legend['colors']) == 5
    assert scene.legend['colors'][0] != scene.legend['colors'][-1]
    result = Scene(legend={'levels': [0, 1], 'unit': 'm', 'title': 'Displacement', 'colors': ['#000', '#fff']})
    _draw_wet_pressures(result, project, project.geometry, project.resolve_case(project.series_children('wave')[0]),
                        None, 6.)
    assert result.legend['title'] == 'Displacement' and len(result.arrows) == PANELS


def test_color_scale_is_symmetric_and_bounded():
    peak = 10.
    assert glyphs.pressure_color(peak, peak) != glyphs.pressure_color(-peak, peak)
    assert glyphs.pressure_color(1e9, peak) == glyphs.pressure_color(peak, peak)  # clipped
    levels = {glyphs.pressure_color(x, peak) for x in np.linspace(-peak, peak, 2001)}
    assert len(levels) <= 2*10 + 1


def test_snapshot_cache_hit_and_invalidation(world, monkeypatch):
    project, old, new, history, bank, load = world
    import anyloads.pressure_series as series
    compiled = []; real = series.compile_pressure_series
    monkeypatch.setattr(series, 'compile_pressure_series', lambda *a, **k: (compiled.append(1) or real(*a, **k)))
    arrows_of(project); arrows_of(project, old); arrows_of(project, new)
    info = glyphs.snapshot_cache_info()
    assert len(compiled) == 1 and info['decodes'] == 1 and info['hits'] == 2 and info['entries'] == 1
    # A different load scale is a different binding: one new decode, still bounded.
    project.load_cases['wave'].wet_pressures[0] = replace(load, scale=3.)
    arrows_of(project)
    assert len(compiled) == 2 and glyphs.snapshot_cache_info()['entries'] == 2
    # A different immutable history identity never reuses a stale entry.
    other, _ = synthetic_history(project, old, scale_amplitude=2.)
    assert other.content_sha256 != history.content_sha256
    project.load_cases['wave'].wet_pressures[0] = WetPressure(history=other, mapping_key='element', scale=2.)
    scene = arrows_of(project)
    assert len(compiled) == 3
    np.testing.assert_allclose(glyphs.source_pressure_snapshot(project, project.load_cases['wave'].wet_pressures[0]).pressures,
                               4. * synthetic_response(bank.centers).real, atol=1e-10)
    assert len(scene.arrows) == PANELS
    for _ in range(10):  # bounded cache
        project.load_cases['wave'].wet_pressures[0] = replace(load, scale=1. + _)
        arrows_of(project)
    assert glyphs.snapshot_cache_info()['entries'] <= glyphs.snapshot_cache_info()['limit']


def test_changed_wet_domain_is_refused_not_stale_and_not_mapped(world):
    project, old, new, history, bank, load = world
    assert len(arrows_of(project, new).arrows) == PANELS
    project.wet_surfaces[0] = replace(project.wet_surfaces[0], fluid_side='positive_normal', interior_point=None)
    scene = arrows_of(project, new)
    assert scene.arrows == [] and scene.legend is None  # neither source nor mapped arrows
    with pytest.raises(bridge.PressureRemapError):
        glyphs.source_pressure_snapshot(project, load, 1.0)


def test_generic_and_mixed_loads_are_preserved(world):
    project, old, new, history, bank, load = world
    generic = WetPressure(value=1000., name='hydrostatic-ish')
    only = project.load_case('generic')
    only.wet_pressures.append(generic)
    plain = build_attribute_overlay(project, case_name='generic', mesh=new)
    assert len(plain.arrows) == 10 and plain.legend is None  # one per structural element, unchanged
    project.load_cases['wave'].wet_pressures.append(generic)
    mixed = arrows_of(project, new)
    assert len(mixed.arrows) == PANELS + 10 and mixed.legend is not None
    mixed_before_mesh = arrows_of(project)
    assert len(mixed_before_mesh.arrows) > PANELS


def test_cached_snapshot_arrays_are_read_only_and_repeated_draws_unchanged(world):
    project, old, new, history, bank, load = world
    first = [(a.start.copy(), a.end.copy(), a.color) for a in arrows_of(project).arrows]
    snapshot = glyphs.source_pressure_snapshot(project, load, 1.0)
    for name in ('centers', 'normals', 'areas', 'pressures'):
        array = getattr(snapshot, name)
        assert not array.flags.writeable
        with pytest.raises(ValueError):
            array[0] = 1e9
    again = [(a.start, a.end, a.color) for a in arrows_of(project, new).arrows]
    for (s0, e0, c0), (s1, e1, c1) in zip(first, again):
        np.testing.assert_array_equal(s0, s1); np.testing.assert_array_equal(e0, e1); assert c0 == c1
    assert glyphs.snapshot_cache_info()['decodes'] == 1


def _last_step(project):
    steps = project.series_children('wave')
    return project.resolve_case(steps[-1]), steps


def test_number_envelope_on_derived_last_step_uses_the_first_time_step(world):
    from anyfem.model.attributes import Envelope
    project, old, new, history, bank, load = world
    project.load_cases['wave'].wet_pressures[0] = replace(
        load, envelope=Envelope(by='number', start=1, stop=1))
    last, steps = _last_step(project)
    assert len(steps) > 1 and last.envelope_factor(project.load_cases['wave'].wet_pressures[0].envelope) == 0.
    scene = Scene()
    _draw_wet_pressures(scene, project, project.geometry, last, None, 6.)
    assert len(scene.arrows) == PANELS  # first sample shown, not the later step's zero
    first = Scene()
    _draw_wet_pressures(first, project, project.geometry, project.resolve_case(steps[0]), None, 6.)
    assert len(first.arrows) == PANELS


def test_overlay_of_last_step_scales_first_snapshot_with_canonical_step_one(world):
    from anyfem.model.attributes import Envelope
    project, old, new, history, bank, load = world
    steps = project.series_children('wave'); n = len(steps)
    project.load_cases['wave'].wet_pressures[0] = replace(
        load, envelope=Envelope(by='number', start=1, stop=n, expression='number'))
    first = build_attribute_overlay(project, case_name=steps[0].name)
    last = build_attribute_overlay(project, case_name=steps[-1].name)  # real public path, load stays visible
    assert len(first.arrows) == len(last.arrows) == PANELS
    peak = 2. * np.abs(synthetic_response(bank.centers)).max()  # step 1 -> factor 1
    np.testing.assert_allclose(last.legend['levels'][-1], peak, atol=1e-10)
    np.testing.assert_allclose(first.legend['levels'], last.legend['levels'], atol=1e-10)


def test_mixed_first_times_share_one_scale_and_truthful_legend(world):
    project, old, new, history, bank, load = world
    other_history, _ = synthetic_history(project, old, times=tuple(0.5 + np.arange(11)/10))
    big = WetPressure(history=other_history, mapping_key='element', name='big', scale=10.)
    case = project.load_cases['wave']
    groups = glyphs.glyph_groups(project, case, [load, big], 6.)
    assert len(groups) == 2
    a, b = groups
    assert a.peak == b.peak == max(np.abs(a.pressures).max(), np.abs(b.pressures).max())
    assert a.legend == b.legend
    assert '0.3' in a.legend['title'] and '0.5' in a.legend['title'] and 'first times' in a.legend['title']
    i = int(np.argmax(np.abs(a.pressures)))
    assert a.colors[i] == glyphs.pressure_color(a.pressures[i], a.peak)
    assert a.colors[i] != glyphs.pressure_color(a.pressures[i], np.abs(a.pressures).max())  # not self-normalised
    assert abs(a.pressures[i]) < b.peak
    single = glyphs.glyph_groups(project, case, [load], 6.)[0]
    assert 't = 0.3 s' in single.legend['title'] and single.peak == np.abs(single.pressures).max()


def test_time_envelope_still_applies_at_the_first_time(world):
    from anyfem.model.attributes import Envelope
    project, old, new, history, bank, load = world
    inside = replace(load, envelope=Envelope(by='time', start=FIRST_TIME - .01, stop=FIRST_TIME + .05))
    project.load_cases['wave'].wet_pressures[0] = inside
    last, _ = _last_step(project)
    scene = Scene(); _draw_wet_pressures(scene, project, project.geometry, last, None, 6.)
    assert len(scene.arrows) == PANELS
    outside = replace(load, envelope=Envelope(by='time', start=FIRST_TIME + .5, stop=FIRST_TIME + .9))
    project.load_cases['wave'].wet_pressures[0] = outside
    last, _ = _last_step(project)
    scene = Scene(); _draw_wet_pressures(scene, project, project.geometry, last, None, 6.)
    assert scene.arrows == []


def test_actual_scene_merge_keeps_base_result_legend_but_other_legends_still_replace(world):
    project, *_ = world
    result_legend = {'levels': [0, 1], 'unit': 'm', 'title': 'Displacement', 'colors': ['#000', '#fff']}
    base = Scene(legend=dict(result_legend))
    base.merge(build_attribute_overlay(project, case_name='wave'))
    assert base.legend == result_legend and len(base.arrows) == PANELS
    empty = Scene(); empty.merge(build_attribute_overlay(project, case_name='wave'))
    assert empty.legend['kind'] == glyphs.LEGEND_KIND and empty.legend['unit'] == 'Pa'
    other = Scene(legend={'title': 'x'}); other.merge(Scene(legend={'title': 'y'}))
    assert other.legend == {'title': 'y'}  # non-hydro merge behaviour unchanged


def test_glyph_count_is_bounded_and_uses_the_existing_arrow_list(world):
    project, *_ = world
    scene = arrows_of(project)
    assert len(scene.arrows) <= glyphs.SOURCE_PANEL_LIMIT
    assert all(type(a) is Arrow for a in scene.arrows)
    # One scene arrow list, no per-structural-element objects: independent of mesh refinement.
    assert len(arrows_of(project, project_and_meshes()[2]).arrows) == len(scene.arrows)
    assert {a.color for a in scene.arrows} <= {glyphs.pressure_color(x, 1.) for x in np.linspace(-1, 1, 2001)}
