"""Shell element axis orientation check (direction programme slice D1).

Recovered shell components such as ``membrane_xx`` or ``top_yy`` are reported
in each element's own local axes, and those axes come from the element's node
order.  Two neighbouring elements on one plate can therefore report "xx" along
different physical directions, which shows up as patches in a contour plot even
where the stress field is smooth.

This module measures that, without re-deriving any solver frame:

1. The finished solver model is built exactly as a solve builds it
   (``anyfem.solve.build.build_fe_model`` without loads or support
   requirements: publication checks, project validation, shells with the
   formulation policy and the S3 owner-normal admission and faceted reference,
   beams, couplings, supports, connections, masses, and the geometric
   imperfections that move the stress-free reference geometry).  If the build
   refuses, every shell element is unchecked with the build's reason.
2. Each shell element of that model is first asked for an ordinary stress
   recovery (zero displacements, its own registered material, the full model
   mesh), so material and model-scope refusals make it unchecked.  Then its
   ``compute_stresses`` is called with six uniform strain fields ``u = E x``
   (``E`` running over a basis of symmetric tensors, zero rotations) and a
   probe material with E = 1 and nu = 0.  At every recovery point the solver
   then reports ``membrane_ab = e_a . E . e_b`` in its output frame
   ``(e_x, e_y)``, so the six calls give that frame directly, including any
   fallback the element takes (a warped qualified Q4 recovers through
   ``ShellElement.compute_stresses`` with a frame per Gauss point; a V2D
   triangle uses its reference normal, faceted when the policy says so).  A
   response that is not that of a rotated orthonormal frame is a refusal.
   The output frame does not depend on the material for the elements the
   build creates (ANYfem sets no ``material_direction`` or
   ``material_angle_deg``; an orthotropic material turns only the material
   axes, see D0); the material's admissibility is checked by the first call.
   Speed: for an exact legacy ``ShellElement`` on its ordinary recovery path
   the frames are read from the two solver functions its ``compute_stresses``
   uses to pick them (``native_recovery_frames``, else
   ``_local_frame_and_derivatives`` at ``gauss_points``) instead of six
   recoveries - only where that is the recovery frame by construction (exact
   class, unpatched class namespaces and helpers, no instance override; see
   ``_solver_frame_eligible``).  Every other element goes through the probe.
   As an extra cross-check, every 25th fast element (the first included) is
   also probed; a disagreement or probe failure switches every element to the
   probe.  Each element's ``frame_source`` says which route gave its frames.
   Precondition of this route (owner solver contract, adopted for D1
   2026-11-18): "no monkeypatching, no mid-request surface changes".  The
   guard catches the common cases but is not an exhaustive detector; a
   module-level patch the guard does not see (e.g. of
   ``recovery_shell_frames.native_recovery_frames``) is outside the contract.
   The route also requires ``frame_conditioning`` (rounding amplification of
   the frame at every recovery point, from the element's own Jacobian, so
   curved and midside-narrowed elements count) to be at most 1e7; otherwise
   the element goes through the full probe, which accepts or refuses it on
   its residual.
   Scale: elements whose node coordinates lie more than 1e7 smallest element
   dimensions from the origin (or are not finite) are unchecked on either
   route - the solver's own frame loses precision there (0.04 deg at 1e12 m).
   Mesh: only the project's active published mesh record is checked; any
   other mesh is unchecked with an ``unpublished mesh`` reason (see
   ``published_mesh_key``).
   Scope (owner decision 2026-11-18, option A): the check covers the model a
   static solve of the project as it stands would build, project imperfections
   included.  Like that solve's build it normalises the project's
   section-assignment records (``Project.resolve_section_assignments``, which
   adopts legacy ``face_sections``/``edge_sections`` edits into assignment,
   region, sheet and part records); it changes nothing else.  Out of scope:
   reference-geometry changes an analysis adds at solve time (the
   ``imperfection`` argument of nonlinear and arc-length solves, the
   eigenmode imperfection of a capacity analysis).  Those belong to D2
   (option C: frames checked per analysis, next to result recovery).
3. Per geometry face, local x is measured against a fixed reference: the
   global axis least aligned with the face's normals (at the element
   centroids), projected onto the tangent plane at each centroid.  The face
   normal is ``du x dv``.  Nothing depends on how the face is parameterised.
   Angles are doubled, so x and -x agree.  The face's dominant direction is the
   mean of the element doubled-angle unit vectors; each recovery point is then
   ``aligned`` (within 45 - 10 deg of it, folded to 0-90), ``turned`` (beyond
   45 + 10 deg: xx and yy exchanged) or ``ambiguous`` (in between).  An element
   whose points disagree (aligned and turned, or normals on both sides) is
   ``inconsistent`` - a folded or strongly curved element.  A face whose
   resultant is too small to name a dominant direction (its elements split
   between layouts with no majority) is ``mixed``: its compared elements are
   counted as mixed - not consistent, and not unchecked.

   Curved faces: the reference is the projected global axis at each
   centroid, and "consistent" means only that local x keeps a constant angle
   (within the bands) from it.  On a developable or gently curved face that is
   a smooth physical reference.  There is no curvature guard: on a strongly
   doubly curved face a constant angle from a projected axis may or may not be
   the layout that matters (meridional x on a sphere band is consistent against
   projected z), and the check does not judge that.  Where the axis becomes
   nearly normal to the face (projected length below 0.5) the element is
   unchecked.  Recovery frames are compared with the face frame at the element
   centroid, so on a face curved strongly within one element the face's turn
   across the element adds to the measured angle.
4. The normal at every recovery point is compared with the face normal; the
   face's majority side is the reference.  Reversing the normal with x kept
   flips e_y: ``membrane_xy``, ``bending_xx`` and ``bending_yy`` change sign,
   ``bending_xy`` and ``membrane_xx/yy`` do not, and top and bottom swap.

The report never calls anything consistent that it did not fully compare:
unchecked (could not be compared), mixed, ambiguous and inconsistent elements
all make a face inconsistent and are counted in ``summary()``.  It changes no
mesh or result and keeps no solver model; its one project change is the
solve-equivalent section-assignment normalisation (see Scope).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "ALIGNED",
    "AMBIGUOUS",
    "INCONSISTENT",
    "MIXED",
    "OrientationCancelled",
    "SCOPE_NOTE",
    "TURNED",
    "ElementAxes",
    "FaceOrientation",
    "OrientationReport",
    "RecoveryFrame",
    "UnpublishedMesh",
    "element_recovery_frames",
    "published_mesh_key",
    "model_orientation_report",
    "model_recovery_frames",
    "project_orientation_report",
    "project_recovery_frames",
    "shell_orientation_report",
]

ALIGNED = "aligned"
TURNED = "turned"
AMBIGUOUS = "ambiguous"
INCONSISTENT = "inconsistent"
MIXED = "mixed"

PROBE = "stress-probe"
SOLVER_FRAME = "solver-frame"
# Every n-th element read through SOLVER_FRAME is also probed; one
# disagreement switches the whole check to the probe.
VERIFY_EVERY = 25
# Largest |node coordinate| in smallest element dimensions: beyond it the
# solver's own frame loses precision (measured on a legacy Q8 2026-11-18:
# 1e9 m from the origin 2e-5 deg, 1e12 m 0.04 deg), so the element is
# unchecked.  The smallest dimension (not the largest) bounds the error in the
# element's narrow direction too (Codex round 5: a 1 x 1e-4 sliver at 1e6 m).
COORDINATE_SCALE_LIMIT = 1.0e7
# Largest frame conditioning R * sum_i |grad N_i| / sigma_min(J) at any recovery
# point for the solver-frame route (see ``frame_conditioning``).  First-order
# bound on the frame's rounding error: about 8 * eps * this ratio, i.e.
# 2e-8 rad at the limit, well inside the probe's 1e-6 tolerance.  Elements
# above it go through the full probe (fail closed).
FRAME_CONDITIONING_LIMIT = 1.0e7

SCOPE_NOTE = ("Static-solve model of the project as it stands; analysis-time "
              "imperfections (nonlinear, arc-length, capacity) are not included (D2).")


class OrientationCancelled(RuntimeError):
    """The caller's cancel event was set while recovery frames were being read."""

