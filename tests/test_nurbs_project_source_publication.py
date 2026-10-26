"""Public Project forwarding reaches source publication with its live source."""
import pytest
from types import SimpleNamespace
from anygeometry import to_dict
from anymesher import Mesh
from anyfem.mesh_execution import MeshExecutionPolicy
from test_nurbs_approximation_request import curved_project, project_module


def test_project_forwards_live_source_and_cancellation_to_publication(monkeypatch):
    import anymesher
    project, _sheet = curved_project()
    before = to_dict(project.geometry)
    callback = lambda _phase: False
    class PublicationReached(Exception):
        pass
    # Intercept before any authority or mesh is published; this is forwarding
    # evidence only, never a successful geometric-admission substitute.
    monkeypatch.setattr(anymesher, 'generate_automatic_mesh_result',
                        lambda *args, **kwargs: SimpleNamespace(mesh=Mesh()))
    def publication(mesh, closure, **kwargs):
        assert kwargs['source_geometry'] is project.geometry
        assert kwargs['cancellation_check'] is callback
        assert kwargs['qualification'] is not None
        assert project._mesh_preparation_qualification is None
        raise PublicationReached()
    monkeypatch.setattr(project_module, 'remap_mesh_to_source', publication)
    with pytest.raises(PublicationReached):
        project.generate_mesh(.25, strategy='quad_first', order='linear',
            certification_mode='interactive', geometry_approximation_error=.0025,
            cancellation_check=callback, execution_policy=MeshExecutionPolicy.off())
    assert to_dict(project.geometry) == before
    assert project._mesh_preparation_cache is None
    assert project._mesh_preparation_qualification is None


def test_principal_public_project_prepares_complete_owned_face_scope_before_meshing(monkeypatch):
    import anymesher
    from test_nurbs_cut_mesh_integration import principal_project
    project = principal_project()
    walls = tuple(face for face in project.geometry.faces
                  if type(project.geometry.faces[face].surface).__name__ != 'Plane')
    plate = next(face for face in project.geometry.faces if face not in walls)
    project.add_plate_section('cut plate', thickness=.02, material='S355')
    project.assign_plates(walls, 'plate')
    project.assign_plate(plate, 'cut plate')
    before = to_dict(project.geometry)
    before_assignments = dict(project.section_assignments)
    prepared = {}
    actual_extract = project_module.extract_model_closure
    actual_prepare = project_module.prepare_structural_connectivity
    def extract(*args, **kwargs):
        prepared['closure'] = actual_extract(*args, **kwargs)
        return prepared['closure']
    def prepare(*args, **kwargs):
        prepared['report'] = actual_prepare(*args, **kwargs)
        return prepared['report']
    monkeypatch.setattr(project_module, 'extract_model_closure', extract)
    monkeypatch.setattr(project_module, 'prepare_structural_connectivity', prepare)
    class PreparedPrincipalReached(Exception):
        pass
    def inspect(working, **request):
        # This is the real Project closure and actual owner batch result,
        # intercepted only before numerical mesh generation, never fabricated.
        assert len(working.faces) == 10
        traces = tuple(edge for edge in working.edges if len(working.faces_using_edge(edge)) == 4)
        assert len(traces) == 4
        assert not working.validate_topology()
        assert working.sheets
        assert request['geometry_approximation_error'] == .0025
        assert request['order'] == 'linear'
        assert request['curvilinear_recovery_selection']['requested_strategy'] == 'quad_first'
        assert project._mesh_preparation_cache is None
        cached_closure, report = prepared['closure'], prepared['report']
        assert report.connections and report.created_count > 0
        originals = {target.id: original.id
                     for target,original in cached_closure.work_to_source.items()
                     if target.kind == 'face'}
        related_edges = set()
        for connection in report.connections:
            assert connection.first.startswith('face:') and connection.second.startswith('face:')
            parents = {originals[int(label.split(':')[1])]
                       for label in (connection.first,connection.second)}
            assert plate in parents and len(parents & set(walls)) == 1
            assert connection.relations
            related_edges.update(int(label.split(':')[1]) for label in connection.relations)
        assert related_edges == set(traces)
        raise PreparedPrincipalReached()
    monkeypatch.setattr(anymesher, 'generate_automatic_mesh_result', inspect)
    with pytest.raises(PreparedPrincipalReached):
        project.generate_mesh(.25, strategy='quad_first', order='linear',
            certification_mode='interactive', geometry_approximation_error=.0025,
            execution_policy=MeshExecutionPolicy.off())
    assert to_dict(project.geometry) == before
    assert project.section_assignments == before_assignments
    assert project._mesh_preparation_cache is None
    assert project._mesh_preparation_qualification is None


