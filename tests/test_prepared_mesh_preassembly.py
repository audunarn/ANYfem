"""Candidate composition and final-guard regressions; no solver execution."""
from copy import deepcopy
import os
from dataclasses import replace
import sys

import numpy as np
import pytest
if os.environ.get('ANYFEM_RUN_PREPARED_MESH_TESTS') != '1':
    pytest.skip('prepared-mesh candidate execution requires explicit reviewed run', allow_module_level=True)

mesher = pytest.importorskip('anymesher')
if not hasattr(mesher, 'prepare_source_bound_qualified_s3_mesh'):
    pytest.skip('requires unpublished owner source S3 candidate', allow_module_level=True)

from anygeometry.entities import EntityRef
from anyfem.model.attributes import LoadCase, fixed
from anyfem.model.formulations import ShellFormulationPolicy
from anyfem.model.materials import steel
from anyfem.prepared_mesh_remap import _snapshot, remap_prepared_mesh_to_project
from anyfem.prepared_mesh_preassembly import (
    PreparedMeshPreassemblyError, _copy_project_for_preassembly,
    preassemble_prepared_project_mesh,
)
from anyfem.prepared_reference_scope import _project_inputs, query_prepared_project_reference_scope
from prepared_mesh_remap_fixture import reopened_child_fixture


@pytest.fixture(scope='module')
def composed():
    inputs, pressure, _ = reopened_child_fixture()
    project = inputs['project']
    project.add_material(steel('S355', .01))
    project.add_plate_section('shell', .01, 'S355')
    project.assign_plates(tuple(project.geometry.faces), 'shell')
    supported = next(face for face in project.geometry.faces if face != pressure.ref.id)
    project.add_support(fixed(EntityRef('face', supported)))
    inputs['manifest'] = query_prepared_project_reference_scope(
        project, inputs['prepared_geometry'], closure=inputs['closure'])
    before = _snapshot(inputs['mesh'])
    artifact = preassemble_prepared_project_mesh(**inputs)
    assert _snapshot(inputs['mesh']) == before
    return inputs, pressure, artifact


@pytest.fixture
def candidate(composed, monkeypatch):
    inputs, _, artifact = composed
    inputs = dict(inputs, mesh=deepcopy(inputs['mesh']))
    # Existing remapper regressions are retained separately. Reuse its validated
    # binding to isolate changed production/build/final-guard paths here.
    bound = replace(artifact.source_binding,
                    mesh=deepcopy(artifact.source_binding.mesh),
                    current_mesh=deepcopy(artifact.source_binding.current_mesh),
                    current_receipt=deepcopy(artifact.source_binding.current_receipt))
    monkeypatch.setattr('anyfem.prepared_mesh_preassembly.remap_prepared_mesh_to_project',
                        lambda *args, **kwargs: bound)
    return inputs, bound


def test_live_composition_preserves_26_cells_six_pressure_cells_and_owner_authority(composed):
    inputs, pressure, artifact = composed
    assert len(artifact.mesh.tris) == len(artifact.element_ids) == 26
    binding = next(item for item in artifact.source_binding.references if item.key.endswith(pressure.id))
    assert len(binding.cell_ids) == 6
    assert set(binding.cell_ids) == set(artifact.mesh.elements_of_face[pressure.ref.id])
    record = artifact.production_record
    assert artifact.mesh.structural_preparation == {'qualified_s3': record}
    assert len(record['element_owner_normals']) == 26
    assert record['authority_model']['source_model_id'] == str(inputs['project'].geometry.model_id)
    assert record['authority_model']['source_revision'] == inputs['project'].geometry.revision
    assert artifact.source_binding.mesh.structural_preparation == {}
    assert dict(artifact.field_inventory)['structural_preparation'] == 1
    assert artifact.preassembly_checked and not artifact.solver_executed
    assert not artifact.publication_qualified
    assert artifact.load_case_name == 'default'
    assert artifact.load_case_id == inputs['project'].load_cases['default'].id
    for node, value in artifact.mesh.nodes.items():
        np.testing.assert_array_equal(value, inputs['mesh'].nodes[node])
        assert not np.shares_memory(value, inputs['mesh'].nodes[node])
    assert not any(name == 'tkinter' or name.startswith(('PySide6', 'anyfem.ui')) for name in sys.modules)


@pytest.mark.parametrize('phase', ['FEM prepared mesh preassembly start',
                                  'FEM prepared mesh preassembly final'])
def test_cancellation_returns_no_artifact(candidate, phase):
    inputs, _ = candidate
    before = _snapshot(inputs['mesh'])
    with pytest.raises(PreparedMeshPreassemblyError, match='cancelled'):
        preassemble_prepared_project_mesh(**inputs, cancellation_check=lambda stage: stage == phase)
    assert _snapshot(inputs['mesh']) == before


def test_project_replacement_during_final_callback_refuses(candidate):
    inputs, _ = candidate
    current = inputs['project']
    def callback(stage):
        nonlocal current
        if stage == 'FEM prepared mesh preassembly final':
            current = object()
        return False
    with pytest.raises(PreparedMeshPreassemblyError, match='project_replaced'):
        preassemble_prepared_project_mesh(**inputs, cancellation_check=callback,
                                         current_project=lambda: current)


