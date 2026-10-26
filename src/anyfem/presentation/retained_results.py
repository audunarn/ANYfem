"""Retained display batches for the application's existing result scenes.

Nodal fields retain the historical, explicit element-mean display reduction.
The source Field remains unchanged for probes and export.
"""
import hashlib
import math
import struct
from dataclasses import fields, is_dataclass, replace
from uuid import UUID
import numpy as np
from ..ui.scene import _ramp_color as _REFERENCE_RAMP_COLOR, _blend as _REFERENCE_BLEND

_REFERENCE_RAMP_CODE = _REFERENCE_RAMP_COLOR.__code__
_REFERENCE_BLEND_CODE = _REFERENCE_BLEND.__code__
_REFERENCE_ARRAY, _REFERENCE_CLIP = np.array, np.clip


def _digest_value(digest, value):
    """Exact scene geometry/style bytes; never NumPy's abbreviated repr."""
    if isinstance(value, np.ndarray):
        digest.update(b'array'+value.dtype.str.encode()+str(value.shape).encode())
        digest.update(np.ascontiguousarray(value).tobytes())
    elif isinstance(value, UUID):digest.update(b'uuid:'+value.bytes)
    elif is_dataclass(value):
        digest.update(type(value).__qualname__.encode())
        for item in fields(value):
            if item.name in {'legend','result_field','result_mesh','result_colormap','result_scalar_range'}:continue
            digest.update(item.name.encode());_digest_value(digest,getattr(value,item.name))
    elif isinstance(value, dict):
        for key in sorted(value):_digest_value(digest,key);_digest_value(digest,value[key])
    elif isinstance(value, (tuple,list)):
        digest.update(b"sequence:"+str(len(value)).encode()+b"[")
        for item in value:_digest_value(digest,item)
        digest.update(b"]")
    elif isinstance(value,(float,np.floating)):digest.update(struct.pack('!d',float(value)))
    elif isinstance(value,(str,bytes,int,np.integer,bool,type(None))):digest.update(type(value).__name__.encode()+b":"+repr(value).encode()+b";")
    else:raise TypeError(f'unsupported retained scene identity: {type(value).__name__}')


def geometry_style_key(style):
    """Only controls that change result geometry belong in its workflow key."""
    return tuple((item.name, getattr(style,item.name)) for item in fields(style)
                 if item.name not in APPEARANCE_FIELDS)


APPEARANCE_FIELDS = frozenset({'background','render_mode','surface_opacity',
    'edge_color','edge_width','show_legend','legend_font_size','legend_width',
    'result_colormap','show_model_lines','antialiasing','lighting','background_gradient','feature_edges',
    'ambient_occlusion','ao_strength','ao_radius'})


def snapshot_appearance_field(field):
    """Freeze the displayed values, rather than cache a mutable field identity."""
    from types import MappingProxyType
    return replace(field, node_values=MappingProxyType(dict(field.node_values)),
                   element_values=MappingProxyType(dict(field.element_values)))


def element_id(owners):
    for owner in owners:
        if getattr(owner,"kind",None)=="element":return int(owner.id)
    return None


def _decoded_ramp(palette):
    """Call-local native RGB stops; unusual palettes retain scene conversion."""
    from ..ui import scene
    if (scene._ramp_color is not _REFERENCE_RAMP_COLOR or scene._blend is not _REFERENCE_BLEND
            or _REFERENCE_RAMP_COLOR.__code__ is not _REFERENCE_RAMP_CODE
            or _REFERENCE_BLEND.__code__ is not _REFERENCE_BLEND_CODE
            or scene.np is not np
            or np.array is not _REFERENCE_ARRAY or np.clip is not _REFERENCE_CLIP):return None
    if type(palette) is not tuple or not 2<=len(palette)<=32:return None
    stops=[]
    for stop in palette:
        if type(stop) is not tuple or len(stop)!=2:return None
        position,color=stop
        if type(position) is not float or not math.isfinite(position):return None
        if type(color) is not str or len(color)!=7 or color[0]!='#':return None
        if any(c not in '0123456789abcdefABCDEF' for c in color[1:]):return None
        if stops and position<=stops[-1][0]:return None
        stops.append((position,tuple(float(int(color[i:i+2],16)) for i in (1,3,5))))
    if stops[0][0]!=0. or stops[-1][0]!=1.:return None
    segments=[]
    for (low,start),(high,end) in zip(stops,stops[1:]):
        segments.append((low,high,high-low,start,tuple(b-a for a,b in zip(start,end))))
    return segments


