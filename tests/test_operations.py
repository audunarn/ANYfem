"""Decomposition: splitting, revolving, and the escape hatches.

These are what make the mapped-only decision workable, so the properties that
matter are checked directly: the model only ever holds mappable faces, cuts
stay on the surface, and neighbouring faces stay conformal.
"""

from __future__ import annotations

import numpy as np
import pytest

from anyfem.geometry import GeometryModel
from anyfem.geometry.curves import Arc, Straight
from anyfem.geometry.model import GeometryError
from anyfem.geometry.operations import (
    check_mappable,
    punch_circular_hole,
    split_face_at,
    split_face_between,
    strip_face,
    surface_point,
    triangle_to_quads,
)
from anyfem.mesh import generate_mesh


def rectangle(model: GeometryModel, width: float = 4.0, height: float = 2.0):
    points = model.add_points(
        [(0, 0, 0), (width, 0, 0), (width, height, 0), (0, height, 0)]
    )
    edges = model.add_polyline(points, close=True)
    return points, edges, model.add_face(edges)


def quarter_cylinder(model: GeometryModel, radius: float = 2.0, height: float = 3.0):
    start = model.add_point(radius, 0.0, 0.0)
    via = model.add_point(radius / np.sqrt(2), radius / np.sqrt(2), 0.0)
    end = model.add_point(0.0, radius, 0.0)
    arc = model.add_arc(start, via, end)
    return arc, model.extrude([arc], (0.0, 0.0, height))[0]


def degenerate_quads(mesh) -> int:
    count = 0
    for nodes in mesh.quads.values():
        corners = np.array([mesh.nodes[node] for node in nodes])
        area = 0.5 * (
            np.linalg.norm(np.cross(corners[1] - corners[0], corners[2] - corners[0]))
            + np.linalg.norm(np.cross(corners[2] - corners[0], corners[3] - corners[0]))
        )
        if len(set(nodes)) != 4 or area <= 1.0e-12:
            count += 1
    return count


# ----------------------------------------------------------------------
# splitting an edge
# ----------------------------------------------------------------------
def test_split_edge_rewrites_the_faces_that_used_it():
    model = GeometryModel()
    _points, edges, face = rectangle(model)
    assert len(model.faces[face].loop) == 4

    _vertex, halves = model.split_edge(edges[0], 0.5)
    assert len(model.faces[face].loop) == 5
    # The split side is now a two-edge chain; the others are untouched.
    assert [len(side) for side in model.faces[face].sides()] == [2, 1, 1, 1]
    assert set(halves) <= set(model.edges)
    assert edges[0] not in model.edges


def test_split_edge_preserves_side_lengths():
    model = GeometryModel()
    _points, edges, face = rectangle(model, 4.0, 2.0)
    before = model.face_side_lengths(face)
    model.split_edge(edges[0], 0.3)
    assert model.face_side_lengths(face) == pytest.approx(before)


def test_split_edge_shifts_corners_correctly():
    model = GeometryModel()
    _points, edges, face = rectangle(model)
    # Split the third edge; only the corners after it move along.
    model.split_edge(edges[2], 0.5)
    assert model.faces[face].corners == (0, 1, 2, 4)


def test_splitting_an_arc_gives_two_arcs_on_the_same_circle():
    radius = 2.0
    model = GeometryModel()
    start = model.add_point(radius, 0.0, 0.0)
    via = model.add_point(radius / np.sqrt(2), radius / np.sqrt(2), 0.0)
    end = model.add_point(0.0, radius, 0.0)
    arc = model.add_arc(start, via, end)
    total = model.edge_length(arc)

    _vertex, (first, second) = model.split_edge(arc, 0.5)
    assert isinstance(model.edges[first].curve, Arc)
    assert isinstance(model.edges[second].curve, Arc)
    assert model.edge_length(first) + model.edge_length(second) == pytest.approx(total)
    for half in (first, second):
        points = model.sample_edge(half, np.linspace(0.0, 1.0, 9))
        assert np.linalg.norm(points, axis=1) == pytest.approx(radius)


