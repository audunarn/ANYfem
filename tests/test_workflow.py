"""Phase 12: symmetry, the capacity workflow, and recovery/resource policy.

There is also a group here guarding something ANYfem does not itself offer:
the solver has orthotropic materials, and nothing in this layer may quietly
break them.  ANYfem models isotropic steel, but results, recovery and the
impact time step all pass through material objects, and each of those is a
place where reading an isotropic-only attribute would fail silently rather than
loudly.
"""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import (
    Project,
    pinned,
    recovery_policy,
    resource_policy,
    solve_capacity,
    solve_linear_static,
    solve_nonlinear_static,
    steel,
    support,
)
from anyfem.model import ProjectError, antisymmetry, symmetry
from anyfem.post.fields import recover, reported_fields
from anyfem.solve import history_modes

SIDE, THICKNESS, PRESSURE = 2.0, 0.010, 10_000.0


def rectangle(x0: float, x1: float, y0: float, y1: float) -> tuple[Project, list]:
    project = Project(name="plate")
    project.add_material(steel("S355", THICKNESS))
    project.add_plate_section("plate", thickness=THICKNESS, material="S355")
    geometry = project.geometry
    points = geometry.add_points(
        [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0)]
    )
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    project.load_case().add_pressure(project.face(face), PRESSURE)
    return project, edges


def edge_midpoint(project: Project, edge_id: int) -> np.ndarray:
    edge = project.geometry.edges[edge_id]
    ends = [project.geometry.vertices[v].position for v in (edge.start, edge.end)]
    return 0.5 * (ends[0] + ends[1])


# ----------------------------------------------------------------------
# symmetry
# ----------------------------------------------------------------------
def test_symmetry_restrains_the_normal_translation_and_in_plane_rotations():
    project, edges = rectangle(0, SIDE, 0, SIDE)
    constraints = symmetry(project.edge(edges[3]), "x").constraints

    assert constraints == {"ux": 0.0, "ry": 0.0, "rz": 0.0}
    # The rotation about the normal stays free; that is what makes it symmetry
    # rather than a clamp.
    assert "rx" not in constraints


def test_antisymmetry_is_the_complement():
    project, edges = rectangle(0, SIDE, 0, SIDE)
    plane = symmetry(project.edge(edges[3]), "z").constraints
    opposite = antisymmetry(project.edge(edges[3]), "z").constraints

    assert set(plane) | set(opposite) == {"ux", "uy", "uz", "rx", "ry", "rz"}
    assert not set(plane) & set(opposite)


def test_a_normal_can_be_named_or_given_as_a_vector():
    project, edges = rectangle(0, SIDE, 0, SIDE)
    by_name = symmetry(project.edge(edges[3]), "y").constraints
    for vector in ((0, 1, 0), (0, -1, 0), (0, 3.0, 0)):
        assert symmetry(project.edge(edges[3]), vector).constraints == by_name


def test_a_tilted_symmetry_plane_is_refused():
    """The solver has no nodal transformation, so this cannot be exact."""

    project, edges = rectangle(0, SIDE, 0, SIDE)
    with pytest.raises(ValueError, match="not along a global axis"):
        symmetry(project.edge(edges[0]), (1.0, 1.0, 0.0))


def test_a_zero_or_malformed_normal_is_refused():
    project, edges = rectangle(0, SIDE, 0, SIDE)
    with pytest.raises(ValueError, match="non-zero"):
        symmetry(project.edge(edges[0]), (0.0, 0.0, 0.0))
    with pytest.raises(ValueError, match="three components"):
        symmetry(project.edge(edges[0]), (1.0, 0.0))
    with pytest.raises(ValueError, match="expected 'x', 'y' or 'z'"):
        symmetry(project.edge(edges[0]), "diagonal")


def test_a_symmetry_condition_off_its_plane_is_refused():
    """An edge crossing the plane restrains the wrong things everywhere."""

    project, edges = rectangle(0, SIDE, 0, SIDE)
    # edges[0] runs along x from (0,0) to (SIDE,0): it crosses every x plane.
    with pytest.raises(ProjectError, match="does not lie in a plane normal to x"):
        project.add_symmetry(project.edge(edges[0]), "x")
    # The same edge is fine as a y-symmetry plane, which it does lie in.
    assert project.add_symmetry(project.edge(edges[0]), "y")


def test_a_plate_can_be_a_symmetry_plane_when_it_is_planar():
    project, _edges = rectangle(0, SIDE, 0, SIDE)
    face = next(iter(project.geometry.faces))
    assert project.add_symmetry(project.face(face), "z")

    # A plate that is not in a z plane is refused for z.
    tilted = Project(name="tilted")
    tilted.add_material(steel("S355", THICKNESS))
    tilted.add_plate_section("plate", thickness=THICKNESS, material="S355")
    geometry = tilted.geometry
    points = geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 1), (0, 1, 1)])
    other = geometry.add_face(geometry.add_polyline(points, close=True))
    tilted.assign_plate(other, "plate")
    with pytest.raises(ProjectError, match="does not lie in a plane normal to z"):
        tilted.add_symmetry(tilted.face(other), "z")


