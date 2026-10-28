"""Wave-following pressure: application route with a stubbed field evaluator (no BEM, no Capytaine).

The stub is the deep-water unit Airy potential  phi = -(i g / w) exp(k z) exp(i k x)  whose elevation
eta = i w phi / g and pressure  pd = i w rho phi  satisfy pd0 = rho g eta exactly.  With a very long period
(k -> 0) the realised state is exactly hydrostatics at a uniformly raised/lowered level A, which gives an
independent closed form for the whole hull wrench (analytic force AND moment), partial upper plating included.
"""
from dataclasses import asdict
import json
import math
import numpy as np
import pytest

from anyfem import Project, steel
from anyfem.model import WetSurface
from anyfem.document import canonical_hash
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.application import wave_surface_pressure as ws
from anyfem.application.hydro_wave_input import (ApplyWavePressure, RegularWaveInput, prepare_wave_preview,
    select_prepared_wave_window)
from anyfem.application.hydro_wave_pressure import _source_mesh
from anyfem.application.irregular_wave_input import IrregularWaveInput
from anyfem.application.pressure_transfer import effective_wave_pressure
from anyhydro.wave_surface_fields import (FieldSnapshot, WaveFieldError, field_request, save_field_snapshot)
from anyloads.pressure_history import pressure_history_from_dict
from anymesher import Mesh
from anymesher.serialize import mesh_to_dict

RHO, G = 1025., 9.81
WET = {'bottom': ((0, -2.5, 0), (0, 5, 0), (60, 0, 0)), 'port': ((0, -2.5, 0), (60, 0, 0), (0, 0, 1.5)),
       'starboard': ((0, 2.5, 0), (0, 0, 1.5), (60, 0, 0)), 'end0': ((0, -2.5, 0), (0, 0, 1.5), (0, 5, 0)),
       'end60': ((60, -2.5, 0), (0, 5, 0), (0, 0, 1.5))}
UPPER = {'port_u': ((0, -2.5, 1.5), (60, 0, 0), (0, 0, .5)), 'starboard_u': ((0, 2.5, 1.5), (0, 0, .5), (60, 0, 0)),
         'end0_u': ((0, -2.5, 1.5), (0, 0, .5), (0, 5, 0)), 'end60_u': ((60, -2.5, 1.5), (0, 5, 0), (0, 0, .5))}
PERIOD = 1e6


def barge(upper=True, divisions=(1, 2)):
    p = Project(); p.add_material(steel()); p.add_plate_section('plate', .01, 'S355')
    rectangles = dict(WET, **(UPPER if upper else {})); faces = {}
    for name, (origin, u, v) in rectangles.items():
        o, u, v = map(np.array, (origin, u, v))
        vertices = p.geometry.add_points([o, o + u, o + u + v, o + v])
        face = p.geometry.add_face(p.geometry.add_polyline(vertices, close=True)); faces[name] = face
        p.assign_plate(face, 'plate')
        if name in WET:
            p.add_wet_surface(WetSurface(ref=p.face(face), name=name, fluid_side='away_from_point', interior_point=(30, 0, 1)))

    def mesh(count, start):
        m = Mesh(); nid = eid = start
        for name, (origin, u, v) in rectangles.items():
            o, u, v = map(np.array, (origin, u, v)); elements = []
            for i in range(count):
                points = [o + u * s + v * t for s, t in ((i / count, 0), ((i + 1) / count, 0), ((i + 1) / count, 1), (i / count, 1))]
                ids = tuple(range(nid, nid + 4)); nid += 4
                for node, point in zip(ids, points): m.nodes[node] = point.astype(float)
                m.quads[eid] = ids; elements.append(eid); eid += 1
            m.elements_of_face[faces[name]] = elements
        return m
    return (p, *(mesh(c, 10 + 1000 * j) for j, c in enumerate(divisions)))


def analytic_potential(snapshot, points):
    w = snapshot.omega; k = w * w / G
    return -(1j * G / w) * np.exp(k * points[:, 2]) * np.exp(1j * k * points[:, 0])


