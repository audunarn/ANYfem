"""Explicit footprint input/Project persistence, not connection qualification."""
from copy import deepcopy
from dataclasses import replace
import json

import numpy as np
import pytest

from anysolver import stiffened_shell as ss
from anyfem.io.project_file import save_project, load_project, FORMAT_VERSION
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel, ExperimentalGENodalRigidFootprintPanel,
    SCHEMA_NODAL_FOOTPRINT, STATUS, save_case, load_case,
)
from anyfem.solve.stiffened_shell_project import (
    GE_TYPE, GE_FORMULATION, point_panel_from_analysis, solve_point_panel,
)
from test_experimental_stiffened_shell_consumer import _case, _j2_fibre_case


def _footprint(mixed=False):
    base = _case()
    patch = ((2, 3, 6), (2, 6, 5)) if mixed else ((2, 3, 6, 5),)
    shell = replace(base.shell, q4_elements=((1, 2, 5, 4),) if mixed else base.shell.q4_elements,
                    s3_elements=patch if mixed else ())
    if mixed == "all-s3":
        shell = replace(shell, q4_elements=(), s3_elements=((1, 2, 5), (1, 5, 4))+patch)
    panel = replace(base, shell=shell, points=(base.points[0],),
                    q4_pullback_policy=ss.Q4_ALGORITHMIC_PULLBACK_V1 if shell.q4_elements else "historical_corotational",
                    actions=(ss.NodalAction("beam", 105, (0., 0., 10., 0., 0., 0.)),))
    return ExperimentalGENodalRigidFootprintPanel(panel,
        (ss.NodalRigidFootprint("tip-pad", (2, 3, 6, 5), 105, patch),))


@pytest.mark.parametrize("mixed", (False, True, "all-s3"))
def test_strict_input_and_project_roundtrip_preserve_groups_without_assembly(tmp_path, monkeypatch, mixed):
    source = _footprint(mixed); before = source.to_dict()
    def forbidden(**kwargs):raise AssertionError("definition persistence assembled an owner")
    monkeypatch.setattr(ss, "create_analysis", forbidden)
    path = save_case(source, tmp_path/"footprint.anyfem-ge.json")
    restored = load_case(path)
    assert type(restored) is ExperimentalGENodalRigidFootprintPanel
    assert restored.to_dict() == before and restored.status == STATUS
    assert restored.footprints == source.footprints
    assert restored.definition().footprints == source.footprints
    assert restored.definition().lines == ()
    project = Project("explicit footprint", mesh_only=True)
    record = AnalysisDefinition.experimental_point_panel("footprint", restored)
    project.add_analysis(record)
    filename = save_project(project, tmp_path/"footprint.anyfem")
    assert json.loads(filename.read_text())["anyfem"]["format"] == FORMAT_VERSION
    reopened = load_project(filename); analysis = reopened.analyses[record.id]
    assert analysis.type == GE_TYPE and analysis.beam_formulation == GE_FORMULATION
    assert analysis.target_kind == "none" and analysis.target_id == ""
    assert point_panel_from_analysis(analysis).to_dict() == before
    assert source.panel.to_dict() == before["panel"]
    # Nested mutation cannot alter the already canonicalized analysis input.
    before["footprints"][0]["shell_nodes"][0] = 999
    assert analysis.beam_formulation_options["panel"]["footprints"][0]["shell_nodes"][0] == 2


@pytest.mark.parametrize("case", ("unknown", "lines", "law", "semantics", "chart", "profile", "empty", "duplicate", "nested-extra", "material", "policy", "bad-cell", "wrong-schema"))
def test_strict_footprint_schema_rejects_unsupported_or_corrupt_payload(case):
    record = deepcopy(_footprint().to_dict())
    if case == "unknown":record["unexpected"] = True
    elif case == "lines":record["lines"] = []
    elif case == "law":record["footprints"][0]["law"] = "weld"
    elif case == "semantics":record["footprints"][0]["semantics"] = "continuous_area"
    elif case == "chart":record["admission"] = "rebased"
    elif case == "profile":record["profile"] = "legacy"
    elif case == "empty":record["footprints"] = []
    elif case == "duplicate":record["footprints"].append(deepcopy(record["footprints"][0]))
    elif case == "nested-extra":record["panel"]["footprints"] = record["footprints"]
    elif case == "material":record["panel"]["shell"]["j2_curve"] = {"unsupported": True}
    elif case == "policy":record["panel"]["q4_pullback_policy"] = "historical_corotational"
    elif case == "bad-cell":record["footprints"][0]["patch_cells"] = [[2, 3]]
    else:record["panel"]["schema"] = "anyfem.experimental-ge-bl5-panel-v1"
    with pytest.raises(ValueError):ExperimentalGENodalRigidFootprintPanel.from_dict(record)


def test_legacy_point_schema_is_unchanged_and_rejects_footprint_fields():
    panel = _case(); before = panel.to_dict()
    assert ExperimentalGEPointPanel.from_dict(before).to_dict() == before
    record = deepcopy(before); record["footprints"] = _footprint().to_dict()["footprints"]
    with pytest.raises(ValueError):ExperimentalGEPointPanel.from_dict(record)


