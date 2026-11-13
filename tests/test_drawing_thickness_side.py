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


# -- S3: automatic interpretation, project overrides and the project file ----------------------

class _Conformal:
    """Plates built on shared topological vertices and edges, as in imported SAT models."""

    def __init__(self):
        self.p = Project("conformal")
        self.p.add_material(MaterialSpec("steel", constants={"elastic_modulus": 210e9, "poisson_ratio": .3},
                                         density=7850))
        self.p.add_plate_section("10 mm", .010, "steel")
        self.g = self.p.geometry
        self.part = self.g.add_part(name="Support")
        self.points, self.lines = {}, {}

    def point(self, xyz):
        if xyz not in self.points:
            self.points[xyz] = self.g.add_point(*xyz)
        return self.points[xyz]

    def face(self, corners):
        from anygeometry import OrientedEdge
        ids = [self.point(c) for c in corners]
        loop = []
        for a, b in zip(ids, ids[1:] + ids[:1]):
            if (b, a) in self.lines:
                loop.append(OrientedEdge(self.lines[b, a], False))
            else:
                self.lines[a, b] = self.g.add_line(a, b)
                loop.append(OrientedEdge(self.lines[a, b], True))
        return self.g.add_face_from_loop(loop)

    def sheet(self, name, *faces, part=None):
        sheet = self.g.add_sheet(tuple(self.face(f) for f in faces),
                                 part_id=self.part if part is None else part, name=name)
        for use in self.g.sheets[sheet].face_use_ids:
            self.p.assign_plate(self.g.face_uses[use].face_id, "10 mm")
        return sheet

    def flange(self, name="flange", z=0.):
        return self.sheet(name, [(0, 0, z), (1, 0, z), (1, 1, z), (0, 1, z)],
                          [(1, 0, z), (2, 0, z), (2, 1, z), (1, 1, z)])

    def web(self, name, z0, z1, part=None):
        return self.sheet(name, [(1, 0, z0), (1, 1, z0), (1, 1, z1), (1, 0, z1)], part=part)


def test_auto_tee_puts_the_flange_toward_the_web_and_centres_the_web():
    from anyfem.application.thickness_side import resolve_thickness_sides
    m = _Conformal()
    flange, web = m.flange(), m.web("web", 0, .5)
    sides = resolve_thickness_sides(m.p, m.part)
    assert sides[flange] == ("front", "auto", "1 neighbour on +n")
    assert sides[web][:2] == ("centre", "auto") and "both sides" in sides[web][2]
    hanging = _Conformal()
    flange, web = hanging.flange(), hanging.web("web", -.5, 0)
    assert resolve_thickness_sides(hanging.p, hanging.part)[flange] == ("back", "auto", "1 neighbour on -n")


def test_auto_cross_isolated_and_other_part_neighbours_are_centred():
    from anyfem.application.thickness_side import resolve_thickness_sides
    m = _Conformal()
    flange, up, down = m.flange(), m.web("up", 0, .5), m.web("down", -.5, 0)
    sides = resolve_thickness_sides(m.p, m.part)
    assert sides[flange] == ("centre", "auto", "neighbours on both sides (1 on +n, 1 on -n)")
    alone = _Conformal()
    plate = alone.flange()
    other = alone.g.add_part(name="Other")
    alone.web("foreign", 0, .5, part=other)
    assert resolve_thickness_sides(alone.p, alone.part)[plate] == ("centre", "auto", "no neighbours")


def test_mixed_part_combines_auto_sides_and_manual_overrides():
    from anyfem.application.drawing_assembly import assembly_views
    from anyfem.application.thickness_side import resolve_thickness_sides
    m = _Conformal()
    flange, web = m.flange(), m.web("web", 0, .5)
    top = m.sheet("top", [(1, 0, .5), (1, 1, .5), (0, 1, .5), (0, 0, .5)])  # horizontal cap on the web
    loose = m.sheet("loose", [(5, 0, 0), (6, 0, 0), (6, 1, 0), (5, 1, 0)])
    m.p.drawing_thickness_sides["web"] = "back"
    m.p.drawing_thickness_sides[loose] = "front"
    sides = resolve_thickness_sides(m.p, m.part)
    assert sides[flange][:2] == ("front", "auto")
    assert sides[web] == ("back", "manual", "override")
    assert sides[loose] == ("front", "manual", "override")
    assert sides[top] == ("back", "auto", "1 neighbour on -n")
    # A call-time override beats the project; "auto" restores the interpretation.
    assert resolve_thickness_sides(m.p, m.part, {"web": "auto"})[web][1] == "auto"
    profiles = {q.mark: q for q in build_plate_profiles(m.p, m.part, thickness_sides={
        sheet: value[0] for sheet, value in sides.items()})}
    assert profiles["flange"].z_min_m == pytest.approx(0.) and profiles["top"].z_max_m == pytest.approx(.5)
    auto = assembly_views(m.p, m.part, all_views=False)
    explicit = assembly_views(m.p, m.part, all_views=False,
                              thickness_sides={sheet: value[0] for sheet, value in sides.items()})
    assert [v.paths for v in auto] == [v.paths for v in explicit]
    assert [v.paths for v in auto] != [v.paths for v in assembly_views(m.p, m.part, all_views=False,
                                                                       thickness_sides="centre")]


def test_conflicting_name_and_id_overrides_are_refused():
    from anyfem.application.thickness_side import resolve_thickness_sides
    m = _Conformal()
    flange = m.flange()
    m.p.drawing_thickness_sides.update({"flange": "front", flange: "back"})
    with pytest.raises(ValueError, match="conflicting"):
        resolve_thickness_sides(m.p, m.part)
    m.p.drawing_thickness_sides = {"flange": "sideways"}
    with pytest.raises(ValueError, match="Thickness side"):
        resolve_thickness_sides(m.p, m.part)


