import sys, time, cProfile, pstats, io
import anymesher.hybrid as H
from anyfem.application import examples as E
from anyfem.mesh_execution import MeshExecutionPolicy
p = E.floating_barge_section(length=15., bulkheads=(5.,10.), girders=(0.,4.,8.,12.), openings=E.BARGE_LARGE_OPENINGS, stress_scale=E.BARGE_LARGE_STRESS_SCALE, target_size=.25)
pr = cProfile.Profile(); t = time.perf_counter(); pr.enable()
mesh = p.generate_mesh(p.target_size, order="linear", execution_policy=MeshExecutionPolicy.off())
pr.disable(); print("profiled wall", round(time.perf_counter()-t,1), "nodes", mesh.num_nodes, "shells", len(mesh.shells), "beams", len(mesh.beams), flush=True)
pr.dump_stats(sys.argv[1])
st = pstats.Stats(pr)
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("cumulative").print_callees("generate_hybrid_mesh_result")
lines = [l for l in s.getvalue().splitlines() if l.strip()]
rows = []
for l in lines:
    parts = l.split()
    if len(parts) >= 4 and parts[0].replace("/","").isdigit():
        try: rows.append((float(parts[3]), l))
        except ValueError: pass
for c, l in sorted(rows, reverse=True)[:22]: print(l[:200])
