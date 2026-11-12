"""Headless validation and application of an edited shell child of a submodel.

The editable child of a shell submodel is ``definition.child``, a detached
project.  A user may reshape its *interior* (a hole, an attached detail, a moved
interior vertex) but the governing boundary -- the cut edges that are driven
from the solved parent -- must stay exactly what the cut produced, and every
child face must still know which parent faces it descends from, because the
submodel solve transfers parent displacements through that lineage.

Nothing here touches Qt, the parent mesh, or any stored run.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np

from .document import canonical_hash
from .model.submodels import SubmodelDefinition, SubmodelRecordError
from .submodel import SubmodelError

__all__ = [
    "ChildEdit",
    "apply_child_edit",
    "build_replacement",
    "child_fingerprint",
    "definition_fingerprint",
    "detach_child",
    "validate_child_edit",
]

# Child registries that must stay empty for a detached v1 child (see ``child_problems``).
_RUN_REGISTRIES = ("jobs", "artifacts", "mesh_records", "analyses", "submodels", "submodel_runs")
_TRACE_PARAMETERS = np.linspace(0.0, 1.0, 33)


@dataclass(frozen=True)
class ChildEdit:
    """The outcome of checking (or applying) an edited child."""

    definition: SubmodelDefinition
    changed: bool
    mapped_faces: Mapping[int, Tuple[int, ...]] = field(default_factory=dict)  # new child face -> source faces
    removed_faces: Tuple[int, ...] = ()
    notes: Tuple[str, ...] = ()


# ----------------------------------------------------------------------
# identities
# ----------------------------------------------------------------------
def definition_fingerprint(definition: Any) -> str:
    """Identity of one stored definition (everything, including child and lineage)."""

    return canonical_hash(definition.to_dict())


def child_fingerprint(project: Any) -> str:
    """Identity of a child project's saved form (used to tell a draft from its baseline)."""

    from .io.project_file import project_to_dict

    return canonical_hash(project_to_dict(project))


def detach_child(definition: Any) -> Any:
    """A deep, independent project copy of ``definition.child`` made through project serialization."""

    from .io.project_file import project_from_dict, project_to_dict

    if getattr(definition, "child_kind", "shell") != "shell":
        raise SubmodelError("CHILD_EDIT_KIND", "Only the child of a shell submodel is edited here.")
    return project_from_dict(project_to_dict(definition.child))


def _refuse(code: str, message: str) -> None:
    raise SubmodelError(code, message)


# ----------------------------------------------------------------------
# geometry helpers
# ----------------------------------------------------------------------
def _fid(use: Any) -> int:
    if isinstance(use, (int, np.integer)):
        return int(use)
    for name in ("face_id", "id"):
        if hasattr(use, name):
            return int(getattr(use, name))
    return int(use[0])


def _edge_faces(geometry: Any) -> Dict[int, Tuple[int, ...]]:
    return {int(e): tuple(sorted({_fid(u) for u in geometry.faces_using_edge(e)})) for e in geometry.edges}


def _exceeds(value: float, tolerance: float) -> bool:
    """True when ``value`` is above ``tolerance`` *or not a number* (NaN must never pass a comparison)."""

    return not (float(value) <= tolerance)


def _trace(geometry: Any, edge_id: int) -> np.ndarray:
    try:
        points = np.asarray(geometry.sample_edge(edge_id, _TRACE_PARAMETERS), dtype=float).reshape(-1, 3)
    except (TypeError, ValueError) as error:
        _refuse("CHILD_CURVE_INVALID", f"Edge {edge_id} has an invalid shape ({error}).")
    if not np.isfinite(points).all():
        _refuse("CHILD_CURVE_INVALID", f"Edge {edge_id} has non-finite points.")
    return points


