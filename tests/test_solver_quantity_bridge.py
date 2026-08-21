"""ANYfem discovers live quantities only through ANYsolver's resolver."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from anysolver import QuantityUnavailableError, ReactionFrame

from anyfem.post.solver_data import (
    available_solution_quantities,
    resolve_solution_quantity,
)


def test_wrapper_displacement_and_linear_reactions_resolve_canonically():
    solution = SimpleNamespace(
        displacements=np.arange(12, dtype=float),
        shapes=(),
        info={"reactions": {7: np.arange(6, dtype=float)}},
        raw_result=None,
    )

    displacement = resolve_solution_quantity(solution, "displacement")
    reactions = resolve_solution_quantity(solution, "reaction")

    assert displacement.descriptor.quantity_id == "displacement"
    assert reactions.descriptor.location == "node"
    assert set(reactions.data) == {7}


def test_unavailable_quantity_is_never_advertised_or_zero_filled():
    solution = SimpleNamespace(
        displacements=np.ones(6), shapes=(), info={}, raw_result=None
    )

    keys = tuple(
        item.descriptor.quantity_id
        for item in available_solution_quantities(solution)
    )

    assert keys == ("displacement",)
    with pytest.raises(QuantityUnavailableError):
        resolve_solution_quantity(solution, "reaction")


def test_recovered_stress_must_resolve_before_anyfem_splits_components():
    recovered = SimpleNamespace(
        element_stresses={1: {"von_mises": np.asarray([10.0, 12.0])}}
    )
    solution = SimpleNamespace(
        displacements=np.ones(6), shapes=(), info={}, raw_result=None
    )

    resolved = resolve_solution_quantity(
        solution, "stress", recovered=recovered
    )

    assert resolved.descriptor.components == ("von_mises",)
    assert resolved.data is recovered.element_stresses


def test_empty_multishape_wrapper_uses_raw_arrays_without_indexing_shape_zero():
    raw = SimpleNamespace(displacements=np.arange(12, dtype=float).reshape(2, 6))

    class EmptyWrapper:
        shapes = ()
        info = {"raw": raw}
        raw_result = None

        @property
        def displacements(self):
            raise IndexError("there is no first display shape")

    resolved = resolve_solution_quantity(EmptyWrapper(), "displacement")
    np.testing.assert_array_equal(resolved.data, raw.displacements)


def test_committed_peeq_final_and_only_real_snapshot_frames_resolve():
    snapshots = (
        SimpleNamespace(
            step_index=1,
            load_factor=0.2,
            element_states={5: {"alpha": np.asarray([0.0, 0.01])}},
        ),
        SimpleNamespace(
            step_index=2,
            load_factor=0.4,
            element_states={5: {"layer_strain": np.asarray([0.02])}},
        ),
        SimpleNamespace(
            step_index=3,
            load_factor=0.6,
            element_states={5: {"alpha": np.asarray([0.02, 0.03])}},
        ),
    )
    raw = SimpleNamespace(
        element_states={5: {"alpha": np.asarray([0.02, 0.03])}},
        snapshots=snapshots,
    )
    solution = SimpleNamespace(shapes=(), info={"raw": raw}, raw_result=None)

    final = resolve_solution_quantity(solution, "equivalent_plastic_strain")
    history = resolve_solution_quantity(
        solution, "equivalent_plastic_strain_history"
    )

    assert final.data == {5: pytest.approx(0.03)}
    assert history.data == ({5: pytest.approx(0.01)}, {5: pytest.approx(0.03)})
    assert history.descriptor.metadata["frame_indices"] == [1, 3]


def test_reaction_and_energy_histories_are_canonical_and_fail_closed():
    raw = SimpleNamespace(
        times=np.asarray([0.0, 0.1]),
        reaction_history=(
            ReactionFrame(
                0,
                0.0,
                "time",
                {7: np.asarray([1.0, 0, 0, 0, 0, 0])},
                {"fixed": np.asarray([1.0, 0, 0, 0, 0, 0])},
            ),
            ReactionFrame(
                1,
                0.1,
                "time",
                {7: np.asarray([2.0, 0, 0, 0, 0, 0])},
                {"fixed": np.asarray([2.0, 0, 0, 0, 0, 0])},
            ),
        ),
        diagnostics={
            "strain_energy_measure": "internal_work_proxy",
            "kinetic_energy": [3.0, 2.0],
            "strain_energy": [0.0, 1.0],
            # Misaligned data is genuinely unavailable, not truncated.
            "sphere_kinetic_energy": [4.0],
        },
    )
    solution = SimpleNamespace(shapes=(), info={"raw": raw}, raw_result=None)

    keys = {
        item.descriptor.quantity_id
        for item in available_solution_quantities(solution)
    }
    assert {"reaction_history", "kinetic_energy", "internal_work"} <= keys
    assert "strain_energy" not in keys
    assert "impactor_kinetic_energy" not in keys
