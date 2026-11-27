"""Shell result-axes station records (direction programme D2-1, data slice).

On an ``axes_*`` output request the worker calls the solver's opt-in module
``anyfesolver.shell_axes_outputs.recover_shell_axes`` once per shell element
and keeps what it returns as one columnar, plain-data record per recovered
case.  Nothing here rotates, groups or labels: the transform, definition and
display are later slices (D2-2..D2-4).  The record keeps, per shell and
station, exactly the arrays the solver module certified, and per family the
solver's certification or typed refusal, so a later evaluation never has to
guess:

* an element the module refuses as a whole (``ShellAxesRefusal``: formulation,
  admission, geometry conditioning, ...) keeps its code and reason and no
  stations;
* a family the cross-check refuses (``AXES_FRAME_RULE_MISMATCH``) keeps its
  reason and detail; its tensor and basis-normal rows stay NaN and its
  ``*_certified`` flag is False, so the NaN is never a value;
* certified arrays are copied bit for bit (float64).

Non-shell elements (beams, springs, solids, couplings) are not shells and are
not recorded.  Solver errors of the recovery itself propagate unchanged (the
analysis job fails, as an ordinary recovery error does).

No Qt; runs in the analysis worker after the solve, only on the axes-request
path.  The ordinary path (no ``axes_*`` request) never imports this module.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

import numpy as np

__all__ = [
    "FAMILIES",
    "RECORD_SCHEMA",
    "STATIC_KEYS",
    "POOL_MIN_SHELLS",
    "ShellAxesPool",
    "default_pool_workers",
    "read_shell_axes_table",
    "recover_shell_axes_record",
    "shell_element_ids",
]

RECORD_SCHEMA = "anyfem.shell_axes_record/1"
FAMILIES = ("membrane", "bending", "shear")
_TENSOR_KEYS = {
    "membrane": ("global_membrane_resultant_tensors", (3, 3)),
    "bending": ("global_bending_resultant_tensors", (3, 3)),
    "shear": ("global_transverse_shear_resultants", (3,)),
}
_NORMAL_KEYS = {
    "membrane": "membrane_basis_normals",
    "bending": "bending_basis_normals",
    "shear": "shear_basis_normals",
}
_OFFSET_KEYS = (
    "reference_surface_offset",
    "section_origin_offset_from_reference",
    "physical_bottom_offset_from_reference",
    "physical_top_offset_from_reference",
)
#: Record entries that depend on the geometry and the element only, never on
#: the load; equal in every case of one analysis (checked when cases are
#: combined).  Everything else is per case.
STATIC_KEYS = (
    "element_ids", "element_refusal", "element_refusal_reason", "branch",
    "station_offsets", "station_positions", "director_polarity",
    "physical_reference_director", "thickness",
    *(f"{fam}_convention" for fam in FAMILIES), *_OFFSET_KEYS,
)


def _shell_class():
    from anyfesolver.elements import ShellElement

    return ShellElement


def shell_element_ids(fe_model: Any) -> tuple[int, ...]:
    """Ids of the model's shell elements, in mesh order (class MRO, not isinstance)."""

    shell = _shell_class()
    return tuple(int(eid) for eid, element in fe_model.mesh.elements.items()
                 if shell in type(element).__mro__)


def _rows(n: int, shape: Sequence[int]) -> np.ndarray:
    return np.full((n, *shape), np.nan, dtype=float)


def _exact(array: Any, shape: Sequence[int], what: str) -> np.ndarray:
    value = np.asarray(array)
    if value.dtype != np.float64 or value.shape != tuple(shape):
        raise ValueError(f"shell axes record: {what} has dtype {value.dtype} and shape {value.shape}, "
                         f"expected float64 {tuple(shape)}")
    return value


def _thickness(element: Any) -> float:
    value = getattr(element, "thickness", None)
    if type(value) in (float, int, np.float64):
        return float(value)
    return float("nan")


#: Below this many shells the serial loop is faster than starting workers.
POOL_MIN_SHELLS = 400
#: Shells per pool task (bounded result messages, cancellation granularity).
POOL_CHUNK = 256


