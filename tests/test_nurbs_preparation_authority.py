"""NURBS preparation may consume only complete owner classification."""
import pytest
import anygeometry as owner
from anygeometry import GeometryModel, GeometryError, to_dict
from anygeometry.nurbs import CurveOnSurface, NurbsCurve, NurbsSurface
from anyfem.structural_preparation import StructuralPreparationError, prepare_structural_connectivity


def _crossing_patch():
    model = GeometryModel()
    support = NurbsSurface(1, 1, (0., 0., 1., 1.), (0., 0., 1., 1.),
        (((0., 0., 0.), (0., 1., 0.)), ((1., 0., 0.), (1., 1., .2))))
    corners = ((0., 0.), (1., 0.), (1., 1.), (0., 1.))
    curves = tuple(CurveOnSurface(support, NurbsCurve(1, (0., 0., 1., 1.),
        (corners[i], corners[(i + 1) % 4]))) for i in range(4))
    vertices = tuple(model.add_point(*curve.exact_point(0)) for curve in curves)
    edges = tuple(model.add_curve(vertices[i], vertices[(i + 1) % 4], curve)
                  for i, curve in enumerate(curves))
    patch = model.add_face(edges, surface=support)
    plate = model.add_plate(model.add_points(
        ((.5, -.2, -.5), (.5, 1.2, -.5), (.5, 1.2, .5), (.5, -.2, .5))))
    return model, patch, plate


def test_incomplete_nurbs_owner_classification_refuses_without_publication(monkeypatch):
    model, patch, plate = _crossing_patch()
    before = to_dict(model)
    calls = []
    def refuse(geometry, operands, *, policy):
        calls.append(tuple((item.kind, item.id) for item in operands))
        assert geometry is model
        raise GeometryError('unclassified NURBS material arrangement')
    def never_apply(*args, **kwargs):
        pytest.fail('a refused owner plan must not reach apply')
    monkeypatch.setattr(owner, 'plan_intersections', refuse)
    monkeypatch.setattr(owner, 'apply_intersections', never_apply)
    with pytest.raises(StructuralPreparationError, match='unclassified NURBS'):
        prepare_structural_connectivity(model)
    assert calls == [(('face', patch), ('face', plate))]
    assert to_dict(model) == before


def test_nurbs_owner_planning_cancellation_propagates_unchanged(monkeypatch):
    model, _, _ = _crossing_patch()
    before = to_dict(model)
    failure = RuntimeError('cancel NURBS owner planning')
    phases = []
    def cancel(phase):
        phases.append(phase)
        if phase == 'structural intersection batch':
            raise failure
    def plan(geometry, operands, *, policy):
        policy.cancellation_check()
        pytest.fail('owner cancellation must abort planning')
    monkeypatch.setattr(owner, 'plan_intersections', plan)
    with pytest.raises(RuntimeError) as caught:
        prepare_structural_connectivity(model, cancellation_check=cancel)
    assert caught.value is failure
    assert phases[-1] == 'structural intersection batch'
    assert to_dict(model) == before
