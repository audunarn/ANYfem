"""Fixed sketch reference snapshots with optional, immediately-breaking source links."""
import numpy as np
import pytest

from anyfem import Project, commands as cmd
from anyfem.io.project_file import load_project, save_project
from anyfem.sketch_references import LINKS_KEY, capture_reference
from anygeometry import (
    SketchConstraint, SketchDefinition, SketchEntity, SketchReferenceError, SketchWire,
    plane_sketch_plane,
)
from anygeometry.entities import EntityRef

PLANE = ((0, 0, 0), (0, 0, 1), (1, 0, 0))


def test_generated_plate_boundaries_and_stiffener_edges_are_reference_sources():
    project=Project(name='panel references')
    stack=cmd.CommandStack(project)
    stack.run(cmd.AddStiffenedPanel(4,3,1,transverse_spacing=2))
    geometry=project.geometry
    # Generated stiffeners are member chains of real model edges; plate
    # boundaries use those same topology edges rather than display outlines.
    member_edges={geometry.member_edge_uses[use].edge_id
                  for member in geometry.members.values() for use in member.edge_use_ids}
    assert member_edges
    boundary_edges=set(geometry.edges)-member_edges
    assert boundary_edges
    plane=plane_sketch_plane(*PLANE)
    for edge_id in (next(iter(member_edges)),next(iter(boundary_edges))):
        snapshot=capture_reference(geometry,plane,EntityRef('edge',edge_id),'r1')
        assert snapshot.kind=='inplane' and len(snapshot.points)==2
        edge=geometry.edges[edge_id]
        assert snapshot.points==pytest.approx(np.asarray([
            geometry.vertex_position(edge.start)[:2],geometry.vertex_position(edge.end)[:2]]))


def build():
    project = Project(name="refs")
    stack = cmd.CommandStack(project)
    vertex = stack.run(cmd.AddPoint(0.25, 0.5, 0.0))
    source = EntityRef("vertex", vertex)
    reference = capture_reference(project.geometry, plane_sketch_plane(*PLANE), source, "r1")
    definition = SketchDefinition(
        points={"p": (1.0, 1.0), "q": (2.0, 1.0)},
        entities=(SketchEntity("e", "line", ("p", "q")),),
        wires=(SketchWire("w", ("e",)),),
        constraints=(SketchConstraint("coincident", "p", "ref:r1"),
                     SketchConstraint("distance", "p", "q", value=1.5)),
        version=2, references=(reference,),
    )
    feature = stack.run(cmd.AddPlaneSketch(definition, *PLANE, references={"r1": source}))
    return project, stack, vertex, source, definition, feature.feature_id


def record(project, feature_id):
    return project.geometry.features.get(feature_id)


def point(project, feature_id, key="p"):
    return np.asarray(project.geometry.vertex_position(record(project, feature_id).outputs[f"point/{key}"].id))


def assert_sketch_unchanged(project, feature_id):
    assert record(project, feature_id).state == "ok"
    assert point(project, feature_id) == pytest.approx((0.25, 0.5, 0.0))
    assert np.linalg.norm(point(project, feature_id, "q") - point(project, feature_id)) == pytest.approx(1.5)


def test_reference_is_snapshot_with_optional_link_and_no_dependency():
    project, _stack, _vertex, _source, _definition, fid = build()
    feature = record(project, fid)
    assert feature.inputs == {} and feature.dependencies == ()
    assert len(feature.parameters[LINKS_KEY]) == 1
    assert not any("ref" in key for key in feature.outputs)
    assert_sketch_unchanged(project, fid)


def test_source_move_breaks_link_keeps_fixed_geometry_and_undo_redo():
    project, stack, vertex, _source, _definition, fid = build()
    stack.run(cmd.MovePoint(vertex, 3.0, 3.0, 0.0))
    assert LINKS_KEY not in record(project, fid).parameters
    assert record(project, fid).parameters["references"][0]["points"] == [[0.25, 0.5]]
    assert_sketch_unchanged(project, fid)
    stack.undo()
    assert len(record(project, fid).parameters[LINKS_KEY]) == 1
    assert_sketch_unchanged(project, fid)
    stack.redo()
    assert LINKS_KEY not in record(project, fid).parameters
    assert_sketch_unchanged(project, fid)


def test_source_delete_breaks_link_and_undo_restores_it():
    project, stack, vertex, _source, _definition, fid = build()
    stack.run(cmd.DeleteEntity(EntityRef("vertex", vertex)))
    assert LINKS_KEY not in record(project, fid).parameters
    assert_sketch_unchanged(project, fid)
    stack.undo()
    assert len(record(project, fid).parameters[LINKS_KEY]) == 1


def test_unrelated_and_failed_commands_do_not_detach():
    project, stack, _vertex, _source, _definition, fid = build()
    stack.run(cmd.AddPoint(5.0, 5.0, 5.0))
    assert len(record(project, fid).parameters[LINKS_KEY]) == 1
    with pytest.raises(Exception):
        stack.run(cmd.MovePoint(987654, 1.0, 1.0, 0.0))
    assert len(record(project, fid).parameters[LINKS_KEY]) == 1


