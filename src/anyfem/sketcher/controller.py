"""Toolkit-neutral sketch application controller and host ports."""
from __future__ import annotations
from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any, Callable
from uuid import uuid4
import numpy as np
from anygeometry import GeometryError, SketchConstraint, solve_sketch_edit, set_sketch_radius, round_sketch_corner
from .state import FaceSketchTask


def _noop(*args,**kwargs):return None

@dataclass
class SketchPorts:
    begin_preview: Callable = _noop
    end_preview: Callable = _noop
    changed: Callable = _noop
    preview: Callable = _noop
    execute: Callable = _noop
    can_edit: Callable = lambda: True
    accepted: Callable = _noop
    focus: Callable = _noop
    finish_requested: Callable = _noop
    geometry: Callable = lambda: None  # live GeometryModel used to capture references and check links

@dataclass
class SketchSession:
    task: FaceSketchTask | None = None
    support: Any = None
    editing_feature_id: Any = None
    editing_plane: Any = None
    last_sketch_feature_id: Any = None
    workplane: Any = None
    systems: Any = None
    pressed: bool = False
    press_position: tuple | None = None
    press_key: str | None = None
    dragging: bool = False

class SketchController:
    """Single action authority shared by all views of a sketch session.

    Ports contain host effects only. All methods accept model-space values except
    press/move screen positions and thresholds, which use the caller's units.
    """
    def __init__(self,ports=None):self.ports=ports or SketchPorts();self.session=SketchSession()
    def bind(self,ports):self.ports=ports
    @property
    def task(self):return self.session.task
    @property
    def active(self):return self.task is not None
    def configure(self,**values):
        for key,value in values.items():
            if key not in ('support','editing_feature_id','editing_plane','last_sketch_feature_id'):raise ValueError('unknown sketch context '+key)
            setattr(self.session,key,value)
    def _editable(self):
        if not self.ports.can_edit():raise PermissionError('This project is read-only')
    def _task(self):
        if self.task is None:raise ValueError('Start a sketch first')
        return self.task
    def changed(self,snap=None):
        task=self._task()
        if task.reference_links:self.refresh_references(task)  # immediate detachment, fast path otherwise
        self.ports.changed(task,snap)
    def begin(self,task,workplane=None,systems=None,**context):
        self._editable()
        if self.active:raise ValueError('Finish or cancel the current sketch first')
        self.configure(**context);self.session.task=task;self.session.workplane=workplane;self.session.systems=systems
        try:self.ports.begin_preview(task,workplane,systems)
        except BaseException:self.session.task=None;raise
        self.changed();return task
    def choose_tool(self,tool,*,degree=3,weights=()):
        self._editable();task=self._task();task.spline_degree=int(degree);task.spline_weights=tuple(weights);task.choose_tool(tool);self.changed()
    def select(self,key=None,shift=False):self._task().select(key,shift);self.changed()
    def select_entity(self,identifier=None,shift=False):
        task=self._task()
        if not shift:task.select()
        if identifier is not None:
            if identifier in task.selected_entities:task.selected_entities.remove(identifier)
            else:task.selected_entities.append(identifier)
        self.changed()
    def select_dimension(self,*,constraint=None,entity=None):
        task=self._task();task.select()
        if entity is not None:task.selected_entities[:]=[entity];kind='radius'
        else:
            task.selected[:]=[constraint.first,constraint.second];kind='distance'
        self.session.pressed=False;self.changed();self.ports.focus(kind)
    def point(self,value=None,*,existing_key=None):
        self._editable();task=self._task()
        if task.tool in ('select','dimension','fillet'):return self.select(existing_key)
        if existing_key in task.reference_points:
            # Clicking fixed reference geometry while drawing places an ordinary sketch point
            # pinned to it by a coincident constraint; the reference never joins a contour.
            result=task.add(None,reference_key=existing_key);self.changed();return result
        if existing_key is not None:value=task.points[task.point_keys.index(existing_key)]
        result=task.add(value);self.changed(value if hasattr(value,'candidate') else None);return result
    def press(self,position,*,point_key=None,entity_id=None,shift=False):
        task=self._task();s=self.session;s.pressed=True;s.press_position=tuple(position);s.press_key=point_key;s.dragging=False
        if task.tool in ('select','dimension','fillet'):
            if point_key is not None:self.select(point_key,shift)
            else:self.select_entity(entity_id,shift)
    def move(self,world,*,position=None,left_down=False,threshold=10):
        task=self._task();s=self.session
        if s.pressed and left_down:
            distance=sum(abs(a-b) for a,b in zip(position or (),s.press_position or ()))
            if s.press_key is not None and task.tool in ('select','dimension') and (s.dragging or distance>=threshold):
                self._editable()
                if not s.dragging:task.begin_drag();s.dragging=True
                if world is not None:task.move_point(s.press_key,world);self.ports.preview()
            return True
        if task.pending and task.tool not in ('select','dimension','fillet'):
            task.hover=world;self.ports.preview()
        return False
    def release(self,value=None):
        task=self._task();s=self.session
        if not s.pressed:return False
        s.pressed=False
        if s.dragging:task.end_drag();s.dragging=False;self.changed();return False
        if task.tool not in ('select','dimension','fillet'):
            if s.press_key is not None:self.point(existing_key=s.press_key);return False
            if value is not None:self.point(value);return False
            return True  # Host must project/snap this release and call point.
        return False
    def close_loop(self):self._editable();self._task().complete_tool(closed=True);self.changed()
    def undo(self,redo=False):
        self._editable();task=self._task();result=(task.redo_local if redo else task.undo_local)();self.changed();return result
    def set_closed(self,closed):self._task().preview_closed=bool(closed);self.changed()
    def remove_constraint(self,index=None):
        self._editable();task=self._task()
        if index is None:task.remove_last_constraint()
        elif 0<=index<len(task.constraints):task.checkpoint();task.constraints.pop(index)
        self.changed()
    def constraint(self,kind,value=None,*,indices=None,extrusion=0):
        self._editable();task=self._task()
        # Constraint operands are point keys: sketch points or fixed reference points.
        if task.selected:chosen=tuple(task.selected)
        else:
            if any(not 0<=int(i)<len(task.point_keys) for i in (indices or ())):raise ValueError('Select one point for Fixed, or two points for this constraint: click a point, then Shift-click the second')
            chosen=tuple(task.point_keys[int(i)] for i in (indices or ()))
        if len(chosen)!=(1 if kind=='fixed' else 2) or len(set(chosen))!=len(chosen) or any(k not in task.selectable_keys for k in chosen):raise ValueError('Select one point for Fixed, or two points for this constraint: click a point, then Shift-click the second')
        if kind=='fixed' and chosen[0] in task.reference_points:raise ValueError('Reference geometry is already fixed')
        if kind in ('horizontal','vertical') and all(k in task.reference_points for k in chosen):raise ValueError('Both points are fixed reference geometry')
        before=task._state();task.checkpoint()
        try:
            if kind=='distance':
                keys=set(chosen);task.constraints[:]=[c for c in task.constraints if c.kind!='distance' or {c.first,c.second}!=keys];task.add_distance(*chosen,float(value))
            elif kind=='coincident':task.add_coincidence(*chosen)
            else:task.constraints.append(SketchConstraint(kind,chosen[0],chosen[1] if len(chosen)>1 else None,id='c'+uuid4().hex))
            if task.ready:
                definition=task.definition(extrusion,allow_incomplete=True);task.constraints[:]=definition.constraints
                bound=task.bound_definition(extrusion,allow_incomplete=True)
                result=solve_sketch_edit(bound,task.plane,fixed_points=(chosen[0],)) if kind=='distance' else solve_sketch_edit(bound,task.plane)
                if not result.converged:result=solve_sketch_edit(bound,task.plane)
                if not result.converged:raise GeometryError('Dimension conflicts with existing constraints')
                task._points[:]=[tuple(task.plane.world(result.points[k])) for k in task.point_keys];task.diagnostic=f'{result.degrees_of_freedom} degrees of freedom'
        except BaseException:
            task._restore(before);task._undo.pop();raise
        self.changed()
    def _reference_geometry(self):
        geometry=self.ports.geometry()
        if geometry is None:raise ValueError('No model is available to capture sketch references')
        return geometry
    def _capture(self,geometry,task,source,identifier):
        """Explicit user capture: fixed snapshot plus (best effort) optional source link."""
        from ..sketch_references import capture_link,capture_reference
        source=geometry.resolve_ref(source)[0] if len(geometry.resolve_ref(source))==1 else source
        reference=capture_reference(geometry,task.plane,source,identifier)
        link=capture_link(geometry,source,identifier)
        return reference,(source if link is not None else None),link
    def add_references(self,sources):
        """Capture model vertices/edges meeting the sketch plane as fixed, read-only references (atomic)."""
        from ..sketch_references import next_reference_id
        self._editable();task=self._task();geometry=self._reference_geometry()
        before=task._state();undo_depth=len(task._undo)
        try:
            for source in sources:
                identifier=next_reference_id([r.id for r in task.references])
                reference,linked,link=self._capture(geometry,task,source,identifier)
                task.add_reference(reference,linked,link)
        except BaseException as error:
            task._restore(before);del task._undo[undo_depth:]
            from ..sketch_references import describe_reference_error
            task.reference_error=describe_reference_error(error) if hasattr(error,'reason') else '';raise
        self.changed()
    def remove_reference(self,reference_id):
        self._editable();self._task().remove_reference(reference_id);self.changed()
    def relink_reference(self,reference_id,source):
        """Explicit user refresh: capture NEW geometry into an existing reference."""
        self._editable();task=self._task();geometry=self._reference_geometry()
        reference,linked,link=self._capture(geometry,task,source,reference_id)
        task.relink_reference(reference_id,reference,linked,link);self.changed()
    def refresh_references(self,task=None):
        """Detach links whose source changed. Snapshots are never touched or re-resolved."""
        from anygeometry import surviving_reference_links
        task=task or self._task()
        if not task.reference_links:return False
        geometry=self.ports.geometry()
        if geometry is None:return False
        links=list(task.reference_links.values());alive=surviving_reference_links(geometry,links)
        if len(alive)==len(links):return False
        task.sync_links(alive);return True
    def radius(self,value,*,diameter=False):
        self._editable();task=self._task();definition=task.definition(0,allow_incomplete=True);radius=float(value)/(2 if diameter else 1)
        if len(task.selected_entities)==2:definition=round_sketch_corner(definition,*task.selected_entities,radius)
        elif len(task.selected_entities)==1:definition=set_sketch_radius(definition,task.selected_entities[0],radius)
        else:raise ValueError('Select one arc/circle or two connected straight segments')
        from anygeometry import bind_references
        result=solve_sketch_edit(bind_references(definition) if definition.references else definition,task.plane)
        if not result.converged:raise GeometryError('Radius conflicts with existing constraints')
        before_ids={e.id for e in task.entities};sources=dict(task.reference_sources)
        replacement=FaceSketchTask(task.plane,task.snap_tolerance,definition,links=dict(task.reference_links))
        task.checkpoint();task._restore(replacement._state());task.reference_sources=sources;task.selected_entities[:]=[e.id for e in task.entities if e.id not in before_ids] or task.selected_entities;self.changed()
    def enter(self,*,field=None,value=None,**finish):
        if field=='coordinates':return self.point(value)
        if field=='distance':return self.constraint('distance',value,indices=finish.get('indices'),extrusion=finish.get('extrusion',0))
        if field=='radius':return self.radius(value,diameter=finish.get('diameter',False))
        task=self._task()
        if task.pending:task.complete_tool();self.changed();return None
        if task.ready:return self.finish(**finish) if finish else self.ports.finish_requested()
    def escape(self):
        task=self._task();task.escape_tool();self.session.pressed=False;self.session.dragging=False;self.changed()
    def finish(self,name='Sketch',extrusion=0,closed=None,constraint_records=None):
        from .. import commands
        self._editable();task=self._task();name=str(name).strip()
        if not name:raise ValueError('Enter a sketch name')
        preview=deepcopy(task)
        if preview.pending:preview.complete_tool()
        if constraint_records is not None:preview.constraints[:]=[c if isinstance(c,SketchConstraint) else SketchConstraint(**c) for c in constraint_records]
        if closed is not None:preview.close=bool(closed);preview.preview_closed=bool(closed)
        s=self.session
        if preview.reference_links:self.refresh_references(preview)  # detach changed sources; snapshots stay fixed
        definition=preview.solve_preview(float(extrusion))
        sources=dict(preview.reference_sources)
        if s.editing_feature_id is None:
            if s.support is not None:command=commands.AddSketch(s.support,definition,name,references=sources)
            else:
                frame=s.workplane.resolve(s.systems);command=commands.AddPlaneSketch(definition,frame.origin,frame.normal,frame.x_axis,name,references=sources)
        else:
            parameters=definition.to_parameters()
            if s.editing_plane is not None:parameters['plane']=s.editing_plane
            command=commands.EditFeature(s.editing_feature_id,name=name,parameters=parameters,sketch_references=sources)
        feature=self.ports.execute(command)
        self.ports.end_preview();s.last_sketch_feature_id=feature.feature_id;s.task=None;s.support=None;s.editing_feature_id=None;s.editing_plane=None;s.pressed=False;s.dragging=False
        self.ports.accepted(feature);return feature
    def cancel(self):
        if not self.active:return False
        task=self.task;task.cancel();self.ports.changed(task,None);self.ports.end_preview();self.session.task=None;self.configure(support=None,editing_feature_id=None,editing_plane=None);self.session.pressed=False;self.session.dragging=False;return True