def test_late_project_edit_is_detected_and_not_undone(candidate):
    inputs, _ = candidate
    project = inputs['project']
    before = dict(project.seeding_overrides)
    edge = next(iter(project.geometry.edges))
    def callback(stage):
        if stage == 'FEM prepared mesh preassembly output':
            project.seeding_overrides[edge] = 9
        return False
    try:
        with pytest.raises(PreparedMeshPreassemblyError, match='invalid_binding'):
            preassemble_prepared_project_mesh(**inputs, cancellation_check=callback)
        assert project.seeding_overrides[edge] == 9
    finally:
        project.seeding_overrides.clear()
        project.seeding_overrides.update(before)


@pytest.mark.parametrize('change', ['coordinates', 'record', 'instance_field'])
def test_late_qualified_output_mutation_refuses(candidate, monkeypatch, change):
    inputs, _ = candidate
    original = mesher.prepare_source_bound_qualified_s3_mesh
    held = {}
    def qualify(*args, **kwargs):
        made, record = original(*args, **kwargs)
        held.update(mesh=made, record=record)
        return made, record
    monkeypatch.setattr(mesher, 'prepare_source_bound_qualified_s3_mesh', qualify)
    def callback(stage):
        if stage == 'FEM prepared mesh preassembly output':
            if change == 'coordinates':
                node = min(held['mesh'].nodes)
                held['mesh'].nodes[node] = held['mesh'].nodes[node].copy()
                held['mesh'].nodes[node][0] += .01
            elif change == 'record':
                held['record']['status'] = 'FORGED'
            else:
                held['mesh'].custom_face_loads = {1: 10}
        return False
    with pytest.raises(PreparedMeshPreassemblyError,
                       match='input_or_output_changed|unsupported_mesh_field'):
        preassemble_prepared_project_mesh(**inputs, cancellation_check=callback)


def test_qualified_coordinate_alias_refuses(candidate, monkeypatch):
    inputs, _ = candidate
    original = mesher.prepare_source_bound_qualified_s3_mesh
    def qualify(source, *args, **kwargs):
        made, record = original(source, *args, **kwargs)
        node = min(made.nodes)
        made.nodes[node] = source.nodes[node]
        return made, record
    monkeypatch.setattr(mesher, 'prepare_source_bound_qualified_s3_mesh', qualify)
    with pytest.raises(PreparedMeshPreassemblyError, match='output_not_detached'):
        preassemble_prepared_project_mesh(**inputs)


def test_raw_structural_preparation_still_refuses_without_owner_qualification(candidate, monkeypatch):
    inputs, _ = candidate
    inputs['mesh'].structural_preparation['qualified_s3'] = {'status': 'ADMITTED'}
    monkeypatch.setattr('anyfem.prepared_mesh_preassembly.remap_prepared_mesh_to_project',
                        remap_prepared_mesh_to_project)
    def forbidden(*args, **kwargs):
        raise AssertionError('raw preparation reached owner qualification')
    monkeypatch.setattr(mesher, 'prepare_source_bound_qualified_s3_mesh', forbidden)
    with pytest.raises(ValueError, match='unsupported_association'):
        preassemble_prepared_project_mesh(**inputs)


def test_foreign_load_case_is_outside_witnessed_project(candidate):
    inputs, _ = candidate
    foreign = LoadCase('outside witness')
    foreign.add_pressure(EntityRef('face', next(iter(inputs['project'].geometry.faces))), 9876)
    with pytest.raises(PreparedMeshPreassemblyError, match='unwitnessed_load_case'):
        preassemble_prepared_project_mesh(**inputs, load_case=foreign)


def test_legacy_s3_policy_cannot_claim_qualified_preassembly(candidate):
    inputs, _ = candidate
    inputs = dict(inputs, project=_copy_project_for_preassembly(inputs['project']))
    inputs['project'].shell_formulation_policy = ShellFormulationPolicy.legacy_compatible()
    inputs['manifest'] = query_prepared_project_reference_scope(
        inputs['project'], inputs['prepared_geometry'], closure=inputs['closure'])
    with pytest.raises(PreparedMeshPreassemblyError, match='unsupported_formulation'):
        preassemble_prepared_project_mesh(**inputs)


@pytest.mark.parametrize('refused', [False, True])
def test_legacy_assignment_normalization_only_changes_detached_build_project(candidate, monkeypatch, refused):
    inputs, _ = candidate
    inputs = dict(inputs, project=_copy_project_for_preassembly(inputs['project']))
    project = inputs['project']
    project.section_assignments.clear()
    project.face_assignment_ids.clear()
    inputs['manifest'] = query_prepared_project_reference_scope(
        project, inputs['prepared_geometry'], closure=inputs['closure'])
    # This changed intent needs a real fresh remapper binding, not the cached one.
    monkeypatch.setattr('anyfem.prepared_mesh_preassembly.remap_prepared_mesh_to_project',
                        remap_prepared_mesh_to_project)
    before = _project_inputs(project)
    from anyfem.solve import build_fe_model
    held = {}
    def build(staged, *args, **kwargs):
        assert staged is not project and staged.geometry is not project.geometry
        held['project'] = staged
        result = build_fe_model(staged, *args, **kwargs)
        if refused:
            raise ValueError('forced failure after detached normalization')
        return result
    monkeypatch.setattr('anyfem.solve.build_fe_model', build)
    if refused:
        with pytest.raises(PreparedMeshPreassemblyError, match='preassembly_refused'):
            preassemble_prepared_project_mesh(**inputs)
    else:
        assert preassemble_prepared_project_mesh(**inputs).preassembly_checked
    assert held['project'].section_assignments
    assert not project.section_assignments
    assert _project_inputs(project) == before
