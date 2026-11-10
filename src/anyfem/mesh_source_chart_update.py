"""Local point refinement on a single four-sided analytic cone frustum.

The source boundary consists of two circular arcs and two straight generators.
Full and retained Q4 cells are checked in the normalized cone chart. The planar
kernel rebuilds complete two-by-two station blocks at a fixed rim. Its rectangle
scales u by the block's largest radius times the absolute sweep and v by the
slant length. This bounds the physical cone metric throughout the block.
New stations are evaluated on the source cone. The assembled mesh must meet the
configured quality policy and demonstrate the requested local physical edge size.
"""

from __future__ import annotations

import copy
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import numpy as np

from . import mesh_curved_update as _mcu
from .mesh_curved_update import (
    CurvedUpdateError as SourceChartError,
    CurvedUpdateUnsupported as SourceChartUnsupported,
    _GAMMA8,
    _Ineligible,
    _corner_jacobians,
    _longest,
    _mapped_groups,
    MAX_REPLACED_CELLS,
)
from .mesh_surface_update import (
    _binding,
    _canon,
    _influence_radius,
    _quality_limits,
    _refinement_records,
    _shells,
)
from .regional_mesh import (
    _check_fn,
    _decode,
    _digest,
    _document,
    _global_state,
    _hex,
    _pins,
    _plain,
    _strategy,
    _validate_quality,
)

__all__ = [
    "FAMILY_HOLDS",
    "ROUTE",
    "SCHEMA",
    "SourceChartError",
    "SourceChartPlan",
    "SourceChartResult",
    "SourceChartUnsupported",
    "capture_source_chart_baseline",
    "explain_source_chart_ineligibility",
    "generate_source_chart_update",
    "plan_source_chart_update",
    "source_chart_hold",
]

ROUTE = "cone-upper-radius-mapped-blocks-v1"
SCHEMA = "anyfem.source-chart-baseline"
VERSION = 2
_TWO_PI = 2.0 * math.pi

FAMILY_HOLDS = {
    "Cone": None,  # implemented here; see the module docstring for the evidence level
    "Cylinder": "cylinder is owned by mesh_curved_update (owner cylinder_patch certificate)",
    "ExtrudedSurface": (
        "extruded surface held: this adapter does not verify the trim, rim stations and physical "
        "sizing metric for an extruded source chart"),
    "RuledSurface": (
        "ruled surface held: this adapter does not bind the prior grid to the ruled chart or "
        "verify its varying metric and normals"),
    "CoonsSurface": (
        "Coons surface held: this adapter does not verify source boundary correspondence, interior "
        "regularity and physical sizing for a Coons chart"),
    "NurbsSurface": (
        "NURBS source chart held: this adapter does not verify trim and patch boundaries, "
        "parameter lifts and physical sizing for NURBS cells"),
}


def source_chart_hold(project: Any) -> str | None:
    """The exact hold text for every non-Cone, non-planar face of ``project`` (or ``None``)."""

    parts = []
    for fid, face in project.geometry.faces.items():
        name = type(getattr(face, "surface", None)).__name__
        if name in ("Plane", "NoneType", "Cone"):
            continue
        parts.append(f"face {fid}: " + FAMILY_HOLDS.get(name, f"{name}: regional source-chart adapter is not implemented"))
    return "; ".join(parts) or None


# ----------------------------------------------------------------------- chart


class _Cone:
    """Analytic source cone and its oriented differential normal."""

    def __init__(self, rec: Mapping[str, Any], tol: float = 0.0) -> None:
        vec = lambda k: np.array([float.fromhex(s) for s in rec[k]], dtype=float)
        num = lambda k: float.fromhex(rec[k])
        self.origin, self.axis, self.r, self.c = vec("origin"), vec("axis"), vec("radial"), vec("circumferential")
        self.r0, self.r1, self.height = num("radius_start"), num("radius_end"), num("height")
        self.start, self.sweep = num("start"), num("sweep")
        dr = self.r1 - self.r0
        if not (self.r0 > 0.0 and self.r1 > 0.0 and dr != 0.0 and self.height != 0.0
                and self.sweep != 0.0 and abs(self.sweep) < _TWO_PI):
            raise _Ineligible("cone support is degenerate (apex in patch, cylinder-like, or periodic)")
        angular = float.fromhex(rec["angular_tolerance"]) if "angular_tolerance" in rec else 0.0
        frame = np.array([self.r, self.c, self.axis])
        if float(np.max(np.abs(frame @ frame.T - np.eye(3)))) > math.sin(angular) + _GAMMA8:
            raise _Ineligible("cone frame is not orthonormal")
        self.slant = math.hypot(self.height, dr)
        from anygeometry import Cone
        self.surface = Cone(self.origin, self.axis, self.r, self.r0, self.r1,
                            self.height, self.start, self.sweep)
        self.angular_limit = math.sin(angular) + _GAMMA8
        self.eps_angle = max(angular, float(tol) / min(self.r0, self.r1), _GAMMA8 * max(_TWO_PI, abs(self.start)))

    def radius(self, v: float) -> float:
        return (1.0 - v) * self.r0 + v * self.r1

    def uv(self, point) -> tuple[float, float, float]:
        u, v = self.surface.local_uv(point)
        return u, v, float(np.linalg.norm(np.asarray(point) - self.xyz(u, v)))

    def xyz(self, u: float, v: float) -> np.ndarray:
        return self.surface.evaluate(u, v)

    def normal(self, u: float) -> np.ndarray:
        theta = self.start + u * self.sweep
        radial = math.cos(theta) * self.r + math.sin(theta) * self.c
        tangent_u = (-math.sin(theta) * self.r + math.cos(theta) * self.c) * (1.0 if self.sweep > 0.0 else -1.0)
        tangent_v = (self.r1 - self.r0) * radial + self.height * self.axis
        n = np.cross(tangent_u, tangent_v)
        return n / float(np.linalg.norm(n))

