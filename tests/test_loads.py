"""Loads and boundary conditions, checked against what they should total.

A load type is only useful if the force that reaches the solver is the force
the user asked for, so most of these compare the assembled load vector against
a hand calculation rather than just checking that a solve runs.
"""

from __future__ import annotations

import numpy as np
import pytest
from anysolver import assemble_load_vector

from anyfem import Project, fixed, pinned, solve_linear_static, steel
from anyfem import commands as cmd
from anyfem.model import (
    Imperfection,
    Mass,
    ProjectError,
    member_bow,
    plate_mode,
    prescribed,
)
from anyfem.solve.build import build_fe_model

DENSITY = 7850.0
GRAVITY = 9.81


@pytest.fixture
def plate():
    """A 2 x 1 m plate, 10 mm thick, pinned all round."""

    project = Project(name="loads")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    return project, face, edges, points


def total_force(solution) -> np.ndarray:
    """Sum the assembled load vector's translational components."""

    vector = assemble_load_vector(
        solution.built.fe_model, solution.built.load_case
    )
    if isinstance(vector, tuple):
        vector = vector[0]
    return np.array(
        [float(vector[axis::6].sum()) for axis in range(3)]
    )


def support_all(project, edges):
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))


# ----------------------------------------------------------------------
# prescribed displacement
# ----------------------------------------------------------------------
def test_a_prescribed_displacement_is_reached_exactly(plate):
    project, face, edges, _points = plate
    project.add_support(fixed(project.edge(edges[3])))
    project.add_support(prescribed(project.edge(edges[1]), uz=0.01))
    project.load_case().add_pressure(project.face(face), 1.0)

    solution = solve_linear_static(project, target_size=0.25)
    moved = [
        solution.node_displacement(node)[2]
        for node in solution.built.mesh.nodes_on(project.edge(edges[1]))
    ]
    assert moved == pytest.approx([0.01] * len(moved))


def test_a_prescribed_displacement_needs_a_value(plate):
    project, _face, edges, _points = plate
    with pytest.raises(ValueError, match="at least one value"):
        prescribed(project.edge(edges[0]))


def test_a_prescribed_displacement_rejects_a_boolean_flag(plate):
    project, _face, edges, _points = plate
    with pytest.raises(ValueError, match="not a boolean flag"):
        prescribed(project.edge(edges[0]), uz=True)


def test_a_support_is_a_prescribed_displacement_of_zero(plate):
    project, _face, edges, _points = plate
    held = pinned(project.edge(edges[0]))
    pushed = prescribed(project.edge(edges[0]), ux=0.0, uy=0.0, uz=0.0)
    assert dict(held.constraints) == dict(pushed.constraints)


# ----------------------------------------------------------------------
# body loads
# ----------------------------------------------------------------------
def test_self_weight_equals_rho_g_volume(plate):
    project, _face, edges, _points = plate
    support_all(project, edges)
    project.load_case().set_gravity()

    solution = solve_linear_static(project, target_size=0.25)
    volume = 2.0 * 1.0 * 0.010
    assert total_force(solution)[2] == pytest.approx(-DENSITY * GRAVITY * volume)


def test_acceleration_acts_in_the_direction_given(plate):
    project, _face, edges, _points = plate
    support_all(project, edges)
    project.load_case().set_acceleration(3.0, 0.0, 0.0)

    solution = solve_linear_static(project, target_size=0.25)
    mass = DENSITY * 2.0 * 1.0 * 0.010
    force = total_force(solution)
    assert force[0] == pytest.approx(mass * 3.0)
    assert force[2] == pytest.approx(0.0, abs=1e-9)


def test_a_mass_adds_its_own_inertial_load(plate):
    project, face, edges, _points = plate
    support_all(project, edges)
    project.add_mass(Mass(ref=project.face(face), value=1000.0, name="equipment"))
    project.load_case().set_gravity()

    solution = solve_linear_static(project, target_size=0.25)
    structure = DENSITY * GRAVITY * 2.0 * 1.0 * 0.010
    assert total_force(solution)[2] == pytest.approx(
        -(structure + 1000.0 * GRAVITY)
    )


