"""Scene building, without a display.

What gets drawn is plain data, so it can be checked directly: that every
entity carries a tag picking can resolve, and that a plate is drawn as the
same surface it will be meshed as.
"""

from __future__ import annotations

import numpy as np
import pytest

from anyfem import Project, pinned, solve_linear_static, steel
from anyfem.geometry.entities import EntityRef
from anyfem.selection import parse_entity_tag
from anyfem.ui.scene import (
    build_geometry_scene,
    build_mesh_scene,
    build_result_scene,
    face_display_polygons,
)


@pytest.fixture
def plate_project():
    project = Project(name="scene")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)])
    edges = geometry.add_polyline(points, close=True)
    face = geometry.add_face(edges)
    project.assign_plate(face, "plate")
    return project, face, edges, points


def test_geometry_scene_covers_every_entity(plate_project):
    project, face, edges, points = plate_project
    scene = build_geometry_scene(project)

    assert len(scene.faces) == 1
    assert len(scene.lines) == len(edges)
    assert len(scene.points) == len(points)


def test_every_drawn_item_carries_a_resolvable_tag(plate_project):
    project, _face, _edges, _points = plate_project
    scene = build_geometry_scene(project)

    for tag in scene.tags():
        assert parse_entity_tag(tag) is not None
    assert EntityRef("face", 1) == parse_entity_tag(scene.faces[0].tag)


def test_display_tessellation_matches_the_meshed_surface():
    """A curved plate must not be drawn as a different surface from the mesh."""

    radius = 2.0
    project = Project()
    geometry = project.geometry
    start = geometry.add_point(radius, 0.0, 0.0)
    via = geometry.add_point(radius / np.sqrt(2), radius / np.sqrt(2), 0.0)
    end = geometry.add_point(0.0, radius, 0.0)
    arc = geometry.add_arc(start, via, end)
    face = geometry.extrude([arc], (0.0, 0.0, 3.0))[0]

    polygons = face_display_polygons(geometry, face, divisions=6)
    corners = np.vstack(polygons)
    assert np.linalg.norm(corners[:, :2], axis=1) == pytest.approx(radius)


def test_beams_are_drawn_differently_from_plain_lines(plate_project):
    project, _face, edges, _points = plate_project
    from anyfem.model.sections import BeamSection

    project.add_beam_section(
        BeamSection(
            name="fb", profile="Flatbar", material="S355",
            flange_width=0.05, flange_thickness=0.02,
        )
    )
    project.assign_beam(edges[0], "fb")

    scene = build_geometry_scene(project)
    by_tag = {line.tag: line for line in scene.lines}
    beam = by_tag[f"ent_edge{edges[0]}"]
    plain = by_tag[f"ent_edge{edges[1]}"]
    assert beam.color != plain.color
    assert beam.width > plain.width


def test_arcs_are_drawn_as_curves_not_chords():
    project = Project()
    geometry = project.geometry
    start = geometry.add_point(1, 0, 0)
    via = geometry.add_point(0, 1, 0)
    end = geometry.add_point(-1, 0, 0)
    geometry.add_arc(start, via, end)

    scene = build_geometry_scene(project, curve_samples=16)
    points = scene.lines[0].points
    assert len(points) == 16
    assert np.linalg.norm(points, axis=1) == pytest.approx(1.0)


def test_mesh_scene_batches_one_patch_per_plate(plate_project):
    project, face, _edges, _points = plate_project
    mesh = project.generate_mesh(0.25)
    scene = build_mesh_scene(project, mesh)

    assert len(scene.faces) == 1
    patch = scene.faces[0]
    assert patch.tag == f"ent_face{face}"
    assert len(patch.polygons) == len(mesh.quads)


def test_scene_bounds_and_size(plate_project):
    project, _face, _edges, _points = plate_project
    scene = build_geometry_scene(project)
    low, high = scene.bounds()

    assert low == pytest.approx([0, 0, 0])
    assert high == pytest.approx([2, 1, 0])
    assert scene.characteristic_size() == pytest.approx(np.sqrt(5.0))


def test_empty_scene_has_no_bounds():
    scene = build_geometry_scene(Project())
    assert scene.bounds() is None
    assert scene.characteristic_size() == 1.0


def solved(project, face, edges):
    for edge in edges:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), 10_000.0)
    return solve_linear_static(project, target_size=0.25)


