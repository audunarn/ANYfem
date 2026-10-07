"""Native Project records for explicit experimental GE/B2 panel inputs.

These analyses own a mesh-native definition; Project geometry, load cases and
meshing defaults do not regenerate it. Saving inputs does not save an accepted
solver state or qualify an experimental model. Standalone consumer files retain
their existing meanings. Explicit selected-node footprints use the same GE
factory with their own strict schema. B2 footprints use their distinct static
nodal law and selected batches. Their distinct virgin reference-modal record
owns physical mass and a modes-only route. BL5 uses its separate explicit
Project selector and preserves each panel input schema and solver policy.
"""

from __future__ import annotations

import json

from .stiffened_shell import (
    ExperimentalGEPointPanel, ExperimentalGENodalRigidFootprintPanel, ExperimentalGEReferenceDistributingPanel,
    ExperimentalGEFiniteDistributingPanel, SCHEMA_FINITE_DISTRIBUTING,
    ExperimentalGEFiniteDistributingMaterialPanel, SCHEMA_FINITE_DISTRIBUTING_MATERIAL,
    SCHEMA_FINITE_DISTRIBUTING_DISPLACEMENT,
    SCHEMA_FINITE_DISTRIBUTING_MATERIAL_DISPLACEMENT,
    SCHEMA_REFERENCE_DISTRIBUTING,
    SCHEMA_NODAL_FOOTPRINT, SCHEMA_NODAL_FOOTPRINT_CHART_V2,
    ExperimentalGEReferenceModalPanel, SCHEMA_REFERENCE_MODAL,
)
from .stiffened_shell_b2 import (
    ExperimentalB2PointPanel, ExperimentalB2NodalRigidFootprintPanel,
    ExperimentalB2PhysicalRigidFootprintPanel, SCHEMA_PHYSICAL_FOOTPRINT as B2_PHYSICAL_FOOTPRINT_SCHEMA,
    ExperimentalB2ReferenceModalPanel, SCHEMA_REFERENCE_MODAL as B2_MODAL_SCHEMA,
    SCHEMA_NODAL_FOOTPRINT as B2_FOOTPRINT_SCHEMA,
    ExperimentalB2MixedPointPanel, SCHEMA_MIXED as B2_MIXED_SCHEMA,
    ExperimentalB2MixedReferenceModalPanel,
    SCHEMA_MIXED_REFERENCE_MODAL as B2_MIXED_MODAL_SCHEMA,
    SCHEMA_MIXED_FREE_REFERENCE_MODAL as B2_MIXED_FREE_MODAL_SCHEMA,
    ExperimentalB2BodyAccelerationPanel, SCHEMA_BODY_ACCELERATION,
    ExperimentalB2LoadedPointPanel, SCHEMA_LOADED_POINT, SCHEMA_LOADED_POINT_BODY,
    SCHEMA_MIXED_BODY_ACCELERATION, SCHEMA_FOOTPRINT_BODY_ACCELERATION,
)


SCHEMA = "anyfem.experimental-point-panel-project-v1"
STATUS = "EXPERIMENTAL_NOT_QUALIFIED"
GE_TYPE = "experimental_ge_point_panel"
B2_TYPE = "experimental_b2_point_panel"
GE_FORMULATION = "experimental-ge-point-panel"
B2_FORMULATION = "experimental-b2-point-panel"
BL5_SCHEMA = "anyfem.experimental-bl5-panel-project-v1"
BL5_TYPE = "experimental_ge_bl5_panel"
BL5_FORMULATION = "experimental-ge-bl5-panel"


def _owned_json(value):
    """Detach every nested input through the existing JSON-compatible schema."""
    try:
        return json.loads(json.dumps(value, allow_nan=False, sort_keys=True))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("finite JSON point-panel inputs required") from exc


def encode_bl5_panel(panel):
    """Own a distinct BL5 input, including native elastic v5 and mixed J2 v6."""
    from .stiffened_shell import ExperimentalGEBL5Panel
    if type(panel) is not ExperimentalGEBL5Panel:
        raise ValueError("exact ExperimentalGEBL5Panel required")
    record = _owned_json(dict(schema=BL5_SCHEMA, status=STATUS,
        definition_source="explicit-mesh-native", panel=panel.to_dict()))
    _decode_bl5_panel(BL5_TYPE, BL5_FORMULATION, record)
    return BL5_TYPE, BL5_FORMULATION, record


def _decode_bl5_panel(analysis_type, formulation, record):
    from .stiffened_shell import ExperimentalGEBL5Panel
    if ((analysis_type, formulation) != (BL5_TYPE, BL5_FORMULATION) or
            type(record) is not dict or set(record) != {
                "schema", "status", "definition_source", "panel"} or
            record["schema"] != BL5_SCHEMA or record["status"] != STATUS or
            record["definition_source"] != "explicit-mesh-native"):
        raise ValueError("strict experimental mesh-native BL5 route required")
    return ExperimentalGEBL5Panel.from_dict(_owned_json(record["panel"]))


