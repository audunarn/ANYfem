"""Experimental BL5 generalized-material input and native Project consumption."""
from dataclasses import replace
import copy

import numpy as np
import pytest
from anysolver import b3_ge, stiffened_shell as ss
from anysolver.control import CancellationToken, SolveCancelled

from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEBL5Panel, SCHEMA_BL5, SCHEMA_BL5_STAGED_V2,
    SCHEMA_BL5_MATERIAL_V3, load_case, save_case,
)
from anyfem.solve.stiffened_shell_project import bl5_panel_from_analysis
from test_experimental_stiffened_shell_consumer import _bl5_case


def _material_panel(basis="p0"):
    base = _bl5_case(basis)
    beams = []
    for definition in base.beams:
        element, inertia = definition.instantiate()
        section = b3_ge.EllipsoidalGeneralizedSection(
            element.section.elastic, element.section.metric, 5., 1e8)
        reference = element.operator.reference
        beams.append(b3_ge.define_beam(
            "b3-ge", element.element_id, element.node_ids,
            reference.coordinates, reference.nodal_triads, section, inertia,
            quadrature=element.operator.order))
    return replace(base, beams=tuple(beams), actions=(),
                   q4_pullback_policy=ss.Q4_ALGORITHMIC_PULLBACK_V1,
                   bl5_material_policy=ss.BL5_GENERALIZED_MATERIAL_V1)


def _programme(panel):
    owner = panel.create_analysis()
    try:
        identity = owner.preflight()["identity"]
    finally:
        owner.close()
    return ss.StagedDeadLoadProgramme(identity, tuple(
        (ss.NodalAction("beam", 105, (0., 0., force, 0., 0., 0.)),)
        for force in (100., -100.)))


def test_bl5_material_v3_strict_schema_and_default_isolation(tmp_path, monkeypatch):
    legacy = _bl5_case()
    selected_elastic = replace(legacy, q4_pullback_policy=ss.Q4_ALGORITHMIC_PULLBACK_V1)
    assert legacy.to_dict()["schema"] == SCHEMA_BL5
    assert selected_elastic.to_dict()["schema"] == SCHEMA_BL5_STAGED_V2
    assert "bl5_material_policy" not in legacy.to_dict()
    assert "bl5_material_policy" not in selected_elastic.to_dict()

    panel = _material_panel()
    record = panel.to_dict()
    assert record["schema"] == SCHEMA_BL5_MATERIAL_V3
    assert record["bl5_material_policy"] == ss.BL5_GENERALIZED_MATERIAL_V1
    assert record["load_programme"] is None
    assert panel.definition().bl5_material_policy == ss.BL5_GENERALIZED_MATERIAL_V1
    reopened = load_case(save_case(panel, tmp_path / "material.anyfem-ge.json"))
    assert reopened.to_dict() == record
    assert tuple(beam.raw for beam in reopened.beams) == tuple(beam.raw for beam in panel.beams)
    owner = reopened.create_analysis()
    try:
        assert owner.capabilities["bl5_material_policy"] == ss.BL5_GENERALIZED_MATERIAL_V1
        assert "generalized B3 plastic finite BL5" not in owner.capabilities["excluded"]
    finally:
        owner.close()

    for mutate in (
        lambda row: row.update(schema=SCHEMA_BL5_STAGED_V2),
        lambda row: row.update(schema=SCHEMA_BL5),
        lambda row: row.pop("bl5_material_policy"),
        lambda row: row.update(bl5_material_policy="elastic"),
        lambda row: row.update(bl5_material_policy="unknown"),
        lambda row: row.update(q4_pullback_policy="historical_corotational"),
        lambda row: row.update(extra=True),
    ):
        changed = copy.deepcopy(record)
        mutate(changed)
        with pytest.raises(ValueError):
            ExperimentalGEBL5Panel.from_dict(changed)
    with pytest.raises(ValueError):
        replace(panel, bl5_material_policy="unknown")
    with pytest.raises(ValueError):
        replace(panel, q4_pullback_policy="historical_corotational")
    with pytest.raises(ValueError):
        replace(panel, shell=replace(panel.shell, s3_elements=((1, 2, 3),)))

    class WrongOwner:
        closed = False

        def preflight(self):
            return {"identity": "0" * 64}

        def close(self):
            self.closed = True

    bound = replace(panel, load_programme=_programme(panel))
    wrong = WrongOwner()
    monkeypatch.setattr(ss, "create_analysis", lambda *, definition: wrong)
    with pytest.raises(ValueError, match="different panel model"):
        bound.create_analysis()
    assert wrong.closed


