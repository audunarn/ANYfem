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