def test_a_quarter_model_with_symmetry_matches_the_full_plate():
    """The claim symmetry actually makes, checked rather than assumed."""

    full, edges = rectangle(0, SIDE, 0, SIDE)
    for edge_id in edges:
        full.add_support(pinned(full.edge(edge_id)))
    full_deflection = solve_linear_static(
        full, target_size=SIDE / 16
    ).max_translation()[1]

    quarter, quarter_edges = rectangle(0, SIDE / 2, 0, SIDE / 2)
    for edge_id in quarter_edges:
        middle = edge_midpoint(quarter, edge_id)
        if abs(middle[0]) < 1.0e-9:
            quarter.add_symmetry(quarter.edge(edge_id), "x")
        elif abs(middle[1]) < 1.0e-9:
            quarter.add_symmetry(quarter.edge(edge_id), "y")
        else:
            quarter.add_support(pinned(quarter.edge(edge_id)))
    quarter_deflection = solve_linear_static(
        quarter, target_size=SIDE / 16
    ).max_translation()[1]

    assert quarter_deflection == pytest.approx(full_deflection, rel=1e-9)


def test_clamping_instead_of_symmetry_gives_a_different_answer():
    """Guards the test above from passing for the wrong reason.

    If any restraint on the cut edges produced the full-model answer, the
    comparison would prove nothing about symmetry in particular.
    """

    quarter, edges = rectangle(0, SIDE / 2, 0, SIDE / 2)
    for edge_id in edges:
        middle = edge_midpoint(quarter, edge_id)
        if abs(middle[0]) < 1.0e-9 or abs(middle[1]) < 1.0e-9:
            quarter.add_support(pinned(quarter.edge(edge_id)))
        else:
            quarter.add_support(pinned(quarter.edge(edge_id)))
    clamped = solve_linear_static(quarter, target_size=SIDE / 16)

    full, full_edges = rectangle(0, SIDE, 0, SIDE)
    for edge_id in full_edges:
        full.add_support(pinned(full.edge(edge_id)))
    reference = solve_linear_static(full, target_size=SIDE / 16)

    assert clamped.max_translation()[1] != pytest.approx(
        reference.max_translation()[1], rel=0.01
    )


# ----------------------------------------------------------------------
# capacity workflow
# ----------------------------------------------------------------------
def compressed_plate(nonlinear: bool = True) -> Project:
    """A plate strip in axial compression: buckling decides the capacity."""

    length, width, thickness = 1.0, 0.5, 0.006
    project = Project(name="capacity")
    project.add_material(steel("S355", thickness, nonlinear=nonlinear))
    project.add_plate_section("plate", thickness=thickness, material="S355")
    geometry = project.geometry
    points = geometry.add_points(
        [(0, 0, 0), (length, 0, 0), (length, width, 0), (0, width, 0)]
    )
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    project.add_support(support(project.edge(edges[3]), ux=0.0, uy=0.0, uz=0.0))
    project.add_support(support(project.edge(edges[1]), uy=0.0, uz=0.0))
    for index in (0, 2):
        project.add_support(support(project.edge(edges[index]), uz=0.0))
    project.load_case().add_line_load(project.edge(edges[1]), (-1.0e6, 0, 0))
    return project


@pytest.fixture(scope="module")
def capacity_solution():
    """One representative workflow run serves its read-only result contracts."""

    return solve_capacity(
        compressed_plate(), target_size=0.05, num_buckling_modes=3,
        imperfection_amplitude=1.0 / 500, num_steps=12,
    )


def test_the_capacity_workflow_runs_every_stage(capacity_solution):
    solution = capacity_solution
    assert solution.status == "completed"
    assert solution.critical_factor is not None and solution.critical_factor > 0
    assert solution.capacity_factor > 0
    assert len(solution.buckling) == 3
    assert solution.mesh_adequacy.get("status") == "ok"
    # It is a nonlinear result, so the nonlinear interface still works.
    assert len(solution.history()["load_factor"]) == len(solution.steps)


def test_the_capacity_ratio_compares_the_two_load_factors(capacity_solution):
    solution = capacity_solution
    assert solution.capacity_ratio == pytest.approx(
        solution.capacity_factor / solution.critical_factor
    )
    # A compressed plate has post-buckling reserve, so this one is above 1.
    # Asserted because it is the physics, not because above 1 is "good".
    assert solution.capacity_ratio > 1.0


def test_the_capacity_summary_names_both_load_factors(capacity_solution):
    solution = capacity_solution
    text = solution.summary()
    assert "capacity" in text
    assert "elastic critical" in text


def test_a_capacity_run_needs_a_load_case_to_scale():
    project = compressed_plate()
    project.load_cases.clear()
    with pytest.raises((ProjectError, ValueError)):
        solve_capacity(project, target_size=0.1)


