import time, anymesher, anygeometry, anymesher.native_cpp as n
print("mesher", anymesher.__file__); print("geometry", anygeometry.__file__)
print("native", n.NATIVE_CPP_AVAILABLE, "v2", n.COMPILED_NATIVE_V2_AVAILABLE, "quality", n.COMPILED_QUALITY_PIPELINE_AVAILABLE, "tri", n.COMPILED_TRIANGULATION_AVAILABLE, flush=True)
from anyfem.application.examples import floating_barge_large
p = floating_barge_large(); t = time.time()
mesh = p.generate_mesh(p.target_size)
print("mesh", mesh.num_nodes, len(mesh.shells), len(mesh.beams), round(time.time()-t, 1), "s", flush=True)
