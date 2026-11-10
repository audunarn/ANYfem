"""Local size updates on one intact, open cylindrical Q4 plate.

ANYgeometry must certify the full four-sided cylinder chart and its source
bindings. The prior all-quad mesh must cover that same support with consistent
orientation, sheet membership and edge associations. Four explicitly declared
mapped corners establish the input grid. An added interior point refinement is
evaluated by the owner's SizeField.

Complete two-by-two blocks covering the affected cells are developed in physical
arc-length/axial coordinates. The ANYmesher planar kernel builds a smaller core
and conforming transition quads on each block's fixed rim. New nodes are lifted
to the cylinder. Membership and size depend on the resulting cell coordinates.
The splice preserves independent exterior cells, including their numbering. The
candidate must pass chart coverage, physical Jacobians, owner quality and the
requested local edge-size check before the application may publish it.
"""

from __future__ import annotations

import copy
import json
import math
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping

import numpy as np

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
    "CurvedPlan",
    "CurvedUpdateError",
    "CurvedUpdateResult",
    "CurvedUpdateUnsupported",
    "ROUTE",
    "capture_curved_baseline",
    "classify_curved_support",
    "explain_curved_ineligibility",
    "generate_curved_update",
    "plan_curved_update",
]

ROUTE = "cylinder-developed-mapped-blocks-v1"
SCHEMA = "anyfem.curved-baseline"
VERSION = 2
MAX_REPLACED_CELLS = 64
_TWO_PI = 2.0 * math.pi
# A length-three dot product, a 2D cross product of differences, and a
# final rounded sum each use fewer than eight binary64 operations.
_UNIT_ROUNDOFF = math.ulp(1.0) / 2
_GAMMA8 = 8 * _UNIT_ROUNDOFF / (1 - 8 * _UNIT_ROUNDOFF)

_PROOF = "no verified owner chart route for this baseline in the cylinder adapter"

_FAMILY_REASON = {
    "Cylinder": "cylinder: " + _PROOF + " (requires a qualified, untrimmed, mapped Q4 source)",
    "Cone": "cone: " + _PROOF + " (an eligible conical frustum uses the cone source-chart adapter)",
    "ExtrudedSurface": "extruded surface: " + _PROOF + " (the regional trim and sizing checks are unimplemented)",
    "RuledSurface": "ruled surface: " + _PROOF + " (the regional parameter and metric checks are unimplemented)",
    "CoonsSurface": "Coons surface: " + _PROOF + " (the regional boundary and metric checks are unimplemented)",
}