def store_snapshot(cache, config):
    v, f, labels = _source_mesh()
    request = field_request(v, f, labels, heading=0., water_depth=config.depth, rho=RHO, gravity=G)
    snapshot = FieldSnapshot(request, math.tau / config.period, v, f, labels, np.ones(len(f), complex), dict(backend='stub'))
    save_field_snapshot(cache, snapshot)
    return snapshot


@pytest.fixture
def no_bem(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('BEM / Capytaine solve is not allowed here')
    monkeypatch.setattr('anyhydro.wave_surface_fields.solve_field_snapshot', forbidden)
    monkeypatch.setattr('anyhydro.wave_surface_fields._capytaine_total_potential', analytic_potential)


def settings(**changes):
    values = dict(follow_surface=True, height=.4, period=PERIOD, duration=1., dt=.5, name='Follow')
    values.update(changes)
    return asdict(RegularWaveInput(**values))


def prepared_for(p, mesh, tmp_path, **changes):
    config = settings(**changes)
    store_snapshot(tmp_path, RegularWaveInput(**config))
    return prepare_wave_preview(document=project_to_dict(p), mesh_data=mesh_to_dict(mesh), settings=config,
                                source_bank_cache_directory=tmp_path)


def hydrostatic_wrench(level):
    fz = RHO * G * (level + 1.5) * 300.
    return np.array([0., 0., fz, 0., -30. * fz, 0.])


# ------------------------------------------------------------------------------ settings and compatibility
def test_follow_flag_is_strict_bool_false_default_and_locks_hydrostatic():
    for cls in (RegularWaveInput, IrregularWaveInput):
        assert cls().follow_surface is False and cls().include_hydrostatic is False
        assert cls(follow_surface=True).include_hydrostatic is True
        assert asdict(cls(follow_surface=True))['follow_surface'] is True
        for bad in (1, 'yes', None):
            with pytest.raises(ValueError): cls(follow_surface=bad)
    old = {k: v for k, v in asdict(RegularWaveInput(include_hydrostatic=True)).items() if k != 'follow_surface'}
    assert RegularWaveInput(**old).follow_surface is False


def test_old_saved_settings_without_flag_are_the_same_source_as_false():
    from anyfem.application.wave_force_input import _same_inputs
    saved = {k: v for k, v in asdict(RegularWaveInput()).items() if k != 'follow_surface'}
    assert _same_inputs(asdict(RegularWaveInput()), saved)
    assert not _same_inputs(asdict(RegularWaveInput(follow_surface=True)), saved)


# ------------------------------------------------------------------------------------------ target manifest
def test_target_adds_existing_upper_plating_and_refuses_when_absent():
    p, mesh, _ = barge()
    target = ws.build_target(p, mesh)
    assert set(target.labels) == set(ws.WET_LABELS) | set(ws.UPPER_LABELS)
    assert len(set(target.element_ids)) == len(target.element_ids) == len(mesh.quads)
    assert target.points[:, 2].min() == -1.5 and target.points[:, 2].max() == .5
    bottom_points = {i for corners, label in zip(target.element_corners, target.labels) if label == 'bottom' for i in corners}
    assert bottom_points and not target.waterline[sorted(bottom_points)].any()      # no eta inside the waterplane footprint
    assert target.waterline.sum() == len(target.waterline) - len(bottom_points)
    q, qmesh, _ = barge(upper=False)
    with pytest.raises(ws.SurfaceTargetError, match='upper'): ws.build_target(q, qmesh)


# ----------------------------------------------------------------------- analytic wrench through full route
@pytest.mark.parametrize('phase,level', [(0., .2), (180., -.2)])
def test_crest_and_trough_hull_wrench_matches_hydrostatics_at_raised_or_lowered_level(no_bem, tmp_path, phase, level):
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path, phase=phase)
    assert prepared['preview_kind'] == 'wave_surface_v1' and prepared['field_requirements_before']['missing'] == 0
    assert prepared['envelope']['overtopping'] is False
    expected = hydrostatic_wrench(level)
    values = np.array(prepared['preview_resultants'])
    assert values.shape == (3, 6)
    assert np.allclose(values, expected, rtol=1e-7, atol=1e-6 * expected[2])


