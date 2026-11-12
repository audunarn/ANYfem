import math

import numpy as np
import pytest
from anygeometry import OrientedEdge
from anygeometry.operations import punch_hole
from anymaterial import MaterialSpec

from anyfem.application.drawing_assembly import assembly_views
from anyfem.model.project import Project

pytest.importorskip("shapely", minversion="2")
from shapely import LineString, Point, union_all


def _project():
    p = Project("assembly")
    p.add_material(MaterialSpec("steel", constants={"elastic_modulus":210e9, "poisson_ratio":.3}, density=7850))
    p.add_plate_section("10 mm", .010, "steel")
    p.add_plate_section("20 mm", .020, "steel")
    return p, p.geometry.add_part(name="Bracket")


def _horizontal(p, part, mark, x0, x1, y0, y1, z, section, *, hole=False):
    g = p.geometry
    f = g.add_plate(g.add_points([(x0,y0,z),(x1,y0,z),(x1,y1,z),(x0,y1,z)]))
    if hole:
        punch_hole(g, f, (3,11,z), .5)
    g.add_sheet((f,), part_id=part, name=mark)
    p.assign_plate(f, section)


def _linework(view):
    return union_all([LineString((*p.points, p.points[0]) if p.closed else p.points) for p in view.paths])


def test_assembly_projection_respects_holes_occlusion_and_true_sheet_stock():
    p, part = _project()
    _horizontal(p,part,"top",1,5,10,12,1.5,"20 mm",hole=True)
    _horizontal(p,part,"obscured",1.3,2.1,10.4,11.6,.5,"10 mm")
    _horizontal(p,part,"through opening",2.2,3.8,10.9,11.1,.7,"10 mm")
    iso, plan, front, right = assembly_views(p,part,quantity=3)
    assert [v.title for v in (iso,plan,front,right)] == [
        f"Bracket  /  {view}" for view in ("Isometric","Plan","Front","Right")]
    assert all(v.paths for v in (iso,plan,front,right))
    drawing = _linework(plan)
    # A rear plate disappears fully; only the span under the hole is visible
    # on the other rear plate. The circular rim stays visible in plan.
    for point in ((1,11),(3,10.5),(2.5,11),(3.5,11),(3,10.9),(3,11.1)):
        assert drawing.distance(Point(point)) < 1e-8
    for point in ((1.3,11),(2.1,11),(2.3,10.9),(3.7,11.1),(3,11)):
        assert drawing.distance(Point(point)) > .05
    assert np.array(plan.extent()) == pytest.approx(np.array([[1,10],[5,12]]))
    assert np.array(front.extent()) == pytest.approx(np.array([[1,.495],[5,1.51]]))
    assert np.array(right.extent()) == pytest.approx(np.array([[10,.495],[12,1.51]]))
    # In the stated isometric direction, screen-right is (X+Y)/sqrt(2)
    # and world Z projects up. Include actual top/bottom plate surfaces.
    projected = []
    for xmin, xmax, ymin, ymax, zmin, zmax in (
        (1,5,10,12,1.49,1.51),(1.3,2.1,10.4,11.6,.495,.505),(2.2,3.8,10.9,11.1,.695,.705),
    ):
        for x in (xmin,xmax):
            for y in (ymin,ymax):
                for z in (zmin,zmax):
                    projected.append(((x+y)/math.sqrt(2),(-x+y+2*z)/math.sqrt(6)))
    assert np.array(iso.extent()) == pytest.approx(np.array([
        np.min(projected,axis=0),np.max(projected,axis=0)]))
    assert _linework(front).distance(Point(1,1.5)) < 1e-8
    assert _linework(right).distance(Point(10,1.5)) < 1e-8
    light = ((2.1-1.3)*(11.6-10.4) + (3.8-2.2)*.2)*.01*7850
    heavy = (4*2 - math.pi*.5**2)*.02*7850
    expected = light + heavy
    assert f"{expected:.2f} kg each" in plan.notes[1]
    assert f"{3*expected:.2f} kg total" in plan.notes[1]
    assert f"10 mm   {light:.2f} kg" in plan.notes[2]
    assert f"20 mm   {heavy:.2f} kg" in plan.notes[2]


