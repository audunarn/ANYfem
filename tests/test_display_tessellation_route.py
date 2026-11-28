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
    display_trim_angle,
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


def test_display_trim_angle_follows_divisions():
    for divisions in (1, 2, 3, 8, 16, 64):
        angle = display_trim_angle(divisions)
        assert type(angle) is float and 0.0 < angle <= math.radians(22.5)


def test_every_tripod_skin_is_routed_and_accepted(tripod):
    geometry = tripod.geometry
    routed = _routed(geometry)
    assert len(routed) == 18
    meshes = trimmed_face_meshes(geometry, display_trim_angle(8))
    assert sorted(meshes) == routed
    for face_id, mesh in meshes.items():
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
    meshes = trimmed_face_meshes(geometry, display_trim_angle(8))
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
    assert face in trimmed_face_meshes(g, display_trim_angle(8))
    points = np.concatenate([np.asarray(q, dtype=float) for q in face_display_polygons(g, face)])
    assert points[:, 2].min() >= 1.0 - 1e-12 and points[:, 2].max() <= 3.0 + 1e-12


def test_refused_face_keeps_the_old_route():
    g, face = _cornered_cylinder()
    old_grid = scene_module._face_grid(g, face, np.linspace(0.0, 1.0, 9))
    # a field outside the lift's domain: N3 refuses it, typed (OUT_OF_DOMAIN)
    object.__setattr__(g.faces[face].surface, "radius", 2)
    with pytest.raises(dt.DisplayTrimRefused):
        dt.face_display_mesh(g, face, dt.DisplayTolerance(display_trim_angle(8)))
    assert face not in trimmed_face_meshes(g, display_trim_angle(8))
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
    assert trimmed_face_meshes(geometry, display_trim_angle(8)) == {}
    for face_id in _routed(geometry)[:16]:
        new = face_display_polygons(geometry, face_id)
        old = scene_module._support_face_display_polygons(geometry, face_id)
        assert len(new) == len(old)
        assert all(np.array_equal(a, b) for a, b in zip(new, old))


def test_wet_overlay_uses_the_given_trimmed_meshes(tripod, monkeypatch):
    geometry = tripod.geometry
    meshes = trimmed_face_meshes(geometry, display_trim_angle(8))
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
