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
    "PoolWorkerLost",
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
def _admission_gate() -> Optional[Callable[[Any, Any], Any]]:
    """The module's own pre-recovery admission (exact type, plain element, mesh
    and nodes, exact attribute types), the first thing ``recover_shell_axes``
    does.  ``anyfesolver.shell_axes_outputs._admit`` is a private name of the
    opt-in module (a public equivalent is requested from the solver side);
    without it there is no pool and no early gate (``recover_shell_axes``
    still admits first)."""

    try:
        from anyfesolver.shell_axes_outputs import _admit
    except ImportError:
        return None
    return _admit if callable(_admit) else None


def _resolve_inputs(fe_model: Any, u: np.ndarray, eid: int):
    """(element, material, element displacements) or a refusal entry.

    Runs after admission: element methods are dispatched through the class
    and element data read from the instance dict; a lookup that fails on
    malformed data is a typed refusal of that element, not a job failure."""

    mesh = fe_model.mesh
    element = mesh.elements[eid]
    try:
        material = fe_model.get_material(vars(element).get("material_name"))
        mapping = np.asarray(type(element).get_dof_mapping(element, mesh), dtype=np.intp)
    except (TypeError, ValueError, KeyError, IndexError, AttributeError) as error:
        return ("refused", "AXES_RECOVERY_MALFORMED", f"material or DOF lookup: {type(error).__name__}: {error}")
    if mapping.ndim != 1 or mapping.size == 0 or int(mapping.min()) < 0 or int(mapping.max()) >= u.size:
        return ("refused", "AXES_MISSING_GLOBAL_TENSORS", "element has no complete in-range DOF mapping")
    return element, material, u[mapping], mapping


def _element_entry(fe_model: Any, u: np.ndarray, eid: int, admitted: bool = False) -> tuple:
    """Recover one shell; a plain, picklable entry.

    The module's admission runs first (here, or already by the caller when
    ``admitted``); nothing else of the element is used before it.
    """

    from anyfesolver.shell_axes_outputs import ShellAxesRefusal, recover_shell_axes

    mesh = fe_model.mesh
    element = mesh.elements[eid]
    gate = None if admitted else _admission_gate()
    if gate is not None:
        try:
            gate(element, mesh)
        except ShellAxesRefusal as refusal:
            return ("refused", refusal.code, refusal.reason)
    resolved = _resolve_inputs(fe_model, u, eid)
    if isinstance(resolved[0], str):
        return resolved
    element, material, element_u, _mapping = resolved
    state = vars(element)
    try:
        result = recover_shell_axes(element, mesh, element_u, material)
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


# ------------------------------------------------------- recovery-input digest
#: State that the result transport does not carry by value and that is not a
#: recovery input: stiffness/component caches the element recomputes from its
#: own inputs, and the solver's mutation-ownership tokens (epoch objects).
#: Measured (Q4 e4-pl, legacy Q8, S3 V2D; ANYfem 2026-11-27): these are the
#: only element and node entries whose values differ after the transport or
#: have no value identity.  Every other entry must be equal by value.
_TRANSPORT_EXEMPT = {
    "element": frozenset({
        "_stiffness_matrix", "_hourglass_stiffness_matrix", "_qualified_cache_key",
        "_qualified_component_guard", "_qualified_components",
        "_qualified_direct_state_token", "_qualified_direct_state_tokens",
    }),
    "node": frozenset({"_qualified_direct_state_token", "_qualified_direct_state_tokens"}),
}
_DIGEST_SCALARS = (float, int, bool, str, bytes, type(None), np.float64, np.int64, np.bool_, np.float32, np.int32)
_DIGEST_BUDGET = 200_000


class _Opaque(Exception):
    """A value whose identity the digest cannot establish."""


