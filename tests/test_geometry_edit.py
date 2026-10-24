"""Guarded selected-only section variants and canonical controls."""
from dataclasses import replace
import pytest
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.geometry_edit import GeometryEditDraft,MIXED
from anyfem.mesh_sizes import MeshSizeProperty,SetMeshSizeProperty,AssignMeshSize
from anyfem.io.project_file import project_to_dict


def model(assigned=True):
    p=default_project();ids=[commands.AddPoint(*xy).do(p) for xy in ((0,0),(1,0),(1,1),(0,1))]
    face=commands.AddPlate(ids).do(p);edges=[use.edge for use in p.geometry.faces[face].loop]
    if assigned:
        commands.AssignPlate(face,'plate').do(p)
        for edge in edges:commands.AssignBeam(edge,'stiffener').do(p)
    return p,p.geometry.entity_ref('face',face),tuple(p.geometry.entity_ref('edge',e) for e in edges)


def test_optin_material_variants_reuse_and_noop():
    p,face,edges=model();registry=dict(p.beam_sections);draft=GeometryEditDraft.capture(p,edges[:2])
    command=draft.command(beam={'rotation_deg':30.});commands.CommandStack(p).run(command)
    chosen=p.edge_sections[edges[0].id];assert chosen==p.edge_sections[edges[1].id]
    assert p.edge_sections[edges[2].id]=='stiffener' and p.beam_sections['stiffener']==registry['stiffener']
    count=len(p.beam_sections);commands.CommandStack(p).run(GeometryEditDraft.capture(p,edges[2:3]).command(beam={'rotation_deg':30.}))
    assert p.edge_sections[edges[2].id]==chosen and len(p.beam_sections)==count
    assert GeometryEditDraft.capture(p,edges[:2]).command(beam={'rotation_deg':30.}) is None


def test_unassigned_requires_base_and_manual_eccentricity_is_explicit():
    p,face,edges=model(False);before=project_to_dict(p);draft=GeometryEditDraft.capture(p,edges[:1])
    with pytest.raises(ValueError,match='base section'):draft.command(beam={'rotation_deg':30.})
    assert project_to_dict(p)==before
    with pytest.raises(ValueError,match='manual'):draft.command(beam={'section':'stiffener','offset_mode':'automatic','eccentricity':.02})
    commands.CommandStack(p).run(draft.command(beam={'section':'stiffener','rotation_deg':30.}))
    assert p.beam_section_of(edges[0].id).rotation_deg==30.


def test_parallel_direction_refused_before_mutation():
    p,face,edges=model();draft=GeometryEditDraft.capture(p,edges[:1]);before=project_to_dict(p)
    start=p.geometry.vertex_position(p.geometry.edges[edges[0].id].start);end=p.geometry.vertex_position(p.geometry.edges[edges[0].id].end)
    with pytest.raises(ValueError,match='parallel'):draft.command(beam={'web_direction':tuple(end-start)})
    assert project_to_dict(p)==before


def test_existing_named_assignment_changes_invalidate_draft():
    p,face,edges=model();a=MeshSizeProperty('Fine',.1);b=MeshSizeProperty('Coarse',.2)
    for item in (a,b):SetMeshSizeProperty(item).do(p)
    AssignMeshSize(a.id,edges[:1]).do(p);draft=GeometryEditDraft.capture(p,edges[:1])
    AssignMeshSize(b.id,edges[:1]).do(p)
    with pytest.raises(ValueError,match='changed'):draft.command(beam={'rotation_deg':30.})


def test_named_mixed_section_and_vector_values_have_distinct_sentinel():
    p,face,edges=model();section=replace(p.beam_sections['stiffener'],name='Mixed',web_direction=[0,0,1],id='mixed-section')
    commands.AddBeamSection(section).do(p);commands.AssignBeam(edges[0].id,section.name).do(p)
    draft=GeometryEditDraft.capture(p,edges[:1]);assert draft.shared('beam','section')=='Mixed' and draft.shared('beam','section') is not MIXED
    assert draft.shared('beam','web_direction')==(0.,0.,1.)
    assert GeometryEditDraft.capture(p,edges[:2]).shared('beam','section') is MIXED


def test_numeric_named_size_retains_policy_and_same_value_is_noop():
    p,face,edges=model();definition=MeshSizeProperty('Policy',.15,radius=.3,growth=1.8);SetMeshSizeProperty(definition).do(p);AssignMeshSize(definition.id,edges[:2]).do(p)
    draft=GeometryEditDraft.capture(p,edges[:1]);assert draft.shared('beam','mesh_size')==.15
    assert draft.command(beam={'mesh_size':.15}) is None
    commands.CommandStack(p).run(draft.command(beam={'mesh_size':.2}))
    selected=[item for item in p.refinements if item.ref==edges[0]][0];other=[item for item in p.refinements if item.ref==edges[1]][0]
    assert (selected.size,selected.radius,selected.growth)==(.2,.3,1.8)
    assert other.property_id==definition.id and p.mesh_size_properties[definition.id]==definition


def test_legacy_direct_control_shown_and_changed_target_refused():
    from anyfem.mesh.refinement import Refinement
    p,face,edges=model();legacy=Refinement(.17,ref=edges[0],radius=.4,growth=2.,name='Generator target');commands.AddRefinement(legacy).do(p)
    draft=GeometryEditDraft.capture(p,edges[:1]);assert draft.shared('beam','mesh_size')==.17
    assert draft.command(beam={'mesh_size':.17}) is None
    before=project_to_dict(p)
    with pytest.raises(ValueError,match='legacy/generator'):draft.command(beam={'mesh_size':.2})
    assert project_to_dict(p)==before


def test_mesh_resolver_runs_once_per_control_not_per_shared_field(monkeypatch):
    p,face,edges=model();definition=MeshSizeProperty('Policy',.15);SetMeshSizeProperty(definition).do(p);AssignMeshSize(definition.id,edges[:2]).do(p)
    calls=[];original=p.resolve_geometry_attachment
    def counted(ref):calls.append(ref);return original(ref)
    monkeypatch.setattr(p,'resolve_geometry_attachment',counted)
    draft=GeometryEditDraft.capture(p,edges[:2]);initial=len(calls)
    for key in ('mesh_size','mesh_size_id','rotation_deg','material'):draft.shared('beam',key)
    assert len(calls)==initial==2
