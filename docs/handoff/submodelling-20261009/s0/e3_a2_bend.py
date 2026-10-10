"""S0 / A2-bend: constant curvature is not representable by bilinear Q4 / linear S3 interpolation.

Global [0,1]^2 coarse, nodal field = analytical w = k (x-0.5)^2 / 2, theta_y = -k (x-0.5)
(sign confirmed in e2: theta_y = -dw/dx), other components zero.  Submodel [0.3,0.7]^2
fine (fixed), driven on its outer edges.  Sweep the global size; report:
  - transfer vs an independent piecewise-linear interpolation of w in x (Q4 source on an
    axis-aligned grid interpolates w(x) linearly between grid lines) -> must agree to roundoff
  - boundary error of w against the analytical field, expected <= k h^2 / 8
  - interior error of w and theta_y, and the observed order
"""

from __future__ import annotations

import s0_env  # noqa: F401

import numpy as np

import s0_lib as L

KAPPA = 1.0e-3
GLOBAL_SIZES = (0.25, 0.125, 0.0625, 0.03125)
SUB_SIZE = 0.025


def field(x):
    s = x[0] - 0.5
    return np.array([0.0, 0.0, 0.5 * KAPPA * s * s, 0.0, -KAPPA * s, 0.0])


def run(target_tri: bool):
    sproj = L.new_project("sub")
    sfaces = L.rectangle_faces(sproj, [0.3, 0.7], [0.3, 0.7])
    smesh = L.mesh(sproj, SUB_SIZE, triangles=target_tri)
    cut = L.boundary_edges(sproj, sfaces.values())
    driven = sorted({n for e in cut for n in smesh.nodes_on(sproj.edge(e))})
    interior = sorted(set(smesh.nodes) - set(driven))
    exact = L.field_at(smesh.nodes, field)
    rows = []
    for h in GLOBAL_SIZES:
        gproj = L.new_project("global")
        L.rectangle_faces(gproj, [0.0, 1.0], [0.0, 1.0])
        gmesh = L.mesh(gproj, h)
        gvals = L.field_at(gmesh.nodes, field)
        tr = L.transfer(gmesh, gvals, smesh, driven)
        grid = np.array(sorted({round(float(x[0]), 12) for x in gmesh.nodes.values()}))
        wgrid = 0.5 * KAPPA * (grid - 0.5) ** 2
        independent = max(
            abs(float(tr.values[n][2]) - float(np.interp(smesh.nodes[n][0], grid, wgrid))) for n in driven
        )
        bnd = L.errors(tr.values, exact, driven)
        for policy, dofs in (("P2", L.DOF_NAMES), ("P1", L.DOF_NAMES[:5]), ("T", L.DOF_NAMES[:3])):
            built = L.build(sproj, smesh, require_loads=False, require_supports=False)
            L.drive(built, tr.values, dofs)
            s = L.solve(built)
            comp = {n: s.node_values(n) for n in smesh.nodes}
            err = L.errors(comp, exact, interior)
            core = [n for n in interior if min(abs(smesh.nodes[n][0] - 0.3), abs(smesh.nodes[n][0] - 0.7),
                                               abs(smesh.nodes[n][1] - 0.3), abs(smesh.nodes[n][1] - 0.7)) >= 0.05 - 1e-12]
            core_err = L.errors(comp, exact, core)
            rows.append({
                "target": "S3" if target_tri else "Q4",
                "global_size": h,
                "policy": policy,
                "transfer_vs_independent_interp_w": independent,
                "boundary_w_error": bnd["per_component_max_abs"][2],
                "boundary_w_bound_kh2_over_8": KAPPA * h * h / 8,
                "interior_w_error": err["per_component_max_abs"][2],
                "interior_thy_error": err["per_component_max_abs"][4],
                "interior_rz_abs": err["per_component_max_abs"][5],
                "core_w_error": core_err["per_component_max_abs"][2],
                "core_thy_error": core_err["per_component_max_abs"][4],
                "w_scale": err["translation_scale"],
                "thy_scale": err["rotation_scale"],
                "backward_error": s.backward_error,
            })
            r = rows[-1]
            print(f"{r['target']} h={h:<8} {policy} indep {independent:.1e}  bnd w {r['boundary_w_error']:.3e} "
                  f"(<= {r['boundary_w_bound_kh2_over_8']:.3e})  int w {r['interior_w_error']:.3e} "
                  f"thy {r['interior_thy_error']:.3e}  core w {r['core_w_error']:.3e} thy {r['core_thy_error']:.3e}", flush=True)
    for policy in ("P2", "P1", "T"):
        sel = [r for r in rows if r["policy"] == policy]
        hs = np.log([r["global_size"] for r in sel])
        for key in ("boundary_w_error", "interior_w_error", "interior_thy_error", "core_w_error", "core_thy_error"):
            slope = np.polyfit(hs, np.log([max(r[key], 1e-300) for r in sel]), 1)[0]
            print(f"   {sel[0]['target']} {policy} observed order {key}: {slope:.2f}", flush=True)
            for r in sel:
                r.setdefault("observed_order", {})[key] = slope
    return rows


def main():
    rows = run(False) + run(True)
    print("saved", L.save("e3_a2_bend", {"kappa": KAPPA, "sub_size": SUB_SIZE, "rows": rows}))


if __name__ == "__main__":
    main()