def test_true_zero_amplitude_input_is_exact_still_water_without_any_field_solution(no_bem, tmp_path):
    p, mesh, _ = barge()
    config = settings(height=0.)                                    # no snapshot is stored: none may be required
    prepared = prepare_wave_preview(document=project_to_dict(p), mesh_data=mesh_to_dict(mesh), settings=config,
                                    source_bank_cache_directory=tmp_path)
    assert prepared['field_requirements_before'] == dict(total=0, missing=0, missing_omegas=[])
    values = np.array(prepared['preview_resultants'])
    assert np.allclose(values, hydrostatic_wrench(0.), rtol=1e-9, atol=1e-8 * hydrostatic_wrench(0.)[2])
    assert np.all(pressure_history_from_dict(prepared['history']).elevation_series() == 0.)


def test_partial_upper_plating_reports_wet_fraction_and_full_area_average_pressure(no_bem, tmp_path):
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path)
    history = pressure_history_from_dict(prepared['history'])
    records = ws.inspection_pressures(p, mesh, history, 0.)
    target = ws.SurfaceJob(p, mesh).target
    upper = [e for e, l in zip(target.element_ids, target.labels) if l.endswith('_upper')]
    assert all(records[e][4] == 1 and records[e][3] == pytest.approx(.4, abs=1e-9) for e in upper)
    # linear pressure rho g (A - z) over the wet height A=.2 of a .5 m plate: full-area mean = rho g A^2 / (2 * .5)
    assert all(abs(records[e][0]) == pytest.approx(RHO * G * .04, rel=1e-9) for e in upper)
    assert all(records[e][4] == 2 for e, l in zip(target.element_ids, target.labels) if l in ws.WET_LABELS)


def test_deep_trough_keel_emergence_is_refused(no_bem, tmp_path):
    p, mesh, _ = barge()
    config = settings(height=6., phase=180.)
    store_snapshot(tmp_path, RegularWaveInput(**config))
    with pytest.raises(ValueError, match='keel emergence'):
        prepare_wave_preview(document=project_to_dict(p), mesh_data=mesh_to_dict(mesh), settings=config,
                             source_bank_cache_directory=tmp_path)


def test_overtopping_is_reported_and_deck_water_is_not_loaded(no_bem, tmp_path):
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path, height=1.6)          # A = .8 m > deck height .5 m above the waterline
    assert prepared['envelope']['overtopping_deck_water_excluded'] is True
    assert 'deck' in prepared['excluded_loads']
    fz = np.array(prepared['preview_resultants'])[0, 2]
    assert fz == pytest.approx(RHO * G * (.8 + 1.5) * 300., rel=1e-7)   # bottom sees the full head; no invented plating


# ------------------------------------------------------------------------ no BEM: warm cache, corrupt, missing
def test_missing_or_corrupt_field_cache_refuses_without_solving_and_reports_count(no_bem, tmp_path):
    p, mesh, _ = barge()
    config = settings()
    assert ws.field_requirements(config, tmp_path) == dict(total=1, missing=1, missing_omegas=[math.tau / PERIOD])
    with pytest.raises(WaveFieldError, match='missing'):          # Apply/reopen style call: solving is not permitted
        ws.prepare_surface_preview(document=project_to_dict(p), mesh_data=mesh_to_dict(mesh), settings=config,
                                   source_bank_cache_directory=tmp_path, allow_solve=False)


def test_corrupt_snapshot_is_not_silently_repaired(no_bem, tmp_path):
    config = settings()
    store_snapshot(tmp_path, RegularWaveInput(**config))
    assert ws.field_requirements(config, tmp_path)['missing'] == 0
    for path in (tmp_path / 'field-snapshots').glob('*.npz'): path.write_bytes(b'not an archive')
    with pytest.raises(WaveFieldError, match='corrupt'):           # corrupt is reported, never counted as missing
        ws.field_requirements(config, tmp_path)
    p, mesh, _ = barge()
    with pytest.raises(WaveFieldError, match='corrupt'):           # and never overwritten by a silent re-solve
        prepare_wave_preview(document=project_to_dict(p), mesh_data=mesh_to_dict(mesh), settings=config,
                             source_bank_cache_directory=tmp_path)


