"""Strict opt-in normalization persistence; old records keep their routing."""
from dataclasses import replace
import copy
import pytest
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel,SCHEMA_CURVED_Q4_MATERIAL,SCHEMA_CURVED_Q4_MATERIAL_NATIVE,
    save_case,load_case)
from anyfem.io.project_file import save_project,load_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from test_curved_material_consumer import _material_case


@pytest.mark.parametrize('j2',[False,True])
def test_native_scale_strict_record_and_project(tmp_path,j2):
    old=_material_case(j2);before=old.to_dict()
    assert before['schema']==SCHEMA_CURVED_Q4_MATERIAL and 'retained_scale_policy' not in before
    native=replace(old,retained_scale_policy='native_force_moment_roles_v1')
    payload=native.to_dict()
    assert payload['schema']==SCHEMA_CURVED_Q4_MATERIAL_NATIVE
    assert native.definition().retained_scale_policy=='native_force_moment_roles_v1'
    assert load_case(save_case(native,tmp_path/'native.anyfem-ge.json')).to_dict()==payload
    project=Project('native retained coordinates',mesh_only=True)
    record=AnalysisDefinition.experimental_point_panel('native',native);project.add_analysis(record)
    path=save_project(project,tmp_path/'native.anyfem')
    stored_record=load_project(path).analyses[record.id]
    stored=ExperimentalGEPointPanel.from_dict(stored_record.beam_formulation_options['panel'])
    assert stored.to_dict()==payload
    owner=stored_record.create_point_panel_analysis();old_owner=old.create_analysis()
    try:
        assert owner.capabilities['retained_coordinate_scale_policy']=='native_force_moment_roles_v1'
        assert owner.preflight()['identity']!=old_owner.preflight()['identity']
    finally:owner.close();old_owner.close()
    assert ExperimentalGEPointPanel.from_dict(before).to_dict()==before


@pytest.mark.parametrize('fault',('missing','profile','unknown','legacy_schema','legacy_added'))
def test_native_scale_record_failures(fault):
    old=_material_case();data=copy.deepcopy(replace(old,retained_scale_policy='native_force_moment_roles_v1').to_dict())
    if fault=='missing':data.pop('retained_scale_policy')
    elif fault=='profile':data['retained_scale_policy']='profile'
    elif fault=='unknown':data['retained_scale_policy']='unsupported'
    elif fault=='legacy_schema':data['schema']=SCHEMA_CURVED_Q4_MATERIAL
    else:data=old.to_dict();data['retained_scale_policy']='native_force_moment_roles_v1'
    with pytest.raises(ValueError):ExperimentalGEPointPanel.from_dict(data)


def test_native_scale_rejects_other_profiles_and_missing_solver(monkeypatch):
    from test_curved_point_consumer import curved_case
    from anysolver import stiffened_shell as ss
    with pytest.raises(ValueError,match='native retained scaling'):
        replace(curved_case(),retained_scale_policy='native_force_moment_roles_v1')
    old=_material_case()
    # Simulate an installed solver without this explicit definition contract.
    class PreviousDefinition:
        __dataclass_fields__={}
    with monkeypatch.context() as patch:
        patch.setattr(ss,'LinearDefinition',PreviousDefinition)
        with pytest.raises(ValueError,match='installed curved material'):
            replace(old,retained_scale_policy='native_force_moment_roles_v1')
