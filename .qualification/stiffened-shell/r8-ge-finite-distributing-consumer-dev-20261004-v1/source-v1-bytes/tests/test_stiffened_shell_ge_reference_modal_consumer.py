"""Definition-only experimental modal persistence and public modes dispatch."""
import copy
from dataclasses import replace, FrozenInstanceError
import json
from types import SimpleNamespace

import numpy as np
import pytest
from anysolver import b3_ge, stiffened_shell as api
from anysolver.control import CancellationToken
from anyfem.io.project_file import save_project, load_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel, ExperimentalGEReferenceModalPanel,
    SCHEMA_REFERENCE_MODAL, save_case, load_case,
)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel


def _definition(free=False):
    e, nu, rho, b, h = 210e9, .3, 7850., .1, .1
    area=b*h; iy=b*h**3/12.; iz=h*b**3/12.; g=e/(2*(1+nu))
    section=b3_ge.EllipsoidalGeneralizedSection(np.diag((e*area,.8*g*area,.8*g*area,
        g*(iy+iz),e*iy,e*iz)),np.eye(6),1e20,1e9)
    center=np.array((0.,.03,-.02)); m=rho*area
    skew=np.array(((0.,-center[2],center[1]),(center[2],0.,-center[0]),(-center[1],center[0],0.)))
    inertia=np.block([[m*np.eye(3),-m*skew],
                      [m*skew,rho*np.diag((iy+iz,iy,iz))-m*skew@skew]])
    beam=b3_ge.define_beam("b3-ge",17,(101,102,103),
        np.array(((0.,0.,.1),(.5,0.,.1),(1.,0.,.1))),np.tile(np.eye(3),(3,1,1)),section,inertia)
    shell=api.ShellPanel((1,2,3,4),((0.,0.,0.),(1.,0.,0.),(1.,1.,0.),(0.,1.,0.)),
                        ((1,2,3,4),),.02,e,nu,rho)
    return api.LinearDefinition(api.B3_GE_E4_PL_STIFFENED_SHELL_V1,shell,(beam,),
        (api.PointConnection(1,101),api.PointConnection(2,103)),
        () if free else tuple(api.FixedCoordinate("shell",n,k) for n in (1,4) for k in range(6)),
        modal_boundary_policy=api.GE_FREE_FREE_MODAL_ONLY_V1 if free else "supported")


def _input(free=False, **kwargs):
    return ExperimentalGEReferenceModalPanel.from_definition(_definition(free),
        api.GEReferenceModesRequest(2,1.),**kwargs)


def _project(panel):
    project=Project("explicit modal panel",mesh_only=True)
    record=AnalysisDefinition.experimental_point_panel("modal",panel)
    project.add_analysis(record)
    return project,record


def _reopen(tmp_path,panel,route):
    if route=="standalone":
        return load_case(save_case(panel,tmp_path/"modal.anyfem-ge.json")),None
    project,record=_project(panel)
    restored=load_project(save_project(project,tmp_path/"modal.anyfem")).analyses[record.id]
    return point_panel_from_analysis(restored),restored


@pytest.mark.parametrize("free",[False,True])
@pytest.mark.parametrize("route",["standalone","project"])
def test_save_reopen_owns_exact_modal_definition_without_owner_assembly(tmp_path,monkeypatch,free,route):
    panel=_input(free,model_identity="a"*64); expected=panel.to_dict()
    def forbidden(**kwargs): raise AssertionError("save/reopen must not assemble a global owner")
    monkeypatch.setattr(api,"create_analysis",forbidden)
    reopened,_=_reopen(tmp_path,panel,route)
    assert reopened.to_dict()==expected
    assert reopened.status=="EXPERIMENTAL_NOT_QUALIFIED"
    assert reopened.model_identity=="a"*64
    assert reopened.modal_boundary_policy==panel.modal_boundary_policy
    assert reopened.beams[0].raw==panel.beams[0].raw
    np.testing.assert_array_equal(reopened.beams[0].instantiate()[1],panel.beams[0].instantiate()[1])
    assert reopened.definition().modal_boundary_policy==panel.modal_boundary_policy