# --------------------------------------------------------------------- composed history, apply, assemble
def test_apply_creates_one_composed_pressure_without_companion_and_survives_undo_save_reopen(no_bem, tmp_path):
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path, duration=1., dt=.5)
    selected = select_prepared_wave_window(prepared, .5, 1.)
    command = ApplyWavePressure(selected)
    owner = command.do(p)
    assert owner.times == (.5, 1.) and len(owner.wet_pressures) == 1          # no hydrostatic companion: no double pressure
    history = owner.wet_pressures[0].history
    assert history.record_times == (0., .5, 1.) and history.selected_window == (.5, 1.)
    command.undo(p); assert not p.load_cases
    command.redo(p); assert p.load_cases[prepared['name']].wet_pressures[0].history is history
    reopened = project_from_dict(json.loads(json.dumps(project_to_dict(p))))
    again = reopened.load_cases[prepared['name']].wet_pressures[0].history
    assert again.content_sha256 == history.content_sha256
    assert np.allclose(again.frame(1.).eta, history.frame(1.).eta, rtol=0, atol=0)


def test_build_assembles_nodal_vectors_equal_to_frame_loads_and_no_element_pressure(no_bem, tmp_path):
    from anysolver.boundary import LoadCase
    from anyfem.solve import build
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path)
    owner = ApplyWavePressure(select_prepared_wave_window(prepared)).do(p)
    case = p.series_children(owner.name)[0]
    target = build._build_load_case(p, mesh, p.resolve_case(case))        # production assembly of the resolved case
    assert target.pressure_loads == {}
    total = sum(v[:3] for v in target.nodal_loads.values())
    assert total[2] == pytest.approx(hydrostatic_wrench(.2)[2], rel=1e-7)
    assert np.allclose(total[:2], 0., atol=1e-6 * total[2])


def test_remap_to_a_different_mesh_recomputes_from_retained_sources_not_old_elements(no_bem, tmp_path):
    p, mesh_a, mesh_b = barge()
    prepared = prepared_for(p, mesh_a, tmp_path)
    owner = ApplyWavePressure(select_prepared_wave_window(prepared)).do(p)
    load = owner.wet_pressures[0]
    made = effective_wave_pressure(p, mesh_b, load)
    assert made.history.mesh_content_sha256 == canonical_hash(mesh_to_dict(mesh_b))
    assert made.history.mesh_content_sha256 != load.history.mesh_content_sha256
    assert made.history.target_sha256 != load.history.target_sha256 and made.history.source_id == load.history.source_id
    assert made.history.record_times == load.history.record_times        # absolute record retained
    wrench = ws.frame_loads(p, mesh_b, made.history, 0.).wrench
    assert np.allclose(wrench, hydrostatic_wrench(.2), rtol=1e-7, atol=1e-6 * hydrostatic_wrench(.2)[2])
    assert effective_wave_pressure(p, mesh_a, load).history is load.history


def test_preparation_cancellation_publishes_nothing(no_bem, tmp_path):
    class Token:
        calls = 0
        def raise_if_cancelled(self, stage):
            Token.calls += 1
            if Token.calls >= 2: raise RuntimeError('cancelled')
    p, mesh, _ = barge()
    config = settings(); store_snapshot(tmp_path, RegularWaveInput(**config))
    before = canonical_hash(project_to_dict(p))
    with pytest.raises(RuntimeError, match='cancelled'):
        prepare_wave_preview(document=project_to_dict(p), mesh_data=mesh_to_dict(mesh), settings=config,
                             source_bank_cache_directory=tmp_path, cancellation_token=Token())
    assert canonical_hash(project_to_dict(p)) == before and not p.load_cases


def test_stale_mesh_or_project_is_refused_before_apply(no_bem, tmp_path):
    from anyfem.application.wave_surface_pressure import recover_surface_selection
    p, mesh_a, mesh_b = barge()
    prepared = prepared_for(p, mesh_a, tmp_path)
    with pytest.raises(ValueError, match='changed'):
        recover_surface_selection(document=project_to_dict(p), mesh_data=mesh_to_dict(mesh_b), prepared=prepared)
    tampered = dict(prepared, preview_resultants=[[0.] * 6] * 3)
    with pytest.raises(ValueError, match='changed'):
        select_prepared_wave_window(tampered)


