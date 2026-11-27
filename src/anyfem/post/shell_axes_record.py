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
    if not admitted:
        gate = _admission_gate()
        if gate is None:
            # Never resolve material or DOFs of an unadmitted element (fail closed).
            return ("refused", "AXES_ADMISSION_UNAVAILABLE",
                    "the solver module's admission gate is unavailable in this installation")
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


def _meta(dataset: Any) -> tuple:
    meta = dataset.table("shell_axes_meta")
    if not isinstance(meta, Mapping) or meta.get("schema") != RECORD_SCHEMA:
        raise ValueError("shell axes tables: unknown or missing schema")
    entries, cases = meta.get("entries"), meta.get("cases")
    if not isinstance(entries, Mapping) or not isinstance(cases, list) or not cases:
        raise ValueError("shell axes tables: malformed meta")
    if any(not isinstance(entry, Mapping) or type(entry.get("case_index")) is not int or entry["case_index"] != slot
           for slot, entry in enumerate(cases)):
        raise ValueError("shell axes tables: case metadata does not name its slot")
    columns = _record_columns()
    if set(entries) != set(columns):
        raise ValueError(f"shell axes tables: entries differ from the record schema: "
                         f"missing {sorted(set(columns) - set(entries))}, unknown {sorted(set(entries) - set(columns))}")
    for name, layout in entries.items():
        if not isinstance(layout, Mapping) or layout.get("layout") not in ("shared", "per_case") \
                or layout.get("kind") != columns[name][0]:
            raise ValueError(f"shell axes tables: malformed layout of {name!r}")
        if columns[name][0] == "numeric":
            if layout.get("dtype") != np.dtype(columns[name][1]).str:
                raise ValueError(f"shell axes tables: {name!r} declared dtype {layout.get('dtype')!r}")
            _int_list(layout.get("shape"), f"{name!r} shape")
    if entries["element_ids"]["layout"] != "shared":
        raise ValueError("shell axes tables: element_ids must be shared")
    return entries, len(cases)


def _read_entry(dataset: Any, entries: Mapping[str, Any], n_cases: int, n_e: int, n_s: Sequence[int],
                key: str, case: Optional[int]) -> Any:
    """One entry of one case (or the shared value), all structural checks."""

    kind, dtype_str, rows, trailing = _record_columns()[key]
    layout = entries[key]
    per_case = layout["layout"] == "per_case"
    slot = case if per_case else 0
    n_rows = n_e if rows == "e" else n_e + 1 if rows == "e+1" else n_s[case if case is not None else 0]
    if kind == "strings":
        name = f"shell_axes_{key}__{slot}" if per_case else f"shell_axes_{key}"
        stored = dataset.table(name) if name in tuple(dataset.table_keys) else None
        values = stored.get("values") if isinstance(stored, Mapping) and set(stored) == {"values"} else None
        if not isinstance(values, list) or len(values) != n_rows or not all(type(v) is str for v in values):
            raise ValueError(f"shell axes entry {key!r}: malformed string column")
        return list(values)
    dtype = np.dtype(dtype_str)
    if per_case:
        # stations per case may differ: every case's span must match its own count
        expected = [n_cases, n_rows, *trailing] if rows != "s" else None
    else:
        expected = [n_rows, *trailing]
    shape = layout["shape"]
    if rows == "s" and not per_case and len(set(n_s)) != 1:
        raise ValueError(f"shell axes entry {key!r}: shared but the cases have different station counts")
    if expected is not None and shape != expected:
        raise ValueError(f"shell axes entry {key!r}: declared shape {shape}, expected {expected}")
    if rows == "s" and per_case and (len(shape) != 2 + len(trailing) or shape[0] != n_cases
                                     or list(shape[2:]) != list(trailing) or len(set(n_s)) != 1 or shape[1] != n_rows):
        # a per-case station entry needs the same station count in every case
        raise ValueError(f"shell axes entry {key!r}: declared shape {shape} does not fit the station counts {list(n_s)}")
    inner = math.prod((int(n_rows), *(int(v) for v in trailing)))
    total = inner * (n_cases if per_case else 1)
    start = inner * slot
    name = "shell_axes_" + key
    _check_flat(dataset, name, total)
    value = _read_span(dataset, name, start, inner, dtype).reshape((n_rows, *trailing))
    if dtype.kind == "f":
        _check_flat(dataset, name + "_present", total)
        present = _read_span(dataset, name + "_present", start, inner, np.dtype(np.bool_)).reshape(value.shape)
        if not np.isfinite(value[present]).all():
            raise ValueError(f"shell axes entry {key!r}: a present value is not finite")
        if np.any(value[~present] != 0.0):
            raise ValueError(f"shell axes entry {key!r}: an absent value is not stored as 0.0")
        value = np.where(present, value, np.nan)
    elif (name + "_present") in tuple(dataset.table_keys):
        raise ValueError(f"shell axes entry {key!r}: unexpected presence mask on a non-float entry")
    return value


