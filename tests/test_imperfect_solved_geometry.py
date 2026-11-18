"""Stress recovery is bound to the geometry the nonlinear solve actually ran on.

A run-time ``imperfection`` (nonlinear static, arc length) and the capacity
workflow's eigenmode imperfection are applied by the solver to a *copy* of the
built model.  The displacements are measured from that imperfect reference, so
recovering them on the perfect ``built.fe_model`` mixes two geometries: the
transverse-shear resultants of a compressed plate differ by more than their own
size.  Project imperfections applied at build time already live in
``built.fe_model`` and need no rebinding.
"""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import (
    Project,
    eigenmode_imperfection,
    solve_arc_length,
    solve_buckling,
    solve_capacity,
    solve_nonlinear_static,
    steel,
)
from anyfem.model import support
from anyfem.model.imperfections import Imperfection
from anyfem.post.fields import recover

AMPLITUDE = 0.005
GEOMETRY_KEYS = ("shear_xz", "shear_yz", "von_mises")


def compressed_plate():
    project = Project(name="plate")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    project.add_support(support(project.edge(edges[3]), ux=0.0, uz=0.0))
    project.add_support(support(project.edge(edges[0]), uy=0.0, uz=0.0))
    project.add_support(support(project.edge(edges[1]), uz=0.0))
    project.add_support(support(project.edge(edges[2]), uz=0.0))
    project.load_case().add_line_load(project.edge(edges[1]), (-2.0e5, 0.0, 0.0))
    return project, face


@pytest.fixture(scope="module")
def plate_and_mode():
    project, face = compressed_plate()
    mesh = project.generate_mesh(0.25)
    buckling = solve_buckling(project, mesh=mesh, num_modes=1)
    return project, mesh, buckling


def _field(result, key):
    return np.concatenate(
        [
            np.asarray(result.element_stresses[element][key], dtype=float).ravel()
            for element in sorted(result.element_stresses)
        ]
    )


def _coordinates(model):
    return np.array(
        [[node.x, node.y, node.z] for _, node in sorted(model.mesh.nodes.items())]
    )


def _assert_recovered_on(view, recovered, solved_model):
    from anyfesolver import recover_stress_result

    expected = recover_stress_result(
        solved_model, view.displacements, nonlinear_result=view.raw_result
    )
    perfect = recover_stress_result(
        view.built.fe_model, view.displacements, nonlinear_result=view.raw_result
    )
    # The case must be sensitive: otherwise the equality below proves nothing.
    assert not np.array_equal(_field(expected, "shear_yz"), _field(perfect, "shear_yz"))
    for key in GEOMETRY_KEYS:
        np.testing.assert_array_equal(_field(recovered, key), _field(expected, key))


def _imperfect_copy(base, imperfection):
    from anyfesolver import apply_imperfection

    return apply_imperfection(base, imperfection, copy_model=True)


def test_nonlinear_runtime_imperfection_recovers_on_the_solved_geometry(plate_and_mode):
    project, mesh, buckling = plate_and_mode
    imperfection = eigenmode_imperfection(buckling, 1, amplitude=AMPLITUDE)
    solution = solve_nonlinear_static(
        project, mesh=mesh, imperfection=imperfection,
        max_load_factor=1.0, num_steps=1, record_increment_snapshots=True,
    )
    solved = _imperfect_copy(solution.built.fe_model, imperfection)

    _assert_recovered_on(solution, solution.stresses(), solved)
    _assert_recovered_on(solution, recover(solution), solved)
    increment = solution.shapes[-1]
    _assert_recovered_on(increment, increment.stresses(), solved)
    np.testing.assert_array_equal(
        _coordinates(solution.solved_model()), _coordinates(solved)
    )


def test_arc_length_runtime_imperfection_recovers_on_the_solved_geometry(plate_and_mode):
    from anyfesolver import ArcLengthControl

    project, mesh, buckling = plate_and_mode
    imperfection = eigenmode_imperfection(buckling, 1, amplitude=AMPLITUDE)
    solution = solve_arc_length(
        project, mesh=mesh, imperfection=imperfection,
        control=ArcLengthControl(max_steps=2, maximum_absolute_load_factor=1.0),
    )
    solved = _imperfect_copy(solution.built.fe_model, imperfection)

    _assert_recovered_on(solution, solution.stresses(), solved)
    _assert_recovered_on(solution, recover(solution), solved)


def test_capacity_recovers_on_the_workflow_imperfect_model(plate_and_mode):
    project, mesh, _buckling = plate_and_mode
    solution = solve_capacity(
        project, mesh=mesh, num_buckling_modes=1,
        imperfection_amplitude=AMPLITUDE, num_steps=1, max_load_factor=1.0,
    )
    solved = solution.info["raw"].imperfect_model

    _assert_recovered_on(solution, solution.stresses(), solved)
    _assert_recovered_on(solution, recover(solution), solved)
    assert solution.solved_model() is solved


