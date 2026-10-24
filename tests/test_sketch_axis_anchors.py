"""Global axis datums are true finite intersections and hard fixed anchors."""
import numpy as np
import pytest

from anygeometry import SketchPlane, bind_references, plane_sketch_plane, solve_sketch
from anyfem.sketcher import FaceSketchTask
from anyfem import Project, commands as cmd
from anyfem.io.project_file import project_from_dict, project_to_dict


def task_at(origin=(0,0,0),normal=(0,0,1),reference=(1,0,0)):
    return FaceSketchTask(plane_sketch_plane(origin,normal,reference),.05)


@pytest.mark.parametrize('origin',[(0,0,0),(2,3,0)])
def test_principal_plane_has_single_shared_origin_not_three_overlapping_points(origin):
    task=task_at(origin)
    assert len(task.axis_references)==1 and not task.references
    key=next(iter(task.reference_points))
    assert task.reference_world(key)==pytest.approx((0,0,0))
    assert not task.definition(0,allow_incomplete=True).references


def test_offset_plane_has_only_true_z_intersection():
    task=task_at((2,3,5))
    assert set(task.axis_references)=={'global-axis-Z'}
    key=task.axis_references['global-axis-Z'].keys[0]
    assert task.reference_world(key)==pytest.approx((0,0,5))
    assert task.reference_points[key]==pytest.approx((-2,-3))


def test_oblique_intercepts_satisfy_axis_and_plane_equations():
    task=task_at((1,2,3),(1,2,3),(1,-1,0))
    assert len(task.axis_references)==3
    for index,axis in enumerate('XYZ'):
        key=task.axis_references['global-axis-'+axis].keys[0]
        world=task.reference_world(key)
        assert world[index]==pytest.approx(14/(index+1))
        assert np.delete(world,index)==pytest.approx((0,0),abs=1e-12)
        assert np.dot(world-np.asarray(task.plane.origin),task.plane.normal)==pytest.approx(0,abs=1e-12)


def test_distinct_axis_intercepts_are_not_merged_by_proximity():
    task=task_at((1e-12,1e-12,1e-12),(1,1,1),(1,-1,0))
    assert len(task.axis_references)==3
    for index,axis in enumerate('XYZ'):
        point=task.reference_world(task.axis_references['global-axis-'+axis].keys[0])
        expected=np.zeros(3);expected[index]=3e-12
        assert point==pytest.approx(expected,abs=1e-25)


def test_datum_can_pin_drawing_and_dimension_without_entering_contour():
    task=task_at((0,0,5));task.choose_tool('line')
    key=task.axis_references['global-axis-Z'].keys[0]
    task.add(None,reference_key=key);task.add((2,0,5))
    assert any(c.kind=='coincident' and key in (c.first,c.second) for c in task.constraints)
    assert all(key not in entity.points for entity in task.entities)
    task.add_distance(key,task.point_keys[1],3)
    definition=task.solve_preview()
    solved=solve_sketch(bind_references(definition),task.plane)
    assert solved[key]==pytest.approx((0,0))
    assert np.linalg.norm(np.asarray(solved[task.point_keys[1]])-solved[key])==pytest.approx(3)
    before=dict(task.reference_points)
    task.begin_drag();task.move_point(key,(8,8,5));task.end_drag()
    assert task.reference_points==before and 'fixed' in task.diagnostic.lower()
    with pytest.raises(ValueError,match='cannot be removed'):task.remove_reference('global-axis-Z')
    task.undo_local();assert task.reference_points==before
    task.redo_local();assert task.reference_points==before


def test_used_datum_persists_without_source_link_and_reopens_fixed():
    task=task_at();task.choose_tool('line')
    key=next(iter(task.reference_points))
    task.add(None,reference_key=key);task.add((1,0,0))
    definition=task.definition(0)
    project=Project();stack=cmd.CommandStack(project)
    record=stack.run(cmd.AddPlaneSketch(definition,(0,0,0),(0,0,1),(1,0,0)))
    assert not record.inputs and not record.dependencies
    reopened=project_from_dict(project_to_dict(project))
    feature=reopened.geometry.features.get(record.feature_id)
    assert not feature.parameters.get('reference_links')
    from anygeometry import SketchDefinition
    loaded=FaceSketchTask(task.plane,.05,SketchDefinition.from_parameters(feature.parameters))
    assert not loaded.references and key in loaded.reference_points
    assert loaded.reference_world(key)==pytest.approx((0,0,0))
    assert len(loaded.definition(0).references)==1
    loaded.cancel();assert loaded.reference_points[key]==pytest.approx((0,0))
