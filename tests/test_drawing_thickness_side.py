"""Plate thickness side (centre / front / back) in drawings and schedules."""
import numpy as np
import pytest
from anymaterial import MaterialSpec

from anyfem.application.drawing_assembly import _stock, assembly_views
from anyfem.application.plate_profiles import build_plate_profiles
from anyfem.model.project import Project

pytest.importorskip("shapely", minversion="2")
from shapely import LineString, Point, union_all


def _tee():
    """Flange z=0 (n=+Z, 20 mm) and a web in x=1 (n=+X, 10 mm) standing on it."""
    p = Project("tee")
    p.add_material(MaterialSpec("steel", constants={"elastic_modulus": 210e9, "poisson_ratio": .3}, density=7850))
    p.add_plate_section("10 mm", .010, "steel")
    p.add_plate_section("20 mm", .020, "steel")
    g = p.geometry
    part = g.add_part(name="Tee")
    flange = g.add_plate(g.add_points([(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)]))
    web = g.add_plate(g.add_points([(1, 0, 0), (1, 1, 0), (1, 1, .5), (1, 0, .5)]))
    ids = {}
    for face, mark, section in ((flange, "flange", "20 mm"), (web, "web", "10 mm")):
        ids[mark] = g.add_sheet((face,), part_id=part, name=mark)
        p.assign_plate(face, section)
    return p, part, ids


def _linework(view):
    return union_all([LineString((*q.points, q.points[0]) if q.closed else q.points) for q in view.paths])


@pytest.mark.parametrize("side, low, high", [("centre", -.01, .01), ("front", 0., .02), ("back", -.02, 0.)])
def test_flange_side_sets_stock_band_and_z_envelope(side, low, high):
    p, part, ids = _tee()
    profiles = {q.mark: q for q in build_plate_profiles(p, part, thickness_sides={ids["flange"]: side})}
    flange, web = profiles["flange"], profiles["web"]
    assert flange.thickness_side == side and web.thickness_side == "centre"
    assert (flange.z_min_m, flange.z_max_m) == pytest.approx((low, high), abs=1e-15)
    assert (web.z_min_m, web.z_max_m) == pytest.approx((0., .5), abs=1e-15)
    bottom, top = _stock(flange).caps
    assert np.vstack(bottom)[:, 2] == pytest.approx(low, abs=1e-15)
    assert np.vstack(top)[:, 2] == pytest.approx(high, abs=1e-15)


@pytest.mark.parametrize("side, low, high", [("centre", .995, 1.005), ("front", 1., 1.01), ("back", .99, 1.)])
def test_web_side_moves_its_material_along_plus_x(side, low, high):
    p, part, ids = _tee()
    profiles = {q.mark: q for q in build_plate_profiles(p, part, thickness_sides={ids["web"]: side})}
    bottom, top = _stock(profiles["web"]).caps
    assert np.vstack(bottom)[:, 0] == pytest.approx(low, abs=1e-15)
    assert np.vstack(top)[:, 0] == pytest.approx(high, abs=1e-15)
    _, plan, _, _ = assembly_views(p, part, thickness_sides={ids["web"]: side, ids["flange"]: "centre"})
    drawing = _linework(plan)
    for x in (low, high):
        assert drawing.distance(Point(x, .5)) < 1e-8
    for x in {.99, .995, 1.005, 1.01} - {low, high}:
        assert drawing.distance(Point(x, .5)) > 1e-3


def test_support_base_follows_lowest_material_and_mass_is_side_independent():
    p, part, ids = _tee()
    notes = {}
    for side, base in (("back", -.02), ("front", 0.), ("centre", -.01)):
        views = assembly_views(p, part, elevation_datum="support_base", thickness_sides={ids["flange"]: side})
        front = views[2]
        assert front.extent()[0][1] == pytest.approx(0., abs=1e-12)
        assert f"global Z {base*1000:g} mm" in front.notes[3]
        notes[side] = front.notes[1:3]
        world = assembly_views(p, part, elevation_datum="global", thickness_sides={ids["flange"]: side})[2]
        assert world.extent()[0][1] == pytest.approx(base, abs=1e-12)
    assert notes["back"] == notes["front"] == notes["centre"]


def test_explicit_all_centre_is_bit_identical_to_the_historical_default():
    p, part, ids = _tee()
    legacy = assembly_views(p, part, thickness_sides="centre")
    explicit = assembly_views(p, part, thickness_sides={sheet: "centre" for sheet in ids.values()})
    assert legacy == explicit
    assert build_plate_profiles(p, part) == build_plate_profiles(p, part, thickness_sides={})


def test_unknown_side_is_refused():
    p, part, ids = _tee()
    with pytest.raises(ValueError, match="Thickness side"):
        build_plate_profiles(p, part, thickness_sides={ids["web"]: "middle"})


# -- S2: sections, intersection references and the single plate view ---------------------------

def _labelled(view, label):
    return [path for path in view.paths if path.label == label]


