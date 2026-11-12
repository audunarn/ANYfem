import numpy as np
import pytest
from anygeometry import OrientedEdge
from anygeometry.operations import punch_hole
from anygeometry.surfaces import Cylinder

from anyfem.application.controller import default_project
from anyfem.application.drawing_sections import build_plane_view, face_plane
from anyfem.model.sections import BeamSection

pytest.importorskip("shapely", minversion="2")
from shapely import Polygon


def _plate(points, *, hole=False, assigned=True):
    project = default_project()
    geometry = project.geometry
    face = geometry.add_plate(geometry.add_points(points))
    if hole:
        punch_hole(geometry, face, (0, 0, 0), .4)
    if assigned:
        project.assign_plate(face, "plate")
    return project, face


def _ranges(path):
    points = np.asarray(path.points)
    return [*points.min(axis=0), *points.max(axis=0)]


def test_cross_cut_plate_preserves_opening_thickness_and_view_direction():
    p, face = _plate([(-2,-1,0),(2,-1,0),(2,1,0),(-2,1,0)], hole=True)
    h = p.plate_section_of(face).thickness / 2
    right = build_plane_view(p,(.1,0,0),(0,1,0),(0,0,1))
    assert right.notes == ("Plane origin XYZ (100, 0, 0) mm",
                           "Unit normal XYZ (0, 1, 0)   Up XYZ (0, 0, 1)")
    assert [x.layer for x in right.paths] == ["SECTION"]*2
    assert all(x.closed for x in right.paths)
    assert np.array(sorted(_ranges(x) for x in right.paths)) == pytest.approx(
        np.array([[-1.9,-h,-.3,h],[.5,-h,2.1,h]]), abs=1e-8)
    left = build_plane_view(p,(.1,0,0),(0,-1,0),(0,0,1))
    assert np.array(sorted(_ranges(x) for x in left.paths)) == pytest.approx(
        np.array([[-2.1,-h,-.5,h],[.3,-h,1.9,h]]), abs=1e-8)


def test_skew_section_and_clipped_extents():
    p, face = _plate([(-2,-1,0),(2,-1,0),(2,1,0),(-2,1,0)])
    h = p.plate_section_of(face).thickness / 2
    view = build_plane_view(p,(.3,.1,0),(0,3,4),(0,0,1))
    path, = view.paths
    assert _ranges(path) == pytest.approx([-1.7,-h/.6,2.3,h/.6])
    clipped = build_plane_view(p,(.3,.1,0),(0,3,4),(0,0,1), bounds=(-1,.7,0,h/.6))
    one, = clipped.paths
    assert one.closed and Polygon(one.points).area == pytest.approx(1.7*h/.6)
    assert _ranges(one) == pytest.approx([-1,0,.7,h/.6])
    assert clipped.notes == ("Plane origin XYZ (300, 100, 0) mm",
                             "Unit normal XYZ (0, 0.6, 0.8)   Up XYZ (0, -0.8, 0.6)",
                             f"View limits   U -1000 to 700 mm   V 0 to {1000*h/.6:.9g} mm")
    assert not build_plane_view(p,(0,0,2),(0,0,1),(0,1,0)).paths


def test_joined_sheet_has_no_mid_seam_and_hole_is_distinct():
    p = default_project()
    g = p.geometry
    a, b, c, d, e, f = g.add_points([(0,0,0),(1,0,0),(1,1,0),(0,1,0),(2,0,0),(2,1,0)])
    seam = g.add_line(b,c)
    left = g.add_face_from_loop([OrientedEdge(i,True) for i in
                                (g.add_line(a,b),seam,g.add_line(c,d),g.add_line(d,a))],
                                corners=(0,1,2,3))
    right = g.add_face_from_loop([OrientedEdge(seam,False), *[OrientedEdge(i,True) for i in
                                (g.add_line(b,e),g.add_line(e,f),g.add_line(f,c))]])
    punch_hole(g, left, (.5,.5,0), .1)
    g.add_sheet((left,right), part_id=g.add_part(), name="Deck")
    p.assign_plates((left,right), "plate")
    view = build_plane_view(p,(0,0,0),(0,0,1),(0,1,0))
    assert not any("omitted" in note for note in view.notes)
    assert [(x.layer,x.label) for x in view.paths] == [("SECTION","Deck"),("HOLES","Deck")]
    assert Polygon(view.paths[0].points).area == pytest.approx(2)
    through = build_plane_view(p,(0,.25,0),(0,-1,0),(0,0,1))
    only, = through.paths
    assert only.layer == "SECTION" and Polygon(only.points).area == pytest.approx(2*p.plate_section_of(right).thickness)
    along_incidence, = build_plane_view(p,(1,0,0),(1,0,0),(0,0,1)).paths
    assert along_incidence.layer == "SECTION"


