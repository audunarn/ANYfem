"""Rigid-sphere impact.

There is no closed form for a plate struck by a sphere, so these check
physical invariants instead: momentum balance, energy going one way, and the
scaling a linear-elastic structure has to obey.
"""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import ImpactSolution, Project, pinned, solve_impact, steel
from anyfem.model.collision import Collision, auto_timing, impact_damage
from anyfem.solve import ContactConfigurationError
from anyfem.solve.build import build_fe_model


def struck_plate(side: float = 1.0, thickness: float = 0.008):
    project = Project(name="impact")
    project.add_material(steel("S355", thickness))
    project.add_plate_section("plate", thickness=thickness, material="S355")
    geometry = project.geometry
    points = geometry.add_points(
        [(0, 0, 0), (side, 0, 0), (side, side, 0), (0, side, 0)]
    )
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    return project


def sphere(speed: float = 4.0, mass: float = 200.0, **overrides) -> Collision:
    settings = dict(
        mass=mass, radius=0.15, start=(0.5, 0.5, 0.6),
        direction=(0.0, 0.0, -1.0), speed=speed,
    )
    settings.update(overrides)
    return Collision(**settings)


def test_impact_analysis_is_part_of_the_top_level_api():
    from anyfem.post import ImpactSolution as PostImpactSolution
    from anyfem.solve import solve_impact as solve_impact_from_solve

    assert ImpactSolution is PostImpactSolution
    assert solve_impact is solve_impact_from_solve


@pytest.fixture(scope="module")
def struck():
    """One impact, shared: the integration is the slow part."""

    project = struck_plate()
    mesh = project.generate_mesh(0.125)
    solution = solve_impact(project, collision=sphere(), mesh=mesh)
    return project, mesh, solution


# ----------------------------------------------------------------------
# the sphere
# ----------------------------------------------------------------------
def test_a_collision_states_its_energy():
    collision = sphere(speed=4.0, mass=200.0)
    assert collision.kinetic_energy == pytest.approx(0.5 * 200.0 * 16.0)
    assert np.allclose(collision.unit_direction, [0, 0, -1])
    assert "kJ" in collision.summary()


def test_an_impossible_sphere_is_refused():
    for bad in ({"mass": 0.0}, {"radius": -1.0}, {"speed": 0.0}):
        with pytest.raises(ValueError):
            sphere(**bad)
    with pytest.raises(ValueError, match="non-zero"):
        sphere(direction=(0.0, 0.0, 0.0))


# ----------------------------------------------------------------------
# timing
# ----------------------------------------------------------------------
def test_the_time_step_resolves_the_contact_period():
    """A step near the contact period breaks the contact, not just accuracy."""

    project = struck_plate()
    mesh = project.generate_mesh(0.25)
    collision = sphere()
    penalty = 1.68e11

    timing = auto_timing(mesh, collision, penalty_stiffness=penalty)
    period = 2.0 * np.pi * np.sqrt(collision.mass / penalty)
    assert timing.contact_period == pytest.approx(period)
    assert timing.dt <= period / 20.0 * (1.0 + 1e-9)


def test_without_a_penalty_the_step_is_travel_limited():
    project = struck_plate()
    mesh = project.generate_mesh(0.25)
    collision = sphere()

    timing = auto_timing(mesh, collision)
    assert timing.contact_period is None
    assert timing.dt == pytest.approx(collision.radius / (collision.speed * 20.0))


def test_the_approach_is_skipped_and_said_so():
    """Free flight is exact, so integrating it buys nothing."""

    project = struck_plate()
    mesh = project.generate_mesh(0.25)
    timing = auto_timing(mesh, sphere(), penalty_stiffness=1.68e11)

    assert timing.start is not None
    assert any("free flight" in note for note in timing.notes)
    # The sphere ends up just clear of the plate rather than far above it.
    assert timing.gap < 0.15
    assert timing.time_to_contact > 0.0


def test_the_approach_can_be_kept():
    project = struck_plate()
    mesh = project.generate_mesh(0.25)
    timing = auto_timing(
        mesh, sphere(), penalty_stiffness=1.68e11, skip_approach=False
    )
    assert timing.start is None
    assert timing.gap == pytest.approx(0.6 - 0.15)


def test_a_sphere_that_misses_is_refused():
    """A miss otherwise runs to completion and reports a clean nothing."""

    project = struck_plate()
    mesh = project.generate_mesh(0.25)
    astray = sphere(start=(5.0, 5.0, 0.6))

    with pytest.raises(ValueError, match="misses the structure"):
        auto_timing(mesh, astray)