# ---------------------------------------------------------------------- source


def _support_record(surface: Any) -> dict[str, Any]:
    return {
        "kind": "cone", "origin": _hex(surface.origin), "axis": _hex(surface.axis),
        "radial": _hex(surface.radial_direction), "circumferential": _hex(surface.circumferential_direction),
        "radius_start": float(surface.radius_start).hex(), "radius_end": float(surface.radius_end).hex(),
        "height": float(surface.height).hex(), "start": float(surface.start_angle).hex(),
        "sweep": float(surface.sweep_angle).hex(),
    }


def _source(project: Any, settings: Any) -> dict[str, Any]:
    from anygeometry import Cone
    from anygeometry.curves import Arc, Straight

    try:
        g = project.geometry
        for name in ("members", "attachments", "junctions"):
            if getattr(g, name, None):
                raise _Ineligible(f"geometry has {name}; only one plain cone face is supported")
        if len(g.faces) != 1:
            raise _Ineligible("exactly one face is supported")
        fid = int(next(iter(g.faces)))
        face = g.faces[fid]
        surface = face.surface
        if type(surface) is not Cone:
            raise _Ineligible(f"support is {type(surface).__name__}, not an analytic Cone")
        if set(face.corners) != {0, 1, 2, 3}:
            raise _Ineligible("the four mapped source corners were not declared")
        if face.holes or len(face.loop) != 4:
            raise _Ineligible("the face is trimmed (holes) or its loop is not the four-edge chart rectangle")
        if face.parameterization is not None and face.parameterization is not surface:
            raise _Ineligible("an explicit face parameterization is outside the cone contract")
        uses = [u for u in g.face_uses.values() if int(u.face_id) == fid]
        if len(uses) != 1:
            raise _Ineligible("exactly one face use is supported")
        for use in face.loop:
            if len(tuple(g.faces_using_edge(int(use.edge)))) != 1:
                raise _Ineligible("an edge of the patch is shared with another face")
        bounds = g.conservative_face_bounds(fid)
        extent = float(np.linalg.norm(np.asarray(bounds[3:], float) - np.asarray(bounds[:3], float)))
        tol = float(g.tolerance.effective_length(extent))
        residual = float(g.tolerance.effective_surface_residual(extent))
        support = _support_record(surface)
        support["angular_tolerance"] = float(g.tolerance.angular).hex()
        cone = _Cone(support, tol)
        if ((_TWO_PI - abs(cone.sweep)) <= cone.eps_angle or abs(cone.sweep) <= cone.eps_angle
                or min(cone.r0, cone.r1, abs(cone.height)) <= tol):
            raise _Ineligible("the cone rim, height or open seam cannot be distinguished at source tolerance")
        # Dimension-specific owner tolerances: the u length is shortest at the small radius (largest
        # parameter tolerance for a fixed length error). v runs along the slant.
        # Use the largest area density to bound a physical area deficit at any v.
        arc_min = min(cone.r0, cone.r1) * abs(cone.sweep)
        arc_max = max(cone.r0, cone.r1) * abs(cone.sweep)
        chart_extent = max(arc_max, cone.slant)
        u_tol = max(g.tolerance.effective_parameter(arc_min, chart_extent), cone.eps_angle / abs(cone.sweep))
        v_tol = max(g.tolerance.effective_parameter(cone.slant, chart_extent), _GAMMA8)
        support["uv_tolerances"] = _hex([u_tol, v_tol])
        support["uv_area_tolerance"] = float(g.tolerance.effective_area(chart_extent) / (arc_max * cone.slant)).hex()
        natural = float(np.asarray(g.face_normal(fid, 0.5, 0.5), dtype=float) @ cone.normal(0.5))
        if not natural > cone.angular_limit:
            raise _Ineligible("cone chart normal is inconsistent with the source face normal")
        support["normal_sign"] = int(uses[0].orientation)
        edges, ends, points = [], [], []
        for use in face.loop:
            edge = g.edges[int(use.edge)]
            a, b = (edge.start, edge.end) if use.forward else (edge.end, edge.start)
            edges.append([int(use.edge), bool(use.forward)])
            ends.append([int(a), int(b)])
            points.append(np.asarray(g.vertex_position(a), dtype=float))
        uv_corners = ((0., 0.), (1., 0.), (1., 1.), (0., 1.))
        corners = [cone.xyz(*c) for c in uv_corners]
        # Loop vertices must be the four chart corners (a rotation or reflection of the loop is allowed).
        matched = [min(range(4), key=lambda k: float(np.linalg.norm(p - corners[k]))) for p in points]
        if sorted(matched) != [0, 1, 2, 3] or any(
                float(np.linalg.norm(p - corners[k])) > tol for p, k in zip(points, matched)):
            raise _Ineligible("the face loop vertices are not the four corners of the full cone chart rectangle")
        for i, use in enumerate(face.loop):
            edge = g.edges[int(use.edge)]
            u0, v0 = uv_corners[matched[i]]
            u1, v1 = uv_corners[matched[(i + 1) % 4]]
            if u0 == u1 and v0 != v1 and type(edge.curve) is Straight:
                continue
            if v0 != v1 or u0 == u1 or type(edge.curve) is not Arc:
                raise _Ineligible("the source rim must contain two circular arcs and two straight generators")
            frame = g.arc_frame(edge.id)
            center = cone.origin + v0 * cone.height * cone.axis
            alignment = float(frame.normal @ cone.axis)
            oriented_sweep = frame.sweep * (1 if use.forward else -1) * (1 if alignment > 0 else -1)
            if (np.linalg.norm(frame.center - center) > tol or abs(frame.radius - cone.radius(v0)) > tol
                    or np.linalg.norm(np.cross(frame.normal, cone.axis)) > cone.angular_limit
                    or abs(oriented_sweep - (u1 - u0) * cone.sweep) > cone.eps_angle):
                raise _Ineligible("a source rim arc differs from the cone's circle or oriented sweep")
        sheets = sorted(int(s.id) for s in g.sheets.values()
                        if any(g.face_uses[u].face_id == fid for u in s.face_use_ids))
        return {
            "face": fid, "sheets": sheets, "support": support, "tolerance": tol.hex(), "surface_tolerance": residual.hex(),
            "trim_evidence": "source corner positions, oriented circular rim supports, straight generators and mesh UV coverage",
            "model": str(g.model_id), "revision": int(g.revision),
            "loop": {"edges": edges, "ends": ends, "points": _hex(points)},
            "refinements": _refinement_records(project, g, dict(settings.overrides)),
            "overrides": [[int(e), int(n)] for e, n in sorted((int(e), int(n)) for e, n in settings.overrides)],
            "target_size": float(settings.target_size).hex(), "order": str(settings.element_order),
        }
    except _Ineligible:
        raise
    except Exception as error:  # noqa: BLE001
        raise _Ineligible(f"source geometry unreadable: {type(error).__name__}: {error}") from error


