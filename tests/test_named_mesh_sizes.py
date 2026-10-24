from dataclasses import replace
import pytest
import numpy as np
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.mesh_sizes import MeshSizeProperty,NamedRefinement,SetMeshSizeProperty,AssignMeshSize,DeleteMeshSizeProperty
from anyfem.mesh.refinement import Refinement,SizeField
from anyfem.io.project_file import project_to_dict,project_from_dict
from anyfem.document import _model_payload,canonical_hash


def model():
    p=default_project();ids=[commands.AddPoint(*xy).do(p) for xy in ((0,0),(1,0),(1,1),(0,1))];face=commands.AddPlate(ids).do(p)
    return p,(p.geometry.entity_ref('face',face),p.geometry.entity_ref('edge',p.geometry.faces[face].loop[0].edge))


def test_unused_definition_no_mesh_effect_and_hash_rename_provenance():
    p,refs=model();before=canonical_hash(_model_payload(project_to_dict(p)));stack=commands.CommandStack(p);fine=stack.run(SetMeshSizeProperty(MeshSizeProperty('Fine',.2)))
    assert not p.refinements and canonical_hash(_model_payload(project_to_dict(p)))==before
    stack.run(AssignMeshSize(fine.id,refs));active=canonical_hash(_model_payload(project_to_dict(p)))
    stack.run(SetMeshSizeProperty(replace(fine,name='Renamed')))
    assert canonical_hash(_model_payload(project_to_dict(p)))==active
    assert commands.SetMeshSizeProperty is SetMeshSizeProperty


def test_apply_replace_preserve_legacy_sizefield_and_atomic_undo_roundtrip():
    p,refs=model();legacy=Refinement(.1,center=(9,9,9));p.refinements.append(legacy)
    stack=commands.CommandStack(p);fine=stack.run(SetMeshSizeProperty(MeshSizeProperty('Fine',.2)));coarse=stack.run(SetMeshSizeProperty(MeshSizeProperty('Coarse',.4)))
    stack.run(AssignMeshSize(fine.id,refs+refs));assert len(p.refinements)==3
    stack.run(AssignMeshSize(coarse.id,(refs[0],)))
    assert p.refinements[0] is legacy and {item.property_id for item in p.refinements[1:]}=={fine.id,coarse.id}
    field=SizeField(p.geometry,1.,p.refinements[1:]);assert field.size_at([p.refinements[-1].sources(p.geometry)[0]])[0]<=.4
    state=project_to_dict(p);loaded=project_from_dict(state);assert project_to_dict(loaded)==state
    stack.undo();assert all(item.property_id==fine.id for item in p.refinements[1:])
    stack.redo();assert any(item.property_id==coarse.id for item in p.refinements[1:])
    stack.run(SetMeshSizeProperty(replace(coarse,size=.3)));assert [x.size for x in p.refinements if getattr(x,'property_id',None)==coarse.id]==[.3]
    with pytest.raises(ValueError,match='assigned'):stack.run(DeleteMeshSizeProperty(coarse.id))


@pytest.mark.parametrize('value',[0,-1,True,float('nan'),float('inf')])
def test_invalid_definition(value):
    with pytest.raises(ValueError):MeshSizeProperty('Invalid',value)


def test_global_cap_and_incompatible_targets_refuse_without_mutation():
    p,refs=model();p.target_size=.2;stack=commands.CommandStack(p);large=stack.run(SetMeshSizeProperty(MeshSizeProperty('Large',.5)));before=project_to_dict(p)
    with pytest.raises(ValueError,match='global'):stack.run(AssignMeshSize(large.id,refs))
    assert project_to_dict(p)==before
    small=stack.run(SetMeshSizeProperty(MeshSizeProperty('Small',.1)));stack.run(AssignMeshSize(small.id,refs))
    before=project_to_dict(p)
    with pytest.raises(ValueError,match='global'):stack.run(SetMeshSizeProperty(replace(small,size=.3)))
    assert project_to_dict(p)==before
    with pytest.raises(ValueError):stack.run(AssignMeshSize(small.id,(p.geometry.entity_ref('vertex',next(iter(p.geometry.vertices))),)))


def test_invalid_or_dangling_links_fail_loading():
    p,refs=model();fine=SetMeshSizeProperty(MeshSizeProperty('Fine',.2)).do(p);AssignMeshSize(fine.id,refs).do(p)
    state=project_to_dict(p);state['mesh_size_properties']=[]
    with pytest.raises(ValueError,match='unknown'):project_from_dict(state)


