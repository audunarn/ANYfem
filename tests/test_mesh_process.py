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


def _fake_parallel_api(monkeypatch):
    # Hermetic containment route: no installed ANYmesh is probed, so the
    # registration/shutdown interleavings below never mesh anything.
    monkeypatch.setattr(
        "anyfem.mesh_execution.parallel_mesh_api",
        lambda: (lambda model, **parameters: None, object, "mesh"),
    )


def test_a_worker_registered_after_shutdown_is_stopped_and_refused(monkeypatch):
    import threading

    from anyfem import mesh_jobs
    from anyfem.job_process import WorkerProcessError

    entered = threading.Event()
    release = threading.Event()
    stopped = []
    refused = []

    class LateWorker:
        def is_alive(self):
            return True

        def terminate(self):
            stopped.append(True)

    def fake_run(**kwargs):
        entered.set()
        assert release.wait(10.0), "shutdown never ran"
        try:
            kwargs["on_process"](LateWorker())
        except RuntimeError as error:
            refused.append(str(error))
        raise WorkerProcessError("the worker was never dispatched")

    monkeypatch.setattr(mesh_jobs, "run_in_worker_process", fake_run)
    _fake_parallel_api(monkeypatch)
    manager = MeshTaskManager(executor="process")
    future = manager.submit(
        "late",
        DocumentSession(plate_project()).snapshot(),
        MeshSettings.create(0.25, element_order="linear"),
    )
    assert entered.wait(10.0), "the spawn never began"
    manager.shutdown()
    release.set()
    future.result(timeout=10.0)
    events = manager.poll()
    assert stopped == [True], "a worker registered after shutdown must be stopped at once"
    assert refused and "closing" in refused[0]
    assert any(e.kind == "cancelled" for e in events), (
        "the cancelled token must report the job as cancelled"
    )


def test_shutdown_stops_the_fallback_worker_not_the_refused_attempt_tree(monkeypatch):
    import threading

    from anyfem import mesh_jobs
    from anyfem.job_process import WorkerProcessError

    fallback_registered = threading.Event()
    release = threading.Event()
    calls = []
    order = []

    class FakeTree:
        def terminate(self):
            order.append("tree.terminate")

    class FakeWorker:
        def __init__(self, name):
            self._name = name

        def is_alive(self):
            order.append(f"{self._name}.is_alive")
            return True

        def terminate(self):
            order.append(f"{self._name}.terminate")

    def fake_run(**kwargs):
        calls.append(bool(kwargs.get("containment")))
        if kwargs.get("containment"):
            contained = FakeWorker("contained")
            kwargs["on_process"](contained)
            kwargs["on_tree"](contained, FakeTree())
            raise WorkerProcessError(
                "the contained mesh worker could not be dispatched",
                remote_type="ContainmentUnavailable",
            )
        fallback = FakeWorker("fallback")
        kwargs["on_process"](fallback)
        fallback_registered.set()
        assert release.wait(10.0), "shutdown never ran"
        raise WorkerProcessError("the fallback worker was stopped by shutdown")

    monkeypatch.setattr(mesh_jobs, "run_in_worker_process", fake_run)
    _fake_parallel_api(monkeypatch)
    manager = MeshTaskManager(executor="process")
    future = manager.submit(
        "fallback-race",
        DocumentSession(plate_project()).snapshot(),
        MeshSettings.create(0.25, element_order="linear"),
    )
    assert fallback_registered.wait(10.0), "the fallback worker was never registered"
    manager.shutdown()
    release.set()
    future.result(timeout=10.0)
    assert calls == [True, False], "exactly one serial fallback may be dispatched"
    assert "fallback.terminate" in order, (
        "shutdown must stop the live fallback worker"
    )
    assert "tree.terminate" not in order, (
        "the refused attempt's closed tree must not mask the live fallback worker"
    )


def test_a_worker_that_outlives_its_spawn_stays_reachable_by_shutdown(monkeypatch):
    import threading

    from anyfem import mesh_jobs
    from anyfem.job_process import WorkerProcessError

    registered = threading.Event()
    release = threading.Event()
    order = []

    class Survivor:
        def is_alive(self):
            order.append("survivor.is_alive")
            return True

        def terminate(self):
            order.append("survivor.terminate")

    def fake_run(**kwargs):
        kwargs["on_process"](Survivor())
        registered.set()
        assert release.wait(10.0), "the spawn never ended"
        raise WorkerProcessError(
            "the worker tree could not be retired",
            remote_type="WorkerCleanupFailed",
        )

    monkeypatch.setattr(mesh_jobs, "run_in_worker_process", fake_run)
    _fake_parallel_api(monkeypatch)
    manager = MeshTaskManager(executor="process")
    future = manager.submit(
        "survivor",
        DocumentSession(plate_project()).snapshot(),
        MeshSettings.create(0.25, element_order="linear"),
    )
    assert registered.wait(10.0), "the worker was never registered"
    release.set()
    future.result(timeout=10.0)
    events = manager.poll()
    assert any(e.kind == "failed" for e in events), "a cleanup failure must fail the job"
    manager.shutdown()
    assert "survivor.terminate" in order, (
        "a worker that outlived its bounded cleanup must stay reachable by shutdown"
    )


def test_a_live_cleanup_survivor_refuses_new_submissions_until_shutdown_stops_it(monkeypatch):
    import threading

    from anyfem import mesh_jobs
    from anyfem.job_process import WorkerProcessError

    registered = threading.Event()
    release = threading.Event()
    stopped = []

    class Survivor:
        def is_alive(self):
            return True

        def terminate(self):
            stopped.append(True)

    def fake_run(**kwargs):
        kwargs["on_process"](Survivor())
        registered.set()
        assert release.wait(10.0), "the spawn never ended"
        raise WorkerProcessError(
            "the worker tree could not be retired",
            remote_type="WorkerCleanupFailed",
        )

    monkeypatch.setattr(mesh_jobs, "run_in_worker_process", fake_run)
    _fake_parallel_api(monkeypatch)
    manager = MeshTaskManager(executor="process")
    future = manager.submit(
        "survivor",
        DocumentSession(plate_project()).snapshot(),
        MeshSettings.create(0.25, element_order="linear"),
    )
    assert registered.wait(10.0), "the worker was never registered"
    release.set()
    future.result(timeout=10.0)
    events = manager.poll()
    assert any(e.kind == "failed" for e in events), "a cleanup failure must fail the job"
    with pytest.raises(RuntimeError, match="still running"):
        manager.submit(
            "replacement",
            DocumentSession(plate_project()).snapshot(),
            MeshSettings.create(0.25, element_order="linear"),
        )
    manager.shutdown()
    assert stopped == [True], (
        "shutdown must still stop the survivor a refused submission never replaced"
    )
