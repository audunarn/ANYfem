"""Shared non-test constructor for the retained reported spline source."""
from anygeometry import SketchDefinition, SketchEntity, SketchWire
from anyfem import commands
from anyfem.application.controller import default_project


def reported_unassigned_extrusion():
    project = default_project()
    points = {
        'p7': (1.840810486134475, -2.865807234095718),
        'p8': (6.045388982873449, .06275490293640225),
        'p9': (3.556111166396149, 3.807130778141757),
        'p10': (-3.953558884993364, 4.12090529282377),
        'p11': (-5.355085050573022, -1.2969346606856549),
        'p12': (-.8994869420884367, -3.702539273247754),
    }
    definition = SketchDefinition(points, version=2, closed=True,
        entities=(SketchEntity('profile', 'spline', tuple(points), closed=True),),
        wires=(SketchWire('outline', ('profile',), True),))
    stack = commands.CommandStack(project)
    feature = stack.run(commands.AddPlaneSketch(definition, (0,0,0), (1,0,0), (0,1,0)))
    edges = tuple(ref.id for key, ref in feature.outputs.items()
                  if key.startswith('entity/') and ref.kind == 'edge')
    stack.run(commands.Extrude(edges, (5.,0.,0.)))
    assert len(project.geometry.faces) == 4
    assert not project.geometry.sheets and not project.geometry.members
    assert not project.face_sections
    return project
