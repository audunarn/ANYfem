"""MeshTaskManager lifecycle for parallel meshing: fallback, shutdown, hashes.

Exercises the real production manager (real worker spawns, real ANYmesher
serial meshing) for the safety repairs:

* a ContainmentUnavailable at startup falls back to ONE serial worker before
  any dispatch, with the reason recorded -- and never after shutdown, and
  never when the refusal carries cleanup failures (the job fails instead);
* shutdown retires the registered owned tree FIRST; the standalone process
  is terminated only when no tree is registered;
* registration refuses when the manager is closing, so nothing is dispatched
  after shutdown and the cancelled token reports the job as cancelled;
* the thread executor forces serial execution (it cannot own a tree);
* the execution policy is execution-only: excluded from the mesh input hash;
* the semantic mesh hash excludes the owner parallel block's execution-route
  diagnostics, present or absent, while preserving unknown semantic fields.
"""

from __future__ import annotations

import time

import pytest

from anyfem.document import DocumentSession
from anyfem.job_process import WorkerProcessError
from anyfem.mesh_execution import MeshExecutionPolicy
from anyfem.mesh_jobs import (
    MeshSettings,
    MeshTaskManager,
    mesh_semantic_hash,
)
from anyfem.model.project import Project


def _tiny_project(name):
    project = Project(name)
    vertices = project.geometry.add_points(
        ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    project.geometry.add_plate(vertices)
    return project


def _settings(policy, target_size=0.5):
    return MeshSettings.create(
        target_size=target_size, element_order="linear", execution_policy=policy
    )


def _wait_for_terminal(manager, job_id, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = manager.poll()
        for event in events:
            if event.job_id == job_id and event.kind in ("completed", "cancelled", "failed"):
                return event, events
        time.sleep(0.02)
    raise AssertionError("the mesh job never reached a terminal event")


def test_execution_policy_is_excluded_from_the_input_hash():
    serial = _settings(MeshExecutionPolicy.off())
    parallel = _settings(MeshExecutionPolicy.explicit(4))
    assert serial.input_hash == parallel.input_hash


def test_mesh_settings_rejects_a_wrong_policy_type():
    with pytest.raises(TypeError):
        _settings("auto")


def test_thread_executor_forces_serial_and_records_the_reason():
    project = _tiny_project("thread-executor-serial")
    manager = MeshTaskManager(executor="thread")
    job_id = "thread-serial"
    future = manager.submit(
        job_id, DocumentSession(project).snapshot(), _settings(MeshExecutionPolicy.explicit(2))
    )
    future.result(timeout=60)
    event, events = _wait_for_terminal(manager, job_id)
    assert event.kind == "completed"
    assert event.payload.execution["route"] == "serial"
    assert "thread executor" in event.payload.execution["reason"]
    assert len(event.payload.mesh.nodes) > 0
    manager.shutdown()


def test_containment_failure_falls_back_to_one_serial_worker(monkeypatch):
    from anyfem import mesh_jobs
    from anyfem.job_process import run_in_worker_process as real_run

    calls = []

    def fake_run(**kwargs):
        calls.append(bool(kwargs.get("containment")))
        if kwargs.get("containment"):
            raise WorkerProcessError(
                "the contained mesh worker did not confirm startup",
                remote_type="ContainmentUnavailable",
            )
        return real_run(**kwargs)

    monkeypatch.setattr(mesh_jobs, "run_in_worker_process", fake_run)
    monkeypatch.setattr(
        "anyfem.mesh_execution.parallel_mesh_api",
        lambda: (lambda model, **parameters: None, object, "mesh"),
    )
    project = _tiny_project("containment-fallback")
    manager = MeshTaskManager(executor="process")
    job_id = "fallback"
    future = manager.submit(
        job_id, DocumentSession(project).snapshot(), _settings(MeshExecutionPolicy.explicit(2))
    )
    future.result(timeout=60)
    event, events = _wait_for_terminal(manager, job_id)
    assert event.kind == "completed"
    assert calls == [True, False], "the fallback must run exactly one serial worker"
    assert event.payload.execution["route"] == "serial"
    assert "contained worker unavailable" in event.payload.execution["reason"]
    assert len(event.payload.mesh.nodes) > 0
    manager.shutdown()


def test_no_dispatch_after_shutdown(monkeypatch):
    import threading

    from anyfem import mesh_jobs

    entered = threading.Event()
    release = threading.Event()
    calls = []

    def fake_run(**kwargs):
        calls.append(bool(kwargs.get("containment")))
        entered.set()
        assert release.wait(10.0)
        raise WorkerProcessError(
            "the contained mesh worker did not confirm startup",
            remote_type="ContainmentUnavailable",
        )

    monkeypatch.setattr(mesh_jobs, "run_in_worker_process", fake_run)
    monkeypatch.setattr(
        "anyfem.mesh_execution.parallel_mesh_api",
        lambda: (lambda model, **parameters: None, object, "mesh"),
    )
    project = _tiny_project("shutdown-race")
    manager = MeshTaskManager(executor="process")
    job_id = "shutdown-race"
    future = manager.submit(
        job_id, DocumentSession(project).snapshot(), _settings(MeshExecutionPolicy.explicit(2))
    )
    assert entered.wait(10.0), "the worker never reached the spawn"
    manager.shutdown()
    release.set()
    future.result(timeout=30)
    event, events = _wait_for_terminal(manager, job_id)
    assert event.kind == "cancelled", f"unexpected terminal event: {event.kind}"
    assert calls == [True], "no fallback worker may be dispatched after shutdown"


def test_a_cleanup_failure_blocks_the_serial_fallback(monkeypatch):
    from anyfem import mesh_jobs
    from anyfem.job_process import run_in_worker_process as real_run

    calls = []

    def fake_run(**kwargs):
        calls.append(bool(kwargs.get("containment")))
        if kwargs.get("containment"):
            error = WorkerProcessError(
                "the contained mesh worker did not confirm startup",
                remote_type="ContainmentUnavailable",
            )
            error.cleanup_failures = [
                "owned worker tree process (pid 4242) was still alive within "
                "the cleanup deadline"
            ]
            raise error
        return real_run(**kwargs)

    monkeypatch.setattr(mesh_jobs, "run_in_worker_process", fake_run)
    monkeypatch.setattr(
        "anyfem.mesh_execution.parallel_mesh_api",
        lambda: (lambda model, **parameters: None, object, "mesh"),
    )
    project = _tiny_project("cleanup-failure-no-fallback")
    manager = MeshTaskManager(executor="process")
    job_id = "no-fallback"
    future = manager.submit(
        job_id, DocumentSession(project).snapshot(), _settings(MeshExecutionPolicy.explicit(2))
    )
    future.result(timeout=60)
    event, events = _wait_for_terminal(manager, job_id)
    assert event.kind == "failed", "a cleanup failure must fail the job, not fall back"
    assert calls == [True], "a serial fallback was dispatched despite failed cleanup"
    manager.shutdown()


def test_shutdown_retires_the_registered_tree_before_touching_the_process():
    order = []

    class FakeTree:
        def terminate(self):
            order.append("tree.terminate")

    class FakeProcess:
        def is_alive(self):
            order.append("process.is_alive")
            return True

        def terminate(self):
            order.append("process.terminate")

    manager = MeshTaskManager(executor="process")
    manager._tree = FakeTree()
    manager._process = FakeProcess()
    manager.shutdown()
    assert order == ["tree.terminate"], (
        "the registered owned tree must be retired first and covers the outer worker"
    )

    manager._tree = None
    manager._process = FakeProcess()
    order.clear()
    manager.shutdown()
    assert order == ["process.is_alive", "process.terminate"], (
        "a standalone worker is terminated only when no tree is registered"
    )


def test_submit_refuses_after_shutdown():
    manager = MeshTaskManager(executor="process")
    manager.shutdown()
    project = _tiny_project("closed-manager")
    with pytest.raises(RuntimeError, match="closed"):
        manager.submit(
            "late", DocumentSession(project).snapshot(), _settings(MeshExecutionPolicy.off())
        )


def test_semantic_hash_excludes_execution_route_diagnostics(monkeypatch):
    base = {
        "nodes": {1: [0.0, 0.0, 0.0], 2: [1.0, 0.0, 0.0]},
        "elements": {1: {"nodes": [1, 2]}},
        "hybrid_diagnostics": {
            "parallel": {
                "used": True,
                "route": "parallel",
                "reason": "explicit worker count requested",
                "components": 3,
                "separation": {"independent_components": 3},
                "merge_reasons": ["component 1 merged last"],
                "runtime": {"seconds": 1.25, "worker_pids": [11, 12, 13, 14]},
            }
        },
    }

    def hash_of(payload):
        monkeypatch.setattr(
            "anymesher.serialize.mesh_to_dict", lambda mesh: dict(payload)
        )
        return mesh_semantic_hash(
            object(), model_hash="m", mesh_input_hash="i", structural_preparation={}
        )

    reference = hash_of(base)

    def hash_of_parallel(mutate):
        payload = dict(base)
        payload["hybrid_diagnostics"] = dict(base["hybrid_diagnostics"])
        payload["hybrid_diagnostics"]["parallel"] = mutate(
            dict(base["hybrid_diagnostics"]["parallel"])
        )
        return hash_of(payload)

    # the owner's whole parallel block is the execution-route record: no route
    # key or runtime measurement may change the mesh hash
    assert hash_of_parallel(lambda parallel: {**parallel, "used": False}) == reference
    assert hash_of_parallel(lambda parallel: {**parallel, "route": "serial"}) == reference
    assert hash_of_parallel(lambda parallel: {**parallel, "reason": "1 component(s)"}) == reference
    assert hash_of_parallel(lambda parallel: {**parallel, "components": 1}) == reference
    assert hash_of_parallel(lambda parallel: {**parallel, "separation": {}}) == reference
    assert hash_of_parallel(lambda parallel: {**parallel, "merge_reasons": []}) == reference
    assert hash_of_parallel(
        lambda parallel: {**parallel, "runtime": {"seconds": 9.9, "pids": [1]}}
    ) == reference
    assert hash_of_parallel(lambda parallel: {**parallel, "runtime": None}) == reference
    # a fully routed block and an absent one are the same mesh: the worker
    # process records the block, a forced-serial run does not (the CI mismatch)
    absent = dict(base)
    absent["hybrid_diagnostics"] = dict(base["hybrid_diagnostics"])
    absent["hybrid_diagnostics"].pop("parallel")
    assert hash_of(absent) == reference
    # unknown semantic fields beside the route record stay hashed
    assert (
        hash_of_parallel(lambda parallel: {**parallel, "geometry_authority": "native"})
        != reference
    )
    # and solver-affecting mesh and preparation/admission data still does
    moved = dict(base)
    moved["nodes"] = {1: [0.0, 0.0, 0.0], 2: [2.0, 0.0, 0.0]}
    assert hash_of(moved) != reference
    assert mesh_semantic_hash(
        object(),
        model_hash="m",
        mesh_input_hash="i",
        structural_preparation={"admission": {"accepted": False}},
    ) != mesh_semantic_hash(
        object(),
        model_hash="m",
        mesh_input_hash="i",
        structural_preparation={"admission": {"accepted": True}},
    )
