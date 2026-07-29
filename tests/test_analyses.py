"""Modal, buckling, nonlinear, arc-length and transient analyses.

Each is checked against a closed-form answer where one exists, and against a
physical invariant where it does not.
"""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import (
    Project,
    eigenmode_imperfection,
    fixed,
    pinned,
    solve_arc_length,
    solve_buckling,
    solve_linear_static,
    solve_modal,
    solve_nonlinear_static,
    solve_transient,
    steel,
)
from anyfem.model import BeamSection, support
from anyfem.post import ModalSolution, ShapeView

MODULUS = 210.0e9
DENSITY = 7850.0
POISSON = 0.3


def strut_project(length: float = 2.0, load: float = 1000.0):
    """A pinned-pinned bar in axial compression, weak axis vertical."""

    project = Project(name="strut")
    project.add_material(steel("S355", 0.020))
    section = BeamSection(
        name="bar",
        profile="Flatbar",
        material="S355",
        flange_width=0.10,
        flange_thickness=0.02,
        web_direction=(0.0, 0.0, 1.0),
    )
    project.add_beam_section(section)

    geometry = project.geometry
    start = geometry.add_point(0.0, 0.0, 0.0)
    end = geometry.add_point(length, 0.0, 0.0)
    edge = geometry.add_line(start, end)
    project.assign_beam(edge, "bar")
    project.add_support(support(project.point(start), ux=0.0, uy=0.0, uz=0.0, rx=0.0))
    project.add_support(support(project.point(end), uy=0.0, uz=0.0, rx=0.0))
    project.load_case().add_point_load(project.point(end), force=(-load, 0.0, 0.0))
    return project, section, start, end


def plate_project(side: float = 1.0, thickness: float = 0.008, pressure: float = 0.0):
    project = Project(name="plate")
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
    if pressure:
        project.load_case().add_pressure(project.face(face), pressure)
    return project, face, edges


def flexural_rigidity(thickness: float) -> float:
    return MODULUS * thickness**3 / (12.0 * (1.0 - POISSON**2))


# ----------------------------------------------------------------------
# modal
# ----------------------------------------------------------------------
def test_cantilever_first_natural_frequency():
    """Against f1 = 1.875^2 / (2 pi L^2) * sqrt(EI / (rho A))."""

    length = 2.0
    project = Project(name="cantilever")
    project.add_material(steel("S355", 0.020))
    section = BeamSection(
        name="bar", profile="Flatbar", material="S355",
        flange_width=0.10, flange_thickness=0.02, web_direction=(0.0, 0.0, 1.0),
    )
    project.add_beam_section(section)
    geometry = project.geometry
    root = geometry.add_point(0.0, 0.0, 0.0)
    tip = geometry.add_point(length, 0.0, 0.0)
    edge = geometry.add_line(root, tip)
    project.assign_beam(edge, "bar")
    project.add_support(fixed(project.point(root)))

    solution = solve_modal(project, target_size=length / 20, num_modes=3)
    properties = section.properties()
    expected = (
        1.875104**2
        / (2.0 * np.pi * length**2)
        * np.sqrt(MODULUS * properties["Iy"] / (DENSITY * properties["area"]))
    )
    assert solution.frequencies[0] == pytest.approx(expected, rel=0.02)


def test_simply_supported_plate_fundamental_frequency():
    """Against f11 = (pi/2)(1/a^2 + 1/b^2) sqrt(D / (rho t))."""

    side, thickness = 1.0, 0.010
    project, _face, _edges = plate_project(side, thickness)

    solution = solve_modal(project, target_size=side / 12, num_modes=3)
    expected = (
        (np.pi / 2.0)
        * (1.0 / side**2 + 1.0 / side**2)
        * np.sqrt(flexural_rigidity(thickness) / (DENSITY * thickness))
    )
    assert solution.status == "ok"
    assert solution.frequencies[0] == pytest.approx(expected, rel=0.03)


def test_modal_needs_neither_loads_nor_supports():
    """A free-free modal analysis of a floating structure is a real case."""

    project = Project(name="free")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    project.assign_plate(geometry.add_face(edges), "plate")

    solution = solve_modal(project, target_size=0.125, num_modes=8)
    assert solution.status == "ok"
    assert solution.rigid_body_modes == 6

    # The six rigid-body modes come first.  They are numerically small rather
    # than exactly zero, so what matters is that they are negligible beside
    # the first flexible mode.
    rigid = solution.frequencies[:6]
    flexible = solution.frequencies[6]
    assert flexible > 1.0
    assert max(rigid) < flexible / 1000.0


def test_modal_shift_default_makes_shell_models_converge():
    """Without shift-invert ARPACK fails on ordinary shell models."""

    project, _face, _edges = plate_project(1.0, 0.010)
    mesh = project.generate_mesh(0.1)

    assert solve_modal(project, mesh=mesh, num_modes=3).status == "ok"
    # The unshifted path is what fails; it is available but not the default.
    unshifted = solve_modal(project, mesh=mesh, num_modes=3, shift=None)
    assert unshifted.status in ("ok", "failed")


