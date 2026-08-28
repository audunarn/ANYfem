"""Fields, probes, paths, envelopes and reports.

Stresses are checked against closed-form answers where they exist, and against
each other where they do not: a probe, a path plot and a contour must all agree
about what "von Mises" means, because they all go through the same field.
"""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import Project, fixed, pinned, solve_linear_static, steel
from anyfem.model import BeamSection
from anyfem.post import (
    along_line,
    available_fields,
    envelope,
    evaluate_field,
    field_to_csv,
    field_unit,
    probe,
    report_markdown,
    write_csv,
    write_report,
)
from anyfem.post.fields import _reduce

MODULUS = 210.0e9
POISSON = 0.3


@pytest.fixture
def loaded_plate():
    """A 1 m simply supported square plate under 10 kPa."""

    side, thickness, pressure = 1.0, 0.010, 10_000.0
    project = Project(name="deck")
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
    solution = solve_linear_static(project, target_size=side / 16)
    return project, face, edges, points, solution


# ----------------------------------------------------------------------
# fields
# ----------------------------------------------------------------------
def test_max_abs_reduction_preserves_a_negative_peak_in_two_dimensions():
    values = np.array([[1.0, -7.0], [3.0, 2.0]])

    assert _reduce(values, "max_abs") == -7.0


def test_max_abs_reduction_uses_the_first_c_order_value_for_tied_peaks():
    values = np.array([[1.0, -7.0], [7.0, 2.0]])

    assert _reduce(values, "max_abs") == -7.0


def test_plate_bending_stress_matches_timoshenko(loaded_plate):
    """Against sigma = 6M/t^2 with M = 0.0479 q a^2."""

    _project, _face, _edges, _points, solution = loaded_plate
    side, thickness, pressure = 1.0, 0.010, 10_000.0

    field = evaluate_field(solution, "bending_xx", reduction="max_abs")
    _key, peak = field.extreme()

    moment = 0.0479 * pressure * side**2
    expected = 6.0 * moment / thickness**2
    assert abs(peak) == pytest.approx(expected, rel=0.02)


def test_top_surface_stress_is_membrane_plus_bending(loaded_plate):
    _project, _face, _edges, _points, solution = loaded_plate

    membrane = evaluate_field(solution, "membrane_xx")
    bending = evaluate_field(solution, "bending_xx")
    top = evaluate_field(solution, "top_xx")
    bottom = evaluate_field(solution, "bottom_xx")

    for element_id in top.element_values:
        assert top.element_values[element_id] == pytest.approx(
            membrane.element_values[element_id] + bending.element_values[element_id]
        )
        assert bottom.element_values[element_id] == pytest.approx(
            membrane.element_values[element_id] - bending.element_values[element_id]
        )


def test_pure_bending_has_no_membrane_stress(loaded_plate):
    """A flat plate in linear analysis does not stretch."""

    _project, _face, _edges, _points, solution = loaded_plate
    membrane = evaluate_field(solution, "membrane_xx")
    assert np.abs(membrane.array()).max() == pytest.approx(0.0, abs=1.0)


def test_beam_axial_stress_matches_force_over_area():
    length, load = 2.0, 50_000.0
    project = Project(name="bar")
    project.add_material(steel("S355", 0.020))
    section = BeamSection(
        name="bar", profile="Flatbar", material="S355",
        flange_width=0.10, flange_thickness=0.02, web_direction=(0.0, 0.0, 1.0),
    )
    project.add_beam_section(section)
    geometry = project.geometry
    start = geometry.add_point(0.0, 0.0, 0.0)
    end = geometry.add_point(length, 0.0, 0.0)
    edge = geometry.add_line(start, end)
    project.assign_beam(edge, "bar")
    project.add_support(fixed(project.point(start)))
    project.load_case().add_point_load(project.point(end), force=(load, 0, 0))

    solution = solve_linear_static(project, target_size=length / 10)
    field = evaluate_field(solution, "axial_stress", reduction="max_abs")
    _key, peak = field.extreme()
    assert peak == pytest.approx(load / section.properties()["area"], rel=0.01)


