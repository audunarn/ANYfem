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
  stations; so does an element whose station positions the module refused;
* a family the cross-check refuses (``AXES_FRAME_RULE_MISMATCH``) keeps its
  reason and detail; its tensor AND basis-normal rows stay NaN (also where the
  module still emits a normal because another family certified the same frame)
  and its ``*_certified`` flag is False, so a NaN is never a value;
* a refused metadata entry (director, offsets) keeps its reason, value NaN;
* certified arrays are copied bit for bit (float64).

Non-shell elements (beams, springs, solids, couplings) are not shells and are
not recorded.  Solver errors of the recovery itself propagate unchanged (the
analysis job fails, as an ordinary recovery error does).

The loop may run in a process pool (:class:`ShellAxesPool`), only inside a
confirmed contained job worker.  The model reaches the pool through ANYfem's
verified result transport; because a transported copy can lack dynamic state
of the original (a solver cache on the mesh or an element, which the module's
admission would refuse), every pooled element is checked against a fingerprint
of the admission-relevant state of the original objects, and any element whose
copy differs (or all of them, if the mesh or its nodes differ) is recovered
again serially on the original.  The record is therefore the serial record.

No Qt; runs in the analysis worker after the solve, only on the axes-request
path.  The ordinary path (no ``axes_*`` request) never imports this module.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

import numpy as np

