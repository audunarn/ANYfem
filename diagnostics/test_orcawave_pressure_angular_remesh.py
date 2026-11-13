"""Retained independent angular-remesh campaign; explicit qualification only.

Original 9f8254f body/assertions; prospective 300 second ceiling is unallocated
and grants no execution authority. Routine pressure-manager coverage lives in
tests/test_orcawave_pressure.py with independent offset triangular sources.
"""
import json
import numpy as np
import pytest
from anyfem.model import WetSurface
from anyfem.application.orcawave_pressure import prepare_prescribed_pressure


def test_independently_faceted_curved_hull_pipeline():
    from anyfem import Project,steel
    from anygeometry import OrientedEdge
    from anygeometry.panel_pressure_projection import validate_panel_geometry
    from anyhydro.orcawave_pressure import ImportedPressureBank
    from anyloads.orcawave_spectrum import regular_realization
    def facets(count):
        angle=np.linspace(0,np.pi/2,count+1)
        low=np.c_[np.cos(angle),np.sin(angle),np.full(count+1,-1.)]
        high=low.copy();high[:,2]=-.2
        return [np.array([low[i],low[i+1],high[i+1],high[i]]) for i in range(count)]
    project=Project();project.add_material(steel('S355',.01));project.add_plate_section('plate',.01,'S355')
    g=project.geometry;vertices={};edges={}
    for k,polygon in enumerate(facets(3)):
        ids=[]
        for p in polygon:
            key=tuple(p)
            if key not in vertices:vertices[key]=g.add_point(*p)
            ids.append(vertices[key])
        loop=[]
        for a,b in zip(ids,ids[1:]+ids[:1]):
            key=tuple(sorted((a,b)))
            if key not in edges:edges[key]=g.add_line(a,b)
            edge=edges[key];loop.append(OrientedEdge(edge,g.edges[edge].start==a))
        face=g.add_face_from_loop(tuple(loop),(0,1,2,3));project.assign_plate(face,'plate')
        project.add_wet_surface(WetSurface(ref=project.face(face),fluid_side='positive_normal',name=f'facet{k}'))
    mesh=project.generate_mesh(1.)
    polygons,normals,centers,areas=validate_panel_geometry(facets(5))
    bank=ImportedPressureBank(np.asarray(polygons).reshape(-1,3),np.arange(20).reshape(5,4),centers,normals,areas,
        np.array([1.]),np.full((1,5),100+0j),tuple(range(5)),('body0',)*5,0.,'fixed_diffraction',0,20.,1025.,9.81,'{}')
    h=prepare_prescribed_pressure(project,mesh,bank,regular_realization(bank,omega=1.,height=2.),times=(0.,.5),
        surfaces=tuple(s.id for s in project.wet_surfaces),source_to_project=np.eye(4),
        limits=dict(gap_m=.05,max_angle=np.deg2rad(25),coverage_tolerance=1e-6,convergence_tolerance=.03,max_depth=8,max_triangles=100000),
        force_relative_limit=1e-6,moment_relative_limit=1e-6,force_absolute_limit=1e-8,moment_absolute_limit=1e-8)
    nodal=h.evaluate(0);xyz=np.array([mesh.nodes[n] for n in h.node_ids])
    assert nodal[:,:3].sum(axis=0)==pytest.approx([-80,-80,0],abs=1e-7)
    assert (np.cross(xyz,nodal[:,:3])+nodal[:,3:]).sum(axis=0)==pytest.approx([-48,48,0],abs=1e-7)
    assert json.loads(h.binding_json)['projection']['projected_targets']>0
