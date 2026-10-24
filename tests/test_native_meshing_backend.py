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


def test_component_quad_first_uses_owner_route_and_keeps_identity() -> None:
    project, sheet_id, _vertex = _sheet_project()
    component = project.geometry.handle("sheet", sheet_id)
    settings = NativeMeshSettings.create(
        0.5, element_order="quadratic", backend="quad_first",
        certification_mode="strict",
    )
    with NativeProjectMeshingSession(project, settings, max_background_jobs=1) as session:
        assert session.request_remesh(component) == (component,)
        assert session.runtime.wait_for_idle(timeout=10.0)
        publication = session.runtime.publication(component)
        assert publication is not None
        assert publication.mesh.geometry_model_id == project.geometry.model_id
        assert publication.mesh.hybrid_diagnostics["high_order_geometry"]["status"] == "CERTIFIED_POSITIVE"


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
            surface_mesh,
            "recombine_triangles_with_report",
            observed_recombine,
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


@pytest.mark.parametrize('cancel_at_owner', (False, True))
def test_public_nurbs_component_returns_to_original_source_with_assignments_and_fresh_s3(monkeypatch, cancel_at_owner):
    import anygeometry as geometry_owner
    from anygeometry import to_dict
    from anygeometry.nurbs import NurbsSurface, NurbsCurve, CurveOnSurface
    from anyfem import steel
    project = Project('owner-qualified NURBS closure')
    geometry = project.geometry
    unrelated = geometry.add_points(((9., 9., 9.), (10., 9., 9.)))
    geometry.add_line(*unrelated)  # not a component: closure vertex/edge IDs must change
    support = NurbsSurface(1, 1, (0., 0., 1., 1.), (0., 0., 1., 1.),
        (((0., 0., 0.), (0., 1., 0.)), ((1., 0., 0.), (1., 1., 0.))))
    uv = ((0., 0.), (1., 0.), (1., 1.), (0., 1.))
    vertices = geometry.add_points(tuple((*p, 0.) for p in uv))
    edges = tuple(geometry.add_curve(vertices[i], vertices[(i+1)%4], CurveOnSurface(support,
        NurbsCurve(1, (0., 0., 1., 1.), (p, uv[(i+1)%4])))) for i, p in enumerate(uv))
    face = geometry.add_face(edges, surface=support)
    sheet = geometry.add_sheet((face,))
    project.add_material(steel('S355', .01))
    project.add_plate_section('deck', .01, 'S355')
    project.assign_plate(face, 'deck')
    assignments = project.section_assignments.copy(), project.face_assignment_ids.copy()
    before = to_dict(geometry)
    project.set_native_triangulation_backend('python')
    settings = NativeMeshSettings.create(2., element_order='linear', backend='native',
        certification_mode='interactive', parameters={'geometry_approximation_error': .0025, 'recombine': False})
    component = geometry.handle('sheet', sheet)
    with NativeProjectMeshingSession(project, settings, max_background_jobs=1) as session:
        request = _component_request(session, component, settings=settings)
        from anyfem.native_meshing import NativeMeshingCancellation
        request.cancellation = NativeMeshingCancellation()
        assert set(request.snapshot.geometry.edges) != set(edges)
        actual = geometry_owner.validate_prepared_material_curvilinear_cells
        signals = []
        def observed_proof(*args, **kwargs):
            signals.append(kwargs.get('cancellation_check'))
            if cancel_at_owner:
                request.cancellation.cancel('cancel during actual owner proof')
            return actual(*args, **kwargs)
        monkeypatch.setattr(geometry_owner, 'validate_prepared_material_curvilinear_cells', observed_proof)
        if cancel_at_owner:
            from anyfem.native_meshing import NativeMeshCancelled
            with pytest.raises(NativeMeshCancelled, match='cancel during actual owner proof'):
                session.generate_component(request)
            assert signals[0] is request.cancellation.geometry_cancellation
            assert session.runtime.publication(component) is None
            assert to_dict(geometry) == before
            assert (project.section_assignments, project.face_assignment_ids) == assignments
            return
        result = session.generate_component(request)
        assert signals[0] is request.cancellation.geometry_cancellation
    mesh = result.mesh
    assert mesh.tris and set(mesh.elements_of_face) == {face}
    assert set(mesh.nodes_of_edge) == set(edges)
    assert mesh.geometry_model_id == geometry.model_id and mesh.geometry_revision == geometry.revision
    assert mesh.structural_preparation['qualified_s3']['status'] == 'ADMITTED'
    assert mesh.structural_preparation['qualified_s3']['authority_model']['source_model_id'] == str(geometry.model_id)
    assert project.face_sections == {face: 'deck'}
    assert (project.section_assignments, project.face_assignment_ids) == assignments
    assert to_dict(geometry) == before


@pytest.mark.parametrize('cancel_requested', (False, True))
def test_passive_geometry_cancellation_keeps_native_outcome(monkeypatch, cancel_requested):
    from anygeometry import GeometryError, is_cancellation_signal, to_dict
    from anyfem.native_meshing import NativeMeshingCancellation, NativeMeshCancelled
    project, sheet, _ = _sheet_project()
    settings = NativeMeshSettings.create(.5, backend='native')
    component = project.geometry.handle('sheet', sheet)
    before = to_dict(project.geometry)
    with NativeProjectMeshingSession(project, settings, max_background_jobs=1) as session:
        request = _component_request(session, component, settings=settings)
        request.cancellation = NativeMeshingCancellation()
        signal = request.cancellation.geometry_cancellation
        assert is_cancellation_signal(signal) and not signal('before cancellation')
        original_error = GeometryError('owner proof cancelled' if cancel_requested else 'owner proof invalid')

        def cancelled_proof(*args, **kwargs):
            assert kwargs['cancellation_check'] is signal
            if cancel_requested:
                assert request.cancellation.cancel('test cancellation')
                assert signal('owner proof')
            raise original_error

        monkeypatch.setattr(backend_module, 'generate_hybrid_mesh_result', cancelled_proof)
        if cancel_requested:
            with pytest.raises(NativeMeshCancelled, match='test cancellation'):
                session.generate_component(request)
        else:
            with pytest.raises(GeometryError) as raised:
                session.generate_component(request)
            assert raised.value is original_error
        assert session.runtime.publication(component) is None
    assert to_dict(project.geometry) == before


def test_incremental_session_snapshots_project_backend(monkeypatch) -> None:
    project, sheet_id, _vertex = _sheet_project()
    component = project.geometry.handle("sheet", sheet_id)
    settings = NativeMeshSettings(target_size=0.3, backend="native")
    project.set_native_triangulation_backend("python")
    captured: list[str] = []

    def fake_generate(_geometry, **kwargs):
        captured.append(kwargs["native_backend"])
        assert "native_backend" not in dict(settings.parameters)
        assert kwargs["structural_preparation"] == {
            "automatic_face_connections": False,
            "automatic_member_connections": False,
            "automatic_member_sheet_connections": False,
            "declare_missing_owners": True,
        }
        assert kwargs["mutation_policy"] == "working_copy"
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
