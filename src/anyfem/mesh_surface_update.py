"""Regional update of one source-owned flat plate (interior holes and local size zones).

``capture_surface_baseline(project, settings, mesh)``
    After any generation of a *single flat plate* (explicit ``Plane`` surface, or an
    exact four-sided face whose straight boundary is planar) record what
    a later update must prove unchanged: the support, the outer boundary loop, every
    hole loop, the point and sizing-edge refinements, the edge-division overrides, the global
    settings, and an exact numbered binding of the mesh.  The mesh is verified against the
    geometry first (independent coverage proof); an unverifiable mesh is ineligible
    and ``None`` is returned (never blocks a full generation).

``plan_surface_update(project, settings, baseline)``
    Diff the *actual* current source definitions against the baseline's.  Covered
    changes (``plan.applicable`` is true only when every change is covered):
    a hole added, a hole removed (material added back), a hole moved or re-divided
    (remove + add), and a refinement zone around a point or an unassociated straight or spline edge
    added, removed or changed.  Anything else is an explicit blocker; a change that
    reaches the support/outer boundary or a retained hole is a *boundary mismatch*
    (the only case that allows a fallback to a full remesh).

``generate_surface_update(project, settings, baseline, old_mesh, ...)``
    Drive the owner producer ``anymesher.regional_patch.remesh_region``.  The
    changed footprint is analytic (hole hull widened by half a local element, or the
    circumscribed polygon of the size-field influence disc), the affected *full*
    cells are replaced, the rest keeps ids/coordinates/connectivity bit-for-bit.
    The explicit quality limits, the id high-water marks and a source check that is
    computed here from the geometry (not trusted from a caller) are handed to the
    owner.  The result is re-verified, a new baseline is captured from the returned
    mesh, and the provenance states what was and was not checked.

Scope limits (fail closed, see ``SurfacePlan.blockers``): one face; straight edges
only; no beams/members/junctions/couplings; sizing sources are points or unassociated straight/Bezier edges and
the replaced patch is meshed at one uniform size (the finest size the *new* size
field asks inside the footprint) because the owner planar kernel has no graded
size input.  Owner gap handled here and stated: the owner publishes one geometry
edge id per added hole loop, a hole bounded by several geometry edges needs one
association per edge, so this module re-publishes the per-edge/vertex
associations from exact station coordinates and re-validates the mesh with the
owner's ``verify_regional_patch``.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import numpy as np

from .regional_mesh import (
    _check_fn,
    _decode,
    _digest,
    _global_state,
    _hex,
    _document,
    _pins,
    _plain,
    _validate_quality,
    _strategy,
)

__all__ = [
    "SurfaceBoundaryMismatch",
    "SurfacePlan",
    "SurfaceUpdateError",
    "SurfaceUpdateResult",
    "SurfaceUpdateUnsupported",
    "capture_surface_baseline",
    "explain_surface_ineligibility",
    "generate_surface_update",
    "plan_surface_update",
]

SCHEMA = "anyfem.surface-baseline"
VERSION = 1
_DISC_SIDES = 32
_PSEUDO_EDGE_BASE = 10**9

# Owner refusals that mean "the replaced region's boundary cannot be honoured".
_BOUNDARY_CODES = frozenset({
    "INTERFACE_MISMATCH", "INTERFACE_NOT_PRESERVED", "PATCH_BOUNDARY_MISMATCH",
    "STATION_NOT_ON_REGION_BOUNDARY", "SURFACE_BOUNDARY_MISMATCH",
})


# ---------------------------------------------------------------------- errors


class SurfaceUpdateError(RuntimeError):
    """A surface update failed closed.  ``fallback_allowed`` is False unless a boundary mismatch."""

    fallback_allowed = False

    def __init__(self, message: str, *, code: str = "SURFACE_UPDATE_FAILED",
                 details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class SurfaceUpdateUnsupported(SurfaceUpdateError):
    """Outside the supported scope (no fallback; the caller decides)."""


class SurfaceBoundaryMismatch(SurfaceUpdateUnsupported):
    """Typed diagnostic: the support/outer boundary or a retained hole is affected."""

    fallback_allowed = True


class _Ineligible(Exception):
    pass


# ------------------------------------------------------------------ 2D helpers


def _o(a, b, c) -> int:
    v = (float(b[0]) - float(a[0])) * (float(c[1]) - float(a[1])) \
        - (float(b[1]) - float(a[1])) * (float(c[0]) - float(a[0]))
    return int(v > 0.0) - int(v < 0.0)


def _on_seg(p, a, b) -> bool:
    return (_o(a, b, p) == 0 and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def _hit(p1, p2, q1, q2) -> bool:
    """Closed segments share a point (touching counts)."""

    if _o(p1, p2, q1) * _o(p1, p2, q2) < 0 and _o(q1, q2, p1) * _o(q1, q2, p2) < 0:
        return True
    return (_on_seg(q1, p1, p2) or _on_seg(q2, p1, p2)
            or _on_seg(p1, q1, q2) or _on_seg(p2, q1, q2))


def _strict_in(p, poly) -> bool:
    n = len(poly)
    for i in range(n):
        if _on_seg(p, poly[i], poly[(i + 1) % n]):
            return False
    inside = False
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]):
            if a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1]) > p[0]:
                inside = not inside
    return inside


def _centroid(poly):
    return (sum(float(p[0]) for p in poly) / len(poly), sum(float(p[1]) for p in poly) / len(poly))


def _area2(poly) -> float:
    return 0.5 * math.fsum(
        poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
        for i in range(len(poly)))


def _overlap(cell, poly) -> bool:
    """Interior overlap by sign tests; touching alone is not overlap."""

    n, m = len(cell), len(poly)
    for i in range(n):
        p1, p2 = cell[i], cell[(i + 1) % n]
        for j in range(m):
            q1, q2 = poly[j], poly[(j + 1) % m]
            if _o(p1, p2, q1) * _o(p1, p2, q2) < 0 and _o(q1, q2, p1) * _o(q1, q2, p2) < 0:
                return True
    return (any(_strict_in(v, poly) for v in cell) or any(_strict_in(v, cell) for v in poly)
            or _strict_in(_centroid(cell), poly) or _strict_in(_centroid(poly), cell))


def _touch(a, b) -> bool:
    """Any shared point, crossing, or containment between two simple polygons."""

    for i in range(len(a)):
        for j in range(len(b)):
            if _hit(a[i], a[(i + 1) % len(a)], b[j], b[(j + 1) % len(b)]):
                return True
    return _strict_in(a[0], b) or _strict_in(b[0], a)


def _hull(points):
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) < 3:
        return pts

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and _o(out[-2], out[-1], p) <= 0:
                out.pop()
            out.append(p)
        return out

    lower, upper = half(pts), half(reversed(pts))
    return lower[:-1] + upper[:-1]


def _inside_domain(poly, outer) -> bool:
    """Polygon lies strictly inside ``outer`` (no touching of the outer boundary)."""

    if not all(_strict_in(p, outer) for p in poly):
        return False
    for i in range(len(poly)):
        for j in range(len(outer)):
            if _hit(poly[i], poly[(i + 1) % len(poly)], outer[j], outer[(j + 1) % len(outer)]):
                return False
    return True


class _Chart:
    """Working frame of the source plate, built from its outer loop only."""

    def __init__(self, source: Mapping[str, Any]) -> None:
        pts = _points(source["outer"])
        origin = pts[0]
        normal = np.zeros(3)
        for i in range(len(pts)):
            normal += np.cross(pts[i] - origin, pts[(i + 1) % len(pts)] - origin)
        length = float(np.linalg.norm(normal))
        if length == 0.0:
            raise _Ineligible("outer loop has no area")
        normal = normal / length
        edge = pts[1] - pts[0]
        e1 = edge - normal * float(edge @ normal)
        e1 = e1 / float(np.linalg.norm(e1))
        self.origin, self.e1, self.normal = origin, e1, normal
        self.e2 = np.cross(normal, e1)

    def to2(self, xyz) -> tuple[float, float]:
        d = np.asarray(xyz, dtype=float) - self.origin
        return (float(d @ self.e1), float(d @ self.e2))

    def to3(self, xy) -> tuple[float, float, float]:
        p = self.origin + float(xy[0]) * self.e1 + float(xy[1]) * self.e2
        return (float(p[0]), float(p[1]), float(p[2]))

    def height(self, xyz) -> float:
        return float((np.asarray(xyz, dtype=float) - self.origin) @ self.normal)


def _points(record: Mapping[str, Any]) -> np.ndarray:
    return np.array([float.fromhex(s) for s in record["points"]], dtype=float).reshape(-1, 3)


def _xy(chart: _Chart, record: Mapping[str, Any]) -> list[tuple[float, float]]:
    return [chart.to2(p) for p in _points(record)]


# ---------------------------------------------------------------- source model


def _loop_record(geometry: Any, loop: Any, overrides: Mapping[int, int]) -> dict[str, Any]:
    if len(loop) < 3:
        raise _Ineligible("a loop needs at least three edges")
    edges, ends = [], []
    for use in loop:
        edge = geometry.edges[int(use.edge)]
        if type(edge.curve).__name__ != "Straight":
            raise _Ineligible(f"edge {int(use.edge)} is {type(edge.curve).__name__}; only straight edges are supported")
        a, b = (edge.start, edge.end) if use.forward else (edge.end, edge.start)
        edges.append([int(use.edge), bool(use.forward)])
        ends.append((int(a), int(b)))
    for i, (_, b) in enumerate(ends):
        if b != ends[(i + 1) % len(ends)][0]:
            raise _Ineligible("loop edges are not contiguous")
    vertices = [a for a, _ in ends]
    points = [np.asarray(geometry.vertex_position(v), dtype=float) for v in vertices]
    ids = {e for e, _ in edges}
    return {
        "edges": edges,
        "vertices": vertices,
        "points": _hex(points),
        "overrides": [[e, int(n)] for e, n in sorted(overrides.items()) if e in ids],
    }


def _sizing_edge_is_free(project, edge_id, vertices, overrides):
    """A sizing-only edge and its controls may not also define a mesh constraint."""
    from .io.project_file import project_to_dict

    g = project.geometry
    if (g.faces_using_edge(edge_id) or g.members_using_edge(edge_id) or edge_id in project.beam_edges
            or edge_id in overrides or edge_id in project.seeding_overrides
            or getattr(project.native_mesh_settings, "controls", ())
            or any(zone.mode == "override" and zone.ref.kind == "edge" and zone.ref.id == edge_id
                   for zone in project.refinements)):
        return False
    for other_id, edge in g.edges.items():
        points = {edge.start, edge.end, getattr(edge.curve, "via_vertex", None),
                  *getattr(edge.curve, "control_vertices", ())}
        if other_id != edge_id and points.intersection(vertices):
            return False
    keys = {("edge", edge_id), *(("vertex", v) for v in vertices)}
    if any((r.kind, r.id) in keys for refs in g.groups.values() for r in refs):
        return False
    from anygeometry.entities import EntityRef
    for region in project.regions:
        if region.domain.value == "geometry" and region.entity_kind in ("edge", "vertex"):
            candidates = tuple(EntityRef(kind, i) for kind, i in keys if kind == region.entity_kind)
            resolved = project.regions.resolve(region.id, geometry=g, candidates=candidates,
                                               feature_resolver=lambda anchor: g.features.resolve(anchor, g))
            if any(r in resolved for r in candidates):
                return False

    def names_edge(value):
        if isinstance(value, dict):
            if value.get("kind") in ("edge", "vertex") and isinstance(value.get("id"), int):
                if (value["kind"], value["id"]) in keys:
                    return True
            return any(names_edge(v) for v in value.values())
        return isinstance(value, (list, tuple)) and any(names_edge(v) for v in value)

    data = project_to_dict(project)
    return not any(names_edge(data.get(k)) for k in ("regions", "supports", "masses", "load_cases",
                   "connections", "wet_surfaces", "imperfections", "assignments"))


def _refinement_records(project: Any, geometry: Any, overrides) -> list[dict[str, Any]]:
    from anygeometry.curves import Spline, Straight

    document = None
    out = []
    for zone in project.refinements:
        record: dict[str, Any] = {
            "mode": str(zone.mode), "size": float(zone.size).hex(),
            "radius": float(zone.radius).hex(), "growth": float(zone.growth).hex(),
        }
        if zone.center is not None:
            record["source"] = {"kind": "point", "xyz": _hex(zone.center)}
        elif zone.ref.kind == "vertex":
            record["source"] = {"kind": "point", "xyz": _hex(geometry.vertex_position(zone.ref.id))}
        elif zone.ref.kind == "edge":
            edge_id = int(zone.ref.id)
            edge = geometry.edges[edge_id]
            document = document or _document(geometry)
            vertices = [edge.start, *getattr(edge.curve, "control_vertices", ()), edge.end]
            record["source"] = {"kind": "edge", "id": edge_id, "definition": document["edges"][edge_id],
                                "vertices": {str(v): document["vertices"][v] for v in vertices},
                                "stations": _hex(zone.sources(geometry)),
                                "sizing_only": type(edge.curve) in (Spline, Straight)
                                and _sizing_edge_is_free(project, edge_id, vertices, overrides)}
        else:
            record["source"] = {"kind": str(zone.ref.kind), "id": int(zone.ref.id)}
        out.append(record)
    return sorted(out, key=lambda r: json.dumps(r, sort_keys=True))


def _source(project: Any, settings: Any) -> dict[str, Any]:
    """Actual source definitions of the plate, or raise :class:`_Ineligible`."""

    from anygeometry.surfaces import CoonsSurface, Plane

    try:
        geometry = project.geometry
        for name in ("members", "attachments", "junctions"):
            if getattr(geometry, name, None):
                raise _Ineligible(f"geometry has {name}; only a plain plate is supported")
        if len(geometry.faces) != 1:
            raise _Ineligible("exactly one face is supported")
        face_id = int(next(iter(geometry.faces)))
        face = geometry.faces[face_id]
        overrides = {int(e): int(n) for e, n in settings.overrides}
        outer = _loop_record(geometry, face.loop, overrides)
        holes = [_loop_record(geometry, hole, overrides) for hole in face.holes]
        if face.parameterization is not None:
            raise _Ineligible("an explicit face parameterization is outside the planar patch contract")
        surface = face.surface
        pts = _points(outer)
        if isinstance(surface, Plane):
            kind, origin, normal = "plane", np.asarray(surface.origin, float), np.asarray(surface.normal, float)
            support = {"kind": kind, "origin": _hex(origin), "normal": _hex(normal),
                       "u": _hex(surface.u_vector), "v": _hex(surface.v_vector)}
        elif (surface is None or (type(surface) is CoonsSurface and surface.bottom is None)) and (
            len(face.corners) == 4 and len(face.loop) == 4
        ):
            origin = pts[0]
            normal = np.zeros(3)
            for i in range(len(pts)):
                normal += np.cross(pts[i] - origin, pts[(i + 1) % len(pts)] - origin)
            normal = normal / float(np.linalg.norm(normal))
            support = {"kind": "four_sided", "origin": _hex(origin), "normal": _hex(normal)}
        else:
            raise _Ineligible("face is neither an explicit Plane nor an exact four-sided straight face")
        extent = float(max(np.ptp(pts, axis=0).max(), 1.0e-300))
        tol = geometry.tolerance.effective_length(extent)
        every = np.vstack([pts] + [_points(h) for h in holes])
        if float(np.max(np.abs((every - origin) @ normal))) > tol:
            raise _Ineligible("face vertices are not coplanar within the owner tolerance")
        support["tolerance"] = tol.hex()
        support["area_tolerance"] = float(geometry.tolerance.effective_area(extent)).hex()
        source = {
            "face": face_id, "support": support, "outer": outer, "holes": holes,
            "refinements": _refinement_records(project, geometry, overrides),
            "overrides": [[e, n] for e, n in sorted(overrides.items())],
            "target_size": float(settings.target_size).hex(),
            "order": str(settings.element_order),
        }
        if abs(extent) <= 0.0 or not math.isfinite(extent):
            raise _Ineligible("degenerate plate extent")
        _Chart(source)
        return source
    except _Ineligible:
        raise
    except Exception as error:  # noqa: BLE001 - any geometry access failure = ineligible
        raise _Ineligible(f"source geometry unreadable: {type(error).__name__}: {error}") from error


def _source_digest(source: Mapping[str, Any]) -> str:
    return _digest(source)


def _associated_sizing_edge(mesh, source):
    for zone in source["refinements"]:
        ref = zone["source"]
        if ref.get("sizing_only"):
            eid = int(ref["id"])
            if (eid in mesh.nodes_of_edge or eid in mesh.elements_of_edge or eid in mesh.offset_nodes_of_edge
                    or any(int(v) in mesh.node_of_vertex for v in ref["vertices"])):
                return eid
    return None


# ----------------------------------------------------------- mesh vs. geometry


def _shells(mesh: Any) -> dict[int, tuple[int, ...]]:
    return {int(k): tuple(int(n) for n in v) for k, v in {**mesh.quads, **mesh.tris}.items()}


def _corners(conn: tuple[int, ...]) -> tuple[int, ...]:
    return conn[:4] if len(conn) in (4, 8) else conn[:3]


def _seg_dist(p, a, b) -> float:
    d = b - a
    t = min(1.0, max(0.0, float((p - a) @ d) / float(d @ d)))
    return float(np.linalg.norm(p - (a + t * d)))


def _coverage(mesh: Any, source: Mapping[str, Any]) -> list[str]:
    """Independent proof that ``mesh`` tiles the source plate exactly (empty list = proven)."""

    problems: list[str] = []
    fid = int(source["face"])
    chart = _Chart(source)
    tol = float.fromhex(source["support"]["tolerance"])
    shells = _shells(mesh)
    ids = [int(e) for e in mesh.elements_of_face.get(fid, ())]
    if not ids or any(e not in shells for e in ids):
        return ["the face has no usable element provenance"]
    outer = _xy(chart, source["outer"])
    holes = [_xy(chart, h) for h in source["holes"]]
    expected = abs(_area2(outer)) - math.fsum(abs(_area2(h)) for h in holes)
    areas, signs, directed = [], set(), {}
    for eid in ids:
        corners = _corners(shells[eid])
        xyz = [mesh.nodes[n] for n in corners]
        if any(abs(chart.height(mesh.nodes[n])) > tol for n in shells[eid]):
            problems.append(f"element {eid} is off the plate plane")
        if len(shells[eid]) == 2 * len(corners):
            for k, node in enumerate(shells[eid][len(corners):]):
                if np.linalg.norm(mesh.nodes[node] - 0.5 * (xyz[k] + xyz[(k + 1) % len(xyz)])) > tol:
                    problems.append(f"element {eid} has a non-straight quadratic edge")
        xy = [chart.to2(p) for p in xyz]
        a = _area2(xy)
        if a == 0.0:
            problems.append(f"element {eid} is degenerate")
            continue
        signs.add(a > 0.0)
        areas.append(abs(a))
        c = _centroid(xy)
        if not _strict_in(c, outer) or any(_strict_in(c, h) for h in holes):
            problems.append(f"element {eid} lies outside the plate or inside a hole")
        for i in range(len(corners)):
            edge = (corners[i], corners[(i + 1) % len(corners)])
            if edge in directed:
                problems.append(f"directed edge {edge} used twice (overlap or flipped cell)")
            directed[edge] = eid
    if signs != {True}:
        problems.append("elements do not follow the source outer-loop orientation")
    if not abs(math.fsum(areas) - expected) <= float.fromhex(source["support"]["area_tolerance"]):
        problems.append(f"element area {math.fsum(areas)!r} differs from the plate area {expected!r}")
    segments = []
    for tag, record in [("outer", source["outer"])] + [("hole", h) for h in source["holes"]]:
        pts = _points(record)
        for i in range(len(pts)):
            segments.append((pts[i], pts[(i + 1) % len(pts)], record["edges"][i][0]))
    covered = [0.0] * len(segments)
    for a, b in directed:
        if (b, a) in directed:
            continue
        pa, pb = np.asarray(mesh.nodes[a], float), np.asarray(mesh.nodes[b], float)
        for k, (s0, s1, _) in enumerate(segments):
            if _seg_dist(pa, s0, s1) <= tol and _seg_dist(pb, s0, s1) <= tol:
                covered[k] += float(np.linalg.norm(pb - pa))
                break
        else:
            problems.append(f"free edge {a}-{b} is not on any geometry edge")
    for (s0, s1, edge_id), length in zip(segments, covered):
        full = float(np.linalg.norm(s1 - s0))
        if not abs(length - full) <= tol:
            problems.append(f"geometry edge {edge_id} is covered by {length!r} of {full!r}")
    face_nodes = np.array([mesh.nodes[n] for n in sorted({n for e in ids for n in shells[e]})], dtype=float)
    for record in [source["outer"], *source["holes"]]:
        for vertex, p in zip(record["vertices"], _points(record)):
            if float(np.min(np.linalg.norm(face_nodes - p, axis=1))) > tol:
                problems.append(f"no mesh node at geometry vertex {vertex}")
    return problems


def _associations(mesh: Any, source: Mapping[str, Any]) -> list[str]:
    """Every loop edge/vertex of the source has a correct node association."""

    problems: list[str] = []
    fid = int(source["face"])
    tol = float.fromhex(source["support"]["tolerance"])
    face_elements = {int(e) for e in mesh.elements_of_face.get(fid, ())}
    for record in [source["outer"], *source["holes"]]:
        pts = _points(record)
        for i, (edge_id, _) in enumerate(record["edges"]):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            seq = list(mesh.nodes_of_edge.get(edge_id, ()))
            if len(seq) < 2 or any(n not in mesh.nodes for n in seq):
                problems.append(f"edge {edge_id} has no node association")
                continue
            ends = [np.asarray(mesh.nodes[seq[0]], float), np.asarray(mesh.nodes[seq[-1]], float)]
            ok = (np.linalg.norm(ends[0] - a) <= tol and np.linalg.norm(ends[1] - b) <= tol) or \
                 (np.linalg.norm(ends[0] - b) <= tol and np.linalg.norm(ends[1] - a) <= tol)
            params = [float((np.asarray(mesh.nodes[n], float) - a) @ (b - a)) / float((b - a) @ (b - a)) for n in seq]
            monotone = all(x < y for x, y in zip(params, params[1:])) or all(x > y for x, y in zip(params, params[1:]))
            on = all(_seg_dist(np.asarray(mesh.nodes[n], float), a, b) <= tol for n in seq)
            if not (ok and monotone and on):
                problems.append(f"edge {edge_id} node association does not follow the geometry edge")
            owners = mesh.elements_of_edge.get(edge_id)
            if owners is not None and not {int(e) for e in owners} <= face_elements:
                problems.append(f"edge {edge_id} element association leaves the face")
        for vertex, p in zip(record["vertices"], pts):
            node = mesh.node_of_vertex.get(vertex)
            if node is None or node not in mesh.nodes or \
                    float(np.linalg.norm(np.asarray(mesh.nodes[node], float) - p)) > tol:
                problems.append(f"vertex {vertex} has no node association at its position")
    return problems


def _binding(mesh: Any, face_id: int) -> dict[str, Any]:
    """Bind the exact codec payload, including numbering, provenance and associations."""
    from anymesher.serialize import mesh_to_dict

    return {"face": face_id, "digest": _digest(mesh_to_dict(mesh))}


def _surface_global_state(project, settings):
    document = _document(project.geometry)
    state = _global_state(project, settings, _strategy(project, settings), document)
    # Hole records are the locally editable part of the face record. Preserve
    # corner parameterization, metadata, and the complete outer/support inputs.
    state["faces"] = _digest([{k: v for k, v in face.items() if k not in ("holes", "loop", "surface")}
                              for face in document["faces"].values()])
    structural = dict(document["raw"]["structural"])
    # Geometry rebuilds coedge IDs when trim loops change. Their edge traversal
    # is bound by the oriented source loops; keep the independent sheet uses.
    structural.pop("coedges", None)
    structural["face_uses"] = [{k: v for k, v in item.items() if k != "loops"}
                               for item in structural["face_uses"]]
    state["structural"] = _digest(structural)
    return state


def _quality_limits(settings):
    from anymesher.regional_update import RegionalQualityLimits

    # The complete effective policy is also evaluated by _validate_quality before publication.
    policy = dict(settings.quality_policy) if settings.quality_policy_explicit else {}
    return RegionalQualityLimits(max_aspect_ratio=policy.get("maximum_aspect_ratio"),
                                 max_warp=policy.get("maximum_warpage"),
                                 min_scaled_jacobian=policy.get("minimum_scaled_jacobian"))


# --------------------------------------------------------------------- capture


def _surface_pins() -> dict[str, str]:
    pins = dict(_pins())
    pins["baseline_schema"] = f"{SCHEMA}/{VERSION}"
    return pins


def _capture(project: Any, settings: Any, mesh: Any) -> dict[str, Any]:
    from anymesher import MeshAutomationOptions
    from .mesh_controls import MeshControls

    controls = settings.controls or MeshControls.from_settings(project.native_mesh_settings)
    if (controls.certification_mode != "interactive" or not controls.recombine
            or settings.quad_options is not None or settings.automation not in (None, MeshAutomationOptions())):
        raise _Ineligible("planar patches require interactive quad controls")
    if not all(hasattr(mesh, name) for name in ("elements_of_face", "quads", "tris", "nodes")):
        raise _Ineligible("mesh carries no face provenance")
    if _strategy(project, settings) not in ("auto", "mapped"):
        raise _Ineligible("the planar patch requires an automatic or mapped quad strategy")
    if mesh.tris:
        raise _Ineligible("a mixed plate needs verified S3 authority for the edited mesh")
    if mesh.geometry_model_id is None or mesh.geometry_revision is None:
        raise _Ineligible("the prior mesh has no source identity")
    if mesh.beams or mesh.couplings:
        raise _Ineligible("mesh has beams or couplings")
    if (mesh.declared_plate_junction_edges or mesh.automatic_intersections
            or mesh.automatic_beam_connections or mesh.automatic_shell_connections):
        raise _Ineligible("mesh has structural connections")
    if mesh.order != settings.element_order:
        raise _Ineligible("mesh order differs from the settings")
    source = _source(project, settings)
    if _associated_sizing_edge(mesh, source) is not None:
        raise _Ineligible("the proposed sizing edge has mesh associations")
    fid = int(source["face"])
    if {int(f) for f in mesh.elements_of_face} != {fid}:
        raise _Ineligible("mesh does not cover exactly the plate face")
    shells = _shells(mesh)
    face_elements = {int(e) for e in mesh.elements_of_face[fid]}
    expected_sheets = {int(sheet.id) for sheet in project.geometry.sheets.values()
                       if any(project.geometry.face_uses[u].face_id == fid for u in sheet.face_use_ids)}
    if {int(k): set(v) for k, v in mesh.elements_of_sheet.items()} != {
        key: face_elements for key in expected_sheets
    }:
        raise _Ineligible("sheet element membership differs from the source face uses")
    if face_elements != set(shells):
        raise _Ineligible("mesh has elements outside the plate face")
    if {n for e in shells.values() for n in e} != {int(n) for n in mesh.nodes}:
        raise _Ineligible("mesh has nodes outside the plate face")
    problems = _coverage(mesh, source) + _associations(mesh, source)
    if problems:
        raise _Ineligible("mesh does not match the geometry: " + "; ".join(problems[:4]))
    return _plain({
        "schema": SCHEMA, "version": VERSION, "pins": _surface_pins(),
        "global": _surface_global_state(project, settings),
        "face": fid, "source": source, "source_digest": _source_digest(source),
        "order": str(mesh.order),
        "mesh": _binding(mesh, fid),
    })


def explain_surface_ineligibility(project: Any, settings: Any, mesh: Any) -> str | None:
    try:
        _capture(project, settings, mesh)
    except Exception as error:  # noqa: BLE001
        return f"{type(error).__name__}: {error}"
    return None


def capture_surface_baseline(project: Any, settings: Any, mesh: Any) -> dict[str, Any] | None:
    """Serializable baseline for a verified single-flat-plate mesh, else ``None``."""

    try:
        return _capture(project, settings, mesh)
    except Exception:  # noqa: BLE001 - ineligible must never block full generation
        return None


# ------------------------------------------------------------------------ plan


@dataclass(frozen=True, eq=False)
class SurfacePlan:
    description: str
    applicable: bool
    is_noop: bool = False
    covered: tuple[str, ...] = ()
    blockers: tuple[tuple[str, str], ...] = ()
    fallback_allowed: bool = False  # True only for a boundary mismatch
    _analysis: Any = field(default=None, repr=False)


@dataclass
class _Analysis:
    source_old: Mapping[str, Any]
    source_new: Mapping[str, Any]
    chart: _Chart
    added_holes: list
    removed_holes: list
    kept_holes: list
    footprints2: list
    target_size: float | None
    size_field: Any
    covered: list
    refinement_footprints: list


def _validate_baseline(baseline: Any) -> None:
    if not isinstance(baseline, Mapping) or baseline.get("schema") != SCHEMA:
        raise _Ineligible("not a surface baseline")
    if baseline.get("version") != VERSION:
        raise _Ineligible("unsupported surface baseline version")
    if dict(baseline.get("pins", {})) != _surface_pins():
        raise _Ineligible("build pins changed; a full rebuild is required")
    if not isinstance(baseline.get("mesh"), Mapping) or not isinstance(baseline.get("global"), Mapping):
        raise _Ineligible("incomplete mesh binding or global settings")
    source = baseline.get("source")
    if not isinstance(source, Mapping) or baseline.get("source_digest") != _source_digest(source):
        raise _Ineligible("baseline source definition fails its digest")


def _canon(record: Any) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def _influence_radius(zone: Mapping[str, Any], target: float) -> float:
    size, radius, growth = (float.fromhex(zone[k]) for k in ("size", "radius", "growth"))
    return radius + size * math.log(target / size) / math.log(growth)


def _analyse(project: Any, settings: Any, baseline: Any, check: Callable[[str], None]):
    """Return (analysis | None, blockers, boundary_flag, noop)."""

    blockers: list[tuple[str, str]] = []
    try:
        _validate_baseline(baseline)
    except _Ineligible as error:
        return None, [("SURFACE_BASELINE_INVALID", str(error))], False, False
    now_global = _surface_global_state(project, settings)
    before = dict(baseline["global"])
    differing = sorted(k for k in set(now_global) | set(before) if now_global.get(k) != before.get(k))
    if differing:
        return None, [("SURFACE_GLOBAL_CHANGED", "global dependencies changed (" + ", ".join(differing)
                       + "); not a local edit")], False, False
    try:
        new = _source(project, settings)
    except _Ineligible as error:
        return None, [("SURFACE_SOURCE_UNSUPPORTED", str(error))], False, False
    old = baseline["source"]
    check("surface source diff")
    if new["face"] != old["face"]:
        return None, [("SURFACE_FACE_CHANGED", "the plate face id changed")], False, False
    if new["support"] != old["support"] or new["outer"] != old["outer"]:
        return None, [("SURFACE_BOUNDARY_MISMATCH", "support or outer boundary (vertices, edges, divisions) changed")], True, False
    if new["target_size"] != old["target_size"] or new["order"] != old["order"]:
        return None, [("SURFACE_GLOBAL_CHANGED", "target size or element order changed")], False, False

    chart = _Chart(new)
    outer2 = _xy(chart, new["outer"])
    old_holes = {tuple(sorted(e for e, _ in h["edges"])): h for h in old["holes"]}
    new_holes = {tuple(sorted(e for e, _ in h["edges"])): h for h in new["holes"]}
    kept = [new_holes[k] for k in new_holes if k in old_holes and new_holes[k] == old_holes[k]]
    removed = [old_holes[k] for k in old_holes if k not in new_holes or new_holes[k] != old_holes[k]]
    added = [new_holes[k] for k in new_holes if k not in old_holes or new_holes[k] != old_holes[k]]
    # Edge-division overrides are part of the loop records: outer-loop changes were
    # refused above as a boundary mismatch, a hole's change makes that hole
    # removed + added; an override on an edge outside the loops has no effect.

    from anymesher.refinement import SizeField

    try:
        sf = SizeField(project.geometry, float.fromhex(new["target_size"]), project.refinements,
                       active_faces=[new["face"]]).face_view(new["face"])
    except ValueError as error:
        return None, [("SURFACE_REFINEMENT_INVALID", str(error))], False, False
    target = float(sf.target_size)

    old_counter = Counter(_canon(r) for r in old["refinements"])
    new_counter = Counter(_canon(r) for r in new["refinements"])
    zone_changes = [("removed", json.loads(k)) for k in (old_counter - new_counter).elements()] + \
                   [("added", json.loads(k)) for k in (new_counter - old_counter).elements()]

    footprints: list[list[tuple[float, float]]] = []
    centers3: list[np.ndarray] = []
    refinement_footprints = []
    covered: list[str] = []

    def margin_at(points3: list[np.ndarray]) -> float:
        return 0.5 * float(np.min(sf.size_at(np.asarray(points3))))

    for label, group in (("removed", removed), ("added", added)):
        for rec in group:
            pts3 = list(_points(rec))
            m = margin_at([np.mean(pts3, axis=0)])
            xy = _xy(chart, rec)
            corners = [(x + dx, y + dy) for x, y in xy for dx in (-m, m) for dy in (-m, m)]
            footprints.append(_hull(corners))
            centers3.append(np.mean(pts3, axis=0))
            covered.append(f"hole {label}: edges {[e for e, _ in rec['edges']]}")

    for label, rec in zone_changes:
        src = rec["source"]
        curve = src["kind"] == "edge" and all(src.get(k) for k in ("sizing_only", "definition", "stations", "vertices"))
        if curve and int(src["id"]) in project.geometry.edges:
            curve = _sizing_edge_is_free(project, int(src["id"]), {int(v) for v in src["vertices"]},
                                         dict(settings.overrides))
        if curve:
            domain = old if label == "removed" else new
            controls = [vertex["position"] for vertex in src["vertices"].values()]
            hull = _hull([chart.to2(p) for p in controls])
            # The Bezier convex-hull property proves the entire curve belongs
            # to this interior, including the parts between sampled stations.
            curve = (all(abs(chart.height(p)) <= float.fromhex(domain["support"]["tolerance"]) for p in controls)
                     and _inside_domain(hull, outer2) and not any(_touch(hull, _xy(chart, h)) for h in domain["holes"]))
        if rec["mode"] != "refine" or not (src["kind"] == "point" or curve):
            blockers.append(("SURFACE_NONLOCAL_REFINEMENT",
                             f"a {rec['mode']} refinement bound to {src['kind']} lacks a verified footprint; "
                             "local sizing supports points and unassociated straight or spline edges"))
            continue
        if float.fromhex(rec["size"]) >= target:
            covered.append(f"refinement {label}: size not finer than the target (no mesh effect)")
            continue
        radius = _influence_radius(rec, target)
        stations = src["stations"] if curve else src["xyz"]
        centers = np.asarray([float.fromhex(v) for v in stations]).reshape(-1, 3)
        discs = []
        for center in centers:
            gap = abs(chart.height(center))
            if gap >= radius:
                continue
            r2 = math.sqrt(radius * radius - gap * gap) * (1.0 + 1.0e-9) / math.cos(math.pi / _DISC_SIDES)
            cx, cy = chart.to2(center)
            disc = [(cx + r2 * math.cos(2 * math.pi * k / _DISC_SIDES),
                     cy + r2 * math.sin(2 * math.pi * k / _DISC_SIDES)) for k in range(_DISC_SIDES)]
            discs.append(disc)
            projected = np.asarray(chart.to3((cx, cy)))
            centers3.append(projected)
            if label == "added":
                wanted = float(sf.size_at(np.asarray([projected]))[0])
                refinement_footprints.append((disc, wanted))
        if discs:
            # Use one enclosing hull per old or current edge definition.
            footprints.append(_hull([p for disc in discs for p in disc]) if curve else discs[0])
        name = f"sizing edge {src['id']}" if curve else "refinement"
        covered.append(f"{name} {label}: size {float.fromhex(rec['size']):g} influence radius {radius:g}")

    if not removed and not added and not zone_changes and not blockers:
        return None, [], False, True
    for poly in footprints:
        if not _inside_domain(poly, outer2):
            blockers.append(("SURFACE_BOUNDARY_MISMATCH", "changed footprint reaches the support/outer boundary"))
            break
    for poly in footprints:
        if any(_touch(poly, _xy(chart, h)) for h in kept):
            blockers.append(("SURFACE_BOUNDARY_MISMATCH", "changed footprint touches a retained hole"))
            break
    new_polys = [_xy(chart, h) for h in added]
    for i, a in enumerate(new_polys):
        if not _inside_domain(a, outer2) or any(_touch(a, b) for b in new_polys[i + 1:]) \
                or any(_touch(a, _xy(chart, h)) for h in kept):
            blockers.append(("SURFACE_HOLE_INVALID", "an added hole is not strictly inside the plate and disjoint from other holes"))
            break
    if blockers:
        point_or_trim = all(rec["source"]["kind"] != "edge" for _, rec in zone_changes)
        return None, blockers, point_or_trim and all(code == "SURFACE_BOUNDARY_MISMATCH" for code, _ in blockers), False
    if not footprints:
        return None, [("SURFACE_NO_AFFECTED_CELLS", "the edited zones have no effect on this plate")], False, False

    samples = list(centers3)
    for poly in footprints:
        samples.extend(np.asarray(chart.to3(p)) for p in poly)
        samples.append(np.asarray(chart.to3(_centroid(poly))))
    target_size = float(np.min(sf.size_at(np.asarray(samples)))) if zone_changes else None
    analysis = _Analysis(old, new, chart, added, removed, kept, footprints, target_size, sf, covered, refinement_footprints)
    return analysis, [], False, False


def plan_surface_update(project: Any, settings: Any, baseline: Mapping[str, Any], *,
                        cancellation_check: Callable[[str], Any] | None = None) -> SurfacePlan:
    check = _check_fn(cancellation_check)
    analysis, blockers, boundary, noop = _analyse(project, settings, baseline, check)
    if noop:
        return SurfacePlan("no change to the plate holes, refinements or boundary", False, True)
    if analysis is None or blockers:
        text = "; ".join(f"{code}: {message}" for code, message in blockers)
        return SurfacePlan(f"surface patch not applicable ({text})", False, False, (), tuple(blockers), boundary)
    description = "surface patch: " + "; ".join(analysis.covered) + \
        f" ({len(analysis.kept_holes)} hole(s) retained exactly)"
    return SurfacePlan(description, True, False, tuple(analysis.covered), (), False, analysis)


# -------------------------------------------------------------------- generate


@dataclass(frozen=True, eq=False)
class SurfaceUpdateResult:
    mesh: Any
    baseline: dict | None
    retained_nodes: frozenset
    retained_elements: frozenset
    plan: SurfacePlan
    provenance: Mapping[str, Any]


def _hole_stations(record: Mapping[str, Any], overrides: Mapping[int, int], size_at) -> list[dict[str, Any]]:
    pts = _points(record)
    count = len(record["edges"])
    plan = []
    for i in range(count):
        a, b = pts[i], pts[(i + 1) % count]
        edge_id = int(record["edges"][i][0])
        length = float(np.linalg.norm(b - a))
        if edge_id in overrides:
            n, fixed = max(1, int(overrides[edge_id])), True
        else:
            n, fixed = max(1, int(round(length / float(size_at(0.5 * (a + b)))))), False
        plan.append({"edge": edge_id, "forward": bool(record["edges"][i][1]), "vertex": int(record["vertices"][i]),
                     "a": a, "b": b, "n": n, "fixed": fixed, "length": length})
    if sum(p["n"] for p in plan) % 2:
        free = [i for i, p in enumerate(plan) if not p["fixed"]]
        if not free:
            raise SurfaceUpdateUnsupported(
                "explicit edge divisions of a hole give an odd boundary count; an all-quad patch is impossible",
                code="ODD_BOUNDARY_PARITY")
        bump = max(free, key=lambda i: (plan[i]["length"], -i))
        plan[bump]["n"] += 1
        plan[bump]["parity_bumped"] = True
    for p in plan:
        p["stations"] = [tuple(float(x) for x in (p["a"] + (p["b"] - p["a"]) * (k / p["n"]))) for k in range(p["n"])]
    return plan


def _publish_hole_associations(new: Any, report: Any, loops: list[list[dict[str, Any]]],
                               pseudo_ids: list[int]) -> None:
    """Replace each loop's single owner edge id by one association per geometry edge."""

    shells = _shells(new)
    node_at = {tuple(float(x) for x in new.nodes[n]): int(n) for n in report.new_node_ids}
    mids: dict[tuple[int, int], int] = {}
    owners: dict[tuple[int, int], list[int]] = {}
    for eid in report.new_element_ids:
        conn = shells[int(eid)]
        corners = _corners(conn)
        for i in range(len(corners)):
            key = tuple(sorted((corners[i], corners[(i + 1) % len(corners)])))
            owners.setdefault(key, []).append(int(eid))
            if len(conn) in (6, 8):
                mids[key] = conn[len(corners) + i]
    for pseudo in pseudo_ids:
        for table in (new.nodes_of_edge, new.elements_of_edge, new.offset_nodes_of_edge):
            table.pop(pseudo, None)
    quadratic = new.order == "quadratic"
    for loop in loops:
        ids = []
        for part in loop:
            try:
                ids.append([node_at[s] for s in part["stations"]])
            except KeyError as error:
                raise SurfaceUpdateError(f"hole station {error.args[0]} has no published node",
                                         code="HOLE_STATION_MISSING") from error
        for i, part in enumerate(loop):
            chain = ids[i] + [ids[(i + 1) % len(loop)][0]]
            keys = [tuple(sorted(pair)) for pair in zip(chain, chain[1:])]
            seq: list[int] = []
            for k, node in enumerate(chain):
                seq.append(node)
                if quadratic and k < len(keys):
                    seq.append(int(mids[keys[k]]))
            if not part["forward"]:
                seq.reverse()
            new.nodes_of_edge[part["edge"]] = seq
            new.elements_of_edge[part["edge"]] = sorted({e for key in keys for e in owners.get(key, ())})
            new.node_of_vertex[part["vertex"]] = ids[i][0]