# Half-width of the band around 45 degrees that is reported as ambiguous.
AMBIGUOUS_HALF_BAND_DEG = 10.0
# Smallest resultant of the element doubled-angle unit vectors that names a
# face's dominant direction (0 = an even split, 1 = all elements identical).
MIN_DOMINANT_RESULTANT = 0.2
# A centroid further than this fraction of the element size from the face is
# not trusted to belong to it.
_PROJECTION_FRACTION = 0.25
# Largest residual of the six-field response against an exact rotated frame.
FRAME_RESIDUAL_TOLERANCE = 1.0e-6
# Smallest |cos| between a recovery normal and the face normal that is
# accepted as a side; below it the element is unchecked.
_NORMAL_COS_LIMIT = 0.5
# Smallest in-plane length of local x, or of the reference axis, after
# projection onto the face's tangent plane.
_IN_PLANE_LIMIT = 0.5

_PAIRS = ((0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2))
_AXES = ("x", "y", "z")


@dataclass(frozen=True)
class RecoveryFrame:
    """Output frame at one recovery point, read from the solver's stress recovery."""

    x_axis: np.ndarray
    y_axis: np.ndarray
    normal: np.ndarray
    # Natural coordinates of the point when the element reports them.
    natural_point: Optional[Tuple[float, ...]] = None


@dataclass(frozen=True)
class ElementAxes:
    element_id: int
    face_id: int
    element_class: str
    # Centroid of the corners in the solver model (imperfections applied).
    centroid: np.ndarray
    size: float
    frames: Tuple[RecoveryFrame, ...]
    # PROBE (six-field compute_stresses probe) or SOLVER_FRAME (the frame
    # function compute_stresses itself calls; legacy ShellElement only).
    frame_source: str = "stress-probe"

    def display_frame(self) -> RecoveryFrame:
        """The recovery frame nearest the element's natural centre (else the first)."""

        points = [f.natural_point for f in self.frames]
        if any(p is None for p in points):
            return self.frames[0]
        centre = np.mean(np.asarray(points, dtype=float), axis=0)
        return self.frames[int(np.argmin([np.linalg.norm(np.asarray(p) - centre) for p in points]))]


