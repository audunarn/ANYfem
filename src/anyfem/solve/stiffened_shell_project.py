"""Native Project records for explicit experimental GE/B2 panel inputs.

These analyses own a mesh-native definition; Project geometry, load cases and
meshing defaults do not regenerate it. Saving inputs does not save an accepted
solver state or qualify an experimental model. Standalone consumer files retain
their existing meanings. Explicit selected-node footprints use the same GE
factory with their own strict schema. B2 footprints use their distinct static
nodal law and selected batches. Their distinct virgin reference-modal record
owns physical mass and a modes-only route; BL5 remains unsupported here.
"""

from __future__ import annotations

import json

from .stiffened_shell import (
    ExperimentalGEPointPanel, ExperimentalGENodalRigidFootprintPanel,
    SCHEMA_NODAL_FOOTPRINT, SCHEMA_NODAL_FOOTPRINT_CHART_V2,
    ExperimentalGEReferenceModalPanel, SCHEMA_REFERENCE_MODAL,
)
from .stiffened_shell_b2 import (
    ExperimentalB2PointPanel, ExperimentalB2NodalRigidFootprintPanel,
    ExperimentalB2ReferenceModalPanel, SCHEMA_REFERENCE_MODAL as B2_MODAL_SCHEMA,
    SCHEMA_NODAL_FOOTPRINT as B2_FOOTPRINT_SCHEMA,
    ExperimentalB2MixedPointPanel, SCHEMA_MIXED as B2_MIXED_SCHEMA,
    ExperimentalB2MixedReferenceModalPanel,
    SCHEMA_MIXED_REFERENCE_MODAL as B2_MIXED_MODAL_SCHEMA,
)


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
    if type(panel) in (ExperimentalGEPointPanel, ExperimentalGENodalRigidFootprintPanel, ExperimentalGEReferenceModalPanel):
        analysis_type, formulation = GE_TYPE, GE_FORMULATION
        payload = panel.to_dict()
    elif type(panel) in (ExperimentalB2PointPanel, ExperimentalB2MixedPointPanel, ExperimentalB2NodalRigidFootprintPanel, ExperimentalB2ReferenceModalPanel, ExperimentalB2MixedReferenceModalPanel):
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
        if type(payload) is dict and payload.get("schema") == SCHEMA_REFERENCE_MODAL:
            return ExperimentalGEReferenceModalPanel.from_dict(payload)
        if type(payload) is dict and payload.get("schema") in (SCHEMA_NODAL_FOOTPRINT, SCHEMA_NODAL_FOOTPRINT_CHART_V2):
            return ExperimentalGENodalRigidFootprintPanel.from_dict(payload)
        return ExperimentalGEPointPanel.from_dict(payload)
    if (analysis_type, formulation) == (B2_TYPE, B2_FORMULATION):
        if type(payload) is dict and payload.get("schema") == B2_MIXED_MODAL_SCHEMA:
            return ExperimentalB2MixedReferenceModalPanel.from_dict(payload)
        if type(payload) is dict and payload.get("schema") == B2_MIXED_SCHEMA:
            return ExperimentalB2MixedPointPanel(json.dumps(payload, allow_nan=False).encode("ascii"))
        if type(payload) is dict and payload.get("schema") == B2_MODAL_SCHEMA:
            return ExperimentalB2ReferenceModalPanel.from_dict(payload)
        consumer = (ExperimentalB2NodalRigidFootprintPanel if type(payload) is dict and
                    payload.get("schema") == B2_FOOTPRINT_SCHEMA else ExperimentalB2PointPanel)
        return consumer(
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


def solve_point_panel(analysis, *, cancellation_token=None, on_case=None,
                      targets=None, increment_control=None, on_accepted=None,
                      max_iterations=12, max_backtracks=8):
    """Run the explicit stored load route with the existing owner semantics.

    B2 retains its ordered case IDs, selected recovery and cancelled prefix. GE
    has one stored action set and returns its own LinearResult; it has no batch
    case IDs or on_case callback. Use create_point_panel_analysis for the owner's
    other explicitly admitted operations and checkpoint lifecycle. A staged GE
    programme requires explicit positions in targets and delegates finite solve;
    its returned result is not a durable accepted-state checkpoint. An explicit
    nodal footprint selects its declared V1 or V2 finite chart policy through
    its persisted input and requests finite stepping by supplying targets;
    selected group recovery/restart remains on the fresh public owner.
    """
    panel = point_panel_from_analysis(analysis)
    if type(panel) in (ExperimentalGEReferenceModalPanel, ExperimentalB2ReferenceModalPanel, ExperimentalB2MixedReferenceModalPanel):
        if (targets is not None or increment_control is not None or on_accepted is not None or
                on_case is not None or max_iterations != 12 or max_backtracks != 8):
            raise ValueError("reference-modal Project input supports modes only, without finite or batch controls")
        return panel.modes(cancellation_token=cancellation_token)
    staged = type(panel) is ExperimentalGEPointPanel and panel.load_programme is not None
    footprint_finite = type(panel) is ExperimentalGENodalRigidFootprintPanel and targets is not None
    if not staged and not footprint_finite and (targets is not None or increment_control is not None or
                       on_accepted is not None or max_iterations != 12 or max_backtracks != 8):
        raise ValueError("finite solve controls require an explicit staged GE programme")
    if type(panel) in (ExperimentalB2PointPanel, ExperimentalB2MixedPointPanel, ExperimentalB2NodalRigidFootprintPanel):
        return panel.solve_linear_many(
            cancellation_token=cancellation_token, on_case=on_case)
    if on_case is not None:
        raise ValueError("GE point-panel reference-linear solve has no case callback")
    if staged and targets is None:
        raise ValueError("staged GE solve requires explicit programme-position targets")
    owner = panel.create_analysis()
    try:
        if staged or footprint_finite:
            load = panel.load_input if staged else panel.actions
            return owner.solve(load, targets, cancellation_token=cancellation_token,
                               increment_control=increment_control, on_accepted=on_accepted,
                               max_iterations=max_iterations, max_backtracks=max_backtracks)
        return owner.solve_linear(panel.actions, cancellation_token=cancellation_token)
    finally:
        owner.close()