def test_a_mass_is_shared_over_the_entity_it_is_on(plate):
    project, _face, edges, points = plate
    support_all(project, edges)
    project.add_mass(Mass(ref=project.edge(edges[0]), value=600.0))
    project.load_case().set_gravity()

    mesh = project.generate_mesh(0.25)
    built = build_fe_model(project, mesh)
    on_edge = mesh.nodes_on(project.edge(edges[0]))
    attached = [built.fe_model.mesh.point_masses.get(node, 0.0) for node in on_edge]
    assert sum(attached) == pytest.approx(600.0)
    assert attached == pytest.approx([600.0 / len(on_edge)] * len(on_edge))


def test_a_negative_mass_is_refused(plate):
    project, face, _edges, _points = plate
    with pytest.raises(ValueError, match="must not be negative"):
        Mass(ref=project.face(face), value=-1.0)


def test_nonfinite_masses_are_refused(plate):
    project, face, _edges, _points = plate
    with pytest.raises(ValueError, match="finite number"):
        Mass(ref=project.face(face), value=np.nan)


# ----------------------------------------------------------------------
# surface traction and line loads
# ----------------------------------------------------------------------
def test_surface_traction_totals_intensity_times_area(plate):
    project, face, edges, _points = plate
    support_all(project, edges)
    project.load_case().add_surface_traction(project.face(face), (0, 0, -5000.0))

    solution = solve_linear_static(project, target_size=0.25)
    assert total_force(solution)[2] == pytest.approx(-5000.0 * 2.0 * 1.0)


def test_surface_traction_keeps_its_direction_on_a_sloped_plate():
    """Unlike pressure, a traction does not follow the plate normal."""

    project = Project()
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    geometry = project.geometry
    # A plate tilted 45 degrees about the y axis.
    points = geometry.add_points(
        [(0, 0, 0), (1, 0, 1), (1, 1, 1), (0, 1, 0)]
    )
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    support_all(project, edges)
    project.load_case().add_surface_traction(project.face(face), (0, 0, -1000.0))

    solution = solve_linear_static(project, target_size=0.25)
    area = np.sqrt(2.0) * 1.0
    force = total_force(solution)
    assert force[2] == pytest.approx(-1000.0 * area)
    assert force[0] == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize(
    ("connectivity", "expected_shares"),
    [
        ((1, 2, 3), (1.0 / 3.0,) * 3),
        ((1, 2, 3, 4, 5, 6), (0.0,) * 3 + (1.0 / 3.0,) * 3),
    ],
)
def test_triangular_shell_traction_uses_consistent_nodal_shares(
    connectivity, expected_shares
):
    from anymesher import EntityRef, Mesh

    from anyfem.solve.build import _traction_to_nodes

    mesh = Mesh()
    mesh.nodes = {
        1: np.array([0.0, 0.0, 0.0]),
        2: np.array([1.0, 0.0, 0.0]),
        3: np.array([0.0, 1.0, 0.0]),
        4: np.array([0.5, 0.0, 0.0]),
        5: np.array([0.5, 0.5, 0.0]),
        6: np.array([0.0, 0.5, 0.0]),
    }
    mesh.tris[10] = connectivity
    mesh.elements_of_face[7] = [10]

    intensity = np.array([0.0, 0.0, -12.0])
    nodal = dict(_traction_to_nodes(mesh, EntityRef("face", 7), intensity))

    area = 0.5
    for node_id, share in zip(connectivity, expected_shares):
        assert nodal[node_id] == pytest.approx(area * share * intensity)
    assert sum(force[2] for force in nodal.values()) == pytest.approx(
        area * intensity[2]
    )


def test_line_load_totals_intensity_times_length(plate):
    project, _face, edges, _points = plate
    support_all(project, edges)
    project.load_case().add_line_load(project.edge(edges[0]), (0, 0, -800.0))

    solution = solve_linear_static(project, target_size=0.25)
    assert total_force(solution)[2] == pytest.approx(-800.0 * 2.0)


def test_a_traction_on_a_line_is_refused(plate):
    project, _face, edges, _points = plate
    with pytest.raises(ValueError, match="applies to a plate"):
        project.load_case().add_surface_traction(project.edge(edges[0]), (0, 0, -1))


@pytest.mark.parametrize(
    ("add", "message"),
    [
        (lambda case, project, face, edge, point: case.add_point_load(
            project.point(point), force=(1.0, 2.0)
        ), "point-load force needs three finite components"),
        (lambda case, project, face, edge, point: case.add_line_load(
            project.edge(edge), (0.0, np.inf, 0.0)
        ), "line-load force per length needs three finite components"),
        (lambda case, project, face, edge, point: case.add_surface_traction(
            project.face(face), (0.0, np.nan, 0.0)
        ), "surface traction needs three finite components"),
    ],
)
def test_load_vectors_are_validated_when_added(plate, add, message):
    project, face, edges, points = plate
    with pytest.raises(ValueError, match=message):
        add(project.load_case(), project, face, edges[0], points[0])


