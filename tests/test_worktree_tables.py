"""Focused shared table contracts beyond independent native acceptance."""
import pytest
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.tables import CategoryAdapter
from anyfem.io.project_file import project_to_dict
from anyfem.mesh_sizes import MeshSizeProperty,SetMeshSizeProperty,AssignMeshSize


def plate_project():
    p=default_project();ids=[commands.AddPoint(*xy).do(p) for xy in ((0,0),(1,0),(1,1),(0,1))]
    face=commands.AddPlate(ids).do(p)
    return p,ids,face


def test_definition_delete_uses_name_keeps_uuid_and_undo():
    p=default_project();stack=commands.CommandStack(p);adapter=CategoryAdapter(p,'Plate sections');row=adapter.rows[0];original=row.record
    stack.run(adapter.delete_command(row));assert original.name not in p.plate_sections
    stack.undo();assert p.plate_sections[original.name].id==original.id
    stack.redo();assert original.name not in p.plate_sections


def test_plate_assignment_cell_and_derived_values():
    p,ids,face=plate_project();stack=commands.CommandStack(p);adapter=CategoryAdapter(p,'Plates');before=project_to_dict(p)
    stack.run(adapter.edit_command(face,'section','plate'));adapter.refresh()
    values={column.key:adapter.get(adapter.by_key[face],column) for column in adapter.columns}
    assert values['section']=='plate' and values['thickness']==.01 and values['material']
    stack.undo();assert not p.face_sections and not p.section_assignments
    assert set(p.geometry.faces)=={face}
    with pytest.raises(ValueError):adapter.edit_command(face,'section','missing')


def test_named_size_row_highlights_assigned_scope():
    p,ids,face=plate_project();definition=MeshSizeProperty('Fine',.1);SetMeshSizeProperty(definition).do(p)
    ref=p.geometry.entity_ref('face',face);AssignMeshSize(definition.id,(ref,)).do(p)
    adapter=CategoryAdapter(p,'Mesh sizes');assert adapter.targets(adapter.by_key[definition.id])==(ref,)


def test_delete_batch_refusal_rolls_back_prior_child():
    p,ids,face=plate_project();commands.AssignPlate(face,'plate').do(p);adapter=CategoryAdapter(p,'Plate sections');before=project_to_dict(p)
    from anyfem.model.materials import MaterialSpec
    children=(commands.AddMaterial(MaterialSpec('Temporary',constants={'elastic_modulus':1e8,'poisson_ratio':.3})),adapter.delete_command(adapter.rows[0]))
    with pytest.raises(ValueError):commands.CompositeCommand(children).do(p)
    assert project_to_dict(p)==before


def test_material_mechanical_cells_and_creation_use_owner_keys():
    p=default_project();adapter=CategoryAdapter(p,'Materials');row=adapter.rows[0];stack=commands.CommandStack(p)
    stack.run(adapter.edit_command(row.key,'E','190 GPa'));assert p.materials[row.record.name].constants['elastic_modulus']==190e9
    stack.undo();assert p.materials[row.record.name].constants['elastic_modulus']==row.record.constants['elastic_modulus']
    command=adapter.create_command(dict(name='New alloy',E=70e9,nu=.3,density=2700.,yield_stress=250e6));stack.run(command)
    assert p.materials['New alloy'].constants['elastic_modulus']==70e9
    stack.undo();assert 'New alloy' not in p.materials
