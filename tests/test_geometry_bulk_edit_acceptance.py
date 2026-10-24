"""Independent selected-only edits through existing structural consumers."""
from dataclasses import replace
import numpy as np
import pytest
from anygeometry import EntityRef
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.io.project_file import project_to_dict,project_from_dict
from anyfem.geometry_edit import GeometryEditDraft,MIXED
from anyfem.mesh_sizes import NamedRefinement
from anyfem.mesh_sizes import MeshSizeProperty,SetMeshSizeProperty,AssignMeshSize


def panel():
    p=default_project()
    feature=commands.AddStiffenedPanel(4,4,1,beam_section='stiffener',plate_section='plate').do(p)
    edges=sorted(p.edge_sections);faces=sorted(p.face_sections)
    assert len(edges)>=3 and len(faces)>=2
    return p,feature,edges,faces


def test_bulk_beam_angle_side_offset_selected_only_undo_and_reopen():
    p,feature,edges,faces=panel();stack=commands.CommandStack(p)
    original=p.beam_sections['stiffener'];before=project_to_dict(p)
    frames={edge:tuple(np.array(v) for v in p.beam_section_frame(edge)) for edge in edges}
    refs=tuple(EntityRef('edge',edge) for edge in edges[:2])
    draft=GeometryEditDraft.capture(p,refs)
    command=draft.command(beam={'rotation_deg':90.,'attachment_side':'back','offset_mode':'manual','eccentricity':.025},current_project=p)
    stack.run(command)
    assert p.beam_sections['stiffener']==original
    assert p.edge_sections[edges[0]]==p.edge_sections[edges[1]]!='stiffener'
    assert p.edge_sections[edges[2]]=='stiffener'
    assert len(p.beam_sections)==2
    for edge in edges[:2]:
        section=p.beam_section_of(edge)
        assert section.rotation_deg==90. and section.attachment_side=='back'
        assert section.web_height==original.web_height and section.material==original.material
        axis,old_y,old_z=frames[edge]
        new_axis,new_y,new_z=p.beam_section_frame(edge)
        np.testing.assert_allclose(new_axis,axis,atol=1e-12)
        # 90-degree rotation of the reversed plate normal around the beam axis.
        expected=np.cross(axis,-old_z)
        np.testing.assert_allclose(new_z,expected,atol=1e-12)
        np.testing.assert_allclose(p.beam_offset_vector(edge),.025*new_z,atol=1e-12)
    accepted=project_to_dict(p)
    loaded=project_from_dict(accepted)
    assert project_to_dict(loaded)==accepted
    np.testing.assert_allclose(loaded.beam_offset_vector(edges[0]),p.beam_offset_vector(edges[0]))
    stack.undo();assert project_to_dict(p)==before
    stack.redo();assert project_to_dict(p)==accepted


def test_mixed_beam_sections_preserve_unedited_dimensions_and_mesh_scope():
    p,feature,edges,faces=panel()
    tall=replace(p.beam_sections['stiffener'],name='Tall',web_height=.4,id='tall-section')
    commands.AddBeamSection(tall).do(p);commands.AssignBeam(edges[1],'Tall').do(p)
    draft=GeometryEditDraft.capture(p,tuple(EntityRef('edge',edge) for edge in edges[:2]))
    assert draft.shared('beam','web_height') is MIXED
    commands.CommandStack(p).run(draft.command(beam={'rotation_deg':30.,'mesh_size':.2},current_project=p))
    assert p.beam_section_of(edges[0]).web_height==.2
    assert p.beam_section_of(edges[1]).web_height==.4
    assert p.edge_sections[edges[2]]=='stiffener'
    policies=[item for item in p.refinements if isinstance(item,NamedRefinement)]
    assert {item.ref for item in policies}=={EntityRef('edge',edge) for edge in edges[:2]}
    assert all(item.size==.2 for item in policies)
    assert len({item.property_id for item in policies})==1


def test_plate_thickness_and_mesh_edit_is_selection_local_with_atomic_refusal():
    p,feature,edges,faces=panel();stack=commands.CommandStack(p)
    draft=GeometryEditDraft.capture(p,tuple(EntityRef('face',face) for face in faces[:2]))
    before=project_to_dict(p)
    with pytest.raises(ValueError):
        stack.run(draft.command(plate={'thickness':-.01,'mesh_size':.2},current_project=p))
    assert project_to_dict(p)==before and not stack.history()
    stack.run(draft.command(plate={'thickness':.03,'mesh_size':.2},current_project=p))
    assert p.plate_sections['plate'].thickness==.01
    assert p.face_sections[faces[0]]==p.face_sections[faces[1]]!='plate'
    assert all(p.face_sections[face]=='plate' for face in faces[2:])
    assert p.plate_sections[p.face_sections[faces[0]]].thickness==.03
    assert p.edge_sections=={edge:'stiffener' for edge in edges}
    stack.undo();assert project_to_dict(p)==before


