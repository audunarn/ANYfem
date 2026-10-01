"""Thin explicit static B2 footprint persistence and selected batch dispatch."""
import copy
from dataclasses import replace, FrozenInstanceError
import json
from types import SimpleNamespace

import numpy as np
import pytest
from scipy import sparse

from anysolver import stiffened_shell as api
from anysolver.control import CancellationToken
from anyfem.io.project_file import FORMAT_VERSION, save_project, load_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell_b2 import (
    ExperimentalB2PointPanel, ExperimentalB2NodalRigidFootprintPanel,
    SCHEMA, SCHEMA_NODAL_FOOTPRINT, STATUS, save_case, load_case,
)
from anyfem.solve.stiffened_shell_project import (
    B2_TYPE, B2_FORMULATION, point_panel_from_analysis, solve_point_panel,
)
from test_experimental_b2_stiffened_shell_consumer import _panel as _legacy_definition


def _definition():
    nodes = {1: (0., 0., 0.), 2: (1., 0., 0.), 3: (2., 0., 0.),
             4: (0., 1., 0.), 5: (1., 1., 0.), 6: (2., 1., 0.)}
    shells = (api.Q4PanelShell("left", (1, 2, 5, 4), 1000., .3, .15),
              api.Q4PanelShell("right", (2, 3, 6, 5), 1000., .3, .15))
    section = api.LinearSection.isotropic(E=2000., nu=.25, area=.04,
        Iy=.00015, Iz=.0003, J=.0004, ky=.8, kz=.7)
    members = tuple(api.PhysicalB2Member(f"beam-{i}", f"source-{i}",
        (10+i, 11+i), ((float(i), .5, .2), (float(i+1), .5, .2)),
        (0., 1., 0.), "section", section) for i in range(2))
    fixed = set(range(6)) | set(range(18, 24)) | set(range(36, 42))
    free = [i for i in range(54) if i not in fixed]
    return api.B2Q4PanelDefinition(api.B2_E4_PL_FAST_LINEAR_V1,
        nodes, shells, members, (), sparse.eye(54, format="csr")[:, free], np.zeros(54),
        (api.B2NodalRigidFootprint("right-patch", (2, 3, 6, 5), 11, ((2, 3, 6, 5),)),))


def _cases(count):
    cases = []
    for i in range(count):
        load = np.zeros(54); scale = 1.+i/count
        load[49] = 2.*scale; load[50] = -.5*scale
        load[51] = .1*scale; load[53] = .15*scale
        cases.append((f"physical-{i:03d}", load))
    return cases


def _input(count=3):
    return ExperimentalB2NodalRigidFootprintPanel.from_definition(
        _definition(), _cases(count), selected_dofs=(49, 50, 53),
        selected_footprints=api.B2NodalRigidFootprintSelection(("right-patch",)),
        recover_members={"beam-1": (.25, .75)})


def _project(panel):
    project = Project("explicit static B2 footprint", mesh_only=True)
    record = AnalysisDefinition.experimental_point_panel("footprint", panel)
    project.add_analysis(record)
    return project, record


def _reopened(tmp_path, panel, route):
    if route == "standalone":
        return load_case(save_case(panel, tmp_path/"footprint.anyfem-b2.json")), None
    project, analysis = _project(panel)
    path = save_project(project, tmp_path/"footprint.anyfem")
    assert json.loads(path.read_text())["anyfem"]["format"] == FORMAT_VERSION
    restored = load_project(path).analyses[analysis.id]
    assert restored.type == B2_TYPE and restored.beam_formulation == B2_FORMULATION
    return point_panel_from_analysis(restored), restored


def test_save_reopen_is_definition_only_and_preserves_all_fields(tmp_path, monkeypatch):
    panel = _input()
    expected = json.loads(panel.payload)
    def forbidden(**kwargs):
        raise AssertionError("input persistence assembled an owner")
    monkeypatch.setattr(api, "create_analysis", forbidden)
    for route in ("standalone", "project"):
        reopened, _ = _reopened(tmp_path, panel, route)
        assert type(reopened) is ExperimentalB2NodalRigidFootprintPanel
        assert reopened.payload == panel.payload
        assert reopened.status == STATUS and reopened.schema == SCHEMA_NODAL_FOOTPRINT
        assert json.loads(reopened.payload) == expected
    assert expected["points"] == []
    assert expected["footprints"][0] == json.loads(json.dumps(_definition().footprints[0].descriptor()))
    assert expected["selection"]["footprints"] == ["right-patch"]


