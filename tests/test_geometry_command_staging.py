"""The caller publishes disposable feature stages only after qualification."""
import pytest
from anygeometry import GeometryModel,GeometryError
from anyfem import Project,commands
from anyfem.document import DocumentSession
from anyfem.io.project_file import project_to_dict


def test_point_command_uses_one_stage_and_keeps_hashes_ids_and_undo(monkeypatch):
    project=Project("Staging");session=DocumentSession(project)
    counters=project.geometry.id_state();counters["vertex"]=100
    project.geometry.reserve_id_state(counters)
    before=project_to_dict(project);before_hash=session.revision.model_hash
    calls=[];original=GeometryModel.clone
    def clone(self,*args,**kwargs):calls.append(kwargs.get("include_features",True));return original(self,*args,**kwargs)
    monkeypatch.setattr(GeometryModel,"clone",clone)
    identifier=session.execute(commands.AddPoint(1,2,3))
    assert identifier==100 and calls==[True]
    after_hash=session.revision.model_hash
    assert after_hash!=before_hash
    assert session.undo()
    assert not project.geometry.vertices and project.geometry.id_state()["vertex"]>=101
    assert session.revision.model_hash==before_hash
    assert session.redo()
    assert tuple(project.geometry.vertex_position(identifier))==(1,2,3)
    assert session.revision.model_hash==after_hash


@pytest.mark.parametrize("late",[False,True])
def test_refused_stage_never_publishes_to_live_project(monkeypatch,late):
    import anyfem.geometry_features as owner
    import anyfem.model.project as project_module
    from anygeometry.features import builtin_feature_registry
    project=Project("Refused stage");session=DocumentSession(project)
    session.execute(commands.AddPoint(0,0,0))
    before=project_to_dict(project);revision=session.revision;events=[]
    project.geometry.add_change_hook(events.append)
    if late:
        original=project_module.reapply_sheet_join_intents
        prior_count=len(project.geometry.features.records)
        def refuse(geometry,*args,**kwargs):
            if len(geometry.features.records)>prior_count:
                raise GeometryError("late ownership refusal")
            return original(geometry,*args,**kwargs)
        monkeypatch.setattr(project_module,"reapply_sheet_join_intents",refuse)
    else:
        registry=builtin_feature_registry()
        def refuse(geometry,feature,inputs):
            geometry.add_point(50,0,0)
            raise GeometryError("executor refusal")
        # Match the declared neutral feature role when replacing its executor.
        registry.register("geometry.point",refuse,replace=True,topology_role=registry.topology_role("geometry.point"))
        monkeypatch.setattr(owner,"anyfem_feature_registry",lambda:registry)
    with pytest.raises(GeometryError,match="refusal"):
        session.execute(commands.AddPoint(2,0,0))
    assert project_to_dict(project)==before and session.revision is revision
    assert not events


def test_existing_feature_edit_uses_atomic_fallback(monkeypatch):
    project=Project();session=DocumentSession(project)
    feature=session.execute(commands.AddFeature("geometry.point",parameters={"position":[1,0,0]}))
    calls=[];original=GeometryModel.clone
    def clone(self,*args,**kwargs):calls.append(kwargs.get("include_features",True));return original(self,*args,**kwargs)
    monkeypatch.setattr(GeometryModel,"clone",clone)
    session.execute(commands.EditFeature(feature.feature_id,parameters={"position":[2,0,0]}))
    assert calls==[True,False]
    assert session.undo() and session.redo()


def test_cancelled_stage_preserves_live_project_revision_and_command_history(monkeypatch):
    import anyfem.geometry_features as owner
    from anygeometry.features import builtin_feature_registry
    class Cancelled(BaseException):pass
    project=Project();session=DocumentSession(project)
    session.execute(commands.AddPoint(0,0,0))
    before=project_to_dict(project);revision=session.revision
    done=list(session.commands._done);undone=list(session.commands._undone)
    registry=builtin_feature_registry()
    def cancel(geometry,feature,inputs):
        geometry.add_point(50,0,0)
        raise Cancelled()
    registry.register("geometry.point",cancel,replace=True,topology_role=registry.topology_role("geometry.point"))
    monkeypatch.setattr(owner,"anyfem_feature_registry",lambda:registry)
    with pytest.raises(Cancelled):session.execute(commands.AddPoint(2,0,0))
    assert project_to_dict(project)==before and session.revision is revision
    assert session.commands._done==done and session.commands._undone==undone