def test_open_slot_is_empty_through_the_entire_plate_thickness():
    project = default_project()
    g = project.geometry
    # The slot opens at the far edge (y=2) and ends at y=.4.
    xyz = [(0,0,0),(3,0,0),(3,2,0),(2,2,0),(2,.4,0),(1,.4,0),(1,2,0),(0,2,0)]
    vertices = g.add_points(xyz)
    loop = [OrientedEdge(g.add_line(a,b),True) for a,b in zip(vertices,vertices[1:]+vertices[:1])]
    face = g.add_face_from_loop(loop)
    project.assign_plate(face,"plate")
    h = project.plate_section_of(face).thickness/2
    view = build_plane_view(project,(0,1,0),(0,-1,0),(0,0,1))
    assert np.array(sorted(_ranges(p) for p in view.paths)) == pytest.approx(
        np.array([[0,-h,1,h],[2,-h,3,h]]))
    assert not build_plane_view(project,(0,1,0),(0,-1,0),(0,0,1),bounds=(1.1,1.9,-h,h)).paths


def test_face_selected_plane_and_trace_without_section_assignment():
    p, face = _plate([(0,1,0),(2,1,0),(2,2,1),(0,2,1)], assigned=False)
    origin, normal, up = face_plane(p,face)
    front = build_plane_view(p,origin,normal,up)
    outline, = front.paths
    assert outline.layer == "OUTLINE" and outline.closed
    assert Polygon(outline.points).area == pytest.approx(2*np.sqrt(2))
    trace, = build_plane_view(p,(1,0,0),(1,0,0),(0,0,1)).paths
    assert trace.layer == "TRACE" and not trace.closed
    with pytest.raises(ValueError, match="independent"):
        build_plane_view(p, origin, normal, normal)
    with pytest.raises(ValueError, match="increasing"):
        build_plane_view(p, origin, normal, up, bounds=(0,1,1,0))


def test_cylinder_quarter_shell_uses_real_radii_and_rulings():
    p = default_project()
    g = p.geometry
    cylinder = Cylinder((3,1,0), (0,0,1), (1,0,0), 2, 3, sweep_angle=np.pi/2)
    corners = [g.add_point(*cylinder.evaluate(u,v)) for u,v in ((0,0),(1,0),(1,1),(0,1))]
    a,b,c,d = corners
    bottom, top = [g.add_point(*cylinder.evaluate(.5,v)) for v in (0,1)]
    loop = [OrientedEdge(e,True) for e in (g.add_arc(a,bottom,b), g.add_line(b,c),
                                           g.add_arc(c,top,d), g.add_line(d,a))]
    face = g.add_face_from_loop(loop,surface=cylinder)
    trace, = build_plane_view(p,(3,1.5,0),(0,-1,0),(0,0,1)).paths
    assert trace.layer == "TRACE" and not trace.closed
    assert _ranges(trace) == pytest.approx([np.sqrt(4-.25),0,np.sqrt(4-.25),3],abs=2e-5)
    p.assign_plate(face, "plate")
    h = p.plate_section_of(face).thickness / 2
    view = build_plane_view(p,(3,1,1),(0,0,1),(0,1,0))
    section, = view.paths
    assert not any("omitted" in note for note in view.notes)
    assert section.closed and section.layer == "SECTION"
    radii = np.linalg.norm(section.points,axis=1)
    assert (radii.min(),radii.max()) == pytest.approx((2-h,2+h))
    assert Polygon(section.points).area == pytest.approx(np.pi*2*h, rel=2e-5)
    side = build_plane_view(p,(3,1.5,0),(0,-1,0),(0,0,1))
    path, = side.paths
    assert _ranges(path) == pytest.approx([np.sqrt((2-h)**2-.25),0,np.sqrt((2+h)**2-.25),3],abs=2e-5)
    with pytest.raises(ValueError,match="unsupported surface"):
        face_plane(p,face)


def test_actual_t_member_profile_uses_automatic_offset_and_removes_web_seam():
    p = default_project()
    g = p.geometry
    edge = g.add_line(*g.add_points([(-2,0,0),(2,0,0)]))
    section = BeamSection("T", "T-bar", p.plate_sections["plate"].material, web_height=.2,
                          web_thickness=.02, flange_width=.12, flange_thickness=.03,
                          web_direction=(0,0,1), offset_mode="automatic")
    p.add_beam_section(section)
    p.assign_beam(edge,"T")
    view = build_plane_view(p,(0,0,0),(1,0,0),(0,0,1))
    profile, = view.paths
    assert not any("omitted" in note for note in view.notes)
    assert profile.layer == "STIFFENERS"
    assert _ranges(profile) == pytest.approx([-.06,0,.06,.23])
    assert Polygon(profile.points).area == pytest.approx(.2*.02+.12*.03)


def test_unsupported_curved_member_is_identified_without_fabricating_a_sweep():
    p = default_project()
    g = p.geometry
    a, mid, b = g.add_points([(-2,0,0),(0,1,0),(2,0,0)])
    edge = g.add_arc(a,mid,b)
    p.assign_beam(edge, "stiffener")
    view = build_plane_view(p,(0,0,0),(1,0,0),(0,0,1))
    assert not view.paths
    assert view.notes and f"Member edge {edge}" in view.notes[0] and "curved" in view.notes[0]
