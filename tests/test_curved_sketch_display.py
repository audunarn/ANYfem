"""Display approximation refines curvature without redundant ruled rows."""
from types import SimpleNamespace
import numpy as np
from anyfem.ui.scene import _curved_sketch_display_polygons


def test_ruled_curve_display_has_bounded_chord_error_and_linear_v_strip():
    class Geometry:
        faces={1:SimpleNamespace(holes=(),loop=(1,2,3,4))}
        def evaluate_face_many(self,face,uv):
            uv=np.asarray(uv);u,v=uv.T
            return np.column_stack((u,4*u*(1-u),3*v))
    g=Geometry();polygons=_curved_sketch_display_polygons(g,1,8)
    assert 8<len(polygons)<100
    assert all(set(p[:,2])=={0.,3.} for p in polygons)
    for p in polygons:
        u=.5*(p[0,0]+p[1,0]);error=abs(4*u*(1-u)-.5*(p[0,1]+p[1,1]))
        assert error<.0015*np.sqrt(11.)
