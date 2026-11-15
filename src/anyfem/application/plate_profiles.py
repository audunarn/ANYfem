"""Develop connected planar structural sheets into physical plate profiles in SI."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import fsum
from pathlib import Path

import numpy as np
from anygeometry.surfaces import CoonsSurface, Cylinder, Plane

from .plate_curves import edge_spans, project_span, signed_area, trace, write_profiles_dxf
from .thickness_side import thickness_band

# Maximum native end gap in a CAD contour, in metres (0.00001 mm).
_END_GAP_M = 1e-8


def _check_curve_loop(ring, ends):
    directed = [ends[edge] if forward else ends[edge][::-1] for edge, forward in ring]
    for previous, current in zip(directed, directed[1:] + directed[:1]):
        if np.linalg.norm(previous[1] - current[0]) > _END_GAP_M:
            raise ValueError("CAD contour has a gap between curve endpoints exceeding 1e-8 m")


@dataclass(frozen=True)
class PlateProfile:
    mark: str
    sheet_id: int
    thickness_m: float
    material: str
    area_m2: float
    z_min_m: float
    z_max_m: float
    _loops: tuple = field(repr=False, compare=False)
    _world_origin: np.ndarray | None = field(default=None, repr=False, compare=False)
    _axes: np.ndarray | None = field(default=None, repr=False, compare=False)
    # Material band along the drawing normal (see ``thickness_side``).
    thickness_side: str = "centre"
    # Curved strips only: the validated strip and the mid-thickness area
    # used for mass. Planar plates weigh their net cut area.
    _strip: object = field(default=None, repr=False, compare=False)
    _mass_area_m2: float | None = field(default=None, repr=False, compare=False)

    @property
    def normal(self) -> np.ndarray | None:
        """The planar plate drawing normal ``+n`` (None for a curved strip)."""
        return None if self._axes is None else np.cross(self._axes[:, 0], self._axes[:, 1])

    @property
    def mass_area_m2(self) -> float:
        """Area weighed in the takeoff: the net area, or a curved strip's mid-thickness area."""
        return self.area_m2 if self._mass_area_m2 is None else self._mass_area_m2

    @property
    def side_text(self) -> str:
        """Side and world direction of +n, for example ``front (+n = +Z)``."""
        from .thickness_side import side_label
        if self._strip is not None:
            return f"{self.thickness_side} (+n = outward radial)"
        return side_label(self.thickness_side, self.normal)

    @property
    def band_m(self) -> tuple[float, float]:
        """Material ``(s_lo, s_hi)`` along ``+n`` from the reference surface."""
        return thickness_band(self.thickness_m, self.thickness_side)

    @property
    def hole_count(self) -> int:
        return len(self._loops) - 1

    def write_dxf(self, path: str | Path):
        return write_profiles_dxf(path, self._loops)


def _plane(geometry, faces, world):
    # Positive-weight rational Bezier curves are contained in their control
    # hulls. Checking all poles also catches an off-plane interior of an edge.
    poles = [span[:, :3] / span[:, 3:] for spans in world.values() for span in spans]
    planes = []
    for face in faces:
        surface = face.surface
        if isinstance(surface, Plane):
            planes.append(surface)
        elif isinstance(surface, CoonsSurface):
            if surface.has_boundaries:
                poles.extend((surface.bottom, surface.right, surface.top, surface.left))
        elif surface is not None:
            raise ValueError(f"Face {face.id} has unsupported surface {type(surface).__name__}; a planar plate is required")
    points = np.vstack(poles)
    centre = points.mean(axis=0)
    extent = float(np.linalg.norm(np.ptp(points, axis=0)))
    tolerance = geometry.tolerance.effective_surface_residual(extent)
    if planes:
        normal = planes[0].normal.copy()
        origin = centre - normal * ((centre - planes[0].origin) @ normal)
    else:
        _, singular, basis = np.linalg.svd(points - centre, full_matrices=False)
        if len(singular) < 2 or singular[1] <= geometry.tolerance.effective_length(extent):
            raise ValueError("Plate has no nondegenerate supporting plane")
        normal, origin = basis[-1], centre
    if (np.max(np.abs((points - origin) @ normal)) > tolerance
            or any(np.max(np.abs((points - plane.origin) @ plane.normal)) > tolerance
                   for plane in planes[1:])):
        raise ValueError("Faces or boundary curves are not coplanar")
    if normal[np.argmax(np.abs(normal))] < 0:
        normal = -normal
    # Keep height vertical on standing/inclined plates, and global X horizontal
    # on horizontal plates. Axis signs are independent of loop traversal.
    if abs(normal[2]) > .9999:
        u = np.array([1., 0., 0.]) - normal * normal[0]
        u /= np.linalg.norm(u)
        v = np.cross(normal, u)
    else:
        v = np.array([0., 0., 1.]) - normal * normal[2]
        v /= np.linalg.norm(v)
        u = np.cross(v, normal)
    return origin, np.column_stack((u, v)), normal