def _vertex_position(geometry: Any, vertex_id: int) -> np.ndarray:
    try:
        point = np.asarray(geometry.vertices[vertex_id].position, dtype=float).reshape(3)
    except (TypeError, ValueError) as error:
        _refuse("CHILD_VERTEX_INVALID", f"Vertex {vertex_id} has an invalid position ({error}).")
    if not np.isfinite(point).all():
        _refuse("CHILD_VERTEX_INVALID", f"Vertex {vertex_id} has a non-finite position.")
    return point


def _tolerance(*geometries: Any) -> float:
    extent = 0.0
    for geometry in geometries:
        box = geometry.bounds()
        if box is not None:
            extent = max(extent, float(np.linalg.norm(np.asarray(box[3:]) - np.asarray(box[:3]))))
    extent = extent or 1.0
    tolerance = 1.0e-9 * extent
    try:
        tolerance = max(tolerance, float(geometries[0].tolerance.effective_length(extent)))
    except Exception:  # noqa: BLE001 - a geometry without a tolerance object keeps the relative default
        pass
    return tolerance


def _same_edge(before: Any, after: Any, edge_id: int, tolerance: float) -> Optional[str]:
    """None when the edge is identical in both models, otherwise why not."""

    if edge_id not in after.edges:
        return "was deleted"
    old, new = before.edges[edge_id], after.edges[edge_id]
    if type(old.curve) is not type(new.curve):
        return f"changed curve type from {type(old.curve).__name__} to {type(new.curve).__name__}"
    if (old.start, old.end) != (new.start, new.end):
        return "changed its end vertices"
    for vertex in (old.start, old.end):
        if vertex not in after.vertices:
            return f"lost vertex {vertex}"
        if _exceeds(np.linalg.norm(_vertex_position(before, vertex) - _vertex_position(after, vertex)), tolerance):
            return f"moved vertex {vertex}"
    problem = _controls_differ(before, after, edge_id, old, new, tolerance)
    if problem is not None:
        return problem
    gap = float(np.max(np.linalg.norm(_trace(before, edge_id) - _trace(after, edge_id), axis=1)))
    if _exceeds(gap, tolerance):
        return f"changed shape by {gap:.3e}"
    return None


def _controls_differ(before: Any, after: Any, edge_id: int, old: Any, new: Any, tolerance: float) -> Optional[str]:
    """Compare the defining controls of a curve (the sampled trace alone cannot prove identity).

    Straight edges are fixed by their end vertices.  Splines, circular and elliptic arcs
    are compared through their controls within ``tolerance``.  Any other curve family is
    identical only when its definition compares equal; otherwise the edge is refused
    explicitly rather than guessed from a few samples.
    """

    from anygeometry.curves import Arc, Spline, Straight

    old_curve, new_curve = old.curve, new.curve
    if isinstance(old_curve, Straight):
        return None
    if isinstance(old_curve, Spline):
        if tuple(old_curve.control_vertices) != tuple(new_curve.control_vertices):
            return "changed its spline control vertices"
        for vertex in old_curve.control_vertices:
            if vertex not in after.vertices:
                return f"lost spline control vertex {vertex}"
            if _exceeds(np.linalg.norm(_vertex_position(before, vertex) - _vertex_position(after, vertex)), tolerance):
                return f"moved spline control vertex {vertex}"
        return None
    if isinstance(old_curve, Arc):
        a, b = before.arc_frame(edge_id), after.arc_frame(edge_id)
        for name in ("center", "e1", "e2"):
            if _exceeds(np.max(np.abs(np.asarray(getattr(a, name), dtype=float) - np.asarray(getattr(b, name), dtype=float))), tolerance):
                return f"changed its arc {name}"
        if (_exceeds(abs(float(a.radius) - float(b.radius)), tolerance)
                or _exceeds(abs(float(a.sweep) - float(b.sweep)), 1.0e-12)):
            return "changed its arc radius or sweep"
        return None
    names = ("center", "u_vector", "v_vector")
    if all(hasattr(old_curve, n) for n in names) and hasattr(old_curve, "sweep_angle"):  # elliptic arc
        for name in names:
            if _exceeds(np.max(np.abs(np.asarray(getattr(old_curve, name), dtype=float)
                                      - np.asarray(getattr(new_curve, name), dtype=float))), tolerance):
                return f"changed its elliptic arc {name}"
        for name in ("start_angle", "sweep_angle"):
            if _exceeds(abs(float(getattr(old_curve, name)) - float(getattr(new_curve, name))), 1.0e-12):
                return f"changed its elliptic arc {name}"
        return None
    try:
        same = bool(old_curve == new_curve) or repr(old_curve) == repr(new_curve)
    except Exception:  # noqa: BLE001 - an incomparable definition is not provably identical
        same = False
    if not same:
        return (f"is a {type(old_curve).__name__} whose definition could not be proved identical "
                "(unsupported curve family)")
    return None


