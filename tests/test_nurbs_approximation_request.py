"""Explicit approximation authority survives public project/request boundaries."""
from types import SimpleNamespace
import pytest
from anygeometry import to_dict
from anyfem.model.project import Project, ProjectError
from anyfem.native_meshing import NativeMeshSettings, MeshBackend, CertificationMode
from anyfem.native_meshing_backend import NativeProjectMeshingSession
from anyfem.mesh_execution import MeshExecutionPolicy
from anyfem.io.project_file import project_to_dict, project_from_dict
import anyfem.model.project as project_module
import anyfem.native_meshing_backend as backend_module


def sheet_project():
    project = Project('approximation authority')
    vertices = project.geometry.add_points(((0,0,0),(1,0,0),(1,1,0),(0,1,0)))
    face = project.geometry.add_plate(vertices)
    sheet = project.geometry.add_sheet((face,))
    project.target_size = .5
    return project, sheet


@pytest.mark.parametrize('value', [True, 0., -1., float('nan'), float('inf'), '.01',
    pytest.param(10**1000, id='overflowing-real')])
def test_project_rejects_invalid_approximation_before_geometry_change(value):
    project, _ = sheet_project()
    before = to_dict(project.geometry)
    with pytest.raises(ProjectError, match='finite positive real'):
        project.generate_mesh(geometry_approximation_error=value,
                              execution_policy=MeshExecutionPolicy.off())
    assert to_dict(project.geometry) == before


def test_project_bound_reaches_mesh_and_invalidates_preparation_cache(monkeypatch):
    project, _ = sheet_project()
    captured = []
    before = to_dict(project.geometry)
    def generate(_geometry, **kwargs):
        captured.append(kwargs['geometry_approximation_error'])
        return SimpleNamespace(hybrid_diagnostics={})
    monkeypatch.setattr(project_module, 'generate_hybrid_mesh', generate)
    project.generate_mesh(strategy='native', geometry_approximation_error=.01,
                          execution_policy=MeshExecutionPolicy.off())
    first_key = project._mesh_preparation_cache[0]
    project.generate_mesh(strategy='native', geometry_approximation_error=.001,
                          execution_policy=MeshExecutionPolicy.off())
    assert first_key != project._mesh_preparation_cache[0]
    assert captured == [.01, .001]
    assert project._last_mesh_preparation['geometry_approximation_error'] == .001
    assert to_dict(project.geometry) == before


def test_persisted_approximation_setting_round_trips_and_reaches_component_request(monkeypatch):
    project, sheet = sheet_project()
    settings = NativeMeshSettings.create(.5, backend='native',
        parameters={'geometry_approximation_error': .01})
    project.set_native_mesh_settings(settings)
    restored = project_from_dict(project_to_dict(project))
    assert dict(restored.native_mesh_settings.parameters)['geometry_approximation_error'] == .01
    seen = []
    def generate(_geometry, **kwargs):
        seen.append(kwargs['geometry_approximation_error'])
        return SimpleNamespace(mesh=object(), certifiable=False, strategy_by_face={})
    monkeypatch.setattr(backend_module, 'generate_hybrid_mesh_result', generate)
    component = project.geometry.handle('sheet', sheet)
    cancellation = SimpleNamespace(raise_if_cancelled=lambda phase: None)
    with NativeProjectMeshingSession(project, settings) as session:
        request = SimpleNamespace(cancellation=cancellation,
            snapshot=session.capture_component(component), component=component,
            settings=settings, controls=(), backend=MeshBackend.NATIVE,
            certification_mode=CertificationMode.INTERACTIVE, changes=None)
        session.generate_component(request)
    assert seen == [.01]


def test_project_positive_fraction_normalizes_before_forwarding(monkeypatch):
    from fractions import Fraction
    project, _ = sheet_project()
    captured = []
    def generate(_geometry, **kwargs):
        captured.append(kwargs['geometry_approximation_error'])
        return SimpleNamespace(hybrid_diagnostics={})
    monkeypatch.setattr(project_module, 'generate_hybrid_mesh', generate)
    project.generate_mesh(strategy='native', geometry_approximation_error=Fraction(1,100),
                          execution_policy=MeshExecutionPolicy.off())
    assert captured == [.01] and type(captured[0]) is float


def curved_project():
    from anygeometry.nurbs import NurbsSurface, NurbsCurve, CurveOnSurface
    project = Project('new curved recovery')
    model = project.geometry
    support = NurbsSurface(1,1,(0.,0.,1.,1.),(0.,0.,1.,1.),
        (((0.,0.,0.),(0.,1.,0.)),((1.,0.,0.),(1.,1.,.1))))
    uv = ((0.,0.),(1.,0.),(1.,1.),(0.,1.))
    curves = tuple(CurveOnSurface(support,NurbsCurve(1,(0.,0.,1.,1.),
                   (uv[i],uv[(i+1)%4]))) for i in range(4))
    points = tuple(model.add_point(*curve.exact_point(0)) for curve in curves)
    edges = tuple(model.add_curve(points[i],points[(i+1)%4],curve)
                  for i,curve in enumerate(curves))
    face = model.add_face(edges,surface=support)
    sheet = model.add_sheet((face,))
    return project, sheet


def test_default_project_quad_first_selects_existing_recovery_before_mesh_generation(monkeypatch):
    import anymesher
    project, _ = curved_project()
    before = to_dict(project.geometry)
    class Selected(Exception):
        pass
    def selected(_geometry, **kwargs):
        assert kwargs['automation'].strict_method is False
        assert kwargs['curvilinear_recovery_selection']['requested_strategy'] == 'quad_first'
        assert kwargs['geometry_approximation_error'] == .01
        assert kwargs['_native_surface_options'] is not None
        raise Selected
    monkeypatch.setattr(anymesher,'generate_automatic_mesh_result',selected)
    with pytest.raises(Selected):
        project.generate_mesh(target_size=.25, strategy='quad_first', order='linear',
            certification_mode='interactive', geometry_approximation_error=.01,
            execution_policy=MeshExecutionPolicy.off())
    assert to_dict(project.geometry) == before


def test_default_component_quad_first_uses_same_shared_recovery_selector(monkeypatch):
    import anymesher.recovery as recovery
    project, sheet = curved_project()
    settings = NativeMeshSettings.create(.25, backend='quad_first',
        parameters={'geometry_approximation_error':.01})
    before = to_dict(project.geometry)
    class Selected(Exception):
        pass
    def selected(_geometry, **kwargs):
        assert kwargs['automation'].strict_method is False
        assert kwargs['curvilinear_recovery_selection']['requested_strategy'] == 'quad_first'
        assert kwargs['geometry_approximation_error'] == .01
        raise Selected
    monkeypatch.setattr(recovery,'generate_automatic_mesh_result',selected)
    component = project.geometry.handle('sheet',sheet)
    with NativeProjectMeshingSession(project,settings) as session:
        request = SimpleNamespace(cancellation=SimpleNamespace(raise_if_cancelled=lambda stage:None),
            snapshot=session.capture_component(component), component=component, settings=settings,
            controls=(), backend=MeshBackend.QUAD_FIRST,
            certification_mode=CertificationMode.INTERACTIVE, changes=None)
        with pytest.raises(Selected):
            session.generate_component(request)
    assert to_dict(project.geometry) == before
