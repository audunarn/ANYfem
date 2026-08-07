"""Application-facing material specifications backed by ANYmaterial.

ANYfem stores descriptions rather than live constitutive objects so project
files remain JSON serializable. ``Material`` keeps the historical isotropic
constructor while also accepting the full ``MaterialSpec`` schema.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from anymaterial import MaterialSpec, dnv_c208_steel_properties

__all__ = ["Material", "MaterialSpec", "steel"]


def _hardening_descriptor(value: Any) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) != 3:
            raise ValueError("a legacy hardening descriptor needs kind, grade and thickness")
        kind, grade, thickness = value
        if str(kind) != "dnv_c208":
            raise ValueError(
                f"unknown hardening source {kind!r}; only 'dnv_c208' is available"
            )
        return {
            "kind": "dnv_c208",
            "grade": str(grade),
            "thickness": float(thickness),
        }
    raise TypeError("hardening must be a descriptor mapping or legacy 3-item sequence")


class Material(MaterialSpec):
    """A ``MaterialSpec`` with ANYfem's former isotropic constructor."""

    def __init__(
        self,
        name: str,
        elastic_modulus: Optional[float] = None,
        poisson_ratio: Optional[float] = None,
        density: float = 7850.0,
        yield_stress: float = 0.0,
        hardening: Any = None,
        *,
        symmetry: str = "isotropic",
        constants: Optional[Mapping[str, float]] = None,
        hill: Optional[Mapping[str, float]] = None,
    ) -> None:
        if constants is None:
            if elastic_modulus is None or poisson_ratio is None:
                raise ValueError(
                    "an isotropic material needs elastic_modulus and poisson_ratio"
                )
            constants = {
                "elastic_modulus": float(elastic_modulus),
                "poisson_ratio": float(poisson_ratio),
            }
        super().__init__(
            name=str(name),
            symmetry=str(symmetry),
            constants=dict(constants),
            density=float(density),
            yield_stress=float(yield_stress),
            hardening=_hardening_descriptor(hardening),
            hill=None if hill is None else dict(hill),
        )
        # MaterialSpec validates the schema. Building also validates the actual
        # constants immediately, preserving ANYfem's fail-fast constructor.
        self.build()

    @property
    def elastic_modulus(self) -> float:
        if self.symmetry != "isotropic":
            raise AttributeError("an orthotropic material has directional elastic moduli")
        return float(self.constants["elastic_modulus"])

    @property
    def poisson_ratio(self) -> float:
        if self.symmetry != "isotropic":
            raise AttributeError("an orthotropic material has directional Poisson ratios")
        return float(self.constants["poisson_ratio"])


def steel(
    grade: str = "S355",
    thickness: float = 0.010,
    *,
    name: Optional[str] = None,
    density: float = 7850.0,
    nonlinear: bool = False,
) -> Material:
    """Build a serializable steel specification from ANYmaterial's table."""

    properties = dnv_c208_steel_properties(grade, thickness)
    return Material(
        name=name or str(properties["grade"]),
        elastic_modulus=float(properties["E_pa"]),
        poisson_ratio=0.3,
        density=density,
        yield_stress=float(properties["sigma_yield"]),
        hardening=("dnv_c208", grade, float(thickness)) if nonlinear else None,
    )
