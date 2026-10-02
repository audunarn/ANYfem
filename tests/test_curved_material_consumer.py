"""Explicit curved Q4 material input persistence through the public point facade."""
from dataclasses import replace
import copy

import pytest
from anymaterial import DNVC208MaterialCurve
from anysolver import b3_ge, stiffened_shell as ss

from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel, SCHEMA_CURVED_Q4, SCHEMA_CURVED_Q4_MATERIAL,
    load_case, save_case,
)
from test_curved_point_consumer import curved_case


def _material_case(j2=True):
    base = curved_case()
    beams = []
    for definition in base.beams:
        element, inertia = definition.instantiate()
        reference = element.operator.reference
        section = b3_ge.EllipsoidalGeneralizedSection(
            element.section.elastic, element.section.metric, 5., 1e8)
        beams.append(b3_ge.define_beam(
            "b3-ge", element.element_id, element.node_ids,
            reference.coordinates, reference.nodal_triads, section, inertia,
            quadrature=element.operator.order))
    shell = base.shell
    if j2:
        p = 1e6
        shell = replace(shell, j2_curve=DNVC208MaterialCurve(
            p, 1.05*p, 1.1*p, .005, .01, 4*p, .2))
    return replace(base, shell=shell, beams=tuple(beams),
                   shell_reference_policy=ss.CURVED_Q4_POINT_MATERIAL_REFERENCE_V1)


@pytest.mark.parametrize("j2", (False, True))
def test_curved_material_distinct_standalone_and_project_roundtrip(tmp_path, j2):
    old = curved_case()
    old_record = old.to_dict()
    assert old_record["schema"] == SCHEMA_CURVED_Q4
    assert old_record["shell_reference_policy"] == ss.CURVED_Q4_POINT_REFERENCE_V1
    panel = _material_case(j2)
    record = panel.to_dict()
    assert record["schema"] == SCHEMA_CURVED_Q4_MATERIAL
    assert record["shell_reference_policy"] == ss.CURVED_Q4_POINT_MATERIAL_REFERENCE_V1
    assert record["shell"]["j2_curve"] == (
        panel.shell.j2_curve.as_dict() if j2 else None)
    assert panel.definition().shell_reference_policy == ss.CURVED_Q4_POINT_MATERIAL_REFERENCE_V1
    standalone = load_case(save_case(panel, tmp_path / "curved-material.anyfem-ge.json"))
    assert standalone.to_dict() == record
    assert tuple(beam.raw for beam in standalone.beams) == tuple(beam.raw for beam in panel.beams)
    assert standalone.status == "EXPERIMENTAL_NOT_QUALIFIED"
    direct = standalone.create_analysis()
    try:
        identity = direct.preflight()["identity"]
    finally:
        direct.close()

    project = Project("curved point material", mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel("curved points", standalone)
    project.add_analysis(analysis)
    restored = load_project(save_project(project, tmp_path / "curved-material.anyfem"))
    stored = restored.analyses[analysis.id]
    assert stored.beam_formulation_options["panel"] == record
    owner = stored.create_point_panel_analysis()
    try:
        assert owner.preflight()["identity"] == identity
        assert owner.capabilities["curved_reference_policy"] == ss.CURVED_Q4_POINT_MATERIAL_REFERENCE_V1
        assert not owner.preflight()["production_qualified"]
    finally:
        owner.close()
    assert old.to_dict() == old_record


def test_curved_material_schema_policy_and_unsupported_inputs_fail_closed(monkeypatch):
    panel = _material_case()
    record = panel.to_dict()
    assert set(record["shell"]["j2_curve"]) == {
        "sigma_prop", "sigma_yield", "sigma_yield_2", "eps_p_y1", "eps_p_y2", "K", "n"}
    for mutate in (
        lambda row: row.update(schema=SCHEMA_CURVED_Q4),
        lambda row: row.update(shell_reference_policy=ss.CURVED_Q4_POINT_REFERENCE_V1),
        lambda row: row.pop("shell_reference_policy"),
        lambda row: row.update(q4_pullback_policy="historical_corotational"),
        lambda row: row["shell"].update(s3_elements=[[1, 2, 3]]),
        lambda row: row["shell"]["j2_curve"].update(extra=1.),
        lambda row: row.update(extra=True),
    ):
        changed = copy.deepcopy(record)
        mutate(changed)
        with pytest.raises(ValueError):
            ExperimentalGEPointPanel.from_dict(changed)
    with pytest.raises(ValueError, match="algorithmic Q4"):
        replace(curved_case(), shell=panel.shell)
    with pytest.raises(ValueError, match="translational"):
        replace(panel, actions=(ss.NodalAction("beam", 105, (0., 0., 0., 1., 0., 0.)),))
    with pytest.raises(ValueError, match="fixed nodal"):
        replace(panel, body_load=ss.GEBodyLoad("0"*64, (0., 0., -9.81)))
    monkeypatch.delattr(ss, "CURVED_Q4_POINT_MATERIAL_REFERENCE_V1")
    with pytest.raises(ValueError, match="curved schema"):
        ExperimentalGEPointPanel.from_dict(record)
