"""Sections: plate thickness, and the typical structural beam profiles.

Beam section properties are resolved through the solver's own
``StiffenerCrossSection.from_geometry`` so area, inertias and torsion constant
cannot diverge from the conventions the beam elements assume.  Only the
profiles that function actually implements are offered; anything else would
silently fall through to a bare-web section, so it is rejected instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Sequence

import numpy as np
from anysolver import StiffenerCrossSection

__all__ = ["BeamSection", "PLATE_PROFILES", "PlateSection", "PROFILES"]

# Profile names accepted by StiffenerCrossSection.from_geometry.  "Flatbar"
# uses (b, tf) as the bar width and thickness; the others use the full
# (hw, tw, b, tf) web/flange set.
PROFILES: tuple[str, ...] = ("Flatbar", "T-bar", "Angle", "L-bulb")
PLATE_PROFILES = PROFILES  # backwards-friendly alias


@dataclass(frozen=True)
class PlateSection:
    """A plate thickness and its material."""

    name: str
    thickness: float
    material: str

    def __post_init__(self) -> None:
        if not np.isfinite(self.thickness) or self.thickness <= 0.0:
            raise ValueError(
                f"plate section {self.name!r}: thickness must be finite and positive"
            )


@dataclass(frozen=True)
class BeamSection:
    """A typical structural profile carried on a line.

    ``web_direction`` prescribes the beam's local z axis -- the direction the
    web stands in.  Without it the solver falls back to a heuristic, which
    leaves asymmetric sections unconstrained, so it is worth setting for
    angles and bulbs.

    ``eccentricity`` offsets the beam's neutral axis from the plate
    midsurface, along the plate normal.  It is not a detail: a stiffener whose
    neutral axis sits in the plating is a materially different structure from
    one standing proud of it, and modelling the second as the first understates
    the stiffness considerably.  Zero keeps the stiffener sharing the plate
    nodes, which is what it did before this existed.
    """

    name: str
    profile: str
    material: str
    web_height: float = 0.0
    web_thickness: float = 0.0
    flange_width: float = 0.0
    flange_thickness: float = 0.0
    web_direction: Sequence[float] | None = None
    eccentricity: float = 0.0

    def __post_init__(self) -> None:
        if self.profile not in PROFILES:
            raise ValueError(
                f"beam section {self.name!r}: unknown profile {self.profile!r}. "
                f"Supported profiles are {', '.join(PROFILES)}."
            )
        dimensions = {
            "web_height": self.web_height,
            "web_thickness": self.web_thickness,
            "flange_width": self.flange_width,
            "flange_thickness": self.flange_thickness,
        }
        invalid = [
            name
            for name, value in dimensions.items()
            if not np.isfinite(value) or value < 0.0
        ]
        if invalid:
            raise ValueError(
                f"beam section {self.name!r}: dimensions must be finite and "
                f"non-negative; invalid {', '.join(invalid)}"
            )
        if not np.isfinite(self.eccentricity):
            raise ValueError(
                f"beam section {self.name!r}: eccentricity must be finite"
            )
        if self.profile == "Flatbar":
            if self.flange_width <= 0.0 or self.flange_thickness <= 0.0:
                raise ValueError(
                    f"beam section {self.name!r}: a flat bar needs a positive "
                    "flange_width (bar width) and flange_thickness"
                )
        else:
            if self.web_height <= 0.0 or self.web_thickness <= 0.0:
                raise ValueError(
                    f"beam section {self.name!r}: profile {self.profile} needs a "
                    "positive web_height and web_thickness"
                )
        if self.web_direction is not None:
            try:
                direction = np.asarray(self.web_direction, dtype=float)
            except (TypeError, ValueError):
                raise ValueError(
                    f"beam section {self.name!r}: web_direction needs three "
                    "finite components"
                ) from None
            if (
                direction.shape != (3,)
                or not np.all(np.isfinite(direction))
                or float(np.linalg.norm(direction)) == 0.0
            ):
                raise ValueError(
                    f"beam section {self.name!r}: web_direction needs three "
                    "finite, non-zero components"
                )
            object.__setattr__(
                self, "web_direction", tuple(float(value) for value in direction)
            )

    def properties(self) -> Dict[str, float]:
        """Cross-section properties in the solver's own dictionary form."""

        section = StiffenerCrossSection.from_geometry(
            self.profile,
            float(self.web_height),
            float(self.web_thickness),
            float(self.flange_width),
            float(self.flange_thickness),
        )
        properties: Dict[str, float] = {
            "area": float(section.area),
            "Iy": float(section.Iy),
            "Iz": float(section.Iz),
            "J": float(section.J),
            "shear_factor_y": float(section.shear_factor_y),
            "shear_factor_z": float(section.shear_factor_z),
            "c_y": float(section.c_y),
            "c_z": float(section.c_z),
            "torsion_modulus": float(section.torsion_modulus),
        }
        if self.web_direction is not None:
            properties["orientation"] = np.asarray(self.web_direction, dtype=float)
        return properties


def rectangular_bar(
    name: str, width: float, thickness: float, material: str
) -> BeamSection:
    """Convenience builder for a plain rectangular bar."""

    return BeamSection(
        name=name,
        profile="Flatbar",
        material=material,
        flange_width=width,
        flange_thickness=thickness,
    )
