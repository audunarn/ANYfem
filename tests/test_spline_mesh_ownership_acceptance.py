"""An unassigned curved extrusion must mesh without modifying design owners."""
from collections import Counter
import time
import numpy as np

from anygeometry import SketchDefinition, SketchEntity, SketchWire, EntityRef
from anygeometry.serialization import to_dict as geometry_to_dict
from anyfem import commands
from anyfem.application.controller import default_project
from anymesher.serialize import mesh_to_dict, mesh_from_dict


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


def assert_boundary_and_shared_seams(project, mesh):
    geometry = project.geometry
    incidence = Counter(item.edge for face in geometry.faces.values() for item in face.loop)
    assert len([edge for edge, count in incidence.items() if count == 2]) == 4
    for edge, count in incidence.items():
        nodes = mesh.nodes_of_edge[edge]
        assert len(nodes) >= 2
        np.testing.assert_allclose([mesh.nodes[nodes[0]], mesh.nodes[nodes[-1]]],
            geometry.sample_edge(edge, [0.,1.]), atol=1e-9, rtol=0.)
        for node in nodes:
            _, _, distance = geometry.closest_edge_point(edge, mesh.nodes[node])
            assert distance < 1e-8
        if count == 2:
            adjacent = [face.id for face in geometry.faces.values() if edge in {e.edge for e in face.loop}]
            for face in adjacent:
                assert set(nodes) <= set(mesh.nodes_on(EntityRef('face', face)))
    for face in geometry.faces:
        assert mesh.elements_of_face[face]
    for node_ids in (*mesh.quads.values(), *mesh.tris.values()):
        coordinates = np.array([mesh.nodes[n] for n in node_ids[:4]])
        area_vector = np.cross(coordinates[1]-coordinates[0], coordinates[-1]-coordinates[0])
        assert np.linalg.norm(area_vector) > 1e-10


def test_unassigned_reported_spline_mesh_has_exact_boundary_and_shared_nodes():
    project = reported_unassigned_extrusion()
    before = geometry_to_dict(project.geometry)
    started = time.perf_counter()
    mesh = project.generate_mesh(.25, strategy='quad_first', certification_mode='interactive')
    print(f'Generation: {time.perf_counter()-started:.6f}s; '
          f'{len(mesh.nodes)} nodes, {len(mesh.quads)} quads, {len(mesh.tris)} triangles')
    assert mesh.quads or mesh.tris
    assert_boundary_and_shared_seams(project, mesh)
    serialized = mesh_to_dict(mesh)
    assert mesh_to_dict(mesh_from_dict(serialized)) == serialized
    assert geometry_to_dict(project.geometry) == before
    assert not project.geometry.sheets and not project.face_sections
