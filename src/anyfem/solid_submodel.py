"""Shell-driven solid submodels: cut a solid detail out of a planar shell face and stage its run.

* :func:`cut_solid_submodel` validates an interior rectangular footprint on one
  planar, single-isotropic-section shell face and returns a
  :class:`~anyfem.model.solid_submodels.SolidSubmodelDefinition`.  Nothing that
  intersects the detail is dropped silently: supported uniform pressure on the
  face is retained on the explicitly named skin; any other support, load, beam,
  mass, wet surface, connection or refinement that reaches the detail refuses.
  Remote entities are neither carried nor refused.
* :func:`stage_solid_submodel_run` re-verifies the source (geometry identity,
  exact face, section, material, loads and lineage, and the solved shell
  elements' own director and pressure), derives the detail box, meshes it
  (structured Tet4), prepares the solid, solves it driven by the certified shell
  field, verifies the solved result, writes the mesh and result artifacts and
  returns the same :class:`~anyfem.submodel_jobs.StagedRun` as the shell route.
  It registers nothing; publication stays in ``publish_submodel_run``.

The source passed to staging is already ``job_source`` + ``bind_source`` certified.

Reach tests (is an attribute record near the detail?)
-----------------------------------------------------
The detail is an oriented :class:`anygeometry.volume_cut.BoxVolume`.  An entity
is *separated* only when the owner's conservative bounds
(``volume_cut._edge_bounds``; a face's extent is that of its boundary, valid on
planes, planar Coons patches and cylinders) do not reach the box enlarged by the
tolerance.  Where the bounds do reach, only exact tests decide: a point against
the box, a straight segment against the box (slab clipping), a curved edge by
sampled points on the curve that lie in the box (proof of reach), and a planar
face by its boundary edges plus the box/plane section against the face's
material.  Anything else that overlaps (a curved edge that cannot be shown to
hit, a cylinder or non-planar face whose extent overlaps, a geometry error)
refuses with a typed code; it is never reported as "does not reach".

Pressure
--------
ANYfem's pressure is ``+p * n`` with ``n`` the signed element normal of the solved
shell element (``solve.build._element_centroid_normal``).  Staging reads that
normal and the pressure actually assembled on every element of the source face,
requires them to be uniform and to match the definition, and loads whichever skin
was named with the same global traction ``p * n``.  The skin chooses *where* the
traction acts, not its direction.

Known limits
------------
* A curved edge whose bounds overlap the box but that has no sampled point in
  it refuses (``CUT_CURVE_UNRESOLVED``) instead of being proved separated.
* A cylinder or non-planar face whose extent overlaps the box, and any face
  whose surface is neither plane, planar Coons patch nor cylinder, refuses
  (``CUT_SURFACE_UNRESOLVED``).
* The shell reference surface has no offset model: the detail is centred on the
  reference plane and ``reference_offset`` is always 0.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from uuid import uuid4

import numpy as np

from .model.solid_submodels import SKINS, SolidSubmodelDefinition, solid_definition_hash
from .model.submodels import SourceReference, SubmodelRecordError, SubmodelRun, descriptor, json_safe
from .submodel import SubmodelError

__all__ = [
    "CutSolidReport",
    "CutSolidSubmodel",
    "SolidSubmodelResult",
    "cut_solid_submodel",
    "stage_solid_submodel_run",
]

_CURVE_SAMPLES = 257


# ----------------------------------------------------------------------
# small utilities
# ----------------------------------------------------------------------
def _refuse(code: str, message: str) -> None:
    raise SubmodelError(code, message)


class _Cancel:
    """One cancellation view usable as callable, ``is_set()`` object or token."""

    def __init__(self, token: Any) -> None:
        self.token = token

    def _flag(self) -> bool:
        token = self.token
        if token is None:
            return False
        for name in ("is_set", "is_cancelled", "cancelled"):
            value = getattr(token, name, None)
            if value is not None:
                return bool(value() if callable(value) else value)
        return False

    def __call__(self) -> bool:
        return self._flag()

    def is_set(self) -> bool:
        return self._flag()

    @property
    def cancelled(self) -> bool:
        return self._flag()


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return set(a) == set(b) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=1e-15)
        except (TypeError, ValueError):
            return False
    return a == b


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(v) for v in value]
    return value


# ----------------------------------------------------------------------
# the detail box and the reach tests
# ----------------------------------------------------------------------
def _detail_volume(points: np.ndarray, normal: np.ndarray, thickness: float, tolerance: float) -> Tuple[Any, float]:
    """``(BoxVolume, margin)``: the footprint extruded ``+-thickness/2`` about the reference plane.

    The box frame is orthonormalised from the footprint (the record allows a
    relative skew of 1e-6) and ``margin`` (>= the geometry tolerance) covers that
    skew, so every test against the enlarged box is conservative.
    """

    from anygeometry.volume_cut import BoxVolume, VolumeCutError

    a, b = points[1] - points[0], points[3] - points[0]
    length_u = float(np.linalg.norm(a))
    if not length_u > 0.0:
        _refuse("CUT_FOOTPRINT", "the footprint has a degenerate side")
    u = a / length_u
    v = b - float(b @ u) * u
    length_v = float(np.linalg.norm(v))
    if not length_v > 0.0:
        _refuse("CUT_FOOTPRINT", "the footprint has a degenerate side")
    v = v / length_v
    w = np.cross(u, v)
    if abs(float(w @ normal)) < 1.0 - 1.0e-6:
        _refuse("CUT_FOOTPRINT", "the footprint is not parallel to the face's plane")
    transform = np.eye(4)
    transform[:3, :3] = np.column_stack([u, v, w])
    transform[:3, 3] = points[0]
    margin = max(float(tolerance), 4.0e-6 * max(length_u, length_v))
    try:
        volume = BoxVolume(lower=(0.0, 0.0, -thickness / 2.0), upper=(length_u, length_v, thickness / 2.0),
                           transform=tuple(tuple(float(c) for c in row) for row in transform))
    except VolumeCutError as error:
        raise SubmodelError("CUT_FOOTPRINT", str(error)) from error
    return volume, margin


@dataclass(frozen=True)
class _Reach:
    geometry: Any
    volume: Any
    margin: float
    face_id: int


def _owner_edge_bounds(geometry: Any, edge_id: int, volume: Any) -> Tuple[np.ndarray, np.ndarray, bool]:
    """The owner's conservative edge bounds in the box frame (a seam, so tests can substitute it)."""

    from anygeometry.volume_cut import _edge_bounds

    return _edge_bounds(geometry, edge_id, volume)


def _is_straight(geometry: Any, edge_id: int) -> bool:
    from anygeometry.curves import Straight

    return isinstance(geometry.edges[edge_id].curve, Straight)


