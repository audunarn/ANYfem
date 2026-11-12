"""Drawing-plane intersections of native structural geometry, in metres."""
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from anygeometry.curves import Arc, Straight, arc_frame
from anygeometry.surfaces import Cylinder

from .drawing_views import DrawingPath, DrawingView
from .plate_curves import edge_spans, trace
from .plate_profiles import _END_GAP_M, _plane


# Curves are linearized for drawing within ten micrometres in their native
# plane. This is separate from the geometry model's topology tolerances.
_CHORD_M = 1e-5
_ROUND = 64 * np.finfo(float).eps


def _vector(value, name):
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.isfinite(vector).all():
        raise ValueError(f"{name} must be a finite three-component vector")
    return vector


def _unit(value, name):
    vector = _vector(value, name)
    scale = np.max(np.abs(vector))
    if scale == 0:
        raise ValueError(f"{name} must be nonzero")
    vector = vector / scale
    return vector / np.linalg.norm(vector)


@dataclass(frozen=True)
class _Frame:
    origin: np.ndarray
    normal: np.ndarray
    axes: np.ndarray

    @classmethod
    def view(cls, origin, normal, up):
        origin = _vector(origin, "Plane origin")
        normal = _unit(normal, "Plane normal")
        horizontal = np.cross(_unit(up, "Up direction"), normal)
        length = np.linalg.norm(horizontal)
        if length < _ROUND:
            raise ValueError("Up direction must be independent of the plane normal")
        u = horizontal / length
        return cls(origin, normal, np.column_stack((u, np.cross(normal, u))))


def _planar_frame(geometry, faces, edges):
    world = {}
    for face in faces:
        for loop in (face.loop, *face.holes):
            for item in loop:
                if item.edge not in edges:
                    edges[item.edge] = edge_spans(geometry, item.edge)
                world[item.edge] = edges[item.edge]
    origin, axes, normal = _plane(geometry, faces, world)
    return _Frame(origin, normal, axes)


def face_plane(project, face_id):
    """Return a planar native face's origin, viewer-facing normal and drawing up."""
    face = project.geometry.faces.get(face_id)
    if face is None:
        raise ValueError(f"No geometry face {face_id}")
    frame = _planar_frame(project.geometry, [face], {})
    return tuple(frame.origin), tuple(frame.normal), tuple(frame.axes[:, 1])


def _polygon(geometry, face, frame, edges, paths):
    from shapely import Polygon

    rings = []
    for loop in (face.loop, *face.holes):
        pieces = []
        for item in loop:
            if item.edge not in paths:
                spans = edges[item.edge]
                ends = [span[[0,-1], :3] / span[[0,-1], 3:] for span in spans]
                if any(np.linalg.norm(a[-1]-b[0]) > _END_GAP_M for a,b in zip(ends, ends[1:])):
                    raise ValueError(f"edge {item.edge} has a gap between curve spans")
                points = np.vstack([trace(span, _CHORD_M)[:-1] for span in spans]
                                   + [spans[-1][-1:, :3] / spans[-1][-1, 3]])
                edge = geometry.edges[item.edge]
                # Topology supplies a common end for incident trimmed carriers.
                endpoints = [geometry.vertex_position(v) for v in (edge.start, edge.end)]
                if np.max(np.linalg.norm(points[[0,-1]]-endpoints, axis=1)) > _END_GAP_M:
                    raise ValueError(f"edge {item.edge} endpoints disagree with topology")
                points[[0, -1]] = endpoints
                paths[item.edge] = points
            points = paths[item.edge]
            pieces.append((points if item.forward else points[::-1])[:-1])
        rings.append((np.vstack(pieces) - frame.origin) @ frame.axes)
    polygon = Polygon(rings[0], rings[1:])
    if not polygon.is_valid or polygon.area == 0:
        raise ValueError("invalid planar boundary or openings")
    return polygon


def _transform(shape, linear, shift):
    from shapely.affinity import affine_transform
    return affine_transform(shape, (*linear.ravel(), *shift))


