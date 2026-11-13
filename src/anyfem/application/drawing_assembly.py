"""Orthographic and isometric visible edges of thicknessed planar plate assemblies."""
from dataclasses import dataclass, field
from collections import defaultdict
import math

import numpy as np

from .drawing_views import DrawingPath, DrawingView
from .plate_curves import trace
from .plate_profiles import build_plate_profiles
from .thickness_side import thickness_band


@dataclass
class _Stock:
    caps: tuple
    walls: list
    edges: list
    flanks: list
    # Smooth rulings of curved walls: (segment, outward normal before, after,
    # flanks). Each is drawn only where the wall turns from front to back.
    folds: list = field(default_factory=list)


def _stock(profile):
    if profile._strip is not None:
        return _strip_stock(profile, "solid")
    axes = profile._axes
    normal = np.cross(axes[:,0], axes[:,1])
    low, high = thickness_band(profile.thickness_m, profile.thickness_side)
    if profile.thickness_side == "centre":
        half = profile.thickness_m * normal / 2
        below, above = -half, half
    else:
        below, above = low * normal, high * normal
    bottom, top, walls, edges, flanks = [], [], [], [], []
    for loop in profile._loops:
        # Each trace is confined to 0.05 mm of the CAD curve. Keep only genuine
        # corners as thickness edges; subdivision points have no physical edge.
        lines = [trace(span, tolerance=.00005) for span in loop]
        ring = np.vstack([line[:-1] for line in lines]) @ axes.T + profile._world_origin
        lo, hi = ring+below, ring+above
        bottom.append(lo)
        top.append(hi)
        for i in range(len(ring)):
            j = (i+1) % len(ring)
            walls.append([lo[i], lo[j], hi[j], hi[i]])
            edges.extend([(lo[i],lo[j]), (hi[i],hi[j])])
            into_cap = np.cross(normal,lo[j]-lo[i])
            flanks.extend(((into_cap,normal), (into_cap,-normal)))
        start_index = 0
        for i, line in enumerate(lines):
            before, here = loop[i-1], loop[i]
            a = before[-1,:2]/before[-1,2] - before[-2,:2]/before[-2,2]
            b = here[1,:2]/here[1,2] - here[0,:2]/here[0,2]
            if float(a @ b) < .999999 * np.linalg.norm(a)*np.linalg.norm(b):
                xy = line[0] @ axes.T + profile._world_origin
                edges.append((xy+below,xy+above))
                flanks.append((ring[start_index-1]-ring[start_index],
                               ring[(start_index+1)%len(ring)]-ring[start_index]))
            start_index += len(line)-1
    return _Stock((bottom,top), walls, edges, flanks)


def _oriented(quad, outward):
    quad = np.asarray(quad)
    return quad if np.cross(quad[1]-quad[0], quad[2]-quad[0]) @ outward >= 0 else quad[::-1]


def _strip_stock(profile, representation):
    """Thickened (or, in 2D, reference) walls of a curved strip, in planar facets.

    Curved faces use 0.05 mm chords of the outer surface, as planar plate
    traces; the Sheet boundary carries the thickness walls and edges.
    """
    from .curved_strips import _angles
    from .thickness_side import thickness_band
    strip = profile._strip
    low, high = thickness_band(profile.thickness_m, profile.thickness_side, representation)
    flat = high == low
    offsets = (low,) if flat else (low, high)
    axis = strip.axis
    walls, edges, flanks, folds = [], [], [], []
    for patch in strip.patches:
        angles = strip.sample_angles(patch, patch.radius + high, .00005)
        radial = patch.direction(angles)
        tangents = np.cross(axis, radial)
        for offset in offsets:
            lower, upper = (strip.point(patch, angles, z, offset) for z in patch.axial)
            sense = 1. if offset == high and not flat else -1.
            normals = [sense * (radial[i] + radial[i+1]) for i in range(len(angles)-1)]
            for i, outward in enumerate(normals):
                walls.append(_oriented([lower[i], lower[i+1], upper[i+1], upper[i]], outward))
            exact = sense * radial
            for i in range(len(angles)):
                before = normals[i-1] if i else exact[0]
                after = normals[i] if i < len(normals) else exact[-1]
                folds.append(((lower[i], upper[i]), before, after, (-tangents[i], tangents[i])))
    corners = {}
    for k, points in strip.free_edges:
        patch = strip.patches[k]
        theta, _ = _angles(patch, axis, points)
        radial = patch.direction(theta)
        middle = strip.point(patch, [sum(patch.angles)/2], sum(patch.axial)/2)[0]
        along = np.gradient(points, axis=0)
        into = middle - points
        for vector in (radial, along):
            unit = vector / np.linalg.norm(vector, axis=1)[:, None]
            into = into - np.sum(into*unit, axis=1)[:, None] * unit
        into /= np.linalg.norm(into, axis=1)[:, None]
        for end, direction in ((0, points[1]-points[0]), (-1, points[-2]-points[-1])):
            key = tuple(np.round(points[end], 7))
            corners.setdefault(key, []).append((points[end], radial[end], direction/np.linalg.norm(direction)))
        if flat:
            for i in range(len(points)-1):
                edges.append((points[i], points[i+1]))
                flanks.append((into[i], into[i]))
            continue
        inner, outer = points + low*radial, points + high*radial
        for i in range(len(points)-1):
            walls.append(_oriented([inner[i], inner[i+1], outer[i+1], outer[i]], -into[i]))
            edges.extend([(inner[i], inner[i+1]), (outer[i], outer[i+1])])
            flanks.extend([(into[i], radial[i]), (into[i], -radial[i])])
    if not flat:
        for ends in corners.values():
            if len(ends) == 2 and ends[0][2] @ ends[1][2] > -.999999:  # the boundary turns here
                (point, radial, first), (_, _, second) = ends
                edges.append((point + low*radial, point + high*radial))
                flanks.append((first, second))
    return _Stock(((), ()), walls, edges, flanks, folds)