def test_a_field_knows_where_it_lives(loaded_plate):
    _project, _face, _edges, _points, solution = loaded_plate

    displacement = evaluate_field(solution, "uz")
    stress = evaluate_field(solution, "von_mises")

    assert not displacement.per_element
    assert len(displacement) == solution.built.mesh.num_nodes
    assert stress.per_element
    assert len(stress) == len(solution.built.mesh.quads)


def test_field_units_and_names():
    assert field_unit("uz") == "m"
    assert field_unit("rx") == ""
    assert field_unit("von_mises") == "Pa"
    assert "von_mises" in available_fields()
    assert "top_xx" in available_fields()


def test_an_unknown_field_is_refused(loaded_plate):
    _project, _face, _edges, _points, solution = loaded_plate
    with pytest.raises(ValueError, match="unknown field"):
        evaluate_field(solution, "banana")


def test_reductions_differ_where_they_should(loaded_plate):
    _project, _face, _edges, _points, solution = loaded_plate

    mean = evaluate_field(solution, "von_mises", reduction="mean")
    peak = evaluate_field(solution, "von_mises", reduction="max_abs")
    assert abs(peak.extreme()[1]) >= abs(mean.extreme()[1])

    with pytest.raises(ValueError, match="unknown reduction"):
        evaluate_field(solution, "von_mises", reduction="median")


def test_elements_without_a_component_are_left_out_not_zeroed():
    """A shell has no torsion; reporting zero would invent a number."""

    project = Project(name="mixed")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    project.add_beam_section(
        BeamSection(
            name="fb", profile="Flatbar", material="S355",
            flange_width=0.05, flange_thickness=0.02,
        )
    )
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    project.assign_beam(edges[0], "fb")
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), 10_000.0)

    solution = solve_linear_static(project, target_size=0.5)
    torsion = evaluate_field(solution, "torsional_stress")
    membrane = evaluate_field(solution, "membrane_xx")

    assert set(torsion.element_values) <= set(solution.built.mesh.beams)
    assert torsion.missing
    assert set(membrane.element_values) <= set(solution.built.mesh.quads)


# ----------------------------------------------------------------------
# probes
# ----------------------------------------------------------------------
def test_a_probe_at_a_supported_point_reads_zero(loaded_plate):
    _project, _face, _edges, points, solution = loaded_plate
    from anyfem.geometry.entities import EntityRef

    reading = probe(solution, EntityRef("vertex", points[0]))
    assert reading.displacement["uz"] == pytest.approx(0.0, abs=1e-12)
    assert reading.node_id is not None


def test_a_plate_probe_reports_the_extreme(loaded_plate):
    project, face, _edges, _points, solution = loaded_plate

    reading = probe(solution, project.face(face))
    _node, magnitude = solution.max_translation()
    assert abs(reading.displacement["uz"]) == pytest.approx(magnitude, rel=1e-9)
    assert "von_mises" in reading.stresses
    assert reading.elements


def test_a_probe_agrees_with_the_field(loaded_plate):
    project, face, _edges, _points, solution = loaded_plate

    reading = probe(solution, project.face(face), reduction="max_abs")
    field = evaluate_field(solution, "von_mises", reduction="max_abs")
    _key, peak = field.extreme()
    assert reading.stresses["von_mises"] == pytest.approx(peak)


def test_probe_text_is_readable(loaded_plate):
    project, face, _edges, _points, solution = loaded_plate
    text = probe(solution, project.face(face)).text()
    assert "displacement" in text
    assert "von_mises" in text


# ----------------------------------------------------------------------
# along a line
# ----------------------------------------------------------------------
def test_a_supported_edge_has_no_deflection_along_it(loaded_plate):
    project, _face, edges, _points, solution = loaded_plate

    path = along_line(solution, project.edge(edges[0]), "uz")
    assert len(path) == len(solution.built.mesh.nodes_on(project.edge(edges[0])))
    assert np.abs(path.values).max() == pytest.approx(0.0, abs=1e-12)
    assert path.length == pytest.approx(1.0)


