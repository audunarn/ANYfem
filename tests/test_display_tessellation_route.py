"""N5: trimmed cylinder and cone faces are drawn from ANYgeometry's display tessellation.

A face whose support is exactly a ``Cylinder`` or ``Cone``, without an explicit parameterization
and with corners, used to be drawn as its support's whole parameter box (the floating tripod's
skins: full-turn supports that overrun the face).  Such a face is now drawn from
``anygeometry.display_tessellation.face_display_mesh``; a typed refusal keeps the old route.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from anygeometry import display_tessellation as dt
from anygeometry.model import GeometryModel
from anygeometry.surfaces import Cone, Cylinder

from anyfem.application.tripod_example import floating_tripod
from anyfem.ui import scene as scene_module
from anyfem.ui.scene import (
    COLOR_WET,
    build_attribute_overlay,
    build_geometry_scene,
    DISPLAY_TRIM_ANGLE,
    face_display_polygons,
    trimmed_face_meshes,
)


def _routed(geometry):
    return sorted(
        face_id for face_id, face in geometry.faces.items()
        if type(face.surface) in (Cylinder, Cone) and face.parameterization is None and face.corners
    )


@pytest.fixture(scope="module")
def tripod():
    return floating_tripod()


def _axial_range(geometry, face_id):
    face = geometry.faces[face_id]
    support = face.surface
    points = np.asarray([
        geometry.evaluate_edge_many(item.edge, np.linspace(0.0, 1.0, 33))
        for loop in (face.loop, *face.holes) for item in loop
    ]).reshape(-1, 3)
    axial = (points - support.origin) @ support.axis
    return float(axial.min()), float(axial.max()), support


def _area(polygons):
    total = 0.0
    for polygon in polygons:
        polygon = np.asarray(polygon, dtype=float)
        for index in range(1, len(polygon) - 1):
            total += 0.5 * float(np.linalg.norm(np.cross(polygon[index] - polygon[0], polygon[index + 1] - polygon[0])))
    return total


def _skin_patches(scene, face_ids):
    found = {}
    for patch in scene.faces:
        for index, polygon in enumerate(patch.polygons):
            for owner in patch.owners_for_polygon(index):
                if getattr(owner, "kind", None) == "face" and owner.id in face_ids:
                    found.setdefault(owner.id, []).append(polygon)
    return found


def test_one_trim_angle_for_every_scene():
    """Owner 2026-11-28: one trim angle of 22.5 degrees for the full and the interaction scene."""

    assert DISPLAY_TRIM_ANGLE == math.radians(22.5)
    assert not hasattr(scene_module, "display_trim_angle")


def test_every_tripod_skin_is_routed_and_accepted(tripod):
    geometry = tripod.geometry
    routed = _routed(geometry)
    assert len(routed) == 18
    meshes = trimmed_face_meshes(geometry)
    assert sorted(meshes.meshes) == routed
    for face_id, mesh in meshes.meshes.items():
        assert isinstance(mesh, dt.FaceDisplayMesh) and mesh.face_id == face_id
        assert len(mesh.triangles) > 0


def test_tripod_skins_are_drawn_inside_their_trim(tripod):
    geometry = tripod.geometry
    for face_id in _routed(geometry):
        low, high, support = _axial_range(geometry, face_id)
        scale = max(abs(low), abs(high), float(support.radius))
        polygons = face_display_polygons(geometry, face_id)
        points = np.concatenate([np.asarray(p, dtype=float) for p in polygons])
        axial = (points - support.origin) @ support.axis
        assert axial.min() >= low - 1e-9 * scale and axial.max() <= high + 1e-9 * scale, face_id
        # a half skin: at most half a turn of the support over the trim's axial length
        assert _area(polygons) <= 1.001 * math.pi * float(support.radius) * (high - low), face_id
        assert _area(polygons) >= 0.5 * math.pi * float(support.radius) * (high - low), face_id


def test_geometry_scene_draws_skins_from_the_trim(tripod):
    geometry = tripod.geometry
    routed = set(_routed(geometry))
    scene = build_geometry_scene(tripod)
    patches = _skin_patches(scene, routed)
    assert set(patches) == routed
    for face_id, polygons in patches.items():
        low, high, support = _axial_range(geometry, face_id)
        points = np.concatenate([np.asarray(p, dtype=float) for p in polygons])
        axial = (points - support.origin) @ support.axis
        scale = max(abs(low), abs(high), float(support.radius))
        assert axial.min() >= low - 1e-9 * scale and axial.max() <= high + 1e-9 * scale, face_id
    # the triangulation is one drawn face: its outline is the face's, not every triangle's
    for patch in scene.faces:
        if patch.ref is not None and getattr(patch.ref, "kind", None) == "face" and patch.ref.id in routed:
            assert patch.outline_groups == [patch.ref.id] * len(patch.polygons)


def test_scene_uses_given_trimmed_meshes_without_recomputing(tripod, monkeypatch):
    geometry = tripod.geometry
    meshes = trimmed_face_meshes(geometry)
    calls = []
    original = dt.face_display_mesh

    def counting(model, face_id, tol):
        calls.append(face_id)
        return original(model, face_id, tol)

    monkeypatch.setattr(dt, "face_display_mesh", counting)
    shared = build_geometry_scene(tripod, trimmed_faces=meshes)
    assert calls == []
    own = build_geometry_scene(tripod)
    assert sorted(calls) == _routed(geometry)
    for a, b in zip(shared.faces, own.faces):
        assert len(a.polygons) == len(b.polygons)
        for p, q in zip(a.polygons, b.polygons):
            assert np.array_equal(np.asarray(p), np.asarray(q))


def test_wet_outline_of_a_routed_face_follows_its_boundary_without_cross(tripod):
    geometry = tripod.geometry
    wet = sorted({surface.ref.id for surface in tripod.wet_surfaces if surface.ref.kind == "face"})
    routed = set(_routed(geometry))
    assert wet and set(wet) <= routed
    overlay = build_attribute_overlay(tripod, show_supports=False, show_masses=False, show_imperfections=False)
    outlines = [line for line in overlay.lines if line.color == COLOR_WET]
    # one closed boundary per wet face, no cross
    assert len(outlines) == len(wet)
    ranges = [_axial_range(geometry, face_id) for face_id in wet]
    for line in outlines:
        points = np.asarray(line.points, dtype=float)
        assert np.array_equal(points[0], points[-1])
        matched = False
        for low, high, support in ranges:
            axial = (points - support.origin) @ support.axis
            radial = np.linalg.norm((points - support.origin) - np.outer(axial, support.axis), axis=1)
            scale = max(abs(low), abs(high), float(support.radius))
            if (axial.min() >= low - 1e-9 * scale and axial.max() <= high + 1e-9 * scale
                    and np.allclose(radial, float(support.radius), rtol=1e-9, atol=1e-9 * scale)):
                matched = True
        assert matched


def _cornered_cylinder(radius=2, height=6):
    """A quarter-turn cylindrical plate (two arcs, two lines) on an overrunning int-built support."""

    g = GeometryModel()
    r = radius
    p = [g.add_point(r, 0.0, 1.0), g.add_point(r * math.sqrt(0.5), r * math.sqrt(0.5), 1.0), g.add_point(0.0, r, 1.0),
         g.add_point(r, 0.0, 3.0), g.add_point(r * math.sqrt(0.5), r * math.sqrt(0.5), 3.0), g.add_point(0.0, r, 3.0)]
    bottom = g.add_arc(p[0], p[1], p[2])
    top = g.add_arc(p[3], p[4], p[5])
    side0 = g.add_line(p[0], p[3])
    side1 = g.add_line(p[2], p[5])
    support = Cylinder(origin=(0, 0, 0), axis=(0, 0, 1), radial_direction=(1, 0, 0), radius=radius,
                       height=height, start_angle=-1, sweep_angle=4)
    face = g.add_face((bottom, side1, top, side0), corners=(0, 1, 2, 3), surface=support)
    return g, face


def test_int_built_support_is_accepted_because_the_constructor_coerces():
    g, face = _cornered_cylinder()
    support = g.faces[face].surface
    for name in ("radius", "height", "start_angle", "sweep_angle"):
        assert type(getattr(support, name)) is float
    for name in ("origin", "axis", "radial_direction"):
        value = getattr(support, name)
        assert type(value) is np.ndarray and value.dtype == np.float64 and value.shape == (3,)
    assert face in trimmed_face_meshes(g).meshes
    points = np.concatenate([np.asarray(q, dtype=float) for q in face_display_polygons(g, face)])
    assert points[:, 2].min() >= 1.0 - 1e-12 and points[:, 2].max() <= 3.0 + 1e-12


def test_refused_face_keeps_the_old_route():
    g, face = _cornered_cylinder()
    old_grid = scene_module._face_grid(g, face, np.linspace(0.0, 1.0, 9))
    # a field outside the lift's domain: N3 refuses it, typed (OUT_OF_DOMAIN)
    object.__setattr__(g.faces[face].surface, "radius", 2)
    with pytest.raises(dt.DisplayTrimRefused):
        dt.face_display_mesh(g, face, dt.DisplayTolerance(DISPLAY_TRIM_ANGLE))
    assert face not in trimmed_face_meshes(g).meshes
    polygons = face_display_polygons(g, face)
    assert len(polygons) == 64
    assert np.array_equal(polygons[0], np.array([old_grid[0, 0], old_grid[1, 0], old_grid[1, 1], old_grid[0, 1]]))


def test_other_errors_are_not_swallowed(monkeypatch):
    g, face = _cornered_cylinder()

    def broken(model, face_id, tol):
        raise RuntimeError("not a refusal")

    monkeypatch.setattr(dt, "face_display_mesh", broken)
    with pytest.raises(RuntimeError, match="not a refusal"):
        face_display_polygons(g, face)


@pytest.mark.parametrize("segments", [8, 400])
def test_generated_cylinder_segments_keep_the_old_route(segments):
    """A segment whose support box is the face (the cylinder generator) is drawn exactly as before."""

    from anyfem import Project
    from anyfem import commands as cmd

    project = Project()
    cmd.CommandStack(project).run(cmd.AddCylinder(1.0, 2.0, circumferential_segments=segments))
    geometry = project.geometry
    assert len(_routed(geometry)) == segments
    assert dict(trimmed_face_meshes(geometry).meshes) == {}
    for face_id in _routed(geometry)[:16]:
        new = face_display_polygons(geometry, face_id)
        old = scene_module._support_face_display_polygons(geometry, face_id)
        assert len(new) == len(old)
        assert all(np.array_equal(a, b) for a, b in zip(new, old))


def test_wet_overlay_uses_the_given_trimmed_meshes(tripod, monkeypatch):
    geometry = tripod.geometry
    meshes = trimmed_face_meshes(geometry)
    calls = []
    original = dt.face_display_mesh

    def counting(model, face_id, tol):
        calls.append(face_id)
        return original(model, face_id, tol)

    monkeypatch.setattr(dt, "face_display_mesh", counting)
    options = dict(show_supports=False, show_masses=False, show_imperfections=False)
    shared = build_attribute_overlay(tripod, trimmed_faces=meshes, **options)
    assert calls == []
    own = build_attribute_overlay(tripod, **options)
    assert calls
    wet_shared = [line.points for line in shared.lines if line.color == COLOR_WET]
    wet_own = [line.points for line in own.lines if line.color == COLOR_WET]
    assert len(wet_shared) == len(wet_own) > 0
    assert all(np.array_equal(a, b) for a, b in zip(wet_shared, wet_own))


# --- Codex N5 round 1 -------------------------------------------------------------------------------------------


def _ring_point(g, radius, angle, z):
    return g.add_point(radius * math.cos(angle), radius * math.sin(angle), z)


def _notched_quarter_cylinder():
    """A quarter-turn plate whose support box is exactly its corners, but whose top side has a notch."""

    g = GeometryModel()
    r, q = 2.0, math.pi / 2
    p0, p2 = _ring_point(g, r, 0.0, 1.0), _ring_point(g, r, q, 1.0)
    p3, p5 = _ring_point(g, r, 0.0, 3.0), _ring_point(g, r, q, 3.0)
    bottom = g.add_arc(p0, _ring_point(g, r, q / 2, 1.0), p2)
    side1 = g.add_line(p2, p5)
    a, b = _ring_point(g, r, 3 * math.pi / 8, 3.0), _ring_point(g, r, 3 * math.pi / 8, 2.5)
    c, d = _ring_point(g, r, math.pi / 8, 2.5), _ring_point(g, r, math.pi / 8, 3.0)
    top1 = g.add_arc(p5, _ring_point(g, r, 7 * math.pi / 16, 3.0), a)
    down = g.add_line(a, b)
    floor = g.add_arc(b, _ring_point(g, r, math.pi / 4, 2.5), c)
    up = g.add_line(c, d)
    top2 = g.add_arc(d, _ring_point(g, r, math.pi / 16, 3.0), p3)
    side0 = g.add_line(p3, p0)
    support = Cylinder(origin=(0., 0., 1.), axis=(0., 0., 1.), radial_direction=(1., 0., 0.), radius=r, height=2.,
                       start_angle=0., sweep_angle=q)
    face = g.add_face((bottom, side1, top1, down, floor, up, top2, side0), corners=(0, 1, 2, 7), surface=support)
    return g, face


def test_notched_face_sharing_the_box_corners_is_drawn_from_its_trim():
    g, face = _notched_quarter_cylinder()
    assert face in trimmed_face_meshes(g).meshes
    polygons = face_display_polygons(g, face)
    full, notch = 2.0 * (math.pi / 2) * 2.0, 2.0 * (math.pi / 4) * 0.5
    area = _area(polygons)
    assert area < full - 0.9 * notch
    assert area > 0.98 * (full - notch)
    points = np.concatenate(polygons)
    angle = np.arctan2(points[:, 1], points[:, 0])
    inside_notch = (angle > math.pi / 8 + 1e-9) & (angle < 3 * math.pi / 8 - 1e-9) & (points[:, 2] > 2.5 + 1e-9)
    assert not inside_notch.any()


def _quarter_on(z, height):
    g = GeometryModel()
    r, q = 2.0, math.pi / 2
    p0, p2 = _ring_point(g, r, 0.0, z), _ring_point(g, r, q, z)
    p3, p5 = _ring_point(g, r, 0.0, z + 2.0), _ring_point(g, r, q, z + 2.0)
    bottom = g.add_arc(p0, _ring_point(g, r, q / 2, z), p2)
    top = g.add_arc(p3, _ring_point(g, r, q / 2, z + 2.0), p5)
    side0, side1 = g.add_line(p0, p3), g.add_line(p2, p5)
    support = Cylinder(origin=(0., 0., z), axis=(0., 0., 1.), radial_direction=(1., 0., 0.), radius=r, height=height,
                       start_angle=0., sweep_angle=q)
    face = g.add_face((bottom, side1, top, side0), corners=(0, 1, 2, 3), surface=support)
    return g, face


def test_far_away_face_on_a_slightly_longer_support_is_drawn_from_its_trim():
    """A local tolerance: 5 cm of overrun at z = 1e8 m is not 'the box'."""

    z = 1.0e8
    g, face = _quarter_on(z, 2.05)
    assert face in trimmed_face_meshes(g).meshes
    points = np.concatenate(face_display_polygons(g, face))
    assert points[:, 2].max() <= z + 2.0 + 1e-6


@pytest.mark.parametrize("z", [0.0, 1.0e8])
def test_a_hand_built_exact_box_face_is_drawn_from_its_trim(z):
    """Contract B: only generator-owned box faces keep the old route; a hand-built one is drawn from its trim."""

    g, face = _quarter_on(z, 2.0)
    assert face in trimmed_face_meshes(g).meshes
    points = np.concatenate(face_display_polygons(g, face))
    assert points[:, 2].min() >= z - 1e-6 and points[:, 2].max() <= z + 2.0 + 1e-6


def test_smooth_feature_face_takes_the_trimmed_route_first(monkeypatch):
    from types import SimpleNamespace

    from anygeometry.entities import EntityRef
    from anyfem import Project

    # an extrusion-owned face that is not structurally a box (a notch): drawn from its trim, before the
    # smooth-feature route (an owned box face keeps the old route: contract B)
    g, face = _notched_quarter_cylinder()
    project = Project()
    project.geometry = g
    record = SimpleNamespace(kind="geometry.extrude", feature_id=7, outputs={})
    history = SimpleNamespace(records=[record])
    real = type(g).features
    monkeypatch.setattr(type(g), "features", property(lambda self: history if self is g else real.fget(self)))
    owners = {EntityRef("face", face): 7}
    meshes = trimmed_face_meshes(g, entity_owners=owners)
    assert face in meshes.meshes
    calls = []
    original = dt.face_display_mesh
    monkeypatch.setattr(dt, "face_display_mesh", lambda m, f, t: calls.append(f) or original(m, f, t))
    scene = build_geometry_scene(project, entity_owners=owners, exposed_feature_ids={7}, trimmed_faces=meshes)
    assert calls == []
    polygons = _skin_patches(scene, {face})[face]
    points = np.concatenate([np.asarray(p, dtype=float) for p in polygons])
    assert points[:, 2].min() >= 1.0 - 1e-12 and points[:, 2].max() <= 3.0 + 1e-12


def test_collapsed_patch_keeps_one_outline_per_trimmed_face_and_per_other_polygon(tripod):
    from anygeometry.entities import EntityRef
    from anygeometry.surfaces import Plane

    geometry = tripod.geometry
    skin = _routed(geometry)[0]
    plane = next(f for f, face in sorted(geometry.faces.items())
                 if type(face.surface) is Plane and len(face_display_polygons(geometry, f, 2)) > 1)
    owners = {EntityRef("face", skin): 99, EntityRef("face", plane): 99}
    scene = build_geometry_scene(tripod, entity_owners=owners, exposed_feature_ids=())
    patch = next(p for p in scene.faces if p.owners and set(p.owners) == set(owners))
    groups = patch.outline_groups
    assert groups is not None and len(groups) == len(patch.polygons)
    skin_owner, plane_owner = (EntityRef("face", skin),), (EntityRef("face", plane),)
    skin_labels = {groups[i] for i in range(len(patch.polygons)) if patch.owners_for_polygon(i) == skin_owner}
    other_labels = [groups[i] for i in range(len(patch.polygons)) if patch.owners_for_polygon(i) == plane_owner]
    assert skin_labels == {skin}
    assert other_labels and len(set(other_labels)) == len(other_labels) and skin not in other_labels


def _reversed_cornered_cylinder():
    """The same plate as _cornered_cylinder, its loop running the other way."""

    g = GeometryModel()
    r = 2
    p = [g.add_point(r, 0.0, 1.0), g.add_point(r * math.sqrt(0.5), r * math.sqrt(0.5), 1.0), g.add_point(0.0, r, 1.0),
         g.add_point(r, 0.0, 3.0), g.add_point(r * math.sqrt(0.5), r * math.sqrt(0.5), 3.0), g.add_point(0.0, r, 3.0)]
    bottom = g.add_arc(p[0], p[1], p[2])
    top = g.add_arc(p[3], p[4], p[5])
    side0 = g.add_line(p[0], p[3])
    side1 = g.add_line(p[2], p[5])
    support = Cylinder(origin=(0, 0, 0), axis=(0, 0, 1), radial_direction=(1, 0, 0), radius=r,
                       height=6, start_angle=-1, sweep_angle=4)
    face = g.add_face((side0, top, side1, bottom), corners=(0, 1, 2, 3), surface=support)
    return g, face


@pytest.mark.parametrize("make", [_cornered_cylinder, _reversed_cornered_cylinder])
def test_trimmed_triangles_face_the_support_normal_whatever_the_loop_direction(make):
    g, face = make()
    assert face in trimmed_face_meshes(g).meshes
    support = g.faces[face].surface
    reference = g.face_normal(face, 0.5, 0.5)
    centre = g.face_point(face, 0.5, 0.5) - support.origin
    centre_radial = centre - float(centre @ support.axis) * support.axis
    sign = np.sign(float(reference @ centre_radial))
    assert sign != 0
    for polygon in face_display_polygons(g, face):
        normal = np.cross(polygon[1] - polygon[0], polygon[2] - polygon[0])
        middle = polygon.mean(axis=0) - support.origin
        radial = middle - float(middle @ support.axis) * support.axis
        assert np.sign(float(normal @ radial)) == sign


def _spline_sided_cylinder():
    """A valid face ANYgeometry refuses, typed: one side is a spline (EDGE_UNSUPPORTED)."""

    g = GeometryModel()
    r = 2.0
    p0, p2 = _ring_point(g, r, 0.0, 1.0), _ring_point(g, r, math.pi / 2, 1.0)
    p3, p5 = _ring_point(g, r, 0.0, 3.0), _ring_point(g, r, math.pi / 2, 3.0)
    bottom = g.add_arc(p0, _ring_point(g, r, math.pi / 4, 1.0), p2)
    top = g.add_arc(p3, _ring_point(g, r, math.pi / 4, 3.0), p5)
    side0 = g.add_line(p0, p3)
    middle = _ring_point(g, r, math.pi / 2, 2.0)
    side1 = g.add_spline(p2, (middle,), p5)
    support = Cylinder(origin=(0., 0., 0.), axis=(0., 0., 1.), radial_direction=(1., 0., 0.), radius=r, height=6.,
                       start_angle=-1., sweep_angle=4.)
    face = g.add_face((bottom, side1, top, side0), corners=(0, 1, 2, 3), surface=support)
    return g, face


def test_typed_refusal_of_a_valid_face_keeps_the_complete_old_route():
    from anyfem import Project

    g, face = _spline_sided_cylinder()
    with pytest.raises(dt.DisplayTrimRefused):
        dt.face_display_mesh(g, face, dt.DisplayTolerance(DISPLAY_TRIM_ANGLE))
    assert dict(trimmed_face_meshes(g).meshes) == {}
    old = scene_module._support_face_display_polygons(g, face)
    new = face_display_polygons(g, face)
    assert len(new) == len(old) and all(np.array_equal(a, b) for a, b in zip(new, old))
    project = Project()
    project.geometry = g
    for extra in ({}, {"trimmed_faces": trimmed_face_meshes(g)}):
        polygons = _skin_patches(build_geometry_scene(project, **extra), {face})[face]
        assert len(polygons) == len(old) and all(np.array_equal(a, b) for a, b in zip(polygons, old))


# --- Codex N5 round 2 -------------------------------------------------------------------------------------------


def _thin_plate(radius, height, face_sweep, support_sweep):
    """A narrow plate spanning ``face_sweep`` of a support of ``support_sweep``, both from angle 0."""

    g = GeometryModel()
    p0, p2 = _ring_point(g, radius, 0.0, 0.0), _ring_point(g, radius, face_sweep, 0.0)
    p3, p5 = _ring_point(g, radius, 0.0, height), _ring_point(g, radius, face_sweep, height)
    bottom = g.add_arc(p0, _ring_point(g, radius, face_sweep / 2, 0.0), p2)
    top = g.add_arc(p3, _ring_point(g, radius, face_sweep / 2, height), p5)
    side0, side1 = g.add_line(p0, p3), g.add_line(p2, p5)
    support = Cylinder(origin=(0., 0., 0.), axis=(0., 0., 1.), radial_direction=(1., 0., 0.), radius=radius,
                       height=height, start_angle=0., sweep_angle=support_sweep)
    face = g.add_face((bottom, side1, top, side0), corners=(0, 1, 2, 3), surface=support)
    return g, face


def test_a_long_axis_does_not_widen_the_angular_box_tolerance():
    """1 mm radius, 100 m tall, 0.40 mrad of a 0.49 mrad support: the overrun is 22 % of the span."""

    g, face = _thin_plate(1.0e-3, 100.0, 0.40e-3, 0.49e-3)
    assert scene_module._routes_to_trim_display(g, face)
    meshes = trimmed_face_meshes(g).meshes
    if face in meshes:
        points = np.concatenate(face_display_polygons(g, face))
        assert float(np.arctan2(points[:, 1], points[:, 0]).max()) <= 0.40e-3 * (1 + 1e-6)


def test_a_hand_built_tiny_box_is_routed():
    g, face = _thin_plate(1.0e-3, 100.0, 0.05e-3, 0.05e-3)
    assert scene_module._routes_to_trim_display(g, face)


def _signed_plate(sweep_sign, height_sign):
    """The quarter plate of _cornered_cylinder on a support whose sweep and/or height run backwards."""

    g = GeometryModel()
    r = 2.0
    p = [g.add_point(r, 0.0, 1.0), g.add_point(r * math.sqrt(0.5), r * math.sqrt(0.5), 1.0), g.add_point(0.0, r, 1.0),
         g.add_point(r, 0.0, 3.0), g.add_point(r * math.sqrt(0.5), r * math.sqrt(0.5), 3.0), g.add_point(0.0, r, 3.0)]
    bottom, top = g.add_arc(p[0], p[1], p[2]), g.add_arc(p[3], p[4], p[5])
    side0, side1 = g.add_line(p[0], p[3]), g.add_line(p[2], p[5])
    start = -1.0 if sweep_sign > 0 else math.pi / 2 + 1.0
    origin_z = 0.0 if height_sign > 0 else 6.0
    support = Cylinder(origin=(0., 0., origin_z), axis=(0., 0., 1.), radial_direction=(1., 0., 0.), radius=r,
                       height=6.0 * height_sign, start_angle=start, sweep_angle=4.0 * sweep_sign)
    face = g.add_face((bottom, side1, top, side0), corners=(0, 1, 2, 3), surface=support)
    return g, face


@pytest.mark.parametrize("signs", [(1, 1), (-1, 1), (1, -1), (-1, -1)])
def test_trimmed_triangles_follow_face_normal_for_signed_supports(signs):
    g, face = _signed_plate(*signs)
    assert face in trimmed_face_meshes(g).meshes
    reference = g.face_normal(face, 0.5, 0.5)
    centre = g.face_point(face, 0.5, 0.5)
    outward = np.array([centre[0], centre[1], 0.0])
    sign = np.sign(float(reference @ outward))
    assert sign != 0
    for polygon in face_display_polygons(g, face):
        normal = np.cross(polygon[1] - polygon[0], polygon[2] - polygon[0])
        middle = polygon.mean(axis=0)
        assert np.sign(float(normal @ np.array([middle[0], middle[1], 0.0]))) == sign


def test_a_map_answers_only_for_the_faces_it_covers(tripod):
    """Playback passes a map of some faces; the overlay resolves every other face itself."""

    geometry = tripod.geometry
    wet = set(scene_module.wet_display_face_ids(tripod))
    other = [f for f in _routed(geometry) if f not in wet][:1]
    partial = trimmed_face_meshes(geometry, face_ids=other)
    options = dict(show_supports=False, show_masses=False, show_imperfections=False)
    own = build_attribute_overlay(tripod, **options)
    shared = build_attribute_overlay(tripod, trimmed_faces=partial, **options)
    a = [line.points for line in own.lines if line.color == COLOR_WET]
    b = [line.points for line in shared.lines if line.color == COLOR_WET]
    assert len(a) == len(b) == len(wet) and all(np.array_equal(x, y) for x, y in zip(a, b))


def test_a_map_of_an_older_revision_or_another_geometry_is_not_used(monkeypatch):
    from anyfem import Project

    g, face = _cornered_cylinder()
    project = Project()
    project.geometry = g
    stale = trimmed_face_meshes(g)
    other_geometry, _ = _cornered_cylinder()
    foreign = trimmed_face_meshes(other_geometry)
    g.add_point(10.0, 10.0, 10.0)                 # a new revision
    calls = []
    original = dt.face_display_mesh
    monkeypatch.setattr(dt, "face_display_mesh", lambda m, f, t: calls.append(f) or original(m, f, t))
    build_geometry_scene(project, trimmed_faces=stale)
    assert calls == [face]
    calls.clear()
    fresh = trimmed_face_meshes(g)
    calls.clear()
    build_geometry_scene(project, trimmed_faces=fresh)
    assert calls == []
    build_geometry_scene(project, trimmed_faces=foreign)
    assert calls == [face]


# --- contract B (owner 2026-11-28) and the angle-bound map ----------------------------------------------------


def _generated_cylinder(segments=8):
    from anyfem import Project
    from anyfem import commands as cmd

    project = Project()
    cmd.CommandStack(project).run(cmd.AddCylinder(1.0, 2.0, circumferential_segments=segments))
    return project


def test_generated_cylinder_scene_is_unchanged_and_tessellates_nothing(monkeypatch):
    project = _generated_cylinder(8)
    calls = []
    original = dt.face_display_mesh
    monkeypatch.setattr(dt, "face_display_mesh", lambda m, f, t: calls.append(f) or original(m, f, t))
    for exposed in ((), tuple(r.feature_id for r in project.geometry.features.records)):
        scene = build_geometry_scene(project, exposed_feature_ids=exposed)
        assert scene.faces
    assert calls == []


def test_a_generator_face_that_is_no_longer_a_box_is_routed(monkeypatch):
    """A generator-owned face whose boundary is not one straight/arc box (a notch) is drawn from its trim."""

    from types import SimpleNamespace

    from anygeometry.entities import EntityRef

    g, face = _notched_quarter_cylinder()
    history = SimpleNamespace(records=[SimpleNamespace(kind="generator.cylinder", feature_id=5, outputs={})])
    real = type(g).features
    monkeypatch.setattr(type(g), "features", property(lambda self: history if self is g else real.fget(self)))
    owners = {EntityRef("face", face): 5}
    assert scene_module._trim_display_faces(g, [face], entity_owners=owners) == [face]
    assert face in trimmed_face_meshes(g, entity_owners=owners).meshes


def test_ownership_alone_is_not_enough(monkeypatch):
    """A generator-owned face whose sides are not one straight/arc box (a spline side) is routed."""

    g, face = _spline_sided_cylinder()
    from types import SimpleNamespace

    from anygeometry.entities import EntityRef

    record = SimpleNamespace(kind="generator.cylinder", feature_id=5, outputs={})
    history = SimpleNamespace(records=[record])
    real = type(g).features
    monkeypatch.setattr(type(g), "features", property(lambda self: history if self is g else real.fget(self)))
    owners = {EntityRef("face", face): 5}
    assert scene_module._trim_display_faces(g, [face], entity_owners=owners) == [face]
    g2, box = _quarter_on(0.0, 2.0)
    monkeypatch.setattr(type(g2), "features", property(lambda self: history if self is g2 else real.fget(self)))
    assert scene_module._trim_display_faces(g2, [box], entity_owners={EntityRef("face", box): 5}) == []
    assert scene_module._trim_display_faces(g2, [box], entity_owners={}) == [box]


def test_a_map_at_another_angle_is_not_reused(monkeypatch):
    from anyfem import Project

    g, face = _cornered_cylinder()
    project = Project()
    project.geometry = g
    other = trimmed_face_meshes(g, angle=math.radians(7.5))
    assert other.angle == math.radians(7.5)
    calls = []
    original = dt.face_display_mesh
    monkeypatch.setattr(dt, "face_display_mesh", lambda m, f, t: calls.append((f, t.angle)) or original(m, f, t))
    build_geometry_scene(project, trimmed_faces=other)
    assert calls == [(face, DISPLAY_TRIM_ANGLE)]
    calls.clear()
    same = trimmed_face_meshes(g)
    calls.clear()
    build_geometry_scene(project, trimmed_faces=same)
    build_attribute_overlay(project, trimmed_faces=same)
    assert calls == []


def _arc_extrusion(recorded):
    from anyfem import Project
    from anyfem import commands as cmd

    project = Project()
    geometry = project.geometry
    start = geometry.add_point(2.0, 0.0, 0.0)
    via = geometry.add_point(math.sqrt(2.0), math.sqrt(2.0), 0.0)
    end = geometry.add_point(0.0, 2.0, 0.0)
    arc = geometry.add_arc(start, via, end)
    if recorded:
        cmd.CommandStack(project).run(cmd.Extrude([arc], (0.0, 0.0, 3.0)))
        (face,) = geometry.faces
    else:
        face = geometry.extrude([arc], (0.0, 0.0, 3.0))[0]
    return project, face


def test_a_raw_model_extrusion_is_hand_built_and_drawn_from_its_trim():
    """Owner 2026-11-28: GeometryModel.extrude records no feature, so its face has no provenance (contract B)."""

    project, face = _arc_extrusion(recorded=False)
    geometry = project.geometry
    assert not geometry.features.records
    assert scene_module._routes_to_trim_display(geometry, face)
    assert face in trimmed_face_meshes(geometry).meshes
    polygons = face_display_polygons(geometry, face)
    assert len(polygons) == 8                    # was 8 x 8 on the old route; 4 segments of 22.5 degrees, 2 triangles each
    points = np.concatenate(polygons)
    assert points[:, 2].min() >= -1e-12 and points[:, 2].max() <= 3.0 + 1e-12


def test_an_extruded_arc_keeps_the_old_route(monkeypatch):
    """Owner 2026-11-28: geometry.extrude / geometry.sketch.extrude are provenance like generator.*."""

    project, face = _arc_extrusion(recorded=True)
    geometry = project.geometry
    assert type(geometry.faces[face].surface) is Cylinder and geometry.faces[face].corners
    assert not scene_module._routes_to_trim_display(geometry, face)
    calls = []
    original = dt.face_display_mesh
    monkeypatch.setattr(dt, "face_display_mesh", lambda m, f, t: calls.append(f) or original(m, f, t))
    new = face_display_polygons(geometry, face)
    old = scene_module._support_face_display_polygons(geometry, face)
    assert len(new) == len(old) and all(np.array_equal(a, b) for a, b in zip(new, old))
    build_geometry_scene(project)
    assert calls == []
