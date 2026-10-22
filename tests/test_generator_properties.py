"""Generator defaults remain application intent over owner feature outputs."""
from copy import deepcopy
from dataclasses import replace
import pytest
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.io.project_file import project_to_dict,project_from_dict
from anyfem.generator_properties import member_edges,properties


def panel(project,**options):return commands.AddStiffenedPanel(4,3,1,**options).do(project)


def test_create_undo_redo_roundtrip_and_real_refinement_policy():
    project=default_project();stack=commands.CommandStack(project)
    feature=stack.run(commands.AddStiffenedPanel(4,3,1,beam_section='stiffener',plate_thickness=.02,material='S355',mesh_size=.4))
    assert set(project.edge_sections)==member_edges(project.geometry,feature)
    assert len(project.face_sections)==3 and len(project.refinements)==5
    assert all(project.plate_sections[name].thickness==.02 for name in project.face_sections.values())
    assert all(r.size==.4 and project.resolve_geometry_attachment(r.ref) for r in project.refinements)
    assert stack.undo() and not project.generator_properties and not project.edge_sections and not project.refinements
    assert stack.redo() and len(project.edge_sections)==2
    loaded=project_from_dict(project_to_dict(project))
    assert loaded.generator_properties==project.generator_properties and loaded.edge_sections==project.edge_sections
    assert all(loaded.resolve_geometry_attachment(r.ref) for r in loaded.refinements)
    assert loaded.regenerate_geometry_features().success
    assert len(loaded.refinements)==5


@pytest.mark.parametrize('options',[{'plate_thickness':-1,'material':'S355'},{'beam_section':'missing'},{'plate_thickness':.01,'material':'missing'},{'mesh_size':float('nan')},{'mesh_size':True}])
def test_invalid_options_are_atomic(options):
    project=default_project();before=deepcopy(project_to_dict(project))
    with pytest.raises((ValueError,KeyError)):panel(project,**options)
    assert project_to_dict(project)==before


def test_override_edit_translation_delete_and_undo():
    project=default_project();stack=commands.CommandStack(project)
    feature=stack.run(commands.AddStiffenedPanel(4,3,1,beam_section='stiffener',plate_thickness=.02,material='S355',mesh_size=.4))
    original=project.beam_sections['stiffener'];project.beam_sections['custom']=replace(original,name='custom',id='custom')
    edge=next(iter(project.edge_sections));stack.run(commands.AssignBeam(edge,'custom'))
    moved=stack.run(commands.EditFeature(feature.feature_id,parameters={**feature.parameters,'origin':(1,0,0),'length':5}))
    assert 'custom' in project.edge_sections.values() and len(project.edge_sections)==2
    assert all(project.resolve_geometry_attachment(r.ref) for r in project.refinements)
    # Explicit overrides on deleted topology retain the existing refusal gate.
    stack.undo();stack.undo()
    stack.run(commands.DeleteFeature(feature.feature_id))
    assert not project.generator_properties and not project.section_assignments and not project.refinements
    stack.undo();assert project.generator_properties and project.section_assignments and project.refinements
    stack.redo();assert not project.generator_properties


def test_material_variant_does_not_mutate_library_section_and_collisions_safe():
    from anyfem import steel
    project=default_project();project.add_material(steel('S235',.01));original=project.beam_sections['stiffener']
    from uuid import uuid5,NAMESPACE_URL
    identity=str(uuid5(NAMESPACE_URL,f'{project.document_id}:generator:1:beam'))
    collisions={}
    for name in ('Generator 1 beam',f'Generator 1 beam {identity[:8]}-1'):
        collisions[name]=replace(original,name=name,id='foreign:'+name);project.beam_sections[name]=collisions[name]
    feature=panel(project,beam_section='stiffener',material='S235')
    assert all(project.beam_sections[name]==value for name,value in collisions.items())
    assert project.beam_sections['stiffener']==original
    assert all(project.beam_sections[name].material=='S235' for name in project.edge_sections.values())


@pytest.mark.parametrize('raw',[{'_managed':{'edge/1':{}}},{'_managed':[]},False,{'mesh_size':True}])
def test_invalid_persisted_schema_rejected(raw):
    with pytest.raises(ValueError):properties(raw)


def test_line_generator_rejects_plate_options():
    project=default_project()
    with pytest.raises(ValueError):commands.AddFeature('generator.girder',parameters={'length':2},engineering={'plate_thickness':.01,'material':'S355'}).do(project)


