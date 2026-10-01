"""Explicit elastic V2 input/chart continuation, not scientific qualification."""
from dataclasses import replace
import json

import numpy as np
import pytest

from anysolver import stiffened_shell as ss
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEPointPanel, ExperimentalGENodalRigidFootprintPanel,
    SCHEMA_NODAL_FOOTPRINT, SCHEMA_NODAL_FOOTPRINT_CHART_V2, STATUS,
    save_case, load_case,
)
from anyfem.solve.stiffened_shell_project import (
    GE_TYPE, GE_FORMULATION, point_panel_from_analysis,
)
from test_stiffened_shell_nodal_footprint_consumer import _footprint
from test_experimental_stiffened_shell_consumer import _j2_fibre_case


def _charted(mixed=False):
    return replace(_footprint(mixed),
                   footprint_chart_policy=ss.GE_NODAL_FOOTPRINT_INCREMENT_ORIGIN_CHART_V2)


def test_v1_serialized_bytes_and_default_definition_are_unchanged(tmp_path):
    source = _footprint()
    expected = dict(schema=SCHEMA_NODAL_FOOTPRINT, status=STATUS,
        profile=ss.B3_GE_E4_PL_STIFFENED_SHELL_V1,
        admission="experimental_elastic_unrebased_point_owner",
        panel=source.panel.to_dict(), footprints=[json.loads(json.dumps(g.descriptor()))
                                                for g in source.footprints])
    expected_bytes = (json.dumps(expected, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False)+"\n").encode("ascii")
    path = save_case(source, tmp_path/"legacy.anyfem-ge.json")
    assert path.read_bytes() == expected_bytes
    assert load_case(path).to_dict() == expected
    assert "footprint_chart_policy" not in source.to_dict()
    assert source.definition().footprint_chart_policy == source.footprint_chart_policy
    point = source.panel.to_dict()
    assert ExperimentalGEPointPanel.from_dict(point).to_dict() == point


def test_v1_factory_retains_historical_two_field_admission_guard(monkeypatch):
    class Owner:
        def preflight(self):
            return {"footprint_chart_admission_policy": {
                "rebase": "unsupported", "chart_checkpoint_restore": "unsupported"}}
    owner = Owner()
    monkeypatch.setattr(ss, "create_analysis", lambda **kwargs: owner)
    assert _footprint().create_analysis() is owner


@pytest.mark.parametrize("mixed", (False, True, "all-s3"))
def test_v2_standalone_project_roundtrip_owns_inputs_without_assembly(tmp_path, monkeypatch, mixed):
    source = _charted(mixed)
    before = source.to_dict()
    def forbidden(**kwargs):
        raise AssertionError("input persistence assembled a solver owner")
    monkeypatch.setattr(ss, "create_analysis", forbidden)
    restored = load_case(save_case(source, tmp_path/"chart.anyfem-ge.json"))
    assert restored.to_dict() == before
    assert before["schema"] == SCHEMA_NODAL_FOOTPRINT_CHART_V2
    assert before["footprint_chart_policy"] == ss.GE_NODAL_FOOTPRINT_INCREMENT_ORIGIN_CHART_V2
    assert restored.definition().footprint_chart_policy == source.footprint_chart_policy
    assert restored.definition().footprints == source.footprints
    project = Project("explicit footprint charts", mesh_only=True)
    record = AnalysisDefinition.experimental_point_panel("chart", restored)
    assert (record.type, record.beam_formulation) == (GE_TYPE, GE_FORMULATION)
    project.add_analysis(record)
    reopened = load_project(save_project(project, tmp_path/"chart.anyfem"))
    owned = point_panel_from_analysis(reopened.analyses[record.id])
    assert owned.to_dict() == before
    before["panel"]["shell"]["coordinates_m"][0][0] = 99.
    before["footprints"][0]["shell_nodes"][0] = 99
    assert owned.to_dict() == source.to_dict() == restored.to_dict()


@pytest.mark.parametrize("case", ("wrong-schema", "wrong-policy", "legacy-policy", "missing-policy",
                                  "unknown-field", "nested-lines", "wrong-admission", "staged"))
def test_v2_strict_parser_rejects_unadvertised_inputs(case):
    record = _charted().to_dict()
    if case == "wrong-schema": record["schema"] = SCHEMA_NODAL_FOOTPRINT
    elif case == "wrong-policy": record["footprint_chart_policy"] = "unknown"
    elif case == "legacy-policy": record["footprint_chart_policy"] = _footprint().footprint_chart_policy
    elif case == "missing-policy": del record["footprint_chart_policy"]
    elif case == "unknown-field": record["chart_state"] = {}
    elif case == "nested-lines": record["panel"]["lines"] = []
    elif case == "wrong-admission": record["admission"] = "experimental_elastic_unrebased_point_owner"
    else: record["panel"]["schema"] = "anyfem.experimental-ge-point-panel-staged-v4"
    with pytest.raises(ValueError): ExperimentalGENodalRigidFootprintPanel.from_dict(record)