def _element_entry(fe_model: Any, u: np.ndarray, eid: int) -> tuple:
    """Recover one shell; a plain, picklable entry (identical in any process)."""

    from anyfesolver.shell_axes_outputs import ShellAxesRefusal, recover_shell_axes

    mesh = fe_model.mesh
    element = mesh.elements[eid]
    thickness = _thickness(element)
    material = fe_model.get_material(element.material_name)
    mapping = np.asarray(element.get_dof_mapping(mesh), dtype=np.intp)
    if mapping.size == 0 or int(mapping.min()) < 0 or int(mapping.max()) >= u.size:
        return ("refused", thickness, "AXES_MISSING_GLOBAL_TENSORS",
                "element has no complete in-range DOF mapping")
    try:
        result = recover_shell_axes(element, mesh, u[mapping], material)
    except ShellAxesRefusal as refusal:
        return ("refused", thickness, refusal.code, refusal.reason)
    axes = result.axes
    arrays = axes.arrays
    positions = np.asarray(arrays["recovery_station_positions"])
    if positions.dtype != np.float64 or positions.ndim != 2 or positions.shape[1] != 3 or positions.shape[0] < 1:
        raise ValueError(f"shell axes record: element {eid} station positions {positions.dtype} {positions.shape}")
    n = positions.shape[0]
    certified = {fam: bool(axes.certified.get(fam, False)) and fam not in axes.refusals for fam in FAMILIES}
    values: Dict[str, np.ndarray] = {"positions": np.array(positions)}
    for fam in FAMILIES:
        key, shape = _TENSOR_KEYS[fam]
        if certified[fam]:
            if key not in arrays:
                raise ValueError(f"shell axes record: element {eid} certified {fam} has no {key}")
            values[key] = np.array(_exact(arrays[key], (n, *shape), key))
        normal_key = _NORMAL_KEYS[fam]
        if normal_key in arrays:
            values[normal_key] = np.array(_exact(arrays[normal_key], (n, 3), normal_key))
    if "shear_normal_components" in arrays:
        values["shear_normal_components"] = np.array(
            _exact(arrays["shear_normal_components"], (n,), "shear_normal_components"))
    polarity = 0
    if "director_polarity" in arrays:
        polarity = int(np.asarray(arrays["director_polarity"]))
        if polarity not in (-1, 1):
            raise ValueError(f"shell axes record: element {eid} director polarity {polarity}")
    if "physical_reference_director" in arrays:
        values["director"] = np.array(
            _exact(arrays["physical_reference_director"], (3,), "physical reference director"))
    offsets = {key: float(_exact(arrays[key], (), key)) for key in _OFFSET_KEYS if key in arrays}
    return (
        "ok", thickness, str(axes.branch),
        {fam: str(axes.conventions.get(fam, "")) for fam in FAMILIES},
        certified,
        {fam: str(axes.refusals[fam].reason) for fam in FAMILIES if fam in axes.refusals},
        {fam: str(axes.refusals[fam].detail) for fam in FAMILIES if fam in axes.refusals},
        polarity, offsets, values,
    )


# -------------------------------------------------------------- worker pool
_POOL_STATE: Dict[str, Any] = {}


def _pool_warm() -> int:
    """Import the recovery stack once per worker (overlaps the solve)."""

    import anyfesolver.shell_axes_outputs  # noqa: F401

    from .. import result_transport  # noqa: F401

    return os.getpid()


def _pool_chunk(payload_path: str, eids: Sequence[int]) -> list:
    # The model travels through ANYfem's verified result transport (elements
    # that refuse pickle, e.g. S3 V2D, use their own to_dict/from_dict route),
    # through a file: tens of MB as task arguments would block the parent.
    # Decoded once per worker and case.
    if _POOL_STATE.get("path") != payload_path:
        from pathlib import Path

        from ..job_process import loads
        from ..result_transport import decode_result
        from .solved_geometry import solved_model_of

        carrier = decode_result(loads(Path(payload_path).read_bytes()))
        _POOL_STATE.clear()
        _POOL_STATE.update(path=payload_path, fe_model=solved_model_of(carrier),
                           u=np.asarray(carrier.displacements, dtype=float))
    fe_model, u = _POOL_STATE["fe_model"], _POOL_STATE["u"]
    return [_element_entry(fe_model, u, int(eid)) for eid in eids]


