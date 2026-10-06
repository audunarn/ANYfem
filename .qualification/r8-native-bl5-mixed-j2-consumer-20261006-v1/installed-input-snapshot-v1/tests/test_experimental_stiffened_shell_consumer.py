"""End-to-end headless construction and persistence of the GE point panel."""

import json
from dataclasses import replace

import numpy as np
import pytest
from anymaterial import DNVC208MaterialCurve


stiffened_shell = pytest.importorskip("anysolver.stiffened_shell")
from anysolver import b3_ge
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel, ExperimentalGEBL5Panel,
    SCHEMA, SCHEMA_J2, SCHEMA_Q4_PULLBACK, SCHEMA_BL5,
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


@pytest.mark.parametrize('mixed', (False, True))
def test_physical_fibre_reference_linear_inputs_reopen_and_restart(tmp_path,mixed):
    case = _case()
    e, g = 210e9, 210e9/2.6
    arm = np.sqrt(8.333333333333333e-6/.01)
    section = b3_ge.PhysicalFibreSection(
        tuple(b3_ge.Fibre(str(i),y*arm,z*arm,.0025,e,
                         b3_ge.FlowCurve((0.,),(1e20,),1e9))
              for i,(y,z) in enumerate(((-1,-1),(-1,1),(1,-1),(1,1)))),
        np.diag(np.sqrt((0.,g*.008,g*.008,g*1.6666666666666667e-5,0.,0.))))
    beams = []
    for i,definition in enumerate(case.beams):
        element,inertia = definition.instantiate()
        reference = element.operator.reference
        beams.append(definition if mixed and i == 0 else b3_ge.define_beam(
            'b3-ge',element.element_id,element.node_ids,reference.coordinates,
            reference.nodal_triads,section,inertia,quadrature=element.operator.order))
    case = replace(case,beams=tuple(beams))
    reopened = load_case(save_case(case,tmp_path/'fibre.anyfem-ge.json'))
    assert reopened.to_dict() == case.to_dict()
    assert tuple(beam.raw for beam in reopened.beams) == tuple(beam.raw for beam in case.beams)
    assert reopened.to_dict()['schema'] == SCHEMA
    owner = reopened.create_analysis()
    assert owner.capabilities['analysis'] == (
        'reference_linear', 'experimental_native_fibre_finite_point')
    result = owner.solve_linear(reopened.actions)
    assert result.status == 'completed'
    assert result.free_equilibrium_ratio <= 1e-8 and result.constraint_max <= 1e-9
    saved = owner.checkpoint()
    continued = reopened.create_analysis()
    restored = continued.restore(saved)
    np.testing.assert_array_equal(restored.beam_coordinates,result.beam_coordinates)
    actions = tuple(replace(a,wrench=tuple(2*v for v in a.wrench)) for a in reopened.actions)
    second = continued.solve_linear(actions)
    np.testing.assert_allclose(second.beam_coordinates,2*result.beam_coordinates,
                               rtol=1e-8,atol=1e-10)
    assert continued.preflight()['reference_linear_factorizations'] == 1
    (tmp_path/'consumer-result.json').write_text(json.dumps({
        'mixed':mixed,'schema':SCHEMA,
        'beam_sha256':[beam.sha256 for beam in reopened.beams],
        'families':owner.capabilities['beam_section_families'],
        'free_equilibrium_ratio':result.free_equilibrium_ratio,
        'constraint_max':result.constraint_max,
        'tip_z_m':float(result.beam_coordinates[6*4+2]),
        'continued_tip_z_m':float(second.beam_coordinates[6*4+2]),
        'reference_linear_factorizations':continued.preflight()['reference_linear_factorizations'],
    },indent=2))
    owner.close()
    continued.close()


