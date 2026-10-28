"""Dedicated floating-body residual inertia-relief batch (stationary model, no motion simulation).

Each frame carries the FULL assembled nodal load ``Fp + Fg - M a_primary`` (global-axes (n,6) forces/moments).  It is
passed to ``anysolver.static_batch.iter_linear_batches(inertia_relief=True)`` as an explicit nodal ``LoadCase`` with
``gravity=None`` so no second gravity can enter, and the solver adds only ``-M delta_a``.  Primary, correction and
total accelerations stay separate; the correction never feeds back into q or the pressure.  This is algebraically
ordinary inertia relief of the same load and is reported as such.  The existing 1e-10 solver gates apply untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Optional

import numpy as np


class FloatingBatchError(ValueError):
    """A frame cannot be submitted or its residual summary cannot be read."""


@dataclass(frozen=True)
class FloatingFrameInput:
    case_id: str
    time: Optional[float]
    nodal_load: np.ndarray                 # (n_nodes, 6) real total Fp+Fg+Fi_primary
    primary_acceleration: Optional[np.ndarray] = None   # (n_nodes, 6) real, optional diagnostics only


@dataclass(frozen=True)
class FloatingFrameResult:
    case_id: str
    time: Optional[float]
    input_index: int
    displacements: np.ndarray              # selected output DOFs, elastic gauge-fixed
    correction_acceleration_cm: np.ndarray  # delta_a at the centre of mass (global axes)
    correction_angular_acceleration: np.ndarray
    center_of_mass: np.ndarray
    correction_acceleration_nodal: np.ndarray   # (n,6) delta_a field at nodes (translation, angular)
    total_acceleration_nodal: Optional[np.ndarray]   # primary + correction (None when primary not supplied)
    relief: dict                            # compact JSON summary from the solver (balance diagnostics)
    # effective-load (quasistatic) element stresses; only when a recovery_config requesting stresses is supplied
    element_stresses: Optional[dict] = None


def build_residual_load_case(model: Any, node_ids: Iterable[int], nodal_load: Any, name: str) -> Any:
    """Explicit nodal LoadCase with gravity=None (no duplicate gravity)."""
    from anysolver import LoadCase
    ids = tuple(int(i) for i in node_ids)
    L = np.asarray(nodal_load, dtype=np.float64)
    if L.shape != (len(ids), 6) or not np.all(np.isfinite(L)):
        raise FloatingBatchError('nodal load must be finite (n_nodes, 6)')
    case = LoadCase(name)
    case.gravity = None
    for i, node_id in enumerate(ids):
        if node_id not in model.mesh.nodes:
            raise FloatingBatchError(f'unknown node {node_id}')
        if np.any(L[i]):
            case.add_nodal_load(node_id, load_vector=L[i])
    return case


def rigid_field(points: Any, center: Any, a_cm: Any, alpha: Any) -> np.ndarray:
    """(n,6) rigid acceleration field a(x) = a_cm + alpha x (x - cm); angular DOFs carry alpha."""
    P = np.asarray(points, dtype=np.float64)
    r = P - np.asarray(center, dtype=np.float64).reshape(3)
    a = np.asarray(a_cm, dtype=np.float64).reshape(3)[None, :] + np.cross(np.asarray(alpha, dtype=np.float64), r)
    return np.concatenate([a, np.broadcast_to(np.asarray(alpha, dtype=np.float64), P.shape)], axis=1)


def iter_residual_relief(model: Any, node_ids: Iterable[int], frames: Iterable[FloatingFrameInput], *,
                         batch_size: int = 8, output_dofs: Any = None, session: Any = None,
                         reference_point: Any = None, cancellation_token: Any = None,
                         progress_callback: Any = None, recovery_config: Any = None,
                         resource_config: Any = None) -> Iterator[FloatingFrameResult]:
    """Stream residual-IR results per frame; shares one session/factorization across all frames.

    Admission (connected body, element route, MPC, supports) is the solver's: nothing is rerouted here.  A rejected
    block raises the solver's StaticBatchError with its diagnostics; already yielded frames remain valid.
    ``recovery_config`` / ``resource_config`` (optional, default None = previous behaviour) are passed unchanged to
    ``iter_linear_batches``; when stresses are recovered they are the EFFECTIVE-LOAD (quasistatic) element stresses
    of the relieved load and are returned in ``element_stresses``.  Inertial / gauge multipliers are never reactions.
    """
    from anysolver.static_batch import StaticCase, iter_linear_batches
    ids = tuple(int(i) for i in node_ids)
    points = np.array([model.mesh.nodes[i].coords() for i in ids], dtype=np.float64)
    meta = {}

    def cases():
        for index, frame in enumerate(frames):
            meta[frame.case_id] = frame
            yield StaticCase(frame.case_id, build_residual_load_case(model, ids, frame.nodal_load, frame.case_id),
                             frame.time)

    extra = {}
    if recovery_config is not None:
        extra["recovery_config"] = recovery_config
    if resource_config is not None:
        extra["resource_config"] = resource_config
    for block in iter_linear_batches(model, cases(), batch_size=batch_size, output_dofs=output_dofs,
                                     session=session, inertia_relief=True, reference_point=reference_point,
                                     cancellation_token=cancellation_token, progress_callback=progress_callback,
                                     **extra):
        for j, case_id in enumerate(block.case_ids):
            frame = meta.pop(case_id)
            summary = block.case_results[j].get('inertia_relief')
            if summary is None:
                raise FloatingBatchError('solver result lacks inertia-relief summary')
            a_cm = np.asarray(summary['acceleration_at_center_of_mass'], dtype=np.float64)
            alpha = np.asarray(summary['angular_acceleration'], dtype=np.float64)
            com = np.asarray(summary['center_of_mass'], dtype=np.float64)
            delta = rigid_field(points, com, a_cm, alpha)
            total = None if frame.primary_acceleration is None else np.asarray(frame.primary_acceleration) + delta
            stresses = None
            if recovery_config is not None:
                stresses = block.case_results[j].get("element_stresses") or None
            yield FloatingFrameResult(case_id, frame.time, int(block.input_indices[j]),
                                      np.asarray(block.displacements[:, j]), a_cm, alpha, com, delta, total, summary,
                                      stresses)
