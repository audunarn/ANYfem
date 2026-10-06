"""Fluid-pressure evaluation shared by the solver build and the viewport.

Both must agree exactly on which way a wet pressure acts and what an equation
gives at a point, so the arithmetic lives here, free of mesh and solver types.
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np

from .attributes import LoadCase, WetPressure, WetSurface
from .expressions import ExpressionError, evaluate_expression

__all__ = ["WetFieldError", "expression_pressure", "fluid_sign"]


class WetFieldError(ValueError):
    """A wet pressure cannot be evaluated."""


def fluid_sign(
    surface: WetSurface, centroid: np.ndarray, normal: np.ndarray
) -> float:
    """Sign turning a pressure magnitude into a normal-signed pressure.

    The solver's positive pressure acts along the element normal; fluid on the
    positive-normal side pushes against it.
    """

    if surface.fluid_side == "positive_normal":
        return -1.0
    if surface.fluid_side == "negative_normal":
        return 1.0
    outward = np.asarray(centroid, dtype=float) - np.asarray(
        surface.interior_point, dtype=float
    )
    side = float(np.dot(normal, outward))
    if side == 0.0:
        raise WetFieldError(
            f"wet surface {surface.name!r}: the interior point lies in the "
            "plane of an element, so the fluid side cannot be decided"
        )
    return -1.0 if side > 0.0 else 1.0


def expression_pressure(
    case: LoadCase, load: WetPressure, centroids: np.ndarray
) -> np.ndarray:
    """An equation's pressure magnitude at each centroid."""

    centroids = np.asarray(centroids, dtype=float).reshape(-1, 3)
    variables: Dict[str, Any] = {
        "x": centroids[:, 0],
        "y": centroids[:, 1],
        "z": centroids[:, 2],
        **case.parameters,
    }
    if case.time is not None:
        variables["t"] = float(case.time)
    try:
        return evaluate_expression(
            load.expression, variables, size=len(centroids)
        )
    except ExpressionError as error:
        hint = (
            "; the case has no time (set one, or generate the case with "
            "add_load_case_series)"
            if case.time is None and "'t'" in str(error)
            else ""
        )
        raise WetFieldError(
            f"load case {case.name!r} wet pressure {load.name!r}: "
            f"{error}{hint}"
        ) from None
