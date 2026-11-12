"""Physical sheets, trim roles and SI quantities at the drawing boundary."""
from math import pi

import numpy as np
import pytest

from anyfem.application.plate_profiles import build_plate_profiles
from anyfem.model.project import Project
from anygeometry import OrientedEdge, SheetTopologyPolicy
from anygeometry.operations import punch_hole
from anymaterial import MaterialSpec

pytest.importorskip("shapely", minversion="2")


def _project():
    p = Project("shop")
    p.add_material(MaterialSpec("steel", constants={"elastic_modulus": 210e9, "poisson_ratio": .3},
                                density=7850.))
    p.add_plate_section("10mm", .010, "steel")
    return p


def _pair(*, tilted=False):
    p = _project()
    g = p.geometry
    a, b, c, d, e, f = g.add_points([(0,0,0), (1,0,0), (1,1,0), (0,1,0),
                                      (2,0,float(tilted)), (2,1,float(tilted))])
    seam = g.add_line(b, c)
    left = g.add_face_from_loop([OrientedEdge(i, True) for i in
              (g.add_line(a,b), seam, g.add_line(c,d), g.add_line(d,a))])
    # The second face was authored backwards. The sheet supplies its orientation.
    right = g.add_face_from_loop([OrientedEdge(i, True) for i in
              (seam, g.add_line(c,f), g.add_line(f,e), g.add_line(e,b))])
    part = g.add_part(name="support")
    g.add_sheet((left, right), part_id=part, name="side", orientations=(1,-1))
    p.assign_plates((left, right), "10mm")
    return p, part, right


def test_joined_faces_cancel_only_the_internal_seam(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    p, part, _ = _pair()
    profile, = build_plate_profiles(p, part)
    assert (profile.mark, profile.material, profile.hole_count) == ("side", "steel", 0)
    assert (profile.area_m2, profile.z_min_m, profile.z_max_m) == pytest.approx((2, -.005, .005))
    path = tmp_path / "out.dxf"
    profile.write_dxf(path)
    doc = ezdxf.readfile(path)
    entities = list(doc.modelspace())
    assert doc.units == ezdxf.units.MM and len(entities) == 6
    assert {e.dxf.layer for e in entities} == {"OUTLINE"}
    assert all(e.dxftype() == "LINE" for e in entities)
    assert max(e.dxf.start.x for e in entities) == pytest.approx(2000)
    assert not any(e.dxf.start.x == e.dxf.end.x == 1000 for e in entities)


def test_inclined_plate_preserves_exact_round_hole_and_thickness_envelope():
    p = _project()
    g = p.geometry
    origin = np.array([4., -2., -4.])
    u, v = np.array([1,0,0]), np.array([0,.8,.6])
    face = g.add_plate(g.add_points([origin, origin+2*u, origin+2*u+v, origin+v]))
    punch_hole(g, face, origin+u+.5*v, .2)
    part = g.add_part()
    g.add_sheet([face], part_id=part, name="brace")
    p.assign_plate(face, "10mm")
    result, = build_plate_profiles(p, part)
    assert result.hole_count == 1
    assert result.area_m2 == pytest.approx(2 - pi*.2**2, abs=1e-12)
    assert (result.z_min_m, result.z_max_m) == pytest.approx((-4.004, -3.396), abs=1e-12)


def test_incompatible_thickness_and_nonplanar_crease_are_refused():
    p, part, right = _pair()
    p.add_plate_section("12mm", .012, "steel")
    p.assign_plate(right, "12mm")
    with pytest.raises(ValueError, match="conflicting plate thickness or material"):
        build_plate_profiles(p, part)
    bent, part, _ = _pair(tilted=True)
    with pytest.raises(ValueError, match="not coplanar"):
        build_plate_profiles(bent, part)


def test_disconnected_nested_face_cannot_be_published_as_a_hole():
    p = _project()
    g = p.geometry
    faces = [g.add_plate(g.add_points([(scale*x,scale*y,0) for x,y in
                    [(-1,-1), (1,-1), (1,1), (-1,1)]])) for scale in (2., 1.)]
    part = g.add_part()
    g.add_sheet(faces, part_id=part, name="overlap",
                policy=SheetTopologyPolicy(connectivity="allow_disconnected"))
    p.assign_plates(faces, "10mm")
    with pytest.raises(ValueError, match="disconnected"):
        build_plate_profiles(p, part)


def test_connected_overlapping_faces_are_not_an_apparent_cutout():
    p = _project()
    g = p.geometry
    a,b,c,d,middle = g.add_points([(0,0,0), (1,0,0), (1,1,0), (0,1,0), (.5,.5,0)])
    seam = g.add_line(a,b)
    full = g.add_face_from_loop([OrientedEdge(i,True) for i in
                                (seam,g.add_line(b,c),g.add_line(c,d),g.add_line(d,a))])
    interior = g.add_face_from_loop([OrientedEdge(seam,False), OrientedEdge(g.add_line(a,middle),True),
                                     OrientedEdge(g.add_line(middle,b),True)])
    part = g.add_part()
    g.add_sheet([full,interior], part_id=part, name="overlapping")
    p.assign_plates([full,interior], "10mm")
    with pytest.raises(ValueError, match="overlap or disagree"):
        build_plate_profiles(p, part)


def test_shared_geometry_between_distinct_marks_is_cut_for_both():
    p = _project()
    g = p.geometry
    a,b,c,d = g.add_points([(0,0,0), (1,0,0), (1,1,0), (0,1,0)])
    ab, bc, ca = g.add_line(a,b), g.add_line(b,c), g.add_line(c,a)
    left = g.add_face([ab,bc,ca])
    right = g.add_face([ca,g.add_line(c,d),g.add_line(d,a)])
    part = g.add_part()
    for name, f in (("L", left), ("R", right)):
        g.add_sheet([f], part_id=part, name=name)
        p.assign_plate(f, "10mm")
    profiles = build_plate_profiles(p, part)
    assert [x.mark for x in profiles] == ["L", "R"]
    assert [x.area_m2 for x in profiles] == pytest.approx([.5, .5])


def test_two_trimmed_carriers_can_fit_one_proxy_but_leave_an_open_cut():
    from fractions import Fraction
    LineRange = pytest.importorskip("anygeometry.line_ranges").LineRange

    p = _project()
    g = p.geometry
    a,b,c,d = g.add_points([(0,0,0), (1,0,0), (1,1,0), (0,1,0)])
    # Each CAD endpoint is 8 nm from its shared proxy. Together they
    # leave a 16 nm gap on an otherwise closed and permissible native face.
    dx = 8e-9
    lower = g.add_curve(a,b,LineRange((-.5+dx,0,0), (1.5+dx,0,0),
                                      (Fraction(1,4), Fraction(3,4))))
    right = g.add_curve(b,c,LineRange((1-dx,0,0), (1,1,0)))
    face = g.add_face_from_loop([OrientedEdge(i,True) for i in
                                (lower,right,g.add_line(c,d),g.add_line(d,a))])
    part = g.add_part()
    g.add_sheet([face], part_id=part, name="open")
    p.assign_plate(face, "10mm")
    with pytest.raises(ValueError, match="gap between curve endpoints"):
        build_plate_profiles(p, part)
