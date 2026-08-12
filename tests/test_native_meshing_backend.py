from __future__ import annotations

import threading

import anymesher.surface_mesh as surface_mesh
from anyfem.model.project import Project
from anyfem.native_meshing import ComponentUpdateKind, NativeMeshSettings
from anyfem.native_meshing_backend import NativeProjectMeshingSession


def _sheet_project() -> tuple[Project, int, int]:
    project = Project("runtime-sheet")
    vertices = project.geometry.add_points(
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 1.0, 0.0),
            (0.0, 1.0, 0.0),
        )
    )
    face_id = project.geometry.add_plate(vertices)
    sheet_id = project.geometry.add_sheet([face_id], name="plate")
    return project, sheet_id, vertices[1]


def test_runtime_publishes_model_bound_component_mesh() -> None:
    project, sheet_id, _vertex = _sheet_project()
    settings = NativeMeshSettings(target_size=0.3, backend="automatic")
    component = project.geometry.handle("sheet", sheet_id)

    with NativeProjectMeshingSession(
        project, settings, max_background_jobs=1
    ) as session:
        assert session.request_remesh(component) == (component,)
        assert session.runtime.wait_for_idle(timeout=5.0)
        publication = session.runtime.publication(component)

        assert publication is not None
        assert publication.component == component
        assert publication.mesh.geometry_model_id == project.geometry.model_id
        assert publication.mesh.geometry_revision == project.geometry.revision
        assert publication.certified is False


def test_geometry_hook_coalesces_and_republishes_after_edit() -> None:
    project, sheet_id, moved_vertex = _sheet_project()
    component = project.geometry.handle("sheet", sheet_id)
    settings = NativeMeshSettings(target_size=0.35, backend="automatic")

    with NativeProjectMeshingSession(
        project, settings, max_background_jobs=1
    ) as session:
        session.request_remesh(component)
        assert session.runtime.wait_for_idle(timeout=5.0)
        first = session.runtime.publication(component)
        assert first is not None

        project.geometry.move_point(moved_vertex, 1.1, 0.0, 0.0)
        resolution = session.runtime.flush_changes()
        assert component in resolution.dirty
        assert session.runtime.wait_for_idle(timeout=5.0)
        second = session.runtime.publication(component)

        assert second is not None
        assert second.publication_sequence > first.publication_sequence
        assert second.mesh.geometry_revision == project.geometry.revision
        assert second.certified is False


def test_in_flight_native_cancellation_stops_before_recombination(
    monkeypatch,
) -> None:
    project, sheet_id, _vertex = _sheet_project()
    component = project.geometry.handle("sheet", sheet_id)
    settings = NativeMeshSettings(target_size=0.2, backend="native")
    entered = threading.Event()
    release = threading.Event()
    recombined = threading.Event()

    with NativeProjectMeshingSession(
        project, settings, max_background_jobs=1
    ) as session:
        session.request_remesh(component)
        assert session.runtime.wait_for_idle(timeout=5.0)
        previous = session.runtime.publication(component)
        assert previous is not None

        original_triangulate = surface_mesh.triangulate_polygon
        original_recombine = surface_mesh.recombine_triangles

        def blocked_triangulate(*args, **kwargs):
            entered.set()
            assert release.wait(2.0)
            return original_triangulate(*args, **kwargs)

        def observed_recombine(*args, **kwargs):
            recombined.set()
            return original_recombine(*args, **kwargs)

        monkeypatch.setattr(surface_mesh, "triangulate_polygon", blocked_triangulate)
        monkeypatch.setattr(surface_mesh, "recombine_triangles", observed_recombine)

        try:
            session.request_remesh(component)
            assert entered.wait(2.0)
            assert session.runtime.cancel_component(component)
            release.set()
            assert session.runtime.wait_for_idle(timeout=5.0)
        finally:
            release.set()

        assert not recombined.is_set()
        assert session.runtime.publication(component) is previous
        assert any(
            event.kind is ComponentUpdateKind.CANCELLED
            for event in session.runtime.poll_events()
        )
