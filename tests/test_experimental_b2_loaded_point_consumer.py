"""Strict persistence and public delegation for the opt-in loaded-point law."""
from dataclasses import replace
from types import SimpleNamespace
import numpy as np
import pytest
from anysolver import stiffened_shell as api
from anysolver.control import CancellationToken
from anyfem.io.project_file import save_project,load_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell_b2 import (
    ExperimentalB2LoadedPointPanel, ExperimentalB2PointPanel,
    ExperimentalB2BodyAccelerationPanel, SCHEMA_LOADED_POINT,SCHEMA_LOADED_POINT_BODY,
    save_case,load_case)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis,solve_point_panel
from test_experimental_b2_body_acceleration_consumer import _definition

POLICY="B2_FIXED_END_LOADED_POINT_TRACE_V1_DEV"


def _input(body):
    d=replace(_definition("q4"),point_trace_policy=POLICY)
    if body:
        cases=(("one",(.3,-.4,-.7)),("two",(.3,-.4,-.7)))
    else:
        load=np.zeros(54);load[49]=.08;load[32]=-.16
        cases=(("one",load),("two",load))
    return ExperimentalB2LoadedPointPanel.from_definition(d,cases,body=body,
        selected_dofs=(49,50),selected_points=("joint-right",),
        recover_members={"beam-right":(.25,.5,.75)})


@pytest.mark.parametrize("body",(False,True))
@pytest.mark.parametrize("route",("standalone","project"))
def test_roundtrip_detached_policy_and_workflow(tmp_path,body,route,monkeypatch):
    panel=_input(body);record=panel.to_dict()
    expected=panel.solve_linear_many()
    assert expected.status=="completed",expected.failure_reason
    original=api.create_analysis
    monkeypatch.setattr(api,"create_analysis",lambda **kw:pytest.fail("persistence assembled solver"))
    if route=="standalone":
        reopened=load_case(save_case(panel,tmp_path/"panel.anyfem-b2.json"));analysis=None
    else:
        project=Project("loaded B2 experimental",mesh_only=True)
        analysis=AnalysisDefinition.experimental_point_panel("loaded",panel)
        project.add_analysis(analysis)
        analysis=load_project(save_project(project,tmp_path/"panel.anyfem")).analyses[analysis.id]
        reopened=point_panel_from_analysis(analysis)
    assert type(reopened) is ExperimentalB2LoadedPointPanel
    assert reopened.to_dict()==record
    assert reopened.to_definition().point_trace_policy==POLICY
    monkeypatch.setattr(api,"create_analysis",original)
    result=reopened.solve_linear_many() if analysis is None else solve_point_panel(analysis)
    assert result==expected
    assert result.factorization_count==1
    assert result.cases[0].loaded_member_poses[0].stations==(.25,.5,.75)
    # Detached reconstructed inputs cannot mutate the immutable payload.
    definition=reopened.to_definition();definition.prescribed_offset[0]=1.
    assert reopened.to_dict()==record


@pytest.mark.parametrize("body",(False,True))
def test_historical_consumers_cannot_drop_selected_policy(body):
    panel=_input(body);d=panel.to_definition()
    with pytest.raises(ValueError,match="policy|schema"):
        ExperimentalB2PointPanel.from_definition(d,(("x",np.zeros(54)),),
            selected_dofs=(49,),schema="anyfem.experimental-b2-q4-point-panel-mass-v2")
    with pytest.raises(ValueError,match="policy|schema"):
        ExperimentalB2BodyAccelerationPanel.from_definition(d,(("x",(0.,0.,-1.)),),
            selected_dofs=(49,))
    for cls in (ExperimentalB2PointPanel,ExperimentalB2BodyAccelerationPanel):
        with pytest.raises(ValueError):
            cls(panel.payload)


@pytest.mark.parametrize("field,value",(("point_trace_policy",None),
    ("point_trace_policy","wrong"),("status","PASSED"),("schema","wrong")))
def test_wrong_scope_or_policy_rejected(field,value):
    record=_input(True).to_dict();record[field]=value
    with pytest.raises(ValueError):
        ExperimentalB2LoadedPointPanel.from_dict(record)


def test_missing_policy_is_not_defaulted():
    record=_input(False).to_dict();record.pop("point_trace_policy")
    with pytest.raises(ValueError):
        ExperimentalB2LoadedPointPanel.from_dict(record)


def test_old_solver_without_policy_support_is_closed(monkeypatch):
    panel=_input(True);closed=[]
    owner=SimpleNamespace(capabilities=SimpleNamespace(status="experimental",
        production_qualified=False,operations=("solve_linear_many","body_acceleration_load")),
        close=lambda:closed.append(True),solve_linear_many=lambda *a,**k:None,
        body_acceleration_load=lambda *a:None)
    monkeypatch.setattr(api,"create_analysis",lambda **kw:owner)
    with pytest.raises(ValueError,match="loaded-point capability"):
        panel.create_analysis()
    assert closed==[True]


@pytest.mark.parametrize("body",(False,True))
def test_consumer_complete_case_cancellation(body):
    panel=_input(body);token=CancellationToken()
    result=panel.solve_linear_many(cancellation_token=token,on_case=lambda _:token.cancel())
    assert result.status=="cancelled" and len(result.cases)==1
    assert result.cases[0].loaded_member_poses[0].member_id=="beam-right"


def test_new_schema_never_accepts_homogeneous_definition():
    d=_definition("q4")
    with pytest.raises(ValueError,match="policy"):
        ExperimentalB2LoadedPointPanel.from_definition(d,(("x",(0.,0.,-1.)),),
            selected_dofs=(49,),body=True)



def test_raw_nullable_physical_mass_is_preserved_and_not_acceleration(tmp_path):
    definition=replace(_definition("q4"),point_trace_policy=POLICY)
    definition=replace(definition,
        shells=tuple(replace(s,density_kg_m3=None) for s in definition.shells),
        members=tuple(replace(m,mass=None) for m in definition.members))
    load=np.zeros(54);load[49]=.08
    panel=ExperimentalB2LoadedPointPanel.from_definition(definition,(("nodal",load),),
        selected_dofs=(49,))
    restored=load_case(save_case(panel,tmp_path/"nullable.anyfem-b2.json"))
    assert all(s.density_kg_m3 is None for s in restored.to_definition().shells)
    assert all(m.mass is None for m in restored.to_definition().members)
    with pytest.raises(ValueError,match="acceleration"):
        _=restored.acceleration_cases
    with pytest.raises(ValueError,match="mass|density"):
        ExperimentalB2LoadedPointPanel.from_definition(definition,(("body",(0.,0.,-1.)),),
            selected_dofs=(49,),body=True)
