"""A geometry approximation bound routes by owner classification, not by presence."""
import pytest
import anymesher
import anymesher.material_source_transfer as routing
from anygeometry.serialization import to_dict as geometry_to_dict
from anyfem.model.project import ProjectError, source_publication_required
from test_loft_basic_public_integration import _constructed
from test_loft_public_integration import _assigned


@pytest.mark.parametrize('kind', ('line', 'square'))
def test_scalar_bound_on_planar_input_skips_source_publication(kind):
    project, faces = _constructed(kind); geometry = project.geometry
    _assigned(project, faces)
    # Owner classification, not overridden here: report if this ever fails.
    assert source_publication_required(geometry) is False
    before = geometry_to_dict(geometry)
    mesh = project.generate_mesh(.5, geometry_approximation_error=.02)
    assert mesh.shells
    assert geometry_to_dict(geometry) == before
    assert project._mesh_preparation_qualification is None
    assert project._last_mesh_preparation['geometry_approximation_error'] == .02


def test_planar_mesher_call_omits_unsupported_bound_but_keeps_metadata(monkeypatch):
    import anymesher.hybrid as hybrid
    import anymesher.component_parallel as parallel
    import anyfem.model.project as project_module
    calls = []

    def spy(name, action):
        def wrapped(*args, **kwargs):
            calls.append((name, set(kwargs)))
            return action(*args, **kwargs)
        return wrapped

    monkeypatch.setattr(project_module, 'generate_hybrid_mesh',
                        spy('generate_hybrid_mesh', project_module.generate_hybrid_mesh))
    monkeypatch.setattr(hybrid, 'generate_hybrid_mesh_result',
                        spy('generate_hybrid_mesh_result', hybrid.generate_hybrid_mesh_result))
    monkeypatch.setattr(parallel, 'generate_hybrid_mesh_result',
                        spy('parallel_serial_fallback', parallel.generate_hybrid_mesh_result))
    project, faces = _constructed('line')
    _assigned(project, faces)
    mesh = project.generate_mesh(.5, geometry_approximation_error=.02)
    assert mesh.shells
    assert calls, 'planar bounded mesh did not reach the hybrid mesher'
    assert all('geometry_approximation_error' not in keys for _, keys in calls)
    assert project._mesh_preparation_qualification is None
    assert project._last_mesh_preparation['geometry_approximation_error'] == .02
    # Cache identity still distinguishes the bound.
    assert project._mesh_preparation_cache[0][-1] == .02


def test_curved_route_refuses_without_owner_publication_service(monkeypatch):
    project, faces = _constructed('square')
    _assigned(project, faces)
    calls = []

    def curved(geometry, mesh):
        calls.append((geometry, mesh))
        return True

    # Simulates only the owner's routing answer; no qualification is granted.
    monkeypatch.setattr(routing, 'requires_material_source_transfer', curved)
    monkeypatch.delattr(anymesher, 'qualify_model_closure_source_publication', raising=False)
    with pytest.raises(ProjectError, match='curved source publication'):
        project.generate_mesh(.5, geometry_approximation_error=.02)
    assert calls == [(project.geometry, None)]
    assert project._mesh_preparation_cache is None


def test_unbounded_mesh_does_not_query_source_routing(monkeypatch):
    project, faces = _constructed('square')
    _assigned(project, faces)

    def unexpected(geometry, mesh):
        raise AssertionError('routing queried without an approximation bound')

    monkeypatch.setattr(routing, 'requires_material_source_transfer', unexpected)
    assert project.generate_mesh(.5).shells
