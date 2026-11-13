"""Curved plate strips: Sheets of parallel-axis cylinder patches with rectangular trims.

A curved strip is a Sheet whose faces are cylinder patches with parallel axes,
each trimmed to a rectangle in (angle, axial) coordinates and joined edge to
edge: side by side along rulings, or stacked along circular rims of the same
cylinder.  The patches may have different radii and axes (a tangent-continuous
approximation of a general curve, as SAT exporters write them).  Such a
surface is developable without distortion: every patch maps to a rectangle of
its reference arc length by its height, placed edge to edge.  The patches must
develop consistently into one polygon without overlap.  Anything else is
refused with a typed ``ValueError``.

All coordinates are metres.  The drawing normal ``+n`` of a patch is the
outward radial direction from its own axis.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import ceil, fsum, sqrt, tau

import numpy as np
from anygeometry.surfaces import Cylinder


@dataclass(frozen=True)
class StripPatch:
    face_id: int
    centre: np.ndarray       # axis point at common axial coordinate zero
    radial: np.ndarray       # common-frame angle zero direction
    tangent: np.ndarray      # common-frame angle +pi/2 direction (axis x radial)
    radius: float
    angles: tuple            # (low, high), high > low, radians about the common axis
    axial: tuple             # (bottom, top) common axial coordinates
    x0: float                # development x at the low angle
    sign: float              # +1 or -1: development x direction with increasing angle

    @property
    def sweep(self) -> float:
        return self.angles[1] - self.angles[0]

    @property
    def height(self) -> float:
        return self.axial[1] - self.axial[0]

    @property
    def area(self) -> float:
        return self.radius * self.sweep * self.height

    def direction(self, angles):
        angles = np.asarray(angles, dtype=float)
        return np.outer(np.cos(angles), self.radial) + np.outer(np.sin(angles), self.tangent)

    def development_x(self, angles):
        return self.x0 + self.sign * self.radius * (np.asarray(angles, dtype=float) - self.angles[0])

    @property
    def x_range(self):
        a, b = self.development_x(self.angles)
        return min(a, b), max(a, b)


@dataclass(frozen=True)
class CurvedStrip:
    patches: tuple           # StripPatch, by face ID
    origin: np.ndarray       # common axial origin
    axis: np.ndarray         # common axis (largest component positive)
    outline: object          # shapely Polygon of the development, lower-left at (0, 0)
    free_edges: tuple        # (patch index, world points) of edges on the Sheet boundary
    consistent: bool         # outward normals agree across every ruling joint
    tolerance: float

    @property
    def axial(self):
        return min(p.axial[0] for p in self.patches), max(p.axial[1] for p in self.patches)

    @property
    def area(self) -> float:
        """Exact reference-surface area, the sum of R dtheta dh per patch."""
        return fsum(patch.area for patch in self.patches)

    def band_area(self, middle: float) -> float:
        """Area of the surface at radius R + ``middle`` (the mid-thickness surface)."""
        return fsum((patch.radius + middle) * patch.sweep * patch.height for patch in self.patches)

    def point(self, patch, angles, axial, offset=0.):
        """World points at ``angles`` on a patch, common axial coordinate and radial offset."""
        return patch.centre + axial * self.axis + (patch.radius + offset) * patch.direction(angles)

    def sample_angles(self, patch, radius, chord):
        # Chord sagitta R (1 - cos(d/2)) <= R d^2 / 8 <= chord.
        count = max(1, ceil(patch.sweep / sqrt(8 * chord / radius)))
        return np.linspace(*patch.angles, count + 1)

    def locate(self, points):
        """Per point: patch index, angle on that patch, axial and radial coordinates."""
        points = np.atleast_2d(np.asarray(points, dtype=float))
        axial = (points - self.origin) @ self.axis
        best = np.full(len(points), np.inf)
        index = np.zeros(len(points), dtype=int)
        angle = np.zeros(len(points))
        radius = np.zeros(len(points))
        for k, patch in enumerate(self.patches):
            theta, distance = _angles(patch, self.axis, points)
            low, high = patch.angles
            bottom, top = patch.axial
            outside = np.hypot(np.maximum(0., np.maximum(low - theta, theta - high)) * patch.radius,
                               np.maximum(0., np.maximum(bottom - axial, axial - top)))
            better = outside < best - 1e-12
            best = np.where(better, outside, best)
            index = np.where(better, k, index)
            angle = np.where(better, theta, angle)
            radius = np.where(better, distance, radius)
        return index, angle, axial, radius

    def radial_distance(self, points):
        """Signed distance along +n (outward) from the reference surface."""
        index, _, _, radius = self.locate(points)
        return radius - np.array([self.patches[k].radius for k in index])

    def develop(self, points):
        """Development coordinates (arc length, height) in the outline's frame."""
        index, angle, axial, _ = self.locate(points)
        x = np.array([self.patches[k].development_x(a) for k, a in zip(index, angle)])
        return np.column_stack((x, axial - self.axial[0]))