def test_nonfinite_pressure_acceleration_and_factors_are_refused(plate):
    project, face, _edges, _points = plate
    case = project.load_case("dead")
    with pytest.raises(ValueError, match="pressure must be a finite number"):
        case.add_pressure(project.face(face), np.nan)
    with pytest.raises(ValueError, match="acceleration needs three finite components"):
        case.set_acceleration(0.0, np.inf, 0.0)
    with pytest.raises(ValueError, match="factor.*finite number"):
        project.add_combination("ULS", {"dead": np.nan})


# ----------------------------------------------------------------------
# combinations
# ----------------------------------------------------------------------
def test_a_combination_is_exactly_its_factored_sum(plate):
    project, face, edges, _points = plate
    support_all(project, edges)
    project.load_case("dead").add_pressure(project.face(face), 10_000.0)
    project.load_case("live").add_pressure(project.face(face), 4_000.0)
    project.add_combination("ULS", {"dead": 1.2, "live": 1.5})

    mesh = project.generate_mesh(0.25)
    dead = solve_linear_static(project, mesh=mesh, load_case="dead")
    live = solve_linear_static(project, mesh=mesh, load_case="live")
    combined = solve_linear_static(project, mesh=mesh, combination="ULS")

    assert combined.max_translation()[1] == pytest.approx(
        1.2 * dead.max_translation()[1] + 1.5 * live.max_translation()[1]
    )


def test_a_combination_accumulates_pressure_on_the_same_plate(plate):
    """The solver's add_pressure_load overwrites, so this has to be summed."""

    project, face, edges, _points = plate
    support_all(project, edges)
    project.load_case("a").add_pressure(project.face(face), 1_000.0)
    project.load_case("b").add_pressure(project.face(face), 2_000.0)
    project.add_combination("sum", {"a": 1.0, "b": 1.0})

    solution = solve_linear_static(project, target_size=0.25, combination="sum")
    assert total_force(solution)[2] == pytest.approx(3_000.0 * 2.0 * 1.0)


def test_a_combination_sums_acceleration_fields(plate):
    project, _face, edges, _points = plate
    support_all(project, edges)
    project.load_case("g").set_gravity()
    project.load_case("heave").set_acceleration(0.0, 0.0, -2.0)
    project.add_combination("both", {"g": 1.0, "heave": 1.0})

    solution = solve_linear_static(project, target_size=0.25, combination="both")
    mass = DENSITY * 2.0 * 1.0 * 0.010
    assert total_force(solution)[2] == pytest.approx(-mass * (GRAVITY + 2.0))


def test_a_combination_of_unknown_cases_is_refused(plate):
    project, _face, _edges, _points = plate
    project.load_case("dead")
    with pytest.raises(ProjectError, match="undefined load case"):
        project.add_combination("bad", {"dead": 1.0, "missing": 1.0})


def test_solving_an_unknown_combination_is_refused(plate):
    project, face, edges, _points = plate
    support_all(project, edges)
    project.load_case().add_pressure(project.face(face), 1_000.0)
    with pytest.raises(ProjectError, match="no load combination"):
        solve_linear_static(project, target_size=0.5, combination="nope")


def test_a_combination_mixing_dead_and_follower_pressure_is_refused(plate):
    """The solver assembles a case in one configuration, not both."""

    project, face, edges, _points = plate
    support_all(project, edges)
    project.load_case("dead").add_pressure(project.face(face), 1_000.0)
    live = project.load_case("live")
    live.add_pressure(project.face(face), 1_000.0)
    live.set_follower_pressure(True)
    project.add_combination("mixed", {"dead": 1.0, "live": 1.0})

    with pytest.raises(ProjectError, match="follower and dead pressure"):
        solve_linear_static(project, target_size=0.5, combination="mixed")


# ----------------------------------------------------------------------
# follower pressure
# ----------------------------------------------------------------------
def test_follower_pressure_reaches_the_solver(plate):
    project, face, edges, _points = plate
    support_all(project, edges)
    case = project.load_case()
    case.add_pressure(project.face(face), 10_000.0)
    case.set_follower_pressure(True)

    mesh = project.generate_mesh(0.5)
    built = build_fe_model(project, mesh)
    assert built.load_case.follower_pressure is True


