import pytest
from anyfem import Project,steel
from anyfem.model import WetSurface,WetPressure
from anyfem.document import canonical_hash
from anyfem.io.project_file import project_from_dict,project_to_dict
from anyfem.solve.build import wet_element_pressures,build_fe_model
from anymesher.serialize import mesh_to_dict


def test_bound_element_pressure_roundtrip_and_mutation_refusal(monkeypatch):
    project=Project()
    project.add_material(steel())
    project.add_plate_section('plate',.01,'S355')
    g=project.geometry
    ids=g.add_points([(0,0,0),(1,0,0),(1,1,0),(0,1,0)])
    face=g.add_face(g.add_polyline(ids,close=True))
    project.assign_plate(face,'plate')
    project.add_wet_surface(WetSurface(ref=project.face(face),fluid_side='positive_normal'))
    mesh=project.generate_mesh(.5)
    load=WetPressure(mapping={int(e):-100. for e in mesh.elements_of_face[face]},mapping_key='element',
                     mesh_content_sha256=canonical_hash(mesh_to_dict(mesh)))
    project.load_case().wet_pressures.append(load)
    data=project_to_dict(project)
    assert data['anyfem']['format']==14
    import anyfem.io.project_file as codec
    with monkeypatch.context() as old_reader:
        old_reader.setattr(codec,'MAX_READ_FORMAT',13)
        with pytest.raises(ValueError,match='upgrade'):
            project_from_dict(data)
    reopened=project_from_dict(data)
    assert reopened.load_case().wet_pressures[0].mesh_content_sha256 == load.mesh_content_sha256
    assert wet_element_pressures(reopened,mesh,reopened.load_case())
    changed=project.generate_mesh(.25)
    with pytest.raises(ValueError,match='mesh identity'):
        wet_element_pressures(reopened,changed,reopened.load_case())
    with pytest.raises(ValueError,match='mesh identity'):
        build_fe_model(reopened,changed,require_supports=False)


def test_mesh_binding_only_accepts_element_maps():
    assert project_to_dict(Project())['anyfem']['format']<14
    with pytest.raises(ValueError,match='mesh_content'):
        WetPressure(value=1.,mesh_content_sha256='0'*64)