def _feed(digest: Any, value: Any) -> None:
    """Deterministic, process-independent value digest of plain data and of
    objects with an instance dict (recursively); anything else is opaque."""

    import types

    stack = [value]
    visited = 0
    while stack:
        item = stack.pop()
        visited += 1
        if visited > _DIGEST_BUDGET:
            raise _Opaque("digest budget exceeded")
        if isinstance(item, tuple) and len(item) == 2 and item[0] is _MARK:
            digest.update(item[1])
            continue
        kind = type(item)
        name = f"{kind.__module__}.{kind.__qualname__}".encode()
        if any(kind is scalar for scalar in _DIGEST_SCALARS):
            digest.update(b"s" + name + repr(item).encode("utf-8", "backslashreplace") + b"\x1f")
            continue
        if kind is np.ndarray:
            if item.dtype.hasobject:
                raise _Opaque("object ndarray")
            digest.update(b"a" + item.dtype.str.encode() + repr(item.shape).encode()
                          + np.ascontiguousarray(item).tobytes() + b"\x1f")
            continue
        # A shared reference is fed again (deterministic); a cycle exhausts the
        # budget and is opaque.
        if kind in (tuple, list):
            digest.update(b"(" + name + str(len(item)).encode())
            stack.append((_MARK, b")"))
            stack.extend(reversed(item))
        elif kind in (dict, types.MappingProxyType):
            pairs = sorted(item.items(), key=lambda pair: repr(pair[0]))
            digest.update(b"{" + name + str(len(pairs)).encode())
            stack.append((_MARK, b"}"))
            for key, entry in reversed(pairs):
                stack.append(entry)
                stack.append(key)
        else:
            state = getattr(item, "__dict__", None)
            if type(state) is not dict or kind in (set, frozenset):
                raise _Opaque(f"{kind.__qualname__} has no plain instance state")
            digest.update(b"o" + name)
            stack.append((_MARK, b"/o"))
            stack.append(state)


_MARK = object()


def _state_digest(obj: Any, exempt: frozenset, digest: Any) -> None:
    state = vars(obj)
    digest.update(f"{type(obj).__module__}.{type(obj).__qualname__}".encode())
    _feed(digest, {key: value for key, value in state.items() if key not in exempt})


def _input_digest(fe_model: Any, element: Any, material: Any, mapping: np.ndarray, element_u: np.ndarray) -> str:
    """What ``recover_shell_axes`` computes from, by value: the element's state
    (minus ``_TRANSPORT_EXEMPT``), its nodes, its material, its DOF mapping and
    displacements.  Raises ``_Opaque`` when identity cannot be established."""

    digest = hashlib.sha256()
    _state_digest(element, _TRANSPORT_EXEMPT["element"], digest)
    node_ids = vars(element).get("node_ids")
    if type(node_ids) not in (tuple, list):
        raise _Opaque("element node_ids")
    for nid in node_ids:
        _state_digest(fe_model.mesh.nodes[nid], _TRANSPORT_EXEMPT["node"], digest)
    _feed(digest, getattr(material, "__dict__", None) if hasattr(material, "__dict__") else material)
    _feed(digest, np.asarray(mapping))
    _feed(digest, np.asarray(element_u))
    return digest.hexdigest()


def _admitted_digest(fe_model: Any, u: np.ndarray, eid: int) -> Optional[str]:
    """Digest of an admitted element's recovery inputs, None if not comparable."""

    resolved = _resolve_inputs(fe_model, u, eid)
    if isinstance(resolved[0], str):
        return None
    element, material, element_u, mapping = resolved
    try:
        return _input_digest(fe_model, element, material, mapping, element_u)
    except (_Opaque, KeyError, TypeError):
        return None


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
    # Decoded once per worker and case.  Each element's input digest is taken
    # on the copy before its recovery; the caller accepts the entry only if it
    # equals the original's.
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
    out = []
    for eid in eids:
        digest = _admitted_digest(fe_model, u, int(eid))
        out.append((digest, _element_entry(fe_model, u, int(eid), admitted=True)))
    return os.getpid(), out


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


#: Payload files older than this are removed by the sweep even if their
#: owner pid looks alive (pid reuse).
_STALE_SECONDS = 2 * 24 * 3600.0


def _sweep_stale_payloads(directory: str) -> None:
    """Remove payload files of pool owners that no longer run (or are old).

    The normal owner of the payload is the job supervisor's per-job scratch
    directory, removed after the worker tree is retired; this sweep covers
    an abrupt exit of the supervisor itself."""

    import time

    try:
        names = os.listdir(directory)
    except OSError:
        return
    now = time.time()
    for name in names:
        if not (name.startswith(_PAYLOAD_PREFIX) and name.endswith(".bin")):
            continue
        path = os.path.join(directory, name)
        owner = name[len(_PAYLOAD_PREFIX):].split("-", 1)[0]
        try:
            old = now - os.path.getmtime(path) > _STALE_SECONDS
        except OSError:
            continue
        if old or (owner.isdigit() and int(owner) != os.getpid() and not _pid_alive(int(owner))):
            try:
                os.unlink(path)
            except OSError:
                pass


class PoolWorkerLost(RuntimeError):
    """A pool worker exited while tasks were outstanding."""


