from __future__ import annotations

import threading
from types import SimpleNamespace

import anymesher.surface_mesh as surface_mesh
import pytest
from anyfem.model.project import Project
from anyfem.native_meshing import (
    CertificationMode,
    ComponentUpdateKind,
    ControlScope,
    MeshBackend,
    NativeMeshControl,
    NativeMeshSettings,
)
import anyfem.native_meshing_backend as backend_module
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
        original_recombine = surface_mesh.recombine_triangles_with_report

        def blocked_triangulate(*args, **kwargs):
            entered.set()
            assert release.wait(2.0)
            return original_triangulate(*args, **kwargs)

        def observed_recombine(*args, **kwargs):
            recombined.set()
            return original_recombine(*args, **kwargs)

        monkeypatch.setattr(surface_mesh, "triangulate_polygon", blocked_triangulate)
        monkeypatch.setattr(
            surface_mesh, "recombine_triangles_with_report", observed_recombine
        )

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


class _NoCancellation:
    def raise_if_cancelled(self, _stage: str) -> None:
        return None


def _component_request(session, component, *, settings, controls=()):
    return SimpleNamespace(
        cancellation=_NoCancellation(),
        snapshot=session.capture_component(component),
        component=component,
        settings=settings,
        controls=tuple(controls),
        backend=MeshBackend.NATIVE,
        certification_mode=CertificationMode.INTERACTIVE,
        changes=None,
    )


def test_incremental_session_snapshots_project_backend(monkeypatch) -> None:
    project, sheet_id, _vertex = _sheet_project()
    component = project.geometry.handle("sheet", sheet_id)
    settings = NativeMeshSettings(target_size=0.3, backend="native")
    project.set_native_triangulation_backend("python")
    captured: list[str] = []

    def fake_generate(_geometry, **kwargs):
        captured.append(kwargs["native_backend"])
        assert "native_backend" not in dict(settings.parameters)
        return SimpleNamespace(
            mesh=object(), certifiable=False, strategy_by_face={}
        )

    monkeypatch.setattr(backend_module, "generate_hybrid_mesh_result", fake_generate)

    with NativeProjectMeshingSession(project, settings) as session:
        project.set_native_triangulation_backend("native")
        session.generate_component(
            _component_request(session, component, settings=settings)
        )
    with NativeProjectMeshingSession(project, settings) as session:
        session.generate_component(
            _component_request(session, component, settings=settings)
        )

    assert captured == ["python", "native"]


@pytest.mark.parametrize("source", ("settings", "control"))
def test_incremental_generation_rejects_nested_backend(source: str, monkeypatch) -> None:
    project, sheet_id, _vertex = _sheet_project()
    component = project.geometry.handle("sheet", sheet_id)
    clean = NativeMeshSettings(target_size=0.3, backend="native")
    bad_settings = NativeMeshSettings.create(
        target_size=0.3, parameters={"native_backend": "python"}
    )
    bad_control = NativeMeshControl.create(
        "bad-backend",
        scope=ControlScope((component,)),
        parameters={"native_backend": "python"},
    )

    def must_not_generate(*_args, **_kwargs):
        raise AssertionError("invalid nested backend reached the mesher")

    monkeypatch.setattr(
        backend_module, "generate_hybrid_mesh_result", must_not_generate
    )

    with NativeProjectMeshingSession(project, clean) as session:
        request = _component_request(
            session,
            component,
            settings=bad_settings if source == "settings" else clean,
            controls=(bad_control,) if source == "control" else (),
        )
        with pytest.raises(ValueError, match="project-level setting"):
            session.generate_component(request)