def _pieces(shape):
    if shape.is_empty:
        return
    if shape.geom_type.startswith("Multi") or shape.geom_type == "GeometryCollection":
        for part in shape.geoms:
            yield from _pieces(part)
    else:
        yield shape


def _prism_cut(profile, frame, half_depth, view):
    """Intersect a finite normal extrusion of a trimmed polygon with a plane.

    X lies on both planes. Y follows the extrusion projected into the view.
    In these coordinates each end cap clips at constant Y. The perpendicular
    case instead extrudes the individual material intervals along the cut.
    """
    from shapely import GeometryCollection, LineString, box, union_all

    d = float((frame.origin - view.origin) @ view.normal)
    cosine = float(view.normal @ frame.normal)
    cross = np.cross(view.normal, frame.normal)
    sine = float(np.linalg.norm(cross))
    shift = (frame.origin - view.origin) @ view.axes
    tolerance = _ROUND * max(1., abs(d), half_depth, *map(abs, profile.bounds))
    if sine <= _ROUND:
        if abs(d) > half_depth + tolerance:
            return GeometryCollection(), False
        return (_transform(profile, view.axes.T @ frame.axes, shift),
                abs(abs(d) - half_depth) <= tolerance)

    x = cross / sine
    t = np.cross(frame.normal, x)  # direction in the footprint toward the viewer
    y = np.cross(x, view.normal)  # extrusion projected into the view
    footprint = _transform(profile, np.stack((x, t)) @ frame.axes, (0., 0.))
    xmin, _, xmax, _ = footprint.bounds
    low, high = (d * cosine - half_depth) / sine, (d * cosine + half_depth) / sine
    if abs(cosine) <= _ROUND:
        line = LineString(((xmin, -d), (xmax, -d)))
        cut = footprint.intersection(line)
        bands = []
        for segment in _pieces(cut):
            x0, _, x1, _ = segment.bounds
            if x0 == x1:
                if half_depth:
                    bands.append(LineString(((x0, low), (x0, high))))
            else:
                bands.append(box(x0, low, x1, high) if half_depth else
                             LineString(((x0, low), (x1, high))))
        region = union_all(bands)
        skin = footprint.geom_type == "Polygon" and cut.difference(footprint.boundary).is_empty
    else:
        # A point in the view at (X,Y) has footprint coordinates
        # (X,-cosine*Y-d*sine) and extrusion height sine*Y-d*cosine.
        projected = _transform(footprint, np.diag([1., -1./cosine]), (0., -d*sine/cosine))
        band = box(xmin, low, xmax, high) if half_depth else LineString(((xmin, low), (xmax, low)))
        region = projected.intersection(band)
        skin = False
    return _transform(region, view.axes.T @ np.column_stack((x, y)), shift), skin