class ShellAxesPool:
    """A spawn process pool for the per-shell loop, started ahead of use.

    Start it before the solve so the worker imports overlap the solve; use it
    for every case; close it (or leave the ``with`` block) afterwards.  On
    cancellation, failure or the loss of a worker every worker is terminated
    and joined with a bound; nothing waits on a pipe a dead worker may have
    left half written.  ``scratch`` is the directory for the model payload
    (the job supervisor's per-job directory; default: the system temp
    directory, swept for stale files).
    """

    #: Seconds to wait for each terminated worker.
    TERMINATE_JOIN_SECONDS = 10.0

    def __init__(self, workers: int, scratch: Optional[str] = None) -> None:
        import multiprocessing
        import tempfile
        from concurrent.futures import ProcessPoolExecutor

        if workers < 2:
            raise ValueError("a shell axes pool needs at least two workers")
        self.scratch = scratch or tempfile.gettempdir()
        _sweep_stale_payloads(tempfile.gettempdir())
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

    @property
    def open(self) -> bool:
        return self._executor is not None

    def _processes(self) -> tuple:
        executor = self._executor
        return tuple((getattr(executor, "_processes", None) or {}).values()) if executor is not None else ()

    def close(self, terminate: bool = False) -> None:
        processes = self._processes()
        executor, self._executor = getattr(self, "_executor", None), None
        if executor is None:
            return
        if not terminate:
            executor.shutdown(wait=True, cancel_futures=True)
            return
        for process in processes:
            if process.is_alive():
                process.terminate()
        # Never block on the executor's manager: a worker killed while writing
        # a result can leave a partial message it would wait on (POSIX).
        executor.shutdown(wait=False, cancel_futures=True)
        for process in processes:
            process.join(self.TERMINATE_JOIN_SECONDS)

    def entries(self, carrier: Any, ids: Sequence[int],
                cancelled: Optional[Callable[[], None]]) -> list:
        """Per element ``(copy input digest, entry)``, in ``ids`` order.

        Raises :class:`PoolWorkerLost` (after terminating the pool) when a
        worker exits while tasks are outstanding."""

        from concurrent.futures import FIRST_COMPLETED, wait
        from concurrent.futures.process import BrokenProcessPool

        from ..job_process import dumps
        from ..result_transport import encode_result

        if self._executor is None:
            raise RuntimeError("the shell axes pool is closed")
        # Small tasks balance workers that become ready at different times.
        size = max(16, min(POOL_CHUNK, -(-len(ids) // (8 * self.workers))))
        chunks = [tuple(int(eid) for eid in ids[i:i + size]) for i in range(0, len(ids), size)]
        results: list = [None] * len(chunks)
        payload_path = os.path.join(self.scratch, f"{_PAYLOAD_PREFIX}{os.getpid()}-{id(self):x}-{len(ids)}.bin")
        try:
            with open(payload_path, "xb") as stream:
                stream.write(dumps(encode_result(carrier, verify=False)))
            processes = self._processes()
            pending = {self._executor.submit(_pool_chunk, payload_path, chunk): index
                       for index, chunk in enumerate(chunks)}
            processes = self._processes() or processes
            while pending:
                done, _ = wait(tuple(pending), timeout=0.2, return_when=FIRST_COMPLETED)
                if cancelled is not None:
                    cancelled()
                for future in done:
                    pid, rows = future.result()
                    self.used_by.add(pid)
                    results[pending.pop(future)] = rows
                if pending and any(not process.is_alive() for process in self._processes() or processes):
                    # A worker died (killed, crashed): the executor's manager may
                    # be blocked on its half-written result; do not wait on it.
                    raise PoolWorkerLost("a shell axes pool worker exited during recovery")
        except BrokenProcessPool as error:
            # Python noticed the lost worker (at submit or result).
            self.close(terminate=True)
            raise PoolWorkerLost(f"a shell axes pool worker exited: {error}") from error
        except BaseException:
            self.close(terminate=True)
            raise
        finally:
            try:
                os.unlink(payload_path)
            except OSError:
                pass
        return [row for rows in results for row in rows]


def _entries(fe_model: Any, u: np.ndarray, ids: Sequence[int], pool: Optional[ShellAxesPool],
             cancelled: Optional[Callable[[], None]], carrier: Any = None) -> tuple[list, dict]:
    """Entries in ``ids`` order and how they were obtained.

    Pool route (see the module docstring): the module's admission runs on every
    original here, before anything is dispatched; a refusal is final.  Admitted
    elements are recovered on transported copies; an entry is kept only if the
    copy's recovery-input digest equals the original's and the copy recovered
    (``ok``).  Everything else is recovered serially on the original.  If the
    pool cannot be used (gate unavailable, worker lost) the record is serial.
    """

    def serial(subset):
        out = []
        for eid in subset:
            if cancelled is not None:
                cancelled()
            out.append(_element_entry(fe_model, u, eid))
        return out

    gate = _admission_gate()
    if pool is None or not pool.open or carrier is None or len(ids) < POOL_MIN_SHELLS or gate is None:
        return serial(ids), {"recovered_with": "serial"}
    from anyfesolver.shell_axes_outputs import ShellAxesRefusal

    from .solved_geometry import solved_model_of

    if solved_model_of(carrier) is not fe_model or not np.array_equal(
            np.asarray(carrier.displacements, dtype=float), u):
        raise ValueError("shell axes record: the pool carrier is not this model and displacement field")
    entries: list = [None] * len(ids)
    originals: Dict[int, Optional[str]] = {}
    dispatch = []
    try:
        for index, eid in enumerate(ids):
            if cancelled is not None and index % 256 == 0:
                cancelled()
            try:
                gate(fe_model.mesh.elements[eid], fe_model.mesh)
            except ShellAxesRefusal as refusal:
                entries[index] = ("refused", refusal.code, refusal.reason)
                continue
            originals[index] = _admitted_digest(fe_model, u, eid)
            dispatch.append(index)
        pooled = pool.entries(carrier, [ids[index] for index in dispatch], cancelled) if dispatch else []
    except PoolWorkerLost:
        return serial(ids), {"recovered_with": "serial", "pool_lost": True}
    except BaseException:
        pool.close(terminate=True)  # cancellation or failure: no worker left behind
        raise
    if len(pooled) != len(dispatch):
        raise ValueError(f"shell axes record: pool returned {len(pooled)} entries for {len(dispatch)} shells")
    redo = []
    for index, (digest, entry) in zip(dispatch, pooled):
        original = originals[index]
        if original is not None and digest == original and entry[0] == "ok":
            entries[index] = entry
        else:
            redo.append(index)
    for index, entry in zip(redo, serial([ids[index] for index in redo])):
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


def _record_columns() -> Dict[str, tuple]:
    """Every record entry: (kind, dtype, rows, trailing shape); rows is "e"
    (one per shell), "e+1" (station offsets) or "s" (one per station)."""

    columns: Dict[str, tuple] = {
        "element_ids": ("numeric", "<i8", "e", ()),
        "station_offsets": ("numeric", "<i8", "e+1", ()),
        "station_positions": ("numeric", "<f8", "s", (3,)),
        "director_polarity": ("numeric", "|i1", "e", ()),
        "physical_reference_director": ("numeric", "<f8", "e", (3,)),
        "thickness": ("numeric", "<f8", "e", ()),
        "shear_normal_components": ("numeric", "<f8", "s", ()),
        "membrane_tensors": ("numeric", "<f8", "s", (3, 3)),
        "bending_tensors": ("numeric", "<f8", "s", (3, 3)),
        "shear_vectors": ("numeric", "<f8", "s", (3,)),
    }
    for key in _OFFSET_KEYS:
        columns[key] = ("numeric", "<f8", "e", ())
    for key in ("element_refusal", "element_refusal_reason", "metadata_refusal", "branch"):
        columns[key] = ("strings", None, "e", ())
    for fam in FAMILIES:
        columns[f"{fam}_certified"] = ("numeric", "|b1", "e", ())
        columns[f"{fam}_basis_normals"] = ("numeric", "<f8", "s", (3,))
        for suffix in ("refusal_reason", "refusal_detail", "convention"):
            columns[f"{fam}_{suffix}"] = ("strings", None, "e", ())
    return columns


def _read_span(dataset: Any, name: str, start: int, count: int, dtype: np.dtype) -> np.ndarray:
    """``count`` values from ``start`` of a flat table, in bounded blocks,
    into one preallocated array (nothing outside the span is kept)."""

    out = np.empty(count, dtype=dtype)
    for offset in range(0, count, READ_BLOCK):
        stop = min(count, offset + READ_BLOCK)
        block = np.asarray(dataset.table(name, slice(start + offset, start + stop)))
        if block.ndim != 1 or block.size != stop - offset or block.dtype != dtype:
            raise ValueError(f"shell axes table {name!r}: block {block.dtype}{block.shape} != {dtype}[{stop - offset}]")
        out[offset:stop] = block
    return out


def _check_flat(dataset: Any, name: str, size: int) -> None:
    if name not in tuple(dataset.table_keys):
        raise ValueError(f"shell axes table {name!r} is missing")
    shape = tuple(int(v) for v in dataset.table_shape(name))
    if shape != (size,):
        raise ValueError(f"shell axes table {name!r}: stored shape {shape}, expected flat ({size},)")


def read_shell_axes_table(dataset: Any, key: str, case: Optional[int] = None) -> Any:
    """Read one persisted record entry back exactly (fails closed).

    ``case`` selects one case of a per-case entry (``shell_axes_meta``
    ``entries[key].layout``); a shared entry is the same for every case.
    Every entry is validated against the record schema: dtype, rank and
    length of the stored table (checked before reading), shell and station
    counts (from ``element_ids`` and the case's ``station_offsets``); float
    entries need their boolean presence mask (absent -> NaN); string columns
    are one list of str per shell.  Only the selected case is read, in bounded
    blocks.
    """

    meta = dataset.table("shell_axes_meta")
    if not isinstance(meta, Mapping) or meta.get("schema") != RECORD_SCHEMA:
        raise ValueError("shell axes tables: unknown or missing schema")
    columns = _record_columns()
    entries = meta.get("entries")
    cases = meta.get("cases")
    if not isinstance(entries, Mapping) or not isinstance(cases, list) or not cases:
        raise ValueError("shell axes tables: malformed meta")
    if key not in columns or key not in entries:
        raise KeyError(f"shell axes tables: no entry {key!r}")
    n_cases = len(cases)
    layout = entries[key]
    if not isinstance(layout, Mapping) or layout.get("layout") not in ("shared", "per_case") \
            or layout.get("kind") != columns[key][0]:
        raise ValueError(f"shell axes entry {key!r}: malformed layout")
    per_case = layout["layout"] == "per_case"
    if per_case and case is None:
        raise ValueError(f"shell axes entry {key!r} is per case; pass case=0..{n_cases - 1}")
    if case is not None and (type(case) is not int or not 0 <= case < n_cases):
        raise IndexError(f"shell axes case {case!r} out of range 0..{n_cases - 1}")
    n_e = _shared_count(dataset, entries)
    kind, dtype_str, rows, trailing = columns[key]
    if rows == "e":
        n_rows = n_e
    elif rows == "e+1":
        n_rows = n_e + 1
    else:
        offsets = read_shell_axes_table(dataset, "station_offsets",
                                        case if entries["station_offsets"].get("layout") == "per_case" else None)
        n_rows = int(offsets[-1])
    if kind == "strings":
        name = f"shell_axes_{key}__{case}" if per_case else f"shell_axes_{key}"
        stored = dataset.table(name)
        values = stored.get("values") if isinstance(stored, Mapping) and set(stored) == {"values"} else None
        if not isinstance(values, list) or len(values) != n_rows or not all(type(v) is str for v in values):
            raise ValueError(f"shell axes entry {key!r}: malformed string column")
        return list(values)
    dtype = np.dtype(dtype_str)
    expected = ((n_cases,) if per_case else ()) + (n_rows, *trailing)
    if layout.get("dtype") != dtype.str or tuple(layout.get("shape", ())) != expected:
        raise ValueError(f"shell axes entry {key!r}: declared {layout.get('dtype')}{layout.get('shape')}, "
                         f"expected {dtype.str}{list(expected)}")
    inner = int(np.prod((n_rows, *trailing), dtype=np.int64))
    total = inner * (n_cases if per_case else 1)
    start = inner * case if per_case else 0
    name = "shell_axes_" + key
    _check_flat(dataset, name, total)
    value = _read_span(dataset, name, start, inner, dtype).reshape((n_rows, *trailing))
    if dtype.kind == "f":
        _check_flat(dataset, name + "_present", total)
        present = _read_span(dataset, name + "_present", start, inner, np.dtype(np.bool_)).reshape(value.shape)
        value = np.where(present, value, np.nan)
    elif (name + "_present") in tuple(dataset.table_keys):
        raise ValueError(f"shell axes entry {key!r}: unexpected presence mask on a non-float entry")
    return value


def _shared_count(dataset: Any, entries: Mapping[str, Any]) -> int:
    layout = entries.get("element_ids")
    if not isinstance(layout, Mapping) or layout.get("layout") != "shared" or layout.get("dtype") != "<i8":
        raise ValueError("shell axes tables: element_ids must be one shared int64 column")
    shape = layout.get("shape")
    if not isinstance(shape, list) or len(shape) != 1 or type(shape[0]) is not int or shape[0] < 0:
        raise ValueError("shell axes tables: malformed element_ids shape")
    _check_flat(dataset, "shell_axes_element_ids", shape[0])
    return shape[0]