class _Box:
    """The original cut volume, as the owner's :class:`BoxVolume` (conservative bounds and membership)."""

    def __init__(self, volume: Mapping[str, Any]) -> None:
        self.lower = np.asarray(volume["lower"], dtype=float)
        self.upper = np.asarray(volume["upper"], dtype=float)
        try:
            from anygeometry.volume_cut import BoxVolume

            self.volume = BoxVolume(lower=list(self.lower), upper=list(self.upper), transform=volume.get("transform") or None)
        except Exception as error:  # noqa: BLE001
            _refuse("CHILD_VOLUME_UNAVAILABLE", f"the original cut volume cannot be evaluated: {error}")

    def _excess(self, local: np.ndarray) -> float:
        local = np.asarray(local, dtype=float).reshape(-1, 3)
        excess = np.maximum(np.maximum(self.lower - local, local - self.upper), 0.0)
        return float(np.max(np.linalg.norm(excess, axis=1))) if len(local) else 0.0

    def outside(self, points: np.ndarray, tolerance: float) -> float:
        """Largest distance by which any point lies outside the volume (0 when all are inside)."""

        worst = self._excess(self.volume.local(np.asarray(points, dtype=float).reshape(-1, 3)))
        if not np.isfinite(worst):
            return float("inf")  # a non-finite point is never inside
        return worst if worst > tolerance else 0.0

    def edge_outside(self, geometry: Any, edge_id: int, tolerance: float) -> Tuple[float, bool]:
        """``(excess, decided)`` from the owner's conservative edge bounds.

        ``(0, True)``: proved inside.  ``(e, True)``: genuinely outside by ``e`` (tight bounds,
        or a sampled point already outside).  ``(0, False)``: loose bounds that exceed the box
        while no sampled point does -- not provable, so the caller refuses explicitly.
        """

        lo, hi, tight = _owner_edge_bounds(geometry, edge_id, self.volume)
        lo, hi = np.asarray(lo, dtype=float).reshape(3), np.asarray(hi, dtype=float).reshape(3)
        excess = float(np.linalg.norm(np.maximum(np.maximum(self.lower - lo, hi - self.upper), 0.0)))
        if excess <= tolerance:
            return 0.0, True
        sampled = self.outside(_trace(geometry, edge_id), tolerance)
        if tight or sampled:
            return (sampled or excess), True
        return 0.0, False


def _owner_edge_bounds(geometry: Any, edge_id: int, volume: Any) -> Tuple[np.ndarray, np.ndarray, bool]:
    """The owner's conservative edge bounds in the box frame (a seam, so tests can substitute it)."""

    from anygeometry.volume_cut import _edge_bounds

    return _edge_bounds(geometry, edge_id, volume)


# ----------------------------------------------------------------------
# face support and lineage proofs
# ----------------------------------------------------------------------
def _loop_edges(model: Any, face_id: int) -> List[int]:
    face = model.faces[face_id]
    return list(dict.fromkeys(int(use.edge) for loop in (face.loop,) + tuple(face.holes) for use in loop))


