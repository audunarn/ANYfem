"""Input-only ANYfem workflow for the experimental B2/Q4 point panel."""

import json
from dataclasses import replace

import numpy as np
import pytest
from scipy import sparse

from anysolver import stiffened_shell
from anysolver.control import CancellationToken
from anyfem.solve.stiffened_shell_b2 import (
    ExperimentalB2PointPanel, STATUS, load_case, save_case,
)


def _panel():
    nodes = {
        1: (0., 0., 0.), 2: (1., 0., 0.), 3: (2., 0., 0.),
        4: (0., 1., 0.), 5: (1., 1., 0.), 6: (2., 1., 0.),
    }
    shells = (
        stiffened_shell.Q4PanelShell("left", (1, 2, 5, 4), 1000., .3, .15),
        stiffened_shell.Q4PanelShell("right", (2, 3, 6, 5), 1000., .3, .15),
    )
    section = stiffened_shell.LinearSection.isotropic(
        E=2000., nu=.25, area=.04, Iy=.00015, Iz=.0003,
        J=.0004, ky=.8, kz=.7)
    members = (
        stiffened_shell.PhysicalB2Member(
            "beam-left", "source-left", (10, 11),
            ((.2, .5, .2), (1.2, .5, .2)),
            (0., 1., 0.), "section", section),
        stiffened_shell.PhysicalB2Member(
            "beam-right", "source-right", (11, 12),
            ((1.2, .5, .2), (2.2, .5, .2)),
            (0., 1., 0.), "section", section),
    )
    points = (
        stiffened_shell.Q4B2Point(
            "joint-left", "left", "beam-left", .5, (0., .08, -.03)),
        stiffened_shell.Q4B2Point(
            "joint-right", "right", "beam-right", .5, (0., .08, -.03)),
    )
    fixed = set()
    for node_id in (1, 4):
        fixed.update(6*(node_id-1)+axis for axis in range(6))
    fixed.update(6*6+axis for axis in range(6))
    for node_id in (2, 3, 5, 6):
        fixed.add(6*(node_id-1)+5)
    free = [index for index in range(54) if index not in fixed]
    T = sparse.eye(54, format="csr")[:, free]
    return stiffened_shell.B2Q4PanelDefinition(
        stiffened_shell.B2_E4_PL_FAST_LINEAR_V1,
        nodes, shells, members, points, T, np.zeros(54))


@pytest.mark.parametrize("count", (1, 10, 100))
def test_b2_panel_save_reopen_and_selected_batch(tmp_path, count):
    definition = _panel()
    cases = []
    for index in range(count):
        load = np.zeros(54)
        load[6*8+1] = .08*(1.+index/count)
        load[6*5+2] = -.16*(1.+index/count)
        cases.append((f"load-{index:03d}", load))
    consumer = ExperimentalB2PointPanel.from_definition(
        definition, cases, selected_dofs=(6*8+1, 6*5+2),
        selected_points=("joint-right",),
        recover_members={"beam-right": (.25, .75)})
    assert consumer.status == STATUS
    destination = save_case(consumer, tmp_path / "panel.anyfem-b2.json")
    reopened = load_case(destination)
    assert reopened.payload == consumer.payload
    definition.transformation.data[:] = 0.
    cases[-1][1][:] = 0.
    result = reopened.solve_linear_many()
    assert result.status == "completed"
    assert len(result.cases) == count
    assert result.factorization_count == 1
    assert result.solve_count == count
    assert result.maximum_rhs_chunk == 1
    assert result.selected_points == ("joint-right",)
    assert all(len(case.selected_displacements) == 2 and
               len(case.selected_affine_reactions) == 2 and
               len(case.point_multipliers) == 1 and
               len(case.beam_resultants_local) == 1 and
               len(case.beam_resultants_local[0][1]) == 2
               for case in result.cases)
    owner = reopened.create_analysis()
    assert owner.capabilities.status == "experimental"
    owner.close()


def test_b2_panel_consumer_cancellation_and_bad_records(tmp_path):
    definition = _panel()
    load = np.zeros(54)
    load[6*8+1] = .08
    consumer = ExperimentalB2PointPanel.from_definition(
        definition, ((f"case-{index}", load) for index in range(10)),
        selected_dofs=(6*8+1,))
    token = CancellationToken()
    completed = []

    def after_case(case_id):
        completed.append(case_id)
        if len(completed) == 2:
            token.cancel("consumer prefix")

    result = consumer.solve_linear_many(
        cancellation_token=token, on_case=after_case)
    assert result.status == "cancelled"
    assert tuple(case.case_id for case in result.cases) == tuple(completed)
    assert result.solve_count == 2 and result.factorization_count == 1

    with pytest.raises(ValueError, match="filename"):
        save_case(consumer, tmp_path / "ordinary.anyfem")
    data = json.loads(consumer.payload)
    data["profile"] = stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1
    with pytest.raises(ValueError, match="profile"):
        ExperimentalB2PointPanel(json.dumps(data).encode("ascii"))
    data = json.loads(consumer.payload)
    data["affine"]["indices"][0] = 54
    with pytest.raises(ValueError, match="CSR"):
        ExperimentalB2PointPanel(json.dumps(data).encode("ascii"))
    with pytest.raises(ValueError, match="elastic physical"):
        ExperimentalB2PointPanel.from_definition(
            replace(definition, profile="wrong"), (("load", load),),
            selected_dofs=(0,))
    path = tmp_path / "bad.anyfem-b2.json"
    path.write_text('{"schema":1,"schema":2}', encoding="ascii")
    with pytest.raises(ValueError, match="duplicate"):
        load_case(path)


def test_b2_panel_consumer_preserves_generalized_section_identity(tmp_path):
    definition = _panel()
    generalized = stiffened_shell.LinearSection.generalized(
        definition.members[1].section.stiffness)
    members = (definition.members[0],
               replace(definition.members[1], section=generalized))
    definition = replace(definition, members=members)
    load = np.zeros(54)
    load[6*8+1] = .08
    case = ExperimentalB2PointPanel.from_definition(
        definition, (("load", load),), selected_dofs=(6*8+1,),
        recover_members={"beam-right": (.5,)})
    reopened = load_case(save_case(case, tmp_path / "generalized.anyfem-b2.json"))
    data = json.loads(reopened.payload)
    assert data["members"][1]["section"]["kind"] == "elastic_generalized"
    assert data["members"][1]["section"]["scalar_parameters"] is None
    result = reopened.solve_linear_many()
    assert result.status == "completed"
    assert result.factorization_count == result.solve_count == 1
    assert len(result.cases[0].beam_resultants_local[0][1]) == 1
