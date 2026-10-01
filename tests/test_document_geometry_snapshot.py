"""Transaction snapshots respect ANYgeometry's read-only owner stores."""

from __future__ import annotations

import pytest

from anyfem.document import DocumentSession
from anyfem.io import project_to_dict
from anyfem.model.project import Project


def test_failed_transaction_restores_through_public_codec_and_keeps_owner_stores_read_only() -> None:
    project = Project("codec rollback")
    project.geometry.add_point(0.0, 0.0, 0.0)
    session = DocumentSession(project)
    before = project_to_dict(project)

    with pytest.raises(RuntimeError, match="rollback"):
        with session.transaction("failing geometry edit"):
            project.geometry.add_point(1.0, 0.0, 0.0)
            raise RuntimeError("rollback")

    assert project_to_dict(project) == before
    assert session.commands.project is project
    with pytest.raises(TypeError):
        project.geometry.vertices[99] = object()  # type: ignore[index]


def test_refused_revision_restores_command_history_project_and_caches():
    from anygeometry import GeometryError
    from anyfem import commands as cmd
    from anyfem.model.imperfections import Imperfection
    project=Project();session=DocumentSession(project)
    feature=session.execute(cmd.AddFeature("generator.plate",parameters={"length":2,"width":1}))
    face=next(ref for ref in feature.outputs.values() if ref.kind=="face")
    imperfection=Imperfection(face,amplitude=.002)
    session.execute(cmd.AddImperfection(imperfection))
    session.execute(cmd.SuppressFeature(feature.feature_id))
    session.execute(cmd.SuppressFeature(feature.feature_id,False))
    live,=project.resolve_geometry_attachment(face)
    before=project_to_dict(project);revision=session.revision;history=session.commands.history();dirty=session.dirty
    marker=object();session.mesh_cache["mesh"]=marker;session.result_cache["result"]=marker
    with pytest.raises(GeometryError,match="references missing entity"):
        session.execute(cmd.DeleteEntity(live))
    assert project_to_dict(project)==before
    assert session.revision==revision and session.dirty==dirty
    assert session.commands.history()==history and session.commands.project is project
    assert session.mesh_cache=={"mesh":marker} and session.result_cache=={"result":marker}
    assert project.imperfections==[imperfection]
    assert project.resolve_geometry_attachment(face)==(live,)
    session.execute(cmd.AddPoint(5,6,7))
    assert session.revision.sequence==revision.sequence+1


@pytest.mark.parametrize("raw",["imperfection","refinement"])
def test_raw_binding_only_edit_changes_solver_identity_and_invalidates_mesh(raw):
    from anyfem import commands as cmd
    from anyfem.model.imperfections import Imperfection
    from anyfem.mesh.refinement import Refinement
    project=Project();stack=cmd.CommandStack(project)
    first=stack.run(cmd.AddFeature("generator.plate",parameters={"length":2,"width":1}))
    second=stack.run(cmd.AddFeature("generator.plate",parameters={"length":2,"width":1,"origin":(5,0,0)}))
    authored=next(ref for ref in first.outputs.values() if ref.kind=="face")
    other=next(ref for ref in second.outputs.values() if ref.kind=="face")
    other_region=project.singleton_region(other)
    if raw=="imperfection":project.imperfections.append(Imperfection(authored,amplitude=.002))
    else:project.refinements.append(Refinement(size=.1,ref=authored))
    session=DocumentSession(project)
    original=session.revision.model_hash;original_binding=project.geometry_attachment_regions[authored]
    snapshot=session.snapshot()
    session.mesh_cache["mesh"]=object()
    with session.transaction("rebind physical scope"):
        project.geometry_attachment_regions[authored]=other_region
    assert project.resolve_geometry_attachment(authored)==(other,)
    assert session.revision.model_hash!=original and not session.mesh_cache
    assert snapshot.thaw().resolve_geometry_attachment(authored)==(authored,)
    rebound=session.revision.model_hash
    with session.transaction("rename region"):
        project.regions[other_region.id].name="Display name"
    assert session.revision.model_hash==rebound
    with session.transaction("restore physical scope"):
        project.geometry_attachment_regions[authored]=original_binding
    assert session.revision.model_hash==original
