"""S1 + S3a integration check on the S0 A1 case (element-aligned restriction).

Runs the new owner code from the worktrees (ANYsolver C:/ANYworkspaceWork/sm1,
ANYmesher C:/ANYworkspaceWork/sm3) on top of the S0 snapshot environment:
ANYfem model -> anymesher.submodel_correspondence.locate_for_transfer (lineage =
global central face) -> anysolver.submodel_transfer.interpolate_nodal_field ->
build/apply_driven_trace -> solve -> interface_forces, checked against the global
solution and, with Newton across the cut, against the complementary outside model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "s0"))
import s0_env  # noqa: E402,F401

sys.path.insert(0, r"C:\ANYworkspaceWork\sm1\src")
sys.path.insert(0, r"C:\ANYworkspaceWork\sm3\src")

import numpy as np  # noqa: E402

import anymesher  # noqa: E402
import anysolver  # noqa: E402
import e2_a1_restriction as e2  # noqa: E402
import s0_lib as L  # noqa: E402
from anymesher.submodel_correspondence import locate_for_transfer  # noqa: E402
from anysolver import solve_linear  # noqa: E402
from anysolver.submodel_transfer import (  # noqa: E402
    TransferPoint, apply_driven_trace, build_driven_trace, estimate_static_error_allowance,
    force_allowance, interface_forces, interpolate_nodal_field,
)


def drive(project, mesh, faces, source, policy, outer):
    """Build a driven model of ``project`` from the global ``source`` solution."""

    gmesh, gbuilt, gu, lineage = source
    cut_edges = [e for e in L.boundary_edges(project, faces.values())
                 if not outer or min(*(L.edge_midpoint(project, e)[:2]), *(1 - L.edge_midpoint(project, e)[:2])) > 1e-9]
    driven = sorted({n for e in cut_edges for n in mesh.nodes_on(project.edge(e))})
    points = {n: mesh.nodes[n] for n in driven}
    hits = locate_for_transfer(gmesh, points, candidate_elements={n: lineage for n in driven})
    transfer = interpolate_nodal_field(gbuilt.fe_model, gu, [
        TransferPoint(n, h.source_element_id, h.natural, tuple(points[n]), h.weights, h.inverse_residual, h.alternatives) for n, h in hits.items()])
    built = L.build(project, mesh, require_supports=outer)
    normals = {n: [(0.0, 0.0, 1.0)] for n in driven}
    trace = build_driven_trace(built.fe_model, {n: v.values for n, v in transfer.items()}, policy=policy, normals=normals)
    apply_driven_trace(built.fe_model, trace)
    u, info = solve_linear(built.fe_model, built.load_case)
    return built, np.asarray(u, float), trace, driven, hits


def main():
    out = {"anysolver": anysolver.__file__, "anymesher": anymesher.__file__}
    gp = L.new_project("global")
    gfaces = L.rectangle_faces(gp, e2.LINES, e2.LINES)
    e2.load_and_support(gp, gfaces)
    gmesh = L.mesh(gp, e2.SIZE)
    gbuilt = L.build(gp, gmesh)
    gsol = L.solve(gbuilt)
    gu = gsol.u
    gallow = estimate_static_error_allowance(gbuilt.fe_model, gu, gbuilt.load_case)
    gdm = gbuilt.fe_model.mesh.dof_manager

    ip = L.new_project("inside")
    ifaces = L.rectangle_faces(ip, e2.LINES[1:3], e2.LINES[1:3])
    e2.load_and_support(ip, ifaces, outer=False)
    imesh = L.mesh(ip, e2.SIZE)
    imap = e2.node_map(imesh, gmesh)
    central = tuple(gmesh.elements_on(gp.face(gfaces[1, 1])))
    for policy in ("drive_all", "release_normal_rotation"):
        built, u, trace, driven, hits = drive(ip, imesh, ifaces, (gmesh, gbuilt, gu, central), policy, outer=False)
        idm = built.fe_model.mesh.dof_manager
        allow = estimate_static_error_allowance(built.fe_model, u, built.load_case)
        interior = sorted(set(imesh.nodes) - set(driven))
        worst = 0.0
        for n in interior:
            for c in range(6):
                di, dg = idm.get_node_dofs(n)[c], gdm.get_node_dofs(imap[n])[c]
                worst = max(worst, abs(u[di] - gu[dg]) / (allow.displacement[di] + gallow.displacement[dg]))
        forces = interface_forces(built.fe_model, u, built.load_case, trace)
        total = sum(forces.node_forces.values())
        out[policy] = {
            "kept_rows": len(trace.kept), "redundant_rows": len(trace.redundant),
            "interior_error_over_allowance": worst,
            "max_abs_interior_translation_error": max(abs(u[idm.get_node_dofs(n)[c]] - gu[gdm.get_node_dofs(imap[n])[c]])
                                                      for n in interior for c in range(3)),
            "max_abs_interior_rotation_error": max(abs(u[idm.get_node_dofs(n)[c]] - gu[gdm.get_node_dofs(imap[n])[c]])
                                                   for n in interior for c in range(3, 6)),
            "cut_force_total_z_plus_pressure": float(total[2] + e2.PRESSURE * 0.16),
            "equilibrium_residual": forces.equilibrium_residual,
            "incident_faces_max": max(len(h.incident_source_faces) for h in hits.values()),
            "inconclusive": allow.inconclusive,
        }
        if policy == "drive_all":
            inside = (built, u, forces, idm)
        print(policy, out[policy], flush=True)

    # Newton across the cut with the outside model driven the same way.
    op = L.new_project("outside")
    ofaces = L.rectangle_faces(op, e2.LINES, e2.LINES, skip={(1, 1)})
    e2.load_and_support(op, ofaces)
    omesh = L.mesh(op, e2.SIZE)
    omap = e2.node_map(omesh, gmesh)
    ring = tuple(e for f, face in gfaces.items() if f != (1, 1) for e in gmesh.elements_on(gp.face(face)))
    obuilt, ou, otrace, odriven, _ = drive(op, omesh, ofaces, (gmesh, gbuilt, gu, ring), "drive_all", outer=True)
    oforces = interface_forces(obuilt.fe_model, ou, obuilt.load_case, otrace)
    ibuilt, iu, iforces, idm = inside
    iallow = estimate_static_error_allowance(ibuilt.fe_model, iu, ibuilt.load_case)
    oallow = estimate_static_error_allowance(obuilt.fe_model, ou, obuilt.load_case)
    ifloor = force_allowance(ibuilt.fe_model, iu, ibuilt.load_case, iallow.displacement)
    ofloor = force_allowance(obuilt.fe_model, ou, obuilt.load_case, oallow.displacement)
    gk = None
    from anysolver.analysis_session import AnalysisSession
    from anysolver.matrix_assembly import assemble_load_vector
    with AnalysisSession(gbuilt.fe_model) as session:
        gk = session.stiffness_plan().matrix
    gr = gk @ gu - np.asarray(assemble_load_vector(gbuilt.fe_model, gbuilt.load_case)[0])
    odm = obuilt.fe_model.mesh.dof_manager
    inv = {imap[n]: n for n in iforces.node_forces}
    ratio, worst_force = 0.0, 0.0
    for n, six in oforces.node_forces.items():
        g = omap[n]
        if g not in inv:
            continue
        ni = inv[g]
        for c in range(6):
            value = abs(iforces.node_forces[ni][c] + six[c])
            allowance = (abs(gr[gdm.get_node_dofs(g)[c]]) + ifloor[idm.get_node_dofs(ni)[c]]
                         + ofloor[odm.get_node_dofs(n)[c]])
            ratio = max(ratio, value / allowance)
            worst_force = max(worst_force, value)
    out["newton"] = {"max_abs": worst_force, "over_allowance": ratio}
    print("newton", out["newton"], flush=True)
    path = HERE / "results"
    path.mkdir(exist_ok=True)
    (path / "integration_a1.json").write_text(json.dumps(out, indent=2, default=float))
    print("saved", path / "integration_a1.json")


if __name__ == "__main__":
    main()
