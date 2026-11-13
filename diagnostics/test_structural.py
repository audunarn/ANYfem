"""Phase 10: eccentric stiffeners, hardening curves and fracture.

The eccentricity tests lean on one model -- a plate strip with a flat bar down
its centre, supported at the ends only -- because the whole claim is that the
same structure behaves differently when the bar stands proud of the plating
instead of sharing its nodes.  Building both from one function keeps the
comparison honest.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from anyfem import (
    Project,
    ShellFormulationPolicy,
    pinned,
    solve_linear_static,
    steel,
    support,
)
from anyfem.geometry.operations import strip_face
from anyfem.model import BeamSection, fracture

LENGTH, WIDTH, THICKNESS = 4.0, 0.2, 0.008
BAR_HEIGHT, BAR_THICKNESS = 0.20, 0.010
OFFSET = 0.5 * THICKNESS + 0.5 * BAR_HEIGHT


@pytest.fixture
def workspace():
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def stiffened_strip(eccentricity: float, pressure: float = 1000.0):
    """A plate strip spanning 4 m with one flat bar along its centreline."""

    project = Project(name="strip")
    project.add_material(steel("S355", THICKNESS))
    project.add_plate_section("plate", thickness=THICKNESS, material="S355")
    project.add_beam_section(
        BeamSection(
            name="bar", profile="Flatbar", material="S355",
            flange_width=BAR_THICKNESS, flange_thickness=BAR_HEIGHT,
            web_direction=(0.0, 0.0, 1.0), eccentricity=eccentricity,
        )
    )
    geometry = project.geometry
    points = geometry.add_points(
        [(0, 0, 0), (LENGTH, 0, 0), (LENGTH, WIDTH, 0), (0, WIDTH, 0)]
    )
    face = geometry.add_face(geometry.add_polyline(points, close=True))
    project.assign_plate(face, "plate")
    strips, dividers = strip_face(geometry, face, axis=1, count=2)
    project.assign_plates(strips, "plate")
    project.assign_beam(dividers[0], "bar")

    def mid_x(edge_id: int) -> float:
        edge = geometry.edges[edge_id]
        ends = [geometry.vertices[v].position for v in (edge.start, edge.end)]
        return 0.5 * (ends[0][0] + ends[1][0])

    for edge_id in list(geometry.edges):
        if abs(mid_x(edge_id)) < 1.0e-9:
            project.add_support(pinned(project.edge(edge_id)))
        elif abs(mid_x(edge_id) - LENGTH) < 1.0e-9:
            project.add_support(support(project.edge(edge_id), uy=0.0, uz=0.0))
    for strip in strips:
        project.load_case().add_pressure(project.face(strip), pressure)
    return project, dividers[0]


def yielding_plate(pressure: float, *, nonlinear: bool = True):
    """A square plate loaded hard enough to go plastic.

    Plasticity is the layered-shell path in the solver; beams stay elastic, so
    a material-nonlinearity test has to load plating rather than a stiffener.
    Pinned edges make the plate pick up membrane action as it deflects, which
    is why the pressure has to be this high before the section yields.
    """

    thickness = 0.008
    project = Project(name="yielding_plate")
    project.add_material(steel("S355", thickness, nonlinear=nonlinear))
    project.add_plate_section("plate", thickness=thickness, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), pressure)
    return project


def transformed_section():
    """Neutral axis and inertia of the plate-plus-bar section, by hand."""

    plate_area = WIDTH * THICKNESS
    bar_area = BAR_THICKNESS * BAR_HEIGHT
    plate_inertia = WIDTH * THICKNESS**3 / 12.0
    bar_inertia = BAR_THICKNESS * BAR_HEIGHT**3 / 12.0
    neutral_axis = bar_area * OFFSET / (plate_area + bar_area)
    return {
        "neutral_axis": neutral_axis,
        "shared": plate_inertia + bar_inertia,
        "eccentric": (
            plate_inertia
            + plate_area * neutral_axis**2
            + bar_inertia
            + bar_area * (OFFSET - neutral_axis) ** 2
        ),
    }


# ----------------------------------------------------------------------
# sections and the model layer
# ----------------------------------------------------------------------




# ----------------------------------------------------------------------
# meshing
# ----------------------------------------------------------------------










# ----------------------------------------------------------------------
# the built model and the solve
# ----------------------------------------------------------------------


















# ----------------------------------------------------------------------
# supports take values, not flags
# ----------------------------------------------------------------------




# ----------------------------------------------------------------------
# hardening curves
# ----------------------------------------------------------------------










def test_a_hardening_material_makes_the_solve_inelastic():
    """Past yield the plastic model must be softer than the elastic one."""

    from anyfem import solve_nonlinear_static

    def run(nonlinear: bool) -> float:
        return solve_nonlinear_static(
            yielding_plate(1_500_000.0, nonlinear=nonlinear),
            target_size=1 / 8, num_steps=10,
        ).max_translation()[1]

    plastic, elastic = run(True), run(False)
    # Not a rounding difference: the plate has shed a third of its stiffness.
    assert plastic > elastic * 1.2


# ----------------------------------------------------------------------
# fracture
# ----------------------------------------------------------------------


def test_a_solve_without_fracture_deletes_nothing():
    from anyfem import solve_nonlinear_static

    solution = solve_nonlinear_static(
        yielding_plate(1_500_000.0), target_size=1 / 8, num_steps=10
    )
    assert not solution.deleted_elements


def test_fracture_erodes_elements_once_the_threshold_is_passed():
    """The threshold is equivalent plastic strain, so it has to be reachable.

    This plate peaks around 0.008 at full load; a threshold above that would
    pass the test for the wrong reason -- nothing eroded because nothing ever
    got close.
    """

    from anyfem import solve_nonlinear_static

    solution = solve_nonlinear_static(
        yielding_plate(1_500_000.0), target_size=1 / 8, num_steps=10,
        fracture=fracture(0.006),
    )
    assert solution.deleted_elements
    summary = solution.info["fracture_summary"]
    assert set(solution.deleted_elements) == set(summary["deleted_element_ids"])
    assert summary["max_trigger_value"] > 0.006


def test_eroding_too_much_of_the_structure_stops_the_solve():
    """Erosion past the configured fraction is reported, not solved through."""

    from anyfem import solve_nonlinear_static

    solution = solve_nonlinear_static(
        yielding_plate(1_500_000.0), target_size=1 / 8, num_steps=10,
        fracture=fracture(0.006),
    )
    assert solution.status == "stopped_at_limit"
    assert solution.info["deleted_fraction"] > 0.25


# ----------------------------------------------------------------------
# round trip
# ----------------------------------------------------------------------


