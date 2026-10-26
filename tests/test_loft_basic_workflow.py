"""Ordinary authored sketches remain ordinary inputs to the public loft command."""
from math import cos, sin, pi

import numpy as np
import pytest
from anygeometry import SketchDefinition, SketchEntity, SketchWire
from anygeometry.curves import Arc
from anygeometry.loft import loft_profile_options, loft_profiles_from_sketch
from anyfem import Project, commands
from anyfem.io import project_from_dict, project_to_dict


BASIC_PROFILES = ("line", "polyline", "triangle", "square", "pentagon", "circle")


def oriented_output_loops(geometry, feature):
    edge_roles = {ref.id: key for key, ref in feature.outputs.items() if ref.kind == "edge"}
    return {key: tuple((edge_roles[use.edge], use.forward) for use in geometry.faces[ref.id].loop)
            for key, ref in feature.outputs.items() if ref.kind == "face"}


def assert_reversed_output_loops(before, after):
    assert before.keys() == after.keys()
    for key, uses in before.items():
        expected = tuple((role, not forward) for role, forward in reversed(uses))
        assert any(after[key] == expected[index:] + expected[:index] for index in range(len(expected)))


def basic_definition(kind, scale=1.0):
    if kind == "circle":
        return SketchDefinition(
            points={"centre": (0., 0.), "rim": (scale, 0.)}, version=2,
            entities=(SketchEntity("circle", "circle", ("centre", "rim")),),
            wires=(SketchWire("profile", ("circle",), True),),
        )
    closed = kind not in {"line", "polyline"}
    if kind == "line":
        coordinates = ((-1., 0.), (1., 0.))
    elif kind == "polyline":
        coordinates = ((-1., 0.), (0., -.25), (1., .2))
    elif kind == "triangle":
        coordinates = ((-1., -.5), (1., -.5), (0., 1.))
    elif kind == "square":
        coordinates = ((-1., -1.), (1., -1.), (1., 1.), (-1., 1.))
    else:
        count = {"pentagon": 5}[kind]
        coordinates = tuple((cos(2*pi*i/count), sin(2*pi*i/count)) for i in range(count))
    points = {f"p{i}": (scale*x, scale*y) for i, (x, y) in enumerate(coordinates)}
    identifiers = tuple(points)
    pairs = list(zip(identifiers, identifiers[1:]))
    if closed:
        pairs.append((identifiers[-1], identifiers[0]))
    entities = tuple(SketchEntity(f"line{i}", "line", pair) for i, pair in enumerate(pairs))
    return SketchDefinition(points=points, version=2, entities=entities,
                            wires=(SketchWire("profile", tuple(e.id for e in entities), closed),))


def add_basic_sketches(execute, kind):
    angle = pi/9
    first = execute(commands.AddPlaneSketch(basic_definition(kind), (0, 0, 0), (0, 0, 1), (1, 0, 0), name="First profile"))
    second = execute(commands.AddPlaneSketch(basic_definition(kind, .8), (.2, .1, 3),
                     (sin(angle), 0, cos(angle)), (cos(angle), 0, -sin(angle)), name="Second profile"))
    return first.feature_id, second.feature_id


@pytest.mark.parametrize("kind", BASIC_PROFILES)
def test_basic_sketch_profiles_on_rotated_planes_persist_and_flip(kind):
    project = Project()
    first, second = add_basic_sketches(lambda command: command.do(project), kind)
    geometry = project.geometry
    first_edges = loft_profiles_from_sketch(geometry, first)[0][1]
    second_edges = loft_profiles_from_sketch(geometry, second)[0][1]
    if kind == "circle":
        assert len(first_edges) == len(second_edges) == 4
        assert all(isinstance(geometry.edges[edge].curve, Arc) for edge in first_edges + second_edges)
    source_samples = {edge: geometry.sample_edge(edge, np.linspace(0, 1, 7)) for edge in first_edges + second_edges}
    options = loft_profile_options(geometry, first_edges, second_edges)
    faces = commands.Loft(first_edges, second_edges, **options).do(project)
    assert faces
    feature = geometry.features.records[-1]
    normals = {key: geometry.face_normal_many(ref.id, [(.5, .5)])[0]
               for key, ref in feature.outputs.items() if ref.kind == "face"}
    original_loops = oriented_output_loops(geometry, feature)
    for edge, points in source_samples.items():
        np.testing.assert_array_equal(geometry.sample_edge(edge, np.linspace(0, 1, 7)), points)
    restored = project_from_dict(project_to_dict(project))
    commands.EditFeature(feature.feature_id, parameters={**feature.parameters, "flip_normal": True}).do(restored)
    flipped = restored.geometry.features.get(feature.feature_id)
    assert_reversed_output_loops(original_loops, oriented_output_loops(restored.geometry, flipped))
    for key, normal in normals.items():
        np.testing.assert_allclose(restored.geometry.face_normal_many(flipped.outputs[key].id, [(.5, .5)])[0], -normal, rtol=1e-12, atol=1e-12)
        assert np.dot(restored.geometry.face_normal(flipped.outputs[key].id, .5, .5), normal) < 0
    assert restored.geometry.features.get(first).state == "ok"
    assert restored.geometry.features.get(second).state == "ok"
