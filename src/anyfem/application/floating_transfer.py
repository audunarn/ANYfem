"""Orchestration: retained floating family + response -> mapped coupled load history on the stationary mesh.

Geometry-owned panel/element mapping and consistent nodal assembly are INJECTED callables (``map_pressure``,
``direct_hydrostatic_nodal``); this module owns the numerics around them: source closure, response admission,
direct p0/perturbation evaluation, weight and primary inertia, mapped closure, and the immutable history.  No BEM
and no motion simulation happens here; unchanged inputs make this a pure post-processing step.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

import numpy as np

from .floating_loads import (FloatingLoadError, NodalDofMap, compose_component, dynamic_closure, SOURCE_CLOSURE_REL)


def _fr():
    from anysolver import floating_response
    return floating_response


def source_dynamic_closure(omega: float, hydro_wrench_body: Any, Hq: Any, mass6: Any, restoring6: Any, *,
                           tolerance: float = SOURCE_CLOSURE_REL) -> dict:
    """Source-panel identity -w^2 M Hq = W_hydro_dyn - C Hq (mean body axes, about mean CG).

    ``hydro_wrench_body`` is the wrench of pI+pD+sum Hq_j pR on the SOURCE panels integrated with the same
    quadrature as C.  Relative to the largest term; numerical zero (all terms ~0) is handled as closed.
    """
    W = np.asarray(hydro_wrench_body, dtype=np.complex128)
    h = np.asarray(Hq, dtype=np.complex128)
    inertia = -float(omega) ** 2 * (np.asarray(mass6) @ h)
    stiff = np.asarray(restoring6) @ h
    resid = inertia - (W - stiff)
    scale = max(float(np.max(np.abs(W))), float(np.max(np.abs(stiff))), float(np.max(np.abs(inertia))))
    rel = 0.0 if scale == 0.0 else float(np.max(np.abs(resid)) / scale)
    return {'relative': rel, 'scale': scale, 'tolerance': float(tolerance), 'passed': rel <= float(tolerance)}


def compose_floating_components(*, points: Any, mass_apply: Callable, dofmap: NodalDofMap, cg_world: Any, Q0: Any,
                                gravity: float, rho: float, omegas: Sequence[float], Hq: Any,
                                source_hydrodynamic: Callable[[int, Any], Any],
                                direct_hydrostatic_nodal: Callable[[int, Any], Any],
                                length: float, enforce_closure: bool = True, fe_frame: str = 'world') -> dict:
    """Per-frequency component loads, each (nk, n, 6) complex per unit amplitude.

    ``source_hydrodynamic(k, Hq_k)`` -> mapped nodal load (n,6) of pI+pD+sum Hq_j pR via geometry-owned conservative
    overlap (signed pressures, no global rescale).  ``direct_hydrostatic_nodal(k, Hq_k)`` assembles the perturbation
    -rho g ez.Q0(t+theta x r) evaluated DIRECTLY on the structural wet cells (never from coarse source panels).
    The production pipeline (``floating_prepare``) supplies both from its own overlap map and FE assembly.
    """
    fr = _fr()
    H = np.asarray(Hq, dtype=np.complex128)
    w = np.asarray(omegas, dtype=np.float64)
    if H.shape != (w.size, 6):
        raise FloatingLoadError('Hq must be (nk,6)')
    out = {'pressure': [], 'weight': [], 'inertia': [], 'acceleration': []}
    reports = []
    for k in range(w.size):
        hyd = np.asarray(source_hydrodynamic(k, H[k]), dtype=np.complex128)
        hs = np.asarray(direct_hydrostatic_nodal(k, H[k]), dtype=np.complex128)
        comp = compose_component(points, mass_apply, dofmap, cg_world=cg_world, Q0=Q0, gravity=gravity, omega=float(w[k]),
                                 Hq=H[k], hydrodynamic_nodal=hyd, hydrostatic_nodal=hs, length=length,
                                 enforce_closure=enforce_closure, fe_frame=fe_frame)
        out['pressure'].append(comp.pressure); out['weight'].append(comp.weight)
        out['inertia'].append(comp.inertia); out['acceleration'].append(comp.acceleration)
        reports.append(comp.closure)
    result = {k: np.stack(v) for k, v in out.items()}
    result['closure'] = reports
    return result
