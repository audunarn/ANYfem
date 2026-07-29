"""Geometric imperfections, bound to geometry like every other attribute.

An imperfection alters the *stress-free reference geometry*.  It is not an
initial displacement and it is not a residual stress: the imperfect shape is
the shape the structure would have with no load on it at all.  The solver keeps
that distinction, and so does this layer -- imperfections are declared on the
project and applied when the FEModel is built, never mixed into a result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from ..geometry.entities import EntityRef

__all__ = ["Imperfection", "member_bow", "plate_mode"]

IMPERFECTION_KINDS: tuple[str, ...] = ("auto", "plate_mode", "member_bow")


@dataclass(frozen=True)
class Imperfection:
    """A stress-free shape deviation on one plate or line.

    ``amplitude`` of None takes the solver's standard default: span/200 for a
    plate mode, length/300 for a member bow.
    """

    ref: EntityRef
    kind: str = "auto"
    amplitude: Optional[float] = None
    direction: Sequence[float] = (0.0, 0.0, 1.0)
    waves: Tuple[int, int] = (1, 1)
    axes: Tuple[int, int] = (0, 1)
    name: str = "imperfection"

    def __post_init__(self) -> None:
        if self.kind not in IMPERFECTION_KINDS:
            raise ValueError(
                f"unknown imperfection kind {self.kind!r}; expected one of "
                f"{', '.join(IMPERFECTION_KINDS)}"
            )
        if self.amplitude is not None and self.amplitude < 0.0:
            raise ValueError("an imperfection amplitude must not be negative")
        if self.resolved_kind == "plate_mode" and self.ref.kind != "face":
            raise ValueError("a plate mode applies to a plate")
        if self.resolved_kind == "member_bow" and self.ref.kind != "edge":
            raise ValueError("a member bow applies to a line")
        if any(int(wave) < 1 for wave in self.waves):
            raise ValueError("wave counts must be at least 1")

    @property
    def resolved_kind(self) -> str:
        """The kind to build, with ``auto`` decided by what it is attached to."""

        if self.kind != "auto":
            return self.kind
        return "plate_mode" if self.ref.kind == "face" else "member_bow"


def plate_mode(
    ref: EntityRef,
    amplitude: Optional[float] = None,
    waves: Tuple[int, int] = (1, 1),
    direction: Sequence[float] = (0.0, 0.0, 1.0),
    name: str = "plate_mode",
) -> Imperfection:
    """A sinusoidal buckling-shaped deviation across a plate."""

    return Imperfection(
        ref=ref,
        kind="plate_mode",
        amplitude=amplitude,
        waves=waves,
        direction=direction,
        name=name,
    )


def member_bow(
    ref: EntityRef,
    amplitude: Optional[float] = None,
    direction: Sequence[float] = (0.0, 0.0, 1.0),
    name: str = "member_bow",
) -> Imperfection:
    """A half-sine bow along a line, as a member out-of-straightness."""

    return Imperfection(
        ref=ref,
        kind="member_bow",
        amplitude=amplitude,
        direction=direction,
        name=name,
    )