def _face_kind(geometry: Any, face_id: int) -> str:
    """``'planar'``, ``'cylinder'`` or ``'other'``: only the first two are bounded by their boundary extent."""

    from anygeometry.surfaces import Cylinder, Plane
    from anygeometry.volume_cut import _planar_coons

    surface = getattr(geometry.faces[face_id], "surface", None)
    if isinstance(surface, Plane) or _planar_coons(geometry, face_id, surface):
        return "planar"
    return "cylinder" if isinstance(surface, Cylinder) else "other"


def _finite(value: Any, shape: Tuple[int, ...], what: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape[array.ndim - len(shape):] != shape or not np.isfinite(array).all():
        raise ValueError(f"{what} is not a finite array of trailing shape {shape}")
    return array


def _segment_meets(volume: Any, a: np.ndarray, b: np.ndarray, margin: float) -> bool:
    """Exact: the closed box enlarged by ``margin`` meets the segment ``a``-``b`` (slab clipping)."""

    from .submodel_jobs import _slab

    return _slab(volume, a, b, margin) is not None


def _edge_reaches(ctx: _Reach, edge_id: int) -> bool:
    geometry, volume, margin = ctx.geometry, ctx.volume, ctx.margin
    low, high, _tight = _owner_edge_bounds(geometry, edge_id, volume)
    low, high = _finite(low, (3,), f"edge {edge_id} bounds"), _finite(high, (3,), f"edge {edge_id} bounds")
    if not volume.reaches((low, high), margin):
        return False  # proved separated by the owner's conservative bounds
    if _is_straight(geometry, edge_id):
        edge = geometry.edges[edge_id]
        start = _finite(geometry.vertex_position(edge.start), (3,), f"edge {edge_id} start")
        end = _finite(geometry.vertex_position(edge.end), (3,), f"edge {edge_id} end")
        return _segment_meets(volume, start, end, margin)
    samples = _finite(geometry.sample_edge(edge_id, np.linspace(0.0, 1.0, _CURVE_SAMPLES)), (3,),
                      f"edge {edge_id} samples")
    local = volume.local(samples.reshape(-1, 3))
    if bool(np.any(np.all((local >= np.asarray(volume.lower) - margin) & (local <= np.asarray(volume.upper) + margin),
                          axis=1))):
        return True  # a point on the curve lies in the box: it reaches
    _refuse("CUT_CURVE_UNRESOLVED",
            f"curved edge {edge_id} has bounds that overlap the detail but no sampled point inside it; it cannot be "
            "proved separated, so the record on it is refused")
    return True  # unreachable


def _vertex_reaches(ctx: _Reach, vertex_id: int) -> bool:
    from anygeometry.volume_cut import OUTSIDE

    point = _finite(ctx.geometry.vertex_position(vertex_id), (3,), f"vertex {vertex_id}")
    return ctx.volume.classify(point.reshape(3), ctx.margin) != OUTSIDE


def _face_reaches(ctx: _Reach, face_id: int) -> bool:
    if face_id == ctx.face_id:
        return True
    geometry, volume, margin = ctx.geometry, ctx.volume, ctx.margin
    kind = _face_kind(geometry, face_id)
    if kind == "other":
        _refuse("CUT_SURFACE_UNRESOLVED",
                f"face {face_id} is not a plane, planar Coons patch or cylinder: its boundary does not bound it, so it "
                "cannot be proved separated from the detail")
    face = geometry.faces[face_id]
    edge_ids = list(dict.fromkeys(use.edge for loop in (face.loop,) + tuple(face.holes) for use in loop))
    if not edge_ids:
        raise ValueError(f"face {face_id} has no boundary")
    bounds = [_owner_edge_bounds(geometry, e, volume) for e in edge_ids]
    low = np.min([_finite(b[0], (3,), "edge bounds") for b in bounds], axis=0)
    high = np.max([_finite(b[1], (3,), "edge bounds") for b in bounds], axis=0)
    if not volume.reaches((low, high), margin):
        return False
    if any(_edge_reaches(ctx, e) for e in edge_ids):
        return True
    if kind != "planar":
        _refuse("CUT_SURFACE_UNRESOLVED",
                f"cylindrical face {face_id} has an extent that overlaps the detail and no exact face/box test exists")
    return _plane_section_in_face(ctx, face_id, low, high)


def _plane_section_in_face(ctx: _Reach, face_id: int, low: np.ndarray, high: np.ndarray) -> bool:
    """No boundary edge meets the box: it meets the face only if the box/plane section lies in the material.

    The section is connected and misses every boundary curve, so it lies wholly in
    the face or wholly outside it; any one of its points decides.
    """

    from anygeometry.volume_cut import _material, _strictly_inside

    geometry, volume, margin = ctx.geometry, ctx.volume, ctx.margin
    lower, upper = np.asarray(volume.lower, dtype=float), np.asarray(volume.upper, dtype=float)
    centre = volume.world(0.5 * (lower + upper))[0]
    projected, uv, _distance = geometry.project_to_face(face_id, centre)
    projected = _finite(projected, (3,), "projected point").reshape(3)
    normal = _finite(geometry.face_normal(face_id, *uv), (3,), "face normal").reshape(3)
    normal = normal / float(np.linalg.norm(normal))
    corners_local = np.array([[(lower, upper)[i][0], (lower, upper)[j][1], (lower, upper)[k][2]]
                              for i in (0, 1) for j in (0, 1) for k in (0, 1)])
    corners = volume.world(corners_local)
    signed = (corners - projected) @ normal
    if float(signed.min()) > margin or float(signed.max()) < -margin:
        return False  # the box misses the face's plane
    candidates = [corners[i] - signed[i] * normal for i in range(8) if abs(float(signed[i])) <= margin]
    for i in range(8):
        for bit in (1, 2, 4):
            j = i ^ bit
            if i < j and signed[i] * signed[j] < 0.0:
                t = signed[i] / (signed[i] - signed[j])
                candidates.append(corners[i] + t * (corners[j] - corners[i]))
    if not candidates:
        raise ValueError("no point of the box lies in the face's plane")
    domain, tolerance = _material(geometry, face_id)
    return any(_strictly_inside(domain, np.asarray(p, dtype=float), tolerance) for p in candidates)


def _entity_reaches(ctx: _Reach, kind: str, identifier: int) -> bool:
    """Whether geometry entity ``(kind, identifier)`` may intersect the detail; refuses when that is unresolved."""

    from anygeometry.errors import GeometryError

    try:
        if kind == "vertex":
            return _vertex_reaches(ctx, identifier)
        if kind == "edge":
            return _edge_reaches(ctx, identifier)
        if kind == "face":
            return _face_reaches(ctx, identifier)
    except SubmodelError:
        raise
    except (GeometryError, KeyError, IndexError, ValueError, TypeError, AttributeError, ArithmeticError) as error:
        raise SubmodelError("CUT_GEOMETRY_UNRESOLVED",
                            f"{kind} {identifier} could not be tested against the detail: "
                            f"{type(error).__name__}: {error}") from error
    raise SubmodelError("CUT_REGION_UNSUPPORTED", f"a {kind} target is not supported")


def _reach(parent: Any, ctx: _Reach, region: Any, ref: Any, what: str) -> Tuple[bool, List[Any]]:
    """``(names_source_face, other_targets_that_reach)`` of an attribute record."""

    from .submodel_cut import _targets

    names_face, others = False, []
    for target in _targets(parent, region, ref, what):
        if target.kind == "face" and int(target.id) == ctx.face_id:
            names_face = True
        elif _entity_reaches(ctx, target.kind, int(target.id)):
            others.append(target)
    return names_face, others


# ----------------------------------------------------------------------
# the one analysis shared by the cut and by every staged run
# ----------------------------------------------------------------------
@dataclass
class _Analysis:
    geometry: Dict[str, Any]
    reconstruction: Dict[str, Any]
    carried: Dict[str, int]
    ignored_remote: Dict[str, int]
    notes: List[str]
    detail: Any
    region_id: str
    normal: np.ndarray
    plane_origin: np.ndarray
    footprint: np.ndarray
    thickness: float
    young: float
    poisson: float
    pressure: Optional[float]
    volume: Any
    margin: float


def _geometry_identity(geometry: Any) -> Dict[str, Any]:
    from anygeometry.serialization import to_dict

    return {"model_id": str(geometry.model_id), "revision": int(geometry.revision),
            "checksum": str(to_dict(geometry)["checksum"]["value"])}


def _section_and_material(parent: Any, face_id: int) -> Tuple[Any, Any, float, float]:
    parent.resolve_section_assignments(strict=False)
    name = parent.face_sections.get(face_id)
    if name is None:
        _refuse("CUT_SECTION_UNRESOLVED", f"face {face_id} has no plate section")
    section = parent.plate_sections.get(name)
    if section is None:
        section = next((s for s in parent.plate_sections.values() if s.name == name or s.id == name), None)
    if section is None:
        _refuse("CUT_SECTION_UNRESOLVED", f"plate section {name!r} of face {face_id} does not exist")
    material = parent.materials.get(section.material)
    if material is None:
        _refuse("CUT_MATERIAL_UNRESOLVED", f"material {section.material!r} of section {section.name!r} does not exist")
    # ``MaterialSpec`` carries ``symmetry`` and ``constants``; the solved shell was built from ``material.build()``
    # of this same spec, so these are the E and nu the shell field was solved with.
    if getattr(material, "symmetry", None) != "isotropic":
        _refuse("CUT_MATERIAL_UNSUPPORTED", f"material {section.material!r} is not isotropic")
    try:
        constants = material.constants
        young, poisson = float(constants["elastic_modulus"]), float(constants["poisson_ratio"])
    except (AttributeError, KeyError, TypeError, ValueError):
        _refuse("CUT_MATERIAL_UNSUPPORTED", f"material {section.material!r} has no isotropic elastic_modulus / "
                                            "poisson_ratio constants")
    if not (math.isfinite(young) and young > 0.0 and math.isfinite(poisson) and 0.0 <= poisson <= 0.45):
        _refuse("CUT_MATERIAL_UNSUPPORTED", "the solid needs finite E > 0 and 0 <= nu <= 0.45")
    return section, material, young, poisson


def _analyse(parent: Any, source: SourceReference, face_id: int, footprint: Any, case_name: str,
             pressure_skin: Optional[str]) -> _Analysis:
    from anygeometry.errors import GeometryError
    from anygeometry.shell_detail import ShellDetailError, derive_shell_detail_box

    if parent.mesh_only or parent.imported_format:
        _refuse("CUT_PARENT_UNSUPPORTED", "imported or mesh-only parents cannot be cut")
    if parent.imperfections:
        _refuse("CUT_IMPERFECTIONS_UNSUPPORTED", "parents with imperfections cannot be cut in v1")
    if source.job_id not in parent.jobs:
        _refuse("CUT_SOURCE_JOB_MISSING", f"the source job {source.job_id!r} is not in the parent project")
    geometry = parent.geometry
    if face_id not in geometry.faces:
        _refuse("CUT_FACE_MISSING", f"the parent has no face {face_id}")
    points = np.asarray(footprint, dtype=float)
    if points.shape != (4, 3) or not np.isfinite(points).all():
        _refuse("CUT_FOOTPRINT", "the footprint must be four finite xyz points")
    section, material, young, poisson = _section_and_material(parent, face_id)
    thickness = float(section.thickness)
    try:
        _projected, uv, _distance = geometry.project_to_face(face_id, points.mean(axis=0))
        normal = np.asarray(geometry.face_normal(face_id, *uv), dtype=float)
    except GeometryError as error:
        raise SubmodelError("CUT_FACE_NORMAL", f"face {face_id} has no usable normal: {error}") from error
    normal = normal / float(np.linalg.norm(normal))
    try:
        detail, region_id = derive_shell_detail_box(geometry, face_id, points, director=normal, thickness=thickness,
                                                    reference_offset=0.0)
    except ShellDetailError as error:
        raise SubmodelError(error.code, str(error)) from error
    diagonal = float(np.linalg.norm(np.ptp(points, axis=0))) + thickness
    tolerance = float(geometry.tolerance.effective_length(diagonal))
    # The plane origin is a projected, proved point of the face (not the surface parameter origin): every corner must
    # project onto the face within the tolerance, and the plane passes through the projected centre.
    try:
        for corner in points:
            _p, _uv, distance = geometry.project_to_face(face_id, corner)
            if not float(distance) <= tolerance:
                _refuse("CUT_FOOTPRINT", f"a footprint corner is {float(distance):.3e} from face {face_id} "
                                         f"(tolerance {tolerance:.3e}): it is not on the reference plane")
        projected_centre, _uv, _distance = geometry.project_to_face(face_id, points.mean(axis=0))
        plane_origin = np.asarray(projected_centre, dtype=float).reshape(3)
    except GeometryError as error:
        raise SubmodelError("CUT_FOOTPRINT", f"the footprint does not project onto face {face_id}: {error}") from error
    if not np.isfinite(plane_origin).all():
        _refuse("CUT_FOOTPRINT", "the projected plane origin is not finite")
    volume, margin = _detail_volume(points, normal, thickness, tolerance)
    ctx = _Reach(geometry, volume, margin, int(face_id))

    case = parent.load_cases.get(case_name)
    if case is None:
        _refuse("CUT_CASE_MISSING", f"the parent has no load case {case_name!r}")
    if case.parent is not None or case.times is not None or case.series is not None:
        _refuse("CUT_LOAD_UNSUPPORTED", f"load case {case_name!r} belongs to a load series")
    carried: Dict[str, int] = {"plate_sections": 1}
    ignored: Dict[str, int] = {}

    def tally(table: Dict[str, int], kind: str) -> None:
        table[kind] = table.get(kind, 0) + 1

    def refuse_reaching(kind: str, items: Iterable[Any], what: str, code: str) -> None:
        for item in items:
            label = f"{what} {getattr(item, 'name', '')!r}"
            names_face, others = _reach(parent, ctx, getattr(item, "region", None), getattr(item, "ref", None), label)
            if names_face or others:
                _refuse(code, f"{label} reaches the detail (not carried by a solid submodel)")
            tally(ignored, kind)

    refuse_reaching("supports", parent.supports, "support", "CUT_SUPPORT_UNSUPPORTED")
    refuse_reaching("masses", parent.masses, "mass", "CUT_MASS_UNSUPPORTED")
    refuse_reaching("wet_surfaces", parent.wet_surfaces, "wet surface", "CUT_WET_UNSUPPORTED")
    refuse_reaching("connections", parent.connections, "connection", "CUT_CONNECTIONS_UNSUPPORTED")
    for refinement in parent.refinements:  # Refinement: exactly one of ref / center, plus radius
        if refinement.ref is not None:
            names_face, others = _reach(parent, ctx, None, refinement.ref, f"refinement {refinement.name!r}")
            reaches = names_face or bool(others)
        else:
            centre = volume.local(np.asarray(refinement.center, dtype=float))[0]
            gap = np.maximum(np.maximum(np.asarray(volume.lower) - centre, centre - np.asarray(volume.upper)), 0.0)
            reaches = float(np.linalg.norm(gap)) <= float(refinement.radius) + margin
        if reaches:
            _refuse("CUT_REFINEMENT_UNSUPPORTED", f"refinement {refinement.name!r} reaches the detail")
        tally(ignored, "refinements")
    for edge_id in sorted(parent.edge_sections):
        if edge_id not in geometry.edges:
            _refuse("CUT_GEOMETRY_UNRESOLVED", f"beam section assigned to edge {edge_id}, which the geometry lacks")
        if _entity_reaches(ctx, "edge", int(edge_id)):
            _refuse("CUT_BEAMS_UNSUPPORTED", f"beam edge {edge_id} reaches the detail")
        tally(ignored, "beam_assignments")

    # loads of the selected case (real record fields: WetPressure has surfaces / history, ConnectionLoad a
    # connection id; only pressures, surface tractions, line loads and point loads have ref / region)
    wet_surfaces = {surface.id: surface for surface in parent.wet_surfaces}
    for wet in case.wet_pressures:
        if wet.history is not None:
            _refuse("CUT_LOAD_UNSUPPORTED", f"wet pressure {wet.name!r} carries a pressure history that may include "
                                            "weight and inertia on the detail")
        unknown = [i for i in wet.surfaces if i not in wet_surfaces]
        if unknown:
            _refuse("CUT_LOAD_UNSUPPORTED", f"wet pressure {wet.name!r} names unknown wet surfaces {unknown}")
        for surface in (wet_surfaces[i] for i in wet.surfaces) if wet.surfaces else wet_surfaces.values():
            names_face, others = _reach(parent, ctx, surface.region, surface.ref, f"wet surface {surface.name!r}")
            if names_face or others:
                _refuse("CUT_LOAD_UNSUPPORTED", f"wet pressure {wet.name!r} acts on a surface that reaches the detail")
        tally(ignored, "wet_pressures")
    connections = {connection.id: connection for connection in parent.connections}
    for connection_load in case.connection_loads:
        connection = connections.get(connection_load.connection)
        if connection is None:
            _refuse("CUT_LOAD_UNSUPPORTED", f"connection load {connection_load.name!r} names the unknown connection "
                                            f"{connection_load.connection!r}")
        names_face, others = _reach(parent, ctx, connection.region, connection.ref, f"connection {connection.name!r}")
        if names_face or others:
            _refuse("CUT_LOAD_UNSUPPORTED", f"connection load {connection_load.name!r} acts through a connection "
                                            "that reaches the detail")
        tally(ignored, "connection_loads")
    if case.gravity is not None and bool(np.any(np.asarray(case.gravity, dtype=float) != 0.0)):
        _refuse("CUT_LOAD_UNSUPPORTED", "gravity is a body load on the detail (not carried)")
    for load in (*case.surface_tractions, *case.line_loads, *case.point_loads):
        kind = type(load).__name__
        names_face, others = _reach(parent, ctx, load.region, load.ref, kind)
        if names_face or others:
            _refuse("CUT_LOAD_UNSUPPORTED", f"a {kind} reaches the detail (only uniform face pressure is carried)")
        tally(ignored, "loads")
    total: Optional[float] = None
    for load in case.pressures:
        names_face, others = _reach(parent, ctx, load.region, load.ref, f"pressure in {case_name!r}")
        if others:
            _refuse("CUT_LOAD_UNSUPPORTED", "a pressure on another face reaches the detail")
        if not names_face:
            tally(ignored, "pressures")
            continue
        if load.envelope is not None:
            _refuse("CUT_LOAD_UNSUPPORTED", "a pressure with a time envelope is not carried")
        value = float(load.value)
        if not math.isfinite(value):
            _refuse("CUT_LOAD_UNSUPPORTED", "a pressure value is not finite")
        total = value if total is None else total + value
        tally(carried, "pressures")
    if total is not None and case.follower_pressure:
        _refuse("CUT_LOAD_UNSUPPORTED", "follower pressure is not carried")
    if total is not None and total != 0.0 and pressure_skin is None:
        _refuse("CUT_PRESSURE_SKIN", "the face carries a pressure that would be dropped: name pressure_skin "
                                     f"{SKINS} to retain it")
    if pressure_skin is not None and total is None:
        _refuse("CUT_PRESSURE_SKIN", f"pressure_skin {pressure_skin!r} is named but the face carries no pressure "
                                     f"in load case {case_name!r}")
    pressure = None if pressure_skin is None else float(total)

    reconstruction = {
        "director": normal.tolist(), "thickness": thickness, "reference_offset": 0.0,
        "section": {"id": str(section.id), "name": str(section.name)},
        "material": {"name": str(section.material), "elastic_modulus": young, "poisson_ratio": poisson},
        "pressure": {"value": pressure},
        "boundary_tolerance": tolerance,
    }
    return _Analysis(_geometry_identity(geometry), reconstruction, carried, ignored, [], detail, region_id, normal,
                     plane_origin, points, thickness, young, poisson, pressure, volume, margin)


# ----------------------------------------------------------------------
# cut
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class CutSolidReport:
    """What the cut checked and carried; evidence, not persisted."""

    carried: Mapping[str, int]
    ignored_remote: Mapping[str, int]  # remote records, neither carried nor refused
    notes: Tuple[str, ...]
    region_id: str
    boundary_tolerance: float
    detail: Any  # anygeometry GeometryModel of the detail box (in memory only)


@dataclass(frozen=True)
class CutSolidSubmodel:
    definition: SolidSubmodelDefinition
    report: CutSolidReport


def cut_solid_submodel(snapshot: Any, *, source: SourceReference, face_id: int, footprint: Any, target_size: float,
                       layers: int, order: int = 1, pressure_skin: Optional[str] = None,
                       name: str = "solid submodel") -> CutSolidSubmodel:
    """Define a solid detail on ``face_id`` of the parent in ``snapshot`` (not registered).

    ``footprint``: four ordered xyz points on the shell reference plane, strictly
    inside the material of the face.  ``pressure_skin`` (``'start'`` or ``'end'``)
    names the skin that carries the face's uniform pressure; it is required when the
    face is loaded and refused when it is not.  Both skins carry the same ``+p * n``
    traction; the choice is the physical location of the load, not its direction.
    """

    snapshot = getattr(snapshot, "snapshot", snapshot)
    if isinstance(face_id, bool) or not isinstance(face_id, int):
        _refuse("CUT_FACE_MISSING", "face_id must be an integer face id")
    if pressure_skin is not None and pressure_skin not in SKINS:
        _refuse("CUT_PRESSURE_SKIN", f"pressure_skin must be one of {SKINS} or None")
    parent = snapshot.thaw()
    analysis = _analyse(parent, source, face_id, footprint, source.case, pressure_skin)
    try:
        definition = SolidSubmodelDefinition(
            name=name, source=source, face_id=int(face_id),
            footprint=tuple(tuple(float(c) for c in p) for p in analysis.footprint.tolist()),
            target_size=target_size, layers=layers, order=order, pressure_skin=pressure_skin,
            child_case=source.case, geometry=analysis.geometry, reconstruction=analysis.reconstruction)
        definition.validate()
    except SubmodelRecordError as error:
        raise SubmodelError("CUT_DEFINITION", str(error)) from error
    report = CutSolidReport(analysis.carried, analysis.ignored_remote, tuple(analysis.notes), analysis.region_id,
                            float(analysis.reconstruction["boundary_tolerance"]), analysis.detail)
    return CutSolidSubmodel(definition, report)


# ----------------------------------------------------------------------
# staging
# ----------------------------------------------------------------------
@dataclass
class SolidSubmodelResult:
    """In-memory result of a staged solid run."""

    solved: Any
    prepared: Any
    mesh: Any
    identity: str
    diagnostics: Dict[str, Any]

    @property
    def coefficients(self) -> np.ndarray:
        return np.asarray(self.solved.coefficients)

    @property
    def nodal_displacements(self) -> np.ndarray:
        return np.asarray(self.solved.nodal_displacements)


def _sha(*parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, np.ndarray):
            digest.update(str(part.shape).encode() + part.dtype.str.encode() + np.ascontiguousarray(part).tobytes())
        else:
            digest.update(json.dumps(part, sort_keys=True, default=str, allow_nan=False).encode())
    return digest.hexdigest()


def _mesh_fingerprint(mesh: Any) -> str:
    owners = sorted((int(b), str(h.model_id), int(h.id)) for b, h in mesh.face_owners.items())
    groups = {kind: {k: sorted(int(i) for i in v) for k, v in sorted(getattr(mesh, kind).items())}
              for kind in ("node_groups", "cell_groups", "boundary_groups")}
    return _sha(*(np.asarray(getattr(mesh, n)) for n in ("coordinates", "cells", "node_ids", "cell_ids",
                                                         "boundary_facets", "boundary_ids")), owners, groups)


def _check_mesh_ownership(mesh: Any, cut_groups: Sequence[str], region: Any = None,
                          tolerance: Optional[float] = None) -> None:
    """The six box face groups must partition the boundary and agree with the per-facet owners.

    With the oriented ``region`` snapshot the mesh was made from, each group must be owned by exactly the
    face handle (source model and face id) the snapshot assigns to its name, and every facet's owner must be
    its geometric owner (the mesher's own ``validate_geometry``); a swapped or foreign assignment refuses
    here, before any load is applied.
    """

    _check_group_partition(mesh, cut_groups)
    if region is None:
        return
    from .io.artifacts import ArtifactError
    from .io.solid_submodel_artifact import check_solid_mesh_against_region

    try:
        check_solid_mesh_against_region(mesh, region, boundary_tolerance=float(tolerance),
                                        group_names=("start", "end", *cut_groups))
    except ArtifactError as error:
        raise SubmodelError("SOLID_MESH_OWNERSHIP", str(error)) from error


def _check_group_partition(mesh: Any, cut_groups: Sequence[str]) -> None:
    names = ("start", "end", *cut_groups)
    missing = [n for n in names if n not in mesh.boundary_groups or not len(mesh.boundary_groups[n])]
    if missing:
        _refuse("SOLID_MESH_OWNERSHIP", f"the mesh has no boundary facets for groups {missing}")
    seen: Dict[int, str] = {}
    for name in names:
        for facet in map(int, mesh.boundary_groups[name]):
            if facet in seen:
                _refuse("SOLID_MESH_OWNERSHIP", f"boundary facet {facet} is in both {seen[facet]!r} and {name!r}")
            seen[facet] = name
    if set(seen) != set(map(int, mesh.boundary_ids)):
        _refuse("SOLID_MESH_OWNERSHIP", "the named box faces do not cover the whole boundary")
    owners = {}
    for facet, name in seen.items():
        owners.setdefault(name, set()).add(int(mesh.face_owners[facet].id))
    flat = [f for ids in owners.values() for f in ids]
    if any(len(ids) != 1 for ids in owners.values()) or len(set(flat)) != len(flat):
        _refuse("SOLID_MESH_OWNERSHIP", "each box face group must be owned by exactly one distinct source face")


def _source_pressure_direction(built: Any, face_id: int, analysis: _Analysis) -> np.ndarray:
    """The signed element normal ``n`` of the solved source face (ANYfem's pressure is ``+p * n``).

    Every shell element of the face must have the same normal (parallel to the
    face's plane), must agree with its own solver director where the element has
    one, and must carry exactly the definition's pressure in the assembled solver
    load case.  Returns the unit normal; refuses otherwise.
    """

    from .model.project import ProjectError
    from .solve.build import _element_centroid_normal

    mesh = built.mesh
    elements = sorted(int(e) for e in mesh.elements_of_face.get(face_id, ()))
    if not elements:
        _refuse("SOLID_SOURCE_LINEAGE", f"the source mesh has no shell elements on face {face_id}")
    fe_elements = getattr(getattr(built.fe_model, "mesh", None), "elements", None)
    fe_elements = fe_elements if isinstance(fe_elements, Mapping) else {}
    case = built.load_case
    loads = {} if case is None else dict(getattr(case, "pressure_loads", None) or {})
    expected = 0.0 if analysis.pressure is None else float(analysis.pressure)
    direction: Optional[np.ndarray] = None
    for element_id in elements:
        try:
            _centroid, normal = _element_centroid_normal(mesh, element_id)
            normal = np.asarray(normal, dtype=float).reshape(3)
        except (ProjectError, KeyError, ValueError, TypeError) as error:
            raise SubmodelError("SOLID_SOURCE_DIRECTOR",
                                f"shell element {element_id} has no usable normal: {error}") from error
        if not np.isfinite(normal).all() or abs(float(np.linalg.norm(normal)) - 1.0) > 1.0e-9:
            _refuse("SOLID_SOURCE_DIRECTOR", f"shell element {element_id} normal is not a unit vector")
        element = fe_elements.get(element_id)
        reference, polarity = getattr(element, "reference_normal", None), getattr(element, "director_polarity", None)
        if reference is not None and polarity is not None:
            director = float(polarity) * np.asarray(reference, dtype=float).reshape(3)
            if float(director @ normal) < 1.0 - 1.0e-6:
                _refuse("SOLID_SOURCE_DIRECTOR", f"shell element {element_id}'s solver director disagrees with its "
                                                 "pressure normal")
        if direction is None:
            direction = normal
        elif float(direction @ normal) < 1.0 - 1.0e-8:
            _refuse("SOLID_SOURCE_DIRECTOR", f"shell element {element_id} has a different normal from the other "
                                             f"elements of face {face_id}: a signed pressure would not be uniform")
        value = float(loads.get(element_id, 0.0))
        if abs(value - expected) > 1.0e-12 * max(1.0, abs(expected)):
            _refuse("SOLID_SOURCE_LOADING", f"the solved pressure on element {element_id} is {value!r}, the "
                                            f"definition carries {expected!r}")
    assert direction is not None
    if abs(float(direction @ analysis.normal)) < 1.0 - 1.0e-6:
        _refuse("SOLID_SOURCE_DIRECTOR", "the solved element normal is not parallel to the face plane's normal")
    return direction


def _skin_tractions(analysis: _Analysis, skin: Optional[str],
                    direction: np.ndarray) -> Optional[Dict[str, Tuple[float, float, float]]]:
    """``{skin: p * n}``.  Both skins carry the same global traction: the skin only places it."""

    if skin is None:
        return None
    return {skin: tuple((float(analysis.pressure) * np.asarray(direction, dtype=float)).tolist())}


def _skin_resultant(mesh: Any, skin: str, traction: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
    """Force, moment about the origin and area of a uniform traction on the facets of boundary group ``skin``."""

    position = {int(b): i for i, b in enumerate(np.asarray(mesh.boundary_ids))}
    facets = np.asarray(mesh.boundary_facets, dtype=np.int64).reshape(-1, 3)
    x = np.asarray(mesh.coordinates, dtype=float)
    force, moment, area = np.zeros(3), np.zeros(3), 0.0
    for boundary_id in mesh.boundary_groups[skin]:
        triangle = x[facets[position[int(boundary_id)]]]
        facet_area = 0.5 * float(np.linalg.norm(np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])))
        facet_force = facet_area * traction
        force += facet_force
        moment += np.cross(triangle.mean(axis=0), facet_force)
        area += facet_area
    return force, moment, area


