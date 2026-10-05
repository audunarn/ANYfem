"""Cheap contract guards only: never call mesh fixtures, FE build or solver."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = Path(__file__).resolve().parents[1] / 'tools/prepared_candidate_integration.py'
spec = importlib.util.spec_from_file_location('candidate_harness', PATH)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def test_cli_proposal_never_dispatches_engine_or_runtime(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError('engineering/runtime dispatch is forbidden here')
    monkeypatch.setattr(harness, 'compose_case', forbidden)
    monkeypatch.setattr(harness, 'inspect_runtime', forbidden)
    harness.main(['proposal'])
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'prepared; execution pending proposal review'
    assert not report['mesh_executed'] and not report['solver_executed']


def test_cli_has_no_engine_execution_action():
    with pytest.raises(SystemExit):
        harness.main(['run'])


def test_wrong_artifact_refuses_before_any_package_import(tmp_path, monkeypatch):
    monkeypatch.setattr(harness.importlib, 'import_module', lambda name: pytest.fail('unexpected package import'))
    (tmp_path / 'anygeometry-0.4.5-py3-none-any.whl').write_bytes(b'wrong wheel')
    with pytest.raises(ValueError, match='wrong exact artifact'):
        harness.inspect_runtime(tmp_path)


@pytest.mark.parametrize('cells', [(), (1, 2, 3, 4, 5, 6, 7)])
def test_child_intent_refuses_missing_or_widened_source_scope(cells):
    pressure = SimpleNamespace(id='pressure-id', value=300, ref=SimpleNamespace(kind='face', id=3))
    binding = SimpleNamespace(key='load_cases/case-id/pressures/pressure-id', cell_ids=cells)
    source = SimpleNamespace(references=(binding,), mesh=SimpleNamespace(
        elements_of_face={3: tuple(range(1, 7))}, tris=dict.fromkeys(range(26))))
    with pytest.raises(ValueError, match='SOURCE face cell scope'):
        harness.assert_pressure_binding('child', pressure, source, case_id='case-id')


def test_duplicate_record_identity_refuses():
    pressure = SimpleNamespace(id='pressure-id', value=300, ref=SimpleNamespace(kind='face', id=3))
    binding = SimpleNamespace(key='load_cases/case-id/pressures/pressure-id', cell_ids=tuple(range(6)))
    with pytest.raises(ValueError, match='ambiguous'):
        harness.assert_pressure_binding('child', pressure, SimpleNamespace(references=(binding, binding)), case_id='case-id')


def test_current_owner_clone_keeps_complete_project_inputs_without_preparation():
    # Geometry construction and extraction only: never call build_fixture,
    # plan/apply intersections, triangulation, FE build or solver.
    harness.inspect_runtime()
    from prepared_pair_owner_fixture import _default_source_factory
    from anyfem import Project
    from anyfem.prepared_reference_scope import _project_inputs, extract_prepared_project_closure
    from anyfem.prepared_mesh_preassembly import _copy_project_for_preassembly
    geometry, roots = _default_source_factory()
    project = Project(geometry=geometry)
    before = _project_inputs(project)
    handles = tuple(geometry.handle('face', key) for key in roots) + tuple(
        geometry.handle('sheet', key) for key in geometry.sheets)
    closure = extract_prepared_project_closure(project, handles)
    assert _project_inputs(project) == before
    assert closure.working_model is not geometry
    assert len(closure.working_model.face_uses) == 2
    assert len(closure.working_model.sheets) == 2
    copied = _copy_project_for_preassembly(project)
    assert copied is not project and copied.geometry is not geometry
    assert _project_inputs(copied) == before


def test_wrong_proposed_pressure_refuses_before_reading_any_mesh():
    pressure = SimpleNamespace(id='pressure-id', value=1200, ref=SimpleNamespace(kind='face', id=3))
    with pytest.raises(ValueError, match='differs from the proposal'):
        harness.assert_pressure_binding('child', pressure, object(), case_id='case-id')


def test_engine_entry_point_refuses_before_runtime_or_fixture(monkeypatch):
    monkeypatch.setattr(harness, 'inspect_runtime', lambda: pytest.fail('runtime reached'))
    with pytest.raises(RuntimeError, match='dispatch disabled'):
        harness.compose_case('root')


@pytest.mark.parametrize('key,cells', [
    ('load_cases/foreign-case/pressures/pressure-id', tuple(range(6))),
    ('load_cases/case-id/pressures/pressure-id', (*range(6), 5)),
])
def test_foreign_case_or_duplicate_cell_refuses(key, cells):
    pressure = SimpleNamespace(id='pressure-id', value=300, ref=SimpleNamespace(kind='face', id=3))
    binding = SimpleNamespace(key=key, cell_ids=cells)
    source = SimpleNamespace(references=(binding,), mesh=SimpleNamespace(
        elements_of_face={3: tuple(range(6))}, tris=dict.fromkeys(range(26))))
    with pytest.raises(ValueError):
        harness.assert_pressure_binding('child', pressure, source, case_id='case-id')


def test_required_new_api_argument_is_not_silent_compatibility():
    def changed_api(first, *, new_required):
        pass
    with pytest.raises(TypeError):
        harness.check_call_shape(changed_api, 1, {})
