import sys, time, collections, numpy as np
import anymesher.hybrid as H
from anyfem.application import examples as E
from anyfem.mesh_execution import MeshExecutionPolicy
reports = []
orig = H._structured_quality_report
def cap(mesh, options):
    q = orig(mesh, options); reports.append((q, mesh)); return q
H._structured_quality_report = cap
p = E.floating_barge_large()
mesh = p.generate_mesh(p.target_size, order="linear", execution_policy=MeshExecutionPolicy.off())
print("reports", len(reports), [bool(q["accepted"]) for q, _ in reports])
q, m = next(((q, m) for q, m in reports if not q["accepted"]))
print("keys", list(q.keys()))
for k, v in q.items():
    if k != "accepted": print(" ", k, str(v)[:400])
print("---- where are the growth violations (first rejected report)")
face_of = {}
for fid, els in m.elements_of_face.items():
    for e in els: face_of[e] = fid
cnt = collections.Counter()
for a, b, r in q["growth_violation_pairs"]:
    cnt[(face_of.get(a), face_of.get(b))] += 1
print("faces (a,b) -> count", dict(cnt))
g = p.geometry
print({fid: [ (round(float(g.vertex_position(g.edges[it.edge].start)[0]),2)) for it in g.faces[fid].loop][:1] for fid in g.faces})
# size of the offending element pairs: centroid + edge-length range for a few
def centroid(e):
    nodes = m.quads.get(e, m.tris.get(e)); pts = np.array([m.nodes[n] for n in nodes]); return pts.mean(axis=0), np.ptp(pts, axis=0)
for a, b, r in sorted(q["growth_violation_pairs"], key=lambda t: -t[2])[:6]:
    (ca, ea), (cb, eb) = centroid(a), centroid(b)
    print(f"ratio {r:.2f}  A {a} at {np.round(ca,2)} extent {np.round(ea,2)}   B {b} at {np.round(cb,2)} extent {np.round(eb,2)}")
