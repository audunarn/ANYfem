"""S0 / A1: element-aligned restriction consistency, plus the complementary outside model.

Global: plate [0,1]^2 as a 3x3 grid of faces (lines at 0.3 and 0.7), mesh 0.1 so the
central face [0.3,0.7]^2 is element-aligned.  Pressure on every face, tension on the
right edge, a tangential load on the top edge; left edge pinned, other outer edges uz=0.

Inside model: the central face alone, same section and pressure, same mesh size.
Outside model: the other eight faces with their loads and supports.
Both are driven on the cut with the global nodal values.

Checks: nodes and elements coincide; inside interior equals global; inside and
outside cut forces are equal and opposite (Newton across the cut); the inside
free body balances the pressure on it; P1 (rz released) deviation; rotation sign.
"""

from __future__ import annotations

import s0_env  # noqa: F401

import numpy as np

import s0_lib as L
from anyfem.model import pinned, support

LINES = [0.0, 0.3, 0.7, 1.0]
SIZE = 0.1
PRESSURE = 10_000.0
TENSION = (1.0e5, 0.0, 0.0)
SHEAR = (2.0e4, 0.0, 0.0)


def load_and_support(project, faces, *, outer=True):
    lc = project.load_case()
    for face in faces.values():
        lc.add_pressure(project.face(face), PRESSURE)
    if not outer:
        return
    for edge in L.boundary_edges(project, faces.values()):
        mid = L.edge_midpoint(project, edge)
        on_outer = min(mid[0], mid[1], 1.0 - mid[0], 1.0 - mid[1]) < 1.0e-9
        if not on_outer:
            continue
        if abs(mid[0]) < 1.0e-9:
            project.add_support(pinned(project.edge(edge)))
        else:
            project.add_support(support(project.edge(edge), uz=0.0))
        if abs(mid[0] - 1.0) < 1.0e-9:
            lc.add_line_load(project.edge(edge), TENSION)
        if abs(mid[1] - 1.0) < 1.0e-9:
            lc.add_line_load(project.edge(edge), SHEAR)


def node_map(fine_mesh, global_mesh, tol=1.0e-12):
    keyed = {tuple(np.round(np.asarray(x) / tol).astype(np.int64)): n for n, x in global_mesh.nodes.items()}
    out = {}
    for n, x in fine_mesh.nodes.items():
        key = tuple(np.round(np.asarray(x) / tol).astype(np.int64))
        if key not in keyed:
            # fall back to a nearest search with the tolerance
            g = min(global_mesh.nodes, key=lambda m: np.linalg.norm(np.asarray(global_mesh.nodes[m]) - x))
            if np.linalg.norm(np.asarray(global_mesh.nodes[g]) - x) > 1.0e-9:
                raise RuntimeError(f"node {n} at {x} has no coincident global node")
            out[n] = g
        else:
            out[n] = keyed[key]
    return out


