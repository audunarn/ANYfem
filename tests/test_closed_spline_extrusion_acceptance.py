"""Collapsed sketch features must display their actual curved shell."""
import numpy as np

from anygeometry import SketchDefinition, SketchEntity, SketchWire
from anyfem import Project, commands
from anyfem.io.project_file import project_to_dict, project_from_dict
from anyfem.ui.scene import build_geometry_scene


def closed_shell(project):
    definition = SketchDefinition(
        points={'a': (0., 0.), 'b': (3., 4.), 'c': (6., 0.)},
        version=2, closed=True, extrusion=.5,
        entities=(SketchEntity('curve', 'spline', ('a', 'b', 'c'), closed=True),),
        wires=(SketchWire('loop', ('curve',), True),),
    )
    return commands.CommandStack(project).run(commands.AddPlaneSketch(
        definition, origin=(0., 0., 0.), normal=(1., 0., 0.), x_axis=(0., 1., 0.),
    ))


def assert_display_follows_closed_profile(project):
    scene = build_geometry_scene(project)
    # All four exact curve pieces remain grouped for feature selection.
    assert len(scene.faces) == 1
    patch = scene.faces[0]
    face_ids = set(project.geometry.faces)
    assert {ref.id for owners in patch.polygon_owners for ref in owners} == face_ids
    boundary = []
    for polygon in patch.polygons:
        for a, b in zip(polygon, np.roll(polygon, -1, axis=0)):
            if abs(a[0]) < 1e-10 and abs(b[0]) < 1e-10:
                boundary.append((a, b))
    assert boundary
    boundary = np.asarray(boundary)
    start = boundary[:, 0]
    vector = boundary[:, 1] - start
    lengths = np.sum(vector*vector, axis=1)
    assert np.all(lengths > 0)
    samples = np.vstack([
        project.geometry.sample_edge(face.loop[0].edge, np.linspace(0., 1., 81))
        for face in project.geometry.faces.values()
    ])
    delta = samples[:, None, :] - start[None, :, :]
    t = np.clip(np.sum(delta*vector[None, :, :], axis=2)/lengths, 0., 1.)
    distances = np.linalg.norm(delta - t[:, :, None]*vector, axis=2)
    error = np.min(distances, axis=1).max()
    diameter = np.linalg.norm(np.ptp(samples, axis=0))
    assert error < .002*diameter, (error, diameter)


def test_finished_closed_spline_scene_and_saved_model_follow_curve():
    project = Project(name='closed spline shell')
    closed_shell(project)
    assert_display_follows_closed_profile(project)
    assert_display_follows_closed_profile(project_from_dict(project_to_dict(project)))
