"""The workbench runs solves in a separate worker process, with the same answers.

Real solvers through the real Qt workflow; only the long-running parts are synthetic.
"""
import os
import time
import pytest
if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)
pytest.importorskip("PySide6")
import numpy as np
from dataclasses import replace
from uuid import uuid4
from anyfem import commands as cmd
from anyfem.model.attributes import Support
from anyfem.model.records import AnalysisDefinition
from test_qt_geometry_actions import qapp, window  # noqa: F401  (shared Qt fixtures)
import job_process_helpers as helpers


def wait_until(qapp, predicate, timeout=120):
    end = time.monotonic() + timeout
    while not predicate():
        qapp.processEvents()
        time.sleep(0.01)
        assert time.monotonic() < end, "timed out"


def cantilever(window):
    a = window.run(cmd.AddPoint(0, 0, 0)); b = window.run(cmd.AddPoint(1, 0, 0))
    edge = window.run(cmd.AddLine(a, b))
    window.run(cmd.AddBeamSection(replace(window.project.beam_sections["stiffener"], id=str(uuid4()),
                                          name="centerline", offset_mode="centerline")))
    window.run(cmd.AssignBeam(edge, "centerline"))
    window.run(cmd.AddSupport(Support("fixed", window.project.geometry.entity_ref("vertex", a),
                                      dict.fromkeys(["ux", "uy", "uz", "rx", "ry", "rz"], 0))))
    window.run(cmd.AddPointLoad(window.project.geometry.entity_ref("vertex", b), force=(-1000, 0, 0)))


def solve(window, qapp, analysis, values, executor):
    window.job_manager.executor = executor
    window.solution = None
    panel = window.panels["Solve"]
    panel.analysis.setCurrentText(analysis)
    panel.controls.set_values(values)
    panel.start()
    job_id = window.active_job_id
    wait_until(qapp, lambda: window.solution is not None
               or str(getattr(window.project.jobs[job_id].status, "value", window.project.jobs[job_id].status)) == "failed")
    job = window.project.jobs[job_id]
    assert str(getattr(job.status, "value", job.status)) == "completed", panel.transcript.toPlainText()
    return window.solution, job_id


def test_the_workbench_defaults_to_a_worker_process(window):
    assert window.job_manager.executor == "process"


@pytest.mark.parametrize("analysis,values", [
    ("Linear static", {}),
    ("Capacity", {"num_buckling_modes": 1, "num_steps": 2, "max_load_factor": 0.01}),
])
def test_worker_process_gives_the_same_answer_as_in_process(window, qapp, analysis, values):
    cantilever(window)
    if analysis == "Capacity":
        load = window.project.load_cases["default"].point_loads[0]
        window.run(cmd.EditAttribute(replace(load, force=(-1000, 0, 0))))
    window.generate_mesh_async(0.5, strategy="auto")
    wait_until(qapp, lambda: window.mesh is not None)
    in_process, _ = solve(window, qapp, analysis, values, "thread")
    in_worker, job_id = solve(window, qapp, analysis, values, "process")
    request = window.job_manager._requests[job_id]
    assert request.executor == "process" and request.process is not None and request.process.exitcode == 0
    np.testing.assert_allclose(np.asarray(in_worker.displacements), np.asarray(in_process.displacements), rtol=1e-12, atol=0)
    assert in_worker.summary() == in_process.summary()
    # the result is fully usable in the GUI process: Results opens and shows it
    window.panels["Results"].show_results(); qapp.processEvents()
    assert window.panels["Results"].outcome.text()


def test_cancel_from_the_gui_reaches_the_worker(window, qapp):
    manager = window.job_manager
    manager.executor = "process"
    record = manager.submit(AnalysisDefinition("Job"), window.session.snapshot(), helpers.wait_for_cancel)
    window.active_job_id = record.id
    wait_until(qapp, lambda: any(entry["message"] == "ready" for entry in manager.log(record.id)))
    window.cancel_solve()
    wait_until(qapp, lambda: str(getattr(record.status, "value", record.status)) == "cancelled", timeout=30)


@pytest.mark.parametrize("action", ["close", "replace"])
def test_releasing_the_workbench_stops_the_worker(window, qapp, action):
    manager = window.job_manager
    manager.executor = "process"
    record = manager.submit(AnalysisDefinition("Job"), window.session.snapshot(), helpers.sleep_long)
    wait_until(qapp, lambda: any(entry["message"] == "sleeping" for entry in manager.log(record.id)))
    process = manager._requests[record.id].process
    assert process.is_alive()
    if action == "close":
        window.workbench.close()
    else:
        from anyfem import Project
        window._set_project(Project("Replacement"))
    process.join(20)
    assert not process.is_alive()
