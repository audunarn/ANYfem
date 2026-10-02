"""Strict experimental BL5 shell-J2/generalized-beam input consumption."""
from dataclasses import replace
import copy

import numpy as np
import pytest
from anymaterial import DNVC208MaterialCurve
from anysolver import stiffened_shell as ss
from anysolver.control import CancellationToken, SolveCancelled

from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEBL5Panel, SCHEMA_BL5, SCHEMA_BL5_STAGED_V2,
    SCHEMA_BL5_MATERIAL_V3, SCHEMA_BL5_COUPLED_MATERIAL_V4,
    load_case, save_case,
)
from anyfem.solve.stiffened_shell_project import bl5_panel_from_analysis
from test_bl5_material_consumer import _material_panel
from test_experimental_stiffened_shell_consumer import _bl5_case


def _coupled_panel():
    base = _material_panel()
    p = 1e6
    curve = DNVC208MaterialCurve(p, 1.05*p, 1.1*p, .005, .01, 4*p, .2)
    return replace(base, shell=replace(base.shell, j2_curve=curve),
                   bl5_material_policy=ss.BL5_COUPLED_J2_MATERIAL_V1)


def _programme(panel):
    owner = panel.create_analysis()
    try:
        identity = owner.preflight()["identity"]
    finally:
        owner.close()
    return ss.StagedDeadLoadProgramme(identity, tuple(
        (ss.NodalAction("beam", 105, (0., 0., force, 0., 0., 0.)),)
        for force in (500.,)))


def test_bl5_coupled_v4_strict_roundtrip_and_legacy_isolation(tmp_path):
    legacy = _bl5_case()
    objective = replace(legacy, q4_pullback_policy=ss.Q4_ALGORITHMIC_PULLBACK_V1)
    generalized = _material_panel()
    assert legacy.to_dict()["schema"] == SCHEMA_BL5
    assert objective.to_dict()["schema"] == SCHEMA_BL5_STAGED_V2
    assert generalized.to_dict()["schema"] == SCHEMA_BL5_MATERIAL_V3
    for case in (legacy, objective, generalized):
        assert "j2_curve" not in case.to_dict()["shell"]

    panel = _coupled_panel()
    record = panel.to_dict()
    assert record["schema"] == SCHEMA_BL5_COUPLED_MATERIAL_V4
    assert record["bl5_material_policy"] == ss.BL5_COUPLED_J2_MATERIAL_V1
    assert set(record["shell"]["j2_curve"]) == {
        "sigma_prop", "sigma_yield", "sigma_yield_2", "eps_p_y1",
        "eps_p_y2", "K", "n"}
    assert record["shell"]["j2_curve"] == panel.shell.j2_curve.as_dict()
    assert panel.definition().bl5_material_policy == ss.BL5_COUPLED_J2_MATERIAL_V1
    reopened = load_case(save_case(panel, tmp_path / "coupled.anyfem-ge.json"))
    assert reopened.to_dict() == record
    assert tuple(beam.raw for beam in reopened.beams) == tuple(beam.raw for beam in panel.beams)

    for mutate in (
        lambda row: row.update(schema=SCHEMA_BL5_MATERIAL_V3),
        lambda row: row.update(schema=SCHEMA_BL5_STAGED_V2),
        lambda row: row.pop("bl5_material_policy"),
        lambda row: row.update(bl5_material_policy=ss.BL5_GENERALIZED_MATERIAL_V1),
        lambda row: row["shell"].pop("j2_curve"),
        lambda row: row["shell"].update(j2_curve=None),
        lambda row: row["shell"]["j2_curve"].update(extra=1.),
        lambda row: row["shell"]["j2_curve"].update(K="4e6"),
        lambda row: row.update(q4_pullback_policy="historical_corotational"),
        lambda row: row.update(extra=True),
    ):
        changed = copy.deepcopy(record)
        mutate(changed)
        with pytest.raises(ValueError):
            ExperimentalGEBL5Panel.from_dict(changed)
    with pytest.raises(ValueError):
        replace(generalized, shell=panel.shell)
    with pytest.raises(ValueError):
        replace(panel, shell=generalized.shell)
    with pytest.raises(ValueError):
        replace(panel, q4_pullback_policy="historical_corotational")


def test_bl5_coupled_v4_distributed_envelope_has_one_base():
    panel = _coupled_panel()
    owner = panel.create_analysis()
    try:
        identity = owner.preflight()["identity"]
    finally:
        owner.close()
    load = ss.GEBeamDistributedLoad(identity, tuple(
        ss.GEBeamLineAction(i, (0., 0., 500.), (2., 3., 0.)) for i in (1, 2)))
    wrapped = replace(panel, distributed_load=load).to_dict()
    assert wrapped["panel"]["schema"] == SCHEMA_BL5_COUPLED_MATERIAL_V4
    assert ExperimentalGEBL5Panel.from_dict(wrapped).to_dict() == wrapped
    nested = copy.deepcopy(wrapped)
    nested["panel"] = copy.deepcopy(wrapped)
    with pytest.raises(ValueError):
        ExperimentalGEBL5Panel.from_dict(nested)