def _verify_solved(solved: Any, prepared: Any, mesh: Any, analysis: _Analysis,
                   definition: SolidSubmodelDefinition, tractions: Optional[Mapping[str, Sequence[float]]]) -> None:
    """The solver's result must be certified, finite, consistent with the mesh and conserve the skin load."""

    def bad(code: str, message: str) -> None:
        _refuse(f"SOLID_RESULT_{code}", message)

    try:
        q = np.asarray(solved.coefficients, dtype=float)
        u = np.asarray(solved.nodal_displacements, dtype=float)
        skin_force = np.asarray(solved.skin_force, dtype=float).reshape(3)
        skin_moment = np.asarray(solved.skin_moment, dtype=float).reshape(3)
        interface_force = np.asarray(solved.interface_force, dtype=float).reshape(3)
        interface_moment = np.asarray(solved.interface_moment, dtype=float).reshape(3)
        reactions = np.asarray(solved.reactions, dtype=float)
        recovery = solved.recovery
        weights = np.asarray(recovery.weights, dtype=float)
        points = np.asarray(recovery.coordinates, dtype=float)
        strain = np.asarray(recovery.strain, dtype=float)
        stress = np.asarray(recovery.stress, dtype=float)
        recovered_cells = np.asarray(recovery.cell_ids)
        diagnostics = solved.diagnostics
    except (AttributeError, TypeError, ValueError) as error:
        raise SubmodelError("SOLID_RESULT_INCOMPLETE", f"the solver result lacks a required field: {error}") from error
    if q.shape != (int(prepared.ndof),) or not np.isfinite(q).all():
        bad("COEFFICIENTS", "the solved coefficients do not match the prepared DOFs or are not finite")
    if u.shape != (len(mesh.node_ids), 3) or not np.isfinite(u).all():
        bad("DISPLACEMENT", "the nodal displacements do not match the mesh nodes or are not finite")
    cells = len(mesh.cell_ids)
    if (weights.ndim != 2 or weights.shape[0] != cells or points.shape != weights.shape + (3,)
            or strain.shape != weights.shape + (3, 3) or stress.shape != weights.shape + (3, 3)
            or recovered_cells.shape != (cells,)
            or not all(np.isfinite(a).all() for a in (weights, points, strain, stress))):
        bad("RECOVERY", "the recovered integration-point fields do not match the mesh cells or are not finite")
    if set(map(int, recovered_cells)) != set(map(int, np.asarray(mesh.cell_ids))):
        bad("RECOVERY", "the recovery covers different cells from the mesh")
    lengths = np.asarray(analysis.volume.upper, dtype=float) - np.asarray(analysis.volume.lower, dtype=float)
    box_volume = float(np.prod(lengths))
    if abs(float(weights.sum()) - box_volume) > 1.0e-8 * box_volume:
        bad("RECOVERY", f"the integration weights sum to {float(weights.sum())!r}, not the detail volume "
                        f"{box_volume!r}")
    if not isinstance(diagnostics, Mapping):
        bad("UNCERTIFIED", "the solver reported no diagnostics")
    certification = diagnostics.get("certification")
    residual = diagnostics.get("residual")
    if (not isinstance(certification, Mapping) or certification.get("certified") is not True
            or not isinstance(residual, Mapping)):
        bad("UNCERTIFIED", "the solve is not certified")
    limit = float(residual.get("balance_limit", 0.0))
    for key in ("force_balance_ratio", "moment_balance_ratio", "free_residual_ratio"):
        value = float(residual.get(key, math.inf))
        if not (math.isfinite(value) and value <= limit):
            bad("BALANCE", f"{key} {value!r} exceeds the solver's balance limit {limit!r}")
    # source mesh effective diagnostics: the shell field was actually evaluated, on the admitted elements, within the
    # shell's half thickness of the reference surface
    source = diagnostics.get("source")
    if not isinstance(source, Mapping) or int(source.get("locations", 0)) < 1 or not source.get("element_kinds"):
        bad("SOURCE", "the solver evaluated the shell field at no location")
    offset = float(source.get("normal_offset_max", math.inf))
    allowed = 0.5 * analysis.thickness * (1.0 + 1.0e-6) + float(analysis.reconstruction["boundary_tolerance"])
    if not (math.isfinite(offset) and offset <= allowed):
        bad("SOURCE", f"a driven point lies {offset!r} from the shell reference surface (limit {allowed!r})")
    # conservation: the skin load is exactly the traction over the skin facets, and the cut reactions balance it
    if tractions is None:
        expected_force, expected_moment = np.zeros(3), np.zeros(3)
    else:
        (skin, traction), = tractions.items()
        expected_force, expected_moment, area = _skin_resultant(mesh, skin, np.asarray(traction, dtype=float))
        footprint_area = float(lengths[0] * lengths[1])
        if abs(area - footprint_area) > 1.0e-8 * footprint_area:
            bad("LOADS", f"the {skin} skin has area {area!r}, the footprint {footprint_area!r}")
    scale = float(np.linalg.norm(expected_force)) + float(np.abs(reactions).sum()) * 1.0e-6
    centre = float(np.max(np.abs(np.asarray(mesh.coordinates, dtype=float)))) if cells else 0.0
    if (np.linalg.norm(skin_force - expected_force) > 1.0e-8 * (scale + 1.0e-300)
            or np.linalg.norm(skin_moment - expected_moment)
            > 1.0e-8 * (scale * max(centre, 1.0) + 1.0e-300)):
        bad("LOADS", "the solver's skin resultant differs from the traction integrated over the skin facets")
    reaction_scale = float(np.abs(reactions).sum()) + float(np.linalg.norm(skin_force))
    if np.linalg.norm(interface_force + skin_force) > 1.0e-6 * (reaction_scale + 1.0e-300) or np.linalg.norm(
            interface_moment + skin_moment) > 1.0e-6 * (reaction_scale * max(centre, 1.0) + 1.0e-300):
        bad("BALANCE", "the cut reactions do not balance the skin load")
    # the persisted interface record (prescribed values, reactions, ownership, allocation, totals) must hold
    from .io.artifacts import ArtifactError
    from .io.solid_submodel_artifact import solid_interface_arrays

    try:
        solid_interface_arrays(solved, prepared)
    except ArtifactError as error:
        bad("INTERFACE", str(error))