def test_a_sphere_aimed_away_is_refused():
    project = struck_plate()
    mesh = project.generate_mesh(0.25)
    away = sphere(direction=(0.0, 0.0, 1.0))

    with pytest.raises(ValueError, match="misses the structure"):
        auto_timing(mesh, away)


def test_the_step_is_kept_when_the_run_is_capped():
    """Shorten the window rather than coarsen the step."""

    project = struck_plate()
    mesh = project.generate_mesh(0.25)
    collision = sphere()

    generous = auto_timing(mesh, collision, penalty_stiffness=1.68e11)
    capped = auto_timing(
        mesh, collision, penalty_stiffness=1.68e11, max_steps=50
    )
    assert capped.dt == pytest.approx(generous.dt)
    assert capped.t_end < generous.t_end
    assert any("shortened" in note for note in capped.notes)


# ----------------------------------------------------------------------
# the impact
# ----------------------------------------------------------------------
def test_the_impact_completes_and_conserves_momentum(struck):
    _project, _mesh, solution = struck

    assert solution.status == "completed"
    # The solver's own balance check: a badly conditioned contact shows here
    # long before it shows in the displacements.
    reference = solution.collision.mass * solution.collision.speed
    assert abs(solution.momentum_balance_error) < 1.0e-3 * reference


def test_the_sphere_actually_makes_contact(struck):
    _project, _mesh, solution = struck

    assert solution.touched()
    assert solution.peak_contact_force > 0.0
    assert solution.contact_duration > 0.0
    assert 0.0 < solution.max_penetration_ratio < 0.05


def test_energy_goes_from_the_sphere_into_the_structure(struck):
    _project, _mesh, solution = struck
    energy = solution.energy()

    assert energy["initial"] == pytest.approx(
        solution.collision.kinetic_energy, rel=1e-6
    )
    assert energy["absorbed"] > 0.0
    # The sphere cannot give up more than it arrived with.
    assert energy["absorbed"] <= energy["initial"]
    assert energy["final"] == pytest.approx(
        energy["initial"] - energy["absorbed"]
    )


def test_the_structure_responds(struck):
    _project, _mesh, solution = struck

    assert solution.peak_displacement > 0.0
    assert solution.peak_node in solution.built.mesh.nodes
    # Browsable by time, like a transient.
    assert len(solution) == len(solution.times)
    assert solution.at_time(solution.time_of_peak_force()).value > 0.0


def test_a_faster_sphere_deflects_a_linear_plate_proportionally():
    """For a linear-elastic structure, peak response scales with speed."""

    project = struck_plate()
    mesh = project.generate_mesh(0.125)
    slow = solve_impact(project, collision=sphere(speed=2.0), mesh=mesh)
    fast = solve_impact(project, collision=sphere(speed=4.0), mesh=mesh)

    ratio = fast.peak_displacement / slow.peak_displacement
    assert ratio == pytest.approx(2.0, rel=0.15)


def test_absorbed_energy_scales_with_the_square_of_speed():
    project = struck_plate()
    mesh = project.generate_mesh(0.125)
    slow = solve_impact(project, collision=sphere(speed=2.0), mesh=mesh)
    fast = solve_impact(project, collision=sphere(speed=4.0), mesh=mesh)

    ratio = fast.energy()["absorbed"] / slow.energy()["absorbed"]
    assert ratio == pytest.approx(4.0, rel=0.25)


def test_the_contact_history_lines_up_with_the_steps(struck):
    _project, _mesh, solution = struck
    history = solution.contact_history()

    assert len(history["time"]) == len(solution)
    assert len(history["contact_force"]) == len(solution)
    assert history["contact_force_vector"].shape == (len(solution), 3)
    # The magnitude is derived from the vector rather than stored separately.
    assert history["contact_force"].max() == pytest.approx(
        np.linalg.norm(history["contact_force_vector"], axis=1).max()
    )
    assert history["sphere_speed"][0] == pytest.approx(
        solution.collision.speed, rel=1e-6
    )


def test_the_penalty_is_taken_from_the_solver_recommendation(struck):
    from anysolver import recommend_sphere_contact_penalty

    project, mesh, solution = struck
    used = solution.info["contact"].penalty_stiffness
    assert used is not None and used > 0.0

    built = build_fe_model(project, mesh, load_case=None, require_loads=False)
    expected = recommend_sphere_contact_penalty(
        built.fe_model, solution.collision.to_solver()
    )
    assert used == pytest.approx(expected, rel=1e-9)


