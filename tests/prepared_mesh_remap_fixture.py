"""Portable synthetic owner 6cffcdd 2-Sheet/16-T3 fixture; no native route.

Private mesher fixture builders are used only to construct live owner inputs.
The FEM consumer itself imports only the public receipt validator.
"""
from collections import defaultdict
import numpy as np
import anygeometry as owner
from anygeometry.entities import EntityRef
from anyfem import Project
from anyfem.prepared_reference_scope import (extract_prepared_project_closure,
    query_prepared_project_reference_scope)
from anymesher.mesh import Mesh
from anymesher.boundary import GlobalEdgeBoundaryRegistry
from anymesher.meshing_view import GeometryMeshingView
from anymesher._authored_component_binding import bind_authored_sheet_joint_component
from anymesher import query_prepared_current_component_associations


def size2_fixture(variant='size2'):
    from prepared_pair_owner_fixture import build_fixture
    captured={}
    def source_factory():
        source=owner.GeometryModel()
        source.add_point(-10,-10,-10)
        root=source.add_plate(source.add_points(((0,0,0),(4,0,0),(4,4,0),(0,4,0))))
        source.set_face_surface(root,owner.Plane((0,0,0),(1,0,0),(0,1,0)))
        cutter=source.add_plate(source.add_points(((3,-1,-1),(3,5,-1),(3,5,1),(3,-1,1))))
        source.add_sheet((root,),name='source root');source.add_sheet((cutter,),name='source cutter')
        project=Project(geometry=source)
        pressure=project.load_case().add_pressure(EntityRef('face',root),1200)
        handles=tuple(source.handle('face',key) for key in source.faces)+tuple(source.handle('sheet',key) for key in source.sheets)
        closure=extract_prepared_project_closure(project,handles)
        roots=tuple(closure.source_to_work[source.handle('face',key)].id for key in (root,cutter))
        captured.update(project=project,closure=closure,pressure=pressure)
        return closure.working_model,roots
    fixture=build_fixture(variant,source_factory=source_factory)
    geometry=fixture.geometry
    manifest=query_prepared_project_reference_scope(captured['project'],geometry,closure=captured['closure'])
    inputs=dict(project=captured['project'],prepared_geometry=geometry,mesh=fixture.staged.mesh,
        manifest=manifest,closure=captured['closure'],association_receipt=fixture.staged.current_receipt,
        association_component=fixture.component,association_registry=fixture.source_registry,
        cell_current_faces=dict(fixture.staged.cell_current_faces))
    return inputs,captured['pressure'],fixture


def reopened_child_fixture():
    """Fresh source design input, then NEW witnessed working preparation.

    Geometry data can be reopened; no old preparation/closure/receipt authority
    is reused. The loaded source contains authored child faces. Only the new
    extracted work model is prepared, with fresh live proof inputs.
    """
    from copy import deepcopy
    _,_,prior=size2_fixture()
    source=owner.from_dict(owner.to_dict(prior.geometry))
    project=Project(geometry=source)
    child=prior.component.current_face_ids[0]
    pressure=project.load_case().add_pressure(EntityRef('face',child),300)
    handles=tuple(source.handle('face',key) for key in source.faces)+tuple(source.handle('sheet',key) for key in source.sheets)
    closure=extract_prepared_project_closure(project,handles)
    work=closure.working_model
    mapped=lambda kind,key:closure.source_to_work[source.handle(kind,key)].id
    roots=tuple(mapped('face',key) for key in source.faces)
    owner.apply_intersections(work,owner.plan_intersections(work,roots,policy='connect'),policy='connect')
    boundary=tuple(owner.query_prepared_authored_boundary_correspondence(work,key) for key in roots)
    joint=mapped('edge',prior.component.joint_edge_id)
    component=bind_authored_sheet_joint_component(work,joint,boundary,roots)
    mesh=deepcopy(prior.staged.mesh)
    mesh.geometry_model_id=work.model_id;mesh.geometry_revision=work.revision
    for field,kind in [('elements_of_face','face'),('elements_of_sheet','sheet'),
                       ('nodes_of_edge','edge'),('node_of_vertex','vertex')]:
        setattr(mesh,field,{mapped(kind,key):value for key,value in getattr(mesh,field).items()})
    registry=GlobalEdgeBoundaryRegistry(GeometryMeshingView(work))
    for entry in prior.source_registry.entries():
        source_edge=source.edges[entry.key.edge_id]
        edge=mapped('edge',entry.key.edge_id);target=work.edges[edge]
        assert (target.start,target.end)==(mapped('vertex',source_edge.start),mapped('vertex',source_edge.end))
        registry.register(edge,entry.key.parameter,node_id=entry.node_id)
    cells={cell:mapped('face',face) for cell,face in prior.staged.cell_current_faces}
    receipt=query_prepared_current_component_associations(work,component,mesh,registry,cells)
    manifest=query_prepared_project_reference_scope(project,work,closure=closure)
    return dict(project=project,prepared_geometry=work,mesh=mesh,manifest=manifest,
        closure=closure,association_receipt=receipt,association_component=component,
        association_registry=registry,cell_current_faces=cells),pressure,prior


