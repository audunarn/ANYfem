import numpy as np
import pytest
from anyfem import Project,steel
from anyfem.model import WetSurface
from anygeometry import OrientedEdge
from anygeometry.floating_envelope import build_envelope
from anyfem.application import floating_prepare as fp,floating_source as fs
from anyfem.application.panel_hull_example import planar_support

PROFILE=dict(name='faceted_hull_v1',full_panel_limit=2500)


def hull():
    p=Project();p.add_material(steel());p.add_plate_section('P',.01,'S355')
    g=p.geometry;vertices={};edges={};wet=[];closing=[]
    def face(points,is_wet=True):
        ids=[]
        for point in points:
            if point not in vertices:vertices[point]=g.add_point(*point)
            ids.append(vertices[point])
        loop=[]
        for a,b in zip(ids,ids[1:]+ids[:1]):
            key=tuple(sorted((a,b)))
            if key not in edges:edges[key]=g.add_line(a,b)
            e=g.edges[edges[key]];loop.append(OrientedEdge(e.id,e.start==a))
        f=g.add_face_from_loop(loop,(0,1,2,3),surface=planar_support(points));p.assign_plate(f,'P')
        if is_wet:p.add_wet_surface(WetSurface(p.face(f)));wet.append(f)
        else:closing.append(f)
    face([(0,-1,-4),(0,1,-4),(4,1,-4),(4,-1,-4)])
    for low,high in ((-4,0),(0,3)):
        face([(0,-1,low),(4,-1,low),(4,-1,high),(0,-1,high)])
        face([(0,1,low),(0,1,high),(4,1,high),(4,1,low)])
        face([(0,-1,low),(0,-1,high),(0,1,high),(0,1,low)])
        face([(4,-1,low),(4,1,low),(4,1,high),(4,-1,high)])
    face([(0,-1,3),(4,-1,3),(4,1,3),(0,1,3)],False)
    return p,closing


def test_faceted_clearance_uses_closed_hull_deck_and_not_waterline_subdivision():
    p,closing=hull();pose=fs.MeanPose(np.eye(3),np.zeros(3))
    assert build_envelope(p.geometry,[s.ref.id for s in p.wet_surfaces]+closing)['volume']==pytest.approx(56.)
    assert fp._vertical_clearance(p,pose,closing_face_ids=closing)==3.
    with pytest.raises(fp.FloatingPrepareError,match='crosses the waterplane'):
        fp._vertical_clearance(p,pose)  # legacy barge admission is retained
    with pytest.raises(ValueError,match='closed'):
        fp._vertical_clearance(p,pose,closing_face_ids=())


def test_profile_validation_and_route_are_explicit():
    st=fp.FloatingSettings(components=(fp.FloatingComponent(1.,.1,0.,0.),),times=(0.,1.),water_depth=50.,source_profile=PROFILE)
    fp._check_components(st)
    assert fp._family_route(st)['source_capacity']=='faceted_hull_v1'
    with pytest.raises(fp.FloatingPrepareError):
        fp._check_components(fp.FloatingSettings(components=st.components,times=st.times,water_depth=50.,source_profile=[]))
