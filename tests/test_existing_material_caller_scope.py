"""The caller selects Face scope; only the public owner can issue preparation."""
import pytest

import anygeometry as owner
from anygeometry.nurbs import NurbsSurface, NurbsCurve, CurveOnSurface
from anyfem.structural_preparation import (
    StructuralPreparationError, prepare_structural_connectivity,
)


def rectangle(model, surface, *, curved=False):
    uv = ((0., 0.), (1., 0.), (1., 1.), (0., 1.))
    vertices = model.add_points(tuple(surface.evaluate(*p) for p in uv))
    edges = []
    for i in range(4):
        pcurve = (NurbsCurve(2, (0., 0., 0., 1., 1., 1.),
                   (uv[0], (.5, .125), uv[1])) if curved and i == 0 else
                  NurbsCurve(1, (0., 0., 1., 1.), (uv[i], uv[(i+1) % 4])))
        edges.append(model.add_curve(vertices[i], vertices[(i+1) % 4],
                                     CurveOnSurface(surface, pcurve)))
    return model.add_face(edges, surface=surface)


@pytest.fixture(scope='module')
def closed_partition():
    model = owner.GeometryModel()
    heights = (1., -1., 1.)
    support = NurbsSurface(2, 2, (0., 0., 0., 1., 1., 1.), (0., 0., 0., 1., 1., 1.),
        tuple(tuple((i-1., j-1., heights[i]+heights[j]) for j in range(3)) for i in range(3)))
    parent = rectangle(model, support)
    model.add_sheet((parent,))
    plane = owner.Plane((0., 0., .5), (1., 0., 0.), (0., 1., 0.))
    owner.apply_plane_split(model, owner.plan_plane_split(model, (parent,), plane), policy='imprint')
    assert len(model.faces) == 2
    assert sorted(len(face.holes) for face in model.faces.values()) == [0, 1]
    return model


@pytest.mark.parametrize('copy', ('clone', 'load'))
def test_closed_same_sheet_partition_uses_real_complete_owner_plan(closed_partition, monkeypatch, copy):
    model = (closed_partition.clone() if copy == 'clone' else
             owner.from_dict(owner.to_dict(closed_partition)))
    before = owner.to_dict(model)
    incidences = {edge: tuple(model.faces_using_edge(edge)) for edge in model.edges}
    old_faces = tuple(model.faces)
    calls = []
    real_plan, real_apply = owner.plan_intersections, owner.apply_intersections
    def plan(geometry, operands, *, policy):
        operands = tuple(operands)
        assert {h.kind for h in operands} == {'face'}
        assert {h.id for h in operands} == set(old_faces)
        assert policy.intent is owner.ConnectionIntent.CONNECT and policy.face_connections
        result = real_plan(geometry, operands, policy=policy)
        calls.append(('plan', result, policy))
        return result
    def apply(geometry, issued, *, policy):
        assert calls == [('plan', issued, policy)]
        result = real_apply(geometry, issued, policy=policy)
        calls.append(('apply', issued, policy))
        return result
    monkeypatch.setattr(owner, 'plan_intersections', plan)
    monkeypatch.setattr(owner, 'apply_intersections', apply)
    report = prepare_structural_connectivity(model)
    assert [row[0] for row in calls] == ['plan', 'apply']
    assert report.created_count == 0
    assert owner.has_current_intersection_preparation(model, face_ids=old_faces)
    assert owner.to_dict(model) == before
    assert {edge: tuple(model.faces_using_edge(edge)) for edge in model.edges} == incidences
    for face in old_faces:
        assert tuple(model.resolve_ref(owner.EntityRef('face', face))) == (owner.EntityRef('face', face),)


@pytest.mark.parametrize('curved', (False, True), ids=('overlap', 'unsupported_trim'))
def test_family_hint_never_accepts_unproved_material_pair(curved):
    model = owner.GeometryModel()
    support = NurbsSurface(1, 1, (0., 0., 1., 1.), (0., 0., 1., 1.),
        (((0., 0., 0.), (0., 1., 0.)), ((1., 0., 0.), (1., 1., 0.))))
    faces = (rectangle(model, support, curved=curved), rectangle(model, support))
    for face in faces:
        model.add_sheet((face,))
    before = owner.to_dict(model)
    with pytest.raises(StructuralPreparationError, match='existing material pair overlaps or is outside'):
        prepare_structural_connectivity(model)
    assert owner.to_dict(model) == before
    assert not owner.has_current_intersection_preparation(model)


def test_owner_callback_exception_is_not_replaced(closed_partition):
    model = closed_partition.clone()
    before = owner.to_dict(model)
    sentinel = KeyboardInterrupt('owner caller cancellation')
    def cancel(phase):
        if phase == 'structural intersection batch':
            raise sentinel
    with pytest.raises(KeyboardInterrupt) as caught:
        prepare_structural_connectivity(model, cancellation_check=cancel)
    assert caught.value is sentinel
    assert owner.to_dict(model) == before
    assert not owner.has_current_intersection_preparation(model)


def test_owner_detects_normal_callback_mutation(closed_partition):
    model = closed_partition.clone()
    faces = tuple(model.faces)
    sheets = tuple(model.sheets)
    def mutate(phase):
        if phase == 'structural intersection batch':
            model.add_point(9., 9., 9.)
    with pytest.raises(StructuralPreparationError, match='source or policy changed'):
        prepare_structural_connectivity(model, cancellation_check=mutate)
    assert tuple(model.faces) == faces and tuple(model.sheets) == sheets
    assert not owner.has_current_intersection_preparation(model)
