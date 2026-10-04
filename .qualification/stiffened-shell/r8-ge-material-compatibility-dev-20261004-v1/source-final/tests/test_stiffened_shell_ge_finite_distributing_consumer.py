"""Explicit finite input/persistence and actual public accepted-state boundary."""
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
    ExperimentalGEFiniteDistributingPanel as Panel,ExperimentalGEReferenceDistributingPanel,
    SCHEMA_FINITE_DISTRIBUTING,FINITE_DISTRIBUTING_LAW,save_case,load_case,
)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis,solve_point_panel


@pytest.fixture(scope='module')
def inputs():
    path=Path(__file__).resolve().parents[1]/'examples/stiffened_shell_ge_finite_distributing_consumer.py'
    sys.path.insert(0,str(path.parent))
    try:
        spec=importlib.util.spec_from_file_location('finite_distributing_example',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    finally:sys.path.pop(0)
    definition,actions=module.definition_and_actions()
    return definition,actions,Panel.from_definition(definition,actions),module


def test_owned_inputs_and_distinct_schema_without_assembly(inputs,monkeypatch,tmp_path):
    definition,actions,panel,_=inputs
    monkeypatch.setattr(ss,'create_analysis',lambda **kwargs:pytest.fail('input persistence assembled owner'))
    coords=[list(p) for p in definition.shell.coordinates_m]
    wrench=np.array(actions[0].wrench)
    owned=Panel.from_definition(replace(definition,shell=replace(definition.shell,coordinates_m=coords)),
                               (replace(actions[0],wrench=wrench),))
    payload=owned.to_dict();coords[0][0]=999.;wrench[:]=np.nan
    assert owned.to_dict()==payload
    assert payload['schema']==SCHEMA_FINITE_DISTRIBUTING and payload['law']==FINITE_DISTRIBUTING_LAW
    assert owned.definition().distributing_footprints==()
    assert owned.definition().finite_distributing_footprints==definition.finite_distributing_footprints
    filename=save_case(owned,tmp_path/'finite.anyfem-ge.json')
    assert type(load_case(filename)) is Panel
    assert load_case(filename).to_dict()==payload
    project=Project('finite input',mesh_only=True)
    analysis=AnalysisDefinition.experimental_point_panel('finite pad',owned)
    project.add_analysis(analysis)
    reopened=load_project(save_project(project,tmp_path/'finite.anyfem')).analyses[analysis.id]
    assert point_panel_from_analysis(reopened).to_dict()==payload
    with pytest.raises(ValueError):ExperimentalGEReferenceDistributingPanel.from_dict(payload)
    with pytest.raises(ValueError):ExperimentalGEReferenceDistributingPanel.from_definition(definition,actions)


def test_public_project_dispatch_recovery_and_restart(inputs,tmp_path):
    _,_,panel,_=inputs
    analysis=AnalysisDefinition.experimental_point_panel('finite pad',panel)
    result=solve_point_panel(analysis,targets=(.25,.5))
    assert result.status=='completed' and result.accepted_factors==(.25,.5)
    owner=analysis.create_point_panel_analysis();resumed=analysis.create_point_panel_analysis()
    try:
        owner.solve(panel.load_input,(.25,.5));checkpoint=owner.checkpoint()
        saved=tmp_path/'accepted.checkpoint';saved.write_bytes(checkpoint)
        restored=resumed.restore(saved.read_bytes())
        assert resumed.checkpoint()==checkpoint and restored.accepted_factors==(.25,.5)
        expected=owner.solve(panel.load_input,(1.,));actual=resumed.solve(panel.load_input,(1.,))
        for name in ('shell_displacement','beam_positions','beam_frames','cell_frames','cell_resultants','multipliers','support_reactions'):
            np.testing.assert_allclose(getattr(actual,name),getattr(expected,name),rtol=1e-10,atol=1e-12)
        record,=resumed.recover(selection=panel.recovery_selection)
        assert type(record) is ss.GEFiniteDistributingFootprintWrench
        assert record.law==FINITE_DISTRIBUTING_LAW and record.generation==actual.generation
        assert record.configuration=='current' and not record.production_qualified
    finally:owner.close();resumed.close()


def test_cancellation_preserves_complete_accepted_prefix(inputs):
    panel=inputs[2];owner=panel.create_analysis();token=CancellationToken()
    try:
        def accepted(result):
            assert owner.recover().generation==result.generation
            token.cancel('consumer accepted prefix')
        with pytest.raises(SolveCancelled):owner.solve(panel.load_input,(.25,.5),cancellation_token=token,on_accepted=accepted)
        assert owner.recover().accepted_factors==(.25,)
        checkpoint=owner.checkpoint();fresh=panel.create_analysis()
        try:
            fresh.restore(checkpoint);assert fresh.solve(panel.load_input,(.5,)).accepted_factors==(.25,.5)
        finally:fresh.close()
    finally:owner.close()


@pytest.mark.parametrize('change',('schema','law','admission','group_law','empty','unknown','material_policy','moment','nan','host_bool','bounds'))
def test_strict_payload_rejection(inputs,change,monkeypatch):
    payload=deepcopy(inputs[2].to_dict())
    monkeypatch.setattr(ss,'create_analysis',lambda **kwargs:pytest.fail('invalid payload assembled'))
    if change in ('schema','law','admission'):payload[change]='wrong'
    elif change=='group_law':payload['finite_distributing_footprints'][0]['law']='wrong'
    elif change=='empty':payload['finite_distributing_footprints']=[]
    elif change=='unknown':payload['extra']=1
    elif change=='material_policy':payload['finite_distributing_material_policy']='selected-material'
    elif change=='moment':payload['actions'][0]['wrench'][3]=1.
    elif change=='nan':payload['actions'][0]['wrench'][0]=float('nan')
    elif change=='host_bool':payload['finite_distributing_footprints'][0]['host_element_ids'][0]=True
    else:payload['finite_distributing_footprints'][0]['bounds_xy_m']=[0.,1.]
    with pytest.raises(ValueError):Panel.from_dict(payload)


@pytest.mark.parametrize('policy',(
    'GE_FINITE_DISTRIBUTING_GENERALIZED_MATERIAL_V1_DEV',
    'GE_FINITE_DISTRIBUTING_COUPLED_J2_MATERIAL_V1_DEV', None, True))
def test_material_selector_cannot_be_discarded(inputs,policy,monkeypatch):
    definition,actions,_,_=inputs
    if 'finite_distributing_material_policy' not in ss.LinearDefinition.__dataclass_fields__:
        pytest.skip('new explicit material selector is absent from older solver')
    monkeypatch.setattr(ss,'create_analysis',lambda **kw:pytest.fail('unsupported material assembled'))
    changed=replace(definition,finite_distributing_material_policy=policy)
    with pytest.raises(ValueError,match='material policy'):
        Panel.from_definition(changed,actions)
    with pytest.raises(ValueError,match='material policy'):
        ExperimentalGEReferenceDistributingPanel.from_definition(
            replace(changed,finite_distributing_footprints=()),actions)
    from anyfem.solve.stiffened_shell import ExperimentalGEReferenceModalPanel
    with pytest.raises(ValueError,match='material policy'):
        ExperimentalGEReferenceModalPanel.from_definition(
            replace(changed,finite_distributing_footprints=()),object())


def test_native_record_rejects_material_selector_injection(inputs,tmp_path):
    record=AnalysisDefinition.experimental_point_panel('elastic only',inputs[2])
    record.beam_formulation_options['panel']['finite_distributing_material_policy']='selected-material'
    with pytest.raises(ValueError):record.to_dict()
    from anyfem.solve.stiffened_shell_project import point_panel_from_analysis
    with pytest.raises(ValueError):point_panel_from_analysis(record)


@pytest.mark.parametrize('kwargs',({},dict(targets=(1.,),increment_control=object()),dict(targets=(1.,),on_case=lambda *a:None)))
def test_unsupported_project_controls_fail_before_owner(inputs,kwargs,monkeypatch):
    analysis=AnalysisDefinition.experimental_point_panel('finite pad',inputs[2])
    monkeypatch.setattr(ss,'create_analysis',lambda **kw:pytest.fail('unsupported controls assembled'))
    with pytest.raises(ValueError):solve_point_panel(analysis,**kwargs)


def test_legacy_reference_format_is_not_upgraded(inputs):
    definition,actions,_,_=inputs
    geometry=definition.finite_distributing_footprints[0].footprint
    old=replace(definition,finite_distributing_footprints=(),distributing_footprints=(geometry,))
    reference=ExperimentalGEReferenceDistributingPanel.from_definition(old,actions)
    reopened=ExperimentalGEReferenceDistributingPanel.from_dict(reference.to_dict())
    assert reopened.definition().finite_distributing_footprints==()
    with pytest.raises(ValueError):Panel.from_definition(old,actions)
    with pytest.raises(ValueError):Panel.from_dict(reference.to_dict())


@pytest.mark.parametrize('invalid',(None,[],False))
def test_malformed_unused_reference_field_is_not_silently_discarded(inputs,invalid):
    with pytest.raises(ValueError):Panel.from_definition(replace(inputs[0],distributing_footprints=invalid),inputs[1])


def test_runnable_public_consumer_example(inputs,tmp_path):
    observations=inputs[3].workflow(tmp_path)
    assert observations['accepted_factors']==(.25,.5,1.)
    assert observations['resumed_displacement_difference']==0.
    assert observations['equilibrium']<1e-8 and observations['constraint']<1e-9
    assert observations['status']=='EXPERIMENTAL_NOT_QUALIFIED'
