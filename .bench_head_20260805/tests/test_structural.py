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

from anyfem import Project, pinned, solve_linear_static, steel, support
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
def test_beam_section_defaults_to_no_eccentricity():
    section = BeamSection(
        name="bar", profile="Flatbar", material="S355",
        flange_width=0.01, flange_thickness=0.2,
    )
    assert section.eccentricity == 0.0


def test_beam_offsets_lists_only_offset_stiffeners():
    project, _divider = stiffened_strip(OFFSET)
    assert list(project.beam_offsets.values()) == [pytest.approx(OFFSET)]

    project, _divider = stiffened_strip(0.0)
    assert project.beam_offsets == {}


# ----------------------------------------------------------------------
# meshing
# ----------------------------------------------------------------------
def test_shared_node_stiffener_makes_no_offset_nodes():
    project, divider = stiffened_strip(0.0)
    mesh = project.generate_mesh(0.1)

    assert mesh.offset_nodes_of_edge == {}
    assert mesh.couplings == {}
    # The beam elements sit directly on the plating nodes.
    plating = set(mesh.nodes_of_edge[divider])
    for element_id in mesh.elements_of_edge[divider]:
        assert set(mesh.beams[element_id]) <= plating


def test_offset_nodes_stand_off_along_the_plate_normal():
    project, divider = stiffened_strip(OFFSET)
    mesh = project.generate_mesh(0.1)

    plating = mesh.nodes_of_edge[divider]
    offset = mesh.offset_nodes_of_edge[divider]
    assert len(offset) == len(plating)
    assert not set(offset) & set(plating)

    for plate_node, beam_node in zip(plating, offset):
        separation = mesh.nodes[beam_node] - mesh.nodes[plate_node]
        assert separation == pytest.approx([0.0, 0.0, OFFSET])


def test_every_station_is_coupled_back_to_the_plating():
    project, divider = stiffened_strip(OFFSET)
    mesh = project.generate_mesh(0.1)

    plating = mesh.nodes_of_edge[divider]
    assert len(mesh.couplings) == len(plating)
    coupled = {
        (beam, plate) for beam, plate in mesh.couplings.values()
    }
    expected = set(zip(mesh.offset_nodes_of_edge[divider], plating))
    assert coupled == expected


def test_beams_run_on_the_offset_nodes():
    project, divider = stiffened_strip(OFFSET)
    mesh = project.generate_mesh(0.1)

    offset = set(mesh.offset_nodes_of_edge[divider])
    for element_id in mesh.elements_of_edge[divider]:
        assert set(mesh.beams[element_id]) <= offset


def test_supports_do_not_land_on_slaved_offset_nodes():
    """A prescribed DOF on a slave node is a contradiction the solver rejects."""

    project, divider = stiffened_strip(OFFSET)
    mesh = project.generate_mesh(0.1)

    offset = set(mesh.offset_nodes_of_edge[divider])
    for constraint in project.supports:
        assert not set(mesh.constrained_nodes_on(constraint.ref)) & offset


# ----------------------------------------------------------------------
# the built model and the solve
# ----------------------------------------------------------------------
def test_couplings_reach_the_solver_as_mpc_elements():
    from anysolver import CoupledBeamShellElement

    from anyfem.solve.build import build_fe_model

    project, _divider = stiffened_strip(OFFSET)
    mesh = project.generate_mesh(0.1)
    built = build_fe_model(project, mesh)

    elements = built.fe_model.mesh.elements.values()
    coupling_elements = [
        element for element in elements
        if isinstance(element, CoupledBeamShellElement)
    ]
    assert len(coupling_elements) == len(mesh.couplings)


def test_the_solve_actually_applies_the_constraints():
    project, _divider = stiffened_strip(OFFSET)
    mesh = project.generate_mesh(0.1)
    solution = solve_linear_static(project, mesh=mesh)

    info = solution.info["constraint_info"]
    # Six slaved DOFs per coupled station.
    assert info["num_mpc_constraints"] == 6 * len(mesh.couplings)


def test_coupled_nodes_follow_the_rigid_offset():
    """u_beam = u_shell + theta_shell x r, which is the whole mechanism."""

    project, divider = stiffened_strip(OFFSET)
    mesh = project.generate_mesh(0.05)
    solution = solve_linear_static(project, mesh=mesh)

    plating = mesh.nodes_of_edge[divider]
    offset = mesh.offset_nodes_of_edge[divider]
    for plate_node, beam_node in zip(plating, offset):
        plate = solution.node_displacement(plate_node)
        beam = solution.node_displacement(beam_node)
        assert beam[0] - plate[0] == pytest.approx(plate[4] * OFFSET, abs=1e-12)
        assert beam[1] - plate[1] == pytest.approx(-plate[3] * OFFSET, abs=1e-12)
        # Rotations are carried across unchanged.
        assert beam[3:] == pytest.approx(plate[3:])


