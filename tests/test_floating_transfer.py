"""Source closure identity and per-frequency composition plumbing (pure numeric)."""
import numpy as np
import pytest

from anyfem.application import floating_loads as fl
from anyfem.application import floating_transfer as ft
from test_floating_loads import cloud, G


def operators():
    M = np.diag([5e5, 5e5, 5e5, 2e6, 8e6, 9e6])
    C = np.zeros((6, 6)); C[2, 2] = 1.2e6; C[3, 3] = 3e6; C[4, 4] = 2e7
    A = np.diag([1e5, 1e5, 4e5, 2e5, 3e6, 3e6]); B = np.diag([1e3, 1e3, 5e4, 1e4, 1e5, 1e5])
    return M, C, A, B


def test_source_closure_identity_and_detects_mutation():
    from anysolver import floating_response as fr
    M, C, A, B = operators()
    w = np.array([0.7])
    F = np.zeros((1, 6), complex); F[0, 2] = 1e5; F[0, 4] = 2e4j
    H = fr.solve_floating_response(w, M, A[None], B[None], C, F, asymmetry_tolerance=1e-12).Hq[0]
    wrench = F[0] + (w[0] ** 2 * A + 1j * w[0] * B) @ H           # Fexc + radiation integrals
    assert ft.source_dynamic_closure(w[0], wrench, H, M, C)["passed"]
    # missing -i w (extra/absent factor) or wrong phase convention must not pass
    bad = F[0] + (w[0] ** 2 * A + B) @ H
    assert not ft.source_dynamic_closure(w[0], bad, H, M, C)["passed"]
    # zero everything is numerically closed
    z = np.zeros(6, complex)
    assert ft.source_dynamic_closure(w[0], z, z, M, C)["passed"]


def test_compose_runs_every_frequency_and_enforces_mapped_closure():
    pts, m, dm, ma = cloud()
    cg = (m[:, None] * pts).sum(0) / m.sum()
    w = np.array([0.8, 1.3])
    H = np.array([[0.0, 0.0, 0.2, 0.01, 0.0, 0.0], [0.0, 0.1, 0.1, 0.0, 0.02, 0.0]], complex)
    seen = []

    def hydro(k, h):
        comp = fl.compose_component(pts, ma, dm, cg_world=cg, Q0=np.eye(3), gravity=G, omega=w[k], Hq=h,
                                    hydrodynamic_nodal=np.zeros((len(m), 6), complex),
                                    hydrostatic_nodal=np.zeros((len(m), 6), complex), length=20.0, enforce_closure=False)
        seen.append(k)
        return -(comp.weight + comp.inertia)

    out = ft.compose_floating_components(points=pts, mass_apply=ma, dofmap=dm, cg_world=cg, Q0=np.eye(3), gravity=G,
                                         rho=1025.0, omegas=w, Hq=H, source_hydrodynamic=hydro,
                                         direct_hydrostatic_nodal=lambda k, h: np.zeros((len(m), 6), complex), length=20.0)
    assert seen == [0, 1] and out["pressure"].shape == (2, len(m), 6)
    assert all(r["passed"] for r in out["closure"])
    with pytest.raises(fl.FloatingLoadError):
        ft.compose_floating_components(points=pts, mass_apply=ma, dofmap=dm, cg_world=cg, Q0=np.eye(3), gravity=G,
                                       rho=1025.0, omegas=w, Hq=H, source_hydrodynamic=lambda k, h: 0.5 * hydro(k, h),
                                       direct_hydrostatic_nodal=lambda k, h: np.zeros((len(m), 6), complex), length=20.0)