_FIXED = ("face", "sheets", "support", "tolerance", "surface_tolerance", "trim_evidence", "model", "revision", "loop", "overrides",
          "target_size", "order")


# -------------------------------------------------------------------- coverage


def _coverage(mesh: Any, source: Mapping[str, Any]):
    """Independent proof in the cone chart that ``mesh`` tiles the full chart rectangle."""

    problems: list[str] = []
    fid = int(source["face"])
    tol = float.fromhex(source["tolerance"])
    cone = _Cone(source["support"], tol)
    utol, vtol = (float.fromhex(x) for x in source["support"]["uv_tolerances"])
    ids = [int(e) for e in mesh.elements_of_face.get(fid, ())]
    if not ids or any(e not in mesh.quads or len(mesh.quads[e]) != 4 for e in ids):
        return ["the face has no usable all-quad element provenance"], None
    conns = {e: tuple(int(n) for n in mesh.quads[e]) for e in ids}
    uv: dict[int, tuple[float, float]] = {}
    for n in sorted({n for c in conns.values() for n in c}):
        u, v, off = cone.uv(mesh.nodes[n])
        if off > float.fromhex(source["surface_tolerance"]):
            problems.append(f"node {n} is {off!r} off the exact cone")
        elif not (-utol <= u <= 1.0 + utol and -vtol <= v <= 1.0 + vtol):
            problems.append(f"node {n} is outside the chart rectangle (u={u!r}, v={v!r})")
        else:
            uv[n] = (u, v)
    if problems:
        return problems[:6], None
    uv_signs, normal_signs, directed, areas, cross_scales = set(), set(), {}, [], []
    expected_sign = source["support"]["normal_sign"]
    for eid, conn in conns.items():
        poly = [uv[n] for n in conn]
        # Diagonal cross product avoids translating tiny cells to the chart origin.
        dx1, dy1 = poly[2][0] - poly[0][0], poly[2][1] - poly[0][1]
        dx2, dy2 = poly[3][0] - poly[1][0], poly[3][1] - poly[1][1]
        pos, neg = dx1 * dy2, dx2 * dy1
        a = (pos - neg) / 2
        if not a * expected_sign > 0.0:
            problems.append(f"element {eid} is degenerate or reversed in the source chart")
            continue
        uv_signs.add(a > 0.0)
        cross_scales.append((abs(pos) + abs(neg)) / 2)
        areas.append(abs(a))
        p = [np.asarray(mesh.nodes[n], dtype=float) for n in conn]
        q = np.cross(p[2] - p[0], p[3] - p[1])
        length = float(np.linalg.norm(q))
        if length == 0.0:
            problems.append(f"element {eid} has no 3D normal")
            continue
        s = float(q @ cone.normal(sum(x[0] for x in poly) / 4.0)) / length
        if (not expected_sign * s > cone.angular_limit or
                not _corner_jacobians(p, cone, expected_sign) > cone.angular_limit):
            problems.append(f"element {eid} normal is not aligned with the source face normal on the cone")
        normal_signs.add(s > 0.0)
        for i in range(4):
            edge = (conn[i], conn[(i + 1) % 4])
            if edge in directed:
                problems.append(f"directed edge {edge} used twice")
            directed[edge] = eid
    if len(uv_signs) != 1 or len(normal_signs) != 1:
        problems.append("elements do not share one orientation in the chart and on the cone normal")
    area = math.fsum(areas)
    area_error = _GAMMA8 * math.fsum(cross_scales) + math.ulp(area)
    if abs(area - 1.0) > float.fromhex(source["support"]["uv_area_tolerance"]) + area_error:
        problems.append(f"element chart area {area!r} differs from the chart rectangle area 1")
    sides = {"u0": [], "u1": [], "v0": [], "v1": []}
    for (a, b) in directed:
        if (b, a) in directed:
            continue
        (ua, va), (ub, vb) = uv[a], uv[b]
        if abs(ua) <= utol and abs(ub) <= utol:
            sides["u0"].append(abs(vb - va))
        elif abs(ua - 1) <= utol and abs(ub - 1) <= utol:
            sides["u1"].append(abs(vb - va))
        elif abs(va) <= vtol and abs(vb) <= vtol:
            sides["v0"].append(abs(ub - ua))
        elif abs(va - 1) <= vtol and abs(vb - 1) <= vtol:
            sides["v1"].append(abs(ub - ua))
        else:
            problems.append(f"free edge {a}-{b} is not on the chart boundary")
    for name, segments in sides.items():
        length = math.fsum(segments)
        endpoint_error = 2 * (vtol if name.startswith("u") else utol)
        if abs(length - 1.0) > endpoint_error + _GAMMA8 * length:
            problems.append(f"chart side {name} is covered by {length!r} of 1")
    for corner in ((0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)):
        if not any(abs(p[0] - corner[0]) <= utol and abs(p[1] - corner[1]) <= vtol for p in uv.values()):
            problems.append(f"no mesh node at chart corner {corner}")
    sign = 0 if problems else (1 if next(iter(normal_signs)) else -1)
    return problems[:6], {"uv": uv, "sign": sign, "ptol": (utol, vtol), "tol": tol}


