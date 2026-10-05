"""Ordinary detached reference-binding checks; no meshing/solver admission."""
from dataclasses import replace
import os
import numpy as np
import pytest
if os.environ.get('ANYFEM_RUN_PREPARED_MESH_TESTS') != '1':
    pytest.skip('prepared-mesh candidate execution requires explicit reviewed run', allow_module_level=True)
pytest.importorskip('anymesher.prepared_current_associations',reason='requires unpublished owner CURRENT receipt candidate')
from prepared_mesh_remap_fixture import staged_fixture,size2_fixture,reopened_child_fixture
from anyfem.prepared_mesh_remap import remap_prepared_mesh_to_project,PreparedMeshRemapError,_snapshot
from copy import deepcopy


@pytest.fixture(scope='module')
def base():
    return staged_fixture()


@pytest.fixture
def candidate(base):
    inputs,pressure=base
    return dict(inputs,mesh=deepcopy(inputs['mesh'])),pressure


def test_complete_source_component_is_detached_and_keeps_current_provenance(candidate):
    inputs,pressure=candidate;mesh=inputs['mesh'];before=_snapshot(mesh)
    result=remap_prepared_mesh_to_project(**inputs)
    assert len(result.mesh.tris)==16 and len(result.mesh.elements_of_sheet)==2
    assert result.mesh.geometry_model_id==inputs['project'].geometry.model_id
    assert result.mesh.tris==mesh.tris and _snapshot(mesh)==before
    assert result.current_receipt==inputs['association_receipt']
    assert not result.current_receipt.source_reference_transfer_qualified
    assert not result.solver_admitted and not result.publication_qualified
    for key in mesh.nodes:
        np.testing.assert_array_equal(result.mesh.nodes[key],mesh.nodes[key])
        assert not np.shares_memory(result.mesh.nodes[key],mesh.nodes[key])
    bound=next(item for item in result.references if item.key.endswith(pressure.id))
    expected={cell for child in inputs['association_component'].boundary_correspondences[0].descendants
              for cell in mesh.elements_of_face[child]}
    assert set(bound.cell_ids)==expected and len(expected)<len(mesh.tris)
    assert len(result.occurrences)==2
    assert len([item for item in result.associations if item.field=='elements_of_face_use'])==8
    assert any(not item.source_ids for item in result.associations if item.field=='nodes_of_edge')
    assert result.source_witness is inputs['closure'] and result.source_manifest is inputs['manifest']
    for kind,source,current in inputs['manifest'].source_to_prepared_ids:
        if kind=='vertex' and current in mesh.node_of_vertex:
            assert result.mesh.node_of_vertex[source]==mesh.node_of_vertex[current]
    assert all(sign==item.source_orientation for item in result.occurrences for _,sign in item.current_orientations)


@pytest.mark.parametrize('kind',['namespace','receipt','mesh','manifest','closure','unsupported'])
def test_forged_stale_unsupported_inputs_refuse(kind,candidate):
    inputs,_=candidate
    if kind=='namespace':inputs['association_namespace']='source'
    elif kind=='receipt':inputs['association_receipt']=replace(inputs['association_receipt'],elements_of_sheet=())
    elif kind=='mesh':
        inputs['mesh'].nodes[1]=inputs['mesh'].nodes[1].copy()
        inputs['mesh'].nodes[1][0]+=1
    elif kind=='manifest':inputs['manifest']=replace(inputs['manifest'],references=())
    elif kind=='closure':inputs['closure']=inputs['closure'].closure
    else:inputs['mesh'].thickness_of_face[3]=.01
    with pytest.raises(PreparedMeshRemapError):remap_prepared_mesh_to_project(**inputs)


def test_cancel_before_binding_returns_no_artifact_and_preserves_mesh(candidate):
    inputs,_=candidate;before=_snapshot(inputs['mesh'])
    with pytest.raises(PreparedMeshRemapError,match='cancelled'):
        remap_prepared_mesh_to_project(**inputs,cancellation_check=lambda phase:True)
    assert _snapshot(inputs['mesh'])==before


def test_corrected_size2_fixture_has_live_witness_and_keeps_all_26_cells():
    inputs,pressure,fixture=size2_fixture()
    assert fixture.geometry is inputs['closure'].working_model
    assert inputs['project'].geometry is not fixture.geometry
    assert fixture.s3_admission.admitted
    result=remap_prepared_mesh_to_project(**inputs)
    assert len(result.mesh.tris)==26 and not result.solver_admitted
    bound=next(item for item in result.references if item.key.endswith(pressure.id))
    source_face=pressure.ref.id
    assert set(bound.cell_ids)==set(result.mesh.elements_of_face[source_face])
    assert len(bound.cell_ids)<26
    assert len([item for item in result.associations if item.field=='joint_chains'])==len(inputs['association_receipt'].joint_chains)
    lookup={(kind,initial):source for kind,source,initial in inputs['manifest'].source_to_prepared_ids}
    ordered_not_sorted=False
    for root,initial,forward,current_edges,chain in inputs['association_receipt'].source_boundary_chains:
        expected=tuple(chain) if forward else tuple(reversed(chain))
        actual=result.mesh.nodes_of_edge[lookup['edge',initial]]
        assert tuple(actual)==expected
        ordered_not_sorted|=actual!=sorted(actual)
    assert ordered_not_sorted  # A sorted node-ID implementation would fail.


def test_interior_v2_receipt_is_source_only_and_keeps_exact_created_uv():
    inputs,_,fixture=size2_fixture('interior')
    assert inputs['association_receipt'].schema.endswith('-v2')
    assert inputs['association_receipt'].created_material_uv_by_root
    result=remap_prepared_mesh_to_project(**inputs)
    assert result.current_receipt.created_material_uv_by_root==inputs['association_receipt'].created_material_uv_by_root
    assert result.mesh.tris==inputs['mesh'].tris and not result.solver_admitted


def test_project_change_during_binding_refuses_at_final_validation(candidate):
    inputs,_=candidate;project=inputs['project'];calls=0
    original=dict(project.seeding_overrides)
    def change(phase):
        nonlocal calls
        if phase=='FEM source reference binding':
            calls+=1
            if calls==2:project.seeding_overrides[next(iter(project.geometry.edges))]=9
        return False
    try:
        with pytest.raises(PreparedMeshRemapError,match='invalid_binding'):
            remap_prepared_mesh_to_project(**inputs,cancellation_check=change)
    finally:
        project.seeding_overrides.clear();project.seeding_overrides.update(original)
    assert calls==2


def test_remapper_imports_no_gui_toolkit():
    import subprocess,sys
    result=subprocess.run([sys.executable,'-c',
        "import sys; import anyfem.prepared_mesh_remap; assert not any(k.startswith(('PySide6','tkinter','anyfem.ui')) for k in sys.modules)"],
        capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_reopened_source_child_load_never_widens_to_its_parent_or_sheet():
    inputs,pressure,prior=reopened_child_fixture()
    result=remap_prepared_mesh_to_project(**inputs)
    bound=next(item for item in result.references if item.key.endswith(pressure.id))
    expected=prior.staged.mesh.elements_of_face[pressure.ref.id]
    assert set(bound.cell_ids)==set(expected)
    assert len(bound.cell_ids)<len(prior.staged.mesh.elements_of_sheet[1])
    assert result.mesh.elements_of_face[pressure.ref.id]==sorted(expected)
    assert result.mesh.tris==prior.staged.mesh.tris and len(result.mesh.tris)==26