def _decoded_color(position, segments):
    # Same float64 subtract/divide/multiply/add order as _ramp_color/_blend.
    value=min(max(float(position),0.),1.)
    for low,high,span,start,delta in segments:
        if value<=high:
            local=(value-low)/span
            channels=[int(min(max(a+d*local,0.),255.)) for a,d in zip(start,delta)]
            return '#{:02x}{:02x}{:02x}'.format(*channels)


def prepare(viewport, scene):
    from any3dview import MeshArrays, PackedOwnerTable
    field, source_mesh = scene.result_field, scene.result_mesh
    if field is None or source_mesh is None:
        return None
    def scalar(eid):
        if field.per_element:
            return field.element_values.get(eid,np.nan)
        nodes = source_mesh.shells[eid] if eid in source_mesh.shells else source_mesh.beams.get(eid,())
        values = [field.node_values.get(node,np.nan) for node in nodes]
        return float(np.mean(values)) if values and np.isfinite(values).all() else np.nan
    groups = {}
    remaining = scene.copy()
    remaining.result_field = remaining.result_mesh = None
    remaining.faces, remaining.lines = [], []
    for patch in scene.faces:
        if not patch.polygons or any(element_id(patch.owners_for_polygon(i)) is None for i in range(len(patch.polygons))):
            remaining.faces.append(patch);continue
        style = viewport._face_style(patch)
        group = groups.setdefault(("face",)+style, [])
        for index, polygon in enumerate(patch.polygons):
            owners = patch.owners_for_polygon(index)
            node_ids=None if patch.polygon_node_ids is None else patch.polygon_node_ids[index]
            group.append((np.asarray(polygon), element_id(owners), viewport._pick_binding(owners,patch.tag),node_ids))
    for line in scene.lines:
        eid = element_id(line.pick_owners)
        if eid is None or line.annotation:
            remaining.lines.append(line);continue
        group = groups.setdefault(("line",int(line.width),bool(line.draw_overlay)), [])
        binding = viewport._pick_binding(line.pick_owners,line.tag)
        points = np.asarray(line.points)
        for index,(start,end) in enumerate(zip(points,points[1:])):
            node_ids=None if line.point_node_ids is None else line.point_node_ids[index:index+2]
            group.append((np.asarray([start,end]),eid,binding,node_ids))
    batches = []; edge_batches = []; node_maps=[]
    digest = hashlib.sha256()
    _digest_value(digest,viewport.visualization)
    for key, primitives in groups.items():
        # Preserve original geometry dtype/shape/bytes before mesh conversion.
        for points, eid, binding, nodes in primitives:_digest_value(digest, points)
        positions, indices, mappings, bindings, edges,vertex_nodes = [], [], [], [], [],[]
        complete_nodes=all(nodes is not None and len(nodes)==len(points) for points,eid,binding,nodes in primitives)
        ids = tuple(dict.fromkeys(eid for points,eid,binding,nodes in primitives))
        dense = {eid:i for i,eid in enumerate(ids)}
        for points,eid,binding,nodes in primitives:
            offset = len(positions);positions.extend(points)
            if complete_nodes:vertex_nodes.extend(nodes)
            if key[0]=="face":
                edges.extend((offset+i,offset+(i+1)%len(points)) for i in range(len(points)))
                for index in range(1,len(points)-1):
                    indices.append((offset,offset+index,offset+index+1));mappings.append(dense[eid]);bindings.append(binding)
            else:indices.append((offset,offset+1));mappings.append(dense[eid]);bindings.append(binding)
        positions = np.asarray(positions,dtype=np.float64).reshape(-1,3)
        indices = np.asarray(indices,dtype=np.uint32).reshape(-1,3 if key[0]=="face" else 2)
        mappings = np.asarray(mappings,dtype=np.uint32)
        values = np.asarray([scalar(eid) for eid in ids],dtype=np.float32)
        owners = PackedOwnerTable.from_owners(**{ "triangles" if key[0]=="face" else "lines":bindings})
        # Hash the complete deduplicated table, including document/key/priority
        # columns and every primitive mapping, rather than recursively hashing
        # the same binding once per triangulated primitive.
        _digest_value(digest,(key,ids,owners,positions,indices,mappings))
        # PickOwner.identity is application metadata outside the packed table.
        # Preserve its previous conservative identity when supplied by a custom
        # binding producer, including tuple/list owner sequences.
        if any(getattr(owner, 'identity', None) is not None
               for binding in bindings
               for owner in getattr(binding, 'owners',
                                    binding if isinstance(binding, (tuple, list)) else ())):
            # Custom identity metadata may differ even for equal packed owner
            # keys. Preserve every primitive/owner association in that case.
            _digest_value(digest, bindings)
        node_ids=tuple(vertex_nodes) if complete_nodes else None
        node_maps.append(node_ids)
        node_values=None if field.per_element or node_ids is None else np.asarray([field.node_values.get(node,np.nan) for node in node_ids],dtype=np.float32)
        _digest_value(digest,node_ids)
        options = dict(owners=owners,scalar_range=scene.result_scalar_range)
        options["colormap"] = scene.result_colormap
        if key[0]=="face":
            # Shells represent surfaces, not closed solids. Their selected
            # result field must remain visible from either viewing direction.
            options.update(outline=key[1],opacity=key[2],lit=key[3],width=viewport.visualization.edge_width,cull_backface=False)
            mesh = MeshArrays(positions,indices,triangle_to_element=mappings,element_ids=np.asarray(ids,dtype=np.uint64),element_scalars=values if node_values is None else None,node_scalars=node_values)
            edge_batches.append(MeshArrays(positions,np.empty((0,3),dtype=np.uint32),lines=edges))
        else:
            options.update(line_width=key[1],draw_overlay=key[2],layer=30)
            mesh = MeshArrays(positions,np.empty((0,3),dtype=np.uint32),lines=indices,line_to_element=mappings,element_ids=np.asarray(ids,dtype=np.uint64),element_scalars=values if node_values is None else None,node_scalars=node_values)
        batches.append((mesh,options))
    # Overlay/style changes require a redraw; component legends do not.
    saved_legend = remaining.legend;remaining.legend=None
    _digest_value(digest,remaining);remaining.legend=saved_legend
    return digest.digest(),batches,remaining,edge_batches,tuple(node_maps)


