"""C0 checks: the D5 MPC example, redundant/inconsistent traces, and ErrorAllowance v2.

Scratch prototype for the C0 contracts (no owner change).  Uses the S0 environment.

Part 1  D5 constraint-reduced trace on a real ANYsolver model.
        Plate [0,1]^2, Q4, mesh 0.25, membrane only (bending DOFs held on the face).
        Cut = the left edge x = 0.  Retained internal MPC:  ux(s) = 1/2 ux(m1) + 1/2 ux(m2)
        with s = (0, 1) and m1 = (0, 0.5) on the cut, m2 = (0.25, 1) interior.
        Trace: every cut DOF driven by the affine field g(x, y); the slave row is imposed
        on the independent coordinates as 1/2 ux(m1) + 1/2 ux(m2) = g_s (dependent m2).
        Expected: ux(s) = g_s, ux(m1) = g_m1, ux(m2) = 2 g_s - g_m1.
        Interface force from multipliers (B T)^T lam = T^T (K u - f):
            lam(m1x) = r(m1x) - r(m2x),   lam(sx) = 2 r(m2x) + r(sx),   others lam = r.
        Free body: sum of cut forces + applied load = 0.
Part 2  Current solver behaviour the contract relies on:
        prescribing an MPC slave raises; a redundant consistent row is a CONSTRAINT007
        warning; an inconsistent row is a CONSTRAINT005 error.
Part 3  ErrorAllowance v2 on the S0 A1 case and the zero-load rigid case:
        eta_s and kappa_s on the solver's own equilibrated operator (S K S), per-DOF
        allowance S_i * kappa_s * eta_s * ||y||, propagation of the global error through the
        inside operator, and a matvec roundoff floor gamma_n (|K| |u|)_i for forces.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s0"))

import s0_env  # noqa: E402,F401

import numpy as np  # noqa: E402
from scipy import sparse  # noqa: E402
from scipy.sparse.linalg import LinearOperator, onenormest, splu  # noqa: E402

import s0_lib as L  # noqa: E402
from anyfem.model import support  # noqa: E402
from anysolver import BoundaryCondition, assemble_load_vector, solve_linear  # noqa: E402
from anysolver.analysis_session import AnalysisSession  # noqa: E402
from anysolver.constraint_audit import audit_constraints  # noqa: E402

EPS = float(np.finfo(float).eps)
OUT = {}


def g_field(x):
    """Affine membrane field used as the driven trace (six components)."""
    return np.array([1.0e-4 * x[0] + 2.0e-5 * x[1] + 1.0e-4, -3.0e-5 * x[1] + 5.0e-5 * x[0], 0, 0, 0, 1.5e-5])


def plate(size=0.25):
    p = L.new_project("c0_plate")
    faces = L.rectangle_faces(p, [0.0, 1.0], [0.0, 1.0])
    face = faces[0, 0]
    p.add_support(support(p.face(face), uz=0.0, rx=0.0, ry=0.0))
    g = p.geometry
    for e, edge in g.edges.items():
        a = np.asarray(g.vertex_position(edge.start)); b = np.asarray(g.vertex_position(edge.end))
        if abs(a[0] - 1) < 1e-9 and abs(b[0] - 1) < 1e-9:
            p.load_case().add_line_load(p.edge(e), (2.0e5, 3.0e4, 0.0))
    m = L.mesh(p, size)
    return p, m


def node_at(mesh_, xy):
    return min(mesh_.nodes, key=lambda n: np.linalg.norm(np.asarray(mesh_.nodes[n])[:2] - np.asarray(xy)))


def full_k_f(built, u=None):
    with AnalysisSession(built.fe_model) as sess:
        k = sparse.csr_matrix(sess.stiffness_plan().matrix)
    f, _info = assemble_load_vector(built.fe_model, built.load_case)
    return k, np.asarray(f, dtype=float)


# ----------------------------------------------------------------------
# Part 1
# ----------------------------------------------------------------------
def part1():
    p, m = plate()
    built = L.build(p, m, require_supports=True)
    fe = built.fe_model
    dm = fe.mesh.dof_manager
    s, m1, m2 = node_at(m, (0, 1)), node_at(m, (0, 0.5)), node_at(m, (0.25, 1))
    sx, m1x, m2x = dm.get_node_dofs(s)[0], dm.get_node_dofs(m1)[0], dm.get_node_dofs(m2)[0]
    # retained internal constraint (stands in for an eccentric coupling)
    fe.add_constraint_equation(terms=((sx, 1.0), (m1x, -0.5), (m2x, -0.5)), rhs=0.0,
                               source_id="internal_mpc", dependent_dof=sx)
    cut = sorted(n for n, x in m.nodes.items() if abs(x[0]) < 1e-12)
    gval = {n: g_field(np.asarray(m.nodes[n])) for n in cut}
    # trace: all six DOFs of every cut node, except the slave row, as supports
    for n in cut:
        dofs = dict(zip(L.DOF_NAMES, gval[n]))
        if n == s:
            dofs.pop("ux")
        fe.add_boundary_condition(BoundaryCondition(name=f"trace_{n}", node_ids=[n], dof_constraints=dofs))
    # slave row imposed on the independent coordinates (dependent: the interior master m2)
    fe.add_constraint_equation(terms=((m2x, 0.5), (m1x, 0.5)), rhs=float(gval[s][0]),
                               source_id="trace_slave_row", dependent_dof=m2x)
    audit = audit_constraints(fe)
    u, info = solve_linear(fe, built.load_case)
    u = np.asarray(u, dtype=float)
    k, f = full_k_f(built)
    r = k @ u - f

    expected_m2x = 2.0 * gval[s][0] - gval[m1][0]
    lam_m1x = r[m1x] - r[m2x]
    lam_sx = 2.0 * r[m2x] + r[sx]
    # equilibrium of every q-DOF that is neither a cut DOF nor m2x must hold
    cut_dofs = {d for n in cut for d in dm.get_node_dofs(n)}
    free_rows = [d for d in range(len(u)) if d not in cut_dofs and d != m2x
                 and d not in {dd for n in m.nodes for dd in dm.get_node_dofs(n)[2:5]}]  # bending held by support
    # applied load total in x and y
    lam = {d: r[d] for d in cut_dofs}
    lam[m1x], lam[sx] = lam_m1x, lam_sx
    fx = sum(lam[dm.get_node_dofs(n)[0]] for n in cut)
    fy = sum(lam[dm.get_node_dofs(n)[1]] for n in cut)
    applied = np.array([2.0e5 * 1.0, 3.0e4 * 1.0])
    OUT["part1"] = {
        "nodes": {"s": s, "m1": m1, "m2": m2},
        "audit_issues": [(i.code, i.severity, i.message) for i in audit.issues if i.code != "CONSTRAINT002"],
        "duplicate_support_warnings": sum(i.code == "CONSTRAINT002" for i in audit.issues),
        "u_sx_minus_gs": float(u[sx] - gval[s][0]),
        "u_m1x_minus_g": float(u[m1x] - gval[m1][0]),
        "u_m2x_minus_expected": float(u[m2x] - expected_m2x),
        "mpc_residual": float(u[sx] - 0.5 * u[m1x] - 0.5 * u[m2x]),
        "max_free_equilibrium_residual": float(np.max(np.abs(r[free_rows]))) if free_rows else 0.0,
        "lambda_m1x": float(lam_m1x), "lambda_sx": float(lam_sx),
        "raw_r_m1x": float(r[m1x]), "raw_r_sx": float(r[sx]), "raw_r_m2x": float(r[m2x]),
        "free_body_x": float(fx + applied[0]), "free_body_y": float(fy + applied[1]),
        "applied": applied,
        "backward_error": float(info["convergence_info"]["relative_backward_error"]),
    }
    print("PART 1", {k: v for k, v in OUT["part1"].items() if k != "audit_issues"})
    print("  audit:", OUT["part1"]["audit_issues"])


# ----------------------------------------------------------------------
# Part 2
# ----------------------------------------------------------------------
def part2():
    results = {}
    for case in ("slave_prescribed", "slave_prescribed_equal", "redundant", "inconsistent"):
        p, m = plate()
        built = L.build(p, m)
        fe = built.fe_model
        dm = fe.mesh.dof_manager
        s, mm = node_at(m, (0, 1)), node_at(m, (0, 0.75))
        sx, mx = dm.get_node_dofs(s)[0], dm.get_node_dofs(mm)[0]
        fe.add_constraint_equation(terms=((sx, 1.0), (mx, -1.0)), rhs=0.0, source_id="u_s=u_m", dependent_dof=sx)
        cut = sorted(n for n, x in m.nodes.items() if abs(x[0]) < 1e-12)
        for n in cut:
            dofs = dict(zip(L.DOF_NAMES, g_field(np.asarray(m.nodes[n]))))
            if n == s and not case.startswith("slave_prescribed"):
                dofs.pop("ux")
            if n == s and case == "slave_prescribed_equal":
                dofs["ux"] = float(g_field(np.asarray(m.nodes[mm]))[0])
            fe.add_boundary_condition(BoundaryCondition(name=f"trace_{n}", node_ids=[n], dof_constraints=dofs))
        gm = float(g_field(np.asarray(m.nodes[mm]))[0])
        if case == "redundant":
            fe.add_constraint_equation(terms=((mx, 1.0),), rhs=gm, source_id="slave_row_on_q", dependent_dof=mx)
        if case == "inconsistent":
            fe.add_constraint_equation(terms=((mx, 1.0),), rhs=gm + 1.0e-5, source_id="slave_row_on_q", dependent_dof=mx)
        audit = audit_constraints(fe)
        try:
            u, info = solve_linear(fe, built.load_case)
            outcome = f"solved, u_s - u_m = {float(u[sx] - u[mx]):.2e}"
        except Exception as error:
            outcome = f"{type(error).__name__}: {str(error)[:160]}"
        results[case] = {"audit": [(i.code, i.severity, i.message[:120]) for i in audit.issues
                                   if i.code != "CONSTRAINT002"],
                         "duplicate_support_warnings": sum(i.code == "CONSTRAINT002" for i in audit.issues),
                         "outcome": outcome}
        print("PART 2", case, results[case])
    OUT["part2"] = results


# ----------------------------------------------------------------------
# Part 3: ErrorAllowance v2
# ----------------------------------------------------------------------
def scaled_quantities(built, u):
    """eta_s, kappa_s, scaling S, y on the solver's own equilibrated operator."""
    fe = built.fe_model
    with AnalysisSession(fe) as sess:
        op = sess.static_operator_plan()
        k_full = sparse.csr_matrix(sess.stiffness_plan().matrix)
        con = op.constraint
        ks = sparse.csc_matrix(op.matrix)
        scale = np.asarray(op.scaling, dtype=float)
    f, _ = assemble_load_vector(fe, built.load_case)
    f = np.asarray(f, dtype=float)
    t = sparse.csr_matrix(con.T)
    u0 = np.asarray(con.u0, dtype=float)
    q, *_ = sparse.linalg.lsqr(t, u - u0, atol=1e-16, btol=1e-16, iter_lim=10000)[:1], None
    q = np.asarray(q[0], dtype=float)
    reconstruction = float(np.max(np.abs(t @ q + u0 - u)))
    f_red = con.reduce_load(k_full, f)
    y = q / scale
    rs = ks @ y - scale * f_red
    norm_ks = float(abs(ks).sum(axis=1).max())
    eta_s = float(np.max(np.abs(rs)) / (norm_ks * np.max(np.abs(y)) + np.max(np.abs(scale * f_red))))
    lu = splu(ks)
    n = ks.shape[0]
    inv = LinearOperator((n, n), matvec=lu.solve, rmatvec=lambda x: lu.solve(x, trans="T"), dtype=float)
    norm_inv = float(onenormest(inv))
    kappa_s = norm_ks * norm_inv
    dq = scale * kappa_s * eta_s * float(np.max(np.abs(y)))
    du = np.abs(t) @ dq
    return {"k": k_full, "f": f, "t": t, "scale": scale, "eta_s": eta_s, "kappa_s": kappa_s,
            "norm_inv_s": norm_inv, "du": du, "reconstruction": reconstruction, "ks": ks, "lu": lu}


