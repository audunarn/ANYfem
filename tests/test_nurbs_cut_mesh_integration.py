"""Required positive integration gates; supply caller-approved error explicitly.

Run only with fresh bounded acceptance authority and ANY_NURBS_ACCEPTANCE_ERROR.
These tests deliberately fail rather than skip when owner proof is unavailable.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import os
import pytest
from anygeometry import (EntityRef, GeometryModel, Plane, plan_intersections,
    apply_intersections, plan_plane_split, apply_plane_split)
from anygeometry.nurbs import NurbsSurface, NurbsCurve, CurveOnSurface
from anygeometry.serialization import to_dict as geometry_to_dict

"""Fresh acceptance fixture; construction only, no paused benchmark execution."""
import math
import numpy as np
from anygeometry import SketchDefinition, SketchEntity, SketchWire
from anyfem.application.controller import default_project
from anyfem import commands


def principal_project():
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
    feature = stack.run(commands.AddPlaneSketch(definition, (0, 0, 0), (1, 0, 0), (0, 1, 0)))
    edges = tuple(ref.id for key, ref in feature.outputs.items()
                  if key.startswith('entity/') and ref.kind == 'edge')
    stack.run(commands.Extrude(edges, (5., 0., 0.)))
    angle = math.radians(20.)
    direction = np.array([-math.sin(angle), math.cos(angle), 0.])
    vertex_ids = [stack.run(commands.AddPoint(*tuple(
        np.array([2.5, 0., 0.]) + u * direction + v * np.array([0., 0., 1.]))))
        for u, v in ((-7., -6.), (7., -6.), (7., 6.), (-7., 6.))]
    stack.run(commands.AddPlate(vertex_ids))
    return project


@pytest.fixture
def approximation_error():
    value = os.environ.get('ANY_NURBS_ACCEPTANCE_ERROR')
    if value is None:
        raise pytest.UsageError('set ANY_NURBS_ACCEPTANCE_ERROR to the caller-approved positive document-unit bound')
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise pytest.UsageError('ANY_NURBS_ACCEPTANCE_ERROR must be finite and positive') from error
    if not np.isfinite(value) or value <= 0:
        raise pytest.UsageError('ANY_NURBS_ACCEPTANCE_ERROR must be finite and positive')
    return value


def _face_nodes(mesh, face):
    return {node for element in mesh.elements_of_face[face]
            for node in mesh.shells[element]}


def _side_incidence(mesh):
    result = defaultdict(list)
    for element, nodes in mesh.shells.items():
        # This acceptance route requests linear elements only.
        assert len(nodes) in (3, 4)
        for a, b in zip(nodes, (*nodes[1:], nodes[0])):
            result[tuple(sorted((a, b)))].append(element)
    return result


def _assert_mesh_assignment_and_admission(project, mesh, before_geometry, assignments):
    assert mesh.quads or mesh.tris
    assert mesh.order == 'linear'
    assert mesh.geometry_model_id == project.geometry.model_id
    assert mesh.geometry_revision == project.geometry.revision
    assert geometry_to_dict(project.geometry) == before_geometry
    assert project.section_assignments == assignments
    assert set(mesh.elements_of_face) == set(project.geometry.faces)
    assert set(project.face_sections) == set(project.geometry.faces)
    owned = [element for elements in mesh.elements_of_face.values() for element in elements]
    assert len(owned) == len(set(owned))
    assert set(owned) == set(mesh.shells)
    for face in project.geometry.faces:
        assert mesh.elements_of_face[face]
        assert project.face_sections[face] in project.plate_sections
    # Preserve default Project S3 admission; do not switch to legacy_compatible.
    assert mesh.structural_preparation['qualified_s3']['status'] == 'ADMITTED'
    assert mesh.hybrid_diagnostics['automation']['status'] == 'ready'
    assert mesh.structural_preparation['qualified_s3']['authority_model']['scope'] == 'SOURCE_GEOMETRY_ORIENTED_SHEET_FACE_USE'


def _assert_closed_joint_graph(mesh, expected_incidence):
    sides = _side_incidence(mesh)
    assert all(len(owners) in (1, 2, expected_incidence) for owners in sides.values())
    joint = {side: owners for side, owners in sides.items() if len(owners) == expected_incidence}
    assert len(joint) >= 4
    neighbours = defaultdict(set)
    for a, b in joint:
        neighbours[a].add(b)
        neighbours[b].add(a)
    assert all(len(near) == 2 for near in neighbours.values())
    seen, pending = set(), [next(iter(neighbours))]
    while pending:
        node = pending.pop()
        if node not in seen:
            seen.add(node)
            pending.extend(neighbours[node] - seen)
    assert seen == set(neighbours)
    # One global edge/node identity has all incident cells. Duplicate coincident
    # station chains would have separate two-cell incidence and fail this gate.
    return joint


def test_principal_public_cut_and_default_project_mesh(approximation_error):
    project = principal_project()
    geometry = project.geometry
    assert len(geometry.faces) == 5
    flat = [face for face in geometry.faces if isinstance(geometry.faces[face].surface, Plane)]
    assert len(flat) == 1
    plate = flat[0]
    walls = tuple(face for face in geometry.faces if face != plate)
    project.add_plate_section('cut plate', thickness=.02, material='S355')
    project.assign_plates(walls, 'plate')
    project.assign_plate(plate, 'cut plate')
    project.resolve_section_assignments(strict=True)
    before = geometry_to_dict(geometry)
    assignments = deepcopy(project.section_assignments)
    sections = dict(project.face_sections)
    preview = geometry.clone(include_features=False)
    application = apply_intersections(preview,
        plan_intersections(preview, tuple(preview.handle('face', face) for face in preview.faces),
                           policy='connect'), policy='connect')
    assert geometry_to_dict(geometry) == before
    assert len(preview.faces) == 10
    traces = tuple(handle.id for handle in application.joint_edges)
    assert len(traces) == 4 and len(set(traces)) == 4
    assert all(len(preview.faces_using_edge(edge)) == 4 for edge in traces)
    descendants = {parent: tuple(ref.id for ref in preview.resolve_ref(EntityRef('face', parent)))
                   for parent in geometry.faces}
    assert all(len(children) == 2 for children in descendants.values())
    assert {child for children in descendants.values() for child in children} == set(preview.faces)
    for parent, children in descendants.items():
        for child in children:
            for key, value in geometry.faces[parent].metadata.items():
                assert preview.faces[child].metadata[key] == value
    for sheet, source_record in geometry.sheets.items():
        parents = {geometry.face_uses[use].face_id for use in source_record.face_use_ids}
        expected = {child for parent in parents for child in descendants[parent]}
        actual = {preview.face_uses[use].face_id for use in preview.sheets[sheet].face_use_ids}
        assert actual == expected
    assert not preview.validate_topology()
    mesh = project.generate_mesh(.25, strategy='quad_first', order='linear',
        certification_mode='interactive', geometry_approximation_error=approximation_error)
    _assert_mesh_assignment_and_admission(project, mesh, before, assignments)
    assert project.face_sections == sections
    assert mesh.hybrid_diagnostics['curvilinear_recovery']['geometry_approximation_error'] == approximation_error
    assert mesh.hybrid_diagnostics['curvilinear_recovery']['recombine'] is True
    provenance = mesh.structural_preparation['prepared_boundary_provenance']
    assert len(provenance['traces']) == 4
    assert all(len(trace['source_face_ids']) == 2 for trace in provenance['traces'].values())
    assert set(mesh.nodes_of_edge) <= set(geometry.edges)
    assert all(mesh.thickness_of_face[face] == (.02 if face == plate else .01)
               for face in geometry.faces)
    joint = _assert_closed_joint_graph(mesh, 4)
    wall_nodes = {face: _face_nodes(mesh, face) for face in walls}
    plate_nodes = _face_nodes(mesh, plate)
    for side, incident in joint.items():
        assert set(side) <= plate_nodes
        assert any(set(side) <= nodes for nodes in wall_nodes.values())
        owners = Counter(face for face, elements in mesh.elements_of_face.items()
                         for element in incident if element in elements)
        assert owners[plate] == 2
        assert sorted(count for face, count in owners.items() if face != plate) == [2]


def dome_project():
    project = default_project()
    model = project.geometry
    heights = (1., -1., 1.)
    surface = NurbsSurface(2,2,(0.,0.,0.,1.,1.,1.),(0.,0.,0.,1.,1.,1.),
        tuple(tuple((i-1.,j-1.,heights[i]+heights[j]) for j in range(3)) for i in range(3)))
    uv = ((0.,0.),(1.,0.),(1.,1.),(0.,1.))
    points = tuple(model.add_point(*surface.evaluate(*row)) for row in uv)
    edges = tuple(model.add_curve(points[i],points[(i+1)%4],CurveOnSurface(surface,
        NurbsCurve(1,(0.,0.,1.,1.),(uv[i],uv[(i+1)%4])))) for i in range(4))
    face = model.add_face(edges,surface=surface)
    project.assign_plate(face,'plate')
    plane = Plane((0.,0.,.5),(1.,0.,0.),(0.,1.,0.))
    return project, face, plane


def test_dome_closed_loop_split_default_project_mesh(approximation_error):
    project, parent, plane = dome_project()
    model = project.geometry
    old_support = model.faces[parent].surface
    result = apply_plane_split(model, plan_plane_split(model,(parent,),plane), policy='imprint')
    assert len(model.faces) == 2
    assert set(result.positive_faces + result.negative_faces) == set(model.faces)
    assert all(face.surface == old_support for face in model.faces.values())
    assert len(result.trace_edges) >= 2
    assert all(len(model.faces_using_edge(edge)) == 2 for edge in result.trace_edges)
    assert sorted(len(face.holes) for face in model.faces.values()) == [0,1]
    project.resolve_section_assignments(strict=True)
    before = geometry_to_dict(model)
    assignments = deepcopy(project.section_assignments)
    assert set(project.face_sections) == set(model.faces)
    mesh = project.generate_mesh(.25, strategy='quad_first', order='linear',
        certification_mode='interactive', geometry_approximation_error=approximation_error)
    _assert_mesh_assignment_and_admission(project, mesh, before, assignments)
    sides = _side_incidence(mesh)
    for edge in result.trace_edges:
        chain = mesh.nodes_of_edge[edge]
        assert len(chain) >= 2
        for face in model.faces_using_edge(edge):
            assert set(chain) <= _face_nodes(mesh, face)
        for a, b in zip(chain[:-1],chain[1:]):
            assert len(sides[tuple(sorted((a,b)))]) == 2
    assert all(len(owners) in (1,2) for owners in sides.values())