def test_a_path_across_the_plate_peaks_in_the_middle():
    """Cut the plate in half so a line runs through its centre."""

    from anyfem.geometry.operations import split_face_at

    side, thickness, pressure = 1.0, 0.010, 10_000.0
    project = Project(name="split")
    project.add_material(steel("S355", thickness))
    project.add_plate_section("plate", thickness=thickness, material="S355")
    geometry = project.geometry
    points = geometry.add_points(
        [(0, 0, 0), (side, 0, 0), (side, side, 0), (0, side, 0)]
    )
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    middle, halves = split_face_at(geometry, face, axis=0, fraction=0.5)
    for half in halves:
        project.assign_plate(half, "plate")
    # Splitting replaced two of the original edges, so support whatever now
    # forms the free boundary rather than the ids captured beforehand.
    for edge_id in geometry.edges:
        if len(geometry.faces_using_edge(edge_id)) == 1:
            project.add_support(pinned(project.edge(edge_id)))
    for half in halves:
        project.load_case().add_pressure(project.face(half), pressure)

    solution = solve_linear_static(project, target_size=side / 12)
    path = along_line(solution, project.edge(middle), "uz")

    # Zero at the supported ends, largest in the middle.
    assert path.values[0] == pytest.approx(0.0, abs=1e-12)
    assert path.values[-1] == pytest.approx(0.0, abs=1e-12)
    peak = int(np.argmax(np.abs(path.values)))
    assert 0 < peak < len(path) - 1
    assert abs(path.values[peak]) == pytest.approx(
        solution.max_translation()[1], rel=0.05
    )


def test_a_path_of_a_stress_field_works_too(loaded_plate):
    project, _face, edges, _points, solution = loaded_plate
    path = along_line(solution, project.edge(edges[0]), "von_mises")
    assert path.unit == "Pa"
    assert np.all(path.values >= 0.0)
    assert "distance_m,von_mises_Pa" in path.to_csv()


def test_along_line_needs_a_line(loaded_plate):
    project, face, _edges, _points, solution = loaded_plate
    with pytest.raises(ValueError, match="expects a line"):
        along_line(solution, project.face(face), "uz")


# ----------------------------------------------------------------------
# envelopes
# ----------------------------------------------------------------------
def test_an_envelope_bounds_every_shape(loaded_plate):
    from anyfem import solve_transient

    project, _face, _edges, _points, _solution = loaded_plate
    mesh = project.generate_mesh(0.2)
    transient = solve_transient(project, mesh=mesh, dt=2.0e-4, t_end=0.01)

    bound = envelope(transient, "magnitude")
    assert bound.shape_count == len(transient)

    for shape in transient:
        field = evaluate_field(shape, "magnitude")
        for key, value in field.values.items():
            assert abs(bound.field.values[key]) >= abs(value) - 1e-12


def test_an_envelope_matches_the_solver_peak(loaded_plate):
    from anyfem import solve_transient

    project, _face, _edges, _points, _solution = loaded_plate
    mesh = project.generate_mesh(0.2)
    transient = solve_transient(project, mesh=mesh, dt=2.0e-4, t_end=0.01)

    bound = envelope(transient, "magnitude")
    _key, peak = bound.field.extreme()
    assert abs(peak) == pytest.approx(transient.peak_displacement, rel=1e-9)
    assert bound.worst_shape() is not None


def test_a_static_result_envelopes_to_itself(loaded_plate):
    _project, _face, _edges, _points, solution = loaded_plate
    bound = envelope(solution, "von_mises")
    assert bound.shape_count == 1
    direct = evaluate_field(solution, "von_mises")
    assert bound.field.extreme()[1] == pytest.approx(direct.extreme()[1])