def test_overrides_round_trip_old_files_load_and_removed_sheets_drop(tmp_path):
    from anyfem.io.project_file import (FORMAT_VERSION, load_project, project_from_dict, project_to_dict,
                                        save_project)
    m = _Conformal()
    flange, web = m.flange(), m.web("web", 0, .5)
    plain = project_to_dict(m.p)
    assert "drawing_thickness_sides" not in plain and plain["anyfem"]["format"] == FORMAT_VERSION
    assert project_from_dict(plain).drawing_thickness_sides == {}
    m.p.drawing_thickness_sides.update({"flange": "back", web: "auto", "gone": "front", 99: "front"})
    data = project_to_dict(m.p)
    assert data["drawing_thickness_sides"] == [{"sheet": web, "side": "auto"}, {"sheet": "flange", "side": "back"}]
    assert data["anyfem"]["format"] == 21
    reopened = load_project(save_project(m.p, tmp_path / "sides.anyfem"))
    assert reopened.drawing_thickness_sides == {web: "auto", "flange": "back"}
    old = dict(data)
    old["anyfem"] = {**data["anyfem"], "format": 20}
    from anyfem.io.project_file import ProjectFileError
    with pytest.raises(ProjectFileError, match="format 21"):
        project_from_dict(old)
    # A geometry edit removing the Sheet drops its override from the document.
    m.g.remove_sheet(m.g.sheets[flange].id)
    assert project_to_dict(m.p)["drawing_thickness_sides"] == [{"sheet": web, "side": "auto"}]


# -- S4: the non-expanded 2D mid-surface representation ---------------------------------------

def test_midsurface_assembly_draws_reference_outlines_without_thickness():
    p, part, ids = _tee()
    g = p.geometry
    hidden = g.add_plate(g.add_points([(.2, .2, -.3), (.6, .2, -.3), (.6, .6, -.3), (.2, .6, -.3)]))
    g.add_sheet((hidden,), part_id=part, name="hidden")
    p.assign_plate(hidden, "10 mm")
    solid = assembly_views(p, part)
    iso, plan, front, right = assembly_views(p, part, representation="midsurface_2d")
    assert front.extent()[0][1] == pytest.approx(-.3, abs=1e-12)
    assert np.array(plan.extent()) == pytest.approx(np.array([[0, 0], [2, 1]]), abs=1e-12)
    # Screen right is (X+Y)/sqrt(2), up (-X+Y+2Z)/sqrt(6): the flange and web reference corners.
    assert np.array(iso.extent()) == pytest.approx(np.array([[0, -2/np.sqrt(6)], [3/np.sqrt(2), 1/np.sqrt(6)]]),
                                                   abs=2e-9)  # 1e-9 linework grid
    assert np.array(solid[0].extent())[1, 1] > 1/np.sqrt(6) + 1e-3
    drawing = _linework(plan)
    assert drawing.distance(Point(1, .5)) < 1e-8  # the edge-on web is one visible line
    for x in (.995, 1.005):
        assert drawing.distance(Point(x, .5)) > 1e-3
    assert drawing.distance(Point(.2, .4)) > 1e-3  # a sheet under the flange is occluded
    side = _linework(front)
    assert side.distance(Point(.5, 0)) < 1e-8 and side.distance(Point(.5, .01)) > 1e-3
    assert side.distance(Point(.4, -.3)) < 1e-8  # nothing in front of the lower sheet in elevation
    assert any("2D mid-surface" in note for note in plan.notes)
    assert solid[1].notes[1:3] == plan.notes[1:3]  # takeoff is unchanged
    with pytest.raises(ValueError, match="representation"):
        assembly_views(p, part, representation="wireframe")


def test_midsurface_sections_are_reference_traces():
    from anyfem.application.drawing_sections import build_plane_view
    p, part, ids = _tee()
    view = build_plane_view(p, (.5, .5, 0), (1, 0, 0), (0, 0, 1), representation="midsurface_2d")
    flange, = _labelled(view, "flange")
    assert flange.layer == "TRACE" and not flange.closed
    assert np.asarray(flange.points)[:, 1] == pytest.approx(0., abs=1e-12)
    web = _labelled(build_plane_view(p, (1, .5, .2), (1, 0, 0), (0, 0, 1), representation="midsurface_2d"), "web")
    assert web and all(path.layer == "OUTLINE" for path in web)
    assert not _labelled(build_plane_view(p, (1.004, .5, .2), (1, 0, 0), (0, 0, 1),
                                          representation="midsurface_2d"), "web")


def test_midsurface_references_are_at_the_reference_intersection_and_export_records_2d(tmp_path):
    import json
    from anyfem.application.plate_drawings import export_plate_drawings
    from anyfem.application.plate_view import build_plate_view
    from anyfem.document import DocumentSession
    p, part, ids = _tee()
    g = p.geometry
    flange_face = g.face_uses[g.sheets[ids["flange"]].face_use_ids[0]].face_id
    view = build_plate_view(p, flange_face, representation="midsurface_2d")
    points = np.vstack([q.points for q in view.paths if q.layer == "PLATE_INTERSECTIONS"])
    assert points[:, 0] == pytest.approx(1., abs=1e-9)
    snapshot = DocumentSession(p).snapshot()
    flat = export_plate_drawings(snapshot, part, tmp_path / "2d", representation="midsurface_2d")
    solid = export_plate_drawings(snapshot, part, tmp_path / "solid")
    assert json.loads((flat / "Drawing-basis.json").read_text())["representation"] == "midsurface_2d"
    assert (flat / "Weight-takeoff.csv").read_text() == (solid / "Weight-takeoff.csv").read_text()
