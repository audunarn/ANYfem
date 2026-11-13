"""Face-picked plate drawings. All coordinates refer to the native shell surface (m)."""
from __future__ import annotations

from collections import defaultdict
from math import ceil, comb, floor, pi, tau

import numpy as np
from anygeometry.surfaces import Cylinder

from .drawing_views import DrawingPath, DrawingView
from .plate_curves import edge_spans, trace
from .plate_profiles import _face_rings, _planar_geometry

try:
    from anygeometry.exact_curves import EllipticArc
except ImportError:  # Older supported geometry owners predate this curve.
    EllipticArc = ()


# World curves used for preview or developed polylines have a 5 micrometre
# chord tolerance. CAD planar contours retain their original rational poles.
_TRACE = 5e-6
_GAP = 1e-8
_GRID = 1e-10


def _path(layer, points, *, closed=False, label=""):
    return DrawingPath(layer, tuple((float(x), float(y)) for x, y in points), closed, label)


def _trace_spans(spans, tol=_TRACE):
    pieces = [trace(span, tolerance=tol) for span in spans]
    if any(np.linalg.norm(a[-1] - b[0]) > _GAP for a, b in zip(pieces, pieces[1:])):
        raise ValueError("Gap between native curve spans exceeds 1e-8 m")
    return np.vstack([piece[:-1] for piece in pieces] + [pieces[-1][-1:]])


def _native_spans(g, edge_id):
    edge = g.edges[edge_id]
    if isinstance(edge.curve, EllipticArc):
        # A sloping plane cuts a cylinder in an affine ellipse. Map the
        # standard rational quadratic circle spans through that affine map.
        curve = edge.curve
        steps = max(1, ceil(abs(curve.sweep_angle)/(pi/2)))
        angles = np.linspace(curve.start_angle, curve.start_angle + curve.sweep_angle, steps+1)
        spans = []
        for first, last in zip(angles, angles[1:]):
            t = np.array([first, .5*(first+last), last])
            w = np.array([1., np.cos(.5*(last-first)), 1.])
            xyz = (np.outer(w, curve.center) + np.outer(np.cos(t), curve.u_vector)
                   + np.outer(np.sin(t), curve.v_vector))
            spans.append(np.column_stack((xyz, w)))
    else:
        spans = edge_spans(g, edge_id)
    for pole, vertex in ((spans[0][0], edge.start), (spans[-1][-1], edge.end)):
        if np.linalg.norm(pole[:3] / pole[3] - g.vertices[vertex].position) > _GAP:
            raise ValueError(f"Edge {edge_id} endpoints disagree with its topology")
    return spans


def _planar(project, sheet, faces):
    from shapely import LineString, Polygon

    profile = _planar_geometry(project.geometry, faces, sheet)
    rings = [_trace_spans(spans) for spans in profile.loops]
    polygon = Polygon(rings[0], rings[1:])
    normal = np.cross(*profile.axes.T)
    tol = project.geometry.tolerance.effective_surface_residual(np.ptp(rings[0], axis=0).max())

    def develop(edge_id):
        spans = _native_spans(project.geometry, edge_id)
        for span in spans:
            poles = span[:, :3] / span[:, 3:] - profile.origin
            if np.max(np.abs(poles @ normal)) > tol:
                raise ValueError(f"Member edge {edge_id} is off the selected plate plane")
        points = (_trace_spans(spans) - profile.origin) @ profile.axes
        return [LineString(points)]

    return polygon, develop, profile.loops, ()


