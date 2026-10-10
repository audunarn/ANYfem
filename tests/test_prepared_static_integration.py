"""Focused integration with the real anysolver prepared static evaluator.

These checks run the adapter against the sibling solver checkout's
``prepare_static_evaluator`` and compare it with the exact live stream.  Missing or incompatible implementation is a failure. The explicit
missing-API check independently exercises fail-closed admission.
"""

import numpy as np
import pytest
import anysolver
from anysolver import RecoveryConfig

from anyfem import StaticLoadRequest, iter_linear_static_batches
from anyfem.solve import (
    PreparedStaticUnavailableError,
    prepare_linear_static_evaluator,
)
from anyfem.solve.build import build_fe_model
from test_static_batch_envelope_factors import _project

def _fixture_project():
    project, series, points, face = _project()
    series.add_point_load(project.point(points[0]), force=(2, 0, 0),
                          envelope={"expression": "1 + t*t"})
    series.add_pressure(project.face(face), 11.0)
    series.add_wet_pressure(expression="17*(x+t)")
    return project, series, points, face


def _assert_recovery_equal(actual, expected):
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            _assert_recovery_equal(actual[key], expected[key])
    elif isinstance(expected, (str, type(None))):
        assert actual == expected
    else:
        np.testing.assert_allclose(actual, expected, rtol=1e-9, atol=1e-9)


def _step_names(project):
    return [step.name for step in project.series_children("series")]


def test_prepared_route_availability_is_explicit(monkeypatch):
    monkeypatch.delattr(anysolver, "prepare_static_evaluator", raising=False)
    with pytest.raises(PreparedStaticUnavailableError, match="prepare_static_evaluator"):
        prepare_linear_static_evaluator()


def test_prepared_results_match_the_exact_live_stream():
    project, _series, points, _face = _fixture_project()
    mesh = project.generate_mesh(1.0)
    node = mesh.node_of_vertex[points[1]]
    names = _step_names(project)
    recovery = RecoveryConfig(element_ids=[next(iter(mesh.quads or mesh.tris))],
        components=["membrane_xx", "bending_xx"], include_displacements=False,
        include_stresses=True, include_reactions=True)
    live = list(iter_linear_static_batches(
        project, mesh=mesh, load_cases=names, output_nodes=[node], output_connections=["end"],
        recovery_config=recovery))
    with prepare_linear_static_evaluator(
            project, mesh=mesh, output_nodes=[node], output_connections=["end"],
            recovery_config=recovery) as evaluator:
        prepared = list(evaluator.iter_batches(names))
    assert len(prepared) == len(live)
    for prepared_block, live_block in zip(prepared, live):
        assert prepared_block.case_ids == live_block.case_ids
        assert prepared_block.labels == live_block.labels
        _assert_recovery_equal(prepared_block.connection_motion("end"),
                               live_block.connection_motion("end"))
        np.testing.assert_allclose(
            prepared_block.displacements, live_block.displacements, rtol=0, atol=1e-9)
        assert np.max(np.abs(prepared_block.displacements)) > 0
        for index in range(len(prepared_block.case_ids)):
            assert prepared_block.stresses(index) and live_block.stresses(index)
            assert prepared_block.reactions(index) and live_block.reactions(index)
            assert any(np.any(value) for fields in prepared_block.stresses(index).values()
                       for value in fields.values())
            assert any(np.any(value) for value in prepared_block.reactions(index).values())
            _assert_recovery_equal(prepared_block.stresses(index), live_block.stresses(index))
            _assert_recovery_equal(prepared_block.reactions(index), live_block.reactions(index))


def test_prepared_results_survive_source_mutation_that_breaks_the_live_stream():
    project, _series, points, _face = _fixture_project()
    mesh = project.generate_mesh(1.0)
    node = mesh.node_of_vertex[points[1]]
    names = _step_names(project)
    with prepare_linear_static_evaluator(
            project, mesh=mesh, output_nodes=[node]) as evaluator:
        first = list(evaluator.iter_batches(names))
        # The same mutation terminates the live stream mid-case.
        mesh.nodes[next(iter(mesh.nodes))][0] += 0.1
        del project.load_cases[names[0]]
        project.geometry.add_points([(9, 9, 9)])
        second = list(evaluator.iter_batches(names))
    assert len(second) == len(first)
    for prepared_block, reference in zip(second, first):
        np.testing.assert_allclose(
            prepared_block.displacements, reference.displacements, rtol=0, atol=1e-12)


def test_solver_certifies_actual_assembled_loads():
    project, _series, points, _face = _fixture_project()
    mesh = project.generate_mesh(1.0)
    names = _step_names(project)
    with prepare_linear_static_evaluator(project, mesh=mesh) as evaluator:
        blocks = list(evaluator.iter_batches(names))
    assert blocks
    for block in blocks:
        assert block.diagnostics["status"] == "converged"
        for case_result in block.case_results:
            info = case_result["solver_info"]
            assert info["status"] == "converged"
            assert info["relative_backward_error"] <= info["relative_backward_error_limit"]
            assert info["load"]


