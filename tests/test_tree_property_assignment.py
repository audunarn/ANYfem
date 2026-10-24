from dataclasses import replace
import pytest
from anygeometry import EntityRef
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.property_assignment import SelectionAssignment
from anyfem.mesh.refinement import Refinement
from anyfem.io.project_file import project_to_dict,project_from_dict


def geometry(project):
    a=project.geometry.add_point(0,0,0);b=project.geometry.add_point(1,0,0);c=project.geometry.add_point(1,1,0);d=project.geometry.add_point(0,1,0)
    e=project.geometry.add_line(a,b);f=project.geometry.add_face((e,project.geometry.add_line(b,c),project.geometry.add_line(c,d),project.geometry.add_line(d,a)))
    second=project.geometry.add_line(project.geometry.add_point(2,0,0),project.geometry.add_point(3,0,0))
    return EntityRef('face',f),EntityRef('edge',e),EntityRef('edge',second),EntityRef('vertex',a)


def test_mixed_compatible_section_selection_atomic_undo_redo_reopen():
    project=default_project();face,edge,second,point=geometry(project);stack=commands.CommandStack(project)
    section=project.beam_sections['stiffener'];scope=SelectionAssignment.capture(project,'beam_sections',section.id,(face,edge,second,point))
    assert scope.references==(edge,second)
    stack.run(scope.command(project));assert set(project.edge_sections)=={edge.id,second.id} and not project.face_sections
    assert len(stack.history())==1
    stack.undo();assert not project.edge_sections and not project.geometry.members
    stack.redo();assert len(project.edge_sections)==2
    restored=project_from_dict(project_to_dict(project));assert restored.edge_sections==project.edge_sections


def test_mesh_size_clone_preserves_policy_and_equal_source_on_undo():
    project=default_project();face,edge,second,point=geometry(project)
    source=Refinement(size=.2,radius=.1,ref=edge,growth=1.8,name='Fine');project.add_refinement(source)
    scope=SelectionAssignment.capture(project,'refinements',id(source),(edge,point,face))
    stack=commands.CommandStack(project);stack.run(scope.command(project))
    assert len(project.refinements)==4 and project.refinements[0] is source
    assert all((r.size,r.radius,r.growth,r.name)==(.2,.1,1.8,'Fine') for r in project.refinements)
    stack.undo();assert project.refinements==[source] and project.refinements[0] is source
    stack.redo();assert len(project.refinements)==4
    loaded=project_from_dict(project_to_dict(project));assert len(loaded.refinements)==4


def test_center_policy_retargets_geometry_and_direct_refinement_undo_identity():
    project=default_project();face,edge,second,point=geometry(project)
    source=Refinement(size=.1,center=(0,0,0),radius=.4,growth=2,name='Around point');project.add_refinement(source)
    command=SelectionAssignment.capture(project,'refinements',id(source),(point,)).command(project);command.do(project)
    assert project.refinements[-1].center is None and project.refinements[-1].ref==point
    equal=replace(project.refinements[-1]);project.refinements.append(equal)
    original=project.refinements[-2];child=commands.AddRefinement(replace(original));child.do(project);child.undo(project)
    assert any(item is original for item in project.refinements) and any(item is equal for item in project.refinements)


def test_stale_context_readonly_and_no_compatible_refuse():
    project=default_project();face,edge,second,point=geometry(project);section=project.plate_sections['plate']
    scope=SelectionAssignment.capture(project,'plate_sections',section.id,(face,))
    with pytest.raises(PermissionError):scope.command(project,read_only=True)
    with pytest.raises(ValueError,match='changed'):scope.command(default_project())
    project.plate_sections['plate']=replace(section,thickness=.02)
    with pytest.raises(ValueError,match='changed'):scope.command(project)
    empty=SelectionAssignment.capture(project,'plate_sections',section.id,(edge,point))
    with pytest.raises(ValueError,match='Select'):empty.command(project)


def test_child_failure_rolls_back_property_and_structural_ownership(monkeypatch):
    project=default_project();face,edge,second,point=geometry(project);section=project.beam_sections['stiffener']
    request=SelectionAssignment.capture(project,'beam_sections',section.id,(edge,second));command=request.command(project)
    original=commands.AssignBeam.do
    def failure(self,project):
        original(self,project)
        if self.edge_id==second.id:raise ValueError('simulated assignment failure')
    monkeypatch.setattr(commands.AssignBeam,'do',failure)
    with pytest.raises(ValueError,match='simulated'):command.do(project)
    assert not project.edge_sections and not project.geometry.members and not project.section_assignments


def test_atomic_property_command_transcript_replays():
    from anyfem.command_recording import command_to_python
    from anyfem.document import DocumentSession
    from anyfem.scripting import ScriptRunner
    project=default_project();face,edge,second,point=geometry(project);section=project.beam_sections['stiffener']
    command=SelectionAssignment.capture(project,'beam_sections',section.id,(edge,second)).command(project)
    transcript=command_to_python(command);session=DocumentSession(project)
    with ScriptRunner(session) as runner:outcome=runner.run(transcript)
    assert outcome.committed and set(session.project.edge_sections)=={edge.id,second.id}