def test_global_settings_fallback_and_named_link_survive_feature_translation():
    from types import SimpleNamespace
    from anyfem.mesh_sizes import global_target
    p=default_project();p.target_size=.3;p.native_mesh_settings={'target_size':None};assert global_target(p)==.3
    p.native_mesh_settings=SimpleNamespace(target_size=None);assert global_target(p)==.3
    p.native_mesh_settings=None
    feature=commands.AddStiffenedPanel(4,3,1).do(p);face=next(iter(p.geometry.faces));fine=SetMeshSizeProperty(MeshSizeProperty('Fine',.2)).do(p);AssignMeshSize(fine.id,(p.geometry.entity_ref('face',face),)).do(p)
    commands.EditFeature(feature.feature_id,parameters={**feature.parameters,'origin':(1,0,0)}).do(p)
    assert all(isinstance(item,NamedRefinement) and item.property_id==fine.id for item in p.refinements)
    assert all(p.resolve_geometry_attachment(item.ref) for item in p.refinements)


def test_reassign_partial_resolved_scope_retains_unselected_named_targets(monkeypatch):
    p,refs=model();edge_ids=list(p.geometry.edges);targets=tuple(p.geometry.entity_ref('edge',i) for i in edge_ids[:2])
    old=SetMeshSizeProperty(MeshSizeProperty('Old',.2)).do(p);new=SetMeshSizeProperty(MeshSizeProperty('New',.3)).do(p)
    AssignMeshSize(old.id,(targets[0],)).do(p)
    original=type(p).resolve_geometry_attachment
    def resolve(self,ref):return targets if ref==targets[0] else original(self,ref)
    monkeypatch.setattr(type(p),'resolve_geometry_attachment',resolve)
    # Expose a resolved split on the old scope only while computing replacement.
    command=AssignMeshSize(new.id,(targets[0],))
    project_type=type(p)
    calls=[0]
    def once(self,ref):
        calls[0]+=1
        return targets if calls[0]==1 and ref==targets[0] else original(self,ref)
    monkeypatch.setattr(project_type,'resolve_geometry_attachment',once)
    command.do(p)
    bindings={item.ref:item.property_id for item in p.refinements}
    assert bindings=={targets[0]:new.id,targets[1]:old.id}


def test_command_recording_and_corrupt_boolean_named_assignment():
    from anyfem.command_recording import command_to_python
    p,refs=model();definition=SetMeshSizeProperty(MeshSizeProperty('Fine',1.)).do(p)
    text=command_to_python(AssignMeshSize(definition.id,refs));assert not text.startswith('#')
    AssignMeshSize(definition.id,refs).do(p);state=project_to_dict(p)
    state['meshing']['refinements'][0]['size']=True
    with pytest.raises(ValueError,match='boolean'):project_from_dict(state)


def test_clear_refinements_keeps_reusable_definition_and_restores_named_links():
    p,refs=model();stack=commands.CommandStack(p);definition=stack.run(SetMeshSizeProperty(MeshSizeProperty('Fine',.2)));stack.run(AssignMeshSize(definition.id,refs));before=tuple(p.refinements)
    stack.run(commands.ClearRefinements());assert not p.refinements and p.mesh_size_properties[definition.id] is definition
    stack.undo();assert tuple(p.refinements)==before and all(isinstance(item,NamedRefinement) for item in p.refinements)
    stack.redo();assert not p.refinements


def test_feature_length_edit_retains_named_definition_output_binding():
    p=default_project();feature=commands.AddStiffenedPanel(4,3,1).do(p);definition=SetMeshSizeProperty(MeshSizeProperty('Fine',.2)).do(p)
    AssignMeshSize(definition.id,(p.geometry.entity_ref('face',next(iter(p.geometry.faces))),)).do(p)
    commands.EditFeature(feature.feature_id,parameters={**feature.parameters,'length':5}).do(p)
    assert definition.id in p.mesh_size_properties
    assert all(item.property_id==definition.id and p.resolve_geometry_attachment(item.ref) for item in p.refinements)


def test_split_edge_named_scope_survives_and_partial_reassignment_preserves_other_half():
    p,refs=model();definition=SetMeshSizeProperty(MeshSizeProperty('Fine',.2)).do(p);other=SetMeshSizeProperty(MeshSizeProperty('Other',.3)).do(p)
    AssignMeshSize(definition.id,(refs[1],)).do(p)
    commands.SplitEdge(refs[1].id,.5).do(p)
    halves=tuple(p.resolve_geometry_attachment(refs[1]));assert len(halves)==2
    AssignMeshSize(other.id,(halves[0],)).do(p)
    bindings={ref:item.property_id for item in p.refinements for ref in p.resolve_geometry_attachment(item.ref)}
    assert bindings[halves[0]]==other.id and bindings[halves[1]]==definition.id


def test_first_import_orders_and_lazy_public_commands():
    import os,subprocess,sys
    for first in ('anyfem.mesh_sizes','anyfem.io.project_file'):
        source=f"import {first}; from anyfem import commands; from anyfem.mesh_sizes import SetMeshSizeProperty; assert commands.SetMeshSizeProperty is SetMeshSizeProperty"
        result=subprocess.run([sys.executable,'-c',source],env=os.environ,capture_output=True,text=True,timeout=20)
        assert result.returncode==0,result.stderr
