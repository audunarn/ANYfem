"""End-to-end headless construction and persistence of the GE point panel."""

import json
from dataclasses import replace

import numpy as np
import pytest
from anymaterial import DNVC208MaterialCurve


stiffened_shell = pytest.importorskip("anysolver.stiffened_shell")
from anysolver import b3_ge
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel, SCHEMA, SCHEMA_J2, SCHEMA_Q4_PULLBACK,
    load_case, save_case,
)


def _case():
    e = 210e9
    nu = .3
    g = e / (2 * (1 + nu))
    section = b3_ge.EllipsoidalGeneralizedSection(
        np.diag((e*.01, g*.008, g*.008, g*1.6666666666666667e-5,
                 e*8.333333333333333e-6, e*8.333333333333333e-6)),
        np.eye(6), 1e20, 1e9,
    )
    beam_points = [(x, -.5, .1) for x in (0., .5, 1., 1.5, 2.)]
    beams = tuple(b3_ge.define_beam(
        b3_ge.SELECTOR, i+1, (101+2*i, 102+2*i, 103+2*i),
        np.array(beam_points[2*i:2*i+3]), np.tile(np.eye(3), (3, 1, 1)),
        section, np.eye(6),
    ) for i in range(2))
    shell = stiffened_shell.ShellPanel(
        (1, 2, 3, 4, 5, 6),
        ((0., -.5, 0.), (1., -.5, 0.), (2., -.5, 0.),
         (0., .5, 0.), (1., .5, 0.), (2., .5, 0.)),
        ((1, 2, 5, 4), (2, 3, 6, 5)), .07, e, nu, 7850.,
    )
    return ExperimentalGEPointPanel(
        shell, beams,
        (stiffened_shell.PointConnection(1, 101),
         stiffened_shell.PointConnection(2, 103),
         stiffened_shell.PointConnection(3, 105)),
        tuple(stiffened_shell.FixedCoordinate("shell", node, axis)
              for node in (1, 4) for axis in range(6)),
        (stiffened_shell.NodalAction("beam", 105, (0., 0., 100., 0., 0., 0.)),),
    )


def test_ge_point_panel_constructs_solves_and_reopens(tmp_path):
    case = _case()
    path = save_case(case, tmp_path / "panel.anyfem-ge.json")
    reopened = load_case(path)
    assert reopened.status == "EXPERIMENTAL_NOT_QUALIFIED"
    assert reopened.to_dict() == case.to_dict()
    owner = reopened.create_analysis()
    assert owner.capabilities["status"] == "EXPERIMENTAL_NOT_QUALIFIED"
    assert owner.preflight()["production_qualified"] is False
    result = owner.solve_linear(reopened.actions)
    assert result.status == "completed"
    assert result.production_qualified is False
    assert result.free_equilibrium_ratio < 1e-8
    assert result.constraint_max < 1e-9
    assert result.beam_coordinates[6*4+2] > 0.
    owner.close()