def _is_planar(model: Any, face_id: int) -> bool:
    """A plane, or the owner's exact planar-Coons predicate (as ``solid_submodel._face_kind``)."""

    from anygeometry.surfaces import Plane
    from anygeometry.volume_cut import _planar_coons

    surface = getattr(model.faces[face_id], "surface", None)
    if isinstance(surface, Plane):
        return True
    try:
        return bool(_planar_coons(model, face_id, surface))
    except Exception:  # noqa: BLE001 - not provably planar
        return False


def _fit_plane(points: np.ndarray) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    centre = points.mean(axis=0)
    _u, singular, vt = np.linalg.svd(points - centre, full_matrices=False)
    if len(singular) < 2 or not singular[1] > 1.0e-9 * max(float(singular[0]), 1.0e-300):
        return None  # collinear: no plane is determined
    return centre, vt[2]


def _plane_volume(centre: np.ndarray, normal: np.ndarray) -> Any:
    """A box volume whose local z axis is the plane normal (its bounds then give the distance from the plane)."""

    from anygeometry.volume_cut import BoxVolume

    n = normal / np.linalg.norm(normal)
    helper = np.eye(3)[int(np.argmin(np.abs(n)))]
    u = np.cross(helper, n)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    matrix = np.eye(4)
    matrix[:3, :3] = np.column_stack([u, v, n])
    matrix[:3, 3] = centre
    return BoxVolume(lower=(-1.0, -1.0, -1.0), upper=(1.0, 1.0, 1.0),
                     transform=tuple(tuple(float(c) for c in row) for row in matrix))


def _support_problem(before: Any, after: Any, face_id: int, tolerance: float) -> Optional[str]:
    """None when a kept face still stands on the same physical support, otherwise why not.

    A planar face may be regenerated as a different surface class (a plane becomes a
    topology-backed Coons patch when a vertex moves) so long as the owner's exact planar
    predicate holds and every boundary edge stays within ``tolerance`` of the original plane
    (conservative owner bounds, not sample points).  Cylinders keep axis, radius and
    position; any other family must keep its boundary unchanged.
    """

    from anygeometry.surfaces import Cylinder

    old, new = getattr(before.faces[face_id], "surface", None), getattr(after.faces[face_id], "surface", None)
    names = f"{type(old).__name__} to {type(new).__name__}"
    old_planar, new_planar = _is_planar(before, face_id), _is_planar(after, face_id)
    if old_planar or new_planar:
        if not (old_planar and new_planar):
            return f"changed its surface type from {names} and is no longer a planar support"
        points = np.vstack([_trace(before, e) for e in _loop_edges(before, face_id)])
        plane = _fit_plane(points)
        if plane is None:
            return "has a degenerate boundary, so its original plane cannot be established"
        frame = _plane_volume(*plane)
        for edge_id in _loop_edges(after, face_id):
            try:
                low, high, _tight = _owner_edge_bounds(after, edge_id, frame)
            except Exception as error:  # noqa: BLE001
                return f"has edge {edge_id} whose distance from the original plane cannot be bounded ({error})"
            off = max(abs(float(low[2])), abs(float(high[2])))
            if off > tolerance:
                return f"left its original plane by {off:.3e} at edge {edge_id}"
        return None
    if type(old) is not type(new):
        return f"changed its surface type from {names}"
    if isinstance(old, Cylinder):
        axis_a, axis_b = np.asarray(old.axis, dtype=float), np.asarray(new.axis, dtype=float)
        offset = np.asarray(new.origin, dtype=float) - np.asarray(old.origin, dtype=float)
        offset = offset - float(offset @ axis_a) * axis_a
        if (np.linalg.norm(np.cross(axis_a, axis_b)) > 1.0e-9 or abs(float(old.radius) - float(new.radius)) > tolerance
                or np.linalg.norm(offset) > tolerance):
            return "changed its cylinder axis, position or radius"
        return None
    if set(_loop_edges(before, face_id)) != set(_loop_edges(after, face_id)):
        return f"is a {type(old).__name__} whose boundary changed; only planes and cylinders can be edited"
    for edge_id in _loop_edges(before, face_id):
        if _same_edge(before, after, edge_id, tolerance) is not None:
            return f"is a {type(old).__name__} whose boundary changed; only planes and cylinders can be edited"
    return None