def test_envelope_modes_differ(loaded_plate):
    _project, _face, _edges, _points, solution = loaded_plate
    highest = envelope(solution, "uz", mode="max").field.range()[1]
    lowest = envelope(solution, "uz", mode="min").field.range()[0]
    assert highest >= lowest

    with pytest.raises(ValueError, match="unknown envelope mode"):
        envelope(solution, "uz", mode="sideways")


# ----------------------------------------------------------------------
# reports
# ----------------------------------------------------------------------
def test_the_report_states_the_model_and_the_extremes(loaded_plate):
    _project, _face, _edges, _points, solution = loaded_plate
    text = report_markdown(solution)

    assert "# deck" in text
    assert "nodes:" in text
    assert "## Extremes" in text
    assert "von_mises" in text
    assert "## Per plate" in text
    # It reports; it does not pass judgement.
    assert "engineering judgement" in text


def test_the_report_lists_shapes_for_a_multi_shape_result(loaded_plate):
    from anyfem import solve_modal

    project, _face, _edges, _points, _solution = loaded_plate
    modal = solve_modal(project, target_size=0.2, num_modes=3)
    text = report_markdown(modal)
    assert "## Shapes" in text
    assert "mode 1" in text


def test_writing_a_report_and_a_csv(loaded_plate):
    project, _face, edges, _points, solution = loaded_plate
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        tmp_path = Path(directory)

        report = write_report(solution, tmp_path / "out" / "report.md")
        assert report.exists()
        assert "# deck" in report.read_text(encoding="utf-8")

        csv_text = field_to_csv(solution, "von_mises")
        header, first = csv_text.splitlines()[:2]
        assert header == "id,x,y,z,von_mises_Pa"
        assert len(first.split(",")) == 5

        path_file = write_csv(
            along_line(solution, project.edge(edges[0]), "uz"),
            tmp_path / "path.csv",
        )
        assert path_file.exists()
        assert path_file.read_text(encoding="utf-8").startswith("distance_m,uz_m")


def test_a_nodal_csv_carries_node_positions(loaded_plate):
    _project, _face, _edges, _points, solution = loaded_plate
    text = field_to_csv(solution, "uz")
    assert text.splitlines()[0] == "id,x,y,z,uz_m"
    assert len(text.splitlines()) == solution.built.mesh.num_nodes + 1


# ----------------------------------------------------------------------
# contouring
# ----------------------------------------------------------------------
def test_the_scene_can_colour_by_a_stress_field(loaded_plate):
    from anyfem.ui.scene import build_result_scene

    _project, _face, _edges, _points, solution = loaded_plate
    scene = build_result_scene(solution, field="von_mises")

    assert scene.legend["unit"] == "Pa"
    assert scene.legend["title"] == "von Mises"
    assert len(set(scene.faces[0].colors)) > 1


def test_result_scene_converts_stress_legend_to_mpa_and_uses_palette(loaded_plate):
    from anyfem.ui.scene import RESULT_COLORMAPS, build_result_scene

    _project, _face, _edges, _points, solution = loaded_plate
    scene = build_result_scene(
        solution,
        field="von_mises",
        display_units="Engineering (mm / MPa)",
        colormap=RESULT_COLORMAPS["Viridis"],
    )

    assert scene.legend["unit"] == "MPa"
    assert scene.legend["levels"][-1] < 10_000.0
    assert scene.legend["colors"][0] == "#440154"


def test_manual_colour_limits_are_honoured(loaded_plate):
    from anyfem.ui.scene import build_result_scene

    _project, _face, _edges, _points, solution = loaded_plate
    scene = build_result_scene(solution, field="von_mises", limits=(0.0, 3.0e7))
    assert scene.legend["levels"][0] == pytest.approx(0.0)
    assert scene.legend["levels"][-1] == pytest.approx(3.0e7)


def test_an_envelope_can_be_contoured(loaded_plate):
    from anyfem.ui.scene import build_result_scene

    _project, _face, _edges, _points, solution = loaded_plate
    bound = envelope(solution, "von_mises")
    scene = build_result_scene(solution, values=bound.field)
    assert scene.faces
    assert "envelope" in scene.legend["title"]