def test_runs_without_an_imperfection_keep_the_built_model(plate_and_mode):
    project, mesh, _buckling = plate_and_mode
    nonlinear = solve_nonlinear_static(project, mesh=mesh, max_load_factor=0.5, num_steps=1)
    assert nonlinear.solved_model() is nonlinear.built.fe_model
    capacity = solve_capacity(
        project, mesh=mesh, num_buckling_modes=1,
        imperfection_amplitude=0.0, num_steps=1, max_load_factor=0.5,
    )
    # A zero eigenmode offset leaves every coordinate unchanged, so the
    # ordinary route keeps recovering on the built model, bit for bit.
    assert capacity.solved_model() is capacity.built.fe_model


def test_build_time_imperfection_is_already_in_the_built_model():
    project, face = compressed_plate()
    project.imperfections.append(
        Imperfection(name="dent", ref=project.face(face), amplitude=AMPLITUDE,
                     kind="plate_mode", direction=(0.0, 0.0, 1.0))
    )
    mesh = project.generate_mesh(0.25)
    solution = solve_nonlinear_static(project, mesh=mesh, max_load_factor=0.5, num_steps=1)

    assert np.max(np.abs(_coordinates(solution.built.fe_model)[:, 2])) > 0.5 * AMPLITUDE
    assert solution.solved_model() is solution.built.fe_model


def test_build_time_and_runtime_imperfections_stack():
    project, face = compressed_plate()
    project.imperfections.append(
        Imperfection(name="dent", ref=project.face(face), amplitude=AMPLITUDE,
                     kind="plate_mode", direction=(0.0, 0.0, 1.0))
    )
    mesh = project.generate_mesh(0.25)
    buckling = solve_buckling(project, mesh=mesh, num_modes=1)
    imperfection = eigenmode_imperfection(buckling, 1, amplitude=AMPLITUDE)
    solution = solve_nonlinear_static(
        project, mesh=mesh, imperfection=imperfection, max_load_factor=0.5, num_steps=1,
    )
    solved = _imperfect_copy(solution.built.fe_model, imperfection)
    _assert_recovered_on(solution, solution.stresses(), solved)


def test_an_unbound_runtime_imperfection_refuses_recovery(plate_and_mode):
    from anyfem.post.results import NonlinearSolution, UnboundSolvedGeometryError

    project, mesh, buckling = plate_and_mode
    imperfection = eigenmode_imperfection(buckling, 1, amplitude=AMPLITUDE)
    solution = solve_nonlinear_static(
        project, mesh=mesh, imperfection=imperfection, max_load_factor=0.5, num_steps=1,
    )
    unbound = NonlinearSolution(
        displacements=solution.displacements, built=solution.built,
        raw_result=solution.raw_result, info=dict(solution.info),
    )
    with pytest.raises(UnboundSolvedGeometryError):
        unbound.stresses()
    with pytest.raises(UnboundSolvedGeometryError):
        recover(unbound)


def test_a_rebuilt_geometry_that_disagrees_with_the_solver_is_refused(plate_and_mode):
    from anyfem.post.results import SolvedGeometry, UnboundSolvedGeometryError

    project, mesh, buckling = plate_and_mode
    imperfection = eigenmode_imperfection(buckling, 1, amplitude=AMPLITUDE)
    solution = solve_nonlinear_static(
        project, mesh=mesh, imperfection=imperfection, max_load_factor=0.5, num_steps=1,
    )
    other = eigenmode_imperfection(buckling, 1, amplitude=2.0 * AMPLITUDE)
    with pytest.raises(UnboundSolvedGeometryError):
        SolvedGeometry.rebuild(
            solution.built.fe_model, other, solution.raw_result, context="test"
        )


def test_an_unbound_capacity_imperfection_refuses_recovery(plate_and_mode):
    from anyfem.post.results import CapacitySolution, UnboundSolvedGeometryError

    project, mesh, _buckling = plate_and_mode
    solution = solve_capacity(
        project, mesh=mesh, num_buckling_modes=1,
        imperfection_amplitude=AMPLITUDE, num_steps=1, max_load_factor=0.5,
    )
    unbound = CapacitySolution(
        displacements=solution.displacements, built=solution.built,
        raw_result=solution.raw_result, info=dict(solution.info),
    )
    with pytest.raises(UnboundSolvedGeometryError):
        unbound.stresses()


def test_a_rebuilt_binding_survives_job_transport_and_refuses_moved_geometry(plate_and_mode):
    from anyfem.job_process import dumps, loads
    from anyfem.post.results import UnboundSolvedGeometryError

    project, mesh, buckling = plate_and_mode
    imperfection = eigenmode_imperfection(buckling, 1, amplitude=AMPLITUDE)
    solution = solve_nonlinear_static(
        project, mesh=mesh, imperfection=imperfection, max_load_factor=0.5, num_steps=1,
    )
    expected = solution.stresses()
    assert solution.solved_geometry.rebuilt

    copy = loads(dumps(solution))
    assert copy.solved_geometry._model is None  # the copy is rebuilt, not shipped
    for key in GEOMETRY_KEYS:
        np.testing.assert_array_equal(_field(copy.stresses(), key), _field(expected, key))

    moved = loads(dumps(solution))
    node = next(iter(moved.built.fe_model.mesh.nodes.values()))
    node.z += 1.0e-3
    with pytest.raises(UnboundSolvedGeometryError):
        moved.solved_model()
