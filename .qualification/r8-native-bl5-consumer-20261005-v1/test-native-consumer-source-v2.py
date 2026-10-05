"""Existing consumer route for explicitly selected elastic native BL5 inputs."""
from dataclasses import replace
import copy
import runpy
from pathlib import Path
import numpy as np
import pytest
from anysolver import stiffened_shell as ss
from anysolver.control import CancellationToken, SolveCancelled
from anyfem.solve.stiffened_shell import ExperimentalGEBL5Panel, SCHEMA_BL5_NATIVE_ELASTIC_V5, save_case, load_case
from anyfem.solve.stiffened_shell_project import bl5_panel_from_analysis, solve_bl5_panel
from anyfem.model.records import AnalysisDefinition
from anyfem.model.project import Project
from anyfem.io.project_file import save_project, load_project

EXAMPLE = runpy.run_path(str(Path(__file__).resolve().parents[1]/'examples/stiffened_shell_native_fibre_bl5_consumer.py'))
panel = EXAMPLE['panel']


@pytest.mark.parametrize('shell,family,beams,basis',[
    ('q4','biaxial','mixed','p0'), ('s3','axial','fibre','p0'),
    ('mixed','biaxial','mixed','continuous_p1_coarse')])
def test_real_consumer_persistence_finite_recovery_restart(tmp_path, shell, family, beams, basis):
    result = EXAMPLE['workflow'](tmp_path, shell, family, beams, basis)
    assert result['status'] == 'completed' and not result['production_qualified']
    assert result['schema'] == SCHEMA_BL5_NATIVE_ELASTIC_V5
    assert result['free_equilibrium_ratio'] <= 1e-8 and result['constraint_max'] <= 1e-9
    assert result['fibre_stations'] and result['line_work_records']
    assert result['input_bytes_equal'] and result['fresh_restore_bytes_equal'] and result['continued_bytes_equal']


def test_strict_v5_mutation_topology_and_project_ownership(tmp_path):
    original = panel(shell='mixed'); raw = original.to_dict()
    for mutate in (
        lambda p: p.update(schema='anyfem.experimental-ge-bl5-panel-material-v3'),
        lambda p: p.update(bl5_material_policy=ss.BL5_GENERALIZED_MATERIAL_V1),
        lambda p: p.update(bl5_material_policy='elastic'),
        lambda p: p.update(extra=True),
        lambda p: p['beams'][0].update(sha256='0'*64),
        lambda p: p['shell'].update(j2_curve={}),
        lambda p: p['lines'][0].update(basis='six-row'),
    ):
        changed = copy.deepcopy(raw); mutate(changed)
        with pytest.raises(ValueError): ExperimentalGEBL5Panel.from_dict(changed)
    with pytest.raises(ValueError): replace(original, bl5_material_policy=ss.BL5_ELASTIC_NATIVE_FIBRE_V1)
    with pytest.raises(ValueError): replace(panel(), bl5_material_policy=ss.BL5_ELASTIC_NATIVE_FIBRE_MIXED_SHELL_V1)
    record = AnalysisDefinition.experimental_bl5_panel('native', original)
    detached = record.to_dict(); detached['beam_formulation_options']['panel']['lines'][0]['basis']='bad'
    assert bl5_panel_from_analysis(record).to_dict() == raw
    with pytest.raises(ValueError): AnalysisDefinition.from_dict(detached)
    project = Project('native', mesh_only=True); project.add_analysis(record)
    record.settings = {'unsupported': True}
    with pytest.raises(ValueError): record.create_bl5_panel_analysis()
    with pytest.raises(ValueError): save_project(project, tmp_path/'invalid.anyfem')
    assert not (tmp_path/'invalid.anyfem').exists()


def test_typed_yield_refusal_cancellation_checkpoint_and_request_guards(tmp_path):
    case = panel(shell='s3', beams='fibre', yield_stress=100.)
    owner = case.create_analysis(); fresh = case.create_analysis()
    other = replace(case, shell=replace(case.shell, young_pa=case.shell.young_pa*1.01)).create_analysis()
    try:
        result = owner.solve(case.load_input, (1e-6,), max_iterations=24, max_backtracks=12)
        assert result.status == 'completed'; before = owner.checkpoint()
        with pytest.raises(ss.BL5NativeElasticDomainError):
            owner.solve(case.load_input, (1.,), max_iterations=24, max_backtracks=12)
        assert owner.checkpoint() == before
        for target in (fresh, other):
            with pytest.raises(ValueError): target.restore(before[:-1]+bytes((before[-1]^1,)) if target is fresh else before)
        token = CancellationToken(); token.cancel('consumer accepted prefix')
        with pytest.raises(SolveCancelled): owner.solve(case.load_input, (2e-6,), cancellation_token=token)
        for request_type in (ss.GEPointBucklingRequest, ss.GELineBucklingRequest, ss.GEMixedBucklingRequest):
            with pytest.raises(ValueError): owner.buckling(request_type(result.generation))
        assert owner.checkpoint() == before
        fresh.restore(before); assert fresh.checkpoint() == before
    finally:
        owner.close(); fresh.close(); other.close()


def test_project_finite_helper_and_accepted_prefix_cancellation(tmp_path):
    case = panel(shell='mixed'); record = AnalysisDefinition.experimental_bl5_panel('native', case)
    result = solve_bl5_panel(record, targets=(.5,), max_iterations=24, max_backtracks=12)
    assert result.status == 'completed'
    owner = record.create_bl5_panel_analysis(); fresh = record.create_bl5_panel_analysis()
    token = CancellationToken()
    try:
        def cancel(accepted): token.cancel('accepted consumer prefix')
        with pytest.raises(SolveCancelled):
            owner.solve(case.load_input, (.5,1.), cancellation_token=token, on_accepted=cancel,
                        max_iterations=24, max_backtracks=12)
        assert owner.recover().amplitude == .5
        before = owner.checkpoint(); fresh.restore(before)
        a = owner.solve(case.load_input, (1.,), max_iterations=24, max_backtracks=12)
        b = fresh.solve(case.load_input, (1.,), max_iterations=24, max_backtracks=12)
        assert a.status == b.status == 'completed' and owner.checkpoint() == fresh.checkpoint()
        np.testing.assert_array_equal(a.beam_positions, b.beam_positions)
    finally: owner.close(); fresh.close()
