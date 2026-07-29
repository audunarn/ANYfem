"""Materials.

Steel grades resolve through the solver's own DNV-RP-C208 table so grade and
thickness rules cannot diverge between ANYfem and ANYsolver.  That lookup fails
closed outside the tabulated thickness range, and so does this wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from anysolver import dnv_c208_steel_properties

__all__ = ["Material", "steel"]


@dataclass(frozen=True)
class Material:
    """An isotropic material in SI units.

    ``hardening`` records *how to build* the curve rather than the curve
    itself: ``("dnv_c208", grade, thickness)``.  A live curve object could not
    be written to a project file, and a material that silently lost its
    hardening on save would turn a plastic analysis elastic without saying so.
    """

    name: str
    elastic_modulus: float
    poisson_ratio: float
    density: float = 7850.0
    yield_stress: float = 0.0
    hardening: Optional[Tuple[str, str, float]] = None

    def __post_init__(self) -> None:
        if self.elastic_modulus <= 0.0:
            raise ValueError(f"material {self.name!r}: elastic modulus must be positive")
        if not -1.0 < self.poisson_ratio < 0.5:
            raise ValueError(
                f"material {self.name!r}: Poisson ratio {self.poisson_ratio} "
                "is outside the physically admissible range"
            )
        if self.density < 0.0:
            raise ValueError(f"material {self.name!r}: density must not be negative")
        if self.hardening is not None and self.hardening[0] != "dnv_c208":
            raise ValueError(
                f"material {self.name!r}: unknown hardening source "
                f"{self.hardening[0]!r}; only 'dnv_c208' is available"
            )

    @property
    def is_nonlinear(self) -> bool:
        """Whether this material yields, rather than staying elastic."""

        return self.hardening is not None

    def hardening_curve(self) -> Optional[Any]:
        """Build the true stress/true plastic strain curve, or None.

        Rebuilt from the solver's table on demand, so the curve a nonlinear
        solve uses is always the solver's own rather than a stored copy that
        could age.
        """

        if self.hardening is None:
            return None
        from anysolver import dnv_c208_steel_curve

        _kind, grade, thickness = self.hardening
        return dnv_c208_steel_curve(grade, float(thickness))


def steel(
    grade: str = "S355",
    thickness: float = 0.010,
    *,
    name: Optional[str] = None,
    density: float = 7850.0,
    nonlinear: bool = False,
) -> Material:
    """Build a steel material from the solver's validated RP-C208 table.

    ``thickness`` is in metres and selects the table row, matching solver SI
    units.  ``nonlinear=True`` attaches the matching hardening curve; without
    it a nonlinear solve is geometrically nonlinear but the material stays
    elastic, which is a different analysis and worth asking for explicitly.
    """

    properties = dnv_c208_steel_properties(grade, thickness)
    return Material(
        name=name or grade,
        elastic_modulus=float(properties["E_pa"]),
        poisson_ratio=0.3,
        density=density,
        yield_stress=float(properties["sigma_yield"]),
        hardening=("dnv_c208", grade, float(thickness)) if nonlinear else None,
    )
