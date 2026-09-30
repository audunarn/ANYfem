"""Native Project records for two explicit experimental point-panel inputs.

These analyses own a mesh-native definition; Project geometry, load cases and
meshing defaults do not regenerate it. Saving inputs does not save an accepted
solver state or qualify an experimental model. Standalone consumer files retain
their existing meanings. BL5 and other consumers are deliberately unsupported.
"""

from __future__ import annotations

import json

from .stiffened_shell import ExperimentalGEPointPanel
from .stiffened_shell_b2 import ExperimentalB2PointPanel


SCHEMA = "anyfem.experimental-point-panel-project-v1"
STATUS = "EXPERIMENTAL_NOT_QUALIFIED"
GE_TYPE = "experimental_ge_point_panel"
B2_TYPE = "experimental_b2_point_panel"
GE_FORMULATION = "experimental-ge-point-panel"
B2_FORMULATION = "experimental-b2-point-panel"


def _owned_json(value):
    """Detach every nested input through the existing JSON-compatible schema."""
    try:
        return json.loads(json.dumps(value, allow_nan=False, sort_keys=True))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("finite JSON point-panel inputs required") from exc


def encode_point_panel(panel):
    """Return the explicit selector pair and a detached native analysis payload."""
    if type(panel) is ExperimentalGEPointPanel:
        analysis_type, formulation = GE_TYPE, GE_FORMULATION
        payload = panel.to_dict()
    elif type(panel) is ExperimentalB2PointPanel:
        analysis_type, formulation = B2_TYPE, B2_FORMULATION
        payload = json.loads(panel.payload)
    else:
        raise ValueError("exact ExperimentalGEPointPanel or ExperimentalB2PointPanel required")
    record = _owned_json({
        "schema": SCHEMA, "status": STATUS,
        "definition_source": "explicit-mesh-native", "panel": payload,
    })
    # Apply the same route-bound parser used on reopen before admitting a record.
    _decode_point_panel(analysis_type, formulation, record)
    return analysis_type, formulation, record


def _decode_point_panel(analysis_type, formulation, record):
    if (type(record) is not dict or set(record) != {
            "schema", "status", "definition_source", "panel"} or
            record["schema"] != SCHEMA or record["status"] != STATUS or
            record["definition_source"] != "explicit-mesh-native"):
        raise ValueError("strict experimental mesh-native point-panel payload required")
    payload = _owned_json(record["panel"])
    if (analysis_type, formulation) == (GE_TYPE, GE_FORMULATION):
        return ExperimentalGEPointPanel.from_dict(payload)
    if (analysis_type, formulation) == (B2_TYPE, B2_FORMULATION):
        return ExperimentalB2PointPanel(
            json.dumps(payload, allow_nan=False).encode("ascii"))
    raise ValueError("matching explicit experimental point-panel type/formulation required")


def validate_analysis(analysis):
    """Revalidate a mutable analysis record without assembling or solving it."""
    if (analysis.target_kind != "none" or analysis.target_id != "" or
            analysis.settings or analysis.resource_policy or
            analysis.output_request_ids or analysis.output_requests):
        raise ValueError(
            "mesh-native point panel requires no Project target, settings, "
            "resource policy or output requests; use its explicit consumer inputs")
    panel = _decode_point_panel(
        analysis.type, analysis.beam_formulation,
        analysis.beam_formulation_options)
    _, _, canonical = encode_point_panel(panel)
    return canonical


def point_panel_from_analysis(analysis):
    """Reconstruct the exact input consumer; no Project geometry is consulted."""
    canonical = validate_analysis(analysis)
    return _decode_point_panel(analysis.type, analysis.beam_formulation, canonical)


def solve_point_panel(analysis, *, cancellation_token=None, on_case=None):
    """Run the stored reference-linear inputs with the existing owner semantics.

    B2 retains its ordered case IDs, selected recovery and cancelled prefix. GE
    has one stored action set and returns its own LinearResult; it has no batch
    case IDs or on_case callback. Use create_point_panel_analysis for the owner's
    other explicitly admitted operations and checkpoint lifecycle.
    """
    panel = point_panel_from_analysis(analysis)
    if type(panel) is ExperimentalB2PointPanel:
        return panel.solve_linear_many(
            cancellation_token=cancellation_token, on_case=on_case)
    if on_case is not None:
        raise ValueError("GE point-panel reference-linear solve has no case callback")
    owner = panel.create_analysis()
    try:
        return owner.solve_linear(panel.actions, cancellation_token=cancellation_token)
    finally:
        owner.close()
