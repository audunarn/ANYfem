"""Picking a face develops its native physical sheet and member axes in SI."""
from math import pi

import numpy as np
import pytest

from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.application.plate_view import build_plate_view
from anygeometry import Cylinder, OrientedEdge
from anygeometry.operations import punch_hole, trim_face
from anygeometry.structural import AttachmentKind, ParameterRange

pytest.importorskip("shapely", minversion="2")


def _attach(p, sheet, face, edge, member_range=(0., 1.)):
    g = p.geometry
    member = g.add_member([edge], part_id=g.sheets[sheet].part_id, name="transverse web")
    p.assign_beam(edge, "stiffener")
    g.add_attachment(member, AttachmentKind.MEMBER_ON_FACE, "face", face,
                     ParameterRange(*member_range), (ParameterRange(0., 1.), ParameterRange(0., 1.)),
                     sheet_id=sheet)
    return member


def _rectangle_on_cylinder(g, surface, a, b, z0, z1):
    def vertex(theta, z):
        return g.add_point(*(surface.origin + surface.radius * (
            np.cos(theta) * surface.radial_direction + np.sin(theta) * surface.circumferential_direction)
            + z * surface.axis))
    bot = [vertex(theta, z0) for theta in (a, .5*(a+b), b)]
    top = [vertex(theta, z1) for theta in (a, .5*(a+b), b)]
    return [OrientedEdge(g.add_arc(*bot), True), OrientedEdge(g.add_line(bot[-1], top[-1]), True),
            OrientedEdge(g.add_arc(*top), False), OrientedEdge(g.add_line(bot[0], top[0]), False)]


def test_gui_cylinder_full_development_includes_ring_and_axial_stiffeners():
    p = default_project()
    commands.AddCylinder(2., 4., circumferential_segments=6, ring_spacing=1.,
                         plate_section="plate", beam_section="stiffener",
                         origin=(3., -2., 1.), axis=(1., 0., 0.), radial_direction=(0., 1., 0.)).do(p)
    face = sorted(p.face_sections)[-1]
    result = build_plate_view(p, face)
    low, high = result.extent()
    assert low == pytest.approx((0., 0.), abs=1e-8)
    assert high == pytest.approx((4*pi, 4.), abs=1e-8)
    assert "radius 2000 mm" in " ".join(result.notes)
    assert "Opened at" in " ".join(result.notes)
    members = [path for path in result.paths if path.layer == "STIFFENERS"]
    assert {m for m in p.geometry.members
            if any(p.geometry.member_edge_uses[u].edge_id in p.edge_sections
                   for u in p.geometry.members[m].edge_use_ids)} == {
                       int(path.label.split()[1]) for path in members}
    dx = [np.ptp(np.asarray(path.points)[:, 0]) for path in members]
    assert any(d < 1e-8 for d in dx)
    assert max(dx) == pytest.approx(4*pi)
    assert all("stiffener" in path.label for path in members)


def test_curved_patch_cutout_and_member_crossing_angle_branch():
    from shapely import Polygon
    p = default_project()
    g = p.geometry
    c = Cylinder((0., 0., 0.), (0., 0., 1.), (1., 0., 0.), 2., 3., pi*.8, pi*.4)
    face = g.add_face_from_loop(_rectangle_on_cylinder(g, c, .8*pi, 1.2*pi, 0., 3.), surface=c)
    hole = _rectangle_on_cylinder(g, c, .95*pi, 1.05*pi, 1., 2.)
    trim_face(g, face, [hole])
    sheet = g.add_sheet([face], name="rolled plate")
    p.assign_plate(face, "plate")
    a, mid, b = g.add_points([c.evaluate(u, .1) for u in (.2,.5,.8)])
    member = _attach(p, sheet, face, g.add_arc(a, mid, b))
    result = build_plate_view(p, face)
    out, cut, axis = result.paths
    assert (out.layer, cut.layer, axis.layer) == ("OUTLINE", "HOLES", "STIFFENERS")
    assert Polygon(out.points, [cut.points]).area == pytest.approx(2*.4*pi*3 - 2*.1*pi)
    points = np.array(axis.points)
    assert np.ptp(points[:, 0]) == pytest.approx(2*.4*pi*.6, abs=1e-8)
    assert points[:, 1] == pytest.approx(.3)
    assert f"Member {member} " in axis.label
    assert all(path.layer != "STIFFENERS" for path in build_plate_view(p, face, include_stiffeners=False).paths)


