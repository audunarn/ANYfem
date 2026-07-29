"""Solver adapter: build an anysolver FEModel and dispatch analyses."""

from .build import BuiltModel, build_fe_model
from .run import (
    ContactConfigurationError,
    eigenmode_imperfection,
    preflight,
    solve_arc_length,
    solve_buckling,
    solve_impact,
    solve_linear_static,
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
    "solve_buckling",
    "solve_impact",
    "solve_linear_static",
    "solve_modal",
    "solve_nonlinear_static",
    "solve_transient",
]
