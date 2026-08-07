"""Geometry kernel: points, lines, faces and the operations on them."""

from .curves import Arc, ArcFrame, CurveShape, DegenerateArcError, Straight, arc_frame
from .entities import Edge, EntityKind, EntityRef, Face, OrientedEdge, Vertex
from .model import GeometryError, GeometryModel
from .operations import (
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
    "Arc",
    "ArcFrame",
    "CurveShape",
    "DegenerateArcError",
    "Edge",
    "EntityKind",
    "EntityRef",
    "Face",
    "GeometryError",
    "GeometryModel",
    "MappabilityReport",
    "OrientedEdge",
    "Straight",
    "Vertex",
    "arc_frame",
    "check_mappable",
    "punch_circular_hole",
    "split_face_at",
    "split_face_between",
    "strip_face",
    "surface_point",
    "triangle_to_quads",
]