def test_q4_j2_panel_round_trip_uses_distinct_experimental_schema(tmp_path):
    original = replace(_case(), actions=(stiffened_shell.NodalAction(
        "beam", 105, (0., 0., 1000., 0., 0., 0.)),))
    yielding_beams = []
    for definition in original.beams:
        element, inertia = definition.instantiate()
        section = b3_ge.EllipsoidalGeneralizedSection(
            element.section.elastic, element.section.metric, 4000., 1e8)
        yielding_beams.append(b3_ge.define_beam(
            b3_ge.SELECTOR, element.element_id, element.node_ids,
            element.operator.reference.coordinates,
            element.operator.reference.nodal_triads, section, inertia,
            quadrature=element.operator.order))
    original = replace(original, beams=tuple(yielding_beams))
    assert original.to_dict()["schema"] == SCHEMA
    assert "q4_pullback_policy" not in original.to_dict()
    curve = DNVC208MaterialCurve(1e6, 1.05e6, 1.1e6,
                                 .005, .01, 4e6, .2)
    case = replace(original, shell=replace(original.shell, j2_curve=curve))
    record = case.to_dict()
    assert record["schema"] == SCHEMA_J2
    assert "q4_pullback_policy" not in record
    reopened = load_case(save_case(case, tmp_path / "j2.anyfem-ge.json"))
    assert reopened.to_dict() == record
    assert reopened.shell.j2_curve == curve
    owner = reopened.create_analysis()
    assert owner.capabilities["shell"] == "experimental isotropic Q4 J2"
    assert owner.preflight()["production_qualified"] is False
    first = owner.solve(reopened.actions, (.5, 1.),
                        max_iterations=20, max_backtracks=12)
    assert first.status == "completed"
    assert max(max(row) for row in first.shell_layer_alpha) > 0.
    assert max(max(row) for row in first.beam_station_accumulated) > 0.
    assert len(first.shell_q4_material) == 2
    assert all(np.isfinite(item.layer_stress).all()
               for item in first.shell_q4_material)
    checkpoint = owner.checkpoint()
    owner.close()

    resumed = reopened.create_analysis()
    restored = resumed.restore(checkpoint)
    assert restored.shell_layer_alpha == first.shell_layer_alpha
    for original, repeated in zip(first.shell_q4_material,
                                  restored.shell_q4_material):
        np.testing.assert_allclose(original.layer_stress,
                                   repeated.layer_stress,
                                   rtol=1e-10, atol=1e-8)
    continued = resumed.solve(reopened.actions, (1.1,),
                              max_iterations=20, max_backtracks=12)
    assert continued.status == "completed"
    assert continued.accepted_factors == (.5, 1., 1.1)
    assert max(max(row) for row in continued.shell_layer_alpha) > 0.
    resumed.close()

    downgraded = dict(record)
    downgraded["schema"] = SCHEMA
    with pytest.raises(ValueError, match="strict GE shell"):
        ExperimentalGEPointPanel.from_dict(downgraded)


def test_q4_pullback_v3_round_trip_and_strict_selection(tmp_path):
    historical = _case()
    assert historical.to_dict()["schema"] == SCHEMA
    assert "q4_pullback_policy" not in historical.to_dict()
    assert historical.definition().q4_pullback_policy == "historical_corotational"
    selected = replace(
        historical,
        q4_pullback_policy=stiffened_shell.Q4_ALGORITHMIC_PULLBACK_V1,
    )
    record = selected.to_dict()
    assert record["schema"] == SCHEMA_Q4_PULLBACK
    assert record["q4_pullback_policy"] == stiffened_shell.Q4_ALGORITHMIC_PULLBACK_V1
    assert record["shell"]["j2_curve"] is None
    reopened = load_case(save_case(selected, tmp_path / "pullback.anyfem-ge.json"))
    assert reopened.to_dict() == record
    assert reopened.definition().q4_pullback_policy == stiffened_shell.Q4_ALGORITHMIC_PULLBACK_V1
    historical_owner = historical.create_analysis()
    selected_owner = reopened.create_analysis()
    try:
        assert (selected_owner.preflight()["identity"] !=
                historical_owner.preflight()["identity"])
        result = selected_owner.solve_linear(reopened.actions)
        assert result.status == "completed"
        checkpoint = selected_owner.checkpoint()
    finally:
        selected_owner.close()
        historical_owner.close()
    resumed = reopened.create_analysis()
    try:
        restored = resumed.restore(checkpoint)
        np.testing.assert_array_equal(restored.shell_displacement,
                                      result.shell_displacement)
        np.testing.assert_array_equal(restored.beam_coordinates,
                                      result.beam_coordinates)
        assert restored.generation == result.generation
    finally:
        resumed.close()

    curve = DNVC208MaterialCurve(1e6, 1.05e6, 1.1e6, .005, .01, 4e6, .2)
    with_j2 = replace(selected, shell=replace(selected.shell, j2_curve=curve))
    assert with_j2.to_dict()["schema"] == SCHEMA_Q4_PULLBACK
    assert ExperimentalGEPointPanel.from_dict(with_j2.to_dict()).shell.j2_curve == curve

    with pytest.raises(ValueError, match="Q4 pullback policy"):
        replace(selected, q4_pullback_policy="unknown")
    unknown = dict(record, q4_pullback_policy="unknown")
    with pytest.raises(ValueError, match="Q4 pullback policy"):
        ExperimentalGEPointPanel.from_dict(unknown)
    all_s3 = replace(historical.shell, q4_elements=(), s3_elements=((1, 2, 5), (1, 5, 4)))
    with pytest.raises(ValueError, match="at least one Q4"):
        replace(selected, shell=all_s3)

    injected = dict(historical.to_dict(), q4_pullback_policy=record["q4_pullback_policy"])
    with pytest.raises(ValueError, match="strict experimental GE panel"):
        ExperimentalGEPointPanel.from_dict(injected)
    invalid_v2 = with_j2.to_dict()
    invalid_v2["schema"] = SCHEMA_J2
    invalid_v2.pop("q4_pullback_policy")
    invalid_v2["shell"]["j2_curve"] = None
    with pytest.raises(ValueError, match="J2 curve required"):
        ExperimentalGEPointPanel.from_dict(invalid_v2)


