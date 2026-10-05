"""Persistent sets and owner-backed displacement commands, without a GUI."""
import numpy as np
import pytest

from anyfem import Project, commands as cmd
from anyfem.io.project_file import load_project, save_project
from anyfem.model.regions import ManualRegion, Region, RegionDomain
from anygeometry.entities import EntityRef
from anygeometry.serialization import to_dict as geometry_to_dict


def square():
    project=Project(name="sets")
    stack=cmd.CommandStack(project)
    vertices=[stack.run(cmd.AddPoint(x,y)) for x,y in ((0,0),(1,0),(1,1),(0,1))]
    face=stack.run(cmd.AddPlate(vertices))
    edge=project.geometry.faces[face].loop[0].edge
    refs=(EntityRef("vertex",vertices[0]),EntityRef("edge",edge),EntityRef("face",face))
    return project,stack,vertices,refs


def test_set_roundtrip_edit_identity_and_undo(tmp_path):
    project,stack,vertices,refs=square()
    region=Region("Frame",RegionDomain.GEOMETRY,"mixed",ManualRegion(refs))
    stack.run(cmd.AddRegion(region));identifier=region.id
    stack.undo();assert identifier not in project.regions
    stack.redo();assert project.regions[identifier] is region
    stack.run(cmd.EditGeometrySet(identifier,name="Whole frame",references=refs+(EntityRef("vertex",vertices[1]),)))
    assert region.name=="Whole frame" and len(region.definition.anchors)==4
    stack.undo();assert region.name=="Frame" and region.definition.anchors==refs
    stack.redo()
    path=save_project(project,tmp_path/"sets.anyfem")
    reopened=load_project(path)
    assert reopened.regions[identifier].name=="Whole frame"
    assert reopened.regions.resolve(identifier,geometry=reopened.geometry)==region.definition.anchors


def test_invalid_set_edit_is_atomic():
    project,stack,_,refs=square()
    region=Region("Frame",RegionDomain.GEOMETRY,"mixed",ManualRegion(refs))
    stack.run(cmd.AddRegion(region))
    before=region.to_dict(),stack.history()
    for change in ({"name":" "},{"references":()},{"references":(EntityRef("edge",999999),)}):
        with pytest.raises(ValueError):stack.run(cmd.EditGeometrySet(region.id,**change))
        assert (region.to_dict(),stack.history())==before


def test_set_follows_split_lineage():
    project,stack,_,refs=square()
    original=refs[1]
    region=Region("Boundary",RegionDomain.GEOMETRY,"edge",ManualRegion((original,)))
    stack.run(cmd.AddRegion(region));stack.run(cmd.SplitEdge(original.id))
    resolved=project.regions.resolve(region.id,geometry=project.geometry)
    assert len(resolved)==2 and all(ref.kind=="edge" for ref in resolved)
    assert set(resolved)==set(project.geometry.resolve_ref(original))
    stack.undo();assert project.regions.resolve(region.id,geometry=project.geometry)==(original,)


def test_move_mixed_closure_once_and_replay(tmp_path):
    project,stack,vertices,refs=square()
    before={key:np.array(project.geometry.vertex_position(key)) for key in vertices}
    move=cmd.MoveEntities(refs+(refs[0],),translation=(2,-3,4))
    stack.run(move)
    assert set(project.geometry.vertices)==set(vertices)
    for key in vertices:np.testing.assert_allclose(project.geometry.vertex_position(key),before[key]+(2,-3,4))
    feature=project.geometry.features.records[-1]
    assert feature.kind=="geometry.transform"
    stack.undo()
    for key in vertices:np.testing.assert_allclose(project.geometry.vertex_position(key),before[key])
    stack.redo()
    # A later feature rebuild must retain the displacement, not revert it.
    stack.run(cmd.AddPoint(10,10,10))
    for key in vertices:np.testing.assert_allclose(project.geometry.vertex_position(key),before[key]+(2,-3,4))
    reopened=load_project(save_project(project,tmp_path/"moved.anyfem"))
    for key in vertices:np.testing.assert_allclose(reopened.geometry.vertex_position(key),before[key]+(2,-3,4))


def test_move_generator_retains_attachments_when_parent_changes(tmp_path):
    from anyfem import steel
    from anyfem.model.attributes import Support
    project=Project();stack=cmd.CommandStack(project)
    project.add_material(steel("S355",.01))
    project.add_plate_section("Shell",thickness=.01,material="S355")
    parent=stack.run(cmd.AddFeature("generator.plate",parameters={"length":2,"width":1}))
    face=next(ref for ref in parent.outputs.values() if ref.kind=="face")
    point=EntityRef("vertex",next(iter(project.geometry.vertices)))
    support=stack.run(cmd.AddSupport(Support("Fixed",point,{"ux":0})))
    stack.run(cmd.AssignPlate(face.id,"Shell"))
    region=Region("Panel",RegionDomain.GEOMETRY,"face",ManualRegion((face,)))
    stack.run(cmd.AddRegion(region))
    stack.run(cmd.MoveEntities((face,),translation=(0,0,3)))
    parameters={**parent.parameters,"length":4}
    stack.run(cmd.EditFeature(parent.feature_id,parameters=parameters))
    assert max(project.geometry.vertex_position(key)[0] for key in project.geometry.vertices)==pytest.approx(4)
    assert all(project.geometry.vertex_position(key)[2]==pytest.approx(3) for key in project.geometry.vertices)
    current_face=project.geometry.resolve_ref(face)[0]
    current_point=project.geometry.resolve_ref(point)[0]
    assert project.face_sections[current_face.id]=="Shell"
    assert project.supports[0].id==support.id
    assert project.regions.resolve(project.supports[0].region.id,geometry=project.geometry,
        feature_resolver=lambda anchor:project.geometry.features.resolve(anchor,project.geometry))==(current_point,)
    assert project.regions.resolve(region.id,geometry=project.geometry)==(current_face,)
    reopened=load_project(save_project(project,tmp_path/"panel.anyfem"))
    assert reopened.face_sections[current_face.id]=="Shell" and reopened.supports[0].id==support.id


@pytest.mark.parametrize("vector",[(1,2),(float("nan"),0,0),(0,float("inf"),0)])
def test_bad_move_does_not_change_geometry_or_history(vector):
    project,stack,vertices,refs=square()
    before=geometry_to_dict(project.geometry),stack.history()
    with pytest.raises(ValueError):stack.run(cmd.MoveEntities(refs,translation=vector))
    assert (geometry_to_dict(project.geometry),stack.history())==before
