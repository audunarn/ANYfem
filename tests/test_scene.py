"""Scene building, without a display.

What gets drawn is plain data, so it can be checked directly: that every
entity carries a tag picking can resolve, and that a plate is drawn as the
same surface it will be meshed as.
"""

from __future__ import annotations

import numpy as np
import pytest
from anygeometry import punch_hole

from anyfem import BeamSection, Project, pinned, solve_linear_static, steel
from anyfem import commands as cmd
from anyfem.model import member_bow, plate_mode
from anyfem.post.fields import Field
from anyfem.geometry.entities import EntityRef
from anyfem.selection import MeshEntityRef, parse_entity_tag
from anyfem.ui.scene import (
    COLOR_LOAD,
    COLOR_MESH_FILL,
    COLOR_MASS,
    COLOR_MOMENT,
    COLOR_ROTATION,
    COLOR_SUPPORT,
    COLOR_IMPERFECTION,
    OVERLAY_SYMBOL_LIMIT,
    build_geometry_scene,
    build_imperfection_overlay,
    build_mesh_scene,
    build_result_scene,
    face_display_polygons,
    geometry_display_resolution,
    geometry_characteristic_size,
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


def test_scene_copy_can_receive_overlays_without_mutating_cached_geometry(
    plate_project,
):
    project, _face, _edges, _points = plate_project
    cached = build_geometry_scene(project)
    displayed = cached.copy()

    displayed.lines.clear()
    displayed.legend = {"title": "temporary"}

    assert cached.lines
    assert cached.legend is None


def test_generated_topology_is_lightweight_until_feature_is_exploded():
    project = Project(name="light generated geometry")
    stack = cmd.CommandStack(project)
    manual_point = stack.run(cmd.AddPoint(3.0, 0.0, 0.0))
    cylinder = stack.run(
        cmd.AddCylinder(1.0, 2.0, circumferential_segments=8)
    )

    collapsed = build_geometry_scene(project)

    # Exact generated faces remain visible as one feature-level surface, while
    # implementation-level vertices and edges do not become retained draw
    # objects by default. Picking that surface targets the complete feature.
    assert len(collapsed.faces) == 1
    assert collapsed.faces[0].owners == tuple(
        EntityRef("face", face_id) for face_id in project.geometry.faces
    )
    assert len(collapsed.faces[0].polygons) == 32
    assert not collapsed.lines
    assert [marker.ref for marker in collapsed.points] == [
        EntityRef("vertex", manual_point)
    ]

    exploded = build_geometry_scene(
        project, exposed_feature_ids=(cylinder.feature_id,)
    )
    assert len(exploded.faces) == len(project.geometry.faces)
    assert len(exploded.lines) == len(project.geometry.edges)
    assert len(exploded.points) == len(project.geometry.vertices)
    assert sum(len(patch.polygons) for patch in exploded.faces) == 512


def test_flat_geometry_uses_native_display_complexity(plate_project):
    """A flat quad and straight edges need no display-only subdivision."""

    project, _face, _edges, _points = plate_project
    scene = build_geometry_scene(project)

    assert sum(len(patch.polygons) for patch in scene.faces) == 1
    assert all(len(line.points) == 2 for line in scene.lines)
    assert geometry_characteristic_size(project.geometry) == pytest.approx(
        np.sqrt(5.0)
    )


def test_trimmed_face_tessellation_does_not_fill_its_hole(plate_project):
    project, face, _edges, _points = plate_project
    centre = np.asarray((1.0, 0.5, 0.0))
    radius = 0.2
    punch_hole(project.geometry, face, centre, radius)

    polygons = face_display_polygons(project.geometry, face)

    assert len(polygons) > 1
    assert all(len(polygon) == 3 for polygon in polygons)
    assert all(
        np.linalg.norm(np.mean(polygon, axis=0) - centre) >= radius
        for polygon in polygons
    )
    area = sum(
        0.5 * np.linalg.norm(np.cross(polygon[1] - polygon[0], polygon[2] - polygon[0]))
        for polygon in polygons
    )
    assert area == pytest.approx(2.0 - np.pi * radius**2, rel=2.0e-3)


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
    assert beam.draw_overlay
    assert not plain.draw_overlay


def test_assigned_beam_section_is_swept_in_geometry_mesh_and_results(plate_project):
    project, face, edges, _points = plate_project
    project.add_beam_section(
        BeamSection(
            name="tee",
            profile="T-bar",
            material="S355",
            web_height=0.20,
            web_thickness=0.010,
            flange_width=0.10,
            flange_thickness=0.012,
            offset_mode="automatic",
        )
    )
    project.assign_beam(edges[0], "tee")

    geometry_scene = build_geometry_scene(project)
    geometry_solids = [
        patch for patch in geometry_scene.faces
        if patch.ref == EntityRef("edge", edges[0])
    ]
    assert geometry_solids
    assert len(geometry_solids[0].polygons) == 12

    for edge in edges[1:]:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), 10_000.0)
    solution = solve_linear_static(project, target_size=0.25)
    mesh_scene = build_mesh_scene(project, solution.built.mesh)
    result_scene = build_result_scene(solution)
    assert any(patch.ref == EntityRef("edge", edges[0]) for patch in mesh_scene.faces)
    assert any(patch.ref == EntityRef("edge", edges[0]) for patch in result_scene.faces)