def test_slot_fill_on_existing_valid_owner_outputs_preserves_override():
    from anyfem.generator_properties import reapply_generator_properties
    project=default_project();feature=panel(project,beam_section='stiffener')
    edge=next(iter(project.edge_sections));project.beam_sections['custom']=replace(project.beam_sections['stiffener'],name='custom',id='custom');project.assign_beam(edge,'custom')
    # Remove one managed assignment to simulate a newly available owner slot.
    options=project.generator_properties[feature.feature_id];slot=next(k for k,v in options['_managed'].items() if project.section_assignments[v['id']].section_id!='custom')
    state=options['_managed'].pop(slot);project.section_assignments.pop(state['id']);project.resolve_section_assignments(strict=False)
    reapply_generator_properties(project)
    assert len(project.edge_sections)==2 and 'custom' in project.edge_sections.values()


def test_suppress_resume_preserves_defaults():
    project=default_project();stack=commands.CommandStack(project)
    feature=stack.run(commands.AddFeature('generator.stiffened_panel',parameters={'length':4,'width':3,'longitudinal_spacing':1},suppressed=True,engineering={'beam_section':'stiffener','plate_thickness':.02,'material':'S355','mesh_size':.4}))
    assert project.generator_properties and not project.edge_sections
    stack.run(commands.SuppressFeature(feature.feature_id,False));assert len(project.edge_sections)==2 and len(project.refinements)==5
    assert project_from_dict(project_to_dict(project)).generator_properties==project.generator_properties
    stack.undo();assert project.geometry.features.get(feature.feature_id).suppressed and project.generator_properties


def test_existing_structural_owner_suppression_refuses_atomically():
    from anygeometry import GeometryError
    for options in ({},{'beam_section':'stiffener','plate_section':'plate'}):
        project=default_project();feature=panel(project,**options);before=deepcopy(project_to_dict(project))
        with pytest.raises(GeometryError,match='lineage'):commands.SuppressFeature(feature.feature_id).do(project)
        assert project_to_dict(project)==before


@pytest.mark.parametrize('kind,parameters,options',[('plate',{'length':2,'width':1},{'plate_thickness':.01,'material':'S355'}),('bulkhead',{'length':2,'width':1},{'plate_section':'plate'}),('frame',{'length':2,'width':1},{'plate_section':'plate'}),('girder',{'length':2},{'beam_section':'stiffener'}),('stiffener',{'length':2},{'beam_section':'stiffener'}),('cylinder',{'radius':2,'height':2,'circumferential_segments':4,'ring_spacing':1},{'plate_section':'plate','beam_section':'stiffener'}),('cone',{'radius_start':2,'radius_end':1,'height':2,'circumferential_segments':4,'ring_spacing':1},{'plate_section':'plate','beam_section':'stiffener'})])
def test_each_generator_applicable_options(kind,parameters,options):
    project=default_project();feature=commands.AddFeature('generator.'+kind,parameters=parameters,engineering={**options,'mesh_size':.5}).do(project)
    assert project.generator_properties[feature.feature_id] and project.refinements
    assert project.edge_sections or project.face_sections


def test_unsupported_spacing_change_is_failure_preserving_owner_refusal():
    from anygeometry import GeometryError
    for options in ({},{'beam_section':'stiffener','plate_section':'plate'}):
        project=default_project();feature=panel(project,**options);before=deepcopy(project_to_dict(project))
        with pytest.raises(GeometryError,match='lineage'):
            commands.EditFeature(feature.feature_id,parameters={**feature.parameters,'longitudinal_spacing':.75}).do(project)
        assert project_to_dict(project)==before


def test_engineering_command_transcript_replays_properties():
    from anyfem.command_recording import command_to_python
    from anyfem.document import DocumentSession
    from anyfem.scripting import ScriptRunner
    command=commands.AddStiffenedPanel(4,3,1,beam_section='stiffener',plate_thickness=.02,material='S355',mesh_size=.4)
    text=command_to_python(command);assert 'engineering=' in text
    session=DocumentSession(default_project())
    with ScriptRunner(session) as runner:outcome=runner.run(text)
    assert outcome.committed and session.project.generator_properties
    assert len(session.project.edge_sections)==2 and len(session.project.refinements)==5