def staged_fixture():
    source=owner.GeometryModel()
    source.add_point(-10,-10,-10)  # Source/current vertex IDs deliberately differ.
    root=source.add_plate(source.add_points(((0,0,0),(4,0,0),(4,4,0),(0,4,0))))
    source.set_face_surface(root,owner.Plane((0,0,0),(1,0,0),(0,1,0)))
    cutter=source.add_plate(source.add_points(((3,-1,-1),(3,5,-1),(3,5,1),(3,-1,1))))
    source.add_sheet((root,),name='source root');source.add_sheet((cutter,),name='source cutter')
    project=Project(geometry=source)
    pressure=project.load_case().add_pressure(EntityRef('face',root),1200)
    handles=tuple(source.handle('face',key) for key in source.faces)+tuple(source.handle('sheet',key) for key in source.sheets)
    closure=extract_prepared_project_closure(project,handles)
    geometry=closure.working_model
    roots=tuple(closure.source_to_work[source.handle('face',key)].id for key in (root,cutter))
    owner.apply_intersections(geometry,owner.plan_intersections(geometry,roots,policy='connect'),policy='connect')
    boundaries=tuple(owner.query_prepared_authored_boundary_correspondence(geometry,key) for key in roots)
    edge=boundaries[0].interior_incidence[0][0]
    component=bind_authored_sheet_joint_component(geometry,edge,boundaries,roots)
    mesh=Mesh(geometry_model_id=geometry.model_id,geometry_revision=geometry.revision,
        nodes={key:np.asarray(geometry.vertex_position(key),dtype=float) for key in geometry.vertices})
    registry=GlobalEdgeBoundaryRegistry(GeometryMeshingView(geometry))
    edges={key for item in boundaries for loop in item.exterior_loops for _,_,parts in loop for key in parts}
    edges.update(key for item in boundaries for key,_ in item.interior_incidence)
    for key in sorted(edges):
        item=geometry.edges[key]
        registry.register(key,0.,node_id=item.start);registry.register(key,1.,node_id=item.end)
        mesh.nodes_of_edge[key]=[item.start,item.end]
    mesh.node_of_vertex={row['id']:row['id'] for row in component.owner_receipt.current_records['vertices']}
    cell_faces={};cell=100
    for face in component.current_face_ids:
        vertices=tuple(geometry.oriented_start_vertex(use) for use in geometry.faces[face].loop)
        assert len(vertices)==4
        mesh.elements_of_face[face]=[]
        for tri in ((vertices[0],vertices[1],vertices[2]),(vertices[0],vertices[2],vertices[3])):
            mesh.tris[cell]=tri;mesh.elements_of_face[face].append(cell);cell_faces[cell]=face;cell+=1
    uses={row['id']:row for row in component.owner_receipt.current_records['face_uses']}
    sheets=defaultdict(set)
    for sheet,_,_,current_uses in component.occurrence_correspondence:
        for use in current_uses:sheets[sheet].update(mesh.elements_of_face[uses[use]['face_id']])
    mesh.elements_of_sheet={sheet:sorted(cells) for sheet,cells in sheets.items()}
    mesh.declared_plate_junction_edges=tuple(sorted({(min(a,b),max(a,b)) for joint in component.owner_receipt.joint_edge_ids
        for a,b in zip(mesh.nodes_of_edge[joint],mesh.nodes_of_edge[joint][1:])}))
    receipt=query_prepared_current_component_associations(geometry,component,mesh,registry,cell_faces)
    manifest=query_prepared_project_reference_scope(project,geometry,closure=closure)
    return dict(project=project,prepared_geometry=geometry,mesh=mesh,manifest=manifest,
        closure=closure,association_receipt=receipt,association_component=component,
        association_registry=registry,cell_current_faces=cell_faces),pressure
