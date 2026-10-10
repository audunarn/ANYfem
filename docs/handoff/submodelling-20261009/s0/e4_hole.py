"""S0 / A3-lite + A4-lite + A8-lite: plate with a central hole, global model WITH the hole.

Strip W = 0.5 m, d = 0.1 m (d/W = 0.2), length 2 m (ends 2W from the hole), t = 10 mm.
Gross nominal stress s0 = 100 MPa (line load 1e6 N/m on x = +1, x = -1 held in x,
one corner held in y, bending DOFs held on every face; membrane problem only).

Hole: hand-built O-grid of four mapped patches on an explicit Plane surface (arcs and
spokes pinned to n divisions, n even, so nodes sit at the peak points (0, +-r)).
Two owner routes fail on HEAD and are S0 findings: the legacy butterfly decomposition
fails structural preparation ("face has no qualified analytic or bilinear support"),
and a plain neutral trimmed hole (anygeometry punch_hole) fails the mesher's
qualified-S3 repair admission at every size tried.

Peak: sigma_xx at (0, +-r) from ANYsolver's Gauss-extrapolated, nodally averaged
recovery (membrane = mean of top and bottom).  Kt = peak / s0 (gross).

Runs (n = divisions per arc and spoke): reference = the global model itself at
n in {16, 32, 64} (= in-place refinement, the A8 comparator); coarse sources at
n in {2, 4, 8}; submodels driven from each source at n in {8, 16, 32}, box A
(half 2d x 1.75d) and box B (half 1.25d x 1.25d, source n = 4 only).
Oracle (provisional, to be pinned before S6): Howland gross Kt for d/W = 0.2,
cross-checked with Heywood's net-section fit Kt_n = 2 + (1 - d/W)^3, Kt_g = Kt_n/(1 - d/W).
"""

from __future__ import annotations

import s0_env  # noqa: F401

import sys
import time

import numpy as np

import s0_lib as L
from anyfem.model import support
from anygeometry.surfaces import Plane
from anysolver.results import recover_nodal_stresses

W, D, LENGTH = 0.5, 0.1, 2.0
R = D / 2
SIGMA0 = 100.0e6
LINE_LOAD = SIGMA0 * L.THICKNESS
HEYWOOD_GROSS = (2.0 + (1.0 - D / W) ** 3) / (1.0 - D / W)
PLANE = Plane(origin=(0.0, 0.0, 0.0), u_vector=(1.0, 0.0, 0.0), v_vector=(0.0, 1.0, 0.0))


def ogrid(project, bx, by, *, side_x=None):
    """Four mapped patches around the hole; optional side rectangles to +-side_x."""

    g = project.geometry
    corners = [(-bx, -by), (bx, -by), (bx, by), (-bx, by)]
    c = [g.add_point(x, y, 0.0) for x, y in corners]
    pts = []
    for x, y in corners:
        rho = float(np.hypot(x, y))
        pts.append(g.add_point(R * x / rho, R * y / rho, 0.0))
    vias = [(0.0, -R), (R, 0.0), (0.0, R), (-R, 0.0)]
    arcs, spokes, sides = [], [], []
    for k in range(4):
        via = g.add_point(vias[k][0], vias[k][1], 0.0)
        arcs.append(g.add_arc(pts[k], via, pts[(k + 1) % 4]))
        spokes.append(g.add_line(c[k], pts[k]))
        sides.append(g.add_line(c[k], c[(k + 1) % 4]))
    faces = [g.add_face([sides[k], spokes[(k + 1) % 4], arcs[k], spokes[k]], surface=PLANE) for k in range(4)]
    if side_x is not None:
        far = [g.add_point(-side_x, -by, 0.0), g.add_point(side_x, -by, 0.0),
               g.add_point(side_x, by, 0.0), g.add_point(-side_x, by, 0.0)]
        left = [g.add_line(far[0], c[0]), sides[3], g.add_line(c[3], far[3]), g.add_line(far[3], far[0])]
        right = [g.add_line(c[1], far[1]), g.add_line(far[1], far[2]), g.add_line(far[2], c[2]), sides[1]]
        faces.append(g.add_face(left, surface=PLANE))
        faces.append(g.add_face(right, surface=PLANE))
    for f in faces:
        project.assign_plate(f, "plate")
    return arcs, spokes


def membrane_support(project):
    for f in project.geometry.faces:
        project.add_support(support(project.face(f), uz=0.0, rx=0.0, ry=0.0))


def global_project(n):
    p = L.new_project(f"global_n{n}")
    arcs, spokes = ogrid(p, W / 2, W / 2, side_x=LENGTH / 2)
    g = p.geometry
    lc = p.load_case()
    for e, edge in g.edges.items():
        a = np.asarray(g.vertex_position(edge.start)); b = np.asarray(g.vertex_position(edge.end))
        if abs(a[0] + 1) < 1e-9 and abs(b[0] + 1) < 1e-9:
            p.add_support(support(p.edge(e), ux=0.0))
        if abs(a[0] - 1) < 1e-9 and abs(b[0] - 1) < 1e-9:
            lc.add_line_load(p.edge(e), (LINE_LOAD, 0.0, 0.0))
    corner = next(v for v in g.vertices if np.allclose(g.vertex_position(v), (-1.0, -W / 2, 0.0)))
    p.add_support(support(p.point(corner), uy=0.0))
    membrane_support(p)
    mesh_ = p.generate_mesh(W / n, overrides={e: n for e in (*arcs, *spokes)})
    return p, mesh_