def _reverse(uses):
    return [(edge, not forward) for edge, forward in reversed(uses)]


def _boundary_cycles(geometry, face_loops):
    incidence = defaultdict(list)
    for face, loops in face_loops.items():
        for loop in loops:
            for edge, forward in loop:
                incidence[edge].append((face, forward))
    neighbors = defaultdict(set)
    outgoing = {}
    incoming = set()
    for edge_id, uses in incidence.items():
        if len(uses) == 2:
            (a, sense), (b, other) = uses
            if a == b or sense == other:
                raise ValueError(f"Conflicting oriented incidence on edge {edge_id}")
            neighbors[a].add(b)
            neighbors[b].add(a)
        elif len(uses) == 1:
            _, forward = uses[0]
            edge = geometry.edges[edge_id]
            start, end = (edge.start, edge.end) if forward else (edge.end, edge.start)
            if start in outgoing or end in incoming:
                raise ValueError(f"Branched or touching plate contour at edge {edge_id}")
            outgoing[start] = (end, edge_id, forward)
            incoming.add(end)
        else:
            raise ValueError(f"Non-manifold plate incidence on edge {edge_id}")
    reached = set()
    todo = [next(iter(face_loops))]
    while todo:
        face = todo.pop()
        if face not in reached:
            reached.add(face)
            todo.extend(neighbors[face] - reached)
    if reached != set(face_loops):
        raise ValueError("Sheet contains disconnected faces without a shared native edge")
    if not outgoing or set(outgoing) != incoming:
        raise ValueError("Plate has empty or open boundary")
    cycles = []
    while outgoing:
        start = vertex = min(outgoing)
        loop = []
        while True:
            vertex, edge, forward = outgoing.pop(vertex)
            loop.append((edge, forward))
            if vertex == start:
                break
        cycles.append(loop)
    return cycles


def _face_rings(geometry, faces, sheet):
    """Directed native edges in physical face-use orientation (or raw single face)."""
    if sheet is None:
        face, = faces
        return {face.id: [[(e.edge, e.forward) for e in r] for r in (face.loop, *face.holes)]}
    face_loops = {}
    for uid in sheet.face_use_ids:
        use = geometry.face_uses[uid]
        rings = []
        for ids in use.loops:
            ring = [(geometry.coedges[c].edge_id, geometry.coedges[c].orientation.value > 0) for c in ids]
            rings.append(ring if use.orientation.value > 0 else _reverse(ring))
        face_loops[use.face_id] = rings
    return face_loops


@dataclass(frozen=True)
class _PlanarGeometry:
    loops: tuple
    area: float
    z_min: float
    z_max: float
    normal: np.ndarray
    origin: np.ndarray
    axes: np.ndarray


