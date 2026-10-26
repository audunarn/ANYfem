"""Worker-side retained frame preparation through canonical scene services."""
from dataclasses import dataclass,replace
from types import SimpleNamespace
import hashlib
import numpy as np
from .retained_results import prepare,element_id,_digest_value,scalar_values


@dataclass
class FrameLayout:
    schema: object
    topology: bytes
    batches: object
    edges: object
    source_mesh: object
    node_maps: object = None


def snapshot(viewport):
    """Copy display policy only; worker never reads Qt widgets or mutable canvas."""
    from .viewport import SceneViewport
    if (getattr(getattr(viewport,'_face_style',None),'__func__',None) is not SceneViewport._face_style
            or getattr(getattr(viewport,'_pick_binding',None),'__func__',None) is not SceneViewport._pick_binding):return None
    from types import MethodType
    view=SimpleNamespace(_visualization=viewport.visualization,visualization=viewport.visualization,
        _commercial_selection=viewport._commercial_selection,_selection_api=dict(viewport._selection_api or {}))
    view._face_style=MethodType(SceneViewport._face_style,view)
    view._pick_binding=MethodType(SceneViewport._pick_binding,view)
    return view


def _collect(view,scene):
    remaining=scene.copy();remaining.result_field=remaining.result_mesh=None
    remaining.faces=[];remaining.lines=[];groups={}
    for patch in scene.faces:
        if not patch.polygons or any(element_id(patch.owners_for_polygon(i)) is None for i in range(len(patch.polygons))):
            remaining.faces.append(patch);continue
        key=('face',)+view._face_style(patch)
        items=groups.setdefault(key,[])
        for index,points in enumerate(patch.polygons):
            owners=patch.owners_for_polygon(index)
            items.append((np.asarray(points),element_id(owners),owners,None if patch.polygon_node_ids is None else patch.polygon_node_ids[index]))
    for line in scene.lines:
        owners=line.pick_owners;eid=element_id(owners)
        if eid is None or line.annotation:remaining.lines.append(line);continue
        key=('line',int(line.width),bool(line.draw_overlay));items=groups.setdefault(key,[])
        points=np.asarray(line.points)
        for index,(first,last) in enumerate(zip(points,points[1:])):items.append((np.asarray([first,last]),eid,owners,None if line.point_node_ids is None else line.point_node_ids[index:index+2]))
    saved=remaining.legend;remaining.legend=None
    digest=hashlib.sha256();_digest_value(digest,remaining);remaining.legend=saved
    schema=(id(scene.result_mesh),view.visualization,view._commercial_selection,
            tuple((key,tuple((eid,tuple(owners),points.shape,nodes) for points,eid,owners,nodes in items)) for key,items in groups.items()),digest.digest())
    return groups,remaining,schema


def prepare_frame(view,scene,previous=None):
    if scene.result_field is None or scene.result_mesh is None:return None
    groups,remaining,schema=_collect(view,scene)
    if previous is None or schema!=previous.schema or len(groups)!=len(previous.batches):
        prepared=prepare(view,scene)
        if prepared is None:return None
        digest=hashlib.sha256();_digest_value(digest,schema)
        layout=FrameLayout(schema,digest.digest(),prepared[1],prepared[3],scene.result_mesh,prepared[4] if len(prepared)>4 else None)
    else:
        batches=[];edges=[];edge_index=0
        for batch_index,((key,items),(old,options)) in enumerate(zip(groups.items(),previous.batches)):
            positions=np.asarray(np.concatenate([points for points,eid,owners,nodes in items],axis=0),dtype=np.float64)
            values=scalar_values(scene.result_field,scene.result_mesh,old.element_ids)
            node_ids=None if previous.node_maps is None else previous.node_maps[batch_index]
            node_values=None if scene.result_field.per_element or node_ids is None else np.asarray([scene.result_field.node_values.get(node,np.nan) for node in node_ids],dtype=np.float32)
            mesh=replace(old,positions=positions,element_scalars=values if node_values is None else None,node_scalars=node_values)
            batches.append((mesh,dict(options,scalar_range=scene.result_scalar_range,colormap=scene.result_colormap)))
            if key[0]=='face':
                edges.append(replace(previous.edges[edge_index],positions=positions));edge_index+=1
        digest=hashlib.sha256(previous.topology)
        for mesh,options in batches:_digest_value(digest,mesh.positions)
        prepared=(digest.digest(),batches,remaining,edges,previous.node_maps)
        layout=FrameLayout(schema,previous.topology,batches,edges,scene.result_mesh,previous.node_maps)
    scene._prepared_result_batches=prepared
    scene._retained_topology_key=layout.topology
    scene._retained_frame_layout=layout
    return prepared
