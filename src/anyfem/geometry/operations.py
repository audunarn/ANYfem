"""Compatibility exports for geometry operations now owned by ANYmesher."""

from anymesher.geometry.operations import (
    MappabilityReport,
    check_mappable,
    punch_circular_hole,
    split_face_at,
    split_face_between,
    strip_face,
    surface_point,
    triangle_to_quads,
)

__all__ = [
    "MappabilityReport",
    "check_mappable",
    "punch_circular_hole",
    "split_face_at",
    "split_face_between",
    "strip_face",
    "surface_point",
    "triangle_to_quads",
]
