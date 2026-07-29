"""End-to-end verification against closed-form solutions."""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import Project, fixed, pinned, solve_linear_static, steel
from anyfem.model import BeamSection, ProjectError


def cantilever_project(length: float = 2.0, load: float = 100.0):
    project = Project(name="cantilever")
    project.add_material(steel("S355", 0.020))
    section = BeamSection(
        name="bar",
        profile="Flatbar",
        material="S355",
        flange_width=0.20,
        flange_thickness=0.02,
        web_direction=(0.0, 0.0, 1.0),
    )
    project.add_beam_section(section)

    geometry = project.geometry
    root = geometry.add_point(0.0, 0.0, 0.0)
    tip = geometry.add_point(length, 0.0, 0.0)
    edge = geometry.add_line(root, tip)
    project.assign_beam(edge, "bar")
    project.add_support(fixed(project.point(root)))
    project.load_case().add_point_load(project.point(tip), force=(0.0, 0.0, -load))
    return project, section, tip


def test_cantilever_tip_deflection():
    """Tip deflection against P L^3 / (3 E I), plus the Timoshenko shear term."""

    length, load = 2.0, 100.0
    project, section, tip = cantilever_project(length, load)
    solution = solve_linear_static(project, target_size=length / 10)

    properties = section.properties()
    modulus = project.materials["S355"].elastic_modulus
    shear_modulus = modulus / (2.0 * (1.0 + 0.3))
    expected = load * length**3 / (3.0 * modulus * properties["Iy"]) + load * length / (
        properties["shear_factor_z"] * shear_modulus * properties["area"]
    )

    deflection = abs(solution.point_displacement(project.point(tip))[2])
    assert deflection == pytest.approx(expected, rel=0.01)


def test_cantilever_root_is_clamped():
    project, _, _ = cantilever_project()
    solution = solve_linear_static(project, target_size=0.2)
    root = project.point(1)
    assert np.allclose(solution.point_displacement(root), 0.0, atol=1.0e-12)


def simply_supported_plate(side: float, thickness: float, pressure: float):
    project = Project(name="ss_plate")
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
    project.load_case().add_pressure(project.face(face), pressure)
    return project, face, edges


def test_simply_supported_plate_deflection():
    """Centre deflection against Timoshenko's 0.00406 q a^4 / D."""

    side, thickness, pressure = 1.0, 0.010, 10_000.0
    project, _, _ = simply_supported_plate(side, thickness, pressure)
    solution = solve_linear_static(project, target_size=side / 16)

    modulus = project.materials["S355"].elastic_modulus
    flexural_rigidity = modulus * thickness**3 / (12.0 * (1.0 - 0.3**2))
    expected = 0.00406 * pressure * side**4 / flexural_rigidity

    _, deflection = solution.max_translation()
    assert deflection == pytest.approx(expected, rel=0.02)


def test_plate_deflection_converges_with_refinement():
    side, thickness, pressure = 1.0, 0.010, 10_000.0
    project, _, _ = simply_supported_plate(side, thickness, pressure)

    modulus = project.materials["S355"].elastic_modulus
    flexural_rigidity = modulus * thickness**3 / (12.0 * (1.0 - 0.3**2))
    expected = 0.00406 * pressure * side**4 / flexural_rigidity

    errors = []
    for divisions in (4, 8, 16):
        solution = solve_linear_static(project, target_size=side / divisions)
        _, deflection = solution.max_translation()
        errors.append(abs(deflection - expected) / expected)

    assert errors[0] > errors[-1]
    assert errors[-1] < 0.02


def test_result_is_addressed_by_geometry_not_node_numbers():
    side, thickness, pressure = 1.0, 0.010, 10_000.0
    project, face, edges = simply_supported_plate(side, thickness, pressure)
    solution = solve_linear_static(project, target_size=side / 8)

    supported = solution.displacement_at(project.edge(edges[0]))
    assert supported
    assert all(abs(value[2]) < 1.0e-12 for value in supported.values())

    on_face = solution.displacement_at(project.face(face))
    assert len(on_face) == solution.built.mesh.num_nodes


def test_pressure_scales_linearly():
    side, thickness = 1.0, 0.010
    single, _, _ = simply_supported_plate(side, thickness, 10_000.0)
    double, _, _ = simply_supported_plate(side, thickness, 20_000.0)

    _, first = solve_linear_static(single, target_size=side / 8).max_translation()
    _, second = solve_linear_static(double, target_size=side / 8).max_translation()
    assert second == pytest.approx(2.0 * first, rel=1.0e-9)


def test_line_load_matches_an_equivalent_pressure():
    """A line load lumped along an edge equals the same total as point loads."""

    length, intensity = 2.0, 500.0
    project = Project(name="line_load")
    project.add_material(steel("S355", 0.020))
    project.add_beam_section(
        BeamSection(
            name="bar",
            profile="Flatbar",
            material="S355",
            flange_width=0.20,
            flange_thickness=0.02,
            web_direction=(0.0, 0.0, 1.0),
        )
    )
    geometry = project.geometry
    root = geometry.add_point(0.0, 0.0, 0.0)
    tip = geometry.add_point(length, 0.0, 0.0)
    edge = geometry.add_line(root, tip)
    project.assign_beam(edge, "bar")
    project.add_support(fixed(project.point(root)))
    project.load_case().add_line_load(project.edge(edge), (0.0, 0.0, -intensity))

    solution = solve_linear_static(project, target_size=length / 20)
    # A uniformly loaded cantilever: w = q L^4 / (8 E I), ignoring shear.
    properties = project.beam_sections["bar"].properties()
    modulus = project.materials["S355"].elastic_modulus
    expected = intensity * length**4 / (8.0 * modulus * properties["Iy"])
    deflection = abs(solution.point_displacement(project.point(tip))[2])
    assert deflection == pytest.approx(expected, rel=0.02)


def test_incomplete_model_fails_closed():
    project = Project(name="incomplete")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    geometry.add_face(edges)
    # No section assigned, no supports, no loads.

    with pytest.raises(ProjectError) as excinfo:
        solve_linear_static(project, target_size=0.5)
    message = str(excinfo.value)
    assert "without a section" in message
    assert "no supports" in message
    assert "no loads" in message
