"""Review findings on curved strips and thickness-side overrides (Codex review of S1..S6, 13 Nov 2026).

1. ``locate`` must use the distance to each trimmed patch, not only its angular/axial trim.
2. Patches must be physically adjacent and must not overlap (coaxial overlap, fold-back).
3. A ruling joint with a normal kink leaves open stock: solid drawing is refused unless the skin gap
   is within geometric tolerance; the 2D mid-surface stays drawable.
4. Call-time overrides naming no Sheet are refused.
5. Project overrides are bound to Sheet identity, so a deleted Sheet's choice never reaches a new
   Sheet of the same name.
"""
import numpy as np
import pytest
from anymaterial import MaterialSpec

from anyfem.application.drawing_assembly import assembly_views
from anyfem.application.plate_profiles import build_plate_profiles
from anyfem.model.project import Project

pytest.importorskip("shapely", minversion="2")

H = .1


def _dir(angle):
    return np.array([np.cos(angle), np.sin(angle)])


def _patches(spec):
    """``spec``: (radius, a, b) per patch; the first has centre 0, later ones are tangent
    continuations (same outward side) unless given as (centre, radius, a, b)."""
    out, previous = [], None
    for item in spec:
        if len(item) == 4:
            centre, radius, a, b = item
            centre = np.asarray(centre, dtype=float)
        else:
            radius, a, b = item
            if previous is None:
                centre = np.zeros(2)
            else:
                c0, r0, _, b0 = previous
                centre = c0 + (r0 - radius) * _dir(b0)
        previous = (centre, radius, a, b)
        out.append(previous)
    return out


def _sheet(patches, *, orientations=None, name="Pl-strip", unshared=()):
    """A Sheet of vertical-axis cylinder patches (angle a -> b, height H) sharing coincident edges."""
    from anygeometry import OrientedEdge as O
    from anygeometry.surfaces import Cylinder
    p = Project("strip")
    p.add_material(MaterialSpec("steel", constants={"elastic_modulus": 210e9, "poisson_ratio": .3}, density=7850))
    p.add_plate_section("12 mm", .012, "steel")
    g = p.geometry
    part = g.add_part(name="Curved")
    points, lines = {}, {}

    def vertex(xy, z):
        key = (round(float(xy[0]), 9), round(float(xy[1]), 9), z)
        if key not in points:
            points[key] = g.add_point(float(xy[0]), float(xy[1]), z)
        return points[key]

    def line(a, b, fresh=False):
        if fresh:  # a coincident edge of its own, not joined to its neighbour
            return O(g.add_line(a, b), True)
        if (b, a) in lines:
            return O(lines[b, a], False)
        if (a, b) not in lines:
            lines[a, b] = g.add_line(a, b)
        return O(lines[a, b], True)

    faces = []
    for k, (centre, radius, a, b) in enumerate(patches):
        at = lambda angle, z: vertex(centre + radius * _dir(angle), z)
        bottom = g.add_arc(at(a, 0), at((a+b)/2, 0), at(b, 0))
        top = g.add_arc(at(b, H), at((a+b)/2, H), at(a, H))
        loop = [O(bottom, True), line(at(b, 0), at(b, H), (k, "b") in unshared), O(top, True),
                line(at(a, H), at(a, 0), (k, "a") in unshared)]
        seam = (a + b) / 2 - np.pi  # parameter seam opposite the patch
        surface = Cylinder((*centre, 0), (0, 0, 1), (np.cos(seam), np.sin(seam), 0), radius, H, sweep_angle=2*np.pi)
        faces.append(g.add_face_from_loop(loop, surface=surface))
    sheet = g.add_sheet(tuple(faces), part_id=part, name=name, orientations=orientations)
    for face in faces:
        p.assign_plate(face, "12 mm")
    return p, part, sheet, faces


def _strip_of(p, sheet):
    from anyfem.application.curved_strips import curved_strip
    g = p.geometry
    return curved_strip(g, [g.faces[g.face_uses[u].face_id] for u in g.sheets[sheet].face_use_ids], g.sheets[sheet])


