"""Planar polygon display follows its complete boundary in either orientation."""
import numpy as np
import pytest
from shapely.geometry import Polygon
from shapely.ops import unary_union
from anyfem import Project
from anyfem.ui.scene import face_display_polygons


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('offset,scale', [((0.,0.,0.),1.), ((13.,-8.,4.),1e-3), ((-1.,2.,5.),1e3)])
def test_five_point_concave_tilted_plane_respects_boundary(reverse, offset, scale):
    coords = np.array(((0,0),(4,0),(4,3),(2,1.5),(0,3)), dtype=float)
    if reverse:coords=coords[::-1]
    x = np.array((1.,0.,1.))/np.sqrt(2.)
    y = np.array((0.,1.,0.))
    origin = np.asarray(offset)
    points = origin + scale * (coords[:, :1] * x + coords[:, 1:] * y)
    geometry = Project().geometry
    ids = geometry.add_points(points)
    face = geometry.add_plate(ids)
    polygons = face_display_polygons(geometry, face)
    projected = [np.stack(((polygon-origin) @ x / scale, (polygon-origin) @ y / scale), axis=1) for polygon in polygons]
    expected = Polygon(coords)
    actual = unary_union([Polygon(polygon) for polygon in projected])
    assert actual.symmetric_difference(expected).area < 1e-8
    assert sum(Polygon(polygon).area for polygon in projected) == pytest.approx(expected.area)
    normal = np.cross(x,y) * (-1 if reverse else 1)
    assert all(np.dot(np.cross(p[1]-p[0],p[2]-p[0]),normal) > 0 for p in polygons)


def test_five_point_boundary_with_collinear_intermediate_point():
    coords = np.array(((0,0),(1,0),(2,0),(2,2),(0,2)), dtype=float)
    geometry = Project().geometry
    ids = geometry.add_points([(*p,0.) for p in coords])
    face = geometry.add_plate(ids)
    polygons = face_display_polygons(geometry, face)
    expected = Polygon(coords)
    actual = unary_union([Polygon(polygon[:,:2]) for polygon in polygons])
    assert actual.symmetric_difference(expected).area < 1e-10
