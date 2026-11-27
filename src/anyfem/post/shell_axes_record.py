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

The loop is serial (a parallel route is a separate, later slice).  Each
shell is first admitted by the solver module's own pre-recovery gate, before
any of its material, DOFs or data are used.  Temporary dependency: that gate
is the module's private ``_admit`` until the solver exposes a public
``admit_shell_axes``; without it every shell is refused
(``AXES_ADMISSION_UNAVAILABLE``), never recovered unadmitted.

Reader contract (owner decision 2026-11-27, option A).  The reader checks
the stored record's structural integrity (schema, dtypes, shapes, lengths,
presence masks, table inventory, its cases against the artifact's frames,
frame kind, solution type and load case names) and the record's own
invariants (offsets, unique element ids, refusal/certification/normal
consistency, metadata domains).  It does NOT bind the record to the solved
mesh or the solver's recovery rules.  That binding belongs to D2-2, which
must refuse an unbound or mismatched record with a typed refusal before any
use: element ids exist in the solved mesh, each element's station count
follows its recovery rule, station positions are the shape-function
positions on the element, and branch metadata is consistent (e.g. a shifted
offset table that keeps the total, or a V2D record with polarity 0 and a
present director).  In this slice nothing displays or derives results from
the record; its only reader is ``read_shell_axes_table``.

No Qt; runs in the analysis worker after the solve, only on the axes-request
path.  The ordinary path (no ``axes_*`` request) never imports this module.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

import numpy as np

__all__ = [
    "FAMILIES",
    "RECORD_SCHEMA",
    "read_shell_axes_meta",
    "read_shell_axes_table",
    "recover_shell_axes_record",
    "check_record_columns",
    "encode_meta",
    "encode_strings",
    "validate_shell_axes_tables",
    "shell_element_ids",
]

RECORD_SCHEMA = "anyfem.shell_axes_record/1"
SOLVER_MODULE = "anyfesolver.shell_axes_outputs.recover_shell_axes"
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


#: Solver-provided text (refusal reasons and details) is kept up to this many
#: characters; a longer text is cut and marked (the reader caps a stored row
#: at MAX_STRING_BYTES, which this always fits in UTF-8).
MAX_TEXT_CHARS = 1_000
MAX_STRING_BYTES = 4_096
_TRUNCATED = " [truncated]"


def _text(value: Any) -> str:
    text = str(value)
    return text if len(text) <= MAX_TEXT_CHARS else text[:MAX_TEXT_CHARS - len(_TRUNCATED)] + _TRUNCATED


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
    """(element, material, element displacements, mapping) or a refusal entry.

    Runs after admission: element methods are dispatched through the class
    and element data read from the instance dict.  The material must be the
    one registered under the element's exact material name (``get_material``
    falls back to a default for unknown names, which would recover with the
    wrong law), and the DOF mapping must be a complete integer map of the
    element's own size, in range.  Anything else is a typed refusal of that
    element, never a fallback.
    """

    mesh = fe_model.mesh
    element = mesh.elements[eid]
    name = vars(element).get("material_name")
    materials = getattr(fe_model, "materials", None)
    if type(name) is not str or not isinstance(materials, Mapping) or name not in materials:
        return ("refused", "AXES_RECOVERY_MALFORMED", f"material {name!r} is not registered on the model")
    try:
        material = fe_model.get_material(name)
        mapping = np.asarray(type(element).get_dof_mapping(element, mesh))
        expected = int(element.total_dofs)  # admitted: no instance attribute shadows it
    except (TypeError, ValueError, KeyError, IndexError, AttributeError) as error:
        return ("refused", "AXES_RECOVERY_MALFORMED", _text(f"material or DOF lookup: {type(error).__name__}: {error}"))
    if material is not materials[name]:
        return ("refused", "AXES_RECOVERY_MALFORMED", f"material {name!r} lookup does not return the registered one")
    if mapping.ndim != 1 or mapping.dtype.kind not in "iu" or mapping.size != expected:
        return ("refused", "AXES_MISSING_GLOBAL_TENSORS",
                f"DOF mapping {mapping.dtype}{mapping.shape} is not a complete integer map of {expected} DOFs")
    mapping = mapping.astype(np.intp)
    if int(mapping.min()) < 0 or int(mapping.max()) >= u.size:
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
    if not admitted:
        gate = _admission_gate()
        if gate is None:
            # Never resolve material or DOFs of an unadmitted element (fail closed).
            return ("refused", "AXES_ADMISSION_UNAVAILABLE",
                    "the solver module's admission gate is unavailable in this installation")
        try:
            gate(element, mesh)
        except ShellAxesRefusal as refusal:
            return ("refused", _text(refusal.code), _text(refusal.reason))
    resolved = _resolve_inputs(fe_model, u, eid)
    if isinstance(resolved[0], str):
        return resolved
    element, material, element_u, _mapping = resolved
    state = vars(element)
    try:
        result = recover_shell_axes(element, mesh, element_u, material)
    except ShellAxesRefusal as refusal:
        return ("refused", _text(refusal.code), _text(refusal.reason))
    axes = result.axes
    arrays = axes.arrays
    refusals = dict(axes.refusals)
    if "positions" in refusals or "recovery_station_positions" not in arrays:
        refusal = refusals.get("positions")
        reason = "station positions refused" if refusal is None else f"{refusal.reason}: {refusal.detail}"
        return ("refused", "AXES_FRAME_RULE_MISMATCH", _text(f"positions: {reason}"))
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
        {fam: _text(refusals[fam].reason) for fam in FAMILIES if fam in refusals},
        {fam: _text(refusals[fam].detail) for fam in FAMILIES if fam in refusals},
        polarity, offsets, values,
        "" if metadata is None else _text(f"{metadata.reason}: {metadata.detail}"),
    )


