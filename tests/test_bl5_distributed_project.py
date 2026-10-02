"""Public distributed BL5 records and native Project execution."""
from dataclasses import replace
import copy
import numpy as np
import pytest
from anysolver import stiffened_shell as ss
from anysolver.control import CancellationToken, SolveCancelled
from anyfem.model.records import AnalysisDefinition
from anyfem.model.project import Project
from anyfem.io.project_file import load_project, save_project
from anyfem.solve.stiffened_shell import ExperimentalGEBL5Panel, save_case, load_case
from anyfem.solve.stiffened_shell_project import bl5_panel_from_analysis, solve_bl5_panel
from test_experimental_stiffened_shell_consumer import _bl5_case


def panel(programme=False):
    base=replace(_bl5_case(),actions=(),q4_pullback_policy=ss.Q4_ALGORITHMIC_PULLBACK_V1)
    owner=base.create_analysis()
    try: identity=owner.preflight()['identity']
    finally: owner.close()
    actions=tuple(ss.GEBeamLineAction(i,(0.,0.,100.),(2.,3.,0.)) for i in (1,2))
    if programme:
        negative=tuple(ss.GEBeamLineAction(i,(0.,0.,-100.),(-2.,-3.,0.)) for i in (1,2))
        load=ss.GEBeamDistributedProgramme(identity,(actions,negative))
        return replace(base,distributed_programme=load)
    return replace(base,distributed_load=ss.GEBeamDistributedLoad(identity,actions))


@pytest.mark.parametrize('programme',[False,True])
def test_bl5_distributed_native_roundtrip_and_execution(tmp_path,request,programme):
    case=panel(programme)
    standalone=load_case(save_case(case,tmp_path/'line.anyfem-ge.json'))
    assert standalone.to_dict()==case.to_dict()
    assert standalone.load_input is not case.load_input
    project=Project('distributed line',mesh_only=True)
    record=AnalysisDefinition.experimental_bl5_panel('line',standalone)
    project.add_analysis(record)
    reopened=load_project(save_project(project,tmp_path/'line.anyfem'))
    record=reopened.analyses[record.id]
    assert bl5_panel_from_analysis(record).to_dict()==case.to_dict()
    if not programme:
        result=solve_bl5_panel(record)
        assert result.status=='completed' and result.distributed_load is not None
        np.testing.assert_allclose(result.distributed_load.applied_force,(0.,0.,100.),atol=1e-12)
        return
    with pytest.raises(ValueError,match='explicit'):solve_bl5_panel(record)
    direct=record.create_bl5_panel_analysis();request.addfinalizer(direct.close)
    token=CancellationToken()
    targets=(.5,1.,1.25,1.75,2.)
    control=ss.AdaptiveIncrementControl(2,16)
    def observe(result):
        if result.load_position==1.:token.cancel('consumer distributed line prefix')
    with pytest.raises(SolveCancelled):
        direct.solve(case.load_input,targets,increment_control=control,
                     cancellation_token=token,on_accepted=observe)
    assert direct.recover().load_position==1.
    direct.rebase_rotations(direct.recover().generation)
    checkpoint=tmp_path/'line.bin';checkpoint.write_bytes(direct.checkpoint())
    restored=record.create_bl5_panel_analysis();request.addfinalizer(restored.close)
    restored.restore(checkpoint.read_bytes())
    a=direct.solve(case.load_input,targets,increment_control=control)
    b=restored.solve(case.load_input,targets,increment_control=control)
    assert a.load_programme_complete and b.load_programme_complete
    assert direct.checkpoint()==restored.checkpoint()
    np.testing.assert_array_equal(a.beam_positions,b.beam_positions)
    via_helper=solve_bl5_panel(record,targets=targets,increment_control=control)
    np.testing.assert_allclose(via_helper.beam_positions,a.beam_positions,rtol=0.,atol=1e-9)


@pytest.mark.parametrize('programme',[False,True])
def test_distributed_bl5_strict_owned_input_and_cleanup(monkeypatch,programme):
    case=panel(programme)
    name='distributed_programme' if programme else 'distributed_load'
    original=case.to_dict()
    changed=copy.deepcopy(original)
    changed['panel']['actions']=[dict(domain='beam',node=105,wrench=[0,0,1,0,0,0])]
    with pytest.raises(ValueError):ExperimentalGEBL5Panel.from_dict(changed)
    changed=copy.deepcopy(original);changed['panel']=copy.deepcopy(original)
    with pytest.raises(ValueError):ExperimentalGEBL5Panel.from_dict(changed)
    changed=copy.deepcopy(original);changed[name]['identity']='0'*64
    with pytest.raises(ValueError):ExperimentalGEBL5Panel.from_dict(changed)
    changed=copy.deepcopy(original);changed['extra']=True
    with pytest.raises(ValueError):ExperimentalGEBL5Panel.from_dict(changed)
    assert case.to_dict()==original
    with pytest.raises(ValueError):replace(case,**{name:object()})
    other=panel(not programme)
    with pytest.raises(ValueError):replace(case,**{
        'distributed_load' if programme else 'distributed_programme':other.load_input})
    with pytest.raises(ValueError):replace(case,q4_pullback_policy='historical_corotational')
    class WrongOwner:
        closed=False
        def preflight(self):return {'identity':'0'*64}
        def close(self):self.closed=True
    wrong=WrongOwner()
    monkeypatch.setattr(ss,'create_analysis',lambda **kwargs:wrong)
    with pytest.raises(ValueError,match='different panel model'):case.create_analysis()
    assert wrong.closed