# --------------------------------------------------------------------- capture


def _source_pins() -> dict[str, str]:
    pins = dict(_pins())
    pins["baseline_schema"] = f"{SCHEMA}/{VERSION}"
    pins["route"] = ROUTE
    return pins


def _global(project: Any, settings: Any) -> Any:
    return _plain(_global_state(project, settings, _strategy(project, settings), _document(project.geometry)))


def _capture(project: Any, settings: Any, mesh: Any) -> dict[str, Any]:
    hold = source_chart_hold(project)
    if hold:
        raise _Ineligible(hold)
    if not all(hasattr(mesh, name) for name in ("elements_of_face", "quads", "tris", "nodes")):
        raise _Ineligible("mesh carries no face provenance")
    if _strategy(project, settings) not in ("auto", "mapped"):
        raise _Ineligible("the cone route requires an automatic or mapped quad strategy")
    if mesh.tris:
        raise _Ineligible("this cone adapter requires an all-Q4 baseline; mixed S3 preparation is not implemented here")
    from .mesh_controls import MeshControls
    from anymesher.recovery import MeshAutomationOptions

    controls = settings.controls or MeshControls.from_settings(project.native_mesh_settings)
    if (not controls.recombine or controls.certification_mode != "interactive" or
            settings.quad_options is not None or project.edge_sections or
            settings.automation not in (None, MeshAutomationOptions()) or
            project.sheet_join_intent_problems() or project._feature_materialization_problems()):
        raise _Ineligible("the cone route requires resolved plates and ordinary interactive quad controls")
    if mesh.order != "linear" or settings.element_order != "linear":
        raise _Ineligible("only linear order is supported")
    if mesh.geometry_model_id is None or mesh.geometry_revision is None or mesh.beams or mesh.couplings:
        raise _Ineligible("the prior mesh has no source identity, or has beams/couplings")
    if (mesh.declared_plate_junction_edges or mesh.automatic_intersections
            or mesh.automatic_beam_connections or mesh.automatic_shell_connections):
        raise _Ineligible("mesh has structural connections")
    source = _source(project, settings)
    fid = int(source["face"])
    if str(mesh.geometry_model_id) != source["model"] or int(mesh.geometry_revision) != source["revision"]:
        raise _Ineligible("the mesh was not generated from this geometry model/revision")
    if {int(f) for f in mesh.elements_of_face} != {fid}:
        raise _Ineligible("mesh does not cover exactly the cone face")
    shells = _shells(mesh)
    face_elements = {int(e) for e in mesh.elements_of_face[fid]}
    if {int(k): set(v) for k, v in mesh.elements_of_sheet.items()} != {s: face_elements for s in source["sheets"]}:
        raise _Ineligible("sheet element membership differs from the source face uses")
    if face_elements != set(shells) or {n for e in shells.values() for n in e} != {int(n) for n in mesh.nodes}:
        raise _Ineligible("mesh has elements or nodes outside the cone face")
    problems, info = _coverage(mesh, source)
    problems = problems + _mcu._associations(mesh, source)
    if problems:
        raise _Ineligible("mesh does not match the exact cone: " + "; ".join(problems[:4]))
    uv = info["uv"]
    try:
        _mapped_groups(mesh, uv, [], info["ptol"])
    except SourceChartUnsupported as error:
        raise _Ineligible(f"cone rim station ranks are ambiguous: {error}") from error
    return _plain({
        "schema": SCHEMA, "version": VERSION, "pins": _source_pins(), "global": _global(project, settings),
        "face": fid, "source": source, "source_digest": _digest(source),
        "orientation": info["sign"], "order": str(mesh.order), "mesh": _binding(mesh, fid),
    })