def test_dead_pressure_is_the_default(plate):
    project, face, edges, _points = plate
    support_all(project, edges)
    project.load_case().add_pressure(project.face(face), 10_000.0)

    mesh = project.generate_mesh(0.5)
    assert build_fe_model(project, mesh).load_case.follower_pressure is False


# ----------------------------------------------------------------------
# imperfections
# ----------------------------------------------------------------------
def test_a_plate_imperfection_moves_the_stress_free_geometry(plate):
    project, face, edges, _points = plate
    support_all(project, edges)
    project.load_case().add_pressure(project.face(face), 1_000.0)
    mesh = project.generate_mesh(0.25)

    flat = build_fe_model(project, mesh)
    assert max(abs(node.z) for node in flat.fe_model.mesh.nodes.values()) == 0.0

    project.add_imperfection(plate_mode(project.face(face), amplitude=0.004))
    bent = build_fe_model(project, mesh)
    heights = [node.z for node in bent.fe_model.mesh.nodes.values()]
    assert max(heights) == pytest.approx(0.004)
    assert min(heights) == pytest.approx(0.0, abs=1e-12)


def test_an_imperfection_is_not_a_displacement(plate):
    """The imperfect shape is the unloaded shape, so the mesh stays put."""

    project, face, edges, _points = plate
    support_all(project, edges)
    project.load_case().add_pressure(project.face(face), 1_000.0)
    project.add_imperfection(plate_mode(project.face(face), amplitude=0.004))

    mesh = project.generate_mesh(0.25)
    build_fe_model(project, mesh)
    assert np.abs(mesh.node_positions()[:, 2]).max() == 0.0


def test_a_member_bow_follows_the_line(plate):
    project, _face, edges, _points = plate
    from anyfem.model.sections import BeamSection

    project.add_beam_section(
        BeamSection(
            name="fb", profile="Flatbar", material="S355",
            flange_width=0.05, flange_thickness=0.02,
        )
    )
    project.assign_beam(edges[0], "fb")
    support_all(project, edges)
    project.load_case().add_pressure(project.face(1), 1_000.0)
    project.add_imperfection(member_bow(project.edge(edges[0]), amplitude=0.006))

    mesh = project.generate_mesh(0.25)
    built = build_fe_model(project, mesh)
    on_line = mesh.nodes_on(project.edge(edges[0]))
    heights = [built.fe_model.mesh.get_node(node).z for node in on_line]
    # A half sine: zero at the ends, the amplitude in the middle.
    assert heights[0] == pytest.approx(0.0, abs=1e-12)
    assert heights[-1] == pytest.approx(0.0, abs=1e-12)
    assert max(heights) == pytest.approx(0.006, rel=1e-6)


def test_imperfections_default_to_the_solver_amplitudes(plate):
    project, face, edges, _points = plate
    support_all(project, edges)
    project.load_case().add_pressure(project.face(face), 1_000.0)
    project.add_imperfection(plate_mode(project.face(face)))

    mesh = project.generate_mesh(0.25)
    built = build_fe_model(project, mesh)
    heights = [node.z for node in built.fe_model.mesh.nodes.values()]
    # The default is the shorter span over 200.
    assert max(heights) == pytest.approx(1.0 / 200.0)


def test_an_imperfection_kind_must_match_what_it_is_on(plate):
    project, face, edges, _points = plate
    with pytest.raises(ValueError, match="applies to a plate"):
        Imperfection(ref=project.edge(edges[0]), kind="plate_mode")
    with pytest.raises(ValueError, match="applies to a line"):
        Imperfection(ref=project.face(face), kind="member_bow")
    with pytest.raises(ValueError, match="unknown imperfection kind"):
        Imperfection(ref=project.face(face), kind="banana")


def test_imperfection_kind_is_inferred_from_the_entity(plate):
    project, face, edges, _points = plate
    assert plate_mode(project.face(face)).resolved_kind == "plate_mode"
    assert Imperfection(ref=project.edge(edges[0])).resolved_kind == "member_bow"


