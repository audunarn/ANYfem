"""Native .anyfem persistence keeps explicit experimental mesh-native routes."""

import copy
import json

import numpy as np
import pytest
from scipy import sparse

from anysolver import b3_ge, stiffened_shell
from anysolver.control import CancellationToken
from anyfem.io.project_file import (
    FORMAT_VERSION, ProjectFileError, load_project, project_from_dict,
    project_to_dict, save_project,
)
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import ExperimentalGEPointPanel, ExperimentalGEBL5Panel
from anyfem.solve.stiffened_shell_b2 import ExperimentalB2PointPanel
from anyfem.solve.stiffened_shell_project import (
    B2_TYPE, GE_TYPE, STATUS, point_panel_from_analysis, solve_point_panel,
)


def _ge():
    section = b3_ge.EllipsoidalGeneralizedSection(
        np.diag((1e5, 8e4, 7e4, 1e4, 2e4, 3e4)), np.eye(6), 1e20, 1e9)
    angle = .2
    triad = np.array(((1., 0., 0.), (0., np.cos(angle), -np.sin(angle)),
                      (0., np.sin(angle), np.cos(angle))))
    beam = b3_ge.define_beam(
        b3_ge.SELECTOR, 71, (11, 12, 13),
        np.array(((0., -.5, .1), (.5, -.5, .1), (1., -.5, .1))),
        np.tile(triad, (3, 1, 1)), section, np.eye(6))
    shell = stiffened_shell.ShellPanel(
        (1, 2, 3, 4), ((0., -.5, 0.), (1., -.5, 0.),
                       (1., .5, 0.), (0., .5, 0.)),
        ((1, 2, 3, 4),), .07, 210e9, .3, 7850.)
    return ExperimentalGEPointPanel(
        shell, (beam,), (stiffened_shell.PointConnection(1, 11),
                        stiffened_shell.PointConnection(2, 13)),
        tuple(stiffened_shell.FixedCoordinate("shell", node, axis)
              for node in (1, 4) for axis in range(6)),
        (stiffened_shell.NodalAction("beam", 13, (0., 0., 10., 0., 0., 0.)),))


def _b2():
    section = stiffened_shell.LinearSection.isotropic(
        E=2000., nu=.25, area=.04, Iy=.00015, Iz=.0003,
        J=.0004, ky=.8, kz=.7)
    nodes = {1: (0., 0., 0.), 2: (1., 0., 0.), 3: (2., 0., 0.),
             4: (0., 1., 0.), 5: (1., 1., 0.), 6: (2., 1., 0.)}
    members = tuple(stiffened_shell.PhysicalB2Member(
        name, f"source-{name}", (10+i, 11+i),
        ((float(i), .5, .2), (float(i+1), .5, .2)),
        (0., 1./np.sqrt(1.04), .2/np.sqrt(1.04)), "section", section)
        for i, name in enumerate(("beam-left", "beam")))
    fixed = {6*(node-1)+axis for node in (1, 4) for axis in range(6)}
    fixed.update(range(36, 42))
    fixed.update(6*(node-1)+5 for node in (2, 3, 5, 6))
    T = sparse.eye(54, format="csr")[:, [i for i in range(54) if i not in fixed]]
    offset = np.zeros(54)
    offset[0] = .001
    definition = stiffened_shell.B2Q4PanelDefinition(
        stiffened_shell.B2_E4_PL_FAST_LINEAR_V1, nodes,
        (stiffened_shell.Q4PanelShell("plate-left", (1, 2, 5, 4), 1000., .3, .15),
         stiffened_shell.Q4PanelShell("plate", (2, 3, 6, 5), 1000., .3, .15)),
        members, (stiffened_shell.Q4B2Point("joint-left", "plate-left", "beam-left", .5,
                                           (0., .08, -.03)),
                  stiffened_shell.Q4B2Point("joint", "plate", "beam", .5,
                                           (0., .08, -.03))), T, offset)
    cases = []
    for index in range(3):
        load = np.zeros(54)
        load[49] = .08*(index+1)
        cases.append((f"physical-case-{index}", load))
    return ExperimentalB2PointPanel.from_definition(
        definition, cases, selected_dofs=(49, 32), selected_points=("joint",),
        recover_members={"beam": (.25, .75)})


def _project(panel):
    project = Project("explicit mesh-native panel", mesh_only=True)
    record = AnalysisDefinition.experimental_point_panel("panel", panel)
    project.add_analysis(record)
    return project, record


@pytest.mark.parametrize("kind", ("ge", "b2"))
def test_native_point_panel_roundtrip_and_public_solve(tmp_path, kind):
    panel = _ge() if kind == "ge" else _b2()
    project, record = _project(panel)
    before = record.to_dict()
    path = save_project(project, tmp_path / f"{kind}.anyfem")
    assert json.loads(path.read_text())["anyfem"]["format"] == FORMAT_VERSION
    reopened = load_project(path)
    restored = reopened.analyses[record.id]
    assert restored.to_dict() == before
    assert restored.target_kind == "none" and restored.target_id == ""
    assert restored.beam_formulation_options["status"] == STATUS
    assert reopened.mesh_only is True
    result = solve_point_panel(restored)
    assert result.status == "completed"
    assert result.production_qualified is False
    owner = restored.create_point_panel_analysis()
    try:
        if kind == "ge":
            assert restored.type == GE_TYPE
            rebuilt = point_panel_from_analysis(restored)
            assert rebuilt.to_dict() == panel.to_dict()
            assert rebuilt.beams[0].raw == panel.beams[0].raw
            assert rebuilt.beams[0].sha256 == panel.beams[0].sha256
            assert owner.capabilities["status"] == STATUS
            assert result.production_qualified is False
            assert result.free_equilibrium_ratio <= 1e-8
            assert result.constraint_max <= 1e-9
            assert np.linalg.norm(result.beam_coordinates) > 0.
        else:
            assert restored.type == B2_TYPE
            assert point_panel_from_analysis(restored).payload == panel.payload
            assert owner.capabilities.status == "experimental"
            assert tuple(case.case_id for case in result.cases) == tuple(
                f"physical-case-{i}" for i in range(3))
            assert result.factorization_count == 1 and result.solve_count == 3
            assert result.selected_points == ("joint",)
            assert all(len(case.selected_displacements) == 2 and
                       len(case.beam_resultants_local) == 1 for case in result.cases)
    finally:
        owner.close()


