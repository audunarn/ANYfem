"""Scalar fields over a result: one way to ask for any quantity.

Displacement lives at nodes; stress lives at elements, several values deep
(one per Gauss point, and membrane and bending separately).  Rather than
scatter that difference through the display code, everything is answered as a
:class:`Field` -- a name, a unit, and values keyed either by node or by
element.  The scene builder, the probes, the path plots, the envelopes and the
report all consume the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


__all__ = [
    "DISPLACEMENT_FIELDS",
    "Field",
    "STRESS_FIELDS",
    "available_fields",
    "element_centroids",
    "evaluate_field",
    "field_unit",
]

DISPLACEMENT_FIELDS: Tuple[str, ...] = (
    "magnitude",
    "ux",
    "uy",
    "uz",
    "rx",
    "ry",
    "rz",
)

# Recovered shell components, plus the surface stresses derived from them.
_SHELL_COMPONENTS: Tuple[str, ...] = (
    "von_mises",
    "membrane_xx",
    "membrane_yy",
    "membrane_xy",
    "bending_xx",
    "bending_yy",
    "bending_xy",
    "shear_xz",
    "shear_yz",
)

_SURFACE_COMPONENTS: Tuple[str, ...] = (
    "top_xx",
    "top_yy",
    "top_xy",
    "bottom_xx",
    "bottom_yy",
    "bottom_xy",
)

_BEAM_COMPONENTS: Tuple[str, ...] = (
    "axial_stress",
    "bending_stress_y",
    "bending_stress_z",
    "shear_stress_y",
    "shear_stress_z",
    "torsional_stress",
)

STRESS_FIELDS: Tuple[str, ...] = (
    _SHELL_COMPONENTS + _SURFACE_COMPONENTS + _BEAM_COMPONENTS
)

_ROTATION_FIELDS = frozenset({"rx", "ry", "rz"})

# How several Gauss-point values become one number for an element.
REDUCTIONS = ("mean", "max_abs", "min", "max")


def available_fields() -> List[str]:
    """Every field name that can be asked for."""

    return list(DISPLACEMENT_FIELDS) + list(STRESS_FIELDS)


def field_unit(name: str) -> str:
    if name in DISPLACEMENT_FIELDS:
        return "" if name in _ROTATION_FIELDS else "m"
    if name in STRESS_FIELDS:
        return "Pa"
    return ""


@dataclass
class Field:
    """A scalar field over one shape.

    Exactly one of ``node_values`` and ``element_values`` is populated, because
    that is a real difference: a displacement is known at nodes and a stress is
    recovered on elements, and pretending otherwise would invent data.
    """

    name: str
    unit: str = ""
    node_values: Dict[int, float] = dataclass_field(default_factory=dict)
    element_values: Dict[int, float] = dataclass_field(default_factory=dict)
    reduction: str = "mean"
    missing: Tuple[int, ...] = ()

    @property
    def per_element(self) -> bool:
        return bool(self.element_values)

    @property
    def values(self) -> Dict[int, float]:
        return self.element_values if self.per_element else self.node_values

    def array(self) -> np.ndarray:
        return np.array([self.values[key] for key in sorted(self.values)])

    def range(self) -> Tuple[float, float]:
        data = self.array()
        if data.size == 0:
            return 0.0, 1.0
        low, high = float(data.min()), float(data.max())
        return (low, high) if high > low else (low, low + 1.0)

    def extreme(self) -> Tuple[Optional[int], float]:
        """The key with the largest magnitude, and its value."""

        if not self.values:
            return None, 0.0
        key = max(self.values, key=lambda item: abs(self.values[item]))
        return key, float(self.values[key])

    def __len__(self) -> int:
        return len(self.values)

    def __repr__(self) -> str:  # pragma: no cover - display only
        low, high = self.range()
        where = "elements" if self.per_element else "nodes"
        return (
            f"Field({self.name!r}, {len(self)} {where}, "
            f"{low:.4g} to {high:.4g} {self.unit})"
        )


def _reduce(values: Any, reduction: str) -> float:
    array = np.atleast_1d(np.asarray(values, dtype=float))
    if array.size == 0:
        return 0.0
    if reduction == "mean":
        return float(array.mean())
    if reduction == "max_abs":
        return float(array[int(np.argmax(np.abs(array)))])
    if reduction == "min":
        return float(array.min())
    if reduction == "max":
        return float(array.max())
    raise ValueError(
        f"unknown reduction {reduction!r}; expected one of {', '.join(REDUCTIONS)}"
    )


def _surface_value(stress: Dict[str, Any], name: str) -> Optional[Any]:
    """Top and bottom surface stress, from membrane plus or minus bending."""

    side, _, component = name.partition("_")
    membrane = stress.get(f"membrane_{component}")
    bending = stress.get(f"bending_{component}")
    if membrane is None or bending is None:
        return None
    membrane = np.asarray(membrane, dtype=float)
    bending = np.asarray(bending, dtype=float)
    return membrane + bending if side == "top" else membrane - bending


def evaluate_field(
    shape,
    name: str = "magnitude",
    *,
    reduction: str = "mean",
    stresses: Any = None,
) -> Field:
    """Evaluate one named field over a shape.

    ``stresses`` lets a caller reuse a recovery it already has, which matters
    when sweeping every step of a transient.
    """

    if name in DISPLACEMENT_FIELDS:
        return _displacement_field(shape, name)
    if name in STRESS_FIELDS:
        return _stress_field(shape, name, reduction=reduction, stresses=stresses)
    raise ValueError(
        f"unknown field {name!r}; expected one of {', '.join(available_fields())}"
    )


def _displacement_field(shape, name: str) -> Field:
    mesh = shape.built.mesh
    node_ids = sorted(mesh.nodes)
    if name == "magnitude":
        values = np.linalg.norm(shape.translations(), axis=1)
    else:
        values = shape.component(name)
    return Field(
        name=name,
        unit=field_unit(name),
        node_values={
            node_id: float(value) for node_id, value in zip(node_ids, values)
        },
    )


def _stress_field(
    shape, name: str, *, reduction: str, stresses: Any = None
) -> Field:
    if stresses is None:
        stresses = recover(shape)

    values: Dict[int, float] = {}
    missing: List[int] = []
    for element_id in sorted(shape.built.mesh.quads) + sorted(
        shape.built.mesh.beams
    ):
        stress = stresses.element_stresses.get(element_id)
        if stress is None:
            missing.append(element_id)
            continue
        raw = stress.get(name)
        if raw is None and name in _SURFACE_COMPONENTS:
            raw = _surface_value(stress, name)
        if raw is None:
            # A beam has no membrane stress and a shell has no torsion; an
            # element that cannot carry this component is left out rather than
            # reported as zero.
            missing.append(element_id)
            continue
        values[element_id] = _reduce(raw, reduction)

    return Field(
        name=name,
        unit=field_unit(name),
        element_values=values,
        reduction=reduction,
        missing=tuple(missing),
    )


def recover(shape, **kwargs: Any):
    """Recover element stresses for any shape, caching on the shape itself."""

    cached = getattr(shape, "_stress", None)
    if cached is not None and not kwargs:
        return cached

    from anysolver import recover_stress_result

    result = recover_stress_result(
        shape.built.fe_model, shape.displacements, **kwargs
    )
    if not kwargs:
        try:
            shape._stress = result
        except AttributeError:  # pragma: no cover - frozen shapes
            pass
    return result


def element_centroids(mesh) -> Dict[int, np.ndarray]:
    """Centroid of every element, for locating element-based values in space."""

    centroids: Dict[int, np.ndarray] = {}
    for element_id in mesh.quads:
        # Corners only: averaging in the mid-side nodes of a quadratic element
        # would pull the centroid towards whichever sides are longer.
        centroids[element_id] = np.mean(
            [mesh.nodes[node] for node in mesh.corners_of(element_id)], axis=0
        )
    for element_id, nodes in mesh.beams.items():
        centroids[element_id] = np.mean(
            [mesh.nodes[node] for node in nodes], axis=0
        )
    return centroids