@pytest.mark.parametrize("free",[False,True])
@pytest.mark.parametrize("route",["standalone","project"])
def test_save_reopen_calls_public_modes_with_full_native_results(tmp_path,free,route):
    panel=_input(free); owner=panel.create_analysis()
    try:
        identity=owner.preflight()["identity"]
        expected=owner.modes(panel.request)
    finally: owner.close()
    panel=replace(panel,model_identity=identity)
    reopened,record=_reopen(tmp_path,panel,route)
    result=reopened.modes() if record is None else solve_point_panel(record)
    assert result.status==expected.status=="completed",result.failure_reason
    assert result.model_identity==identity
    if free:
        assert type(result) is api.GEFreeReferenceModesResult
        assert len(result.rigid_coordinates)==6
        assert result.rigid_flexible_mass_overlap<=1e-7
        result,expected=result.flexible,expected.flexible
    else: assert type(result) is api.GEReferenceModesResult
    np.testing.assert_allclose(result.frequencies_hz,expected.frequencies_hz,rtol=1e-10,atol=0.)
    assert result.factorization_count==1
    assert len(result.full_coordinates)==2
    assert max(result.equation_residuals)<=1e-8 and max(result.constraint_residuals)<=1e-9
    assert result.full_coordinates[0].shape==(66,) and result.multiplier_modes[0].shape==(12,)
    assert not result.full_coordinates[0].flags.writeable


@pytest.mark.parametrize("free",[False,True])
def test_modal_cancelled_result_is_preserved_through_project(tmp_path,free):
    _,record=_reopen(tmp_path,_input(free),"project")
    token=CancellationToken(); token.cancel("consumer cancellation")
    result=solve_point_panel(record,cancellation_token=token)
    assert result.status=="cancelled"
    flexible=result.flexible if free else result
    assert not flexible.full_coordinates and flexible.factorization_count==0


def test_owned_shell_payload_request_and_mutable_project_revalidation(tmp_path):
    definition=_definition()
    coordinates=[list(p) for p in definition.shell.coordinates_m]
    cells=[list(c) for c in definition.shell.q4_elements]
    shell=replace(definition.shell,coordinates_m=coordinates,q4_elements=cells)
    panel=ExperimentalGEReferenceModalPanel.from_definition(replace(definition,shell=shell),api.GEReferenceModesRequest())
    expected=panel.to_dict(); coordinates[0][0]=99.; cells[0][0]=99
    payload=panel.to_dict(); payload["shell"]["coordinates_m"][0][0]=55.
    assert panel.to_dict()==expected
    with pytest.raises(FrozenInstanceError): panel.request.count=8
    project,record=_project(panel)
    record.beam_formulation_options["panel"]["actions"]=[]
    with pytest.raises(ValueError): save_project(project,tmp_path/"bad.anyfem")


@pytest.mark.parametrize("extra",["actions","load_programme","lines","footprints","prestress","mass","export"])
def test_modal_schema_rejects_unknown_or_unverified_fields(extra):
    data=_input().to_dict(); data[extra]=[]
    with pytest.raises(ValueError): ExperimentalGEReferenceModalPanel.from_dict(data)
    with pytest.raises(ValueError): ExperimentalGEPointPanel.from_dict(data)