def test_edit_refuses_stale_or_readonly_and_preserves_generator_linked_override():
    p,feature,edges,faces=panel();refs=(EntityRef('edge',edges[0]),)
    draft=GeometryEditDraft.capture(p,refs)
    with pytest.raises(PermissionError):draft.command(beam={'rotation_deg':30.},current_project=p,read_only=True)
    commands.AddPoint(9,9).do(p)
    with pytest.raises(ValueError):draft.command(beam={'rotation_deg':30.},current_project=p)
    draft=GeometryEditDraft.capture(p,refs)
    commands.CommandStack(p).run(draft.command(beam={'rotation_deg':30.},current_project=p))
    section=p.beam_section_of(edges[0]);identifier=section.id
    commands.EditFeature(feature.feature_id,parameters={**feature.parameters,'origin':(1,0,0)}).do(p)
    heads=p.geometry.resolve_ref(refs[0]);assert heads
    assert all(p.beam_section_of(ref.id).id==identifier for ref in heads)


def test_cylinder_rotation_uses_each_stiffeners_own_plate_frame():
    p=default_project()
    commands.AddCylinder(2,4,circumferential_segments=8,longitudinal_spacing=1,
        beam_section='stiffener',plate_section='plate').do(p)
    edges=sorted(p.edge_sections);assert len(edges)>3
    selected=edges[:-1]
    frames={edge:tuple(np.array(v) for v in p.beam_section_frame(edge)) for edge in edges}
    draft=GeometryEditDraft.capture(p,tuple(EntityRef('edge',edge) for edge in selected))
    commands.CommandStack(p).run(draft.command(beam={'rotation_deg':30.,'offset_mode':'manual','eccentricity':.01},current_project=p))
    angle=np.deg2rad(30.)
    for edge in selected:
        axis,old_y,old_z=frames[edge]
        expected=old_z*np.cos(angle)+np.cross(axis,old_z)*np.sin(angle)
        np.testing.assert_allclose(p.beam_section_frame(edge)[2],expected,atol=1e-12)
        np.testing.assert_allclose(p.beam_offset_vector(edge),.01*expected,atol=1e-12)
    assert p.edge_sections[edges[-1]]=='stiffener'


def test_mid_batch_failure_rolls_back_sections_assignments_and_history(monkeypatch):
    p,feature,edges,faces=panel();stack=commands.CommandStack(p)
    before=project_to_dict(p)
    draft=GeometryEditDraft.capture(p,tuple(EntityRef('edge',edge) for edge in edges[:2]))
    command=draft.command(beam={'rotation_deg':45.,'mesh_size':.2},current_project=p)
    original=commands.AssignBeam.do;calls=[]
    def fail_second(self,project):
        calls.append(self.edge_id)
        if len(calls)==2:raise ValueError('injected second assignment failure')
        return original(self,project)
    monkeypatch.setattr(commands.AssignBeam,'do',fail_second)
    with pytest.raises(ValueError,match='injected'):stack.run(command)
    assert len(calls)==2 and not stack.history()
    assert project_to_dict(p)==before


def test_numeric_mesh_edit_preserves_policy_and_equal_size_is_noop():
    p,feature,edges,faces=panel();stack=commands.CommandStack(p)
    fine=MeshSizeProperty('Fine',.1,radius=.5,growth=2.)
    other=MeshSizeProperty('Other',.15,radius=.25,growth=1.8)
    SetMeshSizeProperty(fine).do(p);SetMeshSizeProperty(other).do(p)
    AssignMeshSize(fine.id,(EntityRef('edge',edges[0]),EntityRef('edge',edges[2]))).do(p)
    AssignMeshSize(other.id,(EntityRef('edge',edges[1]),)).do(p)
    ref=EntityRef('edge',edges[0]);before=project_to_dict(p)
    assert GeometryEditDraft.capture(p,(ref,)).command(beam={'mesh_size':.1},current_project=p) is None
    assert project_to_dict(p)==before
    refs=tuple(EntityRef('edge',edge) for edge in edges[:2])
    stack.run(GeometryEditDraft.capture(p,refs).command(beam={'mesh_size':.2},current_project=p))
    policies={item.ref:item for item in p.refinements if isinstance(item,NamedRefinement)}
    assert (policies[refs[0]].size,policies[refs[0]].radius,policies[refs[0]].growth)==(.2,.5,2.)
    assert (policies[refs[1]].size,policies[refs[1]].radius,policies[refs[1]].growth)==(.2,.25,1.8)
    untouched=policies[EntityRef('edge',edges[2])]
    assert untouched.property_id==fine.id and (untouched.size,untouched.radius,untouched.growth)==(.1,.5,2.)
    assert p.mesh_size_properties[fine.id]==fine and p.mesh_size_properties[other.id]==other
    stack.undo();assert project_to_dict(p)==before


def test_generator_owned_mesh_is_shown_and_conflicting_edit_refuses_atomically():
    p=default_project()
    commands.AddStiffenedPanel(4,4,1,beam_section='stiffener',plate_section='plate',mesh_size=.3).do(p)
    ref=EntityRef('edge',next(iter(p.edge_sections)))
    draft=GeometryEditDraft.capture(p,(ref,))
    assert draft.shared('beam','mesh_size')==.3
    before=project_to_dict(p)
    assert draft.command(beam={'mesh_size':.3},current_project=p) is None
    with pytest.raises(ValueError,match='(?i)(legacy|generator|managed)'):
        draft.command(beam={'rotation_deg':30.,'mesh_size':.4},current_project=p)
    assert project_to_dict(p)==before