def test_two_stage_select_then_recover_keeps_a_valid_preview_hash_and_refuses_a_changed_document(no_bem, tmp_path):
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path)
    selected = select_prepared_wave_window(prepared, .5, 1.)
    assert selected['preview_content_hash'] == prepared['preview_content_hash']
    recovered = ws.recover_surface_selection(document=project_to_dict(p), mesh_data=mesh_to_dict(mesh), prepared=selected,
                                             start=.5, end=1.)
    assert pressure_history_from_dict(recovered['history']).times == (.5, 1.)
    first = select_prepared_wave_window(prepared, 0., .5)                  # first and last windows both recover
    last = select_prepared_wave_window(prepared, .5, 1.)
    assert pressure_history_from_dict(first['history']).times[0] == 0. and pressure_history_from_dict(last['history']).times[-1] == 1.
    q = project_to_dict(p); q['name'] = 'edited'
    with pytest.raises(ValueError, match='changed'):
        ws.recover_surface_selection(document=q, mesh_data=mesh_to_dict(mesh), prepared=selected)


def test_full_and_partial_selections_are_bound_to_the_original_full_record(no_bem, tmp_path):
    from anyloads.wave_surface_pressure import WaveSurfacePressureHistory
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path)
    full = select_prepared_wave_window(prepared, 0., 1.)                      # explicit full-record selection validates
    assert ws.validate_surface_preview(full).record_identity() == prepared['record_history_sha256']
    partial = select_prepared_wave_window(prepared, .5, 1.)
    data = dict(partial['history'])
    for key in ('type', 'algorithm', 'content_sha256'): data.pop(key)
    data['pd_real'] = [[v + 1. for v in row] for row in data['pd_real']]      # altered field, self-consistent own hash
    forged = WaveSurfacePressureHistory(**data)
    assert forged.source_id == prepared['source_id'] and forged.record_times == tuple(prepared['preview_times'])
    with pytest.raises(ValueError, match='changed'):
        ws.validate_surface_preview(dict(partial, history=forged.to_dict()))
    with pytest.raises(ValueError, match='changed'):                          # envelope binds document/mesh/settings too
        ws.validate_surface_preview(dict(partial, input_settings_hash='sha256:' + '0' * 64))


def test_load_free_context_forwards_captured_hashes_and_refuses_inconsistent_ones(no_bem, tmp_path):
    p, mesh, _ = barge()
    config = settings(); store_snapshot(tmp_path, RegularWaveInput(**config))
    full = canonical_hash(project_to_dict(p))
    context = {**project_to_dict(p), 'load_cases': [], 'combinations': [], 'analyses': []}
    mesh_hash = canonical_hash(mesh_to_dict(mesh))
    prepared = prepare_wave_preview(document=context, mesh_data=mesh_to_dict(mesh), settings=config,
        source_bank_cache_directory=tmp_path, document_content_hash=full, mesh_content_hash=mesh_hash)
    assert prepared['input_document_hash'] == full                            # the captured FULL document, not the context
    with pytest.raises(ValueError, match='mesh'):
        prepare_wave_preview(document=context, mesh_data=mesh_to_dict(mesh), settings=config,
            source_bank_cache_directory=tmp_path, document_content_hash=full, mesh_content_hash='sha256:' + '1' * 64)
    with pytest.raises(ValueError, match='load-free|invalid'):
        prepare_wave_preview(document=project_to_dict(p) | {'load_cases': [{'x': 1}]}, mesh_data=mesh_to_dict(mesh),
            settings=config, source_bank_cache_directory=tmp_path, document_content_hash=full)


