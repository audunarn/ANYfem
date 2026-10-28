"""Floating TRANSIENT workflow: mean pose -> coupled rigid 6-DOF transient -> physical nodal loads -> residual relief.

Connected headless method (no BEM, no hydrostatic solve, no mesh changes):

1. Dry mass: the assembled structural M of the selected FE model (``floating_mass.dry_mass_properties``); M_dry = R^T M R
   about the dry CG in FE (mean-body) axes.  The input carries the mass identity; a different model is refused (stale).
2. Generalised operators that ALL have matching structural force distributions (virtual-work consistent by
   construction, R^T of the nodal loads):  weight perturbation  M dg(theta), dg = -theta x g0_body  (K_g);  hydrostatic
   panel-pressure operator Hs mapped to nodes (K_hs);  wave panel pressure history (F_wave);  supplied fairlead mooring
   (K_m, C_m, recovered fairlead forces);  external load histories (F_ext, world-load tangent G(t));  turbine thrust at the
   hub node.  Radiation: the generalised A_inf / K(t) of the hydro bundle drive the rigid ODE; the matching
   RadiationFieldKernel (digest-bound) gives the panel radiation pressure.  No duplicate gravity, inertia or added mass.
3. ``anysolver.floating_transient.solve_floating_transient`` integrates the FULL accepted history (actual times).
4. Guards on EVERY integration sample: rotation <= 5 deg (solver), mooring validity envelope, relative-waterline
   clearance inflated by the native pointwise bound envelope (``guarded_clearance``) when waterline data are supplied.
5. Structural output window (subset of the accepted samples; never restarts physics): per frame the nodal loads
   pressure(radiation+wave+hydrostatic) + weight perturbation + mooring + turbine + external - M a_primary; the owner
   dynamic closure ``abs(r_i) <= 0.01 max(d_i, 0.1 S)`` must pass BEFORE relief; then ``iter_residual_relief``.

The frames are PERTURBATION loads about the mean operating equilibrium (static mean loads are a separate static case).
Unverified (development) hydrodynamics run end-to-end only under the solver's explicit ``development_unverified``
admission and yield ``completed_unverified_hydrodynamics`` with the unpassed gates listed - never an accepted result.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from .floating_transient_record import (
    HYDRO_ACCEPTED,
    HYDRO_UNVERIFIED,
    INPUT_FORMAT,
    STATUS_COMPLETE,
    STATUS_UNVERIFIED,
    FloatingTransientRecordError,
    FloatingTransientResult,
    _canonical,
    _declare,
    _freeze,
    _ro,
    load_npz,
    save_npz,
)

ADMISSIONS_FOR = {HYDRO_ACCEPTED: ("strict", "bounded_native"), HYDRO_UNVERIFIED: ("development_unverified",)}
MAX_WORK_BYTES = 256 * 1024 * 1024
MAX_KERNEL_SAMPLES = 1001
STATUS_SYNTHETIC_UNGUARDED = "synthetic_unguarded_nonaccepted"


class FloatingTransientWorkflowError(ValueError):
    """The transient workflow input is invalid, stale or inconsistent (refused before any integration)."""


def mass_identity(dry: Any) -> str:
    """Identity of the selected assembled dry mass: node ids, node points, total mass, CG, inertia AND the actual
    assembled sparse mass matrix (canonical CSR: shape, sorted indices, summed duplicates, explicit zeros removed) plus
    the node->DOF map when present.  Two models with equal rigid properties but different elastic mass differ."""
    from scipy import sparse
    h = hashlib.sha256(b"anyfem-floating-dry-mass-v2")
    h.update(_canonical([int(i) for i in dry.node_ids]).encode())
    for arr in (np.asarray(dry.points, float), np.array([float(dry.mass)]), np.asarray(dry.cg, float),
                np.asarray(dry.inertia_cg, float)):
        h.update(np.ascontiguousarray(arr, dtype="<f8").tobytes())
    M = getattr(dry, "mass_matrix", None)
    if M is None:
        raise FloatingTransientWorkflowError("the dry mass record lacks its assembled mass matrix")
    C = sparse.csr_matrix(M, dtype=np.float64, copy=True)
    C.sum_duplicates(); C.eliminate_zeros(); C.sort_indices()
    h.update(_canonical(["M", list(C.shape), int(C.nnz)]).encode())
    for arr, dt in ((C.indptr, "<i8"), (C.indices, "<i8"), (C.data, "<f8")):
        h.update(np.ascontiguousarray(arr, dtype=dt).tobytes())
    dm = getattr(dry, "dofmap", None)
    if dm is not None:
        h.update(_canonical(["dofmap", int(dm.ndof)]).encode())
        h.update(np.ascontiguousarray(dm.index, dtype="<i8").tobytes())
    return h.hexdigest()


CANONICAL_MEAN_KIND = "canonical_full_mean_wrench"
CANONICAL_MEAN_STATUSES = ("converged_canonical_development",)
BIND_REL = 1e-12


def _same(a, b, scale=1.0) -> bool:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    # rotated matrices carry ~1e-16 relative roundoff: compare relative to their magnitude (1e-12, unchanged)
    return a.shape == b.shape and bool(np.all(np.abs(a - b) <= BIND_REL * max(1.0, float(scale),
                                                                              float(np.max(np.abs(b), initial=0.0)))))


def external_channels_record(components, frames, points_body_cg, node_ids, mean_values) -> dict:
    """Neutral external-channel description bound verbatim between the operating mean and the transient input
    (points are CG-relative body points, the transient convention)."""
    return dict(components=[str(c) for c in components], frames=[str(f) for f in frames],
                points_body=np.asarray(points_body_cg, dtype=float).tolist(), node_ids=[int(i) for i in node_ids],
                mean_values=np.asarray(mean_values, dtype=float).tolist())


def _check_mean_binding(inp, me: dict, err) -> None:
    """The canonical operating mean must be the one these loads were built from (no stale / re-derived values)."""
    if me.get("kind") != CANONICAL_MEAN_KIND or me.get("status") not in CANONICAL_MEAN_STATUSES:
        raise err(f"the operating-mean record must be a converged canonical full-mean-wrench solve "
                  f"(kind {me.get('kind')!r}, status {me.get('status')!r})")
    b = me.get("binding") or {}
    cg = np.asarray(inp.cg_body, float)
    if b.get("cg_body") is None or not _same(cg, b["cg_body"]):
        raise err("operating mean was solved with a different dry CG (stale)")
    if not _same(inp.Q0, me["rotation_body_to_world"]):
        raise err("Q0 differs from the solved operating-mean rotation (stale pose)")
    if inp.wind_velocity is not None:
        if b.get("rotor_axis_body") is None or not _same(inp.rotor_axis_body, b["rotor_axis_body"]):
            raise err("rotor axis differs from the operating-mean rotor axis")
        # the mean solve uses the ABSOLUTE hub body point; the transient uses hub - CG (converted exactly once)
        if b.get("hub_body") is None or not _same(inp.hub_offset_body, np.asarray(b["hub_body"], float) - cg):
            raise err("hub_offset_body must equal the operating-mean hub body point minus the dry CG")
        curve = b.get("thrust_curve") or {}
        if not (_same(inp.thrust_speeds, curve.get("speeds", [])) and _same(inp.thrust_values, curve.get("thrusts", []))):
            raise err("thrust curve differs from the operating-mean thrust curve")
        if b.get("wind_reference_mean_world") is None or \
                not _same(inp.wind_reference_mean_world, b["wind_reference_mean_world"]):
            raise err("the declared reference mean wind differs from the operating-mean wind")
    bound = {str(f["identity"]): f for f in (b.get("fairleads") or [])}
    forces = b.get("mooring_mean_forces_world") or {}
    if {str(f["identity"]) for f in inp.fairleads} != set(bound):
        raise err("fairlead set differs from the operating-mean mooring laws")
    for f in inp.fairleads:
        law = bound[str(f["identity"])]
        if not _same(f["point_body"], np.asarray(law["point_body"], float) - cg):
            raise err(f"fairlead {f['identity']} point differs from the operating-mean attachment (CG-relative)")
        # frames: the mean law is WORLD (stiffness_world / damping_world); the transient FairleadSpec is MEAN-BODY:
        # K_b = Q0^T K_w Q0, C_b = Q0^T C_w Q0 at the converged operating rotation Q0
        Q = np.asarray(inp.Q0, float)
        Kb = Q.T @ np.asarray(law["stiffness_world"], float) @ Q
        Cb = Q.T @ np.asarray(law.get("damping_world") or np.zeros((3, 3)), float) @ Q
        if not _same(np.asarray(f["stiffness"], float), Kb, scale=float(np.max(np.abs(Kb), initial=0.0))):
            raise err(f"fairlead {f['identity']} body stiffness must be Q0^T K_world Q0 of the operating-mean law")
        C_in = np.zeros((3, 3)) if f.get("damping") is None else np.asarray(f["damping"], float)
        if not _same(C_in, Cb, scale=float(np.max(np.abs(Cb), initial=0.0))):
            raise err(f"fairlead {f['identity']} body damping must be Q0^T C_world Q0 of the bound mean law")
        # relinearised about the operating pose: the perturbation pretension is the EVALUATED mean force
        if str(f["identity"]) not in forces or not _same(f["pretension_world"], forces[str(f["identity"])]):
            raise err(f"fairlead {f['identity']} pretension must be the force evaluated at the operating pose")
    if inp.ext_mean_values is not None:
        rec = external_channels_record(inp.ext_components, inp.ext_frames, inp.ext_points_body, inp.ext_node_ids,
                                       inp.ext_mean_values)
        stored = b.get("external_channels")
        if stored is None or json.loads(_canonical(stored)) != json.loads(_canonical(rec)):
            raise err("external mean channels differ from the operating-mean external channels")


ELEMENT_RECOVERY_ATTRIBUTES = ("cross_section", "generalized_section", "_linear_nodal_offsets", "properties", "section",
                               "shell_section", "thickness", "material_orientation", "orientation", "formulation",
                               "formulation_id", "integration", "reduced_integration")


def _content(value: Any, depth: int = 0) -> Any:
    """Canonical CONTENT of model records (dataclasses / plain objects / arrays), depth-bounded; never ids/addresses."""
    import dataclasses as _dc
    if depth > 6:
        return "<depth>"
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return float(value).hex()
    if isinstance(value, (np.generic,)):
        return _content(value.item(), depth)
    if isinstance(value, np.ndarray):
        return [str(value.dtype), list(value.shape), hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()]
    if isinstance(value, Mapping):
        return {str(k): _content(v, depth + 1) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_content(v, depth + 1) for v in value]
        return sorted(items, key=json.dumps) if isinstance(value, (set, frozenset)) else items
    if _dc.is_dataclass(value):
        return [type(value).__name__, {f.name: _content(getattr(value, f.name), depth + 1) for f in _dc.fields(value)
                                       if not f.name.startswith("_")}]
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict):
        return [type(value).__name__, {k: _content(v, depth + 1) for k, v in sorted(attrs.items())
                                       if not k.startswith("_") and not callable(v)}]
    import re
    return [type(value).__name__, re.sub(r" at 0x[0-9a-fA-F]+", "", repr(value))]   # no memory addresses (stable)


def structural_content(fe_model: Any) -> dict:
    """Recovery-relevant structural CONTENT beyond K: supports, affine constraint equations / MPCs (terms, rhs, pivot),
    materials and element type / connectivity / section properties.  Same K + same M with changed MPCs or section
    recovery data therefore differ."""
    mesh = fe_model.mesh
    elements = []
    for eid in sorted(mesh.elements):
        el = mesh.elements[eid]
        # owner recovery data actually held by the element (anysolver.elements: BeamElement.cross_section incl. c_y /
        # c_z / torsion_modulus / orientation, generalized_section, linear nodal offsets; shell thickness / section /
        # orientation / formulation where present).  Absent attributes are skipped, never guessed.
        recovery = {name: _content(getattr(el, name)) for name in ELEMENT_RECOVERY_ATTRIBUTES if hasattr(el, name)}
        elements.append([int(eid), type(el).__name__, [int(n) for n in getattr(el, "nodes", getattr(el, "node_ids", ()))],
                         _content(getattr(el, "material_name", getattr(el, "material", None))), recovery])
    return dict(boundary=_content(list(getattr(fe_model, "boundary_conditions", ()))),
                constraints=_content(list(getattr(fe_model, "constraint_equations", ()))),
                materials=_content(dict(getattr(fe_model, "materials", {}) or {})), elements=elements)


def structural_identity(fe_model: Any) -> str:
    """Identity of the STRUCTURAL model the relieved displacements / stresses belong to: canonical assembled stiffness
    (CSR shape, pattern, values) + assembled mass identity + structural content (supports, constraint equations / MPCs,
    materials, element sections).  Refuses (never guesses) when the owner assembly is unavailable."""
    from scipy import sparse
    try:
        from anysolver.matrix_assembly import assemble_stiffness_matrix
    except ImportError as exc:                              # no owner assembly: cannot bind -> refuse
        raise FloatingTransientWorkflowError(f"structural identity unavailable (owner stiffness assembly): {exc}") from exc
    out = assemble_stiffness_matrix(fe_model)
    K = out[0] if isinstance(out, tuple) else out
    C = sparse.csr_matrix(K, dtype=np.float64, copy=True)
    C.sum_duplicates(); C.eliminate_zeros(); C.sort_indices()
    h = hashlib.sha256(b"anyfem-floating-structural-v2")
    h.update(_canonical([list(C.shape), int(C.nnz)]).encode())
    for arr, dt in ((C.indptr, "<i8"), (C.indices, "<i8"), (C.data, "<f8")):
        h.update(np.ascontiguousarray(arr, dtype=dt).tobytes())
    from .floating_mass import dry_mass_properties
    h.update(mass_identity(dry_mass_properties(fe_model)).encode())
    h.update(json.dumps(structural_content(fe_model), sort_keys=True, default=str).encode())
    return h.hexdigest()


# ----------------------------------------------------------------------------- input record

_OPTIONAL_ARRAYS = ("kernel_horizon_arr", "admission_omegas", "wave_pressure", "waterline_points_body", "waterline_limit",
                    "incident_elevation", "waterline_bound", "wind_times", "wind_velocity", "thrust_speeds",
                    "thrust_values", "hub_offset_body", "rotor_axis_body", "mooring_validity", "ext_times", "ext_values",
                    "ext_points_body", "wind_reference_mean_world", "hydrostatic_pressure")


@dataclass(frozen=True)
class FloatingTransientInput:
    """Immutable neutral job input (explicit arrays + JSON; no callbacks).  Points are relative to the dry CG in the FE
    (mean-body) axes where stated ``_body``; ``Q0`` maps those axes to world."""

    times: Any
    frame_indices: Any
    Q0: Any
    cg_body: Any
    gravity: float
    length_scale: float
    node_ids: Any
    mass_digest: str
    added_mass_infinite: Any
    radiation_kernel: Any
    hydrodynamic_verification: str
    hydro_admission: str
    panel_to_nodal: Any                    # (n_nodes, 6, n_panels): nodal force/moment per unit panel pressure
    hydrostatic_pressure: Any              # (n_panels, 6): first-order hydrostatic panel pressure per unit q
    field_kernel_hash: str                 # content hash of the digest-bound RadiationFieldKernel
    kernel_horizon: Optional[float] = None
    admission_omegas: Any = None
    native_source_binding: Optional[Mapping[str, Any]] = None
    unpassed_hydro_gates: tuple = ()
    wave_pressure: Any = None              # (n_t, n_panels) incident + diffraction panel pressure history
    waterline_points_body: Any = None      # (n_q, 3) relative to CG; order = field kernel query points
    waterline_limit: Any = None            # (n_q,) allowed |relative elevation|
    incident_elevation: Any = None         # (n_t, n_q) incident + diffraction elevation
    waterline_bound: Any = None            # (n_q, 6) native pointwise bound (exact-z0 policy) or None
    wind_times: Any = None
    wind_velocity: Any = None
    thrust_speeds: Any = None
    thrust_values: Any = None
    hub_offset_body: Any = None
    rotor_axis_body: Any = None
    hub_node_id: Optional[int] = None
    mean_thrust: Optional[float] = None
    fairleads: tuple = ()                  # dicts: identity, point_body, pretension_world, stiffness, damping, node_id
    mooring_validity: Any = None
    ext_times: Any = None
    ext_values: Any = None
    ext_components: tuple = ()
    ext_frames: tuple = ()
    ext_points_body: Any = None
    ext_node_ids: tuple = ()
    identities: Mapping[str, Any] = field(default_factory=dict)
    # ---- static MEAN (operating equilibrium) - mandatory: never implicitly zero
    mean_panel_pressure: Any = None        # (n_panels,) mean hydrostatic panel pressure at the mean pose
    ext_mean_values: Any = None            # (n_ext_channels,) mean values of the external channels
    mean_equilibrium: Optional[Mapping[str, Any]] = None   # operating-mean record (status, mean_thrust, identity, ...)
    # ---- exact hydrostatics (wet-region quadrature of the linear field and of its rigid gradient)
    mean_hydrostatic_nodal: Any = None     # (n_nodes, 6) mean hydrostatic nodal load at the mean pose
    hydrostatic_nodal: Any = None          # (6, n_nodes, 6) first-order hydrostatic nodal load per unit q_j
    # ---- guards / references
    waterline_point_ids: tuple = ()        # must equal the field kernel query-point ids (order included)
    waterline_guard_waiver: str = ""       # explicit synthetic-fixture waiver (result never accepted)
    wind_reference_mean_world: Any = None  # (3,) declared physical reference mean wind (bound to the operating mean)
    wave_provenance: Optional[Mapping[str, Any]] = None   # immutable wave history provenance (wave_history_provenance)

    def __post_init__(self) -> None:
        err = FloatingTransientWorkflowError
        t = _ro(self.times, np.float64)
        if t.ndim != 1 or t.size < 2 or np.any(np.diff(t) <= 0.0):
            raise err("times must be strictly increasing (>= 2 samples)")
        object.__setattr__(self, "times", t)
        fi = np.asarray(self.frame_indices)
        if fi.dtype.kind not in "iu" or fi.ndim != 1 or fi.size < 1 or fi.min() < 0 or fi.max() >= t.size \
                or np.any(np.diff(fi) <= 0):
            raise err("frame_indices: strictly increasing integer indices into the accepted time grid are required")
        object.__setattr__(self, "frame_indices", _ro(fi, np.int64))
        for name, shape in (("Q0", (3, 3)), ("cg_body", (3,)), ("added_mass_infinite", (6, 6))):
            a = _ro(getattr(self, name), np.float64)
            if a.shape != shape:
                raise err(f"{name} must have shape {shape}")
            object.__setattr__(self, name, a)
        Q = self.Q0
        if np.max(np.abs(Q.T @ Q - np.eye(3))) > 1e-10 or abs(np.linalg.det(Q) - 1.0) > 1e-10:
            raise err("Q0 must be a proper rotation")
        for name in ("gravity", "length_scale"):
            v = float(getattr(self, name))
            if not (np.isfinite(v) and v > 0.0):
                raise err(f"{name} must be positive and finite")
            object.__setattr__(self, name, v)
        ker = _ro(self.radiation_kernel, np.float64)
        if ker.ndim != 3 or ker.shape[1:] != (6, 6) or not 2 <= ker.shape[0] <= MAX_KERNEL_SAMPLES:
            raise err(f"radiation_kernel must be (2..{MAX_KERNEL_SAMPLES}, 6, 6)")
        object.__setattr__(self, "radiation_kernel", ker)
        ids = tuple(int(i) for i in self.node_ids)
        if not ids or len(set(ids)) != len(ids):
            raise err("node_ids must be unique and non-empty")
        object.__setattr__(self, "node_ids", ids)
        T = _ro(self.panel_to_nodal, np.float64)
        if T.ndim != 3 or T.shape[:2] != (len(ids), 6) or T.shape[2] < 1:
            raise err("panel_to_nodal must be (n_nodes, 6, n_panels)")
        if T.nbytes > MAX_WORK_BYTES:
            raise err("panel_to_nodal exceeds the 256 MiB working ceiling")
        object.__setattr__(self, "panel_to_nodal", T)
        if self.hydrostatic_pressure is not None:          # panel-centre diagnostic only (never loads a node)
            Hs = _ro(self.hydrostatic_pressure, np.float64)
            if Hs.shape != (T.shape[2], 6):
                raise err("hydrostatic_pressure must be (n_panels, 6)")
            object.__setattr__(self, "hydrostatic_pressure", Hs)
        # exact hydrostatics: conservative overlap/wet-region quadrature of the LINEAR pressure field and of its
        # first-order rigid-displacement gradient (prepared by floating_transient_prepare); mandatory
        if self.mean_hydrostatic_nodal is None or self.hydrostatic_nodal is None:
            raise err("mean_hydrostatic_nodal and hydrostatic_nodal (exact wet-region quadrature) are mandatory")
        mh = _ro(self.mean_hydrostatic_nodal, np.float64)
        hn = _ro(self.hydrostatic_nodal, np.float64)
        if mh.shape != (len(ids), 6) or hn.shape != (6, len(ids), 6):
            raise err("mean_hydrostatic_nodal must be (n_nodes, 6) and hydrostatic_nodal (6, n_nodes, 6)")
        object.__setattr__(self, "mean_hydrostatic_nodal", mh)
        object.__setattr__(self, "hydrostatic_nodal", hn)
        if self.fairleads and self.mooring_validity is None:
            raise err("mooring runs need the explicit mooring validity envelope (mooring_validity, 6 values)")
        if self.wind_velocity is not None and self.wind_reference_mean_world is None:
            raise err("a turbine run needs the declared physical reference mean wind (wind_reference_mean_world)")
        if self.waterline_points_body is None and not self.waterline_guard_waiver:
            raise err("the waterline guard is mandatory for a physical floating run (waterline_points_body + "
                      "waterline_limit); only an explicit synthetic-fixture waiver may omit it (never accepted)")
        if self.wave_pressure is not None and self.waterline_points_body is not None and self.incident_elevation is None:
            raise err("wave pressure requires the matching incident elevation at the waterline guard points")
        if self.hydrodynamic_verification not in ADMISSIONS_FOR:
            raise err("hydrodynamic_verification must be 'accepted' or 'unverified'")
        if self.hydro_admission not in ADMISSIONS_FOR[self.hydrodynamic_verification]:
            raise err(f"hydro_admission {self.hydro_admission!r} is not allowed for {self.hydrodynamic_verification} "
                      f"hydrodynamics (allowed: {ADMISSIONS_FOR[self.hydrodynamic_verification]})")
        if self.hydrodynamic_verification == HYDRO_UNVERIFIED and not self.unpassed_hydro_gates:
            raise err("unverified hydrodynamics must list their unpassed gates explicitly")
        object.__setattr__(self, "unpassed_hydro_gates", tuple(str(g) for g in self.unpassed_hydro_gates))
        if not (isinstance(self.field_kernel_hash, str) and len(self.field_kernel_hash) == 64):
            raise err("field_kernel_hash must be a sha256 hex digest")
        n_t, n_p = t.size, T.shape[2]
        for name, shape in (("wave_pressure", (n_t, n_p)), ("admission_omegas", None), ("waterline_points_body", None),
                            ("waterline_limit", None), ("incident_elevation", None), ("waterline_bound", None),
                            ("wind_times", None), ("wind_velocity", None), ("thrust_speeds", None),
                            ("thrust_values", None), ("hub_offset_body", (3,)), ("rotor_axis_body", (3,)),
                            ("mooring_validity", (6,)), ("ext_times", None), ("ext_values", None),
                            ("ext_points_body", None), ("wind_reference_mean_world", (3,))):
            value = getattr(self, name)
            if value is None:
                continue
            a = _ro(value, np.float64)
            if shape is not None and a.shape != shape:
                raise err(f"{name} must have shape {shape}")
            object.__setattr__(self, name, a)
        wl = (self.waterline_points_body, self.waterline_limit)
        if any(x is not None for x in wl) and not all(x is not None for x in wl):
            raise err("waterline guard needs waterline_points_body and waterline_limit together")
        if self.waterline_points_body is not None:
            n_q = self.waterline_points_body.shape[0]
            if self.waterline_points_body.shape != (n_q, 3) or self.waterline_limit.shape != (n_q,) \
                    or np.any(self.waterline_limit <= 0.0):
                raise err("waterline points (n_q, 3) and positive limits (n_q,) required")
            if self.incident_elevation is not None and self.incident_elevation.shape != (n_t, n_q):
                raise err("incident_elevation must be (n_t, n_q)")
            if self.waterline_bound is not None and self.waterline_bound.shape != (n_q, 6):
                raise err("waterline_bound must be (n_q, 6)")
        if self.mean_panel_pressure is not None:           # panel-centre diagnostic; the mean LOAD is the nodal field
            p0 = _ro(self.mean_panel_pressure, np.float64)
            if p0.shape != (n_p,):
                raise err("mean_panel_pressure must be (n_panels,)")
            object.__setattr__(self, "mean_panel_pressure", p0)
        if self.waterline_point_ids:
            object.__setattr__(self, "waterline_point_ids", tuple(str(i) for i in self.waterline_point_ids))
            if self.waterline_points_body is None or len(self.waterline_point_ids) != self.waterline_points_body.shape[0]:
                raise err("waterline_point_ids must name every waterline guard point in order")
        if self.ext_mean_values is not None:
            ev = _ro(self.ext_mean_values, np.float64)
            if ev.shape != (len(tuple(self.ext_components)),):
                raise err("ext_mean_values must have one value per external channel")
            object.__setattr__(self, "ext_mean_values", ev)
        if self.wind_velocity is not None or self.fairleads or self.ext_mean_values is not None:
            me = None if self.mean_equilibrium is None else dict(self.mean_equilibrium)
            if not me or not me.get("identity") or not me.get("status"):
                raise err("turbine / mooring / external-mean runs need the bound operating-mean record")
            if self.wind_velocity is not None:
                if self.mean_thrust is None or float(me.get("mean_thrust", float("nan"))) != float(self.mean_thrust):
                    raise err("mean_thrust must be exactly the bound operating-mean thrust")
            _check_mean_binding(self, me, err)
        if self.mean_equilibrium is not None:
            object.__setattr__(self, "mean_equilibrium", _freeze(json.loads(_canonical(dict(self.mean_equilibrium)))))
        object.__setattr__(self, "fairleads", tuple(_freeze(json.loads(_canonical(dict(f)))) for f in self.fairleads))
        object.__setattr__(self, "ext_components", tuple(str(c) for c in self.ext_components))
        object.__setattr__(self, "ext_frames", tuple(str(c) for c in self.ext_frames))
        object.__setattr__(self, "ext_node_ids", tuple(int(i) for i in self.ext_node_ids))
        object.__setattr__(self, "native_source_binding",
                           None if self.native_source_binding is None else _freeze(dict(self.native_source_binding)))
        object.__setattr__(self, "identities", _freeze(json.loads(_canonical(dict(self.identities)))))
        check_wave_provenance(self)                       # array / time-stamp digests at construction (and in run)
        if self.wave_provenance is not None:
            object.__setattr__(self, "wave_provenance", _freeze(json.loads(_canonical(dict(self.wave_provenance)))))

    # ---------------------------------------------------------------- persistence
    def _arrays(self) -> dict:
        out = dict(times=self.times, frame_indices=self.frame_indices, Q0=self.Q0, cg_body=self.cg_body,
                   added_mass_infinite=self.added_mass_infinite, radiation_kernel=self.radiation_kernel,
                   panel_to_nodal=self.panel_to_nodal, mean_hydrostatic_nodal=self.mean_hydrostatic_nodal,
                   hydrostatic_nodal=self.hydrostatic_nodal, node_ids=np.asarray(self.node_ids, dtype=np.int64))
        if self.mean_panel_pressure is not None:
            out["mean_panel_pressure"] = self.mean_panel_pressure
        if self.ext_mean_values is not None:
            out["ext_mean_values"] = self.ext_mean_values
        for name in _OPTIONAL_ARRAYS:
            if name == "kernel_horizon_arr":
                continue
            v = getattr(self, name)
            if v is not None:
                out[name] = v
        return out

    def _meta(self) -> dict:
        return dict(gravity=self.gravity, length_scale=self.length_scale, mass_digest=self.mass_digest,
                    hydrodynamic_verification=self.hydrodynamic_verification, hydro_admission=self.hydro_admission,
                    field_kernel_hash=self.field_kernel_hash, kernel_horizon=self.kernel_horizon,
                    native_source_binding=self.native_source_binding, unpassed_hydro_gates=list(self.unpassed_hydro_gates),
                    hub_node_id=self.hub_node_id, mean_thrust=self.mean_thrust,
                    fairleads=[dict(f) for f in self.fairleads], ext_components=list(self.ext_components),
                    ext_frames=list(self.ext_frames), ext_node_ids=list(self.ext_node_ids), identities=self.identities,
                    mean_equilibrium=self.mean_equilibrium, waterline_point_ids=list(self.waterline_point_ids),
                    waterline_guard_waiver=self.waterline_guard_waiver, wave_provenance=self.wave_provenance,
                    arrays=_declare(self._arrays()))

    def content_hash(self) -> str:
        from .floating_transient_record import content_hash
        return content_hash(dict(self._meta(), format=INPUT_FORMAT), self._arrays())

    def save(self, path: str) -> str:
        return save_npz(path, INPUT_FORMAT, self._meta(), self._arrays())


def load_floating_transient_input(path: str, *, expected_hash: Optional[str] = None) -> FloatingTransientInput:
    meta, arrays = load_npz(path, INPUT_FORMAT, expected_hash=expected_hash)
    kw = {k: arrays[k] for k in arrays if k != "node_ids"}
    return FloatingTransientInput(
        node_ids=tuple(int(i) for i in arrays["node_ids"]), gravity=meta["gravity"], length_scale=meta["length_scale"],
        mass_digest=meta["mass_digest"], hydrodynamic_verification=meta["hydrodynamic_verification"],
        hydro_admission=meta["hydro_admission"], field_kernel_hash=meta["field_kernel_hash"],
        kernel_horizon=meta["kernel_horizon"], native_source_binding=meta["native_source_binding"],
        unpassed_hydro_gates=tuple(meta["unpassed_hydro_gates"]), hub_node_id=meta["hub_node_id"],
        mean_thrust=meta["mean_thrust"], fairleads=tuple(meta["fairleads"]), ext_components=tuple(meta["ext_components"]),
        ext_frames=tuple(meta["ext_frames"]), ext_node_ids=tuple(meta["ext_node_ids"]), identities=meta["identities"],
        mean_equilibrium=meta.get("mean_equilibrium"), waterline_point_ids=tuple(meta.get("waterline_point_ids", ())),
        waterline_guard_waiver=meta.get("waterline_guard_waiver", ""), wave_provenance=meta.get("wave_provenance"),
        hydrostatic_pressure=kw.pop("hydrostatic_pressure", None), **kw)


WAVE_KINDS = ("native_bound", "imported")


def wave_history_provenance(*, kind: str, times, wave_pressure, incident_elevation, panel_ids, query_ids,
                            source: str, frame: str = "body", units: str = "Pa;m", geometry_digest: str = "",
                            pose_t=None, Q0=None, realization_digest: str = "") -> dict:
    """Immutable provenance of a wave pressure / incident elevation history: digests of the ACTUAL arrays and time
    stamps, the panel / query ids they are ordered by, and (native_bound) the source geometry, pose and realization.
    ``imported`` histories are explicit user data and are labelled so - never presented as native-bound."""
    from .floating_transient_record import array_digest
    if kind not in WAVE_KINDS:
        raise FloatingTransientWorkflowError(f"wave history kind must be one of {WAVE_KINDS}")
    rec = dict(kind=kind, source=str(source), frame=str(frame), units=str(units),
               times_digest=array_digest(np.asarray(times, float)),
               pressure_digest=array_digest(np.asarray(wave_pressure, float)),
               elevation_digest="" if incident_elevation is None else array_digest(np.asarray(incident_elevation, float)),
               panel_ids=[str(p) for p in panel_ids], query_ids=[str(q) for q in query_ids],
               geometry_digest=str(geometry_digest), realization_digest=str(realization_digest),
               pose_t=None if pose_t is None else [float(x) for x in np.asarray(pose_t, float)],
               Q0=None if Q0 is None else np.asarray(Q0, float).tolist())
    if kind == "native_bound" and not (rec["geometry_digest"] and rec["realization_digest"] and rec["pose_t"]):
        raise FloatingTransientWorkflowError("a native-bound wave history needs its source geometry, pose and "
                                             "realization digests")
    return rec


def check_wave_provenance(inp, field_kernel=None) -> None:
    """Arrays / time stamps must be the recorded ones (input) and, for native-bound histories, the ids / geometry /
    pose must be those of the field kernel and the prepared operators (run).  Same count, wrong source refuses."""
    from .floating_transient_record import array_digest
    if inp.wave_pressure is None:
        return
    w = dict(inp.wave_provenance or {})
    err = FloatingTransientWorkflowError
    if w.get("kind") not in WAVE_KINDS:
        raise err("wave pressure needs its explicit immutable wave_provenance (native_bound or imported)")
    if w.get("times_digest") != array_digest(np.asarray(inp.times, float)) or \
            w.get("pressure_digest") != array_digest(np.asarray(inp.wave_pressure, float)):
        raise err("wave pressure / time stamps differ from their recorded provenance digests")
    if inp.incident_elevation is not None and w.get("elevation_digest") != array_digest(
            np.asarray(inp.incident_elevation, float)):
        raise err("incident elevation differs from its recorded provenance digest")
    if len(w.get("panel_ids", ())) != inp.panel_to_nodal.shape[2]:
        raise err("wave provenance panel ids do not match the panel mapping")
    if field_kernel is None:
        return
    if list(w["panel_ids"]) != [str(p) for p in field_kernel.panel_ids]:
        raise err("wave history panel ids / order differ from the field kernel panels (wrong source)")
    if inp.waterline_points_body is not None and list(w.get("query_ids", ())) != list(inp.waterline_point_ids):
        raise err("wave history query ids / order differ from the waterline guard points (wrong source)")
    if w["kind"] == "native_bound":
        fk_geo = dict(getattr(field_kernel, "identity", None) or {}).get("family_geometry_digest")
        prep = dict((inp.identities or {}).get("coarse_prepare") or {})
        if w.get("geometry_digest") != fk_geo:
            raise err("native-bound wave history belongs to another source geometry (wrong source)")
        if prep.get("pose_t") is None or not np.allclose(w.get("pose_t"), prep["pose_t"], rtol=0.0, atol=1e-9) \
                or not np.allclose(w.get("Q0"), inp.Q0, rtol=0.0, atol=1e-12):
            raise err("native-bound wave history was posed elsewhere (pose differs from the prepared operators)")


def check_waterline_bound_source(inp, field_kernel) -> None:
    """exact_dirichlet_z0 (or any recorded pointwise bound): the guard must use the ACTUAL source-bound envelope -
    inp.waterline_bound whose numeric digest equals the field kernel's recorded pointwise_bound_digest; None never
    becomes zeros."""
    from anyhydro.radiation_field_kernel import numeric_array_digest
    ident = dict(getattr(field_kernel, "identity", None) or {})
    recorded = str(ident.get("pointwise_bound_digest", "") or "")
    needs = recorded or str(ident.get("boundary_policy", "")) == "exact_dirichlet_z0"
    if not needs or inp.waterline_points_body is None:
        return
    if inp.waterline_bound is None:
        raise FloatingTransientWorkflowError("the field kernel records a pointwise waterline bound "
                                             "(exact_dirichlet_z0); the guard needs that actual bound, not zeros")
    if not recorded or numeric_array_digest(np.asarray(inp.waterline_bound, float)) != recorded:
        raise FloatingTransientWorkflowError("waterline_bound is not the source-recorded pointwise bound "
                                             "(digest mismatch)")


def check_guard_coordinates(inp, field_kernel, rel_tol: float = 1e-9) -> None:
    """The waterline guard points must be the field kernel's OWN bound query coordinates (identity
    ``query_points_world``, content-hashed with the kernel) at the prepared pose: world = Q0 (p + cg) + t with t the
    prepared pose translation.  Matching id strings alone are not evidence."""
    fk_id = dict(getattr(field_kernel, "identity", None) or {})
    bound = fk_id.get("query_points_world")
    prep = dict((inp.identities or {}).get("coarse_prepare") or {})
    if bound is None or prep.get("pose_t") is None:
        raise FloatingTransientWorkflowError("the field kernel / preparation does not bind the guard query coordinates "
                                             "(query_points_world, pose_t); refused")
    q = np.asarray(bound, dtype=float)
    world = (np.asarray(inp.waterline_points_body, float) + inp.cg_body) @ inp.Q0.T + np.asarray(prep["pose_t"], float)
    scale = max(1.0, float(np.max(np.abs(q), initial=0.0)))
    if q.shape != world.shape or not float(np.max(np.abs(q - world), initial=0.0)) <= rel_tol * scale:
        raise FloatingTransientWorkflowError("waterline guard coordinates differ from the field kernel's bound query "
                                             "coordinates at the prepared pose")


_ADMITTED_BOUNDARY = ("admitted", "admitted_native_source", "")


def field_kernel_failures(field_kernel: Any) -> list:
    """Failures BOUND in the field-kernel artifact itself (identity + diagnostics), independent of any caller string:
    development marker, retained unpassed gates, failed reconstruction reports and a non-admitted native boundary /
    policy status.  A non-empty list can never be run as accepted hydrodynamics."""
    ident = dict(getattr(field_kernel, "identity", None) or {})
    diag = dict(getattr(field_kernel, "diagnostics", None) or {})
    out = []
    if ident.get("development") or diag.get("development"):
        out.append("development_unverified_field_kernel")
    out += [str(g) for g in (diag.get("unpassed_gates") or ())]
    for kind, rep in dict(diag.get("reconstruction") or {}).items():
        if isinstance(rep, Mapping) and rep.get("passed") is False:
            out.append(f"{kind}_transfer_reconstruction")
    # boundary: the EFFECTIVE status is the recomputed policy status when a policy was selected (e.g. an admitted
    # exact_dirichlet_z0 evaluation over a RAW failed boundary is not a failure); without a policy the raw status rules
    policy = str(ident.get("policy_status", "") or "")
    key, value = ("policy_status", policy) if policy else ("native_boundary_status",
                                                            str(ident.get("native_boundary_status", "") or ""))
    if any(word in value.lower() for word in ("fail", "unchecked", "refus", "unverified", "not_admitted", "rejected")):
        out.append(f"{key}:{value}")
    seen, unique = set(), []
    for g in out:
        if g not in seen:
            seen.add(g); unique.append(g)
    return unique


def hydro_input_fields(*, radiation_kernel: Any, added_mass_infinite: Any, dt: float, field_kernel: Any,
                       verification: str, unpassed_gates: Sequence[str] = (), admission_omegas: Any = None,
                       kernel_horizon: Optional[float] = None) -> dict:
    """Bind supplied / retained hydro operators to a job input (no BEM).  The digests come from the ACTUAL arrays and
    must equal those the field kernel was built from (stale or mixed sources refuse); ``verification='unverified'``
    (coarse development operators) selects the explicit ``development_unverified`` solver admission with the unpassed
    gates listed verbatim, ``'accepted'`` keeps the strict default.  Returns FloatingTransientInput keyword fields."""
    from anyhydro.radiation_field_kernel import numeric_array_digest
    K = np.asarray(radiation_kernel, dtype=np.float64)
    A = np.asarray(added_mass_infinite, dtype=np.float64)
    if K.ndim != 3 or K.shape[1:] != (6, 6) or not 2 <= K.shape[0] <= MAX_KERNEL_SAMPLES:
        raise FloatingTransientWorkflowError(f"radiation kernel must be (2..{MAX_KERNEL_SAMPLES}, 6, 6)")
    kd, ad = numeric_array_digest(K), numeric_array_digest(A)
    if not field_kernel.kernel_digest or field_kernel.kernel_digest != kd or field_kernel.ainf_digest != ad:
        raise FloatingTransientWorkflowError("the field kernel was not built from these generalized operators (digests differ)")
    if abs(float(field_kernel.dt) - float(dt)) > 1e-9 * float(dt):
        raise FloatingTransientWorkflowError("field-kernel dt differs from the operator dt")
    bound = field_kernel_failures(field_kernel)
    if verification == HYDRO_ACCEPTED and bound:
        raise FloatingTransientWorkflowError(
            f"the field kernel carries unverified / failed / development state {bound}; it cannot be declared accepted "
            "(failed reconstruction or development artifacts stay unverified)")
    unpassed_gates = tuple(unpassed_gates) + tuple(g for g in bound if g not in tuple(unpassed_gates))
    if verification == HYDRO_ACCEPTED:
        admission, binding, omegas = "strict", None, None
    elif verification == HYDRO_UNVERIFIED:
        if admission_omegas is None or not unpassed_gates:
            raise FloatingTransientWorkflowError("unverified hydro needs its explicit frequency grid and unpassed gates")
        admission, binding, omegas = "development_unverified", dict(kernel_digest=kd, ainf_digest=ad, kernel_dt=float(dt)), \
            np.asarray(admission_omegas, dtype=np.float64)
    else:
        raise FloatingTransientWorkflowError("verification must be 'accepted' or 'unverified'")
    return dict(added_mass_infinite=A, radiation_kernel=K, kernel_horizon=kernel_horizon,
                hydrodynamic_verification=verification, hydro_admission=admission, native_source_binding=binding,
                admission_omegas=omegas, unpassed_hydro_gates=tuple(unpassed_gates),
                field_kernel_hash=field_kernel.content_hash())


# ----------------------------------------------------------------------------- operators

def rigid_map(points: np.ndarray, cg: np.ndarray) -> np.ndarray:
    """(n, 6, 6): nodal (translation, rotation) of the rigid motion q about the CG: u = q_t + theta x r, rot = theta."""
    r = np.asarray(points, float) - np.asarray(cg, float)[None, :]
    R = np.zeros((r.shape[0], 6, 6))
    R[:, :3, :3] = np.eye(3)
    R[:, 3:, 3:] = np.eye(3)
    R[:, 0, 4], R[:, 0, 5] = r[:, 2], -r[:, 1]
    R[:, 1, 3], R[:, 1, 5] = -r[:, 2], r[:, 0]
    R[:, 2, 3], R[:, 2, 4] = r[:, 1], -r[:, 0]
    return R


def generalized(R: np.ndarray, nodal: np.ndarray) -> np.ndarray:
    """Virtual-work generalized force sum_i R_i^T L_i = [sum F; sum (r x F + M)]."""
    return np.einsum("nki,nk->i", R, np.asarray(nodal, float))


def _skew(v):
    return np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])


STATIC_CLOSURE_TOL = 1e-8
N_FRAME_COMPONENTS = 15        # 8 perturbation/total + 6 mean (incl. mean_total) + inertia reference in relief input


def static_mean_closure(R: np.ndarray, components: Mapping[str, np.ndarray], length_scale: float = 1.0) -> dict:
    """Separate STATIC mean closure: |sum F| <= 1e-8 sum|F_c| and |sum M| <= 1e-8 max(sum|M_c|, L sum|F_c|) about the
    dry CG (virtual-work resultants of the actual nodal distributions).  Not the 1% dynamic criterion."""
    W = {k: generalized(R, v) for k, v in components.items()}
    total = sum(W.values()) if W else np.zeros(6)
    f_scale = sum(float(np.linalg.norm(w[:3])) for w in W.values())
    m_scale = max(sum(float(np.linalg.norm(w[3:])) for w in W.values()), float(length_scale) * f_scale)
    fr = float(np.linalg.norm(total[:3])) / f_scale if f_scale > 0 else (0.0 if not np.any(total[:3]) else np.inf)
    mr = float(np.linalg.norm(total[3:])) / m_scale if m_scale > 0 else (0.0 if not np.any(total[3:]) else np.inf)
    return dict(passed=bool(fr <= STATIC_CLOSURE_TOL and mr <= STATIC_CLOSURE_TOL), force_ratio=fr, moment_ratio=mr,
                resultant=[float(x) for x in total], components={k: [float(x) for x in w] for k, w in W.items()},
                force_scale=f_scale, moment_scale=m_scale, tolerance=STATIC_CLOSURE_TOL)


STATUS_NOT_SCREENED = "completed_elastic_screening_not_passed"
STATUS_SCREENING_ATTENTION = "completed_quasistatic_attention_nonaccepted"
SCREENING_RATIO = 3.0           # declared: lowest elastic frequency >= 3 x loading band (may only tighten)
SCREENING_ENERGY_FRACTION = 0.99


@dataclass(frozen=True)
class ElasticModalRecord:
    """Supplied VERIFIED elastic modal artifact of the SAME structural model (no eigensolve is run here).  The producer
    excludes rigid modes; ``structural_digest`` is ``structural_identity`` of the model it was computed for."""

    structural_digest: str
    frequencies_hz: tuple
    n_rigid_modes_excluded: int
    source: str
    verified: bool = False
    scope: str = ""
    origin: Mapping[str, Any] = field(default_factory=dict)   # supplied-evidence origin (path, sha256) - user evidence,
                                                               # NOT an authentication of dynamic accuracy

    def __post_init__(self):
        f = tuple(float(x) for x in self.frequencies_hz)
        if not f or not all(np.isfinite(x) and x > 0.0 for x in f):
            raise FloatingTransientWorkflowError("elastic modal record needs positive finite elastic frequencies")
        object.__setattr__(self, "frequencies_hz", tuple(sorted(f)))
        if len(str(self.structural_digest)) != 64 or not self.source:
            raise FloatingTransientWorkflowError("elastic modal record needs its structural digest and source")

    def to_dict(self) -> dict:
        return dict(structural_digest=self.structural_digest, frequencies_hz=list(self.frequencies_hz),
                    n_rigid_modes_excluded=int(self.n_rigid_modes_excluded), source=self.source,
                    verified=bool(self.verified), scope=self.scope)

    @classmethod
    def load_json(cls, path: str) -> "ElasticModalRecord":
        with open(path, "rb") as stream:
            raw = stream.read()
        data = json.loads(raw.decode("utf-8"))
        origin = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), kind="user_supplied_modal_json",
                      note="supplied evidence; verified=True is the supplier's declaration, not an authentication")
        return cls(origin=origin, **{k: data[k] for k in ("structural_digest", "frequencies_hz",
                                                          "n_rigid_modes_excluded", "source", "verified", "scope")
                                     if k in data})


def loading_spectrum(times, channels, fraction: float = SCREENING_ENERGY_FRACTION, chunk: int = 64):
    """Energy cutoff over ALL supplied physical load / coefficient histories at EVERY integration sample.

    ``channels``: iterable of (name, (n_t, k) array) - e.g. rigid q / a, thrust, wave and radiation PANEL pressures
    (self-equilibrated panel fields count even with zero rigid response), external perturbation values and their
    world-rotation products, mooring forces.  Each (mean-removed, non-zero) channel is normalised to unit energy and
    weighs equally; columns are transformed in bounded chunks.  Returns the cutoff frequency, the energy fraction
    above it (omitted) and the channel count; None cutoff on a non-uniform grid.  A cutoff is a heuristic
    description of the loading, NOT a proof that no elastic resonance is excited."""
    t = np.asarray(times, float)
    dt = np.diff(t)
    if t.size < 4 or not float(np.max(np.abs(dt - dt.mean()))) <= 1e-9 * float(dt.mean()):
        return dict(cutoff_hz=None, omitted_energy_fraction=None, n_channels=0, reason="non-uniform time grid")
    f = np.fft.rfftfreq(t.size, float(dt.mean()))
    total = np.zeros(f.size)
    n_ch, names = 0, []
    for name, arr in channels:
        x = np.asarray(arr, float).reshape(t.size, -1)
        for s in range(0, x.shape[1], chunk):
            blk = x[:, s:s + chunk] - x[:, s:s + chunk].mean(axis=0)
            P = np.abs(np.fft.rfft(blk, axis=0)) ** 2
            e = P.sum(axis=0)
            live = e > 0.0
            if np.any(live):
                total += (P[:, live] / e[live]).sum(axis=1)
                n_ch += int(np.count_nonzero(live))
        names.append(str(name))
    if n_ch == 0:
        return dict(cutoff_hz=0.0, omitted_energy_fraction=0.0, n_channels=0, channels=names)
    c = np.cumsum(total) / float(total.sum())
    k = int(np.searchsorted(c, fraction))
    return dict(cutoff_hz=float(f[min(k, f.size - 1)]), omitted_energy_fraction=float(1.0 - c[min(k, f.size - 1)]),
                n_channels=n_ch, channels=names, nyquist_hz=float(f[-1]), energy_fraction=fraction)


def loading_bandwidth_hz(times, accelerations, thrust=None, fraction: float = SCREENING_ENERGY_FRACTION):
    """Backward-compatible narrow helper (rigid accelerations + thrust only); the run uses ``loading_spectrum``."""
    ch = [("rigid_acceleration", accelerations)]
    if thrust is not None:
        ch.append(("thrust", np.asarray(thrust, float).reshape(-1, 1)))
    return loading_spectrum(times, ch, fraction)["cutoff_hz"]


def elastic_screening(record: Optional[ElasticModalRecord], fe_model: Any, times, accelerations, thrust=None,
                      ratio: float = SCREENING_RATIO, spectrum: Optional[dict] = None) -> dict:
    """HEURISTIC quasistatic-approximation REPORT (not an accuracy gate): lowest bound elastic frequency vs the
    loading energy cutoff.  A bound verified record gives status 'reported' with indicator 'attention' when
    f1 < ratio x cutoff (no accuracy is inferred either way); missing / unverified / foreign modal data or a
    non-uniform grid -> 'not_assessed' (never implied passed; the run is then non-accepted)."""
    if not (np.isfinite(ratio) and ratio >= SCREENING_RATIO):
        raise FloatingTransientWorkflowError(f"screening ratio may only tighten (>= {SCREENING_RATIO})")
    spec = spectrum if spectrum is not None else loading_spectrum(
        times, [("rigid_acceleration", accelerations)] + ([] if thrust is None else
                                                          [("thrust", np.asarray(thrust, float).reshape(-1, 1))]))
    band = spec.get("cutoff_hz")
    base = dict(ratio_heuristic=float(ratio), loading_band_hz=band, loading_spectrum=spec,
                energy_fraction=SCREENING_ENERGY_FRACTION, label="heuristic quasistatic-approximation indicator",
                scope="energy cutoff of all physical load histories vs lowest supplied bound elastic frequency; no "
                      "eigensolve; a cutoff does not prove that no resonance is excited; no accuracy is inferred")
    if record is None:
        return dict(base, status="not_assessed", reason="no verified elastic modal record supplied")
    if not record.verified:
        return dict(base, status="not_assessed", reason="modal record is not marked verified", source=record.source)
    if fe_model is None or structural_identity(fe_model) != record.structural_digest:
        return dict(base, status="not_assessed", reason="modal record belongs to another structural model")
    if band is None:
        return dict(base, status="not_assessed", reason="non-uniform time grid: loading band not assessed")
    f1 = record.frequencies_hz[0]
    quiet = band == 0.0 or f1 >= ratio * band
    return dict(base, status="reported", indicator="no_attention" if quiet else "attention",
                frequency_ratio=None if band == 0.0 else float(f1 / band), lowest_elastic_hz=f1, source=record.source,
                origin=dict(record.origin or {}), record=record.to_dict(),
                modal_scope=record.scope, n_rigid_modes_excluded=int(record.n_rigid_modes_excluded))


#: DECLARED conservative bound of recovered stress values per element and frame (largest owner layouts: shell
#: integration points x surfaces x components); a recovered layout above it is refused after recovery (never stored)
STRESS_VALUES_PER_ELEMENT_BOUND = 132    # LEGACY compatibility cap only (>= default Q8: 9 Gauss x 11 arrays = 99);
                                         # the preflight uses the per-element owner-contract count below


def element_stress_value_count(fe_model: Any, element: Any, _cache: Optional[dict] = None) -> int:
    """PROVEN per-element recovery layout: numeric values returned by the element's OWN ``compute_stresses`` contract
    (anysolver.elements: e.g. shells return 11 arrays x n Gauss points, more with ``return_global``), probed once
    per element on a zero local displacement field, maximised over ``return_global`` False/True. An element
    without a usable public ``compute_stresses`` contract cannot be bounded and is refused by the caller."""
    # Cache only this element within one preflight. Different material/section
    # recovery layouts must never share a count merely because their types match.
    key = id(element)
    if _cache is not None and key in _cache:
        return _cache[key]
    fn = getattr(element, "compute_stresses", None)
    if not callable(fn):
        raise FloatingTransientWorkflowError(f"element type {type(element).__name__} has no public compute_stresses "
                                             "contract: its recovery layout cannot be bounded before allocation")
    mat_name = getattr(element, "material_name", getattr(element, "material", None))
    material = fe_model.get_material(mat_name if isinstance(mat_name, str) else None)
    # Match anysolver.recovery's local-vector contract for every element family.
    zeros = np.zeros(len(element.get_dof_mapping(fe_model.mesh)))
    best = 0
    for flag in (False, True):
        try:
            values = fn(fe_model.mesh, zeros, material, return_global=flag)
        except TypeError:
            values = fn(fe_model.mesh, zeros, material)
        count = sum(int(np.asarray(v).size) for v in dict(values).values()
                    if np.asarray(v).dtype.kind in "biufc")
        best = max(best, count)
    if _cache is not None:
        _cache[key] = best
    return best


def stress_values_per_frame(fe_model: Any) -> int:
    """Exact sum of the per-element owner-contract counts (refuses an unboundable element before any allocation)."""
    cache, total = {}, 0
    for el in fe_model.mesh.elements.values():
        try:
            total += element_stress_value_count(fe_model, el, cache)
        except FloatingTransientWorkflowError:
            raise
        except Exception as exc:
            raise FloatingTransientWorkflowError(
                f"recovery layout of {type(el).__name__} could not be bounded from its owner contract ({exc}); "
                "refused before allocation (run with recover_stresses=False)") from exc
    return total


def _field_fft_work(n_s: int, n_field: int, n_time: int, chunk: int) -> int:
    """Owner FFT working set of ``field_history_from_transient`` for one field kind (radiation_field_kernel
    ``_admit_history`` formula: kernel-chunk rFFT, product, inverse, velocity spectrum, outputs, endpoint correction)."""
    if n_field == 0:
        return 0
    nfft = 1 << int(np.ceil(np.log2(max(2, n_s + n_time - 1))))
    c = max(1, min(int(chunk), n_field))
    return 8 * (nfft * c * (6 + 2) + nfft * 6 + n_s * c * (3 + 2 * 6))


def transient_memory_preflight(inp, *, n_nodes: int, n_ext: int = 0, recover_stresses: bool = True,
                               max_work_bytes: int = MAX_WORK_BYTES, n_elements: Optional[int] = None,
                               field_kernel: Any = None, stress_values: Optional[int] = None) -> dict:
    """Count the ACTUAL retained arrays before integration: stored input arrays, rigid history, generalised load
    histories, radiation pressure + elevation field histories AND the owner FFT working set, waterline guard arrays,
    every per-frame component array, displacements, stresses (declared per-element bound x actual element count) and
    the published artifact.  The ceiling can only be tightened.  Stresses without an element count cannot be bounded
    and are refused."""
    from .floating_transient_record import MAX_ARTIFACT_BYTES
    if not (isinstance(max_work_bytes, (int, np.integer)) and 0 < int(max_work_bytes) <= MAX_WORK_BYTES):
        raise FloatingTransientWorkflowError(f"max_work_bytes must lie in (0, {MAX_WORK_BYTES}] (may only tighten)")
    if recover_stresses and n_elements is None and stress_values is None:
        raise FloatingTransientWorkflowError("stress recovery cannot be bounded without the selected element count "
                                             "or the owner-contract stress layout")
    n_t, n_p = int(inp.times.size), int(inp.panel_to_nodal.shape[2])
    n_q = 0 if inp.waterline_points_body is None else int(inp.waterline_points_body.shape[0])
    n_f = int(inp.frame_indices.size)
    b = 8
    stored = sum(int(np.asarray(v).nbytes) for v in inp._arrays().values())
    traj = n_t * (6 * 4 + 2) * b
    gen = n_t * 6 * 3 * b + n_t * max(n_ext, 0) * b
    fields = n_t * n_p * b + n_t * n_q * b * 2 + 4 * n_t * n_q * b
    fft = 0
    if field_kernel is not None:
        n_time, chunk = int(field_kernel.n_time), int(field_kernel.chunk_size)
        fft = max(_field_fft_work(n_t, n_p, n_time, chunk), _field_fft_work(n_t, n_q, n_time, chunk))
    frames = n_f * n_nodes * 6 * b * N_FRAME_COMPONENTS
    disp = n_f * n_nodes * 6 * b
    per_frame = int(stress_values) if stress_values is not None else int(n_elements or 0) * STRESS_VALUES_PER_ELEMENT_BOUND
    stress = n_f * per_frame * b if recover_stresses else 0
    # loading-spectrum report: 64-column complex rFFT blocks (+ normalised copy) and the external rotation products
    spectrum = 2 * 16 * (n_t // 2 + 1) * 64 + n_t * max(n_ext, 0) * 3 * b
    artifact = traj + frames + disp + stress
    # the published artifact is serialised from canonical copies while the in-memory result still exists
    work = stored + traj + gen + fields + fft + frames + disp + stress + spectrum + artifact
    out = dict(work_bytes=int(work), artifact_bytes=int(artifact), field_bytes=int(fields), frame_bytes=int(frames),
               stored_input_bytes=int(stored), field_fft_bytes=int(fft), stress_bound_bytes=int(stress),
               stress_values_per_element_bound=STRESS_VALUES_PER_ELEMENT_BOUND if recover_stresses else 0)
    if work > int(max_work_bytes):
        raise FloatingTransientWorkflowError(f"transient working set {work} B (fields {fields} B, {N_FRAME_COMPONENTS} "
                                             f"frame components {frames} B) exceeds {int(max_work_bytes)} B")
    if artifact > MAX_ARTIFACT_BYTES:
        raise FloatingTransientWorkflowError(f"result artifact {artifact} B exceeds {MAX_ARTIFACT_BYTES} B")
    return out


class _Operators:
    """Precomputed structural operators of ONE selected model / mean pose."""

    def __init__(self, inp: FloatingTransientInput, dry: Any):
        if mass_identity(dry) != inp.mass_digest:
            raise FloatingTransientWorkflowError("the selected FE dry mass differs from the job's mass identity (stale input)")
        if tuple(int(i) for i in dry.node_ids) != inp.node_ids:
            raise FloatingTransientWorkflowError("node ids of the selected model differ from the job input")
        cg = np.asarray(dry.cg, float)
        if np.max(np.abs(cg - inp.cg_body)) > 1e-9 * max(1.0, float(np.max(np.abs(cg)))):
            raise FloatingTransientWorkflowError("job CG differs from the authoritative dry-FE CG (stale mean pose)")
        self.inp, self.dry = inp, dry
        self.points = np.asarray(dry.points, float)
        self.cg = cg
        self.R = rigid_map(self.points, cg)
        self.n = self.points.shape[0]
        self.M6 = np.column_stack([generalized(self.R, self.mass_nodal(self.R[:, :, j])) for j in range(6)])
        g0 = inp.Q0.T @ np.array([0.0, 0.0, -inp.gravity])
        self.weight_unit = np.zeros((6, self.n, 6))
        for j in range(3):
            theta = np.zeros(3); theta[j] = 1.0
            acc = np.zeros((self.n, 6)); acc[:, :3] = -np.cross(theta, g0)[None, :]
            self.weight_unit[3 + j] = self.mass_nodal(acc)
        self.K_g = -np.column_stack([generalized(self.R, self.weight_unit[j]) for j in range(6)])
        # exact hydrostatic nodal operator (wet-region quadrature of the linear field gradient); the same nodal
        # distribution enters the generalised stiffness (R^T) and the recovered structural perturbation
        self.hs_unit = np.asarray(inp.hydrostatic_nodal, dtype=float)
        self.K_hs = -np.column_stack([generalized(self.R, self.hs_unit[j]) for j in range(6)])
        self.ext_mean_unit, self.K_ext_mean = self._external_mean_rotation(inp)
        self.index = {nid: i for i, nid in enumerate(inp.node_ids)}

    def _external_mean_rotation(self, inp):
        """World-fixed MEAN external channels seen in body axes rotate with -theta: e_b(theta) = e + e x theta.  The
        first-order term mean * (e x theta_j) is a nodal load per unit rotation (6, n, 6) and its generalised
        negative is a stiffness; it enters BOTH the ODE and the recovered perturbation (the mean itself only once)."""
        unit = np.zeros((6, self.n, 6))
        if inp.ext_mean_values is None:
            return unit, np.zeros((6, 6))
        self.index = {nid: i for i, nid in enumerate(inp.node_ids)}
        for c, compname in enumerate(inp.ext_components):
            if str(inp.ext_frames[c]) != "world":
                continue                                   # body-fixed channels rotate with the body: no term
            e = np.zeros(3); e["XYZ".index(compname[1])] = 1.0
            e = inp.Q0.T @ e
            m0 = float(inp.ext_mean_values[c])
            for j in range(3):
                th = np.zeros(3); th[j] = 1.0
                vec = m0 * np.cross(e, th)
                node, pt = int(inp.ext_node_ids[c]), np.asarray(inp.ext_points_body[c], float)
                unit[3 + j] += (self.point_load(node, pt, vec) if compname[0] == "F"
                                else self.point_load(node, pt, np.zeros(3), moment=vec))
        return unit, -np.column_stack([generalized(self.R, unit[j]) for j in range(6)])

    def mass_nodal(self, acc: np.ndarray) -> np.ndarray:
        dm = self.dry.dofmap
        full = np.asarray(self.dry.mass_matrix @ dm.scatter(np.asarray(acc, dtype=float)), dtype=float)
        return np.asarray(dm.gather(full), dtype=float)

    def point_load(self, node_id: int, point_body: np.ndarray, force: np.ndarray, moment=None) -> np.ndarray:
        """Nodal (n,6) of a force at a body point (relative to CG) applied at a node with the exact transport moment."""
        if node_id not in self.index:
            raise FloatingTransientWorkflowError(f"attachment node {node_id} is not in the structural node set")
        i = self.index[node_id]
        out = np.zeros((self.n, 6))
        arm = self.cg + np.asarray(point_body, float) - self.points[i]
        out[i, :3] = force
        out[i, 3:] = np.cross(arm, force) + (0.0 if moment is None else moment)
        return out


# ----------------------------------------------------------------------------- run

def _external_history(inp):
    if inp.ext_values is None:
        return None
    from anyloads.external_history import ExternalLoadHistory
    return ExternalLoadHistory(inp.ext_times, inp.ext_values, inp.ext_components, inp.ext_frames, inp.ext_points_body,
                               tuple(str(i) for i in inp.ext_node_ids), identity={"source": "floating-transient-input"})


def _mooring(inp):
    if not inp.fairleads:
        return None
    from anyloads.external_history import AffineFairleadMooring, FairleadSpec
    specs = tuple(FairleadSpec(str(f["identity"]), f["point_body"], f["pretension_world"], f["stiffness"],
                               f.get("damping")) for f in inp.fairleads)
    return AffineFairleadMooring(specs, inp.Q0, validity_half_range=inp.mooring_validity)


def run_floating_transient(inp: FloatingTransientInput, *, fe_model: Any = None, dry_mass: Any = None,
                           field_kernel: Any = None, relief: Optional[Callable[..., Any]] = None, batch_size: int = 8,
                           output_dofs: Any = None, cancellation_token: Any = None,
                           progress_callback: Optional[Callable[[str, int, int], None]] = None,
                           session: Any = None, recover_stresses: bool = True,
                           max_work_bytes: int = MAX_WORK_BYTES,
                           resource_config: Any = None,
                           screening: Optional["ElasticModalRecord"] = None,
                           rigid_limits: Optional[Mapping[str, Any]] = None) -> FloatingTransientResult:
    """Run the connected transient.  ``dry_mass`` defaults to ``dry_mass_properties(fe_model)``; ``field_kernel`` is the
    digest-bound RadiationFieldKernel whose content hash must equal ``inp.field_kernel_hash``; ``relief`` defaults to
    ``iter_residual_relief(fe_model, ...)``.  Refusals before integration raise; later failures return an explicit
    non-success result that keeps the accepted rigid history and no structural frames."""
    from anysolver.floating_transient import FloatingTransientSpec, solve_floating_transient
    from anyhydro.floating_transient_fields import guarded_clearance
    from anyhydro.radiation_field_kernel import field_history_from_transient

    if dry_mass is None:
        if fe_model is None:
            raise FloatingTransientWorkflowError("an FE model or its dry mass properties are required")
        from .floating_mass import dry_mass_properties
        dry_mass = dry_mass_properties(fe_model)
    if field_kernel is None or field_kernel.content_hash() != inp.field_kernel_hash:
        raise FloatingTransientWorkflowError("the supplied RadiationFieldKernel does not match the job's field_kernel_hash")
    if field_kernel.pressure_a.shape[0] != inp.panel_to_nodal.shape[2]:
        raise FloatingTransientWorkflowError("field-kernel panels differ from the panel-to-nodal mapping")
    bound_failures = field_kernel_failures(field_kernel)    # re-derived here: the helper cannot be bypassed
    if inp.hydrodynamic_verification == HYDRO_ACCEPTED and bound_failures:
        raise FloatingTransientWorkflowError(
            f"the field kernel carries unverified / failed / development state {bound_failures}; an accepted run is "
            "refused (the input's verification string cannot promote it)")
    if inp.waterline_points_body is not None and \
            tuple(inp.waterline_point_ids) != tuple(str(i) for i in field_kernel.query_point_ids):
        raise FloatingTransientWorkflowError("waterline guard point ids (order included) differ from the field-kernel "
                                             "query points; coordinates are bound at preparation, never by count")
    ops = _Operators(inp, dry_mass)              # mass / node / CG staleness first (the most specific diagnostic)
    if inp.waterline_points_body is not None:
        check_guard_coordinates(inp, field_kernel)
    check_waterline_bound_source(inp, field_kernel)
    check_wave_provenance(inp, field_kernel)
    t = inp.times
    n_t = t.size
    R = ops.R
    from .floating_loads import dynamic_closure

    # ---- static MEAN physical components and their separate balance gate (before any integration / relief)
    mean = {"mean_pressure": np.array(inp.mean_hydrostatic_nodal, dtype=float)}   # exact wet-region quadrature
    acc_g = np.zeros((ops.n, 6)); acc_g[:, :3] = (inp.Q0.T @ np.array([0.0, 0.0, -inp.gravity]))[None, :]
    mean["mean_gravity"] = ops.mass_nodal(acc_g)                      # ONLY gravity term (perturbation: K_g separately)
    mean["mean_pretension"] = np.zeros((ops.n, 6))
    for fl in inp.fairleads:
        mean["mean_pretension"] += ops.point_load(int(fl["node_id"]), np.asarray(fl["point_body"], float),
                                                  inp.Q0.T @ np.asarray(fl["pretension_world"], float))
    mean["mean_thrust"] = np.zeros((ops.n, 6))
    if inp.wind_velocity is not None:
        mean["mean_thrust"] = ops.point_load(int(inp.hub_node_id), inp.hub_offset_body,
                                             inp.rotor_axis_body * float(inp.mean_thrust))
    mean["mean_external"] = np.zeros((ops.n, 6))
    if inp.ext_mean_values is not None:
        for c, compname in enumerate(inp.ext_components):
            e = np.zeros(3); e["XYZ".index(compname[1])] = 1.0
            if inp.ext_frames[c] == "world":
                e = inp.Q0.T @ e
            vec = float(inp.ext_mean_values[c]) * e
            pt = inp.ext_points_body[c]
            mean["mean_external"] += (ops.point_load(int(inp.ext_node_ids[c]), pt, vec) if compname[0] == "F" else
                                      ops.point_load(int(inp.ext_node_ids[c]), pt, np.zeros(3), moment=vec))
    mean_applied = mean["mean_pressure"] + mean["mean_pretension"] + mean["mean_thrust"] + mean["mean_external"]
    static = static_mean_closure(R, dict(mean), inp.length_scale)
    if not static["passed"]:
        raise FloatingTransientWorkflowError(
            f"static mean force/moment closure fails (force {static['force_ratio']:.3g}, moment "
            f"{static['moment_ratio']:.3g} of the component scales > {STATIC_CLOSURE_TOL:g}): the mean hydrostatic, "
            "gravity, pretension, thrust and external loads are not an equilibrium; refused before integration and "
            "relief (a mean imbalance is never absorbed by the residual acceleration)")
    mean["mean_total"] = mean_applied + mean["mean_gravity"]
    n_elements = None if fe_model is None else len(getattr(fe_model.mesh, "elements", {}) or {})
    stress_values = None
    if recover_stresses and fe_model is not None:
        stress_values = stress_values_per_frame(fe_model)     # exact owner-contract layout, before any allocation
    from anyhydro.radiation_field_kernel import FieldResourceLimits
    from anysolver.floating_transient import FloatingTransientResourceConfig
    # ONE derivation of the tighter limits: field histories / clearance (FieldResourceLimits), the rigid solver
    # (FloatingTransientResourceConfig: working bytes, steps, operations) and the owner batch (resource_config)
    field_limits = FieldResourceLimits(max_work_bytes=int(min(max_work_bytes, FieldResourceLimits().max_work_bytes)))
    rl = dict(rigid_limits or {})
    unknown = set(rl) - {"max_steps", "max_operations"}
    if unknown:
        raise FloatingTransientWorkflowError(f"unknown rigid limits {sorted(unknown)} (max_steps, max_operations)")
    rigid_config = FloatingTransientResourceConfig(
        max_working_bytes=int(min(max_work_bytes, FloatingTransientResourceConfig().max_working_bytes)), **rl)
    if n_t - 1 > int(rigid_config.max_steps):
        raise FloatingTransientWorkflowError(f"{n_t - 1} integration steps exceed the requested max_steps "
                                             f"{int(rigid_config.max_steps)}; refused before integration")
    work = transient_memory_preflight(inp, n_nodes=ops.n, n_ext=0 if inp.ext_values is None else
                                      int(np.asarray(inp.ext_values).shape[-1]),
                                      recover_stresses=recover_stresses and fe_model is not None,
                                      max_work_bytes=max_work_bytes, n_elements=n_elements, field_kernel=field_kernel,
                                      stress_values=stress_values)

    # ---- generalised loads with matching distributions
    T = inp.panel_to_nodal
    F_wave = np.zeros((n_t, 6))
    if inp.wave_pressure is not None:
        F_wave = np.stack([generalized(R, np.einsum("nkp,p->nk", T, inp.wave_pressure[k])) for k in range(n_t)])
    ext = _external_history(inp)
    F_ext, G = np.zeros((n_t, 6)), None
    if ext is not None:
        F_ext = ext.generalized_force(t, inp.Q0)
        Gt = ext.load_tangent(t, inp.Q0)
        G = Gt if np.any(Gt) else None
    moor = _mooring(inp)
    K_m = np.zeros((6, 6)) if moor is None else moor.stiffness_matrix()
    C_m = np.zeros((6, 6)) if moor is None else moor.damping_matrix()
    turbine = {}
    if inp.wind_velocity is not None:
        turbine = dict(wind_times=inp.wind_times, wind_velocity=inp.wind_velocity, Q0=inp.Q0,
                       hub_offset_body=inp.hub_offset_body, rotor_axis_body=inp.rotor_axis_body,
                       thrust_speeds=inp.thrust_speeds, thrust_values=inp.thrust_values, mean_thrust=inp.mean_thrust)
        if inp.hub_node_id is None:
            raise FloatingTransientWorkflowError("a turbine needs its structural hub attachment node")
    spec = FloatingTransientSpec(
        times=t, load_components={"wave": F_wave, "external": F_ext}, mass_dry=ops.M6,
        added_mass_infinite=inp.added_mass_infinite, radiation_kernel=inp.radiation_kernel,
        kernel_horizon=inp.kernel_horizon, external_stiffness=ops.K_hs + ops.K_g + K_m + ops.K_ext_mean,
        external_damping=C_m,
        external_load_tangent=G, hydro_admission=inp.hydro_admission, admission_omegas=inp.admission_omegas,
        native_source_binding=None if inp.native_source_binding is None else dict(inp.native_source_binding),
        identity={"job": "floating-transient", "mass_digest": inp.mass_digest}, **turbine)
    if progress_callback is not None:
        progress_callback("trajectory", 0, n_t)
    traj = solve_floating_transient(spec, cancellation_token=cancellation_token, resource_config=rigid_config,
                                    progress_callback=None if progress_callback is None
                                    else (lambda i, n: progress_callback("trajectory", i, n)))
    report = traj.hydrodynamic_admission_report
    verification = inp.hydrodynamic_verification
    unpassed = list(inp.unpassed_hydro_gates) + [g for g in bound_failures if g not in inp.unpassed_hydro_gates]
    if report is not None:
        unpassed += [g for g in report.get("unpassed_gates", ()) if g not in unpassed]
        if report.get("status") == "admitted_development_unverified":
            verification = HYDRO_UNVERIFIED
    diagnostics = dict(trajectory_status=traj.status, trajectory_message=traj.message,
                       max_rotation=traj.max_rotation, max_scaled_residual=traj.max_scaled_residual,
                       negative_damping_steps=traj.negative_damping_steps,
                       hydrodynamic_admission=None if report is None else json.loads(_canonical(dict(report))),
                       K_weight=ops.K_g.tolist(), K_hydrostatic=ops.K_hs.tolist(), M_dry=ops.M6.tolist())
    identities = dict(inp.identities, input_hash=inp.content_hash(), field_kernel_hash=inp.field_kernel_hash,
                      mass_digest=inp.mass_digest, kernel_digest=traj.kernel_digest, ainf_digest=traj.ainf_digest,
                      structural_digest="" if fe_model is None else structural_identity(fe_model))

    meta_arrays = {"mean_hydrostatic_nodal": inp.mean_hydrostatic_nodal}
    for name in ("mean_panel_pressure", "wind_times", "wind_velocity", "wind_reference_mean_world", "thrust_speeds",
                 "thrust_values", "hub_offset_body", "rotor_axis_body", "ext_times", "ext_values", "ext_mean_values"):
        if getattr(inp, name) is not None:
            meta_arrays[name] = getattr(inp, name)        # the ACTUAL realization / curve / attachment data
    diagnostics["static_mean_balance"] = static
    diagnostics["memory_preflight"] = work
    diagnostics["wave_history"] = None if inp.wave_provenance is None else json.loads(_canonical(
        dict(inp.wave_provenance, label="IMPORTED user wave history (not native-bound)"
             if inp.wave_provenance.get("kind") == "imported" else "native-bound wave history")))
    diagnostics["stress_label"] = ("quasistatic elastic stresses of the residual-relieved effective load per frame "
                                   "(not a dynamic modal stress)")
    diagnostics["mean_equilibrium"] = None if inp.mean_equilibrium is None else json.loads(
        _canonical(dict(inp.mean_equilibrium)))

    def finish(status, message, frame_idx=(), loads=None, disp=None, corr=None, stress=None):
        n_f = len(frame_idx)
        empty_loads = {k: np.zeros((0, ops.n, 6)) for k in ("pressure", "weight", "mooring", "turbine", "external",
                                                            "inertia", "perturbation_total", "total", *mean)}
        return FloatingTransientResult(
            status=status, message=message, hydrodynamic_verification=verification, unpassed_gates=tuple(unpassed),
            times=traj.times, displacements=traj.displacements, velocities=traj.velocities,
            accelerations=traj.accelerations, thrust=traj.thrust, relative_axial_speed=traj.relative_axial_speed,
            frame_indices=np.asarray(frame_idx, dtype=np.int64), frame_loads=loads if loads is not None else empty_loads,
            frame_displacements=np.zeros((n_f, 0)) if disp is None else disp,
            frame_correction_acceleration=np.zeros((n_f, 6)) if corr is None else corr,
            diagnostics=diagnostics, identities=identities,
            frame_stresses=np.zeros((n_f, 0)) if stress is None else stress, metadata_arrays=meta_arrays)

    if not traj.complete:
        return finish(traj.status, f"trajectory non-success: {traj.message}")
    q, v, a = np.asarray(traj.displacements), np.asarray(traj.velocities), np.asarray(traj.accelerations)

    # ---- every-sample guards (independent of the structural window)
    if moor is not None:
        bad = [k for k in range(n_t) if np.any(np.abs(q[k]) > inp.mooring_validity)]
        diagnostics["mooring_envelope_violations"] = len(bad)
        if bad:
            return finish("mooring_envelope_exceeded", f"pose leaves the mooring validity envelope at sample {bad[0]}")
    if inp.waterline_points_body is not None:
        eta = np.asarray(field_history_from_transient(field_kernel, traj, "elevation", limits=field_limits))
        if eta.shape[1] != inp.waterline_points_body.shape[0]:
            raise FloatingTransientWorkflowError("field-kernel query points differ from the waterline guard points")
        if inp.incident_elevation is not None:
            eta = eta + inp.incident_elevation
        p = inp.waterline_points_body
        # WORLD vertical displacement of the body point p (CG-relative, body axes): e_z . Q0 (q_t + theta x p)
        #   = q_t . ez_b + theta . (p x ez_b),  ez_b = Q0^T e_z (the body coordinates of the world vertical)
        ez_b = inp.Q0[2]
        wz = q[:, :3] @ ez_b[:, None] + q[:, 3:] @ np.cross(p, ez_b[None, :]).T
        margins = inp.waterline_limit[None, :] - np.abs(eta - wz)
        bound = np.zeros((p.shape[0], 6)) if inp.waterline_bound is None else inp.waterline_bound
        guard = guarded_clearance(bound, a, margins, max_work_bytes=int(field_limits.max_work_bytes))
        diagnostics["waterline"] = dict(samples=int(n_t), min_guarded_margin=float(np.min(guard.guarded_margin)),
                                        max_envelope=float(np.max(guard.envelope)), status=guard.status,
                                        bound_envelope_included=inp.waterline_bound is not None)
        if not guard.is_clear:
            return finish("waterline_guard_failed",
                          f"relative waterline clearance (with bound envelope) consumed at {guard.first_consumed}")

    # ---- structural frames (output window only; physics already complete)
    p_rad = np.asarray(field_history_from_transient(field_kernel, traj, "pressure", limits=field_limits))
    # loading spectrum over ALL integration samples and ALL physical histories (never the selected frames only)
    chans = [("rigid_displacement", q), ("rigid_acceleration", a), ("thrust", np.asarray(traj.thrust, float)),
             ("radiation_panel_pressure", p_rad), ("mooring_generalised_force", q @ K_m.T + v @ C_m.T)]
    if inp.wave_pressure is not None:
        chans.append(("wave_panel_pressure", inp.wave_pressure))
    if ext is not None:
        vals_all = np.asarray(ext.at(t), float)
        chans.append(("external_perturbation", vals_all))
        chans.append(("external_world_rotation_products",
                      (vals_all[:, :, None] * q[:, None, 3:]).reshape(n_t, -1)))
    loading = loading_spectrum(t, chans)
    frames = inp.frame_indices
    comp = {k: np.zeros((frames.size, ops.n, 6)) for k in ("pressure", "weight", "mooring", "turbine", "external",
                                                           "inertia", "perturbation_total", "total")}
    for name, value in mean.items():                       # constant static mean components, kept separately
        comp[name] = np.broadcast_to(value, (frames.size, ops.n, 6)).copy()
    closure_reports = []
    from .floating_loads import dynamic_closure
    for f_i, k in enumerate(frames):
        pressure = p_rad[k]                                # piecewise-constant radiation panel field (own mapping)
        if inp.wave_pressure is not None:
            pressure = pressure + inp.wave_pressure[k]
        comp["pressure"][f_i] = (np.einsum("nkp,p->nk", T, pressure)
                                 + np.einsum("j,jnk->nk", q[k], ops.hs_unit))   # exact hydrostatic perturbation
        comp["weight"][f_i] = np.einsum("j,jnk->nk", q[k], ops.weight_unit)
        if moor is not None:
            forces = moor.fairlead_forces(q[k], v[k])
            for fl in inp.fairleads:
                f0 = inp.Q0.T @ np.asarray(fl["pretension_world"], float)
                comp["mooring"][f_i] += ops.point_load(int(fl["node_id"]), np.asarray(fl["point_body"], float),
                                                       forces[str(fl["identity"])] - f0)
        if turbine:
            n_ax = inp.rotor_axis_body
            comp["turbine"][f_i] = ops.point_load(int(inp.hub_node_id), inp.hub_offset_body,
                                                  n_ax * (float(traj.thrust[k]) - float(inp.mean_thrust)))
        if ext is not None:
            vals = ext.at([t[k]])[0]
            theta = q[k, 3:]
            for c, compname in enumerate(ext.components):
                e = np.zeros(3); e["XYZ".index(compname[1])] = 1.0
                if ext.frames[c] == "world":
                    e = inp.Q0.T @ e
                    e = e + np.cross(e, theta)                     # first-order pull-back of a world-fixed direction
                vec = vals[c] * e
                if compname[0] == "F":
                    comp["external"][f_i] += ops.point_load(int(inp.ext_node_ids[c]), ext.points_body[c], vec)
                else:
                    comp["external"][f_i] += ops.point_load(int(inp.ext_node_ids[c]), ext.points_body[c],
                                                            np.zeros(3), moment=vec)
        # convention: ext_values are PERTURBATIONS about ext_mean_values; the world-fixed MEAN load rotates in body
        # axes (mean * e x theta) - the same nodal term as K_ext_mean in the ODE; the mean itself stays in mean_external
        comp["external"][f_i] += np.einsum("j,jnk->nk", q[k], ops.ext_mean_unit)
        comp["inertia"][f_i] = -ops.mass_nodal(np.einsum("nkj,j->nk", R, a[k]))
        applied = comp["pressure"][f_i] + comp["mooring"][f_i] + comp["turbine"][f_i] + comp["external"][f_i]
        rep = dynamic_closure(ops.points, ops.cg, inp.length_scale, applied, comp["weight"][f_i], comp["inertia"][f_i])
        closure_reports.append(dict(time=float(t[k]), passed=bool(rep["passed"]),
                                    worst=float(np.max(rep["residual"] / np.maximum(rep["allowed"], 1e-300)))))
        comp["perturbation_total"][f_i] = applied + comp["weight"][f_i] + comp["inertia"][f_i]
        # TOTAL physical frame = static mean + perturbation (which already holds -M a_primary); gravity and the mean
        # external loads enter once each (mean terms only in 'mean_*', perturbation terms only in the perturbation)
        comp["total"][f_i] = mean["mean_total"] + comp["perturbation_total"][f_i]
    diagnostics["closure"] = closure_reports
    if not all(r["passed"] for r in closure_reports):
        worst = max(closure_reports, key=lambda r: r["worst"])
        return finish("closure_failed", f"owner dynamic closure failed at t={worst['time']} (worst {worst['worst']:.3g}); "
                      "refused before residual relief", frames, comp)

    # ---- residual inertia relief (existing owner route)
    if relief is None:
        if fe_model is None:
            raise FloatingTransientWorkflowError("residual relief needs the FE model")
        from ..solve.floating_batch import iter_residual_relief
        relief = lambda node_ids, fr, **kw: iter_residual_relief(fe_model, node_ids, fr, **kw)
    from ..solve.floating_batch import FloatingFrameInput
    frame_inputs = [FloatingFrameInput(f"t{int(k)}", float(t[k]), comp["total"][i],
                                       np.einsum("nkj,j->nk", R, a[k])) for i, k in enumerate(frames)]
    disp, corr, relief_summaries, stresses = [], [], [], []
    kw = dict(batch_size=batch_size, output_dofs=output_dofs, cancellation_token=cancellation_token)
    if session is not None:
        kw["session"] = session                             # explicit caller-owned session reuse
    if resource_config is not None:
        kw["resource_config"] = resource_config            # owner static-batch resource limits (may only tighten)
    if recover_stresses:
        from anysolver.recovery import RecoveryConfig
        kw["recovery_config"] = RecoveryConfig(include_reactions=False, include_displacements=True,
                                               include_stresses=True)
    try:
        for out in relief(inp.node_ids, frame_inputs, **kw):
            disp.append(np.asarray(out.displacements, float))
            corr.append(np.r_[np.asarray(out.correction_acceleration_cm, float),
                              np.asarray(out.correction_angular_acceleration, float)])
            relief_summaries.append(json.loads(_canonical(dict(out.relief))))
            stresses.append(getattr(out, "element_stresses", None))
            if progress_callback is not None:
                progress_callback("relief", len(disp), frames.size)
    except Exception as exc:                               # cancellation / solver refusal: explicit non-success
        try:
            from anysolver import SolveCancelled as _OwnerCancelled
        except ImportError:                                # pragma: no cover
            _OwnerCancelled = ()
        token_cancelled = cancellation_token is not None and bool(getattr(cancellation_token, "is_cancelled", False))
        kind = ("cancelled" if (_OwnerCancelled and isinstance(exc, _OwnerCancelled)) or token_cancelled
                or "cancel" in type(exc).__name__.lower() else "relief_failed")
        diagnostics["relief_error"] = f"{type(exc).__name__}: {exc}"
        return finish(kind, f"residual relief stopped: {exc}", frames, comp)
    diagnostics["relief"] = relief_summaries
    if len(disp) != frames.size:
        return finish("relief_failed", "residual relief returned fewer frames than requested", frames, comp)
    flat, layout = flatten_stresses(stresses)
    if stress_values is not None and flat.shape[1] > stress_values:
        return finish("resource_bound_exceeded", f"recovered stress layout {flat.shape[1]} values per frame exceeds the "
                      f"owner-contract preflight count {stress_values}; nothing stored", frames, comp)
    diagnostics["stress_layout"] = layout
    diagnostics["displacement_layout"] = dict(
        output_dofs=None if output_dofs is None else [int(d) for d in np.asarray(output_dofs).ravel()],
        meaning="residual-relief elastic displacements in the owner output-DOF order (all model DOFs when "
                "output_dofs is None); gauge-fixed, quasistatic")
    diagnostics["stress_label"] = ("quasistatic effective-load element stresses of the relieved TOTAL frame load "
                                   "(mean + perturbation - M a_primary); no structural dynamics")
    status = STATUS_COMPLETE if verification == HYDRO_ACCEPTED else STATUS_UNVERIFIED
    msg = ("all gates passed" if status == STATUS_COMPLETE else
           f"connected method completed on UNVERIFIED hydrodynamics; unpassed gates: {unpassed}")
    screen = elastic_screening(screening, fe_model, traj.times, a, traj.thrust, spectrum=loading)
    diagnostics["elastic_screening"] = screen
    if screen["status"] == "reported" and screen.get("indicator") == "attention":
        # parent adoption: an attention indicator is never accepted structural output; histories / stresses stay
        # inspectable, labelled quasistatic-with-attention; no dynamic-accuracy claim either way
        if "elastic_screening_attention" not in unpassed:
            unpassed.append("elastic_screening_attention")
        if status == STATUS_COMPLETE:
            status = STATUS_SCREENING_ATTENTION
            msg = ("connected method completed; the heuristic elastic-frequency / loading-band indicator flags "
                   "ATTENTION - quasistatic stresses are inspectable but NOT accepted")
    if screen["status"] != "reported":
        gate = f"elastic_screening_{screen['status']}"
        if gate not in unpassed:
            unpassed.append(gate)
        if status == STATUS_COMPLETE:
            status = STATUS_NOT_SCREENED
            msg = (f"connected method completed but the elastic-frequency / loading-bandwidth report is "
                   f"{screen['status']} ({screen.get('reason', '')}); quasistatic stresses are NOT accepted")
    if inp.waterline_guard_waiver:
        status = STATUS_SYNTHETIC_UNGUARDED
        msg = (f"synthetic fixture without the physical waterline guard ({inp.waterline_guard_waiver}); "
               "NOT an accepted or development physical result")
    corr_arr = np.stack(corr)
    meta_arrays["frame_primary_acceleration_cg"] = a[frames]
    meta_arrays["frame_total_acceleration_cg"] = a[frames] + corr_arr
    return finish(status, msg, frames, comp, np.stack(disp), corr_arr, flat)


def flatten_stresses(per_frame: Sequence[Any]) -> tuple:
    """(n_frames, n_values) array + layout [[element_id, key, size], ...] of numeric element stress results.
    Frames without stresses give an empty (n_frames, 0) array; a changing layout is refused."""
    if not per_frame or all(s is None for s in per_frame):
        return np.zeros((len(per_frame), 0)), []
    layout, rows = None, []
    for s in per_frame:
        entries, row = [], []
        for eid in sorted((s or {}), key=lambda x: str(x)):
            value = s[eid]
            items = sorted(value.items()) if isinstance(value, Mapping) else [("value", value)]
            for key, v in items:
                try:
                    arr = np.asarray(v, dtype=np.float64).ravel()
                except (TypeError, ValueError):
                    continue
                entries.append([str(eid), str(key), int(arr.size)])
                row.append(arr)
        if layout is None:
            layout = entries
        elif entries != layout:
            raise FloatingTransientWorkflowError("element stress layout changed between frames")
        rows.append(np.concatenate(row) if row else np.zeros(0))
    return np.stack(rows), layout