def _check_cylinder_span(span, cylinder, tol, edge_id):
    """Bound radial squared residual on the whole rational Bezier span.

    R²(w²) and the squared radial numerator have Bernstein degree 2n.
    Their difference is bounded by the extrema of its Bernstein coefficients.
    """
    w = span[:, -1]
    delta = span[:, :3] - w[:, None] * cylinder.origin
    radial = delta - (delta @ cylinder.axis)[:, None] * cylinder.axis
    degree = len(w) - 1
    residual = np.zeros(2 * degree + 1)
    for i in range(degree + 1):
        for j in range(degree + 1):
            coefficient = comb(degree, i) * comb(degree, j) / comb(2 * degree, i+j)
            residual[i+j] += coefficient * (radial[i] @ radial[j] - cylinder.radius**2 * w[i]*w[j])
    if not np.isfinite(residual).all() or max(abs(residual)) > tol * cylinder.radius * min(w)**2:
        raise ValueError(f"Edge {edge_id} cannot be verified on the reference cylinder")


def _periodic_parts(shape, cut, period, shape_type):
    """Translate material into one period, splitting paths that cross its cut."""
    from shapely import box
    from shapely.affinity import translate

    xmin, ymin, xmax, ymax = shape.bounds
    pieces = []
    # A meridian coincident with the drawing cut belongs on one edge only.
    if xmax - xmin <= _GRID:
        k = floor((.5*(xmin+xmax) - cut + _GRID) / period)
        return [translate(shape, xoff=-cut-k*period)]
    for k in range(floor((xmin-cut)/period), floor((xmax-cut)/period) + 1):
        left = cut + k*period
        window = box(left, ymin-1, left+period, ymax+1)
        clipped = shape.intersection(window)
        pending = [clipped]
        while pending:
            part = pending.pop()
            if isinstance(part, shape_type) and not part.is_empty:
                pieces.append(translate(part, xoff=-left))
            elif hasattr(part, "geoms"):
                pending.extend(reversed(part.geoms))
    return pieces


