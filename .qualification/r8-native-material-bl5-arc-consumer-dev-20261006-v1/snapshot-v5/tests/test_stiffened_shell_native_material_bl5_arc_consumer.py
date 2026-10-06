"""Integrated public native BL5 arc consumer coverage; source-only, unrun."""
import copy
import json
from pathlib import Path
import runpy
import numpy as np
import pytest
from anysolver import stiffened_shell as ss
from anyfem.solve.stiffened_shell import load_case, ExperimentalGEBL5Panel, SCHEMA_BL5_NATIVE_MIXED_J2_V6
from anyfem.solve.stiffened_shell_project import bl5_panel_from_analysis
from anyfem.model.records import AnalysisDefinition
from anyfem.io.project_file import load_project

EXAMPLE = runpy.run_path(str(Path(__file__).resolve().parents[1]/
    'examples/stiffened_shell_native_material_bl5_arc_consumer.py'))


def test_project_native_material_arc_prefix_cancel_restore_continue(tmp_path):
    directory = tmp_path/'arc'
    result = EXAMPLE['workflow'](directory)
    assert result['status'] == 'completed' and result['restored_status'] == 'accepted_prefix'
    assert not result['production_qualified'] and result['full_frame_metric']
    assert result['schema'] == SCHEMA_BL5_NATIVE_MIXED_J2_V6
    assert result['profile'] == dict(shell='mixed', family='biaxial', beams='mixed', basis='continuous_p1_coarse')
    assert result['policy'] == ss.BL5_NATIVE_FIBRE_MIXED_SHELL_MATERIAL_V1
    assert result['analysis_type'] == 'experimental_ge_bl5_panel'
    assert result['formulation'] == 'experimental-ge-bl5-panel'
    assert result['physical_fibre_alpha_max'] > 0. and result['s3_alpha_max'] > 0.
    assert result['loaded_generation']+1 == result['prefix_generation']
    assert result['prefix_generation']+1 == result['generation']
    assert len(result['history']) == 2
    assert result['prefix']['status'] == 'accepted_prefix'
    assert result['continued']['status'] == 'completed'
    assert result['cancellation']['before_commit']['stage'] == 'stiffened-shell.arc.commit'
    assert all(item['reason'] and item['stage'] for item in result['cancellation'].values())
    assert all(result['refusals'].values())
    assert result['wrong_model_identity'] != result['model_identity']
    assert result['input_roundtrip_equal'] and result['native_definition_bytes_equal']
    assert result['fresh_restore_bytes_equal'] and result['continued_bytes_equal']
    assert result['continued']['free_equilibrium_ratio'] <= 1e-8
    assert result['continued']['constraint_max'] <= 1e-9
    for name in ('shell_q4_material', 'shell_s3_material', 'beam_fibre_material',
                 'beam_generalized_material', 'cell_frames', 'cell_resultants',
                 'multipliers', 'support_reactions', 'applied_dead_load'):
        assert result['continued'][name]
    physical = {row['element_id'] for row in result['continued']['beam_fibre_material']}
    generalized = {row['element_id'] for row in result['continued']['beam_generalized_material']}
    assert physical and generalized and physical.isdisjoint(generalized)
    assert physical == {row['element_id'] for row in result['physical_fibre_members']}
    case = load_case(directory/'original-case.anyfem-ge.json')
    reopened = load_project(directory/'original-project.anyfem')
    record = reopened.analyses[result['project_analysis_id']]
    assert bl5_panel_from_analysis(record).to_dict() == case.to_dict() == EXAMPLE['panel']().to_dict()
    assert json.loads((directory/'workflow.json').read_text()) == result
    assert (directory/'loaded-seed.bin').read_bytes() != (directory/'accepted-arc-prefix.bin').read_bytes()
    assert (directory/'accepted-arc-prefix.bin').read_bytes() != (directory/'continued-arc.bin').read_bytes()


def test_existing_v6_project_ownership_and_strict_input_refusals():
    case = EXAMPLE['panel']()
    original = case.to_dict()
    record = AnalysisDefinition.experimental_bl5_panel('arc inputs', case)
    detached = record.to_dict()
    detached['beam_formulation_options']['panel']['lines'][0]['basis'] = 'six-row'
    assert bl5_panel_from_analysis(record).to_dict() == original
    with pytest.raises(ValueError): AnalysisDefinition.from_dict(detached)
    for change in (
        lambda value: value.update(schema='anyfem.experimental-ge-bl5-panel-native-elastic-v5'),
        lambda value: value.update(bl5_material_policy=ss.BL5_ELASTIC_NATIVE_FIBRE_MIXED_SHELL_V1),
        lambda value: value.update(load_programme={}),
        lambda value: value.update(arc_checkpoint='accepted-arc-prefix.bin'),
        lambda value: value['beams'][0].update(sha256='0'*64),
    ):
        changed = copy.deepcopy(original); change(changed)
        with pytest.raises(ValueError): ExperimentalGEBL5Panel.from_dict(changed)
        assert case.to_dict() == original
    weights = np.array([.2, .8])
    metric = ss.PhysicalChordMetric(weights, 1.)
    weights[:] = 9.
    np.testing.assert_array_equal(metric.weights, np.array([.2, .8]))
    assert bl5_panel_from_analysis(record).to_dict()['schema'] == SCHEMA_BL5_NATIVE_MIXED_J2_V6
