"""Cache-reuse qualification for the headless native meshing runtime."""

from __future__ import annotations

from uuid import uuid4

from anygeometry import ChangeSet, EntityHandle, GeometryModel
from anyfem.native_meshing import (
    DirtyComponentResolution,
    NativeMeshSettings,
    NativeMeshingRuntime,
)
from anyfem.mesh_jobs import mesh_semantic_hash
from anymesher.hybrid import generate_hybrid_mesh


def test_only_dirty_components_regenerate_and_empty_resolution_is_a_cache_hit() -> None:
    model_id = uuid4()
    first = EntityHandle(model_id, "part", 1)
    second = EntityHandle(model_id, "part", 2)
    dirty = {"components": (first,)}
    calls = {first: 0, second: 0}

    def generate(request):
        calls[request.component] += 1
        return f"mesh-{request.component.id}-{calls[request.component]}"

    runtime = NativeMeshingRuntime(
        NativeMeshSettings(0.2),
        resolve_dirty_components=lambda _changes: DirtyComponentResolution(
            dirty["components"]
        ),
        generate_component=generate,
        max_background_jobs=1,
    )
    try:
        runtime.request_remesh((first, second))
        assert runtime.wait_for_idle(2.0)
        clean_publication = runtime.publication(second)
        assert clean_publication is not None
        assert calls == {first: 1, second: 1}

        runtime.on_geometry_change(ChangeSet(0, 1, modified=(("face", 1),)))
        resolution = runtime.flush_changes()
        assert resolution.dirty == (first,)
        assert runtime.wait_for_idle(2.0)
        assert calls == {first: 2, second: 1}
        assert runtime.publication(second) is clean_publication

        first_publication = runtime.publication(first)
        dirty["components"] = ()
        runtime.on_geometry_change(ChangeSet(1, 2, modified=(("face", 99),)))
        resolution = runtime.flush_changes()
        assert resolution.dirty == ()
        assert runtime.wait_for_idle(2.0)
        assert calls == {first: 2, second: 1}
        assert runtime.publication(first) is first_publication
        assert runtime.publication(second) is clean_publication
    finally:
        runtime.shutdown()


def test_serial_and_concurrent_component_meshing_publish_identical_meshes() -> None:
    geometry = GeometryModel()
    face_ids = []
    for offset in (0.0, 2.0):
        vertices = geometry.add_points(
            (
                (offset, 0.0, 0.0),
                (offset + 1.0, 0.0, 0.0),
                (offset + 1.0, 1.0, 0.0),
                (offset, 1.0, 0.0),
            )
        )
        face_ids.append(geometry.add_plate(vertices))
    components = tuple(
        EntityHandle(geometry.model_id, "face", face_id) for face_id in face_ids
    )

    def generate(request):
        mesh = generate_hybrid_mesh(
            geometry,
            target_size=0.2,
            strategy="native",
            face_ids=(request.component.id,),
        )
        return mesh_semantic_hash(
            mesh,
            model_hash="component-model",
            mesh_input_hash="native-cache-reuse",
            structural_preparation=mesh.structural_preparation,
        )

    def run(max_background_jobs: int, order):
        runtime = NativeMeshingRuntime(
            NativeMeshSettings(0.2),
            resolve_dirty_components=lambda _changes: DirtyComponentResolution(
                components
            ),
            generate_component=generate,
            max_background_jobs=max_background_jobs,
        )
        try:
            runtime.request_remesh(order)
            assert runtime.wait_for_idle(5.0)
            return {component: runtime.published_mesh(component) for component in components}
        finally:
            runtime.shutdown()

    serial = run(1, components)
    concurrent = run(2, tuple(reversed(components)))

    assert concurrent == serial
