"""Explicit BL5 Project identity, persistence and public solver lifecycle."""
from dataclasses import replace
import copy

import numpy as np
import pytest

from anysolver import stiffened_shell as ss
from anysolver.control import CancellationToken, SolveCancelled
from anyfem.model.records import AnalysisDefinition
from anyfem.model.project import Project
from anyfem.io.project_file import load_project, save_project
from anyfem.solve.stiffened_shell_project import (
    BL5_TYPE, BL5_FORMULATION, bl5_panel_from_analysis, solve_bl5_panel,
)
from test_experimental_stiffened_shell_consumer import _bl5_case


def _panel(staged=False):
    panel = _bl5_case()
    if not staged:
        return panel
    panel = replace(panel, actions=(), q4_pullback_policy=ss.Q4_ALGORITHMIC_PULLBACK_V1)
    owner = panel.create_analysis()
    try:
        identity = owner.preflight()['identity']
    finally:
        owner.close()
    programme = ss.StagedDeadLoadProgramme(identity, tuple(
        (ss.NodalAction('beam',105,(0.,0.,f,0.,0.,0.)),) for f in (100.,-100.)))
    return replace(panel, load_programme=programme)


@pytest.mark.parametrize('staged',[False,True])
def test_bl5_project_roundtrip_and_solve(tmp_path,request,staged):
    panel = _panel(staged)
    project = Project('nonmatching BL5',mesh_only=True)
    record = AnalysisDefinition.experimental_bl5_panel('line',panel)
    project.add_analysis(record)
    before = record.to_dict()
    reopened = load_project(save_project(project,tmp_path/'line.anyfem'))
    record = reopened.analyses[record.id]
    assert record.to_dict() == before
    assert (record.type,record.beam_formulation) == (BL5_TYPE,BL5_FORMULATION)
    assert bl5_panel_from_analysis(record).to_dict() == panel.to_dict()
    with pytest.raises(ValueError):
        record.create_point_panel_analysis()
    if not staged:
        result = solve_bl5_panel(record)
        assert result.status == 'completed' and result.free_equilibrium_ratio <= 1e-8
        return
    targets = (.5,1.,1.25,1.75,2.)
    control = ss.AdaptiveIncrementControl(2,16)
    direct = record.create_bl5_panel_analysis();request.addfinalizer(direct.close)
    token = CancellationToken()
    def cancel(result):
        if result.load_position == 1.:token.cancel('saved BL5 accepted prefix')
    with pytest.raises(SolveCancelled):
        direct.solve(panel.load_input,targets,increment_control=control,
                     cancellation_token=token,on_accepted=cancel)
    assert direct.recover().load_position == 1.
    direct.rebase_rotations(direct.recover().generation)
    checkpoint = tmp_path/'accepted.bin'
    checkpoint.write_bytes(direct.checkpoint())
    restored = record.create_bl5_panel_analysis();request.addfinalizer(restored.close)
    restored.restore(checkpoint.read_bytes())
    a = direct.solve(panel.load_input,targets,increment_control=control)
    b = restored.solve(panel.load_input,targets,increment_control=control)
    assert direct.checkpoint() == restored.checkpoint()
    assert a.load_programme_complete and b.load_programme_complete
    np.testing.assert_array_equal(a.beam_positions,b.beam_positions)
    # The Project helper forwards the explicit stored programme to a fresh owner.
    result = solve_bl5_panel(record,targets=targets,increment_control=control)
    assert result.load_position == 2. and result.load_programme_complete
    np.testing.assert_allclose(result.beam_positions,a.beam_positions,rtol=0.,atol=1e-9)


@pytest.mark.parametrize('field,value',[
    ('type','experimental_ge_point_panel'),
    ('beam_formulation','experimental-ge-point-panel'),
    ('settings',{'unexpected':1}),('target_id','geometry'),
    ('output_requests',{'stress':True}),('resource_policy',{'threads':2}),
])
def test_mutated_bl5_project_rejected_before_save_or_solve(tmp_path,field,value):
    record = AnalysisDefinition.experimental_bl5_panel('line',_panel())
    project = Project('line',mesh_only=True);project.add_analysis(record)
    setattr(record,field,copy.deepcopy(value))
    with pytest.raises(ValueError):record.create_bl5_panel_analysis()
    with pytest.raises(ValueError):save_project(project,tmp_path/'invalid.anyfem')
    assert not (tmp_path/'invalid.anyfem').exists()


def test_bl5_payload_is_owned_and_route_cannot_be_relabelled():
    record = AnalysisDefinition.experimental_bl5_panel('line',_panel())
    original = record.to_dict()
    changed = record.to_dict()
    changed['beam_formulation_options']['panel']['lines'][0]['basis']='unknown'
    assert record.to_dict() == original
    with pytest.raises(ValueError):AnalysisDefinition.from_dict(changed)
    changed = copy.deepcopy(original)
    changed['beam_formulation_options']['schema']='anyfem.experimental-point-panel-project-v1'
    with pytest.raises(ValueError):AnalysisDefinition.from_dict(changed)
