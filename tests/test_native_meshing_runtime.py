"""Focused runtime tests for incremental native component meshing."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import threading
from uuid import uuid4

import pytest

from anygeometry import ChangeSet, EntityHandle
from anyfem.native_meshing import (
    CertificationMode,
    ComponentUpdateKind,
    ControlScope,
    DirtyComponentResolution,
    MeshBackend,
    NativeMeshControl,
    NativeMeshResult,
    NativeMeshSettings,
    NativeMeshingRuntime,
    handle_from_dict,
    handle_to_dict,
)


def _handles(count: int) -> tuple[EntityHandle, ...]:
    model_id = uuid4()
    return tuple(EntityHandle(model_id, "part", index + 1) for index in range(count))


def _runtime(
    settings: NativeMeshSettings,
    *,
    components: tuple[EntityHandle, ...],
    generate,
    capture=None,
    resolver=None,
    max_background_jobs: int = 2,
) -> NativeMeshingRuntime:
    return NativeMeshingRuntime(
        settings,
        resolve_dirty_components=resolver or (lambda _changes: components),
        capture_component=capture,
        generate_component=generate,
        max_background_jobs=max_background_jobs,
    )


def test_schema_3_settings_scopes_and_handles_round_trip_immutably() -> None:
    first, second = _handles(2)
    scope = ControlScope((second, first, first), include_descendants=True)
    control = NativeMeshControl.create(
        "edge-size",
        scope=scope,
        target_size=0.05,
        backend="native",
        parameters={"growth": 1.2, "layers": [1, 2]},
    )
    settings = NativeMeshSettings.create(
        0.2,
        element_order="quadratic",
        backend="automatic",
        certification_mode="strict",
        controls=(control,),
        parameters={"quality": {"minimum": 0.4}},
    )

    payload = json.loads(json.dumps(settings.to_dict()))

    assert payload["schema"] == 3
    assert NativeMeshSettings.from_dict(payload) == settings
    assert handle_from_dict(handle_to_dict(first)) == first
    assert scope.handles == tuple(sorted((first, second)))
    with pytest.raises(FrozenInstanceError):
        scope.handles = ()  # type: ignore[misc]


def test_geometry_hook_only_queues_then_flush_coalesces_and_resolves() -> None:
    (component,) = _handles(1)
    calls: list[object] = []

    def resolve(changes):
        calls.append(("resolve", changes))
        return DirtyComponentResolution((component,))

    def capture(handle):
        calls.append(("capture", handle))
        return {"mapped_mesh_eligible": True, "revision": 2}

    def generate(request):
        calls.append(("generate", request.backend, request.token))
        return {"component": request.component.id, "revision": request.snapshot["revision"]}

    runtime = _runtime(
        NativeMeshSettings(0.25),
        components=(component,),
        generate=generate,
        capture=capture,
        resolver=resolve,
    )
    try:
        runtime.on_geometry_change(
            ChangeSet(0, 1, added=(("face", 1),))
        )
        runtime.on_geometry_change(
            ChangeSet(1, 2, modified=(("face", 1), ("edge", 2)))
        )

        assert calls == []
        assert runtime.active_job_count == 0
        resolution = runtime.flush_changes()
        assert resolution.dirty == (component,)
        assert runtime.wait_for_idle(2.0)

        coalesced = calls[0][1]
        assert coalesced.source_count == 2
        assert coalesced.revision_before == 0
        assert coalesced.revision_after == 2
        publication = runtime.publication(component)
        assert publication is not None
        assert publication.backend is MeshBackend.MAPPED
        assert publication.mesh == {"component": 1, "revision": 2}
        assert publication.token.component_generation == 1
        assert [event.kind for event in runtime.poll_events()] == [
            ComponentUpdateKind.QUEUED,
            ComponentUpdateKind.STARTED,
            ComponentUpdateKind.PUBLISHED,
        ]
    finally:
        runtime.shutdown()


def test_superseded_noncooperative_result_is_stale_and_never_published() -> None:
    (component,) = _handles(1)
    entered = threading.Event()
    release = threading.Event()
    revision = {"value": 1}

    def capture(_component):
        return {"revision": revision["value"], "mapped_mesh_eligible": False}

    def generate(request):
        if request.snapshot["revision"] == 1:
            entered.set()
            assert release.wait(2.0)
            # Deliberately ignore request.cancellation inside this phase.
        return f"mesh-{request.snapshot['revision']}"

    runtime = _runtime(
        NativeMeshSettings(0.2),
        components=(component,),
        generate=generate,
        capture=capture,
        max_background_jobs=2,
    )
    try:
        runtime.request_remesh(component)
        assert entered.wait(1.0)
        revision["value"] = 2
        runtime.request_remesh(component)
        release.set()

        assert runtime.wait_for_idle(2.0)
        assert runtime.published_mesh(component) == "mesh-2"
        events = runtime.poll_events()
        assert any(event.kind is ComponentUpdateKind.STALE for event in events)
        published = [
            event for event in events if event.kind is ComponentUpdateKind.PUBLISHED
        ]
        assert len(published) == 1
        assert published[0].component_generation == 2
    finally:
        release.set()
        runtime.shutdown()


def test_strict_certification_rejection_retains_last_interactive_mesh() -> None:
    (component,) = _handles(1)
    generated = {"count": 0}

    def generate(_request):
        generated["count"] += 1
        return NativeMeshResult(
            f"mesh-{generated['count']}", valid=True, certified=False
        )

    interactive = NativeMeshSettings(
        0.2, certification_mode=CertificationMode.INTERACTIVE
    )
    runtime = _runtime(
        interactive,
        components=(component,),
        generate=generate,
    )
    try:
        runtime.request_remesh(component)
        assert runtime.wait_for_idle(2.0)
        first = runtime.publication(component)
        assert first is not None and first.mesh == "mesh-1"

        prior_control_generation = runtime.control_generation
        runtime.set_settings(
            NativeMeshSettings(0.2, certification_mode=CertificationMode.STRICT)
        )
        assert runtime.wait_for_idle(2.0)

        retained = runtime.publication(component)
        assert retained is first
        assert runtime.control_generation == prior_control_generation + 1
        rejected = [
            event
            for event in runtime.poll_events()
            if event.kind is ComponentUpdateKind.REJECTED
        ]
        assert rejected and rejected[-1].retained_previous
    finally:
        runtime.shutdown()


def test_background_submission_is_bounded_and_coalesced_per_component() -> None:
    components = _handles(3)
    release = threading.Event()
    two_started = threading.Event()
    state = {"active": 0, "maximum": 0, "starts": 0}
    lock = threading.Lock()

    def generate(request):
        with lock:
            state["active"] += 1
            state["starts"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
            if state["active"] == 2:
                two_started.set()
        assert release.wait(2.0)
        with lock:
            state["active"] -= 1
        return NativeMeshResult.certified_result(f"mesh-{request.component.id}")

    runtime = _runtime(
        NativeMeshSettings(0.2),
        components=components,
        generate=generate,
        max_background_jobs=2,
    )
    try:
        runtime.request_remesh(components)
        assert two_started.wait(1.0)
        assert runtime.active_job_count == 2
        assert runtime.pending_job_count == 1
        assert state["maximum"] == 2

        release.set()
        assert runtime.wait_for_idle(2.0)
        assert state["starts"] == 3
        assert set(runtime.publications()) == set(components)
    finally:
        release.set()
        runtime.shutdown()


def test_explicit_cancellation_rejects_result_and_retains_publication() -> None:
    (component,) = _handles(1)
    entered = threading.Event()
    release = threading.Event()
    call = {"number": 0}

    def generate(_request):
        call["number"] += 1
        if call["number"] == 2:
            entered.set()
            assert release.wait(2.0)
        return f"mesh-{call['number']}"

    runtime = _runtime(
        NativeMeshSettings(0.2),
        components=(component,),
        generate=generate,
        max_background_jobs=1,
    )
    try:
        runtime.request_remesh(component)
        assert runtime.wait_for_idle(2.0)
        first = runtime.publication(component)
        assert first is not None

        runtime.request_remesh(component)
        assert entered.wait(1.0)
        assert runtime.cancel_component(component)
        release.set()
        assert runtime.wait_for_idle(2.0)

        assert runtime.publication(component) is first
        events = runtime.poll_events()
        assert any(event.kind is ComponentUpdateKind.CANCELLED for event in events)
    finally:
        release.set()
        runtime.shutdown()
