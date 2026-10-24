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


def test_curved_closure_requests_temporary_owner_policy_only_on_working_copy(monkeypatch):
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


def test_curved_owner_policy_preserves_owner_refusal(monkeypatch):
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


def test_cancel_before_curved_preparation_does_not_declare_owners():
    source = curved_geometry()
    revision = source.revision

    def cancel(stage):
        raise RuntimeError('cancelled')

    with pytest.raises(RuntimeError, match='cancelled'):
        prepare_structural_connectivity(source, cancellation_check=cancel)
    assert source.revision == revision
    assert not source.sheets
