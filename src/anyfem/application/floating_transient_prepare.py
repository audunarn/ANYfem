"""Coarse-source preparation for the floating transient workflow (development, UNQUALIFIED).

Everything here is derived from ONE supplied source manifest (the fixed coarse BEM panelisation; never remeshed or
refined here) and the structural wet target at ONE mean pose, through the existing owners:

* ``floating_source.prepare_target_cells`` / ``build_overlap_map`` (geometry-owner clipping, actual overlap polygons);
* the solver owner's region integration (``anysolver.floating_pressure_regions.region_nodal_vectors``) exactly as
  ``floating_source.map_pressure_nodal`` uses it, once per (target cell, source panel) overlap pair, so the conservative
  panel operator ``T`` (n_nodes, 6, n_panels) satisfies ``T @ p == map_pressure_nodal(p)`` for every panel-constant
  pressure ``p`` (checked on the mean pressure before the operator is returned);
* the mean hydrostatic panel pressure ``rho g max(-z_world, 0)`` at the source panel centres and its rigid
  perturbation operator ``Hs`` (n_panels, 6): ``dp_k = -rho g e_z^T Q0 [I, -[r_k]x] q`` with ``r_k`` the panel centre
  about the dry CG in body axes (generalized coordinates of the transient solver).

Source panel ids are ``f"{k}:{surface_id}"`` in source order; a supplied field kernel must carry exactly these ids and the
same query-point count, otherwise preparation refuses (no reordering, no nearest matching).  Resources are admitted
before the operator is allocated (600 panels, 256 MiB operator).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Optional

import numpy as np

from .floating_source import (MeanPose, OverlapMap, SourceManifest, TargetCells, build_overlap_map,
                              map_pressure_nodal, prepare_target_cells)

MAX_SOURCE_PANELS = 600
MAX_OPERATOR_BYTES = 256 * 1024 ** 2
PREPARE_FORMAT = "anyfem-floating-transient-coarse-prepare-v1"


class FloatingTransientPrepareError(ValueError):
    """Coarse preparation refused (never repaired silently)."""


def _digest(*arrays, meta=None) -> str:
    h = hashlib.sha256(json.dumps(meta or {}, sort_keys=True, default=str).encode())
    for a in arrays:
        a = np.ascontiguousarray(np.asarray(a, dtype=np.float64))
        h.update(str(a.shape).encode())
        h.update(a.tobytes())
    return h.hexdigest()


@dataclass(frozen=True)
class CoarseTransientOperators:
    """Operators of one coarse source at one mean pose (all body frame, about the dry CG)."""

    node_ids: tuple
    panel_ids: tuple
    panel_to_nodal: np.ndarray          # (n_nodes, 6, n_p): nodal wrench per unit panel pressure
    mean_panel_pressure: np.ndarray     # (n_p,) rho g max(-z_world, 0) at the panel centres
    hydrostatic_pressure: np.ndarray    # (n_p, 6) rigid perturbation of the panel pressure
    waterline_points_body: np.ndarray   # (n_q, 3) body axes relative to the dry CG, or (0, 3)
    pose_Q0: np.ndarray
    pose_t: np.ndarray
    cg_body: np.ndarray
    identities: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    mean_hydrostatic_nodal: Optional[np.ndarray] = None   # (n_nodes, 6) exact wet-region quadrature of rho g max(-z,0)
    hydrostatic_nodal: Optional[np.ndarray] = None        # (6, n_nodes, 6) exact quadrature of the rigid gradient
    waterline_point_ids: tuple = ()

    def input_fields(self) -> dict:
        """Keyword fields of ``FloatingTransientInput`` produced by preparation (the rest comes from hydro/mean)."""
        if self.mean_hydrostatic_nodal is None or self.hydrostatic_nodal is None:
            raise FloatingTransientPrepareError("operators lack the exact nodal hydrostatics")
        out = dict(node_ids=tuple(int(n) for n in self.node_ids), panel_to_nodal=self.panel_to_nodal,
                   hydrostatic_pressure=self.hydrostatic_pressure, mean_panel_pressure=self.mean_panel_pressure,
                   mean_hydrostatic_nodal=self.mean_hydrostatic_nodal, hydrostatic_nodal=self.hydrostatic_nodal,
                   Q0=self.pose_Q0, cg_body=self.cg_body)
        if self.waterline_points_body.shape[0]:
            out["waterline_points_body"] = self.waterline_points_body
            out["waterline_point_ids"] = tuple(self.waterline_point_ids)
        return out


def source_panel_ids(source: SourceManifest) -> tuple:
    return tuple(f"{k}:{s}" for k, s in enumerate(source.surface_ids))


def panel_operator(fe_model, node_ids, target: TargetCells, omap: OverlapMap, n_panels: int,
                   kernel_proof: Optional[dict] = None) -> np.ndarray:
    """(n_nodes, 6, n_panels) operator through the owner region integration, once per overlap pair."""
    from anysolver.floating_pressure_regions import region_nodal_vectors
    n = len(node_ids)
    need = n * 6 * int(n_panels) * 8
    if not 1 <= int(n_panels) <= MAX_SOURCE_PANELS:
        raise FloatingTransientPrepareError(f"{n_panels} source panels outside 1..{MAX_SOURCE_PANELS}")
    if need > MAX_OPERATOR_BYTES:
        raise FloatingTransientPrepareError(f"panel operator needs {need} B > {MAX_OPERATOR_BYTES} B")
    row = {int(nid): i for i, nid in enumerate(node_ids)}
    T = np.zeros((n, 6, int(n_panels)))
    for j, (tris, src) in omap.regions.items():
        element = fe_model.mesh.elements[int(target.element_ids[j])]
        src = np.asarray(src, dtype=np.int64)
        for k in np.unique(src):
            if not 0 <= int(k) < n_panels:
                raise FloatingTransientPrepareError(f"overlap region references source panel {int(k)} outside the source")
            rv = region_nodal_vectors(element, fe_model.mesh, tris,
                                      lambda pts, ti, src=src, k=int(k): (src[ti] == k).astype(float),
                                      sign=float(target.sign[j]), kernel_proof=kernel_proof)
            for nid, vec in zip(rv.node_ids, rv.vectors):
                if int(nid) not in row:
                    raise FloatingTransientPrepareError(f"loaded node {int(nid)} is not in the transient node set")
                T[row[int(nid)], :, int(k)] += np.asarray(vec, dtype=float)
    return T


def mean_hydrostatic(source: SourceManifest, pose: MeanPose, cg_body, *, rho: float, gravity: float):
    """Mean panel pressure (n_p,) and its rigid perturbation operator Hs (n_p, 6), from the same panel centres."""
    c_world = np.asarray(source.centers, dtype=float)
    p0 = rho * gravity * np.maximum(-c_world[:, 2], 0.0)
    r = pose.body(c_world) - np.asarray(cg_body, dtype=float).reshape(3)
    ez = np.asarray(pose.Q0, dtype=float)[2]                     # world z row in body axes
    Hs = np.empty((len(c_world), 6))
    Hs[:, :3] = -rho * gravity * ez[None, :]
    Hs[:, 3:] = -rho * gravity * np.cross(r, ez[None, :])        # e_z.(theta x r) = theta.(r x e_z)
    return p0, Hs


def prepare_coarse_transient(project, mesh, fe_model, node_ids, source: SourceManifest, pose: MeanPose, cg_body, *,
                             rho: float, gravity: float, hydro_identity: Any, field_kernel: Any = None,
                             mean_identity: Optional[dict] = None, kernel_proof: Optional[dict] = None,
                             pose_tol: float = 1e-9,
                             _target: Optional[TargetCells] = None, _omap: Optional[OverlapMap] = None
                             ) -> CoarseTransientOperators:
    """Prepare the coarse operators on the SUPPLIED source at the supplied mean pose (no BEM, no remesh).

    ``hydro_identity`` is the ACTUAL provenance of the cached hydro family (``DevelopmentCoarseSource.identity`` or the
    equivalent accepted record): ``family_Q0`` / ``family_cg_world`` (the posed family request), ``panel_ids``,
    ``query_point_ids`` and ``query_points_world``.  A family posed elsewhere than this mean pose is STALE and refused
    (never silently reused; a re-posed family needs a new hydro preparation, which this module never runs)."""
    if not (np.isfinite(rho) and rho > 0 and np.isfinite(gravity) and gravity > 0):
        raise FloatingTransientPrepareError("rho and gravity must be positive and finite")
    from anyhydro.floating_transient_prepare import family_geometry_digest, query_points_digest
    hid = dict(hydro_identity or {})
    missing = {"family_Q0", "family_cg_world", "panel_ids", "query_point_ids", "query_points_world",
               "query_points_digest", "family_geometry_digest"} - set(hid)
    if missing:
        raise FloatingTransientPrepareError(f"hydro provenance lacks {sorted(missing)} (actual family pose / geometry / "
                                            "query points are required, never caller copies)")
    if not (isinstance(pose_tol, (int, float)) and np.isfinite(pose_tol) and 0.0 < float(pose_tol) <= 1e-9):
        raise FloatingTransientPrepareError("pose_tol must be finite in (0, 1e-9] (may only tighten)")
    Qf, cf = np.asarray(hid["family_Q0"], float), np.asarray(hid["family_cg_world"], float)
    cg_w = pose.world(np.asarray(cg_body, float).reshape(1, 3))[0]
    if Qf.shape != (3, 3) or cf.shape != (3,) or not (np.all(np.isfinite(Qf)) and np.all(np.isfinite(cf))
                                                         and np.all(np.isfinite(cg_w))):
        raise FloatingTransientPrepareError("hydro family pose / mean CG must be finite (3x3, 3)")
    # the supplied source manifest must BE the retained family geometry (same-pose same-id different geometry refuses)
    if family_geometry_digest(source.vertices, source.faces, source.surface_ids) != hid["family_geometry_digest"]:
        raise FloatingTransientPrepareError("the supplied source geometry (vertices / faces / labels) is not the "
                                            "geometry of the retained hydro family")
    # NaN-safe comparisons: 'not (x <= tol)' refuses NaN
    if not float(np.max(np.abs(Qf - np.asarray(pose.Q0, float)))) <= pose_tol or \
            not float(np.max(np.abs(cf - cg_w))) <= pose_tol * max(1.0, float(np.max(np.abs(cg_w)))):
        raise FloatingTransientPrepareError(
            "the cached hydro family was posed at a different pose than this mean (stale family artifact): "
            f"family CG {cf.tolist()} vs mean CG {cg_w.tolist()}; re-pose the family (hydro preparation) before use")
    n_p = int(len(source.areas))
    if not 1 <= n_p <= MAX_SOURCE_PANELS:
        raise FloatingTransientPrepareError(f"{n_p} source panels outside 1..{MAX_SOURCE_PANELS} (no remesh here)")
    need = len(node_ids) * 6 * n_p * 8
    if need > MAX_OPERATOR_BYTES:                                # preflight before target / overlap work
        raise FloatingTransientPrepareError(f"panel operator needs {need} B > {MAX_OPERATOR_BYTES} B")
    pids = source_panel_ids(source)
    if tuple(str(p) for p in hid["panel_ids"]) != pids:
        raise FloatingTransientPrepareError("hydro family panel ids differ from the supplied source panels "
                                            "(the family must belong to this coarse source)")
    q = np.asarray(hid["query_points_world"], dtype=float)
    qids = tuple(str(i) for i in hid["query_point_ids"])
    if q.ndim != 2 or q.shape[1] != 3 or not np.all(np.isfinite(q)) or q.shape[0] != len(qids):
        raise FloatingTransientPrepareError("hydro query points must be finite (n, 3) with one id each")
    if query_points_digest(q) != hid["query_points_digest"]:
        raise FloatingTransientPrepareError("hydro query coordinates differ from their recorded digest")
    if field_kernel is not None:
        if tuple(str(p) for p in field_kernel.panel_ids) != pids:
            raise FloatingTransientPrepareError("field kernel panel ids differ from the supplied source panels "
                                                "(the kernel must belong to this coarse source)")
        if tuple(str(i) for i in field_kernel.query_point_ids) != qids:
            raise FloatingTransientPrepareError("field kernel query-point ids / order differ from the hydro family")
        if "field_kernel_hash" in hid and hid["field_kernel_hash"] != field_kernel.content_hash():
            raise FloatingTransientPrepareError("field kernel is not the one recorded in the hydro provenance")
        fk_geo = dict(getattr(field_kernel, "identity", None) or {}).get("family_geometry_digest")
        if fk_geo != hid["family_geometry_digest"]:
            raise FloatingTransientPrepareError("field kernel does not bind the geometry of this hydro family")
    target = _target if _target is not None else prepare_target_cells(project, mesh, pose)
    omap = _omap if _omap is not None else build_overlap_map(source, target, pose)
    T = panel_operator(fe_model, node_ids, target, omap, n_p, kernel_proof=kernel_proof)
    p0, Hs = mean_hydrostatic(source, pose, cg_body, rho=rho, gravity=gravity)
    # conservative operator: identical to the owner mapping on the actual mean pressure
    ref = np.asarray(map_pressure_nodal(fe_model, node_ids, target, omap, p0, kernel_proof=kernel_proof), dtype=float)
    got = np.einsum("nip,p->ni", T, p0)
    scale = max(float(np.max(np.abs(ref))), np.finfo(float).tiny)
    if float(np.max(np.abs(got - ref))) > 1e-10 * scale:
        raise FloatingTransientPrepareError("panel operator does not reproduce the owner pressure mapping")
    Q0 = np.asarray(pose.Q0, dtype=float); t = np.asarray(pose.t, dtype=float)
    cg = np.asarray(cg_body, dtype=float).reshape(3)
    mh, hn = exact_hydrostatic_nodal(fe_model, node_ids, target, pose, cg, rho=rho, gravity=gravity,
                                     kernel_proof=kernel_proof)
    ident = {"format": PREPARE_FORMAT, "source_digest": source.digest(),
             "hydro_family": {k: hid[k] for k in ("family_Q0", "family_cg_world", "query_point_ids",
                                                  "family_geometry_digest", "query_points_digest")},
             "pose_t": [float(x) for x in np.asarray(pose.t, float)],
             "query_points_digest": _digest(q), "hydrostatic_digest": _digest(mh, hn),
             "target_digest": target.digest() if hasattr(target, "digest") else "",
             "pose_digest": _digest(Q0, t), "cg_digest": _digest(cg), "panel_ids_digest": _digest(meta=list(pids)),
             "operator_digest": _digest(T, p0, Hs), "rho": float(rho), "gravity": float(gravity),
             "source_mode": str(getattr(source, "mode", "source")), "remeshed": False}
    if field_kernel is not None:
        ident["field_kernel_hash"] = field_kernel.content_hash()
    if mean_identity is not None:
        ident["mean_equilibrium"] = dict(mean_identity)
    for a in (T, p0, Hs, mh, hn):
        a.setflags(write=False)
    qb = pose.body(q) - cg if q.shape[0] else q                  # input convention: body axes relative to the dry CG
    return CoarseTransientOperators(node_ids=tuple(int(n) for n in node_ids), panel_ids=pids, panel_to_nodal=T,
                                    mean_panel_pressure=p0, hydrostatic_pressure=Hs, waterline_points_body=qb,
                                    pose_Q0=Q0, pose_t=t, cg_body=cg, identities=ident,
                                    coverage=dict(getattr(omap, "coverage", {}) or {}),
                                    mean_hydrostatic_nodal=mh, hydrostatic_nodal=hn, waterline_point_ids=qids)


def exact_hydrostatic_nodal(fe_model, node_ids, target: TargetCells, pose: MeanPose, cg_body, *, rho: float,
                            gravity: float, kernel_proof: Optional[dict] = None):
    """Mean hydrostatic nodal load and its first-order rigid operator by the owner wet-region quadrature
    (``floating_source.wet_region_nodal``: element pressure virtual work over each mean wet polygon).  Both fields are
    LINEAR in the position on the wet region (p = rho g (-z_world) <= hydrostatic, dp_j = -rho g e_z.Q0 dU_j(x)), so the
    owner's quadrature integrates them exactly, including the within-panel moment and the shape-function distribution.
    The waterline-strip change is second order (p = 0 on the waterline)."""
    from .floating_source import wet_region_nodal
    cg = np.asarray(cg_body, dtype=float).reshape(3)
    ez = np.asarray(pose.Q0, dtype=float)[2]                    # world vertical in body axes
    rg = float(rho) * float(gravity)
    mean = np.asarray(wet_region_nodal(fe_model, node_ids, target, pose,
                                       lambda X: rg * np.maximum(-np.asarray(X, float)[:, 2], 0.0),
                                       kernel_proof=kernel_proof), dtype=float)
    op = np.zeros((6, len(node_ids), 6))
    for j in range(6):
        if j < 3:
            fn = (lambda X, j=j: np.full(len(X), -rg * ez[j]))
        else:
            fn = (lambda X, j=j: -rg * np.cross(pose.body(X) - cg, ez[None, :])[:, j - 3])
        op[j] = np.asarray(wet_region_nodal(fe_model, node_ids, target, pose, fn, kernel_proof=kernel_proof), dtype=float)
    return mean, op


def require_bound_to_mean(ops: CoarseTransientOperators, mean_pose_Q0, mean_pose_t, *, tol: float = 1e-9) -> None:
    """Refuse operators prepared at a different pose than the solved mean (stale until rebound)."""
    dq = float(np.max(np.abs(np.asarray(mean_pose_Q0, dtype=float) - ops.pose_Q0)))
    dt = float(np.max(np.abs(np.asarray(mean_pose_t, dtype=float) - ops.pose_t)))
    if dq > tol or dt > tol * max(1.0, float(np.max(np.abs(ops.pose_t)))):
        raise FloatingTransientPrepareError(
            f"coarse operators were prepared at a different pose than the solved mean (dQ={dq:.3e}, dt={dt:.3e}); "
            "rebind the source/kernel to the mean before an accepted run")
