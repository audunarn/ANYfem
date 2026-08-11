"""Adapt displayable ANYfem solutions to immutable result sidecars.

The postprocessing wrappers deliberately present a small, convenient surface,
while solver results retain analysis-specific histories.  This module is the
single boundary between those two contracts and :meth:`ArtifactStore.write_result`.
It writes only quantities that are actually present: an imported stress-only
result does not acquire a zero displacement field, and an envelope-only
transient does not acquire invented time frames.

All field arrays are frame-major.  Even a static field has a leading frame
axis, so ``ResultField.read(0)`` always returns the complete spatial field.
Entity IDs are stored alongside each spatial field in ``tables`` because the
v1 result sidecar has no built-in association dataset.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from importlib import metadata as importlib_metadata
import math
from pathlib import Path
import platform
import re
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from ..model.records import ArtifactRef, ResultQuantityDescriptor
from .artifacts import ArtifactStore

__all__ = [
    "ResultArtifactPayload",
    "build_result_artifact_inputs",
    "result_artifact_payload",
    "write_solution_artifact",
]


_DOF_COMPONENTS = ("ux", "uy", "uz", "rx", "ry", "rz")
_VECTOR_COMPONENTS = ("x", "y", "z")
_SAFE_KEY = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class ResultArtifactPayload:
    """The analysis-dependent portion of ``ArtifactStore.write_result``.

    Identity and hash arguments are intentionally absent.  They belong to the
    immutable job submission and are supplied by :func:`write_solution_artifact`.
    """

    fields: Mapping[str, tuple[ResultQuantityDescriptor, Any]] = field(
        default_factory=dict
    )
    frames: tuple[float, ...] = ()
    frame_kind: str = "static"
    histories: Mapping[str, tuple[Sequence[float], Sequence[float]]] = field(
        default_factory=dict
    )
    tables: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    summary: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: tuple[Any, ...] = ()
    partial: bool = False

    def write_result_inputs(self) -> dict[str, Any]:
        """Return fresh keyword arguments accepted by ``write_result``."""

        return {
            "fields": dict(self.fields),
            "frames": tuple(self.frames),
            "frame_kind": self.frame_kind,
            "histories": dict(self.histories),
            "tables": dict(self.tables),
            "provenance": dict(self.provenance),
            "summary": dict(self.summary),
            "diagnostics": tuple(self.diagnostics),
            "partial": bool(self.partial),
        }


@dataclass(frozen=True)
class _PackedDofs:
    values: np.ndarray
    node_ids: tuple[int, ...]
    components: tuple[str, ...]
    dof_indices: tuple[int, ...]
    rectangular: bool


class _Builder:
    def __init__(self, *, frames: Sequence[float], frame_kind: str) -> None:
        self.frames = tuple(float(value) for value in frames)
        self.frame_kind = str(frame_kind)
        self.fields: dict[str, tuple[ResultQuantityDescriptor, Any]] = {}
        self.histories: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.tables: dict[str, Any] = {}

    def key(self, value: object) -> str:
        base = _safe_key(value)
        candidate = base
        index = 2
        while candidate in self.fields:
            candidate = f"{base}_{index}"
            index += 1
        return candidate

    def add_field(
        self,
        key: str,
        values: Any,
        *,
        label: str,
        location: str,
        unit: str = "",
        components: Sequence[str] = (),
        basis: str = "global",
        frames: Sequence[float] = (),
        recovery: str = "native",
        reduction: str = "none",
        deformation_required: bool = False,
        provenance: Optional[Mapping[str, Any]] = None,
    ) -> Optional[str]:
        array = _numeric_array(values)
        if array is None or array.size == 0:
            return None
        # A scalar cannot be compressed/chunked by the v1 artifact writer.
        if array.ndim == 0:
            array = array.reshape(1)
        resolved = self.key(key)
        descriptor = ResultQuantityDescriptor(
            key=resolved,
            label=str(label),
            location=location,
            unit=str(unit),
            components=tuple(str(value) for value in components),
            basis=str(basis),
            frames=tuple(float(value) for value in frames),
            recovery=str(recovery),
            reduction=str(reduction),
            deformation_required=bool(deformation_required),
            provenance=_json_safe(dict(provenance or {})),
        )
        self.fields[resolved] = (descriptor, array)
        return resolved

    def add_history(
        self,
        key: str,
        x_values: Any,
        y_values: Any,
    ) -> None:
        x = _numeric_array(x_values)
        y = _numeric_array(y_values)
        if x is None or y is None:
            return
        x = x.reshape(-1)
        y = y.reshape(-1)
        if x.size == 0 or x.shape != y.shape:
            return
        self.histories[_safe_key(key)] = (x, y)

    def add_table(self, key: str, value: Any) -> None:
        if value is None:
            return
        safe_key = _safe_key(key)
        if isinstance(value, np.ndarray) and value.dtype.kind not in "OUS":
            self.tables[safe_key] = value.reshape(1) if value.ndim == 0 else value
            return
        safe = _json_safe(value)
        try:
            array = np.asarray(safe)
        except (TypeError, ValueError):
            # The artifact writer probes tables with np.asarray before choosing
            # JSON.  A wrapper object keeps ragged contact/state histories on
            # the JSON path instead of failing that probe.
            self.tables[safe_key] = {"values": safe}
            return
        if array.ndim == 0 and array.dtype.kind in "biufc":
            self.tables[safe_key] = array.reshape(1)
        else:
            self.tables[safe_key] = safe


def result_artifact_payload(
    solution: Any,
    *,
    provenance: Optional[Mapping[str, Any]] = None,
    summary: Optional[Mapping[str, Any]] = None,
    diagnostics: Sequence[Any] = (),
    partial: bool = False,
) -> ResultArtifactPayload:
    """Describe all persistable quantities actually present on ``solution``.

    The function accepts the current linear, modal, buckling, nonlinear,
    capacity, transient, impact and imported wrappers.  It is intentionally
    duck-typed at the solver-result boundary so newer raw result bundles can be
    retained without making this module depend on their concrete classes.
    """

    if solution is None:
        raise TypeError("solution must not be None")

    kind = type(solution).__name__
    frame_values, frame_kind = _primary_frames(solution)
    builder = _Builder(frames=frame_values, frame_kind=frame_kind)

    if kind == "ImportedSolution":
        _adapt_imported(builder, solution)
    elif kind == "ImpactSolution":
        _adapt_transient(builder, solution, impact=True)
    elif kind == "TransientSolution":
        _adapt_transient(builder, solution, impact=False)
    elif kind == "CapacitySolution":
        _adapt_nonlinear(builder, solution, capacity=True)
    elif kind == "NonlinearSolution":
        _adapt_nonlinear(builder, solution, capacity=False)
    elif kind == "ModalSolution":
        _adapt_modes(builder, solution, modal=True)
    elif kind == "BucklingSolution":
        _adapt_modes(builder, solution, modal=False)
    elif kind == "LinearBatchSolution":
        _adapt_linear_batch(builder, solution)
    elif kind == "LinearSolution" or hasattr(solution, "displacements"):
        _adapt_linear(builder, solution)
    else:
        raise TypeError(f"unsupported ANYfem solution wrapper {kind!r}")

    raw = _raw_result(solution)
    _add_common_raw_quantities(builder, raw, solution)

    built_provenance = _base_provenance(solution, raw)
    built_provenance.update(_json_safe(dict(provenance or {})))
    built_summary = _base_summary(solution, builder)
    built_summary.update(_json_safe(dict(summary or {})))
    built_diagnostics = _collect_diagnostics(solution, raw, diagnostics)

    return ResultArtifactPayload(
        fields=dict(builder.fields),
        frames=builder.frames,
        frame_kind=builder.frame_kind,
        histories=dict(builder.histories),
        tables=dict(builder.tables),
        provenance=built_provenance,
        summary=built_summary,
        diagnostics=built_diagnostics,
        partial=bool(partial),
    )


def build_result_artifact_inputs(
    solution: Any,
    **metadata: Any,
) -> dict[str, Any]:
    """Return only the keyword inputs consumed by ``ArtifactStore.write_result``."""

    return result_artifact_payload(solution, **metadata).write_result_inputs()


def write_solution_artifact(
    store: ArtifactStore,
    solution: Any,
    *,
    job_id: str,
    document_id: str,
    mesh_id: str,
    model_hash: str,
    mesh_hash: str,
    analysis_hash: str,
    provenance: Optional[Mapping[str, Any]] = None,
    summary: Optional[Mapping[str, Any]] = None,
    diagnostics: Sequence[Any] = (),
    partial: bool = False,
) -> ArtifactRef:
    """Write one solution using immutable submission identity and hashes."""

    if not isinstance(store, ArtifactStore):
        raise TypeError("store must be an ArtifactStore")
    hashes = {
        "model_hash": str(model_hash),
        "mesh_hash": str(mesh_hash),
        "analysis_hash": str(analysis_hash),
    }
    identity = _json_safe(dict(provenance or {}))
    supplied_submission = identity.pop("submission", {})
    submission = _solution_submission_identity(solution)
    if isinstance(supplied_submission, Mapping):
        submission.update(dict(supplied_submission))
    from ..document import canonical_hash

    submission.setdefault("project_hash", submission.get("document_hash", ""))
    submission.setdefault("job_hash", canonical_hash(hashes))
    identity.update({
        "job_id": str(job_id),
        "document_id": str(document_id),
        "mesh_id": str(mesh_id),
        "hashes": hashes,
        "submission": submission,
        "producer_versions": _producer_versions(),
    })
    payload = result_artifact_payload(
        solution,
        provenance=identity,
        summary=summary,
        diagnostics=diagnostics,
        partial=partial,
    )
    return store.write_result(
        job_id=str(job_id),
        document_id=str(document_id),
        mesh_id=str(mesh_id),
        model_hash=str(model_hash),
        mesh_hash=str(mesh_hash),
        analysis_hash=str(analysis_hash),
        **payload.write_result_inputs(),
    )


# ---------------------------------------------------------------------------
# Wrapper adapters
# ---------------------------------------------------------------------------
def _add_equivalent_plastic_strain(
    builder: _Builder,
    state_frames: Sequence[Mapping[int, Any]],
    *,
    frames: Sequence[float],
) -> None:
    """Persist max committed PEEQ per element and frame when it exists."""

    identifiers = sorted(
        {
            int(element_id)
            for states in state_frames
            for element_id, state in (states or {}).items()
            if isinstance(state, Mapping)
            and np.asarray(state.get("alpha", ())).size > 0
        }
    )
    if not identifiers:
        return
    columns = {identifier: index for index, identifier in enumerate(identifiers)}
    values = np.full((len(state_frames), len(identifiers)), np.nan, dtype=float)
    for frame_index, states in enumerate(state_frames):
        for element_id, state in (states or {}).items():
            identifier = int(element_id)
            if identifier not in columns or not isinstance(state, Mapping):
                continue
            alpha = np.asarray(state.get("alpha", ()), dtype=float).reshape(-1)
            if alpha.size and np.all(np.isfinite(alpha)):
                values[frame_index, columns[identifier]] = float(np.max(alpha))
    key = builder.add_field(
        "equivalent_plastic_strain",
        values,
        label="Equivalent plastic strain (PEEQ)",
        location="element",
        unit="1",
        components=("PEEQ",),
        frames=frames,
        recovery="committed_state",
        reduction="max integration point/layer/fibre",
        provenance={
            "source": "element_states.alpha",
            "missing_entities": "NaN, never zero-filled",
        },
    )
    if key is not None:
        builder.add_table(f"{key}_element_ids", np.asarray(identifiers, dtype=int))


def _adapt_linear(builder: _Builder, solution: Any) -> None:
    layout = _layout(solution)
    _add_dof_field(
        builder,
        "displacement",
        "Displacement",
        getattr(solution, "displacements", None),
        layout,
        frames=builder.frames or (0.0,),
        kind="displacement",
        deformation_required=True,
    )
    cached_stress = getattr(solution, "_stress", None)
    if cached_stress is not None:
        _add_static_stresses(builder, cached_stress, prefix="stress")


def _adapt_linear_batch(builder: _Builder, solution: Any) -> None:
    shapes = tuple(getattr(solution, "shapes", ()) or ())
    names = tuple(str(item) for item in getattr(solution, "case_names", ()))
    if len(names) != len(shapes):
        raise ValueError("linear batch case labels do not match its shapes")
    vectors = _stack_present(
        getattr(shape, "displacements", None) for shape in shapes
    )
    _add_dof_field(
        builder,
        "displacement",
        "Linear-case displacement",
        vectors,
        _layout(solution),
        frames=builder.frames,
        kind="displacement",
        deformation_required=True,
        provenance={"load_cases": list(names), "shared_factorization": True},
    )
    builder.add_table("load_case_names", np.asarray(names, dtype=object))


def _adapt_modes(builder: _Builder, solution: Any, *, modal: bool) -> None:
    shapes = tuple(getattr(solution, "shapes", ()) or ())
    layout = _layout(solution)
    vectors = _stack_present(getattr(shape, "displacements", None) for shape in shapes)
    _add_dof_field(
        builder,
        "displacement",
        "Mode shape",
        vectors,
        layout,
        frames=builder.frames,
        kind="mode_shape",
        unit="normalized",
        deformation_required=True,
        provenance={"normalization": "solver_native"},
    )
    values = np.asarray([float(getattr(shape, "value", 0.0)) for shape in shapes])
    if values.size:
        quantity = "frequency" if modal else "buckling_factor"
        label = "Frequency" if modal else "Buckling factor"
        unit = "Hz" if modal else "1"
        builder.add_field(
            quantity,
            values.reshape(-1, 1),
            label=label,
            location="history",
            unit=unit,
            components=("value",),
            frames=builder.frames,
        )
        builder.add_history(quantity, np.arange(1, values.size + 1), values)


def _adapt_nonlinear(
    builder: _Builder,
    solution: Any,
    *,
    capacity: bool,
) -> None:
    layout = _layout(solution)
    raw = getattr(solution, "raw_result", None)
    snapshots = tuple(getattr(raw, "snapshots", ()) or ())
    if snapshots:
        vectors = _stack_present(
            getattr(snapshot, "displacements", None) for snapshot in snapshots
        )
        loads = tuple(float(getattr(snapshot, "load_factor")) for snapshot in snapshots)
        builder.frames = loads
        builder.frame_kind = "load_factor"
        _add_dof_field(
            builder,
            "displacement",
            "Capacity displacement" if capacity else "Nonlinear displacement",
            vectors,
            layout,
            frames=loads,
            kind="displacement",
            recovery="committed_state",
            deformation_required=True,
            provenance={"increment_snapshots": True},
        )
        builder.add_table(
            "increment_element_states",
            [
                {
                    "step_index": int(getattr(snapshot, "step_index", index)),
                    "load_factor": float(getattr(snapshot, "load_factor", loads[index])),
                    "control_value": getattr(snapshot, "control_value", None),
                    "element_states": getattr(snapshot, "element_states", {}),
                }
                for index, snapshot in enumerate(snapshots)
            ],
        )
        _add_equivalent_plastic_strain(
            builder,
            [getattr(snapshot, "element_states", {}) for snapshot in snapshots],
            frames=loads,
        )
    else:
        load = float(getattr(solution, "load_factor", getattr(solution, "value", 0.0)))
        builder.frames = (load,)
        builder.frame_kind = "load_factor"
        _add_dof_field(
            builder,
            "displacement",
            "Capacity displacement" if capacity else "Nonlinear displacement",
            getattr(solution, "displacements", None),
            layout,
            frames=(load,),
            kind="displacement",
            recovery="committed_state" if raw is not None else "native",
            deformation_required=True,
            provenance={"increment_snapshots": False},
        )

    _add_nonlinear_histories(builder, solution)
    element_states = getattr(raw, "element_states", None)
    if isinstance(element_states, Mapping) and element_states:
        builder.add_table("final_element_states", element_states)
        if not snapshots:
            _add_equivalent_plastic_strain(
                builder, [element_states], frames=builder.frames
            )

    if capacity:
        buckling = getattr(solution, "buckling", None)
        if buckling is not None:
            shapes = tuple(getattr(buckling, "shapes", ()) or ())
            vectors = _stack_present(
                getattr(shape, "displacements", None) for shape in shapes
            )
            factors = tuple(float(getattr(shape, "value", 0.0)) for shape in shapes)
            _add_dof_field(
                builder,
                "buckling_mode_shape",
                "Buckling mode shape",
                vectors,
                layout,
                frames=factors,
                kind="mode_shape",
                unit="normalized",
                deformation_required=True,
                provenance={"capacity_stage": "elastic_buckling"},
            )
            if factors:
                builder.add_field(
                    "buckling_factor",
                    np.asarray(factors).reshape(-1, 1),
                    label="Buckling factor",
                    location="history",
                    unit="1",
                    components=("value",),
                    frames=factors,
                    provenance={"capacity_stage": "elastic_buckling"},
                )
        workflow = _raw_result(solution)
        static = getattr(workflow, "static_displacements", None)
        _add_dof_field(
            builder,
            "static_displacement",
            "Reference static displacement",
            static,
            layout,
            frames=(0.0,),
            kind="displacement",
            deformation_required=True,
            provenance={"capacity_stage": "reference_static"},
        )


def _adapt_transient(
    builder: _Builder,
    solution: Any,
    *,
    impact: bool,
) -> None:
    raw = _raw_result(solution)
    layout = _layout(solution)
    times = _time_values(solution, raw)
    builder.frames = times
    builder.frame_kind = "time"

    if raw is None:
        vectors = _stack_present(
            getattr(shape, "displacements", None)
            for shape in tuple(getattr(solution, "shapes", ()) or ())
        )
        _add_dof_field(
            builder,
            "displacement",
            "Displacement",
            vectors,
            layout,
            frames=times,
            kind="displacement",
            deformation_required=True,
        )
        return

    storage_mode = _history_storage_mode(raw)
    selected_indices = _history_dof_indices(raw, layout)
    for attribute, key, label, quantity_kind in (
        ("displacements", "displacement", "Displacement", "displacement"),
        ("velocities", "velocity", "Velocity", "velocity"),
        ("accelerations", "acceleration", "Acceleration", "acceleration"),
    ):
        _add_dof_field(
            builder,
            key,
            label,
            getattr(raw, attribute, None),
            layout,
            frames=times,
            kind=quantity_kind,
            dof_indices=selected_indices if storage_mode == "selected" else None,
            storage_mode=storage_mode,
            deformation_required=key == "displacement",
        )

    if not _has_values(getattr(raw, "displacements", None)):
        _add_node_displacement_histories(builder, raw, times)

    for attribute, key, label, quantity_kind in (
        (
            "displacement_envelope",
            "displacement_envelope",
            "Displacement envelope",
            "displacement",
        ),
        ("velocity_envelope", "velocity_envelope", "Velocity envelope", "velocity"),
        (
            "acceleration_envelope",
            "acceleration_envelope",
            "Acceleration envelope",
            "acceleration",
        ),
    ):
        values = getattr(raw, attribute, None)
        if _has_values(values):
            _add_dof_field(
                builder,
                key,
                label,
                values,
                layout,
                frames=(),
                kind=quantity_kind,
                storage_mode="envelope",
                recovery="envelope",
                reduction="max_abs",
                deformation_required=False,
                provenance={"signed": False, "source_frames": list(times)},
            )

    _add_impulses(builder, raw, layout)
    _add_energy_histories(builder, getattr(raw, "diagnostics", {}), times)
    _add_stress_history(
        builder,
        getattr(raw, "stress_history", None),
        times,
        prefix="stress_history",
        recovery="recovered",
    )

    if impact:
        _add_impact_quantities(builder, solution, raw, times)


def _adapt_imported(builder: _Builder, solution: Any) -> None:
    imported = getattr(solution, "results", None)
    layout = _layout(solution)
    components = tuple(
        component for component in _DOF_COMPONENTS
        if component in set(getattr(solution, "components", ()) or ())
    )
    if components:
        node_ids = tuple(
            sorted(
                int(value)
                for value in (getattr(imported, "displacements", {}) or {})
                if int(value) in set(layout[0])
            )
        )
        _add_dof_field(
            builder,
            "displacement",
            "Imported displacement",
            getattr(solution, "displacements", None),
            layout,
            frames=(0.0,),
            kind="displacement",
            component_indices=tuple(_DOF_COMPONENTS.index(value) for value in components),
            node_ids=node_ids,
            recovery="imported",
            deformation_required={"ux", "uy", "uz"}.issubset(components),
            provenance={"available_components": list(components)},
        )

    for name, imported_field in dict(getattr(solution, "fields", {}) or {}).items():
        values = dict(getattr(imported_field, "values", {}) or {})
        if not values:
            continue
        ids = np.asarray(sorted(int(value) for value in values), dtype=np.int64)
        data = np.asarray([values[int(value)] for value in ids], dtype=float)
        location = "element" if bool(getattr(imported_field, "per_element", False)) else "node"
        key = builder.add_field(
            f"stress_{name}",
            data.reshape(1, -1, 1),
            label=f"Imported {name}",
            location=location,
            unit=str(getattr(imported_field, "unit", "Pa")),
            components=(str(name),),
            frames=(0.0,),
            recovery="imported",
            reduction=str(getattr(imported_field, "reduction", "none")),
            provenance={"source": _import_source(imported)},
        )
        if key is not None:
            builder.add_table(f"{key}_{location}_ids", ids)

    _add_reactions(builder, getattr(imported, "reactions", None), recovery="imported")
    for attribute, key, label, unit in (
        ("frequencies", "frequency", "Frequency", "Hz"),
        ("buckling_factors", "buckling_factor", "Buckling factor", "1"),
    ):
        values = _numeric_array(getattr(imported, attribute, None))
        if values is None or values.size == 0:
            continue
        values = values.reshape(-1)
        frames = tuple(float(index + 1) for index in range(values.size))
        builder.add_field(
            key,
            values.reshape(-1, 1),
            label=label,
            location="history",
            unit=unit,
            components=("value",),
            frames=frames,
            recovery="imported",
        )
        builder.add_history(key, frames, values)
    warnings = tuple(getattr(imported, "warnings", ()) or ())
    if warnings:
        builder.add_table("import_warnings", warnings)


# ---------------------------------------------------------------------------
# Quantity extraction
# ---------------------------------------------------------------------------
def _add_dof_field(
    builder: _Builder,
    key: str,
    label: str,
    values: Any,
    layout: tuple[tuple[int, ...], Any, int],
    *,
    frames: Sequence[float],
    kind: str,
    dof_indices: Optional[Sequence[int]] = None,
    component_indices: Sequence[int] = tuple(range(6)),
    node_ids: Optional[Sequence[int]] = None,
    storage_mode: str = "full",
    unit: Optional[str] = None,
    recovery: str = "native",
    reduction: str = "none",
    deformation_required: bool = False,
    provenance: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    if not _has_values(values):
        return None
    packed = _pack_dofs(
        values,
        layout,
        dof_indices=dof_indices,
        component_indices=component_indices,
        node_ids=node_ids,
    )
    metadata = {
        "history_storage_mode": str(storage_mode),
        "global_dof_indices": list(packed.dof_indices),
        **dict(provenance or {}),
    }
    field_frames = tuple(float(value) for value in frames)
    if field_frames and (
        packed.values.ndim == 0 or packed.values.shape[0] != len(field_frames)
    ):
        metadata["unassociated_frame_values"] = list(field_frames)
        field_frames = ()
    if packed.rectangular:
        resolved = builder.add_field(
            key,
            packed.values,
            label=label,
            location="node",
            unit=unit or _dof_unit(kind, packed.components),
            components=packed.components,
            frames=field_frames,
            recovery=recovery,
            reduction=reduction,
            deformation_required=deformation_required,
            provenance=metadata,
        )
        if resolved is not None:
            builder.add_table(
                f"{resolved}_node_ids", np.asarray(packed.node_ids, dtype=np.int64)
            )
        return resolved

    # Irregular selected DOFs cannot honestly be advertised as a rectangular
    # node/component field.  Preserve the exact columns as a typed global
    # history and document their global-DOF identities.
    return builder.add_field(
        f"{key}_selected_dofs",
        packed.values,
        label=f"{label} (selected DOFs)",
        location="global",
        unit=unit or _dof_unit(kind, ()),
        components=(),
        frames=field_frames,
        recovery=recovery,
        reduction=reduction,
        deformation_required=False,
        provenance={**metadata, "storage": "columnar_selected_dofs"},
    )


def _pack_dofs(
    values: Any,
    layout: tuple[tuple[int, ...], Any, int],
    *,
    dof_indices: Optional[Sequence[int]] = None,
    component_indices: Sequence[int] = tuple(range(6)),
    node_ids: Optional[Sequence[int]] = None,
) -> _PackedDofs:
    array = _numeric_array(values)
    if array is None:
        return _PackedDofs(np.zeros((0, 0)), (), (), (), False)
    if array.ndim == 1:
        array = array.reshape(1, -1)
    elif array.ndim != 2:
        return _PackedDofs(array, (), (), (), False)

    all_node_ids, manager, total_dofs = layout
    width = int(array.shape[1])
    if dof_indices is None:
        if width != total_dofs:
            return _PackedDofs(array, (), (), (), False)
        indices = tuple(range(total_dofs))
    else:
        indices = tuple(int(value) for value in dof_indices)
        if len(indices) != width:
            return _PackedDofs(array, (), (), indices, False)

    allowed_nodes = set(all_node_ids if node_ids is None else (int(v) for v in node_ids))
    allowed_components = tuple(int(value) for value in component_indices)
    columns: dict[int, dict[int, int]] = {}
    for column, global_dof in enumerate(indices):
        try:
            node_id, component, _name = manager.get_dof_info(int(global_dof))
        except Exception:
            continue
        node_id = int(node_id)
        component = int(component)
        if node_id not in allowed_nodes or component not in allowed_components:
            continue
        columns.setdefault(node_id, {})[component] = column

    ordered_nodes = tuple(sorted(columns))
    if not ordered_nodes:
        return _PackedDofs(array, (), (), indices, False)
    patterns = {tuple(sorted(columns[node_id])) for node_id in ordered_nodes}
    if len(patterns) != 1:
        return _PackedDofs(array, ordered_nodes, (), indices, False)
    pattern = next(iter(patterns))
    if pattern != tuple(value for value in allowed_components if value in pattern):
        return _PackedDofs(array, ordered_nodes, (), indices, False)
    selected = np.asarray(
        [[columns[node_id][component] for component in pattern] for node_id in ordered_nodes],
        dtype=np.intp,
    )
    packed = array[:, selected]
    components = tuple(_DOF_COMPONENTS[index] for index in pattern)
    used_indices = tuple(indices[int(column)] for column in selected.reshape(-1))
    return _PackedDofs(packed, ordered_nodes, components, used_indices, True)


def _add_nonlinear_histories(builder: _Builder, solution: Any) -> None:
    try:
        values = solution.history()
    except Exception:
        values = {}
    if not isinstance(values, Mapping):
        return
    step = _numeric_array(values.get("step"))
    if step is None or step.size == 0:
        return
    step = step.reshape(-1)
    for key, item in values.items():
        if key == "step":
            continue
        data = _numeric_array(item)
        if data is None or data.size != step.size:
            continue
        data = data.reshape(-1)
        builder.add_history(key, step, data)
        unit = "1"
        builder.add_field(
            key,
            data.reshape(-1, 1),
            label=key.replace("_", " ").title(),
            location="history",
            unit=unit,
            components=("value",),
            frames=tuple(float(value) for value in step),
        )


def _add_node_displacement_histories(
    builder: _Builder,
    raw: Any,
    times: Sequence[float],
) -> None:
    histories = getattr(raw, "node_histories", None)
    if not isinstance(histories, Mapping) or not histories:
        return
    ids = []
    rows = []
    for node_id in sorted(histories, key=int):
        values = _numeric_array(histories[node_id])
        if values is None or values.ndim != 2 or values.shape[0] != len(times):
            continue
        ids.append(int(node_id))
        rows.append(values)
    if not rows or len({row.shape for row in rows}) != 1:
        builder.add_table("node_displacement_histories", histories)
        return
    values = np.stack(rows, axis=1)
    component_count = int(values.shape[-1])
    key = builder.add_field(
        "selected_node_displacement",
        values,
        label="Selected-node displacement",
        location="node",
        unit=_dof_unit("displacement", _DOF_COMPONENTS[:component_count]),
        components=_DOF_COMPONENTS[:component_count],
        frames=times,
        deformation_required=False,
        provenance={"history_storage_mode": "selected_node_history"},
    )
    if key is not None:
        builder.add_table(f"{key}_node_ids", np.asarray(ids, dtype=np.int64))


def _add_impulses(builder: _Builder, raw: Any, layout) -> None:
    _add_dof_field(
        builder,
        "load_impulse",
        "Nodal load impulse",
        getattr(raw, "load_impulse", None),
        layout,
        frames=(0.0,),
        kind="impulse",
        deformation_required=False,
    )
    for attribute, key, label, unit, components in (
        ("force_impulse", "force_impulse", "Force impulse", "N*s", _VECTOR_COMPONENTS),
        ("moment_impulse", "moment_impulse", "Moment impulse", "N*m*s", _VECTOR_COMPONENTS),
        ("sphere_impulse", "impactor_impulse", "Impactor impulse", "N*s", _VECTOR_COMPONENTS),
    ):
        values = getattr(raw, attribute, None)
        if not _has_values(values):
            continue
        builder.add_field(
            key,
            np.asarray(values, dtype=float).reshape(1, -1),
            label=label,
            location="global",
            unit=unit,
            components=components,
            frames=(0.0,),
        )


def _add_energy_histories(
    builder: _Builder,
    diagnostics: Any,
    times: Sequence[float],
) -> None:
    if not isinstance(diagnostics, Mapping):
        return
    x = np.asarray(times, dtype=float).reshape(-1)
    for key, label, unit in (
        ("kinetic_energy", "Kinetic energy", "J"),
        ("strain_energy", "Strain energy", "J"),
        ("sphere_kinetic_energy", "Impactor kinetic energy", "J"),
        ("plastic_work_proxy", "Plastic work proxy", "1"),
    ):
        values = _numeric_array(diagnostics.get(key))
        if values is None:
            continue
        values = values.reshape(-1)
        if values.size == 0 or values.size != x.size:
            builder.add_table(key, diagnostics.get(key))
            continue
        builder.add_field(
            key,
            values.reshape(-1, 1),
            label=label,
            location="history",
            unit=unit,
            components=("value",),
            frames=times,
        )
        builder.add_history(key, x, values)


def _add_impact_quantities(
    builder: _Builder,
    solution: Any,
    raw: Any,
    times: Sequence[float],
) -> None:
    for attribute, key, label, unit, components in (
        (
            "sphere_positions",
            "impactor_position",
            "Impactor position",
            "m",
            _VECTOR_COMPONENTS,
        ),
        (
            "sphere_velocities",
            "impactor_velocity",
            "Impactor velocity",
            "m/s",
            _VECTOR_COMPONENTS,
        ),
        (
            "sphere_accelerations",
            "impactor_acceleration",
            "Impactor acceleration",
            "m/s^2",
            _VECTOR_COMPONENTS,
        ),
        (
            "contact_force_history",
            "contact_force",
            "Contact force",
            "N",
            ("fx", "fy", "fz"),
        ),
    ):
        values = getattr(raw, attribute, getattr(solution, attribute, None))
        array = _numeric_array(values)
        if array is None or array.size == 0:
            continue
        if array.ndim == 1:
            array = array.reshape(-1, 1)
        builder.add_field(
            key,
            array,
            label=label,
            location="history",
            unit=unit,
            components=components[: array.shape[-1]],
            frames=times if array.shape[0] == len(times) else (),
        )
        if key == "contact_force" and array.shape[0] == len(times):
            magnitude = (
                np.linalg.norm(array, axis=1)
                if array.ndim == 2 and array.shape[1] > 1
                else np.abs(array.reshape(-1))
            )
            builder.add_history("contact_force_magnitude", times, magnitude)

    active = getattr(raw, "active_contact_history", None)
    if active:
        builder.add_table("active_contact_history", active)
    diagnostics = getattr(raw, "diagnostics", {})
    if isinstance(diagnostics, Mapping):
        for key, value in diagnostics.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("damage", "fracture", "erosion")):
                builder.add_table(str(key), value)
        committed = diagnostics.get("state_von_mises_history")
        _add_stress_history(
            builder,
            committed,
            times,
            prefix="committed_von_mises_history",
            recovery="committed_state",
            scalar_component="von_mises",
        )


def _add_common_raw_quantities(builder: _Builder, raw: Any, solution: Any) -> None:
    if raw is not None:
        _add_static_stresses(builder, raw, prefix="stress")
        _add_reactions(builder, getattr(raw, "reactions", None))
    # Capacity wraps the nonlinear result one level down.
    nonlinear = getattr(raw, "nonlinear_result", None)
    if nonlinear is not None:
        _add_static_stresses(builder, nonlinear, prefix="nonlinear_stress")
        _add_reactions(builder, getattr(nonlinear, "reactions", None))
    info = getattr(solution, "info", None)
    if isinstance(info, Mapping):
        _add_reactions(builder, info.get("reactions"))
        _add_stress_history(
            builder,
            info.get("stress_history"),
            builder.frames,
            prefix="stress_history",
            recovery="recovered",
        )


def _add_reactions(
    builder: _Builder,
    reactions: Any,
    *,
    recovery: str = "native",
) -> None:
    if not isinstance(reactions, Mapping) or not reactions:
        return
    ids = []
    rows = []
    for node_id in sorted(reactions, key=int):
        row = _numeric_array(reactions[node_id])
        if row is None or row.size == 0:
            continue
        ids.append(int(node_id))
        rows.append(row.reshape(-1))
    if not rows or len({row.shape for row in rows}) != 1:
        builder.add_table("reactions", reactions)
        return
    values = np.vstack(rows)
    components = ("fx", "fy", "fz", "mx", "my", "mz")[: values.shape[1]]
    key = builder.add_field(
        "reaction",
        values.reshape(1, values.shape[0], values.shape[1]),
        label="Reaction",
        location="node",
        unit="mixed:N,N*m" if values.shape[1] > 3 else "N",
        components=components,
        frames=(0.0,),
        recovery=recovery,
    )
    if key is not None:
        builder.add_table(f"{key}_node_ids", np.asarray(ids, dtype=np.int64))


def _add_static_stresses(builder: _Builder, result: Any, *, prefix: str) -> None:
    stresses = getattr(result, "element_stresses", None)
    if not isinstance(stresses, Mapping) or not stresses:
        return
    components = sorted(
        {
            str(component)
            for values in stresses.values()
            if isinstance(values, Mapping)
            for component in values
        }
    )
    provenance = _stress_provenance(result)
    for component in components:
        ids = []
        rows = []
        for element_id in sorted(stresses, key=int):
            values = stresses[element_id]
            if not isinstance(values, Mapping) or component not in values:
                continue
            array = _numeric_array(values[component])
            if array is None or array.size == 0:
                continue
            ids.append(int(element_id))
            rows.append(array)
        if not rows or len({row.shape for row in rows}) != 1:
            continue
        spatial = np.stack(rows)
        scalar_per_element = spatial.ndim == 1
        if scalar_per_element:
            spatial = spatial[:, np.newaxis]
        location = "element" if scalar_per_element else "integration_point"
        key = builder.add_field(
            f"{prefix}_{component}",
            spatial.reshape((1,) + spatial.shape),
            label=component.replace("_", " ").title(),
            location=location,
            unit="Pa",
            components=(component,),
            basis="element_local",
            frames=(0.0,),
            recovery="recovered",
            provenance=provenance,
        )
        if key is not None:
            builder.add_table(f"{key}_element_ids", np.asarray(ids, dtype=np.int64))


def _add_stress_history(
    builder: _Builder,
    history: Any,
    frames: Sequence[float],
    *,
    prefix: str,
    recovery: str,
    scalar_component: Optional[str] = None,
) -> None:
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes)) or not history:
        return
    mappings = [value for value in history if isinstance(value, Mapping)]
    if len(mappings) != len(history):
        builder.add_table(prefix, history)
        return
    component_names = (
        (scalar_component,)
        if scalar_component is not None
        else tuple(
            sorted(
                {
                    str(component)
                    for frame in mappings
                    for values in frame.values()
                    if isinstance(values, Mapping)
                    for component in values
                }
            )
        )
    )
    for component in component_names:
        ids = tuple(sorted({int(value) for frame in mappings for value in frame}))
        rows = []
        complete = bool(ids)
        expected_shape = None
        for frame in mappings:
            frame_rows = []
            for element_id in ids:
                entry = frame.get(element_id, frame.get(str(element_id)))
                value = (
                    entry
                    if scalar_component is not None
                    else entry.get(component) if isinstance(entry, Mapping) else None
                )
                array = _numeric_array(value)
                if array is None or array.size == 0:
                    complete = False
                    break
                expected_shape = array.shape if expected_shape is None else expected_shape
                if array.shape != expected_shape:
                    complete = False
                    break
                frame_rows.append(array)
            if not complete:
                break
            rows.append(np.stack(frame_rows))
        if not complete:
            builder.add_table(f"{prefix}_{component}", history)
            continue
        values = np.stack(rows)
        if values.ndim == 2:
            values = values[..., np.newaxis]
            location = "element"
        else:
            location = "integration_point"
        descriptor_frames = (
            tuple(float(value) for value in frames)
            if len(frames) == len(values)
            else tuple(float(index) for index in range(len(values)))
        )
        key = builder.add_field(
            f"{prefix}_{component}",
            values,
            label=f"{component.replace('_', ' ').title()} history",
            location=location,
            unit="Pa",
            components=(component,),
            basis="element_local",
            frames=descriptor_frames,
            recovery=recovery,
        )
        if key is not None:
            builder.add_table(f"{key}_element_ids", np.asarray(ids, dtype=np.int64))


# ---------------------------------------------------------------------------
# Introspection, metadata and sanitization
# ---------------------------------------------------------------------------
def _layout(solution: Any) -> tuple[tuple[int, ...], Any, int]:
    built = getattr(solution, "built", None)
    fe_model = getattr(built, "fe_model", None)
    fe_mesh = getattr(fe_model, "mesh", None)
    manager = getattr(fe_mesh, "dof_manager", None)
    if manager is None:
        raise ValueError("solution has no FE DOF manager")
    mapped_mesh = getattr(built, "mesh", None)
    nodes = getattr(mapped_mesh, "nodes", None)
    if not isinstance(nodes, Mapping):
        nodes = getattr(fe_mesh, "nodes", {})
    node_ids = tuple(sorted(int(value) for value in nodes))
    total_dofs = int(getattr(manager, "total_dofs", 0))
    return node_ids, manager, total_dofs


def _primary_frames(solution: Any) -> tuple[tuple[float, ...], str]:
    kind = type(solution).__name__
    if kind in ("TransientSolution", "ImpactSolution"):
        return _time_values(solution, _raw_result(solution)), "time"
    if kind == "ModalSolution":
        return tuple(float(value) for value in getattr(solution, "values", ())), "frequency"
    if kind == "BucklingSolution":
        return tuple(float(value) for value in getattr(solution, "values", ())), "buckling_factor"
    if kind == "LinearBatchSolution":
        return tuple(
            float(index) for index, _shape in enumerate(getattr(solution, "shapes", ()))
        ), "load_case"
    if kind in ("NonlinearSolution", "CapacitySolution"):
        return (float(getattr(solution, "value", 0.0)),), "load_factor"
    return (0.0,), "static"


def _time_values(solution: Any, raw: Any) -> tuple[float, ...]:
    values = getattr(raw, "times", None) if raw is not None else None
    if values is None:
        values = getattr(solution, "times", ())
    array = _numeric_array(values)
    return () if array is None else tuple(float(value) for value in array.reshape(-1))


def _raw_result(solution: Any) -> Any:
    info = getattr(solution, "info", None)
    if isinstance(info, Mapping) and info.get("raw") is not None:
        return info["raw"]
    raw = getattr(solution, "raw_result", None)
    return raw if raw is not None else None


def _history_storage_mode(raw: Any) -> str:
    direct = getattr(raw, "history_storage_mode", None)
    if direct:
        return str(direct)
    result_case = getattr(raw, "result_case", None)
    if isinstance(result_case, Mapping):
        recovery = result_case.get("recovery", {})
        if isinstance(recovery, Mapping) and recovery.get("history_storage_mode"):
            return str(recovery["history_storage_mode"])
    diagnostics = getattr(raw, "diagnostics", None)
    if isinstance(diagnostics, Mapping) and diagnostics.get("history_storage_mode"):
        return str(diagnostics["history_storage_mode"])
    return "full"


def _history_dof_indices(
    raw: Any,
    layout: tuple[tuple[int, ...], Any, int],
) -> Optional[tuple[int, ...]]:
    direct = getattr(raw, "history_dof_indices", None)
    if direct is not None:
        return tuple(int(value) for value in np.asarray(direct).reshape(-1))
    diagnostics = getattr(raw, "diagnostics", None)
    if isinstance(diagnostics, Mapping) and diagnostics.get("history_dof_indices") is not None:
        return tuple(int(value) for value in diagnostics["history_dof_indices"])
    node_histories = getattr(raw, "node_histories", None)
    if isinstance(node_histories, Mapping) and node_histories:
        _nodes, manager, _total = layout
        return tuple(
            int(dof)
            for node_id in node_histories
            for dof in manager.get_node_dofs(int(node_id))
        )
    return None


def _base_provenance(solution: Any, raw: Any) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "adapter": "anyfem.io.result_artifact",
        "solution_type": type(solution).__name__,
        "units": "SI",
    }
    status = getattr(solution, "status", None)
    if status is not None:
        provenance["status"] = str(status)
    result_case = getattr(raw, "result_case", None)
    if result_case is None and hasattr(raw, "info") and isinstance(raw.info, Mapping):
        result_case = raw.info.get("result_case")
    if result_case is None:
        solution_info = getattr(solution, "info", None)
        if isinstance(solution_info, Mapping):
            result_case = solution_info.get("result_case")
    if result_case is None:
        nonlinear = getattr(raw, "nonlinear_result", None)
        nonlinear_info = getattr(nonlinear, "info", None)
        if isinstance(nonlinear_info, Mapping):
            result_case = nonlinear_info.get("result_case")
    if result_case is not None:
        provenance["result_case"] = _json_safe(result_case)
    metadata = getattr(raw, "quantity_metadata", None)
    if metadata:
        provenance["solver_quantities"] = _json_safe(
            [value.to_dict() if hasattr(value, "to_dict") else value for value in metadata]
        )
    imported = getattr(solution, "results", None)
    if imported is not None:
        provenance["imported_source"] = _import_source(imported)
    return provenance


def _solution_submission_identity(solution: Any) -> dict[str, Any]:
    """Best-effort immutable document identity from the solved working copy."""

    built = getattr(solution, "built", None)
    project = getattr(built, "project", None)
    if project is None:
        return {}
    identity: dict[str, Any] = {
        "document_id": str(getattr(project, "document_id", "")),
        "project_name": str(getattr(project, "name", "")),
    }
    try:
        # ``DocumentSession`` computes the same canonical v4 semantic hash as
        # job submission.  It is non-serialized and does not mutate Project.
        from ..document import DocumentSession

        identity["document_hash"] = DocumentSession(project).revision.document_hash
    except Exception as error:
        # Hash absence is explicit in generated reports; never substitute a
        # hash of a different representation or the current edited document.
        identity["document_hash_error"] = str(error)
    return identity


def _producer_versions() -> dict[str, str]:
    """Versions captured at artifact creation, not report-viewing time."""

    versions = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "numpy": str(np.__version__),
    }
    for label, distribution in (
        ("ANYfem", "ANYfem"),
        ("ANYgeometry", "ANYgeometry"),
        ("ANYmaterial", "ANYmaterial"),
        ("ANYmesher", "ANYmesher"),
        ("ANYsolver", "ANYsolver"),
    ):
        try:
            versions[label] = importlib_metadata.version(distribution)
        except importlib_metadata.PackageNotFoundError:
            continue
    return {key: versions[key] for key in sorted(versions)}


def _base_summary(solution: Any, builder: _Builder) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "solution_type": type(solution).__name__,
        "status": str(getattr(solution, "status", "available")),
        "frame_count": len(builder.frames),
        "frame_kind": builder.frame_kind,
        "field_keys": sorted(builder.fields),
    }
    method = getattr(solution, "summary", None)
    if callable(method):
        try:
            summary["text"] = str(method())
        except Exception as error:
            summary["summary_error"] = str(error)
    for name in (
        "peak_displacement",
        "peak_contact_force",
        "contact_duration",
        "max_penetration",
        "max_penetration_ratio",
        "momentum_balance_error",
        "peak_load_factor",
        "critical_factor",
    ):
        value = getattr(solution, name, None)
        if value is not None and isinstance(value, (int, float, np.number)):
            summary[name] = _json_safe(value)
    deleted = getattr(solution, "deleted_elements", ()) or ()
    if deleted:
        summary["deleted_element_ids"] = [int(value) for value in deleted]
    return summary


def _collect_diagnostics(
    solution: Any,
    raw: Any,
    supplied: Sequence[Any],
) -> tuple[Any, ...]:
    collected = list(supplied)
    info = getattr(solution, "info", None)
    if isinstance(info, Mapping) and info.get("diagnostics") is not None:
        collected.append(info["diagnostics"])
    raw_diagnostics = getattr(raw, "diagnostics", None)
    if raw_diagnostics is not None and not any(
        value is raw_diagnostics for value in collected
    ):
        collected.append(raw_diagnostics)
    raw_info = getattr(raw, "info", None)
    if isinstance(raw_info, Mapping) and raw_info.get("diagnostics") is not None:
        collected.append(raw_info["diagnostics"])
    imported = getattr(solution, "results", None)
    warnings = tuple(getattr(imported, "warnings", ()) or ())
    if warnings:
        collected.append({"import_warnings": warnings})
    return tuple(_json_safe(value) for value in collected)


def _stress_provenance(result: Any) -> Mapping[str, Any]:
    if hasattr(result, "provenance_dict"):
        try:
            return _json_safe(result.provenance_dict())
        except Exception:
            pass
    value = getattr(result, "provenance", None)
    return _json_safe(value) if isinstance(value, Mapping) else {}


def _import_source(imported: Any) -> dict[str, Any]:
    source = getattr(imported, "source", None)
    return {
        "format": str(getattr(imported, "format", "unknown")),
        "name": "" if source is None else Path(source).name,
    }


def _dof_unit(kind: str, components: Sequence[str]) -> str:
    rotational = any(
        str(value).startswith("r") or str(value).startswith("w")
        for value in components
    )
    if kind in ("displacement", "mode_shape"):
        return "mixed:m,rad" if rotational else "m"
    if kind == "velocity":
        return "mixed:m/s,rad/s" if rotational else "m/s"
    if kind == "acceleration":
        return "mixed:m/s^2,rad/s^2" if rotational else "m/s^2"
    if kind == "impulse":
        return "mixed:N*s,N*m*s" if rotational else "N*s"
    return ""


def _stack_present(values: Any) -> Optional[np.ndarray]:
    arrays = []
    for value in values:
        array = _numeric_array(value)
        if array is None or array.size == 0:
            return None
        arrays.append(array.reshape(-1))
    if not arrays or len({array.shape for array in arrays}) != 1:
        return None
    return np.stack(arrays)


def _has_values(value: Any) -> bool:
    array = _numeric_array(value)
    return array is not None and array.size > 0


def _numeric_array(value: Any) -> Optional[np.ndarray]:
    if value is None:
        return None
    try:
        array = np.asarray(value)
    except Exception:
        return None
    if array.dtype.kind not in "biufc":
        return None
    return array


def _safe_key(value: object) -> str:
    key = _SAFE_KEY.sub("_", str(value).strip()).strip("._-")
    return key or "quantity"


def _json_safe(value: Any) -> Any:
    """Convert solver metadata to strict, deterministic JSON-compatible data."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if math.isfinite(number):
            return number
        return "NaN" if math.isnan(number) else ("Infinity" if number > 0 else "-Infinity")
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if is_dataclass(value):
        return _json_safe(asdict(value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _json_safe(to_dict())
        except Exception:
            pass
    return str(value)