def test_v2_requires_available_public_solver_policy(monkeypatch):
    source = _charted()
    record = source.to_dict()
    monkeypatch.delattr(ss, "GE_NODAL_FOOTPRINT_INCREMENT_ORIGIN_CHART_V2")
    with pytest.raises(ValueError, match="installed public"):
        ExperimentalGENodalRigidFootprintPanel.from_dict(record)
    with pytest.raises(ValueError, match="installed public"):
        replace(source)
    # A V2-unaware public solver can still receive the old default input route.
    assert _footprint().to_dict()["schema"] == SCHEMA_NODAL_FOOTPRINT


@pytest.mark.parametrize("kind", ("shell-j2", "fibre", "staged"))
def test_v2_preserves_elastic_unstaged_scope(kind):
    source = _charted(); panel = source.panel
    declared = _j2_fibre_case(False, False)
    if kind == "shell-j2": panel = replace(panel, shell=replace(panel.shell, j2_curve=declared.shell.j2_curve))
    elif kind == "fibre": panel = replace(panel, beams=declared.beams)
    else:
        programme = ss.StagedDeadLoadProgramme("ab"*32, (panel.actions,
            (ss.NodalAction("beam", 105, (0., 0., 20., 0., 0., 0.)),)))
        panel = replace(panel, actions=(), load_programme=programme)
    with pytest.raises(ValueError): replace(source, panel=panel)


def test_v2_partial_shell_rotation_supports_reject_in_parser_and_public_owner(request):
    source = _charted()
    partial = tuple(item for item in source.panel.fixed
                    if not (item.node == 1 and item.component in (4, 5)))
    legacy = replace(_footprint(), panel=replace(source.panel, fixed=partial))
    with pytest.raises(ValueError, match="rotational triples"):
        replace(source, panel=legacy.panel)
    record = source.to_dict()
    record["panel"]["fixed"] = legacy.panel.to_dict()["fixed"]
    with pytest.raises(ValueError, match="rotational triples"):
        ExperimentalGENodalRigidFootprintPanel.from_dict(record)
    definition = replace(source.definition(), fixed=partial)
    owner = ss.create_analysis(definition=definition)
    request.addfinalizer(owner.close)
    with pytest.raises(ValueError, match="rotational triples"):
        owner.solve(source.actions, (.5,), max_iterations=20, max_backtracks=12)


def test_v2_parser_admits_fully_free_rotation_triples_without_changing_v1():
    source = _charted()
    fixed = tuple(item for item in source.panel.fixed if item.component < 3)
    free = replace(source, panel=replace(source.panel, fixed=fixed))
    assert ExperimentalGENodalRigidFootprintPanel.from_dict(free.to_dict()).to_dict() == free.to_dict()
    legacy = replace(_footprint(), panel=free.panel)
    assert legacy.to_dict()["schema"] == SCHEMA_NODAL_FOOTPRINT


@pytest.mark.parametrize("fault", ("missing", "wrong-policy", "wrong-anchor", "qualified"))
def test_v2_preflight_mismatch_closes_owner(monkeypatch, fault):
    source = _charted(); closed = []
    policy = dict(policy=source.footprint_chart_policy, rebase="experimental_elastic",
        chart_checkpoint_restore="experimental_elastic",
        increment_origin="zero_rotation_coordinates_at_accepted_physical_frames",
        newton_anchor="fixed_for_all_iterations_and_backtracks",
        rotational_supports="fully_fixed_or_fully_free_triples", production_qualified=False)
    if fault == "missing": policy = None
    elif fault == "wrong-policy": policy["policy"] = _footprint().footprint_chart_policy
    elif fault == "wrong-anchor": policy["newton_anchor"] = "moving"
    else: policy["production_qualified"] = True
    class Owner:
        def preflight(self): return {"footprint_chart_admission_policy": policy}
        def close(self): closed.append(True)
    def factory(*, definition):
        assert definition.footprint_chart_policy == source.footprint_chart_policy
        return Owner()
    monkeypatch.setattr(ss, "create_analysis", factory)
    with pytest.raises(ValueError, match="declared public"): source.create_analysis()
    assert closed == [True]


def test_preflight_failure_keeps_exception_type_and_closes_owner(monkeypatch):
    class PreflightFailure(RuntimeError): pass
    failure = PreflightFailure("public owner failure"); closed = []
    class Owner:
        def preflight(self): raise failure
        def close(self): closed.append(True)
    monkeypatch.setattr(ss, "create_analysis", lambda **kwargs: Owner())
    with pytest.raises(PreflightFailure) as caught: _charted().create_analysis()
    assert caught.value is failure and closed == [True]