@pytest.mark.parametrize("route", ("standalone", "project"))
@pytest.mark.parametrize("count", (1, 10, 100))
def test_actual_roundtrip_selected_batches_one_factor(tmp_path, route, count):
    panel = _input(count)
    reopened, record = _reopened(tmp_path, panel, route)
    result = (reopened.solve_linear_many() if record is None else solve_point_panel(record))
    assert result.status == "completed", result.failure_reason
    assert type(result) is api.ExperimentalB2Q4FootprintBatchResult
    assert result.factorization_count == 1 and result.solve_count == count
    assert result.selected_footprints == ("right-patch",)
    assert tuple(c.case_id for c in result.cases) == tuple(name for name, _ in _cases(count))
    first = result.cases[0]
    for i, case in enumerate(result.cases):
        np.testing.assert_allclose(case.selected_displacements,
            np.asarray(first.selected_displacements)*(1.+i/count), rtol=1e-8, atol=1e-12)
        assert case.point_multipliers == case.point_gaps == ()
        assert tuple(name for name, _ in case.beam_resultants_local) == ("beam-1",)
        group = case.footprint_wrenches[0]
        assert group.footprint_id == "right-patch"
        assert tuple(a.shell_node_id for a in group.node_actions) == (2, 3, 6, 5)
        assert all(a.beam_node_id == 11 for a in group.node_actions)
        np.testing.assert_allclose(group.shell_force_N, -np.asarray(group.beam_force_N), atol=1e-9)
        np.testing.assert_allclose(group.shell_moment_Nm, -np.asarray(group.beam_moment_Nm), atol=1e-9)
        assert group.production_qualified is False


@pytest.mark.parametrize("route", ("standalone", "project"))
def test_cancelled_prefix_keeps_complete_case_ids_and_group_actions(tmp_path, route):
    panel, record = _reopened(tmp_path, _input(), route)
    token = CancellationToken(); observed = []
    def after_case(case_id):
        observed.append(case_id)
        if len(observed) == 2:
            token.cancel()
    result = (panel.solve_linear_many(cancellation_token=token, on_case=after_case)
              if record is None else solve_point_panel(record, cancellation_token=token, on_case=after_case))
    assert result.status == "cancelled"
    assert tuple(c.case_id for c in result.cases) == tuple(observed) == ("physical-000", "physical-001")
    assert result.factorization_count == 1 and result.solve_count == 2
    assert all(len(c.footprint_wrenches) == 1 for c in result.cases)


def test_input_and_project_own_nested_mutable_payloads(tmp_path):
    definition = _definition(); cases = _cases(2)
    membership = [2, 3, 6, 5]; patch = [[2, 3, 6, 5]]
    group = api.B2NodalRigidFootprint("owned", membership, 11, patch)
    supplied = replace(definition, footprints=[group])
    panel = ExperimentalB2NodalRigidFootprintPanel.from_definition(supplied, cases,
        selected_dofs=(49,), selected_footprints=api.B2NodalRigidFootprintSelection(("owned",)), recover_members={})
    before = panel.payload
    membership.clear(); patch[0].clear(); supplied.footprints.clear()
    cases[0][1][:] = 999.; supplied.transformation.data[:] = 999.; supplied.prescribed_offset[:] = 999.
    assert panel.payload == before
    mutable = bytearray(before)
    with pytest.raises(ValueError): ExperimentalB2NodalRigidFootprintPanel(mutable)
    with pytest.raises(FrozenInstanceError): panel.payload = b"{}"
    project, record = _project(panel)
    detached = record.to_dict()
    detached["beam_formulation_options"]["panel"]["footprints"][0]["shell_nodes"][0] = 999
    assert point_panel_from_analysis(record).payload == before
    path = save_project(project, tmp_path/"owned.anyfem")
    assert point_panel_from_analysis(load_project(path).analyses[record.id]).payload == before


@pytest.mark.parametrize("defect", ("ge_law", "ge_profile", "schema", "unknown", "mass", "prestress", "staged", "finite", "selection", "duplicate_group", "empty_group", "configuration", "spin", "qualification"))
def test_strict_schema_rejects_unsupported_or_corrupt_payload(defect):
    data = json.loads(_input().payload)
    if defect == "ge_law": data["footprints"][0]["law"] = api.NodalRigidFootprint("ge", (2, 3, 6, 5), 11).descriptor()["law"]
    elif defect == "ge_profile": data["profile"] = api.B3_GE_E4_PL_STIFFENED_SHELL_V1
    elif defect == "schema": data["schema"] = SCHEMA
    elif defect == "unknown": data["unexpected"] = 1
    elif defect == "mass": data["members"][0]["mass"] = None
    elif defect == "prestress": data["prestress"] = []
    elif defect == "staged": data["load_programme"] = {}
    elif defect == "finite": data["targets"] = [.5, 1.]
    elif defect == "selection": data["selection"]["footprints"] = ["absent"]
    elif defect == "duplicate_group": data["footprints"].append(copy.deepcopy(data["footprints"][0]))
    elif defect == "empty_group": data["footprints"] = []
    elif defect == "configuration": data["footprints"][0]["configuration"] = "finite"
    elif defect == "spin": data["footprints"][0]["normal_spin"] = "released"
    else: data["footprints"][0]["production_qualified"] = True
    with pytest.raises(ValueError):
        ExperimentalB2NodalRigidFootprintPanel(json.dumps(data).encode("ascii"))


