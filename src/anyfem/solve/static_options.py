"""Application admission policy for the public linear-static solver modes."""

from typing import Literal

StaticConstraintMode = Literal["transformation", "nullspace", "auto"]


def allows_free_static(constraint_mode: StaticConstraintMode, *, inertia_relief: bool = False,
                       allow_unbalanced_free_free: bool = False) -> bool:
    """Explicit nullspace or mass-based relief admits an unsupported model.

    ``auto`` without relief retains the application's supported-model default. Load balancing
    is a separate solver opt-in and does not itself relax model validation.
    Inertia relief is an explicit physical analysis choice, not generalized
    balancing; its solver profile still refuses physical supports/mechanisms.
    """
    if type(inertia_relief) is not bool:
        raise ValueError("inertia_relief must be a boolean")
    if constraint_mode not in ("transformation", "nullspace", "auto"):
        raise ValueError(
            "constraint_mode must be 'transformation', 'nullspace', or 'auto'"
        )
    if inertia_relief:
        if constraint_mode == "transformation":
            raise ValueError("inertia relief requires nullspace or auto constraint mode")
        if allow_unbalanced_free_free:
            raise ValueError("inertia relief cannot be combined with generalized load balancing")
    return inertia_relief or constraint_mode == "nullspace"