def test_ordinary_planar_preparation_keeps_sheet_operands(monkeypatch):
    import anygeometry as owner
    from anyfem.structural_preparation import prepare_structural_connectivity
    model = owner.GeometryModel()
    points = model.add_points(((0.,0.,0.),(1.,0.,0.),(1.,1.,0.),(0.,1.,0.)))
    face = model.add_plate(points)
    sheet = model.add_sheet((face,))
    before = to_dict(model)
    class OwnerPlanReached(Exception):
        pass
    def inspect(geometry, operands, **kwargs):
        assert geometry is model
        assert tuple(operands) == (model.handle('sheet',sheet),)
        raise OwnerPlanReached()
    monkeypatch.setattr(owner, 'plan_intersections', inspect)
    with pytest.raises(OwnerPlanReached):
        prepare_structural_connectivity(model)
    assert to_dict(model) == before


def test_connection_report_relations_have_actual_original_owner_incidence():
    import anygeometry as owner
    from anyfem.structural_preparation import prepare_structural_connectivity
    model = owner.GeometryModel()
    first = model.add_plate(model.add_points(((-1.,-1.,0.),(1.,-1.,0.),
                                             (1.,1.,0.),(-1.,1.,0.))))
    second = model.add_plate(model.add_points(((0.,-1.,-1.),(0.,1.,-1.),
                                              (0.,1.,1.),(0.,-1.,1.))))
    model.add_sheet((first,))
    model.add_sheet((second,))
    report = prepare_structural_connectivity(model)
    assert len(report.connections) == 1
    connection = report.connections[0]
    assert {connection.first,connection.second} == {f'face:{first}',f'face:{second}'}
    descendants = {parent: {ref.id for ref in model.resolve_ref(owner.EntityRef('face',parent))}
                   for parent in (first,second)}
    assert connection.relations
    for label in connection.relations:
        edge = int(label.split(':')[1])
        incident = set(model.faces_using_edge(edge))
        assert all(incident & children for children in descendants.values())


def test_connections_do_not_report_other_owner_pairs_trace_edges():
    import anygeometry as owner
    from anyfem.structural_preparation import prepare_structural_connectivity
    model = owner.GeometryModel()
    plate = model.add_plate(model.add_points(((-1.,-1.,0.),(1.,-1.,0.),
                                              (1.,1.,0.),(-1.,1.,0.))))
    walls = tuple(model.add_plate(model.add_points(((x,-1.,-1.),(x,1.,-1.),
                                                    (x,1.,1.),(x,-1.,1.))))
                  for x in (-.5,.5))
    for face in (plate,*walls):
        model.add_sheet((face,))
    report = prepare_structural_connectivity(model)
    assert len(report.connections) == 2
    descendants = {parent: {ref.id for ref in model.resolve_ref(owner.EntityRef('face',parent))}
                   for parent in (plate,*walls)}
    reported = {}
    for connection in report.connections:
        parents = {int(label.split(':')[1]) for label in (connection.first,connection.second)}
        assert plate in parents and len(parents & set(walls)) == 1
        wall = next(iter(parents & set(walls)))
        edges = {int(label.split(':')[1]) for label in connection.relations}
        assert edges
        for edge in edges:
            incident = set(model.faces_using_edge(edge))
            assert all(incident & descendants[parent] for parent in parents)
            assert not incident & descendants[next(other for other in walls if other != wall)]
        reported[wall] = edges
    assert set(reported) == set(walls)
    assert reported[walls[0]].isdisjoint(reported[walls[1]])

