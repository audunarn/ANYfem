"""Orthographic and isometric visible edges of thicknessed planar plate assemblies."""
from dataclasses import dataclass
from collections import defaultdict
import math

import numpy as np

from .drawing_views import DrawingPath, DrawingView
from .plate_curves import trace
from .plate_profiles import build_plate_profiles


@dataclass
class _Stock:
    caps: tuple
    walls: list
    edges: list
    flanks: list


def _stock(profile):
    axes = profile._axes
    normal = np.cross(axes[:,0], axes[:,1])
    half = profile.thickness_m * normal / 2
    bottom, top, walls, edges, flanks = [], [], [], [], []
    for loop in profile._loops:
        # Each trace is confined to 0.05 mm of the CAD curve. Keep only genuine
        # corners as thickness edges; subdivision points have no physical edge.
        lines = [trace(span, tolerance=.00005) for span in loop]
        ring = np.vstack([line[:-1] for line in lines]) @ axes.T + profile._world_origin
        lo, hi = ring-half, ring+half
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
                edges.append((xy-half,xy+half))
                flanks.append((ring[start_index-1]-ring[start_index],
                               ring[(start_index+1)%len(ring)]-ring[start_index]))
            start_index += len(line)-1
    return _Stock((bottom,top), walls, edges, flanks)


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
    reference = stocks[0].caps[0][0][0]
    view_origin = reference @ axes
    def project(points):
        return (np.asarray(points) - reference) @ axes

    polygons, depths, candidates, owners = [], [], [], []
    front_faces = set()

    def cover(rings, owner, outward=1):
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


def assembly_views(project, part_id, quantity=1, density=None, *, all_views=True):
    if isinstance(quantity,bool) or not isinstance(quantity,int) or quantity<1:
        raise ValueError("Quantity must be a positive integer")
    profiles = build_plate_profiles(project, part_id)
    part = project.geometry.parts[part_id]
    stocks = [_stock(p) for p in profiles]
    gauges = defaultdict(list)
    for p in profiles:
        rho = project.materials[p.material].density if density is None else density
        if not math.isfinite(rho) or rho <= 0:
            raise ValueError(f"Select a positive takeoff density for {p.material}.")
        gauges[p.thickness_m].append(p.area_m2 * p.thickness_m * rho)
    unit_mass = math.fsum(m for masses in gauges.values() for m in masses)
    name = part.name or f"Part {part_id}"
    notes = (f"{quantity} x {name}   |   {len(profiles)} plate marks",
             f"Net plate steel   {unit_mass:.2f} kg each   /   {quantity*unit_mass:.2f} kg total",
             "   /   ".join(f"{t*1000:g} mm   {math.fsum(m):.2f} kg each" for t,m in sorted(gauges.items())))
    views = [("Isometric", (1,-1,1),(0,0,1)), ("Plan", (0,0,1),(0,1,0)),
             ("Front", (0,-1,0),(0,0,1)), ("Right", (1,0,0),(0,0,1))]
    return tuple(DrawingView(f"{name}  /  {label}", _projection(stocks, direction, up), notes,projection=label.lower())
                 for label,direction,up in (views if all_views else views[:1]))
