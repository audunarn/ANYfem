"""Solver adapter: build an anysolver FEModel and dispatch analyses."""

from .build import BuiltModel, build_fe_model
from .policy import history_modes, recovery_policy, resource_policy
from .run import (
    ContactConfigurationError,
    eigenmode_imperfection,
    preflight,
    solve_arc_length,
    solve_buckling,
    solve_capacity,
    solve_impact,
    solve_linear_static,
    solve_linear_static_many,
    solve_modal,
    solve_nonlinear_static,
    solve_transient,
)

__all__ = [
    "BuiltModel",
    "ContactConfigurationError",
    "build_fe_model",
    "eigenmode_imperfection",
    "preflight",
    "solve_arc_length",
    "history_modes",
    "recovery_policy",
    "resource_policy",
    "solve_buckling",
    "solve_capacity",
    "solve_impact",
    "solve_linear_static",
    "solve_linear_static_many",
    "solve_modal",
    "solve_nonlinear_static",
    "solve_transient",
]