def _sheet_stock(profile):
    """A zero-thickness sheet at the reference surface: outline and openings only.

    Both caps coincide, so the sheet occludes from either side; each rim edge
    has the sheet itself as its single incident flank.
    """
    if profile._strip is not None:
        return _strip_stock(profile, "midsurface_2d")
    axes = profile._axes
    normal = np.cross(axes[:,0], axes[:,1])
    rings, edges, flanks = [], [], []
    for loop in profile._loops:
        lines = [trace(span, tolerance=.00005) for span in loop]
        ring = np.vstack([line[:-1] for line in lines]) @ axes.T + profile._world_origin
        rings.append(ring)
        for i in range(len(ring)):
            j = (i+1) % len(ring)
            edges.append((ring[i],ring[j]))
            into_sheet = np.cross(normal,ring[j]-ring[i])
            flanks.append((into_sheet,into_sheet))
    return _Stock((rings,rings), [], edges, flanks)


def _interface_edges(polygons, planes, owners, front_faces, index):
    """Creases where two distinct stocks' front surfaces physically intersect."""
    from shapely import LineString

    for i in front_faces:
        for j in index.query(polygons[i]):
            if j <= i or j not in front_faces or owners[i] == owners[j]:
                continue
            # Use the better-conditioned face to lift the projected line to
            # XYZ. Solve the second plane's signed distance = 0 on that face.
            (a, na), (b, nb) = planes[i], planes[j]
            if na[2] < nb[2]:
                (a, na), (b, nb) = (b, nb), (a, na)
            gradient = nb[:2] - (nb[2]/na[2])*na[:2]
            solve = int(np.argmax(abs(gradient)))
            if abs(gradient[solve]) <= 1e-12:
                continue
            bounds_a, bounds_b = polygons[i].bounds, polygons[j].bounds
            low = np.maximum(bounds_a[:2], bounds_b[:2])
            high = np.minimum(bounds_a[2:], bounds_b[2:])
            if np.any(high <= low):
                continue
            middle = (low+high)/2
            def depth(xy):
                return a[2] - (xy-a[:2]) @ na[:2]/na[2]

            distance = (middle-b[:2]) @ nb[:2] + (depth(middle)-b[2])*nb[2]
            # Require a genuine crossing, beyond half-space roundoff, inside
            # the common bounds. Parallel/coplanar contacts have no crease.
            span = ((high-low)/2) @ abs(gradient)
            if abs(distance) >= span - 1e-9:
                continue
            ends = []
            free = 1-solve
            for value in (low[free], high[free]):
                point = middle.copy()
                point[free] = value
                point[solve] -= (distance + (value-middle[free])*gradient[free])/gradient[solve]
                ends.append(point)
            line = LineString(ends).intersection(polygons[i]).intersection(polygons[j])
            for piece in getattr(line, "geoms", (line,)):
                if piece.geom_type == "LineString" and piece.length > 1e-8:
                    start, end = (np.asarray(piece.coords[k]) for k in (0,-1))
                    yield None, np.array([(*start,depth(start)), (*end,depth(end))]), None


