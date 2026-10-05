"""A detached copy hook must not run after the final Project freshness check."""
import numpy as np
import os
import pytest
if os.environ.get('ANYFEM_RUN_PREPARED_MESH_TESTS') != '1':
    pytest.skip('prepared-mesh candidate execution requires explicit reviewed run', allow_module_level=True)
pytest.importorskip('anymesher.prepared_current_associations',reason='requires unpublished owner CURRENT receipt candidate')
from prepared_mesh_remap_fixture import staged_fixture
from anyfem.prepared_mesh_remap import remap_prepared_mesh_to_project,PreparedMeshRemapError


def test_second_coordinate_copy_cannot_change_project_after_final_validation():
    inputs,_=staged_fixture();project=inputs['project'];copies=0
    class Coordinates(np.ndarray):
        def __deepcopy__(self,memo):
            nonlocal copies
            copies+=1
            if copies==2:
                project.seeding_overrides[next(iter(project.geometry.edges))]=19
            return np.asarray(self).copy()
    mesh=inputs['mesh'];node=next(iter(mesh.nodes))
    mesh.nodes[node]=mesh.nodes[node].view(Coordinates)
    with pytest.raises(PreparedMeshRemapError,match='invalid_binding'):
        remap_prepared_mesh_to_project(**inputs)
    assert copies==2


def test_final_callback_cannot_change_retained_detached_coordinates():
    inputs,_=staged_fixture();copies=[];phases=0;changed=False
    class Coordinates(np.ndarray):
        def __deepcopy__(self,memo):
            copy=np.asarray(self).copy()
            copies.append(copy)
            return copy
    mesh=inputs['mesh'];node=next(iter(mesh.nodes))
    mesh.nodes[node]=mesh.nodes[node].view(Coordinates)
    def change(phase):
        nonlocal phases,changed
        if phase=='FEM source reference binding':
            phases+=1
            if phases==2:
                copies[0][0]+=1
                changed=True
        return False
    with pytest.raises(PreparedMeshRemapError,match='output_changed'):
        remap_prepared_mesh_to_project(**inputs,cancellation_check=change)
    assert changed and phases==2 and len(copies)==2