def explain_source_chart_ineligibility(project: Any, settings: Any, mesh: Any) -> str | None:
    try:
        _capture(project, settings, mesh)
    except Exception as error:  # noqa: BLE001
        return f"{type(error).__name__}: {error}"
    return None


def capture_source_chart_baseline(project: Any, settings: Any, mesh: Any) -> dict[str, Any] | None:
    """Serializable baseline for a verified single uncut cone mesh, else ``None`` (never blocks generation)."""

    try:
        return _capture(project, settings, mesh)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------------ plan


@dataclass(frozen=True, eq=False)
class SourceChartPlan:
    description: str
    applicable: bool = False
    is_noop: bool = False
    covered: tuple[str, ...] = ()
    blockers: tuple[tuple[str, str], ...] = ()
    fallback_allowed: bool = False
    _analysis: Any = field(default=None, repr=False)


@dataclass
class _Analysis:
    source: Mapping[str, Any]
    zone: Mapping[str, Any]
    center: np.ndarray
    radius_influence: float
    target: float
    covered: list


def _check_zone(rec: Mapping[str, Any], source: Mapping[str, Any]):
    src = rec["source"]
    if rec["mode"] != "refine" or src["kind"] != "point":
        raise _Ineligible(f"a {rec['mode']} zone bound to {src['kind']} is not a point zone on the surface")
    size, radius, growth = (float.fromhex(rec[k]) for k in ("size", "radius", "growth"))
    target = float.fromhex(source["target_size"])
    if not (math.isfinite(size) and size > 0.0 and radius >= 0.0 and math.isfinite(radius) and growth > 1.0):
        raise _Ineligible("the zone size/radius/growth is outside the supported range")
    if size >= target:
        raise _Ineligible("the zone is not finer than the target size (no mesh effect)")
    influence = _influence_radius(rec, target)
    tol = float.fromhex(source["tolerance"])
    cone = _Cone(source["support"], tol)
    center = np.array([float.fromhex(s) for s in src["xyz"]], dtype=float)
    u, v, off = cone.uv(center)
    if off > float.fromhex(source["surface_tolerance"]) or not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
        raise _Ineligible("the zone centre is not on the exact cone patch (no projection is guessed)")
    if not influence < min(cone.r0, cone.r1):
        raise _Ineligible("the influence radius is not smaller than the smallest cone radius (wrap-around reach)")
    # On either circular rim the closest point has the centre's angle.
    # On a generator the closest point is the projection onto its finite segment.
    distances = [float(np.linalg.norm(center - cone.xyz(u, end))) for end in (0., 1.)]
    for side in (0., 1.):
        start = cone.xyz(side, 0.)
        edge = cone.xyz(side, 1.) - start
        t = float(np.clip((center - start) @ edge / (edge @ edge), 0., 1.))
        distances.append(float(np.linalg.norm(center - (start + t * edge))))
    if min(distances) <= influence + tol:
        raise _Ineligible("the zone's influence region is not wholly inside the face")
    return center, influence, target


def _analyse(project: Any, settings: Any, baseline: Any, check: Callable[[str], Any]):
    try:
        if not isinstance(baseline, Mapping) or baseline.get("schema") != SCHEMA:
            raise _Ineligible("not a source-chart baseline")
        if baseline.get("version") != VERSION:
            raise _Ineligible("unsupported source-chart baseline version")
        if dict(baseline.get("pins", {})) != _source_pins():
            raise _Ineligible("build pins changed; a full rebuild is required")
        if not isinstance(baseline.get("mesh"), Mapping) or not isinstance(baseline.get("global"), Mapping):
            raise _Ineligible("incomplete mesh binding or global settings")
        source = baseline.get("source")
        if not isinstance(source, Mapping) or baseline.get("source_digest") != _digest(source):
            raise _Ineligible("baseline source definition fails its digest")
    except _Ineligible as error:
        return None, [("SOURCE_CHART_BASELINE_INVALID", str(error))], False
    now, before = _global(project, settings), dict(baseline["global"])
    differing = sorted(k for k in set(now) | set(before) if now.get(k) != before.get(k))
    if differing:
        return None, [("SOURCE_CHART_GLOBAL_CHANGED", "global dependencies changed (" + ", ".join(differing)
                       + "); not a local edit")], False
    try:
        new = _source(project, settings)
    except _Ineligible as error:
        return None, [("SOURCE_CHART_UNSUPPORTED", str(error))], False
    old = baseline["source"]
    check("source chart diff")
    changed = [k for k in _FIXED if _plain(new[k]) != old[k]]
    if changed:
        return None, [("SOURCE_CHART_BOUNDARY_MISMATCH",
                       "support, trim or source model changed (" + ", ".join(changed) + ")")], False
    old_c = Counter(_canon(r) for r in old["refinements"])
    new_c = Counter(_canon(r) for r in new["refinements"])
    removed = [json.loads(k) for k in (old_c - new_c).elements()]
    added = [json.loads(k) for k in (new_c - old_c).elements()]
    if not removed and not added:
        return None, [], True
    if removed or len(added) != 1:
        return None, [("SOURCE_CHART_ZONE_CHANGE_UNSUPPORTED",
                       f"only the addition of exactly one point refinement zone is supported (removed {len(removed)}, "
                       f"added {len(added)})")], False
    try:
        center, influence, target = _check_zone(added[0], new)
    except _Ineligible as error:
        return None, [("SOURCE_CHART_ZONE_UNSUPPORTED", str(error))], False
    covered = [f"point zone added: size {float.fromhex(added[0]['size']):g}, influence radius {influence:g}"]
    return _Analysis(new, added[0], center, influence, target, covered), [], False