@pytest.mark.parametrize('mixed', (False, True))
def test_physical_fibre_finite_input_reopens_and_preserves_native_history(tmp_path, mixed):
    case = _case()
    e, g = 210e9, 210e9/2.6
    arm = np.sqrt(8.333333333333333e-6/.01)
    section = b3_ge.PhysicalFibreSection(
        tuple(b3_ge.Fibre(str(i), y*arm, z*arm, .0025, e,
                           b3_ge.FlowCurve((0.,), (1e5 if i == 0 else 1e20,), 1e9))
              for i, (y, z) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1)))),
        np.diag(np.sqrt((0., g*.008, g*.008, g*1.6666666666666667e-5, 0., 0.))))
    beams = []
    for i, definition in enumerate(case.beams):
        element, inertia = definition.instantiate()
        reference = element.operator.reference
        beams.append(definition if mixed and i == 0 else b3_ge.define_beam(
            'b3-ge', element.element_id, element.node_ids, reference.coordinates,
            reference.nodal_triads, section, inertia, quadrature=element.operator.order))
    actions = (stiffened_shell.NodalAction('beam', 105,
                                          (0., 0., 1000., 0., 0., 0.)),)
    case = replace(case, beams=tuple(beams), actions=actions)
    reopened = load_case(save_case(case, tmp_path / 'finite-fibre.anyfem-ge.json'))
    assert reopened.status == 'EXPERIMENTAL_NOT_QUALIFIED'
    assert reopened.to_dict() == case.to_dict()
    assert reopened.to_dict()['schema'] == SCHEMA
    assert tuple(beam.raw for beam in reopened.beams) == tuple(beam.raw for beam in case.beams)
    assert tuple(beam.sha256 for beam in reopened.beams) == tuple(
        beam.sha256 for beam in case.beams)

    owner = reopened.create_analysis()
    resumed = reopened.create_analysis()
    try:
        assert owner.capabilities['analysis'] == (
            'reference_linear', 'experimental_native_fibre_finite_point')
        first = owner.solve(reopened.actions, targets=(.5, 1.), max_iterations=18)
        assert first.status == 'completed' and first.production_qualified is False
        assert first.accepted_factors == (.5, 1.)
        assert first.free_equilibrium_ratio <= 1e-8
        assert first.constraint_max <= 1e-9
        assert max(value for row in first.beam_station_accumulated for value in row) > 0.
        assert first.beam_fibre_material
        assert any(sum(f.plastic_increment) != 0.
                   for station in first.beam_fibre_material for f in station.fibres)
        saved = owner.checkpoint()
        restored = resumed.restore(saved)
        assert resumed.checkpoint() == saved
        assert restored.beam_station_accumulated == first.beam_station_accumulated
        for actual, expected in zip(restored.beam_fibre_material, first.beam_fibre_material):
            assert actual.fibres == expected.fibres
            np.testing.assert_array_equal(actual.strain, expected.strain)
            np.testing.assert_array_equal(actual.resultants, expected.resultants)
        unloaded = resumed.solve(reopened.actions, targets=(.5,), max_iterations=18)
        direct = owner.solve(reopened.actions, targets=(.5,), max_iterations=18)
        assert unloaded.accepted_factors == direct.accepted_factors == (.5, 1., .5)
        assert unloaded.beam_station_accumulated == direct.beam_station_accumulated
        np.testing.assert_array_equal(unloaded.cell_resultants, direct.cell_resultants)
        assert resumed.checkpoint() == owner.checkpoint()
    finally:
        owner.close()
        resumed.close()


