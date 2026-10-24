"""Application extrusion must draw and persist the actual curved profile."""
import numpy as np
import pytest

from anygeometry import SketchDefinition, SketchEntity, SketchWire
from anyfem import Project, commands
from anyfem.io.project_file import project_to_dict, project_from_dict
from anyfem.ui.scene import face_display_polygons


def curved_definition(kind, extrusion=0., count=4):
    points = {'a': (0., 0.), 'b': (1., 1.5), 'c': (3., 1.), 'd': (4., 0.)}
    if count == 3:
        points = {'a': (0., 0.), 'b': (2., 1.5), 'c': (4., 0.)}
    return SketchDefinition(
        points=points,
        version=2, closed=False, extrusion=extrusion,
        entities=(SketchEntity('curve', kind, tuple(points)),),
        wires=(SketchWire('profile', ('curve',), False),),
    )


def assert_curved_display(geometry, face_id, vector):
    face = geometry.faces[face_id]
    edge = face.loop[0].edge
    parameters = np.linspace(0., 1., 13)
    profile = geometry.sample_edge(edge, parameters)
    # This profile visibly departs from its endpoint chord.
    assert np.max(np.linalg.norm(profile - np.linspace(profile[0], profile[-1], 13), axis=1)) > .2
    # It must curve within each half, rather than merely join two straight bays.
    assert np.linalg.norm(profile[1] - 2*profile[2] + profile[3]) > 1e-4
    polygons = face_display_polygons(geometry, face_id, divisions=12)
    assert len(polygons) == 144
    expected = np.array([
        [profile[i] + v*vector for i, v in ((u, w), (u+1, w), (u+1, w+1/12), (u, w+1/12))]
        for u in range(12) for w in parameters[:-1]
    ])
    np.testing.assert_allclose(polygons, expected, atol=2e-11)


@pytest.mark.parametrize('kind', ['spline', 'nurbs'])
@pytest.mark.parametrize('separate_command', [False, True])
@pytest.mark.parametrize('count', [3, 4])
def test_curve_extrusion_command_display_undo_and_reopen(kind, separate_command, count):
    project = Project(name='curved extrusion')
    stack = commands.CommandStack(project)
    sketch = stack.run(commands.AddPlaneSketch(
        curved_definition(kind, 0. if separate_command else 2., count=count),
        origin=(1., 2., 3.), normal=(0., 1., 0.), x_axis=(1., 0., 0.),
    ))
    if separate_command:
        record = project.geometry.features.get(sketch.feature_id)
        edges = [ref.id for key, ref in record.outputs.items() if key.startswith('entity/') and ref.kind == 'edge']
        stack.run(commands.Extrude(edges, (0., 2., 0.)))
    faces = tuple(project.geometry.faces)
    assert len(faces) == 1
    before = tuple(project.geometry.entity_keys())
    assert_curved_display(project.geometry, faces[0], np.array((0., 2., 0.)))
    stack.undo()
    assert not project.geometry.faces
    stack.redo()
    assert tuple(project.geometry.entity_keys()) == before
    reopened = project_from_dict(project_to_dict(project))
    assert_curved_display(reopened.geometry, faces[0], np.array((0., 2., 0.)))