def _verify_source(parent: Any, definition: SolidSubmodelDefinition) -> _Analysis:
    analysis = _analyse(parent, definition.source, definition.face_id, definition.footprint, definition.child_case,
                        definition.pressure_skin)
    if not _same(analysis.geometry, _thaw(definition.geometry)):
        _refuse("SOLID_SOURCE_CHANGED", "the source geometry is not the geometry the definition was cut from")
    if not _same(analysis.reconstruction, _thaw(definition.reconstruction)):
        _refuse("SOLID_SOURCE_CHANGED", "the face's section, material, direction, offset or pressure differ from "
                                        "the definition's reconstruction inputs")
    return analysis


def _make_locate(shell_mesh: Any, face_id: int, analysis: _Analysis):
    from anymesher.errors import MeshError
    from anymesher.submodel_correspondence import locate_for_transfer

    elements = sorted(int(e) for e in shell_mesh.elements_of_face.get(face_id, ()))
    if not elements:
        _refuse("SOLID_SOURCE_LINEAGE", f"the source mesh has no shell elements on face {face_id}")
    normal, origin = analysis.normal, analysis.plane_origin

    def locate(xyz: Any) -> List[Tuple[int, Tuple[float, float]]]:
        point = np.asarray(xyz, dtype=float).reshape(3)
        projected = point - float((point - origin) @ normal) * normal
        try:
            hit = locate_for_transfer(shell_mesh, {0: projected}, candidate_elements={0: elements})[0]
        except MeshError as error:
            raise SubmodelError("SOLID_LOCATE", f"no source shell element owns {projected.tolist()}: {error}") from error
        found = [(int(hit.source_element_id), tuple(float(v) for v in hit.natural))]
        found += [(int(item[0]), tuple(float(v) for v in item[1])) for item in hit.alternatives]
        return found

    return locate


