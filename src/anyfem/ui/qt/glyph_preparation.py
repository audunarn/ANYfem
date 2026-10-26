"""Bounded worker-only canonical glyph admission and batched display geometry."""
from math import prod
import numpy as np
from ...presentation.workspace import stable_sample_ids


def _cancel(event):
    if event.is_set():
        from anypost.errors import RequestCancelled
        raise RequestCancelled('glyph preparation cancelled')


def arrow_batch(starts,ends):
    """One non-pickable line mesh: shafts plus two deterministic head wings."""
    from any3dview import MeshArrays
    starts=np.asarray(starts,dtype=float).reshape(-1,3);ends=np.asarray(ends,dtype=float).reshape(-1,3)
    delta=ends-starts;length=np.linalg.norm(delta,axis=1)
    good=np.isfinite(starts).all(axis=1)&np.isfinite(ends).all(axis=1)&(length>0)
    starts,ends,delta,length=starts[good],ends[good],delta[good],length[good]
    if not len(length):return None
    unit=delta/length[:,None];seed=np.eye(3)[np.argmin(np.abs(unit),axis=1)]
    side=np.cross(unit,seed);side/=np.linalg.norm(side,axis=1)[:,None]
    back=ends-unit*length[:,None]*.20;wing=side*length[:,None]*.085
    segments=np.stack((np.stack((starts,ends),axis=1),np.stack((ends,back+wing),axis=1),np.stack((ends,back-wing),axis=1)),axis=1)
    positions=segments.reshape(-1,3);lines=np.arange(len(positions),dtype=np.uint32).reshape(-1,2)
    return MeshArrays(positions,np.empty((0,3),dtype=np.uint32),lines=lines)


def displayed_anchors(scene,kind,sample,cancel):
    """Use explicit displayed connectivity; never infer node identity by location."""
    from ...presentation.retained_results import element_id
    chosen=set(sample);positions={};ambiguous=set()
    for patch in scene.faces:
        _cancel(cancel)
        for index,polygon in enumerate(patch.polygons):
            if index%128==0:_cancel(cancel)
            if kind=='node' and patch.polygon_node_ids is not None:
                for identifier,point in zip(patch.polygon_node_ids[index],polygon):
                    identifier=int(identifier)
                    if identifier in chosen:
                        point=np.asarray(point,dtype=float)
                        if identifier in positions and not np.array_equal(positions[identifier],point):ambiguous.add(identifier)
                        positions[identifier]=point
            elif kind=='element':
                identifier=element_id(patch.owners_for_polygon(index))
                if identifier in chosen:
                    center=np.mean(polygon,axis=0)
                    if identifier in positions and not np.array_equal(positions[identifier],center):ambiguous.add(identifier)
                    positions[identifier]=center
    for line in scene.lines:
        _cancel(cancel)
        if kind=='node' and line.point_node_ids is not None:
            for identifier,point in zip(line.point_node_ids,line.points):
                identifier=int(identifier)
                if identifier in chosen:
                    point=np.asarray(point,dtype=float)
                    if identifier in positions and not np.array_equal(positions[identifier],point):ambiguous.add(identifier)
                    positions[identifier]=point
        elif kind=='element' and not line.annotation:
            identifier=element_id(line.pick_owners)
            if identifier in chosen:
                # Canonical displayed beam centerline resolves section-surface
                # ambiguity without guessing an engineering reference position.
                positions[identifier]=np.mean(line.points,axis=0);ambiguous.discard(identifier)
    if any(identifier not in positions or identifier in ambiguous for identifier in sample):
        raise ValueError('A canonical glyph sample has no unambiguous displayed position')
    return np.asarray([positions[identifier] for identifier in sample],dtype=float).reshape(-1,3)