def _cylinder_prism(geometry, face, thickness):
    """Native circular rim / straight ruling patches, including trimmed sectors."""
    from shapely import LineString, Polygon

    surface = face.surface
    if face.holes or len(face.loop) != 4:
        raise ValueError("cylinder requires two circular rims and two rulings")
    rims = []
    tolerance = geometry.tolerance.effective_surface_residual(max(abs(surface.height), surface.radius))
    for item in face.loop:
        edge = geometry.edges[item.edge]
        a, b = [geometry.vertex_position(i) for i in (edge.start, edge.end)]
        if isinstance(edge.curve, Straight):
            if np.linalg.norm(np.cross(b-a, surface.axis)) > tolerance:
                raise ValueError("non-axial cylinder boundary")
        elif isinstance(edge.curve, Arc):
            arc = arc_frame(a, geometry.vertex_position(edge.curve.via_vertex), b)
            axial = (arc.center - surface.origin) @ surface.axis
            centre = surface.origin + axial * surface.axis
            if (np.linalg.norm(arc.center-centre) > tolerance or
                    abs(arc.radius - surface.radius) > tolerance or
                    np.linalg.norm(np.cross(arc.normal, surface.axis))*surface.radius > tolerance):
                raise ValueError("rim does not follow the native cylinder")
            rims.append((axial, arc))
        else:
            raise ValueError("unsupported cylindrical trim curve")
    if len(rims) != 2:
        raise ValueError("cylinder requires two circular rims")
    (bottom, arc), (top, opposite) = sorted(rims, key=lambda rim: rim[0])
    def midpoint_direction(rim):
        return np.cos(rim.sweep/2)*rim.e1 + np.sin(rim.sweep/2)*rim.e2
    if (top-bottom <= tolerance or abs(abs(arc.sweep)-abs(opposite.sweep))*arc.radius > tolerance
            or np.linalg.norm(midpoint_direction(arc)-midpoint_direction(opposite))*arc.radius > tolerance):
        raise ValueError("cylinder rims span different angular intervals")
    radius = surface.radius
    half = thickness / 2
    if half >= radius:
        raise ValueError("cylinder inner radius must be positive")
    # Bound the outer chord sagitta using 1-cos(a) <= a**2/2.
    count = max(2, int(np.ceil(abs(arc.sweep) * np.sqrt((radius+half)/(8*_CHORD_M)))))
    angles = np.linspace(0., arc.sweep, count+1)
    axes = np.column_stack((surface.radial_direction, surface.circumferential_direction))
    radial = (np.outer(np.cos(angles), arc.e1) + np.outer(np.sin(angles), arc.e2)) @ axes
    footprint = (Polygon(np.vstack((radial*(radius+half), radial[::-1]*(radius-half))))
                 if half else LineString(radial * radius))
    frame = _Frame(surface.origin + (bottom+top)/2 * surface.axis, surface.axis, axes)
    return footprint, frame, (top-bottom)/2


def _beam_prism(project, edge_id):
    from shapely import box, union_all

    edge = project.geometry.edges[edge_id]
    if not isinstance(edge.curve, Straight):
        raise ValueError("curved member sweep is unsupported")
    start, end = (project.geometry.vertex_position(i) for i in (edge.start, edge.end))
    axis, y, z = project.beam_section_frame(edge_id)
    origin = (start+end)/2 + np.asarray(project.beam_offset_vector(edge_id))
    section = project.beam_section_of(edge_id)
    rectangles = [box(cy-w/2, cz-h/2, cy+w/2, cz+h/2)
                  for cy, cz, w, h in section.centered_profile_rectangles()]
    scale = max(abs(value) for rect in rectangles for value in rect.bounds)
    profile = union_all(rectangles, grid_size=scale*_ROUND)
    return profile, _Frame(origin, axis, np.column_stack((y, z))), np.linalg.norm(end-start)/2


def _append_paths(paths, shape, layer, label):
    for piece in _pieces(shape):
        if piece.geom_type == "Polygon":
            for ring, ring_layer in [(piece.exterior, layer), *((r, "HOLES") for r in piece.interiors)]:
                paths.append(DrawingPath(ring_layer, tuple(ring.coords)[:-1], True, label))
        elif piece.geom_type in {"LineString", "LinearRing"} and piece.length:
            points = tuple(piece.coords)
            paths.append(DrawingPath("TRACE", points[:-1] if piece.is_ring else points, piece.is_ring, label))


def _sheet_cuts(project, faces, view, edges, sampled, notes):
    from shapely import union_all

    geometry = project.geometry
    thickness = {f.id: (project.plate_section_of(f.id).thickness if f.id in project.face_sections else 0.)
                 for f in faces}
    if len(faces) > 1 and len(set(thickness.values())) == 1:
        # Join the physical material first. In particular, a perpendicular cut
        # precisely along an internal face incidence is inside the sheet.
        try:
            frame = _planar_frame(geometry, faces, edges)
        except ValueError:
            pass  # A bent sheet needs a separate supporting frame per face.
        else:
            try:
                footprint = union_all([_polygon(geometry, f, frame, edges, sampled) for f in faces])
                yield _prism_cut(footprint, frame, thickness[faces[0].id]/2, view)
                return
            except ValueError:
                pass  # Isolate and identify any individual invalid contour.
    for face in faces:
        try:
            if isinstance(face.surface, Cylinder):
                prism = _cylinder_prism(geometry, face, thickness[face.id])
            else:
                frame = _planar_frame(geometry, [face], edges)
                prism = _polygon(geometry, face, frame, edges, sampled), frame, thickness[face.id]/2
            yield _prism_cut(*prism, view)
        except ValueError as error:
            notes.append(f"Face {face.id} omitted ({error})")