def _covered(before: Any, face_ids: Iterable[int], point: np.ndarray) -> bool:
    """The point lies in (or on the boundary of) the material of one of ``face_ids`` of ``before``."""

    from anygeometry.volume_cut import _material

    for face_id in face_ids:
        try:
            domain, tolerance = _material(before, face_id)
            uv = np.asarray(domain.support.local_uv(point), dtype=float)
            if np.linalg.norm(np.asarray(domain.support.evaluate(*uv)) - point) > tolerance:
                continue
            if (domain.contains_loop(domain.boundaries[0], uv, tolerance, boundary=True)
                    and not any(domain.contains_loop(loop, uv, tolerance, boundary=False)
                                for loop in domain.boundaries[1:])):
                return True
        except Exception:  # noqa: BLE001 - not provable for this face
            continue
    return False


def _lineage_problem(previous: Any, after: Any, face_id: int, lineage: Sequence[int]) -> Optional[str]:
    """None when owner-verified interior points of a new face lie in original faces of ``lineage``."""

    from anygeometry.volume_cut import _SCAN_SAMPLES, _material, _scan_candidates, _strictly_inside

    before = previous.child.geometry
    owners = [f for f in before.faces if tuple(previous.source_faces.get(f, ())) == tuple(lineage)]
    if not owners:
        return "no face of the original child carries that lineage"
    points: List[np.ndarray] = []
    try:
        domain, tolerance = _material(after, face_id)
        for count in _SCAN_SAMPLES:
            points = [p for p in _scan_candidates(after, face_id, count) if _strictly_inside(domain, p, tolerance)]
            if points:
                break
    except Exception as error:  # noqa: BLE001
        return f"its interior could not be sampled ({error})"
    if not points:
        return "no point could be verified inside it"
    for point in points[:8]:
        if not _covered(before, owners, point):
            return f"a verified interior point {np.round(point, 6).tolist()} lies outside the original faces {sorted(owners)}"
    return None