def test_saved_linked_and_detached_references_reopen_fixed(tmp_path):
    project, stack, vertex, _source, _definition, fid = build()
    linked = load_project(save_project(project, tmp_path / "linked.anyfem"))
    assert len(record(linked, fid).parameters[LINKS_KEY]) == 1
    stack.run(cmd.MovePoint(vertex, 3.0, 3.0, 0.0))
    detached = load_project(save_project(project, tmp_path / "detached.anyfem"))
    assert LINKS_KEY not in record(detached, fid).parameters
    assert_sketch_unchanged(detached, fid)


def source_feature(project, vertex):
    return project.geometry.features.get(
        next(r.feature_id for r in project.geometry.features.records if r.kind == "geometry.point"))


def assert_materialization_valid(project):
    assert not project._feature_materialization_problems()


def test_source_edit_feature_keeps_project_valid_and_detaches_through_undo_redo():
    # Authored source editing goes through EditFeature. Raw MovePoint keeps its
    # public vertex id and its pre-existing materialization-checksum behaviour.
    project, stack, vertex, _source, _definition, fid = build()
    source = source_feature(project, vertex)
    stack.run(cmd.EditFeature(
        source.feature_id, parameters={**source.parameters, "position": (1.0, 0.0, 0.0)}))
    assert_materialization_valid(project)
    assert tuple(source_feature(project, vertex).parameters["position"]) == (1.0, 0.0, 0.0)
    assert LINKS_KEY not in record(project, fid).parameters
    assert_sketch_unchanged(project, fid)
    stack.undo()
    assert_materialization_valid(project)
    assert len(record(project, fid).parameters[LINKS_KEY]) == 1
    stack.redo()
    assert_materialization_valid(project)
    assert_sketch_unchanged(project, fid)


def test_public_move_entities_and_delete_feature_sources_keep_sketch_usable():
    project, stack, vertex, source, _definition, fid = build()
    stack.run(cmd.MoveEntities([source], (1.0, 0.0, 0.0)))
    assert_materialization_valid(project)
    assert LINKS_KEY not in record(project, fid).parameters
    assert_sketch_unchanged(project, fid)
    project, stack, vertex, source, _definition, fid = build()
    stack.run(cmd.DeleteFeature(source_feature(project, vertex).feature_id))
    assert_materialization_valid(project)
    assert LINKS_KEY not in record(project, fid).parameters
    assert_sketch_unchanged(project, fid)
    stack.undo()
    assert_materialization_valid(project)
    assert len(record(project, fid).parameters[LINKS_KEY]) == 1


def stale_saved_file(tmp_path, change):
    project, stack, vertex, _source, _definition, fid = build()
    links = record(project, fid).parameters[LINKS_KEY]
    change(project, stack, vertex)
    project.geometry.features.set_metadata_parameter(fid, LINKS_KEY, links)  # old link metadata survives
    return fid, save_project(project, tmp_path / "stale.anyfem")


@pytest.mark.parametrize("change", [
    lambda project, stack, vertex: stack.run(cmd.MovePoint(vertex, 3.0, 3.0, 0.0)),
    lambda project, stack, vertex: stack.run(cmd.DeleteFeature(source_feature(project, vertex).feature_id)),
])
def test_loading_saved_project_with_stale_link_detaches_to_fixed_snapshot(tmp_path, change):
    fid, path = stale_saved_file(tmp_path, change)
    loaded = load_project(path)
    assert LINKS_KEY not in record(loaded, fid).parameters
    assert record(loaded, fid).parameters["references"][0]["points"] == [[0.25, 0.5]]
    assert_sketch_unchanged(loaded, fid)


def test_malformed_optional_links_do_not_block_detach_or_load():
    project, _stack, _vertex, _source, _definition, fid = build()
    project.geometry.features.set_metadata_parameter(fid, LINKS_KEY, [{"nonsense": 1}])
    from anyfem.sketch_references import detach_stale_links

    assert detach_stale_links(project) == (fid,)
    assert LINKS_KEY not in record(project, fid).parameters
    assert_sketch_unchanged(project, fid)


def test_clicking_reference_while_drawing_makes_coincident_sketch_point_not_reference_geometry():
    from anyfem.sketcher import FaceSketchTask, SketchController

    project = Project(name="draw")
    stack = cmd.CommandStack(project)
    vertex = stack.run(cmd.AddPoint(0.25, 0.5, 0.0))
    plane = plane_sketch_plane(*PLANE)
    task = FaceSketchTask(plane, 0.05)
    task.add_reference(capture_reference(project.geometry, plane, EntityRef("vertex", vertex), "r1"))
    controller = SketchController()
    controller.begin(task)
    controller.choose_tool("line")
    controller.point(existing_key="ref:r1")
    controller.point((1.0, 1.0, 0.0))
    assert len(task.entities) == 1 and not any("ref" in key for e in task.entities for key in e.points)
    assert any(c.kind == "coincident" and "ref:r1" in (c.first, c.second) for c in task.constraints)
    assert np.allclose(task.points[0], (0.25, 0.5, 0.0))
    # a fixed reference refuses dragging
    controller.choose_tool("select")
    assert task.move_point("ref:r1", (5.0, 5.0, 0.0)) is False