def test_surface_selection_contract_and_exact_type_guard(no_bem, tmp_path):
    from dataclasses import replace
    from anyfem.application.pressure_transfer import PressureRemapError
    from anyfem.model import WetPressure
    from anyloads.wave_surface_pressure import WaveSurfacePressureHistory
    p, mesh_a, mesh_b = barge()
    prepared = prepared_for(p, mesh_a, tmp_path)
    owner = ApplyWavePressure(select_prepared_wave_window(prepared)).do(p)
    load = owner.wet_pressures[0]
    ids = tuple(s.id for s in p.wet_surfaces)
    assert effective_wave_pressure(p, mesh_a, load).history is load.history             # all five: accepted
    assert effective_wave_pressure(p, mesh_a, replace(load, surfaces=())).history is load.history   # empty = all
    before = canonical_hash(project_to_dict(p))
    for bad in (ids[:2], ids + ('foreign-surface',), ('foreign-surface',)):
        with pytest.raises(PressureRemapError, match='selection|five'):
            effective_wave_pressure(p, mesh_b, replace(load, surfaces=bad))
    assert canonical_hash(project_to_dict(p)) == before and owner.wet_pressures[0] is load

    class Forged(WaveSurfacePressureHistory):
        pass
    data = load.history.to_dict()
    for key in ('type', 'algorithm', 'content_sha256'): data.pop(key)
    with pytest.raises(ValueError, match='subclass'):
        WetPressure(history=Forged(**data), mapping_key='element', surfaces=ids)


def test_job_reuse_is_refused_after_mesh_or_project_mutation(no_bem, tmp_path):
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path)
    history = pressure_history_from_dict(prepared['history'])
    job = ws.SurfaceJob(p, mesh)
    ws.frame_loads(p, mesh, history, 0., job)                              # populates kernels
    node = next(iter(mesh.nodes)); mesh.nodes[node] = mesh.nodes[node] + np.array([0., 0., .01])
    with pytest.raises(ws.SurfaceTargetError, match='changed'):
        ws.frame_loads(p, mesh, history, 0., job)
    mesh.nodes[node] = mesh.nodes[node] - np.array([0., 0., .01])
    ws.frame_loads(p, mesh, history, 0., job)                              # restored mesh is accepted again
    job.verify(full=True)
    p.geometry.add_point(0., 0., 9.)                                       # geometry revision moves
    with pytest.raises(ws.SurfaceTargetError, match='geometry changed'):
        ws.frame_loads(p, mesh, history, 0., job)


def test_inspection_reports_the_same_physical_numbers_as_assembly(no_bem, tmp_path):
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path)
    history = pressure_history_from_dict(prepared['history'])
    node = sorted(mesh.nodes)[2]
    loads = ws.frame_loads(p, mesh, history, 0.)
    records = ws.inspection_pressures(p, mesh, history, 0.)
    target = ws.SurfaceJob(p, mesh).target
    for i, eid in enumerate(target.element_ids):
        assert records[eid][3] == pytest.approx(loads.wet_fraction[i], abs=0) and records[eid][4] == int(loads.state[i])
        assert abs(records[eid][0]) == pytest.approx(abs(loads.mean_pressure[i]), abs=0)


def store_omegas(cache, config, omegas):
    v, f, labels = _source_mesh()
    request = field_request(v, f, labels, heading=math.radians(config.heading) % math.tau, water_depth=config.depth,
                            rho=RHO, gravity=G)
    for w in omegas:
        save_field_snapshot(cache, FieldSnapshot(request, w, v, f, labels, np.ones(len(f), complex), dict(backend='stub')))


def test_irregular_follow_sums_components_with_saved_phases_before_clipping(no_bem, tmp_path):
    from anyfem.application.irregular_wave_input import IrregularWaveInput
    config = IrregularWaveInput(follow_surface=True, significant_height=1., component_count=3, seed=5, duration=1., dt=.5,
                                name='Irr')
    realization = config.realization()
    store_omegas(tmp_path, config, [c.omega for c in realization.components])
    p, mesh, _ = barge()
    prepared = prepare_wave_preview(document=project_to_dict(p), mesh_data=mesh_to_dict(mesh), settings=asdict(config),
                                    source_bank_cache_directory=tmp_path)
    history = pressure_history_from_dict(prepared['history'])
    assert history.omegas == tuple(c.omega for c in realization.components) and history.heading == 0.
    target = ws.SurfaceJob(p, mesh).target
    port = sorted({i for c, l in zip(target.element_corners, target.labels) if l == 'port' for i in c})
    x = target.points[port, 0]
    for t in (0., .5, 1.):
        expected = sum(c.amplitude * np.cos((c.omega ** 2 / G) * x - c.omega * t + c.phase) for c in realization.components)
        assert np.allclose(history.frame(t).eta[port], expected, rtol=1e-9, atol=1e-9)    # one coherent sum, saved phases
    assert prepared['preview_kind'] == 'wave_surface_v1' and np.isfinite(prepared['preview_resultants']).all()
    assert np.array(prepared['preview_resultants'])[:, 2].min() > 0.                         # buoyant at every sample