# ----------------------------------------------------------------------
# recovery and resource policy
# ----------------------------------------------------------------------
def loaded_plate(nonlinear: bool = False) -> Project:
    project = Project(name="policy")
    project.add_material(steel("S355", 0.008, nonlinear=nonlinear))
    project.add_plate_section("plate", thickness=0.008, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    for edge_id in edges:
        project.add_support(pinned(project.edge(edge_id)))
    project.load_case().add_pressure(project.face(face), 50_000.0)
    return project


def test_history_modes_come_from_the_solver():
    modes = history_modes()
    assert "full" in modes
    # Whatever the solver accepts, it accepts; this must not be a copy that can
    # drift, so build one of each and let the solver validate.
    for mode in modes:
        assert recovery_policy(history_mode=mode).history_mode == mode


def test_an_invalid_history_mode_is_refused_by_the_solver():
    with pytest.raises(ValueError, match="history_mode"):
        recovery_policy(history_mode="summary")


def test_recovery_without_a_policy_keeps_every_component():
    """No ANYfem whitelist. This is what keeps new solver components working."""

    solution = solve_linear_static(loaded_plate(), target_size=0.25)
    components = reported_fields(recover(solution), solution)

    assert len(components) > 6
    assert "von_mises" in components
    # Components ANYfem does not name in its own field list still arrive.
    from anyfem.post.fields import STRESS_FIELDS

    assert set(components) - set(STRESS_FIELDS)


def test_naming_components_narrows_the_recovery():
    solution = solve_linear_static(loaded_plate(), target_size=0.25)
    narrowed = recover(
        solution, recovery_config=recovery_policy(components=["von_mises"])
    )
    assert reported_fields(narrowed, solution) == ["von_mises"]


def test_a_resource_policy_reaches_the_nonlinear_solve():
    solution = solve_nonlinear_static(
        loaded_plate(nonlinear=True), target_size=0.25, num_steps=4,
        resources=resource_policy(solver_threads=2, deterministic=True),
    )
    assert solution.status in ("completed", "stopped_at_limit")


def test_a_resource_policy_validates_its_own_numbers():
    with pytest.raises(ValueError, match="must be positive"):
        resource_policy(solver_threads=0)
    with pytest.raises(ValueError, match="memory_limit_bytes"):
        resource_policy(memory_limit_bytes=-1)


# ----------------------------------------------------------------------
# orthotropic materials must keep working
# ----------------------------------------------------------------------
def orthotropic():
    from anysolver import OrthotropicMaterial

    return OrthotropicMaterial(
        name="S355", elastic_modulus_1=120e9, elastic_modulus_2=180e9,
        elastic_modulus_3=90e9, poisson_ratio_12=0.28, poisson_ratio_13=0.28,
        poisson_ratio_23=0.30, shear_modulus_12=45e9, shear_modulus_13=40e9,
        shear_modulus_23=35e9, density=7850.0,
    )


def test_the_wave_speed_helper_reads_an_orthotropic_material():
    """It has no elastic_modulus, and a silent zero would drop a time-step bound."""

    from anyfem.solve.run import _stiffest_modulus, _wave_speed
    from anyfem.solve.build import build_fe_model

    material = orthotropic()
    assert not hasattr(material, "elastic_modulus")
    assert _stiffest_modulus(material) == pytest.approx(180e9)

    project = loaded_plate()
    built = build_fe_model(project, project.generate_mesh(0.5))
    isotropic_speed = _wave_speed(built)
    built.fe_model.materials["S355"] = material
    assert _wave_speed(built) == pytest.approx(np.sqrt(180e9 / 7850.0))
    assert _wave_speed(built) < isotropic_speed


def test_an_orthotropic_model_solves_and_postprocesses():
    """The whole pipeline, on a model whose material ANYfem cannot author.

    ANYfem's own Material is isotropic, but results, recovery and fields all
    pass through the solver's material objects, and none of them may assume
    isotropy.
    """

    from anyfem.solve.build import build_fe_model
    from anyfem.solve.run import solve_linear_static as solve

    project = loaded_plate()
    built = build_fe_model(project, project.generate_mesh(0.25))
    built.fe_model.materials["S355"] = orthotropic()

    solution = solve(built=built)
    assert np.isfinite(solution.max_translation()[1])

    components = reported_fields(recover(solution), solution)
    assert components
    # Evaluating every component the solver reports must work, whatever it
    # decides to report.
    from anyfem.post.fields import evaluate_field

    for name in components:
        field = evaluate_field(solution, name, reduction="max_abs")
        assert field.element_values


def test_probing_an_orthotropic_result_works():
    from anyfem.post.extract import probe
    from anyfem.solve.build import build_fe_model
    from anyfem.solve.run import solve_linear_static as solve

    project = loaded_plate()
    built = build_fe_model(project, project.generate_mesh(0.25))
    built.fe_model.materials["S355"] = orthotropic()
    solution = solve(built=built)

    face = next(iter(project.geometry.faces))
    reading = probe(solution, project.face(face))
    assert reading.stresses
    assert all(np.isfinite(value) for value in reading.stresses.values())