@pytest.mark.parametrize("side, low, high", [("centre", -.01, .01), ("front", 0., .02), ("back", -.02, 0.)])
def test_section_of_a_sided_flange_spans_its_band(side, low, high):
    from anyfem.application.drawing_sections import build_plane_view
    p, part, ids = _tee()
    view = build_plane_view(p, (.5, .5, 0), (1, 0, 0), (0, 0, 1), thickness_sides={ids["flange"]: side})
    cut, = _labelled(view, "flange")
    points = np.asarray(cut.points)
    assert cut.layer == "SECTION"
    assert (points[:, 1].min(), points[:, 1].max()) == pytest.approx((low, high), abs=1e-12)
    # A plane parallel to the flange meets it only inside the material band.
    for z in (-.015, -.005, .005, .015):
        cuts = _labelled(build_plane_view(p, (.5, .5, z), (0, 0, 1), (0, 1, 0),
                                          thickness_sides={ids["flange"]: side}), "flange")
        assert bool(cuts) == (low < z < high)
    skin = _labelled(build_plane_view(p, (.5, .5, high), (0, 0, 1), (0, 1, 0),
                                      thickness_sides={ids["flange"]: side}), "flange")
    assert skin and all(path.layer == "OUTLINE" for path in skin)


@pytest.mark.parametrize("side, inner, outer", [("centre", -.005, .005), ("front", 0., .01), ("back", -.01, 0.)])
def test_cylindrical_section_annulus_follows_the_outward_side(side, inner, outer):
    from anygeometry import OrientedEdge
    from anygeometry.surfaces import Cylinder
    from anyfem.application.controller import default_project
    from anyfem.application.drawing_sections import build_plane_view
    p = default_project()
    g = p.geometry
    cylinder = Cylinder((3, 1, 0), (0, 0, 1), (1, 0, 0), 2, 3, sweep_angle=np.pi/2)
    a, b, c, d = [g.add_point(*cylinder.evaluate(u, v)) for u, v in ((0, 0), (1, 0), (1, 1), (0, 1))]
    bottom, top = [g.add_point(*cylinder.evaluate(.5, v)) for v in (0, 1)]
    loop = [OrientedEdge(e, True) for e in (g.add_arc(a, bottom, b), g.add_line(b, c),
                                            g.add_arc(c, top, d), g.add_line(d, a))]
    face = g.add_face_from_loop(loop, surface=cylinder)
    sheet = g.add_sheet((face,), name="shell")
    p.assign_plate(face, "plate")
    t = p.plate_section_of(face).thickness
    view = build_plane_view(p, (3, 1, 1), (0, 0, 1), (0, 1, 0), thickness_sides={sheet: side})
    section, = view.paths
    radii = np.linalg.norm(section.points, axis=1)
    assert (radii.min(), radii.max()) == pytest.approx((2+inner/.01*t, 2+outer/.01*t), abs=2e-6)


@pytest.mark.parametrize("side, visible", [("centre", True), ("front", True), ("back", False)])
def test_plate_reference_skins_follow_the_target_band(side, visible):
    """A web ending 5 mm above the flange reference reaches only an upper skin."""
    from anyfem.application.plate_view import build_plate_view
    p, part, ids = _tee()
    g = p.geometry
    flange_face = g.face_uses[g.sheets[ids["flange"]].face_use_ids[0]].face_id
    short = g.add_plate(g.add_points([(1.5, .2, .005), (1.5, .8, .005), (1.5, .8, .5), (1.5, .2, .5)]))
    g.add_sheet((short,), part_id=part, name="short web")
    p.assign_plate(short, "10 mm")
    view = build_plate_view(p, flange_face, thickness_sides={ids["flange"]: side})
    refs = [path for path in view.paths if path.layer == "PLATE_INTERSECTIONS" and "short web" in path.label]
    assert bool(refs) == visible
    assert any(f"Thickness side {side} (+n = +Z)" in note for note in view.notes)


@pytest.mark.parametrize("side, low, high", [("centre", .995, 1.005), ("front", 1., 1.01), ("back", .99, 1.)])
def test_plate_reference_marks_follow_the_other_plate_band(side, low, high):
    from anyfem.application.plate_view import build_plate_view
    p, part, ids = _tee()
    g = p.geometry
    flange_face = g.face_uses[g.sheets[ids["flange"]].face_use_ids[0]].face_id
    view = build_plate_view(p, flange_face, thickness_sides={ids["web"]: side, ids["flange"]: "centre"})
    refs = [path for path in view.paths if path.layer == "PLATE_INTERSECTIONS"]
    points = np.vstack([path.points for path in refs])
    # Plate-view coordinates are shifted to the flange's lower-left corner (x=0).
    assert (points[:, 0].min(), points[:, 0].max()) == pytest.approx((low, high), abs=1e-9)


def test_bent_sheet_refuses_an_off_centre_side_in_sections():
    from anygeometry import OrientedEdge as O
    from anyfem.application.controller import default_project
    from anyfem.application.drawing_sections import build_plane_view
    p = default_project()
    g = p.geometry
    a, b, c, d, e, f = g.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (1, 0, 1), (1, 1, 1)])
    ab, bc, cd, da, be, ef, fc = (g.add_line(*q) for q in ((a, b), (b, c), (c, d), (d, a), (b, e), (e, f), (f, c)))
    f1 = g.add_face_from_loop([O(ab, True), O(bc, True), O(cd, True), O(da, True)])
    f2 = g.add_face_from_loop([O(bc, False), O(be, True), O(ef, True), O(fc, True)])
    sheet = g.add_sheet((f1, f2), name="bent")
    p.assign_plate(f1, "plate")
    p.assign_plate(f2, "plate")
    assert build_plane_view(p, (.5, .5, .5), (0, 1, 0), (0, 0, 1), thickness_sides={sheet: "centre"}).paths
    refused = build_plane_view(p, (.5, .5, .5), (0, 1, 0), (0, 0, 1), thickness_sides={sheet: "front"})
    assert not refused.paths
    assert sum("one drawing normal across a bent Sheet" in note for note in refused.notes) == 2