def test_true_zero_significant_height_is_still_water_hydrostatics(no_bem, tmp_path):
    from anyfem.application.irregular_wave_input import IrregularWaveInput
    config = IrregularWaveInput(follow_surface=True, significant_height=0., component_count=2, duration=1., dt=.5, name='Irr0')
    p, mesh, _ = barge()
    prepared = prepare_wave_preview(document=project_to_dict(p), mesh_data=mesh_to_dict(mesh), settings=asdict(config),
                                    source_bank_cache_directory=tmp_path)             # no field solutions exist or are needed
    assert prepared['field_requirements_before']['total'] == 0
    assert np.allclose(np.array(prepared['preview_resultants']), hydrostatic_wrench(0.), rtol=1e-9,
                       atol=1e-8 * hydrostatic_wrench(0.)[2])


def test_assembled_vectors_are_six_dof_and_wrench_includes_direct_nodal_moments(no_bem, tmp_path):
    """Quad fixture only; the current-default S3 (V2D) mechanics are covered in ANYsolver's test_wave_surface_regions."""
    from anyfem.solve import build
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path)
    owner = ApplyWavePressure(select_prepared_wave_window(prepared)).do(p)
    case = build._build_load_case(p, mesh, p.resolve_case(p.series_children(owner.name)[0]))
    assert all(len(v) == 6 for v in case.nodal_loads.values())
    # Direct nodal moments are retained in the wrench the preview reports (same route): compare a recomputed wrench.
    frame = ws.frame_loads(p, mesh, pressure_history_from_dict(prepared['history']), 0.)
    direct = sum(v[3:] for v in frame.nodal.values())
    origin = ws.ORIGIN_PROJECT
    expected = sum(np.cross(np.asarray(mesh.nodes[n], float) - origin, v[:3]) + v[3:] for n, v in frame.nodal.items())
    assert np.allclose(frame.wrench[3:], expected) and np.all(np.isfinite(direct))


def test_frame_evaluation_kernel_and_assembly_timings_are_reportable(no_bem, tmp_path):
    import time
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path)
    history = pressure_history_from_dict(prepared['history'])
    job = ws.SurfaceJob(p, mesh)
    t0 = time.perf_counter(); job.elements(); kernel_s = time.perf_counter() - t0
    t0 = time.perf_counter(); history.frame(0.); frame_s = time.perf_counter() - t0
    t0 = time.perf_counter(); [ws.frame_loads(p, mesh, history, t, job) for t in history.times]; loads_s = time.perf_counter() - t0
    report = dict(kernel_build_s=kernel_s, frame_eval_s=frame_s, loads_per_sample_s=loads_s / len(history.times),
                  samples=len(history.times), elements=len(history.element_ids), points=len(history.corner_z),
                  frequencies=len(history.omegas), payload_json_bytes=len(json.dumps(history.to_dict())))
    (tmp_path / 'wave-surface-timings.json').write_text(json.dumps(report))
    assert all(np.isfinite(list(report.values())))


def test_old_modes_are_untouched_by_the_new_branch():
    from anyfem.application.wave_hydrostatic import history_includes_hydrostatic
    from anyloads.pressure_history import MappedPressureHistory
    h = MappedPressureHistory((0., 1.), (1,), (1.,), (0.,), 1., 'a' * 64, 'src', json.dumps(dict(settings=asdict(RegularWaveInput(include_hydrostatic=True)))))
    assert history_includes_hydrostatic(h) is True
    assert ws.is_surface_history(h) is False


def test_surface_envelope_evaluates_in_bounded_chunks():
    from types import SimpleNamespace
    calls = []
    class History:
        record_times = tuple(range(151))
        def elevation_series(self, times):
            calls.append(len(times))
            return np.array([[t / 1000.] for t in times])
    report = ws.envelope_report(History(), SimpleNamespace(waterline=[0]))
    assert calls == [64, 64, 23]
    assert report['min_eta_m'] == 0. and report['max_eta_m'] == .15