def _validated(dataset: Any) -> tuple:
    """Validate the whole artifact once (all entries, all cases, the family /
    certification consistency); cached on the dataset object when possible."""

    cached = getattr(dataset, "_shell_axes_validated", None)
    if cached is not None:
        return cached
    entries, n_cases = _meta(dataset)
    n_e = entries["element_ids"]["shape"][0] if len(entries["element_ids"]["shape"]) == 1 else -1
    if n_e < 0:
        raise ValueError("shell axes tables: malformed element_ids shape")
    ids = _read_entry(dataset, entries, n_cases, n_e, [0], "element_ids", None)
    if len({int(v) for v in ids}) != ids.size:
        raise ValueError("shell axes entry 'element_ids': duplicate element ids")
    n_s = []
    for case in range(n_cases):
        offsets = _read_entry(dataset, entries, n_cases, n_e, [0], "station_offsets",
                              case if entries["station_offsets"]["layout"] == "per_case" else None)
        # zero-based and nondecreasing, compared as Python ints (no wrapping)
        offsets_list = [int(v) for v in offsets]
        if not offsets_list or offsets_list[0] != 0 or any(a > b for a, b in zip(offsets_list, offsets_list[1:])):
            raise ValueError("shell axes entry 'station_offsets': not zero-based and nondecreasing")
        n_s.append(offsets_list[-1])
    for case in range(n_cases):
        row = {key: _read_entry(dataset, entries, n_cases, n_e, n_s, key,
                                case if entries[key]["layout"] == "per_case" else None)
               for key in _record_columns()}
        _check_consistency(row, case)
    result = (entries, n_cases, n_e, tuple(n_s))
    try:
        dataset._shell_axes_validated = result
    except (AttributeError, TypeError):
        pass
    return result


def _check_consistency(row: Mapping[str, Any], case: int) -> None:
    """Record invariants of one case: a refused shell has no stations and no
    certified family; a family's tensor rows are present exactly when it is
    certified; a basis normal or shear-normal component only for a certified
    family; a refusal reason exactly when not certified (admitted shells)."""

    offsets = row["station_offsets"]
    for index in range(len(row["element_ids"])):
        lo, hi = int(offsets[index]), int(offsets[index + 1])
        refused = row["element_refusal"][index] != ""
        if refused and hi != lo:
            raise ValueError(f"shell axes case {case}: refused shell {index} has stations")
        if hi == lo and not refused:
            raise ValueError(f"shell axes case {case}: admitted shell {index} has no stations")
        for fam, key in (("membrane", "membrane_tensors"), ("bending", "bending_tensors"), ("shear", "shear_vectors")):
            certified = bool(row[f"{fam}_certified"][index])
            if refused and certified:
                raise ValueError(f"shell axes case {case}: refused shell {index} has a certified {fam}")
            tensors = row[key][lo:hi]
            present = ~np.isnan(tensors.reshape(hi - lo, -1))
            if certified != bool(present.all()) or (not certified and present.any()):
                raise ValueError(f"shell axes case {case}: {fam} of shell {index} disagrees with its certification")
            if not certified and np.any(~np.isnan(row[f"{fam}_basis_normals"][lo:hi])):
                raise ValueError(f"shell axes case {case}: refused {fam} of shell {index} has a basis normal")
            if not refused and certified == bool(row[f"{fam}_refusal_reason"][index]):
                raise ValueError(f"shell axes case {case}: {fam} of shell {index} certification and reason disagree")
        if not row["shear_certified"][index] and np.any(~np.isnan(row["shear_normal_components"][lo:hi])):
            raise ValueError(f"shell axes case {case}: refused shear of shell {index} has normal components")


def read_shell_axes_table(dataset: Any, key: str, case: Optional[int] = None) -> Any:
    """Read one persisted record entry back exactly (fails closed).

    The whole artifact is validated once first (every declaration, table,
    mask and per-case inventory; integer dimensions; offsets; unique element
    ids; family certification against tensors, normals and reasons).  ``case``
    selects one case of a per-case entry (``shell_axes_meta``
    ``entries[key].layout``); a shared entry is the same for every case.
    Float entries come back with NaN where absent; string columns are lists.
    """

    entries, n_cases, n_e, n_s = _validated(dataset)
    if key not in entries:
        raise KeyError(f"shell axes tables: no entry {key!r}")
    per_case = entries[key]["layout"] == "per_case"
    if per_case and case is None:
        raise ValueError(f"shell axes entry {key!r} is per case; pass case=0..{n_cases - 1}")
    if case is not None and (type(case) is not int or not 0 <= case < n_cases):
        raise IndexError(f"shell axes case {case!r} out of range 0..{n_cases - 1}")
    return _read_entry(dataset, entries, n_cases, n_e, n_s, key, case if per_case else None)
