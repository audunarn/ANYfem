"""Residual inertia relief contract: explicit nodal 6-vector LoadCase (gravity=None) through iter_linear_batches."""
import numpy as np
import pytest

from anysolver import BeamElement, FEModel, solve_inertia_relief
from anysolver.matrix_assembly import assemble_mass_matrix

from anyfem.application.floating_loads import NodalDofMap
from anyfem.solve import floating_batch as fb


def bar(n=4):
    model = FEModel("floating-relief-bar")
    model.add_material("steel", 1.0e7, 0.3, density=5.0)
    for i, x in enumerate(np.linspace(0.0, 2.0, n + 1), 1):
        model.add_node(i, x, 0.0, 0.0)
    for i in range(1, n + 1):
        model.add_element(i, BeamElement(i, [i, i + 1], "steel", {"area": 0.2, "Iy": 0.001, "Iz": 0.002, "J": 0.001,
                                                                    "consistent_mass": True}))
    return model


def setup():
    model = bar()
    ids = sorted(model.mesh.nodes)
    dofmap = NodalDofMap(np.array([model.mesh.nodes[i].dofs for i in ids]), model.mesh.dof_manager.total_dofs)
    M, _ = assemble_mass_matrix(model)
    points = np.array([model.mesh.nodes[i].coords() for i in ids])
    return model, ids, dofmap, M, points


def rigid_nodal(points, center, a6):
    a = np.asarray(a6, float)
    lin = a[:3][None, :] + np.cross(a[3:], points - center)
    return np.concatenate([lin, np.broadcast_to(a[3:], points.shape)], axis=1)


def load_case_of(model, ids, nodal):
    return fb.build_residual_load_case(model, ids, nodal, "x")


def test_explicit_nodal_case_has_no_gravity_and_accepts_full_six_vectors():
    model, ids, dofmap, M, pts = setup()
    nodal = np.zeros((len(ids), 6)); nodal[-1] = [1.0, 0.2, -0.1, 0.03, -0.05, 0.01]
    case = load_case_of(model, ids, nodal)
    assert case.gravity is None and not case.pressure_loads and not case.added_node_masses
    result = solve_inertia_relief(model, case)
    assert result.converged
    np.testing.assert_allclose(result.applied_loads, dofmap.scatter(nodal), atol=1e-12)  # moments included, no gravity


def test_primary_plus_correction_equals_pure_relief():
    model, ids, dofmap, M, pts = setup()
    rng = np.random.default_rng(5)
    F = rng.normal(size=(len(ids), 6))
    pure = solve_inertia_relief(model, load_case_of(model, ids, F))
    com = pure.center_of_mass
    # prescribe an arbitrary primary rigid acceleration at the CM; residual route adds only the correction
    a_primary6 = np.array([0.2, -0.1, 0.3, 0.01, 0.02, -0.03])
    a_nodal = rigid_nodal(pts, com, a_primary6)
    residual = F - dofmap.gather(M @ dofmap.scatter(a_nodal))
    frames = [fb.FloatingFrameInput("f0", 0.0, residual, a_nodal)]
    out = list(fb.iter_residual_relief(model, ids, frames))[0]
    np.testing.assert_allclose(out.total_acceleration_nodal,
                               rigid_nodal(pts, com, np.concatenate([pure.acceleration_at_center_of_mass,
                                                                      pure.angular_acceleration])),
                               rtol=1e-8, atol=1e-10)
    np.testing.assert_allclose(out.displacements, pure.displacements, rtol=1e-7, atol=1e-12)
    for key in ("force_balance_error", "moment_balance_error"):
        assert out.relief["diagnostics"][key] <= 1e-10


def test_zero_residual_gives_zero_correction_and_no_elastic_response():
    model, ids, dofmap, M, pts = setup()
    com = solve_inertia_relief(model, load_case_of(model, ids, np.zeros((len(ids), 6)))).center_of_mass
    a6 = np.array([0.3, 0.2, -0.1, 0.01, -0.02, 0.04])
    a_nodal = rigid_nodal(pts, com, a6)
    F = dofmap.gather(M @ dofmap.scatter(a_nodal))                # exactly the rigid inertial demand
    residual = F - dofmap.gather(M @ dofmap.scatter(a_nodal))
    out = list(fb.iter_residual_relief(model, ids, [fb.FloatingFrameInput("z", 0.0, residual, a_nodal)]))[0]
    assert np.linalg.norm(out.correction_acceleration_nodal) < 1e-12
    assert np.linalg.norm(out.displacements) < 1e-12
    np.testing.assert_allclose(out.total_acceleration_nodal, a_nodal, atol=1e-12)


def test_rejects_malformed_nodal_load():
    model, ids, *_ = setup()
    with pytest.raises(fb.FloatingBatchError):
        fb.build_residual_load_case(model, ids, np.zeros((len(ids), 3)), "bad")
    with pytest.raises(fb.FloatingBatchError):
        fb.build_residual_load_case(model, ids, np.full((len(ids), 6), np.nan), "bad")
