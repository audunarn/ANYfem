"""Independent table-to-command acceptance: identity, units and refusal."""
import pytest

from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.io.project_file import project_to_dict, project_from_dict
from anyfem.model.attributes import Support, Pressure
from anyfem.tables import CategoryAdapter, CATEGORIES


def test_tables_preserve_definition_identity_assignments_units_and_undo():
    p=default_project();stack=commands.CommandStack(p)
    ids=[commands.AddPoint(*xy).do(p) for xy in ((0,0),(1,0),(1,1),(0,1))]
    face=commands.AddPlate(ids).do(p)
    commands.AssignPlate(face,'plate').do(p)
    section=p.plate_sections['plate'];before=project_to_dict(p)
    adapter=CategoryAdapter(p,'Plate sections')
    stack.run(adapter.edit_command(section.id,'thickness','20 mm'))
    assert p.plate_sections['plate'].id==section.id
    assert p.plate_sections['plate'].thickness==pytest.approx(.02)
    assert p.face_sections[face]=='plate'
    stack.undo();assert project_to_dict(p)==before
    stack.redo()
    restored=project_from_dict(project_to_dict(p))
    assert restored.plate_sections['plate'].id==section.id
    assert restored.face_sections[face]=='plate'
    adapter.refresh();accepted=project_to_dict(p)
    with pytest.raises((ValueError,TypeError)):
        stack.run(adapter.edit_command(section.id,'thickness','-1 mm'))
    assert project_to_dict(p)==accepted


def test_support_cell_edit_keeps_targets_identity_other_constraints():
    p=default_project();stack=commands.CommandStack(p)
    vertex=commands.AddPoint(0,0).do(p);ref=p.geometry.entity_ref('vertex',vertex)
    original=commands.AddSupport(Support('End',ref,{'ux':0.,'uz':0.})).do(p)
    adapter=CategoryAdapter(p,'Supports')
    stack.run(adapter.edit_command(original.id,'ux','2 mm'))
    current=p.supports[0]
    assert current.id==original.id and current.ref==original.ref
    assert current.region==original.region and current.constraints=={'ux':.002,'uz':0.}
    stack.undo();assert p.supports==[original]


def test_all_categories_empty_or_populated_expose_stable_unique_keys():
    p=default_project()
    assert len(CATEGORIES)==25 and len(set(CATEGORIES))==25
    for category in CATEGORIES:
        adapter=CategoryAdapter(p,category)
        assert adapter.columns
        assert len(adapter.by_key)==len(adapter.rows)
        for row in adapter.rows:
            assert adapter.by_key[row.key] is row
            for column in adapter.columns:
                adapter.get(row,column)


def test_point_table_exceeds_tree_window_without_truncation():
    p=default_project()
    ids=[p.geometry.add_point(float(i),0.,0.) for i in range(2051)]
    adapter=CategoryAdapter(p,'Points')
    assert len(adapter.rows)==2051
    last=adapter.rows[-1]
    assert adapter.get(last,adapter.columns[0])==2050.


def test_table_delete_batch_refuses_assigned_definition_atomically():
    from anyfem.model.sections import PlateSection
    p=default_project();stack=commands.CommandStack(p)
    commands.AddPlateSection(PlateSection('Unused',.02,'S355')).do(p)
    ids=[commands.AddPoint(*xy).do(p) for xy in ((0,0),(1,0),(0,1))]
    face=commands.AddPlate(ids).do(p);commands.AssignPlate(face,'plate').do(p)
    adapter=CategoryAdapter(p,'Plate sections');rows={row.record.name:row for row in adapter.rows}
    before=project_to_dict(p)
    with pytest.raises(ValueError,match='assigned'):
        stack.run(commands.CompositeCommand(tuple(adapter.delete_command(rows[name]) for name in ('Unused','plate')),'delete table rows'))
    assert project_to_dict(p)==before and not stack.history()


def test_geometry_table_assigns_existing_section_through_canonical_command():
    from anyfem.model.sections import PlateSection
    p=default_project();stack=commands.CommandStack(p)
    commands.AddPlateSection(PlateSection('Heavy',.03,'S355')).do(p)
    ids=[commands.AddPoint(*xy).do(p) for xy in ((0,0),(1,0),(0,1))]
    face=commands.AddPlate(ids).do(p)
    adapter=CategoryAdapter(p,'Plates');row=adapter.by_key[face]
    stack.run(adapter.edit_command(face,'section','Heavy'))
    assert p.face_sections[face]=='Heavy'
    adapter.refresh();row=adapter.by_key[face]
    assert adapter.get(row,next(c for c in adapter.columns if c.key=='thickness'))==.03
    stack.undo();assert face not in p.face_sections


def test_material_and_pressure_inputs_update_existing_records_only():
    p=default_project();stack=commands.CommandStack(p)
    material_id=p.material_ids['S355']
    adapter=CategoryAdapter(p,'Materials')
    stack.run(adapter.edit_command(material_id,'E','200 GPa'))
    adapter.refresh()
    assert adapter.get(adapter.by_key[material_id],next(c for c in adapter.columns if c.key=='E'))==pytest.approx(200e9)
    assert p.material_ids['S355']==material_id
    ids=[commands.AddPoint(*xy).do(p) for xy in ((0,0),(1,0),(0,1))]
    face=commands.AddPlate(ids).do(p);ref=p.geometry.entity_ref('face',face)
    original=commands.AddPressure(ref,1000.).do(p)
    adapter=CategoryAdapter(p,'Loads')
    stack.run(adapter.edit_command(original.id,'value','2 kPa'))
    current=p.load_cases['default'].pressures[0]
    assert current.id==original.id and current.ref==original.ref
    assert current.value==2000. and current.envelope==original.envelope
    assert len(p.load_cases['default'].pressures)==1
    stack.undo();assert p.load_cases['default'].pressures==[original]