def test_round_plate_side_view_includes_silhouette_at_both_tangencies():
    p, part = _project()
    g = p.geometry
    points = g.add_points([(math.cos(i*math.pi/4), math.sin(i*math.pi/4), 0) for i in range(8)])
    f = g.add_face_from_loop([OrientedEdge(g.add_arc(points[i],points[i+1],points[(i+2)%8]),True)
                              for i in range(0,8,2)])
    g.add_sheet((f,), part_id=part, name="disc")
    p.assign_plate(f,"20 mm")
    _, _, front, _ = assembly_views(p,part)
    shape = _linework(front)
    for x in (-1,1):
        assert shape.distance(Point(x,0)) < 1e-8


@pytest.mark.parametrize("quantity", [0, 1.5, True])
def test_takeoff_requires_a_positive_integer_quantity(quantity):
    project, part = _project()
    with pytest.raises(ValueError,match="positive integer"):
        assembly_views(project, part, quantity=quantity)


def test_grazing_walls_leave_both_lips_of_a_backed_slot_visible():
    from anyfem.application.drawing_assembly import _projection, _stock
    from anyfem.application.plate_profiles import PlateProfile

    # A transferred vertical plate is a fraction of a nanoradian off-axis.
    # A second plate behind its slot fills the projected background, so the
    # slot edges cannot be recovered from the assembly silhouette alone.
    normal = np.array([1.05e-10,1.,1.01e-10])
    u = np.cross(normal, [0.,0.,1.])
    u /= np.linalg.norm(u)
    axes = np.column_stack((u, np.cross(normal,u)))
    outer = [(0,0),(2,0),(2,1),(0,1)]
    inner = [(.3,.3),(.3,.7),(1.7,.7),(1.7,.3)]
    def ring(corners):
        return tuple(np.array([(*a,1.),(*b,1.)]) for a,b in zip(corners, corners[1:]+corners[:1]))
    def plate(origin, loops):
        return _stock(PlateProfile("plate",1,.015,"steel",0.,0.,0.,loops,np.asarray(origin),axes))
    stocks = [plate((29,0,-2),(ring(outer),ring(inner))), plate((29,.4,-2),(ring(outer),))]
    paths = _projection(stocks,(0,-1,0),(0,0,1))
    drawing = union_all([LineString(p.points) for p in paths])
    for z in (-2.,-2.3,-2.7,-3.):
        ends = (29.31,30.69) if z in (-2.3,-2.7) else (29.,31.)
        assert LineString([(x,z) for x in ends]).difference(drawing.buffer(1e-6)).is_empty


def test_plan_shows_a_stock_intersection_only_on_its_uncovered_length():
    project, part = _project()
    _horizontal(project,part,"flat",0,2,0,2,0,"20 mm")
    g = project.geometry
    # Midplane z=x-1; its upper face intersects the flat plate's top face
    # at x = 1 + t/2 - sqrt(2)*t/2, through both plate interiors.
    face = g.add_plate(g.add_points([(0,0,-1),(2,0,1),(2,2,1),(0,2,-1)]))
    g.add_sheet((face,),part_id=part,name="slope")
    project.assign_plate(face,"20 mm")
    _horizontal(project,part,"cover",.7,1.3,.75,1.25,2,"10 mm")
    _, plan, _, _ = assembly_views(project,part)
    drawing = _linework(plan)
    x = 1.01 - math.sqrt(2)*.01
    for y in (.25,1.75):
        assert drawing.distance(Point(x,y)) < 1e-8
    assert drawing.distance(Point(x,1)) > .2


def test_plan_hides_rims_flush_inside_another_front_surface():
    project,part = _project()
    _horizontal(project,part,"large",0,2,0,2,0,"20 mm")
    _horizontal(project,part,"flush",1,3,.5,1.5,.005,"10 mm")
    _,plan,_,_ = assembly_views(project,part)
    drawing = _linework(plan)
    for xy in ((1.,1.),(1.5,.5),(2,1.)):
        assert drawing.distance(Point(xy)) > .2
    for xy in ((2.5,.5),(2,.25)):
        assert drawing.distance(Point(xy)) < 1e-8