def test_project_cache_keeps_live_qualification_and_exact_source_maps(monkeypatch):
    """A real successful mesh establishes the cache before refused reuse."""
    import anymesher
    import anygeometry as owner
    project, _sheet = curved_project()
    before = to_dict(project.geometry)
    real_qualify = anymesher.qualify_model_closure_source_publication
    issued, published = [], []
    def qualify(source, closure, **kwargs):
        token = real_qualify(source, closure, **kwargs)
        issued.append((token, closure))
        return token
    monkeypatch.setattr(anymesher, 'qualify_model_closure_source_publication', qualify)
    project.generate_mesh(.25, strategy='quad_first', order='linear',
        certification_mode='interactive', geometry_approximation_error=.0025,
        execution_policy=MeshExecutionPolicy.off())
    baseline_cache = project._mesh_preparation_cache
    baseline_token = project._mesh_preparation_qualification
    assert baseline_cache is not None and baseline_token is issued[0][0]
    monkeypatch.setattr(anymesher, 'generate_automatic_mesh_result',
                        lambda *args, **kwargs: SimpleNamespace(mesh=Mesh()))
    class PublicationReached(Exception):
        pass
    def inspect(_mesh, closure, **kwargs):
        assert kwargs['source_geometry'] is project.geometry
        assert kwargs['qualification'] is issued[0][0]
        original = issued[0][1]
        assert closure.source_to_work is original.source_to_work
        assert closure.work_to_source is original.work_to_source
        assert closure.source_handles is original.source_handles
        published.append(closure)
        raise PublicationReached()
    monkeypatch.setattr(project_module, 'remap_mesh_to_source', inspect)
    for _attempt in range(2):
        with pytest.raises(PublicationReached):
            project.generate_mesh(.25, strategy='quad_first', order='linear',
                certification_mode='interactive', geometry_approximation_error=.0025,
                execution_policy=MeshExecutionPolicy.off())
    assert len(issued) == 1 and len(published) == 2
    assert len(project._mesh_preparation_cache) == 3
    assert project._mesh_preparation_cache is baseline_cache
    assert project._mesh_preparation_qualification is baseline_token
    assert published[0].working_model is not published[1].working_model
    assert published[0].working_model.model_id == published[1].working_model.model_id
    assert to_dict(project.geometry) == before


def test_project_failed_new_preparation_keeps_previous_cache_and_qualification(monkeypatch):
    """A failed cache miss cannot pair an old cache with a newly issued token."""
    import anymesher
    project, _sheet = curved_project()
    request = dict(strategy='quad_first', order='linear', certification_mode='interactive',
                   geometry_approximation_error=.0025, execution_policy=MeshExecutionPolicy.off())
    project.generate_mesh(.25, **request)
    monkeypatch.setattr(anymesher, 'generate_automatic_mesh_result',
                        lambda *args, **kwargs: SimpleNamespace(mesh=Mesh()))
    class PublicationReached(Exception):
        pass
    def stop(*_args, **_kwargs):
        raise PublicationReached()
    monkeypatch.setattr(project_module, 'remap_mesh_to_source', stop)
    with pytest.raises(PublicationReached):
        project.generate_mesh(.25, **request)
    cache, token = project._mesh_preparation_cache, project._mesh_preparation_qualification
    assert cache is not None and token is not None
    class Cancelled(BaseException):
        pass
    failure = Cancelled()
    def fail_preparation(*_args, **_kwargs):
        raise failure
    monkeypatch.setattr(project_module, 'prepare_structural_connectivity', fail_preparation)
    request['geometry_approximation_error'] = .001
    with pytest.raises(Cancelled) as caught:
        project.generate_mesh(.25, **request)
    assert caught.value is failure
    assert project._mesh_preparation_cache is cache
    assert project._mesh_preparation_qualification is token


