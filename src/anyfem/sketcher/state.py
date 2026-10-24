"""Editable sketch working copies; geometry and constraint truth belongs to ANYgeometry."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import replace
from typing import Sequence
from uuid import uuid4
import numpy as np
from anygeometry import GeometryError, SketchConstraint, SketchDefinition, SketchPlane, solve_sketch
from ..geometry.construction import ConstructionMode, ConstructionTask
from ..geometry.snapping import SnapResult

class FaceSketchTask(ConstructionTask):
    def __init__(self, plane: SketchPlane, snap_tolerance: float, definition=None, *, links=None):
        super().__init__(ConstructionMode.POLYLINE, close=True if definition is None else definition.closed)
        self.plane=plane;self.snap_tolerance=float(snap_tolerance)
        # Read-only FIXED reference snapshots (persisted with the sketch). A source
        # link is optional metadata: reference_links holds still-valid links
        # (id -> ReferenceLink); reference_sources holds EntityRefs imported in this
        # session so the command can capture their link. A reference without a link
        # is "detached" but equally fixed and valid.
        self.references=[];self.reference_links=dict(links or {});self.reference_sources={};self.reference_points={};self.reference_curves={};self.reference_error=''
        self.axis_references={}
        self.point_keys=[];self.constraints=[];self.entities=[];self.wires=[]
        self.preview_closed=None;self.selected=[];self.selected_entities=[]
        self.tool='connected' if definition is None else 'select'
        self.pending=[];self.hover=None;self.spline_kind='spline';self.spline_degree=3;self.spline_weights=()
        self._undo=[];self._redo=[];self._drag_before=None;self.diagnostic='';self._point_serial=0;self._persistent=definition is not None
        if definition is not None:
            from anygeometry import upgrade_sketch
            definition=upgrade_sketch(definition)
            if definition.references:
                from anygeometry import bind_references
                self.references=list(definition.references)
                for item in self.references:
                    self.reference_points.update(item.anchors())
                    self.reference_curves.update(self._curves_of(item))
                self.reference_links={k:v for k,v in self.reference_links.items() if k in {r.id for r in self.references}}
                solved=solve_sketch(bind_references(definition),plane)
            else:solved=solve_sketch(definition,plane)
            for key in definition.points:
                self.point_keys.append(key);self._points.append(tuple(float(v) for v in plane.world(solved[key])));self._snaps.append(None)
            self.constraints=list(definition.constraints);self.entities=list(definition.entities);self.wires=list(definition.wires)
        # Intrinsic datums are separate from imported geometry. Keep persisted
        # snapshots fixed on reopen; add unused datums only to this working copy.
        for item in tuple(self.references):
            if item.id.startswith('global-axis-'):
                self.axis_references[item.id]=item;self.references.remove(item)
        for item in self._axis_intersections():
            self.axis_references.setdefault(item.id,item)
        for item in self.axis_references.values():self.reference_points.update(item.anchors())

    def _axis_intersections(self):
        """Fixed snapshots of true global-axis/plane intercepts, not projections."""
        import math
        from anygeometry import SketchReference
        normal=np.asarray(self.plane.normal,dtype=float)
        distance=math.fsum(float(a)*float(b) for a,b in zip(normal,self.plane.origin))
        if distance==0.:
            # All three axes meet at world zero (some may lie in the plane).
            local=tuple(float(v) for v in self.plane.local((0.,0.,0.)))
            return (SketchReference('global-axis-origin','vertex',points=(local,)),)
        result=[]
        for index,name in enumerate('XYZ'):
            if normal[index]==0.:continue  # parallel, disjoint axis
            with np.errstate(over='ignore',invalid='ignore'):
                coordinate=distance/normal[index]
            if not np.isfinite(coordinate):continue
            world=np.zeros(3);world[index]=coordinate
            local=tuple(float(v) for v in self.plane.local(world))
            if all(math.isfinite(v) for v in local):result.append(SketchReference('global-axis-'+name,'vertex',points=(local,)))
        return tuple(result)

    def _state(self):
        return deepcopy((self._points,self._snaps,self.point_keys,self.constraints,self.entities,self.wires,self.pending,self.close,self.preview_closed,self.references,self.reference_sources,self.reference_points,self.reference_curves,self.reference_links))
    def _restore(self,state):
        (self._points,self._snaps,self.point_keys,self.constraints,self.entities,self.wires,self.pending,self.close,self.preview_closed,self.references,self.reference_sources,self.reference_points,self.reference_curves,self.reference_links)=deepcopy(state)
        self.selected[:]=[k for k in self.selected if k in self.point_keys or k in self.reference_points]
        self.selected_entities[:]=[k for k in self.selected_entities if k in {e.id for e in self.entities}]
    def checkpoint(self):self._undo.append(self._state());self._redo.clear()
    def undo_local(self):
        if not self._undo:return False
        self._redo.append(self._state());self._restore(self._undo.pop());return True
    def redo_local(self):
        if not self._redo:return False
        self._undo.append(self._state());self._restore(self._redo.pop());return True
    @property
    def active_closed(self):return self.close if self.preview_closed is None else self.preview_closed
    @property
    def ready(self):return bool(self.entities) or len(self.points)>=(3 if self.active_closed else 2)
    @property
    def accepts_more(self):return True
    def sample_entity(self,entity,count=48):
        from anygeometry import sample_sketch_entity
        # Derived presentation cache; accepted geometry always uses owner solve.
        dependencies=(entity,tuple(self._points[self.point_keys.index(k)] for k in entity.points))
        cache=getattr(self,'_sample_cache',None)
        if cache is None:cache={};self._sample_cache=cache
        key=(entity.id,count)
        if key not in cache or cache[key][0]!=dependencies:
            cache[key]=(dependencies,sample_sketch_entity(self.definition(0,allow_incomplete=True),entity,count=count))
        return cache[key][1]

    @property
    def preview_segments(self):
        from anygeometry import sample_sketch_entity
        pairs=[]
        for entity in self.entities:
            try:
                values=[tuple(self.plane.world(p)) for p in self.sample_entity(entity)]
                pairs.extend(zip(values,values[1:]))
            except ValueError:continue
        if self.pending:
            values=[self._points[self.point_keys.index(k)] for k in self.pending]
            pairs.extend(zip(values,values[1:]))
            if self.hover is not None:pairs.append((values[-1],self.hover))
            elif self.active_closed and len(values)>=3 and self.tool=='connected':pairs.append((values[-1],values[0]))
        return tuple(pairs)
    def _next_key(self):
        if self._persistent:return "p"+uuid4().hex
        self._point_serial+=1
        while f'p{self._point_serial}' in self.point_keys:self._point_serial+=1
        return f'p{self._point_serial}'
    def _point(self,value):
        world=value.position if isinstance(value,SnapResult) else value
        # Reusing an existing endpoint is distinct from snapping to model geometry.
        for key,p in zip(self.point_keys,self.points):
            if np.linalg.norm(np.asarray(world)-p)<1e-10:return key
        key=self._next_key();self.point_keys.append(key)
        self._points.append(tuple(float(x) for x in world));self._snaps.append(value if isinstance(value,SnapResult) else None)
        local=self.plane.local(world)
        distances=[float(np.linalg.norm(local-p)) for p in self.plane.boundary_vertices]
        if distances and min(distances)<=self.snap_tolerance:
            self.constraints.append(SketchConstraint('on_vertex',key,boundary_index=int(np.argmin(distances)),id='c'+uuid4().hex))
        else:
            distances=[]
            for a,b in self.plane.boundary_edges:
                d=b-a;t=0 if float(d@d)<=0 else float(np.clip((local-a)@d/(d@d),0,1));distances.append(float(np.linalg.norm(local-a-t*d)))
            if distances and min(distances)<=self.snap_tolerance:self.constraints.append(SketchConstraint('on_edge',key,boundary_index=int(np.argmin(distances)),id='c'+uuid4().hex))
        return key
    def _entity(self,kind,keys,**kwargs):
        from anygeometry import SketchEntity,SketchWire
        entity=SketchEntity('e'+uuid4().hex,kind,tuple(keys),**kwargs);self.entities.append(entity)
        closed=bool(kwargs.get('closed',False) or kind=='circle')
        wire=SketchWire('w'+uuid4().hex,(entity.id,),closed)
        if not closed:
            # Joining uses stable endpoint identity, never coordinate guesses.
            by_id={e.id:e for e in self.entities}
            for previous in tuple(self.wires):
                if previous.closed:continue
                a=by_id[previous.entities[0]].points[0];b=by_id[previous.entities[-1]].points[-1]
                first=by_id[wire.entities[0]].points[0];last=by_id[wire.entities[-1]].points[-1]
                if a!=b and {a,b}=={first,last}:
                    wire=SketchWire(previous.id,previous.entities+wire.entities,True);self.wires.remove(previous)
                elif b==first:wire=SketchWire(previous.id,previous.entities+wire.entities,a==last);self.wires.remove(previous)
                elif last==a:wire=SketchWire(previous.id,wire.entities+previous.entities,first==b);self.wires.remove(previous)
        self.wires.append(wire)
        return entity
    def add(self,value,reference_key=None):
        if reference_key is not None:
            if reference_key not in self.reference_points:raise ValueError('Unknown reference point')
            value=tuple(float(x) for x in self.reference_world(reference_key))
        if self.tool in ('select','dimension','fillet'):
            world=value.position if isinstance(value,SnapResult) else value
            for key,p in list(zip(self.point_keys,self.points))+[(k,self.reference_world(k)) for k in self.reference_points]:
                if np.linalg.norm(np.asarray(world)-p)<1e-10:self.select(key);return p
            raise ValueError('Choose a drawing tool to add points')
        self.checkpoint();key=self._point(value)
        if reference_key is not None and not any(c.kind=='coincident' and {c.first,c.second}=={key,reference_key} for c in self.constraints):
            # An ordinary sketch point pinned to the fixed reference; reference geometry
            # itself never enters contours.
            self.constraints.append(SketchConstraint('coincident',key,reference_key,id='c'+uuid4().hex))
        if not self.pending or self.pending[-1]!=key:self.pending.append(key)
        if self.tool=='line' and len(self.pending)==2:self._entity('line',self.pending);self.pending=[]
        elif self.tool=='arc' and len(self.pending)==3:self._entity('arc',self.pending);self.pending=[]
        elif self.tool=='circle' and len(self.pending)==2:self._entity('circle',self.pending,closed=True);self.pending=[]
        return self._points[self.point_keys.index(key)]
    def add_plane_coordinates(self,u,v,frame):return self.add(frame.world_position((u,v)))
    def select(self,key=None,shift=False):
        if not shift:self.selected.clear();self.selected_entities.clear()
        if key is not None:
            if key in self.selected:self.selected.remove(key)
            else:self.selected.append(key)
    def choose_tool(self,tool):
        if self.pending:self.complete_tool()
        self.tool=tool;self.hover=None
    def complete_tool(self,closed=None):
        from anygeometry import SketchWire
        if not self.pending:return False
        if len(self.pending)<2:raise ValueError('Place another point or press Escape to cancel this tool')
        self.checkpoint();keys=tuple(self.pending)
        closed=self.active_closed if closed is None else bool(closed)
        if self.tool=='connected':
            previous_wires=list(self.wires)
            made=[]
            pairs=list(zip(keys,keys[1:]))
            if closed and len(keys)>=3 and keys[-1]!=keys[0]:pairs.append((keys[-1],keys[0]))
            for a,b in pairs:made.append(self._entity('line',(a,b)).id)
            self.wires[:]=previous_wires
            wire=SketchWire('w'+uuid4().hex,tuple(made),closed and len(keys)>=3)
            if not wire.closed:
                by_id={e.id:e for e in self.entities}
                for previous in tuple(self.wires):
                    if previous.closed:continue
                    a=by_id[previous.entities[0]].points[0];b=by_id[previous.entities[-1]].points[-1]
                    first=by_id[wire.entities[0]].points[0];last=by_id[wire.entities[-1]].points[-1]
                    if b==first:wire=SketchWire(previous.id,previous.entities+wire.entities,a==last);self.wires.remove(previous)
                    elif last==a:wire=SketchWire(previous.id,wire.entities+previous.entities,first==b);self.wires.remove(previous)
            self.wires.append(wire)
        elif self.tool in ('spline','nurbs'):
            self._entity(self.tool,keys,degree=min(self.spline_degree,len(keys)-1),weights=tuple(self.spline_weights),closed=closed)
        else:raise ValueError('Complete the current curve by placing its required points')
        self.pending=[];self.hover=None;self.tool='select';return True
    def escape_tool(self):
        if self._drag_before is not None:self._restore(self._drag_before);self._drag_before=None
        if self.pending:
            used={k for e in self.entities for k in e.points}
            for key in tuple(self.pending):
                if key not in used:
                    i=self.point_keys.index(key);self.point_keys.pop(i);self._points.pop(i);self._snaps.pop(i)
            known=set(self.point_keys)|set(self.reference_points)
            self.constraints[:]=[c for c in self.constraints if c.first in known and (not c.second or c.second in known)]
        self.pending=[];self.hover=None;self.tool='select'
    def begin_drag(self):self._drag_before=self._state()
    def move_point(self,key,world):
        from anygeometry import solve_sketch_edit
        if key in self.reference_points or key not in self.point_keys:
            self.diagnostic='Reference geometry is fixed and cannot be moved';return False
        result=solve_sketch_edit(self.bound_definition(0,allow_incomplete=True),self.plane,drag_targets={key:tuple(self.plane.local(world))})
        if not result.converged:
            self.diagnostic='Conflicting constraints: '+', '.join(map(str,result.conflicts));return False
        self._points[:]=[tuple(self.plane.world(result.points[k])) for k in self.point_keys]
        self.diagnostic=f'{result.degrees_of_freedom} degrees of freedom';return True
    def end_drag(self):
        if self._drag_before is not None and self._drag_before[0]!=self._points:self._undo.append(self._drag_before);self._redo.clear()
        self._drag_before=None
    def backspace(self):return self.undo_local()
    def reset(self):
        super().reset();self.references.clear();self.reference_links.clear();self.reference_sources.clear();self.reference_points.clear();self.reference_curves.clear();self.point_keys.clear();self.constraints.clear();self.entities.clear();self.wires.clear();self.pending.clear();self.selected.clear();self.selected_entities.clear()
        for item in self.axis_references.values():self.reference_points.update(item.anchors())
    def cancel(self):self.reset();self.cancelled=True
    @property
    def selectable_keys(self):return list(self.point_keys)+list(self.reference_points)
    def reference_world(self,key):return np.asarray(self.plane.world(self.reference_points[key]),dtype=float)
    def key_world(self,key):
        """World position of a sketch point or fixed reference point."""
        return self.reference_world(key) if key in self.reference_points else np.asarray(self.points[self.point_keys.index(key)],dtype=float)
    def reference_of(self,key):
        return next((r for r in list(self.references)+list(self.axis_references.values()) if key in r.keys),None)
    def _key(self,index_or_key):
        return index_or_key if isinstance(index_or_key,str) else self.point_keys[int(index_or_key)]
    def _pair(self,first,second):
        a,b=self._key(first),self._key(second)
        if a in self.reference_points and b in self.reference_points:
            raise ValueError('Both points are fixed reference geometry; choose at least one sketch point')
        return a,b
    def add_distance(self,first,second,value):
        a,b=self._pair(first,second)
        c=SketchConstraint('distance',a,b,float(value),id='c'+uuid4().hex);self.constraints.append(c);return c
    def add_coincidence(self,first,second):
        a,b=self._pair(first,second)
        c=SketchConstraint('coincident',a,b,id='c'+uuid4().hex);self.constraints.append(c);return c
    def remove_last_constraint(self):
        # Hard reference anchors are not constraints, so they can never be removed here.
        if not self.constraints:return False
        self.checkpoint();self.constraints.pop();return True
    def is_linked(self,reference_id):return reference_id in self.reference_links
    def detached_ids(self):return tuple(r.id for r in self.references if r.id not in self.reference_links)
    def sync_links(self,alive):
        """Keep only still-valid links (ReferenceLink list). Snapshots never move."""
        keep={l.reference_id:l for l in alive}
        self.reference_links={k:v for k,v in keep.items() if k in {r.id for r in self.references}}
        self.reference_sources={k:v for k,v in self.reference_sources.items() if k in self.reference_links}
    def add_reference(self,reference,source=None,link=None):
        """Add an immutable snapshot; ``source``/``link`` are optional link metadata."""
        if reference.id.startswith('global-axis-'):raise ValueError('Global axis reference names are reserved')
        if reference.id in {r.id for r in self.references}:raise ValueError('Reference name is already used')
        self.checkpoint()
        self.references.append(reference)
        if source is not None and link is not None:self.reference_sources[reference.id]=source;self.reference_links[reference.id]=link
        self.reference_points.update(reference.anchors())
        self.reference_curves.update(self._curves_of(reference))
        self.reference_error=''
    @staticmethod
    def _curves_of(reference):
        """Display polylines of a reference: its curve, or one 2-point polyline per section segment."""
        if reference.curve:return {reference.id:reference.curve}
        return {f'{reference.id}:{i}':segment for i,segment in enumerate(reference.segments)}
    def _drop_curves(self,reference_id):
        for key in [k for k in self.reference_curves if k==reference_id or k.startswith(reference_id+':')]:self.reference_curves.pop(key,None)
    def remove_reference(self,reference_id):
        if reference_id in self.axis_references:raise ValueError('Global axis reference points cannot be removed')
        reference=next((r for r in self.references if r.id==reference_id),None)
        if reference is None:raise ValueError('Unknown sketch reference')
        self.checkpoint();keys=set(reference.keys)
        # Constraints that would lose their fixed anchor go with the reference.
        self.constraints[:]=[c for c in self.constraints if c.first not in keys and c.second not in keys]
        self.references.remove(reference);self.reference_sources.pop(reference_id,None);self.reference_links.pop(reference_id,None);self._drop_curves(reference_id)
        for key in keys:self.reference_points.pop(key,None)
        self.selected[:]=[k for k in self.selected if k not in keys]
    def relink_reference(self,reference_id,reference,source=None,link=None):
        """Explicit user refresh: capture NEW geometry into an existing reference, keeping constraints."""
        if reference_id in self.axis_references:raise ValueError('Global axis reference points cannot be relinked')
        old=next((r for r in self.references if r.id==reference_id),None)
        if old is None:raise ValueError('Unknown sketch reference')
        if reference.id!=reference_id or reference.keys!=old.keys:
            raise ValueError('The new source has a different number of reference points; remove and add the reference again')
        self.checkpoint()
        self.references[self.references.index(old)]=reference
        self.reference_sources.pop(reference_id,None);self.reference_links.pop(reference_id,None)
        if source is not None and link is not None:self.reference_sources[reference_id]=source;self.reference_links[reference_id]=link
        self.reference_points.update(reference.anchors())
        self._drop_curves(reference_id);self.reference_curves.update(self._curves_of(reference))
        self.reference_error=''
    def bound_definition(self,extrusion,**kwargs):
        definition=self.definition(extrusion,**kwargs)
        if not definition.references:return definition
        from anygeometry import bind_references
        return bind_references(definition)
    def solve_preview(self,extrusion=0):
        from anygeometry import solve_sketch_edit
        definition=self.definition(extrusion,allow_incomplete=True)
        self.constraints[:]=definition.constraints
        result=solve_sketch_edit(self.bound_definition(extrusion,allow_incomplete=True),self.plane)
        if not result.converged:raise GeometryError('Conflicting sketch constraints: '+', '.join(map(str,result.conflicts)))
        self._points[:]=[tuple(self.plane.world(result.points[k])) for k in self.point_keys]
        self.diagnostic=f'{result.degrees_of_freedom} degrees of freedom'
        return self.definition(extrusion)
    def definition(self,extrusion,*,closed=None,allow_incomplete=False):
        from anygeometry import SketchEntity,SketchWire
        closed=self.active_closed if closed is None else bool(closed)
        entities=list(self.entities);wires=list(self.wires)
        if self.pending and len(self.pending)>=2 and self.tool=='connected':
            pairs=list(zip(self.pending,self.pending[1:]))
            if closed and len(self.pending)>=3:pairs.append((self.pending[-1],self.pending[0]))
            es=[SketchEntity(f'draft/{a}/{b}','line',(a,b)) for a,b in pairs];entities.extend(es);wires.append(SketchWire('draft',tuple(e.id for e in es),closed and len(self.pending)>=3))
        if not allow_incomplete and not entities:raise ValueError('The sketch needs a completed line or curve')
        used={key for c in self.constraints for key in (c.first,c.second)}
        datums=tuple(r for r in self.axis_references.values() if used.intersection(r.keys))
        return SketchDefinition(points={k:tuple(float(v) for v in self.plane.local(p)) for k,p in zip(self.point_keys,self.points)},path=tuple(self.point_keys),constraints=tuple(self.constraints),closed=closed,extrusion=float(extrusion),version=2,entities=tuple(entities),wires=tuple(wires),references=tuple(self.references)+datums)