@dataclass(frozen=True)
class FaceOrientation:
    face_id: int
    # Shell element references on the face, including missing ones.
    element_count: int
    # Global axis the in-plane angles are measured from (None: no element compared).
    reference_axis: Optional[str]
    # Dominant local-x angle from the projected reference axis, degrees in
    # (-90, 90]; None when no dominant direction exists.
    dominant_angle_deg: Optional[float]
    dominant_resultant: float
    aligned: Tuple[int, ...]
    # Local x turned about 90 degrees from the dominant direction.
    turned: Tuple[int, ...]
    # Some recovery point within the band around 45 degrees.
    ambiguous: Tuple[int, ...]
    # Recovery points of one element in different axis classes or on
    # different normal sides.
    inconsistent: Tuple[int, ...]
    # Compared elements of a face with no dominant direction.
    mixed: Tuple[int, ...]
    normal_with: int
    normal_against: int
    # Elements whose normal is on the minority side of the face (the
    # against-du-x-dv side on a tie).
    reversed: Tuple[int, ...]
    # element id -> reason it could not be checked.
    unchecked: Mapping[int, str] = field(default_factory=dict)

    @property
    def checked(self) -> int:
        return self.element_count - len(self.unchecked)

    @property
    def consistent(self) -> bool:
        # A face with no shell element compared nothing, so it is not consistent.
        return self.element_count > 0 and not (self.turned or self.ambiguous or self.inconsistent
                                               or self.mixed or self.reversed or self.unchecked)


@dataclass(frozen=True)
class OrientationReport:
    faces: Tuple[FaceOrientation, ...]
    # Recovery frames of the elements that were classified.
    element_axes: Mapping[int, ElementAxes] = field(default_factory=dict, compare=False, repr=False)

    def _all(self, name: str) -> Tuple[int, ...]:
        return tuple(sorted(e for face in self.faces for e in getattr(face, name)))

    @property
    def turned(self) -> Tuple[int, ...]:
        return self._all("turned")

    @property
    def ambiguous(self) -> Tuple[int, ...]:
        return self._all("ambiguous")

    @property
    def inconsistent(self) -> Tuple[int, ...]:
        return self._all("inconsistent")

    @property
    def reversed(self) -> Tuple[int, ...]:
        return self._all("reversed")

    @property
    def mixed(self) -> Tuple[int, ...]:
        return self._all("mixed")

    # Names kept from the first D1 version.
    axis_minority = turned
    normal_minority = reversed

    @property
    def unchecked(self) -> Dict[int, str]:
        return {e: reason for face in self.faces for e, reason in face.unchecked.items()}

    @property
    def consistent(self) -> bool:
        # An empty report compared nothing, so it is not consistent.
        return bool(self.faces) and all(face.consistent for face in self.faces)

    def summary(self) -> str:
        total = sum(face.element_count for face in self.faces)
        unchecked = self.unchecked
        verdict = "consistent" if self.consistent else "NOT consistent"
        lines = [
            f"Shell axis check ({verdict}): {total} elements on {len(self.faces)} faces; "
            f"{len(self.turned)} with local x turned against their face, "
            f"{len(self.reversed)} with reversed normal, "
            f"{len(self.ambiguous)} ambiguous (near 45 deg), "
            f"{len(self.inconsistent)} inconsistent within the element, "
            f"{len(self.mixed)} on {sum(1 for f in self.faces if f.mixed)} mixed faces "
            f"(no dominant direction), "
            f"{len(unchecked)} unchecked. {SCOPE_NOTE}"
        ]
        for face in self.faces:
            if face.consistent:
                continue
            angle = ("no dominant direction" if face.dominant_angle_deg is None
                     else f"dominant x {face.dominant_angle_deg:.1f} deg from global {face.reference_axis}")
            lines.append(
                f"  face {face.face_id}: {face.element_count} elements, {angle} "
                f"(resultant {face.dominant_resultant:.2f}); aligned {len(face.aligned)}, "
                f"turned {len(face.turned)}, ambiguous {len(face.ambiguous)}, "
                f"inconsistent {len(face.inconsistent)}, mixed {len(face.mixed)}; normal with/against "
                f"{face.normal_with}/{face.normal_against}, reversed {len(face.reversed)}; "
                f"unchecked {len(face.unchecked)}"
            )
            reasons: Dict[str, int] = {}
            for reason in face.unchecked.values():
                reasons[reason] = reasons.get(reason, 0) + 1
            for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[:3]:
                lines.append(f"    unchecked {count}: {reason}")
        return "\n".join(lines)


class UnpublishedMesh(ValueError):
    """The mesh is not the project's active published mesh record (typed refusal)."""