# ----------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------
def validate_child_edit(previous: Any, edited: Any) -> ChildEdit:
    """Check ``edited`` (a project) as a replacement child for ``previous`` (a definition).

    Raises :class:`SubmodelError` with a precise code for anything that would break
    the submodel's driving boundary or lineage; returns the replacement otherwise.
    Neither argument is modified.
    """

    if getattr(previous, "child_kind", "shell") != "shell":
        _refuse("CHILD_EDIT_KIND", "Only the child of a shell submodel is edited here.")
    if getattr(edited, "imperfections", None):
        _refuse("CHILD_IMPERFECTIONS", "Imperfections in a submodel child are not supported in v1.")
    if getattr(edited, "mesh_only", False) or getattr(edited, "imported_format", None):
        _refuse("CHILD_IMPORTED", "An imported or mesh-only child cannot drive a submodel.")
    before, after = previous.child.geometry, edited.geometry
    tolerance = _tolerance(before, after)

    # ---- the driving boundary: identity of every cut edge
    cut_edges = sorted({int(e) for edges in previous.cut_faces.values() for e in edges})
    for edge_id in cut_edges:
        if edge_id not in before.edges:  # the stored record itself is inconsistent
            _refuse("CHILD_CUT_RECORD", f"the stored cut edge {edge_id} is not in the original child")
        problem = _same_edge(before, after, edge_id, tolerance)
        if problem is not None:
            _refuse("CHILD_CUT_EDGE_CHANGED", f"Cut edge {edge_id} {problem}. The cut boundary drives the submodel "
                    "and cannot be edited; change only the interior.")

    # ---- every changed or new entity stays inside the original cut volume
    box = _Box(previous.volume)
    for vertex_id in after.vertices:
        unchanged = (vertex_id in before.vertices
                     and not _exceeds(np.linalg.norm(_vertex_position(before, vertex_id)
                                                     - _vertex_position(after, vertex_id)), tolerance))
        if not unchanged:
            excess = box.outside(_vertex_position(after, vertex_id), tolerance)
            if excess:
                _refuse("CHILD_OUTSIDE_CUT", f"Vertex {vertex_id} lies {excess:.3e} outside the original cut volume.")
    for edge_id in after.edges:
        if edge_id in before.edges and _same_edge(before, after, edge_id, tolerance) is None:
            continue
        try:
            excess, decided = box.edge_outside(after, edge_id, tolerance)
        except SubmodelError:
            raise
        except Exception as error:  # noqa: BLE001 - no usable enclosure: not provable
            _refuse("CHILD_CURVE_UNPROVEN", f"Edge {edge_id} has no usable enclosure to prove it inside the original "
                    f"cut volume ({error}).")
        if excess:
            _refuse("CHILD_OUTSIDE_CUT", f"Edge {edge_id} lies {excess:.3e} outside the original cut volume.")
        if not decided:
            _refuse("CHILD_CURVE_UNPROVEN", f"Edge {edge_id} is a curve whose conservative bounds leave the original "
                    "cut volume; it cannot be proved inside, so the edit is refused.")

    # ---- lineage: kept faces keep theirs, new faces must inherit an unambiguous one
    old_faces, new_faces = set(map(int, before.faces)), set(map(int, after.faces))
    kept, added, removed = old_faces & new_faces, new_faces - old_faces, old_faces - new_faces
    sources: Dict[int, Tuple[int, ...]] = {}
    for face_id in kept:
        found = previous.source_faces.get(face_id)
        if not found:
            _refuse("CHILD_LINEAGE_MISSING", f"Face {face_id} has no source lineage in the stored submodel.")
        sources[face_id] = tuple(found)
        problem = _support_problem(before, after, face_id, tolerance)
        if problem is not None:
            _refuse("CHILD_SURFACE_CHANGED", f"Face {face_id} {problem}.")
    old_edge_faces, new_edge_faces = _edge_faces(before), _edge_faces(after)
    face_edges: Dict[int, Set[int]] = {}
    for edge_id, users in new_edge_faces.items():
        for face_id in users:
            face_edges.setdefault(face_id, set()).add(edge_id)

    mapped: Dict[int, Tuple[int, ...]] = {}
    pending = set(added)
    while pending:
        progressed = False
        for face_id in sorted(pending):
            via_removed: Set[Tuple[int, ...]] = set()
            via_new_edges: Set[Tuple[int, ...]] = set()
            unresolved_neighbour = False
            for edge_id in face_edges.get(face_id, ()):
                if edge_id in old_edge_faces:
                    via_removed.update(tuple(previous.source_faces[f]) for f in old_edge_faces[edge_id]
                                       if f in removed and f in previous.source_faces)
                    continue
                for other in new_edge_faces[edge_id]:
                    if other == face_id:
                        continue
                    if other in sources:
                        via_new_edges.add(sources[other])
                    else:
                        unresolved_neighbour = True
            choice = via_removed or via_new_edges
            if len(choice) > 1:
                _refuse("CHILD_LINEAGE_AMBIGUOUS", f"New face {face_id} descends from faces with different source "
                        f"lineage {sorted(choice)}; it cannot be mapped to the parent.")
            if len(choice) == 1 and not (not via_removed and unresolved_neighbour):
                sources[face_id] = mapped[face_id] = next(iter(choice))
                pending.discard(face_id)
                progressed = True
        if not progressed:
            break
    if pending:
        _refuse("CHILD_LINEAGE_UNRECOVERED", f"New face(s) {sorted(pending)} could not be traced to a parent face. "
                "Edit existing faces (a hole or detail inside one face) so the lineage stays unambiguous.")
    # adjacency only proposes a lineage; the new face must also lie physically in the faces that carry it
    for face_id, lineage in sorted(mapped.items()):
        problem = _lineage_problem(previous, after, face_id, lineage)
        if problem is not None:
            _refuse("CHILD_LINEAGE_UNPROVEN", f"New face {face_id} is next to faces of source lineage {list(lineage)} "
                    f"but {problem}; it is not accepted as their descendant.")

    # ---- ownership/incidence of the cut edges, expressed through lineage
    for edge_id in cut_edges:
        expected = sorted(tuple(previous.source_faces.get(f, ())) for f in old_edge_faces[edge_id])
        actual = sorted(sources[f] for f in new_edge_faces[edge_id])
        if expected != actual:
            _refuse("CHILD_CUT_OWNERSHIP", f"Cut edge {edge_id} no longer belongs to the faces it was cut from "
                    f"(source lineage {expected} became {actual}).")

    notes: List[str] = []
    if mapped:
        notes.append(f"{len(mapped)} new face(s) inherited their parent faces: "
                     + ", ".join(f"{f}->{list(s)}" for f, s in sorted(mapped.items())))
    if removed:
        notes.append(f"{len(removed)} face(s) removed: {sorted(removed)}")

    # ---- the replacement: a clean detached child, same lineage plus recovered faces
    clean = _clean_child(edited)
    try:
        clean.resolve_section_assignments(strict=True)
    except Exception as error:  # noqa: BLE001 - the project reports which face lacks a section
        _refuse("CHILD_SECTION_UNRESOLVED", f"A child face has no plate section: {error}")
    # strict resolution alone does not guarantee a plate section on every retained face
    lacking = sorted(int(f) for f in clean.geometry.faces
                     if clean.face_sections.get(f) not in clean.plate_sections)
    if lacking:
        _refuse("CHILD_SECTION_UNRESOLVED", f"Child face(s) {lacking} have no plate section.")
    _check_cut_sides(previous, box, tolerance)
    # the stored target size follows the child's (it may be edited); an unusable value is refused
    target_size = previous.target_size
    if clean.target_size is not None:
        target_size = _positive_finite(clean.target_size, "the child target size")
    replacement = replace(
        previous, child=clean, id=previous.id, target_size=target_size,
        source_faces={f: tuple(s) for f, s in sorted(sources.items())},
        cut_faces={k: tuple(v) for k, v in previous.cut_faces.items()},
    )
    try:
        replacement.validate()
    except SubmodelRecordError as error:
        _refuse("CHILD_DEFINITION", str(error))
    changed = definition_fingerprint(replacement) != definition_fingerprint(previous)
    return ChildEdit(replacement, changed, dict(mapped), tuple(sorted(removed)), tuple(notes))