def test_planar_sheet_resolves_all_faces_and_places_members_in_the_exact_profile_frame():
    from shapely import Polygon
    p = default_project()
    g = p.geometry
    a,b,c,d,e,f = g.add_points([(8,1,2), (9,1,2), (9,1,4), (8,1,4), (10,1,2), (10,1,4)])
    seam = g.add_line(b,c)
    left = g.add_face_from_loop([OrientedEdge(i,True) for i in
                                 (g.add_line(a,b),seam,g.add_line(c,d),g.add_line(d,a))])
    right = g.add_face_from_loop([OrientedEdge(seam,False), *[OrientedEdge(i,True)
                                for i in (g.add_line(b,e),g.add_line(e,f),g.add_line(f,c))]], corners=(0,1,2,3))
    punch_hole(g, right, (9.5, 1., 3.), .1)
    sheet = g.add_sheet([left,right], name="side")
    p.assign_plates([left,right], "plate")
    p.assign_beam(seam, "stiffener")
    start, end = g.add_points([(8.2,1,3.5), (8.8,1,3.5)])
    member = _attach(p, sheet, left, g.add_line(start,end))
    unrelated = g.add_line(*g.add_points([(8.2,1,3.8), (8.7,1,3.8)]))
    p.assign_beam(unrelated, "stiffener")
    result = build_plate_view(p, right)
    assert result.title == "side" and len(result.exact_loops) == 2
    out, hole, *axes = result.paths
    assert len(axes) == 2
    assert np.ptp(np.asarray(out.points), axis=0) == pytest.approx((2., 2.))
    assert Polygon(out.points, [hole.points]).area == pytest.approx(4-pi*.1**2, abs=1e-5)
    axis, = [a for a in axes if a.label.startswith(f"Member {member} ")]
    assert sorted(x for x,_ in axis.points) == pytest.approx([1.2,1.8])
    assert [y for _,y in axis.points] == pytest.approx([1.5,1.5])


@pytest.mark.parametrize("partial", (False, True))
def test_attachment_cannot_invent_a_member_line(partial):
    p = default_project()
    g = p.geometry
    face = g.add_plate(g.add_points([(0,0,0),(1,0,0),(1,1,0),(0,1,0)]))
    p.assign_plate(face, "plate")
    sheet, = g.sheets
    p0,p1 = g.add_points([(.2,.3,0.), (.2, .7 if partial else 1.1, 0.)])
    _attach(p, sheet, face, g.add_line(p0,p1), (.2,1.) if partial else (0.,1.))
    with pytest.raises(ValueError, match="only part|outside"):
        build_plate_view(p, face)


def test_unassigned_gui_cylinder_and_unowned_native_plate_are_geometry_only():
    p = default_project()
    commands.AddCylinder(1., 2., circumferential_segments=4).do(p)
    face = next(iter(p.geometry.faces))
    result = build_plate_view(p, face)
    low, high = result.extent()
    assert high-low == pytest.approx((2*pi, 2.))
    assert "thickness and material unassigned" in result.notes[0]
    members = [path for path in result.paths if path.layer == "STIFFENERS"]
    assert len(members) == 4
    assert all("section unassigned" in m.label for m in members)
    g = p.geometry
    plain = g.add_plate(g.add_points([(5.,0.,0.), (7.,0.,0.), (7.,3.,0.), (5.,3.,0.)]))
    revision = g.revision
    result = build_plate_view(p, plain)
    assert result.title == f"Face {plain}"
    assert "thickness and material unassigned" in result.notes[0]
    assert len(result.exact_loops) == 1
    assert np.ptp(result.paths[0].points, axis=0) == pytest.approx((2., 3.))
    assert g.revision == revision and plain not in p.face_sections