def _projection(stocks, direction, up):
    from shapely import LineString, Polygon, STRtree, line_merge, union_all
    n = np.asarray(direction, dtype=float)
    n /= np.linalg.norm(n)
    u = np.cross(up,n)
    u /= np.linalg.norm(u)
    axes = np.column_stack((u, np.cross(n,u), n))
    # Work near the assembly. Imported geometry can be far from world zero;
    # avoid subtracting large, nearly equal camera coordinates in depth tests.
    reference = stocks[0].caps[0][0][0] if stocks[0].caps[0] else stocks[0].walls[0][0]
    view_origin = reference @ axes
    def project(points):
        return (np.asarray(points) - reference) @ axes

    polygons, depths, candidates, owners = [], [], [], []
    front_faces = set()

    def cover(rings, owner, outward=1):
        if not rings:
            return
        outer = rings[0]
        # Store the actual unit plane normal toward the viewer. Interpolating
        # z(x,y) would divide by a nearly zero component on grazing side walls
        # and can put a plate's own rim behind its incident wall.
        p = outer[0]
        cross = np.cross(outer-p, np.roll(outer,-1,axis=0)-p).sum(axis=0)
        if abs(cross[2]) <= max(1e-17, np.linalg.norm(cross)*1e-10):
            return
        polygon = Polygon(outer[:,:2], [r[:,:2] for r in rings[1:]])
        if polygon.is_empty or polygon.area <= 1e-16:
            return
        normal = cross * np.sign(cross[2]) / np.linalg.norm(cross)
        if outward*cross[2] > 0:
            front_faces.add(len(polygons))
        polygons.append(polygon)
        depths.append((p,normal))
        owners.append(owner)

    for owner,stock in enumerate(stocks):
        for cap,outward in zip(stock.caps, (-1,1)):
            cover([project(r) for r in cap], owner, outward)
        for wall in stock.walls:
            cover([project(wall)], owner)
        candidates.extend((owner, segment, flanks) for segment,flanks in
                          zip(project(stock.edges), np.asarray(stock.flanks) @ axes))
        for segment,before,after,flanks in stock.folds:
            # A smooth curved wall shows a silhouette where it turns away.
            if (before @ n) * (after @ n) < 0:
                candidates.append((owner, project(segment), np.asarray(flanks) @ axes))
        for lower,upper in zip(*stock.caps):
            # A smooth rim has no thickness crease but does have a silhouette
            # where neighbouring side walls change from front to back facing.
            facing = np.cross(np.roll(lower,-1,axis=0)-lower,upper-lower) @ n
            for j in np.flatnonzero(facing * np.roll(facing,1) < 0):
                flanks = np.array([lower[j-1]-lower[j],lower[(j+1)%len(lower)]-lower[j]]) @ axes
                candidates.append((owner, project((lower[j],upper[j])), flanks))
    index = STRtree(polygons)
    candidates.extend(_interface_edges(polygons, depths, owners, front_faces, index))
    drawn = []
    interiors = {}
    for owner,(p,q),flanks in candidates:
        delta = q[:2]-p[:2]
        length2 = float(delta @ delta)
        if length2 < 1e-18:
            continue
        line = LineString([p[:2], q[:2]])
        hidden = []
        for j in index.query(line, predicate="intersects"):
            anchor,normal = depths[j]
            d0, d1 = (anchor-p) @ normal, (anchor-q) @ normal
            # Ten nanometres along the sight line, bounded below by the
            # planar precision grid for an almost edge-on face.
            tol = max(1e-8*normal[2], 1e-9)
            first, last = 0., 1.
            if max(d0,d1) <= tol:
                if (owner is None or owner == owners[j] or j not in front_faces or min(d0,d1) < -tol):
                    continue
                # On another stock's face a contact rim is visible only if an
                # incident side of its own plate rises out of that face. This
                # preserves a sitting plate's toe and removes a buried end.
                if np.any(flanks @ normal > 1e-8 * np.linalg.norm(flanks,axis=1)):
                    continue
                if j not in interiors:
                    interiors[j] = polygons[j].buffer(-1e-9)
                overlap = interiors[j].intersection(line)
            else:
                if min(d0,d1) <= tol:
                    crossing = (tol-d0)/(d1-d0)
                    if d0 <= tol:
                        first = crossing
                    else:
                        last = crossing
                overlap = polygons[j].intersection(line)
            for segment in getattr(overlap,"geoms",(overlap,)):
                if segment.geom_type != "LineString" or segment.is_empty:
                    continue
                ts = [(np.asarray(pt)-p[:2]) @ delta/length2 for pt in (segment.coords[0],segment.coords[-1])]
                lo,hi = max(first,min(ts)),min(last,max(ts))
                if hi > lo:
                    hidden.append((lo,hi))
        cursor = 0.
        for lo,hi in sorted(hidden)+[(1.,1.)]:
            if (lo-cursor)*math.sqrt(length2)>1e-8:
                drawn.append(LineString([p[:2]+cursor*delta,p[:2]+lo*delta]))
            cursor = max(cursor,hi)
    if polygons:
        # The boundary of projected stock always separates material from
        # background, including openings passing all the way through.
        drawn.append(union_all(polygons, grid_size=1e-9).boundary)
    merged = line_merge(union_all(drawn,grid_size=1e-9))
    return tuple(DrawingPath("VISIBLE",tuple(map(tuple,np.asarray(g.coords)+view_origin[:2]))) for g in getattr(merged,"geoms",(merged,))
                 if not g.is_empty and g.geom_type == "LineString")