def _angles(patch, axis, points):
    """Angle about a patch's axis (near its trim) and radial distance of world points."""
    rel = np.atleast_2d(points) - patch.centre
    rel = rel - np.outer(rel @ axis, axis)
    theta = np.arctan2(rel @ patch.tangent, rel @ patch.radial)
    theta = theta + tau * np.round((sum(patch.angles)/2 - theta) / tau)
    return theta, np.linalg.norm(rel, axis=1)


def _unit_axis(vector):
    vector = np.asarray(vector, dtype=float) / np.linalg.norm(vector)
    return -vector if vector[np.argmax(np.abs(vector))] < 0 else vector


def _edge_points(geometry, edge_id, cache):
    from .plate_view import _native_spans, _trace_spans
    if edge_id not in cache:
        cache[edge_id] = _trace_spans(_native_spans(geometry, edge_id), 1e-6)
    return cache[edge_id]


def rectangular_trim(geometry, face, *, cache=None):
    """Return ``(angle_low, angle_high, axial_low, axial_high)`` of a rectangular cylinder trim.

    Angles are about the face's own surface frame (radial_direction toward
    circumferential_direction), axial along its own axis from its origin.
    """
    surface = face.surface
    if not isinstance(surface, Cylinder):
        raise ValueError(f"face {face.id} is not cylindrical")
    if face.holes:
        raise ValueError(f"face {face.id} has openings; a curved strip patch must be a plain rectangle")
    cache = {} if cache is None else cache
    pieces = []
    for use in face.loop:
        points = _edge_points(geometry, use.edge, cache)
        pieces.append(points if use.forward else points[::-1])
    ring = np.vstack([piece[:-1] for piece in pieces])
    rel = ring - surface.origin
    axial = rel @ surface.axis
    radial = rel - np.outer(axial, surface.axis)
    radius = np.linalg.norm(radial, axis=1)
    extent = max(float(np.ptp(axial)), surface.radius)
    tolerance = geometry.tolerance.effective_surface_residual(extent)
    if np.max(np.abs(radius - surface.radius)) > tolerance:
        raise ValueError(f"face {face.id} boundary leaves its cylinder")
    raw = np.arctan2(radial @ surface.circumferential_direction, radial @ surface.radial_direction)
    closed = np.unwrap(np.append(raw, raw[0]))
    angle = closed[:-1]
    if abs(closed[-1] - closed[0]) > 1e-6 or np.ptp(angle) >= tau - 1e-9:
        raise ValueError(f"face {face.id} trim winds around its axis")
    low, high = angle.min(), angle.max()
    bottom, top = axial.min(), axial.max()
    r = surface.radius
    gap = np.minimum.reduce((np.abs(angle-low)*r, np.abs(angle-high)*r, np.abs(axial-bottom), np.abs(axial-top)))
    x, y = angle * r, axial
    area = abs(.5 * np.sum(x*np.roll(y, -1) - np.roll(x, -1)*y))
    rectangle = (high-low) * r * (top-bottom)
    if (np.max(gap) > tolerance or (high - low) * r <= tolerance or top - bottom <= tolerance
            or abs(area - rectangle) > 2 * tolerance * ((high-low)*r + (top-bottom))):
        raise ValueError(f"face {face.id} trim is not rectangular in (angle, axial)")
    return float(low), float(high), float(bottom), float(top)