__all__ = [
    "FAMILIES",
    "RECORD_SCHEMA",
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
#: Below this many shells the serial loop is faster than using workers.
POOL_MIN_SHELLS = 400
#: Upper bound of shells per pool task (bounded messages, cancellation granularity).
POOL_CHUNK = 256
_PAYLOAD_PREFIX = "anyfem-axes-"


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


def _plain_float(value: Any) -> float:
    return float(value) if type(value) in (float, int, np.float64) else float("nan")


# ------------------------------------------------------------ one element
def _element_entry(fe_model: Any, u: np.ndarray, eid: int) -> tuple:
    """Recover one shell; a plain, picklable entry (identical in any process).

    Element methods are dispatched through the class (an instance attribute
    cannot shadow them) and element data are read from the instance dict only;
    the solver module admits the objects before anything else is used.
    """

    from anyfesolver.shell_axes_outputs import ShellAxesRefusal, recover_shell_axes

    mesh = fe_model.mesh
    element = mesh.elements[eid]
    state = vars(element)
    material = fe_model.get_material(state.get("material_name"))
    mapping = np.asarray(type(element).get_dof_mapping(element, mesh), dtype=np.intp)
    if mapping.ndim != 1 or mapping.size == 0 or int(mapping.min()) < 0 or int(mapping.max()) >= u.size:
        return ("refused", "AXES_MISSING_GLOBAL_TENSORS", "element has no complete in-range DOF mapping")
    try:
        result = recover_shell_axes(element, mesh, u[mapping], material)
    except ShellAxesRefusal as refusal:
        return ("refused", refusal.code, refusal.reason)
    axes = result.axes
    arrays = axes.arrays
    refusals = dict(axes.refusals)
    if "positions" in refusals or "recovery_station_positions" not in arrays:
        refusal = refusals.get("positions")
        reason = "station positions refused" if refusal is None else f"{refusal.reason}: {refusal.detail}"
        return ("refused", "AXES_FRAME_RULE_MISMATCH", f"positions: {reason}")
    positions = np.asarray(arrays["recovery_station_positions"])
    if positions.dtype != np.float64 or positions.ndim != 2 or positions.shape[1] != 3 or positions.shape[0] < 1:
        raise ValueError(f"shell axes record: element {eid} station positions {positions.dtype} {positions.shape}")
    n = positions.shape[0]
    certified = {fam: bool(axes.certified.get(fam, False)) and fam not in refusals for fam in FAMILIES}
    values: Dict[str, np.ndarray] = {"positions": np.array(positions)}
    for fam in FAMILIES:
        if not certified[fam]:
            continue  # neither tensor nor normal of a refused family is kept
        key, shape = _TENSOR_KEYS[fam]
        if key not in arrays:
            raise ValueError(f"shell axes record: element {eid} certified {fam} has no {key}")
        values[key] = np.array(_exact(arrays[key], (n, *shape), key))
        normal_key = _NORMAL_KEYS[fam]
        if normal_key in arrays:
            values[normal_key] = np.array(_exact(arrays[normal_key], (n, 3), normal_key))
    if certified["shear"] and "shear_normal_components" in arrays:
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
    metadata = refusals.get("metadata")
    return (
        "ok", _plain_float(state.get("thickness")), str(axes.branch),
        {fam: str(axes.conventions.get(fam, "")) for fam in FAMILIES},
        certified,
        {fam: str(refusals[fam].reason) for fam in FAMILIES if fam in refusals},
        {fam: str(refusals[fam].detail) for fam in FAMILIES if fam in refusals},
        polarity, offsets, values,
        "" if metadata is None else f"{metadata.reason}: {metadata.detail}",
    )


# ------------------------------------------------- admission-state profiles
def _admitted_value_check() -> Optional[Callable[[Any], bool]]:
    """The module's own per-value admission rule, or None (then no pool).

    ``anyfesolver.shell_axes_outputs._plain_value`` is a private name of the
    opt-in module (a public equivalent is requested from the solver side);
    if it is missing the record is recovered serially, never approximated.
    """

    try:
        from anyfesolver.shell_axes_outputs import _plain_value
    except ImportError:
        return None
    return _plain_value if callable(_plain_value) else None


def _profile(obj: Any, check: Callable[[Any], bool]) -> tuple:
    """Admission-relevant profile of one object: its exact class, its
    instance-dict class, and per attribute (value class, admitted by the
    module's value rule).  The values themselves are not compared."""

    state = getattr(obj, "__dict__", None)
    head = (f"{type(obj).__module__}.{type(obj).__qualname__}", type(state).__qualname__)
    if type(state) is not dict:
        return head + ((),)
    rows = []
    for key in sorted(state, key=repr):
        value = state[key]
        rows.append((f"{type(key).__qualname__}:{key!r}", f"{type(value).__module__}.{type(value).__qualname__}",
                     bool(check(value))))
    return head + (tuple(rows),)


def _class_names(kind: type) -> frozenset:
    names = set()
    for cls in kind.__mro__:
        names.update(vars(cls))
    return frozenset(repr(name) for name in names)


def _equivalent(original: tuple, copied: tuple, class_names: frozenset) -> bool:
    """Whether the copy is admitted by ``recover_shell_axes`` exactly when the
    original is, and both are: same object class, an exact instance dict on
    both, every attribute present on both sides admitted on both sides (its
    class may differ: the transport resets element caches to None and rebuilds
    the mesh without its dynamic caches, measured), and an attribute on one
    side only admitted and naming no class attribute (the module's shadow
    rule; dataclass fields are counted as class attributes here, which is
    stricter).  Anything else is recovered serially on the original."""

    if original[:2] != copied[:2] or original[1] != "dict":
        return False
    left = {row[0]: row[1:] for row in original[2]}
    right = {row[0]: row[1:] for row in copied[2]}
    for key in set(left) | set(right):
        a, b = left.get(key), right.get(key)
        if (a is not None and not a[1]) or (b is not None and not b[1]):
            return False
        if (a is None or b is None) and key.split(":", 1)[1] in class_names:
            return False
    return True


def _nodes_profile(fe_model: Any, check: Callable[[Any], bool]) -> str:
    """Digest of every node's profile (each element's admission also looks at
    its own nodes); the transport carries nodes exactly (measured), so the
    digests must match exactly."""

    digest = hashlib.sha256()
    for nid in sorted(fe_model.mesh.nodes, key=repr):
        digest.update(repr((repr(nid), _profile(fe_model.mesh.nodes[nid], check))).encode("utf-8", "backslashreplace"))
    return digest.hexdigest()


# -------------------------------------------------------------- worker pool
_POOL_STATE: Dict[str, Any] = {}


def _pool_warm() -> int:
    """Import the recovery stack once per worker (overlaps the solve)."""

    import anyfesolver.shell_axes_outputs  # noqa: F401

    from .. import result_transport  # noqa: F401

    return os.getpid()


def _pool_chunk(payload_path: str, eids: Sequence[int]) -> tuple:
    # The model travels through ANYfem's verified result transport (elements
    # that refuse pickle, e.g. S3 V2D, use their own to_dict/from_dict route),
    # through a file: tens of MB as task arguments would block the parent.
    # Decoded once per worker and case.
    if _POOL_STATE.get("path") != payload_path:
        from pathlib import Path

        from ..job_process import loads
        from ..result_transport import decode_result
        from .solved_geometry import solved_model_of

        check = _admitted_value_check()
        if check is None:
            raise RuntimeError("shell axes pool: the module's value admission rule is unavailable")
        carrier = decode_result(loads(Path(payload_path).read_bytes()))
        fe_model = solved_model_of(carrier)
        _POOL_STATE.clear()
        _POOL_STATE.update(path=payload_path, fe_model=fe_model, check=check,
                           model=(_nodes_profile(fe_model, check), _profile(fe_model.mesh, check)),
                           u=np.asarray(carrier.displacements, dtype=float))
    fe_model, u, check = _POOL_STATE["fe_model"], _POOL_STATE["u"], _POOL_STATE["check"]
    # Profiles are taken before each element's recovery, like the original's.
    profiles = [_profile(fe_model.mesh.elements[int(eid)], check) for eid in eids]
    return (os.getpid(), _POOL_STATE["model"], profiles, [_element_entry(fe_model, u, int(eid)) for eid in eids])


def default_pool_workers() -> int:
    """Pool size for this process: 0 (serial) unless it is a confirmed contained
    job worker, whose owned process tree also retires any pool children.

    Thread jobs, uncontained (daemonic) workers and library calls stay serial.
    In a contained worker: half the logical CPUs, 2..16;
    ``ANYFEM_SHELL_AXES_WORKERS`` overrides the size (0 = serial).
    """

    from ..job_process import contained_worker

    if not contained_worker():
        return 0
    override = os.environ.get("ANYFEM_SHELL_AXES_WORKERS", "").strip()
    if override:
        return max(0, int(override))
    return int(min(16, max(2, (os.cpu_count() or 2) // 2)))


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _sweep_stale_payloads() -> None:
    """Remove payload files of pool owners that no longer run.

    A contained worker retired by its owned tree (cancellation, application
    exit) cannot run its own cleanup; the next pool removes what it left.
    """

    import tempfile

    directory = tempfile.gettempdir()
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if not (name.startswith(_PAYLOAD_PREFIX) and name.endswith(".bin")):
            continue
        owner = name[len(_PAYLOAD_PREFIX):].split("-", 1)[0]
        if owner.isdigit() and int(owner) != os.getpid() and not _pid_alive(int(owner)):
            try:
                os.unlink(os.path.join(directory, name))
            except OSError:
                pass


class ShellAxesPool:
    """A spawn process pool for the per-shell loop, started ahead of use.

    Start it before the solve so the worker imports overlap the solve; use it
    for every case; close it (or leave the ``with`` block) afterwards.  On
    cancellation or failure every worker is terminated and joined with a
    bound; nothing waits on a pipe a dead worker may have left half written.
    """

    #: Seconds to wait for each terminated worker.
    TERMINATE_JOIN_SECONDS = 10.0

    def __init__(self, workers: int) -> None:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor

        if workers < 2:
            raise ValueError("a shell axes pool needs at least two workers")
        _sweep_stale_payloads()
        self.workers = int(workers)
        self.used_by: set = set()
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
        if not terminate:
            executor.shutdown(wait=True, cancel_futures=True)
            return
        processes = tuple((getattr(executor, "_processes", None) or {}).values())
        for process in processes:
            if process.is_alive():
                process.terminate()
        # Never block on the executor's manager: a worker killed while writing
        # a result can leave a partial message it would wait on (POSIX).
        executor.shutdown(wait=False, cancel_futures=True)
        for process in processes:
            process.join(self.TERMINATE_JOIN_SECONDS)

    def entries(self, carrier: Any, ids: Sequence[int],
                cancelled: Optional[Callable[[], None]]) -> tuple[list, list, list, str]:
        """Pool entries plus, per element, the copy's fingerprints (see module doc)."""

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
        handle, payload_path = tempfile.mkstemp(prefix=f"{_PAYLOAD_PREFIX}{os.getpid()}-", suffix=".bin")
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
        models = {result[1] for result in results}  # (nodes profile digest, mesh profile)
        self.used_by.update(result[0] for result in results)
        return ([entry for result in results for entry in result[3]],
                [fp for result in results for fp in result[2]],
                sorted(models), payload_path)


def _entries(fe_model: Any, u: np.ndarray, ids: Sequence[int], pool: Optional[ShellAxesPool],
             cancelled: Optional[Callable[[], None]], carrier: Any = None) -> tuple[list, dict]:
    def serial(subset):
        out = []
        for eid in subset:
            if cancelled is not None:
                cancelled()
            out.append(_element_entry(fe_model, u, eid))
        return out

    check = _admitted_value_check()
    if pool is None or carrier is None or len(ids) < POOL_MIN_SHELLS or check is None:
        return serial(ids), {"recovered_with": "serial"}
    from .solved_geometry import solved_model_of

    if solved_model_of(carrier) is not fe_model or not np.array_equal(
            np.asarray(carrier.displacements, dtype=float), u):
        raise ValueError("shell axes record: the pool carrier is not this model and displacement field")
    # The original's admission-relevant state, before anything recovers on it.
    nodes_print = _nodes_profile(fe_model, check)
    mesh_profile = _profile(fe_model.mesh, check)
    original = [_profile(fe_model.mesh.elements[eid], check) for eid in ids]
    entries, copied, models, _path = pool.entries(carrier, ids, cancelled)
    if len(entries) != len(ids) or len(copied) != len(ids):
        raise ValueError(f"shell axes record: pool returned {len(entries)} entries for {len(ids)} shells")
    if len(models) != 1 or models[0][0] != nodes_print or not _equivalent(
            mesh_profile, models[0][1], _class_names(type(fe_model.mesh))):
        # The transported nodes or mesh may be admitted differently from the
        # original: recover every element on the original (the serial record).
        return serial(ids), {"recovered_with": f"pool:{pool.workers}", "serial_rechecked": len(ids)}
    names: Dict[type, frozenset] = {}
    redo = []
    for index, (a, b) in enumerate(zip(original, copied)):
        kind = type(fe_model.mesh.elements[ids[index]])
        if kind not in names:
            names[kind] = _class_names(kind)
        if not _equivalent(a, b, names[kind]):
            redo.append(index)
    if redo:
        again = serial([ids[index] for index in redo])
        for index, entry in zip(redo, again):
            entries[index] = entry
    return entries, {"recovered_with": f"pool:{pool.workers}", "serial_rechecked": len(redo)}


# ------------------------------------------------------------------ record
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
    least ``POOL_MIN_SHELLS`` shells the loop runs in the pool (see the module
    docstring for why the result is still the serial record).  Returns the
    plain-data record (numpy arrays, lists of str, a provenance dict).
    """

    u = np.asarray(displacements, dtype=float)
    ids = shell_element_ids(fe_model)
    n_e = len(ids)
    entries, how = _entries(fe_model, u, ids, pool, cancelled, carrier)
    if len(entries) != n_e:
        raise ValueError(f"shell axes record: {len(entries)} entries for {n_e} shells")
    element_refusal = [""] * n_e
    element_reason = [""] * n_e
    metadata_refusal = [""] * n_e
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
        if entry[0] == "refused":
            element_refusal[index], element_reason[index] = entry[1], entry[2]
            continue
        _, thick, branch[index], conv, cert, reasons, details, pol, offs, values, meta = entry
        thickness[index] = thick
        metadata_refusal[index] = meta
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
        values = entry[9]
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
        "provenance": {**dict(provenance or {}), **how},
        "element_ids": np.asarray(ids, dtype=np.int64),
        "element_refusal": element_refusal,
        "element_refusal_reason": element_reason,
        "metadata_refusal": metadata_refusal,
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


# ------------------------------------------------------------------ reading
#: Values read per request (well under the desktop reader's working cap).
READ_BLOCK = 2_000_000


def _read_flat(dataset: Any, name: str, size: int) -> np.ndarray:
    blocks = [np.asarray(dataset.table(name, slice(start, min(size, start + READ_BLOCK)))).reshape(-1)
              for start in range(0, size, READ_BLOCK)]
    return np.concatenate(blocks) if blocks else np.asarray(dataset.table(name)).reshape(-1)


def read_shell_axes_table(dataset: Any, key: str, case: Optional[int] = None) -> Any:
    """Read one persisted record entry back exactly (fails closed).

    ``case`` selects one case of a per-case entry (``shell_axes_meta``
    ``per_case`` lists them); a shared entry is the same for every case.
    Float entries need their boolean presence mask of the same size (absent ->
    NaN); shapes come from the stored shape table; string columns are lists.
    Numeric reads are bounded blocks (the desktop reader caps one request).
    """

    meta = dataset.table("shell_axes_meta")
    if not isinstance(meta, Mapping) or meta.get("schema") != RECORD_SCHEMA:
        raise ValueError("shell axes tables: unknown or missing schema")
    entries = meta.get("entries", {})
    if key not in entries:
        raise KeyError(f"shell axes tables: no entry {key!r}")
    layout = entries[key]
    per_case = layout.get("layout") == "per_case"
    n_cases = len(meta.get("cases", ()))
    if per_case and case is None:
        raise ValueError(f"shell axes entry {key!r} is per case; pass case=0..{n_cases - 1}")
    if case is not None and not (0 <= int(case) < n_cases):
        raise IndexError(f"shell axes case {case} out of range 0..{n_cases - 1}")
    name = "shell_axes_" + key
    if layout.get("kind") == "strings":
        stored = dataset.table(name)
        if not isinstance(stored, Mapping) or set(stored) != {"values"} or not isinstance(stored["values"], list):
            raise ValueError(f"shell axes entry {key!r}: malformed string column")
        values = stored["values"]
        return list(values[int(case)]) if per_case else list(values)
    shape = tuple(int(v) for v in layout.get("shape", ()))
    dtype = np.dtype(layout.get("dtype", ""))
    if any(v < 0 for v in shape) or (per_case and (not shape or shape[0] != n_cases)):
        raise ValueError(f"shell axes entry {key!r}: malformed shape {shape}")
    size = int(np.prod(shape, dtype=np.int64))
    flat = _read_flat(dataset, name, size)
    if flat.dtype != dtype or flat.size != size:
        raise ValueError(f"shell axes entry {key!r}: stored {flat.dtype}[{flat.size}], expected {dtype}[{size}]")
    value = flat.reshape(shape)
    if dtype.kind == "f":
        present = _read_flat(dataset, name + "_present", size)
        if present.dtype != np.bool_ or present.size != size:
            raise ValueError(f"shell axes entry {key!r}: presence mask missing or malformed")
        value = np.where(present.reshape(shape), value, np.nan)
    return value[int(case)] if per_case else value
