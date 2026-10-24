from dataclasses import replace
import pytest
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.model.attributes import Support
from anyfem.load_assignment import AttributeEdit,attribute_targets
from anyfem.io.project_file import project_to_dict


def model():
    p=default_project();ids=[commands.AddPoint(x,0).do(p) for x in (0,1,2)]
    refs=[p.geometry.entity_ref('vertex',i) for i in ids]
    return p,refs


def test_record_edit_preserves_identity_metadata_and_canonical_undo():
    p,refs=model();item=commands.AddSupport(Support('A',refs[0],{'ux':.002})).do(p)
    context=AttributeEdit.capture(p,item);stack=commands.CommandStack(p)
    updated=stack.run(context.command(p,replace(item,name='B',constraints={'ux':.003,'uz':0.})))
    assert updated.id==item.id and updated.region is item.region and updated.ref==item.ref
    stack.undo();assert p.supports==[item]
    stack.redo();assert p.supports[0] is updated
    with pytest.raises(ValueError,match='changed'):context.validate(p)


def test_record_guard_refuses_replacement_project_address_readonly_targets():
    p,refs=model();item=commands.AddSupport(Support('A',refs[0],{'ux':0.})).do(p);context=AttributeEdit.capture(p,item)
    with pytest.raises(PermissionError):context.validate(p,read_only=True)
    other,_=model()
    with pytest.raises(ValueError,match='Project'):context.validate(other)
    with pytest.raises(ValueError,match='targets'):context.command(p,replace(item,ref=refs[1]))
    commands.DeleteAttribute(item.id).do(p)
    with pytest.raises(ValueError):context.validate(p)


def test_region_targets_resolve_all_members_and_feature_lineage():
    from anyfem.model.regions import Region,ManualRegion,RegionRef
    p,refs=model();region=Region('Group','geometry','vertex',ManualRegion(tuple(refs)));p.regions.add(region)
    item=Support('Group support',refs[0],{'ux':0.},region=RegionRef(region.id))
    assert attribute_targets(p,item)==tuple(refs)


def test_wet_pressure_targets_resolve_selected_or_all_surface_scopes():
    from anyfem.model.attributes import WetSurface,WetPressure
    p=default_project();ids=[commands.AddPoint(*xy).do(p) for xy in ((0,0),(1,0),(0,1))];face=commands.AddPlate(ids).do(p);refs=[p.geometry.entity_ref('face',face)]
    # Surface records resolve their durable scope just like ordinary supports.
    surface=WetSurface(ref=refs[0]);p.wet_surfaces.append(surface)
    assert attribute_targets(p,WetPressure(value=2))==(refs[0],)
    assert attribute_targets(p,WetPressure(value=2,surfaces=(surface.id,)))==(refs[0],)
