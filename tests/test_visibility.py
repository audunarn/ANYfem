"""Visibility changes draw/pick ownership without changing engineering payloads."""
import numpy as np
from anygeometry.entities import EntityRef
from anyfem.selection import MeshEntityRef
from anyfem.ui.scene import Scene, FacePatch, PointMarker, Polyline
from anyfem.presentation.visibility import SelectionVisibility


def test_collapsed_feature_can_hide_one_face_without_hiding_its_siblings():
    first,second=EntityRef('face',1),EntityRef('face',2)
    a=np.zeros((3,3));b=np.ones((3,3))
    patch=FacePatch(first,[a,b],['red','blue'],owners=(first,second),
                    polygon_visibility_owners=[(first,),(second,)])
    source=Scene(faces=[patch]);visibility=SelectionVisibility()
    visibility.set_hidden([first])
    result=visibility.filter_scene(source)
    assert len(result.faces)==1 and result.faces[0].ref==second
    assert result.faces[0].owners==(second,) and result.faces[0].colors==['blue']
    assert result.faces[0].polygons[0] is b
    assert patch.ref==first and patch.owners==(first,second) and len(patch.polygons)==2
    visibility.set_hidden([first],False)
    assert len(visibility.filter_scene(source).faces[0].polygons)==2


def test_mesh_polygon_and_geometry_ownership_are_filtered_together():
    face=EntityRef('face',1);a,b=MeshEntityRef('element',1),MeshEntityRef('element',2)
    patch=FacePatch(None,[np.zeros((3,3)),np.ones((3,3))],['red','blue'],
                    polygon_owners=[(face,a),(face,b)])
    source=Scene(faces=[patch],points=[PointMarker(MeshEntityRef('node',1),np.zeros(3))],
                 lines=[Polyline(EntityRef('edge',1),np.zeros((2,3)))])
    visibility=SelectionVisibility();visibility.set_hidden([a])
    result=visibility.filter_scene(source)
    assert result.faces[0].polygon_owners==[(face,b)]
    assert len(result.points)==len(result.lines)==1
    visibility.set_hidden([face,MeshEntityRef('node',1),EntityRef('edge',1)])
    result=visibility.filter_scene(source)
    assert not result.faces and not result.points and not result.lines
    assert len(source.faces[0].polygons)==2


def test_primary_marker_reference_can_hide_a_marker_with_other_pick_owners():
    node=MeshEntityRef('node',1);face=EntityRef('face',3)
    marker=PointMarker(node,np.zeros(3),owners=(face,))
    visibility=SelectionVisibility();visibility.set_hidden([node])
    assert not visibility.filter_scene(Scene(points=[marker])).points
