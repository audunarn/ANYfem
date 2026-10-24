"""Incomplete application transfer must not publish restamped S3 authority."""
import pytest
from anygeometry import GeometryModel, extract_model_closure, to_dict
from anygeometry.nurbs import NurbsCurve, NurbsSurface, CurveOnSurface
from anymesher.mesh import Mesh
from anymesher.serialize import mesh_to_dict
from anyfem.structural_preparation import remap_mesh_to_source, StructuralPreparationError


@pytest.mark.parametrize('association', ['complete', 'missing', 'duplicate', 'unknown', 'planar_reassignment'])
def test_curvilinear_generic_closure_remap_refuses_before_any_mesh_mutation(association):
    source = GeometryModel()
    surface = NurbsSurface(1, 1, (0., 0., 1., 1.), (0., 0., 1., 1.),
        (((0., 0., 0.), (0., 1., 0.)), ((1., 0., 0.), (1., 1., .1))))
    uv = ((0., 0.), (1., 0.), (1., 1.), (0., 1.))
    points = tuple(source.add_point(*surface.evaluate(*point)) for point in uv)
    edges = tuple(source.add_curve(points[i], points[(i+1)%4], CurveOnSurface(surface,
        NurbsCurve(1, (0., 0., 1., 1.), (uv[i], uv[(i+1)%4])))) for i in range(4))
    face = source.add_face(edges, surface=surface)
    source.add_sheet((face,))
    refs = [source.handle('face', face)]
    if association == 'planar_reassignment':
        vertices = [source.add_point(*point) for point in
                    ((2., 0., 0.), (3., 0., 0.), (3., 1., 0.), (2., 1., 0.))]
        lines = [source.add_line(vertices[i], vertices[(i+1)%4]) for i in range(4)]
        from anygeometry import Plane
        refs.append(source.handle('face', source.add_face(lines,
            surface=Plane((2., 0., 0.), (1., 0., 0.), (0., 1., 0.)))))
    closure = extract_model_closure(source, tuple(refs),
        include_structural_closure=True, include_features=False)
    working = closure.working_model
    current = next(iter(working.faces))
    mesh = Mesh(geometry_model_id=working.model_id, geometry_revision=working.revision)
    mesh.nodes = {i+1: surface.evaluate(*point) for i, point in enumerate(uv[:3])}
    mesh.tris = {1: (1, 2, 3)}
    mesh.elements_of_face = {current: [1]}
    if association == 'missing':
        mesh.elements_of_face = {}
    elif association == 'duplicate':
        mesh.elements_of_face[current] = [1, 1]
    elif association == 'unknown':
        mesh.elements_of_face[current] = [1, 2]
    elif association == 'planar_reassignment':
        ordinary = next(identifier for identifier, definition in working.faces.items()
                        if not isinstance(definition.surface, NurbsSurface))
        mesh.elements_of_face = {ordinary: [1]}
    # A persisted record is not a geometric proof, even if labelled admitted.
    mesh.structural_preparation = {'qualified_s3': {'status': 'ADMITTED',
        'authority_model': {'source_model_id': str(working.model_id),
                            'source_revision': working.revision}}}
    before = mesh_to_dict(mesh), to_dict(source), to_dict(working)
    with pytest.raises(StructuralPreparationError, match='live owner source-cell|shell face associations'):
        remap_mesh_to_source(mesh, closure)
    assert (mesh_to_dict(mesh), to_dict(source), to_dict(working)) == before
