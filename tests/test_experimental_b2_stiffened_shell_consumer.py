"""Input-only ANYfem workflow for the experimental B2/Q4 point panel."""

import json
from dataclasses import replace

import numpy as np
import pytest
from scipy import sparse

from anysolver import stiffened_shell
from anysolver.control import CancellationToken
from anyfem.solve.stiffened_shell_b2 import (
    ExperimentalB2PointPanel, SCHEMA, SCHEMA_MASS, STATUS, load_case, save_case,
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


def test_b2_panel_consumer_rejects_shell_density_before_persistence():
    definition = _panel()
    shells = (replace(definition.shells[0], density_kg_m3=7850.),
              definition.shells[1])
    with pytest.raises(ValueError, match="shell density is unsupported"):
        ExperimentalB2PointPanel.from_definition(
            replace(definition, shells=shells),
            (("load", np.zeros(54)),), selected_dofs=(0,))


def _mass_panel():
    definition = _panel()
    mass = stiffened_shell.MassMetadata(
        "physical-section-mass", (0., .04, -.02),
        np.diag((.3, .3, .3, .003, .001, .002)))
    return replace(definition,
                   shells=tuple(replace(s, density_kg_m3=2.)
                                for s in definition.shells),
                   members=tuple(replace(m, mass=mass)
                                 for m in definition.members))


def _mass_case(definition=None):
    definition = _mass_panel() if definition is None else definition
    load = np.zeros(54)
    load[49] = .08
    return ExperimentalB2PointPanel.from_definition(
        definition, (("load", load),), selected_dofs=(49,),
        selected_points=("joint-right",),
        recover_members={"beam-right": (.25, .75)}, schema=SCHEMA_MASS)


def test_b2_mass_save_reopen_preserves_static_and_reference_modes(tmp_path):
    definition = _mass_panel()
    case = _mass_case(definition)
    assert case.schema == SCHEMA_MASS and case.status == STATUS
    record = json.loads(case.payload)
    assert record["shells"][0]["density_kg_m3"] == 2.
    assert record["members"][0]["mass"] == {
        "source_id": "physical-section-mass", "origin_local_m": [0., .04, -.02],
        "matrix_per_m": [list(r) for r in definition.members[0].mass.matrix_per_m],
    }
    reopened = load_case(save_case(case, tmp_path / "mass.anyfem-b2.json"))
    assert reopened.payload == case.payload
    request = stiffened_shell.B2ReferenceModesRequest(count=3)
    owner = stiffened_shell.create_analysis(definition=definition)
    try:
        expected = owner.modes(request)
    finally:
        owner.close()
    definition.transformation.data[:] = 0.
    definition.prescribed_offset[:] = 1.
    actual = reopened.modes(request)
    assert actual.status == expected.status == "ok"
    assert not actual.production_qualified
    np.testing.assert_allclose(actual.frequencies_hz, expected.frequencies_hz,
                               rtol=2e-8)
    # Eigenvector signs are arbitrary; compare each physical mode up to sign.
    for a, b in zip(actual.physical_modes, expected.physical_modes):
        sign = 1. if np.dot(a, b) >= 0. else -1.
        np.testing.assert_allclose(a, sign*np.asarray(b), rtol=2e-7, atol=1e-8)
    assert max(actual.equation_residuals) <= 1e-8
    assert max(actual.constraint_residuals) <= 1e-8
    assert actual.factorization_count == 1
    static = reopened.solve_linear_many()
    legacy = ExperimentalB2PointPanel.from_definition(
        _panel(), (("load", np.eye(54)[49]*.08),), selected_dofs=(49,),
        selected_points=("joint-right",), recover_members={"beam-right": (.25, .75)})
    assert static == legacy.solve_linear_many()
    assert legacy.schema == SCHEMA
    assert "mass" not in json.loads(legacy.payload)["members"][0]
    assert "density_kg_m3" not in json.loads(legacy.payload)["shells"][0]


@pytest.mark.parametrize("missing", ("beam", "shell"))
def test_b2_partial_mass_preserves_static_but_rejects_modes(missing):
    definition = _mass_panel()
    if missing == "beam":
        definition = replace(definition, members=(
            replace(definition.members[0], mass=None), definition.members[1]))
    else:
        definition = replace(definition, shells=(
            replace(definition.shells[0], density_kg_m3=None), definition.shells[1]))
    case = _mass_case(definition)
    assert case.solve_linear_many().status == "completed"
    with pytest.raises(ValueError, match="explicit shell density and beam physical mass"):
        case.modes(stiffened_shell.B2ReferenceModesRequest())


def test_b2_mass_affine_model_is_preserved_without_claiming_modal_support(tmp_path):
    definition = _mass_panel()
    transformation = definition.transformation.tolil()
    # One existing free row also depends on a second free coordinate.
    transformation[6, 1] = .1
    offset = np.zeros(54)
    offset[0] = .001
    definition = replace(definition, transformation=transformation.tocsr(),
                         prescribed_offset=offset)
    case = _mass_case(definition)
    reopened = load_case(save_case(case, tmp_path / "affine.anyfem-b2.json"))
    assert reopened.payload == case.payload
    assert reopened.solve_linear_many().status == "completed"
    with pytest.raises(ValueError, match="coordinate supports without MPCs"):
        reopened.modes(stiffened_shell.B2ReferenceModesRequest())


@pytest.mark.parametrize("corrupt", (
    "downgrade", "missing-density", "missing-mass", "unknown-mass",
    "bool-density", "nonfinite-density", "bad-mass", "short-matrix",
))
def test_b2_mass_schema_rejects_lossy_or_invalid_records(corrupt):
    record = json.loads(_mass_case().payload)
    if corrupt == "downgrade":
        record["schema"] = SCHEMA
    elif corrupt == "missing-density":
        del record["shells"][0]["density_kg_m3"]
    elif corrupt == "missing-mass":
        del record["members"][0]["mass"]
    elif corrupt == "unknown-mass":
        record["members"][0]["mass"]["density"] = 1.
    elif corrupt == "bool-density":
        record["shells"][0]["density_kg_m3"] = True
    elif corrupt == "nonfinite-density":
        record["shells"][0]["density_kg_m3"] = float("inf")
    elif corrupt == "bad-mass":
        record["members"][0]["mass"]["matrix_per_m"][0][0] = -1.
    else:
        record["members"][0]["mass"]["matrix_per_m"].pop()
    with pytest.raises(ValueError):
        ExperimentalB2PointPanel(json.dumps(record).encode("ascii"))


def test_b2_mass_modes_preserves_cancellation_and_closes_owner(monkeypatch):
    case = _mass_case()
    token = CancellationToken()
    token.cancel("consumer modal cancellation")
    result = case.modes(stiffened_shell.B2ReferenceModesRequest(), cancellation=token)
    assert result.status == "cancelled" and not result.frequencies_hz
    assert result.factorization_count == 0
    owners = []
    failure = stiffened_shell.B2ReferenceModesResult(
        "failed", (), (), (), (), (), 1, 0, "backend detail")

    class Owner:
        closed = False

        def modes(self, request, *, cancellation):
            assert request is control and cancellation is token
            if raises:
                raise RuntimeError("programming failure")
            return failure

        def close(self):
            self.closed = True

    def factory(*, definition):
        owner = Owner()
        owners.append(owner)
        return owner

    monkeypatch.setattr(stiffened_shell, "create_analysis", factory)
    control = stiffened_shell.B2ReferenceModesRequest()
    raises = False
    assert case.modes(control, cancellation=token) is failure
    assert owners[-1].closed
    raises = True
    with pytest.raises(RuntimeError, match="programming failure"):
        case.modes(control, cancellation=token)
    assert owners[-1].closed
