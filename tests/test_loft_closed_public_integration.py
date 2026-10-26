"""Closed loft seam stays conformal through the public mesh/build route."""
from anyfem import Project, commands
from anygeometry.serialization import to_dict as geometry_to_dict
from anyfem.solve.build import build_fe_model
from test_loft_public_integration import _assigned


def test_closed_loft_public_mesh_shares_seam_nodes():
    project=Project();geometry=project.geometry;profiles=[]
    for z in (0.,1.):
        vertices=geometry.add_points(((0,0,z),(1,0,z),(1,1,z),(0,1,z)))
        profiles.append(tuple(geometry.add_polyline(vertices,close=True)))
    faces=commands.Loft(*profiles).do(project)
    assert len(faces)==4
    _assigned(project,faces)
    before=geometry_to_dict(geometry)
    mesh=project.generate_mesh(.5)
    assert geometry_to_dict(geometry)==before
    assert mesh.shells and not mesh.couplings
    for first,second in zip(faces,faces[1:]+faces[:1]):
        shared={u.edge for u in geometry.faces[first].loop}&{u.edge for u in geometry.faces[second].loop}
        assert len(shared)==1
        edge=shared.pop();seam_nodes=set(mesh.nodes_of_edge[edge])
        assert len(seam_nodes)>=3
        for face in (first,second):
            nodes={node for element in mesh.elements_of_face[face] for node in mesh.shells[element]}
            assert seam_nodes<=nodes
    built=build_fe_model(project,mesh,load_case=None,require_loads=False,require_supports=False)
    assert len(built.fe_model.mesh.elements)==len(mesh.shells)
