"""Selected material intent is preserved across public input and Project routes."""
from copy import deepcopy
from dataclasses import replace
import importlib.util,sys
from pathlib import Path
import numpy as np,pytest
from anysolver import stiffened_shell as ss
from anysolver.control import CancellationToken,SolveCancelled
from anyfem.io.project_file import save_project,load_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEFiniteDistributingMaterialPanel as Panel,
    ExperimentalGEFiniteDistributingPanel as ElasticPanel,save_case,load_case,
)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis,solve_point_panel


@pytest.fixture(scope='module')
def example():
    path=Path(__file__).resolve().parents[1]/'examples/stiffened_shell_ge_distributing_material_consumer.py'
    sys.path.insert(0,str(path.parent))
    try:
        spec=importlib.util.spec_from_file_location('material_consumer_example',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    finally:sys.path.pop(0)
    return module


@pytest.mark.parametrize('coupled',(False,True))
def test_owned_input_standalone_and_project_without_assembly(example,coupled,tmp_path,monkeypatch):
    model,loads=example.definition_and_actions(coupled)
    monkeypatch.setattr(ss,'create_analysis',lambda **kw:pytest.fail('input framing assembled an owner'))
    coordinates=[list(row) for row in model.shell.coordinates_m]
    panel=Panel.from_definition(replace(model,shell=replace(model.shell,coordinates_m=coordinates)),loads)
    payload=panel.to_dict();coordinates[0][0]=123.
    assert panel.to_dict()==payload and panel.definition().finite_distributing_material_policy==model.finite_distributing_material_policy
    if coupled:
        assert panel.shell.j2_curve is not model.shell.j2_curve
        assert panel.shell.j2_curve.as_dict()==model.shell.j2_curve.as_dict()
    assert type(load_case(save_case(panel,tmp_path/'material.anyfem-ge.json'))) is Panel
    project=Project('material inputs',mesh_only=True)
    record=AnalysisDefinition.experimental_point_panel('material',panel);project.add_analysis(record)
    reopened=load_project(save_project(project,tmp_path/'material.anyfem')).analyses[record.id]
    assert point_panel_from_analysis(reopened).to_dict()==payload
    with pytest.raises(ValueError):ElasticPanel.from_dict(payload)
    with pytest.raises(ValueError):ElasticPanel.from_definition(model,loads)


@pytest.mark.parametrize('change',('policy','missing_policy','schema','extra','curve','law','action','nan'))
def test_invalid_material_record_fails_closed(example,change,monkeypatch):
    model,loads=example.definition_and_actions()
    payload=Panel.from_definition(model,loads).to_dict()
    monkeypatch.setattr(ss,'create_analysis',lambda **kw:pytest.fail('invalid record assembled'))
    if change=='policy':payload['finite_distributing_material_policy']='elastic'
    elif change=='missing_policy':del payload['finite_distributing_material_policy']
    elif change=='schema':payload['schema']='anyfem.experimental-ge-finite-distributing-panel-v1'
    elif change=='extra':payload['unknown']=1
    elif change=='curve':payload['shell']['j2_curve']=None
    elif change=='law':payload['law']='unknown'
    elif change=='action':payload['actions'][0]['wrench'][3]=1.
    elif change=='nan':payload['shell']['j2_curve']['sigma_yield']=float('nan')
    with pytest.raises(ValueError):Panel.from_dict(payload)


def test_native_mutation_revalidates_material_intent(example):
    model,loads=example.definition_and_actions()
    record=AnalysisDefinition.experimental_point_panel('material',Panel.from_definition(model,loads))
    record.beam_formulation_options['panel']['finite_distributing_material_policy']='elastic'
    with pytest.raises(ValueError):record.to_dict()
    with pytest.raises(ValueError):point_panel_from_analysis(record)


@pytest.mark.parametrize('change',('lines','shell_policy','material_policy','pullback'))
def test_definition_unsupported_policy_is_not_discarded(example,change):
    model,loads=example.definition_and_actions()
    kwargs=dict(lines=(object(),)) if change=='lines' else dict(shell_reference_policy='unknown') if change=='shell_policy' else dict(finite_distributing_material_policy=None) if change=='material_policy' else dict(q4_pullback_policy='unknown')
    with pytest.raises(ValueError):Panel.from_definition(replace(model,**kwargs),loads)


@pytest.mark.parametrize('coupled',(False,True))
def test_public_project_yield_cancel_and_restart(example,tmp_path,coupled):
    model,loads=example.definition_and_actions(coupled)
    panel=Panel.from_definition(model,loads)
    record=AnalysisDefinition.experimental_point_panel('yielding',panel)
    result=solve_point_panel(record,targets=(.25,.5,.75,1.),max_iterations=24,max_backtracks=16)
    assert np.max(result.beam_station_accumulated)>0.
    if coupled:assert np.max(result.shell_layer_alpha)>0.
    owner=record.create_point_panel_analysis();resumed=record.create_point_panel_analysis()
    token=CancellationToken()
    try:
        def accepted(result):
            if result.generation==4:token.cancel('complete yielded prefix')
        with pytest.raises(SolveCancelled):
            owner.solve(panel.load_input,(.25,.5,.75,1.,1.0001),on_accepted=accepted,
                cancellation_token=token,max_iterations=24,max_backtracks=16)
        raw=owner.checkpoint();resumed.restore(raw)
        a=owner.solve(panel.load_input,(1.0001,),max_iterations=24,max_backtracks=16)
        b=resumed.solve(panel.load_input,(1.0001,),max_iterations=24,max_backtracks=16)
        assert owner.checkpoint()==resumed.checkpoint()
        np.testing.assert_array_equal(a.shell_layer_alpha,b.shell_layer_alpha)
        np.testing.assert_array_equal(a.support_reactions,b.support_reactions)
        record,=resumed.recover(selection=panel.recovery_selection)
        assert record.configuration=='current' and not record.production_qualified
    finally:owner.close();resumed.close()


def test_public_example(example,tmp_path):
    result=example.workflow(tmp_path)
    assert result['status']=='EXPERIMENTAL_NOT_QUALIFIED'
    assert result['shell_alpha']>0. and result['beam_accumulated']>0.


@pytest.mark.parametrize('field',('material_policy','checkpoints','fixed_step_max_backtracks','preflight'))
def test_wrong_owner_contract_is_closed(example,monkeypatch,field):
    model,loads=example.definition_and_actions()
    panel=Panel.from_definition(model,loads)
    class Owner:
        closed=False
        capabilities=dict(status=panel.status,profile=model.profile,
            distributing_footprint_law=ss.GE_DISTRIBUTING_FINITE_DIRECTOR_POLAR_V1_EXPERIMENTAL,
            material_policy=model.finite_distributing_material_policy,
            analysis=('nonlinear_force',),loads=('nodal dead translation forces',),
            checkpoints=('finite_distributing_coupled',),fixed_step_max_backtracks=16)
        def preflight(self):return dict(identity='0'*64,constraints=6)
        def recover(self):pass
        def close(self):self.closed=True
    owner=Owner()
    if field=='preflight':owner.preflight=lambda:dict(identity=None,constraints=6)
    else:owner.capabilities[field]='wrong'
    monkeypatch.setattr(ss,'create_analysis',lambda **kw:owner)
    with pytest.raises(ValueError,match='public owner lacks'):
        panel.create_analysis()
    assert owner.closed
