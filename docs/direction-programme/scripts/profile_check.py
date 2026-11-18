"""Where the shell-axis check spends its time on drawing_barge_60m (0.5 m quadratic quad_first adaptive)."""
import time, cProfile, pstats, io, warnings, numpy as np
from anyfem.application.examples import drawing_barge_60m
from anyfem.jobs import _cancellation_token
from anyfem.mesh_jobs import MeshSettings, run_mesh_job
from anymesher import MeshAutomationOptions
import anyfem.mesh_orientation as mo
from anyfem.solve.build import build_fe_model
project = drawing_barge_60m()
settings = MeshSettings.create(.5, element_order="quadratic", strategy="quad_first", layout_policy="adaptive",
    automation=MeshAutomationOptions(strict_method=False, allow_inspection=True))
mesh = project.mesh_session().generate_mesh(settings)  # published record (D1 round 6)
t0=time.perf_counter(); r=mo.project_orientation_report(project, mesh); total=time.perf_counter()-t0
t=time.perf_counter(); built=build_fe_model(project, mesh, load_case=None, require_loads=False, require_supports=False); tb=time.perf_counter()-t
fe=built.fe_model
els=[fe.mesh.elements[int(e)] for f in mesh.elements_of_face for e in mesh.elements_of_face[f]]
t=time.perf_counter()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    for el in els: el.compute_stresses(fe.mesh, np.zeros(int(el.total_dofs)), fe.materials[el.material_name])
ta=time.perf_counter()-t
probe=mo._probe_material()
t=time.perf_counter()
for el in els: mo.element_recovery_frames(el, fe.mesh, probe)
tp=time.perf_counter()-t
axes, unc = mo.model_recovery_frames(fe, mesh)
t=time.perf_counter(); mo.shell_orientation_report(project.geometry, mesh, axes, unc); tc=time.perf_counter()-t
print(f"TOTAL {total:.2f} s | build_fe_model {tb:.2f} | admission recovery {ta:.2f} | 6-field probe {tp:.2f} | classification {tc:.2f} | elements {len(els)}")
pr=cProfile.Profile(); pr.enable()
for el in els[:500]: mo.element_recovery_frames(el, fe.mesh, probe)
pr.disable(); s=io.StringIO(); pstats.Stats(pr,stream=s).sort_stats("tottime").print_stats(12); print("\n".join(s.getvalue().splitlines()[:30]))
