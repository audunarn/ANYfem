"""Workflow delegation only; geometric proof belongs to the real owner service."""
from types import SimpleNamespace

import pytest
import anymesher
from anyfem.structural_preparation import (
    StructuralPreparationError, remap_mesh_to_source,
)


def operands(monkeypatch):
    mesh = SimpleNamespace(tris={7: object()}, quads={}, elements_of_face={3: [7]})
    closure = SimpleNamespace(working_model=SimpleNamespace(faces={3: object()}))
    monkeypatch.setattr(anymesher, 'requires_material_source_transfer', lambda *_: True)
    return mesh, closure


def test_curved_publication_delegates_live_operands_and_returns_before_generic_remap(monkeypatch):
    mesh, closure = operands(monkeypatch)
    source, token = object(), object()
    callback = lambda phase: False
    calls = []
    def publish(actual_source, actual_closure, actual_mesh, **kwargs):
        calls.append((actual_source, actual_closure, actual_mesh, kwargs))
    monkeypatch.setattr(anymesher, 'publish_model_closure_mesh_to_source', publish)
    remap_mesh_to_source(mesh, closure, source_geometry=source,
                        qualification=token, cancellation_check=callback)
    assert calls == [(source, closure, mesh,
                      {'qualification': token, 'cancellation_check': callback})]


def test_curved_publication_requires_live_source(monkeypatch):
    mesh, closure = operands(monkeypatch)
    def unexpected(*args, **kwargs):
        raise AssertionError('owner called without live source')
    monkeypatch.setattr(anymesher, 'publish_model_closure_mesh_to_source', unexpected)
    with pytest.raises(StructuralPreparationError, match='live source geometry'):
        remap_mesh_to_source(mesh, closure)


def test_curved_publication_requires_owner_service(monkeypatch):
    mesh, closure = operands(monkeypatch)
    monkeypatch.delattr(anymesher, 'publish_model_closure_mesh_to_source')
    with pytest.raises(StructuralPreparationError, match='source-publication service'):
        remap_mesh_to_source(mesh, closure, source_geometry=object())


def test_curved_publication_preserves_owner_refusal(monkeypatch):
    mesh, closure = operands(monkeypatch)
    from anymesher import MeshError
    failure = MeshError('unproved source parameter correspondence')
    def refuse(*args, **kwargs):
        raise failure
    monkeypatch.setattr(anymesher, 'publish_model_closure_mesh_to_source', refuse)
    with pytest.raises(StructuralPreparationError, match=str(failure)) as caught:
        remap_mesh_to_source(mesh, closure, source_geometry=object())
    assert caught.value.__cause__ is failure


def test_curved_publication_preserves_callback_exception_identity(monkeypatch):
    mesh, closure = operands(monkeypatch)
    failure = RuntimeError('cancel callback failed')
    def callback(phase):
        raise failure
    def publish(source, closure, mesh, **kwargs):
        kwargs['cancellation_check']('source publication')
    monkeypatch.setattr(anymesher, 'publish_model_closure_mesh_to_source', publish)
    with pytest.raises(RuntimeError) as caught:
        remap_mesh_to_source(mesh, closure, source_geometry=object(), cancellation_check=callback)
    assert caught.value is failure