def test_bl5_coupled_v4_project_yields_rebases_and_restores(tmp_path, request):
    panel = _coupled_panel()
    panel = replace(panel, load_programme=_programme(panel))
    standalone = load_case(save_case(panel, tmp_path / "coupled.anyfem-ge.json"))
    assert standalone.to_dict() == panel.to_dict()
    assert standalone.load_input is standalone.load_programme
    assert standalone.load_programme is not panel.load_programme

    project = Project("coupled BL5 experimental", mesh_only=True)
    definition = AnalysisDefinition.experimental_bl5_panel("line", standalone)
    project.add_analysis(definition)
    reopened = load_project(save_project(project, tmp_path / "coupled.anyfem"))
    record = reopened.analyses[definition.id]
    assert bl5_panel_from_analysis(record).to_dict() == panel.to_dict()

    direct = record.create_bl5_panel_analysis()
    request.addfinalizer(direct.close)
    assert direct.capabilities["bl5_material_policy"] == ss.BL5_COUPLED_J2_MATERIAL_V1
    targets = (.5, 1.)
    control = ss.AdaptiveIncrementControl(4, 64)
    token = CancellationToken()

    def observe(result):
        assert result.free_equilibrium_ratio <= 1e-8
        assert result.constraint_max <= 1e-9
        if result.load_position == .5:
            token.cancel("coupled BL5 accepted prefix")

    with pytest.raises(SolveCancelled):
        direct.solve(standalone.load_input, targets, on_accepted=observe,
                     cancellation_token=token, increment_control=control,
                     max_iterations=20, max_backtracks=12)
    prefix = direct.recover()
    assert prefix.load_position == .5
    direct.rebase_rotations(prefix.generation)
    saved = direct.checkpoint()
    restored = record.create_bl5_panel_analysis()
    request.addfinalizer(restored.close)
    restored.restore(saved)
    assert restored.checkpoint() == saved
    final = direct.solve(standalone.load_input, targets, increment_control=control,
                         max_iterations=20, max_backtracks=12)
    resumed = restored.solve(standalone.load_input, targets, increment_control=control,
                             max_iterations=20, max_backtracks=12)
    assert final.load_programme_complete and resumed.load_programme_complete
    assert np.max(final.shell_layer_alpha) > 0.
    assert np.max(final.beam_station_accumulated) > 0.
    np.testing.assert_array_equal(final.shell_displacement, resumed.shell_displacement)
    np.testing.assert_array_equal(final.beam_station_accumulated,
                                  resumed.beam_station_accumulated)
    assert direct.checkpoint() == restored.checkpoint()

    # Continue the reopened public owner with signed displacement adaptation.
    # This fixture's last beam node is 105 at reference z=.0675 m.
    initial = float(final.beam_positions[-1, 2] - .0675)
    displacement_targets = (initial * 1.01, initial * 1.02)
    direction = (ss.NodalAction("beam", 105, (0., 0., 50., 0., 0., 0.)),)
    options = dict(displacement_control=ss.DisplacementControl("beam", 105, 2),
                   increment_control=ss.AdaptiveIncrementControl(2, 8),
                   max_iterations=20, max_backtracks=12)
    token = CancellationToken()
    with pytest.raises(SolveCancelled):
        direct.solve(direction, displacement_targets, **options,
                     cancellation_token=token,
                     on_accepted=lambda result: token.cancel("displacement prefix"))
    checkpoint = direct.checkpoint()
    resumed_owner = record.create_bl5_panel_analysis()
    request.addfinalizer(resumed_owner.close)
    resumed_owner.restore(checkpoint)
    assert resumed_owner.checkpoint() == checkpoint
    a = direct.solve(direction, displacement_targets, **options)
    b = resumed_owner.solve(direction, displacement_targets, **options)
    assert a.status == b.status == "completed"
    assert len(a.increment_attempts) == 2
    np.testing.assert_array_equal(a.beam_positions, b.beam_positions)
    assert direct.checkpoint() == resumed_owner.checkpoint()

    selection = ss.GERecoverySelection(beam_nodes=(105,), shell_nodes=(1,),
        shell_material_elements=(a.shell_q4_material[0].element_id,))
    picked = resumed_owner.recover(selection=selection)
    assert picked.generation == b.generation and picked.amplitude == b.amplitude
    np.testing.assert_array_equal(picked.beam_positions[0], b.beam_positions[-1])
    np.testing.assert_array_equal(picked.shell_material[0].layer_stress,
                                  b.shell_q4_material[0].layer_stress)
    assert resumed_owner.checkpoint() == direct.checkpoint()