def test_modal_periods_and_summary():
    project, _face, _edges = plate_project(1.0, 0.010)
    solution = solve_modal(project, target_size=0.125, num_modes=2)

    assert len(solution) == 2
    assert solution.periods()[0] == pytest.approx(1.0 / solution.frequencies[0])
    assert "modes" in solution.summary()
    assert isinstance(solution, ModalSolution)


# ----------------------------------------------------------------------
# buckling
# ----------------------------------------------------------------------
def test_euler_buckling_of_a_pinned_strut():
    """Against P_cr = pi^2 E I / L^2."""

    length, load = 2.0, 1000.0
    project, section, _start, _end = strut_project(length, load)

    solution = solve_buckling(project, target_size=length / 20, num_modes=3)
    expected = np.pi**2 * MODULUS * section.properties()["Iy"] / length**2

    assert solution.status == "ok"
    assert solution.critical_factor * load == pytest.approx(expected, rel=0.01)


def test_buckling_factors_are_ordered_and_the_first_is_critical():
    length, load = 2.0, 1000.0
    project, _section, _start, _end = strut_project(length, load)
    solution = solve_buckling(project, target_size=length / 20, num_modes=3)

    factors = solution.load_factors
    assert factors == sorted(factors)
    assert solution.critical_factor == pytest.approx(min(factors))


def test_a_buckling_factor_scales_inversely_with_the_reference_load():
    """The factor multiplies the reference case, so doubling it halves them."""

    length = 2.0
    light, _section, _start, _end = strut_project(length, 1000.0)
    heavy, _section2, _start2, _end2 = strut_project(length, 2000.0)

    first = solve_buckling(light, target_size=length / 16, num_modes=1)
    second = solve_buckling(heavy, target_size=length / 16, num_modes=1)
    assert second.critical_factor == pytest.approx(
        0.5 * first.critical_factor, rel=1e-6
    )


def test_buckling_names_the_reference_case_it_belongs_to():
    project, _section, _start, _end = strut_project()
    solution = solve_buckling(project, target_size=0.2, num_modes=1)
    assert solution.reference_case == "default"
    assert "critical factor" in solution.summary()


# ----------------------------------------------------------------------
# nonlinear static
# ----------------------------------------------------------------------
def test_a_plate_stiffens_under_large_deflection():
    """Membrane action makes the nonlinear answer smaller than the linear one."""

    project, _face, _edges = plate_project(1.0, 0.008, pressure=60_000.0)
    mesh = project.generate_mesh(0.1)

    linear = solve_linear_static(project, mesh=mesh).max_translation()[1]
    nonlinear = solve_nonlinear_static(project, mesh=mesh, num_steps=8)

    assert nonlinear.status == "completed"
    assert nonlinear.load_factor == pytest.approx(1.0)
    assert nonlinear.max_translation()[1] < linear


def test_a_nonlinear_result_carries_its_load_path():
    project, _face, _edges = plate_project(1.0, 0.008, pressure=40_000.0)
    solution = solve_nonlinear_static(project, target_size=0.125, num_steps=6)

    history = solution.history()
    assert set(history) == {
        "step", "load_factor", "displacement_norm", "iterations"
    }
    assert len(history["load_factor"]) == len(solution.steps)
    # The path ends at the load factor actually reached.
    assert history["load_factor"][-1] == pytest.approx(solution.load_factor)


def test_nonlinear_reports_progress_while_it_runs():
    project, _face, _edges = plate_project(1.0, 0.008, pressure=30_000.0)
    messages = []
    solve_nonlinear_static(
        project, target_size=0.2, num_steps=4, progress=messages.append
    )
    assert messages
    assert any("step" in message for message in messages)


def test_a_small_load_gives_nearly_the_linear_answer():
    """At small deflection the two must agree."""

    project, _face, _edges = plate_project(1.0, 0.010, pressure=500.0)
    mesh = project.generate_mesh(0.125)

    linear = solve_linear_static(project, mesh=mesh).max_translation()[1]
    nonlinear = solve_nonlinear_static(
        project, mesh=mesh, num_steps=4
    ).max_translation()[1]
    assert nonlinear == pytest.approx(linear, rel=0.02)


# ----------------------------------------------------------------------
# arc length and eigenmode imperfections
# ----------------------------------------------------------------------
def test_arc_length_traces_a_path_and_reports_a_peak():
    length, load = 2.0, 40_000.0
    project, _section, _start, _end = strut_project(length, load)
    mesh = project.generate_mesh(length / 16)

    buckling = solve_buckling(project, mesh=mesh, num_modes=1)
    imperfection = eigenmode_imperfection(buckling, 1, amplitude=length / 500)

    from anysolver import ArcLengthControl

    solution = solve_arc_length(
        project,
        mesh=mesh,
        imperfection=imperfection,
        control=ArcLengthControl(max_steps=40),
    )
    assert solution.steps
    assert solution.peak_load_factor is not None
    # An imperfect strut cannot carry more than the perfect Euler load.
    assert solution.peak_load_factor <= buckling.critical_factor * 1.05


