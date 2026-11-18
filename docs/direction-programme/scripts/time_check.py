"""Time project_orientation_report on drawing_barge_60m (0.5 m quadratic quad_first adaptive), 3 runs."""
import time, collections
from anyfem.application.examples import drawing_barge_60m
from anyfem.jobs import _cancellation_token
from anyfem.mesh_jobs import MeshSettings, run_mesh_job
from anymesher import MeshAutomationOptions
import anyfem.mesh_orientation as mo
project = drawing_barge_60m()
settings = MeshSettings.create(.5, element_order="quadratic", strategy="quad_first", layout_policy="adaptive",
    automation=MeshAutomationOptions(strict_method=False, allow_inspection=True))
mesh = project.mesh_session().generate_mesh(settings)  # published record (D1 round 6)
times=[]
for _ in range(3):
    t=time.perf_counter(); r=mo.project_orientation_report(project, mesh); times.append(time.perf_counter()-t)
print("check s:", " ".join(f"{x:.2f}" for x in times), "| module", mo.__file__.split("\\")[-3])
print(r.summary().splitlines()[0][:230])
src = getattr(next(iter(r.element_axes.values())), "frame_source", "n/a")
print("frame sources", collections.Counter(getattr(a,"frame_source","n/a") for a in r.element_axes.values()))