def _cylinder(project, sheet, faces):
    from shapely import LineString, Polygon, union_all

    g = project.geometry
    cylinder = faces[0].surface
    radius = cylinder.radius
    period = tau * radius
    tol = g.tolerance.effective_surface_residual(max(radius, *(abs(f.surface.height) for f in faces)))
    for face in faces:
        surface = face.surface
        offset = surface.origin - cylinder.origin
        if (abs(surface.radius-radius) > tol or
                np.linalg.norm(np.cross(surface.axis, cylinder.axis))*abs(surface.height) > tol or
                np.linalg.norm(offset-(offset @ cylinder.axis)*cylinder.axis) > tol):
            raise ValueError("Sheet faces have different reference cylinders")

    edge_paths = {}

    def edge_path(edge_id):
        if edge_id not in edge_paths:
            spans = _native_spans(g, edge_id)
            for span in spans:
                _check_cylinder_span(span, cylinder, tol, edge_id)
            points = _trace_spans(spans, min(_TRACE, radius/16)) - cylinder.origin
            angles = np.unwrap(np.arctan2(points @ cylinder.circumferential_direction,
                                          points @ cylinder.radial_direction))
            coords = np.column_stack((radius*angles, points @ cylinder.axis))
            edge_paths[edge_id] = np.asarray(LineString(coords).simplify(_GRID).coords)
        return edge_paths[edge_id]

    incidence = defaultdict(list)
    patches = []
    for face_id, face_rings in _face_rings(g, faces, sheet).items():
        rings = []
        for ring in face_rings:
            pieces = []
            for edge_id, forward in ring:
                path = edge_path(edge_id)
                if not forward:
                    path = path[::-1]
                if pieces:
                    shift = round((pieces[-1][-1, 0] - path[0, 0]) / period)
                    path = path + (shift*period, 0.)
                    if np.linalg.norm(pieces[-1][-1] - path[0]) > _GAP:
                        raise ValueError(f"Open cylindrical trim at edge {edge_id}")
                pieces.append(path)
                incidence[edge_id].append((face_id, forward))
            if np.linalg.norm(pieces[-1][-1] - pieces[0][0]) > _GAP:
                raise ValueError("A winding cylindrical trim needs an explicit meridian cut")
            rings.append(np.vstack([piece[:-1] for piece in pieces]))
        outer = Polygon(rings[0])
        if not outer.is_valid or outer.area <= 0 or outer.bounds[2]-outer.bounds[0] > period + _GAP:
            raise ValueError(f"Invalid cylindrical outer trim on face {face_id}")
        holes = []
        for ring in rings[1:]:
            # Use containment to choose the copy of a hole, including a hole
            # which straddles the angle convention's -pi/pi branch.
            turns = round((outer.centroid.x - ring[:, 0].mean())/period)
            candidates = [ring + (k*period, 0.) for k in (turns-1, turns, turns+1)]
            found = [ring for ring in candidates if outer.contains(Polygon(ring))]
            if len(found) != 1:
                raise ValueError(f"Ambiguous cylindrical hole placement on face {face_id}")
            holes.append(found[0])
        patch = Polygon(rings[0], holes)
        if not patch.is_valid or patch.area <= 0:
            raise ValueError(f"Invalid cylindrical holes on face {face_id}")
        patches.append(patch)

    neighbors = defaultdict(set)
    for edge_id, uses in incidence.items():
        if len(uses) == 1:
            continue
        if len(uses) != 2 or uses[0][0] == uses[1][0] or uses[0][1] == uses[1][1]:
            raise ValueError(f"Conflicting cylindrical sheet incidence on edge {edge_id}")
        a, b = (item[0] for item in uses)
        neighbors[a].add(b)
        neighbors[b].add(a)
    reached = set()
    todo = [faces[0].id]
    while todo:
        current = todo.pop()
        if current not in reached:
            reached.add(current)
            todo.extend(neighbors[current] - reached)
    if len(reached) != len(faces):
        raise ValueError("Cylinder faces lack shared native edges connecting the sheet")

    # Prefer an unoccupied angle for partial sheets. A complete shell opens
    # at the minimum angular extent of its first face, usually a native seam.
    intervals = []
    for patch in patches:
        lo, _, hi, _ = patch.bounds
        k = floor(lo/period)
        lo, hi = lo-k*period, hi-k*period
        intervals.append((lo, min(period, hi)))
        if hi > period:
            intervals.append((0., hi-period))
    end = 0.
    cut = patches[0].bounds[0]
    full = True
    for lo, hi in sorted(intervals) + [(period, period)]:
        if lo-end > _GAP:
            cut = (end+lo)/2
            full = False
            break
        end = max(end, hi)

    polys = [poly for patch in patches for poly in _periodic_parts(patch, cut, period, Polygon)]
    physical = union_all(polys, grid_size=_GRID)
    if not isinstance(physical, Polygon) or not physical.is_valid or physical.area <= 0:
        raise ValueError("Cylindrical trims do not develop into one connected plate at this meridian")
    if abs(sum(patch.area for patch in patches)-physical.area) > _GAP * physical.length:
        raise ValueError("Cylindrical patches overlap in the development")

    def develop(edge_id):
        return _periodic_parts(LineString(edge_path(edge_id)), cut, period, LineString)

    # A native free edge must survive on the physical contour. A coincident
    # geometric line belonging to two different free edges is not a weld.
    boundary = physical.boundary.buffer(_GAP)
    for edge_id, uses in incidence.items():
        if len(uses) == 1 and any(not boundary.covers(line) for line in develop(edge_id)):
            raise ValueError(f"Unjoined native edge {edge_id} would disappear in the development")

    from shapely.affinity import translate
    x0, y0, _, _ = physical.bounds

    def local_develop(edge_id):
        return [translate(line, -x0, -y0) for line in develop(edge_id)]

    def develop_points(points):
        points = np.asarray(points) - cylinder.origin
        angles = np.unwrap(np.arctan2(points @ cylinder.circumferential_direction,
                                     points @ cylinder.radial_direction))
        coords = np.column_stack((radius*angles, points @ cylinder.axis))
        return [translate(line, -x0, -y0) for line in _periodic_parts(
            LineString(coords), cut, period, LineString)]

    notes = [f"Cylindrical development on the shell reference surface, radius {radius*1000:g} mm.",
             "Curves traced with 0.005 mm chord tolerance on the reference surface."]
    if full:
        angle = np.degrees(cut/radius) % 360
        notes.append(f"Opened at {angle:g} degrees from face {faces[0].id}'s radial direction.")
    return translate(physical, -x0, -y0), local_develop, (), tuple(notes), develop_points