def test_native_component_qualifies_fresh_uncut_snapshot_before_publication(monkeypatch):
    """Transported native snapshots issue their own local token; no token serde."""
    import anymesher
    from anymesher import Mesh
    from anyfem.native_meshing_backend import NativeProjectMeshingSession
    from anyfem.native_meshing import NativeMeshSettings, MeshBackend, CertificationMode
    from test_nurbs_approximation_request import backend_module
    project, sheet = curved_project()
    settings = NativeMeshSettings.create(.25, backend='native',
        parameters={'geometry_approximation_error':.0025})
    issued = []
    actual = anymesher.qualify_model_closure_source_publication
    def qualify(source, closure, **kwargs):
        token = actual(source, closure, **kwargs)
        issued.append((token, closure))
        return token
    monkeypatch.setattr(anymesher, 'qualify_model_closure_source_publication', qualify)
    monkeypatch.setattr(backend_module, 'generate_hybrid_mesh_result',
                        lambda *args, **kwargs: SimpleNamespace(mesh=Mesh()))
    class PublicationReached(Exception):
        pass
    def inspect(_mesh, closure, **kwargs):
        assert len(issued) == 1 and issued[0][1] is closure
        assert kwargs['qualification'] is issued[0][0]
        assert kwargs['source_geometry'] is project.geometry
        raise PublicationReached()
    monkeypatch.setattr(backend_module, 'remap_mesh_to_source', inspect)
    cancellation = SimpleNamespace(raise_if_cancelled=lambda _phase: None)
    component = project.geometry.handle('sheet', sheet)
    before = to_dict(project.geometry)
    with NativeProjectMeshingSession(project, settings) as session:
        snapshot = session.capture_component(component)
        assert not issued
        request = SimpleNamespace(cancellation=cancellation, snapshot=snapshot,
            component=component, settings=settings, controls=(), backend=MeshBackend.NATIVE,
            certification_mode=CertificationMode.INTERACTIVE, changes=None)
        with pytest.raises(PublicationReached):
            session.generate_component(request)
    assert to_dict(project.geometry) == before

@pytest.mark.parametrize('curved', (True, False))
def test_native_curvilinear_recovery_requests_s3_without_changing_ordinary_audit_delegation(monkeypatch, curved):
    """Effective options only; interception issues no successful S3 authority."""
    import anymesher.recovery as recovery
    from anyfem.native_meshing_backend import NativeProjectMeshingSession
    from anyfem.native_meshing import NativeMeshSettings, MeshBackend, CertificationMode
    from test_nurbs_approximation_request import backend_module, sheet_project
    project, sheet = curved_project() if curved else sheet_project()
    backend = MeshBackend.QUAD_FIRST if curved else MeshBackend.NATIVE
    parameters = {'geometry_approximation_error':.0025} if curved else {}
    settings = NativeMeshSettings.create(.25, backend=backend.value, parameters=parameters)
    before = to_dict(project.geometry)
    reached = []
    class OptionsReached(Exception):
        pass
    def inspect(_geometry, **options):
        reached.append(options)
        # Existing source-controller interactive auditing remains delegated.
        assert options['certification_mode'] == 'none'
        assert options['order'] == 'linear'
        if curved:
            assert options['qualified_s3'] is True
            assert options['curvilinear_recovery_selection']['requested_strategy'] == 'quad_first'
            assert options['automation'].strict_method is False
            assert options['automation'].allow_inspection is False
            assert options['geometry_approximation_error'] == .0025
        else:
            assert 'qualified_s3' not in options
            assert 'curvilinear_recovery_selection' not in options
        raise OptionsReached()
    monkeypatch.setattr(recovery, 'generate_automatic_mesh_result', inspect)
    monkeypatch.setattr(backend_module, 'generate_hybrid_mesh_result', inspect)
    component = project.geometry.handle('sheet', sheet)
    with NativeProjectMeshingSession(project, settings) as session:
        request = SimpleNamespace(cancellation=SimpleNamespace(raise_if_cancelled=lambda _phase: None),
            snapshot=session.capture_component(component), component=component, settings=settings,
            controls=(), backend=backend, certification_mode=CertificationMode.INTERACTIVE, changes=None)
        with pytest.raises(OptionsReached):
            session.generate_component(request)
    assert len(reached) == 1
    assert to_dict(project.geometry) == before