@pytest.mark.parametrize("shell_mass", (False, True))
def test_new_factory_rejects_mass_inputs(shell_mass):
    definition = _definition()
    if shell_mass:
        definition = replace(definition, shells=(replace(definition.shells[0], density_kg_m3=1.), definition.shells[1]))
    else:
        mass = api.MassMetadata("mass", (0., 0., 0.), tuple(tuple(row) for row in np.eye(6)))
        definition = replace(definition, members=(replace(definition.members[0], mass=mass), definition.members[1]))
    with pytest.raises(ValueError):
        ExperimentalB2NodalRigidFootprintPanel.from_definition(definition, _cases(1), selected_dofs=(49,))


def test_legacy_factory_rejects_footprints_and_keeps_historical_schema(tmp_path):
    legacy = _legacy_definition()
    consumer = ExperimentalB2PointPanel.from_definition(legacy, _cases(1), selected_dofs=(49,),
        selected_points=("joint-right",), recover_members={})
    assert consumer.payload == load_case(save_case(consumer, tmp_path/"old.anyfem-b2.json")).payload
    data = json.loads(consumer.payload)
    assert data["schema"] == SCHEMA
    assert "footprints" not in data and "footprints" not in data["selection"]
    for definition in (_definition(), replace(legacy, footprints=_definition().footprints)):
        with pytest.raises(ValueError, match="explicit footprint consumer schema"):
            ExperimentalB2PointPanel.from_definition(definition, _cases(1), selected_dofs=(49,))
    with pytest.raises(ValueError): ExperimentalB2PointPanel(_input().payload)
    with pytest.raises(ValueError): ExperimentalB2NodalRigidFootprintPanel(consumer.payload)


def test_missing_exports_fail_usefully_without_assembly(monkeypatch):
    payload = _input().payload
    monkeypatch.delattr(api, "B2NodalRigidFootprint")
    monkeypatch.setattr(api, "create_analysis", lambda **kwargs: pytest.fail("assembled incompatible solver"))
    with pytest.raises(ValueError, match="installed solver lacks public"):
        ExperimentalB2NodalRigidFootprintPanel(payload)


@pytest.mark.parametrize("defect", ("old_type", "law", "groups", "operations"))
def test_incompatible_owner_closed_before_publication(monkeypatch, defect):
    panel = _input(); groups = _definition().footprints
    cap = api.ExperimentalB2Q4FootprintCapabilities(footprints=groups)
    if defect == "old_type": cap = SimpleNamespace()
    elif defect == "law": cap = replace(cap, footprint_law_id="GE")
    elif defect == "groups": cap = replace(cap, footprints=())
    else: cap = replace(cap, operations=("solve_linear", "modes", "close"))
    closed = []
    monkeypatch.setattr(api, "create_analysis", lambda **kwargs: SimpleNamespace(capabilities=cap, close=lambda: closed.append(True)))
    with pytest.raises(ValueError, match="matching static B2 footprint capability"):
        panel.create_analysis()
    assert closed == [True]


def test_no_modal_finite_or_staged_consumer_dispatch(monkeypatch):
    panel = _input(); _, record = _project(panel)
    monkeypatch.setattr(api, "create_analysis", lambda **kwargs: pytest.fail("unsupported request assembled owner"))
    with pytest.raises(ValueError, match="static only"):
        panel.modes(api.B2ReferenceModesRequest())
    with pytest.raises(TypeError): panel.solve_linear_many(targets=(.5, 1.))
    with pytest.raises(ValueError): solve_point_panel(record, targets=(.5, 1.))
    with pytest.raises(ValueError): solve_point_panel(record, on_accepted=lambda result: None)


@pytest.mark.parametrize("defect", ("unknown_node", "point_overlap", "supported_rank"))
def test_core_retains_mechanical_admission(defect):
    definition = _definition()
    if defect == "unknown_node":
        definition = replace(definition, footprints=(replace(definition.footprints[0], beam_node=999),))
    elif defect == "point_overlap":
        definition = replace(definition, points=(api.Q4B2Point("point", "right", "beam-1", .5),))
    else:
        # Three selected, fully fixed shell poses cannot supply eighteen
        # independent rows through one free six-coordinate beam node.
        group = replace(definition.footprints[0], shell_nodes=(2, 3, 6), patch_cells=())
        fixed = {6*(n-1)+j for n in group.shell_nodes for j in range(6)}
        T = sparse.eye(54, format="csr")[:, [i for i in range(54) if i not in fixed]]
        definition = replace(definition, footprints=(group,), transformation=T)
    panel = ExperimentalB2NodalRigidFootprintPanel.from_definition(definition, _cases(1), selected_dofs=(49,))
    with pytest.raises(ValueError): panel.create_analysis()


def test_unselected_group_output_and_owner_finally_close(monkeypatch):
    panel = ExperimentalB2NodalRigidFootprintPanel.from_definition(_definition(), _cases(1),
        selected_dofs=(49,), recover_members={})
    received = []; closed = []
    sentinel = object()
    owner = SimpleNamespace(solve_linear_many=lambda *args, **kwargs: (received.append(kwargs), sentinel)[1],
                            close=lambda: closed.append(True))
    monkeypatch.setattr(ExperimentalB2NodalRigidFootprintPanel, "create_analysis", lambda self: owner)
    assert panel.solve_linear_many() is sentinel
    assert received[0]["selected_footprints"] is None
    assert closed == [True]