_ON_PLATE = {"member_on_face", "member_on_boundary", "member_on_face_boundary",
             "member_on_sheet", "member_on_sheet_intersection"}


def _member_edges(project, sheet, face_ids, edges):
    g = project.geometry
    candidates = set()
    sheet_id = None if sheet is None else sheet.id
    for edge_id in edges:
        members = g.members_using_edge(edge_id)
        if edge_id not in project.edge_sections:
            members = [m for m in members if sheet and g.members[m].part_id == sheet.part_id]
            if not members:
                continue
        if len(members) != 1:
            raise ValueError(f"Assigned plate edge {edge_id} has ambiguous Member ownership")
        candidates.add((members[0], edge_id))

    connected = set()
    disconnected = set()
    targets = {"face": face_ids, "edge": edges, "sheet": {sheet_id}}
    for attachment in g.attachments.values():
        if attachment.kind not in _ON_PLATE | {"intentionally_disconnected"} or attachment.member_id is None:
            continue
        matches = attachment.target_id in targets.get(attachment.target_kind, set())
        if attachment.sheet_id is not None and attachment.sheet_id != sheet_id:
            if matches and attachment.target_kind == "face":
                raise ValueError(f"Attachment {attachment.id} names a conflicting face and sheet")
            continue
        if not matches and (sheet_id is None or attachment.sheet_id != sheet_id):
            continue
        if not matches:
            raise ValueError(f"Attachment {attachment.id} has an inconsistent target on sheet {sheet_id}")
        mi = attachment.member_id
        member = g.members[mi]
        for uid in member.edge_use_ids:
            use = g.member_edge_uses[uid]
            left = max(use.parent_range.start, attachment.member_range.start)
            right = min(use.parent_range.end, attachment.member_range.end)
            if left >= right:
                continue
            if left > use.parent_range.start or right < use.parent_range.end:
                raise ValueError(f"Attachment {attachment.id} spans only part of member edge {use.edge_id}")
            if (attachment.kind == "intentionally_disconnected" or
                    attachment.connection_intent in {"keep_disconnected", "contact_only", "reject"}):
                disconnected.add((mi, use.edge_id))
            else:
                connected.add((mi, use.edge_id))
    if connected & disconnected:
        raise ValueError("Conflicting connection intents for a member on the selected plate")
    return (candidates | connected) - disconnected


def _stiffeners(project, sheet, face_ids, edges, polygon, develop):
    g = project.geometry
    candidates = _member_edges(project, sheet, face_ids, edges)
    paths = []
    covered = polygon.buffer(2*_TRACE + _GAP)
    for member_id in sorted({m for m, _ in candidates}):
        member = g.members[member_id]
        label = f"Member {member_id} {member.name}".rstrip()
        previous_vertex = None
        run = []
        name = ""
        for use_id in member.edge_use_ids:
            use = g.member_edge_uses[use_id]
            edge_id = use.edge_id
            if (member_id, edge_id) not in candidates:
                previous_vertex = None
                continue
            edge = g.edges[edge_id]
            start, end = edge.start, edge.end
            if edge_id in project.edge_sections:
                section = project.beam_section_of(edge_id)
                edge_label = f"{label} | {section.name} ({section.profile})"
            else:
                edge_label = f"{label} | section unassigned"
            lines = develop(edge_id)
            if not lines or any(not covered.covers(line) for line in lines):
                raise ValueError(f"Member {member_id} edge {edge_id} extends outside the plate or over a hole")
            segments = [list(line.coords) for line in lines]
            if use.orientation.value < 0:
                start, end = end, start
                segments = [piece[::-1] for piece in reversed(segments)]
            for i, points in enumerate(segments):
                # Join contiguous native member uses only. A cut meridian or a
                # different profile starts a new line and label in the drawing.
                if (i == 0 and previous_vertex == start and name == edge_label and run and
                        np.linalg.norm(np.subtract(points[0], run[-1])) <= _GAP):
                    run.extend(points[1:])
                else:
                    if run:
                        paths.append(_path("STIFFENERS", run, label=name))
                    run, name = list(points), edge_label
            previous_vertex = end
        if run:
            paths.append(_path("STIFFENERS", run, label=name))
    return paths


