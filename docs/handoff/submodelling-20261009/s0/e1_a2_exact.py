"""S0 / A2-exact: exactly representable fields through the transfer and the submodel solve.

Global: plate [0,1]^2 in the xy-plane, coarse.  Its nodal field is the analytical
field (no global solve), so this isolates transfer + submodel.  Submodel:
[0.3,0.7]^2, fine, not aligned with the global grid.  Driven on its outer edges.

Fields (all six components, rotation vector right-handed about global axes):
  a  uniaxial tension     u = s x / E, v = -nu s y / E, w = 0, theta = 0
  b  affine membrane      u = ex x + (g/2 - om) y, v = (g/2 + om) x + ey y, w = 0,
                          thx = thy = 0, thz = om
  c  rigid motion         u = a + Om x X, theta = Om
Policies: P2 drive all six; P1 release the rotation about the shell normal (z here,
so P1 is "do not drive rz"; valid only because the plate is in the xy-plane).
"""

from __future__ import annotations

import s0_env  # noqa: F401

import itertools

import numpy as np

import s0_lib as L

E_MOD, NU = 210.0e9, 0.3
SIGMA = 100.0e6
EX, EY, GAMMA, OMEGA = 1.0e-4, -3.0e-5, 5.0e-5, 2.0e-4
A_RIGID = np.array([1.0e-3, -2.0e-3, 3.0e-3])
OM_RIGID = np.array([1.0e-3, -5.0e-4, 2.0e-3])


def field_a(x):
    return np.array([SIGMA * x[0] / E_MOD, -NU * SIGMA * x[1] / E_MOD, 0, 0, 0, 0])


def field_b(x):
    return np.array([
        EX * x[0] + (GAMMA / 2 - OMEGA) * x[1],
        (GAMMA / 2 + OMEGA) * x[0] + EY * x[1],
        0, 0, 0, OMEGA,
    ])


def field_c(x):
    return np.concatenate([A_RIGID + np.cross(OM_RIGID, x), OM_RIGID])


FIELDS = {"a_tension": field_a, "b_affine_rotation": field_b, "c_rigid": field_c}
GRADIENT = {"a_tension": SIGMA / E_MOD, "b_affine_rotation": 2.5e-4, "c_rigid": float(np.linalg.norm(OM_RIGID))}


def run_case(source_tri: bool, target_tri: bool):
    gproj = L.new_project("global")
    gfaces = L.rectangle_faces(gproj, [0.0, 1.0], [0.0, 1.0])
    gmesh = L.mesh(gproj, 0.25, triangles=source_tri)

    sproj = L.new_project("sub")
    sfaces = L.rectangle_faces(sproj, [0.3, 0.7], [0.3, 0.7])
    smesh = L.mesh(sproj, 0.05, triangles=target_tri)
    cut_edges = L.boundary_edges(sproj, sfaces.values())
    driven = sorted({n for e in cut_edges for n in smesh.nodes_on(sproj.edge(e))})
    interior = sorted(set(smesh.nodes) - set(driven))

    rows = []
    for (fname, function), (policy, dofs) in itertools.product(
        FIELDS.items(), (("P2", L.DOF_NAMES), ("P1", L.DOF_NAMES[:5]))
    ):
        gvalues = L.field_at(gmesh.nodes, function)
        tr = L.transfer(gmesh, gvalues, smesh, driven)
        exact_sub = L.field_at(smesh.nodes, function)
        boundary_err = L.errors(tr.values, exact_sub, driven)

        built = L.build(sproj, smesh, require_loads=False, require_supports=False)
        L.drive(built, tr.values, dofs)
        s = L.solve(built)
        computed = {n: s.node_values(n) for n in smesh.nodes}
        interior_err = L.errors(computed, exact_sub, interior)
        cond = L.condition_estimate(built)
        u_norm = float(np.max(np.abs(s.u)))
        eta = s.backward_error
        max_value = max(float(np.max(np.abs(v))) for v in gvalues.values())
        interp_allowance = 8 * L.EPS * max_value + GRADIENT[fname] * max(tr.residual.values())
        forces = L.interface_forces(built, s.u, driven)
        fmax = max(float(np.max(np.abs(f))) for f in forces.values())
        rows.append({
            "source": "S3" if source_tri else "Q4",
            "target": "S3" if target_tri else "Q4",
            "field": fname,
            "policy": policy,
            "global_elements": gmesh.num_elements,
            "sub_elements": smesh.num_elements,
            "driven_nodes": len(driven),
            "element_classes": sorted({type(e).__name__ for e in built.fe_model.mesh.elements.values()}),
            "boundary": boundary_err,
            "interior": interior_err,
            "interp_allowance": interp_allowance,
            "max_inverse_residual": max(tr.residual.values()),
            "hits_max": tr.candidates_max,
            "ambiguous_nodes": len(tr.ambiguous),
            "backward_error": eta,
            "kappa1": cond["kappa1"],
            "kappa1_scaled": cond["kappa1_scaled"],
            "solve_allowance_unscaled": cond["kappa1"] * eta * u_norm,
            "solve_allowance_scaled": cond["kappa1_scaled"] * eta * u_norm,
            "u_inf": u_norm,
            "max_interface_force_component": fmax,
            "solve_seconds": s.seconds,
        })
        r = rows[-1]
        print(
            f"{r['source']}->{r['target']} {fname:18s} {policy}  "
            f"bnd t/r {boundary_err['max_abs_translation_error']:.2e}/{boundary_err['max_abs_rotation_error']:.2e}  "
            f"int t/r {interior_err['max_abs_translation_error']:.2e}/{interior_err['max_abs_rotation_error']:.2e}  "
            f"allow interp {interp_allowance:.1e} solve {r['solve_allowance_scaled']:.1e}/{r['solve_allowance_unscaled']:.1e}  "
            f"eta {eta:.1e} k1 {cond['kappa1']:.1e} k1s {cond['kappa1_scaled']:.1e}  "
            f"|F|max {fmax:.2e}  amb {len(tr.ambiguous)}",
            flush=True,
        )
    return rows


def main():
    rows = []
    for source_tri, target_tri in ((False, False), (False, True), (True, False), (True, True)):
        try:
            rows.extend(run_case(source_tri, target_tri))
        except Exception as error:  # record the failure, it is a finding
            rows.append({"source": "S3" if source_tri else "Q4", "target": "S3" if target_tri else "Q4",
                         "error": f"{type(error).__name__}: {error}"})
            print("FAILED", source_tri, target_tri, type(error).__name__, error, flush=True)
    path = L.save("e1_a2_exact", {"rows": rows})
    print("saved", path)


if __name__ == "__main__":
    main()