def _assert_group(owner, panel, result):
    group, = owner.recover(selection=panel.recovery_selection)
    assert type(group) is ss.NodalRigidFootprintWrench
    assert group.generation == result.generation and not group.production_qualified
    assert group.footprint_id == "tip-pad" and group.beam_node_id == 105
    assert group.shell_node_ids == (2, 3, 6, 5) and group.configuration == "current"
    assert tuple(point.shell_node_id for point in group.point_wrenches) == group.shell_node_ids
    np.testing.assert_allclose((np.array(group.shell_action)+group.beam_action)/
                               np.array((10.,)*3+(20.,)*3), 0., rtol=0., atol=1e-8)
    return group


def test_q4_project_public_four_branch_half_to_full_successor(tmp_path, request):
    source = _charted()
    project = Project("charted footprint", mesh_only=True)
    record = AnalysisDefinition.experimental_point_panel("chart", source); project.add_analysis(record)
    reopened = load_project(save_project(project, tmp_path/"charted.anyfem"))
    record = reopened.analyses[record.id]; panel = point_panel_from_analysis(record)
    def fresh():
        owner = record.create_point_panel_analysis(); request.addfinalizer(owner.close); return owner
    direct = fresh()
    prefix = direct.solve(panel.actions, (.25, .5), max_iterations=20, max_backtracks=12)
    saved = direct.checkpoint()
    restored = fresh(); restored.restore(saved)
    assert restored.checkpoint() == saved
    rebased = fresh(); rebased.restore(saved)
    event = rebased.rebase_rotations(expected_generation=prefix.generation)
    assert event.generation == prefix.generation+1
    rebased_saved = rebased.checkpoint()
    rebased_restored = fresh(); rebased_restored.restore(rebased_saved)
    assert rebased_restored.checkpoint() == rebased_saved
    branches = (direct, restored, rebased, rebased_restored)
    results = [owner.solve(panel.actions, (1.,), max_iterations=20, max_backtracks=12)
               for owner in branches]
    reference = results[0]; groups = []
    for owner, result in zip(branches, results):
        assert result.status == "completed" and result.accepted_factors == (.25, .5, 1.)
        assert result.iterations == reference.iterations
        assert result.free_equilibrium_ratio <= 1e-8 and result.constraint_max <= 1e-9
        assert not result.production_qualified
        np.testing.assert_allclose(result.shell_displacement.reshape(-1, 6)[:, :3]/2.,
            reference.shell_displacement.reshape(-1, 6)[:, :3]/2., rtol=0., atol=1e-9)
        for name, scale in (("beam_positions", 2.), ("beam_frames", 1.), ("cell_frames", 1.),
                            ("shell_frames", 1.), ("cell_resultants", np.array((10.,)*6+(20.,)*12))):
            np.testing.assert_allclose(getattr(result, name)/scale,
                                       getattr(reference, name)/scale, rtol=0., atol=1e-9)
        for name in ("multipliers", "support_reactions"):
            scale = np.array((10.,)*3+(20.,)*3)
            np.testing.assert_allclose(getattr(result, name).reshape(-1, 6)/scale,
                getattr(reference, name).reshape(-1, 6)/scale, rtol=0., atol=1e-9)
        groups.append(_assert_group(owner, panel, result))
        successor = fresh(); successor.restore(owner.checkpoint())
        assert successor.checkpoint() == owner.checkpoint()
        assert successor.recover(selection=panel.recovery_selection) == (groups[-1],)
    for group in groups[1:]:
        for name in ("shell_action", "beam_action"):
            np.testing.assert_allclose(np.array(getattr(group, name))/np.array((10.,)*3+(20.,)*3),
                np.array(getattr(groups[0], name))/np.array((10.,)*3+(20.,)*3), rtol=0., atol=1e-9)


@pytest.mark.parametrize("mixed", (True, "all-s3"))
def test_mixed_and_s3_public_chart_reset_restore_continue(request, mixed):
    panel = _charted(mixed)
    owner = panel.create_analysis(); request.addfinalizer(owner.close)
    prefix = owner.solve(panel.actions, (.25, .5), max_iterations=20, max_backtracks=12)
    owner.rebase_rotations(expected_generation=prefix.generation)
    raw = owner.checkpoint()
    resumed = panel.create_analysis(); request.addfinalizer(resumed.close); resumed.restore(raw)
    assert resumed.checkpoint() == raw
    result = resumed.solve(panel.actions, (1.,), max_iterations=20, max_backtracks=12)
    assert result.accepted_factors == (.25, .5, 1.)
    assert result.free_equilibrium_ratio <= 1e-8 and result.constraint_max <= 1e-9
    _assert_group(resumed, panel, result)