def test_back_side_axial_rotation_offsets_from_plate_intersection(plate_project):
    project, _face, edges, _points = plate_project
    project.add_beam_section(
        BeamSection(
            name="rotated tee",
            profile="T-bar",
            material="S355",
            web_height=0.20,
            web_thickness=0.010,
            flange_width=0.10,
            flange_thickness=0.012,
            offset_mode="automatic",
            attachment_side="back",
            rotation_deg=90.0,
        )
    )
    project.assign_beam(edges[0], "rotated tee")

    offset = np.asarray(project.beam_offset_vector(edges[0]))
    assert offset[0] == pytest.approx(0.0, abs=1.0e-12)
    assert offset[1] > 0.0
    assert offset[2] == pytest.approx(0.0, abs=1.0e-12)


def test_coplanar_diagonal_beam_is_connected_and_drawn_continuously(plate_project):
    project, _face, _edges, points = plate_project
    from anyfem.model.sections import BeamSection

    diagonal = project.geometry.add_line(points[0], points[2])
    project.add_beam_section(
        BeamSection(
            name="diagonal", profile="Flatbar", material="S355",
            flange_width=0.05, flange_thickness=0.02,
        )
    )
    project.assign_beam(diagonal, "diagonal")

    mesh = project.generate_mesh(0.25)
    scene = build_mesh_scene(project, mesh)
    beam_elements = tuple(mesh.elements_of_edge[diagonal])
    beam_lines = [
        line for line in scene.lines if line.ref == EntityRef("edge", diagonal)
    ]

    assert len(beam_lines) == len(beam_elements)
    assert all(line.draw_overlay for line in beam_lines)
    shell_nodes = {node for shell in mesh.shells.values() for node in shell}
    coupled_nodes = {item.beam_node for item in mesh.couplings.values()}
    assert set(mesh.nodes_of_edge[diagonal]).issubset(shell_nodes | coupled_nodes)


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


def test_curved_geometry_keeps_display_tessellation():
    project = Project()
    geometry = project.geometry
    start = geometry.add_point(2.0, 0.0, 0.0)
    via = geometry.add_point(np.sqrt(2.0), np.sqrt(2.0), 0.0)
    end = geometry.add_point(0.0, 2.0, 0.0)
    arc = geometry.add_arc(start, via, end)
    face = geometry.extrude([arc], (0.0, 0.0, 3.0))[0]

    scene = build_geometry_scene(project)

    patch = next(item for item in scene.faces if item.ref.id == face)
    curve = next(item for item in scene.lines if item.ref.id == arc)
    assert len(patch.polygons) == 8 * 8
    assert len(curve.points) == 24


