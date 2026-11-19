"""Hash of the built 60 m barge FE model + build timing (O7 isotropic bit-identity)."""
import hashlib, json, sys, time
import numpy as np
from anyfem.application.examples import drawing_barge_60m
from anyfem.solve.build import build_fe_model

size = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
p = drawing_barge_60m()
t = time.perf_counter(); mesh = p.generate_mesh(size); tm = time.perf_counter() - t
times = []
for _ in range(3):
    t = time.perf_counter(); built = build_fe_model(p, mesh); times.append(time.perf_counter() - t)
fe = built.fe_model
h = hashlib.sha256()
def feed(x): h.update(repr(x).encode())
for k in sorted(fe.mesh.nodes): feed((k, np.asarray(fe.mesh.nodes[k], float).tolist() if not hasattr(fe.mesh.nodes[k], "x") else (fe.mesh.nodes[k].x, fe.mesh.nodes[k].y, fe.mesh.nodes[k].z)))
for k in sorted(fe.mesh.elements):
    e = fe.mesh.elements[k]
    d = e.to_dict() if hasattr(e, "to_dict") else {}
    feed((k, type(e).__name__, json.dumps(d, sort_keys=True, default=lambda o: np.asarray(o).tolist())))
for k in sorted(fe.materials): feed((k, repr(fe.materials[k])))
print("size", size, "elements", len(fe.mesh.elements), "mesh_s %.1f" % tm, "build_s", ["%.3f" % x for x in times], "sha256", h.hexdigest())