def test_explicit_refresh_relinks_to_new_geometry_only_on_request():
    project, stack, vertex, source, definition, fid = build()
    stack.run(cmd.MovePoint(vertex, 3.0, 3.0, 0.0))
    parameters = {**definition.to_parameters(), "plane": record(project, fid).parameters["plane"]}
    stack.run(cmd.EditFeature(fid, parameters=parameters, sketch_references={"r1": source}))
    assert len(record(project, fid).parameters[LINKS_KEY]) == 1
    assert_sketch_unchanged(project, fid)  # snapshot itself is not re-captured


def plate_project(points):
    project = Project(name="plate")
    stack = cmd.CommandStack(project)
    vertices = [stack.run(cmd.AddPoint(*item)) for item in points]
    return project, stack, stack.run(cmd.AddPlate(vertices))


def test_tilted_plate_declined_by_plane_plane_is_not_captured_as_coplanar():
    tilt = 1e-8
    project, _stack, face = plate_project(
        [(-1, -1, -tilt), (1, -1, -tilt), (1, 1, tilt), (-1, 1, tilt)])
    try:
        section = capture_reference(project.geometry, plane_sketch_plane(*PLANE), EntityRef("face", face), "r1")
    except SketchReferenceError as error:
        assert error.reason in ("no_intersection", "unsupported")
    else:  # plane_plane resolved the true crossing line: a real section, never a projected boundary
        assert section.kind == "section" and len(section.segments) == 1


def test_coplanar_plate_projects_its_boundary_and_off_plane_plate_is_refused():
    project, _stack, face = plate_project([(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)])
    section = capture_reference(project.geometry, plane_sketch_plane(*PLANE), EntityRef("face", face), "r1")
    assert section.kind == "section" and len(section.segments) == 4
    project, _stack, face = plate_project([(0, 0, 1), (2, 0, 1), (2, 1, 1), (0, 1, 1)])
    with pytest.raises(SketchReferenceError) as caught:
        capture_reference(project.geometry, plane_sketch_plane(*PLANE), EntityRef("face", face), "r1")
    assert caught.value.reason == "no_intersection"


def test_face_source_move_and_delete_clear_link_and_keep_snapshot():
    for change in (lambda stack, face, fid: stack.run(cmd.MoveEntities([face], (0.0, 1.0, 0.0))),
                   lambda stack, face, fid: stack.run(cmd.DeleteFeature(fid))):
        project, stack, face = plate_project([(0, -1, -1), (0, 1, -1), (0, 1, 1), (0, -1, 1)])
        face = EntityRef("face", face)
        plane = plane_sketch_plane(*PLANE)
        section = capture_reference(project.geometry, plane, face, "r1")
        definition = SketchDefinition(
            points={"p": (0.0, 0.0), "q": (0.0, 0.5)},
            entities=(SketchEntity("e", "line", ("p", "q")),), wires=(SketchWire("w", ("e",)),),
            constraints=(), version=2, references=(section,))
        sketch = stack.run(cmd.AddPlaneSketch(definition, *PLANE, references={"r1": face})).feature_id
        assert len(record(project, sketch).parameters[LINKS_KEY]) == 1
        before = record(project, sketch).parameters["references"]
        plate_feature = next(r.feature_id for r in project.geometry.features.records
                             if r.kind == "geometry.plate")
        change(stack, face, plate_feature)
        assert LINKS_KEY not in record(project, sketch).parameters
        assert record(project, sketch).parameters["references"] == before
        assert record(project, sketch).inputs == {} and record(project, sketch).dependencies == ()


def test_plate_sections_are_captured_and_off_plane_geometry_is_refused():
    project = Project(name="refusal")
    stack = cmd.CommandStack(project)
    vertices = [stack.run(cmd.AddPoint(x, 0.0, z)) for x, z in ((0, -1), (2, -1), (2, 1), (0, 1))]
    face = stack.run(cmd.AddPlate(vertices))
    plane = plane_sketch_plane(*PLANE)
    section = capture_reference(project.geometry, plane, EntityRef("face", face), "r1")
    assert section.kind == "section" and len(section.segments) == 1
    high = stack.run(cmd.AddPoint(0.0, 0.0, 1.0))
    with pytest.raises(SketchReferenceError) as caught:
        capture_reference(project.geometry, plane, EntityRef("vertex", high), "r2")
    assert caught.value.reason == "no_intersection"
