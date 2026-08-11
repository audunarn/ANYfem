"""Headless engineering summaries for the Results workspace.

The GUI consumes these immutable values, but keeping the interpretation out
of Tk makes nonlinear termination semantics directly testable.  In
particular, ``stopped_at_limit`` is not presented as a completed target run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


_STOP_REASONS = {
    "target_load_factor_reached": "Requested load target reached",
    "target_displacement_reached": "Requested displacement target reached",
    "minimum_increment_reached": "Minimum increment reached after repeated cutbacks",
    "minimum_step_size_reached": "Minimum increment reached after repeated cutbacks",
    "max_iterations_reached": "Maximum equilibrium iterations reached",
    "maximum_iterations_reached": "Maximum equilibrium iterations reached",
    "nonfinite_residual": "Non-finite residual encountered",
    "singular_tangent": "Tangent stiffness became singular",
    "load_factor_limit_reached": "Configured load-factor limit reached",
    "post_peak_drop_reached": "Configured post-peak drop reached",
    "empty_reduced_system": "No unconstrained degrees of freedom remain",
}


def _humanize(value: Any) -> str:
    text = str(value or "").strip()
    return _STOP_REASONS.get(text, text.replace("_", " ").strip().capitalize())


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


@dataclass(frozen=True)
class NonlinearPathSummary:
    """What began, what converged, and why the nonlinear path ended."""

    status: str
    severity: str
    start_load_factor: float
    first_converged_load_factor: Optional[float]
    peak_load_factor: Optional[float]
    last_converged_load_factor: Optional[float]
    target_load_factor: Optional[float]
    first_failed_load_factor: Optional[float]
    stop_reason: str
    converged_steps: int
    total_iterations: int
    max_peeq: Optional[float]
    saved_increments: int

    @property
    def progress_fraction(self) -> Optional[float]:
        if (
            self.last_converged_load_factor is None
            or self.target_load_factor is None
            or self.target_load_factor == 0.0
        ):
            return None
        return max(0.0, min(1.0, self.last_converged_load_factor / self.target_load_factor))


def nonlinear_path_summary(solution: Any) -> Optional[NonlinearPathSummary]:
    """Describe an incremental solution, returning ``None`` for other results."""

    steps = tuple(getattr(solution, "steps", ()) or ())
    raw = getattr(solution, "raw_result", None)
    if not steps and raw is None:
        return None
    info = _mapping(getattr(raw, "info", None)) or _mapping(
        getattr(solution, "info", None)
    )
    # ANYfem's wrapper also retains the solver object under info['raw'].
    nested_raw = info.get("raw")
    if nested_raw is not None and nested_raw is not raw:
        raw = nested_raw
        info = _mapping(getattr(raw, "info", None)) or info

    status = str(getattr(solution, "status", getattr(raw, "status", "available")))
    category = str(info.get("status_category", status)).casefold()
    stop_key = info.get("stop_reason") or info.get("failure_reason") or status
    completed = status.casefold() in {"completed", "converged", "ok"}
    severity = "success" if completed else (
        "warning" if category in {"stopped", "limit", "partial", "stopped_at_limit"}
        or status.casefold() == "stopped_at_limit" else "error"
    )

    result_case = _mapping(info.get("result_case"))
    settings = _mapping(result_case.get("settings"))
    target = _number(settings.get("max_load_factor"))
    first = _number(getattr(steps[0], "load_factor", None)) if steps else None
    last = _number(
        info.get(
            "last_converged_load_factor",
            getattr(solution, "load_factor", getattr(solution, "value", None)),
        )
    )
    peak = _number(
        info.get("peak_load_factor", getattr(solution, "peak_load_factor", None))
    )
    if peak is None and steps:
        peak = max(float(getattr(step, "load_factor", 0.0)) for step in steps)
    peeq_values = [
        _number(getattr(step, "max_equivalent_plastic_strain", None))
        for step in steps
    ]
    peeq = max((value for value in peeq_values if value is not None), default=None)
    iterations = int(
        info.get(
            "total_newton_iterations",
            sum(int(getattr(step, "iterations", 0) or 0) for step in steps),
        )
        or 0
    )
    snapshots = tuple(getattr(raw, "snapshots", ()) or ())
    return NonlinearPathSummary(
        status=status,
        severity=severity,
        start_load_factor=0.0,
        first_converged_load_factor=first,
        peak_load_factor=peak,
        last_converged_load_factor=last,
        target_load_factor=target,
        first_failed_load_factor=_number(info.get("first_failed_load_factor")),
        stop_reason=_humanize(stop_key) or "Termination reason unavailable",
        converged_steps=len(steps),
        total_iterations=iterations,
        max_peeq=peeq,
        saved_increments=len(snapshots),
    )

