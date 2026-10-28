"""Production-route checks for the wave-following mode: transport, toggle/reapply, and a gated native worker run.

Stubbed tests need no BEM.  ``test_native_*`` really solves one small field-capable frequency (Capytaine, 236
panels) in an owned worker process and is skipped unless ANYFEM_NATIVE_WAVE_FIELD=1, so the parent can schedule it.
"""
from dataclasses import asdict
import json
import os
import shutil
import numpy as np
import pytest

from test_wave_surface_application import (PERIOD, RHO, G, barge, hydrostatic_wrench, no_bem, prepared_for, settings,
    store_snapshot)
from anyfem.application import wave_surface_pressure as ws
from anyfem.application.hydro_wave_input import (ApplyWavePressure, RegularWaveInput, prepare_wave_preview,
    select_prepared_wave_window)
from anyfem.application.pressure_transfer import effective_wave_pressure
from anyfem.document import canonical_hash
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyloads.pressure_history import MappedPressureHistory
from anymesher.serialize import mesh_to_dict


def legacy_result(p, mesh, name):
    """A synthetic old-mode record (dynamic + hydrostatic companion) to exercise on/off/reapply replacement."""
    ids = tuple(sorted(mesh.quads))
    history = MappedPressureHistory((0., .5, 1.), ids, tuple(0. for _ in ids), tuple(0. for _ in ids), 1., 'c' * 64, 'legacy',
        json.dumps(dict(settings=asdict(RegularWaveInput(include_hydrostatic=True)))))
    return dict(name=name, history=history.to_dict(), surfaces=[s.id for s in p.wet_surfaces],
                input_document_hash=canonical_hash(project_to_dict(p)))


def test_prepared_result_is_plain_transportable_data(no_bem, tmp_path):
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path)
    again = json.loads(json.dumps(prepared))                       # owned-process results are plain data
    assert again['preview_kind'] == 'wave_surface_v1'
    selected = select_prepared_wave_window(again)
    assert selected['history']['content_sha256'] != again['history']['content_sha256']  # window changes identity
    assert selected['history']['source_id'] == again['history']['source_id']


def test_toggle_on_off_reapply_never_double_the_pressure(no_bem, tmp_path):
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path, name='Series')
    ApplyWavePressure(legacy_result(p, mesh, 'Series')).do(p)
    assert len(p.load_cases['Series'].wet_pressures) == 2          # old mode: dynamic + owned hydrostatic companion
    follow = dict(select_prepared_wave_window(prepared), input_document_hash=canonical_hash(project_to_dict(p)))
    ApplyWavePressure(follow, replace_legacy=True).do(p)           # follow ON replaces the owned pair
    assert len(p.load_cases['Series'].wet_pressures) == 1 and ws.is_surface_history(p.load_cases['Series'].wet_pressures[0].history)
    again = dict(follow, input_document_hash=canonical_hash(project_to_dict(p)))
    ApplyWavePressure(again, replace_legacy=True).do(p)            # reapply
    assert len(p.load_cases['Series'].wet_pressures) == 1
    back = legacy_result(p, mesh, 'Series')
    ApplyWavePressure(back, replace_legacy=True).do(p)             # follow OFF: dynamic + one companion, no surface load
    wet = p.load_cases['Series'].wet_pressures
    assert len(wet) == 2 and not any(ws.is_surface_history(w.history) for w in wet if w.history is not None)


def test_altered_companion_or_extra_load_refuses_replacement_by_follow_mode(no_bem, tmp_path):
    from dataclasses import replace
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path, name='Series')
    ApplyWavePressure(legacy_result(p, mesh, 'Series')).do(p)
    case = p.load_cases['Series']
    case.wet_pressures[1] = replace(case.wet_pressures[1], scale=2.)          # altered companion: no longer the owned pair
    follow = dict(select_prepared_wave_window(prepared), input_document_hash=canonical_hash(project_to_dict(p)))
    before = canonical_hash(project_to_dict(p))
    with pytest.raises(ValueError, match='discard|already exists'):
        ApplyWavePressure(follow, replace_legacy=True).do(p)
    assert canonical_hash(project_to_dict(p)) == before                        # rolled back, nothing published