def assembly_views(project, part_id, quantity=1, density=None, *, all_views=True,
                   elevation_datum="global", thickness_sides="auto", representation="solid"):
    """Isometric/plan/front/right views of one plate Part.

    ``thickness_sides`` places each plate's thickness (``auto``, ``centre`` or
    a Sheet-to-side mapping, see :func:`thickness_side.resolve_sides`);
    ``representation="midsurface_2d"`` draws reference surfaces without
    thickness. The takeoff and elevation datum always use the solid plates.
    """
    if isinstance(quantity,bool) or not isinstance(quantity,int) or quantity<1:
        raise ValueError("Quantity must be a positive integer")
    from .thickness_side import check_representation, sides_for_part
    flat = check_representation(representation) == "midsurface_2d"
    profiles = build_plate_profiles(project, part_id,
                                    thickness_sides=sides_for_part(project, part_id, thickness_sides))
    from .drawing_datum import elevation_origin
    base_z = elevation_origin(profiles, elevation_datum)
    part = project.geometry.parts[part_id]
    stocks = [_sheet_stock(p) if flat else _stock(p) for p in profiles]
    gauges = defaultdict(list)
    for p in profiles:
        rho = project.materials[p.material].density if density is None else density
        if not math.isfinite(rho) or rho <= 0:
            raise ValueError(f"Select a positive takeoff density for {p.material}.")
        gauges[p.thickness_m].append(p.mass_area_m2 * p.thickness_m * rho)
    unit_mass = math.fsum(m for masses in gauges.values() for m in masses)
    name = part.name or f"Part {part_id}"
    notes = (f"{quantity} x {name}   |   {len(profiles)} plate marks",
             f"Net plate steel   {unit_mass:.2f} kg each   /   {quantity*unit_mass:.2f} kg total",
             "   /   ".join(f"{t*1000:g} mm   {math.fsum(m):.2f} kg each" for t,m in sorted(gauges.items())),
             f"Elevation datum {elevation_datum}: global Z {base_z*1000:g} mm")
    off_centre = [p for p in profiles if p.thickness_side != "centre"]
    if off_centre:
        counts = {side: sum(p.thickness_side == side for p in profiles) for side in ("front", "back", "centre")}
        notes += ("Thickness sides   " + "   /   ".join(f"{n} {side}" for side, n in counts.items() if n),)
    if flat:
        notes += ("Representation 2D mid-surface: plates drawn at their reference surfaces, no thickness",)
    views = [("Isometric", (1,-1,1),(0,0,1)), ("Plan", (0,0,1),(0,1,0)),
             ("Front", (0,-1,0),(0,0,1)), ("Right", (1,0,0),(0,0,1))]
    result = []
    for label, direction, up in (views if all_views else views[:1]):
        n = np.asarray(direction, dtype=float)
        n /= np.linalg.norm(n)
        u = np.cross(up, n)
        u /= np.linalg.norm(u)
        axes = np.column_stack((u, np.cross(n, u)))
        shift = np.array((0., 0., base_z)) @ axes
        paths = _projection(stocks, direction, up)
        if base_z:
            paths = tuple(DrawingPath(path.layer, tuple(map(tuple, np.asarray(path.points)-shift)),
                                      path.closed, path.label) for path in paths)
        result.append(DrawingView(f"{name}  /  {label}", paths, notes, projection=label.lower()))
    return tuple(result)