def _j2_fibre_case(mixed, triangles):
    case = _case()
    e, g = 210e9, 210e9/2.6
    arm = np.sqrt(8.333333333333333e-6/.01)
    section = b3_ge.PhysicalFibreSection(
        tuple(b3_ge.Fibre(str(i), y*arm, z*arm, .0025, e,
                           b3_ge.FlowCurve((0.,),
                               (1e20 if triangles or i != 0 else 1e5,), 1e9))
              for i, (y, z) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1)))),
        np.diag(np.sqrt((0., g*.008, g*.008, g*1.6666666666666667e-5, 0., 0.))))
    beams = []
    for i, definition in enumerate(case.beams):
        element, inertia = definition.instantiate()
        reference = element.operator.reference
        beams.append(definition if mixed and i == 0 else b3_ge.define_beam(
            'b3-ge', element.element_id, element.node_ids, reference.coordinates,
            reference.nodal_triads, section, inertia, quadrature=element.operator.order))
    shell = case.shell
    if triangles:
        shell = replace(shell, q4_elements=(),
                        s3_elements=((1, 2, 5), (1, 5, 4), (2, 3, 6), (2, 6, 5)))
    pressure = 20_000. if triangles else 1e6
    curve = DNVC208MaterialCurve(pressure, 1.05*pressure, 1.1*pressure,
                                .005, .01, 4.*pressure, .2)
    return replace(case, shell=replace(shell, j2_curve=curve), beams=tuple(beams),
                   actions=(stiffened_shell.NodalAction(
                       'beam', 105, (0., 0., 1000., 0., 0., 0.)),))


@pytest.mark.parametrize('mixed,triangles', ((False, False), (True, False), (True, True)))
def test_j2_native_fibre_input_reopens_v8_and_continues(tmp_path, mixed, triangles):
    case = _j2_fibre_case(mixed, triangles)
    reopened = load_case(save_case(case, tmp_path / 'j2-fibre.anyfem-ge.json'))
    assert reopened.status == 'EXPERIMENTAL_NOT_QUALIFIED'
    assert reopened.to_dict() == case.to_dict()
    assert reopened.to_dict()['schema'] == SCHEMA_J2
    assert tuple(beam.raw for beam in reopened.beams) == tuple(beam.raw for beam in case.beams)
    assert tuple(beam.sha256 for beam in reopened.beams) == tuple(
        beam.sha256 for beam in case.beams)

    owner = reopened.create_analysis()
    resumed = reopened.create_analysis()
    targets = (.05,) if triangles else (.5, 1.)
    following = .055 if triangles else .5
    try:
        assert owner.capabilities['status'] == 'EXPERIMENTAL_NOT_QUALIFIED'
        assert owner.capabilities['analysis'] == (
            'reference_linear', 'experimental_native_fibre_finite_point')
        assert ('RESULTANT_ELLIPSOID' in owner.capabilities['beam_section_families']) == mixed
        first = owner.solve(reopened.actions, targets=targets,
                            max_iterations=20, max_backtracks=12)
        assert first.status == 'completed' and first.production_qualified is False
        assert first.accepted_factors == targets
        assert first.free_equilibrium_ratio <= 1e-8
        assert first.constraint_max <= 1e-9
        assert len(first.beam_fibre_energy) == (1 if mixed else 2)
        if triangles:
            assert max(max(row) for row in first.shell_layer_alpha) > 0.
            assert all(sum(row.incremental_dissipation_j) == 0.
                       for row in first.beam_fibre_energy)
        else:
            # Q4 J2 is defined, but this fixture yields only the beam fibre.
            assert max(max(row) for row in first.shell_layer_alpha) == 0.
            assert any(sum(row.incremental_dissipation_j) > 0.
                       for row in first.beam_fibre_energy)
        saved = owner.checkpoint()
        assert saved.startswith(b'ANYSGECP8\0')
        restored = resumed.restore(saved)
        assert resumed.checkpoint() == saved
        assert restored.shell_layer_alpha == first.shell_layer_alpha
        assert restored.beam_fibre_energy == first.beam_fibre_energy
        for name in ('shell_displacement', 'cell_resultants', 'support_reactions'):
            np.testing.assert_array_equal(getattr(restored, name), getattr(first, name))
        for original, repeated in zip(first.shell_q4_material+first.shell_s3_material,
                                      restored.shell_q4_material+restored.shell_s3_material):
            for name in ('layer_strain', 'layer_stress', 'plastic_strain', 'alpha'):
                np.testing.assert_array_equal(getattr(original, name),
                                              getattr(repeated, name))
        continued = resumed.solve(reopened.actions, targets=(following,),
                                  max_iterations=20, max_backtracks=12)
        direct = owner.solve(reopened.actions, targets=(following,),
                             max_iterations=20, max_backtracks=12)
        assert continued.accepted_factors == direct.accepted_factors == targets+(following,)
        assert continued.beam_fibre_energy == direct.beam_fibre_energy
        assert continued.shell_layer_alpha == direct.shell_layer_alpha
        np.testing.assert_array_equal(continued.cell_resultants, direct.cell_resultants)
        assert resumed.checkpoint() == owner.checkpoint()
    finally:
        owner.close()
        resumed.close()


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