# -- 1: locate by distance to the trimmed patch ------------------------------------------------

SCROLL = ((1., 0., np.pi), (1.5, np.pi, 2*np.pi), (2., 2*np.pi, 3*np.pi))  # a tangent scroll past one turn


def test_scroll_points_develop_on_their_own_patch():
    patches = _patches(SCROLL)
    p, part, sheet, _ = _sheet(patches)
    strip = _strip_of(p, sheet)
    centre, radius, a, b = patches[2]
    angle = (a + b) / 2
    point = (*(centre + radius * _dir(angle)), H / 2)
    expected = np.pi * 1. + np.pi * 1.5 + np.pi / 2 * 2.
    assert strip.develop([point])[0] == pytest.approx((expected, H / 2), abs=1e-9)
    assert strip.radial_distance([point])[0] == pytest.approx(0., abs=1e-9)
    # Off the surface, outside the last turn: 0.3 m outward from the R = 2 patch.
    outside = (*(centre + (radius + .3) * _dir(angle)), H / 2)
    assert strip.radial_distance([outside])[0] == pytest.approx(.3, abs=1e-9)
    profile, = build_plate_profiles(p, part)
    assert profile.area_m2 == pytest.approx((1. + 1.5 + 2.) * np.pi * H, rel=1e-12)


def test_equidistant_points_that_develop_differently_are_refused():
    from anyfem.application.curved_strips import curved_strip  # noqa: F401
    patches = _patches(SCROLL)
    p, part, sheet, _ = _sheet(patches)
    strip = _strip_of(p, sheet)
    # Halfway between the R = 1 and R = 2 coaxial turns at the same angle: 0.5 m from both.
    point = (*(1.5 * _dir(np.pi / 2)), H / 2)
    with pytest.raises(ValueError, match="ambiguous"):
        strip.develop([point])


# -- 2: physical adjacency and overlap ----------------------------------------------------------

def test_coaxial_overlapping_patches_on_a_shared_ruling_are_refused():
    from anygeometry.structural import Orientation
    patches = [(np.zeros(2), 2., 0., np.pi/4), (np.zeros(2), 2., 0., 4*np.pi/9)]
    p, part, sheet, _ = _sheet(patches, orientations=[Orientation.FORWARD, Orientation.REVERSED])
    with pytest.raises(ValueError, match="overlap|same side"):
        _strip_of(p, sheet)
    with pytest.raises(ValueError, match="overlap|same side"):
        build_plate_profiles(p, part)
    with pytest.raises(ValueError, match="overlap|same side"):
        assembly_views(p, part)


def test_overlapping_strip_is_omitted_from_sections_not_cut_per_face():
    from anygeometry.structural import Orientation
    from anyfem.application.drawing_sections import build_plane_view
    patches = [(np.zeros(2), 2., 0., np.pi/4), (np.zeros(2), 2., 0., 4*np.pi/9)]
    p, part, sheet, _ = _sheet(patches, orientations=[Orientation.FORWARD, Orientation.REVERSED])
    for representation in ("solid", "midsurface_2d"):
        view = build_plane_view(p, (0, 0, H / 2), (0, 0, 1), (0, 1, 0), thickness_sides="centre",
                                representation=representation)
        assert not [q for q in view.paths if q.label == "Pl-strip"]
        assert any("omitted" in note and "same side" in note for note in view.notes)


def test_a_shared_edge_does_not_excuse_a_second_unshared_contact():
    # A lens of two R = 1 m patches about (0, 0) and (1, 0): joined along the upper crossing
    # ruling, but the lower crossing rulings are two separate coincident edges.
    patches = [((0., 0.), 1., -np.pi/3, np.pi/3), ((1., 0.), 1., 2*np.pi/3, 4*np.pi/3)]
    p, part, sheet, _ = _sheet(patches, unshared={(1, "b")})
    with pytest.raises(ValueError, match="away from a shared edge"):
        _strip_of(p, sheet)
    with pytest.raises(ValueError, match="away from a shared edge"):
        build_plate_profiles(p, part)