def main():
    # ---------------- global
    gp = L.new_project("global")
    gfaces = L.rectangle_faces(gp, LINES, LINES)
    load_and_support(gp, gfaces)
    gmesh = L.mesh(gp, SIZE)
    gbuilt = L.build(gp, gmesh)
    gs = L.solve(gbuilt)
    gvals = {n: gs.node_values(n) for n in gmesh.nodes}
    gcond = L.condition_estimate(gbuilt)

    # ---------------- inside
    ip = L.new_project("inside")
    ifaces = L.rectangle_faces(ip, LINES[1:3], LINES[1:3])
    load_and_support(ip, ifaces, outer=False)
    imesh = L.mesh(ip, SIZE)
    imap = node_map(imesh, gmesh)
    g_central = set(gmesh.elements_on(gp.face(gfaces[1, 1])))

    inside_conn = {frozenset(imap[n] for n in conn) for conn in imesh.shells.values()}
    central_conn = {frozenset(gmesh.shells[e]) for e in g_central}
    elements_coincide = inside_conn == central_conn
    cut_edges = L.boundary_edges(ip, ifaces.values())
    driven = sorted({n for e in cut_edges for n in imesh.nodes_on(ip.edge(e))})
    interior = sorted(set(imesh.nodes) - set(driven))

    # transfer by the BVH route, compared with the direct nodal copy
    tr = L.transfer(gmesh, gvals, imesh, driven, element_ids=sorted(g_central))
    direct = {n: gvals[imap[n]] for n in driven}
    transfer_vs_direct = max(float(np.max(np.abs(tr.values[n] - direct[n]))) for n in driven)

    rows = {}
    for policy, dofs in (("P2", L.DOF_NAMES), ("P1", L.DOF_NAMES[:5])):
        ib = L.build(ip, imesh, require_supports=False)
        L.drive(ib, tr.values, dofs)
        s = L.solve(ib)
        cond = L.condition_estimate(ib)
        computed = {n: s.node_values(n) for n in imesh.nodes}
        expected = {n: gvals[imap[n]] for n in imesh.nodes}
        err = L.errors(computed, expected, interior)
        forces_in = L.interface_forces(ib, s.u, driven)
        positions = {n: np.asarray(imesh.nodes[n]) for n in driven}
        centre = np.array([0.5, 0.5, 0.0])
        total_in = L.resultant(forces_in, positions, centre)
        applied = np.array([0, 0, PRESSURE * 0.16, 0, 0, 0])  # sign checked against the solver below
        rows[policy] = {
            "interior": err,
            "backward_error": s.backward_error,
            "kappa1": cond["kappa1"],
            "kappa1_scaled": cond["kappa1_scaled"],
            "norm1_K": cond["norm1_K"],
            "u_inf": float(np.max(np.abs(s.u))),
            "free_body_resultant_of_cut_forces": total_in,
            "pressure_resultant_magnitude": PRESSURE * 0.16,
            "forces_in": {int(n): f for n, f in forces_in.items()},
        }
        print(f"{policy}: interior t {err['max_abs_translation_error']:.3e} (scale {err['translation_scale']:.3e}) "
              f"r {err['max_abs_rotation_error']:.3e} (scale {err['rotation_scale']:.3e}); "
              f"eta {s.backward_error:.1e} k1 {cond['kappa1']:.2e} k1s {cond['kappa1_scaled']:.2e}", flush=True)
        print(f"    cut-force resultant about centre {np.array2string(total_in, precision=4)}", flush=True)

    # ---------------- outside (complement), driven on the same cut with global values
    op = L.new_project("outside")
    ofaces = L.rectangle_faces(op, LINES, LINES, skip={(1, 1)})
    load_and_support(op, ofaces)
    omesh = L.mesh(op, SIZE)
    omap = node_map(omesh, gmesh)
    inv_imap = {imap[n]: n for n in driven}
    o_cut = sorted(n for n in omesh.nodes if omap[n] in inv_imap)
    ob = L.build(op, omesh)
    L.drive(ob, {n: gvals[omap[n]] for n in o_cut})
    os_ = L.solve(ob)
    forces_out = L.interface_forces(ob, os_.u, o_cut)
    outside_interior = sorted(set(omesh.nodes) - set(o_cut))
    oerr = L.errors({n: os_.node_values(n) for n in omesh.nodes}, {n: gvals[omap[n]] for n in omesh.nodes}, outside_interior)
    newton = []
    for n in o_cut:
        fin = rows["P2"]["forces_in"][inv_imap[omap[n]]]
        newton.append(np.abs(fin + forces_out[n]))
    newton = np.array(newton)
    fscale = max(float(np.max(np.abs(np.array(list(forces_out.values()))))), 1e-300)
    print(f"outside interior t {oerr['max_abs_translation_error']:.3e} r {oerr['max_abs_rotation_error']:.3e}", flush=True)
    print(f"Newton across cut: max |f_in + f_out| force {newton[:, :3].max():.3e} N, moment {newton[:, 3:].max():.3e} Nm; "
          f"max cut force {fscale:.3e}", flush=True)

    # ---------------- per-edge resultants (corners reported separately)
    corner_nodes = {imesh.node_of_vertex[v] for v in ip.geometry.vertices}
    per_edge = {}
    for e in cut_edges:
        nodes = [n for n in imesh.nodes_on(ip.edge(e)) if n not in corner_nodes]
        mid = L.edge_midpoint(ip, e)
        per_edge[int(e)] = {
            "midpoint": mid,
            "resultant_excluding_corners": L.resultant(
                {n: rows["P2"]["forces_in"][n] for n in nodes}, {n: imesh.nodes[n] for n in nodes}, mid
            ),
        }

    # ---------------- rotation sign: theta_y against -dw/dx on the global grid
    xs = sorted({round(float(x[0]), 9) for x in gmesh.nodes.values()})
    signs = []
    for n, x in gmesh.nodes.items():
        if not (0.15 < x[0] < 0.85 and 0.15 < x[1] < 0.85):
            continue
        left = [m for m, y in gmesh.nodes.items() if abs(y[1] - x[1]) < 1e-9 and abs(y[0] - (x[0] - SIZE)) < 1e-9]
        right = [m for m, y in gmesh.nodes.items() if abs(y[1] - x[1]) < 1e-9 and abs(y[0] - (x[0] + SIZE)) < 1e-9]
        if left and right:
            dwdx = (gvals[right[0]][2] - gvals[left[0]][2]) / (2 * SIZE)
            signs.append((gvals[n][4], -dwdx))
    signs = np.array(signs)
    correlation = float(np.corrcoef(signs[:, 0], signs[:, 1])[0, 1])
    print(f"rotation sign: corr(theta_y, -dw/dx) = {correlation:.4f} over {len(signs)} nodes", flush=True)

    summary = {
        "global": {"elements": gmesh.num_elements, "nodes": gmesh.num_nodes, "backward_error": gs.backward_error,
                   "kappa1": gcond["kappa1"], "kappa1_scaled": gcond["kappa1_scaled"], "u_inf": float(np.max(np.abs(gs.u)))},
        "inside": {"elements": imesh.num_elements, "driven_nodes": len(driven), "elements_coincide": elements_coincide},
        "transfer_vs_direct_max_abs": transfer_vs_direct,
        "policies": {k: {kk: vv for kk, vv in v.items() if kk != "forces_in"} for k, v in rows.items()},
        "outside_interior": oerr,
        "newton_max_force": float(newton[:, :3].max()),
        "newton_max_moment": float(newton[:, 3:].max()),
        "max_cut_force_component": fscale,
        "per_edge": per_edge,
        "rotation_sign_correlation_thetay_vs_minus_dwdx": correlation,
    }
    print("elements coincide:", elements_coincide, " transfer vs direct:", f"{transfer_vs_direct:.2e}")
    print("saved", L.save("e2_a1_restriction", summary))


if __name__ == "__main__":
    main()
