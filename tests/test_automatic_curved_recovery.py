"""Project resolves live owner approximation before automatic method selection."""
import json
import pytest

from anygeometry import clone_prepared_geometry, query_material_surface_regions, to_dict
from anygeometry.local_approximation_field import bind_local_approximation_field
from anymesher.approximation_policy import validate_geometry_approximation_binding
from anyfem.mesh_execution import MeshExecutionPolicy
from test_nurbs_approximation_request import curved_project


@pytest.mark.parametrize('strategy', ('auto', 'quad_first'))
def test_automatic_recovery_preserves_live_owner_field_across_clones(monkeypatch, strategy):
    import anymesher
    import anymesher.approximation_policy as approximation
    import anymesher.recovery as recovery
    project, _ = curved_project()
    before = to_dict(project.geometry)
    resolutions = []
    actual_resolve = approximation.resolve_geometry_approximation
    def resolve(source, *args, **kwargs):
        result = actual_resolve(source, *args, **kwargs)
        resolutions.append((source, result))
        return result
    monkeypatch.setattr(approximation, 'resolve_geometry_approximation', resolve)
    actual_select = recovery.select_curvilinear_recovery_policy
    def select(*args, **kwargs):
        assert kwargs['geometry_approximation_error'] is not None
        return actual_select(*args, **kwargs)
    monkeypatch.setattr(recovery, 'select_curvilinear_recovery_policy', select)
    class Selected(Exception): pass
    def selected(working, **kwargs):
        source, resolution = resolutions[-1]
        assert working is source and source is not project.geometry
        assert kwargs['geometry_approximation_source'] is source
        assert kwargs['geometry_approximation_resolution'] is resolution
        assert kwargs['geometry_approximation_error'] == resolution.effective_error
        assert resolution.mode == 'automatic' and resolution._field is not None
        validate_geometry_approximation_binding(source, resolution)
        clone = clone_prepared_geometry(working)
        regions = query_material_surface_regions(clone, tuple(clone.faces))
        allocation = bind_local_approximation_field(source, clone, resolution._field, regions)
        assert allocation is not None
        assert kwargs['curvilinear_recovery_selection']['requested_strategy'] == strategy
        json.dumps(resolution.to_dict())
        raise Selected
    monkeypatch.setattr(anymesher, 'generate_automatic_mesh_result', selected)
    with pytest.raises(Selected):
        project.generate_mesh(.25, strategy=strategy, certification_mode='interactive',
                              execution_policy=MeshExecutionPolicy.off())
    assert len(resolutions) == 1
    assert to_dict(project.geometry) == before
    assert project._mesh_preparation_cache is None


@pytest.mark.parametrize('mutation', (False, True))
def test_automatic_resolution_callback_is_guarded_and_atomic(monkeypatch, mutation):
    import anymesher.approximation_policy as approximation
    from anygeometry import GeometryError
    project, _ = curved_project()
    before = to_dict(project.geometry)
    previous = project._last_mesh_preparation
    active = []
    actual = approximation.resolve_geometry_approximation
    sentinel = KeyboardInterrupt('resolution cancelled')
    def resolve(source, *args, **kwargs):
        active.append(source)
        return actual(source, *args, **kwargs)
    monkeypatch.setattr(approximation, 'resolve_geometry_approximation', resolve)
    def callback(phase):
        if active:
            if mutation:
                active[-1].add_point(9., 9., 9.)
            else:
                raise sentinel
    with pytest.raises(GeometryError if mutation else KeyboardInterrupt) as caught:
        project.generate_mesh(.25, strategy='quad_first', certification_mode='interactive',
            cancellation_check=callback, execution_policy=MeshExecutionPolicy.off())
    if not mutation:
        assert caught.value is sentinel
    assert project._last_mesh_preparation is previous
    assert project._mesh_preparation_cache is None
    assert to_dict(project.geometry) == before
