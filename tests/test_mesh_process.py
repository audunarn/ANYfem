"""Meshing in a separate worker process: the same mesh, the same events, no GUI contention.

Pressing Generate mesh on the barge example froze the window for about 10 s: the mesh
thread's Python work took the GUI thread's interpreter lock and a repaint that costs
0.2 s when idle took ten.  See anyfem.job_process.
"""

from __future__ import annotations

import time

import pytest

from anyfem.document import DocumentSession
from anyfem.mesh_jobs import MeshProgress, MeshSettings, MeshTaskManager
from anyfem.model.project import Project


def plate_project(name="mesh process"):
    project = Project(name)
    vertices = project.geometry.add_points(((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 1.0, 0.0), (0.0, 1.0, 0.0)))
    project.geometry.add_plate(vertices)
    return project


def run(manager, project, settings, job_id="job", timeout=120.0):
    # a Project is snapshotted as given; pass the same object to compare executors on identical input
    manager.submit(job_id, DocumentSession(project).snapshot(), settings)
    events, deadline = [], time.monotonic() + timeout
    while time.monotonic() < deadline:
        events.extend(manager.poll())
        if any(e.kind in ("completed", "cancelled", "failed") for e in events):
            return events
        time.sleep(0.02)
    raise AssertionError("mesh job did not finish")


def final(events):
    return next(e for e in events if e.kind in ("completed", "cancelled", "failed"))


def test_worker_process_gives_the_same_mesh_and_the_same_progress():
    settings = MeshSettings.create(0.25, element_order="linear")
    project = plate_project()                      # one project: the mesh hash includes the model hash
    in_process = run(MeshTaskManager(), project, settings)
    in_worker = run(MeshTaskManager(executor="process"), project, settings)
    a, b = final(in_process).payload, final(in_worker).payload
    assert final(in_worker).kind == "completed"
    assert (b.mesh.num_nodes, b.mesh.num_elements) == (a.mesh.num_nodes, a.mesh.num_elements)
    assert b.mesh_hash == a.mesh_hash                                  # the semantic hash survives the codec
    assert b.quality == a.quality
    # the mesher stamps every run (thread or worker) with a fresh working-model UUID; all else must match
    identity = lambda prep: {k: v for k, v in prep.items() if k != "working_model_id"}
    assert identity(b.structural_preparation) == identity(a.structural_preparation)
    assert b.structural_preparation["working_model_id"] != a.structural_preparation["working_model_id"]
    # actual meshing stages are identical across executors; execution-route
    # notices are route-specific and asserted explicitly below
    meshing = lambda events: [
        (e.payload.stage, e.payload.fraction)
        for e in events
        if e.kind == "progress" and e.payload.stage != "execution"
    ]
    assert meshing(in_worker) == meshing(in_process) and meshing(in_worker)
    notices = lambda events: [
        (e.payload.fraction, e.payload.message)
        for e in events
        if e.kind == "progress" and e.payload.stage == "execution"
    ]
    # the thread executor cannot contain a parallel worker tree: it announces
    # the forced serial route before meshing, then reports the resolved route
    assert any(
        fraction == 0.15 and "thread executor" in message
        for fraction, message in notices(in_process)
    )
    assert any(
        fraction == 0.8 and message.startswith("mesh route:")
        for fraction, message in notices(in_process)
    )
    # the worker process reports exactly its own resolved route: no
    # forced-serial fallback notice precedes it
    assert [
        (fraction, message.startswith("mesh route: serial"))
        for fraction, message in notices(in_worker)
    ] == [(0.8, True)]
    assert all(isinstance(e.payload, MeshProgress) for e in in_worker if e.kind == "progress")


def test_the_mesh_returned_from_the_worker_is_a_usable_mesh():
    events = run(MeshTaskManager(executor="process"), plate_project(), MeshSettings.create(0.25, element_order="linear"))
    mesh = final(events).payload.mesh
    assert mesh.num_elements > 0 and callable(mesh.nodes_on)
    # a mesh saved and reopened in ANYfem is rebuilt through the same codec
    from anymesher.serialize import mesh_from_dict, mesh_to_dict

    assert mesh_from_dict(mesh_to_dict(mesh)).num_elements == mesh.num_elements


def test_a_meshing_failure_keeps_its_message_and_structured_diagnostic():
    def failing_project():
        project = Project("nothing to mesh")        # no geometry: the mesher refuses
        return project

    settings = MeshSettings.create(0.25, element_order="linear")
    in_process = final(run(MeshTaskManager(), failing_project(), settings))
    in_worker = final(run(MeshTaskManager(executor="process"), failing_project(), settings))
    assert in_process.kind == in_worker.kind == "failed"
    assert in_worker.message == in_process.message
    assert in_worker.payload["type"] == in_process.payload["type"]
    assert set(in_worker.payload) >= {"type", "message", "traceback"}
    assert "details" in in_worker.payload.keys() or "details" not in in_process.payload.keys()


def test_cancelling_reaches_the_worker():
    from anyfem.application.examples import floating_barge_section

    project = floating_barge_section()
    manager = MeshTaskManager(executor="process")
    manager.submit("barge", DocumentSession(project).snapshot(), MeshSettings.create(0.7, element_order="linear"))
    events, deadline = [], time.monotonic() + 120
    while not any(e.kind == "progress" and e.payload.stage == "generation" for e in events):
        events.extend(manager.poll())
        assert time.monotonic() < deadline, "the worker never reported the generation stage"
        time.sleep(0.02)
    assert manager.cancel("barge")
    while not any(e.kind in ("cancelled", "completed", "failed") for e in events):
        events.extend(manager.poll())
        assert time.monotonic() < deadline, "cancellation never took effect"
        time.sleep(0.02)
    assert final(events).kind == "cancelled"


def test_shutdown_stops_the_mesh_worker_at_once():
    from anyfem.application.examples import floating_barge_section

    manager = MeshTaskManager(executor="process")
    manager.submit("barge", DocumentSession(floating_barge_section()).snapshot(), MeshSettings.create(0.7, element_order="linear"))
    deadline = time.monotonic() + 120
    while manager._process is None:
        assert time.monotonic() < deadline, "no worker was started"
        time.sleep(0.02)
    process = manager._process
    assert process.is_alive()
    manager.shutdown()
    process.join(20)
    assert not process.is_alive()


def test_executor_selection(monkeypatch):
    assert MeshTaskManager().executor == "thread"                # the library default is unchanged
    assert MeshTaskManager(executor="process").executor == "process"
    monkeypatch.setenv("ANYFEM_JOB_EXECUTOR", "process")
    assert MeshTaskManager().executor == "process" and MeshTaskManager(executor="thread").executor == "thread"
    with pytest.raises(ValueError, match="unknown job executor"):
        MeshTaskManager(executor="gpu")
