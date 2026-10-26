"""Public mesh boundary checks for the isolated loft candidate."""
import numpy as np
from anyfem import Project, steel, commands
from anygeometry.serialization import to_dict as geometry_to_dict
from anyfem.solve.build import build_fe_model


def _chain(project, points):
    vertices=project.geometry.add_points(points)
    return tuple(project.geometry.add_line(a,b) for a,b in zip(vertices,vertices[1:]))


def _assigned(project, faces):
    project.add_material(steel())
    project.add_plate_section('loft-shell',.01,'S355')
    project.assign_plates(faces,'loft-shell')


def test_planar_loft_public_mesh_and_solver_build_preserve_design():
    project=Project()
    first=_chain(project,((0,0,0),(1,0,0),(2,0,0)))
    second=_chain(project,((0,1,0),(2,1,0)))
    faces=commands.Loft(first,second).do(project)
    _assigned(project,faces)
    before=geometry_to_dict(project.geometry)
    mesh=project.generate_mesh(.5)
    assert mesh.shells
    assert geometry_to_dict(project.geometry)==before
    built=build_fe_model(project,mesh,load_case=None,require_loads=False,require_supports=False)
    assert len(built.fe_model.mesh.elements)==len(mesh.shells)
    assert not mesh.couplings
    points=np.asarray(list(mesh.nodes.values()))
    np.testing.assert_allclose(points[:,2],0,atol=1e-12)
    assert np.min(points[:,0]) >= -1e-12 and np.max(points[:,0]) <= 2+1e-12
    assert np.min(points[:,1]) >= -1e-12 and np.max(points[:,1]) <= 1+1e-12
