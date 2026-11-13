"""Source-free importer/authoring contracts; no licensed reference is bundled or needed."""
import numpy as np
import pytest
from anyfem.application.panel_hull_example import read_gdf,planar_support,_symmetry_fan_seeds


def write_box(path):
    panels=[[(0,-2,-4),(0,2,-4),(8,2,-4),(8,-2,-4)],
            [(0,-2,-4),(8,-2,-4),(8,-2,0),(0,-2,0)],
            [(0,2,-4),(0,2,0),(8,2,0),(8,2,-4)],
            [(0,-2,-4),(0,-2,0),(0,2,0),(0,2,-4)],
            [(8,-2,-4),(8,2,-4),(8,2,0),(8,-2,0)],
            [(0,-2,0),(8,-2,0),(8,-2,3),(0,-2,3)]]
    path.write_text('synthetic\n1 9.81\n0 0\n6\n'+'\n'.join(' '.join(map(str,p)) for f in panels for p in f))


def test_import_retains_exact_wet_facets_and_one_closed_waterline(tmp_path):
    file=tmp_path/'box.gdf';write_box(file)
    d=read_gdf(file)
    assert len(d['wet_faces'])==5 and len(d['waterline'])==4 and d['deck_z']==3.
    assert all(d['vertices'][i,2]==0 for i in d['waterline'])
    assert len(d['source']['sha256'])==64


def test_underwater_gap_is_refused(tmp_path):
    file=tmp_path/'box.gdf';write_box(file)
    text=file.read_text().splitlines();text[3]='5';file.write_text('\n'.join(text[:4]+text[8:]))
    with pytest.raises(ValueError,match='open below'):
        read_gdf(file)


@pytest.mark.parametrize('points',[
    [(0,0,0),(2,0,0),(3,1,0),(0,1,0)],
    [(0,0,0),(2,1,1),(-1,2,2)],
])
def test_bounded_plane_covers_all_authored_polygon_vertices(points):
    points=np.array(points,dtype=float);plane=planar_support(points)
    for point in points:
        uv=np.array(plane.local_uv(point))
        assert (uv>=-1e-14).all() and (uv<=1+1e-14).all()
        np.testing.assert_allclose(plane.evaluate(*np.clip(uv,0,1)),point,atol=1e-13,rtol=0)


def test_warped_symmetry_cusp_does_not_create_a_false_internal_cap():
    panel=np.array([[(0,0,-2),(0,1,-1),(1,0,-.5),(1,0,-2)]],dtype=float)
    P=_symmetry_fan_seeds(panel,1)[0]
    Q=P[[0,3,2,1]]*[1,-1,1]
    original=[P[[0,1,2]],P[[0,2,3]]];mirrored=[Q[[0,1,2]],Q[[0,2,3]]]
    key=lambda tri:tuple(sorted(map(tuple,tri)))
    assert {key(tri*[1,-1,1]) for tri in original}=={key(tri) for tri in mirrored}
    assert all(np.any(tri[:,1]!=0) for tri in original+mirrored)