def test_reopened_ge_panel_restores_and_continues_finite_accepted_state(tmp_path):
    case = replace(_case(), actions=(stiffened_shell.NodalAction(
        "beam", 105, (0., 0., 1000., 0., 0., 0.)),))
    reopened = load_case(save_case(case, tmp_path / "finite.anyfem-ge.json"))
    owner = reopened.create_analysis()
    first = owner.solve(reopened.actions, (.5, 1.))
    assert first.status == "completed"
    assert first.accepted_factors == (.5, 1.)
    assert first.free_equilibrium_ratio <= 1e-8
    assert first.constraint_max <= 1e-9
    saved = owner.checkpoint()
    owner.close()

    resumed = reopened.create_analysis()
    restored = resumed.restore(saved)
    np.testing.assert_array_equal(restored.beam_positions, first.beam_positions)
    continued = resumed.solve(reopened.actions, (2.,))
    assert continued.status == "completed"
    assert continued.accepted_factors == (.5, 1., 2.)
    assert continued.generation == first.generation+1
    assert continued.free_equilibrium_ratio <= 1e-8
    direct_owner = reopened.create_analysis()
    direct = direct_owner.solve(reopened.actions, (.5, 1., 2.))
    assert direct.accepted_factors == continued.accepted_factors
    for name in ("shell_displacement", "beam_positions", "cell_resultants",
                 "multipliers"):
        np.testing.assert_allclose(getattr(continued, name),
                                   getattr(direct, name), rtol=1e-10, atol=1e-11)
    np.testing.assert_allclose(continued.support_reactions,
                               direct.support_reactions, rtol=1e-9, atol=1e-6)
    direct_owner.close()
    resumed.close()


def test_ge_point_panel_rejects_changed_profile_and_native_beam(tmp_path):
    case = _case()
    with pytest.raises(ValueError, match="filename"):
        save_case(case, tmp_path / "ordinary.anyfem")
    data = case.to_dict()
    data["profile"] = "B2_E4_PL_FAST_LINEAR_V1"
    with pytest.raises(ValueError, match="profile"):
        ExperimentalGEPointPanel.from_dict(data)

    data = case.to_dict()
    data["beams"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        ExperimentalGEPointPanel.from_dict(data)

    path = tmp_path / "invalid.anyfem-ge.json"
    path.write_text('{"schema":1,"schema":2}', encoding="ascii")
    with pytest.raises(ValueError, match="duplicate"):
        load_case(path)

    data = case.to_dict()
    data["actions"][0]["domain"] = "line"
    path.write_text(json.dumps(data), encoding="ascii")
    with pytest.raises(ValueError, match="domain"):
        load_case(path)
