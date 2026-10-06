"""Dedicated physical-law schema and public case/Project execution contracts."""
from dataclasses import replace
import copy
import json
from pathlib import Path
import runpy
import numpy as np
import pytest
from anysolver import stiffened_shell as api
from anysolver.control import CancellationToken
from anyfem.solve.stiffened_shell_b2 import (
    ExperimentalB2PointPanel, ExperimentalB2NodalRigidFootprintPanel,
    ExperimentalB2PhysicalRigidFootprintPanel, SCHEMA_PHYSICAL_FOOTPRINT,
)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel
from anyfem.model.records import AnalysisDefinition

EXAMPLE = runpy.run_path(str(Path(__file__).resolve().parents[1]/
    'examples/stiffened_shell_b2_physical_footprint_consumer.py'))
panel = EXAMPLE['panel']


def test_real_case_project_static_batches_and_physical_buckling(tmp_path):
    result = EXAMPLE['workflow'](tmp_path)
    assert result['status'] == result['project_status'] == 'completed'
    assert result['schema'] == SCHEMA_PHYSICAL_FOOTPRINT and result['input_bytes_equal']
    assert result['selected_mapping_matches_original_definition']
    assert result['requested_member_stations'] == {'beam-right':[0.,.5,1.]}
    assert result['beam_resultants_local'][0][0] == 'beam-right'
    assert len(result['beam_resultants_local'][0][1]) == 3
    assert not result['production_qualified']
    descriptor = result['physical_descriptor']
    assert descriptor['law'] == api.B2_PHYSICAL_RIGID_FOOTPRINT_DIRECTOR_REFERENCE_V1_EXPERIMENTAL
    assert descriptor['rows_per_node'] == 5 and descriptor['normal_spin'] == 'independent'
    assert [item['case_count'] for item in result['batches']] == [1,10,100]
    for item in result['batches']:
        assert item['status'] == 'completed' and item['factorization_count'] == 1
        assert item['solve_count'] == item['case_count']
        assert item['selected_dofs'] == list(EXAMPLE['DOFS'])
        assert item['selected_footprint_counts'] == [1]*item['case_count']
        assert 1 <= item['maximum_rhs_chunk'] <= item['case_count']
    assert result['batches'][-1]['maximum_rhs_chunk'] < 100
    assert result['buckling_status'] == 'completed', result['buckling_failure_reason']
    assert result['buckling_policy'] == 'B2_PHYSICAL_RIGID_FOOTPRINT_GREEN_BUCKLING_V1_DEV'
    assert result['load_factors'] and all(np.isfinite(result['load_factors']))


def test_strict_distinct_descriptor_owned_input_and_legacy_guards(monkeypatch):
    original = panel(); raw = original.to_dict()
    for mutate in (
        lambda p: p.update(schema='anyfem.experimental-b2-nodal-rigid-footprint-panel-v1'),
        lambda p: p.update(extra=True),
        lambda p: p['physical_footprints'][0].update(rows_per_node=6),
        lambda p: p['physical_footprints'][0].update(normal_spin='locked'),
        lambda p: p['physical_footprints'][0].update(director=[0.,0.,2.]),
        lambda p: p['physical_footprints'][0].update(tangent_basis=[[1.,0.],[0.,1.],[1.,0.]]),
        lambda p: p.update(selected_physical_footprints=['undeclared']),
        lambda p: p['panel'].update(schema='anyfem.experimental-b2-q4-point-panel-mass-v2'),
    ):
        changed = copy.deepcopy(raw); mutate(changed)
        with pytest.raises(ValueError): ExperimentalB2PhysicalRigidFootprintPanel.from_dict(changed)
    for legacy in (ExperimentalB2PointPanel, ExperimentalB2NodalRigidFootprintPanel):
        with pytest.raises(ValueError): legacy(json.dumps(raw).encode('ascii'))
    with pytest.raises(ValueError): ExperimentalB2PointPanel(json.dumps(raw['panel']).encode('ascii'))
    changed = original.to_dict(); changed['physical_footprints'][0]['director'][2] = 2.
    assert original.to_dict() == raw
    record = AnalysisDefinition.experimental_point_panel('physical', original)
    detached = record.to_dict(); detached['beam_formulation_options']['panel']['physical_footprints'][0]['rows_per_node']=6
    assert point_panel_from_analysis(record).payload == original.payload
    with pytest.raises(ValueError): AnalysisDefinition.from_dict(detached)
    with pytest.raises(ValueError): original.modes(None)
    with pytest.raises(ValueError): original.buckling(api.B2ReferenceBucklingRequest(
        EXAMPLE['preload'](), EXAMPLE['traction_sources']()))
    with pytest.raises(ValueError): original.solve_linear('undeclared')
    with pytest.raises(ValueError): ExperimentalB2PhysicalRigidFootprintPanel.from_definition(
        EXAMPLE['definition'](), (('one',EXAMPLE['preload']()),), selected_dofs=(0,),
        selected_footprints=api.B2NodalRigidFootprintSelection(('physical-left',)))
    model = EXAMPLE['definition']()
    with pytest.raises(ValueError): ExperimentalB2PhysicalRigidFootprintPanel.from_definition(
        replace(model, shells=(replace(model.shells[0], density_kg_m3=1.),)+model.shells[1:]),
        (('one',EXAMPLE['preload']()),), selected_dofs=(0,))
    def forbidden(**kwargs): raise AssertionError('input construction must not assemble/factor a POINT owner')
    monkeypatch.setattr(api, 'create_analysis', forbidden)
    assert ExperimentalB2PhysicalRigidFootprintPanel.from_dict(raw).payload == original.payload


def test_project_cancelled_prefix_and_callback_failures():
    original = panel(10); record = AnalysisDefinition.experimental_point_panel('physical', original)
    token = CancellationToken(); token.cancel('before physical batch')
    before = solve_point_panel(record, cancellation_token=token)
    assert before.status == 'cancelled' and before.cases == () and before.factorization_count == 0
    token = CancellationToken()
    def cancel(case_id): token.cancel('accepted selected prefix')
    prefix = solve_point_panel(record, cancellation_token=token, on_case=cancel)
    assert prefix.status == 'cancelled' and len(prefix.cases) == 1
    assert prefix.factorization_count == prefix.solve_count == 1
    assert prefix.cases[0].case_id == 'case-0' and len(prefix.cases[0].footprint_wrenches) == 1
    def explode(case_id): raise RuntimeError('callback programming error')
    with pytest.raises(RuntimeError, match='callback programming error'):
        solve_point_panel(record, on_case=explode)
