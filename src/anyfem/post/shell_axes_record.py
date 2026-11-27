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

from typing import Any, Callable, Dict, Mapping, Optional, Sequence

import numpy as np

__all__ = [
    "FAMILIES",
    "RECORD_SCHEMA",
    "STATIC_KEYS",
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


def recover_shell_axes_record(
    fe_model: Any,
    displacements: Any,
    *,
    provenance: Optional[Mapping[str, Any]] = None,
    cancelled: Optional[Callable[[], None]] = None,
) -> Dict[str, Any]:
    """Recover every shell of ``fe_model`` through ``recover_shell_axes``.

    ``displacements`` is the solved global vector of one case.  ``cancelled``
    (optional) is called between elements and may raise to stop the loop.
    Returns the plain-data record (numpy arrays, lists of str, a provenance
    dict); see the module docstring.
    """

    from anyfesolver.shell_axes_outputs import ShellAxesRefusal, recover_shell_axes

    u = np.asarray(displacements, dtype=float)
    ids = shell_element_ids(fe_model)
    mesh = fe_model.mesh
    n_e = len(ids)
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
    per_element: list[Optional[Any]] = [None] * n_e
    counts = np.zeros(n_e, dtype=np.int64)

    for index, eid in enumerate(ids):
        if cancelled is not None:
            cancelled()
        element = mesh.elements[eid]
        thickness[index] = _thickness(element)
        material = fe_model.get_material(element.material_name)
        mapping = np.asarray(element.get_dof_mapping(mesh), dtype=np.intp)
        if mapping.size == 0 or int(mapping.min()) < 0 or int(mapping.max()) >= u.size:
            element_refusal[index] = "AXES_MISSING_GLOBAL_TENSORS"
            element_reason[index] = "element has no complete in-range DOF mapping"
            continue
        try:
            result = recover_shell_axes(element, mesh, u[mapping], material)
        except ShellAxesRefusal as refusal:
            element_refusal[index] = refusal.code
            element_reason[index] = refusal.reason
            continue
        axes = result.axes
        arrays = axes.arrays
        positions = _exact(arrays["recovery_station_positions"],
                           np.shape(arrays["recovery_station_positions"]), "station positions")
        if positions.ndim != 2 or positions.shape[1] != 3 or positions.shape[0] < 1:
            raise ValueError(f"shell axes record: element {eid} station positions shape {positions.shape}")
        counts[index] = positions.shape[0]
        per_element[index] = axes
        branch[index] = str(axes.branch)
        for fam in FAMILIES:
            conventions[fam][index] = str(axes.conventions.get(fam, ""))
            certified[fam][index] = bool(axes.certified.get(fam, False)) and fam not in axes.refusals
            if fam in axes.refusals:
                fam_reason[fam][index] = str(axes.refusals[fam].reason)
                fam_detail[fam][index] = str(axes.refusals[fam].detail)
        if "director_polarity" in arrays:
            value = int(np.asarray(arrays["director_polarity"]))
            if value not in (-1, 1):
                raise ValueError(f"shell axes record: element {eid} director polarity {value}")
            polarity[index] = value
        if "physical_reference_director" in arrays:
            director[index] = _exact(arrays["physical_reference_director"], (3,), "physical reference director")
        for key in _OFFSET_KEYS:
            if key in arrays:
                offsets[key][index] = float(_exact(arrays[key], (), key))

    station_offsets = np.zeros(n_e + 1, dtype=np.int64)
    np.cumsum(counts, out=station_offsets[1:])
    n_s = int(station_offsets[-1])
    positions = _rows(n_s, (3,))
    tensors = {fam: _rows(n_s, _TENSOR_KEYS[fam][1]) for fam in FAMILIES}
    normals = {fam: _rows(n_s, (3,)) for fam in FAMILIES}
    shear_normal = np.full(n_s, np.nan)
    for index, axes in enumerate(per_element):
        if axes is None:
            continue
        lo, hi = int(station_offsets[index]), int(station_offsets[index + 1])
        n = hi - lo
        arrays = axes.arrays
        positions[lo:hi] = _exact(arrays["recovery_station_positions"], (n, 3), "station positions")
        for fam in FAMILIES:
            key, shape = _TENSOR_KEYS[fam]
            if certified[fam][index]:
                if key not in arrays:
                    raise ValueError(f"shell axes record: element {ids[index]} certified {fam} has no {key}")
                tensors[fam][lo:hi] = _exact(arrays[key], (n, *shape), key)
            normal_key = _NORMAL_KEYS[fam]
            if normal_key in arrays:
                normals[fam][lo:hi] = _exact(arrays[normal_key], (n, 3), normal_key)
        if "shear_normal_components" in arrays:
            shear_normal[lo:hi] = _exact(arrays["shear_normal_components"], (n,), "shear_normal_components")

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