def published_mesh_key(project: Any, mesh: Any) -> Tuple[str, str]:
    """``(record id, record mesh hash)`` of the active published mesh, or ``UnpublishedMesh``.

    D1 runs only on a published mesh record (owner decision 2026-11-18): the
    mesh object must carry the publication tag of the project's active mesh
    record, and that record must have its semantic mesh hash.  Precondition
    (contract): a published mesh is immutable - no ANYfem code writes its
    nodes, elements, face map, preparation or diagnostics after publication
    (verified by inspection 2026-11-18: all writers are on the generation
    path).  The solve build re-verifies the content against the record hash
    (``matches_mesh_hash``) before anything is compared.
    """

    record_id = getattr(mesh, "_anyfem_mesh_record_id", None)
    if record_id is None:
        raise UnpublishedMesh("mesh is not a published mesh record; generate or update the mesh first")
    numbering = getattr(project, "mesh_numbering", None)
    if not getattr(numbering, "generation", 0) or getattr(numbering, "active_mesh_id", None) != record_id:
        raise UnpublishedMesh("mesh is not the project's active published mesh")
    record = getattr(project, "mesh_records", {}).get(record_id)
    if record is None or not getattr(record, "mesh_hash", None):
        raise UnpublishedMesh("the published mesh record has no mesh hash")
    return str(record_id), str(record.mesh_hash)


def _unit(vector: np.ndarray) -> Optional[np.ndarray]:
    length = float(np.linalg.norm(vector))
    if not np.isfinite(length) or length <= 1.0e-14:
        return None
    return vector / length


def _probe_material():
    from anyfesolver.fe_core import Material

    return Material(name="anyfem-axis-probe", elastic_modulus=1.0, poisson_ratio=0.0)


def element_recovery_frames(element: Any, fe_mesh: Any, material: Any = None) -> Tuple[RecoveryFrame, ...]:
    """Read the output frame at every recovery point from ``compute_stresses``.

    ``fe_mesh`` is the solver mesh the element belongs to.  Raises
    ``ValueError`` when the element's response is not that of an orthonormal
    output frame (the element is then unchecked, never guessed).
    """

    material = _probe_material() if material is None else material
    coords = np.asarray(element.get_node_coordinates(fe_mesh), dtype=float)
    count = len(coords)
    if int(element.total_dofs) != 6 * count:
        raise ValueError(f"unexpected DOF layout ({element.total_dofs} DOFs on {count} nodes)")
    responses = []
    natural = None
    for i, j in _PAIRS:
        strain = np.zeros((3, 3))
        strain[i, j] += 0.5
        strain[j, i] += 0.5
        displacements = np.zeros(6 * count)
        for n in range(count):
            displacements[6 * n:6 * n + 3] = strain @ coords[n]
        result = element.compute_stresses(fe_mesh, displacements, material)
        try:
            rows = np.asarray([result["membrane_xx"], result["membrane_yy"], result["membrane_xy"]],
                              dtype=float)
        except KeyError as error:
            raise ValueError(f"stress recovery reports no {error.args[0]}") from None
        if rows.ndim != 2 or rows.shape[1] == 0 or not np.all(np.isfinite(rows)):
            raise ValueError("stress recovery returned no finite membrane stresses")
        if responses and rows.shape != responses[0].shape:
            raise ValueError("stress recovery changed its number of points")
        responses.append(rows)
        if natural is None and "natural_point_coordinates" in result:
            natural = np.asarray(result["natural_point_coordinates"], dtype=float)
    points = responses[0].shape[1]
    if natural is None:
        gauss = getattr(element, "gauss_points", None)
        if gauss is not None and len(gauss) == points:
            natural = np.asarray(gauss, dtype=float)
    if natural is not None and len(natural) != points:
        natural = None
    frames = []
    for p in range(points):
        tensors = np.zeros((3, 3, 3))  # component (xx, yy, xy) -> symmetric 3x3
        for (i, j), rows in zip(_PAIRS, responses):
            for c in range(3):
                tensors[c, i, j] = tensors[c, j, i] = rows[c, p]
        e1 = np.linalg.eigh(tensors[0])[1][:, -1]
        e2 = np.linalg.eigh(tensors[1])[1][:, -1]
        if float(e1 @ tensors[2] @ e2) < 0.0:
            e2 = -e2
        expected = (np.outer(e1, e1), np.outer(e2, e2), 0.5 * (np.outer(e1, e2) + np.outer(e2, e1)))
        residual = max(float(np.abs(tensors[c] - expected[c]).max()) for c in range(3))
        residual = max(residual, abs(float(e1 @ e2)))
        if not residual <= FRAME_RESIDUAL_TOLERANCE:
            raise ValueError(
                f"stress recovery at point {p} is not a rotated orthonormal frame "
                f"(residual {residual:.2e} > {FRAME_RESIDUAL_TOLERANCE:.0e})")
        point = None if natural is None else tuple(float(v) for v in natural[p])
        frames.append(RecoveryFrame(e1, e2, np.cross(e1, e2), point))
    return tuple(frames)