def scalar_values(field, source_mesh, ids):
    if field.per_element:
        return np.asarray([field.element_values.get(int(eid),np.nan) for eid in ids],dtype=np.float32)
    rows=[]
    shells,beams=source_mesh.shells,source_mesh.beams
    for eid in ids:
        eid=int(eid)
        nodes=shells[eid] if eid in shells else beams.get(eid,())
        values=[field.node_values.get(node,np.nan) for node in nodes]
        rows.append(float(np.mean(values)) if values and np.isfinite(values).all() else np.nan)
    return np.asarray(rows,dtype=np.float32)


def prepare_warm_field(field, scene, retained, *, limits=None, display_units='SI (m / Pa)', colormap=None, working_bytes=128*1024*1024, include_scalars=True):
    """Worker-side scalar conversion and software fallback colours."""
    import sys
    from anypost.errors import WorkingSetError
    from ..ui.scene import result_display_metadata, _ramp_color, COLOR_MESH_FILL, COLOR_BEAM
    count=sum(len(patch.polygons) for patch in scene.faces)+len(scene.lines)
    # Scalar outputs, temporary mean rows and retained colour strings/lists.
    estimate=count*128+sum(len(ids)*32 for ids in retained[2])+16384
    if estimate>working_bytes:raise WorkingSetError('retained display conversion exceeds working cap')
    # Share full-precision means across GPU batches and software primitives.
    # This is call-local: fields/connectivity may mutate between preparations.
    # Reserve conservative dict/key/value overhead within the existing estimate's
    # headroom. A tight cap keeps the original uncached conversion admissible.
    # Reserve decoded palette workspace before means/colors consume headroom.
    # At most 32 stops, including construction temporaries, fit this allowance.
    color_workspace=32768 if working_bytes-estimate>=32768 else 0
    cache_slots=max(0,(working_bytes-estimate-color_workspace-1024)//256)
    gpu_means={}
    cpu_means=gpu_means if retained[3] is scene.result_mesh else {}
    cpu_colors={}
    per_element=field.per_element
    def connectivity(mesh):
        # Mesh.shells rebuilds {**quads, **tris} on every access. Its exact
        # native facade permits equivalent live dictionary lookups without a
        # copied map. Keep custom mesh/property behavior on the original path.
        from anymesher import Mesh
        if type(mesh) is Mesh and all(type(value) is dict for value in (mesh.tris,mesh.quads,mesh.beams)):
            return mesh.tris,mesh.quads,mesh.beams
        return None
    gpu_connectivity=None if per_element else connectivity(retained[3])
    cpu_connectivity=gpu_connectivity
    if retained[3] is not scene.result_mesh and not per_element:
        cpu_connectivity=connectivity(scene.result_mesh)
    def element_mean(eid,mesh,means,native_connectivity):
        nonlocal cache_slots
        eid=int(eid)
        if eid in means:return means[eid]
        if native_connectivity is None:
            nodes=mesh.shells[eid] if eid in mesh.shells else mesh.beams.get(eid,())
        else:
            tris,quads,beams=native_connectivity
            nodes=tris[eid] if eid in tris else quads[eid] if eid in quads else beams.get(eid,())
        samples=[field.node_values.get(node,np.nan) for node in nodes]
        value=float(np.mean(samples)) if samples and np.isfinite(samples).all() else np.nan
        if cache_slots:
            means[eid]=value
            cache_slots-=1
        return value
    if not include_scalars:
        scalars=()
    elif per_element:
        scalars=tuple(scalar_values(field,retained[3],ids) for ids in retained[2])
    else:
        scalars=tuple(np.asarray([element_mean(eid,retained[3],gpu_means,gpu_connectivity) for eid in ids],dtype=np.float32) for ids in retained[2])
    range_,palette,legend=result_display_metadata(field,limits=limits,display_units=display_units,colormap=colormap)
    low,high=range_
    decoded=None
    if color_workspace and type(low) is float and type(high) is float:
        decoded=_decoded_ramp(palette)
    def color(eid,fallback):
        nonlocal cache_slots
        if eid in cpu_colors:
            cached=cpu_colors[eid]
            return fallback if cached is None else cached
        value=field.element_values.get(eid,np.nan) if per_element else element_mean(eid,scene.result_mesh,cpu_means,cpu_connectivity)
        # Keep software colours at full precision, independently of the float32
        # GPU scalars. None preserves each primitive's distinct missing colour.
        if np.isfinite(value):
            position=(float(value)-low)/(high-low)
            cached=(_decoded_color(position,decoded) if decoded is not None and math.isfinite(position)
                    else _ramp_color(position,palette))
        else:cached=None
        if cache_slots:
            cpu_colors[eid]=cached
            cache_slots-=1
        return fallback if cached is None else cached
    faces=[];lines=[]
    for patch in scene.faces:
        colors=list(patch.colors)
        for i in range(len(patch.polygons)):
            eid=element_id(patch.owners_for_polygon(i))
            if eid is not None:colors[i]=color(eid,COLOR_MESH_FILL)
        faces.append((len(faces),colors))
    for index,line in enumerate(scene.lines):
        eid=element_id(line.pick_owners)
        if eid is not None:lines.append((index,color(eid,COLOR_BEAM)))
    colors=(faces,lines)
    resident=sum(a.nbytes for a in scalars)+sum(sys.getsizeof(c)+sum(sys.getsizeof(v) for v in c) for p,c in faces)+sum(sys.getsizeof(c) for p,c in lines)
    node_maps=retained[4] if len(retained)>4 else (None,)*len(retained[1])
    nodes=tuple(None if not include_scalars or field.per_element or ids is None else np.asarray([field.node_values.get(int(node),np.nan) for node in ids],dtype=np.float32) for ids in node_maps)
    field._display_node_buffers=nodes
    resident+=sum(value.nbytes for value in nodes if value is not None)
    gpu_elements=tuple(None if nodes[index] is not None else values for index,values in enumerate(scalars))
    field._display_buffers=(retained[0],gpu_elements,colors)
    field._display_buffer_bytes=resident


def cpu_color_bytes(scene,updates=None):
    import sys
    seen=set();total=0
    def count(value):
        nonlocal total
        if id(value) not in seen:seen.add(id(value));total+=sys.getsizeof(value)
    faces,lines=updates if updates is not None else (tuple((i,p.colors) for i,p in enumerate(scene.faces)),tuple((i,p.color) for i,p in enumerate(scene.lines)))
    for i,colors in faces:
        count(colors)
        for color in colors:count(color)
    for i,color in lines:count(color)
    return total


def prepared_publication_bytes(field,scene,scalar_bytes):
    import sys
    prepared=getattr(field,'_display_buffers',None)
    # Frozen display maps share immutable scalar/key objects with the admitted
    # source Field, but own dictionary tables. Admit their resident storage
    # before viewport publication creates them, including small proxy overhead.
    snapshot_bytes=sum(sys.getsizeof(mapping) if type(mapping) is dict else 64+80*len(mapping)
                       for mapping in (field.node_values,field.element_values))+512
    return int(scalar_bytes)+cpu_color_bytes(scene,None if prepared is None else prepared[2])+snapshot_bytes


def discard_prepared_field(field):
    field.__dict__.pop('_display_buffers',None);field.__dict__.pop('_display_node_buffers',None);field.__dict__.pop('_display_buffer_bytes',None)


def scalar_buffer_bytes(mesh):
    return sum(array.nbytes for array in (getattr(mesh,'element_scalars',None),getattr(mesh,'node_scalars',None)) if array is not None)