def plan_source_chart_update(project: Any, settings: Any, baseline: Any, *,
                             cancellation_check: Callable[[str], Any] | None = None) -> SourceChartPlan:
    if baseline is None:
        hold = source_chart_hold(project)
        return SourceChartPlan("source-chart regional update refused: "
                               + (hold or "no eligible baseline was recorded for this project"))
    analysis, blockers, noop = _analyse(project, settings, baseline, _check_fn(cancellation_check))
    if noop:
        return SourceChartPlan("no change to the cone refinements or boundary", is_noop=True)
    if analysis is None or blockers:
        text = "; ".join(f"{code}: {message}" for code, message in blockers)
        return SourceChartPlan(f"source-chart patch not applicable ({text})", blockers=tuple(blockers))
    return SourceChartPlan("cone upper-radius chart patch: " + "; ".join(analysis.covered), True, False,
                           tuple(analysis.covered), (), False, analysis)


# -------------------------------------------------------------------- generate


@dataclass(frozen=True, eq=False)
class SourceChartResult:
    mesh: Any
    baseline: dict | None
    retained_nodes: frozenset
    retained_elements: frozenset
    plan: SourceChartPlan
    provenance: Mapping[str, Any]


def generate_source_chart_update(project: Any, settings: Any, baseline: Any, old_mesh: Any, *,
                                 node_high_water: int, element_high_water: int,
                                 cancellation_check: Callable[[str], Any] | None = None) -> SourceChartResult:
    from anymesher.errors import MeshError
    from anymesher.mesh import Mesh
    from anymesher.refinement import Refinement, SizeField
    from anymesher.regional_patch import KernelInput, owner_planar_kernel
    from anymesher.regional_update import assemble_regional_replacement

    check = _check_fn(cancellation_check)
    plan = plan_source_chart_update(project, settings, baseline, cancellation_check=cancellation_check)
    if not plan.applicable:
        raise SourceChartUnsupported(plan.description,
                                     code=plan.blockers[0][0] if plan.blockers else "SOURCE_CHART_NOT_APPLICABLE",
                                     details={"blockers": list(plan.blockers)})
    analysis: _Analysis = plan._analysis
    source = analysis.source
    fid = int(source["face"])
    old = _decode(old_mesh)
    if _binding(old, fid) != baseline["mesh"]:
        raise SourceChartError("old mesh does not match the baseline", code="SOURCE_CHART_OLD_MESH_MISMATCH")
    problems, info = _coverage(old, baseline["source"])
    problems = problems + _mcu._associations(old, baseline["source"])
    if problems or info is None:
        raise SourceChartError("old mesh does not match the baseline cone: " + "; ".join(problems[:3]),
                               code="SOURCE_CHART_OLD_MESH_MISMATCH")
    tol, ptol, uv, sign = info["tol"], info["ptol"], info["uv"], int(info["sign"])
    cone = _Cone(source["support"], tol)

    rec = analysis.zone
    zone = Refinement(size=float.fromhex(rec["size"]), radius=float.fromhex(rec["radius"]),
                      center=tuple(float(x) for x in analysis.center), growth=float.fromhex(rec["growth"]),
                      mode="refine")
    field_ = SizeField(project.geometry, analysis.target, (zone,))
    face_ids = sorted(int(e) for e in old.elements_of_face[fid])
    cell_xyz = {e: [np.asarray(old.nodes[n], dtype=float) for n in old.quads[e]] for e in face_ids}
    centroids = np.array([sum(cell_xyz[e]) / 4.0 for e in face_ids], dtype=float)
    selected = [e for e, c, want in zip(face_ids, centroids, field_.size_at(centroids))
                if float(np.linalg.norm(c - analysis.center)) <= analysis.radius_influence
                and _longest(cell_xyz[e]) > float(want) + tol]
    if not selected:
        raise SourceChartUnsupported("no cell inside the influence region is coarser than the zone requests",
                                     code="SOURCE_CHART_NO_AFFECTED_CELLS")
    groups = _mapped_groups(old, uv, selected, ptol)
    selected = sorted(e for cells, _, _ in groups for e in cells)
    if len(selected) > MAX_REPLACED_CELLS:
        raise SourceChartUnsupported(f"{len(selected)} cells are affected; capped at {MAX_REPLACED_CELLS}",
                                     code="SOURCE_CHART_TOO_MANY_CELLS")
    for eid in selected:
        pts = [uv[int(n)] for n in old.quads[eid]]
        if any(min(abs(p[d]), abs(p[d] - 1)) <= ptol[d] for p in pts for d in (0, 1)):
            raise SourceChartUnsupported(f"affected cell {eid} touches the face boundary",
                                         code="SOURCE_CHART_BOUNDARY_CELL")
        area_tol = float.fromhex(source["support"]["uv_area_tolerance"])
        for i, point in enumerate(pts):
            a = np.asarray(pts[(i + 1) % 4]) - point
            b = np.asarray(pts[(i - 1) % 4]) - point
            pos, neg = a[0] * b[1], a[1] * b[0]
            if sign * (pos - neg) > area_tol + _GAMMA8 * (abs(pos) + abs(neg)):
                continue
            raise SourceChartUnsupported(f"affected cell {eid} is not strictly convex in the chart",
                                         code="SOURCE_CHART_NONCONVEX_CELL")
    check("source chart staging")

    used_e = [0] + [int(k) for table in (old.quads, old.tris, old.beams, old.couplings) for k in table]
    counters = {"node": max(int(node_high_water), max(int(n) for n in old.nodes)) + 1,
                "element": max(int(element_high_water), max(used_e)) + 1}

    def take(kind: str) -> int:
        counters[kind] += 1
        return counters[kind] - 1

    selected_set = set(selected)
    conns = {e: tuple(int(n) for n in c) for e, c in old.quads.items()}
    interface = ({n for e in selected for n in conns[e]}
                 & {n for e, c in conns.items() if e not in selected_set for n in c})
    stage = Mesh(geometry_model_id=old.geometry_model_id, geometry_revision=old.geometry_revision, order="linear")
    stage.nodes, stage.quads = {}, {}
    boundary_ids, new_elements, max_edges = {}, [], {}
    core_target = float(field_.size_at(analysis.center)[0])
    for cells, rim_ids, rim_uv in groups:
        activity = {old.activity.get(e) for e in cells}
        if len(activity) != 1:
            raise SourceChartUnsupported("a block with nonuniform activity requires a source-bound transfer")
        block_activity = activity.pop()
        # In normalized source coordinates the metric is diagonal. Its two
        # scale factors are |sweep|*radius(v) and the slant length. Using the
        # largest radius on this block bounds physical edge length everywhere
        # inside it and preserves rectangular tensor-product station lines.
        u_scale = abs(cone.sweep) * max(cone.radius(v) for _, v in rim_uv)
        scale = np.array([u_scale, cone.slant])
        ring = np.asarray(rim_uv) * scale
        fixed = {}
        for n, p, xy in zip(rim_ids, rim_uv, ring):
            if np.linalg.norm(cone.xyz(*p) - old.nodes[n]) > tol:
                raise SourceChartUnsupported("ranked cone rim station misses its prior node")
            if n not in boundary_ids:
                boundary_ids[n] = n if n in interface else take("node")
                stage.nodes[boundary_ids[n]] = np.array(old.nodes[n], copy=True)
            fixed[tuple(xy)] = boundary_ids[n]
        output = owner_planar_kernel(KernelInput(ring, (), core_target, True, "linear"))
        if output.tris or not output.quads:
            raise SourceChartUnsupported("cone chart block requires a complete owner quad patch")
        rows = []
        lower, upper = np.min(rim_uv, axis=0), np.max(rim_uv, axis=0)
        for point in output.points:
            chart_point = point / scale
            if np.any(chart_point < lower - ptol) or np.any(chart_point > upper + ptol):
                raise SourceChartUnsupported("owner patch returned a station beyond the cone block metric bounds")
            node = fixed.get(tuple(point))
            if node is None:
                node = take("node")
                stage.nodes[node] = cone.xyz(*chart_point)
            rows.append(node)
        if not set(fixed.values()) <= set(rows):
            raise SourceChartUnsupported("owner patch moved a frozen cone block station")
        limit = max(_longest(cell_xyz[e]) for e in cells)
        for cell in output.quads:
            conn = tuple(rows[int(i)] for i in cell)
            if _corner_jacobians([stage.nodes[n] for n in conn], cone, sign) < 0:
                conn = tuple(reversed(conn))
            e = take("element")
            stage.quads[e] = conn
            if block_activity is not None:
                stage.activity[e] = float(block_activity)
            max_edges[e] = limit
            new_elements.append(e)
    stage.elements_of_face = {fid: list(new_elements)}
    stage.elements_of_sheet = {int(s): list(new_elements) for s in source["sheets"]}
    for e in new_elements:
        xyz = [stage.nodes[n] for n in stage.quads[e]]
        if not _corner_jacobians(xyz, cone, sign) > cone.angular_limit:
            raise SourceChartError(f"cell {e} has a non-positive 3D Jacobian on the cone", code="SOURCE_CHART_JACOBIAN")
        if _longest(xyz) > max_edges[e] + tol:
            raise SourceChartError(f"cell {e} exceeds its prior block edge length", code="SOURCE_CHART_SIZE_POLICY")

    previous = copy.deepcopy(old)
    dropped = [name for name in ("grid_of_face", "block_grids_of_face") if fid in getattr(previous, name)]
    for name in dropped:
        getattr(previous, name).pop(fid)
    try:
        assembly = assemble_regional_replacement(
            previous, stage, quality=_quality_limits(settings),
            node_ids={n: n for n in stage.nodes if n not in interface},
            element_ids={e: e for e in stage.quads}, affected_element_ids=selected,
            interface_node_ids={n: n for n in sorted(interface)},
            stations={n: tuple(float(x) for x in old.nodes[n]) for n in sorted(interface)},
            coordinate_tolerance=0.0, discard_stale_diagnostics=True)
    except MeshError as error:
        raise SourceChartError(f"owner splice refused the patch: {error}", code="SOURCE_CHART_SPLICE_REFUSED") from error
    check("source chart verify")
    new = assembly.mesh
    if str(new.geometry_model_id) != source["model"] or new.geometry_revision != source["revision"]:
        raise SourceChartError("owner splice returned a different geometry identity", code="SOURCE_CHART_RESULT_UNVERIFIED")
    if min(assembly.new_element_ids) <= int(element_high_water) or \
            (assembly.new_node_ids and min(assembly.new_node_ids) <= int(node_high_water)):
        raise SourceChartError("a new id is not above the high water mark", code="SOURCE_CHART_RESULT_UNVERIFIED")
    retired = set(selected)
    if set(assembly.retired_element_ids) != retired:
        raise SourceChartError("owner splice retired other cells than the analytic selection",
                               code="SOURCE_CHART_RESULT_UNVERIFIED")
    for eid, conn in conns.items():
        if eid not in retired and (tuple(new.quads[eid]) != conn
                                   or any(not np.array_equal(old.nodes[n], new.nodes[n]) for n in conn)):
            raise SourceChartError(f"retained element {eid} changed", code="SOURCE_CHART_RESULT_UNVERIFIED")
    norm = lambda table: {k: (list(v) if not isinstance(v, int) else v) for k, v in table.items()}
    for table in ("nodes_of_edge", "elements_of_edge", "node_of_vertex"):
        if norm(getattr(old, table)) != norm(getattr(new, table)):
            raise SourceChartError(f"{table} of the retained boundary changed", code="SOURCE_CHART_RESULT_UNVERIFIED")
    expected = sorted((set(old.elements_of_face[fid]) - retired) | set(new_elements))
    if sorted(int(e) for e in new.elements_of_face[fid]) != expected or any(
            sorted(int(e) for e in new.elements_of_sheet[int(s)]) != expected for s in source["sheets"]):
        raise SourceChartError("face/sheet element provenance differs from the spliced cells",
                               code="SOURCE_CHART_RESULT_UNVERIFIED")
    final, new_info = _coverage(new, source)
    final = final + _mcu._associations(new, source)
    if final or new_info is None or new_info["sign"] != sign:
        raise SourceChartError("updated mesh does not match the exact cone: "
                               + "; ".join(final[:4] or ["orientation flipped"]), code="SOURCE_CHART_RESULT_UNVERIFIED")
    for e in new_elements:
        if not _corner_jacobians([new.nodes[n] for n in new.quads[e]], cone, sign) > cone.angular_limit:
            raise SourceChartError(f"published cell {e} has a non-positive Jacobian",
                                   code="SOURCE_CHART_RESULT_UNVERIFIED")
    met = [e for e in new_elements
           if np.linalg.norm(np.mean([new.nodes[n] for n in new.quads[e]], axis=0) - analysis.center)
           <= analysis.radius_influence
           and _longest([new.nodes[n] for n in new.quads[e]]) <= core_target + tol]
    if not met:
        raise SourceChartUnsupported(f"no new cell meets the zone's centre target size {core_target:g} within {tol:g}",
                                     code="SOURCE_CHART_CENTRE_TARGET_UNMET")
    retained_nodes = frozenset(int(n) for n in old.nodes) & frozenset(int(n) for n in new.nodes)
    retained_elements = frozenset(int(e) for e in conns) - retired
    provenance = _plain({
        "operation": "anyfem.source_chart_update", "route": ROUTE, "family": "Cone",
        "owner": "anymesher.regional_update.assemble_regional_replacement",
        "chart": "anygeometry.Cone.evaluate/local_uv",
        "sizing_metric": "block maximum radius times absolute sweep in u; slant length in v",
        "changes": list(plan.covered), "replaced_elements": len(retired), "new_elements": len(new_elements),
        "new_nodes": len(assembly.new_node_ids), "retained_elements": len(retained_elements),
        "interface_nodes": len(interface), "mapped_blocks": len(groups),
        "kernel": "anymesher.regional_patch.owner_planar_kernel", "size_field": "anymesher.refinement.SizeField",
        "centre_target_size": core_target, "new_cells_meeting_centre_target": len(met),
        "dropped_grids": dropped, "diagnostics_discarded": list(assembly.discarded_diagnostic_keys),
        "node_high_water": max(int(node_high_water), max(int(n) for n in new.nodes)),
        "element_high_water": max(int(element_high_water), max(int(e) for e in new.quads)),
        "owner_quality": None if assembly.quality is None else assembly.quality.as_dict(),
        "anyfem_checks": ["nodes_on_exact_cone", "chart_tiling_area_orientation_free_edges", "loop_vertices_at_chart_corners",
                          "affected_blocks_at_source_rim_station_ranks", "positive_corner_jacobian_and_cone_normal", "retained_cells_bit_exact",
                          "ids_above_high_water", "face_sheet_edge_vertex_provenance", "project_quality_policy"],
        "anyfem_unchecked": ["trimmed or periodic cone domains",
                             "uniform target attainment throughout the transition layer",
                             "equality_with_a_full_remesh", "mapped grid and whole-mesh diagnostics are dropped"],
    })
    _validate_quality(project, new, settings, cancellation_check)
    updated = capture_source_chart_baseline(project, settings, new)
    if updated is None:
        raise SourceChartError("the updated mesh is not baseline-eligible: "
                               + str(explain_source_chart_ineligibility(project, settings, new)),
                               code="SOURCE_CHART_BASELINE_UNAVAILABLE")
    updated["last_update"] = provenance
    return SourceChartResult(new, updated, retained_nodes, retained_elements, plan, provenance)