def curved_strip(geometry, faces, sheet=None) -> CurvedStrip:
    """Validate a curved strip Sheet and return its exact development data."""
    from shapely import Polygon, box, union_all
    from shapely.affinity import translate

    if not faces or not all(isinstance(face.surface, Cylinder) for face in faces):
        raise ValueError("A curved strip consists of cylindrical faces only")
    first = faces[0].surface
    axis = _unit_axis(first.axis)
    origin = np.asarray(first.origin, dtype=float)
    cache, trims = {}, {}
    for face in faces:
        surface = face.surface
        scale = max(surface.radius, abs(surface.height), 1.)
        if np.linalg.norm(np.cross(surface.axis, axis)) * scale > geometry.tolerance.effective_surface_residual(scale):
            raise ValueError("Curved strip faces have non-parallel cylinder axes")
        trims[face.id] = rectangular_trim(geometry, face, cache=cache)
    points = np.vstack(list(cache.values()))
    extent = float(np.linalg.norm(np.ptp(points, axis=0)))
    tolerance = geometry.tolerance.effective_surface_residual(extent)

    # Patch frames about the common axis. A reversed surface axis reverses its
    # angle sense and its axial coordinate; express both in common terms.
    frames = {}
    for face in faces:
        surface = face.surface
        sense = 1. if surface.axis @ axis > 0 else -1.
        low, high, bottom, top = trims[face.id]
        shift = (surface.origin - origin) @ axis
        radial = np.asarray(surface.radial_direction, dtype=float)
        frames[face.id] = dict(centre=surface.origin - shift * axis, radial=radial, tangent=np.cross(axis, radial),
                               radius=surface.radius, angles=(low, high) if sense > 0 else (-high, -low),
                               axial=tuple(sorted((shift + sense*bottom, shift + sense*top))))

    def classify(face_id, edge_id):
        """('angle', 'low'|'high') for a ruling or ('axial', 'bottom'|'top') for a rim."""
        f = frames[face_id]
        patch = StripPatch(face_id, f["centre"], f["radial"], f["tangent"], f["radius"], f["angles"], f["axial"], 0., 1.)
        theta, _ = _angles(patch, axis, cache[edge_id])
        z = (cache[edge_id] - origin) @ axis
        for value, name in zip(f["angles"], ("low", "high")):
            if np.max(np.abs(theta - value)) * f["radius"] <= tolerance:
                return "angle", name, theta
        for value, name in zip(f["axial"], ("bottom", "top")):
            if np.max(np.abs(z - value)) <= tolerance:
                return "axial", name, theta
        raise ValueError(f"Curved strip edge {edge_id} is neither a ruling nor a rim of face {face_id}")

    incidence = {}
    for face in faces:
        for use in face.loop:
            incidence.setdefault(use.edge, []).append(face.id)
    links = {face.id: [] for face in faces}
    for edge_id, users in incidence.items():
        if len(users) > 2 or len(set(users)) != len(users):
            raise ValueError(f"Curved strip edge {edge_id} has non-manifold incidence")
        if len(users) == 2:
            a, b = users
            links[a].append((b, edge_id))
            links[b].append((a, edge_id))

    # Place each patch in the development by walking shared edges.
    placed = {}
    start = min(frames)
    placed[start] = (0., 1.)
    queue = deque([start])
    consistent = True
    while queue:
        this = queue.popleft()
        x0, sign = placed[this]
        f = frames[this]
        for other, edge_id in links[this]:
            g = frames[other]
            kind, side, theta = classify(this, edge_id)
            other_kind, other_side, other_theta = classify(other, edge_id)
            if kind != other_kind:
                raise ValueError(f"Curved strip edge {edge_id} joins a ruling to a rim")
            x_here = x0 + sign * f["radius"] * (theta - f["angles"][0])
            if kind == "angle":
                # The other patch lies on the opposite side of the ruling.
                into = sign if side == "low" else -sign
                other_sign = -into if other_side == "low" else into
                out = f["radial"] * np.cos(theta[0]) + f["tangent"] * np.sin(theta[0])
                back = g["radial"] * np.cos(other_theta[0]) + g["tangent"] * np.sin(other_theta[0])
                if out @ back <= 0:
                    consistent = False
            else:
                if (abs(f["radius"] - g["radius"]) > tolerance
                        or np.linalg.norm(f["centre"] - g["centre"]) > tolerance):
                    raise ValueError(f"Curved strip patches stacked at edge {edge_id} lie on different cylinders")
                other_sign = sign
            other_x0 = float(x_here[0] - other_sign * g["radius"] * (other_theta[0] - g["angles"][0]))
            other_x = other_x0 + other_sign * g["radius"] * (other_theta - g["angles"][0])
            if np.max(np.abs(other_x - x_here)) > 2 * tolerance:
                raise ValueError(f"Curved strip patches do not develop consistently along edge {edge_id}")
            if other in placed:
                if placed[other][1] != other_sign or abs(placed[other][0] - other_x0) > 2 * tolerance:
                    raise ValueError("Curved strip patches do not develop consistently (a closed ring needs a seam)")
                continue
            placed[other] = (other_x0, other_sign)
            queue.append(other)
    if len(placed) != len(faces):
        raise ValueError("Curved strip faces are not joined into one Sheet by shared edges")

    patches = []
    for face in faces:
        f = frames[face.id]
        x0, sign = placed[face.id]
        patches.append(StripPatch(face.id, f["centre"], f["radial"], f["tangent"], f["radius"],
                                  f["angles"], f["axial"], x0, sign))
    bottom = min(p.axial[0] for p in patches)
    rectangles = [box(*p.x_range[:1], p.axial[0] - bottom, *p.x_range[1:], p.axial[1] - bottom) for p in patches]
    outline = union_all(rectangles, grid_size=1e-10)
    total = fsum(r.area for r in rectangles)
    if (not isinstance(outline, Polygon) or abs(outline.area - total) > tolerance * outline.length
            or not outline.is_valid):
        raise ValueError("Curved strip patches overlap or do not develop into one connected plate")
    left = outline.bounds[0]
    outline = translate(outline.simplify(0.), -left, 0.)
    patches = tuple(StripPatch(p.face_id, p.centre, p.radial, p.tangent, p.radius, p.angles, p.axial,
                               p.x0 - left, p.sign) for p in patches)
    index = {p.face_id: k for k, p in enumerate(patches)}
    from .plate_view import _native_spans, _trace_spans
    # Boundary edges for drawing walls, at the 0.05 mm drawing chord.
    free = tuple((index[users[0]], _trace_spans(_native_spans(geometry, edge_id), .00005))
                 for edge_id, users in sorted(incidence.items()) if len(users) == 1)
    return CurvedStrip(patches, origin, axis, outline, free, consistent, tolerance)