def matvec_floor(k, u, f):
    k = sparse.csr_matrix(k)
    nnz = np.diff(k.indptr)
    gamma = nnz * EPS / (1.0 - nnz * EPS)
    return gamma * (abs(k) @ np.abs(u)) + EPS * np.abs(f)


def part3():
    import importlib

    e2 = importlib.import_module("e2_a1_restriction")
    # global
    gp = L.new_project("global")
    gfaces = L.rectangle_faces(gp, e2.LINES, e2.LINES)
    e2.load_and_support(gp, gfaces)
    gmesh = L.mesh(gp, e2.SIZE)
    gb = L.build(gp, gmesh)
    gs = L.solve(gb)
    gq = scaled_quantities(gb, gs.u)
    gvals = {n: gs.node_values(n) for n in gmesh.nodes}
    # inside
    ip = L.new_project("inside")
    ifaces = L.rectangle_faces(ip, e2.LINES[1:3], e2.LINES[1:3])
    e2.load_and_support(ip, ifaces, outer=False)
    imesh = L.mesh(ip, e2.SIZE)
    imap = e2.node_map(imesh, gmesh)
    cut_edges = L.boundary_edges(ip, ifaces.values())
    driven = sorted({n for e in cut_edges for n in imesh.nodes_on(ip.edge(e))})
    ib = L.build(ip, imesh, require_supports=False)
    L.drive(ib, {n: gvals[imap[n]] for n in driven})
    isol = L.solve(ib)
    iq = scaled_quantities(ib, isol.u)
    # per-DOF allowance for inside-vs-global interior agreement
    idm, gdm = ib.fe_model.mesh.dof_manager, gb.fe_model.mesh.dof_manager
    interior = sorted(set(imesh.nodes) - set(driven))
    # propagation of global boundary error into the inside interior (first order):
    # |dy_I| <= ||Ks_II^-1||_1 * ||Ks_I,Gamma||_1 * max|dy_Gamma|, in the inside scaling.
    g_du = gq["du"]
    boundary_dy = max(float(g_du[gdm.get_node_dofs(imap[n])[c]] / iq["scale"].max()) for n in driven for c in range(6))
    propagation = iq["norm_inv_s"]  # ||Ks_in^-1||, with ||Ks_I,Gamma|| <= ||Ks|| ~ O(1); conservative
    ratios_t, ratios_r = [], []
    for n in interior:
        for c in range(6):
            di, dg = idm.get_node_dofs(n)[c], gdm.get_node_dofs(imap[n])[c]
            err = abs(isol.u[di] - gs.u[dg])
            allowance = iq["du"][di] + g_du[dg]
            (ratios_t if c < 3 else ratios_r).append(err / allowance if allowance > 0 else np.inf)
    # Newton across the cut with the outside model
    op_ = L.new_project("outside")
    ofaces = L.rectangle_faces(op_, e2.LINES, e2.LINES, skip={(1, 1)})
    e2.load_and_support(op_, ofaces)
    omesh = L.mesh(op_, e2.SIZE)
    omap = e2.node_map(omesh, gmesh)
    inv_imap = {imap[n]: n for n in driven}
    o_cut = sorted(n for n in omesh.nodes if omap[n] in inv_imap)
    ob = L.build(op_, omesh)
    L.drive(ob, {n: gvals[omap[n]] for n in o_cut})
    osol = L.solve(ob)
    oq = scaled_quantities(ob, osol.u)
    ri = iq["k"] @ isol.u - iq["f"]
    ro = oq["k"] @ osol.u - oq["f"]
    rg = gq["k"] @ gs.u - gq["f"]
    odm = ob.fe_model.mesh.dof_manager
    newton_ratio = []
    for n in o_cut:
        ni = inv_imap[omap[n]]
        for c in range(6):
            di, do, dg = idm.get_node_dofs(ni)[c], odm.get_node_dofs(n)[c], gdm.get_node_dofs(omap[n])[c]
            value = abs(ri[di] + ro[do])
            allow = (abs(rg[dg])
                     + (abs(iq["k"][di]) @ iq["du"]).item() + (abs(oq["k"][do]) @ oq["du"]).item()
                     + float(matvec_floor(iq["k"][di], isol.u, iq["f"][di:di + 1])[0])
                     + float(matvec_floor(oq["k"][do], osol.u, oq["f"][do:do + 1])[0]))
            newton_ratio.append(value / allow if allow > 0 else np.inf)
    OUT["part3_a1"] = {
        "global": {"eta_s": gq["eta_s"], "kappa_s": gq["kappa_s"], "reconstruction": gq["reconstruction"],
                   "eta_reported_unscaled": gs.backward_error},
        "inside": {"eta_s": iq["eta_s"], "kappa_s": iq["kappa_s"], "reconstruction": iq["reconstruction"],
                   "eta_reported_unscaled": isol.backward_error},
        "interior_error_over_allowance_max_translation": float(max(ratios_t)),
        "interior_error_over_allowance_max_rotation": float(max(ratios_r)),
        "newton_over_allowance_max": float(max(newton_ratio)),
        "boundary_dy_note": boundary_dy, "propagation_norm_inv": propagation,
    }
    print("PART 3 A1", OUT["part3_a1"])

    # zero-load rigid motion (S0 E1 c, Q4 -> Q4, P2): interface force vs matvec floor
    import e1_a2_exact as e1

    gproj = L.new_project("global")
    L.rectangle_faces(gproj, [0.0, 1.0], [0.0, 1.0])
    gm2 = L.mesh(gproj, 0.25)
    sproj = L.new_project("sub")
    sf = L.rectangle_faces(sproj, [0.3, 0.7], [0.3, 0.7])
    sm = L.mesh(sproj, 0.05)
    cut = L.boundary_edges(sproj, sf.values())
    drv = sorted({n for e in cut for n in sm.nodes_on(sproj.edge(e))})
    tr = L.transfer(gm2, L.field_at(gm2.nodes, e1.field_c), sm, drv)
    sb = L.build(sproj, sm, require_loads=False, require_supports=False)
    L.drive(sb, tr.values)
    ss = L.solve(sb)
    sq = scaled_quantities(sb, ss.u)
    r = sq["k"] @ ss.u - sq["f"]
    sdm = sb.fe_model.mesh.dof_manager
    ratios = []
    for n in drv:
        for d in sdm.get_node_dofs(n):
            allow = (abs(sq["k"][d]) @ sq["du"]).item() + float(matvec_floor(sq["k"][d], ss.u, sq["f"][d:d + 1])[0])
            ratios.append(abs(r[d]) / allow if allow > 0 else np.inf)
    OUT["part3_rigid"] = {"max_force": float(np.max(np.abs([r[d] for n in drv for d in sdm.get_node_dofs(n)]))),
                          "force_over_allowance_max": float(max(ratios)),
                          "eta_s": sq["eta_s"], "kappa_s": sq["kappa_s"]}
    print("PART 3 rigid", OUT["part3_rigid"])