def test_an_explicit_time_step_is_honoured():
    project = struck_plate()
    mesh = project.generate_mesh(0.25)
    solution = solve_impact(
        project,
        collision=sphere(start=(0.5, 0.5, 0.16)),
        mesh=mesh,
        dt=1.0e-5,
        t_end=2.0e-3,
    )
    assert solution.timing is None
    assert solution.times[-1] == pytest.approx(2.0e-3, rel=0.05)


def test_a_rejected_contact_configuration_stops_the_run():
    """A bad contact does not fail loudly; it returns plausible nonsense."""

    project = Project(name="beams only")
    project.add_material(steel("S355", 0.020))
    from anyfem.model import BeamSection

    project.add_beam_section(
        BeamSection(
            name="bar", profile="Flatbar", material="S355",
            flange_width=0.05, flange_thickness=0.02,
        )
    )
    geometry = project.geometry
    start = geometry.add_point(0.0, 0.0, 0.0)
    end = geometry.add_point(1.0, 0.0, 0.0)
    edge = geometry.add_line(start, end)
    project.assign_beam(edge, "bar")
    from anyfem.model import fixed

    project.add_support(fixed(project.point(start)))

    # No shells to strike, and beam contact is off by default.
    with pytest.raises((ContactConfigurationError, ValueError)):
        solve_impact(
            project,
            collision=sphere(start=(0.5, 0.0, 0.5)),
            target_size=0.25,
        )


def test_progress_is_reported(struck):
    project = struck_plate()
    mesh = project.generate_mesh(0.25)
    messages = []
    solve_impact(
        project, collision=sphere(), mesh=mesh, progress=messages.append
    )
    assert messages
    assert any("penalty" in message for message in messages)
    assert any("dt =" in message for message in messages)


def test_damage_can_be_asked_for():
    """A soft plate hit hard should give up elements.

    This particular impact -- 3 mm plating, 500 kg at 20 m/s on a 0.125 m mesh
    -- does not converge: the contact iteration gives up part-way. That is
    worth stating rather than tolerating, so the run is asked for explicitly
    with ``strict=False`` and only its *contract* is checked. The numbers it
    reports are not a structural response and are deliberately not asserted.
    """

    project = struck_plate(thickness=0.003)
    mesh = project.generate_mesh(0.125)
    solution = solve_impact(
        project,
        collision=sphere(speed=20.0, mass=500.0),
        mesh=mesh,
        damage=impact_damage(delete_at=1.0),
        strict=False,
    )
    # Whether elements go is physics; that the field exists and is a tuple of
    # element IDs is the contract.
    assert isinstance(solution.deleted_elements, tuple)
    assert all(
        element in solution.built.mesh.quads
        for element in solution.deleted_elements
    )


def test_a_failed_contact_iteration_is_refused_by_default():
    """A run that gave up part-way must not come back looking like an answer.

    Its peak force and absorbed energy are whatever the integration reached
    before diverging. Returning them is worse than failing, because they are
    the right order of magnitude and carry no warning.
    """

    project = struck_plate(thickness=0.003)
    mesh = project.generate_mesh(0.125)
    with pytest.raises(ContactConfigurationError, match="rather than completing"):
        solve_impact(
            project,
            collision=sphere(speed=20.0, mass=500.0),
            mesh=mesh,
            damage=impact_damage(delete_at=1.0),
        )


def test_the_summary_says_what_happened(struck):
    _project, _mesh, solution = struck
    text = solution.summary()
    assert "peak contact force" in text
    assert "kJ absorbed" in text


# ----------------------------------------------------------------------
# drawing
# ----------------------------------------------------------------------
def test_the_sphere_and_its_path_are_drawn(struck):
    from anyfem.ui.scene import build_collision_overlay

    _project, _mesh, solution = struck
    overlay = build_collision_overlay(solution, index=len(solution) // 2)

    assert len(overlay.spheres) == 1
    assert overlay.spheres[0].radius == pytest.approx(solution.collision.radius)
    assert overlay.lines, "the travel path should be drawn"
    # Annotations never carry an entity tag, so they cannot steal a pick.
    assert overlay.tags() == []


def test_the_overlay_follows_the_step(struck):
    from anyfem.ui.scene import build_collision_overlay

    _project, _mesh, solution = struck
    first = build_collision_overlay(solution, 0).spheres[0].centre
    last = build_collision_overlay(solution, len(solution) - 1).spheres[0].centre
    assert first[2] > last[2], "the sphere should have travelled downwards"


def test_the_overlay_bounds_include_the_sphere(struck):
    from anyfem.ui.scene import build_collision_overlay

    _project, _mesh, solution = struck
    overlay = build_collision_overlay(solution, 0)
    low, high = overlay.bounds()
    assert np.all(np.isfinite(low)) and np.all(np.isfinite(high))
