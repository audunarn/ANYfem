"""Acceptance coverage for committed nonlinear material-state recovery."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.post.fields import evaluate_field, recover
from anyfem.post.results import NonlinearSolution
from anyfem.solve.build import BuiltModel
from anysolver import generate_simple_panel_mesh, recover_stress_result


@pytest.mark.parametrize("kinematics", ["von_karman", "corotational"])
def test_increment_recovery_retains_committed_state_kinematics_and_cache(kinematics):
    model = generate_simple_panel_mesh(1., 1., .01, num_divisions_x=1, num_divisions_y=1)
    displacement = np.zeros(model.mesh.dof_manager.total_dofs)
    point_count = len(model.mesh.elements[1].gauss_points) * 3
    snapshots = [SimpleNamespace(step_index=index, load_factor=.5, displacements=displacement.copy(),
        element_states={1: {"layer_strain": np.zeros((point_count,3)),
            "plastic_strain": np.full((point_count,3),.002),
            "layer_stress": np.tile([stress,50.e6,10.e6],(point_count,1)),
            "alpha": np.full(point_count,.002)}}) for index,stress in [(7,250.e6),(9,270.e6)]]
    built = BuiltModel(fe_model=model,load_case=None,mesh=SimpleNamespace(),project=SimpleNamespace(name="increments"))
    raw = SimpleNamespace(snapshots=snapshots,info={"kinematics":kinematics})
    solution = NonlinearSolution(displacements=displacement,built=built,raw_result=raw)
    for view,snapshot in zip(solution.shapes,snapshots):
        actual = view.stresses()
        expected = recover_stress_result(model,displacement,nonlinear_result=snapshot,kinematics=kinematics)
        assert actual.provenance.analysis_context["kinematics"] == kinematics
        assert actual.provenance.mode == "material_history"
        for name,values in expected.element_stresses[1].items():
            np.testing.assert_array_equal(actual.element_stresses[1][name],values)
        assert view.stresses() is actual
        global_result = view.stresses(return_global=True)
        assert global_result is not actual and view._stress is actual
        assert view.raw_result is snapshot
    assert solution.shapes[0]._stress.element_stresses[1]["von_mises"].max() != solution.shapes[1]._stress.element_stresses[1]["von_mises"].max()


def test_nonlinear_solution_recovers_yielded_committed_state_not_elastic_guess():
    model = generate_simple_panel_mesh(
        1.0,
        1.0,
        0.01,
        num_divisions_x=1,
        num_divisions_y=1,
    )
    displacement = np.zeros(model.mesh.dof_manager.total_dofs, dtype=float)
    element = model.mesh.elements[1]
    point_count = len(element.gauss_points) * 3
    committed_stress = np.tile(
        np.array([250.0e6, 50.0e6, 10.0e6]),
        (point_count, 1),
    )
    raw = SimpleNamespace(
        displacements=displacement.copy(),
        element_states={
            1: {
                "layer_strain": np.zeros((point_count, 3)),
                "plastic_strain": np.full((point_count, 3), 0.002),
                "layer_stress": committed_stress,
                "alpha": np.full(point_count, 0.002),
            }
        },
    )
    built = BuiltModel(
        fe_model=model,
        load_case=None,
        mesh=SimpleNamespace(),
        project=SimpleNamespace(name="yielded panel"),
    )
    solution = NonlinearSolution(
        displacements=displacement,
        built=built,
        raw_result=raw,
    )

    committed = solution.stresses()
    elastic_guess = recover_stress_result(model, displacement)

    assert committed.provenance.mode == "material_history"
    assert (
        committed.provenance.per_element_source[1]
        == "committed_shell_layer_state"
    )
    assert np.max(committed.element_stresses[1]["von_mises"]) > 0.0
    np.testing.assert_allclose(
        elastic_guess.element_stresses[1]["von_mises"],
        0.0,
        atol=1.0e-12,
    )
    # Both the result API and generic field path share the cached, committed
    # recovery instead of silently rebuilding an elastic field.
    assert solution.stresses() is committed
    assert recover(solution) is committed

    plastic = evaluate_field(solution, "equivalent_plastic_strain")
    assert plastic.unit == "1"
    assert plastic.element_values == {1: 0.002}
    assert "equivalent_plastic_strain" in solution.available_fields()
