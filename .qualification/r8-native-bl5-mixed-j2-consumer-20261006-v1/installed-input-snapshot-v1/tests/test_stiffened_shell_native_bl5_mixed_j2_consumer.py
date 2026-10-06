"""Strict native mixed J2 inputs and public Project material restart contract."""
from dataclasses import replace
import copy
from pathlib import Path
import runpy
import pytest
from anysolver import stiffened_shell as ss
from anysolver.control import CancellationToken, SolveCancelled
from anyfem.solve.stiffened_shell import ExperimentalGEBL5Panel, SCHEMA_BL5_NATIVE_MIXED_J2_V6
from anyfem.solve.stiffened_shell_project import bl5_panel_from_analysis, solve_bl5_panel
from anyfem.model.records import AnalysisDefinition

EXAMPLE = runpy.run_path(str(Path(__file__).resolve().parents[1]/
    'examples/stiffened_shell_native_fibre_bl5_mixed_j2_consumer.py'))
panel = EXAMPLE['panel']


@pytest.mark.parametrize('shell,family,beams,basis', [
    ('s3', 'axial', 'fibre', 'p0'),
    ('mixed', 'biaxial', 'mixed', 'continuous_p1_coarse')])
def test_project_fixed_translation_material_restart(tmp_path, shell, family, beams, basis):
    result = EXAMPLE['workflow'](tmp_path, shell, family, beams, basis)
    assert result['status'] == 'completed' and not result['production_qualified']
    assert result['schema'] == SCHEMA_BL5_NATIVE_MIXED_J2_V6
    assert result['policy'] == ss.BL5_NATIVE_FIBRE_MIXED_SHELL_MATERIAL_V1
    assert result['target'] == EXAMPLE['TARGETS'][-1]
    assert result['shell_s3_material'] and result['beam_fibre_material']
    assert bool(result['beam_generalized_material']) == (beams == 'mixed')
    s3_alpha = max(value for record in result['shell_s3_material'] for value in record['alpha'])
    if family == 'axial':
        # This axial-only fixture yields its physical fibres while the shell is elastic.
        assert s3_alpha == 0.
    else:
        assert s3_alpha > 0.
    assert result['physical_fibre_alpha_max'] > 0.
    physical_ids = {item['element_id'] for item in result['beam_fibre_material']}
    assert physical_ids == {item['element_id'] for item in result['physical_fibre_members']}
    assert physical_ids.isdisjoint(item['element_id'] for item in result['beam_generalized_material'])
    assert result['input_bytes_equal'] and result['force_seed_restore_bytes_equal']
    assert result['fresh_restore_bytes_equal'] and result['continued_bytes_equal']
    assert result['recovered_generation'] == result['generation']
    assert result['free_equilibrium_ratio'] <= 1e-8 and result['constraint_max'] <= 1e-9


def test_v6_strict_legacy_isolation_and_owned_project_record():
    original = panel(); raw = original.to_dict()
    for mutate in (
        lambda p: p.update(schema='anyfem.experimental-ge-bl5-panel-native-elastic-v5'),
        lambda p: p.update(schema='anyfem.experimental-ge-bl5-panel-coupled-material-v4'),
        lambda p: p.update(bl5_material_policy=ss.BL5_ELASTIC_NATIVE_FIBRE_MIXED_SHELL_V1),
        lambda p: p.update(extra=True),
        lambda p: p['shell'].pop('j2_curve'),
        lambda p: p['shell'].update(j2_curve={}),
        lambda p: p.update(load_programme={}),
        lambda p: p['beams'][0].update(sha256='0'*64),
        lambda p: p['lines'][0].update(basis='six-row'),
    ):
        changed = copy.deepcopy(raw); mutate(changed)
        with pytest.raises(ValueError): ExperimentalGEBL5Panel.from_dict(changed)
    with pytest.raises(ValueError): replace(original, shell=replace(original.shell, j2_curve=None))
    with pytest.raises(ValueError): replace(original, shell=replace(original.shell, j2_curve={}))
    with pytest.raises(ValueError): replace(original, shell=replace(original.shell, s3_elements=()))
    record = AnalysisDefinition.experimental_bl5_panel('material', original)
    detached = record.to_dict(); detached['beam_formulation_options']['panel']['bl5_material_policy']='elastic'
    assert bl5_panel_from_analysis(record).to_dict() == raw
    with pytest.raises(ValueError): AnalysisDefinition.from_dict(detached)


def test_public_failure_cancel_and_checkpoint_atomicity():
    case = panel(shell='s3', family='axial', beams='fibre')
    record = AnalysisDefinition.experimental_bl5_panel('material', case)
    seed = solve_bl5_panel(record, targets=(.001,), **EXAMPLE['OPTIONS'])
    assert seed.status == 'completed'
    owner = record.create_bl5_panel_analysis(); fresh = case.create_analysis()
    changed = replace(case, shell=replace(case.shell, young_pa=case.shell.young_pa*1.01)).create_analysis()
    token = CancellationToken()
    try:
        owner.solve(case.load_input, (.001,), **EXAMPLE['OPTIONS'])
        before = owner.checkpoint()
        with pytest.raises(ValueError): fresh.restore(before[:-1]+bytes((before[-1]^1,)))
        with pytest.raises(ValueError): changed.restore(before)
        with pytest.raises(ValueError): owner.solve(case.load_input, (), **EXAMPLE['OPTIONS'])
        for request_type in (ss.GEPointBucklingRequest, ss.GELineBucklingRequest, ss.GEMixedBucklingRequest):
            with pytest.raises(ValueError): owner.buckling(request_type(seed.generation))
        assert owner.checkpoint() == before
        token.cancel('before next material solve')
        with pytest.raises(SolveCancelled):
            owner.solve(case.load_input, EXAMPLE['TARGETS'][:1], displacement_control=EXAMPLE['CONTROL'],
                cancellation_token=token, **EXAMPLE['OPTIONS'])
        assert owner.checkpoint() == before
        token = CancellationToken()
        def cancel(accepted): token.cancel('accepted material prefix')
        with pytest.raises(SolveCancelled):
            owner.solve(case.load_input, EXAMPLE['TARGETS'][:2], displacement_control=EXAMPLE['CONTROL'],
                cancellation_token=token, on_accepted=cancel, **EXAMPLE['OPTIONS'])
        assert owner.recover().target == EXAMPLE['TARGETS'][0]
        accepted = owner.checkpoint(); fresh.restore(accepted)
        assert fresh.checkpoint() == accepted
        a = owner.solve(case.load_input, EXAMPLE['TARGETS'][1:], displacement_control=EXAMPLE['CONTROL'], **EXAMPLE['OPTIONS'])
        b = fresh.solve(case.load_input, EXAMPLE['TARGETS'][1:], displacement_control=EXAMPLE['CONTROL'], **EXAMPLE['OPTIONS'])
        assert a.status == b.status == 'completed' and owner.checkpoint() == fresh.checkpoint()
    finally:
        owner.close(); fresh.close(); changed.close()