def test_combinations_and_time_overrides_match_the_live_stream():
    project, series, points, face = _fixture_project()
    step = project.series_children("series")[0]
    project.add_combination("twice", {step.name: 2.0})
    mesh = project.generate_mesh(1.0)
    node = mesh.node_of_vertex[points[1]]
    requests = [StaticLoadRequest("label", load_case=step.name, time=time)
                for time in (0.0, 0.5, 1.0)]
    requests.append(StaticLoadRequest("combination", combination="twice", time=1.0))
    recovery = RecoveryConfig(element_ids=[next(iter(mesh.quads or mesh.tris))],
        components=["membrane_xx", "bending_xx"], include_displacements=False,
        include_stresses=True, include_reactions=True)
    live = list(iter_linear_static_batches(
        project, mesh=mesh, load_cases=requests, output_nodes=[node], recovery_config=recovery))
    with prepare_linear_static_evaluator(
            project, mesh=mesh, output_nodes=[node], recovery_config=recovery) as evaluator:
        prepared = list(evaluator.iter_batches(requests))
    assert len(prepared) == len(live)
    for prepared_block, live_block in zip(prepared, live):
        assert prepared_block.case_ids == live_block.case_ids
        assert prepared_block.times == live_block.times
        np.testing.assert_allclose(
            prepared_block.displacements, live_block.displacements, rtol=0, atol=1e-9)
        assert np.max(np.abs(prepared_block.displacements)) > 0
        for index in range(len(prepared_block.case_ids)):
            assert prepared_block.stresses(index) and live_block.stresses(index)
            assert prepared_block.reactions(index) and live_block.reactions(index)
            assert any(np.any(value) for fields in prepared_block.stresses(index).values()
                       for value in fields.values())
            assert any(np.any(value) for value in prepared_block.reactions(index).values())
            _assert_recovery_equal(prepared_block.stresses(index), live_block.stresses(index))
            _assert_recovery_equal(prepared_block.reactions(index), live_block.reactions(index))


@pytest.mark.parametrize("cancel_stage", ["prepared_static.prepare.stiffness",
                                        "prepared_static.factorization"])
def test_preparation_cancellation_releases_actual_solver_owner(cancel_stage, monkeypatch):
    from anysolver.control import CancellationToken, SolveCancelled
    import anysolver.prepared_static as solver_module
    token = CancellationToken()
    released = []
    original_release = solver_module._release_owned_resources
    def release(cache, backend):
        released.append(True)
        original_release(cache, backend)
    monkeypatch.setattr(solver_module, "_release_owned_resources", release)
    events = []
    messages = []
    def progress(event):
        events.append(event)
        if event.get("stage") == cancel_stage:
            token.cancel("cancel during preparation")
    project, _, _, _ = _fixture_project()
    with pytest.raises(SolveCancelled, match="cancel during preparation"):
        prepare_linear_static_evaluator(project, mesh=project.generate_mesh(1.0),
            cancellation_token=token, progress=messages.append, progress_callback=progress)
    assert released == [True]
    stages = [event.get("stage") for event in events]
    assert cancel_stage in stages
    assert len(stages) == len(set(stages))


def test_preparation_progress_mutation_of_app_definition_refused(monkeypatch):
    from anyfem.model import ProjectError
    import anysolver.prepared_static as solver_module
    project, series, points, _ = _fixture_project()
    released = []
    original_release = solver_module._release_owned_resources
    def release(cache, backend):
        released.append(True)
        original_release(cache, backend)
    monkeypatch.setattr(solver_module, "_release_owned_resources", release)
    def progress(event):
        if event.get("stage") == "prepared_static.prepare.stiffness":
            series.add_point_load(project.point(points[1]), force=(10, 0, 0))
    with pytest.raises(ProjectError, match="changed during prepared static capture"):
        prepare_linear_static_evaluator(project, mesh=project.generate_mesh(1.0),
            progress_callback=progress)
    assert released == [True]


def test_balanced_free_free_nonproportional_public_history_recovery_parity():
    from pathlib import Path
    import runpy
    example = runpy.run_path(str(Path(__file__).parents[1] / "examples" /
                                "prepared_static_time_history.py"))
    project, mesh, node, element, requests = example["build_example"](True)
    recovery = RecoveryConfig(element_ids=[element],
        components=["axial_stress", "torsional_stress"], include_displacements=False,
        include_stresses=True, include_reactions=True)
    live = list(iter_linear_static_batches(project, mesh=mesh, load_cases=requests,
        output_nodes=[node], recovery_config=recovery, batch_size=2))
    with prepare_linear_static_evaluator(project, mesh=mesh, output_nodes=[node],
            recovery_config=recovery, allow_unbalanced_free_free=False) as evaluator:
        prepared = list(evaluator.iter_batches(requests, batch_size=2))
    assert len(prepared) == len(live)
    for actual, expected in zip(prepared, live):
        assert actual.case_ids == expected.case_ids
        assert actual.times == expected.times
        assert np.max(np.abs(actual.displacements)) > 0
        np.testing.assert_allclose(actual.displacements, expected.displacements,
                                   rtol=0, atol=1e-9)
        for index in range(len(actual.case_ids)):
            stress = actual.stresses(index)
            assert stress and expected.stresses(index)
            assert set(stress[element]) == {"axial_stress", "torsional_stress"}
            assert all(np.any(value) for value in stress[element].values())
            _assert_recovery_equal(stress, expected.stresses(index))
            # This balanced free-free beam has no supports or MPCs. An empty
            # reaction map is the physical result, not disabled recovery.
            assert actual.case_results[index]["reaction_node_ids"]
            assert actual.case_results[index]["constraint_forces"]
            assert actual.reactions(index) == expected.reactions(index) == {}
            assert actual.case_results[index]["solver_info"]["relative_rigid_body_load_imbalance"] <= 1e-7