def test_split_parameter_must_be_interior():
    model = GeometryModel()
    _points, edges, _face = rectangle(model)
    for bad in (0.0, 1.0, -0.5, 2.0):
        with pytest.raises(GeometryError, match="strictly between"):
            model.split_edge(edges[0], bad)


# ----------------------------------------------------------------------
# corners
# ----------------------------------------------------------------------
def test_corners_can_be_overridden_after_creation():
    model = GeometryModel()
    _points, edges, face = rectangle(model)
    model.split_edge(edges[0], 0.5)
    model.set_face_corners(face, (0, 1, 2, 3))
    assert model.faces[face].corners == (0, 1, 2, 3)
    assert [len(side) for side in model.faces[face].sides()] == [1, 1, 1, 2]


def test_bad_corner_override_is_refused():
    model = GeometryModel()
    _points, _edges, face = rectangle(model)
    with pytest.raises(GeometryError, match="distinct"):
        model.set_face_corners(face, (0, 0, 1, 2))
    with pytest.raises(GeometryError, match="outside"):
        model.set_face_corners(face, (0, 1, 2, 9))


def test_mappability_report_points_at_geometry():
    model = GeometryModel()
    _points, _edges, face = rectangle(model)
    assert check_mappable(model, face).ok

    # A sliver: one pair of opposite sides is far shorter than the other.
    points = model.add_points([(0, 5, 0), (10, 5, 0), (10, 5.01, 0), (0, 5.2, 0)])
    sliver = model.add_face(model.add_polyline(points, close=True))
    report = check_mappable(model, sliver)
    assert not report.ok
    assert "splitting" in " ".join(report.messages)


# ----------------------------------------------------------------------
# revolve
# ----------------------------------------------------------------------
def test_full_revolve_closes_back_onto_its_profile():
    model = GeometryModel()
    start = model.add_point(2.0, 0.0, 0.0)
    end = model.add_point(2.0, 0.0, 3.0)
    edge = model.add_line(start, end)

    faces = model.revolve([edge], (0, 0, 0), (0, 0, 1), 2.0 * np.pi)
    assert len(faces) == 4

    mesh = generate_mesh(model, target_size=0.5)
    positions = mesh.node_positions()
    assert np.linalg.norm(positions[:, :2], axis=1) == pytest.approx(2.0)

    # A closed revolve must not leave a seam of coincident-but-separate nodes.
    unique = {tuple(np.round(point, 9)) for point in positions}
    assert len(unique) == mesh.num_nodes


def test_partial_revolve_does_not_close():
    model = GeometryModel()
    start = model.add_point(2.0, 0.0, 0.0)
    end = model.add_point(2.0, 0.0, 3.0)
    edge = model.add_line(start, end)

    faces = model.revolve([edge], (0, 0, 0), (0, 0, 1), np.pi)
    assert len(faces) == 2
    mesh = generate_mesh(model, target_size=0.5)
    angles = np.arctan2(
        mesh.node_positions()[:, 1], mesh.node_positions()[:, 0]
    )
    assert angles.min() == pytest.approx(0.0, abs=1e-9)
    assert angles.max() == pytest.approx(np.pi, abs=1e-9)


def test_revolve_rejects_a_point_on_the_axis():
    model = GeometryModel()
    start = model.add_point(0.0, 0.0, 0.0)
    end = model.add_point(2.0, 0.0, 0.0)
    edge = model.add_line(start, end)

    with pytest.raises(GeometryError, match="lies on the revolve axis"):
        model.revolve([edge], (0, 0, 0), (0, 0, 1), np.pi / 2)


def test_revolve_rejects_a_degenerate_axis():
    model = GeometryModel()
    start = model.add_point(2.0, 0.0, 0.0)
    end = model.add_point(2.0, 0.0, 1.0)
    edge = model.add_line(start, end)

    with pytest.raises(GeometryError, match="non-zero"):
        model.revolve([edge], (0, 0, 0), (0, 0, 0), np.pi)
    with pytest.raises(GeometryError, match="angle must be non-zero"):
        model.revolve([edge], (0, 0, 0), (0, 0, 1), 0.0)


