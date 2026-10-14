import sys, time, functools, collections
import anymesher.native_cpp as n
import anyfem.structural_preparation, anymesher.structural_pipeline, anymesher.hybrid, anygeometry.component_partition, anygeometry.batch_intersections
import anymesher.component_parallel, anygeometry.attachment_remapping
from anyfem.application.examples import floating_barge_large
from anyfem.mesh_execution import MeshExecutionPolicy
totals = collections.Counter(); calls = collections.Counter(); depth = collections.Counter()
def wrap(fn, label):
    @functools.wraps(fn)
    def inner(*a, **k):
        depth[label] += 1; t = time.perf_counter()
        try: return fn(*a, **k)
        finally:
            depth[label] -= 1; calls[label] += 1
            if depth[label] == 0: totals[label] += time.perf_counter() - t
    return inner
targets = [("anyfem.structural_preparation", "prepare_structural_connectivity"),
           ("anygeometry.batch_intersections", "apply_intersections"),
           ("anygeometry.attachment_remapping", "remap_face_attachments"),
           ("anymesher.hybrid", "generate_hybrid_mesh_result"),
           ("anygeometry.component_partition", "plan_independent_components")]
originals = {}
from anymesher.structural_pipeline import StructuralMeshingPipeline as SMP
for meth in ("apply_connectivity", "_add_attachment_coupling"):
    setattr(SMP, meth, wrap(getattr(SMP, meth), meth)); originals[meth] = None
for mod, name in targets:
    originals[name] = getattr(sys.modules[mod], name)
for name, orig in originals.items():
    if orig is None: continue
    w = wrap(orig, name)
    for m in list(sys.modules.values()):
        if getattr(m, name, None) is orig: setattr(m, name, w)
label = sys.argv[1]
policy = MeshExecutionPolicy.off() if label == "off" else MeshExecutionPolicy.auto()
p = floating_barge_large(); t = time.perf_counter()
mesh = p.generate_mesh(p.target_size, order="linear", execution_policy=policy)
wall = time.perf_counter() - t
print(f"== {label}: wall {wall:.1f} s, nodes {mesh.num_nodes}, route {p._last_mesh_execution.get('route')} ({p._last_mesh_execution.get('reason')})")
for name in originals: print(f"  {name:34s} {totals[name]:7.1f} s  ({100*totals[name]/wall:4.1f}%)  calls {calls[name]}")
print(f"  unaccounted by the above after hybrid+prepare: {wall - totals['prepare_structural_connectivity'] - totals['generate_hybrid_mesh_result'] - totals['plan_independent_components']:.1f} s")
