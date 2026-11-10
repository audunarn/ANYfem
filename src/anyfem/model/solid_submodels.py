"""Persisted shell-driven *solid* submodel definition (child_kind ``'solid'``).

Unlike the shell :class:`~anyfem.model.submodels.SubmodelDefinition` this record
carries no child project.  It stores only what is needed to rebuild the solid
detail deterministically from the parent: the immutable identity of the source
geometry, the exact source face, the validated four-point footprint, sizing,
layers, element order, the explicit pressure skin and the reconstruction inputs
(director, thickness, offset, section and material identity, retained pressure).

The record is immutable (``frozen``); ``validate()`` is a pure check.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional, Tuple
from uuid import uuid4

from .submodels import SourceReference, SubmodelRecordError, _finite, _mapping, _schema, _text

__all__ = [
    "CHILD_KIND",
    "SCHEMA_VERSION",
    "SKINS",
    "SolidSubmodelDefinition",
    "solid_definition_hash",
]

CHILD_KIND = "solid"
SCHEMA_VERSION = 1
ORDERS = (1, 2)
SKINS = ("start", "end")
_RELATIVE = 1.0e-6  # footprint rectangle shape tolerance, relative to its longest side
_GEOMETRY_KEYS = ("model_id", "revision", "checksum")
_KEYS = frozenset(("child_kind", "schema_version", "id", "name", "source", "child_case", "face_id", "footprint",
                   "target_size", "layers", "order", "pressure_skin", "geometry", "reconstruction"))
_RECONSTRUCTION_KEYS = ("director", "thickness", "reference_offset", "section", "material", "pressure",
                        "boundary_tolerance")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(v) for v in value]
    return value


def _integer(value: Any, context: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SubmodelRecordError(f"{context} must be an integer >= {minimum}")
    return int(value)


def _point(value: Any, context: str) -> Tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise SubmodelRecordError(f"{context} must be three coordinates")
    return tuple(_finite(v, context) for v in value)  # type: ignore[return-value]


def _vector_problem(footprint: Tuple[Tuple[float, ...], ...]) -> Optional[str]:
    """Why four points are not a non-degenerate rectangle (None when they are)."""

    points = [tuple(p) for p in footprint]
    edges = [tuple(points[(i + 1) % 4][k] - points[i][k] for k in range(3)) for i in range(4)]
    lengths = [math.sqrt(sum(c * c for c in e)) for e in edges]
    longest = max(lengths)
    if not longest > 0.0 or min(lengths) <= _RELATIVE * longest:
        return "has a degenerate or repeated corner"
    for i in range(4):
        a, b = edges[i], edges[(i + 1) % 4]
        if abs(sum(x * y for x, y in zip(a, b))) > _RELATIVE * lengths[i] * lengths[(i + 1) % 4]:
            return "corners are not right angles"
    closure = [points[0][k] + points[2][k] - points[1][k] - points[3][k] for k in range(3)]
    if math.sqrt(sum(c * c for c in closure)) > _RELATIVE * longest:
        return "opposite edges differ"
    if abs(lengths[0] - lengths[2]) > _RELATIVE * longest or abs(lengths[1] - lengths[3]) > _RELATIVE * longest:
        return "opposite edges differ in length"
    return None


@dataclass(frozen=True)
class SolidSubmodelDefinition:
    """One shell-driven solid detail: source identity, footprint, sizing, order, skin."""

    name: str
    source: SourceReference
    face_id: int
    footprint: Tuple[Tuple[float, float, float], ...]
    target_size: float
    layers: int
    geometry: Mapping[str, Any]  # source geometry identity: model_id, revision, checksum
    reconstruction: Mapping[str, Any]
    order: int = 1
    pressure_skin: Optional[str] = None
    child_case: str = ""
    id: str = field(default_factory=lambda: str(uuid4()))
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Normalise to immutable plain values; ``validate`` checks them.
        object.__setattr__(self, "footprint", tuple(
            tuple(float(c) if not isinstance(c, bool) and isinstance(c, (int, float)) else c for c in p)
            if isinstance(p, (list, tuple)) else p for p in self.footprint)
            if isinstance(self.footprint, (list, tuple)) else self.footprint)
        if not self.child_case and isinstance(self.source, SourceReference):
            object.__setattr__(self, "child_case", self.source.case)
        object.__setattr__(self, "geometry", _freeze(self.geometry))
        object.__setattr__(self, "reconstruction", _freeze(self.reconstruction))

    @property
    def child_kind(self) -> str:
        return CHILD_KIND

    # ------------------------------------------------------------------
    def validate(self) -> None:
        _text(self.id, "solid submodel.id")
        if not isinstance(self.name, str):
            raise SubmodelRecordError("solid submodel.name must be a string")
        if not isinstance(self.source, SourceReference):
            raise SubmodelRecordError("solid submodel.source must be a SourceReference")
        _text(self.child_case, "solid submodel.child_case")
        if self.schema_version != SCHEMA_VERSION:
            raise SubmodelRecordError(f"solid submodel.schema_version must be {SCHEMA_VERSION}")
        _integer(self.face_id, "solid submodel.face_id", 0)
        if not isinstance(self.footprint, tuple) or len(self.footprint) != 4:
            raise SubmodelRecordError("solid submodel.footprint must be four xyz points")
        points = tuple(_point(p, "solid submodel.footprint") for p in self.footprint)
        problem = _vector_problem(points)
        if problem is not None:
            raise SubmodelRecordError(f"solid submodel.footprint {problem}")
        if _finite(self.target_size, "solid submodel.target_size") <= 0.0:
            raise SubmodelRecordError("solid submodel.target_size must be positive")
        _integer(self.layers, "solid submodel.layers", 1)
        if isinstance(self.order, bool) or self.order not in ORDERS:
            raise SubmodelRecordError(f"solid submodel.order must be one of {ORDERS}")
        if self.pressure_skin is not None and self.pressure_skin not in SKINS:
            raise SubmodelRecordError(f"solid submodel.pressure_skin must be one of {SKINS} or None")
        geometry = _mapping(self.geometry, "solid submodel.geometry")
        if set(geometry) != set(_GEOMETRY_KEYS):
            raise SubmodelRecordError(f"solid submodel.geometry must have exactly {_GEOMETRY_KEYS}")
        _text(geometry["model_id"], "solid submodel.geometry.model_id")
        _integer(geometry["revision"], "solid submodel.geometry.revision", 0)
        _text(geometry["checksum"], "solid submodel.geometry.checksum")
        rec = _mapping(self.reconstruction, "solid submodel.reconstruction")
        missing = [key for key in _RECONSTRUCTION_KEYS if key not in rec]
        if missing:
            raise SubmodelRecordError(f"solid submodel.reconstruction lacks {missing}")
        extra = sorted(set(rec) - set(_RECONSTRUCTION_KEYS))
        if extra:
            raise SubmodelRecordError(f"solid submodel.reconstruction has unknown fields {extra}")
        director = _point(rec["director"], "solid submodel.reconstruction.director")
        if abs(math.sqrt(sum(c * c for c in director)) - 1.0) > 1.0e-9:
            raise SubmodelRecordError("solid submodel.reconstruction.director must be a unit vector")
        if _finite(rec["thickness"], "solid submodel.reconstruction.thickness") <= 0.0:
            raise SubmodelRecordError("solid submodel.reconstruction.thickness must be positive")
        if _finite(rec["reference_offset"], "solid submodel.reconstruction.reference_offset") != 0.0:
            raise SubmodelRecordError("solid submodel.reconstruction.reference_offset must be 0 (the shell has no "
                                      "reference-offset model)")
        if _finite(rec["boundary_tolerance"], "solid submodel.reconstruction.boundary_tolerance") <= 0.0:
            raise SubmodelRecordError("solid submodel.reconstruction.boundary_tolerance must be positive")
        section = _mapping(rec["section"], "solid submodel.reconstruction.section")
        _text(section.get("id"), "solid submodel.reconstruction.section.id")
        _text(section.get("name"), "solid submodel.reconstruction.section.name")
        material = _mapping(rec["material"], "solid submodel.reconstruction.material")
        _text(material.get("name"), "solid submodel.reconstruction.material.name")
        if _finite(material.get("elastic_modulus"), "solid submodel material.elastic_modulus") <= 0.0:
            raise SubmodelRecordError("solid submodel material.elastic_modulus must be positive")
        if not 0.0 <= _finite(material.get("poisson_ratio"), "solid submodel material.poisson_ratio") <= 0.45:
            raise SubmodelRecordError("solid submodel material.poisson_ratio must be in [0, 0.45]")
        pressure = _mapping(rec["pressure"], "solid submodel.reconstruction.pressure")
        value = pressure.get("value")
        if (value is None) != (self.pressure_skin is None):
            raise SubmodelRecordError("a retained pressure value requires pressure_skin, and conversely")
        if value is not None:
            _finite(value, "solid submodel.reconstruction.pressure.value")

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "child_kind": CHILD_KIND,
            "schema_version": int(self.schema_version),
            "id": self.id,
            "name": self.name,
            "source": self.source.to_dict(),
            "child_case": self.child_case,
            "face_id": int(self.face_id),
            "footprint": [[float(c) for c in p] for p in self.footprint],
            "target_size": float(self.target_size),
            "layers": int(self.layers),
            "order": int(self.order),
            "pressure_skin": self.pressure_skin,
            "geometry": _thaw(self.geometry),
            "reconstruction": _thaw(self.reconstruction),
        }

    @classmethod
    def from_dict(cls, data: Any, context: str = "submodel") -> "SolidSubmodelDefinition":
        data = _mapping(data, context)
        if data.get("child_kind") != CHILD_KIND:
            raise SubmodelRecordError(f"{context}.child_kind must be {CHILD_KIND!r}")
        version = _schema(data, context)
        unknown = sorted(set(data) - _KEYS)
        if unknown:
            raise SubmodelRecordError(f"{context} has unknown fields {unknown}")
        name = data.get("name", "")
        if not isinstance(name, str):
            raise SubmodelRecordError(f"{context}.name must be a string")
        footprint = data.get("footprint")
        if not isinstance(footprint, (list, tuple)):
            raise SubmodelRecordError(f"{context}.footprint must be four xyz points")
        skin = data.get("pressure_skin")
        if skin is not None and not isinstance(skin, str):
            raise SubmodelRecordError(f"{context}.pressure_skin must be a string or null")
        definition = cls(
            id=_text(data.get("id"), f"{context}.id"),
            name=name,
            source=SourceReference.from_dict(data.get("source"), f"{context}.source"),
            child_case=_text(data.get("child_case"), f"{context}.child_case"),
            face_id=data.get("face_id"),  # type: ignore[arg-type]
            footprint=tuple(footprint),  # type: ignore[arg-type]
            target_size=_finite(data.get("target_size"), f"{context}.target_size"),
            layers=data.get("layers"),  # type: ignore[arg-type]
            order=data.get("order"),  # type: ignore[arg-type]
            pressure_skin=skin,
            geometry=_mapping(data.get("geometry"), f"{context}.geometry"),
            reconstruction=_mapping(data.get("reconstruction"), f"{context}.reconstruction"),
            schema_version=version,
        )
        definition.validate()
        return definition


def solid_definition_hash(entry: Mapping[str, Any]) -> str:
    """Identity of a serialized solid definition: everything but ``id`` and ``name``.

    This *is* :func:`anyfem.model.submodels.definition_hash` (which dispatches on
    ``child_kind``), so a run staged through ``stage_submodel_run`` and one hashed
    here can never disagree.  An entry of another kind is refused.
    """

    from .submodels import definition_hash

    if not isinstance(entry, Mapping) or entry.get("child_kind") != CHILD_KIND:
        raise SubmodelRecordError(f"a solid definition hash needs an entry with child_kind {CHILD_KIND!r}")
    return definition_hash(entry)