@pytest.mark.parametrize("amplitude", [-1.0, np.nan, np.inf, True])
def test_imperfection_amplitude_must_be_finite_and_nonnegative(plate, amplitude):
    project, face, _edges, _points = plate
    with pytest.raises(ValueError, match="finite, non-negative"):
        plate_mode(project.face(face), amplitude=amplitude)


@pytest.mark.parametrize(
    "direction",
    [(0.0, 1.0), (0.0, 0.0, 0.0), (0.0, np.nan, 1.0)],
)
def test_imperfection_direction_must_be_a_finite_nonzero_vector(plate, direction):
    project, face, _edges, _points = plate
    with pytest.raises(ValueError, match="three finite, non-zero components"):
        plate_mode(project.face(face), direction=direction)


@pytest.mark.parametrize(
    "waves", [(1,), (1, 1, 1), (0, 1), (1.5, 1), (True, 1)]
)
def test_plate_wave_counts_must_be_two_positive_integers(plate, waves):
    project, face, _edges, _points = plate
    with pytest.raises(ValueError, match="exactly two positive integers"):
        plate_mode(project.face(face), waves=waves)


@pytest.mark.parametrize(
    "axes", [(0,), (0, 1, 2), (0, 0), (0, 3), (0.5, 1)]
)
def test_plate_axes_must_be_two_distinct_coordinate_axes(plate, axes):
    project, face, _edges, _points = plate
    with pytest.raises(ValueError, match="two distinct coordinate axes"):
        Imperfection(ref=project.face(face), kind="plate_mode", axes=axes)


def test_imperfection_defaults_and_vertical_plate_axes_remain_supported(plate):
    project, face, _edges, _points = plate
    default = plate_mode(project.face(face))
    vertical = Imperfection(
        ref=project.face(face), kind="plate_mode",
        axes=(1.0, 2.0), waves=(1.0, 2.0),
    )

    assert default.amplitude is None
    assert default.direction == (0.0, 0.0, 1.0)
    assert default.waves == (1, 1)
    assert default.axes == (0, 1)
    assert vertical.axes == (1, 2)
    assert vertical.waves == (1, 2)


# ----------------------------------------------------------------------
# through the command stack
# ----------------------------------------------------------------------
def test_load_commands_are_undoable(plate):
    project, face, edges, points = plate
    stack = cmd.CommandStack(project)

    stack.run(cmd.AddSurfaceTraction(project.face(face), (0, 0, -100.0)))
    stack.run(cmd.AddLineLoad(project.edge(edges[0]), (0, 0, -50.0)))
    stack.run(cmd.AddMass(project.point(points[0]), 25.0))
    stack.run(cmd.SetAcceleration((0.0, 0.0, -9.81)))
    stack.run(cmd.SetFollowerPressure(True))

    case = project.load_case()
    assert case.surface_tractions and case.line_loads
    assert project.masses and case.gravity is not None
    assert case.follower_pressure

    for _ in range(5):
        stack.undo()
    case = project.load_case()
    assert not case.surface_tractions
    assert not case.line_loads
    assert not project.masses
    assert case.gravity is None
    assert not case.follower_pressure


def test_load_case_commands_are_undoable(plate):
    project, _face, _edges, _points = plate
    stack = cmd.CommandStack(project)

    stack.run(cmd.AddLoadCase("live"))
    assert "live" in project.load_cases
    stack.undo()
    assert "live" not in project.load_cases

    stack.redo()
    stack.run(cmd.DeleteLoadCase("live"))
    assert "live" not in project.load_cases
    stack.undo()
    assert "live" in project.load_cases


def test_a_case_used_by_a_combination_cannot_be_deleted(plate):
    project, _face, _edges, _points = plate
    stack = cmd.CommandStack(project)
    stack.run(cmd.AddLoadCase("dead"))
    stack.run(cmd.AddCombination("ULS", {"dead": 1.2}))

    with pytest.raises(ProjectError, match="used by combination"):
        stack.run(cmd.DeleteLoadCase("dead"))


def test_combination_and_imperfection_commands_are_undoable(plate):
    project, face, _edges, _points = plate
    stack = cmd.CommandStack(project)
    stack.run(cmd.AddLoadCase("dead"))

    stack.run(cmd.AddCombination("ULS", {"dead": 1.2}))
    stack.run(cmd.AddImperfection(plate_mode(project.face(face), amplitude=0.001)))
    assert project.combinations and project.imperfections

    stack.undo()
    assert not project.imperfections
    stack.undo()
    assert not project.combinations