# ----------------------------------------------------------------------
# splitting a face
# ----------------------------------------------------------------------
def test_planar_split_is_a_straight_line():
    model = GeometryModel()
    _points, _edges, face = rectangle(model, 4.0, 1.0)
    edge, (first, second) = split_face_at(model, face, axis=0, fraction=0.25)

    assert isinstance(model.edges[edge].curve, Straight)
    assert model.edge_length(edge) == pytest.approx(1.0)
    assert check_mappable(model, first).ok
    assert check_mappable(model, second).ok
    assert sorted(model.face_side_lengths(second)) == pytest.approx([1.0, 1.0, 1.0, 1.0])


def test_a_hoop_cut_across_a_cylinder_comes_out_as_an_arc():
    """A chord would leave the surface; the cut has to follow it."""

    model = GeometryModel()
    _arc, face = quarter_cylinder(model, radius=2.0, height=3.0)
    edge, _faces = split_face_at(model, face, axis=1, fraction=0.5)

    assert isinstance(model.edges[edge].curve, Arc)
    points = model.sample_edge(edge, np.linspace(0.0, 1.0, 9))
    assert np.linalg.norm(points[:, :2], axis=1) == pytest.approx(2.0)
    assert points[:, 2] == pytest.approx(np.full(9, 1.5))


def test_a_generator_cut_along_a_cylinder_is_straight():
    model = GeometryModel()
    _arc, face = quarter_cylinder(model)
    edge, _faces = split_face_at(model, face, axis=0, fraction=0.5)
    assert isinstance(model.edges[edge].curve, Straight)


def test_split_cylinder_stays_exact_and_conformal():
    model = GeometryModel()
    _arc, face = quarter_cylinder(model, radius=2.0)
    edge, (first, second) = split_face_at(model, face, axis=1, fraction=0.5)

    mesh = generate_mesh(model, target_size=0.4)
    assert np.linalg.norm(mesh.node_positions()[:, :2], axis=1) == pytest.approx(2.0)

    shared = set(mesh.nodes_on(model.entity_ref("edge", edge)))
    left = set(mesh.nodes_on(model.entity_ref("face", first)))
    right = set(mesh.nodes_on(model.entity_ref("face", second)))
    assert left & right == shared


def test_split_fraction_must_be_interior():
    model = GeometryModel()
    _points, _edges, face = rectangle(model)
    with pytest.raises(GeometryError, match="strictly between"):
        split_face_at(model, face, axis=0, fraction=0.0)
    with pytest.raises(GeometryError, match="axis must be"):
        split_face_at(model, face, axis=2, fraction=0.5)


def test_a_cut_between_adjacent_sides_is_refused():
    model = GeometryModel()
    points, _edges, face = rectangle(model)
    with pytest.raises(GeometryError, match="opposite sides"):
        split_face_between(model, face, points[0], points[1])


def test_surface_point_reproduces_the_boundary():
    model = GeometryModel()
    _arc, face_id = quarter_cylinder(model, radius=2.0, height=3.0)
    face = model.faces[face_id]
    for u in (0.0, 0.25, 0.5, 1.0):
        for v in (0.0, 0.5, 1.0):
            point = surface_point(model, face, u, v)
            assert float(np.linalg.norm(point[:2])) == pytest.approx(2.0)


# ----------------------------------------------------------------------
# escape hatches
# ----------------------------------------------------------------------
def test_triangle_becomes_three_mappable_quads():
    model = GeometryModel()
    points = model.add_points([(0, 0, 0), (2, 0, 0), (1, 1.6, 0)])
    edges = model.add_polyline(points, close=True)

    faces = triangle_to_quads(model, edges)
    assert len(faces) == 3
    assert all(check_mappable(model, face).ok for face in faces)

    mesh = generate_mesh(model, target_size=0.25)
    assert degenerate_quads(mesh) == 0
    assert len(mesh.quads) > 0


def test_triangle_needs_exactly_three_edges():
    model = GeometryModel()
    _points, edges, _face = rectangle(model)
    with pytest.raises(GeometryError, match="exactly three edges"):
        triangle_to_quads(model, edges)


