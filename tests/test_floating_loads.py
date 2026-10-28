"""Coupled floating load composition: pure translation/rotation mass loads, gravity perturbation, closure rule."""
import numpy as np
import pytest

from anyfem.application import floating_loads as fl

G = 9.81


def cloud(n=40, seed=3):
    rng = np.random.default_rng(seed)
    pts = rng.uniform([-10, -3, -2], [10, 3, 0], size=(n, 3))
    masses = rng.uniform(1.0, 2.0, size=n) * 1e3
    index = np.arange(n * 6).reshape(n, 6)
    dofmap = fl.NodalDofMap(index, n * 6)

    def mass_apply(full):  # lumped translational mass only; rotational DOF mass zero (point-mass idealisation)
        out = np.zeros_like(full)
        nodal = dofmap.gather(full)
        nodal[:, :3] *= masses[:, None]
        nodal[:, 3:] = 0
        return dofmap.scatter(nodal)

    return pts, masses, dofmap, mass_apply


def test_dofmap_roundtrip_and_rejects_duplicates():
    idx = np.arange(12).reshape(2, 6)
    m = fl.NodalDofMap(idx, 12)
    v = np.arange(12.0)
    np.testing.assert_array_equal(m.scatter(m.gather(v)), v)
    bad = idx.copy(); bad[1, 0] = 0
    with pytest.raises(fl.FloatingLoadError):
        fl.NodalDofMap(bad, 12)


def test_primary_translation_and_rotation_inertia_are_analytic():
    pts, m, dm, ma = cloud()
    cg = (m[:, None] * pts).sum(0) / m.sum()
    w = 0.9
    heave = np.array([0, 0, 0.3, 0, 0, 0], complex)
    acc = fl.primary_acceleration_nodal(pts, cg, np.eye(3), heave, w)
    Fi = fl.primary_inertia_nodal(ma, dm, acc)
    np.testing.assert_allclose(Fi[:, 2], w ** 2 * 0.3 * m)
    roll = np.array([0, 0, 0, 0.02, 0, 0], complex)
    acc = fl.primary_acceleration_nodal(pts, cg, np.eye(3), roll, w)
    np.testing.assert_allclose(acc[:, 3], -w ** 2 * 0.02)            # angular nodal DOFs carried
    Fi = fl.primary_inertia_nodal(ma, dm, acc)
    r = pts - cg
    wr = fl.nodal_wrench(pts, Fi, cg)
    np.testing.assert_allclose(wr[3], w ** 2 * 0.02 * np.sum(m * (r[:, 1] ** 2 + r[:, 2] ** 2)), rtol=1e-12)
    np.testing.assert_allclose(wr[:3], 0, atol=1e-6)


def test_weight_perturbation_is_minus_theta_cross_g0_with_no_normal_rotation_term():
    pts, m, dm, ma = cloud()
    th = np.array([0.02, -0.01, 0.0])
    h = np.concatenate([np.zeros(3), th]).astype(complex)
    Fg = fl.weight_perturbation_nodal(ma, dm, np.eye(3), G, h)
    expected = -np.cross(th, [0, 0, -G])
    np.testing.assert_allclose(Fg[:, :3], m[:, None] * expected[None, :], atol=1e-9)
    np.testing.assert_array_equal(Fg[:, 3:], 0)
    # heeled mean pose: delta_g_world = -theta_world x g_world
    c, s = np.cos(0.1), np.sin(0.1)
    Q0 = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    Fg = fl.weight_perturbation_nodal(ma, dm, Q0, G, h)
    np.testing.assert_allclose(Fg[0, :3], m[0] * (-np.cross(Q0 @ th, [0, 0, -G])), atol=1e-9)
    # static weight
    Fs = fl.static_weight_nodal(ma, dm, G)
    np.testing.assert_allclose(Fs[:, 2], -m * G)


def _components(scale_pressure=1.0):
    pts, m, dm, ma = cloud()
    cg = (m[:, None] * pts).sum(0) / m.sum()
    h = np.array([0.1, -0.05, 0.3, 0.01, -0.02, 0.005], complex) * (1 + 0.3j)
    w = 1.1
    zero = np.zeros((len(m), 6), complex)
    base = fl.compose_component(pts, ma, dm, cg_world=cg, Q0=np.eye(3), gravity=G, omega=w, Hq=h,
                                hydrodynamic_nodal=zero, hydrostatic_nodal=zero, length=20.0, enforce_closure=False)
    return pts, cg, base, ma, dm, h, w


def test_closure_passes_when_pressure_balances_and_fails_when_not():
    pts, cg, base, ma, dm, h, w = _components()
    Fp = -(base.weight + base.inertia)
    rep = fl.dynamic_closure(pts, cg, 20.0, Fp, base.weight, base.inertia)
    assert rep["passed"] and rep["global_norm"] < 1e-8 * max(rep["scale_S"], 1.0)
    bad = fl.dynamic_closure(pts, cg, 20.0, 1.05 * Fp, base.weight, base.inertia)
    assert not bad["passed"]
    with pytest.raises(fl.FloatingLoadError):
        fl.require_closure(bad)
    # no global rescale is attempted: calling compose with unbalanced pressure and enforcement raises
    zero = np.zeros_like(Fp)
    with pytest.raises(fl.FloatingLoadError):
        fl.compose_component(pts, ma, dm, cg_world=cg, Q0=np.eye(3), gravity=G, omega=1.1, Hq=h,
                             hydrodynamic_nodal=0.5 * Fp, hydrostatic_nodal=zero, length=20.0)


def test_zero_resultant_pressure_pattern_keeps_physical_scale():
    pts = np.array([[1.0, 1, 0], [1.0, -1, 0], [-1.0, 1, 0], [-1.0, -1, 0]])
    p = np.zeros((4, 6), complex); p[:, 2] = [5.0, -5.0, -5.0, 5.0]   # zero net force AND moment, distributed pressure
    zero = np.zeros((4, 6), complex)
    rep = fl.dynamic_closure(pts, (0, 0, 0), 2.0, p, zero, zero)
    assert rep["scale_S"] == pytest.approx(20.0) and rep["d"].max() == 0.0   # physical scale from local magnitudes
    # a small genuine residual on top of that pattern is judged against that scale, not against zero
    q = p.copy(); q[0, 2] += 1.0
    assert not fl.dynamic_closure(pts, (0, 0, 0), 2.0, q, zero, zero)["passed"]
    ok = fl.dynamic_closure(pts, (0, 0, 0), 2.0, zero, zero, zero)
    assert ok["passed"] and ok["scale_S"] == 0.0


def test_synthesis_is_real_and_follows_realization_phase():
    L = np.zeros((2, 1, 6), complex); L[0, 0, 2] = 1.0; L[1, 0, 2] = 1j
    out = fl.synthesize_real(L, [2.0, 1.0], [0.3, 0.0], [1.0, 2.0], 0.5)
    expect = 2.0 * np.real(np.exp(1j * 0.3) * np.exp(-0.5j)) + 1.0 * np.real(1j * np.exp(-1.0j))
    np.testing.assert_allclose(out[0, 2], expect)
