"""Plate splitting orchestration; all intersection geometry belongs to ANYgeometry."""
from dataclasses import dataclass
from itertools import product

import numpy as np
from anygeometry import (GeometryError, Plane, MutationPolicy,
                         IntersectionDimension, query_intersection, plan_imprint,
                         apply_imprint)


@dataclass(frozen=True)
class PlateCutResult:
    target_faces: tuple[int, ...]
    cutter_faces: tuple[int, ...]
    trace_edges: tuple[int, ...]
    replacements: tuple
    diagnostic: str = "Exact cut materialized through ANYgeometry."


def cut_plate(geometry, face_id, *, cutter_face_id=None, origin=None, normal=None):
    """Build an exact detached imprint, preserving both sides of each plate.

    A plane is represented by a temporary face covering the target's complete
    conservative AABB. The temporary face and its unused topology are removed
    after the owner publishes the trace. A plate cutter keeps its finite extent.
    """
    if face_id not in geometry.faces:
        raise GeometryError("Select an active target plate face.")
    if ((cutter_face_id is not None and (origin is not None or normal is not None))
            or (cutter_face_id is None and (origin is None or normal is None))):
        raise GeometryError("Choose either a cutter plate or an explicit plane origin and normal.")
    if cutter_face_id == face_id:
        raise GeometryError("The target and cutter must be different plates.")
    # Qualify topology independently of the pending consumer feature record.
    # The feature executor retains its history and publishes this topology.
    working = geometry.clone(include_features=False)
    original_edges, original_vertices = set(working.edges), set(working.vertices)
    temporary = cutter_face_id is None
    if temporary:
        point, direction = np.asarray(origin, dtype=float), np.asarray(normal, dtype=float)
        if point.shape != (3,) or direction.shape != (3,) or not np.all(np.isfinite([point, direction])) or np.linalg.norm(direction) == 0:
            raise GeometryError("Plane needs a finite origin and a nonzero finite normal.")
        direction = direction / np.linalg.norm(direction)
        seed = np.eye(3)[int(np.argmin(np.abs(direction)))]
        u = np.cross(direction, seed); u /= np.linalg.norm(u)
        v = np.cross(direction, u)
        bounds = working.conservative_face_bounds(face_id)
        if bounds is None:
            raise GeometryError("The geometry owner cannot bound this plate for an infinite plane cut.")
        corners = np.array(tuple(product(*zip(bounds[:3], bounds[3:]))))
        uv = np.column_stack(((corners-point) @ u, (corners-point) @ v))
        low, high = uv.min(axis=0), uv.max(axis=0)
        margin = max(float(np.linalg.norm(np.asarray(bounds[3:])-bounds[:3])), 1.0)
        low -= margin; high += margin
        support = Plane(point + low[0]*u + low[1]*v,
                        (high[0]-low[0])*u, (high[1]-low[1])*v)
        vertices = [working.add_point(*support.evaluate(a,b)) for a,b in ((0,0),(1,0),(1,1),(0,1))]
        cutter_face_id = working.add_face(working.add_polyline(vertices, close=True), (0,1,2,3), surface=support)
    elif cutter_face_id not in working.faces:
        raise GeometryError("Select an active cutter plate face.")
    working.begin_replacement_log()
    query = query_intersection(working, working.handle("face", face_id), working.handle("face", cutter_face_id))
    if not query.classified or query.dimension != IntersectionDimension.CURVE:
        detail = "; ".join(query.diagnostics)
        raise GeometryError("Cut requires a qualified crossing curve; tangent, coplanar, disjoint or unsupported cuts are refused. " + detail)
    application = apply_imprint(working, plan_imprint(working, query, policy=MutationPolicy.IMPRINT), policy=MutationPolicy.IMPRINT)
    intersection = application.face_intersection
    if intersection is None or not intersection.edges:
        raise GeometryError("The owner produced no selectable cut edges.")
    replacements = tuple((old, tuple(new for new in descendants if new.kind == old.kind))
                         for old, descendants in working.replacement_log()
                         if not (temporary and old.kind == "face" and old.id == cutter_face_id))
    if temporary:
        for ref in intersection.second_faces:
            working.remove_face(ref.id)
        for edge in tuple(set(working.edges)-original_edges):
            if not working.faces_using_edge(edge):
                working.remove_edge(edge)
        for vertex in tuple(set(working.vertices)-original_vertices):
            if not working.edges_using_vertex(vertex):
                working.remove_vertex(vertex)
    trace = tuple(ref.id for ref in intersection.edges)
    if any(edge not in working.edges for edge in trace):
        raise GeometryError("Cut trace did not survive temporary plane removal.")
    target = tuple(ref.id for ref in intersection.first_faces)
    cutter = () if temporary else tuple(ref.id for ref in intersection.second_faces)
    result = PlateCutResult(target, cutter, trace, replacements)
    return working, result