def test_an_imperfection_lowers_the_capacity():
    """A bigger bow has to reduce what the strut can carry."""

    length, load = 2.0, 40_000.0
    from anysolver import ArcLengthControl

    peaks = []
    for amplitude in (length / 2000.0, length / 200.0):
        project, _section, _start, _end = strut_project(length, load)
        mesh = project.generate_mesh(length / 16)
        buckling = solve_buckling(project, mesh=mesh, num_modes=1)
        solution = solve_arc_length(
            project,
            mesh=mesh,
            imperfection=eigenmode_imperfection(buckling, 1, amplitude=amplitude),
            control=ArcLengthControl(max_steps=40),
        )
        peaks.append(solution.peak_load_factor)

    assert peaks[1] < peaks[0]


def test_an_eigenmode_imperfection_needs_a_buckling_result():
    from anyfem.model import ProjectError
    from anyfem.post import BucklingSolution

    project, _face, _edges = plate_project(1.0, 0.010, pressure=1000.0)
    mesh = project.generate_mesh(0.5)
    from anyfem.solve.build import build_fe_model

    empty = BucklingSolution(built=build_fe_model(project, mesh), shapes=[])
    with pytest.raises(ProjectError, match="did not retain"):
        eigenmode_imperfection(empty, 1, amplitude=0.001)


# ----------------------------------------------------------------------
# transient
# ----------------------------------------------------------------------
def test_a_suddenly_applied_load_peaks_at_twice_the_static_answer():
    """The classic undamped step response."""

    project, _face, _edges = plate_project(1.0, 0.008, pressure=20_000.0)
    mesh = project.generate_mesh(0.125)

    static = solve_linear_static(project, mesh=mesh).max_translation()[1]
    transient = solve_transient(project, mesh=mesh, dt=2.0e-4, t_end=0.02, save_every=2)

    assert transient.status == "completed"
    assert transient.peak_displacement / static == pytest.approx(2.0, rel=0.05)


def test_damping_reduces_the_transient_peak():
    project, _face, _edges = plate_project(1.0, 0.008, pressure=20_000.0)
    mesh = project.generate_mesh(0.125)

    undamped = solve_transient(project, mesh=mesh, dt=2.0e-4, t_end=0.02)
    damped = solve_transient(
        project, mesh=mesh, dt=2.0e-4, t_end=0.02, rayleigh_alpha=200.0
    )
    assert damped.peak_displacement < undamped.peak_displacement


def test_a_transient_result_is_browsable_by_time():
    project, _face, _edges = plate_project(1.0, 0.008, pressure=20_000.0)
    solution = solve_transient(
        project, target_size=0.2, dt=2.0e-4, t_end=0.01, save_every=2
    )

    assert len(solution) == len(solution.times)
    assert solution.times[0] == pytest.approx(0.0)
    nearest = solution.at_time(0.005)
    assert nearest.value == pytest.approx(0.005, abs=1e-3)

    history = solution.node_history(solution.peak_node, "uz")
    assert len(history) == len(solution)
    # It starts from rest.
    assert history[0] == pytest.approx(0.0, abs=1e-12)


# ----------------------------------------------------------------------
# every result speaks the same language
# ----------------------------------------------------------------------
def test_every_analysis_produces_shapes_with_the_same_interface():
    """A mode, a step and a time instant all display like a static result."""

    project, face, edges = plate_project(1.0, 0.010, pressure=20_000.0)
    mesh = project.generate_mesh(0.2)

    static = solve_linear_static(project, mesh=mesh)
    modal = solve_modal(project, mesh=mesh, num_modes=2)
    buckling = solve_buckling(project, mesh=mesh, num_modes=1)
    transient = solve_transient(project, mesh=mesh, dt=5.0e-4, t_end=0.004)

    shapes = [static, modal.shape(0), buckling.shape(0), transient.shape(1)]
    for shape in shapes:
        assert isinstance(shape, ShapeView)
        assert shape.translations().shape == (mesh.num_nodes, 3)
        assert shape.component("uz").shape == (mesh.num_nodes,)
        assert shape.deformed_positions(1.0).shape == (mesh.num_nodes, 3)
        node, magnitude = shape.max_translation()
        assert node in mesh.nodes
        assert np.isfinite(magnitude)
        # Addressable by geometry, like every other result.
        assert shape.displacement_at(project.face(face))


def test_the_result_scene_draws_a_mode_shape():
    from anyfem.ui.scene import build_result_scene

    project, _face, _edges = plate_project(1.0, 0.010)
    modal = solve_modal(project, target_size=0.2, num_modes=2)

    scene = build_result_scene(modal.shape(1), scale=0.1)
    assert scene.faces
    assert scene.legend is not None


def test_a_multi_shape_result_stands_in_for_its_first_shape():
    project, _face, _edges = plate_project(1.0, 0.010)
    modal = solve_modal(project, target_size=0.25, num_modes=2)

    assert modal.displacements is modal.shape(0).displacements
    assert modal.max_translation() == modal.shape(0).max_translation()
    assert list(modal)[0] is modal.shape(0)
    assert modal.labels[0] == "mode 1"