def validate_bl5_analysis(analysis):
    if (analysis.target_kind != "none" or analysis.target_id != "" or
            analysis.settings or analysis.resource_policy or
            analysis.output_request_ids or analysis.output_requests):
        raise ValueError("mesh-native BL5 requires no Project target/settings/resources/output requests")
    panel = _decode_bl5_panel(analysis.type, analysis.beam_formulation,
                              analysis.beam_formulation_options)
    return encode_bl5_panel(panel)[2]


def bl5_panel_from_analysis(analysis):
    canonical = validate_bl5_analysis(analysis)
    return _decode_bl5_panel(analysis.type, analysis.beam_formulation, canonical)


def solve_bl5_panel(analysis, *, targets=None, cancellation_token=None,
                    increment_control=None, on_accepted=None,
                    max_iterations=12, max_backtracks=8):
    """Execute the stored BL5 inputs; checkpoint ownership stays with the solver.

Staged loads require explicit positions. Fixed nodal or distributed actions default to the
reference-linear route; explicit targets select admitted finite stepping.
"""
    panel = bl5_panel_from_analysis(analysis)
    if (panel.load_programme is not None or panel.distributed_programme is not None) and targets is None:
        raise ValueError("staged BL5 solve requires explicit programme-position targets")
    if targets is None and (increment_control is not None or on_accepted is not None or
                            max_iterations != 12 or max_backtracks != 8):
        raise ValueError("finite BL5 controls require explicit targets")
    owner = panel.create_analysis()
    try:
        if targets is None:
            return owner.solve_linear(panel.load_input, cancellation_token=cancellation_token)
        return owner.solve(panel.load_input, targets, cancellation_token=cancellation_token,
                           increment_control=increment_control, on_accepted=on_accepted,
                           max_iterations=max_iterations, max_backtracks=max_backtracks)
    finally:
        owner.close()