def test_triangle_refuses_edges_already_bounding_a_face():
    """A triangle whose sides are shared with an existing plate is refused."""

    model = GeometryModel()
    points = model.add_points([(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)])
    edges = model.add_polyline(points, close=True)
    model.add_face(edges)
    diagonal = model.add_line(points[2], points[0])

    # edges[0], edges[1] and the diagonal do close a triangle, but the first
    # two already bound the plate.
    with pytest.raises(GeometryError, match="already bounds a face"):
        triangle_to_quads(model, [edges[0], edges[1], diagonal])


def test_butterfly_hole_gives_four_mappable_patches():
    model = GeometryModel()
    _points, _edges, face = rectangle(model, 4.0, 3.0)
    patches, arcs = punch_circular_hole(model, face, (2.0, 1.5, 0.0), 0.6)

    assert len(patches) == 4
    assert len(arcs) == 4
    assert all(check_mappable(model, patch).ok for patch in patches)
    assert face not in model.faces


def test_hole_edge_nodes_lie_on_the_circle():
    centre = np.array([2.0, 1.5])
    radius = 0.6
    model = GeometryModel()
    _points, _edges, face = rectangle(model, 4.0, 3.0)
    _patches, arcs = punch_circular_hole(model, face, (*centre, 0.0), radius)

    mesh = generate_mesh(model, target_size=0.3)
    assert degenerate_quads(mesh) == 0

    ring = np.vstack(
        [
            [mesh.nodes[node] for node in mesh.nodes_on(model.entity_ref("edge", arc))]
            for arc in arcs
        ]
    )
    assert np.linalg.norm(ring[:, :2] - centre, axis=1) == pytest.approx(radius)


def test_hole_must_fit_inside_the_plate():
    model = GeometryModel()
    _points, _edges, face = rectangle(model, 4.0, 3.0)
    with pytest.raises(GeometryError, match="does not fit"):
        punch_circular_hole(model, face, (2.0, 1.5, 0.0), 5.0)
    with pytest.raises(GeometryError, match="radius must be positive"):
        punch_circular_hole(model, face, (2.0, 1.5, 0.0), 0.0)


def test_hole_in_a_curved_plate_is_refused():
    model = GeometryModel()
    _arc, face = quarter_cylinder(model)
    with pytest.raises(GeometryError, match="not planar"):
        punch_circular_hole(model, face, (1.4, 1.4, 1.5), 0.2)


# ----------------------------------------------------------------------
# strips
# ----------------------------------------------------------------------
def test_strips_are_equal_and_share_their_dividers():
    model = GeometryModel()
    _points, _edges, face = rectangle(model, 6.0, 2.0)
    strips, dividers = strip_face(model, face, axis=0, count=3)

    assert len(strips) == 3
    assert len(dividers) == 2
    for strip in strips:
        assert sorted(model.face_side_lengths(strip)) == pytest.approx(
            [2.0, 2.0, 2.0, 2.0]
        )
    for divider in dividers:
        assert len(model.faces_using_edge(divider)) == 2


def test_stiffeners_on_dividers_share_the_plating_nodes():
    model = GeometryModel()
    _points, _edges, face = rectangle(model, 6.0, 2.0)
    strips, dividers = strip_face(model, face, axis=0, count=3)

    mesh = generate_mesh(model, target_size=0.5, beam_edges=dividers)
    beam_nodes = {node for pair in mesh.beams.values() for node in pair}
    plate_nodes = set()
    for strip in strips:
        plate_nodes |= set(mesh.nodes_on(model.entity_ref("face", strip)))

    assert beam_nodes
    # Coupling comes from sharing nodes, not from any added constraint.
    assert beam_nodes <= plate_nodes


def test_strip_count_must_be_at_least_two():
    model = GeometryModel()
    _points, _edges, face = rectangle(model)
    with pytest.raises(GeometryError, match="at least 2"):
        strip_face(model, face, axis=0, count=1)


def test_stripping_a_cylinder_keeps_it_exact():
    model = GeometryModel()
    _arc, face = quarter_cylinder(model, radius=2.0, height=4.0)
    strips, _dividers = strip_face(model, face, axis=1, count=4)

    assert len(strips) == 4
    mesh = generate_mesh(model, target_size=0.4)
    assert np.linalg.norm(mesh.node_positions()[:, :2], axis=1) == pytest.approx(2.0)
