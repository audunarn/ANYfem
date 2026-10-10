"""Probe the public build/solve/post path used by the S0 spike (no assertions)."""

import s0_env  # noqa: F401  (must be first)

import inspect

import numpy as np

from anyfem import Project, pinned, steel
from anyfem.post.fields import available_fields, evaluate_field
from anyfem.post.results import LinearSolution
from anyfem.solve.build import build_fe_model
from anysolver import solve_linear

print(s0_env.verify()["anysolver"])
print("build_fe_model", inspect.signature(build_fe_model))

project = Project(name="probe")
project.add_material(steel("S355", 0.01))
project.add_plate_section("plate", thickness=0.01, material="S355")
g = project.geometry
pts = g.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
edges = g.add_polyline(pts, close=True)
face = g.add_face(edges)
project.assign_plate(face, "plate")
for e in edges:
    project.add_support(pinned(project.edge(e)))
project.load_case().add_pressure(project.face(face), 1000.0)

mesh = project.generate_mesh(0.25)
print("mesh nodes", mesh.num_nodes, "quads", len(mesh.quads), "tris", len(mesh.tris))
built = build_fe_model(project, mesh)
fe = built.fe_model
print("BuiltModel fields", [f for f in vars(built)])
kinds = {type(el).__name__ for el in fe.mesh.elements.values()}
print("element classes", kinds)
el = next(iter(fe.mesh.elements.values()))
print("element attrs with shape:", [n for n in dir(el) if "shape" in n.lower()])
print("element node_ids", el.node_ids, "mesh quad", mesh.quads.get(el.element_id) if hasattr(el, "element_id") else None)
first_q = next(iter(mesh.quads))
print("mesh quad id", first_q, mesh.quads[first_q], "fe element", fe.mesh.elements[first_q].node_ids)
print("material(s)", list(getattr(fe, "materials", {}).keys())[:3])
print("dof of node", first_q, fe.mesh.dof_manager.get_node_dofs(mesh.quads[first_q][0]))

u, info = solve_linear(fe, built.load_case)
print("solve info keys", sorted(info.keys()))
for key in sorted(info):
    if "backward" in key or "residual" in key or "cond" in key:
        print("  ", key, info[key])
sol = LinearSolution(displacements=u, built=built, info=info, label="probe")
print("max translation", sol.max_translation())
print("fields", available_fields())