def encode_point_panel(panel):
    """Return the explicit selector pair and a detached native analysis payload."""
    if type(panel) in (ExperimentalGEPointPanel, ExperimentalGENodalRigidFootprintPanel, ExperimentalGEReferenceModalPanel, ExperimentalGEReferenceDistributingPanel, ExperimentalGEFiniteDistributingPanel, ExperimentalGEFiniteDistributingMaterialPanel):
        analysis_type, formulation = GE_TYPE, GE_FORMULATION
        payload = panel.to_dict()
    elif type(panel) in (ExperimentalB2PointPanel, ExperimentalB2MixedPointPanel, ExperimentalB2NodalRigidFootprintPanel, ExperimentalB2PhysicalRigidFootprintPanel, ExperimentalB2ReferenceModalPanel, ExperimentalB2MixedReferenceModalPanel, ExperimentalB2BodyAccelerationPanel, ExperimentalB2LoadedPointPanel):
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
        if type(payload) is dict and payload.get("schema") in (SCHEMA_FINITE_DISTRIBUTING_MATERIAL, SCHEMA_FINITE_DISTRIBUTING_MATERIAL_DISPLACEMENT):
            return ExperimentalGEFiniteDistributingMaterialPanel.from_dict(payload)
        if type(payload) is dict and payload.get("schema") in (SCHEMA_FINITE_DISTRIBUTING, SCHEMA_FINITE_DISTRIBUTING_DISPLACEMENT):
            return ExperimentalGEFiniteDistributingPanel.from_dict(payload)
        if type(payload) is dict and payload.get("schema") == SCHEMA_REFERENCE_DISTRIBUTING:
            return ExperimentalGEReferenceDistributingPanel.from_dict(payload)
        if type(payload) is dict and payload.get("schema") == SCHEMA_REFERENCE_MODAL:
            return ExperimentalGEReferenceModalPanel.from_dict(payload)
        if type(payload) is dict and payload.get("schema") in (SCHEMA_NODAL_FOOTPRINT, SCHEMA_NODAL_FOOTPRINT_CHART_V2):
            return ExperimentalGENodalRigidFootprintPanel.from_dict(payload)
        return ExperimentalGEPointPanel.from_dict(payload)
    if (analysis_type, formulation) == (B2_TYPE, B2_FORMULATION):
        if type(payload) is dict and payload.get("schema") == B2_PHYSICAL_FOOTPRINT_SCHEMA:
            return ExperimentalB2PhysicalRigidFootprintPanel.from_dict(payload)
        if type(payload) is dict and payload.get("schema") in (SCHEMA_LOADED_POINT, SCHEMA_LOADED_POINT_BODY):
            return ExperimentalB2LoadedPointPanel.from_dict(payload)
        if type(payload) is dict and payload.get("schema") in (
                SCHEMA_BODY_ACCELERATION, SCHEMA_MIXED_BODY_ACCELERATION,
                SCHEMA_FOOTPRINT_BODY_ACCELERATION):
            return ExperimentalB2BodyAccelerationPanel.from_dict(payload)
        if type(payload) is dict and payload.get("schema") in (B2_MIXED_MODAL_SCHEMA, B2_MIXED_FREE_MODAL_SCHEMA):
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
    programme (nodal or distributed) requires explicit positions in targets and
    delegates finite solve and position validation to the solver. Its returned
    result is not a durable accepted-state checkpoint. A body, fixed distributed,
    or pressure load uses reference-linear solve by default and finite solve
    with explicit targets.
    An explicit
    nodal footprint selects its declared V1 or V2 finite chart policy through
    its persisted input and requests finite stepping by supplying targets;
    selected group recovery/restart remains on the fresh public owner.
    An explicitly persisted finite distributing displacement request first
    accepts its ordinary-force seed on this fresh owner, then solves absolute
    signed physical translation targets. Its observer receives displacement
    publications only. To restore or continue an accepted prefix, use
    create_point_panel_analysis and the public owner's checkpoint/restore/solve;
    this convenience function creates a fresh owner on every call.
    """
    panel = point_panel_from_analysis(analysis)
    finite_distributing = type(panel) in (ExperimentalGEFiniteDistributingPanel, ExperimentalGEFiniteDistributingMaterialPanel)
    displacement = finite_distributing and panel.displacement_control is not None
    if finite_distributing and (targets is None or increment_control is not None or on_case is not None):
        raise ValueError("finite distributing Project input requires explicit targets; adaptive/batch controls unsupported")
    if displacement:
        # Refuse malformed requests before constructing an owner or publishing
        # a seed. Physical admission and predecessor target checks stay solver-owned.
        from .stiffened_shell_ge_distributing_displacement import (
            validate_targets, validate_execution_options,
        )
        targets = validate_targets(targets)
        validate_execution_options(on_accepted, max_iterations, max_backtracks)
    if type(panel) is ExperimentalGEReferenceDistributingPanel and (
            targets is not None or increment_control is not None or on_accepted is not None or
            on_case is not None or max_iterations != 12 or max_backtracks != 8):
        raise ValueError("reference-distributing Project input supports reference-linear nodal dead actions only; finite/adaptive/case controls are unsupported")
    if type(panel) in (ExperimentalGEReferenceModalPanel, ExperimentalB2ReferenceModalPanel, ExperimentalB2MixedReferenceModalPanel):
        if (targets is not None or increment_control is not None or on_accepted is not None or
                on_case is not None or max_iterations != 12 or max_backtracks != 8):
            raise ValueError("reference-modal Project input supports modes only, without finite or batch controls")
        return panel.modes(cancellation_token=cancellation_token)
    staged = type(panel) is ExperimentalGEPointPanel and (
        panel.load_programme is not None or panel.distributed_programme is not None)
    body_finite = (type(panel) is ExperimentalGEPointPanel and
                   panel.body_load is not None and targets is not None)
    distributed_finite = (type(panel) is ExperimentalGEPointPanel and
                          panel.distributed_load is not None and targets is not None)
    pressure_finite = (type(panel) is ExperimentalGEPointPanel and
                       panel.pressure_load is not None and targets is not None)
    footprint_finite = type(panel) is ExperimentalGENodalRigidFootprintPanel and targets is not None
    if not (finite_distributing or staged or footprint_finite or body_finite or distributed_finite or pressure_finite) and (
            targets is not None or increment_control is not None or on_accepted is not None or
            max_iterations != 12 or max_backtracks != 8):
        raise ValueError("finite solve controls require an explicit staged GE programme, footprint, or body/distributed/pressure load with targets")
    if type(panel) in (ExperimentalB2PointPanel, ExperimentalB2MixedPointPanel, ExperimentalB2NodalRigidFootprintPanel, ExperimentalB2PhysicalRigidFootprintPanel, ExperimentalB2BodyAccelerationPanel, ExperimentalB2LoadedPointPanel):
        return panel.solve_linear_many(
            cancellation_token=cancellation_token, on_case=on_case)
    if on_case is not None:
        raise ValueError("GE point-panel reference-linear solve has no case callback")
    if staged and targets is None:
        raise ValueError("staged GE solve requires explicit programme-position targets")
    owner = panel.create_analysis()
    try:
        if displacement:
            owner.solve(panel.actions, panel.displacement_seed_factors,
                        cancellation_token=cancellation_token,
                        max_iterations=max_iterations, max_backtracks=max_backtracks)
            return owner.solve(panel.actions, targets,
                               displacement_control=panel.displacement_control,
                               cancellation_token=cancellation_token, on_accepted=on_accepted,
                               max_iterations=max_iterations, max_backtracks=max_backtracks)
        if finite_distributing or staged or footprint_finite or body_finite or distributed_finite or pressure_finite:
            load = panel.actions if footprint_finite else panel.load_input
            return owner.solve(load, targets, cancellation_token=cancellation_token,
                               increment_control=increment_control, on_accepted=on_accepted,
                               max_iterations=max_iterations, max_backtracks=max_backtracks)
        load = panel.load_input if type(panel) is ExperimentalGEPointPanel else panel.actions
        return owner.solve_linear(load, cancellation_token=cancellation_token)
    finally:
        owner.close()
