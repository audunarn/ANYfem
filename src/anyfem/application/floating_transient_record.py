"""Immutable neutral records for the floating TRANSIENT (time-domain) workflow: job input and result.

Everything is explicit numeric data plus JSON metadata - no callbacks, no pickled objects, no executable persistence.
Records are saved as versioned ``.npz`` files whose header carries a SHA-256 content hash over the metadata and every
array; loading inspects the zip directory and every ``.npy`` header (names, shapes, dtypes, sizes) BEFORE any array is
materialised and refuses unknown / missing / oversize members.  Reopening a saved record never runs BEM or the solver.

Status vocabulary (never relabelled):
  * ``complete``                              every gate passed with ACCEPTED hydrodynamics;
  * ``completed_unverified_hydrodynamics``    the connected method ran to the end on explicitly UNVERIFIED (development)
                                              hydrodynamic operators; the unpassed hydro gates are listed; NOT an accepted
                                              structural load/result;
  * any other status is an explicit non-success (trajectory / guard / closure / relief / cancelled).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

import numpy as np

INPUT_FORMAT = "anyfem-floating-transient-input-v1"
RESULT_FORMAT = "anyfem-floating-transient-result-v1"
MAX_ARTIFACT_BYTES = 2 * 1024**3
MAX_HEADER_BYTES = 4 << 20
STATUS_COMPLETE = "complete"
STATUS_UNVERIFIED = "completed_unverified_hydrodynamics"
HYDRO_ACCEPTED = "accepted"
HYDRO_UNVERIFIED = "unverified"


class FloatingTransientRecordError(ValueError):
    """A floating transient record is invalid, stale, tampered or too large."""


def _canonical(value: Any) -> str:
    def plain(v):
        if isinstance(v, Mapping):
            return {str(k): plain(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [plain(x) for x in v]
        if isinstance(v, np.generic):
            v = v.item()
        if isinstance(v, np.ndarray):
            return plain(v.tolist())
        if isinstance(v, float) and not np.isfinite(v):
            return repr(v)                       # 'inf' / '-inf' / 'nan' as explicit strings (JSON has no infinity)
        return v
    return json.dumps(plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, np.generic):
        return value.item()
    return value


def _ro(a: Any, dtype=None) -> np.ndarray:
    raw = np.asarray(a)
    if raw.dtype.kind == "c" and (dtype is None or np.dtype(dtype).kind != "c"):
        raise FloatingTransientRecordError("complex data refused for a real-valued record field")
    arr = np.array(raw, dtype=dtype, copy=True, order="C")
    if arr.dtype.kind == "f" and not np.all(np.isfinite(arr)):
        raise FloatingTransientRecordError("non-finite values are not admitted in records")
    if arr.size == 0:
        arr.flags.writeable = False
        return arr
    return np.frombuffer(arr.tobytes(), dtype=arr.dtype).reshape(arr.shape)


def content_hash(meta: Mapping[str, Any], arrays: Mapping[str, np.ndarray]) -> str:
    h = hashlib.sha256()
    m = _canonical(meta).encode("utf-8")
    h.update(len(m).to_bytes(8, "little")); h.update(m)
    for name in sorted(arrays):
        a = np.ascontiguousarray(arrays[name])
        h.update(_canonical([name, list(a.shape), a.dtype.str]).encode("utf-8"))
        h.update(a.tobytes())
    return h.hexdigest()


def array_digest(a: Any) -> str:
    x = np.ascontiguousarray(np.asarray(a))
    h = hashlib.sha256(_canonical([list(x.shape), x.dtype.str]).encode("utf-8"))
    h.update(x.tobytes())
    return h.hexdigest()


class PublicationCancelled(FloatingTransientRecordError):
    """Cancellation arrived before the atomic replace: the temp is removed and an existing target is untouched."""


def save_npz(path: str, fmt: str, meta: Mapping[str, Any], arrays: Mapping[str, np.ndarray],
             cancel: Optional[Callable[[], bool]] = None) -> str:
    """Atomic, hashed, pickle-free ``.npz``; refuses above the artifact ceiling before writing.  ``cancel`` (optional)
    is polled before encoding, after the temp is written and IMMEDIATELY before ``os.replace``; a cancel raises
    PublicationCancelled and never replaces the existing target (default behaviour unchanged)."""
    def check(where):
        if cancel is not None and cancel():
            raise PublicationCancelled(f"cancelled {where}; nothing was published and the existing target is untouched")
    check("before encoding")
    stored = sum(int(np.asarray(a).nbytes) for a in arrays.values())
    if 2 * stored > MAX_ARTIFACT_BYTES:
        raise FloatingTransientRecordError(f"record needs {stored} B (2x on reload) > ceiling {MAX_ARTIFACT_BYTES} B")
    full_meta = dict(meta, format=fmt)
    digest = content_hash(full_meta, arrays)
    header = _canonical({"meta": full_meta, "content_hash": digest}).encode("utf-8")
    payload = {k: np.ascontiguousarray(v) for k, v in arrays.items()}
    if "header" in payload:
        raise FloatingTransientRecordError("'header' is a reserved member name")
    payload["header"] = np.frombuffer(header, dtype=np.uint8)
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            np.savez(handle, **payload)
        check("after serialisation")
        check("immediately before the atomic replace")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return digest


def load_npz(path: str, fmt: str, *, expected_hash: Optional[str] = None) -> tuple:
    """(meta, arrays) after header-first admission: zip directory + .npy headers, exact member set from the metadata,
    real numeric dtypes only, expanded size bound, then the content hash."""
    err = FloatingTransientRecordError
    # ONE open file handle for inspection, admission and reading: no path re-open between the header-first checks and
    # the allocation (no TOCTOU gap)
    try:
        stream = open(path, "rb")
    except OSError as exc:
        raise err(f"cannot read record: {exc}") from exc
    with stream:
        return _load_npz_stream(stream, fmt, expected_hash, err)


def _load_npz_stream(stream, fmt, expected_hash, err) -> tuple:
    size = os.fstat(stream.fileno()).st_size
    if 2 * size > MAX_ARTIFACT_BYTES:
        raise err("record file exceeds the artifact ceiling")
    members, header_raw = {}, None
    try:
        zf = zipfile.ZipFile(stream)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        raise err(f"record is corrupt or unreadable: {exc}") from exc
    with zf:
        return _load_from_zip(zf, fmt, expected_hash, err, members, header_raw)


def _load_from_zip(zf, fmt, expected_hash, err, members, header_raw) -> tuple:
    try:
        if True:
            infos = zf.infolist()
            if not 0 < len(infos) <= 4096:
                raise err("record has no members or too many members")
            for info in infos:
                name = info.filename
                if not name.endswith(".npy") or "/" in name or "\\" in name:
                    raise err(f"unsafe member {name!r}")
                key = name[:-4]
                if key in members:
                    raise err(f"duplicate member {name!r}")
                with zf.open(info) as fh:
                    version = np.lib.format.read_magic(fh)
                    if version == (1, 0):
                        shape, _f, dtype = np.lib.format.read_array_header_1_0(fh)
                    elif version == (2, 0):
                        shape, _f, dtype = np.lib.format.read_array_header_2_0(fh)
                    else:
                        raise err(f"unsupported .npy version {version}")
                if dtype.hasobject or dtype.kind not in "fiub":
                    raise err(f"member {name!r}: unsafe dtype {dtype}")
                count = 1
                for d in shape:
                    count *= int(d)
                if not count * dtype.itemsize <= info.file_size <= count * dtype.itemsize + 65536:
                    raise err(f"member {name!r}: size inconsistent with its header")
                if key == "header":
                    if info.file_size > MAX_HEADER_BYTES or dtype != np.dtype("u1"):
                        raise err("oversize or invalid header member")
                    header_raw = zf.read(info)
                members[key] = (tuple(int(d) for d in shape), dtype)
    except err:
        raise
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        raise err(f"record is corrupt or unreadable: {exc}") from exc
    if header_raw is None:
        raise err("record has no header")
    try:
        header = json.loads(bytes(np.lib.format.read_array(io.BytesIO(header_raw), allow_pickle=False).tobytes()).decode())
        meta = header["meta"]
    except Exception as exc:
        raise err(f"record header is malformed: {exc}") from exc
    if meta.get("format") != fmt:
        raise err(f"record format {meta.get('format')!r} is not {fmt!r}")
    declared = dict(meta.get("arrays", {}))
    del members["header"]
    if set(declared) != set(members):
        raise err(f"unexpected or missing arrays: {sorted(set(declared) ^ set(members))}")
    total = 0
    for key, (shape, dtype) in members.items():
        want = declared[key]
        if list(shape) != list(want["shape"]) or dtype.str != want["dtype"]:
            raise err(f"array {key!r} shape/dtype differ from the declaration")
        n = 1
        for d in shape:
            n *= d
        total += n * dtype.itemsize
    if 2 * total > MAX_ARTIFACT_BYTES:
        raise err("expanded record arrays exceed the artifact ceiling")
    arrays = {}
    for k in declared:                                     # same open archive that was just admitted
        with zf.open(k + ".npy") as fh:
            arrays[k] = np.lib.format.read_array(fh, allow_pickle=False)
    if content_hash(meta, arrays) != header.get("content_hash"):
        raise err("content hash mismatch: the record was modified or corrupted")
    if expected_hash is not None and header.get("content_hash") != expected_hash:
        raise err("record differs from the expected (stale) hash")
    return meta, arrays


def _declare(arrays: Mapping[str, np.ndarray]) -> dict:
    return {k: {"shape": list(np.asarray(v).shape), "dtype": np.asarray(v).dtype.str} for k, v in arrays.items()}


@dataclass(frozen=True)
class FloatingTransientResult:
    """Immutable result of one transient run (full accepted rigid history + structural output frames).

    ``times`` is the FULL accepted history; ``frame_indices`` select the structural output window from it (the window
    never restarts the physics).  Per frame: nodal loads by physical component and the residual-relief outputs.
    """

    status: str
    message: str
    hydrodynamic_verification: str
    unpassed_gates: tuple
    times: np.ndarray
    displacements: np.ndarray
    velocities: np.ndarray
    accelerations: np.ndarray
    thrust: np.ndarray
    relative_axial_speed: np.ndarray
    frame_indices: np.ndarray
    frame_loads: Mapping[str, np.ndarray]          # component -> (n_frames, n_nodes, 6)
    frame_displacements: np.ndarray                # (n_frames, n_output_dofs) elastic, gauge-fixed (may be empty)
    frame_correction_acceleration: np.ndarray      # (n_frames, 6): delta a_cm (3) + alpha (3)
    diagnostics: Mapping[str, Any]
    identities: Mapping[str, Any]
    # quasistatic effective-load element stresses, flattened per frame (layout in diagnostics['stress_layout'])
    frame_stresses: Any = None
    # actual input data preserved with the result: wind realization, thrust curve, attachment / mean data,
    # per-frame primary / total CG accelerations, ...  (name -> real array)
    metadata_arrays: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        err = FloatingTransientRecordError
        nf_raw = int(np.asarray(self.frame_indices).size)
        if self.frame_stresses is None:
            object.__setattr__(self, "frame_stresses", np.zeros((nf_raw, 0)))
        meta_in = dict(self.metadata_arrays)
        if sum(int(np.asarray(v).nbytes) for v in meta_in.values()) * 2 > MAX_ARTIFACT_BYTES:
            raise err("metadata arrays exceed the artifact ceiling")
        object.__setattr__(self, "metadata_arrays", MappingProxyType({str(k): _ro(v, np.float64)
                                                                    for k, v in meta_in.items()}))
        fs = _ro(self.frame_stresses, np.float64)
        if fs.ndim != 2 or fs.shape[0] != nf_raw:
            raise err(f"frame_stresses must be ({nf_raw}, n_values)")
        object.__setattr__(self, "frame_stresses", fs)
        # ---- bounded admission from shapes BEFORE any copy
        loads_in = dict(self.frame_loads)
        total = sum(int(np.asarray(getattr(self, n)).nbytes) for n in (
            "times", "displacements", "velocities", "accelerations", "thrust", "relative_axial_speed",
            "frame_indices", "frame_displacements", "frame_correction_acceleration"))
        total += sum(int(np.asarray(v).nbytes) for v in loads_in.values())
        if 2 * total > MAX_ARTIFACT_BYTES:
            raise err("result arrays exceed the artifact ceiling")
        raw_idx = np.asarray(self.frame_indices)
        if raw_idx.dtype.kind not in "iu":
            raise err("frame_indices must have an integer dtype (no fractional / bool / float indices)")
        for name in ("times", "displacements", "velocities", "accelerations", "thrust", "relative_axial_speed",
                     "frame_displacements", "frame_correction_acceleration"):
            object.__setattr__(self, name, _ro(getattr(self, name), np.float64))
        object.__setattr__(self, "frame_indices", _ro(raw_idx, np.int64))
        object.__setattr__(self, "frame_loads", MappingProxyType({str(k): _ro(v, np.float64)
                                                                for k, v in loads_in.items()}))
        object.__setattr__(self, "unpassed_gates", tuple(str(g) for g in self.unpassed_gates))
        # ---- structural consistency (valid non-success records may hold 0 frames and a partial history)
        t = self.times
        if t.ndim != 1 or (t.size > 1 and np.any(np.diff(t) <= 0.0)):
            raise err("times must be a strictly increasing 1-D accepted history")
        n = t.size
        for name in ("displacements", "velocities", "accelerations"):
            if getattr(self, name).shape != (n, 6):
                raise err(f"{name} must be ({n}, 6)")
        for name in ("thrust", "relative_axial_speed"):
            if getattr(self, name).shape != (n,):
                raise err(f"{name} must be ({n},)")
        fi = self.frame_indices
        if fi.ndim != 1 or (fi.size and (fi.min() < 0 or fi.max() >= n or np.any(np.diff(fi) <= 0))):
            raise err("frame_indices must be strictly increasing indices into the accepted history")
        nf = fi.size
        if self.frame_correction_acceleration.shape != (nf, 6):
            raise err(f"frame_correction_acceleration must be ({nf}, 6)")
        if self.frame_displacements.ndim != 2 or self.frame_displacements.shape[0] != nf:
            raise err(f"frame_displacements must be ({nf}, n_output_dofs)")
        nodes = {v.shape[1] for v in self.frame_loads.values() if v.ndim == 3}
        for k, v in self.frame_loads.items():
            if v.ndim != 3 or v.shape[0] != nf or v.shape[2] != 6:
                raise err(f"frame load {k!r} must be ({nf}, n_nodes, 6)")
        if len(nodes) > 1:
            raise err("frame load components disagree on the node count")
        if self.status in (STATUS_COMPLETE, STATUS_UNVERIFIED) and n < 1:
            raise err("a completed result needs an accepted history")
        if self.status == STATUS_COMPLETE and self.unpassed_gates:
            raise err("an accepted 'complete' result cannot carry unpassed gates")
        if self.status == STATUS_UNVERIFIED and not self.unpassed_gates:
            raise err("'completed_unverified_hydrodynamics' must list its unpassed gates")
        object.__setattr__(self, "diagnostics", _freeze(json.loads(_canonical(dict(self.diagnostics)))))
        object.__setattr__(self, "identities", _freeze(json.loads(_canonical(dict(self.identities)))))
        if self.status == STATUS_COMPLETE and self.hydrodynamic_verification != HYDRO_ACCEPTED:
            raise FloatingTransientRecordError("a result on unverified hydrodynamics can never be 'complete'")
        if self.status == STATUS_UNVERIFIED and self.hydrodynamic_verification != HYDRO_UNVERIFIED:
            raise FloatingTransientRecordError("'completed_unverified_hydrodynamics' requires unverified hydrodynamics")

    @property
    def accepted_structural_output(self) -> bool:
        """Only a complete run on ACCEPTED hydrodynamics is an ordinary accepted structural load/result."""
        return self.status == STATUS_COMPLETE and self.hydrodynamic_verification == HYDRO_ACCEPTED

    @property
    def qualification_status(self) -> str:
        return "unqualified"

    def _arrays(self) -> dict:
        out = dict(times=self.times, displacements=self.displacements, velocities=self.velocities,
                   accelerations=self.accelerations, thrust=self.thrust, relative_axial_speed=self.relative_axial_speed,
                   frame_indices=self.frame_indices, frame_displacements=self.frame_displacements,
                   frame_correction_acceleration=self.frame_correction_acceleration)
        out.update({f"load__{k}": v for k, v in self.frame_loads.items()})
        out["frame_stresses"] = self.frame_stresses
        out.update({f"meta__{k}": v for k, v in self.metadata_arrays.items()})
        return out

    def _meta(self) -> dict:
        return dict(status=self.status, message=self.message, hydrodynamic_verification=self.hydrodynamic_verification,
                    unpassed_gates=list(self.unpassed_gates), diagnostics=self.diagnostics, identities=self.identities,
                    load_components=sorted(self.frame_loads), metadata_arrays=sorted(self.metadata_arrays),
                    arrays=_declare(self._arrays()))

    def content_hash(self) -> str:
        return content_hash(dict(self._meta(), format=RESULT_FORMAT), self._arrays())

    def save(self, path: str, *, cancel: Optional[Callable[[], bool]] = None) -> str:
        return save_npz(path, RESULT_FORMAT, self._meta(), self._arrays(), cancel=cancel)


def load_floating_transient_result(path: str, *, expected_hash: Optional[str] = None) -> FloatingTransientResult:
    """Reopen a saved result: header-first admission, hash verification; never solves (no BEM, no transient)."""
    meta, arrays = load_npz(path, RESULT_FORMAT, expected_hash=expected_hash)
    loads = {k: arrays[f"load__{k}"] for k in meta["load_components"]}
    return FloatingTransientResult(
        status=meta["status"], message=meta["message"], hydrodynamic_verification=meta["hydrodynamic_verification"],
        unpassed_gates=tuple(meta["unpassed_gates"]), times=arrays["times"], displacements=arrays["displacements"],
        velocities=arrays["velocities"], accelerations=arrays["accelerations"], thrust=arrays["thrust"],
        relative_axial_speed=arrays["relative_axial_speed"], frame_indices=arrays["frame_indices"], frame_loads=loads,
        frame_displacements=arrays["frame_displacements"],
        frame_correction_acceleration=arrays["frame_correction_acceleration"], diagnostics=meta["diagnostics"],
        identities=meta["identities"], frame_stresses=arrays["frame_stresses"],
        metadata_arrays={k: arrays[f"meta__{k}"] for k in meta.get("metadata_arrays", [])})
