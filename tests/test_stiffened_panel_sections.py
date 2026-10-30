"""Directional generator intent and complete-member section/offset edits."""
from dataclasses import replace
from uuid import uuid4
import numpy as np
import pytest
from anygeometry import EntityRef
from anyfem.application.controller import default_project
from anyfem import commands as cmd
from anyfem.geometry_edit import GeometryEditDraft
from anyfem.io.project_file import project_to_dict,project_from_dict
from anyfem.ui.scene import build_geometry_scene
from anyfem.document import canonical_hash


def project():
    p=default_project();base=p.beam_sections['stiffener']
    p.add_beam_section(replace(base,name='girder',id=str(uuid4()),web_height=.7,offset_mode='manual',eccentricity=.35))
    return p


def axes(p,feature):
    record=p.geometry.features.get(feature)
    owned={ref.id for ref in record.outputs.values() if ref.kind=='edge'}
    return {direction:{ref.id for ref in p.geometry.group(direction+'_stiffeners') if ref.kind=='edge'} & owned
            for direction in ('longitudinal','transverse')}


@pytest.mark.parametrize('rotated',[False,True])
def test_directional_sections_follow_owner_groups_and_roundtrip(rotated):
    p=project()
    frame={'u_direction':(0,1,0),'v_direction':(-1,0,0)} if rotated else {}
    feature=cmd.AddStiffenedPanel(5,4,1,2,longitudinal_beam_section='stiffener',transverse_beam_section='girder',**frame).do(p).feature_id
    groups=axes(p,feature)
    assert groups['longitudinal'] and groups['transverse']
    assert {p.edge_sections[e] for e in groups['longitudinal']}=={'stiffener'}
    assert {p.edge_sections[e] for e in groups['transverse']}=={'girder'}
    restored=project_from_dict(project_to_dict(p))
    assert restored.edge_sections==p.edge_sections
    assert restored.generator_properties==p.generator_properties
    assert restored.beam_offsets==p.beam_offsets


def test_legacy_default_and_directional_edit_preserve_explicit_override_and_undo():
    p=project();feature=cmd.AddStiffenedPanel(5,4,1,2,beam_section='stiffener').do(p).feature_id
    assert set(p.edge_sections.values())=={'stiffener'}
    edge=min(axes(p,feature)['transverse'])
    override=replace(p.beam_sections['girder'],name='override',id=str(uuid4()))
    p.add_beam_section(override);cmd.AssignBeam(edge,'override').do(p)
    slot=next(key for key,ref in p.geometry.features.get(feature).outputs.items() if ref==EntityRef('edge',edge))
    before=canonical_hash(project_to_dict(p))
    command=cmd.EditFeature(feature,engineering={'beam_section':'stiffener','transverse_beam_section':'girder'})
    command.do(p)
    record=p.geometry.features.get(feature);overridden=record.outputs[slot].id
    assert p.edge_sections[overridden]=='override'
    assert {p.edge_sections[e] for e in axes(p,feature)['transverse'] if e!=overridden}=={'girder'}
    after=canonical_hash(project_to_dict(p))
    command.undo(p);assert canonical_hash(project_to_dict(p))==before
    command.redo(p);assert canonical_hash(project_to_dict(p))==after
    p.regenerate_geometry_features()
    assert p.edge_sections[p.geometry.features.get(feature).outputs[slot].id]=='override'


@pytest.mark.parametrize('options',[{'transverse_beam_section':'missing'}, {'longitudinal_beam_section':'missing'}])
def test_invalid_directional_section_is_atomic(options):
    p=project();before=canonical_hash(project_to_dict(p))
    with pytest.raises(ValueError):cmd.AddStiffenedPanel(5,4,1,2,**options).do(p)
    assert canonical_hash(project_to_dict(p))==before


def test_directional_material_variants_do_not_collide():
    from anyfem import steel
    p=project();p.add_material(steel('S275'))
    feature=cmd.AddStiffenedPanel(5,4,1,2,longitudinal_beam_section='stiffener',transverse_beam_section='girder',material='S275').do(p).feature_id
    groups=axes(p,feature)
    long=p.beam_section_of(min(groups['longitudinal']));trans=p.beam_section_of(min(groups['transverse']))
    assert long.id!=trans.id and long.material==trans.material=='S275'
    assert long.web_height!=trans.web_height


def test_geometry_regeneration_keeps_directional_sections_and_clear_keeps_override():
    p=project();feature=cmd.AddStiffenedPanel(5,4,1,2,beam_section='stiffener',transverse_beam_section='girder').do(p).feature_id
    record=p.geometry.features.get(feature)
    cmd.EditFeature(feature,parameters={**record.parameters,'origin':(0,0,1)}).do(p)
    groups=axes(p,feature)
    assert {p.edge_sections[e] for e in groups['longitudinal']}=={'stiffener'}
    assert {p.edge_sections[e] for e in groups['transverse']}=={'girder'}
    edge=min(groups['longitudinal']);cmd.AssignBeam(edge,'girder').do(p)
    cmd.EditFeature(feature,engineering={}).do(p)
    assert p.edge_sections=={edge:'girder'}


def test_transverse_section_requires_actual_transverse_members():
    p=project();before=canonical_hash(project_to_dict(p))
    with pytest.raises(ValueError,match='no transverse beams'):
        cmd.AddStiffenedPanel(5,4,1,transverse_beam_section='girder').do(p)
    assert canonical_hash(project_to_dict(p))==before


def test_full_member_offset_edit_updates_model_and_rendered_axis_and_undo():
    p=project();feature=cmd.AddStiffenedPanel(5,4,1,2,beam_section='stiffener',transverse_beam_section='girder').do(p).feature_id
    member=next(m for m in p.geometry.members.values() if m.name.startswith('longitudinal_'))
    refs=tuple(EntityRef('edge',p.geometry.member_edge_uses[use].edge_id) for use in member.edge_use_ids)
    assert len(refs)>1
    before=dict(p.beam_offsets)
    edit=GeometryEditDraft.capture(p,refs).command(beam={'section':'girder','offset_mode':'manual','eccentricity':.5})
    edit.do(p)
    for ref in refs:
        assert p.beam_section_of(ref.id).eccentricity==.5
        np.testing.assert_allclose(p.beam_offset_vector(ref.id),(0,0,.5))
        assert p.beam_offsets[ref.id]==.5
    transverse=axes(p,feature)['transverse']
    assert all(p.beam_offsets[e]==before[e] for e in transverse)
    scene=build_geometry_scene(p)
    lines={line.ref:line for line in scene.lines}
    for ref in refs:np.testing.assert_allclose(lines[ref].points[:,2],.5)
    restored=project_from_dict(project_to_dict(p));assert restored.beam_offsets==p.beam_offsets
    edit.undo(p);assert p.beam_offsets==before
    edit.redo(p);assert all(p.beam_offsets[ref.id]==.5 for ref in refs)