def main():
    part1()
    part2()
    part2b()
    part3()
    path = Path(__file__).with_name("results")
    path.mkdir(exist_ok=True)
    import json

    payload = {"environment": s0_env.verify(), **OUT}
    (path / "verify_c0.json").write_text(json.dumps(payload, indent=2, default=L._jsonable))
    print("saved", path / "verify_c0.json")



# ----------------------------------------------------------------------
# Part 2b: element-provided coupling (eccentric beam node) on the cut
# ----------------------------------------------------------------------
def part2b():
    from anysolver.mesh_gen import InterpolatedBeamShellMPCElement

    results = {}
    for case in ("beam_node_prescribed", "beam_node_follows"):
        p, m = plate()
        built = L.build(p, m)
        fe = built.fe_model
        dm = fe.mesh.dof_manager
        shell = node_at(m, (0, 0.5))
        x = np.asarray(m.nodes[shell], dtype=float)
        ecc = np.array([0.0, 0.0, 0.05])
        beam = max(fe.mesh.nodes) + 1
        fe.add_node(beam, *(x + ecc))
        fe.add_element(max(fe.mesh.elements) + 1,
                       InterpolatedBeamShellMPCElement(max(fe.mesh.elements) + 1, beam, [shell], np.array([1.0]), ecc))
        cut = sorted(n for n, xx in m.nodes.items() if abs(xx[0]) < 1e-12)
        for n in cut:
            fe.add_boundary_condition(BoundaryCondition(name=f"trace_{n}", node_ids=[n],
                                                        dof_constraints=dict(zip(L.DOF_NAMES, g_field(np.asarray(m.nodes[n]))))))
        expected = g_field(x).copy()
        expected[:3] = expected[:3] + np.cross(expected[3:], ecc)
        if case == "beam_node_prescribed":
            fe.add_boundary_condition(BoundaryCondition(name="trace_beam", node_ids=[beam],
                                                        dof_constraints=dict(zip(L.DOF_NAMES, expected))))
        try:
            u, info = solve_linear(fe, built.load_case)
            got = np.asarray(u)[dm.get_node_dofs(beam)]
            outcome = f"solved, beam node vs u + theta x e: {float(np.max(np.abs(got - expected))):.2e}"
        except Exception as error:
            outcome = f"{type(error).__name__}: {str(error)[:200]}"
        results[case] = outcome
        print("PART 2b", case, outcome)
    OUT["part2b"] = results


if __name__ == "__main__":
    main()