def _clean_child(edited: Any) -> Any:
    """A serialization copy of ``edited`` without solve records a detached child must not carry."""

    from .io.project_file import project_from_dict, project_to_dict

    from .mesh_numbering import MeshNumbering

    clean = project_from_dict(project_to_dict(edited))
    for name in _RUN_REGISTRIES:
        registry = getattr(clean, name, None)
        if registry and hasattr(registry, "clear"):
            registry.clear()
    # numbering names a published mesh record; with the records gone it must go too
    clean.mesh_numbering = MeshNumbering()
    return clean


def _positive_finite(value: Any, what: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float("nan")
    if isinstance(value, bool) or not np.isfinite(number) or number <= 0.0:
        _refuse("CHILD_TARGET_SIZE", f"{what} must be a positive finite number, not {value!r}.")
    return number


# canonical box side -> (axis, 0 for the lower face / 1 for the upper face)
_SIDES = {"x0": (0, 0), "x1": (0, 1), "y0": (1, 0), "y1": (1, 1), "z0": (2, 0), "z1": (2, 1)}


def _check_cut_sides(previous: Any, box: "_Box", tolerance: float) -> None:
    """Every canonical cut side lies on its box face; other (custom) group names are kept as they are.

    Edge bounds come from the owner (box frame).  A cut edge that cannot be bounded, or whose
    bounds are not finite, refuses rather than passing quietly.
    """

    geometry = previous.child.geometry
    for name, edges in previous.cut_faces.items():
        if name not in _SIDES:
            continue
        axis, upper = _SIDES[name]
        plane = float((box.upper if upper else box.lower)[axis])
        for edge_id in edges:
            try:
                low, high, _tight = _owner_edge_bounds(geometry, int(edge_id), box.volume)
                low, high = np.asarray(low, dtype=float).reshape(3), np.asarray(high, dtype=float).reshape(3)
            except Exception as error:  # noqa: BLE001 - not provable
                _refuse("CHILD_CUT_SIDE", f"cut side {name!r} edge {edge_id} cannot be bounded in the cut box ({error}).")
            if not (np.isfinite(low).all() and np.isfinite(high).all()):
                _refuse("CHILD_CUT_SIDE", f"cut side {name!r} edge {edge_id} has non-finite bounds.")
            slack = max(tolerance, 1.0e-9)
            if _exceeds(max(abs(float(low[axis]) - plane), abs(float(high[axis]) - plane)), slack):
                # loose conservative bounds of a curve: decide from its samples instead
                local = np.asarray(box.volume.local(_trace(geometry, int(edge_id))), dtype=float).reshape(-1, 3)
                if not _exceeds(float(np.max(np.abs(local[:, axis] - plane))), slack):
                    continue
                _refuse("CHILD_CUT_SIDE", f"cut edge {edge_id} of side {name!r} does not lie on the box plane "
                        f"{name[0]}={plane:g}.")


def build_replacement(previous: Any, edited: Any) -> SubmodelDefinition:
    """The validated replacement definition (same id, source, cut and policy)."""

    return validate_child_edit(previous, edited).definition


# ----------------------------------------------------------------------
# application
# ----------------------------------------------------------------------
def apply_child_edit(session: Any, definition_id: str, edited: Any, *, base_fingerprint: Optional[str] = None) -> ChildEdit:
    """Validate ``edited`` and replace the stored child in one undoable session command.

    ``base_fingerprint`` is the :func:`definition_fingerprint` the edit started from; a
    different stored definition is a conflict and nothing is changed.  The parent model,
    mesh, solved jobs and stored runs are not touched (the command is not solver affecting).
    """

    from .submodel_design import ReplaceSubmodelDefinition

    if getattr(session, "read_only", False):
        _refuse("CHILD_READ_ONLY", "The document is read-only.")
    current = session.project.submodels.get(definition_id)
    if current is None:
        _refuse("CHILD_DEFINITION_GONE", "The submodel being edited is no longer in the project.")
    if base_fingerprint is not None and definition_fingerprint(current) != base_fingerprint:
        _refuse("SUBMODEL_EDIT_CONFLICT", "The submodel changed since the editor was opened. Reopen it before applying.")
    outcome = validate_child_edit(current, edited)
    if not outcome.changed:
        return outcome
    # revalidate immediately before the command: nothing may have replaced the definition meanwhile
    latest = session.project.submodels.get(definition_id)
    if latest is not current or (base_fingerprint is not None
                                 and definition_fingerprint(latest) != base_fingerprint):
        _refuse("SUBMODEL_EDIT_CONFLICT", "The submodel changed while the edit was validated. Reopen it before applying.")
    session.execute(ReplaceSubmodelDefinition(current, outcome.definition), solver_affecting=False)
    installed = session.project.submodels[definition_id]
    return ChildEdit(installed, True, outcome.mapped_faces, outcome.removed_faces, outcome.notes)