def test_native_b2_cancelled_prefix_after_reopen(tmp_path):
    project, record = _project(_b2())
    restored = load_project(save_project(project, tmp_path / "cancel.anyfem")).analyses[record.id]
    token = CancellationToken()
    completed = []

    def after_case(case_id):
        completed.append(case_id)
        if len(completed) == 2:
            token.cancel("native project prefix")

    result = solve_point_panel(restored, cancellation_token=token, on_case=after_case)
    assert result.status == "cancelled"
    assert tuple(case.case_id for case in result.cases) == tuple(completed)
    assert completed == ["physical-case-0", "physical-case-1"]
    assert result.solve_count == 2


@pytest.mark.parametrize("kind", ("ge", "b2"))
def test_native_point_panel_deep_ownership(kind):
    _, record = _project(_ge() if kind == "ge" else _b2())
    caller = record.to_dict()
    created = AnalysisDefinition.from_dict(caller)
    saved = created.to_dict()
    caller["beam_formulation_options"]["panel"]["profile"] = "corrupt"
    exported = created.to_dict()
    exported["beam_formulation_options"]["panel"]["profile"] = "also corrupt"
    assert created.to_dict() == saved


@pytest.mark.parametrize("kind", ("ge", "b2"))
@pytest.mark.parametrize("defect", (
    "type", "formulation", "target", "settings", "resource", "output",
    "outer-schema", "status", "source", "extra", "profile", "inner-schema",
    "native-or-affine", "load", "payload-object",
))
def test_native_corrupt_point_panels_fail_on_load_save_and_create(tmp_path, kind, defect):
    project, record = _project(_ge() if kind == "ge" else _b2())
    data = record.to_dict()
    payload = data["beam_formulation_options"]
    if defect == "type":
        data["type"] = B2_TYPE if kind == "ge" else GE_TYPE
    elif defect == "formulation":
        data["beam_formulation"] = "legacy-b3"
    elif defect == "target":
        data["target_kind"], data["target_id"] = "load_case", "default"
    elif defect == "settings":
        data["settings"] = {"analysis": "finite"}
    elif defect == "resource":
        data["resource_policy"] = {"threads": 8}
    elif defect == "output":
        data["output_request_ids"] = ["not-consumed"]
    elif defect == "outer-schema":
        payload["schema"] = "future"
    elif defect == "status":
        payload["status"] = "QUALIFIED"
    elif defect == "source":
        payload["definition_source"] = "project-geometry"
    elif defect == "extra":
        payload["fallback"] = True
    elif defect == "profile":
        payload["panel"]["profile"] = "other-profile"
    elif defect == "inner-schema":
        payload["panel"]["schema"] = "unsupported-bl5"
    elif defect == "native-or-affine":
        if kind == "ge":
            payload["panel"]["beams"][0]["sha256"] = "0"*64
        else:
            payload["panel"]["affine"]["shape"][0] += 1
    elif defect == "payload-object":
        data["beam_formulation_options"] = list(payload.items())
    elif kind == "ge":
        payload["panel"]["actions"][0]["wrench"] = [0.]*5
    else:
        payload["panel"]["cases"][1]["id"] = payload["panel"]["cases"][0]["id"]
    document = project_to_dict(project)
    document["analyses"] = [data]
    with pytest.raises(ProjectFileError):
        project_from_dict(document)
    # Analysis records are mutable: both save and materialization revalidate.
    for key, value in data.items():
        if key != "id":
            setattr(record, key, copy.deepcopy(value))
    with pytest.raises(ValueError):
        save_project(project, tmp_path / "invalid.anyfem")
    with pytest.raises(ValueError):
        record.create_point_panel_analysis()
    assert not (tmp_path / "invalid.anyfem").exists()


def test_other_consumers_and_legacy_routes_remain_explicit():
    with pytest.raises(ValueError, match="exact Experimental"):
        AnalysisDefinition.experimental_point_panel("BL5", object.__new__(ExperimentalGEBL5Panel))
    ordinary = AnalysisDefinition("ordinary")
    assert ordinary.beam_formulation == "legacy-b3"
    assert ordinary.beam_formulation_options == {}
    with pytest.raises(ValueError):
        ordinary.create_point_panel_analysis()
    with pytest.raises(ValueError, match="no case callback"):
        solve_point_panel(_project(_ge())[1], on_case=lambda _: None)
    # Missing selector fields in historical projects still mean legacy B3.
    legacy = Project("old")
    legacy.add_analysis(ordinary)
    data = project_to_dict(legacy)
    data["analyses"][0].pop("beam_formulation")
    data["analyses"][0].pop("beam_formulation_options")
    restored = project_from_dict(data).analyses[ordinary.id]
    assert restored.beam_formulation == "legacy-b3"
    assert restored.type == "linear_static"
