"""L01 example: stored pressure source chosen by paths/environment only; synthetic fixtures, no licensed data."""
import importlib.util
import json
from pathlib import Path
import numpy as np
import pytest
from test_orcawave_pressure import fixture, job_fixture
from test_pressure_source_selector import _family_cache
from anyfem.application import orcawave_pressure as app

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples' / 'free_floating_l01.py'


def example():
    spec = importlib.util.spec_from_file_location('free_floating_l01_under_test', EXAMPLE)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def settings_for(mapping_settings):
    keep = ('limits', 'force_relative_limit', 'moment_relative_limit', 'force_absolute_limit', 'moment_absolute_limit')
    out = {k: mapping_settings[k] for k in keep}
    out.update(body_id=0, heading=0., composition='fixed_diffraction', omega=1., height=2.)
    return out


def test_anyhydro_family_source_maps_below_existing_gates_and_matches_orcawave_route(tmp_path):
    spec, _, _, _ = _family_cache(tmp_path)
    project, mesh, kw = job_fixture()
    mapping = settings_for(kw['settings'])
    data = app.load_pressure_source('anyhydro', spec)
    report = example().pressure_mapping_check(project, mesh, data, mapping)
    evidence = report['frequency_evidence'][0]
    assert evidence['force']['absolute'] <= report['limits']['force_absolute'] + report['limits']['force_relative'] * evidence['force']['reference_norm']
    assert evidence['moment']['absolute'] <= report['limits']['moment_absolute'] + report['limits']['moment_relative'] * evidence['moment']['reference_norm']
    # the OrcaWave stored-result route (portable document) maps to the same transfer errors
    portable = tmp_path / 'portable.json'
    from anyfileio.orcawave import OrcaWavePressureDocument
    OrcaWavePressureDocument.from_dict(kw['source']).save(portable)
    other = example().pressure_mapping_check(project, mesh, app.load_pressure_source('orcawave', portable), mapping)
    assert other['frequency_evidence'][0]['force']['absolute'] == pytest.approx(evidence['force']['absolute'], abs=1e-9)
    assert json.dumps(report)           # evidence is plain data


def test_gate_failure_is_not_widened(tmp_path):
    spec, _, _, _ = _family_cache(tmp_path)
    project, mesh, kw = job_fixture()
    mapping = settings_for(kw['settings'])
    mapping['limits'] = dict(mapping['limits'], gap_m=1e-12)
    mapping['source_to_project'] = np.array([[1, 0, 0, 0], [0, 1, 0, 0.5], [0, 0, 1, 0.3], [0, 0, 0, 1]]).tolist()
    with pytest.raises(Exception):
        example().pressure_mapping_check(project, mesh, app.load_pressure_source('anyhydro', spec), mapping)


def test_kind_mismatch_and_licence_refusals(tmp_path, monkeypatch):
    spec, _, _, _ = _family_cache(tmp_path)
    with pytest.raises(ValueError, match='anyhydro pressure source but orcawave was selected'):
        app.load_pressure_source('orcawave', spec)
    project, mesh, kw = job_fixture()
    portable = tmp_path / 'portable.json'
    from anyfileio.orcawave import OrcaWavePressureDocument
    OrcaWavePressureDocument.from_dict(kw['source']).save(portable)
    with pytest.raises(ValueError, match='orcawave pressure source but anyhydro was selected'):
        app.load_pressure_source('anyhydro', portable)
    owr = tmp_path / 'r.owr'; owr.write_bytes(b'x')
    with pytest.raises(ValueError, match='not an ANYhydro source'):
        app.load_pressure_source('anyhydro', owr)
    monkeypatch.setattr(app, 'orcfxapi_installed', lambda: False)
    with pytest.raises(ValueError, match='OrcFxAPI.*licence'):
        app.load_pressure_source('orcawave', owr)
    with pytest.raises(ValueError, match='must be one of'):
        app.load_pressure_source('wadam', spec)


def test_record_holds_identity_only(tmp_path):
    spec, _, _, _ = _family_cache(tmp_path)
    data = app.load_pressure_source('anyhydro', spec)
    record = example().pressure_source_record('anyhydro', spec, data)
    assert record['sha256'] == app.file_sha256(spec) and record['file_name'] == 'spec.json'
    assert set(record) == {'kind', 'file_name', 'sha256', 'note', 'bank_id', 'composition', 'panels', 'omegas'}
    assert 'responses' not in json.dumps(record)


def test_paths_and_environment_select_the_source(tmp_path, monkeypatch):
    module = example()
    seen = {}
    monkeypatch.setattr('anyfem.application.panel_hull_example.build_structured_hull',
                        lambda gdf, mesh_size: (type('P', (), {'generate_mesh': lambda self, size: None})(), {}))
    monkeypatch.setattr(module, 'publish_model', lambda *a, **k: seen.update(k) or {})
    base = ['--gdf', 'g.gdf', '--reference', 'r.json', '--output', str(tmp_path / 'out')]
    module.main(base); assert seen['pressure'] is None
    settings = tmp_path / 's.json'; settings.write_text(json.dumps({'a': 1}))
    monkeypatch.setenv('ANYFEM_L01_PRESSURE_SOURCE', 'orcawave')
    monkeypatch.setenv('ANYFEM_L01_PRESSURE_FILE', str(tmp_path / 'x.owr'))
    monkeypatch.setenv('ANYFEM_L01_PRESSURE_SETTINGS', str(settings))
    module.main(base)
    assert seen['pressure'] == dict(kind='orcawave', path=str(tmp_path / 'x.owr'), settings={'a': 1})
    module.main(base + ['--pressure-source', 'anyhydro', '--pressure-file', 'f.json'])
    assert seen['pressure']['kind'] == 'anyhydro' and seen['pressure']['path'] == 'f.json'



@pytest.mark.parametrize('argv,env', [
    (['--pressure-source', 'orcawave'], {}),
    (['--pressure-settings', 's.json'], {}),
    ([], {'ANYFEM_L01_PRESSURE_SOURCE': 'anyhydro'}),
    ([], {'ANYFEM_L01_PRESSURE_SETTINGS': 's.json'}),
])
def test_incomplete_pressure_request_is_refused_before_meshing(tmp_path, monkeypatch, argv, env):
    module = example()
    built = []
    monkeypatch.setattr('anyfem.application.panel_hull_example.build_structured_hull',
                        lambda *a, **k: built.append(1))
    for key in ('ANYFEM_L01_PRESSURE_SOURCE', 'ANYFEM_L01_PRESSURE_FILE', 'ANYFEM_L01_PRESSURE_SETTINGS'):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit):
        module.main(['--gdf', 'g.gdf', '--reference', 'r.json', '--output', str(tmp_path / 'out')] + argv)
    assert not built and not (tmp_path / 'out').exists()
