import pytest
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.load_assignment import LoadSelection,ApplyLoadAttributes
from anyfem.model.attributes import fixed
from anyfem.io.project_file import project_to_dict,project_from_dict


def project():
    p=default_project();a=commands.AddPoint(0,0).do(p);b=commands.AddPoint(1,0).do(p);edge=commands.AddLine(a,b).do(p)
    return p,(p.geometry.entity_ref('vertex',a),p.geometry.entity_ref('vertex',b),p.geometry.entity_ref('edge',edge))


def test_bulk_support_undo_redo_regions_and_existing_identity():
    p,refs=project();existing=p.add_support(fixed(refs[0]));region=p.regions[existing.region.id];section=p.beam_sections['stiffener'];stack=commands.CommandStack(p)
    made=stack.run(ApplyLoadAttributes(tuple(commands.AddSupport(fixed(ref)) for ref in refs[1:])))
    ids={r.id for r in made};assert len(p.supports)==3
    stack.undo();assert p.supports==[existing] and p.regions[region.id] is region and p.beam_sections['stiffener'] is section
    stack.redo();assert {r.id for r in p.supports[1:]}==ids
    loaded=project_from_dict(project_to_dict(p));assert {r.id for r in loaded.supports}=={r.id for r in p.supports}


def test_atomic_failure_after_new_region_and_case_restores_everything():
    p,refs=project();before=project_to_dict(p)
    class Fail(commands.AddPressure):
        def do(self,p):raise ValueError('injected failure')
    batch=ApplyLoadAttributes((commands.AddSupport(fixed(refs[0])),commands.AddPointLoad(refs[1],force=(1,0,0)),Fail(refs[0],0.)))
    with pytest.raises(ValueError,match='injected'):batch.do(p)
    assert project_to_dict(p)==before


def test_capture_filters_mixed_refs_and_refuses_stale_read_only_empty():
    p,refs=project();draft=LoadSelection.capture(p,refs,{'edge'})
    assert draft.refs==(refs[-1],)
    with pytest.raises(PermissionError):draft.validate(p,read_only=True)
    commands.AddPoint(2,0).do(p)
    with pytest.raises(ValueError,match='changed'):draft.validate(p)
    with pytest.raises(ValueError,match='Select'):LoadSelection.capture(p,refs,{'face'}).validate(p)


def test_bulk_command_recording_replays_canonical_children():
    from anyfem.command_recording import command_to_python
    p,refs=project();batch=ApplyLoadAttributes(tuple(commands.AddSupport(fixed(ref)) for ref in refs))
    source=command_to_python(batch);assert not source.startswith('#')
    replay,other=project()
    class Runner:
        def __getattr__(self,name):return getattr(commands,name)
        def run(self,command):return command.do(replay)
    # The transcript explicitly imports application-owned wrapper and records.
    exec(source,{'commands':Runner(),'np':__import__('numpy')})
    assert len(replay.supports)==3


def test_imported_group_selection_requires_current_explicit_authority_and_preserves_project():
    from types import SimpleNamespace
    import numpy as np
    from anygeometry import EntityRef
    from anymesher import Mesh

    p=default_project();p.mesh_only=True
    ref=EntityRef('face',10)
    mesh=Mesh(nodes={1:np.array([0.,0.,0.]),2:np.array([1.,0.,0.]),3:np.array([0.,1.,0.])},
              tris={1:(1,2,3)},elements_of_face={10:[1]})
    imported=SimpleNamespace(mesh=mesh,groups={'source group':ref})
    before=project_to_dict(p)
    draft=LoadSelection.capture(p,[ref],{'face'},imported=imported,mesh=mesh)
    draft.validate(p,imported=imported,mesh=mesh)
    with pytest.raises(ValueError,match='authority changed'):
        draft.validate(p,imported=imported,mesh=Mesh())
    with pytest.raises(ValueError,match='authority changed'):
        draft.validate(p)
    invalid=LoadSelection.capture(p,[EntityRef('face',99)],{'face'},imported=imported,mesh=mesh)
    with pytest.raises(ValueError,match='not a populated group'):
        invalid.validate(p,imported=imported,mesh=mesh)
    imported.groups['new group']=EntityRef('face',20)
    with pytest.raises(ValueError,match='authority changed'):
        draft.validate(p,imported=imported,mesh=mesh)
    assert project_to_dict(p)==before