def test_follow_series_with_an_extra_load_cannot_be_silently_replaced(no_bem, tmp_path):
    p, mesh, _ = barge()
    prepared = prepared_for(p, mesh, tmp_path, name='Series')
    ApplyWavePressure(select_prepared_wave_window(prepared)).do(p)
    p.load_cases['Series'].gravity = (0., 0., -9.81)                          # an additional (self-weight) load
    again = dict(select_prepared_wave_window(prepared), input_document_hash=canonical_hash(project_to_dict(p)))
    with pytest.raises(ValueError, match='discard'):
        ApplyWavePressure(again, replace_legacy=True).do(p)


def test_reopened_project_needs_no_cache_and_no_bem(no_bem, tmp_path, monkeypatch):
    p, mesh_a, mesh_b = barge()
    prepared = prepared_for(p, mesh_a, tmp_path)
    ApplyWavePressure(select_prepared_wave_window(prepared)).do(p)
    shutil.rmtree(tmp_path / 'field-snapshots')                    # the cache is gone: reopening must still work
    reopened = project_from_dict(json.loads(json.dumps(project_to_dict(p))))
    load = reopened.load_cases[prepared['name']].wet_pressures[0]
    made = effective_wave_pressure(reopened, mesh_b, load)         # remap from the saved retained source only
    wrench = ws.frame_loads(reopened, mesh_b, made.history, .5).wrench
    expected = hydrostatic_wrench(.2)
    assert np.allclose(wrench, expected, rtol=1e-7, atol=1e-6 * expected[2])


@pytest.mark.skipif(os.environ.get('ANYFEM_NATIVE_WAVE_FIELD') != '1', reason='native BEM field capture is scheduled by the parent')
def test_native_worker_prepare_then_warm_prepare_apply_save_reopen(tmp_path):
    from anysolver import CancellationToken
    from anyfem.job_process import run_in_worker_process
    p, mesh, _ = barge(divisions=(2, 4))
    config = settings(period=6., height=1., duration=1., dt=.5, name='Native')
    assert ws.field_requirements(config, tmp_path)['missing'] == 1      # shown BEFORE preparation

    def run():
        return run_in_worker_process(function=prepare_wave_preview, kwargs=dict(document=project_to_dict(p),
            mesh_data=mesh_to_dict(mesh), settings=config, source_bank_cache_directory=str(tmp_path)),
            snapshot=None, project_override={}, progress=lambda value: None, token=CancellationToken(), containment=True,
            on_tree=lambda process, tree: None)
    cold = run()
    assert ws.field_requirements(config, tmp_path)['missing'] == 0
    cached = sorted((tmp_path / 'field-snapshots').glob('*.npz'))
    warm = run()                                                        # second prepare must not solve again
    assert sorted((tmp_path / 'field-snapshots').glob('*.npz')) == cached
    assert warm['history']['source_id'] == cold['history']['source_id']
    from anyfem.application.hydro_wave_input import recover_prepared_wave_input
    results = {}
    for label, (a, b) in (('first', (0., .5)), ('last', (.5, 1.))):        # selected first and last windows, Apply stage
        selection = select_prepared_wave_window(warm, a, b)
        results[label] = run_in_worker_process(function=recover_prepared_wave_input, kwargs=dict(
            document=project_to_dict(p), mesh_data=mesh_to_dict(mesh), prepared=selection, start=a, end=b),
            snapshot=None, project_override={}, progress=lambda value: None, token=CancellationToken(), containment=True,
            on_tree=lambda process, tree: None)
        assert sorted((tmp_path / 'field-snapshots').glob('*.npz')) == cached      # Apply never solves
    assert results['first']['history']['times'][0] == 0. and results['last']['history']['times'][-1] == 1.
    owner = ApplyWavePressure(results['last']).do(p)
    shutil.rmtree(tmp_path / 'field-snapshots')
    reopened = project_from_dict(json.loads(json.dumps(project_to_dict(p))))
    history = reopened.load_cases[owner.name].wet_pressures[0].history
    assert history.selected_window == (.5, 1.) and history.record_times == (0., .5, 1.)
    wrench = ws.frame_loads(reopened, mesh, history, .5).wrench
    assert np.isfinite(wrench).all() and wrench[2] > 0.                  # buoyant: net upward hull force