@pytest.mark.parametrize("kind",["schema","profile","policy","supports","s3","density","inertia_digest","request","identity"])
def test_corrupt_or_mismatched_modal_inputs_fail_closed(kind):
    data=_input().to_dict()
    if kind=="schema": data["schema"]="anyfem.experimental-ge-point-panel-v1"
    elif kind=="profile": data["profile"]="B2_E4_PL_FAST_LINEAR_V1"
    elif kind=="policy": data["modal_boundary_policy"]="free-free"
    elif kind=="supports": data["modal_boundary_policy"]=api.GE_FREE_FREE_MODAL_ONLY_V1
    elif kind=="s3": data["shell"]["s3_elements"]=[[1,2,3]]
    elif kind=="density": data["shell"]["density_kg_m3"]=0.
    elif kind=="inertia_digest": data["beams"][0]["sha256"]="0"*64
    elif kind=="request": data["request"]["count"]=True
    else: data["model_identity"]="A"*64
    with pytest.raises(ValueError): ExperimentalGEReferenceModalPanel.from_dict(data)


@pytest.mark.parametrize("kwargs",[{"targets":(1.,)},{"increment_control":object()},
    {"on_accepted":lambda r:None},{"on_case":lambda c:None},{"max_iterations":13}])
def test_project_nonlinear_controls_fail_before_creation(monkeypatch,kwargs):
    _,record=_project(_input())
    monkeypatch.setattr(api,"create_analysis",lambda **k:pytest.fail("must reject before owner"))
    with pytest.raises(ValueError,match="modes only"): solve_point_panel(record,**kwargs)


@pytest.mark.parametrize("bad_request",[{"count":2},None])
def test_explicit_typed_request_required(bad_request):
    with pytest.raises(ValueError): ExperimentalGEReferenceModalPanel.from_definition(_definition(),bad_request)


def test_new_solver_exports_and_incompatible_owner_close(monkeypatch):
    panel=_input(); monkeypatch.delattr(api,"GEReferenceModesRequest")
    with pytest.raises(ValueError,match="public"): ExperimentalGEReferenceModalPanel.from_dict(panel.to_dict())
    monkeypatch.undo()
    closed=[]
    owner=SimpleNamespace(reference_modal_capabilities={"operation":"modes","eligible":False},
                          modes=lambda request:None,close=lambda:closed.append(True))
    monkeypatch.setattr(api,"create_analysis",lambda **kwargs:owner)
    with pytest.raises(ValueError,match="does not admit"): panel.create_analysis()
    assert closed==[True]


def test_optional_model_identity_is_checked_and_incompatible_owner_closed(monkeypatch):
    panel=_input(model_identity="a"*64); closed=[]
    owner=SimpleNamespace(reference_modal_capabilities={"operation":"modes","eligible":True},
        modes=lambda request:None,preflight=lambda:{"identity":"b"*64},close=lambda:closed.append(True))
    monkeypatch.setattr(api,"create_analysis",lambda **kwargs:owner)
    with pytest.raises(ValueError,match="different solver model identity"): panel.create_analysis()
    assert closed==[True]


def test_from_definition_rejects_unverified_attachment_and_material_scope():
    definition=_definition()
    with pytest.raises(ValueError):
        ExperimentalGEReferenceModalPanel.from_definition(replace(definition,lines=(object(),)),api.GEReferenceModesRequest())
    with pytest.raises(ValueError):
        ExperimentalGEReferenceModalPanel.from_definition(replace(definition,footprints=(object(),)),api.GEReferenceModesRequest())
    if "distributing_footprints" in api.LinearDefinition.__dataclass_fields__:
        footprint = api.GEReferenceDistributingFootprint("unsupported-area", (1, 2), 102,
                                                        (0., 2., -.5, .5))
        with pytest.raises(ValueError, match="footprints unsupported"):
            ExperimentalGEReferenceModalPanel.from_definition(
                replace(definition, distributing_footprints=(footprint,)), api.GEReferenceModesRequest())
        if "finite_distributing_footprints" in api.LinearDefinition.__dataclass_fields__:
            with pytest.raises(ValueError, match="footprints unsupported"):
                ExperimentalGEReferenceModalPanel.from_definition(replace(definition,
                    finite_distributing_footprints=(api.GEFiniteDistributingFootprint(footprint),)),
                    api.GEReferenceModesRequest())