def _planar_geometry(geometry, faces, sheet):
    try:
        from shapely import Polygon, union_all
    except ImportError as error:
        raise ImportError("Install ANYfem's drawings extra to validate planar plate contours") from error
    if not faces or len({face.id for face in faces}) != len(faces):
        raise ValueError("Empty or repeated plate face")
    edge_ids = sorted({edge.edge for face in faces for loop in (face.loop, *face.holes) for edge in loop})
    world = {edge: edge_spans(geometry, edge) for edge in edge_ids}
    ends = {}
    for key, spans in world.items():
        span_ends = [s[[0,-1], :3] / s[[0,-1], 3:] for s in spans]
        if any(np.linalg.norm(a[1] - b[0]) > _END_GAP_M for a,b in zip(span_ends, span_ends[1:])):
            raise ValueError(f"CAD edge {key} has a gap between Bezier spans exceeding 1e-8 m")
        ends[key] = span_ends[0][0], span_ends[-1][1]
        edge = geometry.edges[key]
        vertices = [geometry.vertices[v].position for v in (edge.start, edge.end)]
        if np.any(np.linalg.norm(np.asarray(ends[key]) - vertices, axis=1) > _END_GAP_M):
            raise ValueError(f"CAD edge {key} endpoints disagree with topology by more than 1e-8 m")
    face_loops = _face_rings(geometry, faces, sheet)
    for rings in face_loops.values():
        for ring in rings:
            _check_curve_loop(ring, ends)
    origin, axes, normal = _plane(geometry, faces, world)
    local = {edge: [project_span(span, origin, axes) for span in spans] for edge, spans in world.items()}
    paths = {}
    for key, spans in local.items():
        path = np.vstack([trace(span, tolerance=1e-7)[:-1] for span in spans] + [spans[-1][-1:, :2]/spans[-1][-1, 2]])
        # The topology weld is authoritative for polygon predicates. Retain
        # original curve poles, including source fit residuals, for CAD/area.
        edge = geometry.edges[key]
        path[[0,-1]] = (np.asarray([geometry.vertices[v].position for v in (edge.start, edge.end)]) - origin) @ axes
        paths[key] = path

    def ring_points(ring):
        return np.vstack([(paths[e] if fwd else paths[e][::-1])[:-1] for e, fwd in ring])

    def ring_area(ring):
        return fsum((1 if forward else -1) * area[edge] for edge, forward in ring)

    area = {edge: fsum(signed_area(span) for span in spans) for edge, spans in local.items()}
    patches = []
    for face_id, rings in face_loops.items():
        # The face API identifies holes by role. Older trim creators can
        # traverse inner and outer loops in the same direction.
        outer_sign = ring_area(rings[0]) > 0
        for i in range(1, len(rings)):
            if (ring_area(rings[i]) > 0) == outer_sign:
                rings[i] = _reverse(rings[i])
        points = [ring_points(ring) for ring in rings]
        patch = Polygon(points[0], points[1:])
        if not patch.is_valid or patch.area <= 0:
            raise ValueError(f"Invalid outer contour or holes on face {face_id}")
        patches.append(patch)
    cycles = sorted(_boundary_cycles(geometry, face_loops), key=lambda ring: -abs(ring_area(ring)))
    for ring in cycles:
        _check_curve_loop(ring, ends)
    boundary = [ring_points(ring) for ring in cycles]
    physical = Polygon(boundary[0], boundary[1:])
    if not physical.is_valid or physical.area <= 0:
        raise ValueError("Sheet has disjoint, crossing or improperly nested physical contours")
    if len(patches) > 1:
        union = union_all(patches)
        roundoff = 128 * np.finfo(float).eps * fsum(p.area for p in patches)
        if (abs(fsum(p.area for p in patches) - union.area) > roundoff or
                physical.symmetric_difference(union).area > roundoff):
            raise ValueError("Sheet faces overlap or disagree with their physical boundary")
    # Shift the local drawing to the lower-left of its projected contour.
    low = np.min(boundary[0], axis=0)
    cad_loops = []
    areas = []
    for i, ring in enumerate(cycles):
        a = ring_area(ring)
        if (a > 0) != (i == 0):
            ring, a = _reverse(ring), -a
        areas.append(a)
        spans = []
        for eid, forward in ring:
            source = local[eid] if forward else (p[::-1] for p in reversed(local[eid]))
            for span in source:
                shifted = span.copy()
                shifted[:, :2] -= low * shifted[:, 2:]
                spans.append(shifted)
        cad_loops.append(tuple(spans))
    elevations = np.concatenate([trace(span, tolerance=1e-7)[:, 2]
                                 for eid, _ in cycles[0] for span in world[eid]])
    return _PlanarGeometry(tuple(cad_loops), fsum(areas), float(elevations.min()),
                           float(elevations.max()), normal, origin + axes @ low, axes)