def _face_ids(mesh: Any, faces: Optional[Iterable[int]]) -> Sequence[int]:
    known = sorted(int(face) for face in mesh.elements_of_face)
    if faces is None:
        return known
    wanted = sorted({int(face) for face in faces})
    missing = [face for face in wanted if face not in mesh.elements_of_face]
    if missing:
        raise ValueError(f"faces {missing} have no mesh elements")
    return wanted


def _check_cancel(cancel: Any) -> None:
    if cancel is not None and cancel.is_set():
        raise OrientationCancelled("shell axis check cancelled")


def model_recovery_frames(
    fe_model: Any,
    mesh: Any,
    *,
    faces: Optional[Iterable[int]] = None,
    cancel: Any = None,
) -> Tuple[Dict[int, ElementAxes], Dict[int, Tuple[int, str]]]:
    """Read the recovery frames of the shell elements of a built solver model.

    ``mesh`` supplies the face membership (``elements_of_face``); every
    element is taken from ``fe_model.mesh``, so its coordinates, neighbours
    and material are the model's.  Returns ``(axes, unchecked)``;
    ``unchecked`` maps element id to ``(face id, reason)``.
    """

    import warnings

    from anyfesolver.elements import ShellElement

    fe_mesh = fe_model.mesh
    probe = _probe_material()
    fast_ok = True
    eligible_count = 0
    axes: Dict[int, ElementAxes] = {}
    unchecked: Dict[int, Tuple[int, str]] = {}
    seen: Dict[int, int] = {}
    for face_id in _face_ids(mesh, faces):
        for raw in mesh.elements_of_face[face_id]:
            _check_cancel(cancel)
            element_id = int(raw)
            if element_id in seen:
                unchecked[element_id] = (face_id, f"element also on face {seen[element_id]}")
                axes.pop(element_id, None)
                continue
            seen[element_id] = face_id
            element = fe_mesh.elements.get(element_id)
            if element is None:
                unchecked[element_id] = (face_id, "face references a shell element missing from the solver model")
                continue
            if not isinstance(element, ShellElement):
                unchecked[element_id] = (face_id, f"not a shell element ({type(element).__name__})")
                continue
            try:
                coords = np.asarray(element.get_node_coordinates(fe_mesh), dtype=float)
                corners = coords[: 4 if len(coords) in (4, 8) else 3]
                size = float(np.linalg.norm(corners[:, None, :] - corners[None, :, :], axis=2).max())
                refusal = _scale_refusal(coords, corners)
                if refusal is not None:
                    unchecked[element_id] = (face_id, refusal)
                    continue
                material = fe_model.materials[element.material_name]
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    # Ordinary recovery first: the element's own material and
                    # the full model scope must be admitted.
                    element.compute_stresses(fe_mesh, np.zeros(int(element.total_dofs)), material)
                    source = PROBE
                    if (fast_ok and _solver_frame_eligible(element, ShellElement)
                            and frame_conditioning(element, coords) <= FRAME_CONDITIONING_LIMIT):
                        frames = _legacy_recovery_frames(element, fe_mesh, material)
                        source = SOLVER_FRAME
                        if eligible_count % VERIFY_EVERY == 0:
                            # Extra cross-check only: eligibility, not this sample,
                            # is what admits the fast route.  Any disagreement or
                            # probe failure sends every element through the probe.
                            try:
                                probed = element_recovery_frames(element, fe_mesh, probe)
                            except Exception:
                                probed = None
                            if probed is None or not _same_frames(frames, probed):
                                fast_ok = False
                                frames = element_recovery_frames(element, fe_mesh, probe)
                                source = PROBE
                        eligible_count += 1
                    else:
                        frames = element_recovery_frames(element, fe_mesh, probe)
            except Exception as error:  # solver refusal: report, never assume
                unchecked[element_id] = (face_id, f"{type(error).__name__}: {error}")
                continue
            axes[element_id] = ElementAxes(
                element_id=element_id,
                face_id=face_id,
                element_class=type(element).__name__,
                centroid=corners.mean(axis=0),
                size=size,
                frames=frames,
                frame_source=source,
            )
    if not fast_ok:
        # A sampled cross-check disagreed: trust only the probe, for every element.
        for element_id, item in list(axes.items()):
            if item.frame_source != SOLVER_FRAME:
                continue
            _check_cancel(cancel)
            element = fe_mesh.elements[element_id]
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    frames = element_recovery_frames(element, fe_mesh, probe)
            except Exception as error:
                del axes[element_id]
                unchecked[element_id] = (item.face_id, f"{type(error).__name__}: {error}")
                continue
            axes[element_id] = dataclasses.replace(item, frames=frames, frame_source=PROBE)
    return axes, unchecked


def smallest_dimension(corners: np.ndarray) -> float:
    """Smallest extent of an element: its shortest corner-to-corner distance or,
    if smaller, its width across the longest direction (second singular value
    of the centred corners; equals the short side of a rectangle)."""

    corners = np.asarray(corners, dtype=float)
    pairs = np.linalg.norm(corners[:, None, :] - corners[None, :, :], axis=2)
    shortest = float(pairs[np.triu_indices(len(corners), 1)].min())
    singular = np.linalg.svd(corners - corners.mean(axis=0), compute_uv=False)
    return min(shortest, float(singular[1]))