def test_arc_crossing_development_cut_is_split_instead_of_drawing_across_plate():
    p = default_project()
    commands.AddCylinder(2., 3., circumferential_segments=6, plate_section="plate").do(p)
    g = p.geometry
    face = sorted(g.faces)[0]
    sheet, = g.sheets
    cylinder = g.faces[face].surface
    angles = (-.2, 0., .2)
    points = g.add_points([cylinder.origin + 2*np.array([np.cos(a), np.sin(a), .5]) for a in angles])
    edge = g.add_arc(*points)
    member = g.add_member([edge], part_id=g.sheets[sheet].part_id)
    p.assign_beam(edge, "stiffener")
    g.add_attachment(member, AttachmentKind.MEMBER_ON_SHEET, "sheet", sheet, ParameterRange(0.,1.),
                     (ParameterRange(0.,1.), ParameterRange(0.,1.)))
    result = build_plate_view(p, face)
    parts = [path for path in result.paths if path.label.startswith(f"Member {member} ")]
    assert len(parts) == 2
    intervals = sorted((min(x for x,_ in path.points),max(x for x,_ in path.points)) for path in parts)
    np.testing.assert_allclose(intervals, [(0., .4), (4*pi-.4, 4*pi)], atol=1e-8)


def test_axis_chord_with_endpoints_on_cylinder_refuses():
    p = default_project()
    g = p.geometry
    c = Cylinder((0.,0.,0.), (0.,0.,1.), (1.,0.,0.), 2., 1., 0., 1.)
    face = g.add_face_from_loop(_rectangle_on_cylinder(g,c,0.,1.,0.,1.), surface=c)
    sheet = g.add_sheet([face])
    p.assign_plate(face, "plate")
    vertices = g.add_points([c.evaluate(u, .5) for u in (.1,.9)])
    _attach(p, sheet, face, g.add_line(*vertices))
    with pytest.raises(ValueError, match="reference cylinder"):
        build_plate_view(p, face)


def test_oblique_elliptical_cut_develops_to_a_sinusoidal_profile():
    from shapely import Polygon
    ellipse_type = pytest.importorskip("anygeometry.exact_curves").EllipticArc
    p = default_project()
    g = p.geometry
    radius, slope = 1.5, .4
    c = Cylinder((0.,0.,0.), (0.,0.,1.), (1.,0.,0.), radius, 2., 0., pi/2)
    p0, p1, p2 = g.add_points([c.evaluate(u,0.) for u in (0., .5, 1.)])
    ellipse = ellipse_type((0.,0.,1.), (radius,0.,slope), (0.,radius,0.), 0., pi/2)
    q0, q1 = g.add_points(ellipse.evaluate(np.array([0.,1.])))
    face = g.add_face_from_loop([
        OrientedEdge(g.add_arc(p0,p1,p2), True), OrientedEdge(g.add_line(p2,q1), True),
        OrientedEdge(g.add_curve(q0,q1,ellipse), False), OrientedEdge(g.add_line(q0,p0), True)
    ], surface=c)
    result = build_plate_view(p, face)
    outline, = result.paths
    assert Polygon(outline.points).area == pytest.approx(radius*(pi/2 + slope), abs=1e-5)
    assert max(z for _,z in outline.points) == pytest.approx(1+slope, abs=1e-8)


def test_explicit_disconnection_overrides_a_shared_assigned_edge():
    p = default_project()
    g = p.geometry
    face = g.add_plate(g.add_points([(0,0,0),(1,0,0),(1,1,0),(0,1,0)]))
    p.assign_plate(face, "plate")
    sheet, = g.sheets
    edge = g.faces[face].loop[0].edge
    p.assign_beam(edge, "stiffener")
    member, = g.members_using_edge(edge)
    g.add_attachment(member, AttachmentKind.MEMBER_ON_FACE_BOUNDARY, "edge", edge, ParameterRange(0.,1.),
                     (ParameterRange(0.,1.),), sheet_id=sheet, connection_intent="keep_disconnected")
    assert all(path.layer != "STIFFENERS" for path in build_plate_view(p, face).paths)


def test_cylinder_cannot_join_independent_edges_at_the_same_location():
    from anygeometry import SheetTopologyPolicy
    p = default_project()
    g = p.geometry
    surface = Cylinder((0.,0.,0.), (0.,0.,1.), (1.,0.,0.), 2., 1., 0., 1.)
    faces = [g.add_face_from_loop(_rectangle_on_cylinder(g, surface, a, b, 0., 1.), surface=surface)
             for a,b in ((0.,.5), (.5,1.))]
    g.add_sheet(faces, policy=SheetTopologyPolicy(connectivity="allow_disconnected"))
    p.assign_plates(faces, "plate")
    with pytest.raises(ValueError, match="shared native edges"):
        build_plate_view(p, faces[0])