@pytest.mark.parametrize("kind", ("shell-j2", "fibre", "staged"))
def test_valid_unsupported_material_or_programme_combinations_reject(kind):
    source = _footprint(); panel = source.panel
    if kind == "shell-j2":
        declared = _j2_fibre_case(False, False)
        panel = replace(panel, shell=replace(panel.shell, j2_curve=declared.shell.j2_curve))
    elif kind == "fibre":panel = replace(panel, beams=_j2_fibre_case(False, False).beams)
    else:
        programme = ss.StagedDeadLoadProgramme("ab"*32, (panel.actions,
            (ss.NodalAction("beam", 105, (0., 0., 20., 0., 0., 0.)),)))
        panel = replace(panel, actions=(), load_programme=programme)
    with pytest.raises(ValueError):ExperimentalGENodalRigidFootprintPanel(panel, source.footprints)


def test_public_factory_requires_unrebased_policy_and_closes_unsupported_owner(monkeypatch):
    panel = _footprint(); seen = []
    class Owner:
        def preflight(self):return {"identity": "unused"}
        def close(self):seen.append("closed")
    def factory(*, definition):
        assert definition.footprints == panel.footprints and definition.lines == ()
        return Owner()
    monkeypatch.setattr(ss, "create_analysis", factory)
    with pytest.raises(ValueError, match="unrebased admission"):panel.create_analysis()
    assert seen == ["closed"]


def test_project_dispatch_preserves_explicit_linear_and_finite_selection(monkeypatch):
    panel = _footprint(); analysis = AnalysisDefinition.experimental_point_panel("footprint", panel)
    calls = []
    class Owner:
        def solve_linear(self, actions, **kwargs):calls.append(("linear", actions, kwargs));return "linear-result"
        def solve(self, actions, targets, **kwargs):calls.append(("finite", actions, targets, kwargs));return "finite-result"
        def close(self):calls.append(("closed",))
    monkeypatch.setattr(ExperimentalGENodalRigidFootprintPanel, "create_analysis", lambda self: Owner())
    assert solve_point_panel(analysis) == "linear-result"
    assert solve_point_panel(analysis, targets=(.25, .5), max_iterations=20, max_backtracks=12) == "finite-result"
    assert calls[0][1] == panel.actions and calls[2][1:3] == (panel.actions, (.25, .5))
    assert calls[-1] == ("closed",)
    with pytest.raises(ValueError):solve_point_panel(analysis, max_iterations=20)
    with pytest.raises(ValueError):solve_point_panel(analysis, on_case=lambda value: None)


def test_q4_public_linear_finite_restart_and_selected_recovery(tmp_path, request):
    panel = _footprint(); path = save_case(panel, tmp_path/"footprint.anyfem-ge.json")
    project = Project("footprint owner", mesh_only=True)
    record = AnalysisDefinition.experimental_point_panel("footprint", load_case(path)); project.add_analysis(record)
    reopened = load_project(save_project(project, tmp_path/"footprint.anyfem"))
    analysis = reopened.analyses[record.id]; restored_input = point_panel_from_analysis(analysis)
    def fresh():
        owner = analysis.create_point_panel_analysis(); request.addfinalizer(owner.close); return owner
    linear = fresh(); linear_result = linear.solve_linear(restored_input.actions)
    group, = linear.recover(selection=restored_input.recovery_selection)
    assert group.footprint_id == "tip-pad" and group.shell_node_ids == (2, 3, 6, 5)
    assert len(group.point_wrenches) == 4 and not group.production_qualified
    assert linear_result.free_equilibrium_ratio <= 1e-8 and linear_result.constraint_max <= 1e-9
    linear_restored = fresh(); linear_restored.restore(linear.checkpoint())
    assert linear_restored.recover(selection=restored_input.recovery_selection) == (group,)
    owner = fresh(); prefix = owner.solve(restored_input.actions, (.25, .5), max_iterations=20, max_backtracks=12)
    saved = owner.checkpoint(); resumed = fresh(); resumed.restore(saved)
    assert resumed.checkpoint() == saved
    direct = owner.solve(restored_input.actions, (1.,), max_iterations=20, max_backtracks=12)
    continued = resumed.solve(restored_input.actions, (1.,), max_iterations=20, max_backtracks=12)
    assert continued.accepted_factors == direct.accepted_factors == (.25, .5, 1.)
    for name, scale in (("beam_positions", 2.), ("beam_frames", 1.), ("cell_frames", 1.),
                        ("cell_resultants", np.array((10.,)*6+(20.,)*12)),
                        ("multipliers", np.tile((10.,)*3+(20.,)*3, 5))):
        np.testing.assert_allclose(getattr(continued, name)/scale, getattr(direct, name)/scale, rtol=0., atol=1e-9)
    assert continued.free_equilibrium_ratio <= 1e-8 and continued.constraint_max <= 1e-9
    selected, = resumed.recover(selection=restored_input.recovery_selection)
    assert selected.configuration == "current" and selected.beam_node_id == 105
    current = resumed.checkpoint()
    with pytest.raises(ValueError, match="chart rebase is unsupported"):
        resumed.rebase_rotations(expected_generation=continued.generation)
    assert resumed.checkpoint() == current and resumed.recover() is continued