def build_plane_view(project, origin, normal, up, *, bounds=None) -> DrawingView:
    """Cut native sheets and assigned straight members. Plane and bounds use SI."""
    from shapely import box, union_all

    view = _Frame.view(origin, normal, up)
    clip = None
    if bounds is not None:
        rect = np.asarray(bounds, dtype=float)
        if (rect.shape != (4,) or not np.isfinite(rect).all()
                or rect[0] >= rect[1] or rect[2] >= rect[3]):
            raise ValueError("Plane bounds must have finite increasing U and V limits")
        clip = box(rect[0], rect[2], rect[1], rect[3])

    geometry = project.geometry
    unresolved = project.sheet_join_intent_problems()
    if unresolved:
        raise ValueError("Unresolved sheet joins: " + ", ".join(unresolved))
    notes, paths, edges, sampled = [], [], {}, {}
    groups = defaultdict(list)
    owners = {}
    covered = set()
    for sheet in geometry.sheets.values():
        ids = [geometry.face_uses[u].face_id for u in sheet.face_use_ids]
        owners[("sheet",sheet.id),sheet.name or f"Sheet {sheet.id}"] = [geometry.faces[i] for i in ids]
        covered.update(ids)
    for face in geometry.faces.values():
        if face.id not in covered:
            owners[("face",face.id),f"Face {face.id}"] = [face]
    for (key, label), faces in owners.items():
        for cut, skin in _sheet_cuts(project, faces, view, edges, sampled, notes):
            if not cut.is_empty:
                groups[key, label, "OUTLINE" if skin else "SECTION"].append(cut)
    member_owners = defaultdict(list)
    for member in geometry.members.values():
        for use_id in member.edge_use_ids:
            use = geometry.member_edge_uses[use_id]
            member_owners[use.edge_id].append((("member", member.id), member.name or f"Member {member.id}"))
    for edge_id in sorted(set(project.beam_edges) | member_owners.keys()):
        try:
            if edge_id not in project.edge_sections:
                raise ValueError("no assigned member section")
            cut, _ = _prism_cut(*_beam_prism(project, edge_id), view)
            if not cut.is_empty:
                for key, label in member_owners.get(edge_id, [(("edge", edge_id), f"Edge {edge_id}")]):
                    groups[key, label, "STIFFENERS"].append(cut)
        except ValueError as error:
            notes.append(f"Member edge {edge_id} omitted ({error})")
    for (_, label, layer), parts in groups.items():
        # Merge incidences of the same physical sheet. The roundoff grid is
        # relative to view coordinates and much finer than the drawing chord.
        scale = max(1., *(abs(x) for part in parts for x in part.bounds))
        merged = union_all(parts, grid_size=scale*_ROUND) if len(parts) > 1 else parts[0]
        if clip is not None:
            merged = merged.intersection(clip)
        _append_paths(paths, merged, layer, label)
    def xyz(vector):
        return "(" + ", ".join(f"{float(x):.9g}" if x else "0" for x in vector) + ")"

    notes.extend((f"Plane origin XYZ {xyz(view.origin*1000)} mm",
                  f"Unit normal XYZ {xyz(view.normal)}   Up XYZ {xyz(view.axes[:,1])}"))
    if clip is not None:
        u0, u1, v0, v1 = rect * 1000
        notes.append(f"View limits   U {u0:.9g} to {u1:.9g} mm   V {v0:.9g} to {v1:.9g} mm")
    return DrawingView("Plane section", tuple(paths), tuple(notes),projection="section")