def test_bl5_material_v3_distributed_envelope_is_single_and_strict():
    panel = _material_panel()
    owner = panel.create_analysis()
    try:
        identity = owner.preflight()["identity"]
    finally:
        owner.close()
    load = ss.GEBeamDistributedLoad(identity, tuple(
        ss.GEBeamLineAction(i, (0., 0., 100.), (2., 3., 0.)) for i in (1, 2)))
    panel = replace(panel, distributed_load=load)
    record = panel.to_dict()
    assert record["panel"]["schema"] == SCHEMA_BL5_MATERIAL_V3
    assert ExperimentalGEBL5Panel.from_dict(record).to_dict() == record
    changed = copy.deepcopy(record)
    changed["panel"] = copy.deepcopy(record)
    with pytest.raises(ValueError):
        ExperimentalGEBL5Panel.from_dict(changed)
    changed = copy.deepcopy(record)
    changed["panel"]["bl5_material_policy"] = "elastic"
    with pytest.raises(ValueError):
        ExperimentalGEBL5Panel.from_dict(changed)


@pytest.mark.parametrize("basis", ("p0", "continuous_p1_coarse"))
def test_bl5_material_project_reopens_yielded_programme_and_checkpoint(
        tmp_path, request, basis):
    panel = _material_panel(basis)
    panel = replace(panel, load_programme=_programme(panel))
    standalone = load_case(save_case(panel, tmp_path / "material.anyfem-ge.json"))
    assert standalone.to_dict() == panel.to_dict()
    assert standalone.load_programme is not panel.load_programme
    assert standalone.load_input is standalone.load_programme

    project = Project("BL5 generalized material", mesh_only=True)
    definition = AnalysisDefinition.experimental_bl5_panel("line", standalone)
    project.add_analysis(definition)
    reopened = load_project(save_project(project, tmp_path / "material.anyfem"))
    record = reopened.analyses[definition.id]
    assert bl5_panel_from_analysis(record).to_dict() == panel.to_dict()

    direct = record.create_bl5_panel_analysis()
    request.addfinalizer(direct.close)
    targets = (.5, 1., 1.25, 1.75, 2.)
    control = ss.AdaptiveIncrementControl(4, 64)
    token = CancellationToken()

    def observe(result):
        assert result.free_equilibrium_ratio <= 1e-8
        assert result.constraint_max <= 1e-9
        if result.load_position == 1.:
            token.cancel("BL5 generalized material accepted prefix")

    with pytest.raises(SolveCancelled):
        direct.solve(standalone.load_input, targets, cancellation_token=token,
                     on_accepted=observe, increment_control=control,
                     max_iterations=20, max_backtracks=12)
    prefix = direct.recover()
    assert prefix.load_position == 1.
    assert np.max(prefix.beam_station_accumulated) > 0.
    direct.rebase_rotations(prefix.generation)
    saved = direct.checkpoint()
    restored = record.create_bl5_panel_analysis()
    request.addfinalizer(restored.close)
    restored.restore(saved)
    assert restored.checkpoint() == saved
    finished = direct.solve(standalone.load_input, targets,
                            increment_control=control, max_iterations=20,
                            max_backtracks=12)
    resumed = restored.solve(standalone.load_input, targets,
                             increment_control=control, max_iterations=20,
                             max_backtracks=12)
    assert finished.load_programme_complete and resumed.load_programme_complete
    assert np.max(finished.beam_station_accumulated) > np.max(prefix.beam_station_accumulated)
    np.testing.assert_array_equal(finished.beam_station_accumulated,
                                  resumed.beam_station_accumulated)
    np.testing.assert_array_equal(finished.beam_positions, resumed.beam_positions)
    assert direct.checkpoint() == restored.checkpoint()
