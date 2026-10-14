import sys, time, functools
import anymesher.hybrid as H
from anyfem.application import examples as E
from anyfem.mesh_execution import MeshExecutionPolicy
calls = []; msgs = []
orig = H.generate_hybrid_mesh_result
def counted(*a, **k):
    calls.append((str(k.get("strategy")), bool(k.get("_refine_declared_junction_transition")), k.get("_evaluate_declared_junction_alignment", True)))
    return orig(*a, **k)
H.generate_hybrid_mesh_result = counted
orig_msg = H._quality_rejection_message
def m(q):
    s = orig_msg(q); msgs.append(s[:260]); return s
H._quality_rejection_message = m
variant = sys.argv[1]
B = E
if variant == "basic5": p = E.floating_barge_section()
elif variant == "large_plain": p = E.floating_barge_section(length=15., bulkheads=(5.,10.), girders=(0.,4.,8.,12.), stress_scale=E.BARGE_LARGE_STRESS_SCALE, target_size=.25)
elif variant == "large_rect": p = E.floating_barge_section(length=15., bulkheads=(5.,10.), girders=(0.,4.,8.,12.), openings=tuple(E.BulkheadOpening(y,1.25,1.4,1.1) for y in (-3.4,.1,3.6)), stress_scale=E.BARGE_LARGE_STRESS_SCALE, target_size=.25)
elif variant == "large_arcs": p = E.floating_barge_section(length=15., bulkheads=(5.,10.), girders=(0.,4.,8.,12.), openings=E.BARGE_LARGE_OPENINGS, stress_scale=E.BARGE_LARGE_STRESS_SCALE, target_size=.25)
elif variant == "large_stiff": p = E.floating_barge_section(length=15., bulkheads=(5.,10.), girders=(0.,4.,8.,12.), openings=tuple(E.BulkheadOpening(o.y,o.z,o.width,o.height) for o in E.BARGE_LARGE_OPENINGS), bulkhead_stiffener=E.BARGE_LARGE_BULKHEAD_STIFFENER, stress_scale=E.BARGE_LARGE_STRESS_SCALE, target_size=.25)
elif variant == "large_full": p = E.floating_barge_large()
t = time.perf_counter()
mesh = p.generate_mesh(p.target_size, order="linear", execution_policy=MeshExecutionPolicy.off())
print(f"== {variant}: {time.perf_counter()-t:.1f} s, nodes {mesh.num_nodes}, hybrid passes {len(calls)}")
for c in calls: print("   pass", c)
for s in msgs: print("   rejected:", s)
d = mesh.hybrid_diagnostics
print("   diag:", [k for k in d if "alignment" in k or "repair" in k or "reject" in k or "fallback" in k or "structured" in k][:10])