def stage_solid_submodel_run(snap: Any, parent: Any, definition: SolidSubmodelDefinition, source: Any, binding: Any,
                             definition_digest: str, store: Any, cancellation_token: Any = None) -> Any:
    """Re-verify, mesh, solve and write a solid run; touches no project and registers nothing.

    ``source`` is the certified ``JobSource`` and ``binding`` its ``SourceBinding``.
    The mesh and result artifacts are checked by ``submodel_jobs._verify_child``, which
    reads the solid mesh codec for ``child_kind == 'solid'``.
    """

    from anygeometry.shell_detail import ShellDetailError

    from .document import canonical_hash
    from .submodel_jobs import StagedRun, _check_cancelled, _producer_versions, _remove, _utc_now, _verify_child

    cancel = _Cancel(cancellation_token)
    _check_cancelled(cancellation_token, "solid source verification")
    entry = definition.to_dict()
    child_model = solid_definition_hash(entry)
    if child_model != definition_digest:
        _refuse("SOLID_SOURCE_CHANGED", "the definition does not have the identity the run was requested for")
    analysis = _verify_source(parent, definition)
    built = source.solution.built
    shell_mesh = built.mesh
    direction = _source_pressure_direction(built, definition.face_id, analysis)
    locate = _make_locate(shell_mesh, definition.face_id, analysis)
    for corner in (*analysis.footprint, analysis.footprint.mean(axis=0)):  # lineage: the face's elements own the detail
        if not locate(corner):
            _refuse("SOLID_SOURCE_LINEAGE", "the source elements do not own the footprint")
    cut_groups = tuple(f"side_{i}" for i in range(4))

    _check_cancelled(cancellation_token, "solid meshing")
    import anysolid
    from anymesher.structured_box import generate_structured_box_mesh
    from anymesher.volume import VolumeMeshError, VolumeMeshingCancelled
    from anysolver.solid_submodel import SolidSubmodelCancelled, SolidSubmodelError, solve_shell_driven_solid

    try:
        region = analysis.detail.solid_region_snapshot(analysis.region_id)
        mesh = generate_structured_box_mesh(
            region, target_edge_length=float(definition.target_size), layers=int(definition.layers),
            boundary_tolerance=float(analysis.reconstruction["boundary_tolerance"]), cancel=cancel)
    except VolumeMeshingCancelled as error:
        raise SubmodelError("SUBMODEL_CANCELLED", f"cancelled during solid meshing: {error}") from error
    except (VolumeMeshError, ShellDetailError) as error:
        raise SubmodelError("SOLID_MESH", str(error)) from error
    _check_mesh_ownership(mesh, cut_groups, region, float(analysis.reconstruction["boundary_tolerance"]))

    _check_cancelled(cancellation_token, "solid preparation")
    try:
        prepared = anysolid.prepare(mesh, {"elastic_modulus": analysis.young, "poisson_ratio": analysis.poisson},
                                    order=int(definition.order))
    except ValueError as error:
        raise SubmodelError("SOLID_PREPARE", str(error)) from error

    _check_cancelled(cancellation_token, "solid solve")
    tractions = _skin_tractions(analysis, definition.pressure_skin, direction)
    try:
        solved = solve_shell_driven_solid(
            built.fe_model, np.asarray(source.solution.displacements, dtype=float), prepared,
            cut_groups=cut_groups, locate=locate, skin_tractions=tractions, cancel=cancel)
    except SolidSubmodelCancelled as error:
        raise SubmodelError("SUBMODEL_CANCELLED", f"cancelled during solid solve: {error}") from error
    except SolidSubmodelError as error:
        raise SubmodelError(f"SOLID_{error.code}", str(error)) from error
    _verify_solved(solved, prepared, mesh, analysis, definition, tractions)

    _check_cancelled(cancellation_token, "artifact writing")
    run_id = str(uuid4())
    mesh_id = f"{run_id}-mesh"
    destinations = (store.resolve(f"meshes/{mesh_id}.anymesh.h5"), store.resolve(f"results/{run_id}.anyres.h5"))
    if any(path.exists() for path in destinations):
        _refuse("SUBMODEL_ARTIFACT", "the run's artifact destinations already exist")
    child_loading = canonical_hash({"child_case": definition.child_case, "pressure_skin": definition.pressure_skin,
                                    "pressure": analysis.pressure})
    fingerprint = _mesh_fingerprint(mesh)
    solver_source = solved.diagnostics.get("source", {})
    diagnostics = json_safe({
        "child_kind": "solid", "solver": solved.diagnostics,
        "interface_force": np.asarray(solved.interface_force).tolist(),
        "interface_moment": np.asarray(solved.interface_moment).tolist(),
        "interface_groups": {name: {"force": np.asarray(g.force).tolist(), "moment": np.asarray(g.moment).tolist(),
                                    "dof_count": int(g.dof_count), "shared_dof_count": int(g.shared_dof_count)}
                             for name, g in solved.group_resultants.items()},
        "attribution_rule": str(solved.attribution_rule),
        "moment_origin": [0.0, 0.0, 0.0], "moment_frame": "world",
        "mesh": {"nodes": int(len(mesh.node_ids)), "cells": int(len(mesh.cell_ids)),
                 "boundary_facets": int(len(mesh.boundary_ids)), "order": int(definition.order),
                 "layers": int(definition.layers)},
        "source_mesh": {"face_elements": len(shell_mesh.elements_of_face.get(definition.face_id, ())),
                        "locations": solver_source.get("locations"), "element_kinds": solver_source.get("element_kinds"),
                        "hit_spread_max": solver_source.get("hit_spread_max"),
                        "normal_offset_max": solver_source.get("normal_offset_max")},
        "retained_pressure": {
            "skin": definition.pressure_skin, "value": analysis.pressure,
            "direction": direction.tolist(),
            "direction_vs_geometry_normal": float(np.sign(direction @ analysis.normal)),
            "traction": None if tractions is None else {k: list(v) for k, v in tractions.items()},
            "resultant_force": np.asarray(solved.skin_force).tolist(),
            "resultant_moment": np.asarray(solved.skin_moment).tolist()},
    })
    identity = _sha(definition_digest, fingerprint, np.asarray(solved.coefficients, dtype=float),
                    binding.field_fingerprint, binding.operator_fingerprint)
    from .io.artifacts import ArtifactError

    try:
        from .io.solid_submodel_artifact import read_solid_mesh, write_solid_mesh, write_solid_result

        boundary_tolerance = float(analysis.reconstruction["boundary_tolerance"])
        mesh_ref = write_solid_mesh(store, mesh, mesh_id=mesh_id, document_id=parent.document_id,
                                    model_hash=child_model, mesh_hash=fingerprint, detail=analysis.detail,
                                    region_id=analysis.region_id, boundary_tolerance=boundary_tolerance)
        result_ref = write_solid_result(
            store, solved, prepared, mesh, run_id=run_id, document_id=parent.document_id, mesh_id=mesh_id,
            model_hash=child_model, mesh_hash=fingerprint, analysis_hash=definition_digest,
            provenance={"submodel": {"definition_id": definition.id, "child_kind": "solid",
                                     "source_job_id": definition.source.job_id}},
            summary={"submodel_run": run_id, "child_kind": "solid"}, diagnostics=[diagnostics])
        run = SubmodelRun(
            id=run_id, definition_id=definition.id, definition_hash=definition_digest,
            child_case=definition.child_case, source={**source.manifest, "binding": vars(binding)},
            child={"child_kind": "solid", "schema_version": 1, "model_hash": child_model,
                   "loading_hash": child_loading, "mesh_fingerprint": fingerprint,
                   "mesh_artifact": descriptor(mesh_ref), "result_artifact": descriptor(result_ref),
                   "face_id": int(definition.face_id), "order": int(definition.order),
                   "layers": int(definition.layers), "pressure_skin": definition.pressure_skin,
                   "region_id": analysis.region_id, "geometry": dict(analysis.geometry)},
            identity=identity, diagnostics=diagnostics,
            producer_versions={"job": source.manifest["job_producer_versions"], "run": _producer_versions()},
            created_utc=_utc_now())
        reread = read_solid_mesh(store, mesh_ref)  # the written file rebuilds its stored source and passes every check
        if {int(b): (str(h.model_id), int(h.id)) for b, h in mesh.face_owners.items()} != dict(reread.face_owners):
            _refuse("SOLID_MESH_OWNERSHIP", "the written mesh's face owners differ from the meshed ones")
        if reread.region_id != analysis.region_id or str(reread.source_geometry.model_id) != str(analysis.detail.model_id):
            _refuse("SOLID_MESH_OWNERSHIP", "the written mesh's stored source is not the detail it was generated from")
        _verify_child(store, mesh_ref, "mesh", run, parent.document_id)
        _verify_child(store, result_ref, "result", run, parent.document_id)
        json.dumps(run.to_dict(), allow_nan=False)
        json.dumps([mesh_ref.to_dict(), result_ref.to_dict()], allow_nan=False)
    except ArtifactError as error:
        # Typed refusal: this attempt's files go, nothing was registered, the project is untouched.
        _remove(destinations)
        raise SubmodelError("SUBMODEL_ARTIFACT", str(error)) from error
    except BaseException:
        # The destinations were reserved (absent before), so whatever exists is this attempt's.
        _remove(destinations)
        raise
    return StagedRun(run, (mesh_ref, result_ref), snap.snapshot.revision.id, snap.thread_id, destinations, store,
                     parent.document_id, SolidSubmodelResult(solved, prepared, mesh, identity, diagnostics))