def prepare_glyphs(dataset,mesh,scene,settings,cancel,working_bytes):
    """Read/derive only under the caller's existing serial-worker ceiling."""
    from ...ui.scene import _persisted_frame
    from anypost.errors import WorkingSetError
    _cancel(cancel)
    mode=settings['mode'];name='displacement' if mode=='vector' else settings['field']
    if name not in dataset.field_keys:raise ValueError(f'This artifact has no {name} glyph field')
    stored=dataset.field(name);descriptor=stored.descriptor;labels=tuple(descriptor.components)
    if mode=='vector':
        if descriptor.location!='node' or descriptor.basis!='global' or not {'ux','uy','uz'}<=set(labels):
            raise ValueError('Vector glyphs require canonical global nodal ux, uy, uz vectors')
        kind='node'
    else:
        if descriptor.basis!='global' or descriptor.unit!='Pa' or set(labels)!=set(('xx','yy','zz','xy','yz','xz')):
            raise ValueError('Principal glyphs require canonical global stress tensors xx, yy, zz, xy, yz, xz in Pa')
        kind='node' if descriptor.location=='node' else 'element' if descriptor.location=='element' else None
        if kind is None:raise ValueError('Tensor glyphs require canonical node or element associations')
    local=_persisted_frame(stored,settings['frame'])
    shape=tuple(int(value) for value in stored.shape)
    frame_shape=shape[1:] if local is not None else shape
    if len(frame_shape)!=2 or frame_shape[1]!=len(labels):raise ValueError('Glyphs require one canonical vector or tensor per associated sample')
    source_bytes=prod(frame_shape)*24+frame_shape[0]*256
    estimate=source_bytes+min(settings['count'],frame_shape[0])*4096+16384
    if estimate>working_bytes:raise WorkingSetError('Glyph source frame and batched conversion exceed the result working ceiling')
    _cancel(cancel)
    raw=np.asarray(stored.read(local),dtype=float)
    if raw.shape!=frame_shape:raise ValueError('Glyph source shape changed during preparation')
    key=f'{name}_{kind}_ids'
    if key not in dataset.table_keys:raise ValueError('Canonical glyph sample association is unavailable')
    _cancel(cancel);ids=[int(value) for value in np.asarray(dataset.table(key)).reshape(-1)]
    if len(ids)!=len(raw) or len(set(ids))!=len(ids):raise ValueError('Glyph sample correspondence is inconsistent')
    sample=stable_sample_ids(ids,settings['count']);lookup={value:index for index,value in enumerate(ids)}
    selected=raw[[lookup[value] for value in sample]]
    centers=displayed_anchors(scene,kind,sample,cancel)
    scale=settings['scale'];normalized=settings['normalized'];size=scene.characteristic_size()*.035*scale
    _cancel(cancel);batches=[]
    if mode=='vector':
        direction=selected[:,[labels.index(label) for label in ('ux','uy','uz')]]
        length=np.linalg.norm(direction,axis=1)
        if normalized:direction=np.divide(direction,length[:,None],out=np.zeros_like(direction),where=length[:,None]>0)*size
        else:direction=direction*scale
        batch=arrow_batch(centers,centers+direction)
        if batch is not None:batches.append((batch,'#efb343'))
        caption='Displacement vectors: canonical ux, uy, uz; '+('normalized lengths' if normalized else 'length follows displacement magnitude')
    else:
        tensors=np.zeros((len(sample),3,3))
        for component,(i,j) in zip(('xx','yy','zz','xy','yz','xz'),((0,0),(1,1),(2,2),(0,1),(1,2),(0,2))):
            tensors[:,i,j]=tensors[:,j,i]=selected[:,labels.index(component)]
        from anypost import principal_axes
        axes=principal_axes(tensors,cancel_event=cancel,working_bytes=working_bytes-source_bytes)
        ranks=range(3) if settings['rank']=='All' else (int(settings['rank'])-1,)
        for rank in ranks:
            _cancel(cancel);valid=axes.unique[:,rank]
            origin=centers[valid];length=np.full(valid.sum(),size) if normalized else np.abs(axes.values[valid,rank])*scale
            delta=axes.axes[valid,rank]*length[:,None]
            batch=arrow_batch(np.concatenate((origin,origin)),np.concatenate((origin+delta,origin-delta)))
            if batch is not None:batches.append((batch,('#e45756','#54a24b','#4c78a8')[rank]))
        finite=axes.values[np.isfinite(axes.values)]
        caption=(f'Unoriented principal axes: paired arrows; ranks 1 red, 2 green, 3 blue. Signed values {float(finite.min()):.6g} to {float(finite.max()):.6g} Pa; ambiguous axes omitted.' if finite.size else 'Principal values unavailable')
    _cancel(cancel)
    if not batches:caption="No finite glyph samples are available on displayed geometry"
    return batches,caption