def _scale_refusal(coords: np.ndarray, corners: np.ndarray) -> Optional[str]:
    if not np.all(np.isfinite(coords)):
        return "non-finite node coordinates"
    smallest = smallest_dimension(corners)
    if not smallest > 0.0:
        return "degenerate element (zero width)"
    ratio = float(np.abs(coords).max()) / smallest
    if ratio > COORDINATE_SCALE_LIMIT:
        return (f"node coordinates {ratio:.1e} smallest element dimensions from the origin exceed "
                f"{COORDINATE_SCALE_LIMIT:.0e}: the solver's frame loses precision")
    return None


def frame_conditioning(element: Any, coords: np.ndarray) -> float:
    """Worst rounding amplification of the element's frame at its recovery points.

    The solver frame at a recovery point comes from the tangents
    ``J = [sum_i dN_i/dxi X_i, sum_i dN_i/deta X_i]``.  Their rounding error
    is at most about ``n eps R sum_i |grad N_i|`` (``R`` the largest
    coordinate magnitude, ``n`` nodes), and an orthonormalised frame built
    from ``J`` turns by at most about that error over ``sigma_min(J)``, the
    smallest singular value of the 3 x 2 tangent matrix - the element's local
    width in the narrowest direction, including the effect of midside nodes
    (a curved Q8 that is narrow between well separated corners has a small
    ``sigma_min`` there).  Returns ``max R sum_i |grad N_i| / sigma_min(J)``
    over ``gauss_points`` (infinite if ``J`` is singular).
    """

    coords = np.asarray(coords, dtype=float)
    scale = max(float(np.abs(coords).max()), np.finfo(float).tiny)
    derivatives = [element.compute_shape_functions(float(xi), float(eta))[1:]
                   for xi, eta in np.asarray(element.gauss_points, dtype=float)]
    d_xi = np.asarray([d for d, _e in derivatives], dtype=float)     # points x nodes
    d_eta = np.asarray([e for _d, e in derivatives], dtype=float)
    t_xi, t_eta = d_xi @ coords, d_eta @ coords                       # points x 3
    # sigma_min of [t_xi t_eta] = |t_xi x t_eta| / sigma_max, with the area
    # computed directly from the cross product (no cancellation).
    area = np.linalg.norm(np.cross(t_xi, t_eta), axis=1)
    gram_trace = np.einsum("ij,ij->i", t_xi, t_xi) + np.einsum("ij,ij->i", t_eta, t_eta)
    sigma_max = np.sqrt(0.5 * (gram_trace + np.sqrt(np.maximum(gram_trace ** 2 - 4.0 * area ** 2, 0.0))))
    smallest = area / np.maximum(sigma_max, np.finfo(float).tiny)
    if not np.all(np.isfinite(smallest)) or not np.all(smallest > 0.0):
        return float("inf")
    return float((scale * np.hypot(d_xi, d_eta).sum(axis=1) / smallest).max())


def _solver_frame_eligible(element: Any, shell_class: type) -> bool:
    """True only where the solver-frame route is the recovery frame by construction.

    Exact ``ShellElement`` on its ordinary path (no generalised section), with
    the same guard the solver's ``native_recovery_frames`` applies: the
    ``ShellElement`` and ``Element`` class namespaces and the frame helpers are
    the ones captured when anyfesolver.elements was imported, and no instance
    attribute shadows any of their names (so ``compute_stresses`` and the
    frame functions it calls are the originals for this element).
    """

    from anyfesolver import elements

    if type(element) is not shell_class or getattr(element, "shell_section", None) is not None:
        return False
    if getattr(element, "gauss_points", None) is None:
        return False
    instance = vars(element)
    for cls, name in ((elements.ShellElement, "_SHELL_RECOVERY_FRAME_METHODS"),
                      (elements.Element, "_SHELL_RECOVERY_BASE_METHODS")):
        expected = getattr(elements, name, None)
        namespace = vars(cls)
        if expected is None or len(namespace) != len(expected) or any(
                namespace.get(key) is not value or key in instance for key, value in expected):
            return False
    helpers = getattr(elements, "_SHELL_RECOVERY_FRAME_HELPERS", None)
    return helpers is not None and all(getattr(elements, key, None) is value for key, value in helpers)


