"""Discriminators for independent review; original child acceptance unchanged."""
from copy import deepcopy
import pytest
import os
if os.environ.get('ANYFEM_RUN_PREPARED_MESH_TESTS') != '1':
    pytest.skip('prepared-mesh candidate execution requires explicit reviewed run', allow_module_level=True)
pytest.importorskip('anymesher.prepared_current_associations',reason='requires unpublished owner CURRENT receipt candidate')
from prepared_mesh_remap_fixture import staged_fixture
from anyfem.prepared_mesh_remap import remap_prepared_mesh_to_project,PreparedMeshRemapError


@pytest.fixture(scope='module')
def base():
    return staged_fixture()[0]


@pytest.fixture
def inputs(base):
    return dict(base,mesh=deepcopy(base['mesh']))


def test_unknown_geometry_bearing_mesh_field_refuses(inputs):
    mesh=inputs['mesh']
    mesh.custom_face_loads={next(iter(mesh.elements_of_face)):1200}
    with pytest.raises(PreparedMeshRemapError,match='unsupported_mesh_field'):
        remap_prepared_mesh_to_project(**inputs)


def test_project_change_in_final_owner_callback_refuses(inputs):
    project=inputs['project'];original=dict(project.seeding_overrides)
    phases=0;changed=False
    def change(phase):
        nonlocal phases,changed
        if phase=='FEM source reference binding':phases+=1
        elif phases==2 and phase=='prepared Sheet joint component' and not changed:
            project.seeding_overrides[next(iter(project.geometry.edges))]=11
            changed=True
        return False
    try:
        with pytest.raises(PreparedMeshRemapError,match='invalid_binding'):
            remap_prepared_mesh_to_project(**inputs,cancellation_check=change)
    finally:
        project.seeding_overrides.clear();project.seeding_overrides.update(original)
    assert phases==2 and changed


def test_unknown_mesh_field_added_in_final_owner_callback_refuses(inputs):
    mesh=inputs['mesh'];phases=0;changed=False
    def change(phase):
        nonlocal phases,changed
        if phase=='FEM source reference binding':phases+=1
        elif phases==2 and phase=='prepared Sheet joint component' and not changed:
            mesh.custom_face_loads={next(iter(mesh.elements_of_face)):1200}
            changed=True
        return False
    with pytest.raises(PreparedMeshRemapError,match='unsupported_mesh_field'):
        remap_prepared_mesh_to_project(**inputs,cancellation_check=change)
    assert phases==2 and changed
