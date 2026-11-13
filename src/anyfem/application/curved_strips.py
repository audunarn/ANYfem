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

# Chord of drawn curved walls (m).  A skin opening at a ruling joint up to this
# size is below the drawing's own accuracy; a larger one is refused.
DRAWING_CHORD = .00005


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
    normal_jump: float = 0.  # largest |n_a -+ n_b| across a ruling joint (0 when tangent)

    def skin_gap(self, band) -> float:
        """Largest opening between the offset skins of two patches at a ruling joint.

        A skin at offset ``s`` leaves the shared ruling at ``s n_a`` on one patch
        and ``s n_b`` on the other, so it opens by ``|s| |n_a - n_b|``.
        """
        return max(abs(band[0]), abs(band[1])) * self.normal_jump

    def solid_refusal(self, band, side="centre"):
        """Why a solid of material ``band`` cannot be drawn on this strip, or ``None``."""
        if side != "centre" and band[0] != band[1] and not self.consistent:
            return "curved strip faces have disagreeing drawing normals; only a centred side is defined"
        gap = self.skin_gap(band)
        if gap > max(self.tolerance, DRAWING_CHORD):
            kink = np.degrees(2 * np.arcsin(min(1., self.normal_jump / 2)))
            return (f"curved strip has a {kink:.3f} deg normal kink at a ruling joint; its {side} solid "
                    f"would open by {gap * 1000:.3f} mm there, more than the {DRAWING_CHORD * 1000:.2f} mm "
                    "drawing chord (only the 2D mid-surface can be drawn)")
        return None

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

    def locate(self, points, face_id=None):
        """Per point: patch index, angle on that patch, axial and radial coordinates.

        A point belongs to the patch whose trimmed surface is nearest (Euclidean
        distance to the patch clamped to its angle and axial trim).  Patches that
        are equally near within tolerance must give the same development
        coordinate and radial offset, as on a shared joint; otherwise the mapping
        is ambiguous and refused.  ``face_id`` names the patch when it is known.
        """
        points = np.atleast_2d(np.asarray(points, dtype=float))
        axial = (points - self.origin) @ self.axis
        shape = (len(self.patches), len(points))
        distance, angle, radius, x = (np.empty(shape) for _ in range(4))
        for k, patch in enumerate(self.patches):
            theta, rho = _angles(patch, self.axis, points)
            nearest = (patch.centre + np.outer(np.clip(axial, *patch.axial), self.axis)
                       + patch.radius * patch.direction(np.clip(theta, *patch.angles)))
            distance[k] = np.linalg.norm(points - nearest, axis=1)
            angle[k], radius[k], x[k] = theta, rho, patch.development_x(theta)
        columns = np.arange(len(points))
        if face_id is not None:
            ids = [patch.face_id for patch in self.patches]
            if face_id not in ids:
                raise ValueError(f"Face {face_id} is not a patch of this curved strip")
            index = np.full(len(points), ids.index(face_id))
            if np.any(distance[index, columns] > 4 * self.tolerance):
                raise ValueError(f"Points do not lie on curved strip patch {face_id}")
        else:
            index = np.argmin(distance, axis=0)
            near = distance <= distance[index, columns] + self.tolerance
            offset = radius - np.array([patch.radius for patch in self.patches])[:, None]
            for values in (x, offset):
                spread = np.where(near, values, -np.inf).max(axis=0) - np.where(near, values, np.inf).min(axis=0)
                if np.any(spread > 2 * self.tolerance):
                    raise ValueError("Curved strip point mapping is ambiguous: a point is equally near "
                                     "two patches that develop it differently")
        return index, angle[index, columns], axial, radius[index, columns]

    def radial_distance(self, points, face_id=None):
        """Signed distance along +n (outward) from the reference surface."""
        index, _, _, radius = self.locate(points, face_id)
        return radius - np.array([self.patches[k].radius for k in index])

    def develop(self, points, face_id=None):
        """Development coordinates (arc length, height) in the outline's frame."""
        index, angle, axial, _ = self.locate(points, face_id)
        x = np.array([self.patches[k].development_x(a) for k, a in zip(index, angle)])
        return np.column_stack((x, axial - self.axial[0]))


def _angles(patch, axis, points):
    """Angle about a patch's axis (near its trim) and radial distance of world points."""
    rel = np.atleast_2d(points) - patch.centre
    rel = rel - np.outer(rel @ axis, axis)
    theta = np.arctan2(rel @ patch.tangent, rel @ patch.radial)
    theta = theta + tau * np.round((sum(patch.angles)/2 - theta) / tau)
    return theta, np.linalg.norm(rel, axis=1)


def _normal(frame, angle):
    """Outward radial unit normal of a patch frame at a common-frame angle."""
    return frame["radial"] * np.cos(angle) + frame["tangent"] * np.sin(angle)


def _arc_direction(frame, angle):
    """Unit direction of increasing angle along a patch frame's arc."""
    return frame["tangent"] * np.cos(angle) - frame["radial"] * np.sin(angle)


