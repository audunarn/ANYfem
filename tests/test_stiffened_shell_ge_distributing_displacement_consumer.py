"""Experimental consumer framing/dispatch; mechanics remain public-owner truth."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from anysolver import stiffened_shell as ss
from anysolver.control import CancellationToken, SolveCancelled
from anyfem.io.project_file import load_project, save_project
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition
from anyfem.solve.stiffened_shell import (
    ExperimentalGEFiniteDistributingPanel as Panel,
    ExperimentalGEFiniteDistributingMaterialPanel as MaterialPanel,
    SCHEMA_FINITE_DISTRIBUTING_DISPLACEMENT,
    SCHEMA_FINITE_DISTRIBUTING_MATERIAL_DISPLACEMENT, load_case, save_case,
)
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel


def _example(name):
    path = Path(__file__).resolve().parents[1] / "examples" / (name + ".py")
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


@pytest.fixture(scope="module")
def inputs():
    example = _example("stiffened_shell_ge_distributing_displacement_consumer")
    model, actions = example.definition_and_actions()
    panel = Panel.from_definition(model, actions,
        displacement_control=ss.DisplacementControl("beam", 103, 2),
        displacement_seed_factors=(.25,))
    return model, actions, panel, example


def test_request_roundtrip_owned_inputs_and_force_identity(inputs, monkeypatch, tmp_path):
    model, actions, panel, _ = inputs
    monkeypatch.setattr(ss, "create_analysis", lambda **kw: pytest.fail("input assembled owner"))
    direction = np.array(actions[0].wrench)
    control = ss.DisplacementControl("beam", 103, 2)
    owned = Panel.from_definition(model, (replace(actions[0], wrench=direction),),
        displacement_control=control, displacement_seed_factors=(.25,))
    expected = owned.to_dict()
    direction[:] = np.nan
    object.__setattr__(control, "node", 999)
    assert owned.to_dict() == expected
    assert expected["schema"] == SCHEMA_FINITE_DISTRIBUTING_DISPLACEMENT
    assert "displacement_control" not in Panel.from_definition(model, actions).to_dict()
    assert replace(panel, displacement_control=None, displacement_seed_factors=()).to_dict() == (
        Panel.from_definition(model, actions).to_dict())
    assert load_case(save_case(owned, tmp_path / "control.anyfem-ge.json")).to_dict() == expected
    project = Project("experimental displacement input", mesh_only=True)
    analysis = AnalysisDefinition.experimental_point_panel("control", owned)
    project.add_analysis(analysis)
    restored = load_project(save_project(project, tmp_path / "control.anyfem")).analyses[analysis.id]
    assert point_panel_from_analysis(restored).to_dict() == expected
    expected["displacement_control"]["node"] = 999
    assert owned.displacement_control.node == 103


@pytest.mark.parametrize("factors", ((), [.25], (0.,), (-.25,), (True,),
                                      (float("nan"),), (float("inf"),), (.5, .25)))
def test_bad_seed_refused_without_assembly(inputs, factors, monkeypatch):
    monkeypatch.setattr(ss, "create_analysis", lambda **kw: pytest.fail("bad seed assembled"))
    with pytest.raises(ValueError):
        Panel.from_definition(inputs[0], inputs[1],
            displacement_control=inputs[2].displacement_control,
            displacement_seed_factors=factors)


@pytest.mark.parametrize("control", (object(), ss.DisplacementControl("bad", 103, 2),
    ss.DisplacementControl("beam", True, 2), ss.DisplacementControl("beam", 103, True),
    ss.DisplacementControl("beam", 103, 3), ss.DisplacementControl("beam", 999, 2)))
def test_bad_control_refused_without_assembly(inputs, control, monkeypatch):
    monkeypatch.setattr(ss, "create_analysis", lambda **kw: pytest.fail("bad control assembled"))
    with pytest.raises(ValueError):
        Panel.from_definition(inputs[0], inputs[1], displacement_control=control,
                              displacement_seed_factors=(.25,))


def test_restraint_event_empty_direction_and_unselected_seed_refused(inputs):
    fixed = inputs[0].fixed[0]
    control = ss.DisplacementControl(fixed.domain, fixed.node, fixed.component)
    with pytest.raises(ValueError):
        replace(inputs[2], displacement_control=control)
    event = ss.DisplacementControl("beam", 103, 2)
    object.__setattr__(event, "trial_policy", "event")
    with pytest.raises(ValueError):
        replace(inputs[2], displacement_control=event)
    with pytest.raises(ValueError):
        replace(inputs[2], actions=())
    with pytest.raises(ValueError):
        replace(inputs[2], displacement_control=None)


@pytest.mark.parametrize("mutation", ("policy", "extra", "trial", "seed", "domain", "drop", "v1", "seed_admission", "admission"))
def test_strict_displacement_schema_rejects_injection(inputs, mutation):
    payload = deepcopy(inputs[2].to_dict())
    if mutation == "policy": payload["displacement_policy"] = "qualified"
    if mutation == "extra": payload["arc_control"] = {}
    if mutation == "trial": payload["displacement_control"]["trial_policy"] = None
    if mutation == "seed": payload["displacement_seed_factors"] = (1.,)
    if mutation == "domain": payload["displacement_control"]["domain"] = "retained"
    if mutation == "drop": del payload["displacement_seed_factors"]
    if mutation == "v1": payload["schema"] = Panel.from_definition(inputs[0], inputs[1]).to_dict()["schema"]
    if mutation == "seed_admission": payload["force_seed_admission"] = "reference_linear"
    if mutation == "admission": payload["admission"] = "monotone_nodal_translation_force_only"
    with pytest.raises(ValueError): Panel.from_dict(payload)
    analysis = AnalysisDefinition.experimental_point_panel("control", inputs[2])
    analysis.beam_formulation_options["panel"] = payload
    with pytest.raises(ValueError): point_panel_from_analysis(analysis)


@pytest.mark.parametrize("profile", ("generalized", "coupled", "cyclic", "native", "native_coupled"))
def test_material_request_preserves_policy_reference_and_raw_inputs(profile, monkeypatch, tmp_path):
    if profile in ("native", "native_coupled"):
        model, actions = _example("stiffened_shell_ge_distributing_native_material_consumer").native_definition_and_actions()
        if profile == "native":
            model = replace(model, shell=replace(model.shell, j2_curve=None),
                finite_distributing_material_policy=ss.GE_FINITE_DISTRIBUTING_NATIVE_CYCLIC_MATERIAL_V1)
    else:
        model, actions = _example("stiffened_shell_ge_distributing_material_consumer").definition_and_actions(
            coupled=profile == "coupled")
        if profile == "cyclic":
            model = replace(model, cyclic_reference_actions=actions,
                finite_distributing_material_policy=ss.GE_FINITE_DISTRIBUTING_GENERALIZED_CYCLIC_MATERIAL_V1)
    monkeypatch.setattr(ss, "create_analysis", lambda **kw: pytest.fail("input assembled material owner"))
    legacy = MaterialPanel.from_definition(model, actions)
    panel = MaterialPanel.from_definition(model, actions,
        displacement_control=ss.DisplacementControl("beam", 103, 0),
        displacement_seed_factors=(1.,))
    payload = panel.to_dict()
    assert payload["schema"] == SCHEMA_FINITE_DISTRIBUTING_MATERIAL_DISPLACEMENT
    assert MaterialPanel.from_dict(payload).to_dict() == payload
    assert load_case(save_case(panel, tmp_path / "material.anyfem-ge.json")).to_dict() == payload
    analysis = AnalysisDefinition.experimental_point_panel("material control", panel)
    assert point_panel_from_analysis(analysis).to_dict() == payload
    assert tuple(beam.raw for beam in panel.definition().beams) == tuple(beam.raw for beam in model.beams)
    assert panel.definition().finite_distributing_material_policy == model.finite_distributing_material_policy
    assert replace(panel, displacement_control=None, displacement_seed_factors=()).to_dict() == legacy.to_dict()
    if profile in ("cyclic", "native", "native_coupled"):
        assert panel.definition().cyclic_reference_actions == panel.actions
        nonunit = replace(panel, displacement_seed_factors=(.25,))
        assert MaterialPanel.from_dict(nonunit.to_dict()).displacement_seed_factors == (.25,)
        assert nonunit.definition().cyclic_reference_actions == panel.actions


class _Owner:
    def __init__(self, panel):
        self.calls = []
        self.closed = False
        self.capabilities = dict(status=panel.status,
            profile=ss.B3_GE_E4_PL_STIFFENED_SHELL_V1,
            distributing_footprint_law=ss.GE_DISTRIBUTING_FINITE_DIRECTOR_POLAR_V1_EXPERIMENTAL,
            analysis=("nonlinear_force", "experimental_fixed_displacement"),
            loads=("nodal dead translation forces",),
            checkpoints=("finite_distributing", "finite_distributing_displacement"),
            displacement_checkpoint="finite_distributing_displacement",
            displacement_control="fixed signed physical translational targets from accepted fixed-force seed")
        self.constraints = 6 * (len(panel.points) + len(panel.finite_distributing_footprints))
    def preflight(self): return dict(identity="0" * 64, constraints=self.constraints)
    def recover(self): return None
    def close(self): self.closed = True
    def restore(self, raw): self.calls.append(("restore", raw))
    def solve(self, actions, targets, **kwargs):
        self.calls.append((actions, targets, kwargs))
        if "on_accepted" in kwargs and kwargs["on_accepted"] is not None:
            kwargs["on_accepted"]("displacement-publication")
        return "result"


def test_combined_helper_seeds_fresh_owner_and_factory_never_reseeds(inputs, monkeypatch):
    panel = inputs[2]
    owners = []
    def create(**kw):
        owner = _Owner(panel)
        owners.append(owner)
        return owner
    monkeypatch.setattr(ss, "create_analysis", create)
    analysis = AnalysisDefinition.experimental_point_panel("control", panel)
    token, seen = CancellationToken(), []
    assert solve_point_panel(analysis, targets=(.1, -.1), cancellation_token=token,
        on_accepted=seen.append, max_iterations=20, max_backtracks=12) == "result"
    owner = owners[0]
    assert owner.closed and seen == ["displacement-publication"]
    assert owner.calls[0][1] == panel.displacement_seed_factors
    assert "displacement_control" not in owner.calls[0][2]
    assert "on_accepted" not in owner.calls[0][2]
    assert owner.calls[1][1] == (.1, -.1)
    assert owner.calls[1][2]["displacement_control"] == panel.displacement_control
    assert all(call[2]["cancellation_token"] is token for call in owner.calls)
    restored = analysis.create_point_panel_analysis()
    assert restored.calls == []
    restored.restore(b"accepted-displacement")
    restored.solve(panel.actions, (.2,), displacement_control=panel.displacement_control)
    assert len(restored.calls) == 2 and restored.calls[0][0] == "restore"
    assert "displacement_control" in restored.calls[1][2]
    restored.close()


@pytest.mark.parametrize("targets", (None, (), [.1], (True,), (float("nan"),), (.1, .1)))
def test_invalid_targets_and_unsupported_controls_refused_before_owner(inputs, targets, monkeypatch):
    analysis = AnalysisDefinition.experimental_point_panel("control", inputs[2])
    monkeypatch.setattr(ss, "create_analysis", lambda **kw: pytest.fail("bad targets assembled"))
    with pytest.raises(ValueError): solve_point_panel(analysis, targets=targets)
    for kwargs in (dict(increment_control=object()), dict(on_case=lambda _: None)):
        with pytest.raises(ValueError): solve_point_panel(analysis, targets=(.1,), **kwargs)


@pytest.mark.parametrize("kwargs", (dict(on_accepted=object()),
    dict(max_iterations=True), dict(max_iterations=0), dict(max_iterations=25),
    dict(max_backtracks=True), dict(max_backtracks=-1), dict(max_backtracks=13)))
def test_invalid_execution_options_refused_before_force_seed(inputs, kwargs, monkeypatch):
    analysis = AnalysisDefinition.experimental_point_panel("control", inputs[2])
    monkeypatch.setattr(ss, "create_analysis", lambda **kw: pytest.fail("bad execution options seeded"))
    with pytest.raises(ValueError): solve_point_panel(analysis, targets=(.1,), **kwargs)


def test_example_shell_translation_reads_flat_public_output(inputs):
    model, _, _, example = inputs
    node = sorted(model.shell.node_ids)[-1]
    control = ss.DisplacementControl("shell", node, 2)
    flat = np.arange(6 * len(model.shell.node_ids), dtype=float)
    result = SimpleNamespace(shell_displacement=flat)
    assert example.physical_translation(model, result, control) == flat[-4]
    with pytest.raises(ValueError, match="flat six-coordinate"):
        example.physical_translation(model, SimpleNamespace(shell_displacement=flat.reshape(-1, 6)), control)


@pytest.mark.parametrize("failure_phase", (1, 2))
def test_cancel_or_failed_seed_never_runs_next_phase_and_owner_closes(inputs, failure_phase, monkeypatch):
    panel, count = inputs[2], []
    owner = _Owner(panel)
    def fail(actions, targets, **kwargs):
        count.append(targets)
        if len(count) == failure_phase:
            raise SolveCancelled("consumer cancellation")
    owner.solve = fail
    monkeypatch.setattr(ss, "create_analysis", lambda **kw: owner)
    analysis = AnalysisDefinition.experimental_point_panel("control", panel)
    with pytest.raises(SolveCancelled): solve_point_panel(analysis, targets=(.1,))
    assert len(count) == failure_phase and owner.closed


def test_observer_exception_closes_combined_owner(inputs, monkeypatch):
    owner = _Owner(inputs[2])
    monkeypatch.setattr(ss, "create_analysis", lambda **kw: owner)
    def observe(result): raise RuntimeError("observer")
    with pytest.raises(RuntimeError, match="observer"):
        solve_point_panel(AnalysisDefinition.experimental_point_panel("control", inputs[2]),
                          targets=(.1,), on_accepted=observe)
    assert len(owner.calls) == 2 and owner.closed


@pytest.mark.parametrize("field,value", (("analysis", ("nonlinear_force",)),
    ("checkpoints", ("finite_distributing",)), ("displacement_checkpoint", "point_displacement"),
    ("displacement_control", "adaptive")))
def test_partial_capability_upgrade_refused_and_owner_closed(inputs, field, value, monkeypatch):
    owner = _Owner(inputs[2])
    owner.capabilities[field] = value
    monkeypatch.setattr(ss, "create_analysis", lambda **kw: owner)
    with pytest.raises(ValueError, match="public owner lacks"):
        inputs[2].create_analysis()
    assert owner.closed and not owner.calls


def test_legacy_owner_only_admitted_for_unselected_force_input(inputs, monkeypatch):
    owner = _Owner(inputs[2])
    owner.capabilities.update(analysis=("nonlinear_force",), checkpoints=("finite_distributing",))
    monkeypatch.setattr(ss, "create_analysis", lambda **kw: owner)
    force = replace(inputs[2], displacement_control=None, displacement_seed_factors=())
    assert force.create_analysis() is owner
    with pytest.raises(ValueError, match="public owner lacks"):
        inputs[2].create_analysis()
    assert owner.closed


def test_public_checkpoint_example(inputs, tmp_path):
    observations = inputs[3].workflow(tmp_path)
    assert observations["status"] == "EXPERIMENTAL_NOT_QUALIFIED"
    assert observations["schema"] == SCHEMA_FINITE_DISTRIBUTING_DISPLACEMENT
    assert observations["equilibrium"] <= 1e-8 and observations["gap"] <= 1e-9
