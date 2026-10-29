import pytest
from anyfem.mesh_jobs import MeshSettings, run_mesh_job, _exception_diagnostic
from anyfem.mesh_execution import MeshExecutionPolicy
from anyfem.jobs import _cancellation_token
from anyfem.model.project import Project, ProjectError


def plate():
    project = Project('preparation-phase')
    project.geometry.add_plate(project.geometry.add_points(((0.,0.,0.),(1.,0.,0.),(1.,1.,0.),(0.,1.,0.))))
    return project


def test_actual_job_reports_preparation_before_element_generation():
    project = plate()
    events = []
    run_mesh_job(project, 'test-model',
                 MeshSettings.create(.5, element_order='linear', execution_policy=MeshExecutionPolicy.off()),
                 lambda *args: events.append(args), _cancellation_token())
    prep = next(i for i, event in enumerate(events) if event[0] == 'preparation')
    generation = next(i for i, event in enumerate(events) if event[0] == 'generation')
    assert prep < generation
    assert 'structural' in events[prep][1].lower()
    assert any(event[0] == 'preparation' and 'batch' in event[1] for event in events)


def test_public_project_failure_retains_nested_pair_evidence_and_runtime(monkeypatch):
    import anyfem.model.project as module
    from anyfem.structural_preparation import StructuralPreparationError
    from anygeometry.errors import GeometryError
    from anygeometry.runtime_diagnostics import _increment
    class Refusal(GeometryError):
        def to_diagnostic(self):
            return {'face_pair': (199,200), 'reason': 'test_bound', 'tolerance': 1e-9}
    def fail(*args, **kwargs):
        _increment('test_preparation_count')
        try:
            try:
                raise Refusal('unproved')
            except Refusal as error:
                raise GeometryError('intersection batch face:199/face:200') from error
        except GeometryError as error:
            raise StructuralPreparationError('cannot prepare') from error
    monkeypatch.setattr(module, 'prepare_structural_connectivity', fail)
    project = plate()
    before = project.geometry.revision
    with pytest.raises(ProjectError) as caught:
        project.generate_mesh(.5)
    payload = _exception_diagnostic(caught.value)['details']
    assert payload['element_generation_started'] is False
    assert payload['cause']['face_pair'] == (199,200)
    assert payload['geometry_runtime']['test_preparation_count'] == 1
    assert project.geometry.revision == before
    import pickle
    restored = pickle.loads(pickle.dumps(caught.value))
    assert isinstance(restored, ProjectError)
    assert restored.to_diagnostic() == caught.value.to_diagnostic()


def test_preparation_cancellation_propagates_without_failure_conversion():
    probe = _cancellation_token()
    probe.cancel()
    with pytest.raises(Exception) as expected:
        probe.raise_if_cancelled('structural preparation')
    project = plate()
    before = project.geometry.revision
    cancellation = _cancellation_token()
    def progress(stage, message, fraction):
        if stage == 'preparation':
            cancellation.cancel()
    with pytest.raises(type(expected.value)):
        run_mesh_job(project, 'model', MeshSettings.create(.5, element_order='linear', execution_policy=MeshExecutionPolicy.off()),
                     progress, cancellation)
    assert project.geometry.revision == before