def test_patches_crossing_away_from_their_joint_are_refused():
    # Three patches: the last comes back through the first (R = 2 at 0..90 deg, then a tight
    # tangent R = 0.5 turn of 180 deg, then an R = 3 patch crossing the first patch's arc).
    first = (np.zeros(2), 2., 0., np.pi/2)
    turn = _patches([first[1:], (.5, np.pi/2, 3*np.pi/2)])[1]
    c, r, a, b = turn
    # Continue tangentially from the end of the turn, outward side kept, with a large radius.
    end_normal = _dir(b)
    big = 3.
    centre = c + (r - big) * end_normal
    third = (centre, big, b, b + np.pi/6)  # from (0, 1) toward (1.5, 1.40), through R = 2
    p, part, sheet, _ = _sheet([first, turn, third])
    with pytest.raises(ValueError, match="overlap|cross"):
        _strip_of(p, sheet)


# -- 3: kinked joints ---------------------------------------------------------------------------

def _kinked(kink=np.pi/4):
    first = (np.zeros(2), 2., 0., np.pi/4)
    joint = 2. * _dir(np.pi/4)
    centre = joint - 1. * _dir(np.pi/4 + kink)  # the second patch's normal turns by ``kink`` at the joint
    return _sheet([first, (centre, 1., np.pi/4 + kink, np.pi/2 + kink)])


@pytest.mark.parametrize("degrees, refused", [(.1, False), (.5, True)])
def test_kink_refusal_starts_above_the_drawing_chord(degrees, refused):
    # Front side, 12 mm: the skin opens by 12 mm * 2 sin(kink / 2): 0.021 mm, then 0.105 mm.
    p, part, sheet, _ = _kinked(np.radians(degrees))
    if refused:
        with pytest.raises(ValueError, match=r"open by 0\.105 mm there, more than the 0\.05 mm drawing chord"):
            assembly_views(p, part, thickness_sides={sheet: "front"})
    else:
        assert assembly_views(p, part, thickness_sides={sheet: "front"})[1].paths


@pytest.mark.parametrize("side", ["front", "back", "centre"])
def test_kinked_joint_refuses_solid_stock(side):
    from anyfem.application.drawing_sections import build_plane_view
    p, part, sheet, _ = _kinked()
    # The reference development and takeoff stay defined; the drawn solid does not close.
    profile, = build_plate_profiles(p, part, thickness_sides={sheet: side})
    assert profile.area_m2 == pytest.approx((2. + 1.) * np.pi / 4 * H, rel=1e-12)
    with pytest.raises(ValueError, match=r"45\.000 deg normal kink"):
        assembly_views(p, part, thickness_sides={sheet: side})
    view = build_plane_view(p, (0, 0, H / 2), (0, 0, 1), (0, 1, 0), thickness_sides={sheet: side})
    assert not [q for q in view.paths if q.label == "Pl-strip"]
    assert any("normal kink" in note and "omitted" in note for note in view.notes)
    gap = {"front": 12., "back": 12., "centre": 6.}[side] * 2 * np.sin(np.pi / 8)
    assert any(f"open by {gap:.3f} mm" in note for note in view.notes)


def test_kinked_joint_still_draws_its_midsurface_and_develops():
    p, part, sheet, _ = _kinked()
    strip = _strip_of(p, sheet)
    assert strip.normal_jump == pytest.approx(2 * np.sin(np.pi / 8), rel=1e-9)
    views = assembly_views(p, part, representation="midsurface_2d")
    assert [len(v.paths) > 0 for v in views] == [True] * 4
    assert strip.area == pytest.approx((2. + 1.) * np.pi / 4 * H, rel=1e-12)


def test_tangent_strip_has_no_normal_jump():
    p, part, sheet, _ = _sheet(_patches(SCROLL))
    assert _strip_of(p, sheet).normal_jump <= 1e-9


# -- 4 and 5: overrides --------------------------------------------------------------------------

def _tee():
    from tests.test_drawing_thickness_side import _Conformal
    m = _Conformal()
    return m, m.flange(), m.web("web", 0, .5)


