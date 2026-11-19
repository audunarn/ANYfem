"""O7 evidence: orthotropic shell material axes follow element node order.

Flat 1 m x 1 m plate, pressure, pinned edges, orthotropic E1 << E2.
Case A: mesh as generated.  Case B: nodes of quads with centroid x > 0.5 cyclically
rotated by one (same geometry, same normal, element local x turned 90 deg).
Prints max deflection and per-element local-x orientation (project_orientation_report).
Run with the D1 PYTHONPATH and: python o7_ortho_axes_probe.py [target_size]
"""
import sys
import numpy as np
from anyfem import Project
from anyfem.model.materials import Material
from anyfem.solve.build import build_fe_model
from anyfem.solve.run import solve_linear_static
from anyfem.mesh_orientation import project_orientation_report

def make(symmetry):
    from anyfem import pinned as pin
    p = Project(name="o7")
    if symmetry == "ortho":
        m = Material("M", symmetry="orthotropic", density=7850.0, constants=dict(
            elastic_modulus_1=40e9, elastic_modulus_2=200e9, elastic_modulus_3=100e9,
            poisson_ratio_12=0.05, poisson_ratio_13=0.25, poisson_ratio_23=0.25,
            shear_modulus_12=30e9, shear_modulus_13=30e9, shear_modulus_23=30e9))
    else:
        m = Material("M", 200e9, 0.3)
    p.add_material(m)
    p.add_plate_section("plate", thickness=0.01, material="M")
    g = p.geometry
    pts = g.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    edges = g.add_polyline(pts, close=True)
    face = g.add_face(edges)
    p.assign_plate(face, "plate")
    for e in edges:
        p.add_support(pin(p.edge(e)))
    p.load_case().add_pressure(p.face(face), 50_000.0)
    return p

def run(symmetry, rotate, size):
    p = make(symmetry)
    mesh = p.generate_mesh(size)
    n = 0
    if rotate:
        for eid, nodes in list(mesh.quads.items()):
            cx = np.mean([mesh.nodes[k][0] for k in nodes])
            if cx > 0.5:
                mesh.quads[eid] = list(nodes[1:]) + [nodes[0]]; n += 1
    built = build_fe_model(p, mesh)
    sol = solve_linear_static(built=built)
    return p, mesh, sol, n

if __name__ == "__main__":
    size = float(sys.argv[1]) if len(sys.argv) > 1 else 0.125
    for sym in ("iso", "ortho"):
        res = {}
        for rot in (False, True):
            p, mesh, sol, n = run(sym, rot, size)
            res[rot] = sol
            print(sym, "rotated" if rot else "plain", "n_rot", n, "ntri", len(mesh.tris),
                  "max_translation", sol.max_translation())