class CurvedUpdateError(RuntimeError):
    """A curved update failed closed (never a fallback by itself)."""

    fallback_allowed = False

    def __init__(self, message: str, *, code: str = "CURVED_UPDATE_FAILED",
                 details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class CurvedUpdateUnsupported(CurvedUpdateError):
    """The curved regional update is refused; the message states the exact reason."""


class _Ineligible(Exception):
    pass


# ------------------------------------------------------------------ classification


def classify_curved_support(project: Any) -> dict[int, str]:
    """Face id -> support family name for every non-planar face (``Plane``/None omitted)."""

    result: dict[int, str] = {}
    for face_id, face in project.geometry.faces.items():
        surface = getattr(face, "surface", None)
        name = type(surface).__name__ if surface is not None else "Plane"
        if name != "Plane":
            result[int(face_id)] = name
    return result


def _family_reason(project: Any) -> str | None:
    families = classify_curved_support(project)
    if not families:
        return None
    parts = [f"face {f}: " + _FAMILY_REASON.get(n, f"{n}: unknown family; " + _PROOF)
             for f, n in sorted(families.items())]
    return "; ".join(parts)


# ---------------------------------------------------------------- cylinder chart


class _Cyl:
    """Certified cylinder frame: (u, v) in [0,1]^2 <-> xyz.  theta = start + u*sweep, z = v*height."""

    def __init__(self, rec: Mapping[str, Any], tol: float = 0.0) -> None:
        def vec(key):
            return np.array([float.fromhex(s) for s in rec[key]], dtype=float)

        def num(key):
            return float.fromhex(rec[key])

        self.origin, self.axis = vec("origin"), vec("axis")
        self.r, self.c = vec("radial"), vec("circumferential")
        self.radius, self.height = num("radius"), num("height")
        self.start, self.sweep = num("start"), num("sweep")
        if not (self.radius > 0.0 and self.height != 0.0 and self.sweep != 0.0
                and abs(self.sweep) < _TWO_PI):
            raise _Ineligible("cylinder support is degenerate or periodic")
        angular = float.fromhex(rec["angular_tolerance"]) if "angular_tolerance" in rec else 0.0
        frame = np.array([self.r, self.c, self.axis])
        if float(np.max(np.abs(frame @ frame.T - np.eye(3)))) > math.sin(angular) + _GAMMA8:
            raise _Ineligible("cylinder frame is not orthonormal")
        self.arc = self.radius * abs(self.sweep)
        self.length = abs(self.height)
        self.eps_angle = max(angular, float(tol) / self.radius, _GAMMA8 * max(_TWO_PI, abs(self.start)))

    def uv(self, point) -> tuple[float, float, float]:
        """(u, v, off-surface distance).  The periodic gap maps to u > 1 and is rejected by callers."""

        d = np.asarray(point, dtype=float) - self.origin
        x, y, z = float(d @ self.r), float(d @ self.c), float(d @ self.axis)
        sign = 1.0 if self.sweep > 0.0 else -1.0
        diff = (sign * (math.atan2(y, x) - self.start)) % _TWO_PI
        if diff > _TWO_PI - self.eps_angle:
            diff -= _TWO_PI
        return diff / abs(self.sweep), z / self.height, abs(math.hypot(x, y) - self.radius)

    def xyz(self, u: float, v: float) -> np.ndarray:
        theta = self.start + u * self.sweep
        return (self.origin + self.radius * (math.cos(theta) * self.r + math.sin(theta) * self.c)
                + (v * self.height) * self.axis)

    def normal(self, u: float) -> np.ndarray:
        theta = self.start + u * self.sweep
        return math.cos(theta) * self.r + math.sin(theta) * self.c


def _support_record(surface: Any) -> dict[str, Any]:
    return {
        "kind": "cylinder", "origin": _hex(surface.origin), "axis": _hex(surface.axis),
        "radial": _hex(surface.radial_direction), "circumferential": _hex(surface.circumferential_direction),
        "radius": float(surface.radius).hex(), "height": float(surface.height).hex(),
        "start": float(surface.start_angle).hex(), "sweep": float(surface.sweep_angle).hex(),
    }


def _cross2(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _convexity(uv4) -> float:
    """Smallest signed turn / largest turn magnitude (>0 strictly convex, orientation +; <0 all clockwise)."""

    turns = [_cross2(uv4[i], uv4[(i + 1) % 4], uv4[(i + 2) % 4]) for i in range(4)]
    if all(t > 0 for t in turns):
        return min(turns) / max(turns)
    if all(t < 0 for t in turns):
        return max(turns) / min(turns)
    return 0.0


def _longest(xyz4) -> float:
    return max(float(np.linalg.norm(np.asarray(xyz4[(i + 1) % 4]) - np.asarray(xyz4[i]))) for i in range(4))


def _corner_jacobians(xyz4, cyl: _Cyl, sign: int) -> float:
    """Smallest per-corner scaled Jacobian along the owner normal (positive = same side as the baseline)."""

    pts = [np.asarray(p, dtype=float) for p in xyz4]
    worst = math.inf
    for k in range(4):
        e1, e0 = pts[(k + 1) % 4] - pts[k], pts[(k - 1) % 4] - pts[k]
        scale = float(np.linalg.norm(e1) * np.linalg.norm(e0))
        if scale == 0.0:
            return -math.inf
        u = cyl.uv(pts[k])[0]
        worst = min(worst, sign * float(np.cross(e1, e0) @ cyl.normal(u)) / scale)
    return worst


# ----------------------------------------------------------------- source model


def _source(project: Any, settings: Any) -> dict[str, Any]:
    """Current source definitions + owner patch proof of the one cylinder face, or ``_Ineligible``."""

    from anygeometry.cylinder_patch import (
        CylinderPatchStatus,
        query_cylinder_patch,
        validate_cylinder_patch_binding,
    )
    from anygeometry.identity import EntityHandle
    from anygeometry.surfaces import Cylinder

    try:
        geometry = project.geometry
        for name in ("members", "attachments", "junctions"):
            if getattr(geometry, name, None):
                raise _Ineligible(f"geometry has {name}; only one plain cylinder face is supported")
        if len(geometry.faces) != 1:
            raise _Ineligible("exactly one face is supported")
        fid = int(next(iter(geometry.faces)))
        face = geometry.faces[fid]
        surface = face.surface
        if type(surface) is not Cylinder:
            raise _Ineligible(f"support is {type(surface).__name__}, not an analytic Cylinder")
        if set(face.corners) != {0, 1, 2, 3}:
            raise _Ineligible("the four mapped source corners were not declared")
        if face.holes or len(face.loop) != 4:
            raise _Ineligible("the face is trimmed (holes) or its loop is not the four-edge support rectangle")
        if face.parameterization is not None and face.parameterization is not surface:
            raise _Ineligible("an explicit face parameterization is outside the cylinder patch contract")
        uses = [u for u in geometry.face_uses.values() if int(u.face_id) == fid]
        if len(uses) != 1:
            raise _Ineligible("exactly one face use is supported")
        handle = EntityHandle(geometry.model_id, "face_use", uses[0].id)
        revision = int(geometry.revision)
        result = query_cylinder_patch(geometry, (handle,), expected_revision=revision)
        if result.status is not CylinderPatchStatus.QUALIFIED:
            raise _Ineligible(f"owner cylinder patch is {result.status.value}: {'; '.join(result.diagnostics)}")
        validate_cylinder_patch_binding(geometry, result, (handle,), expected_revision=revision)
        sheet_id = int(geometry.face_uses[uses[0].id].sheet_id)
        expected = {"model": geometry.model_id, "face": EntityHandle(geometry.model_id, "face", fid),
                    "sheet": EntityHandle(geometry.model_id, "sheet", sheet_id),
                    "part": EntityHandle(geometry.model_id, "part", int(geometry.sheets[sheet_id].part_id))}
        if (result.model_id != expected["model"] or int(result.revision) != revision
                or result.requested_face_uses != (handle,) or result.face_use != handle
                or result.face != expected["face"] or result.sheet != expected["sheet"]
                or result.part != expected["part"]):
            raise _Ineligible("owner patch identity (model/revision/face use/face/sheet/part) differs from the project")
        if not result.certificate.complete:
            raise _Ineligible("owner patch certificate is not complete")
        if len(result.loops) != 1 or result.loops[0].role != "OUTER" or len(result.occurrences) != 4:
            raise _Ineligible("owner patch is not one four-occurrence outer loop")
        loop_edges = {int(use.edge) for use in face.loop}
        if {int(o.edge.id) for o in result.occurrences} != loop_edges or any(
                o.loop_index != 0 for o in result.occurrences):
            raise _Ineligible("owner patch occurrences do not bind exactly the face loop edges")
        if any(o.external_coedges for o in result.occurrences):
            raise _Ineligible("an edge of the patch is shared with another face (not an intact single patch)")
        tol = float(result.certificate.tolerance_length)
        support = _support_record(surface)
        support["angular_tolerance"] = float(result.certificate.tolerance_angular).hex()
        cyl = _Cyl(support, tol)
        for key, owner in (("origin", result.source_origin), ("axis", result.axis),
                           ("radial", result.radial_direction)):
            if not np.array_equal([float.fromhex(x) for x in support[key]], owner):
                raise _Ineligible(f"owner patch {key} differs from the face support")
        for key, value in (("radius", result.radius), ("height", result.height),
                           ("start", result.start_angle), ("sweep", result.sweep_angle)):
            if float.fromhex(support[key]) != value:
                raise _Ineligible(f"owner patch {key} differs from the face support")
        extent = max(cyl.arc, cyl.length)
        u_tol = max(geometry.tolerance.effective_parameter(cyl.arc, extent), cyl.eps_angle / abs(cyl.sweep))
        v_tol = max(geometry.tolerance.effective_parameter(cyl.length, extent), _GAMMA8)
        support["uv_tolerances"] = _hex([u_tol, v_tol])
        # The developable metric has constant area factor R*|sweep|*|height|.
        support["uv_area_tolerance"] = float(geometry.tolerance.effective_area(extent) /
                                             (cyl.arc * cyl.length)).hex()
        loop = result.loops[0]
        bounds = ((0., u_tol), (1., u_tol), (0., v_tol), (1., v_tol))
        if len(loop.uv_bounds) != 4 or any(
                lo < target - limit or hi > target + limit or lo > hi
                for (lo, hi), (target, limit) in zip(loop.uv_bounds, bounds)):
            raise _Ineligible("the face is trimmed: its certified loop is not the full support rectangle")
        orientation = int(result.face_use_orientation)
        if orientation not in (-1, 1) or orientation != int(uses[0].orientation):
            raise _Ineligible("owner cylinder face-use orientation differs from the source")
        # dX/du cross dX/dv is radial with the sign of sweep*height.
        natural = 1 if cyl.sweep * cyl.height > 0 else -1
        normal = geometry.face_normal(fid, .5, .5)
        if (loop.winding != orientation or
                natural * float(normal @ cyl.normal(.5)) <= math.sin(float(result.certificate.tolerance_angular))):
            raise _Ineligible("cylinder loop winding is inconsistent with the source face normal")
        support["uv_sign"] = orientation
        support["normal_sign"] = natural * orientation
        edges, ends, points = [], [], []
        for use in face.loop:
            edge = geometry.edges[int(use.edge)]
            a, b = (edge.start, edge.end) if use.forward else (edge.end, edge.start)
            edges.append([int(use.edge), bool(use.forward)])
            ends.append([int(a), int(b)])
            points.append(np.asarray(geometry.vertex_position(a), dtype=float))
        sheets = sorted(int(s.id) for s in geometry.sheets.values()
                        if any(geometry.face_uses[u].face_id == fid for u in s.face_use_ids))
        overrides = {int(e): int(n) for e, n in settings.overrides}
        return {
            "face": fid, "sheets": sheets, "support": support, "tolerance": tol.hex(),
            "patch": {"digest": result.digest, "algorithm": result.certificate.algorithm,
                      "status": result.status.value, "revision": revision},
            "model": str(geometry.model_id), "revision": revision,
            "loop": {"edges": edges, "ends": ends, "points": _hex(points)},
            "refinements": _refinement_records(project, geometry, dict(settings.overrides)),
            "overrides": [[e, n] for e, n in sorted(overrides.items())],
            "target_size": float(settings.target_size).hex(), "order": str(settings.element_order),
        }
    except _Ineligible:
        raise
    except Exception as error:  # noqa: BLE001 - any owner/geometry failure = ineligible
        raise _Ineligible(f"source geometry unreadable: {type(error).__name__}: {error}") from error


# -------------------------------------------------------- mesh vs. owner surface


def _coverage(mesh: Any, source: Mapping[str, Any]):
    """Independent proof in the certified chart that ``mesh`` tiles the support.

    Returns ``(problems, info)``; ``info`` holds node UV and the element orientation sign.
    """

    problems: list[str] = []
    fid = int(source["face"])
    tol = float.fromhex(source["tolerance"])
    cyl = _Cyl(source["support"], tol)
    utol, vtol = (float.fromhex(x) for x in source["support"]["uv_tolerances"])
    ids = [int(e) for e in mesh.elements_of_face.get(fid, ())]
    if not ids or any(e not in mesh.quads or len(mesh.quads[e]) != 4 for e in ids):
        return ["the face has no usable all-quad element provenance"], None
    conns = {e: tuple(int(n) for n in mesh.quads[e]) for e in ids}
    used = sorted({n for c in conns.values() for n in c})
    uv: dict[int, tuple[float, float]] = {}
    for n in used:
        u, v, residual = cyl.uv(mesh.nodes[n])
        if residual > tol:
            problems.append(f"node {n} is {residual!r} off the owner cylinder")
        elif not (-utol <= u <= 1.0 + utol and -vtol <= v <= 1.0 + vtol):
            problems.append(f"node {n} is outside the support UV domain or in the periodic gap (u={u!r}, v={v!r})")
        else:
            uv[n] = (u, v)
    if problems:
        return problems[:6], None
    areas, cross_scales, directed = [], [], {}
    sign = source["support"]["normal_sign"]
    for eid, conn in conns.items():
        poly = [uv[n] for n in conn]
        # Diagonal cross product avoids translating tiny cells to the chart origin.
        dx1, dy1 = poly[2][0] - poly[0][0], poly[2][1] - poly[0][1]
        dx2, dy2 = poly[3][0] - poly[1][0], poly[3][1] - poly[1][1]
        pos, neg = dx1 * dy2, dx2 * dy1
        area = (pos - neg) / 2
        if not area * source["support"]["uv_sign"] > 0:
            problems.append(f"element {eid} has incorrect source chart orientation or zero area")
            continue
        cross_scales.append((abs(pos) + abs(neg)) / 2)
        areas.append(abs(area))
        p = [np.asarray(mesh.nodes[n], dtype=float) for n in conn]
        q = np.cross(p[2] - p[0], p[3] - p[1])
        length = float(np.linalg.norm(q))
        if length == 0.0:
            problems.append(f"element {eid} has no 3D normal")
            continue
        s = float(q @ cyl.normal(sum(x[0] for x in poly) / 4.0)) / length
        if not sign * s >= 0.5:
            problems.append(f"element {eid} normal is not aligned with the owner surface normal")
        for i in range(4):
            edge = (conn[i], conn[(i + 1) % 4])
            if edge in directed:
                problems.append(f"directed edge {edge} used twice (overlap or flipped cell)")
            directed[edge] = eid
    area = math.fsum(areas)
    # gamma8 bounds the diagonal cross arithmetic at its observed product
    # scale. fsum rounds the final sum once; the owner area policy is physical.
    area_error = _GAMMA8 * math.fsum(cross_scales) + math.ulp(area)
    if abs(area - 1.0) > float.fromhex(source["support"]["uv_area_tolerance"]) + area_error:
        problems.append(f"element chart area {area!r} differs from the support area 1")
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
            problems.append(f"free edge {a}-{b} is not on the support boundary")
    for name, segments in sides.items():
        length = math.fsum(segments)
        # Both ends of the interval can move by one owner parameter tolerance.
        endpoint_error = 2 * (vtol if name.startswith("u") else utol)
        if abs(length - 1.0) > endpoint_error + _GAMMA8 * length:
            problems.append(f"support side {name} is covered by {length!r} of 1")
    for corner in ((0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0)):
        if not any(abs(p[0] - corner[0]) <= utol and abs(p[1] - corner[1]) <= vtol for p in uv.values()):
            problems.append(f"no mesh node at support corner {corner}")
    return problems[:6], {"uv": uv, "sign": sign, "ptol": (utol, vtol), "tol": tol}


def _associations(mesh: Any, source: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    fid = int(source["face"])
    tol = float.fromhex(source["tolerance"])
    face_elements = {int(e) for e in mesh.elements_of_face.get(fid, ())}
    quads = {int(e): tuple(int(n) for n in c) for e, c in mesh.quads.items() if int(e) in face_elements}
    directed = {(c[i], c[(i + 1) % 4]) for c in quads.values() for i in range(4)}
    points = [np.asarray([float.fromhex(s) for s in source["loop"]["points"]]).reshape(-1, 3)][0]
    for (edge_id, forward), (a, b), start in zip(source["loop"]["edges"], source["loop"]["ends"], points):
        seq = [int(n) for n in mesh.nodes_of_edge.get(edge_id, ())]
        if len(seq) < 2 or any(n not in mesh.nodes for n in seq):
            problems.append(f"edge {edge_id} has no node association")
            continue
        endpoints = (mesh.node_of_vertex.get(a), mesh.node_of_vertex.get(b))
        if (seq[0], seq[-1]) != (endpoints if forward else endpoints[::-1]):
            problems.append(f"edge {edge_id} does not join its own source vertices")
        for x, y in zip(seq, seq[1:]):
            if ((x, y) in directed) == ((y, x) in directed):
                problems.append(f"edge {edge_id} association follows an interior edge {x}-{y}")
                break
        owners = mesh.elements_of_edge.get(edge_id)
        if owners is not None and not {int(e) for e in owners} <= face_elements:
            problems.append(f"edge {edge_id} element association leaves the face")
        node = mesh.node_of_vertex.get(a)
        if node is None or node not in mesh.nodes or \
                float(np.linalg.norm(np.asarray(mesh.nodes[node], float) - start)) > tol:
            problems.append(f"vertex {a} has no node association at its position")
    return problems


# ---------------------------------------------------------------------- capture


def _curved_pins() -> dict[str, str]:
    pins = dict(_pins())
    pins["baseline_schema"] = f"{SCHEMA}/{VERSION}"
    pins["route"] = ROUTE
    return pins


def _curved_global(project: Any, settings: Any) -> Any:
    return _plain(_global_state(project, settings, _strategy(project, settings), _document(project.geometry)))


def _capture(project: Any, settings: Any, mesh: Any) -> dict[str, Any]:
    if not all(hasattr(mesh, name) for name in ("elements_of_face", "quads", "tris", "nodes")):
        raise _Ineligible("mesh carries no face provenance")
    if _strategy(project, settings) not in ("auto", "mapped"):
        raise _Ineligible("the cylinder route requires an automatic or mapped quad strategy")
    if mesh.tris:
        raise _Ineligible("a mixed mesh is not supported")
    from .mesh_controls import MeshControls
    from anymesher.recovery import MeshAutomationOptions

    controls = settings.controls or MeshControls.from_settings(project.native_mesh_settings)
    if (not controls.recombine or controls.certification_mode != "interactive" or
            settings.quad_options is not None or project.edge_sections or
            settings.automation not in (None, MeshAutomationOptions()) or
            project.sheet_join_intent_problems() or project._feature_materialization_problems()):
        raise _Ineligible("the cylinder route requires resolved plates and ordinary interactive quad controls")
    if mesh.order != "linear" or settings.element_order != "linear":
        raise _Ineligible("only linear order is supported")
    if mesh.geometry_model_id is None or mesh.geometry_revision is None:
        raise _Ineligible("the prior mesh has no source identity")
    if mesh.beams or mesh.couplings:
        raise _Ineligible("mesh has beams or couplings")
    if (mesh.declared_plate_junction_edges or mesh.automatic_intersections
            or mesh.automatic_beam_connections or mesh.automatic_shell_connections):
        raise _Ineligible("mesh has structural connections")
    source = _source(project, settings)
    fid = int(source["face"])
    if str(mesh.geometry_model_id) != source["model"] or int(mesh.geometry_revision) != source["revision"]:
        raise _Ineligible("the mesh was not generated from this geometry model/revision")
    if {int(f) for f in mesh.elements_of_face} != {fid}:
        raise _Ineligible("mesh does not cover exactly the cylinder face")
    shells = _shells(mesh)
    face_elements = {int(e) for e in mesh.elements_of_face[fid]}
    if {int(k): set(v) for k, v in mesh.elements_of_sheet.items()} != {s: face_elements for s in source["sheets"]}:
        raise _Ineligible("sheet element membership differs from the source face uses")
    if face_elements != set(shells) or {n for e in shells.values() for n in e} != {int(n) for n in mesh.nodes}:
        raise _Ineligible("mesh has elements or nodes outside the cylinder face")
    problems, info = _coverage(mesh, source)
    problems = problems + _associations(mesh, source)
    if problems:
        raise _Ineligible("mesh does not match the owner surface: " + "; ".join(problems[:4]))
    return _plain({
        "schema": SCHEMA, "version": VERSION, "pins": _curved_pins(),
        "global": _curved_global(project, settings),
        "face": fid, "source": source, "source_digest": _digest(source),
        "orientation": info["sign"], "order": str(mesh.order), "mesh": _binding(mesh, fid),
    })


def explain_curved_ineligibility(project: Any, settings: Any, mesh: Any) -> str | None:
    try:
        _capture(project, settings, mesh)
    except Exception as error:  # noqa: BLE001
        return f"{type(error).__name__}: {error}"
    return None


def capture_curved_baseline(project: Any, settings: Any, mesh: Any) -> dict[str, Any] | None:
    """Serializable baseline for a verified single-cylinder-face mesh, else ``None`` (never blocks generation)."""

    try:
        return _capture(project, settings, mesh)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------------- plan


@dataclass(frozen=True, eq=False)
class CurvedPlan:
    description: str
    applicable: bool = False
    families: tuple[tuple[int, str], ...] = ()
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


def _validate_baseline(baseline: Any) -> None:
    if not isinstance(baseline, Mapping) or baseline.get("schema") != SCHEMA:
        raise _Ineligible("not a curved baseline")
    if baseline.get("version") != VERSION:
        raise _Ineligible("unsupported curved baseline version")
    if dict(baseline.get("pins", {})) != _curved_pins():
        raise _Ineligible("build pins changed; a full rebuild is required")
    if not isinstance(baseline.get("mesh"), Mapping) or not isinstance(baseline.get("global"), Mapping):
        raise _Ineligible("incomplete mesh binding or global settings")
    source = baseline.get("source")
    if not isinstance(source, Mapping) or baseline.get("source_digest") != _digest(source):
        raise _Ineligible("baseline source definition fails its digest")


_FIXED = ("face", "sheets", "support", "tolerance", "patch", "model", "revision", "loop",
          "overrides", "target_size", "order")


def _check_zone(rec: Mapping[str, Any], source: Mapping[str, Any]) -> tuple[np.ndarray, float, float]:
    """Validate one added zone against the certified support; return (center, influence radius, target)."""

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
    cyl = _Cyl(source["support"], tol)
    center = np.array([float.fromhex(s) for s in src["xyz"]], dtype=float)
    u, v, off = cyl.uv(center)
    if off > tol:
        raise _Ineligible("the zone centre is not on the owner surface (no projection is guessed)")
    if not influence < cyl.radius:
        raise _Ineligible("the influence radius is not smaller than the cylinder radius (wrap-around reach)")
    margin = min(u * cyl.arc, (1.0 - u) * cyl.arc, v * cyl.length, (1.0 - v) * cyl.length)
    if not margin > 0.5 * math.pi * influence + tol:
        raise _Ineligible("the zone's influence region is not wholly inside the face")
    return center, influence, target


def _analyse(project: Any, settings: Any, baseline: Any, check: Callable[[str], Any]):
    """Return ``(analysis | None, blockers, boundary_flag, noop)``."""

    try:
        _validate_baseline(baseline)
    except _Ineligible as error:
        return None, [("CURVED_BASELINE_INVALID", str(error))], False, False
    now = _curved_global(project, settings)
    before = dict(baseline["global"])
    differing = sorted(k for k in set(now) | set(before) if now.get(k) != before.get(k))
    if differing:
        return None, [("CURVED_GLOBAL_CHANGED", "global dependencies changed (" + ", ".join(differing)
                       + "); not a local edit")], False, False
    try:
        new = _source(project, settings)
    except _Ineligible as error:
        return None, [("CURVED_SOURCE_UNSUPPORTED", str(error))], False, False
    old = baseline["source"]
    check("curved source diff")
    changed = [k for k in _FIXED if _plain(new[k]) != old[k]]
    if changed:
        return None, [("CURVED_BOUNDARY_MISMATCH",
                       "support, trim, owner patch or source model changed (" + ", ".join(changed) + ")")], True, False
    old_c = Counter(_canon(r) for r in old["refinements"])
    new_c = Counter(_canon(r) for r in new["refinements"])
    removed = [json.loads(k) for k in (old_c - new_c).elements()]
    added = [json.loads(k) for k in (new_c - old_c).elements()]
    if not removed and not added:
        return None, [], False, True
    if removed or len(added) != 1:
        return None, [("CURVED_ZONE_CHANGE_UNSUPPORTED",
                       "only the addition of exactly one point refinement zone is supported "
                       f"(removed {len(removed)}, added {len(added)})")], False, False
    try:
        center, influence, target = _check_zone(added[0], new)
    except _Ineligible as error:
        return None, [("CURVED_ZONE_UNSUPPORTED", str(error))], False, False
    covered = [f"point zone added: size {float.fromhex(added[0]['size']):g}, influence radius {influence:g}"]
    return _Analysis(new, added[0], center, influence, target, covered), [], False, False


def _whole_sector_plan(project, settings, baseline, rejected, read_source):
    """Only an isolated rim-division change can expand this curved patch."""
    from .application.mesh_publication import mesh_source_digests
    from .mesh_whole_plate_update import WholePlatePlan

    if {code for code, _ in rejected.blockers} not in ({"CURVED_BOUNDARY_MISMATCH"},
                                                      {"SOURCE_CHART_BOUNDARY_MISMATCH"}):
        return rejected
    old = baseline["source"]
    source = _plain(read_source(project, settings))
    if {k for k in set(old) | set(source) if old.get(k) != source.get(k)} != {"overrides"} or source["refinements"]:
        return rejected
    parent = project.mesh_records.get(project.mesh_numbering.active_mesh_id)
    recorded = parent.summary if parent is not None else {}
    previous_inputs = recorded.get("mesh_sources", {})
    inputs = mesh_source_digests(project)
    if (recorded.get("regional_baseline", {}).get("mesh") != baseline["mesh"] or "geometry" not in previous_inputs
            or any(previous_inputs.get(k) != inputs.get(k) for k in (inputs.keys() | previous_inputs.keys()) - {"meshing"})):
        reason = "The published sector geometry and structural ownership must match the current source."
        return replace(rejected, description=reason, blockers=(("CURVED_RIM_INPUTS_CHANGED", reason),))
    g = project.geometry
    edges = {int(e) for e, _ in source["loop"]["edges"]}
    vertices = {v for e in edges for v in (g.edges[e].start, g.edges[e].end)}
    vertices.update(g.edges[e].curve.via_vertex for e in edges if hasattr(g.edges[e].curve, "via_vertex"))
    if (set(g.edges) != edges or set(g.vertices) != vertices or len(g.sheets) != 1
            or len(g.face_uses) != 1 or len(g.parts) != 1 or project.beam_edges or project.beam_offsets
            or project.read_only_reason or getattr(project.native_mesh_settings, "controls", ())
            or any(e not in edges for e, _ in old["overrides"] + source["overrides"])):
        reason = "Whole-sector reseeding requires a single fully owned rim and support."
        return replace(rejected, description=reason, blockers=(("CURVED_RIM_SCOPE", reason),))
    family = type(g.faces[source["face"]].surface).__name__.lower()
    before, after = dict(old["overrides"]), dict(source["overrides"])
    changes = {e for e in edges if before.get(e) != after.get(e)}
    reason = f"changed rim divisions on {family} edges {sorted(changes)}"
    return WholePlatePlan(f"Regenerate the whole open {family} sector (face {source['face']}) because of {reason}. "
                          "All mesh nodes and elements will receive new permanent IDs.", reason, source["face"])


def plan_curved_update(project: Any, settings: Any, baseline: Any, *,
                       cancellation_check: Callable[[str], Any] | None = None):
    from . import mesh_source_chart_update as chart

    if isinstance(baseline, Mapping) and baseline.get("schema") == chart.SCHEMA:
        plan = chart.plan_source_chart_update(project, settings, baseline, cancellation_check=cancellation_check)
        return _whole_sector_plan(project, settings, baseline, plan, chart._source)
    families = tuple(sorted(classify_curved_support(project).items()))
    if not families:
        return CurvedPlan("curved regional update refused: no curved support face; use the planar route",
                          False, families)
    if baseline is None:
        return CurvedPlan("curved regional update refused: " + str(_family_reason(project)), False, families)
    check = _check_fn(cancellation_check)
    analysis, blockers, boundary, noop = _analyse(project, settings, baseline, check)
    if noop:
        return CurvedPlan("no change to the cylinder refinements or boundary", False, families, is_noop=True)
    if analysis is None or blockers:
        text = "; ".join(f"{code}: {message}" for code, message in blockers)
        rejected = CurvedPlan(f"curved patch not applicable ({text})", False, families, False, (),
                              tuple(blockers), False)
        return _whole_sector_plan(project, settings, baseline, rejected, _source)
    return CurvedPlan("developed cylinder quad patch: " + "; ".join(analysis.covered), True, families,
                      False, tuple(analysis.covered), (), False, analysis)


# --------------------------------------------------------------------- generate


@dataclass(frozen=True, eq=False)
class CurvedUpdateResult:
    mesh: Any
    baseline: dict | None
    retained_nodes: frozenset
    retained_elements: frozenset
    plan: CurvedPlan
    provenance: Mapping[str, Any]


def _size_field(project: Any, analysis: Any) -> Any:
    """The owner ``anymesher.refinement.SizeField`` for the added zone (no local growth formula)."""

    from anymesher.refinement import Refinement, SizeField

    rec = analysis.zone
    zone = Refinement(size=float.fromhex(rec["size"]), radius=float.fromhex(rec["radius"]),
                      center=tuple(float(x) for x in analysis.center),
                      growth=float.fromhex(rec["growth"]), mode="refine")
    return SizeField(project.geometry, analysis.target, (zone,))


def _mapped_groups(mesh, uv, selected, ptol):
    """Locate complete two-by-two blocks using the source rectangle's rim stations."""
    axes = []
    for axis in (0, 1):
        # The zero side determines station rank; opposite stations must match
        # that parameter within the owner's inverse-chart tolerance.
        values = sorted(uv[n][axis] for n in uv if abs(uv[n][1-axis]) <= ptol[1-axis])
        if len(values) < 3 or np.min(np.diff(values)) <= 2 * ptol[axis]:
            raise CurvedUpdateUnsupported("ambiguous cylindrical rim station ranks")
        axes.append(np.asarray(values))
    ranks = {}
    for n, point in uv.items():
        pair = tuple(int(np.argmin(np.abs(axis-coordinate))) for axis, coordinate in zip(axes, point))
        if all(abs(axes[d][pair[d]] - point[d]) <= ptol[d] for d in (0, 1)):
            ranks[n] = pair
    grid, positions = {}, {}
    for eid, conn in mesh.quads.items():
        if any(n not in ranks for n in conn):
            continue
        corners = {ranks[n] for n in conn}
        i, j = min(p[0] for p in corners), min(p[1] for p in corners)
        if corners == {(i,j), (i+1,j), (i+1,j+1), (i,j+1)}:
            if (i, j) in grid:
                raise CurvedUpdateUnsupported("two cells occupy one source-chart grid position")
            grid[i,j], positions[eid] = eid, (i,j)
    if any(e not in positions for e in selected):
        raise CurvedUpdateUnsupported("the requested zone crosses a previously replaced or unmapped block")
    origins = sorted({(positions[e][0]//2*2, positions[e][1]//2*2) for e in selected})
    groups = []
    for i, j in origins:
        keys = [(i,j), (i+1,j), (i+1,j+1), (i,j+1)]
        if not all(k in grid for k in keys):
            raise CurvedUpdateUnsupported("the changed zone has no complete two-by-two mapped block")
        cells = [grid[k] for k in keys]
        ids = {ranks[n]: n for e in cells for n in mesh.quads[e]}
        if len({n for e in cells for n in mesh.quads[e]}) != 9:
            raise CurvedUpdateUnsupported("block cells do not share an intact nine-node grid")
        rim = [(i,j), (i+1,j), (i+2,j), (i+2,j+1), (i+2,j+2), (i+1,j+2), (i,j+2), (i,j+1)]
        groups.append((cells, [ids[k] for k in rim], [(axes[0][a],axes[1][b]) for a,b in rim]))
    return groups


def _check_sector_stations(geometry, source, mesh, check):
    """Verify source-edge station coordinates in canonical edge direction."""
    from anymesher.seeding import solve_seeding

    overrides = dict(source["overrides"])
    seeds = solve_seeding(geometry, target_size=float.fromhex(source["target_size"]), overrides=overrides,
                          cancellation_check=check)
    tolerance = float.fromhex(source["tolerance"])
    edges = {e for e, _ in source["loop"]["edges"]}
    if set(mesh.nodes_of_edge) != edges or not set(mesh.elements_of_edge) <= edges:
        raise CurvedUpdateUnsupported("The mesh does not have exactly the sector rim edge associations.")
    for edge in edges:
        check("verify whole-sector rim stations")
        count = seeds.divisions[edge]
        stations = mesh.nodes_of_edge[edge]
        if len(stations) != count + 1 or count != overrides.get(edge, count):
            raise CurvedUpdateUnsupported(f"Edge {edge} does not have its requested {overrides.get(edge, count)} divisions.")
        expected = geometry.sample_edge(edge, np.linspace(0., 1., count + 1))
        actual = np.asarray([mesh.nodes[n] for n in stations])
        if not np.all(np.linalg.norm(actual - expected, axis=1) <= tolerance):
            raise CurvedUpdateUnsupported(f"Ordered mesh stations on edge {edge} differ from the source curve.")


def _generate_whole_sector(project, settings, baseline, old, plan, node_high_water, element_high_water, check):
    from . import mesh_source_chart_update as chart
    from .document import DocumentSession
    from .mesh_jobs import run_mesh_job
    from .mesh_numbering import MeshNumbering, number_full_mesh, raise_high_water
    from .mesh_whole_plate_update import _Cancellation

    capture = chart._capture if baseline["schema"] == chart.SCHEMA else _capture
    original = replace(settings, overrides=tuple(tuple(x) for x in baseline["source"]["overrides"]), regional=None)
    check("verify previous curved sector")
    try:
        prior = capture(project, original, old)
    except _Ineligible as error:
        raise CurvedUpdateError(f"The parent failed its curved source checks: {error}") from error
    if any(baseline.get(k) != v for k, v in prior.items()):
        raise CurvedUpdateError("The parent differs from the verified curved-sector baseline.")
    _check_sector_stations(project.geometry, prior["source"], old, check)
    check("whole curved sector generation")
    snapshot = DocumentSession(project).snapshot()
    candidate = run_mesh_job(snapshot.thaw(), snapshot.revision.model_hash, replace(settings, regional=None),
                             lambda *_: None, _Cancellation(check)).mesh
    if candidate.hybrid_diagnostics.get("automation", {}).get("status") == "inspection_only":
        raise CurvedUpdateUnsupported("An inspection-only curved sector cannot be published.")
    check("verify whole curved sector")
    allocation = raise_high_water(MeshNumbering(node_high_water, element_high_water), old)
    new = number_full_mesh(candidate, allocation, source_geometry=project.geometry)
    _validate_quality(project, new, settings, check)
    try:
        updated = capture(project, settings, new)
    except _Ineligible as error:
        raise CurvedUpdateError(f"The generated sector failed its curved source checks: {error}") from error
    if updated["orientation"] != prior["orientation"]:
        raise CurvedUpdateError("The generated sector changed the source face orientation.")
    _check_sector_stations(project.geometry, updated["source"], new, check)
    provenance = {"operation": "anyfem.whole_curved_sector_update", "face": plan.face, "reason": plan.reason,
                  "retired_nodes": len(old.nodes), "retired_elements": len(old.quads),
                  "retained_nodes": 0, "retained_elements": 0}
    updated["last_update"] = provenance
    check("complete whole curved sector")
    return CurvedUpdateResult(new, updated, frozenset(), frozenset(), plan, provenance)


def generate_curved_update(project: Any, settings: Any, baseline: Any, old_mesh: Any, *,
                           node_high_water: int, element_high_water: int,
                           cancellation_check: Callable[[str], Any] | None = None) -> CurvedUpdateResult:
    from anymesher.mesh import Mesh
    from anymesher.regional_update import assemble_regional_replacement
    from anymesher.errors import MeshError
    from . import mesh_source_chart_update as chart
    from .mesh_whole_plate_update import WholePlatePlan

    check = _check_fn(cancellation_check)
    plan = plan_curved_update(project, settings, baseline, cancellation_check=cancellation_check)
    if isinstance(plan, WholePlatePlan):
        return _generate_whole_sector(project, settings, baseline, _decode(old_mesh), plan,
                                      node_high_water, element_high_water, check)
    if isinstance(plan, chart.SourceChartPlan):
        if plan.blockers and plan.blockers[0][0].startswith("CURVED_RIM_"):
            raise chart.SourceChartUnsupported(plan.description, code=plan.blockers[0][0])
        return chart.generate_source_chart_update(project, settings, baseline, old_mesh, node_high_water=node_high_water,
                                                  element_high_water=element_high_water, cancellation_check=cancellation_check)
    if not plan.applicable:
        raise CurvedUpdateUnsupported(plan.description,
                                      code=plan.blockers[0][0] if plan.blockers else "CURVED_NOT_APPLICABLE",
                                      details={"blockers": list(plan.blockers)})
    analysis: _Analysis = plan._analysis
    source = analysis.source
    fid = int(source["face"])
    old = _decode(old_mesh)
    if _binding(old, fid) != baseline["mesh"]:
        raise CurvedUpdateError("old mesh does not match the baseline", code="CURVED_OLD_MESH_MISMATCH")
    problems, info = _coverage(old, baseline["source"])
    problems = problems + _associations(old, baseline["source"])
    if problems or info is None:
        raise CurvedUpdateError("old mesh does not match the baseline surface: " + "; ".join(problems[:3]),
                                code="CURVED_OLD_MESH_MISMATCH")
    tol, ptol, uv = info["tol"], info["ptol"], info["uv"]
    sign = int(info["sign"])
    cyl = _Cyl(source["support"], tol)

    # -- choose the affected cells by analytic rules only
    selected: list[int] = []
    field_ = _size_field(project, analysis)
    face_ids = sorted(int(e) for e in old.elements_of_face[fid])
    cell_xyz = {eid: [np.asarray(old.nodes[n], dtype=float) for n in old.quads[eid]] for eid in face_ids}
    centroids = np.array([sum(cell_xyz[eid]) / 4.0 for eid in face_ids], dtype=float)
    sizes = field_.size_at(centroids)
    for eid, centroid, want in zip(face_ids, centroids, sizes):
        if float(np.linalg.norm(centroid - analysis.center)) > analysis.radius_influence:
            continue
        want = float(want)
        if _longest(cell_xyz[eid]) <= want + tol:
            continue
        selected.append(eid)
    if not selected:
        raise CurvedUpdateUnsupported("no cell inside the influence region is coarser than the zone requests",
                                      code="CURVED_NO_AFFECTED_CELLS")
    groups = _mapped_groups(old, uv, selected, ptol)
    selected = sorted(e for cells, _, _ in groups for e in cells)
    if len(selected) > MAX_REPLACED_CELLS:
        raise CurvedUpdateUnsupported(f"{len(selected)} cells are affected; the ring route is capped at "
                                      f"{MAX_REPLACED_CELLS}", code="CURVED_TOO_MANY_CELLS")
    for eid in selected:
        conn = tuple(int(n) for n in old.quads[eid])
        pts = [uv[n] for n in conn]
        if any(min(abs(p[d]), abs(p[d] - 1)) <= ptol[d] for p in pts for d in (0, 1)):
            raise CurvedUpdateUnsupported(f"affected cell {eid} touches the face boundary; only interior cells "
                                          "are supported", code="CURVED_BOUNDARY_CELL")
        if not _convexity(pts) > 1.0e-6:
            raise CurvedUpdateUnsupported(f"affected cell {eid} is not strictly convex in the support chart",
                                          code="CURVED_NONCONVEX_CELL")
    check("curved ring staging")

    # -- stage the replacement with permanent ids above both high waters
    used_e = [0] + [int(k) for table in (old.quads, old.tris, old.beams, old.couplings) for k in table]
    counters = {"node": max(int(node_high_water), max(int(n) for n in old.nodes)) + 1,
                "element": max(int(element_high_water), max(used_e)) + 1}

    def take(kind: str) -> int:
        value = counters[kind]
        counters[kind] += 1
        return value

    selected_set = set(selected)
    conns = {e: tuple(int(n) for n in c) for e, c in old.quads.items()}
    aff_nodes = {n for e in selected for n in conns[e]}
    out_nodes = {n for e, c in conns.items() if e not in selected_set for n in c}
    interface = aff_nodes & out_nodes
    stage = Mesh(geometry_model_id=old.geometry_model_id, geometry_revision=old.geometry_revision, order="linear")
    from anymesher.regional_patch import KernelInput, owner_planar_kernel

    stage.nodes, stage.quads = {}, {}
    boundary_ids = {}
    new_elements = []
    max_edges = {}
    core_target = float(field_.size_at(analysis.center)[0])
    lengths = np.array([cyl.arc, cyl.length])
    for cells, rim_ids, rim_uv in groups:
        activity = {old.activity.get(e) for e in cells}
        if len(activity) != 1:
            raise CurvedUpdateUnsupported("a block with nonuniform activity requires a source-bound transfer")
        block_activity = activity.pop()
        # A cylinder develops isometrically into (radius*angle, axial distance).
        # Deviation from the ranked station used by the owner kernel is checked
        # in 3D against the certified support tolerance; retain its original bytes.
        ring = np.asarray(rim_uv) * lengths
        fixed = {}
        for n, point, xyz2 in zip(rim_ids, rim_uv, ring):
            if np.linalg.norm(cyl.xyz(*point) - old.nodes[n]) > tol:
                raise CurvedUpdateUnsupported("ranked cylindrical rim station misses its prior node")
            if n not in boundary_ids:
                boundary_ids[n] = n if n in interface else take("node")
                stage.nodes[boundary_ids[n]] = np.array(old.nodes[n], copy=True)
            fixed[tuple(xyz2)] = boundary_ids[n]
        output = owner_planar_kernel(KernelInput(ring, (), core_target, True, "linear"))
        if output.tris or not output.quads:
            raise CurvedUpdateUnsupported("developed cylinder block requires a complete owner quad patch")
        rows = []
        for point in output.points:
            node = fixed.get(tuple(point))
            if node is None:
                node = take("node")
                stage.nodes[node] = cyl.xyz(*(point / lengths))
            rows.append(node)
        if not set(fixed.values()) <= set(rows):
            raise CurvedUpdateUnsupported("owner patch moved a frozen cylinder block station")
        limit = max(_longest(cell_xyz[e]) for e in cells)
        for cell in output.quads:
            conn = tuple(rows[int(i)] for i in cell)
            xyz = [stage.nodes[n] for n in conn]
            if _corner_jacobians(xyz, cyl, sign) < 0:
                conn = tuple(reversed(conn))
            e = take("element")
            stage.quads[e] = conn
            if block_activity is not None:
                stage.activity[e] = float(block_activity)
            max_edges[e] = limit
            new_elements.append(e)
    stage.elements_of_face = {fid: list(new_elements)}
    stage.elements_of_sheet = {int(s): list(new_elements) for s in source["sheets"]}

    # -- checks on the staged cells BEFORE the owner splice
    for element in new_elements:
        c = stage.quads[element]
        xyz = [stage.nodes[n] for n in c]
        if not _corner_jacobians(xyz, cyl, sign) > 1.0e-6:
            raise CurvedUpdateError(f"ring cell {element} has a non-positive 3D Jacobian on the owner surface",
                                    code="CURVED_JACOBIAN")
        if _longest(xyz) > max_edges[element] + tol:
            raise CurvedUpdateError(f"cell {element} exceeds its prior block edge length",
                                    code="CURVED_SIZE_POLICY")

    previous = copy.deepcopy(old)
    dropped_grids = [name for name in ("grid_of_face", "block_grids_of_face")
                     if fid in getattr(previous, name)]
    for name in dropped_grids:
        getattr(previous, name).pop(fid)
    try:
        assembly = assemble_regional_replacement(
            previous, stage, quality=_quality_limits(settings),
            node_ids={n: n for n in stage.nodes if n not in interface},
            element_ids={e: e for e in stage.quads},
            affected_element_ids=selected,
            interface_node_ids={n: n for n in sorted(interface)},
            stations={n: tuple(float(x) for x in old.nodes[n]) for n in sorted(interface)},
            coordinate_tolerance=0.0, discard_stale_diagnostics=True,
        )
    except MeshError as error:
        raise CurvedUpdateError(f"owner splice refused the ring patch: {error}", code="CURVED_SPLICE_REFUSED") from error
    check("curved verify")
    new = assembly.mesh
    if (str(new.geometry_model_id) != source["model"] or new.geometry_revision != source["revision"]):
        raise CurvedUpdateError("owner splice returned a different geometry identity")

    # -- independent verification of the published mesh
    if min(assembly.new_element_ids) <= int(element_high_water) or \
            (assembly.new_node_ids and min(assembly.new_node_ids) <= int(node_high_water)):
        raise CurvedUpdateError("a new id is not above the high water mark", code="CURVED_RESULT_UNVERIFIED")
    retired = set(selected)
    if set(assembly.retired_element_ids) != retired:
        raise CurvedUpdateError("owner splice retired other cells than the analytic selection",
                                code="CURVED_RESULT_UNVERIFIED")
    for eid, conn in conns.items():
        if eid in retired:
            continue
        if tuple(new.quads[eid]) != conn or any(not np.array_equal(old.nodes[n], new.nodes[n]) for n in conn):
            raise CurvedUpdateError(f"retained element {eid} changed", code="CURVED_RESULT_UNVERIFIED")
    for table in ("nodes_of_edge", "elements_of_edge", "node_of_vertex"):
        before, after = getattr(old, table), getattr(new, table)
        if {k: list(v) if not isinstance(v, int) else v for k, v in before.items()} != \
                {k: list(v) if not isinstance(v, int) else v for k, v in after.items()}:
            raise CurvedUpdateError(f"{table} of the retained boundary changed", code="CURVED_RESULT_UNVERIFIED")
    expected_face = sorted((set(old.elements_of_face[fid]) - retired) | set(new_elements))
    if sorted(int(e) for e in new.elements_of_face[fid]) != expected_face or any(
            sorted(int(e) for e in new.elements_of_sheet[int(s)]) != expected_face for s in source["sheets"]):
        raise CurvedUpdateError("face/sheet element provenance differs from the spliced cells",
                                code="CURVED_RESULT_UNVERIFIED")
    final, new_info = _coverage(new, source)
    final = final + _associations(new, source)
    if final or new_info is None or new_info["sign"] != sign:
        raise CurvedUpdateError("updated mesh does not match the owner surface: " + "; ".join(final[:4] or ["orientation flipped"]),
                                code="CURVED_RESULT_UNVERIFIED")
    for element in new_elements:
        if not _corner_jacobians([new.nodes[n] for n in new.quads[element]], cyl, sign) > 1.0e-6:
            raise CurvedUpdateError(f"published cell {element} has a non-positive Jacobian",
                                    code="CURVED_RESULT_UNVERIFIED")
    centre_target = float(field_.size_at(analysis.center)[0])
    centre_met = []
    for element in new_elements:
        xyz = [new.nodes[n] for n in new.quads[element]]
        local = np.linalg.norm(np.mean(xyz, axis=0) - analysis.center) <= analysis.radius_influence
        if local and _longest(xyz) <= centre_target + tol:
            centre_met.append(element)
    if not centre_met:
        raise CurvedUpdateUnsupported(
            f"no new cell meets the zone's centre target size {centre_target:g} within the owner tolerance "
            f"{tol:g}; the developed patch does not meet the requested size",
            code="CURVED_CENTRE_TARGET_UNMET")
    retained_nodes = frozenset(int(n) for n in old.nodes) & frozenset(int(n) for n in new.nodes)
    retained_elements = frozenset(int(e) for e in conns) - retired
    provenance = _plain({
        "operation": "anyfem.curved_update", "route": ROUTE,
        "owner": "anymesher.regional_update.assemble_regional_replacement",
        "owner_patch": source["patch"], "changes": list(plan.covered),
        "replaced_elements": len(retired), "new_elements": len(new_elements),
        "new_nodes": len(assembly.new_node_ids), "retained_elements": len(retained_elements),
        "interface_nodes": len(interface),
        "reissued_interior_nodes": len(assembly.retired_node_ids),
        "mapped_blocks": len(groups), "kernel": "anymesher.regional_patch.owner_planar_kernel",
        "size_field": "anymesher.refinement.SizeField", "centre_target_size": centre_target,
        "new_cells_meeting_centre_target": len(centre_met),
        "dropped_grids": dropped_grids, "diagnostics_discarded": list(assembly.discarded_diagnostic_keys),
        "node_high_water": max(int(node_high_water), max(int(n) for n in new.nodes)),
        "element_high_water": max(int(element_high_water), max(int(e) for e in new.quads)),
        "owner_quality": None if assembly.quality is None else assembly.quality.as_dict(),
        "anyfem_checks": ["owner_cylinder_patch_qualified_and_binding_validated", "source_model_revision_support_unchanged",
                          "uv_domain_and_periodic_gap", "uv_tiling_area_orientation_free_edges_corners",
                          "positive_corner_jacobian_and_owner_normal", "retained_cells_bit_exact",
                          "ids_above_high_water", "face_sheet_edge_vertex_provenance",
                          "new_edges_within_prior_block_limit", "project_quality_policy"],
        "anyfem_unchecked": ["uniform target attainment throughout the transition layer", "equality_with_a_full_remesh",
                             "mapped grid and whole-mesh diagnostics are dropped, not rebuilt"],
    })
    _validate_quality(project, new, settings, cancellation_check)
    updated = capture_curved_baseline(project, settings, new)
    if updated is None:
        raise CurvedUpdateError("the updated mesh is not baseline-eligible: "
                                + str(explain_curved_ineligibility(project, settings, new)),
                                code="CURVED_BASELINE_UNAVAILABLE")
    updated["last_update"] = provenance
    return CurvedUpdateResult(new, updated, retained_nodes, retained_elements, plan, provenance)
