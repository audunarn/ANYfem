"""Public Project forwarding reaches source publication with its live source."""
import pytest
from types import SimpleNamespace
from anygeometry import to_dict
from anymesher import Mesh
from anyfem.mesh_execution import MeshExecutionPolicy
from test_nurbs_approximation_request import curved_project, project_module




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












