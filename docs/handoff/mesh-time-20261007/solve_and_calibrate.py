import sys, time, pickle, numpy as np
from anyfem.application.examples import floating_barge_large, floating_barge_section
from anyfem.solve.run import solve_linear_static
kind = sys.argv[1] if len(sys.argv) > 1 else "line_loads"
p = floating_barge_large(kind)
t = time.time()
mesh = p.generate_mesh(p.target_size)
print("mesh", mesh.num_nodes, "nodes", mesh.num_elements, "elements", len(mesh.shells), "shells", len(mesh.beams), "beams", round(time.time()-t, 1), "s", flush=True)
t = time.time()
sol = solve_linear_static(p, mesh=mesh)
print("solve", round(time.time()-t, 1), "s", sol.summary(), flush=True)
r = sol.stresses()
rows = [(float(np.max(np.abs(s["von_mises"]))), eid) for eid, s in r.element_stresses.items() if s.get("von_mises") is not None]
vm, eid = max(rows)
print("peak von Mises MPa", vm/1e6, "element", eid, "beam" if eid in mesh.beams else "shell")
# stress on the opening flat bars and bulkheads
from anygeometry.entities import EntityRef
fb = {item.edge for item in ()}
bars = [i for i, n in p.edge_sections.items() if n.startswith("FB")]
bar_vm = [max(rows_ for rows_ in [float(np.max(np.abs(r.element_stresses[e]["von_mises"])))] ) for i in bars for e in mesh.elements_on(EntityRef("edge", i)) if e in r.element_stresses and r.element_stresses[e].get("von_mises") is not None]
print("FB beam elements", len(bar_vm), "peak FB von Mises MPa", max(bar_vm)/1e6 if bar_vm else None)
for f, ref in ((fid, EntityRef("face", fid)) for fid, face in p.geometry.faces.items() if face.holes):
    v = sol.face_von_mises(ref)
    print("bulkhead face", f, "elements", len(v), "peak MPa", max(v.values())/1e6)