def _wrap_refusal(refusal: Any, problems: list[tuple[str, str]]) -> SurfaceUpdateError:
    code = getattr(refusal, "code", "OWNER_REFUSED")
    if code == "SOURCE_CORRESPONDENCE_REJECTED" and problems:
        kinds = {kind for kind, _ in problems}
        text = "; ".join(message for _, message in problems[:5])
        if kinds == {"boundary"}:
            return SurfaceBoundaryMismatch(f"source check rejected the patch: {text}", code="SURFACE_BOUNDARY_MISMATCH")
        return SurfaceUpdateError(f"source check rejected the patch: {text}", code="SURFACE_SOURCE_CHECK_FAILED",
                                  details={"problems": [m for _, m in problems]})
    details = dict(getattr(refusal, "details", {}) or {})
    cls = SurfaceBoundaryMismatch if code in _BOUNDARY_CODES else SurfaceUpdateError
    return cls(f"owner refused the regional patch: {refusal}", code=code, details=details)


def generate_surface_update(project: Any, settings: Any, baseline: Mapping[str, Any], old_mesh: Any, *,
                            node_high_water: int, element_high_water: int,
                            cancellation_check: Callable[[str], Any] | None = None) -> SurfaceUpdateResult:
    from anymesher.regional_patch import (
        PatchLoop, PatchStation, PatchTolerances, RegionalPatchRefusal, RegionalPatchRequest,
        remesh_region, verify_regional_patch,
    )

    check = _check_fn(cancellation_check)
    plan = plan_surface_update(project, settings, baseline, cancellation_check=cancellation_check)
    if not plan.applicable:
        text = "; ".join(f"{c}: {m}" for c, m in plan.blockers) or plan.description
        cls = SurfaceBoundaryMismatch if plan.fallback_allowed else SurfaceUpdateUnsupported
        code = "SURFACE_BOUNDARY_MISMATCH" if plan.fallback_allowed else (
            plan.blockers[0][0] if plan.blockers else "SURFACE_NOT_APPLICABLE")
        raise cls(text, code=code, details={"blockers": list(plan.blockers)})
    analysis: _Analysis = plan._analysis
    source_old, source_new, chart = analysis.source_old, analysis.source_new, analysis.chart
    fid = int(source_new["face"])
    old = _decode(old_mesh)
    if _binding(old, fid) != baseline["mesh"]:
        raise SurfaceUpdateError("old mesh does not match the baseline", code="SURFACE_OLD_MESH_MISMATCH")
    if any(_associated_sizing_edge(old, source) is not None for source in (source_old, source_new)):
        raise SurfaceUpdateUnsupported("The sizing source also carries mesh associations.",
                                       code="SURFACE_CONSTRAINED_SIZING_EDGE")
    bad = _coverage(old, source_old) + _associations(old, source_old)
    if bad:
        raise SurfaceUpdateError("old mesh does not match the baseline geometry: " + "; ".join(bad[:3]),
                                 code="SURFACE_OLD_MESH_MISMATCH")
    tol = float.fromhex(source_new["support"]["tolerance"])
    overrides = {int(e): int(n) for e, n in source_new["overrides"]}
    sf = analysis.size_field

    def size_at(point) -> float:
        return float(sf.size_at(np.asarray([point]))[0])

    loops = [_hole_stations(rec, overrides, size_at) for rec in analysis.added_holes]
    base = max([_PSEUDO_EDGE_BASE, *(int(e) for e in old.nodes_of_edge), *(int(e) for e in old.elements_of_edge),
                *(e for r in [source_new["outer"], *source_new["holes"]] for e, _ in r["edges"])]) + 1
    pseudo = [base + i for i in range(len(loops))]
    added = [PatchLoop(tuple(PatchStation(xyz, None, part["edge"]) for part in loop for xyz in part["stations"]))
             for loop in loops]
    drop = []
    retired: dict[str, list[int]] = {"edge": [], "vertex": []}
    for rec in analysis.removed_holes:
        seq = old.nodes_of_edge[int(rec["edges"][0][0])]
        drop.append(int(seq[0]))
        retired["edge"].extend(int(e) for e, _ in rec["edges"])
        retired["vertex"].extend(int(v) for v in rec["vertices"])
    footprints = [[chart.to3(p) for p in poly] for poly in analysis.footprints2]

    shells_old = _shells(old)
    expected = sorted(e for e in (int(x) for x in old.elements_of_face[fid])
                      if any(_overlap([chart.to2(old.nodes[n]) for n in _corners(shells_old[e])], poly)
                             for poly in analysis.footprints2))
    problems: list[tuple[str, str]] = []
    kept_loops = [source_new["outer"], *analysis.kept_holes]

    def source_check(before: Any, after: Any, report: Any) -> bool:
        problems.clear()
        problems.extend(("geometry", p) for p in _coverage(after, source_new))
        if not set(expected) <= set(report.replaced_element_ids):
            problems.append(("geometry", "an analytically affected cell was not replaced"))
        if report.quality is None:
            problems.append(("quality", "no explicit owner quality evidence"))
        if report.new_node_ids and min(report.new_node_ids) <= int(node_high_water):
            problems.append(("ids", "a new node id is not above the high water"))
        if report.new_element_ids and min(report.new_element_ids) <= int(element_high_water):
            problems.append(("ids", "a new element id is not above the high water"))
        for record in kept_loops:
            for (edge_id, _), vertex in zip(record["edges"], record["vertices"]):
                if list(after.nodes_of_edge.get(edge_id, ())) != list(before.nodes_of_edge.get(edge_id, ())) \
                        or sorted(after.elements_of_edge.get(edge_id, ())) != sorted(before.elements_of_edge.get(edge_id, ())) \
                        or after.node_of_vertex.get(vertex) != before.node_of_vertex.get(vertex):
                    problems.append(("boundary", f"associations of retained geometry edge {edge_id} changed"))
                for n in before.nodes_of_edge.get(edge_id, ()):
                    if n not in after.nodes or not np.array_equal(before.nodes[n], after.nodes[n]):
                        problems.append(("boundary", f"node {n} of retained edge {edge_id} was removed or moved"))
        return not problems

    request = RegionalPatchRequest(
        old_mesh=old, face_id=fid, surface_kind="plane", footprints=footprints,
        target_size=analysis.target_size, added_holes=tuple(added), added_hole_edge_ids=tuple(pseudo),
        sizing_zones=tuple(([chart.to3(p) for p in polygon], wanted)
                           for polygon, wanted in analysis.refinement_footprints),
        drop_hole_node_ids=tuple(drop), retired_geometry_keys=(retired if drop else None),
        remove_mapped_grid=True, discard_stale_diagnostics=True, require_all_quad=True,
        tolerances=PatchTolerances(planarity=tol), quality=_quality_limits(settings),
        source_check=source_check, node_high_water=int(node_high_water),
        element_high_water=int(element_high_water),
        provenance={"operation": "anyfem.surface_update", "changes": list(plan.covered)},
    )
    check("surface remesh region")
    try:
        result = remesh_region(request)
    except RegionalPatchRefusal as refusal:
        raise _wrap_refusal(refusal, problems) from refusal
    check("surface verify")
    new, report = result.mesh, result.report
    try:
        _publish_hole_associations(new, report, loops, pseudo)
        verify_regional_patch(old, new, fid, report.replaced_element_ids, report.new_element_ids)
    except RegionalPatchRefusal as refusal:
        raise _wrap_refusal(refusal, []) from refusal
    for footprint, wanted in analysis.refinement_footprints:
        # Exact interface edges may be longer than the requested size. Demand
        # measurable refinement within each changed zone, even when its source
        # is a vertex of several cells. Never report a re-numbered copy as refined.
        for eid in report.new_element_ids:
            xyz = [new.nodes[n] for n in _corners(new.quads[eid])]
            if not _overlap([chart.to2(p) for p in xyz], footprint):
                continue
            if max(np.linalg.norm(xyz[(i+1) % len(xyz)] - p) for i, p in enumerate(xyz)) <= wanted + tol:
                break
        else:
            raise SurfaceUpdateUnsupported(
                f"no new quad in the edited size zone meets its {wanted:g} edge target",
                code="SURFACE_LOCAL_SIZE_UNMET",
            )
    final = _coverage(new, source_new) + _associations(new, source_new)
    if final:
        raise SurfaceUpdateError("updated mesh does not match the current geometry: " + "; ".join(final[:4]),
                                 code="SURFACE_RESULT_UNVERIFIED")
    removed_edges = set(retired["edge"])
    updates = {part["edge"]: part["n"] for loop in loops for part in loop}
    if new.seeding is not None:
        # The owner splice drops removed seed edges. Install the actual new rim counts
        # and discard the prior in-memory field, which was built on a different source.
        from anymesher.seeding import Seeding
        counts = {e: n for e, n in new.seeding.divisions.items() if e not in removed_edges}
        counts.update(updates)
        classes = {e: c for e, c in new.seeding.classes.items() if e not in removed_edges}
        new.seeding = Seeding(counts, classes=classes)

    retained_nodes = frozenset(int(n) for n in old.nodes) & frozenset(int(n) for n in new.nodes)
    new_shells = _shells(new)
    retained_elements = frozenset(set(shells_old) & set(new_shells))
    for node in retained_nodes:
        if not np.array_equal(np.asarray(old.nodes[node]), np.asarray(new.nodes[node])):
            raise SurfaceUpdateError(f"retained node {node} moved", code="SURFACE_RESULT_UNVERIFIED")
    for element in retained_elements:
        if shells_old[element] != new_shells[element]:
            raise SurfaceUpdateError(f"retained element {element} changed", code="SURFACE_RESULT_UNVERIFIED")
    if retained_elements != set(shells_old) - set(report.replaced_element_ids):
        raise SurfaceUpdateError("retained elements differ from the owner's report", code="SURFACE_RESULT_UNVERIFIED")

    provenance = _plain({
        "operation": "anyfem.surface_update", "owner": "anymesher.regional_patch.remesh_region",
        "route": report.route, "kernel_injected": report.kernel_injected, "changes": list(plan.covered),
        "target_size": report.target_size, "growth_rings": report.growth_rings,
        "replaced_elements": len(report.replaced_element_ids), "new_elements": len(report.new_element_ids),
        "retained_elements": len(retained_elements), "analytic_affected_elements": len(expected),
        "owner_checks": list(report.checks), "owner_unchecked": list(report.unchecked),
        "anyfem_checks": ["analytic_footprint_cells_replaced", "geometry_coverage_area_orientation_free_edges",
                          "retained_boundary_associations_unchanged", "per_edge_hole_associations_published",
                          "ids_above_high_water", "retained_cells_bit_exact", "added_sizing_stations_have_target_size_cells"],
        "anyfem_unchecked": ["graded_size_inside_patch (uniform patch size)",
                             "equality_with_a_full_remesh (retained cells are kept, not regenerated)"],
        "owner_post_processing": ["per-edge/vertex hole associations re-published from exact station coordinates"]
        if loops else [],
        "node_high_water": report.node_high_water, "element_high_water": report.element_high_water,
    })
    # The owner has validated the splice against the prior mesh, and the independent
    # coverage and association checks above establish its correspondence to this source.
    new.geometry_model_id = project.geometry.model_id
    new.geometry_revision = project.geometry.revision
    _validate_quality(project, new, settings, cancellation_check)
    updated = capture_surface_baseline(project, settings, new)
    if updated is None:
        raise SurfaceUpdateError("the updated mesh is not baseline-eligible: "
                                 + str(explain_surface_ineligibility(project, settings, new)),
                                 code="SURFACE_BASELINE_UNAVAILABLE")
    updated["last_update"] = provenance
    return SurfaceUpdateResult(new, updated, retained_nodes, retained_elements, plan, provenance)
