"""Basic loft construction, persistence and supported polygon mesh/build workflows."""
import math
import numpy as np
import pytest
from anyfem import Project, commands
from anygeometry.loft import loft_profile_options
from anygeometry.serialization import to_dict as geometry_to_dict
from anyfem.solve.build import build_fe_model
from test_loft_public_integration import _assigned


def _profile(geometry, kind, z, radius=1.):
    if kind in ('circle','arc'):
        stops=range(4) if kind=='circle' else range(1)
        vertices={}
        def point(angle):
            key=angle%4
            if key not in vertices:
                if float(key).is_integer():
                    xy=((1,0),(0,1),(-1,0),(0,-1))[int(key)]
                else:
                    xy=(math.cos(key*math.pi/2),math.sin(key*math.pi/2))
                vertices[key]=geometry.add_point(radius*xy[0],radius*xy[1],z)
            return vertices[key]
        return tuple(geometry.add_arc(point(i),point(i+.5),point(i+1)) for i in stops)
    count={'line':2,'polyline':3,'triangle':3,'square':4,'pentagon':5}[kind]
    closed=kind in ('triangle','square','pentagon')
    xy=([(0,0),(1,0),(2,.25)][:count] if not closed else
        [(radius*math.cos(2*math.pi*i/count),radius*math.sin(2*math.pi*i/count)) for i in range(count)])
    vertices=geometry.add_points([(x,y,z) for x,y in xy])
    return tuple(geometry.add_polyline(vertices,close=closed))


def _constructed(kind):
    project=Project();geometry=project.geometry
    first=_profile(geometry,kind,0.)
    second=_profile(geometry,kind,2.,radius=.8)
    original={edge:geometry.sample_edge(edge,np.linspace(0,1,7)) for edge in first+second}
    options=loft_profile_options(geometry,first,second)
    faces=commands.Loft(first,second,**options).do(project)
    assert faces
    for edge,points in original.items():
        np.testing.assert_array_equal(geometry.sample_edge(edge,np.linspace(0,1,7)),points)
    return project,faces


@pytest.mark.parametrize('kind',('line','circle','triangle','square','pentagon','polyline','arc'))
def test_basic_non_nurbs_loft_construction_and_persistence(kind):
    from anyfem.io import project_to_dict,project_from_dict
    project,faces=_constructed(kind)
    restored=project_from_dict(project_to_dict(project))
    assert set(restored.geometry.faces)==set(faces)
    assert restored.geometry.features.records[-1].kind=='geometry.loft'
    for face in faces:
        np.testing.assert_allclose(restored.geometry.face_point(face,.3,.6),project.geometry.face_point(face,.3,.6))


@pytest.mark.parametrize('kind',('line','triangle','square','pentagon','polyline'))
def test_basic_polygon_loft_public_mesh_and_build(kind):
    project,faces=_constructed(kind);geometry=project.geometry
    _assigned(project,faces)
    before=geometry_to_dict(geometry)
    mesh=project.generate_mesh(.5,geometry_approximation_error=.02)
    assert mesh.shells
    assert geometry_to_dict(geometry)==before
    built=build_fe_model(project,mesh,load_case=None,require_loads=False,require_supports=False)
    assert len(built.fe_model.mesh.elements)==len(mesh.shells)



def test_circle_seam_edit_keeps_feature_roles_and_sections():
    from anygeometry import EntityRef
    project=Project();geometry=project.geometry
    first=_profile(geometry,'circle',0.)
    second=_profile(geometry,'circle',2.,radius=.8)
    faces=commands.Loft(first,second,**loft_profile_options(geometry,first,second)).do(project)
    _assigned(project,faces)
    feature=geometry.features.records[-1]
    original=dict(feature.outputs)
    commands.EditFeature(feature.feature_id,parameters={**feature.parameters,'second_start':1}).do(project)
    updated=geometry.features.get(feature.feature_id)
    assert updated.state=='ok'
    assert original.keys()==updated.outputs.keys()
    for key,old in original.items():
        assert tuple(geometry.resolve_ref(old))==(updated.outputs[key],)
        if old.kind=='face':
            assert project.face_sections[updated.outputs[key].id]=='loft-shell'