def build_plate_view(project, face_id: int, *, include_stiffeners: bool = True,
                     include_plate_intersections: bool = True) -> DrawingView:
    """Draw the entire Sheet owning a face, including its attached member axes."""
    g = project.geometry
    uses = [use for use in g.face_uses.values() if use.face_id == face_id]
    if face_id not in g.faces:
        raise ValueError(f"Unknown native face {face_id}")
    if len(uses) > 1:
        raise ValueError(f"Face {face_id} has ambiguous physical Sheet ownership")
    sheet = g.sheets[uses[0].sheet_id] if uses else None
    if sheet:
        faces = [g.faces[g.face_uses[uid].face_id] for uid in sheet.face_use_ids]
    else:
        faces = [g.faces[face_id]]
    face_ids = {f.id for f in faces}
    if sheet and (len(face_ids) != len(faces) or
                  any(u.sheet_id != sheet.id and u.face_id in face_ids for u in g.face_uses.values())):
        raise ValueError(f"Sheet {sheet.id} contains faces with ambiguous physical ownership")
    problems = project.sheet_join_intent_problems()
    if problems:
        raise ValueError("Unresolved sheet ownership: " + ", ".join(problems))
    sections = [project.plate_section_of(f.id) for f in faces if f.id in project.face_sections]
    properties = {(s.thickness, s.material) for s in sections}
    if len(properties) > 1 or (sections and len(sections) != len(faces)):
        raise ValueError("Sheet faces have conflicting or incomplete plate thickness or material")
    if properties:
        thickness, material = properties.pop()
        if material not in project.materials:
            raise ValueError(f"Missing plate material {material}")
        stock = f"{material} | plate {thickness*1000:g} mm"
    else:
        thickness = 0.
        stock = "plate thickness and material unassigned"
    cylinders = [isinstance(f.surface, Cylinder) for f in faces]
    if any(cylinders) and not all(cylinders):
        raise ValueError("Sheet combines cylindrical and other surfaces; separate plate developments are required")
    try:
        develop_points = None
        if all(cylinders):
            physical, develop, exact, notes, develop_points = _cylinder(project, sheet, faces)
        else:
            physical, develop, exact, notes = _planar(project, sheet, faces)
    except ImportError as error:
        raise ImportError("Install ANYfem's drawings extra to develop plate contours") from error
    paths = [_path("OUTLINE", list(physical.exterior.coords)[:-1], closed=True)]
    paths.extend(_path("HOLES", list(r.coords)[:-1], closed=True) for r in physical.interiors)
    if include_plate_intersections:
        from .plate_intersections import plate_intersections
        references, intersection_notes = plate_intersections(
            project, sheet, faces, physical, thickness, cylindrical=all(cylinders), develop_points=develop_points)
        paths.extend(references)
        notes = (*notes, *intersection_notes)
    if include_stiffeners:
        edges = {e.edge for f in faces for loop in (f.loop, *f.holes) for e in loop}
        paths.extend(_stiffeners(project, sheet, face_ids, edges, physical, develop))
    owner = f"Sheet {sheet.id}" if sheet else f"Face {face_id}"
    mark = (sheet.name or owner) if sheet else owner
    return DrawingView(mark, tuple(paths), (f"{owner} | {stock}", *notes), exact, projection="plate")
