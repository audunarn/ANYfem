"""Independent presentation consolidation preserves canonical scopes."""
import pytest
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.io.project_file import project_to_dict,project_from_dict
from anyfem.model.attributes import Support
from anyfem.model.regions import Region,RegionDomain,ManualRegion,BooleanRegion
from anyfem.tables import CategoryAdapter,CATEGORIES


def assigned_model():
    p=default_project()
    vertices=[commands.AddPoint(*xy).do(p) for xy in ((0,0),(2,0),(2,1),(0,1))]
    face=commands.AddPlate(vertices).do(p)
    fref=p.geometry.entity_ref('face',face)
    edge=p.geometry.faces[face].loop[0].edge;eref=p.geometry.entity_ref('edge',edge)
    commands.AssignPlate(face,'plate').do(p);commands.AssignBeam(edge,'stiffener').do(p)
    commands.AddSupport(Support('End',eref,{'ux':0.,'uz':0.})).do(p)
    commands.AddPressure(fref,2000.).do(p)
    # The combined set intentionally sorts before its operands in persisted UUID order.
    left=Region('Plate set',RegionDomain.GEOMETRY,'face',ManualRegion((fref,)),id='00000000-0000-4000-8000-000000000010')
    right=Region('Other plate set',RegionDomain.GEOMETRY,'face',ManualRegion((fref,)),id='00000000-0000-4000-8000-000000000020')
    union=Region('Combined plates',RegionDomain.GEOMETRY,'face',BooleanRegion('union',(left.id,right.id)),id='00000000-0000-4000-8000-000000000001')
    for region in (left,right,union):commands.AddRegion(region).do(p)
    return p,(left,right,union),(fref,eref)


def test_only_user_sets_in_table_preserve_internal_records_and_assignments():
    p,regions,refs=assigned_model();before=project_to_dict(p)
    hidden={region.id for region in p.regions if region.hidden}
    assert hidden and 'Regions' not in CATEGORIES
    table=CategoryAdapter(p,'Sets')
    assert {row.key for row in table.rows}=={region.id for region in regions}
    assert not hidden & set(table.by_key)
    for row in table.rows:assert table.targets(row)==(refs[0],)
    assert project_to_dict(p)==before
    loaded=project_from_dict(before)
    assert {region.id for region in loaded.regions if region.hidden}==hidden
    assert project_to_dict(loaded)==before
    assert len(loaded.supports)==1 and len(loaded.load_cases['default'].pressures)==1
    assert loaded.face_sections==p.face_sections and loaded.edge_sections==p.edge_sections


def test_no_user_sets_means_empty_table_even_with_many_internal_scopes():
    p,regions,refs=assigned_model()
    for region in reversed(regions):p.regions.remove(region.id)
    assert len(p.regions)>0
    assert not CategoryAdapter(p,'Sets').rows


@pytest.mark.parametrize('damage',['duplicate','missing','cycle'])
def test_combined_set_loading_retains_invalid_dependency_refusal(damage):
    from anyfem.io.project_file import ProjectFileError
    p,regions,refs=assigned_model();data=project_to_dict(p)
    by_id={entry['id']:entry for entry in data['regions']}
    if damage=='duplicate':data['regions'].append(dict(by_id[regions[0].id]))
    elif damage=='missing':data['regions']=[entry for entry in data['regions'] if entry['id']!=regions[0].id]
    if damage=='cycle':
        # Obtain the exact canonical wire form without touching the accepted registry.
        record=Region('Cyclic',RegionDomain.GEOMETRY,'face',BooleanRegion('union',(regions[-1].id,)))
        by_id[regions[0].id]['definition']=record.to_dict()['definition']
    with pytest.raises(ProjectFileError):project_from_dict(data)