def _frame_angle(frame, axis, point):
    patch = StripPatch(0, frame["centre"], frame["radial"], frame["tangent"], frame["radius"],
                       frame["angles"], frame["axial"], 0., 1.)
    return float(_angles(patch, axis, point)[0][0])


def _check_overlaps(frames, axis, linked, tolerance):
    """Refuse two patches that share interior points or touch without a shared edge.

    Parallel-axis cylinders meet along at most two rulings (the intersections
    of their cross-section circles) unless they are the same cylinder, where
    their angle intervals must not overlap.  A crossing ruling within both
    angle trims, over a common axial range, is refused unless it is the shared
    ruling of two joined patches (on the trim boundary of both), or a second
    crossing next to that joint whose overlap stays within tolerance.
    """
    ids = sorted(frames)
    for i, a in enumerate(ids):
        f = frames[a]
        for b in ids[i + 1:]:
            g = frames[b]
            if min(f["axial"][1], g["axial"][1]) - max(f["axial"][0], g["axial"][0]) <= tolerance:
                continue
            delta = g["centre"] - f["centre"]
            delta = delta - (delta @ axis) * axis
            d = float(np.linalg.norm(delta))
            ra, rb = f["radius"], g["radius"]
            if d <= tolerance:
                if abs(ra - rb) > tolerance:
                    continue  # coaxial, different radii: no common point
                offset = np.arctan2(g["radial"] @ f["tangent"], g["radial"] @ f["radial"])
                low, high = g["angles"][0] + offset, g["angles"][1] + offset
                for turn in (-tau, 0., tau):
                    shared = min(f["angles"][1], high + turn) - max(f["angles"][0], low + turn)
                    if shared * ra > tolerance:
                        raise ValueError(f"Curved strip patches of faces {a} and {b} overlap on one cylinder")
                continue
            u = delta / d
            w = np.cross(axis, u)
            if abs(d - (ra + rb)) <= tolerance:
                crossings = [f["centre"] + ra * u]
            elif abs(d - abs(ra - rb)) <= tolerance:
                crossings = [f["centre"] + (ra if ra > rb else -ra) * u]
            elif d > ra + rb or d < abs(ra - rb):
                continue
            else:
                along = (d*d + ra*ra - rb*rb) / (2*d)
                across = sqrt(max(0., ra*ra - along*along))
                crossings = [f["centre"] + along * u + s * across * w for s in (1., -1.)]
            joined = frozenset((a, b)) in linked
            states = []
            for point in crossings:
                state = []
                for frame in (f, g):
                    theta = _frame_angle(frame, axis, point)
                    slack = tolerance / frame["radius"]
                    low, high = frame["angles"]
                    edge = min(abs(theta - low), abs(theta - high)) <= slack
                    state.append((low - slack <= theta <= high + slack, edge, theta))
                if state[0][0] and state[1][0]:
                    states.append((point, state))
            joints = [point for point, ((_, edge_a, _), (_, edge_b, _)) in states if joined and edge_a and edge_b]
            for point, ((_, edge_a, theta_a), (_, edge_b, _)) in states:
                if joined and edge_a and edge_b:
                    continue
                if joints:
                    # Second crossing beside the joint: the arcs between them part by at most
                    # the separation at the middle of patch a's arc between the two.
                    middle = (theta_a + _frame_angle(f, axis, joints[0])) / 2
                    probe = f["centre"] + ra * _normal(f, middle)
                    rest = probe - g["centre"]
                    rest = rest - (rest @ axis) * axis
                    if abs(np.linalg.norm(rest) - rb) <= tolerance:
                        continue
                raise ValueError(f"Curved strip patches of faces {a} and {b} cross or touch away from a "
                                 "shared edge (they overlap)")


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
    consistent, jump = True, 0.
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
                # The other patch must lie physically on the opposite side of the ruling:
                # the directions into the two patches, along their arcs, must oppose.
                into = sign if side == "low" else -sign
                other_sign = -into if other_side == "low" else into
                out = _normal(f, theta[0])
                back = _normal(g, other_theta[0])
                inward = _arc_direction(f, theta[0]) * (1. if side == "low" else -1.)
                other_inward = _arc_direction(g, other_theta[0]) * (1. if other_side == "low" else -1.)
                if inward @ other_inward >= 0:
                    raise ValueError(f"Curved strip patches joined at edge {edge_id} lie on the same side of "
                                     "their ruling (they fold back or overlap)")
                if out @ back <= 0:
                    consistent = False
                jump = max(jump, min(np.linalg.norm(out - back), np.linalg.norm(out + back)))
            else:
                if (abs(f["radius"] - g["radius"]) > tolerance
                        or np.linalg.norm(f["centre"] - g["centre"]) > tolerance):
                    raise ValueError(f"Curved strip patches stacked at edge {edge_id} lie on different cylinders")
                if side == other_side:
                    raise ValueError(f"Curved strip patches stacked at edge {edge_id} overlap (both lie "
                                     f"{'below' if side == 'top' else 'above'} the shared rim)")
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
    _check_overlaps(frames, axis, {frozenset((a, b)) for a in links for b, _ in links[a]}, tolerance)

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
    return CurvedStrip(patches, origin, axis, outline, free, consistent, tolerance, float(jump))
