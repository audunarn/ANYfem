"""Curved mesh closures may acquire owners without changing the design."""
from types import SimpleNamespace

import pytest
from anygeometry import SketchDefinition, SketchEntity, SketchWire, to_dict
from anyfem import Project, commands
from anyfem.structural_preparation import (
    StructuralPreparationError, prepare_structural_connectivity,
)


def curved_geometry():
    project = Project('unassigned curved shell')
    definition = SketchDefinition(
        points={'a': (0., 0.), 'b': (1., 1.), 'c': (2., 0.)},
        version=2, closed=False, extrusion=1.,
        entities=(SketchEntity('curve', 'spline', ('a', 'b', 'c')),),
        wires=(SketchWire('profile', ('curve',), False),),
    )
    commands.AddPlaneSketch(definition, (0., 0., 0.), (1., 0., 0.), (0., 1., 0.)).do(project)
    assert not project.geometry.sheets
    return project.geometry


@pytest.fixture
def legacy_curved_chart_refusal(monkeypatch):
    """Exercise the fallback adapter boundary, not fresh geometric qualification."""
    import anygeometry
    from anygeometry.errors import GeometryError
    from anygeometry.surfaces import CoonsSurface

    def refuse(geometry, face_ids):
        assert not geometry.sheets and not geometry.members
        assert len(face_ids) == 1
        handle = face_ids[0]
        assert handle.kind == 'face' and handle.model_id == geometry.model_id
        assert isinstance(geometry.faces[handle.id].surface, CoonsSurface)
        raise GeometryError(f'face {handle.id} has curved Coons boundaries')

    monkeypatch.setattr(anygeometry, 'query_trimmed_surface_charts', refuse)


def test_curved_closure_requests_temporary_owner_policy_only_on_working_copy(monkeypatch, legacy_curved_chart_refusal):
    import anymesher.preparation
    source = curved_geometry()
    before = to_dict(source)
    working = source.clone()
    calls = []

    def prepare(geometry, *, face_ids, options, reuse_working_copy, cancellation_check):
        calls.append(options)
        assert geometry is working and reuse_working_copy
        owners = tuple(geometry.add_sheet((face,)) for face in face_ids)
        return geometry, SimpleNamespace(temporary_sheet_ids=owners)

    monkeypatch.setattr(anymesher.preparation, 'prepare_structural_closure', prepare)
    report = prepare_structural_connectivity(working)
    assert calls == [{'declare_missing_owners': True}]
    assert len(working.sheets) == len(working.faces)
    assert any('temporary Sheet owner' in item for item in report.diagnostics)
    assert to_dict(source) == before


def test_curved_owner_policy_preserves_owner_refusal(monkeypatch, legacy_curved_chart_refusal):
    import anymesher.preparation
    from anymesher.errors import MeshError
    source = curved_geometry()
    before = to_dict(source)

    def refuse(*args, **kwargs):
        raise MeshError('curved overlap remains unqualified')

    monkeypatch.setattr(anymesher.preparation, 'prepare_structural_closure', refuse)
    with pytest.raises(StructuralPreparationError, match='curved overlap remains unqualified'):
        prepare_structural_connectivity(source.clone())
    assert to_dict(source) == before


def test_admitted_curved_charts_use_public_route_without_legacy_owner_fallback(monkeypatch):
    import anymesher.preparation
    from anygeometry import query_trimmed_surface_charts
    from anygeometry.surfaces import CoonsSurface

    source = curved_geometry()
    before = to_dict(source)
    working = source.clone()
    assert not working.sheets and not working.members
    assert all(isinstance(face.surface, CoonsSurface) for face in working.faces.values())
    handles = tuple(working.handle('face', face) for face in working.faces)
    assert query_trimmed_surface_charts(working, handles) is not None

    def unexpected(*args, **kwargs):
        pytest.fail('admitted real charts entered the legacy owner fallback')

    monkeypatch.setattr(anymesher.preparation, 'prepare_structural_closure', unexpected)
    # Real owner chart admission and public serial preparation remain covered;
    # neither this test nor the injected adapter branch issues new qualification.
    monkeypatch.setenv('ANYGEOMETRY_EXPERIMENTAL_PARALLEL_PLANNING', '0')
    prepare_structural_connectivity(working)
    assert to_dict(source) == before


def test_cancel_before_curved_preparation_does_not_declare_owners():
    source = curved_geometry()
    revision = source.revision

    def cancel(stage):
        raise RuntimeError('cancelled')

    with pytest.raises(RuntimeError, match='cancelled'):
        prepare_structural_connectivity(source, cancellation_check=cancel)
    assert source.revision == revision
    assert not source.sheets