def test_geometry_display_resolution_adapts_to_curved_face_count():
    from types import SimpleNamespace

    def geometry(count):
        return SimpleNamespace(
            faces={
                identifier: SimpleNamespace(support_surface=object())
                for identifier in range(count)
            }
        )

    fine = geometry_display_resolution(geometry(64))
    balanced = geometry_display_resolution(geometry(65))
    fast = geometry_display_resolution(geometry(257))

    assert (fine.divisions, fine.interaction_divisions) == (8, 2)
    assert (balanced.divisions, balanced.interaction_divisions) == (4, 1)
    assert (fast.divisions, fast.interaction_divisions) == (2, 1)
    assert geometry_display_resolution(geometry(1000), "Fine") == fine


def test_straight_topology_does_not_flatten_an_explicit_ruled_surface():
    from anygeometry.surfaces import RuledSurface

    project = Project()
    geometry = project.geometry
    points = geometry.add_points(
        [(0, 0, 0), (1, 0, 0), (1, 1, 1), (0, 1, 0)]
    )
    face = geometry.add_face(geometry.add_polyline(points, close=True))
    geometry.set_face_surface(
        face,
        RuledSurface(
            np.array([(0, 0, 0), (1, 0, 0)]),
            np.array([(0, 1, 0), (1, 1, 1)]),
        ),
    )

    patch = build_geometry_scene(project).faces[0]

    assert len(patch.polygons) == 8 * 8
    height = max(point[2] for polygon in patch.polygons for point in polygon)
    assert height == pytest.approx(1.0)


def test_mesh_scene_batches_one_patch_per_plate(plate_project):
    project, face, _edges, _points = plate_project
    mesh = project.generate_mesh(0.25)
    scene = build_mesh_scene(project, mesh)

    assert len(scene.faces) == 1
    patch = scene.faces[0]
    assert patch.tag == f"ent_face{face}"
    assert len(patch.polygons) == len(mesh.quads)


def test_mesh_scene_carries_geometry_element_face_and_node_ownership(
    plate_project,
):
    project, face, _edges, points = plate_project
    mesh = project.generate_mesh(0.5)

    scene = build_mesh_scene(project, mesh)
    patch = scene.faces[0]
    element_id = mesh.elements_of_face[face][0]

    assert patch.polygon_owners is not None
    assert patch.polygon_owners[0] == (
        EntityRef("face", face),
        MeshEntityRef("element", element_id),
        MeshEntityRef("element_face", (element_id, 0)),
    )
    assert len(scene.points) == len(mesh.nodes)
    assert {
        marker.ref for marker in scene.points
    } == {
        MeshEntityRef("node", node_id) for node_id in mesh.nodes
    }

    vertex_node = mesh.node_of_vertex[points[0]]
    endpoint = next(marker for marker in scene.points if marker.ref.id == vertex_node)
    assert endpoint.owners == (
        EntityRef("vertex", points[0]),
        MeshEntityRef("node", vertex_node),
    )


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


def test_result_scene_with_shells_and_beams_builds_legend(plate_project):
    """A beam node tuple must not replace the scalar colour-range span."""

    project, face, edges, _points = plate_project
    project.add_beam_section(
        BeamSection(
            "stiffener",
            "Flatbar",
            "S355",
            flange_width=0.10,
            flange_thickness=0.010,
        )
    )
    project.assign_beam(edges[0], "stiffener")
    solution = solved(project, face, edges)

    scene = build_result_scene(solution, component="magnitude")

    assert scene.legend is not None
    assert len(scene.legend["colors"]) == 5
    assert scene.lines


def test_result_scene_components_differ(plate_project):
    project, face, edges, _points = plate_project
    solution = solved(project, face, edges)

    vertical = build_result_scene(solution, component="uz").faces[0].colors
    horizontal = build_result_scene(solution, component="ux").faces[0].colors
    assert vertical != horizontal


def test_sparse_nodal_result_is_unavailable_on_incomplete_elements(plate_project):
    """Changing quantity/frame must not crash on a partial result scope."""

    project, face, edges, _points = plate_project
    solution = solved(project, face, edges)
    one_node = min(solution.built.mesh.nodes)
    sparse = Field("partial probe", unit="m", node_values={one_node: 0.01})

    scene = build_result_scene(solution, values=sparse)

    assert scene.faces
    assert all(
        colour == COLOR_MESH_FILL
        for patch in scene.faces
        for colour in patch.colors
    )
    # The one real sample remains a valid legend value even though no whole
    # element can be contoured from it.
    assert scene.legend["levels"]


