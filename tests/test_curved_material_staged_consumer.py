"""Strict staged nodal input records for the curved Q4 material point owner."""
from dataclasses import replace
import copy

import pytest
from anysolver import stiffened_shell as ss

from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel, SCHEMA_CURVED_Q4, SCHEMA_CURVED_Q4_MATERIAL,
    SCHEMA_CURVED_Q4_MATERIAL_NATIVE, SCHEMA_CURVED_Q4_MATERIAL_STAGED,
    SCHEMA_STAGED, load_case, save_case,
)
from test_curved_material_consumer import _material_case
from test_curved_point_consumer import curved_case


def _staged_case(*, j2, native):
    base = replace(_material_case(j2), actions=(),
                   retained_scale_policy=("native_force_moment_roles_v1"
                                          if native else "profile"))
    owner = base.create_analysis()
    try:
        identity = owner.preflight()["identity"]
    finally:
        owner.close()
    programme = ss.StagedDeadLoadProgramme(identity, tuple(
        (ss.NodalAction("beam", 105, (0., 0., force, 0., 0., 0.)),)
        for force in (500., -500.)))
    return replace(base, load_programme=programme)


@pytest.mark.parametrize("j2,native", ((False, False), (True, False), (True, True)))
def test_curved_material_staged_standalone_and_project_roundtrip(tmp_path, j2, native):
    case = _staged_case(j2=j2, native=native)
    payload = case.to_dict()
    assert payload["schema"] == SCHEMA_CURVED_Q4_MATERIAL_STAGED
    assert payload["panel"]["schema"] == (
        SCHEMA_CURVED_Q4_MATERIAL_NATIVE if native else SCHEMA_CURVED_Q4_MATERIAL)
    assert payload["panel"]["shell"]["j2_curve"] == (
        case.shell.j2_curve.as_dict() if j2 else None)
    assert payload["panel"]["actions"] == []
    if native:
        assert payload["panel"]["retained_scale_policy"] == "native_force_moment_roles_v1"
    assert case.load_input is case.load_programme
    assert case.status == "EXPERIMENTAL_NOT_QUALIFIED"

    reopened = load_case(save_case(case, tmp_path / "curved-staged.anyfem-ge.json"))
    assert reopened.to_dict() == payload
    assert reopened.load_programme is not case.load_programme
    assert tuple(beam.raw for beam in reopened.beams) == tuple(beam.raw for beam in case.beams)
    owner = reopened.create_analysis()
    try:
        assert owner.preflight()["identity"] == reopened.load_programme.model_identity
        assert owner.capabilities["curved_reference_policy"] == ss.CURVED_Q4_POINT_MATERIAL_REFERENCE_V1
    finally:
        owner.close()

    project = Project("curved material staged input", mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel("curved staged", reopened)
    project.add_analysis(analysis)
    record = load_project(save_project(project, tmp_path / "curved-staged.anyfem")).analyses[analysis.id]
    assert record.beam_formulation_options["panel"] == payload
    project_owner = record.create_point_panel_analysis()
    try:
        assert project_owner.preflight()["identity"] == reopened.load_programme.model_identity
    finally:
        project_owner.close()


def test_curved_material_staged_strict_nesting_and_ownership(monkeypatch):
    case = _staged_case(j2=True, native=False)
    record = case.to_dict()
    for mutate in (
        lambda row: row.update(schema=SCHEMA_STAGED),
        lambda row: row.update(status="QUALIFIED"),
        lambda row: row.update(profile="wrong"),
        lambda row: row.update(extra=True),
        lambda row: row.pop("load_programme"),
        lambda row: row["panel"].update(schema=SCHEMA_CURVED_Q4),
        lambda row: row["panel"].update(actions=[dict(
            domain="beam", node=105, wrench=[0., 0., 1., 0., 0., 0.])]),
        lambda row: row["load_programme"].update(identity="0"*64),
    ):
        changed = copy.deepcopy(record)
        mutate(changed)
        with pytest.raises(ValueError):
            ExperimentalGEPointPanel.from_dict(changed)
    nested = copy.deepcopy(record)
    nested["panel"] = copy.deepcopy(record)
    with pytest.raises(ValueError):
        ExperimentalGEPointPanel.from_dict(nested)
    old = curved_case()
    with pytest.raises(ValueError, match="fixed nodal"):
        replace(old, actions=(), load_programme=case.load_programme)
    with pytest.raises(ValueError, match="staged"):
        replace(case, actions=old.actions)
    with pytest.raises(ValueError, match="fixed nodal"):
        replace(case, body_load=ss.GEBodyLoad("0"*64, (0., 0., -9.81)))

    class WrongOwner:
        closed = False

        def preflight(self):
            return {"identity": "0"*64}

        def close(self):
            self.closed = True

    wrong = WrongOwner()
    monkeypatch.setattr(ss, "create_analysis", lambda *, definition: wrong)
    with pytest.raises(ValueError, match="different panel model"):
        case.create_analysis()
    assert wrong.closed


def test_curved_material_staged_requires_installed_policy(monkeypatch):
    record = _staged_case(j2=False, native=False).to_dict()
    monkeypatch.delattr(ss, "CURVED_Q4_POINT_MATERIAL_REFERENCE_V1")
    with pytest.raises(ValueError, match="curved schema"):
        ExperimentalGEPointPanel.from_dict(record)
