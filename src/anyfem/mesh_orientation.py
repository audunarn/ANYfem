"""Shell element axis orientation check (direction programme slice D1).

Recovered shell components such as ``membrane_xx`` or ``top_yy`` are reported
in each element's own local axes, and those axes come from the element's node
order.  Two neighbouring elements on one plate can therefore report "xx" along
different physical directions, which shows up as patches in a contour plot even
where the stress field is smooth.

This module measures that, without re-deriving any solver frame:

1. Every shell element is constructed exactly as the solve constructs it
   (``anyfem.solve.build._shell_element``: the project's formulation policy,
   the S3 owner normals after the solve's admission check, the faceted S3
   reference normal, the formulation identity checks).  An element the solve
   would refuse is *unchecked* with the solve's reason.
2. The element's own ``compute_stresses`` is called with six uniform strain
   fields ``u = E x`` (``E`` running over a basis of symmetric tensors, zero
   rotations) and a probe material with E = 1 and nu = 0.  At every recovery
   point the solver then reports ``membrane_ab = e_a . E . e_b`` in its output
   frame ``(e_x, e_y)``, so the six calls give that frame directly, including
   any fallback the element takes (a warped qualified Q4 recovers through
   ``ShellElement.compute_stresses`` with a frame per Gauss point; a V2D
   triangle uses its reference normal, faceted when the policy says so).  If
   the response is not that of a rotated orthonormal frame the element is
   unchecked.  The output frame does not depend on the material for the
   elements the build creates (ANYfem sets no ``material_direction`` or
   ``material_angle_deg``; an orthotropic material turns only the material
   axes, see D0).
3. Per geometry face, local x is measured in the face frame at the element
   centroid's projection (``u`` = projected ``du``, normal ``du x dv``) as a
   doubled angle, so x and -x agree.  The face's dominant direction is the mean
   of the element doubled-angle unit vectors; each recovery point is then
   ``aligned`` (within 45 - 10 deg of it, folded to 0-90), ``turned`` (beyond
   45 + 10 deg: xx and yy exchanged) or ``ambiguous`` (in between).  This does
   not depend on how the face is parameterised.  An element is turned or
   ambiguous when its points say so; an element whose points disagree (aligned
   and turned, or normals on both sides) is ``inconsistent`` - a folded or
   strongly curved element.  A face whose resultant is too small to name a
   dominant direction has all its elements unchecked.

   Curved faces: the reference is the face's own ``u`` direction at each
   centroid, so the dominant direction is a constant angle *relative to the
   parameter lines*.  That is a physical reference only where the parameter
   lines are; near a degenerate point (``du`` or ``du x dv`` vanishing) the
   element is unchecked.  Recovery frames are compared with the face frame at
   the centroid, so on a face curved strongly within one element the face frame
   turn across the element adds to the measured angle.
4. The normal at every recovery point is compared with the face normal; the
   face's majority side is the reference.  Reversing the normal with x kept
   flips e_y: ``membrane_xy``, ``bending_xx`` and ``bending_yy`` change sign,
   ``bending_xy`` and ``membrane_xx/yy`` do not, and top and bottom swap.

The report never calls anything consistent that it did not fully compare:
unchecked, ambiguous and inconsistent elements all make a face inconsistent
and are counted in ``summary()``.  It is a diagnostic only: it changes no mesh,
no element and no result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "ALIGNED",
    "AMBIGUOUS",
    "INCONSISTENT",
    "TURNED",
    "ElementAxes",
    "FaceOrientation",
    "OrientationReport",
    "RecoveryFrame",
    "element_recovery_frames",
    "project_orientation_report",
    "project_recovery_frames",
    "shell_orientation_report",
]

ALIGNED = "aligned"
TURNED = "turned"
AMBIGUOUS = "ambiguous"
INCONSISTENT = "inconsistent"

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
# Smallest in-plane length of local x after projection onto the face.
_IN_PLANE_LIMIT = 0.5

_PAIRS = ((0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2))


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
    formulation: str
    element_class: str
    centroid: np.ndarray
    size: float
    frames: Tuple[RecoveryFrame, ...]

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
    # Dominant local-x angle from the face u direction, degrees in (-90, 90];
    # None when no dominant direction exists.
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
        return not (self.turned or self.ambiguous or self.inconsistent
                    or self.reversed or self.unchecked)


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

    # Names kept from the first D1 version.
    axis_minority = turned
    normal_minority = reversed

    @property
    def unchecked(self) -> Dict[int, str]:
        return {e: reason for face in self.faces for e, reason in face.unchecked.items()}

    @property
    def consistent(self) -> bool:
        return all(face.consistent for face in self.faces)

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
            f"{len(unchecked)} unchecked."
        ]
        for face in self.faces:
            if face.consistent:
                continue
            angle = ("no dominant direction" if face.dominant_angle_deg is None
                     else f"dominant x {face.dominant_angle_deg:.1f} deg from u")
            lines.append(
                f"  face {face.face_id}: {face.element_count} elements, {angle} "
                f"(resultant {face.dominant_resultant:.2f}); aligned {len(face.aligned)}, "
                f"turned {len(face.turned)}, ambiguous {len(face.ambiguous)}, "
                f"inconsistent {len(face.inconsistent)}; normal with/against "
                f"{face.normal_with}/{face.normal_against}, reversed {len(face.reversed)}; "
                f"unchecked {len(face.unchecked)}"
            )
            reasons: Dict[str, int] = {}
            for reason in face.unchecked.values():
                reasons[reason] = reasons.get(reason, 0) + 1
            for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[:3]:
                lines.append(f"    unchecked {count}: {reason}")
        return "\n".join(lines)


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

    Raises ``ValueError`` when the element's response is not that of an
    orthonormal output frame (the element is then unchecked, never guessed).
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


def project_recovery_frames(
    project: Any,
    mesh: Any,
    *,
    faces: Optional[Iterable[int]] = None,
) -> Tuple[Dict[int, ElementAxes], Dict[int, Tuple[int, str]]]:
    """Construct every shell element as the solve does and read its recovery frames.

    Returns ``(axes, unchecked)``; ``unchecked`` maps element id to
    ``(face id, reason)``.  Element ids referenced by a face but missing from
    the mesh, elements the solve would refuse, and V2D triangles whose owner
    normals the solve would refuse are all unchecked.
    """

    import warnings

    from anyfesolver.fe_core import FEMesh

    from .solve import build

    face_ids = _face_ids(mesh, faces)
    owner_normals: Mapping[int, Any] = {}
    s3_refusal = None
    try:
        owner_normals = build._admitted_s3_owner_normals(project, mesh)
    except Exception as error:  # the solve refuses: every V2D triangle is unchecked
        s3_refusal = f"S3 owner normals refused by the solve build: {error}"
    material = _probe_material()
    axes: Dict[int, ElementAxes] = {}
    unchecked: Dict[int, Tuple[int, str]] = {}
    seen: Dict[int, int] = {}
    for face_id in face_ids:
        section_error = None
        try:
            section = project.plate_section_of(face_id)
        except Exception as error:
            section_error = f"plate section refused: {error}"
        for raw in mesh.elements_of_face[face_id]:
            element_id = int(raw)
            if element_id in seen:
                unchecked[element_id] = (face_id, f"element also on face {seen[element_id]}")
                axes.pop(element_id, None)
                continue
            seen[element_id] = face_id
            node_ids = mesh.quads.get(element_id, mesh.tris.get(element_id))
            if node_ids is None:
                unchecked[element_id] = (face_id, "face references a missing shell element")
                continue
            if section_error is not None:
                unchecked[element_id] = (face_id, section_error)
                continue
            formulation = project.shell_formulation_policy.for_node_count(len(node_ids))
            if formulation == "e4-pl-s3-v2d" and s3_refusal is not None:
                unchecked[element_id] = (face_id, s3_refusal)
                continue
            try:
                element = build._shell_element(project, mesh, section, face_id, element_id,
                                               owner_normals)
                fe_mesh = FEMesh()
                for n in node_ids:
                    x, y, z = mesh.nodes[n]
                    fe_mesh.add_node(int(n), float(x), float(y), float(z))
                fe_mesh.add_element(element_id, element)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    frames = element_recovery_frames(element, fe_mesh, material)
            except Exception as error:  # solver refusal: report, never assume
                unchecked[element_id] = (face_id, f"{type(error).__name__}: {error}")
                continue
            coords = np.asarray([mesh.nodes[n] for n in node_ids], dtype=float)
            corners = coords[: 4 if len(node_ids) in (4, 8) else 3]
            axes[element_id] = ElementAxes(
                element_id=element_id,
                face_id=face_id,
                formulation=formulation,
                element_class=type(element).__name__,
                centroid=corners.mean(axis=0),
                size=float(max(np.linalg.norm(a - b) for a in corners for b in corners)),
                frames=frames,
            )
    return axes, unchecked


def _face_ids(mesh: Any, faces: Optional[Iterable[int]]) -> Sequence[int]:
    known = sorted(int(face) for face in mesh.elements_of_face)
    if faces is None:
        return known
    wanted = sorted({int(face) for face in faces})
    missing = [face for face in wanted if face not in mesh.elements_of_face]
    if missing:
        raise ValueError(f"faces {missing} have no mesh elements")
    return wanted


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
        # element id -> list of (doubled-angle unit vector, normal sign) per point
        samples: Dict[int, list] = {}
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
                item = axes[e]
                if not np.isfinite(distances[row]) or distances[row] > _PROJECTION_FRACTION * item.size:
                    face_unchecked[e] = f"centroid {float(distances[row]):.3g} m off the face"
                    continue
                face_normal = _unit(np.cross(du[row], dv[row]))
                u_dir = None if face_normal is None else _unit(
                    du[row] - np.dot(du[row], face_normal) * face_normal)
                if u_dir is None:
                    face_unchecked[e] = "face has no usable u direction or normal here"
                    continue
                v_dir = np.cross(face_normal, u_dir)
                rows = []
                for index, frame in enumerate(item.frames):
                    cos_n = float(np.dot(frame.normal, face_normal))
                    in_plane = frame.x_axis - np.dot(frame.x_axis, face_normal) * face_normal
                    if abs(cos_n) < _NORMAL_COS_LIMIT or np.linalg.norm(in_plane) < _IN_PLANE_LIMIT:
                        rows = None
                        face_unchecked[e] = (f"recovery point {index}: local axes not tangent to the "
                                             f"face (normal cos {cos_n:.2f})")
                        break
                    theta = np.arctan2(float(np.dot(in_plane, v_dir)), float(np.dot(in_plane, u_dir)))
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
        if element_vectors:
            total = np.mean(element_vectors, axis=0)
            resultant = float(np.linalg.norm(total))
            if resultant >= MIN_DOMINANT_RESULTANT:
                dominant = 0.5 * float(np.degrees(np.arctan2(total[1], total[0])))
            else:
                for e in samples:
                    face_unchecked[e] = (f"face has no dominant local-x direction "
                                         f"(resultant {resultant:.2f} < {MIN_DOMINANT_RESULTANT})")
                samples = {}
        aligned, turned, ambiguous, inconsistent = [], [], [], []
        normal_sign: Dict[int, int] = {}
        for e, rows in sorted(samples.items()):
            classes = set()
            for vector, _sign in rows:
                deviation = _fold(0.5 * float(np.degrees(np.arctan2(vector[1], vector[0]))) - dominant)
                if deviation <= 45.0 - AMBIGUOUS_HALF_BAND_DEG:
                    classes.add(ALIGNED)
                elif deviation >= 45.0 + AMBIGUOUS_HALF_BAND_DEG:
                    classes.add(TURNED)
                else:
                    classes.add(AMBIGUOUS)
            signs = {sign for _vector, sign in rows}
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
            dominant_angle_deg=dominant,
            dominant_resultant=resultant,
            aligned=tuple(aligned),
            turned=tuple(turned),
            ambiguous=tuple(ambiguous),
            inconsistent=tuple(inconsistent),
            normal_with=with_count,
            normal_against=against_count,
            reversed=reversed_,
            unchecked=dict(sorted(face_unchecked.items())),
        ))
    return OrientationReport(tuple(results), used)


def project_orientation_report(project: Any, mesh: Any, *, faces: Optional[Iterable[int]] = None) -> OrientationReport:
    """Run the check on the elements the solve would build for ``project``."""

    axes, unchecked = project_recovery_frames(project, mesh, faces=faces)
    return shell_orientation_report(project.geometry, mesh, axes, unchecked, faces=faces)