def test_empty_result_field_draws_neutral_mesh_instead_of_inventing_zero(
    plate_project,
):
    project, face, edges, _points = plate_project
    solution = solved(project, face, edges)

    scene = build_result_scene(solution, values=Field("missing result", unit="Pa"))

    assert scene.legend["colors"] == []
    assert all(
        colour == COLOR_MESH_FILL
        for patch in scene.faces
        for colour in patch.colors
    )


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


def dense_pressure_project(nx=20, ny=15):
    project = Project("dense-overlay")
    geometry = project.geometry
    vertices = {
        (i, j): geometry.add_point(float(i), float(j), 0.0)
        for j in range(ny + 1)
        for i in range(nx + 1)
    }
    horizontal = {
        (i, j): geometry.add_line(vertices[i, j], vertices[i + 1, j])
        for j in range(ny + 1)
        for i in range(nx)
    }
    vertical = {
        (i, j): geometry.add_line(vertices[i, j], vertices[i, j + 1])
        for j in range(ny)
        for i in range(nx + 1)
    }
    case = project.load_case()
    for j in range(ny):
        for i in range(nx):
            face = geometry.add_face(
                [
                    horizontal[i, j],
                    vertical[i + 1, j],
                    horizontal[i, j + 1],
                    vertical[i, j],
                ]
            )
            case.add_pressure(project.face(face), 10_000.0)
    return project


def test_overlay_draws_something_for_every_attribute(plate_project):
    from anyfem.ui.scene import build_attribute_overlay

    project, _face, _edges, _points = loaded_project(plate_project)
    overlay = build_attribute_overlay(project)

    assert overlay.arrows
    assert overlay.points


def test_plate_imperfection_preview_is_visible_and_does_not_mutate_geometry(
    plate_project,
):
    project, face, _edges, points = plate_project
    original = np.asarray([project.geometry.vertex_position(item) for item in points])
    project.add_imperfection(
        plate_mode(project.face(face), amplitude=0.001, waves=(1, 1))
    )

    overlay = build_imperfection_overlay(project)
    preview = [line for line in overlay.lines if line.color == COLOR_IMPERFECTION]
    assert preview
    assert overlay.arrows
    assert max(float(np.max(line.points[:, 2])) for line in preview) > 0.001
    assert np.asarray(
        [project.geometry.vertex_position(item) for item in points]
    ) == pytest.approx(original)

    mesh = project.generate_mesh(0.25)
    meshed_overlay = build_imperfection_overlay(project, mesh=mesh)
    assert any(line.color == COLOR_IMPERFECTION for line in meshed_overlay.lines)


def test_member_bow_preview_uses_the_imperfection_direction(plate_project):
    project, _face, edges, _points = plate_project
    project.add_imperfection(
        member_bow(project.edge(edges[0]), amplitude=0.002, direction=(0, 0, 1))
    )

    overlay = build_imperfection_overlay(project)
    line = next(item for item in overlay.lines if item.color == COLOR_IMPERFECTION)
    assert line.points[0, 2] == pytest.approx(0.0)
    assert line.points[-1, 2] == pytest.approx(0.0)
    assert np.max(line.points[:, 2]) > 0.002


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


def test_overlay_does_not_build_a_hidden_geometry_scene(plate_project, monkeypatch):
    import anyfem.ui.scene as scene_module

    project, _face, _edges, _points = loaded_project(plate_project)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("overlay rebuilt the geometry scene")

    monkeypatch.setattr(scene_module, "build_geometry_scene", unexpected)
    overlay = scene_module.build_attribute_overlay(project)
    assert overlay.arrows and overlay.points