def test_result_scene_colours_and_deforms(plate_project):
    project, face, edges, _points = plate_project
    solution = solved(project, face, edges)

    flat = build_result_scene(solution, scale=0.0)
    deformed = build_result_scene(solution, scale=100.0)

    assert len(flat.faces) == 1
    # Colouring varies across the plate, so more than one colour appears.
    assert len(set(flat.faces[0].colors)) > 1
    # Scaling moves the surface out of plane.
    flat_z = np.vstack(flat.faces[0].polygons)[:, 2]
    deformed_z = np.vstack(deformed.faces[0].polygons)[:, 2]
    assert np.allclose(flat_z, 0.0)
    assert np.abs(deformed_z).max() > 0.0


def test_result_scene_carries_a_legend(plate_project):
    project, face, edges, _points = plate_project
    solution = solved(project, face, edges)
    scene = build_result_scene(solution, component="uz")

    assert scene.legend is not None
    assert scene.legend["title"] == "Displacement Z"
    assert len(scene.legend["levels"]) == 5


def test_result_scene_components_differ(plate_project):
    project, face, edges, _points = plate_project
    solution = solved(project, face, edges)

    vertical = build_result_scene(solution, component="uz").faces[0].colors
    horizontal = build_result_scene(solution, component="ux").faces[0].colors
    assert vertical != horizontal


# ----------------------------------------------------------------------
# the loads and supports overlay
# ----------------------------------------------------------------------
def loaded_project(plate_project):
    from anyfem.model import Mass, prescribed

    project, face, edges, points = plate_project
    project.add_support(pinned(project.edge(edges[0])))
    project.add_support(prescribed(project.edge(edges[2]), uz=0.01))
    project.add_mass(Mass(ref=project.point(points[0]), value=500.0))
    case = project.load_case()
    case.add_pressure(project.face(face), 10_000.0)
    case.add_point_load(project.point(points[1]), force=(0, 0, -1000.0))
    case.add_line_load(project.edge(edges[1]), (0, 0, -500.0))
    case.add_surface_traction(project.face(face), (100.0, 0, 0))
    return project, face, edges, points


def test_overlay_draws_something_for_every_attribute(plate_project):
    from anyfem.ui.scene import build_attribute_overlay

    project, _face, _edges, _points = loaded_project(plate_project)
    overlay = build_attribute_overlay(project)

    assert overlay.arrows
    assert overlay.points


def test_overlay_never_carries_an_entity_tag(plate_project):
    """An arrow over a plate must not steal the plate's click."""

    from anyfem.ui.scene import build_attribute_overlay

    project, _face, _edges, _points = loaded_project(plate_project)
    overlay = build_attribute_overlay(project)
    assert overlay.tags() == []


def test_merging_the_overlay_keeps_the_geometry_pickable(plate_project):
    from anyfem.ui.scene import build_attribute_overlay, build_geometry_scene

    project, face, _edges, _points = loaded_project(plate_project)
    merged = build_geometry_scene(project).merge(
        build_attribute_overlay(project)
    )
    assert f"ent_face{face}" in merged.tags()
    assert merged.arrows


def test_pressure_arrows_flip_with_the_sign(plate_project):
    from anyfem.ui.scene import build_attribute_overlay

    project, face, edges, _points = plate_project
    project.add_support(pinned(project.edge(edges[0])))
    project.load_case("push").add_pressure(project.face(face), 1000.0)
    project.load_case("pull").add_pressure(project.face(face), -1000.0)

    push = build_attribute_overlay(project, case_name="push").arrows
    pull = build_attribute_overlay(project, case_name="pull").arrows
    assert push and pull
    pushed = push[0].end - push[0].start
    pulled = pull[0].end - pull[0].start
    assert float(pushed @ pulled) < 0.0


def test_overlay_can_be_switched_off(plate_project):
    from anyfem.ui.scene import build_attribute_overlay

    project, _face, _edges, _points = loaded_project(plate_project)
    bare = build_attribute_overlay(
        project, show_supports=False, show_loads=False, show_masses=False
    )
    assert not bare.arrows
    assert not bare.points


def test_overlay_shows_only_the_named_case(plate_project):
    from anyfem.ui.scene import build_attribute_overlay

    project, face, edges, _points = plate_project
    project.add_support(pinned(project.edge(edges[0])))
    project.load_case("a").add_pressure(project.face(face), 1000.0)
    project.load_case("b")

    assert build_attribute_overlay(project, case_name="a").arrows
    assert not build_attribute_overlay(project, case_name="b").arrows


def test_overlay_bounds_include_the_arrows(plate_project):
    from anyfem.ui.scene import build_attribute_overlay

    project, _face, _edges, _points = loaded_project(plate_project)
    overlay = build_attribute_overlay(project)
    low, high = overlay.bounds()
    assert np.all(np.isfinite(low)) and np.all(np.isfinite(high))
