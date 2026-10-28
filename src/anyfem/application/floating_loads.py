"""Coupled floating-body load composition on the STATIONARY FE mesh (no motion simulation).

Per spectral component the loads are pressure (mapped hydrodynamic + direct hydrostatic perturbation), weight
perturbation delta_g = -theta x g0 on the DRY FE mass, and primary inertia -M a with a = -w^2 R(r) Hq (angular nodal
DOFs included).  Closure is judged per frequency/component against the owner criterion
``abs(r_i) <= 0.01 * max(d_i, 0.1 * S)``.  Everything here is numeric on nodal (n,6) arrays; mass is applied through a
caller supplied operator so the DRY assembled FE mass is the only mass (no added mass twice).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

CLOSURE_REL = 0.01
CLOSURE_FLOOR = 0.1
SOURCE_CLOSURE_REL = 1e-8


class FloatingLoadError(ValueError):
    """Loads cannot be composed or do not close; nothing was rescaled."""


def _fr():
    from anysolver import floating_response
    return floating_response


@dataclass(frozen=True)
class NodalDofMap:
    """(n_nodes, 6) global DOF indices into the assembled system; -1 marks an absent DOF (e.g. solid rotations)."""
    index: np.ndarray
    ndof: int

    def __post_init__(self):
        idx = np.array(self.index, dtype=np.intp, copy=True)
        if idx.ndim != 2 or idx.shape[1] != 6 or int(self.ndof) <= 0:
            raise FloatingLoadError('DOF map must be (n,6)')
        used = idx[idx >= 0]
        if used.size and (used.max() >= self.ndof or len(np.unique(used)) != used.size):
            raise FloatingLoadError('DOF map must be unique and inside the system')
        idx.setflags(write=False)
        object.__setattr__(self, 'index', idx)
        object.__setattr__(self, 'ndof', int(self.ndof))

    def scatter(self, nodal: Any) -> np.ndarray:
        a = np.asarray(nodal)
        if a.shape != self.index.shape:
            raise FloatingLoadError('nodal array shape disagrees with DOF map')
        out = np.zeros(self.ndof, dtype=a.dtype)
        m = self.index >= 0
        out[self.index[m]] = a[m]
        return out

    def gather(self, full: Any) -> np.ndarray:
        v = np.asarray(full)
        if v.shape != (self.ndof,):
            raise FloatingLoadError('full vector length disagrees with DOF map')
        out = np.zeros(self.index.shape, dtype=v.dtype)
        m = self.index >= 0
        out[m] = v[self.index[m]]
        return out


def nodal_wrench(points: Any, nodal: Any, origin: Any) -> np.ndarray:
    """Resultant (Fx,Fy,Fz,Mx,My,Mz) of nodal forces/moments about ``origin`` (global axes); complex allowed."""
    P = np.asarray(points, dtype=np.float64)
    L = np.asarray(nodal)
    r = P - np.asarray(origin, dtype=np.float64).reshape(3)
    force = L[:, :3].sum(axis=0)
    moment = L[:, 3:].sum(axis=0) + np.cross(r, L[:, :3]).sum(axis=0)
    return np.concatenate([force, moment])


def _scaled(w: np.ndarray, length: float) -> np.ndarray:
    out = np.array(w, copy=True)
    out[..., 3:] = out[..., 3:] / length
    return out


def dynamic_closure(points: Any, cg_world: Any, length: float, pressure: Any, weight: Any, inertia: Any, *,
                    force_scale_floor: float = 0.0) -> dict:
    """Owner closure criterion for ONE complex frequency component (nodal (n,6) loads, global axes, about CG).

    Moment components are divided by ``length``.  d_i = max(|Wp|,|Wg|,|Wi|), S = max d_i; if every generalized
    dynamic component vanishes but distributed loads remain, S = max component of the summed MAGNITUDES of the local
    scaled wrenches.  Static weight is never in the denominator.  Returns per-component residuals, allowances, pass.
    """
    L = float(length)
    if not np.isfinite(L) or L <= 0.0:
        raise FloatingLoadError('moment length scale must be positive')
    P = np.asarray(points, dtype=np.float64)
    parts = [np.asarray(x, dtype=np.complex128) for x in (pressure, weight, inertia)]
    W = [_scaled(nodal_wrench(P, x, cg_world), L) for x in parts]
    d = np.max(np.abs(np.stack(W)), axis=0)
    S = float(d.max())
    if S == 0.0:
        r = P - np.asarray(cg_world, dtype=np.float64).reshape(3)
        local = np.zeros(6)
        for x in parts:
            w = np.concatenate([x[:, :3], x[:, 3:] + np.cross(r, x[:, :3])], axis=1)
            local += np.abs(_scaled(w, L)).sum(axis=0)
        S = float(local.max())
    S = max(S, float(force_scale_floor))
    resid = W[0] + W[1] + W[2]
    allowed = CLOSURE_REL * np.maximum(d, CLOSURE_FLOOR * S)
    ok = bool(np.all(np.abs(resid) <= allowed))
    return {'residual': np.abs(resid), 'allowed': allowed, 'scale_S': S, 'd': d,
            'global_norm': float(np.linalg.norm(resid)), 'passed': ok,
            'criterion': 'abs(r_i)<=0.01*max(d_i,0.1*S); moments/L'}


def require_closure(report: dict, context: str = '') -> None:
    if not report['passed']:
        worst = float(np.max(report['residual'] / np.maximum(report['allowed'], 1e-300)))
        raise FloatingLoadError(f'dynamic closure failed {context}: worst residual/allowed = {worst:.3g}')


def primary_acceleration_nodal(points: Any, cg_world: Any, Q0: Any, Hq: Any, omega: float) -> np.ndarray:
    """(n,6) complex primary acceleration -w^2 R(r) Hq: translation then angular (global axes)."""
    a = _fr().rigid_acceleration_world(points, cg_world, Q0, Hq, omega)
    return np.concatenate([a['translation'], a['rotation']], axis=1)


def weight_perturbation_nodal(mass_apply: Callable[[np.ndarray], np.ndarray], dofmap: NodalDofMap, Q0: Any,
                              gravity: float, Hq: Any, *, body_frame: bool = False) -> np.ndarray:
    """M_FE * delta_g with delta_g = -theta x g0 (body); DRY mass only; (n,6) complex.

    ``body_frame=False``: nodal components in WORLD axes (delta_g rotated by Q0).  ``True``: the FE model lives in the
    mean body (project) axes, so delta_g stays in body components.
    """
    fr = _fr()
    Q = fr.rotation_matrix(Q0)
    dg_body = fr.perturbed_gravity_body(Q, gravity, np.asarray(Hq)[3:])
    dg_world = dg_body if body_frame else Q @ dg_body
    n = dofmap.index.shape[0]
    acc = np.zeros((n, 6), dtype=np.complex128)
    acc[:, :3] = dg_world[None, :]
    return dofmap.gather(np.asarray(mass_apply(dofmap.scatter(acc)), dtype=np.complex128))


def static_weight_nodal(mass_apply: Callable[[np.ndarray], np.ndarray], dofmap: NodalDofMap,
                        gravity: float, g_vector: Any = None) -> np.ndarray:
    """M_FE * g (real (n,6)); ``g_vector`` is the gravity vector in the FE axes (default world (0,0,-g))."""
    acc = np.zeros(dofmap.index.shape)
    acc[:, :3] = (np.array([0.0, 0.0, -float(gravity)]) if g_vector is None else np.asarray(g_vector, dtype=float))[None, :]
    return dofmap.gather(np.asarray(mass_apply(dofmap.scatter(acc)), dtype=np.float64))


def primary_inertia_nodal(mass_apply: Callable[[np.ndarray], np.ndarray], dofmap: NodalDofMap,
                          acceleration: Any) -> np.ndarray:
    """-M_FE a_primary (dry M only)."""
    return -dofmap.gather(np.asarray(mass_apply(dofmap.scatter(np.asarray(acceleration, dtype=np.complex128))),
                                     dtype=np.complex128))


@dataclass(frozen=True)
class FloatingComponentLoads:
    pressure: np.ndarray     # (n,6) complex: mapped hydrodynamic + direct hydrostatic perturbation
    weight: np.ndarray
    inertia: np.ndarray
    acceleration: np.ndarray
    closure: dict

    @property
    def total(self) -> np.ndarray:
        return self.pressure + self.weight + self.inertia


def compose_component(points: Any, mass_apply: Callable, dofmap: NodalDofMap, *, cg_world: Any, Q0: Any,
                      gravity: float, omega: float, Hq: Any, hydrodynamic_nodal: Any, hydrostatic_nodal: Any,
                      length: float, enforce_closure: bool = True, fe_frame: str = 'world') -> FloatingComponentLoads:
    """One component: p = mapped(pI+pD+sum Hq_j pR) + direct hydrostatic perturbation; + weight + primary inertia.

    ``fe_frame='body'``: ``points``/``cg_world`` are in the FE (mean body) axes, so the rigid motion is
    u = t_b + theta_b x r_b with Hq already in those axes (Q0 only rotates gravity).
    """
    if fe_frame not in ('world', 'body'):
        raise FloatingLoadError("fe_frame must be 'world' or 'body'")
    body = fe_frame == 'body'
    acc = primary_acceleration_nodal(points, cg_world, np.eye(3) if body else Q0, Hq, omega)
    Fp = np.asarray(hydrodynamic_nodal, dtype=np.complex128) + np.asarray(hydrostatic_nodal, dtype=np.complex128)
    Fg = weight_perturbation_nodal(mass_apply, dofmap, Q0, gravity, Hq, body_frame=body)
    Fi = primary_inertia_nodal(mass_apply, dofmap, acc)
    rep = dynamic_closure(points, cg_world, length, Fp, Fg, Fi)
    if enforce_closure:
        require_closure(rep, f'at omega={omega:.6g}')
    return FloatingComponentLoads(Fp, Fg, Fi, acc, rep)


def synthesize_real(loads: Any, amplitude: Any, phase: Any, omega: Any, time: float) -> np.ndarray:
    """Real nodal load at an actual time from (nk,n,6) complex per-unit-amplitude loads (exp(-i w t))."""
    z = (np.asarray(amplitude, dtype=np.float64) * np.exp(1j * np.asarray(phase, dtype=np.float64))
         * np.exp(-1j * np.asarray(omega, dtype=np.float64) * float(time)))
    return np.real(np.tensordot(z, np.asarray(loads), axes=(0, 0)))