def test_dense_pressure_symbols_are_capped_before_face_sampling(monkeypatch):
    import anyfem.ui.scene as scene_module

    project = dense_pressure_project()
    sampled = []
    normals = []
    original = scene_module.entity_sample_points
    original_normal = scene_module.face_normal

    def counted(geometry, ref, mesh=None):
        sampled.append(ref)
        return original(geometry, ref, mesh)

    def counted_normal(geometry, face_id):
        normals.append(face_id)
        return original_normal(geometry, face_id)

    monkeypatch.setattr(scene_module, "entity_sample_points", counted)
    monkeypatch.setattr(scene_module, "face_normal", counted_normal)
    overlay = scene_module.build_attribute_overlay(project)

    assert len(overlay.arrows) == OVERLAY_SYMBOL_LIMIT
    assert len(sampled) == OVERLAY_SYMBOL_LIMIT // 4
    assert len(normals) == OVERLAY_SYMBOL_LIMIT // 4
    arrow_ends = np.array([arrow.end for arrow in overlay.arrows])
    assert np.ptp(arrow_ends[:, 0]) > 18.0
    assert np.ptp(arrow_ends[:, 1]) > 13.0


def test_all_distributed_attributes_budget_entity_sampling(monkeypatch):
    import anyfem.ui.scene as scene_module
    from anyfem.model.attributes import Mass, Support

    project = dense_pressure_project()
    faces = [project.face(face_id) for face_id in project.geometry.faces]
    edges = [project.edge(edge_id) for edge_id in project.geometry.edges]
    for index, ref in enumerate(faces):
        project.add_support(Support(f"face-{index}", ref, {"uz": 0.0}))
        project.add_mass(Mass(ref, 1.0, f"face-{index}"))
        project.load_case().add_surface_traction(ref, (0.0, 0.0, -1.0))
    for ref in edges:
        project.load_case().add_line_load(ref, (0.0, 0.0, -1.0))

    sampled = []
    original = scene_module.entity_sample_points

    def counted(geometry, ref, mesh=None):
        sampled.append(ref)
        return original(geometry, ref, mesh)

    monkeypatch.setattr(scene_module, "entity_sample_points", counted)

    scene_module.build_attribute_overlay(
        project, show_loads=False, show_masses=False
    )
    assert len(sampled) == OVERLAY_SYMBOL_LIMIT // 4

    sampled.clear()
    scene_module.build_attribute_overlay(
        project, show_supports=False, show_loads=False
    )
    assert len(sampled) == OVERLAY_SYMBOL_LIMIT // 4

    sampled.clear()
    scene_module.build_attribute_overlay(
        project, show_supports=False, show_masses=False
    )
    assert sum(ref.kind == "face" for ref in sampled) == 2 * (
        OVERLAY_SYMBOL_LIMIT // 4
    )
    assert sum(ref.kind == "edge" for ref in sampled) == (
        OVERLAY_SYMBOL_LIMIT // 3
    )


def test_support_dofs_moments_and_gravity_have_directional_symbols(plate_project):
    from anyfem.model.attributes import Support
    from anyfem.ui.scene import build_attribute_overlay

    project, _face, edges, points = plate_project
    project.add_support(
        Support(
            "directional",
            project.edge(edges[0]),
            {"ux": 0.0, "uy": 0.01, "rz": 0.0},
        )
    )
    case = project.load_case()
    case.add_point_load(
        project.point(points[0]),
        force=(0.0, 0.0, 0.0),
        moment=(0.0, 0.0, 500.0),
    )
    case.set_gravity()

    overlay = build_attribute_overlay(project)

    assert any(line.color == COLOR_SUPPORT for line in overlay.lines)
    assert any(line.color == COLOR_ROTATION for line in overlay.lines)
    assert any(arrow.color == COLOR_LOAD for arrow in overlay.arrows)
    moment = next(arrow for arrow in overlay.arrows if arrow.color == COLOR_MOMENT)
    gravity = next(arrow for arrow in overlay.arrows if arrow.color == COLOR_MASS)
    moment_vector = moment.end - moment.start
    assert moment_vector[:2] == pytest.approx([0.0, 0.0])
    assert moment_vector[2] > 0.0
    assert (gravity.end - gravity.start)[2] < 0.0
