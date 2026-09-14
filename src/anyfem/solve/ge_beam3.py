"""Explicit GE-B3 consumer adapter; no project or beam alias is changed."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
from anysolver.beam_sections import GeneralizedBeamSection
from anysolver.elements import create_element
from anysolver.ge_beam3_element import (
    GE_BEAM3_QUALIFIED_FORMULATION_ID, GeometricallyExactBeam3D3NElement,
)
from anysolver.ge_beam3_native import ConsumerPolicy


SCHEMA = "anyfem.ge-beam3-explicit-opt-in-v1"


def _matrix(value: Any, label: str) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if result.shape != (6, 6) or not np.isfinite(result).all():
        raise ValueError(f"{label} must be a finite 6x6 matrix")
    if not np.array_equal(result, result.T):
        raise ValueError(f"{label} must be exactly symmetric")
    np.linalg.cholesky(result)
    return result.copy()


@dataclass(frozen=True)
class GeBeam3OptIn:
    """Complete physical authority required to construct one GE-B3 family."""

    section_stiffness: Any
    section_mass_per_length: Any
    reference_orientation: Sequence[float]
    section_name: str = "ANYfem explicit GE-B3"
    contact_radius: float = 0.0

    def __post_init__(self) -> None:
        stiffness = _matrix(self.section_stiffness, "section stiffness")
        mass = _matrix(self.section_mass_per_length, "section mass")
        orientation = np.asarray(self.reference_orientation, dtype=float)
        if orientation.shape != (3,) or not np.isfinite(orientation).all() or np.linalg.norm(orientation) <= 0.:
            raise ValueError("reference_orientation must be a finite nonzero three-vector")
        if type(self.section_name) is not str or not self.section_name.strip():
            raise ValueError("section_name must be nonempty text")
        if not np.isfinite(self.contact_radius) or self.contact_radius < 0.:
            raise ValueError("contact_radius must be finite and non-negative")
        stiffness.setflags(write=False); mass.setflags(write=False); orientation = orientation.copy(); orientation.setflags(write=False)
        object.__setattr__(self, "section_stiffness", stiffness)
        object.__setattr__(self, "section_mass_per_length", mass)
        object.__setattr__(self, "reference_orientation", orientation)

    @property
    def policy(self) -> ConsumerPolicy:
        return ConsumerPolicy.ge_beam3()

    def build(self, element_id: int, node_ids: Sequence[int], material_name: str) -> GeometricallyExactBeam3D3NElement:
        section = GeneralizedBeamSection(
            self.section_stiffness, mass_matrix=self.section_mass_per_length,
            name=self.section_name,
        )
        made = create_element(
            "ge-beam3", int(element_id), list(node_ids), str(material_name),
            cross_section={"contact_radius": float(self.contact_radius)},
            section=section, reference_orientation=self.reference_orientation,
        )
        if type(made) is not GeometricallyExactBeam3D3NElement or made.formulation_id != GE_BEAM3_QUALIFIED_FORMULATION_ID:
            raise RuntimeError("ANYfem GE-B3 adapter resolved an unexpected formulation")
        return made

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "consumer_policy": self.policy.to_bytes().decode("ascii").strip(),
            "section_stiffness": self.section_stiffness.tolist(),
            "section_mass_per_length": self.section_mass_per_length.tolist(),
            "reference_orientation": self.reference_orientation.tolist(),
            "section_name": self.section_name,
            "contact_radius": float(self.contact_radius),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GeBeam3OptIn":
        required = {"schema", "consumer_policy", "section_stiffness", "section_mass_per_length",
                    "reference_orientation", "section_name", "contact_radius"}
        if type(data) is not dict or set(data) != required or data["schema"] != SCHEMA:
            raise ValueError("strict ANYfem GE-B3 opt-in record required")
        raw = (str(data["consumer_policy"]) + "\n").encode("ascii")
        from hashlib import sha256
        if ConsumerPolicy.from_bytes(raw, expected_sha256=sha256(raw).hexdigest()) != ConsumerPolicy.ge_beam3():
            raise ValueError("qualified GE-B3 consumer policy required")
        return cls(data["section_stiffness"], data["section_mass_per_length"],
                   data["reference_orientation"], data["section_name"], data["contact_radius"])


__all__ = ["GeBeam3OptIn", "SCHEMA"]