def test_elastic_s3_native_fibre_reopens_v9_and_continues(tmp_path):
    case = _j2_fibre_case(False, True)
    case = replace(case, shell=replace(case.shell, j2_curve=None),
                   actions=(stiffened_shell.NodalAction(
                       'beam',105,(0.,0.,1000.,0.,0.,0.)),))
    reopened = load_case(save_case(case,tmp_path/'elastic-s3-fibre.anyfem-ge.json'))
    assert reopened.to_dict() == case.to_dict()
    owner = reopened.create_analysis()
    resumed = reopened.create_analysis()
    try:
        first = owner.solve(reopened.actions,targets=(.5,1.))
        assert first.accepted_factors == (.5,1.)
        assert first.free_equilibrium_ratio <= 1e-8 and first.constraint_max <= 1e-9
        saved = owner.checkpoint()
        resumed.restore(saved)
        assert resumed.checkpoint() == saved
        assert owner._finite_driver._stable_s3 and resumed._finite_driver._stable_s3
        direct = owner.solve(reopened.actions,targets=(.5,))
        continued = resumed.solve(reopened.actions,targets=(.5,))
        assert direct.accepted_factors == continued.accepted_factors == (.5,1.,.5)
        assert owner.checkpoint() == resumed.checkpoint()
        np.testing.assert_array_equal(direct.cell_resultants,continued.cell_resultants)
    finally:
        owner.close()
        resumed.close()


def test_yielded_s3_adaptive_history_cancels_restores_and_completes(tmp_path,monkeypatch):
    import anysolver._stiffened_shell_ge_finite_experimental as finite
    from anysolver.control import CancellationToken,SolveCancelled
    case = _j2_fibre_case(False,True)
    beams=[]
    for native in case.beams:
        element,inertia=native.instantiate()
        section=b3_ge.PhysicalFibreSection(tuple(replace(fibre,
            curve=b3_ge.FlowCurve((0.,),(1e5,),1e9)) if i==0 else fibre
            for i,fibre in enumerate(element.section.fibres)),element.section.background_factor)
        reference=element.operator.reference
        beams.append(b3_ge.define_beam('b3-ge',element.element_id,element.node_ids,
            reference.coordinates,reference.nodal_triads,section,inertia,
            quadrature=element.operator.order))
    case=replace(case,shell=replace(case.shell,j2_curve=None),beams=tuple(beams),
        actions=(stiffened_shell.NodalAction('beam',105,(0.,0.,1000.,0.,0.,0.)),))
    reopened=load_case(save_case(case,tmp_path/'adaptive-s3.anyfem-ge.json'))
    assert reopened.to_dict()==case.to_dict()
    owner=reopened.create_analysis();resumed=reopened.create_analysis()
    control=stiffened_shell.AdaptiveIncrementControl(4,24)
    targets=(.5,1.,.5);token=CancellationToken()
    original=finite.cancellation_safe_point
    def cancel(token_value,stage):
        if owner._generation==1 and stage=='stiffened-shell.finite.increment':
            token.cancel('yielded adaptive prefix')
        return original(token_value,stage)
    try:
        with monkeypatch.context() as context:
            context.setattr(finite,'cancellation_safe_point',cancel)
            with pytest.raises(SolveCancelled,match='yielded adaptive prefix'):
                owner.solve(reopened.actions,targets=targets,increment_control=control,
                            cancellation_token=token)
        prefix=owner.recover()
        assert prefix.status=='accepted_prefix' and prefix.accepted_factors==(.5,)
        assert max(v for row in prefix.beam_station_accumulated for v in row)>0.
        saved=owner.checkpoint();restored=resumed.restore(saved)
        assert resumed.checkpoint()==saved
        assert restored.increment_attempts==prefix.increment_attempts
        direct=owner.solve(reopened.actions,targets=targets,increment_control=control)
        continued=resumed.solve(reopened.actions,targets=targets,increment_control=control)
        assert direct.status==continued.status=='completed'
        assert direct.accepted_factors==continued.accepted_factors==(.5,1.,.75,.5)
        assert any(row.status=='rejected' for row in direct.increment_attempts)
        assert direct.increment_attempts==continued.increment_attempts
        assert direct.beam_fibre_energy==continued.beam_fibre_energy
        assert owner.checkpoint()==resumed.checkpoint()
    finally:
        owner.close();resumed.close()


