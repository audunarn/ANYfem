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

    _assert_recovered_on(solution, recover(solution), solved)
    solution._stress = None
    _assert_recovered_on(solution, solution.stresses(), solved)
    increment = solution.shapes[-1]
    _assert_recovered_on(increment, recover(increment), solved)
    increment._stress = None
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

    _assert_recovered_on(solution, recover(solution), solved)
    solution._stress = None
    _assert_recovered_on(solution, solution.stresses(), solved)


def test_capacity_recovers_on_the_workflow_imperfect_model(plate_and_mode):
    project, mesh, _buckling = plate_and_mode
    solution = solve_capacity(
        project, mesh=mesh, num_buckling_modes=1,
        imperfection_amplitude=AMPLITUDE, num_steps=1, max_load_factor=1.0,
    )
    solved = solution.info["raw"].imperfect_model

    _assert_recovered_on(solution, recover(solution), solved)
    solution._stress = None
    _assert_recovered_on(solution, solution.stresses(), solved)
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
    from anyfem.post.solved_geometry import owned_imperfection_field

    other = owned_imperfection_field(
        solution.built.fe_model,
        eigenmode_imperfection(buckling, 1, amplitude=2.0 * AMPLITUDE),
    )
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


def test_an_unbound_parent_makes_its_increments_refuse(plate_and_mode):
    from anyfem.post.results import (
        CapacitySolution, NonlinearSolution, UnboundSolvedGeometryError,
    )

    project, mesh, buckling = plate_and_mode
    imperfection = eigenmode_imperfection(buckling, 1, amplitude=AMPLITUDE)
    nonlinear = solve_nonlinear_static(
        project, mesh=mesh, imperfection=imperfection, max_load_factor=0.5,
        num_steps=1, record_increment_snapshots=True,
    )
    capacity = solve_capacity(
        project, mesh=mesh, num_buckling_modes=1, imperfection_amplitude=AMPLITUDE,
        num_steps=1, max_load_factor=0.5, record_increment_snapshots=True,
    )
    for kind, solution in ((NonlinearSolution, nonlinear), (CapacitySolution, capacity)):
        unbound = kind(
            displacements=solution.displacements, built=solution.built,
            raw_result=solution.raw_result, info=dict(solution.info),
            steps=list(solution.steps),
        )
        assert unbound.shapes
        increment = unbound.shapes[-1]
        with pytest.raises(UnboundSolvedGeometryError):
            increment.stresses()
        with pytest.raises(UnboundSolvedGeometryError):
            recover(increment)


class _FlippingImperfection:
    """A stateful converter: each conversion returns the opposite offsets."""

    def __init__(self, field):
        self.field = field
        self.calls = 0

    def to_field(self, model):
        from anyfesolver.imperfections import ImperfectionField

        self.calls += 1
        sign = 1.0 if self.calls % 2 else -1.0
        return ImperfectionField(
            {node: sign * np.asarray(offset) for node, offset in self.field.offsets.items()},
            name=self.field.name, metadata=dict(self.field.metadata),
        )


class _OffsetDict(dict):
    """A mapping subclass whose converter disagrees with its own entries."""

    def to_field(self, model):
        from anyfesolver.imperfections import ImperfectionField

        return ImperfectionField({node: -np.asarray(v) for node, v in self.items()})


