"""Shell element axis orientation check (direction programme slice D1).

Recovered shell components such as ``membrane_xx`` or ``top_yy`` are reported
in each element's own local axes, and those axes come from the element's node
order: the solver takes local x from the element's first parametric direction
and local z from the winding (see ``anyfesolver.elements.ShellElement.
_local_frame_and_derivatives``).  Two neighbouring elements on one plate can
therefore report "xx" along different physical directions, which shows up as
patches in a contour plot even where the stress field is smooth.

This module measures that.  For every shell element on a geometry face it
computes the local axes the solver uses for that element's formulation, then
compares them with the face itself:

* the in-plane axis against the face's ``u`` direction, folded to 0-90 degrees
  (a 180 degree in-plane turn leaves every tensor component unchanged, a 90
  degree turn swaps xx and yy);
* the element normal against the face's ``du x dv`` normal (a reversed normal
  swaps top and bottom and changes the sign of bending and of xy).

Per face it reports the elements that disagree with the face's majority.  It is
a diagnostic only: it changes no mesh, no element and no result.  Where it
cannot establish an element's axes or the face reference, it lists the element
as unchecked with the reason rather than counting it as consistent.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "AXIS_ALONG_U",
    "AXIS_ACROSS_U",
    "AXIS_OBLIQUE",
    "ElementAxes",
    "FaceOrientation",
    "OrientationReport",
    "element_axes",
    "project_orientation_report",
    "shell_orientation_report",
]

AXIS_ALONG_U = "along_u"
AXIS_ACROSS_U = "across_u"
AXIS_OBLIQUE = "oblique"

# In-plane classification bands, degrees between local x and the face u
# direction folded to [0, 90].  The gap between them is reported as oblique
# rather than forced into a class.
_ALONG_LIMIT_DEG = 30.0
_ACROSS_LIMIT_DEG = 60.0
# A centroid further than this fraction of the element size from the face is
# not trusted to belong to it.
_PROJECTION_FRACTION = 0.25


@dataclass(frozen=True)
class ElementAxes:
    """Local axes of one shell element at its centre, as the solver builds them."""

    element_id: int
    centroid: np.ndarray
    x_axis: np.ndarray
    normal: Optional[np.ndarray]
    formulation: str
    size: float
    # Why the normal is not compared (None when it is).
    normal_note: Optional[str] = None
    # A second candidate local x when the element's x cannot be decided here.
    x_alternative: Optional[np.ndarray] = None


@dataclass(frozen=True)
class FaceOrientation:
    face_id: int
    element_count: int
    axis_classes: Mapping[str, int]
    normal_with: int
    normal_against: int
    # Elements whose in-plane axis class differs from the face majority.
    axis_minority: Tuple[int, ...]
    # Elements whose normal differs from the face majority.
    normal_minority: Tuple[int, ...]
    # element id -> reason it could not be checked.
    unchecked: Mapping[int, str] = field(default_factory=dict)
    # Elements whose normal the solver does not take from the winding.
    normal_not_compared: Tuple[int, ...] = ()

    @property
    def consistent(self) -> bool:
        return not (self.axis_minority or self.normal_minority or self.unchecked)


@dataclass(frozen=True)
class OrientationReport:
    faces: Tuple[FaceOrientation, ...]

    @property
    def axis_minority(self) -> Tuple[int, ...]:
        return tuple(sorted(e for face in self.faces for e in face.axis_minority))

    @property
    def normal_minority(self) -> Tuple[int, ...]:
        return tuple(sorted(e for face in self.faces for e in face.normal_minority))

    @property
    def unchecked(self) -> Dict[int, str]:
        return {e: reason for face in self.faces for e, reason in face.unchecked.items()}

    @property
    def consistent(self) -> bool:
        return all(face.consistent for face in self.faces)

    def summary(self) -> str:
        checked = sum(face.element_count - len(face.unchecked) for face in self.faces)
        lines = [
            f"Shell axis check: {checked} elements on {len(self.faces)} faces; "
            f"{len(self.axis_minority)} with local x turned against their face, "
            f"{len(self.normal_minority)} with reversed normal, "
            f"{len(self.unchecked)} unchecked."
        ]
        for face in self.faces:
            if face.consistent:
                continue
            classes = ", ".join(f"{name} {count}" for name, count in sorted(face.axis_classes.items()))
            lines.append(
                f"  face {face.face_id}: {face.element_count} elements ({classes}); "
                f"normal with/against {face.normal_with}/{face.normal_against}; "
                f"axis minority {len(face.axis_minority)}, normal minority "
                f"{len(face.normal_minority)}, unchecked {len(face.unchecked)}"
            )
        return "\n".join(lines)


def _unit(vector: np.ndarray) -> Optional[np.ndarray]:
    length = float(np.linalg.norm(vector))
    if not np.isfinite(length) or length <= 1.0e-14:
        return None
    return vector / length


def _shape_derivatives_at_centre(count: int) -> Tuple[np.ndarray, np.ndarray]:
    """dN/dxi and dN/deta at the element centre, solver node conventions."""

    if count == 8:
        # Serendipity, corners (-1,-1),(1,-1),(1,1),(-1,1) then mid-sides of
        # edges 0-1, 1-2, 2-3, 3-0.  At the centre only mid-sides contribute.
        dxi = np.array([0, 0, 0, 0, 0, .5, 0, -.5], dtype=float)
        deta = np.array([0, 0, 0, 0, -.5, 0, .5, 0], dtype=float)
        return dxi, deta
    if count == 4:
        dxi = np.array([-1, 1, 1, -1], dtype=float) / 4.0
        deta = np.array([-1, -1, 1, 1], dtype=float) / 4.0
        return dxi, deta
    if count == 6:
        # anyfesolver _jit_compute_6node_shape_functions at r = s = 1/3.
        l1 = l2 = l3 = 1.0 / 3.0
        dxi = np.array([1 - 4 * l1, 4 * l2 - 1, 0, 4 * (l1 - l2), 4 * l3, -4 * l3])
        deta = np.array([1 - 4 * l1, 0, 4 * l3 - 1, -4 * l2, 4 * l2, 4 * (l1 - l3)])
        return dxi, deta
    if count == 3:
        return np.array([-1.0, 1.0, 0.0]), np.array([-1.0, 0.0, 1.0])
    raise ValueError(f"unsupported shell node count {count}")


def _legacy_axes(coords: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """ShellElement._local_frame_and_derivatives at the element centre."""

    dxi, deta = _shape_derivatives_at_centre(len(coords))
    t_xi, t_eta = coords.T @ dxi, coords.T @ deta
    e3 = _unit(np.cross(t_xi, t_eta))
    if e3 is None:
        raise ValueError("near-zero surface Jacobian at the element centre")
    e1 = _unit(t_xi - np.dot(t_xi, e3) * e3)
    if e1 is None:
        raise ValueError("first parametric direction is parallel to the normal")
    e2 = _unit(np.cross(e3, e1))
    e1 = _unit(np.cross(e2, e3))
    return e1, e3


def element_axes(
    mesh: Any,
    element_id: int,
    policy: Any = None,
    s3_reference_normals: Optional[Mapping[int, Any]] = None,
) -> ElementAxes:
    """Return the local axes the solver uses for one mesh shell element.

    ``policy`` is the project's ``ShellFormulationPolicy``; without one the
    current default policy is assumed.  ``s3_reference_normals`` are the
    mesher's reference normals for V2D triangles (as the solve build uses
    them); without them a V2D triangle's normal is not compared and its local
    x is one of two candidates (see ``ElementAxes.x_alternative``).  Raises
    ``ValueError`` when the axes cannot be established.
    """

    if policy is None:
        from .model.formulations import ShellFormulationPolicy

        policy = ShellFormulationPolicy.current_default()
    if element_id in mesh.quads:
        node_ids = mesh.quads[element_id]
    elif element_id in mesh.tris:
        node_ids = mesh.tris[element_id]
    else:
        raise ValueError(f"element {element_id} is not a shell element")
    coords = np.asarray([mesh.nodes[n] for n in node_ids], dtype=float)
    corners = coords[: 4 if len(node_ids) in (4, 8) else 3]
    centroid = corners.mean(axis=0)
    size = float(max(np.linalg.norm(a - b) for a in corners for b in corners))
    token = policy.for_node_count(len(node_ids))
    note = None
    alternative = None
    if len(node_ids) == 4 and token == "e4-pl":
        from anyfesolver.e4_pl_element import equation7_frame

        frame, _local, _warp = equation7_frame(coords)
        x_axis, normal = frame[:, 0], frame[:, 2]
    elif len(node_ids) == 3 and token == "e4-pl-s3-v2d":
        # The V2D triangle takes its normal from the mesher's reference normal
        # and reorders the nodes to agree with it, so local x is the edge from
        # node 0 to node 1 when the winding agrees and to node 2 when it does
        # not (anyfesolver.e4_pl_s3_v2d_element, stress geometry).
        facet = _unit(np.cross(coords[1] - coords[0], coords[2] - coords[0]))
        if facet is None:
            raise ValueError("degenerate triangle")
        reference = None if s3_reference_normals is None else s3_reference_normals.get(int(element_id))
        if reference is None:
            # Unknown reference: the caller compares both candidate edges.
            x_axis = _unit(coords[1] - coords[0])
            normal = facet
            note = "S3 V2D reference normal not supplied"
            alternative = _unit(coords[2] - coords[0])
        else:
            normal = _unit(np.asarray(reference, dtype=float))
            if normal is None:
                raise ValueError("S3 reference normal is degenerate")
            x_axis = _unit(coords[1] - coords[0]) if float(np.dot(facet, normal)) > 0.0 \
                else _unit(coords[2] - coords[0])
    else:
        x_axis, normal = _legacy_axes(coords)
    if x_axis is None or normal is None:
        raise ValueError("local axes are degenerate")
    return ElementAxes(int(element_id), centroid, x_axis, normal, token, size, note, alternative)


def _face_ids(mesh: Any, faces: Optional[Iterable[int]]) -> Sequence[int]:
    known = sorted(int(face) for face in mesh.elements_of_face)
    if faces is None:
        return known
    wanted = sorted({int(face) for face in faces})
    missing = [face for face in wanted if face not in mesh.elements_of_face]
    if missing:
        raise ValueError(f"faces {missing} have no mesh elements")
    return wanted


def shell_orientation_report(
    geometry: Any,
    mesh: Any,
    *,
    policy: Any = None,
    faces: Optional[Iterable[int]] = None,
    s3_reference_normals: Optional[Mapping[int, Any]] = None,
) -> OrientationReport:
    """Compare every shell element's local axes with its geometry face."""

    def classify(vector, face_normal, u_dir):
        in_plane = _unit(vector - np.dot(vector, face_normal) * face_normal)
        if in_plane is None:
            return None
        angle = float(np.degrees(np.arccos(min(1.0, abs(float(np.dot(in_plane, u_dir)))))))
        if angle <= _ALONG_LIMIT_DEG:
            return AXIS_ALONG_U
        if angle >= _ACROSS_LIMIT_DEG:
            return AXIS_ACROSS_U
        return AXIS_OBLIQUE

    results = []
    for face_id in _face_ids(mesh, faces):
        element_ids = [int(e) for e in mesh.elements_of_face[face_id]
                       if int(e) in mesh.quads or int(e) in mesh.tris]
        unchecked: Dict[int, str] = {}
        axes: Dict[int, ElementAxes] = {}
        for element_id in element_ids:
            try:
                axes[element_id] = element_axes(mesh, element_id, policy, s3_reference_normals)
            except (ValueError, KeyError) as error:
                unchecked[element_id] = f"element axes: {error}"
        classes: Dict[int, str] = {}
        normal_sign: Dict[int, int] = {}
        not_compared = []
        if axes:
            ids = sorted(axes)
            centroids = np.asarray([axes[e].centroid for e in ids])
            try:
                _points, uvs, distances = geometry.project_to_face_many(face_id, centroids)
                du, dv = geometry.face_derivatives_many(face_id, uvs)
            except Exception as error:  # geometry refusal: report, never assume
                for e in ids:
                    unchecked[e] = f"face reference: {type(error).__name__}: {error}"
                ids = []
            for row, e in enumerate(ids):
                item = axes[e]
                if not np.isfinite(distances[row]) or distances[row] > _PROJECTION_FRACTION * item.size:
                    unchecked[e] = f"centroid {float(distances[row]):.3g} m off the face"
                    continue
                face_normal = _unit(np.cross(du[row], dv[row]))
                u_dir = None if face_normal is None else _unit(
                    du[row] - np.dot(du[row], face_normal) * face_normal)
                if u_dir is None:
                    unchecked[e] = "face has no usable u direction or normal here"
                    continue
                name = classify(item.x_axis, face_normal, u_dir)
                if name is None:
                    unchecked[e] = "local x is normal to the face"
                    continue
                if item.x_alternative is not None:
                    other = classify(item.x_alternative, face_normal, u_dir)
                    if other != name:
                        unchecked[e] = (f"{item.normal_note}: local x is {name} or {other}"
                                        " depending on it")
                        continue
                classes[e] = name
                if item.normal_note is not None:
                    not_compared.append(e)
                else:
                    normal_sign[e] = 1 if float(np.dot(item.normal, face_normal)) > 0.0 else -1
        counts = Counter(classes.values())
        axis_minority: Tuple[int, ...] = ()
        if counts:
            # Ties go to along_u so the result is deterministic.
            majority = max(counts, key=lambda name: (counts[name], name == AXIS_ALONG_U))
            axis_minority = tuple(sorted(e for e, name in classes.items() if name != majority))
        with_count = sum(1 for s in normal_sign.values() if s > 0)
        against_count = len(normal_sign) - with_count
        majority_sign = 1 if with_count >= against_count else -1
        normal_minority = tuple(sorted(e for e, s in normal_sign.items() if s != majority_sign))
        results.append(FaceOrientation(
            face_id=face_id,
            element_count=len(element_ids),
            axis_classes=dict(counts),
            normal_with=with_count,
            normal_against=against_count,
            axis_minority=axis_minority,
            normal_minority=normal_minority,
            unchecked=dict(sorted(unchecked.items())),
            normal_not_compared=tuple(sorted(not_compared)),
        ))
    return OrientationReport(tuple(results))


def project_orientation_report(project: Any, mesh: Any, *, faces: Optional[Iterable[int]] = None) -> OrientationReport:
    """Run the check with the project's formulation policy and S3 reference normals.

    The S3 reference normals are read exactly as the solve build reads them;
    if that refuses, the refusal propagates rather than the check silently
    falling back to winding normals.
    """

    policy = project.shell_formulation_policy
    normals = None
    if policy.s3 == "e4-pl-s3-v2d" and any(len(nodes) == 3 for nodes in mesh.tris.values()):
        from .solve.build import _qualified_s3_owner_normals

        _ids, normals = _qualified_s3_owner_normals(project, mesh)
    return shell_orientation_report(project.geometry, mesh, policy=policy, faces=faces,
                                    s3_reference_normals=normals)