def _bl5_case(basis="p0", multiplier_span=1):
    """Same bounded nonmatching geometry as the public solver history example."""
    e, nu = 210e9, .3
    g = e / (2 * (1 + nu))
    section = b3_ge.EllipsoidalGeneralizedSection(
        np.diag((e*.01, g*.008, g*.008, g*1.6666666666666667e-5,
                 e*8.333333333333333e-6, e*8.333333333333333e-6)),
        np.eye(6), 1e20, 1e9)
    beam_points = np.array([(x, 0., .0675) for x in (0., .25, .5, .75, 1.)])
    beams = tuple(b3_ge.define_beam(
        b3_ge.SELECTOR, i+1, (101+2*i, 102+2*i, 103+2*i),
        beam_points[2*i:2*i+3], np.tile(np.eye(3), (3, 1, 1)),
        section, np.eye(6)) for i in range(2))
    # Native continuous-P1 requires a shell trace at least as fine as the beam.
    xs = (0., .25, .5, .75, 1.) if basis == "continuous_p1" else (0., 1/3, 2/3, 1.)
    n = len(xs)
    shell = stiffened_shell.ShellPanel(
        tuple(range(1, 2*n+1)), tuple((x, y, 0.) for y in (0., .2) for x in xs),
        tuple((i, i+1, n+i+1, n+i) for i in range(1, n)), .035, e, nu, 7850.)
    line = stiffened_shell.ExperimentalBL5Line(
        tuple(range(1, n+1)), ((0, 0), (0, 1), (1, 0), (1, 1)),
        multiplier_span=multiplier_span, basis=basis)
    fixed = tuple(stiffened_shell.FixedCoordinate("shell", node, component)
                  for node in (1, n+1) for component in range(6))
    return ExperimentalGEBL5Panel(shell, beams, (line,), fixed,
        (stiffened_shell.NodalAction("beam", 105, (0., 0., 100., 0., 0., 0.)),))


@pytest.mark.parametrize("basis,span", (
    ("p0", 1), ("p0", 2), ("continuous_p1", 1), ("continuous_p1_coarse", 1)))
def test_bl5_input_round_trip_preserves_explicit_public_controls(tmp_path, basis, span):
    case = _bl5_case(basis, span)
    record = case.to_dict()
    assert record["schema"] == SCHEMA_BL5
    assert record["lines"][0]["basis"] == basis
    assert record["lines"][0]["multiplier_span"] == span
    assert "points" not in record and "q4_pullback_policy" not in record
    reopened = load_case(save_case(case, tmp_path / "bl5.anyfem-ge.json"))
    assert type(reopened) is ExperimentalGEBL5Panel
    assert reopened.to_dict() == record
    assert reopened.status == "EXPERIMENTAL_NOT_QUALIFIED"
    assert tuple(beam.raw for beam in reopened.beams) == tuple(beam.raw for beam in case.beams)
    definition = reopened.definition()
    assert definition.points == () and definition.lines == case.lines
    assert definition.q4_pullback_policy == "historical_corotational"
    historical = _case()
    assert historical.to_dict()["schema"] == SCHEMA
    assert "lines" not in historical.to_dict()


