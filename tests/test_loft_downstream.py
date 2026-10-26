"""A loft remains editable through the existing public plate-split workflow."""
import numpy as np
from anyfem import Project, commands
from anygeometry import EntityRef
from anyfem.solve.build import build_fe_model
from test_loft_public_integration import _chain, _assigned


def test_loft_split_preserves_source_and_plate_assignment():
    project=Project();geometry=project.geometry
    first=_chain(project,((0,0,0),(2,0,0)))
    second=_chain(project,((0,1,0),(2,1,0)))
    samples={edge:geometry.sample_edge(edge,np.array([0.,.5,1.])) for edge in (*first,*second)}
    face,=commands.Loft(first,second).do(project)
    _assigned(project,[face])
    children=commands.SplitFace(face,axis=0,fraction=.5).do(project)
    assert len(children)==2
    assert {ref.id for ref in geometry.resolve_ref(EntityRef('face',face))}==set(children)
    assert all(project.face_sections[child]=='loft-shell' for child in children)
    for edge,points in samples.items():
        np.testing.assert_array_equal(geometry.sample_edge(edge,np.array([0.,.5,1.])),points)
    mesh=project.generate_mesh(.5)
    assert mesh.shells and not mesh.couplings
    built=build_fe_model(project,mesh,load_case=None,require_loads=False,require_supports=False)
    assert len(built.fe_model.mesh.elements)==len(mesh.shells)
