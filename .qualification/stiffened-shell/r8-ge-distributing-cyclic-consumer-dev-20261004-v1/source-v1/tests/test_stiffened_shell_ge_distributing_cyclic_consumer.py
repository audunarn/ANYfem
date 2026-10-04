"""Explicit cyclic distributing inputs keep their immutable reference actions."""

from dataclasses import replace
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest
from anysolver import stiffened_shell as ss

from anyfem.solve.stiffened_shell import (
    ExperimentalGEFiniteDistributingMaterialPanel as MaterialPanel,
    ExperimentalGEFiniteDistributingPanel as ElasticPanel,
    FINITE_DISTRIBUTING_ADMISSION, FINITE_DISTRIBUTING_CYCLIC_ADMISSION,
    load_case, save_case,
)


@pytest.fixture(scope="module")
def inputs():
    path = (Path(__file__).resolve().parents[1] / "examples" /
            "stiffened_shell_ge_distributing_material_consumer.py")
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location("cyclic_material_inputs", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module.definition_and_actions


def _cyclic(inputs, coupled):
    model, actions = inputs(coupled)
    policy = (ss.GE_FINITE_DISTRIBUTING_COUPLED_J2_CYCLIC_MATERIAL_V1 if coupled
              else ss.GE_FINITE_DISTRIBUTING_GENERALIZED_CYCLIC_MATERIAL_V1)
    return replace(model, finite_distributing_material_policy=policy,
                   cyclic_reference_actions=actions), actions


@pytest.mark.parametrize("coupled", (False, True))
def test_cyclic_reference_actions_survive_definition_and_input_round_trip(
        inputs, coupled, tmp_path):
    model, actions = _cyclic(inputs, coupled)
    panel = MaterialPanel.from_definition(model, actions)
    assert panel.definition().cyclic_reference_actions == panel.actions == actions
    record = panel.to_dict()
    assert record["finite_distributing_material_policy"] == model.finite_distributing_material_policy
    assert record["admission"] == FINITE_DISTRIBUTING_CYCLIC_ADMISSION
    assert MaterialPanel.from_dict(record).to_dict() == record
    reopened = load_case(save_case(panel, tmp_path / "cyclic.anyfem-ge.json"))
    assert type(reopened) is MaterialPanel
    assert reopened.to_dict() == record
    assert reopened.definition().cyclic_reference_actions == actions


def test_material_policy_and_admission_cannot_be_relabelled(inputs):
    model, actions = _cyclic(inputs, False)
    cyclic = MaterialPanel.from_definition(model, actions).to_dict()
    wrong = dict(cyclic, admission=FINITE_DISTRIBUTING_ADMISSION)
    with pytest.raises(ValueError, match="policy and admission"):
        MaterialPanel.from_dict(wrong)
    old_model, old_actions = inputs(False)
    old = MaterialPanel.from_definition(old_model, old_actions).to_dict()
    assert old["admission"] == FINITE_DISTRIBUTING_ADMISSION
    assert MaterialPanel.from_dict(old).to_dict() == old
    wrong = dict(old, admission=FINITE_DISTRIBUTING_CYCLIC_ADMISSION)
    with pytest.raises(ValueError, match="policy and admission"):
        MaterialPanel.from_dict(wrong)


@pytest.mark.parametrize("coupled", (False, True))
def test_cyclic_selection_rejects_mismatched_or_dropped_reference_actions(inputs, coupled):
    model, actions = _cyclic(inputs, coupled)
    with pytest.raises(ValueError, match="reference actions"):
        MaterialPanel.from_definition(replace(model, cyclic_reference_actions=()), actions)
    with pytest.raises(ValueError, match="reference actions"):
        MaterialPanel.from_definition(model, ())
    with pytest.raises(ValueError, match="reference actions"):
        MaterialPanel.from_definition(replace(model, cyclic_reference_actions=actions * 2), actions)
    with pytest.raises(ValueError, match="reference actions"):
        MaterialPanel.from_definition(replace(model, finite_distributing_material_policy=(
            ss.GE_FINITE_DISTRIBUTING_COUPLED_J2_MATERIAL_V1 if coupled else
            ss.GE_FINITE_DISTRIBUTING_GENERALIZED_MATERIAL_V1)), actions)


def test_array_wrench_reference_comparison_owns_both_inputs(inputs):
    model, actions = _cyclic(inputs, False)
    applied_wrench = np.array(actions[0].wrench)
    reference_wrench = np.array(actions[0].wrench)
    applied = (replace(actions[0], wrench=applied_wrench),)
    reference = (replace(actions[0], wrench=reference_wrench),)
    panel = MaterialPanel.from_definition(
        replace(model, cyclic_reference_actions=reference), applied)
    record = panel.to_dict()
    applied_wrench[:] = np.nan
    reference_wrench[:] = np.nan
    assert panel.to_dict() == record
    assert panel.definition().cyclic_reference_actions == panel.actions
    different = (replace(actions[0], wrench=np.array(actions[0].wrench) +
                         np.array((1., 0., 0., 0., 0., 0.))),)
    with pytest.raises(ValueError, match="reference actions"):
        MaterialPanel.from_definition(
            replace(model, cyclic_reference_actions=different), actions)


def test_old_policy_survives_missing_new_constants_and_elastic_rejects_reference_actions(
        inputs, monkeypatch):
    model, actions = inputs(False)
    for name in ("GE_FINITE_DISTRIBUTING_GENERALIZED_CYCLIC_MATERIAL_V1",
                 "GE_FINITE_DISTRIBUTING_COUPLED_J2_CYCLIC_MATERIAL_V1"):
        monkeypatch.delattr(ss, name, raising=False)
    assert MaterialPanel.from_definition(model, actions).definition().cyclic_reference_actions == ()
    with pytest.raises(ValueError, match="cyclic reference actions"):
        ElasticPanel.from_definition(replace(model,
            finite_distributing_material_policy="elastic",
            cyclic_reference_actions=actions), actions)
    with pytest.raises(ValueError, match="material policy"):
        MaterialPanel.from_definition(replace(model,
            finite_distributing_material_policy=
            "GE_FINITE_DISTRIBUTING_GENERALIZED_CYCLIC_MATERIAL_V1_DEV",
            cyclic_reference_actions=actions), actions)


def test_selected_cyclic_policy_requires_public_reference_field(inputs, monkeypatch):
    model, actions = _cyclic(inputs, False)
    old_fields = dict(ss.LinearDefinition.__dataclass_fields__)
    old_fields.pop("cyclic_reference_actions")
    monkeypatch.setattr(ss.LinearDefinition, "__dataclass_fields__", old_fields)
    with pytest.raises(ValueError, match="lacks cyclic reference action definition field"):
        MaterialPanel.from_definition(model, actions)


@pytest.mark.parametrize("coupled,expected_kind", (
    (False, "finite_distributing_cyclic_material"),
    (True, "finite_distributing_cyclic_coupled"),
))
@pytest.mark.parametrize("wrong_field,wrong_value", (
    ("checkpoints", ("finite_distributing_material",)),
    ("increment_control", ("fixed", "experimental_adaptive_halving")),
    ("zero_load_normalization", "actual applied force"),
    ("adaptive_max_backtracks", 12),
))
def test_cyclic_checkpoint_kind_is_checked_and_mismatched_owner_closed(
        inputs, coupled, expected_kind, wrong_field, wrong_value, monkeypatch):
    model, actions = _cyclic(inputs, coupled)
    panel = MaterialPanel.from_definition(model, actions)
    class Owner:
        closed = False
        capabilities = dict(status=panel.status, profile=model.profile,
            distributing_footprint_law=ss.GE_DISTRIBUTING_FINITE_DIRECTOR_POLAR_V1_EXPERIMENTAL,
            material_policy=model.finite_distributing_material_policy,
            analysis=("nonlinear_force",), loads=("nodal dead translation forces",),
            checkpoints=(expected_kind,), fixed_step_max_backtracks=16,
            increment_control=("fixed",), adaptive_max_backtracks=None,
            zero_load_normalization="immutable unit-factor reference pattern")
        def preflight(self):
            return dict(identity="0" * 64,
                        constraints=6 * (len(panel.points) + len(panel.finite_distributing_footprints)))
        def recover(self):
            pass
        def close(self):
            self.closed = True
    owner = Owner()
    monkeypatch.setattr(ss, "create_analysis", lambda **kwargs: owner)
    assert panel.create_analysis() is owner
    owner.capabilities = dict(owner.capabilities, **{wrong_field: wrong_value})
    with pytest.raises(ValueError, match="public owner lacks"):
        panel.create_analysis()
    assert owner.closed


def test_public_cyclic_zero_factor_restart(inputs):
    model, actions = _cyclic(inputs, False)
    panel = MaterialPanel.from_definition(model, actions)
    owner = panel.create_analysis()
    resumed = panel.create_analysis()
    try:
        prefix = owner.solve(panel.load_input, (.01, 0.),
                             max_iterations=24, max_backtracks=16)
        assert prefix.status == "completed"
        raw = owner.checkpoint()
        resumed.restore(raw)
        expected = owner.solve(panel.load_input, (.01,),
                               max_iterations=24, max_backtracks=16)
        actual = resumed.solve(panel.load_input, (.01,),
                               max_iterations=24, max_backtracks=16)
        assert expected.status == actual.status == "completed"
        assert owner.checkpoint() == resumed.checkpoint()
    finally:
        owner.close()
        resumed.close()