def default_pool_workers() -> int:
    """Pool size for this process: 0 (serial) where children are not allowed.

    A daemonic process (an ordinary, uncontained solve worker) cannot start
    children, so it stays serial.  Otherwise half the logical CPUs, 2..16;
    ``ANYFEM_SHELL_AXES_WORKERS`` overrides (0 = serial).
    """

    import multiprocessing

    if multiprocessing.current_process().daemon:
        return 0
    override = os.environ.get("ANYFEM_SHELL_AXES_WORKERS", "").strip()
    if override:
        return max(0, int(override))
    return int(min(16, max(2, (os.cpu_count() or 2) // 2)))


class ShellAxesPool:
    """A spawn process pool for the per-shell loop, started ahead of use.

    Start it before the solve so the worker imports overlap the solve; use it
    for every case; close it (or leave the ``with`` block) afterwards.  On
    cancellation or failure every worker is terminated.
    """

    def __init__(self, workers: int) -> None:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor

        if workers < 2:
            raise ValueError("a shell axes pool needs at least two workers")
        self.workers = int(workers)
        self._executor = ProcessPoolExecutor(max_workers=self.workers,
                                             mp_context=multiprocessing.get_context("spawn"))
        try:
            # One task per worker starts every process now.
            self._warm = [self._executor.submit(_pool_warm) for _ in range(self.workers)]
        except BaseException:
            self.close(terminate=True)
            raise

    def __enter__(self) -> "ShellAxesPool":
        return self

    def __exit__(self, kind, value, traceback) -> None:
        self.close(terminate=kind is not None)

    def close(self, terminate: bool = False) -> None:
        executor, self._executor = getattr(self, "_executor", None), None
        if executor is None:
            return
        if terminate:
            for process in tuple((getattr(executor, "_processes", None) or {}).values()):
                if process.is_alive():
                    process.terminate()
        executor.shutdown(wait=True, cancel_futures=True)

    def entries(self, carrier: Any, ids: Sequence[int], cancelled: Optional[Callable[[], None]]) -> list:
        import tempfile
        from concurrent.futures import FIRST_COMPLETED, wait

        from ..job_process import dumps
        from ..result_transport import encode_result

        if self._executor is None:
            raise RuntimeError("the shell axes pool is closed")
        # Small tasks balance workers that become ready at different times.
        size = max(16, min(POOL_CHUNK, -(-len(ids) // (8 * self.workers))))
        chunks = [tuple(int(eid) for eid in ids[i:i + size]) for i in range(0, len(ids), size)]
        results: list = [None] * len(chunks)
        handle, payload_path = tempfile.mkstemp(prefix="anyfem-axes-", suffix=".bin")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(dumps(encode_result(carrier, verify=False)))
            pending = {self._executor.submit(_pool_chunk, payload_path, chunk): index
                       for index, chunk in enumerate(chunks)}
            while pending:
                done, _ = wait(tuple(pending), timeout=0.2, return_when=FIRST_COMPLETED)
                if cancelled is not None:
                    cancelled()
                for future in done:
                    results[pending.pop(future)] = future.result()
        except BaseException:
            # Cancellation or a failure: stop at once, never leave workers behind.
            self.close(terminate=True)
            raise
        finally:
            try:
                os.unlink(payload_path)
            except OSError:
                pass
        return [entry for chunk in results for entry in chunk]


def _entries(fe_model: Any, u: np.ndarray, ids: Sequence[int], pool: Optional[ShellAxesPool],
             cancelled: Optional[Callable[[], None]], carrier: Any = None) -> list:
    if pool is not None and carrier is not None and len(ids) >= POOL_MIN_SHELLS:
        from .solved_geometry import solved_model_of

        if solved_model_of(carrier) is not fe_model or not np.array_equal(
                np.asarray(carrier.displacements, dtype=float), u):
            raise ValueError("shell axes record: the pool carrier is not this model and displacement field")
        return pool.entries(carrier, ids, cancelled)
    entries = []
    for eid in ids:
        if cancelled is not None:
            cancelled()
        entries.append(_element_entry(fe_model, u, eid))
    return entries


def recover_shell_axes_record(
    fe_model: Any,
    displacements: Any,
    *,
    provenance: Optional[Mapping[str, Any]] = None,
    cancelled: Optional[Callable[[], None]] = None,
    pool: Optional[ShellAxesPool] = None,
    carrier: Any = None,
) -> Dict[str, Any]:
    """Recover every shell of ``fe_model`` through ``recover_shell_axes``.

    ``displacements`` is the solved global vector of one case.  ``cancelled``
    (optional) is called between elements (serial) or while pool tasks run and
    may raise to stop the loop.  With a ``pool`` and a ``carrier`` (the exact
    ``LinearSolution`` whose solved model and displacements these are) and at
    least ``POOL_MIN_SHELLS`` shells the loop runs in the pool; the model
    reaches it through the verified result transport.  The record is the same
    bit for bit either way: every element is recovered by the same function on
    a transported copy of the model (checked by the tests and on the barge).
    Returns the plain-data record (numpy arrays, lists of str, a provenance
    dict); see the module docstring.
    """

    u = np.asarray(displacements, dtype=float)
    ids = shell_element_ids(fe_model)
    n_e = len(ids)
    entries = _entries(fe_model, u, ids, pool, cancelled, carrier)
    if len(entries) != n_e:
        raise ValueError(f"shell axes record: {len(entries)} entries for {n_e} shells")
    element_refusal = [""] * n_e
    element_reason = [""] * n_e
    branch = [""] * n_e
    conventions = {fam: [""] * n_e for fam in FAMILIES}
    certified = {fam: np.zeros(n_e, dtype=bool) for fam in FAMILIES}
    fam_reason = {fam: [""] * n_e for fam in FAMILIES}
    fam_detail = {fam: [""] * n_e for fam in FAMILIES}
    polarity = np.zeros(n_e, dtype=np.int8)
    director = _rows(n_e, (3,))
    thickness = np.full(n_e, np.nan)
    offsets = {key: np.full(n_e, np.nan) for key in _OFFSET_KEYS}
    counts = np.zeros(n_e, dtype=np.int64)
    for index, entry in enumerate(entries):
        thickness[index] = entry[1]
        if entry[0] == "refused":
            element_refusal[index], element_reason[index] = entry[2], entry[3]
            continue
        _, _, branch[index], conv, cert, reasons, details, pol, offs, values = entry
        counts[index] = values["positions"].shape[0]
        for fam in FAMILIES:
            conventions[fam][index] = conv[fam]
            certified[fam][index] = cert[fam]
            fam_reason[fam][index] = reasons.get(fam, "")
            fam_detail[fam][index] = details.get(fam, "")
        polarity[index] = pol
        if "director" in values:
            director[index] = values["director"]
        for key, value in offs.items():
            offsets[key][index] = value

    station_offsets = np.zeros(n_e + 1, dtype=np.int64)
    np.cumsum(counts, out=station_offsets[1:])
    n_s = int(station_offsets[-1])
    positions = _rows(n_s, (3,))
    tensors = {fam: _rows(n_s, _TENSOR_KEYS[fam][1]) for fam in FAMILIES}
    normals = {fam: _rows(n_s, (3,)) for fam in FAMILIES}
    shear_normal = np.full(n_s, np.nan)
    for index, entry in enumerate(entries):
        if entry[0] != "ok":
            continue
        values = entry[-1]
        lo, hi = int(station_offsets[index]), int(station_offsets[index + 1])
        positions[lo:hi] = values["positions"]
        for fam in FAMILIES:
            key = _TENSOR_KEYS[fam][0]
            if key in values:
                tensors[fam][lo:hi] = values[key]
            if _NORMAL_KEYS[fam] in values:
                normals[fam][lo:hi] = values[_NORMAL_KEYS[fam]]
        if "shear_normal_components" in values:
            shear_normal[lo:hi] = values["shear_normal_components"]

    record: Dict[str, Any] = {
        "schema": RECORD_SCHEMA,
        "provenance": dict(provenance or {}),
        "element_ids": np.asarray(ids, dtype=np.int64),
        "element_refusal": element_refusal,
        "element_refusal_reason": element_reason,
        "branch": branch,
        "station_offsets": station_offsets,
        "station_positions": positions,
        "director_polarity": polarity,
        "physical_reference_director": director,
        "thickness": thickness,
        "shear_normal_components": shear_normal,
        **offsets,
    }
    for fam in FAMILIES:
        record[f"{fam}_certified"] = certified[fam]
        record[f"{fam}_refusal_reason"] = fam_reason[fam]
        record[f"{fam}_refusal_detail"] = fam_detail[fam]
        record[f"{fam}_convention"] = conventions[fam]
        record[f"{fam}_basis_normals"] = normals[fam]
    record["membrane_tensors"] = tensors["membrane"]
    record["bending_tensors"] = tensors["bending"]
    record["shear_vectors"] = tensors["shear"]
    return record


def read_shell_axes_table(dataset: Any, key: str) -> Any:
    """Read one persisted ``shell_axes_<key>`` entry back to its record form.

    Float entries are restored from their presence mask (absent -> NaN);
    string columns come back as lists.  ``dataset`` is an opened result
    (anything with ``table(name)`` and ``table_keys``).
    """

    stored = dataset.table("shell_axes_" + key)
    if isinstance(stored, Mapping) and set(stored) == {"values"}:
        return stored["values"]
    shape = tuple(int(v) for v in np.asarray(dataset.table("shell_axes_" + key + "_shape"), dtype=np.int64).reshape(-1))
    value = np.asarray(stored).reshape(shape)
    mask_key = "shell_axes_" + key + "_present"
    if value.dtype.kind == "f" and mask_key in tuple(dataset.table_keys):
        present = np.asarray(dataset.table(mask_key), dtype=bool).reshape(-1)
        if present.size != value.size:
            raise ValueError(f"shell axes table {key!r}: presence mask size {present.size} != {value.size}")
        value = np.where(present.reshape(shape), value, np.nan)
    return value
