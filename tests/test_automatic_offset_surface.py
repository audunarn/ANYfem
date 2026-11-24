"""Automatic stiffener offset: the profile foot stands on the plate surface.

Owner decision 2026-11-24: with ``offset_mode="automatic"`` the stiffener foot
sits on the attached plate's surface, not on its mid-surface.  The neutral-axis
offset is therefore the profile centroid above its foot plus half the plate
thickness along the plate normal on the stiffener's side.  Manual and
centerline offsets are unchanged.

The end-to-end check compares a simply supported T-bar stiffened strip with
transformed-section (composite beam) theory, as in the independent ANYfeSolver
spot check of 2026-11-24.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from anyfem import Project, pinned, solve_linear_static, steel, support
from anyfem.geometry.operations import strip_face
from anyfem.model import BeamSection

E, NU = 210.0e9, 0.3
G = E / (2.0 * (1.0 + NU))
L, W, T = 5.0, 0.4, 0.010
HW, TW, BF, TF = 0.25, 0.008, 0.10, 0.012


def _tbar(**kwargs) -> BeamSection:
    values = dict(
        name="bar", profile="T-bar", material="S355", web_height=HW,
        web_thickness=TW, flange_width=BF, flange_thickness=TF,
        offset_mode="automatic",
    )
    values.update(kwargs)
    return BeamSection(**values)


def _strip(section: BeamSection, *, plate_section: bool = True, loads: bool = False):
    project = Project(name="strip")
    project.add_material(steel("S355", T))
    project.add_plate_section("plate", thickness=T, material="S355")
    project.add_beam_section(section)
    g = project.geometry
    pts = g.add_points([(0, 0, 0), (L, 0, 0), (L, W, 0), (0, W, 0)])
    face = g.add_face(g.add_polyline(pts, close=True))
    if plate_section:
        project.assign_plate(face, "plate")
    strips, dividers = strip_face(g, face, axis=1, count=2)
    if plate_section:
        project.assign_plates(strips, "plate")
    project.assign_beam(dividers[0], "bar")
    if loads:
        for eid in list(g.edges):
            edge = g.edges[eid]
            xs = [g.vertices[v].position[0] for v in (edge.start, edge.end)]
            if max(abs(x) for x in xs) < 1e-9:
                project.add_support(pinned(project.edge(eid)))
            elif max(abs(x - L) for x in xs) < 1e-9:
                project.add_support(support(project.edge(eid), uy=0.0, uz=0.0))
        for s in strips:
            project.load_case().add_pressure(project.face(s), 1000.0)
    return project, dividers[0]


def _profile_centroid() -> float:
    """T-bar centroid above its foot, by hand."""

    rects = [(TW, HW, 0.0), (BF, TF, HW)]
    area = sum(b * h for b, h, _ in rects)
    return sum(b * h * (z0 + h / 2.0) for b, h, z0 in rects) / area


@pytest.mark.parametrize("web_direction", [(0.0, 0.0, 1.0), None])
def test_automatic_offset_is_centroid_plus_half_plate_thickness(web_direction):
    project, edge = _strip(_tbar(web_direction=web_direction))
    offset = np.asarray(project.beam_offset_vector(edge))
    expected = _profile_centroid() + T / 2.0
    assert offset[2] == pytest.approx(expected, rel=1e-12, abs=1e-15)
    assert abs(offset[0]) < 1e-15 and abs(offset[1]) < 1e-15
    assert project.beam_offsets[edge] == pytest.approx((0.0, 0.0, expected))


def test_back_side_offset_goes_through_the_other_surface():
    project, edge = _strip(_tbar(attachment_side="back"))
    offset = np.asarray(project.beam_offset_vector(edge))
    assert offset[2] == pytest.approx(-(_profile_centroid() + T / 2.0), rel=1e-12)


def test_rotated_section_adds_half_thickness_along_the_plate_normal():
    angle = 30.0
    project, edge = _strip(_tbar(rotation_deg=angle))
    _axis, local_y, local_z = project.beam_section_frame(edge)
    y, z = project.beam_section_of(edge).centroid_from_attachment()
    offset = np.asarray(project.beam_offset_vector(edge))
    expected = y * local_y + z * local_z + np.array([0.0, 0.0, T / 2.0])
    np.testing.assert_allclose(offset, expected, rtol=0.0, atol=1e-15)
    # The web is rotated, the half thickness is not.
    assert local_z[2] == pytest.approx(math.cos(math.radians(angle)))


def test_plate_without_section_and_free_beam_add_nothing():
    project, edge = _strip(_tbar(), plate_section=False)
    assert project.beam_offset_vector(edge)[2] == pytest.approx(
        _profile_centroid(), rel=1e-12
    )

    free = Project(name="free")
    free.add_material(steel("S355", T))
    free.add_beam_section(_tbar(web_direction=(0.0, 0.0, 1.0)))
    g = free.geometry
    a, b = g.add_points([(0, 0, 0), (L, 0, 0)])
    line = g.add_line(a, b)
    free.assign_beam(line, "bar")
    assert free.beam_offset_vector(line)[2] == pytest.approx(
        _profile_centroid(), rel=1e-12
    )


@pytest.mark.parametrize(
    "mode, eccentricity, expected",
    [("manual", 0.123, (0.0, 0.0, 0.123)), ("centerline", 0.123, (0.0, 0.0, 0.0))],
)
def test_manual_and_centerline_offsets_are_unchanged(mode, eccentricity, expected):
    project, edge = _strip(
        _tbar(offset_mode=mode, eccentricity=eccentricity, web_direction=(0.0, 0.0, 1.0))
    )
    assert project.beam_offset_vector(edge) == expected


def test_tbar_strip_deflection_matches_foot_on_surface_theory():
    section = _tbar(web_direction=(0.0, 0.0, 1.0))
    project, edge = _strip(section, loads=True)
    mesh = project.generate_mesh(0.1)
    solution = solve_linear_static(project, mesh=mesh)

    rects = [(TW, HW, 0.0), (BF, TF, HW)]
    area = sum(b * h for b, h, _ in rects)
    zc = _profile_centroid()
    ib = sum(b * h ** 3 / 12 + b * h * (z0 + h / 2 - zc) ** 2 for b, h, z0 in rects)
    plate_area = W * T
    e = T / 2.0 + zc
    na = area * e / (plate_area + area)
    inertia = W * T ** 3 / 12 + plate_area * na ** 2 + ib + area * (e - na) ** 2
    q = 1000.0 * W
    props = section.properties()
    kga = props["shear_factor_z"] * props["area"] * G
    reference = 5 * q * L ** 4 / (384 * E * inertia) + q * L ** 2 / (8 * kga)

    nodes = mesh.nodes_of_edge[edge]
    xs = np.array([mesh.nodes[n][0] for n in nodes])
    middle = nodes[int(np.argmin(np.abs(xs - L / 2)))]
    deflection = abs(solution.node_displacement(middle)[2])
    assert deflection == pytest.approx(reference, rel=0.01)


def test_declared_attachments_give_the_same_plate_as_the_surface_search():
    """The barge's stiffeners are members declared on their plates.

    The fast declared route must pick the plate the full surface search picks,
    and every automatic offset must be the centroid plus half that plate's
    thickness along its normal on the stiffener side.
    """

    from anyfem.application.examples import floating_barge_section

    project = floating_barge_section()
    offsets = project.beam_offsets
    for edge_id in sorted(project.edge_sections):
        section = project.beam_section_of(edge_id)
        assert section.offset_mode == "automatic"
        g = project.geometry
        points = np.asarray(g.sample_edge(edge_id, np.asarray((0.0, 0.5, 1.0))))
        tolerance = g.tolerance.effective_surface_residual(
            float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
        )
        # Brute force: every face, all three points within tolerance.
        searched = [
            f for f in sorted(g.faces)
            if all(g.project_to_face(f, q)[2] <= tolerance for q in points)
        ]
        assert project._beam_plate_face(edge_id) in searched, edge_id
        face = project._beam_plate_face(edge_id)
        axis, local_y, local_z = project.beam_section_frame(edge_id)
        y, z = section.centroid_from_attachment()
        thickness = project.plate_section_of(face).thickness
        normal = np.asarray(project.geometry.face_normal(face, 0.5, 0.5), dtype=float)
        normal -= float(normal @ axis) * axis
        normal /= np.linalg.norm(normal)
        if normal @ local_z < 0.0:
            normal = -normal
        expected = y * local_y + z * local_z + 0.5 * thickness * normal
        np.testing.assert_allclose(offsets[edge_id], expected, rtol=0.0, atol=1e-15)
        assert project.beam_offset_vector(edge_id) == offsets[edge_id]


# ----------------------------------------------------------------------
# Review round 1 (Codex, 2026-11-24): which plate, which side, which normal
# ----------------------------------------------------------------------

def _flatbar(**kwargs) -> BeamSection:
    values = dict(name="fb", profile="Flatbar", material="S355", flange_width=0.01,
                  flange_thickness=0.1, offset_mode="automatic")
    values.update(kwargs)
    return BeamSection(**values)


def _corner_project():
    """A free line along the corner of a 10 mm deck (face 1) and a 40 mm wall (face 2)."""

    project = Project(name="corner")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("t10", thickness=0.010, material="S355")
    project.add_plate_section("t40", thickness=0.040, material="S355")
    g = project.geometry
    deck = g.add_face(g.add_polyline(g.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]), close=True))
    wall = g.add_face(g.add_polyline(g.add_points([(0, 0, 0), (1, 0, 0), (1, 0, -1), (0, 0, -1)]), close=True))
    a, b = g.add_points([(0, 0, 0), (1, 0, 0)])
    line = g.add_line(a, b)
    assert not g.faces_using_edge(line)
    project.assign_plate(deck, "t10")
    project.assign_plate(wall, "t40")
    project.add_beam_section(_flatbar(web_direction=(0.0, 0.0, 1.0)))
    project.assign_beam(line, "fb")
    return project, line, deck, wall


def test_only_an_exact_connect_whole_member_declaration_names_the_plate():
    """Owner contract 2026-11-24: topology or a declaration names the plate.

    A free, undeclared line gets no half thickness; unverified, disconnected
    or partial declarations do not count; several valid ones take the lowest
    face ID."""

    from anygeometry.structural import (
        AttachmentEvidence, AttachmentKind, ConnectionIntent, ParameterRange,
    )

    project, line, deck, wall = _corner_project()
    bare = (0.0, 0.0, 0.05)
    assert project._beam_plate_face(line) is None
    assert project.beam_offset_vector(line) == pytest.approx(bare, abs=1e-15)
    g = project.geometry
    member = g.add_member((line,))

    def declare(face, evidence=AttachmentEvidence.EXACT,
                intent=ConnectionIntent.CONNECT, span=(0.0, 1.0)):
        g.add_attachment(
            member, AttachmentKind.MEMBER_ON_FACE, "face", face,
            ParameterRange(*span), (ParameterRange(0.0, 1.0), ParameterRange(0.0, 0.0)),
            evidence=evidence, connection_intent=intent,
        )

    declare(wall, evidence=AttachmentEvidence.UNVERIFIED)
    declare(wall, intent=ConnectionIntent.KEEP_DISCONNECTED)
    declare(wall, span=(0.0, 0.5))
    assert project._beam_plate_face(line) is None
    assert project.beam_offset_vector(line) == pytest.approx(bare, abs=1e-15)
    declare(wall)
    assert project._beam_plate_face(line) == wall
    # Web (0, 0, 1) lies in the wall's plane: Front takes the wall's +normal.
    normal = np.asarray(g.face_normal(wall, 0.5, 0.5))
    np.testing.assert_allclose(
        project.beam_offset_vector(line), np.array(bare) + 0.02 * normal, rtol=0.0, atol=1e-15
    )
    declare(deck)
    assert project._beam_plate_face(line) == deck == min(deck, wall)
    assert project.beam_offset_vector(line) == pytest.approx((0.0, 0.0, 0.055), abs=1e-15)


def test_line_that_only_pierces_a_plate_gets_no_half_thickness():
    project = Project(name="pierce")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("t10", thickness=0.010, material="S355")
    g = project.geometry
    face = g.add_face(g.add_polyline(g.add_points([(-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)]), close=True))
    project.assign_plate(face, "t10")
    a, b = g.add_points([(-1, 0, -1), (1, 0, 1)])
    line = g.add_line(a, b)
    project.add_beam_section(_flatbar(web_direction=(-1.0, 0.0, 1.0)))
    project.assign_beam(line, "fb")
    _axis, local_y, local_z = project.beam_section_frame(line)
    y, z = project.beam_section_of(line).centroid_from_attachment()
    assert project._beam_plate_face(line) is None
    np.testing.assert_array_equal(
        np.asarray(project.beam_offset_vector(line)), y * local_y + z * local_z
    )


@pytest.mark.parametrize("side, sign", [("front", 1.0), ("back", -1.0)])
def test_web_in_the_plate_plane_takes_front_or_back(side, sign):
    project, edge = _strip(_tbar(web_direction=(0.0, 1.0, 0.0), attachment_side=side))
    _axis, local_y, local_z = project.beam_section_frame(edge)
    y, z = project.beam_section_of(edge).centroid_from_attachment()
    offset = np.asarray(project.beam_offset_vector(edge))
    expected = y * local_y + z * local_z + np.array([0.0, 0.0, sign * T / 2.0])
    np.testing.assert_allclose(offset, expected, rtol=0.0, atol=1e-15)


def test_curved_shell_half_thickness_is_radial_at_the_stiffener():
    from anyfem.commands import AddCylinder
    from anyfem.document import DocumentSession

    session = DocumentSession(Project("cylinder"))
    session.execute(AddCylinder(1.0, 2.0, circumferential_segments=8,
                                longitudinal_spacing=1.0, ring_spacing=2.0))
    project = session.project
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("shell", thickness=0.010, material="S355")
    project.assign_plates(sorted(project.geometry.faces), "shell")
    project.add_beam_section(_flatbar())
    g = project.geometry
    axial = []
    for edge in sorted(g.edges):
        ends = np.asarray(g.sample_edge(edge, np.asarray((0.0, 1.0))))
        if abs(ends[0][2] - ends[1][2]) > 0.5:
            project.assign_beam(edge, "fb")
            axial.append((edge, ends[0]))
    assert len(axial) == 8
    offsets = project.beam_offsets
    for edge, start in axial:
        _axis, local_y, local_z = project.beam_section_frame(edge)
        y, z = project.beam_section_of(edge).centroid_from_attachment()
        extra = np.asarray(offsets[edge]) - (y * local_y + z * local_z)
        radial = np.array([start[0], start[1], 0.0]) / np.hypot(start[0], start[1])
        np.testing.assert_allclose(extra, 0.005 * radial, rtol=0.0, atol=1e-12)


def test_free_undeclared_lines_get_nothing_and_never_project(monkeypatch):
    project = Project(name="row")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("t10", thickness=0.010, material="S355")
    g = project.geometry
    faces = []
    for i in range(30):
        x = 1.5 * i
        faces.append(g.add_face(g.add_polyline(
            g.add_points([(x, 0, 0), (x + 1, 0, 0), (x + 1, 1, 0), (x, 1, 0)]), close=True)))
    project.assign_plates(faces, "t10")
    project.add_beam_section(_flatbar(web_direction=(0.0, 0.0, 1.0)))
    lines = []
    for i in (3, 17, 29):
        a, b = g.add_points([(1.5 * i + 0.2, 0.5, 0), (1.5 * i + 0.8, 0.5, 0)])
        lines.append(g.add_line(a, b))
        project.assign_beam(lines[-1], "fb")
    calls = []
    original = type(g).project_to_face

    def counting(self, face_id, point):
        calls.append(face_id)
        return original(self, face_id, point)

    monkeypatch.setattr(type(g), "project_to_face", counting)
    offsets = project.beam_offsets
    assert calls == []
    for line in lines:
        assert offsets[line] == pytest.approx((0.0, 0.0, 0.05), abs=1e-15)


def test_nurbs_plate_edge_uses_its_chart_normal():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from test_nurbs_preparation_authority import _crossing_patch

    model, patch, _plate = _crossing_patch()
    project = Project(name="nurbs", geometry=model)
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("t10", thickness=0.010, material="S355")
    project.assign_plate(patch, "t10")
    project.add_beam_section(_flatbar())
    offsets_checked = 0
    for use in model.faces[patch].loop:
        edge = use.edge
        project.assign_beam(edge, "fb")
        vector = np.asarray(project.beam_offset_vector(edge))
        assert project.beam_offsets[edge] == pytest.approx(tuple(vector), abs=0.0)
        axis, local_y, local_z = project.beam_section_frame(edge)
        y, z = project.beam_section_of(edge).centroid_from_attachment()
        extra = vector - (y * local_y + z * local_z)
        uv = model.edges[edge].curve.chart(np.asarray([0.5]))[0]
        normal = model.face_normal_many(patch, [uv])[0]
        normal = normal if normal @ local_z >= 0.0 else -normal
        np.testing.assert_allclose(extra, 0.005 * normal, rtol=0.0, atol=1e-15)
        offsets_checked += 1
    assert offsets_checked == 4


def test_planar_plate_far_from_origin_keeps_its_normal():
    project = Project(name="far")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("t125", thickness=0.125, material="S355")
    g = project.geometry
    x = 1.0e12
    from anygeometry.surfaces import Plane

    pts = g.add_points([(x, 0, 0), (x + 1, 0, 0), (x + 1, 1, 0), (x, 1, 0)])
    edges = g.add_polyline(pts, close=True)
    face = g.add_face(edges, surface=Plane(
        np.array([x, 0.0, 0.0]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])))
    assert type(g.faces[face].surface).__name__ == "Plane"
    project.assign_plate(face, "t125")
    project.add_beam_section(_flatbar(web_direction=(0.0, 0.0, 1.0)))
    project.assign_beam(edges[0], "fb")
    assert project.beam_offset_vector(edges[0]) == pytest.approx(
        (0.0, 0.0, 0.05 + 0.0625), abs=1e-15
    )


def test_half_thickness_uses_the_plate_normal_not_the_chord_normal():
    """An extruded asymmetric spline: the normal at the line's middle is
    not perpendicular to the chord; it must not be projected onto it."""

    project = Project(name="spline")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("t10", thickness=0.010, material="S355")
    g = project.geometry
    start, c1, c2, end = g.add_points([(0, 0, 0), (0.2, 0.6, 0), (0.3, 0.1, 0), (1, 0, 0)])
    spline = g.add_spline(start, (c1, c2), end)
    (face,) = g.extrude([spline], (0.0, 0.0, 1.0))
    project.assign_plate(face, "t10")
    project.add_beam_section(_flatbar())
    project.assign_beam(spline, "fb")
    axis, local_y, local_z = project.beam_section_frame(spline)
    y, z = project.beam_section_of(spline).centroid_from_attachment()
    extra = np.asarray(project.beam_offset_vector(spline)) - (y * local_y + z * local_z)
    middle = np.asarray(g.sample_edge(spline, np.asarray((0.5,))))[0]
    normal = g.face_normal_many(face, [g.face_local_uv(face, middle)])[0]
    normal = normal if normal @ local_z >= 0.0 else -normal
    assert abs(float(normal @ axis)) > 1e-3  # the case is really off-chord
    np.testing.assert_allclose(extra, 0.005 * normal, rtol=0.0, atol=1e-15)


def _nurbs_patch(knots=(0.0, 0.0, 1.0, 1.0)):
    from anygeometry import GeometryModel
    from anygeometry.nurbs import CurveOnSurface, NurbsCurve, NurbsSurface

    model = GeometryModel()
    support = NurbsSurface(1, 1, (0., 0., 1., 1.), (0., 0., 1., 1.),
                           (((0., 0., 0.), (0., 1., 0.)), ((1., 0., 0.), (1., 1., .4))))
    corners = ((0., 0.), (1., 0.), (1., 1.), (0., 1.))
    curves = tuple(CurveOnSurface(support, NurbsCurve(1, knots, (corners[i], corners[(i + 1) % 4])))
                   for i in range(4))
    vertices = tuple(model.add_point(*curve.exact_point(0)) for curve in curves)
    edges = tuple(model.add_curve(vertices[i], vertices[(i + 1) % 4], curve)
                  for i, curve in enumerate(curves))
    return model, model.add_face(edges, surface=support), edges, support


def _nurbs_project(model, patch):
    project = Project(name="nurbs", geometry=model)
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("t10", thickness=0.010, material="S355")
    project.assign_plate(patch, "t10")
    project.add_beam_section(_flatbar())
    return project


def _extra(project, edge):
    _axis, local_y, local_z = project.beam_section_frame(edge)
    y, z = project.beam_section_of(edge).centroid_from_attachment()
    return np.asarray(project.beam_offset_vector(edge)) - (y * local_y + z * local_z), local_z


@pytest.mark.parametrize("knots", [(0.0, 0.0, 1.0, 1.0), (2.0, 2.0, 4.0, 4.0), (0.0, 0.0, 0.5, 0.5)])
def test_nurbs_edge_normal_is_at_the_middle_of_its_active_domain(knots):
    model, patch, edges, _support = _nurbs_patch(knots)
    project = _nurbs_project(model, patch)
    for edge in edges:
        project.assign_beam(edge, "fb")
        extra, local_z = _extra(project, edge)
        uv = model.edges[edge].curve.chart(np.asarray([0.5]))[0]
        normal = model.face_normal_many(patch, [uv])[0]
        normal = normal if normal @ local_z >= 0.0 else -normal
        np.testing.assert_allclose(extra, 0.005 * normal, rtol=0.0, atol=1e-15)


def test_nurbs_offsets_survive_save_and_reload():
    import anygeometry

    model, patch, edges, _support = _nurbs_patch()
    project = _nurbs_project(model, patch)
    for edge in edges:
        project.assign_beam(edge, "fb")
    before = project.beam_offsets
    reloaded = anygeometry.from_dict(anygeometry.to_dict(model))
    again = _nurbs_project(reloaded, patch)
    for edge in edges:
        again.assign_beam(edge, "fb")
    after = again.beam_offsets
    assert set(after) == set(before)
    for edge in edges:
        np.testing.assert_array_equal(np.asarray(after[edge]), np.asarray(before[edge]))


@pytest.mark.parametrize("web_direction", [(0.0, 0.0, 1.0), None])
def test_declared_interior_line_on_a_nurbs_plate_refuses(web_direction):
    from anygeometry.nurbs import CurveOnSurface, NurbsCurve
    from anygeometry.structural import (
        AttachmentEvidence, AttachmentKind, ConnectionIntent, ParameterRange,
    )
    from anyfem.model.project import ProjectError

    model, patch, _edges, support = _nurbs_patch()
    project = _nurbs_project(model, patch)
    inner = CurveOnSurface(support, NurbsCurve(1, (0., 0., 1., 1.), ((0.2, 0.5), (0.8, 0.5))))
    a = model.add_point(*inner.exact_point(0))
    b = model.add_point(*inner.exact_point(1))
    line = model.add_curve(a, b, inner)
    assert patch not in model.faces_using_edge(line)
    member = model.add_member((line,))
    model.add_attachment(
        member, AttachmentKind.MEMBER_ON_FACE, "face", patch,
        ParameterRange(0.0, 1.0), (ParameterRange(0.2, 0.8), ParameterRange(0.5, 0.5)),
        evidence=AttachmentEvidence.EXACT, connection_intent=ConnectionIntent.CONNECT,
    )
    project.add_beam_section(_flatbar(name="inner", web_direction=web_direction))
    project.assign_beam(line, "inner")
    with pytest.raises(ProjectError, match="manual or centerline"):
        project.beam_offset_vector(line)
    with pytest.raises(ProjectError, match="manual or centerline"):
        project.beam_offsets


def _mesh_state(project, record):
    from types import SimpleNamespace

    from anyfem.application.workflow import WorkbenchWorkflow
    from anyfem.document import DocumentSession

    app = SimpleNamespace(project=project, session=DocumentSession(project))
    return WorkbenchWorkflow.mesh_record_state(app, record)


def _saved_records(project, kind="generated"):
    """A mesh record as written now, and as read from a file saved before the rule tag."""

    from anyfem.document import DocumentSession
    from anyfem.model.records import MeshRecord

    current = MeshRecord(name="Mesh", kind=kind, mesh_input_hash="", mesh_hash="m",
                         source_model_hash=DocumentSession(project).revision.model_hash)
    saved = current.to_dict()
    assert saved["beam_offset_rule"] == 2
    assert MeshRecord.from_dict(saved).beam_offset_rule == 2
    legacy = MeshRecord.from_dict({k: v for k, v in saved.items() if k != "beam_offset_rule"})
    assert legacy.beam_offset_rule == 1
    return current, legacy


def test_mesh_saved_under_the_mid_surface_rule_is_stale_on_reopen():
    # A mesh carries the offsets it was generated with and the model hash does not
    # change with the rule, so a pre-rule mesh must not be accepted as current.
    project, _edge = _strip(_tbar())
    current, legacy = _saved_records(project)
    assert _mesh_state(project, current) == "completed"
    assert _mesh_state(project, legacy) == "stale"


@pytest.mark.parametrize("case", ["manual", "centerline", "no plate section", "imported"])
def test_pre_rule_mesh_stays_current_where_the_rule_moves_nothing(case):
    if case == "no plate section":
        project, _edge = _strip(_tbar(), plate_section=False)
    elif case == "imported":
        project, _edge = _strip(_tbar())
    else:
        project, _edge = _strip(_tbar(offset_mode=case, eccentricity=0.123))
    _current, legacy = _saved_records(project, kind="imported" if case == "imported" else "generated")
    assert _mesh_state(project, legacy) == "completed"


@pytest.mark.parametrize("rule", [1, 2])
def test_pre_rule_parent_mesh_is_not_reused_for_an_unchanged_update(rule, monkeypatch):
    from types import SimpleNamespace

    import anyfem.regional_dispatch as regional
    from anyfem.application import mesh_publication

    project, _edge = _strip(_tbar())
    current, legacy = _saved_records(project)
    record = legacy if rule == 1 else current
    record.summary["regional_settings_hash"] = "h"
    project.add_mesh_record(record)
    monkeypatch.setattr(mesh_publication, "check_ticket", lambda *args: None)
    monkeypatch.setattr(mesh_publication, "verify_published_content", lambda *args: None)
    monkeypatch.setattr(mesh_publication, "retained_mesh", lambda *args: None)
    monkeypatch.setattr(regional, "plan_regional_update", lambda *args: SimpleNamespace(is_noop=True))
    ticket = SimpleNamespace(parent_id=record.id, model_hash=record.source_model_hash)
    reused = mesh_publication.unchanged_record(SimpleNamespace(project=project), ticket,
                                               SimpleNamespace(input_hash="h"), None)
    assert (reused is record) is (rule == 2)


def _saved_session(tmp_path, *, strip_rule):
    """Mesh, save and reopen a stiffened strip; optionally as a file from before the rule tag."""

    import json

    from anyfem.io.project_file import load_project
    from anyfem.mesh_session import ProjectMeshSession
    from anyfem.mesh_jobs import MeshSettings

    project, _edge = _strip(_tbar(), loads=True)
    session = ProjectMeshSession(project)
    session.generate_mesh(MeshSettings.create(.2, element_order="linear", strategy="mapped"))
    path = session.save(tmp_path / "strip.anyfem")
    session.close()
    if strip_rule:
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data["mesh_records"]
        assert records and all(entry.pop("beam_offset_rule") == 2 for entry in records)
        path.write_text(json.dumps(data), encoding="utf-8")
    return ProjectMeshSession(load_project(path), path)


@pytest.mark.parametrize("strip_rule", [False, True])
def test_headless_solve_refuses_a_mesh_saved_under_the_mid_surface_rule(tmp_path, strip_rule):
    from anyfem.model.project import ProjectError
    from anyfem.solve.build import build_fe_model

    restored = _saved_session(tmp_path, strip_rule=strip_rule)
    try:
        mesh = restored.mesh
        if strip_rule:
            with pytest.raises(ProjectError, match="regenerate the mesh"):
                build_fe_model(restored.project, mesh)
        else:
            build_fe_model(restored.project, mesh)
    finally:
        restored.close()



def test_a_new_record_of_a_saved_mesh_keeps_its_offset_rule():
    from types import SimpleNamespace

    from anyfem.application.mesh_publication import inherited_offset_rule

    project, _edge = _strip(_tbar())
    current, legacy = _saved_records(project)
    legacy.id = "legacy"
    for record in (current, legacy):
        project.add_mesh_record(record)
    assert inherited_offset_rule(project, SimpleNamespace()) == 2
    assert inherited_offset_rule(project, SimpleNamespace(_anyfem_mesh_record_id=current.id)) == 2
    assert inherited_offset_rule(project, SimpleNamespace(_anyfem_mesh_record_id=legacy.id)) == 1
    assert inherited_offset_rule(project, SimpleNamespace(_anyfem_mesh_record_id="unknown")) == 1


def test_pressure_transfer_records_inherit_the_mesh_offset_rule():
    import inspect

    from anyfem.application import hydro_wave_pressure

    assert "beam_offset_rule=inherited_offset_rule(project, mesh)" in inspect.getsource(
        hydro_wave_pressure.map_barge_regular_wave)


def test_submodel_parent_mesh_saved_under_the_mid_surface_rule_is_refused(tmp_path, monkeypatch):
    from anyfem.model.project import Project as _Project
    from test_submodel_jobs import Setup, refused_without_publication

    setup = Setup(tmp_path)
    run = setup.run()
    assert setup.staleness(run) == ()
    # Make the parent model one the rule moves (its own beams are not plate-attached).
    monkeypatch.setattr(_Project, "has_plate_surface_offsets", lambda self: True)
    assert setup.staleness(run) == ()
    setup.source()
    setup.edit(lambda _p: setattr(setup.record, "beam_offset_rule", 1))
    assert "SOURCE_MESH_CHANGED" in setup.staleness(run)
    with refused_without_publication(setup.session, setup.store, "SOURCE_MESH"):
        setup.source()