def _legacy_recovery_frames(element: Any, fe_mesh: Any, material: Any) -> Tuple[RecoveryFrame, ...]:
    """The frames ``ShellElement.compute_stresses`` itself uses, read without recovering stresses.

    Mirrors the frame selection of anyfesolver/elements.py ``ShellElement.
    compute_stresses`` (``native_recovery_frames``, else
    ``_local_frame_and_derivatives`` at each of ``gauss_points``) by calling the
    same two solver functions; only eligible elements (see
    ``_solver_frame_eligible``) use it, and a sample is cross-checked with the
    six-field probe.
    """

    from anyfesolver.recovery_shell_frames import native_recovery_frames

    coords = element.get_node_coordinates(fe_mesh)
    points = np.asarray(element.gauss_points, dtype=float)
    native = native_recovery_frames(element, coords, points, material)
    frames = []
    for index, (xi, eta) in enumerate(points):
        if native is None:
            _n, d_xi, d_eta = element.compute_shape_functions(float(xi), float(eta))
            rotation = element._local_frame_and_derivatives(coords, d_xi, d_eta)[0]
        else:
            rotation = native[index][1]
        rotation = np.asarray(rotation, dtype=float).reshape(3, 3)
        frames.append(RecoveryFrame(rotation[:, 0].copy(), rotation[:, 1].copy(),
                                    rotation[:, 2].copy(), (float(xi), float(eta))))
    return tuple(frames)


def _same_frames(left: Sequence[RecoveryFrame], right: Sequence[RecoveryFrame], tolerance: float = 1.0e-9) -> bool:
    """Same output frames: x up to sign (the probe cannot see it), y and normal exactly."""

    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        sign = 1.0 if float(np.dot(a.x_axis, b.x_axis)) >= 0.0 else -1.0
        if not (np.allclose(a.x_axis, sign * b.x_axis, atol=tolerance)
                and np.allclose(a.y_axis, sign * b.y_axis, atol=tolerance)
                and np.allclose(a.normal, b.normal, atol=tolerance)):
            return False
    return True


def project_recovery_frames(
    project: Any,
    mesh: Any,
    *,
    faces: Optional[Iterable[int]] = None,
    cancel: Any = None,
) -> Tuple[Dict[int, ElementAxes], Dict[int, Tuple[int, str]]]:
    """Build the solver model as a solve does and read its shell recovery frames.

    A build refusal makes every shell element referenced by the faces
    unchecked with the build's reason.
    """

    from .solve import build

    face_ids = _face_ids(mesh, faces)
    _check_cancel(cancel)
    try:
        published_mesh_key(project, mesh)
    except UnpublishedMesh as error:
        reason = f"unpublished mesh: {error}"
        return {}, {int(e): (face_id, reason) for face_id in face_ids
                    for e in mesh.elements_of_face[face_id]}
    try:
        built = build.build_fe_model(project, mesh, load_case=None,
                                     require_loads=False, require_supports=False)
    except Exception as error:  # the solve refuses: nothing can be compared
        reason = f"solve build refused: {type(error).__name__}: {error}"
        return {}, {int(e): (face_id, reason) for face_id in face_ids
                    for e in mesh.elements_of_face[face_id]}
    return model_recovery_frames(built.fe_model, mesh, faces=face_ids, cancel=cancel)


def _fold(angle_deg: float) -> float:
    """Angle between two sign-free axes, folded to [0, 90]."""

    return abs((angle_deg + 90.0) % 180.0 - 90.0)


