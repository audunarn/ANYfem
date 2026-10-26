"""A failed replacement mesh retains the prior preparation and receipt."""
from types import SimpleNamespace
import pytest
from anygeometry import to_dict
from anyfem.mesh_execution import MeshExecutionPolicy
from test_nurbs_approximation_request import sheet_project, project_module


@pytest.mark.parametrize('cancelled', [False, True])
def test_failed_new_candidate_preserves_prior_cache_receipt_and_preparation(monkeypatch, cancelled):
    project, _ = sheet_project()
    monkeypatch.setattr(project_module, 'generate_hybrid_mesh',
        lambda *args, **kwargs: SimpleNamespace(hybrid_diagnostics={}))
    project.generate_mesh(strategy='native', geometry_approximation_error=.01,
        execution_policy=MeshExecutionPolicy.off())
    cache = project._mesh_preparation_cache
    preparation = project._last_mesh_preparation
    # The opaque marker stands in only for previous state; it is never used
    # for admission because the new approximation bound misses the cache key.
    receipt = object()
    project._mesh_preparation_qualification = receipt
    before = to_dict(project.geometry)
    assignments = dict(project.section_assignments)
    failure = RuntimeError('cancelled' if cancelled else 'cell proof refused')
    def callback(phase):
        if phase == 'cancel native candidate':
            raise failure
    def generate(*args, **kwargs):
        assert project._mesh_preparation_cache is cache
        assert project._mesh_preparation_qualification is receipt
        if cancelled:
            kwargs['cancellation_check']('cancel native candidate')
        raise failure
    monkeypatch.setattr(project_module, 'generate_hybrid_mesh', generate)
    with pytest.raises(RuntimeError) as caught:
        project.generate_mesh(strategy='native', geometry_approximation_error=.0025,
            cancellation_check=callback, execution_policy=MeshExecutionPolicy.off())
    assert caught.value is failure
    assert project._mesh_preparation_cache is cache
    assert project._mesh_preparation_qualification is receipt
    assert project._last_mesh_preparation is preparation
    assert to_dict(project.geometry) == before
    assert project.section_assignments == assignments