def test_eccentric_stiffener_is_markedly_stiffer():
    section = transformed_section()
    deflection = {}
    for label, eccentricity in (("shared", 0.0), ("eccentric", OFFSET)):
        project, _divider = stiffened_strip(eccentricity)
        solution = solve_linear_static(project, target_size=0.05)
        deflection[label] = solution.max_translation()[1]

    ratio = deflection["shared"] / deflection["eccentric"]
    expected = section["eccentric"] / section["shared"]
    assert ratio == pytest.approx(expected, rel=0.03)
    # Not a rounding-level difference: the section is more than twice as stiff.
    assert ratio > 2.0


def test_neutral_axis_matches_the_transformed_section():
    project, divider = stiffened_strip(OFFSET)
    mesh = project.generate_mesh(0.05)
    solution = solve_linear_static(project, mesh=mesh)

    plating = mesh.nodes_of_edge[divider]
    offset = mesh.offset_nodes_of_edge[divider]
    stations = np.array([mesh.nodes[node][0] for node in plating])
    first = int(np.argmin(np.abs(stations - 1.9)))
    last = int(np.argmin(np.abs(stations - 2.1)))
    span = stations[last] - stations[first]

    def axial_strain(nodes):
        return (
            solution.node_displacement(nodes[last])[0]
            - solution.node_displacement(nodes[first])[0]
        ) / span

    plate_strain = axial_strain(plating)
    bar_strain = axial_strain(offset)
    # The plate and the bar sit on opposite sides of the neutral axis.
    assert plate_strain * bar_strain < 0.0

    height = OFFSET * plate_strain / (plate_strain - bar_strain)
    assert height == pytest.approx(transformed_section()["neutral_axis"], rel=0.01)


# ----------------------------------------------------------------------
# supports take values, not flags
# ----------------------------------------------------------------------
def test_a_boolean_support_value_is_refused():
    project, _divider = stiffened_strip(0.0)
    edge = project.edge(next(iter(project.geometry.edges)))
    with pytest.raises(ValueError, match="prescribed displacement, not a flag"):
        support(edge, uz=True)


def test_a_numeric_support_value_still_prescribes_it():
    project, _divider = stiffened_strip(0.0)
    edge = project.edge(next(iter(project.geometry.edges)))
    assert support(edge, uz=0.005).constraints == {"uz": 0.005}


# ----------------------------------------------------------------------
# hardening curves
# ----------------------------------------------------------------------
def test_plain_steel_stays_elastic():
    material = steel("S355", 0.020)
    assert not material.is_nonlinear
    assert material.hardening_curve() is None


def test_nonlinear_steel_carries_its_own_table_row():
    material = steel("S355", 0.020, nonlinear=True)
    assert material.is_nonlinear
    curve = material.hardening_curve()
    assert curve.sigma_yield == pytest.approx(material.yield_stress)
    assert curve.sigma_prop <= material.yield_stress
    # Hardening, not perfect plasticity.
    assert curve.flow_stress(0.05) > curve.flow_stress(0.0)


def test_an_unknown_hardening_source_is_refused():
    from anyfem.model.materials import Material

    with pytest.raises(ValueError, match="unknown hardening source"):
        Material(
            name="odd", elastic_modulus=210e9, poisson_ratio=0.3,
            hardening=("ramberg_osgood", "S355", 0.02),
        )


def test_the_hardening_curve_reaches_the_built_material():
    from anyfem.solve.build import build_fe_model

    project = yielding_plate(1000.0)
    built = build_fe_model(project, project.generate_mesh(0.25))

    curve = getattr(built.fe_model.materials["S355"], "hardening_curve", None)
    assert curve is not None
    assert curve.sigma_yield == pytest.approx(
        project.materials["S355"].yield_stress
    )


def test_an_elastic_material_builds_without_a_curve():
    from anyfem.solve.build import build_fe_model

    project = yielding_plate(1000.0, nonlinear=False)
    built = build_fe_model(project, project.generate_mesh(0.25))

    assert getattr(built.fe_model.materials["S355"], "hardening_curve", None) is None


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
def test_fracture_builds_the_solver_configuration():
    from anysolver import FractureConfig

    config = fracture(0.15)
    assert isinstance(config, FractureConfig)
    assert config.threshold == pytest.approx(0.15)


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
def test_eccentricity_and_hardening_survive_a_project_file(workspace):
    from anyfem.io import load_project, save_project

    project, _divider = stiffened_strip(OFFSET)
    project.add_material(steel("S355", THICKNESS, nonlinear=True))

    path = workspace / "strip.anyfem"
    save_project(project, path)
    restored = load_project(path)

    assert restored.beam_sections["bar"].eccentricity == pytest.approx(OFFSET)
    assert restored.materials["S355"].hardening == ("dnv_c208", "S355", THICKNESS)
    assert restored.beam_offsets == pytest.approx(project.beam_offsets)


def test_a_restored_model_still_couples_the_stiffener(workspace):
    from anyfem.io import load_project, save_project

    project, _divider = stiffened_strip(OFFSET)
    path = workspace / "strip.anyfem"
    save_project(project, path)

    restored = load_project(path)
    assert len(restored.generate_mesh(0.1).couplings) == len(
        project.generate_mesh(0.1).couplings
    )