def shell_orientation_report(
    geometry: Any,
    mesh: Any,
    axes: Mapping[int, ElementAxes],
    unchecked: Mapping[int, Tuple[int, str]],
    *,
    faces: Optional[Iterable[int]] = None,
) -> OrientationReport:
    """Classify recovery frames against each geometry face (see the module docstring)."""

    face_ids = _face_ids(mesh, faces)
    results = []
    used: Dict[int, ElementAxes] = {}
    for face_id in face_ids:
        references = [int(e) for e in mesh.elements_of_face[face_id]]
        face_unchecked: Dict[int, str] = {e: reason for e, (f, reason) in unchecked.items()
                                          if f == face_id}
        ids = sorted(e for e in set(references) if e in axes and e not in face_unchecked
                     and axes[e].face_id == face_id)
        for e in references:
            if e not in ids and e not in face_unchecked:
                face_unchecked[e] = "no recovery frames for this element"
        # element id -> (face normal, centroid tangent plane) after projection
        normals: Dict[int, np.ndarray] = {}
        if ids:
            centroids = np.asarray([axes[e].centroid for e in ids])
            try:
                _points, uvs, distances = geometry.project_to_face_many(face_id, centroids)
                du, dv = geometry.face_derivatives_many(face_id, uvs)
            except Exception as error:  # geometry refusal: report, never assume
                for e in ids:
                    face_unchecked[e] = f"face reference: {type(error).__name__}: {error}"
                ids = []
            for row, e in enumerate(ids):
                if not np.isfinite(distances[row]) or distances[row] > _PROJECTION_FRACTION * axes[e].size:
                    face_unchecked[e] = f"centroid {float(distances[row]):.3g} m off the face"
                    continue
                normal = _unit(np.cross(du[row], dv[row]))
                if normal is None:
                    face_unchecked[e] = "face has no usable normal here"
                    continue
                normals[e] = normal
        # Fixed reference: the global axis least aligned with the face normals.
        reference = None
        if normals:
            stack = np.abs(np.asarray(list(normals.values())))
            reference = int(np.argmin(stack.max(axis=0)))
        samples: Dict[int, list] = {}
        for e, face_normal in normals.items():
            axis = np.zeros(3)
            axis[reference] = 1.0
            ref_dir = axis - np.dot(axis, face_normal) * face_normal
            if np.linalg.norm(ref_dir) < _IN_PLANE_LIMIT:
                face_unchecked[e] = f"global {_AXES[reference]} is nearly normal to the face here"
                continue
            ref_dir = ref_dir / np.linalg.norm(ref_dir)
            side_dir = np.cross(face_normal, ref_dir)
            rows = []
            for index, frame in enumerate(axes[e].frames):
                cos_n = float(np.dot(frame.normal, face_normal))
                in_plane = frame.x_axis - np.dot(frame.x_axis, face_normal) * face_normal
                if abs(cos_n) < _NORMAL_COS_LIMIT or np.linalg.norm(in_plane) < _IN_PLANE_LIMIT:
                    rows = None
                    face_unchecked[e] = (f"recovery point {index}: local axes not tangent to the "
                                         f"face (normal cos {cos_n:.2f})")
                    break
                theta = np.arctan2(float(np.dot(in_plane, side_dir)), float(np.dot(in_plane, ref_dir)))
                rows.append((np.array([np.cos(2 * theta), np.sin(2 * theta)]),
                             1 if cos_n > 0.0 else -1))
            if rows is not None:
                samples[e] = rows
        # Dominant direction: mean of the element mean doubled-angle unit vectors.
        element_vectors = []
        for rows in samples.values():
            mean = np.mean([vector for vector, _sign in rows], axis=0)
            length = float(np.linalg.norm(mean))
            element_vectors.append(mean / length if length > 1.0e-9 else np.zeros(2))
        resultant = 0.0
        dominant = None
        mixed: list = []
        if element_vectors:
            total = np.mean(element_vectors, axis=0)
            resultant = float(np.linalg.norm(total))
            if resultant >= MIN_DOMINANT_RESULTANT:
                dominant = 0.5 * float(np.degrees(np.arctan2(total[1], total[0])))
        aligned, turned, ambiguous, inconsistent = [], [], [], []
        normal_sign: Dict[int, int] = {}
        for e, rows in sorted(samples.items()):
            signs = {sign for _vector, sign in rows}
            if dominant is None:
                # Mixed face: no axis class, but the normal is still compared.
                if len(signs) > 1:
                    inconsistent.append(e)
                else:
                    mixed.append(e)
                    normal_sign[e] = signs.pop()
                continue
            classes = set()
            for vector, _sign in rows:
                deviation = _fold(0.5 * float(np.degrees(np.arctan2(vector[1], vector[0]))) - dominant)
                if deviation <= 45.0 - AMBIGUOUS_HALF_BAND_DEG:
                    classes.add(ALIGNED)
                elif deviation >= 45.0 + AMBIGUOUS_HALF_BAND_DEG:
                    classes.add(TURNED)
                else:
                    classes.add(AMBIGUOUS)
            if len(signs) > 1 or {ALIGNED, TURNED} <= classes:
                inconsistent.append(e)
                continue
            if AMBIGUOUS in classes:
                ambiguous.append(e)
            elif TURNED in classes:
                turned.append(e)
            else:
                aligned.append(e)
            normal_sign[e] = signs.pop()
        with_count = sum(1 for s in normal_sign.values() if s > 0)
        against_count = len(normal_sign) - with_count
        majority_sign = 1 if with_count >= against_count else -1
        reversed_ = tuple(sorted(e for e, s in normal_sign.items() if s != majority_sign))
        for e in ids:
            if e not in face_unchecked:
                used[e] = axes[e]
        results.append(FaceOrientation(
            face_id=face_id,
            element_count=len(references),
            reference_axis=None if reference is None else _AXES[reference],
            dominant_angle_deg=dominant,
            dominant_resultant=resultant,
            aligned=tuple(aligned),
            turned=tuple(turned),
            ambiguous=tuple(ambiguous),
            inconsistent=tuple(inconsistent),
            mixed=tuple(mixed),
            normal_with=with_count,
            normal_against=against_count,
            reversed=reversed_,
            unchecked=dict(sorted(face_unchecked.items())),
        ))
    return OrientationReport(tuple(results), used)


def model_orientation_report(geometry: Any, mesh: Any, fe_model: Any, *,
                             faces: Optional[Iterable[int]] = None) -> OrientationReport:
    """Run the check on an already built solver model."""

    faces = None if faces is None else list(faces)
    axes, unchecked = model_recovery_frames(fe_model, mesh, faces=faces)
    return shell_orientation_report(geometry, mesh, axes, unchecked, faces=faces)


def project_orientation_report(project: Any, mesh: Any, *, faces: Optional[Iterable[int]] = None,
                               cancel: Any = None) -> OrientationReport:
    """Run the check on the solver model a static solve of ``project`` would build.

    ``cancel`` is an optional event; when it is set the check raises
    ``OrientationCancelled`` at the next element.
    """

    faces = None if faces is None else list(faces)
    axes, unchecked = project_recovery_frames(project, mesh, faces=faces, cancel=cancel)
    return shell_orientation_report(project.geometry, mesh, axes, unchecked, faces=faces)