def test_call_time_override_naming_no_sheet_is_refused(tmp_path):
    from anyfem.application.thickness_side import resolve_sides, resolve_thickness_sides
    m, flange, web = _tee()
    for bad in ({"flnage": "back"}, {9999: "back"}, {"9999": "front"}):
        with pytest.raises(ValueError, match="names no Sheet"):
            resolve_sides(m.p, bad)
        with pytest.raises(ValueError, match="names no Sheet"):
            resolve_thickness_sides(m.p, m.part, bad)
    from anyfem.application.plate_drawings import main
    from anyfem.io.project_file import save_project
    source = save_project(m.p, tmp_path / "tee.anyfem")
    with pytest.raises((SystemExit, ValueError)):
        main([str(source), str(tmp_path / "bad"), "--part", str(m.part), "--thickness-side", "flnage=back"])
    assert not (tmp_path / "bad").exists() or not any((tmp_path / "bad").iterdir())


def test_override_follows_sheet_identity_not_its_name():
    from anyfem.application.thickness_side import resolve_thickness_sides, set_override
    m, flange, web = _tee()
    set_override(m.p, flange, "back")
    assert resolve_thickness_sides(m.p, m.part)[flange][:2] == ("back", "manual")
    m.g.remove_sheet(flange)
    again = m.sheet("flange", [(0, 2, 0), (1, 2, 0), (1, 3, 0), (0, 3, 0)])
    assert again != flange
    assert resolve_thickness_sides(m.p, m.part)[again][1] == "auto"


def test_saved_overrides_are_sheet_ids_and_name_records_bind_at_load(tmp_path):
    from anyfem.application.thickness_side import set_override
    from anyfem.io.project_file import ProjectFileError, load_project, project_from_dict, project_to_dict
    m, flange, web = _tee()
    set_override(m.p, flange, "back")
    data = project_to_dict(m.p)
    assert data["drawing_thickness_sides"] == [{"sheet": flange, "side": "back"}]
    legacy = dict(data, drawing_thickness_sides=[{"sheet": "web", "side": "front"}])
    assert project_from_dict(legacy).drawing_thickness_sides == {web: "front"}
    for bad in ("nosuch", 9999):
        with pytest.raises(ProjectFileError, match="names no Sheet"):
            project_from_dict(dict(data, drawing_thickness_sides=[{"sheet": bad, "side": "front"}]))


def test_automatic_side_is_centred_when_a_neighbour_maps_ambiguously():
    from anygeometry import OrientedEdge
    from anyfem.application.thickness_side import resolve_thickness_sides
    patches = _patches(SCROLL)
    p, part, sheet, _ = _sheet(patches)
    g = p.geometry
    # A bracket from the strip's start corner (1, 0, 0) to the point between the two turns.
    corner = next(v for v, vertex in g.vertices.items() if np.allclose(vertex.position, (1, 0, 0)))
    a, b = g.add_point(*(1.5 * _dir(np.pi / 2)), 0.), g.add_point(*(1.5 * _dir(np.pi / 2)), H)
    bracket = g.add_face_from_loop([OrientedEdge(g.add_line(*pair), True)
                                    for pair in ((corner, a), (a, b), (b, corner))])
    g.add_sheet((bracket,), part_id=part, name="bracket")
    p.assign_plate(bracket, "12 mm")
    side, source, reason = resolve_thickness_sides(p, part)[sheet]
    assert (side, source) == ("centre", "auto") and "ambiguous" in reason


def test_side_by_side_patches_on_one_cylinder_then_another_cylinder_validate():
    # Two patches on one R = 1 cylinder, then a tangent R = 1.5 patch (the Pl4253 pattern of
    # same-cylinder neighbours followed by pairs on different cylinders).
    p, part, sheet, _ = _sheet(_patches(((1., 0., np.pi/2), (1., np.pi/2, np.pi), (1.5, np.pi, 2*np.pi))))
    assert _strip_of(p, sheet).area == pytest.approx((1. * np.pi + 1.5 * np.pi) * H, rel=1e-12)