def _plate(project, sheet, side="centre"):
    geometry = project.geometry
    faces = [geometry.faces[geometry.face_uses[u].face_id] for u in sheet.face_use_ids]
    sections = [project.plate_section_of(face.id) for face in faces]
    properties = {(section.thickness, section.material) for section in sections}
    if len(properties) != 1:
        raise ValueError("Sheet faces have conflicting plate thickness or material")
    thickness, material = properties.pop()
    if material not in project.materials:
        raise ValueError(f"Missing assigned material {material}")
    low, high = thickness_band(thickness, side)
    if faces and all(isinstance(face.surface, Cylinder) for face in faces):
        return _strip_plate(project, sheet, faces, thickness, material, side, (low, high))
    shape = _planar_geometry(geometry, faces, sheet)
    if side == "centre":
        # Historical arithmetic, kept literally (including signed zeros).
        half_height = abs(shape.normal[2]) * thickness / 2
        z_min, z_max = shape.z_min-half_height, shape.z_max+half_height
    else:
        rise = (low * shape.normal[2], high * shape.normal[2])
        z_min, z_max = shape.z_min+min(rise), shape.z_max+max(rise)
    return PlateProfile(sheet.name or f"Sheet-{sheet.id}", sheet.id, thickness, material,
                        shape.area, z_min, z_max,
                        shape.loops, shape.origin, shape.axes, side)


def _strip_plate(project, sheet, faces, thickness, material, side, band):
    """A curved strip: exact reference area and its rectangular development."""
    from .curved_strips import curved_strip
    strip = curved_strip(project.geometry, faces, sheet)
    low, high = band
    if side != "centre" and not strip.consistent:
        raise ValueError("Curved strip faces have disagreeing drawing normals; only a centred side is defined")
    if any(patch.radius + low <= 0 for patch in strip.patches):
        raise ValueError("Curved strip inner radius must be positive")
    from shapely.geometry.polygon import orient
    outline = orient(strip.outline, 1.)
    rings = [np.asarray(ring.coords)[:-1] for ring in (outline.exterior, *outline.interiors)]
    loops = tuple(tuple(np.array([(*a, 1.), (*b, 1.)]) for a, b in zip(ring, np.roll(ring, -1, axis=0)))
                  for ring in rings)
    elevations = []
    for patch in strip.patches:
        angles = strip.sample_angles(patch, patch.radius + high, 1e-7)
        for axial in patch.axial:
            for offset in (low, high):
                elevations.append(strip.point(patch, angles, axial, offset)[:, 2])
    elevations = np.concatenate(elevations)
    return PlateProfile(sheet.name or f"Sheet-{sheet.id}", sheet.id, thickness, material, strip.area,
                        float(elevations.min()), float(elevations.max()), loops, None, None, side,
                        strip, strip.band_area((low + high) / 2))


def build_plate_profiles(project, part_id: int, *, thickness_sides=None) -> tuple[PlateProfile, ...]:
    """Return net true-plane profiles for the physical sheets owned by one Part.

    ``thickness_sides`` maps Sheet IDs to resolved sides (``centre``, ``front``
    or ``back``); omitted Sheets are centred.
    """
    sides = dict(thickness_sides or {})
    part = project.geometry.parts.get(part_id)
    if part is None or not part.sheet_ids or part.member_ids:
        raise ValueError(f"Part {part_id} must be an existing, nonempty plate-only Part")
    unresolved = project.sheet_join_intent_problems()
    if unresolved:
        raise ValueError("Unresolved sheet ownership: " + ", ".join(unresolved))
    project.resolve_section_assignments(strict=True)
    result = []
    marks = set()
    owned = set()
    for sheet_id in part.sheet_ids:
        sheet = project.geometry.sheets[sheet_id]
        faces = {project.geometry.face_uses[u].face_id for u in sheet.face_use_ids}
        if faces & owned:
            raise ValueError(f"Plate {sheet.name or sheet_id} repeats faces assigned to another physical plate")
        owned.update(faces)
        try:
            profile = _plate(project, sheet, sides.get(sheet_id, "centre"))
        except ValueError as error:
            raise ValueError(f"Plate {sheet.name or sheet.id}: {error}") from error
        if profile.mark.casefold() in marks:
            raise ValueError(f"Duplicate physical plate mark {profile.mark}")
        marks.add(profile.mark.casefold())
        result.append(profile)
    return tuple(result)
