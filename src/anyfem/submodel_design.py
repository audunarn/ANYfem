"""Box selection and reversible changes to the editable child of a submodel."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from itertools import product

import numpy as np

from .commands import Command
from .document import canonical_hash
from .model.submodels import SubmodelDefinition
from .submodel import SubmodelError


def solid_box_footprint(parent, face_id, lower, upper):
    """Return the rectangular full-thickness part of a plate inside a world-space box.

    Each midsurface point must have room for both skins. Intersect the plate
    plane with the box inset by the projection of half the plate thickness.
    ANYgeometry checks the resulting polygon against the actual trimmed face.
    """
    from anygeometry.shell_detail import derive_shell_detail_box, ShellDetailError

    geometry = parent.geometry
    low, high = np.asarray(lower, dtype=float), np.asarray(upper, dtype=float)
    if (low.shape != (3,) or high.shape != (3,) or not np.isfinite([low, high]).all()
            or np.any(low >= high)):
        raise SubmodelError("CUT_BOX_BOUNDS", "Enter finite box corners with min below max on every axis.")
    if face_id not in geometry.faces:
        raise SubmodelError("CUT_FACE_MISSING", "Select a plate in the parent project.")
    centre = low + (high - low) * 0.5
    point, uv, _ = geometry.project_to_face(face_id, centre)
    normal = np.asarray(geometry.face_normal(face_id, *uv), dtype=float)
    normal /= np.linalg.norm(normal)
    thickness = float(parent.plate_section_of(face_id).thickness)
    clearance = abs(normal) * (0.5 * thickness)
    tolerance = geometry.tolerance.effective_length(float(np.linalg.norm(high - low)))
    low, high = low + clearance, high - clearance
    if np.any(low > high + tolerance):
        raise SubmodelError("CUT_BOX_THICKNESS", "Expand the box to include both skins of the selected plate.")
    corners = np.array(list(product(*zip(low, high))))
    distances = (corners - point) @ normal
    vertices = []

    def append(position):
        if all(np.linalg.norm(position - previous) > tolerance for previous in vertices):
            vertices.append(position)

    for i, distance in enumerate(distances):
        if abs(distance) <= tolerance:
            append(corners[i] - distance * normal)
        for bit in (1, 2, 4):
            j = i ^ bit
            crosses_plane = ((distance < -tolerance and distances[j] > tolerance)
                             or (distances[j] < -tolerance and distance > tolerance))
            if j > i and crosses_plane:
                fraction = distance / (distance - distances[j])
                append(corners[i] + fraction * (corners[j] - corners[i]))

    if len(vertices) < 3:
        raise SubmodelError("CUT_BOX_MISSES_PLATE", "The box contains no full-thickness area of this plate.")
    if len(vertices) != 4:
        raise SubmodelError("CUT_BOX_FOOTPRINT", "The box must cut a rectangular detail on the selected plate.")
    points = np.asarray(vertices)
    radial = points - points.mean(axis=0)
    along = radial[0] / np.linalg.norm(radial[0])
    angles = np.arctan2(radial @ np.cross(normal, along), radial @ along)
    footprint = points[np.argsort(angles)]
    try:
        derive_shell_detail_box(geometry, face_id, footprint, director=normal, thickness=thickness,
                                reference_offset=0.0)
    except ShellDetailError as error:
        raise SubmodelError(error.code, str(error)) from error
    return tuple(tuple(float(c) for c in corner) for corner in footprint)


def _record_hash(definition):
    return canonical_hash(definition.to_dict()) if definition is not None else None


class _ChangeSubmodelDefinition(Command):
    """Compare-and-swap the serialized definition, leaving run records in place."""

    def __init__(self, previous, replacement):
        self.definition_id = previous.id
        self.before = deepcopy(previous.to_dict())
        self.after = deepcopy(replacement.to_dict()) if replacement is not None else None

    def _install(self, project, expected, updated):
        current = project.submodels.get(self.definition_id)
        if _record_hash(current) != (canonical_hash(expected) if expected is not None else None):
            raise SubmodelError("SUBMODEL_EDIT_CONFLICT", "The submodel changed. Reopen it before applying edits.")
        if updated is None:
            del project.submodels[self.definition_id]
            return None
        definition = SubmodelDefinition.from_dict(deepcopy(updated))
        project.submodels[self.definition_id] = definition
        return definition

    def do(self, project):
        return self._install(project, self.before, self.after)

    def undo(self, project):
        self._install(project, self.after, self.before)


class ReplaceSubmodelDefinition(_ChangeSubmodelDefinition):
    label = "edit submodel"

    def __init__(self, previous, replacement):
        if type(previous) is not type(replacement) or previous.source != replacement.source:
            raise SubmodelError("SUBMODEL_EDIT_SOURCE", "The child type, source job and case must match.")
        super().__init__(previous, replace(replacement, id=previous.id))


class RemoveSubmodelDefinition(_ChangeSubmodelDefinition):
    label = "delete submodel"

    def __init__(self, previous):
        super().__init__(previous, None)
