"""Submission refusals and the real production caller for parallel meshing.

``run_parallel_hybrid_mesh`` must refuse BEFORE submission when the installed
public API cannot carry a requested control (workers, pool_lease,
min_estimated_elements, min_components) instead of silently dropping it, and
``Project.generate_mesh`` must exercise that contract end to end: a refusal
falls back to the ordinary serial hybrid mesher with the reason recorded.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anyfem.mesh_execution import (
    MeshExecutionPolicy,
    MeshExecutionRoute,
    ParallelMeshUnsupported,
    run_parallel_hybrid_mesh,
)
from anyfem.model.project import Project


class _FullOptions:
    """Shaped exactly like the settled public ParallelOptions signature."""

    def __init__(self, workers=None, pad_factor=1, min_components=2,
                 min_estimated_elements=None, pool_lease=None, executor=None):
        self.workers = workers
        self.pad_factor = pad_factor
        self.min_components = min_components
        self.min_estimated_elements = min_estimated_elements
        self.pool_lease = pool_lease
        self.executor = executor


class _WorkersOnlyOptions:
    def __init__(self, workers=None):
        self.workers = workers


class _ForwardingOptions:
    """A compatible shape: every control declared explicitly, plus **kwargs."""

    def __init__(self, workers=None, min_components=None,
                 min_estimated_elements=None, pool_lease=None, **extra):
        self.workers = workers
        self.min_components = min_components
        self.min_estimated_elements = min_estimated_elements
        self.pool_lease = pool_lease
        self.extra = extra


def _generate_ok(model, *, parallel=None, **parameters):
    return "MESH"


def _explicit_route():
    return MeshExecutionRoute("parallel", workers=2, reason="explicit worker count requested")


def _auto_route():
    return MeshExecutionRoute(
        "parallel", workers=4, reason="automatic sizing proposed",
        min_estimated_elements=3000, min_components=4,
    )


def test_submission_passes_requested_controls_to_the_public_api():
    seen = {}

    def generate(model, *, parallel=None, **parameters):
        seen["parallel"] = parallel
        seen["parameters"] = parameters
        return "MESH"

    lease = object()
    result = run_parallel_hybrid_mesh(
        "MODEL", {"a": 1}, _explicit_route(), (generate, _FullOptions, "mesh"),
        pool_lease=lease,
    )
    assert result == "MESH"
    assert seen["parameters"] == {"a": 1}
    assert seen["parallel"].workers == 2
    assert seen["parallel"].pool_lease is lease


def test_submission_unwraps_the_result_variant():
    def generate(model, *, parallel=None, **parameters):
        return SimpleNamespace(mesh="MESH")

    result = run_parallel_hybrid_mesh(
        "MODEL", {}, _explicit_route(), (generate, _FullOptions, "result")
    )
    assert result == "MESH"


def test_submission_refuses_when_workers_cannot_be_carried():
    class NoControls:
        def __init__(self):
            pass

    with pytest.raises(ParallelMeshUnsupported, match="workers"):
        run_parallel_hybrid_mesh("MODEL", {}, _explicit_route(), (_generate_ok, NoControls, "mesh"))


def test_submission_refuses_a_pool_lease_the_api_cannot_carry():
    with pytest.raises(ParallelMeshUnsupported, match="pool_lease"):
        run_parallel_hybrid_mesh(
            "MODEL", {}, _explicit_route(), (_generate_ok, _WorkersOnlyOptions, "mesh"),
            pool_lease=object(),
        )


def test_submission_refuses_auto_gates_the_api_cannot_carry():
    with pytest.raises(ParallelMeshUnsupported) as refused:
        run_parallel_hybrid_mesh("MODEL", {}, _auto_route(), (_generate_ok, _WorkersOnlyOptions, "mesh"))
    message = str(refused.value)
    assert "min_components" in message and "min_estimated_elements" in message


def test_submission_refuses_a_generate_without_the_parallel_keyword():
    def generate(model, **parameters):
        return "MESH"

    with pytest.raises(ParallelMeshUnsupported, match="parallel="):
        run_parallel_hybrid_mesh("MODEL", {}, _explicit_route(), (generate, _FullOptions, "mesh"))


def test_submission_refuses_options_that_reject_the_settings():
    class Rejecting:
        def __init__(self, workers=None):
            raise TypeError("unexpected keyword 'workers'")

    with pytest.raises(ParallelMeshUnsupported, match="rejected the execution settings"):
        run_parallel_hybrid_mesh("MODEL", {}, _explicit_route(), (_generate_ok, Rejecting, "mesh"))


def test_submission_passes_every_control_through_a_forwarding_options():
    seen = {}

    def generate(model, *, parallel=None, **parameters):
        seen["parallel"] = parallel
        return "MESH"

    lease = object()
    run_parallel_hybrid_mesh(
        "MODEL", {}, _auto_route(), (generate, _ForwardingOptions, "mesh"), pool_lease=lease
    )
    assert seen["parallel"].workers == 4
    assert seen["parallel"].min_estimated_elements == 3000
    assert seen["parallel"].min_components == 4
    assert seen["parallel"].pool_lease is lease


# ----------------------------------------------------------------------
# the real production caller: Project.generate_mesh
# ----------------------------------------------------------------------
def _tiny_project(name):
    project = Project(name)
    vertices = project.geometry.add_points(
        ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    project.geometry.add_plate(vertices)
    return project


def test_production_caller_runs_the_parallel_route(monkeypatch):
    from anymesher import generate_hybrid_mesh

    captured = {}

    def generate(model, *, parallel=None, **parameters):
        captured["parallel"] = parallel
        captured["parameters"] = dict(parameters)
        return generate_hybrid_mesh(model, **parameters)

    monkeypatch.setattr(
        "anyfem.mesh_execution.parallel_mesh_api",
        lambda: (generate, _FullOptions, "mesh"),
    )
    project = _tiny_project("parallel-production-caller")
    mesh = project.generate_mesh(
        target_size=0.5, execution_policy=MeshExecutionPolicy.explicit(2)
    )
    assert captured["parallel"].workers == 2
    assert project._last_mesh_execution["route"] == "parallel"
    assert project._last_mesh_execution["workers"] == 2
    assert len(mesh.nodes) > 0


def test_production_caller_falls_back_to_serial_on_refusal(monkeypatch):
    monkeypatch.setattr(
        "anyfem.mesh_execution.parallel_mesh_api",
        lambda: (_generate_ok, _WorkersOnlyOptions, "mesh"),
    )
    project = _tiny_project("parallel-refused-caller")
    mesh = project.generate_mesh(
        target_size=0.5, execution_policy=MeshExecutionPolicy.auto()
    )
    execution = project._last_mesh_execution
    assert execution["route"] == "serial"
    assert execution["workers"] is None
    assert "refused before dispatch" in execution["reason"]
    assert "min_components" in execution["reason"]
    assert len(mesh.nodes) > 0


def test_production_caller_refuses_a_lease_the_api_cannot_carry(monkeypatch):
    monkeypatch.setattr(
        "anyfem.mesh_execution.parallel_mesh_api",
        lambda: (_generate_ok, _WorkersOnlyOptions, "mesh"),
    )
    project = _tiny_project("parallel-lease-refused")
    mesh = project.generate_mesh(
        target_size=0.5,
        execution_policy=MeshExecutionPolicy.explicit(2),
        pool_lease=object(),
    )
    execution = project._last_mesh_execution
    assert execution["route"] == "serial"
    assert "refused before dispatch" in execution["reason"]
    assert "pool_lease" in execution["reason"]
    assert len(mesh.nodes) > 0


def test_production_caller_rejects_a_wrong_policy_type():
    project = _tiny_project("parallel-bad-policy")
    with pytest.raises(Exception, match="MeshExecutionPolicy"):
        project.generate_mesh(target_size=0.5, execution_policy="auto")


@pytest.mark.parametrize("requested,environment,expected,contained", [
    (None, None, MeshExecutionPolicy.auto(), True),
    (None, "off", MeshExecutionPolicy.off(), False),
    (None, "3", MeshExecutionPolicy.explicit(3), True),
    (MeshExecutionPolicy.off(), "auto", MeshExecutionPolicy.off(), False),
])
def test_async_submission_resolves_policy_before_containment(
    monkeypatch, requested, environment, expected, contained
):
    from anyfem.document import DocumentSession
    from anyfem.job_process import WorkerProcessError
    from anyfem.mesh_jobs import MeshSettings, MeshTaskManager

    if environment is None:
        monkeypatch.delenv("ANYFEM_MESH_WORKERS", raising=False)
    else:
        monkeypatch.setenv("ANYFEM_MESH_WORKERS", environment)
    monkeypatch.setattr("anyfem.mesh_execution.parallel_mesh_api",
                        lambda: (_generate_ok, _FullOptions, "mesh"))
    captured = []

    def fake_spawn(snapshot, settings, cancellation, progress, use_tree, register, register_tree):
        captured.append((settings.execution_policy, use_tree))
        raise WorkerProcessError("fake worker stopped before meshing")

    manager = MeshTaskManager(executor="process")
    monkeypatch.setattr(manager, "_spawn_worker", fake_spawn)
    try:
        settings = MeshSettings.create(0.5, element_order="linear", execution_policy=requested)
        manager.submit("default-policy", DocumentSession(Project("default-policy")).snapshot(),
                       settings).result(timeout=5)
        assert captured == [(expected, contained)]
        assert settings.execution_policy is requested  # Submission leaves caller settings unchanged.
        assert any(event.kind == "failed" for event in manager.poll())
    finally:
        manager.shutdown()