@pytest.mark.parametrize("basis", ("p0", "continuous_p1_coarse"))
def test_bl5_reopened_input_loads_unloads_restores_and_continues(tmp_path, basis):
    case = _bl5_case(basis)
    path = save_case(case, tmp_path / "bl5-history.anyfem-ge.json")
    reopened = load_case(path)
    owner = reopened.create_analysis()
    resumed = load_case(path).create_analysis()
    try:
        first = owner.solve(reopened.actions, targets=(.5, 1., .75))
        assert first.status == "completed" and first.accepted_factors == (.5, 1., .75)
        assert not first.production_qualified
        assert owner.capabilities["status"] == "EXPERIMENTAL_NOT_QUALIFIED"
        assert first.free_equilibrium_ratio <= 1e-8 and first.constraint_max <= 1e-9
        assert first.beam_shell_line_work
        checkpoint = owner.checkpoint()
        restored = resumed.restore(checkpoint)
        assert resumed.checkpoint() == checkpoint
        assert restored.accepted_factors == first.accepted_factors
        direct = owner.solve(reopened.actions, targets=(.9,))
        continued = resumed.solve(reopened.actions, targets=(.9,))
        assert direct.status == continued.status == "completed"
        assert direct.accepted_factors == continued.accepted_factors == (.5, 1., .75, .9)
        assert direct.free_equilibrium_ratio <= 1e-8 and direct.constraint_max <= 1e-9
        assert owner.checkpoint() == resumed.checkpoint()
        for name in ("shell_displacement", "beam_positions", "beam_frames", "cell_frames",
                     "cell_resultants", "multipliers", "support_reactions"):
            np.testing.assert_array_equal(getattr(direct, name), getattr(continued, name))
        assert load_case(path).to_dict() == case.to_dict()
    finally:
        owner.close()
        resumed.close()


def test_bl5_input_rejects_unsupported_combinations_and_schema_injection(tmp_path):
    case = _bl5_case()
    with pytest.raises(ValueError, match="pure Q4 elastic"):
        replace(case, shell=replace(case.shell, s3_elements=((1, 2, 6),)))
    curve = DNVC208MaterialCurve(1e6, 1.05e6, 1.1e6, .005, .01, 4e6, .2)
    with pytest.raises(ValueError, match="pure Q4 elastic"):
        replace(case, shell=replace(case.shell, j2_curve=curve))
    with pytest.raises(ValueError, match="generalized B3 sections"):
        replace(case, beams=_j2_fibre_case(False, False).beams)
    with pytest.raises(ValueError, match="BL5 lines"):
        replace(case, lines=())
    with pytest.raises(ValueError, match="strict experimental GE panel"):
        ExperimentalGEPointPanel.from_dict(case.to_dict())
    with pytest.raises(ValueError, match="strict experimental BL5 panel"):
        ExperimentalGEBL5Panel.from_dict(_case().to_dict())

    mutations = (
        (lambda record: record.update(points=[]), "strict experimental BL5 panel"),
        (lambda record: record.update(q4_pullback_policy="historical_corotational"),
         "strict experimental BL5 panel"),
        (lambda record: record.update(schema=SCHEMA), "strict experimental GE panel"),
        (lambda record: record.update(profile="changed-profile"), "profile"),
        (lambda record: record["beams"][0].update(sha256="0" * 64), "hash"),
        (lambda record: record["lines"][0].update(multiplier_span=True), "positive integer"),
        (lambda record: record["lines"][0].update(beam_halfcells=[[True, 0], [0, 1]]),
         "half-cell pairs"),
        (lambda record: record["lines"][0].update(expected_beam_arm=[0., 0., False]),
         "finite numeric"),
        (lambda record: record["lines"][0].update(basis="unselected"), "BL5 line"),
    )
    path = tmp_path / "invalid-bl5.anyfem-ge.json"
    for mutate, message in mutations:
        record = case.to_dict()
        mutate(record)
        path.write_text(json.dumps(record), encoding="ascii")
        with pytest.raises(ValueError, match=message):
            load_case(path)