def _entries(fe_model: Any, u: np.ndarray, ids: Sequence[int],
             cancelled: Optional[Callable[[], None]]) -> list:
    entries = []
    for eid in ids:
        if cancelled is not None:
            cancelled()
        entries.append(_element_entry(fe_model, u, eid))
    return entries


# ------------------------------------------------------------------ record
def recover_shell_axes_record(
    fe_model: Any,
    displacements: Any,
    *,
    provenance: Optional[Mapping[str, Any]] = None,
    cancelled: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """Recover every shell of ``fe_model`` through ``recover_shell_axes``.

    ``displacements`` is the solved global vector of one case.  ``cancelled``
    (optional) is called before each element and may raise to stop the loop.
    Returns the plain-data record (numpy arrays, lists of str, a provenance
    dict); see the module docstring.
    """

    u = np.asarray(displacements, dtype=float)
    ids = shell_element_ids(fe_model)
    n_e = len(ids)
    entries = _entries(fe_model, u, ids, cancelled)
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
            element_refusal[index], element_reason[index] = _text(entry[1]), _text(entry[2])
            continue
        _, thick, branch[index], conv, cert, reasons, details, pol, offs, values, meta = entry
        thickness[index] = thick
        metadata_refusal[index] = _text(meta)
        counts[index] = values["positions"].shape[0]
        for fam in FAMILIES:
            conventions[fam][index] = conv[fam]
            certified[fam][index] = cert[fam]
            fam_reason[fam][index] = _text(reasons.get(fam, ""))
            fam_detail[fam][index] = _text(details.get(fam, ""))
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
        "provenance": dict(provenance or {}),
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
#: Shells validated per span, and stations per span (together they bound the
#: validation's working memory).
VALIDATE_SHELLS = 65_536
VALIDATE_STATIONS = 262_144
#: Bytes of string rows held per span, all string columns together.
VALIDATE_STRING_BYTES = 16 * 1024 * 1024
#: Structural cap on one shell's stations (no element has more than 9; a
#: larger count is a malformed record, refused before it is read).
MAX_STATIONS_PER_SHELL = 1_024
_ANALYSES = {"linear_static": "static", "batch_linear_static": "load_case"}
_SOLUTION_TYPES = {"linear_static": "LinearSolution", "batch_linear_static": "LinearBatchSolution"}


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
    if count == 0:  # the stored dtype is checked even for an empty span
        block = np.asarray(dataset.table(name, slice(start, start)))
        if block.ndim != 1 or block.size != 0 or block.dtype != dtype:
            raise ValueError(f"shell axes table {name!r}: block {block.dtype}{block.shape} != {dtype}[0]")
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


def _int_list(value: Any, what: str) -> list:
    if not isinstance(value, list) or not all(type(v) is int and v >= 0 for v in value):
        raise ValueError(f"shell axes tables: {what} must be a list of non-negative ints")
    return value


#: Largest stored record metadata (its size is O(cases), checked before decoding).
MAX_META_BYTES = 4 * 1024 * 1024


def encode_meta(meta: Mapping[str, Any]) -> np.ndarray:
    """The record metadata as UTF-8 JSON bytes (uint8), size-checked."""

    import json

    data = json.dumps(meta, sort_keys=True, allow_nan=False, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_META_BYTES:
        raise ValueError(f"shell axes record: metadata exceeds {MAX_META_BYTES} bytes")
    return np.frombuffer(data, dtype=np.uint8).copy()


def _read_meta(dataset: Any) -> Any:
    """The stored metadata, its byte size checked BEFORE it is read or decoded."""

    import json

    name = "shell_axes_meta__bytes"
    if name not in tuple(dataset.table_keys):
        raise ValueError("shell axes tables: missing metadata")
    shape = tuple(int(v) for v in dataset.table_shape(name))
    if len(shape) != 1 or shape[0] > MAX_META_BYTES:
        raise ValueError(f"shell axes tables: metadata shape {shape} exceeds {MAX_META_BYTES} bytes")
    data = _read_span(dataset, name, 0, shape[0], np.dtype("|u1")).tobytes()

    def unique(pairs):
        keys = [key for key, _value in pairs]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate keys")
        return dict(pairs)

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"shell axes tables: malformed metadata: {error}") from error


def read_shell_axes_meta(dataset: Any) -> Dict[str, Any]:
    """The record metadata (cases, entry layouts), after full validation."""

    _validated(dataset)
    return dict(_read_meta(dataset))


def encode_strings(values: Sequence[str]) -> tuple:
    """A string column as flat UTF-8 bytes (uint8) and int64 row offsets."""

    encoded = [str(value).encode("utf-8") for value in values]
    if any(len(item) > MAX_STRING_BYTES for item in encoded):
        raise ValueError(f"shell axes record: a string exceeds {MAX_STRING_BYTES} bytes")
    offsets = np.zeros(len(encoded) + 1, dtype=np.int64)
    np.cumsum([len(item) for item in encoded], out=offsets[1:])
    data = np.frombuffer(b"".join(encoded), dtype=np.uint8).copy()
    return data, offsets


def _string_rows(dataset: Any, base: str, n_e: int, lo: int, hi: int, key: str) -> list:
    """Rows [lo, hi) of a stored string column, bounded: offsets of the span,
    then exactly the bytes they address; every row at most MAX_STRING_BYTES,
    strict UTF-8."""

    offsets = _read_span(dataset, base + "__offsets", lo, hi - lo + 1, np.dtype("<i8"))
    values = [int(v) for v in offsets]
    if any(b < a or b - a > MAX_STRING_BYTES for a, b in zip(values, values[1:])):
        raise ValueError(f"shell axes entry {key!r}: malformed string offsets")
    data = _read_span(dataset, base + "__bytes", values[0], values[-1] - values[0], np.dtype("|u1")).tobytes()
    out = []
    for a, b in zip(values, values[1:]):
        try:
            out.append(data[a - values[0]:b - values[0]].decode("utf-8"))
        except UnicodeDecodeError as error:
            raise ValueError(f"shell axes entry {key!r}: a row is not UTF-8") from error
    return out


class _Layout:
    """The validated declarations of one artifact (no values read yet)."""

    def __init__(self, dataset: Any) -> None:
        meta = _read_meta(dataset)
        if not isinstance(meta, Mapping) or meta.get("schema") != RECORD_SCHEMA:
            raise ValueError("shell axes tables: unknown or missing schema")
        if meta.get("solver_module") != SOLVER_MODULE or set(meta) != {"schema", "solver_module", "cases", "entries"}:
            raise ValueError("shell axes tables: missing or unknown producer declaration")
        entries, cases = meta.get("entries"), meta.get("cases")
        if not isinstance(entries, Mapping) or not isinstance(cases, list) or not cases:
            raise ValueError("shell axes tables: malformed meta")
        if any(not isinstance(entry, Mapping) or type(entry.get("case_index")) is not int
               or entry["case_index"] != slot for slot, entry in enumerate(cases)):
            raise ValueError("shell axes tables: case metadata does not name its slot")
        for entry in cases:
            # exactly the fields the worker writes, with their types
            if not set(entry) <= {"analysis", "case_index", "case_label", "case_name"} \
                    or {"analysis", "case_index", "case_label"} - set(entry) \
                    or any(type(entry[key]) is not str for key in ("analysis", "case_label", "case_name") if key in entry):
                raise ValueError("shell axes tables: malformed case metadata")
        columns = _record_columns()
        if set(entries) != set(columns):
            raise ValueError(f"shell axes tables: entries differ from the record schema: missing "
                             f"{sorted(set(columns) - set(entries))}, unknown {sorted(set(entries) - set(columns))}")
        for name, layout in entries.items():
            if not isinstance(layout, Mapping) or layout.get("layout") not in ("shared", "per_case") \
                    or layout.get("kind") != columns[name][0]:
                raise ValueError(f"shell axes tables: malformed layout of {name!r}")
            keys = {"layout", "kind", "dtype", "shape"} if columns[name][0] == "numeric" else {"layout", "kind"}
            if set(layout) != keys:
                raise ValueError(f"shell axes tables: declaration of {name!r} has keys {sorted(layout)}")
            if columns[name][0] == "numeric":
                if layout.get("dtype") != np.dtype(columns[name][1]).str:
                    raise ValueError(f"shell axes tables: {name!r} declared dtype {layout.get('dtype')!r}")
                _int_list(layout.get("shape"), f"{name!r} shape")
        if entries["element_ids"]["layout"] != "shared":
            raise ValueError("shell axes tables: element_ids must be shared")
        self.entries, self.cases, self.n_cases = entries, cases, len(cases)
        self._check_cases(dataset)
        shape = entries["element_ids"]["shape"]
        if len(shape) != 1:
            raise ValueError("shell axes tables: malformed element_ids shape")
        self.n_e = shape[0]
        self._check_inventory(dataset)

    def _check_cases(self, dataset: Any) -> None:
        """The axes cases are the artifact's own cases: one analysis, its frame
        kind, frames 0..n-1 in order, and for a batch its load case names."""

        analyses = {entry.get("analysis") for entry in self.cases}
        if len(analyses) != 1 or next(iter(analyses)) not in _ANALYSES:
            raise ValueError(f"shell axes tables: analysis {sorted(map(str, analyses))} not qualified")
        analysis = next(iter(analyses))
        provenance = dataset.metadata("provenance") if callable(getattr(dataset, "metadata", None)) else None
        solution_type = provenance.get("solution_type") if isinstance(provenance, Mapping) else None
        if solution_type != _SOLUTION_TYPES[analysis]:
            raise ValueError(f"shell axes tables: {analysis} records in a {solution_type!r} result")
        identity = getattr(dataset, "identity", None)
        kind = identity.get("frame_kind") if isinstance(identity, Mapping) else None
        if kind != _ANALYSES[analysis]:
            raise ValueError(f"shell axes tables: frame kind {kind!r} is not that of {analysis}")
        frames = np.asarray(getattr(dataset, "frames", ()), dtype=float).reshape(-1)
        if frames.tolist() != [float(index) for index in range(self.n_cases)]:
            raise ValueError(f"shell axes tables: artifact frames {frames.tolist()} are not the "
                             f"{self.n_cases} axes case(s) in order")
        has_names = "load_case_names" in tuple(dataset.table_keys)
        if analysis == "linear_static":
            if self.n_cases != 1 or has_names or "case_name" in self.cases[0]:
                raise ValueError("shell axes tables: a linear static result has exactly one case and no case names")
            return
        if not has_names:
            raise ValueError("shell axes tables: a batch result needs its load case names")
        # The writer bound each case_name to the artifact's load_case_names
        # before publishing.  The reader does not re-read that legacy JSON
        # table (no size bound before decoding); it requires a typed name per
        # case.  Binding to the artifact's names beyond that is D2-2's.
        if any(type(entry.get("case_name")) is not str for entry in self.cases):
            raise ValueError("shell axes tables: a batch case has no case name")

    def _check_inventory(self, dataset: Any) -> None:
        expected = {"shell_axes_meta__bytes"}
        for key, layout in self.entries.items():
            name = "shell_axes_" + key
            if layout["kind"] == "strings":
                bases = [name] if layout["layout"] == "shared" else [f"{name}__{index}" for index in range(self.n_cases)]
                for base in bases:
                    expected.update((base + "__bytes", base + "__offsets"))
            else:
                expected.add(name)
                if np.dtype(_record_columns()[key][1]).kind == "f":
                    expected.add(name + "_present")
        actual = {name for name in tuple(dataset.table_keys) if name.startswith("shell_axes_")}
        if actual != expected:
            raise ValueError(f"shell axes tables: inventory differs: missing {sorted(expected - actual)}, "
                             f"surplus {sorted(actual - expected)}")

    def rows(self, key: str, n_s: int) -> int:
        rows = _record_columns()[key][2]
        return self.n_e if rows == "e" else self.n_e + 1 if rows == "e+1" else n_s

    def check_shape(self, dataset: Any, key: str, n_s_all: Sequence[int]) -> None:
        """Declared shape and stored flat length of a numeric entry."""

        kind, dtype_str, rows, trailing = _record_columns()[key]
        layout = self.entries[key]
        per_case = layout["layout"] == "per_case"
        counts = [self.rows(key, n_s) for n_s in n_s_all]
        if (not per_case or rows == "s") and len(set(counts)) != 1:
            raise ValueError(f"shell axes entry {key!r}: one shape for cases with different station counts")
        expected = ([self.n_cases] if per_case else []) + [counts[0], *trailing]
        if layout["shape"] != expected:
            raise ValueError(f"shell axes entry {key!r}: declared shape {layout['shape']}, expected {expected}")
        total = math.prod(int(v) for v in expected)
        _check_flat(dataset, "shell_axes_" + key, total)
        if np.dtype(dtype_str).kind == "f":
            _check_flat(dataset, "shell_axes_" + key + "_present", total)

    def read_rows(self, dataset: Any, key: str, case: int, lo: int, hi: int, n_s: int) -> np.ndarray:
        """Rows [lo, hi) of a numeric entry for one case, with its mask applied."""

        kind, dtype_str, rows, trailing = _record_columns()[key]
        per_case = self.entries[key]["layout"] == "per_case"
        width = math.prod(int(v) for v in trailing)
        inner = self.rows(key, n_s) * width
        start = (inner * case if per_case else 0) + lo * width
        dtype = np.dtype(dtype_str)
        name = "shell_axes_" + key
        value = _read_span(dataset, name, start, (hi - lo) * width, dtype).reshape((hi - lo, *trailing))
        if dtype.kind == "f":
            present = _read_span(dataset, name + "_present", start, (hi - lo) * width,
                                 np.dtype(np.bool_)).reshape(value.shape)
            if not np.isfinite(value[present]).all():
                raise ValueError(f"shell axes entry {key!r}: a present value is not finite")
            if np.any(value[~present] != 0.0):
                raise ValueError(f"shell axes entry {key!r}: an absent value is not stored as 0.0")
            value = np.where(present, value, np.nan)
        return value

    def string_base(self, key: str, case: int) -> str:
        per_case = self.entries[key]["layout"] == "per_case"
        return f"shell_axes_{key}__{case}" if per_case else f"shell_axes_{key}"

    def check_strings(self, dataset: Any, key: str, case: int) -> None:
        """Stored lengths of one string column: offsets n_e+1, zero-based,
        bytes exactly as long as the last offset (read as one value)."""

        base = self.string_base(key, case)
        _check_flat(dataset, base + "__offsets", self.n_e + 1)
        first = _read_span(dataset, base + "__offsets", 0, 1, np.dtype("<i8"))
        last = _read_span(dataset, base + "__offsets", self.n_e, 1, np.dtype("<i8"))
        if int(first[0]) != 0 or int(last[0]) < 0:
            raise ValueError(f"shell axes entry {key!r}: malformed string offsets")
        _check_flat(dataset, base + "__bytes", int(last[0]))
        _read_span(dataset, base + "__bytes", 0, 0, np.dtype("|u1"))  # dtype, also when empty

    def read_strings(self, dataset: Any, key: str, case: int, lo: int = 0, hi: Optional[int] = None) -> list:
        return _string_rows(dataset, self.string_base(key, case), self.n_e, lo, self.n_e if hi is None else hi, key)


def _validated(dataset: Any) -> tuple:
    """Validate the whole artifact once, in bounded shell spans (declarations,
    inventory, cases against the artifact, shapes and stored lengths, offsets,
    unique ids, finite present values, and the record invariants per shell);
    cached on the dataset object when possible."""

    cached = getattr(dataset, "_shell_axes_validated", None)
    if cached is not None:
        return cached
    layout = _Layout(dataset)
    n_e = layout.n_e
    _check_flat(dataset, "shell_axes_element_ids", n_e)
    _read_span(dataset, "shell_axes_element_ids", 0, 0, np.dtype("<i8"))  # dtype, also with no shells
    seen: set = set()
    for lo in range(0, n_e, VALIDATE_SHELLS):
        block = _read_span(dataset, "shell_axes_element_ids", lo, min(n_e, lo + VALIDATE_SHELLS) - lo,
                           np.dtype("<i8"))
        for value in block.tolist():
            if value in seen:
                raise ValueError("shell axes entry 'element_ids': duplicate element ids")
            seen.add(value)
    seen.clear()
    offsets_name = "shell_axes_station_offsets"
    offsets_shared = layout.entries["station_offsets"]["layout"] == "shared"
    layout.check_shape(dataset, "station_offsets", [0] * layout.n_cases)

    def offsets_span(case: int, lo: int, hi: int) -> np.ndarray:
        """Offsets of shells [lo, hi] (hi inclusive) of one case, streamed."""

        slot = 0 if offsets_shared else case
        return _read_span(dataset, offsets_name, slot * (n_e + 1) + lo, hi - lo + 1, np.dtype("<i8"))

    def check_offsets(case: int) -> int:
        """Zero-based and nondecreasing (Python ints: no wrapping), in spans;
        returns the case's station count."""

        previous = None
        for lo in range(0, n_e + 1, VALIDATE_SHELLS):
            hi = min(n_e, lo + VALIDATE_SHELLS)
            values = [int(v) for v in offsets_span(case, lo, hi)]
            if lo == 0 and values[0] != 0:
                break
            if previous is not None and values[0] != previous:
                raise ValueError("shell axes entry 'station_offsets': inconsistent spans")
            if any(a > b for a, b in zip(values, values[1:])):
                break
            previous = values[-1]
            if hi == n_e:
                return previous
        raise ValueError("shell axes entry 'station_offsets': not zero-based and nondecreasing")

    n_s = tuple(check_offsets(case) for case in (range(1) if offsets_shared else range(layout.n_cases)))
    if offsets_shared:
        n_s = n_s * layout.n_cases
    for key, (kind, _dtype, _rows, _trailing) in _record_columns().items():
        if kind == "numeric" and key != "station_offsets":
            layout.check_shape(dataset, key, n_s)
    station_keys = [key for key, column in _record_columns().items() if column[0] == "numeric" and column[2] == "s"]
    shell_keys = [key for key, column in _record_columns().items()
                  if column[0] == "numeric" and column[2] == "e" and key != "element_ids"]
    string_keys = [key for key, column in _record_columns().items() if column[0] == "strings"]
    for case in range(layout.n_cases):
        # every stored column and mask is dtype-checked, also when it is empty
        for key in shell_keys + station_keys:
            layout.read_rows(dataset, key, case, 0, 0, n_s[case])
        for key in string_keys:
            layout.check_strings(dataset, key, case)
        lo = 0
        while lo < n_e:
            # a span of at most VALIDATE_SHELLS shells and VALIDATE_STATIONS
            # stations (at least one shell, itself capped)
            offsets = offsets_span(case, lo, min(n_e, lo + VALIDATE_SHELLS))
            counts = np.diff(offsets)
            if int(counts.max(initial=0)) > MAX_STATIONS_PER_SHELL:
                raise ValueError(f"shell axes case {case}: a shell has more than {MAX_STATIONS_PER_SHELL} stations")
            within = np.cumsum(counts) <= VALIDATE_STATIONS
            take = max(1, int(np.count_nonzero(within)))
            # ... and at most VALIDATE_STRING_BYTES of string rows, all columns
            row_bytes = np.zeros(take, dtype=np.int64)
            for key in string_keys:
                spans = _read_span(dataset, layout.string_base(key, case) + "__offsets", lo, take + 1,
                                   np.dtype("<i8"))
                lengths = np.diff(spans)
                if np.any(lengths < 0) or np.any(lengths > MAX_STRING_BYTES):
                    raise ValueError(f"shell axes entry {key!r}: malformed string offsets")
                row_bytes += lengths
            take = max(1, int(np.count_nonzero(np.cumsum(row_bytes) <= VALIDATE_STRING_BYTES)))
            hi = lo + take
            offsets = offsets[:take + 1]
            s_lo, s_hi = int(offsets[0]), int(offsets[-1])
            row = {key: layout.read_rows(dataset, key, case, lo, hi, n_s[case]) for key in shell_keys}
            row.update({key: layout.read_rows(dataset, key, case, s_lo, s_hi, n_s[case]) for key in station_keys})
            row.update({key: layout.read_strings(dataset, key, case, lo, hi) for key in string_keys})
            row["station_offsets"] = offsets - s_lo
            _check_consistency(row, case, lo)
            lo = hi
    result = (layout, n_s)
    try:
        dataset._shell_axes_validated = result
    except (AttributeError, TypeError):
        pass
    return result


def _whole(values: np.ndarray) -> Optional[bool]:
    """True if every value is present, False if none is, None if mixed (the
    writer only ever emits a shell's or a row's array as a whole)."""

    present = ~np.isnan(values)
    if present.all():
        return True
    if not present.any():
        return False
    return None


def _check_consistency(row: Mapping[str, Any], case: int, first: int = 0) -> None:
    """Record invariants of one span of shells of one case."""

    offsets = row["station_offsets"]
    for local in range(len(row["thickness"])):
        index = first + local
        lo, hi = int(offsets[local]), int(offsets[local + 1])
        code = row["element_refusal"][local]
        if bool(code) != bool(row["element_refusal_reason"][local]):
            raise ValueError(f"shell axes case {case}: shell {index} refusal code and reason disagree")
        polarity = int(row["director_polarity"][local])
        if polarity not in (-1, 0, 1):
            raise ValueError(f"shell axes case {case}: shell {index} director polarity {polarity}")
        director = _whole(row["physical_reference_director"][local])
        if director is None:
            raise ValueError(f"shell axes case {case}: shell {index} has a partial director")
        if code:
            # a refused shell: no stations and none of its admitted-shell data
            empty = (hi == lo and polarity == 0 and director is False
                     and np.isnan(row["thickness"][local])
                     and all(np.isnan(row[key][local]) for key in _OFFSET_KEYS)
                     and row["branch"][local] == "" and row["metadata_refusal"][local] == ""
                     and not any(bool(row[f"{fam}_certified"][local]) or row[f"{fam}_refusal_reason"][local]
                                 or row[f"{fam}_refusal_detail"][local] or row[f"{fam}_convention"][local]
                                 for fam in FAMILIES))
            if not empty:
                raise ValueError(f"shell axes case {case}: refused shell {index} carries admitted-shell data")
            continue
        if hi == lo or not row["branch"][local]:
            raise ValueError(f"shell axes case {case}: admitted shell {index} has no stations or branch")
        if not (np.isfinite(row["thickness"][local]) and row["thickness"][local] > 0.0):
            raise ValueError(f"shell axes case {case}: admitted shell {index} has no positive thickness")
        if _whole(row["station_positions"][lo:hi]) is not True:
            raise ValueError(f"shell axes case {case}: a station of shell {index} lacks a coordinate")
        for fam, key in (("membrane", "membrane_tensors"), ("bending", "bending_tensors"), ("shear", "shear_vectors")):
            certified = bool(row[f"{fam}_certified"][local])
            if _whole(row[key][lo:hi]) is not certified:
                raise ValueError(f"shell axes case {case}: {fam} of shell {index} disagrees with its certification")
            normals = _whole(row[f"{fam}_basis_normals"][lo:hi])
            if normals is None or (normals and not certified):
                raise ValueError(f"shell axes case {case}: {fam} basis normals of shell {index} are partial "
                                 "or belong to a refused family")
            if certified == bool(row[f"{fam}_refusal_reason"][local]):
                raise ValueError(f"shell axes case {case}: {fam} of shell {index} certification and reason disagree")
            if certified and not row[f"{fam}_convention"][local]:
                raise ValueError(f"shell axes case {case}: certified {fam} of shell {index} has no convention")
            if certified and row[f"{fam}_refusal_detail"][local]:
                raise ValueError(f"shell axes case {case}: certified {fam} of shell {index} has a refusal detail")
        components = _whole(row["shear_normal_components"][lo:hi])
        if components is None or (components and not row["shear_certified"][local]):
            raise ValueError(f"shell axes case {case}: shear normal components of shell {index} are partial "
                             "or belong to a refused shear")


def read_shell_axes_table(dataset: Any, key: str, case: Optional[int] = None) -> Any:
    """Read one persisted record entry back exactly (fails closed).

    Structural integrity and the record's own invariants only (see the
    module's reader contract): binding to the solved mesh and the solver's
    recovery rules is D2-2's, before any use.  The whole artifact is
    validated once first, in bounded shell spans (see ``_validated``).  ``case`` selects one case of a per-case entry
    (``shell_axes_meta`` ``entries[key].layout``); a shared entry is the same
    for every case.  Float entries come back with NaN where absent; string
    columns are lists.
    """

    layout, n_s = _validated(dataset)
    if key not in layout.entries:
        raise KeyError(f"shell axes tables: no entry {key!r}")
    per_case = layout.entries[key]["layout"] == "per_case"
    if per_case and case is None:
        raise ValueError(f"shell axes entry {key!r} is per case; pass case=0..{layout.n_cases - 1}")
    if case is not None and (type(case) is not int or not 0 <= case < layout.n_cases):
        raise IndexError(f"shell axes case {case!r} out of range 0..{layout.n_cases - 1}")
    slot = case if per_case else 0
    if layout.entries[key]["kind"] == "strings":
        return list(layout.read_strings(dataset, key, slot))
    return layout.read_rows(dataset, key, slot, 0, layout.rows(key, n_s[slot]), n_s[slot])


class TableView:
    """An in-memory stand-in for an opened result (tables, shapes, frames,
    identity), so the writer validates what it is about to publish with the
    reader's own checks."""

    def __init__(self, tables: Mapping[str, Any], frames: Sequence[float], frame_kind: str,
                 solution_type: str) -> None:
        self.tables = dict(tables)
        self.frames = np.asarray(frames, dtype=float)
        self.identity = {"frame_kind": frame_kind}
        self._provenance = {"solution_type": solution_type}

    def metadata(self, name: str) -> Any:
        return dict(self._provenance) if name == "provenance" else None

    def table(self, name: str, rows: Any = None) -> Any:
        value = self.tables[name]
        return value if rows is None or not isinstance(value, np.ndarray) else value[rows]

    def table_shape(self, name: str) -> tuple:
        value = self.tables[name]
        return tuple(value.shape) if isinstance(value, np.ndarray) else ()

    @property
    def table_keys(self) -> tuple:
        return tuple(self.tables)


def validate_shell_axes_tables(tables: Mapping[str, Any], frames: Sequence[float], frame_kind: str,
                               solution_type: str) -> None:
    """Raise ValueError unless ``tables`` read back as a valid axes record."""

    _validated(TableView(tables, frames, frame_kind, solution_type))


def check_record_columns(record: Mapping[str, Any]) -> None:
    """Every column of one in-memory record has its schema kind, exact dtype
    and trailing shape (nothing is coerced before it is written)."""

    columns = _record_columns()
    if set(record) != set(columns) | {"schema", "provenance"}:
        raise ValueError(f"shell axes record: columns differ from the schema: "
                         f"{sorted(set(record) ^ (set(columns) | {'schema', 'provenance'}))}")
    for key, (kind, dtype_str, _rows, trailing) in columns.items():
        value = record[key]
        if kind == "strings":
            if type(value) is not list or not all(type(item) is str for item in value):
                raise ValueError(f"shell axes record: {key!r} must be a list of str")
        elif type(value) is not np.ndarray or value.dtype != np.dtype(dtype_str) \
                or value.ndim != 1 + len(trailing) or tuple(value.shape[1:]) != tuple(trailing):
            raise ValueError(f"shell axes record: {key!r} must be {np.dtype(dtype_str)} (n, {trailing}), "
                             f"got {type(value).__name__} {getattr(value, 'dtype', None)} {getattr(value, 'shape', None)}")
