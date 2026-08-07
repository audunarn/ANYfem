"""Compatibility exports for topology entities now owned by ANYmesher."""

from anymesher.geometry.entities import (
    Edge,
    EntityKind,
    EntityRef,
    Face,
    OrientedEdge,
    Vertex,
)

__all__ = ["Edge", "EntityKind", "EntityRef", "Face", "OrientedEdge", "Vertex"]