def test_untrusted_imperfection_inputs_go_to_the_solver_unchanged_and_refuse_recovery(
    plate_and_mode,
):
    from anyfesolver.imperfections import to_imperfection_field

    from anyfem.post.results import UnboundSolvedGeometryError

    project, mesh, buckling = plate_and_mode
    base_field = to_imperfection_field(
        buckling.built.fe_model, eigenmode_imperfection(buckling, 1, amplitude=AMPLITUDE)
    )
    flipping = _FlippingImperfection(base_field)
    solution = solve_nonlinear_static(
        project, mesh=mesh, imperfection=flipping, max_load_factor=0.5, num_steps=1,
    )
    # Only the solver converted it, under its own guard.
    assert flipping.calls == 1
    direct = solve_nonlinear_static(
        project, mesh=mesh, imperfection=base_field, max_load_factor=0.5, num_steps=1,
    )
    np.testing.assert_array_equal(solution.displacements, direct.displacements)
    with pytest.raises(UnboundSolvedGeometryError):
        solution.stresses()
    with pytest.raises(UnboundSolvedGeometryError):
        recover(solution)

    subclass = _OffsetDict({node: offset for node, offset in base_field.offsets.items()})
    by_subclass = solve_nonlinear_static(
        project, mesh=mesh, imperfection=subclass, max_load_factor=0.5, num_steps=1,
    )
    as_plain = solve_nonlinear_static(
        project, mesh=mesh, imperfection=dict(subclass), max_load_factor=0.5, num_steps=1,
    )
    # The solver reads a mapping as its entries, exactly as before this change.
    np.testing.assert_array_equal(by_subclass.displacements, as_plain.displacements)
    with pytest.raises(UnboundSolvedGeometryError):
        by_subclass.stresses()
    _assert_recovered_on(
        as_plain, as_plain.stresses(), _imperfect_copy(as_plain.built.fe_model, dict(subclass))
    )


def test_invalid_imperfection_metadata_is_still_refused_by_the_solver(plate_and_mode):
    from anyfesolver.imperfections import ImperfectionField

    project, mesh, buckling = plate_and_mode
    offsets = {node: (0.0, 0.0, 1.0e-3) for node in list(buckling.built.fe_model.mesh.nodes)[:3]}
    bad = ImperfectionField(offsets, metadata=[("source", "test")])
    with pytest.raises(TypeError):
        solve_nonlinear_static(
            project, mesh=mesh, imperfection=bad, max_load_factor=0.5, num_steps=1,
        )


def test_edits_to_the_built_model_after_the_solve_do_not_reach_the_solved_copy(plate_and_mode):
    project, mesh, buckling = plate_and_mode
    imperfection = eigenmode_imperfection(buckling, 1, amplitude=AMPLITUDE)
    solution = solve_nonlinear_static(
        project, mesh=mesh, imperfection=imperfection, max_load_factor=0.5, num_steps=1,
    )
    solved = _imperfect_copy(solution.built.fe_model, imperfection)
    element_id = next(iter(solution.built.fe_model.mesh.elements))
    original = solution.built.fe_model.mesh.elements[element_id].thickness
    try:
        solution.built.fe_model.mesh.elements[element_id].thickness = 2.0 * original
        next(iter(solution.built.fe_model.mesh.nodes.values())).z += 1.0e-3
        model = solution.solved_model()
        assert model.mesh.elements[element_id].thickness == original
        np.testing.assert_array_equal(_coordinates(model), _coordinates(solved))
    finally:
        solution.built.fe_model.mesh.elements[element_id].thickness = original
        next(iter(solution.built.fe_model.mesh.nodes.values())).z -= 1.0e-3


def test_a_bound_result_recovers_identically_after_job_transport(plate_and_mode):
    from anyfem.job_process import dumps, loads

    project, mesh, buckling = plate_and_mode
    imperfection = eigenmode_imperfection(buckling, 1, amplitude=AMPLITUDE)
    for solve in (
        lambda: solve_nonlinear_static(
            project, mesh=mesh, imperfection=imperfection, max_load_factor=0.5, num_steps=1),
        lambda: solve_capacity(
            project, mesh=mesh, num_buckling_modes=1, imperfection_amplitude=AMPLITUDE,
            num_steps=1, max_load_factor=0.5),
    ):
        solution = solve()
        transported = loads(dumps(solution))  # before any recovery: nothing cached
        assert transported._stress is None
        assert transported.solved_model() is not transported.built.fe_model
        np.testing.assert_array_equal(
            _coordinates(transported.solved_model()), _coordinates(solution.solved_model())
        )
        expected = solution.stresses()
        for key in GEOMETRY_KEYS:
            np.testing.assert_array_equal(
                _field(recover(transported), key), _field(expected, key)
            )
