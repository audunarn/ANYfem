"""Postprocessing: results addressed by geometry."""

from .extract import (
    Envelope,
    PathResult,
    Probe,
    along_line,
    envelope,
    nodes_to_elements,
    probe,
)
from .history import Series, has_history, history_series
from .fields import (
    DISPLACEMENT_FIELDS,
    STRESS_FIELDS,
    Field,
    available_fields,
    evaluate_field,
    field_unit,
)
from .report import (
    field_to_csv,
    path_to_csv,
    report_markdown,
    write_csv,
    write_report,
)
from .results import (
    BucklingSolution,
    ImpactSolution,
    LinearSolution,
    ModalSolution,
    MultiShapeSolution,
    NonlinearSolution,
    ShapeView,
    TransientSolution,
)

__all__ = [
    "Series",
    "has_history",
    "history_series",
    "BucklingSolution",
    "DISPLACEMENT_FIELDS",
    "Envelope",
    "Field",
    "ImpactSolution",
    "LinearSolution",
    "ModalSolution",
    "MultiShapeSolution",
    "NonlinearSolution",
    "PathResult",
    "Probe",
    "STRESS_FIELDS",
    "ShapeView",
    "TransientSolution",
    "along_line",
    "available_fields",
    "envelope",
    "evaluate_field",
    "field_to_csv",
    "field_unit",
    "nodes_to_elements",
    "path_to_csv",
    "probe",
    "report_markdown",
    "write_csv",
    "write_report",
]
