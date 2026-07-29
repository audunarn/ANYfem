"""Boundary conditions and loads, bound to geometry entities.

Nothing here names a node or an element.  Attributes reference geometry by
``EntityRef``, and the mesh association map resolves them at build time, so a
load survives a re-mesh.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Mapping, Sequence

import numpy as np

from ..geometry.entities import EntityRef

__all__ = [
    "DOF_NAMES",
    "Combination",
    "LineLoad",
    "LoadCase",
    "Mass",
    "PointLoad",
    "Pressure",
    "Support",
    "SurfaceTraction",
    "fixed",
    "pinned",
    "prescribed",
    "simply_supported",
    "support",
]

DOF_NAMES: tuple[str, ...] = ("ux", "uy", "uz", "rx", "ry", "rz")


@dataclass(frozen=True)
class Support:
    """Prescribed degrees of freedom on one geometry entity."""

    name: str
    ref: EntityRef
    constraints: Mapping[str, float]

    def __post_init__(self) -> None:
        unknown = set(self.constraints) - set(DOF_NAMES)
        if unknown:
            raise ValueError(
                f"support {self.name!r}: unknown degrees of freedom "
                f"{sorted(unknown)}; valid names are {', '.join(DOF_NAMES)}"
            )
        if not self.constraints:
            raise ValueError(f"support {self.name!r} constrains nothing")


def support(ref: EntityRef, name: str | None = None, **dofs: float) -> Support:
    """Constrain named degrees of freedom, e.g. ``support(ref, uz=0.0)``.

    The value is the prescribed displacement, not a flag.  ``uz=True`` reads
    like "restrain uz" but would mean "move this by one metre", so booleans are
    rejected rather than quietly coerced -- the resulting model solves happily
    and gives a wrong answer, which is the worst kind of mistake to allow.
    """

    for key, value in dofs.items():
        if isinstance(value, bool):
            raise ValueError(
                f"support({key}={value!r}): the value is a prescribed "
                f"displacement, not a flag. Use {key}=0.0 to restrain the "
                f"degree of freedom."
            )
    return Support(
        name=name or f"support_{ref}",
        ref=ref,
        constraints={key: float(value) for key, value in dofs.items()},
    )


def fixed(ref: EntityRef, name: str | None = None) -> Support:
    """Clamp all six degrees of freedom."""

    return Support(
        name=name or f"fixed_{ref}",
        ref=ref,
        constraints={dof: 0.0 for dof in DOF_NAMES},
    )


def pinned(ref: EntityRef, name: str | None = None) -> Support:
    """Restrain the three translations, leave rotations free."""

    return Support(
        name=name or f"pinned_{ref}",
        ref=ref,
        constraints={"ux": 0.0, "uy": 0.0, "uz": 0.0},
    )


def simply_supported(
    ref: EntityRef, name: str | None = None, normal: str = "uz"
) -> Support:
    """Restrain only the out-of-plane translation."""

    if normal not in ("ux", "uy", "uz"):
        raise ValueError("the simply supported direction must be ux, uy or uz")
    return Support(
        name=name or f"ss_{ref}", ref=ref, constraints={normal: 0.0}
    )


def prescribed(ref: EntityRef, name: str | None = None, **dofs: float) -> Support:
    """Force degrees of freedom to stated non-zero values.

    ``prescribed(ref, uz=0.005)`` pushes the entity 5 mm rather than holding
    it.  This is the same mechanism as a support -- the solver constrains a
    DOF to a value -- so a prescribed displacement and a restraint are the
    same object with different numbers.
    """

    if not dofs:
        raise ValueError("a prescribed displacement needs at least one value")
    return Support(
        name=name or f"prescribed_{ref}",
        ref=ref,
        constraints={key: float(value) for key, value in dofs.items()},
    )


@dataclass(frozen=True)
class Mass:
    """A lumped mass attached to geometry.

    On a point it is one mass; on a line or plate the total is shared equally
    over the nodes there.  It enters the mass matrix, so it shifts natural
    frequencies as well as producing inertial load under acceleration.
    """

    ref: EntityRef
    value: float
    name: str = "mass"

    def __post_init__(self) -> None:
        if self.value < 0.0:
            raise ValueError(f"mass {self.name!r} must not be negative")


@dataclass(frozen=True)
class Combination:
    """A factored sum of load cases."""

    name: str
    factors: Mapping[str, float]

    def __post_init__(self) -> None:
        if not self.factors:
            raise ValueError(f"combination {self.name!r} combines nothing")


@dataclass(frozen=True)
class PointLoad:
    """A force and moment applied at a point."""

    ref: EntityRef
    force: np.ndarray = field(default_factory=lambda: np.zeros(3))
    moment: np.ndarray = field(default_factory=lambda: np.zeros(3))

    def __post_init__(self) -> None:
        if self.ref.kind != "vertex":
            raise ValueError("a point load applies to a point")


@dataclass(frozen=True)
class Pressure:
    """Uniform pressure on a plate, positive along the element normal."""

    ref: EntityRef
    value: float

    def __post_init__(self) -> None:
        if self.ref.kind != "face":
            raise ValueError("a pressure applies to a plate")


@dataclass(frozen=True)
class LineLoad:
    """A distributed force per unit length along a line, in N/m."""

    ref: EntityRef
    force_per_length: np.ndarray

    def __post_init__(self) -> None:
        if self.ref.kind != "edge":
            raise ValueError("a line load applies to a line")


@dataclass(frozen=True)
class SurfaceTraction:
    """A distributed force per unit area on a plate, in N/m^2.

    Unlike a pressure, the direction is fixed in space rather than following
    the plate normal, so it describes things like a horizontal wind load on a
    sloping surface.
    """

    ref: EntityRef
    traction: np.ndarray

    def __post_init__(self) -> None:
        if self.ref.kind != "face":
            raise ValueError("a surface traction applies to a plate")


@dataclass
class LoadCase:
    """A named collection of loads.

    ``follower_pressure`` is a property of the whole case, not of individual
    pressures, because that is how the solver models it: the load vector is
    assembled either in the reference configuration or the current one.
    Mixing dead and follower pressure inside one case is therefore not
    representable, and asking for it is refused rather than silently resolved.
    """

    name: str
    point_loads: List[PointLoad] = field(default_factory=list)
    pressures: List[Pressure] = field(default_factory=list)
    line_loads: List[LineLoad] = field(default_factory=list)
    surface_tractions: List[SurfaceTraction] = field(default_factory=list)
    gravity: np.ndarray | None = None
    follower_pressure: bool = False

    def add_point_load(
        self,
        ref: EntityRef,
        force: Sequence[float] = (0.0, 0.0, 0.0),
        moment: Sequence[float] = (0.0, 0.0, 0.0),
    ) -> PointLoad:
        load = PointLoad(
            ref=ref,
            force=np.asarray(force, dtype=float),
            moment=np.asarray(moment, dtype=float),
        )
        self.point_loads.append(load)
        return load

    def add_pressure(self, ref: EntityRef, value: float) -> Pressure:
        load = Pressure(ref=ref, value=float(value))
        self.pressures.append(load)
        return load

    def add_line_load(
        self, ref: EntityRef, force_per_length: Sequence[float]
    ) -> LineLoad:
        load = LineLoad(
            ref=ref, force_per_length=np.asarray(force_per_length, dtype=float)
        )
        self.line_loads.append(load)
        return load

    def add_surface_traction(
        self, ref: EntityRef, traction: Sequence[float]
    ) -> SurfaceTraction:
        load = SurfaceTraction(
            ref=ref, traction=np.asarray(traction, dtype=float)
        )
        self.surface_tractions.append(load)
        return load

    def set_gravity(
        self, gx: float = 0.0, gy: float = 0.0, gz: float = -9.81
    ) -> None:
        self.gravity = np.array([gx, gy, gz], dtype=float)

    def set_acceleration(
        self, ax: float = 0.0, ay: float = 0.0, az: float = 0.0
    ) -> None:
        """A design acceleration field, e.g. a vessel motion.

        This is the same mechanism as gravity -- a consistent inertial load
        over the structural mass -- so setting one replaces the other.
        """

        self.gravity = np.array([ax, ay, az], dtype=float)

    def set_follower_pressure(self, follower: bool = True) -> None:
        """Make this case's pressures act on the deformed configuration."""

        self.follower_pressure = bool(follower)

    def is_empty(self) -> bool:
        return not (
            self.point_loads
            or self.pressures
            or self.line_loads
            or self.surface_tractions
            or self.gravity is not None
        )
