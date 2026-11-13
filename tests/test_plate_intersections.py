"""Plate references use finite stock material, leaving cutting profiles intact."""
import numpy as np
import pytest

from anyfem.application.controller import default_project
from anyfem.application.plate_view import build_plate_view
from anygeometry.operations import punch_hole

pytest.importorskip("shapely", minversion="2")


def _plate(p, points, mark):
    g = p.geometry
    face = g.add_plate(g.add_points(points))
    g.add_sheet((face,), name=mark)
    p.assign_plate(face, "plate")
    return face


def _target(p):
    return _plate(p, [(0,0,0),(2,0,0),(2,2,0),(0,2,0)], "base")


def _refs(view):
    return [path for path in view.paths if path.layer == "PLATE_INTERSECTIONS"]


@pytest.mark.parametrize("low", [-1., 0., .005])
def test_x_t_and_skin_contact_with_default_and_optout(low):
    p = default_project()
    face = _target(p)
    half = p.plate_section_of(face).thickness/2
    low = half if low == .005 else low
    _plate(p, [(1,.25,low),(1,1.75,low),(1,1.75,1),(1,.25,1)], "web")
    revision = p.geometry.revision
    result = build_plate_view(p, face)
    refs = _refs(result)
    assert refs and all("web | plate" in r.label and "mm" in r.label for r in refs)
    points = np.vstack([r.points for r in refs])
    assert points[:,1].min() == pytest.approx(.25)
    assert points[:,1].max() == pytest.approx(1.75)
    without = build_plate_view(p, face, include_plate_intersections=False)
    assert not _refs(without)
    assert result.exact_loops is not None
    assert [r for r in result.paths if r.layer != "PLATE_INTERSECTIONS"] == list(without.paths)
    assert p.geometry.revision == revision


def test_projection_only_overlap_and_equivalent_coplanar_seam_are_absent():
    p = default_project()
    face = _target(p)
    _plate(p, [(0,0,1),(2,0,1),(2,2,1),(0,2,1)], "above")
    _plate(p, [(2,0,0),(3,0,0),(3,2,0),(2,2,0)], "same stock")
    assert not _refs(build_plate_view(p, face))


def test_target_hole_has_no_reference_paths_through_void():
    from shapely import LineString, Polygon
    p = default_project()
    face = _target(p)
    g = p.geometry
    punch_hole(g, face, (1.,1.,0.), .2)
    _plate(p, [(1,0,-1),(1,2,-1),(1,2,1),(1,0,1)], "web")
    view = build_plate_view(p, face)
    refs = _refs(view)
    assert len(refs) == 2
    hole = Polygon(next(path.points for path in view.paths if path.layer == "HOLES"))
    for path in refs:
        assert LineString((*path.points, path.points[0]) if path.closed else path.points).intersection(hole.buffer(-1e-7)).is_empty


def test_source_hole_excludes_contact():
    p = default_project()
    face = _target(p)
    g = p.geometry
    other = _plate(p, [(1,0,-1),(1,2,-1),(1,2,1),(1,0,1)], "web")
    punch_hole(g, other, (1.,1.,0.), .5)
    refs = _refs(build_plate_view(p, face))
    assert len(refs) == 2
    assert all(max(y for _, y in r.points) <= .501 or min(y for _, y in r.points) >= 1.499 for r in refs)


@pytest.mark.parametrize("axial", [False, True])
def test_selected_cylinder_common_plane_references(axial):
    from anyfem import commands
    p = default_project()
    commands.AddCylinder(1., 2., circumferential_segments=4, plate_section="plate").do(p)
    face = next(iter(p.geometry.faces))
    if axial:
        points = [(-2,0,.25),(2,0,.25),(2,0,1.75),(-2,0,1.75)]
    else:
        points = [(-2,-2,1),(2,-2,1),(2,2,1),(-2,2,1)]
    _plate(p, points, "ring or web")
    view = build_plate_view(p, face, include_stiffeners=False)
    refs = _refs(view)
    assert refs and all("ring or web" in r.label for r in refs)
    assert all(np.isfinite(r.points).all() for r in refs)
    assert not any("unsupported" in note for note in view.notes)


def test_translated_rotated_plate_has_reference_in_its_drawing_frame():
    from shapely import Polygon, union_all
    p=default_project()
    u=np.array((1.,1.,0.))/np.sqrt(2)
    v=np.array((-1.,1.,2.))/np.sqrt(6)
    normal=np.cross(u,v)
    origin=np.array((17.,-8.,3.))
    def world(points):
        return [origin+x*u+y*v+z*normal for x,y,z in points]
    corners=world([(0,0,0),(2,0,0),(2,2,0),(0,2,0)])
    target=_plate(p,corners,"target")
    _plate(p,world([(1,.25,-1),(1,1.75,-1),(1,1.75,1),(1,.25,1)]),"web")
    up=np.array((0.,0.,1.))-(normal[2]*normal)
    up/=np.linalg.norm(up)
    axes=np.column_stack((np.cross(up,normal),up))
    shift=(np.asarray(corners)-origin).dot(axes).min(axis=0)
    half=p.plate_section_of(target).thickness/2
    expected=Polygon((np.asarray(world([(1-half,.25,0),(1+half,.25,0),(1+half,1.75,0),(1-half,1.75,0)]))-origin).dot(axes)-shift)
    actual=union_all([Polygon(path.points) for path in _refs(build_plate_view(p,target))])
    assert actual.symmetric_difference(expected).area<1e-10


def test_multi_face_planar_source_intersects_selected_cylinder():
    from anyfem import commands
    from anygeometry import OrientedEdge
    p=default_project()
    commands.AddCylinder(1.,2.,circumferential_segments=4,plate_section="plate").do(p)
    target=next(iter(p.geometry.faces))
    g=p.geometry
    points=g.add_points([(-2,-2,1),(0,-2,1),(2,-2,1),(-2,2,1),(0,2,1),(2,2,1)])
    first=g.add_plate([points[i] for i in (0,1,4,3)])
    shared=g.faces[first].loop[1]
    second=g.add_face_from_loop([
        OrientedEdge(g.add_line(points[a],points[b]),True) for a,b in ((1,2),(2,5),(5,4))
    ]+[OrientedEdge(shared.edge,not shared.forward)])
    g.add_sheet((first,second),name="Joined flange")
    p.assign_plates((first,second),"plate")
    view=build_plate_view(p,target,include_stiffeners=False)
    assert _refs(view) and all("Joined flange" in path.label for path in _refs(view))
    assert not any("unsupported" in note for note in view.notes)


def test_ambiguous_target_ownership_refuses():
    p=default_project()
    face=_target(p)
    p.geometry.add_sheet((face,),name="Duplicate owner")
    with pytest.raises(ValueError,match="owner"):
        build_plate_view(p,face)
