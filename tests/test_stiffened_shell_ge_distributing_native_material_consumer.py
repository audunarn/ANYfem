"""Native fibre distributing inputs retain explicit policy and beam bytes."""

from dataclasses import replace
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest
from anysolver import b3_ge, stiffened_shell as ss

from anyfem.solve.stiffened_shell import (
    ExperimentalGEFiniteDistributingMaterialPanel as Panel,
    FINITE_DISTRIBUTING_CYCLIC_ADMISSION, load_case, save_case,
)


@pytest.fixture(scope="module")
def inputs():
    path = (Path(__file__).resolve().parents[1] / "examples" /
            "stiffened_shell_ge_distributing_material_consumer.py")
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location("native_distributing_inputs", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module.definition_and_actions


def _model(inputs, coupled, mixed=False):
    base, _ = inputs(coupled)
    source = base.beams[0]
    element, inertia = source.instantiate()
    reference = element.operator.reference
    coordinates = reference.coordinates.copy()
    coordinates[:, 2] = 0.
    young = base.shell.young_pa
    shear = young / (2. * (1. + base.shell.poisson))
    arm = np.sqrt(8.333333333333333e-6 / .01)
    fibre_section = b3_ge.PhysicalFibreSection(
        tuple(b3_ge.Fibre(str(i), y*arm, z*arm, .0025, young,
                          b3_ge.FlowCurve.linear(200e6, 200e9))
              for i, (y, z) in enumerate(((-1., -1.), (-1., 1.),
                                           (1., -1.), (1., 1.)))),
        np.diag(np.sqrt((0., shear*.008, shear*.008,
                          shear*1.6666666666666667e-5, 0., 0.))))
    fibre = b3_ge.define_beam(
        b3_ge.SELECTOR, element.element_id, element.node_ids,
        coordinates, reference.nodal_triads, fibre_section, inertia,
        quadrature=element.operator.order,
        arithmetic_policy=element.operator.arithmetic_policy)
    beams = (source,) if mixed else (fibre,)
    load_node = 103
    if mixed:
        translated = coordinates + np.array((2., 0., 0.))
        second_fibre = b3_ge.define_beam(
            b3_ge.SELECTOR, element.element_id + 1,
            (element.node_ids[-1], 104, 105), translated,
            reference.nodal_triads, fibre_section, inertia,
            quadrature=element.operator.order,
            arithmetic_policy=element.operator.arithmetic_policy)
        beams += (second_fibre,)
        load_node = 105
    actions = (ss.NodalAction("beam", load_node,
                             (1000. if mixed else 16e6, 0., 0., 0., 0., 0.)),)
    policy = (ss.GE_FINITE_DISTRIBUTING_NATIVE_COUPLED_J2_CYCLIC_MATERIAL_V1
              if coupled else ss.GE_FINITE_DISTRIBUTING_NATIVE_CYCLIC_MATERIAL_V1)
    return replace(base, beams=beams, finite_distributing_material_policy=policy,
                   cyclic_reference_actions=actions), actions


@pytest.mark.parametrize("coupled,mixed", ((False, False), (False, True), (True, False)))
def test_native_policy_and_raw_beams_round_trip(inputs, coupled, mixed, tmp_path):
    model, actions = _model(inputs, coupled, mixed)
    panel = Panel.from_definition(model, actions)
    record = panel.to_dict()
    assert record["admission"] == FINITE_DISTRIBUTING_CYCLIC_ADMISSION
    assert record["finite_distributing_material_policy"] == model.finite_distributing_material_policy
    assert Panel.from_dict(record).to_dict() == record
    reopened = load_case(save_case(panel, tmp_path / "native.anyfem-ge.json"))
    assert type(reopened) is Panel
    assert reopened.to_dict() == record
    assert tuple(beam.raw for beam in reopened.beams) == tuple(beam.raw for beam in model.beams)
    assert reopened.definition().cyclic_reference_actions == actions


def test_native_policy_requires_fibre_and_old_policy_rejects_fibre(inputs):
    model, actions = _model(inputs, False)
    original, _ = inputs(False)
    with pytest.raises(ValueError, match="native beam section families"):
        Panel.from_definition(replace(model, beams=original.beams), actions)
    with pytest.raises(ValueError, match="native beam section families"):
        Panel.from_definition(replace(model,
            finite_distributing_material_policy=
            ss.GE_FINITE_DISTRIBUTING_GENERALIZED_CYCLIC_MATERIAL_V1), actions)


def test_old_solver_without_native_constants_keeps_old_policies(inputs, monkeypatch):
    old, actions = inputs(False)
    for name in ("GE_FINITE_DISTRIBUTING_NATIVE_CYCLIC_MATERIAL_V1",
                 "GE_FINITE_DISTRIBUTING_NATIVE_COUPLED_J2_CYCLIC_MATERIAL_V1"):
        monkeypatch.delattr(ss, name, raising=False)
    assert Panel.from_definition(old, actions).to_dict()["finite_distributing_material_policy"] == (
        ss.GE_FINITE_DISTRIBUTING_GENERALIZED_MATERIAL_V1)
    with pytest.raises(ValueError, match="installed solver lacks the selected cyclic"):
        Panel.from_definition(replace(old, finite_distributing_material_policy=
            "GE_FINITE_DISTRIBUTING_NATIVE_CYCLIC_MATERIAL_V1_DEV",
            cyclic_reference_actions=actions), actions)


@pytest.mark.parametrize("wrong_field,wrong_value", (
    ("checkpoints", ("finite_distributing_cyclic_material",)),
    ("beam_section_families", ("RESULTANT_ELLIPSOID",)),
    ("increment_control", ("fixed", "experimental_adaptive_halving")),
    ("zero_load_normalization", "actual applied force"),
    ("adaptive_max_backtracks", 12),
))
def test_native_owner_contract_failure_closes_owner(
        inputs, wrong_field, wrong_value, monkeypatch):
    model, actions = _model(inputs, False, mixed=True)
    panel = Panel.from_definition(model, actions)
    class Owner:
        closed = False
        capabilities = dict(
            status=panel.status, profile=model.profile,
            distributing_footprint_law=ss.GE_DISTRIBUTING_FINITE_DIRECTOR_POLAR_V1_EXPERIMENTAL,
            material_policy=model.finite_distributing_material_policy,
            analysis=("nonlinear_force",), loads=("nodal dead translation forces",),
            checkpoints=("finite_distributing_cyclic_native_material",),
            fixed_step_max_backtracks=16,
            beam="physical axial/biaxial fibre and generalized material B3 with native retained coordinates",
            beam_section_families=("RESULTANT_ELLIPSOID", "PHYSICAL_AXIAL_BIAXIAL_FIBRE"),
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
    owner.capabilities = dict(owner.capabilities, **{wrong_field: wrong_value})
    monkeypatch.setattr(ss, "create_analysis", lambda **kwargs: owner)
    with pytest.raises(ValueError, match="public owner lacks"):
        panel.create_analysis()
    assert owner.closed


def test_public_native_zero_factor_checkpoint_restart(inputs, tmp_path):
    model, actions = _model(inputs, False)
    panel = load_case(save_case(
        Panel.from_definition(model, actions), tmp_path / "native-history.anyfem-ge.json"))
    owner = panel.create_analysis()
    resumed = panel.create_analysis()
    try:
        zero = owner.solve(panel.load_input, (.25, .5, .75, 1., 0.),
                           max_iterations=24, max_backtracks=16)
        assert zero.status == "completed" and zero.load_factor == 0.
        assert zero.free_equilibrium_ratio <= 1e-8 and zero.constraint_max <= 1e-9
        raw = owner.checkpoint()
        restored = resumed.restore(raw)
        assert restored.load_factor == 0. and resumed.checkpoint() == raw
        expected = owner.solve(panel.load_input, (-.25,),
                               max_iterations=24, max_backtracks=16)
        actual = resumed.solve(panel.load_input, (-.25,),
                               max_iterations=24, max_backtracks=16)
        assert expected.status == actual.status == "completed"
        assert owner.checkpoint() == resumed.checkpoint()
        np.testing.assert_array_equal(actual.support_reactions, expected.support_reactions)
    finally:
        owner.close()
        resumed.close()