def sub_project(bx, by, n):
    p = L.new_project(f"sub_{bx}_{by}_n{n}")
    arcs, spokes = ogrid(p, bx, by)
    membrane_support(p)
    mesh_ = p.generate_mesh(2 * min(bx, by) / n, overrides={e: n for e in (*arcs, *spokes)})
    g = p.geometry
    cut = []
    for e, edge in g.edges.items():
        a = np.asarray(g.vertex_position(edge.start)); b = np.asarray(g.vertex_position(edge.end))
        on_x = abs(abs(a[0]) - bx) < 1e-9 and abs(a[0] - b[0]) < 1e-9
        on_y = abs(abs(a[1]) - by) < 1e-9 and abs(a[1] - b[1]) < 1e-9
        if on_x or on_y:
            cut.append(e)
    driven = sorted({k for e in cut for k in mesh_.nodes_on(p.edge(e))})
    return p, mesh_, driven


def peak(built, u, mesh_):
    rec = recover_nodal_stresses(built.fe_model, u)
    out = {}
    for label, y in (("top", R), ("bottom", -R)):
        node = min(mesh_.nodes, key=lambda m: np.linalg.norm(np.asarray(mesh_.nodes[m]) - (0.0, y, 0.0)))
        values = rec["nodal"][node]
        out[label] = {
            "node": int(node),
            "distance": float(np.linalg.norm(np.asarray(mesh_.nodes[node]) - (0.0, y, 0.0))),
            "sxx": 0.5 * (values["global_xx_top"] + values["global_xx_bot"]),
        }
    out["skipped_elements"] = len(rec["skipped_element_ids"])
    out["kt"] = 0.5 * (out["top"]["sxx"] + out["bottom"]["sxx"]) / SIGMA0
    return out


def richardson(values):
    k1, k2, k3 = values
    if k2 == k3:
        return {"order": None, "extrapolated": k3, "note": "converged"}
    ratio = (k1 - k2) / (k2 - k3)
    if ratio <= 1.0:
        return {"order": None, "extrapolated": None, "note": f"non-monotone ratio {ratio:.3f}"}
    p = float(np.log2(ratio))
    return {"order": p, "extrapolated": k3 + (k3 - k2) / (2 ** p - 1),
            "estimated_error_finest": abs(k3 - k2) / (2 ** p - 1)}


def main():
    rows = {"reference": [], "submodel": []}
    sources = {}
    levels = [int(a) for a in sys.argv[1:]] or [2, 4, 8, 16, 32, 64]
    for n in levels:
        t = time.perf_counter()
        p, m = global_project(n)
        built = L.build(p, m)
        s = L.solve(built)
        pk = peak(built, s.u, m)
        rows["reference"].append({"n": n, "elements": m.num_elements, "quads": len(m.quads), "tris": len(m.tris),
                                  "kt": pk["kt"], "peak": pk, "backward_error": s.backward_error,
                                  "seconds": time.perf_counter() - t})
        print(f"global n={n:3d} elems {m.num_elements:6d} (tris {len(m.tris)}) Kt {pk['kt']:.5f} "
              f"(node dist {pk['top']['distance']:.1e}, skipped {pk['skipped_elements']})  "
              f"{time.perf_counter() - t:.1f}s", flush=True)
        if n in (2, 4, 8):
            sources[n] = (m, {k: s.node_values(k) for k in m.nodes})
    ref_levels = [r for r in rows["reference"] if r["n"] in (16, 32, 64)]
    if len(ref_levels) == 3:
        rows["reference_richardson"] = richardson([r["kt"] for r in ref_levels])
        print("reference Richardson", rows["reference_richardson"], flush=True)

    for box_name, (bx, by) in {"A": (2 * D, 1.75 * D), "B": (1.25 * D, 1.25 * D)}.items():
        for ng, (gm, gv) in sources.items():
            if box_name == "B" and ng != 4:
                continue
            kts = []
            for ns in (8, 16, 32):
                t = time.perf_counter()
                sp, sm, driven = sub_project(bx, by, ns)
                tr = L.transfer(gm, gv, sm, driven)
                sb = L.build(sp, sm, require_loads=False)
                L.drive(sb, tr.values)
                ss = L.solve(sb)
                pk = peak(sb, ss.u, sm)
                kts.append(pk["kt"])
                rows["submodel"].append({"box": box_name, "half": (bx, by), "global_n": ng, "sub_n": ns,
                                         "elements": sm.num_elements, "driven": len(driven), "kt": pk["kt"],
                                         "ambiguous_nodes": len(tr.ambiguous), "hits_max": tr.candidates_max,
                                         "backward_error": ss.backward_error, "seconds": time.perf_counter() - t})
                print(f"box {box_name} global n={ng} sub n={ns:3d} elems {sm.num_elements:5d} Kt {pk['kt']:.5f} "
                      f"amb {len(tr.ambiguous)} hits<= {tr.candidates_max}  {time.perf_counter() - t:.1f}s", flush=True)
            rr = richardson(kts)
            rows.setdefault("submodel_richardson", []).append({"box": box_name, "global_n": ng, **rr})
            print(f"   box {box_name} global n={ng} Richardson {rr}", flush=True)
    rows["heywood_gross_kt"] = HEYWOOD_GROSS
    rows["howland_gross_kt_provisional"] = 3.14
    print("Heywood gross", HEYWOOD_GROSS)
    print("saved", L.save("e4_hole", rows))


if __name__ == "__main__":
    main()
