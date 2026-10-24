"""Qt ownership of the shared ANYfem scene presenter and ANY3dView host."""
from .theme import overlay
from PySide6.QtCore import QObject, QEvent, Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel
from types import SimpleNamespace

from ...presentation.viewport import SceneViewport
from ...presentation.visibility import SelectionVisibility
from any3dview import create_viewer, Point3D
from any3dview.qt import QtViewerHostAdapter


class _ConstructionKeys(QObject):
    def __init__(self, viewport):
        super().__init__(viewport.widget)
        self.viewport = viewport
        self._right_press = None
        self._right_dragged = False
        self._camera_drag = False
        self._plate_left_down = False

    def eventFilter(self, target, event):
        if target is self.viewport.widget and event.type() == QEvent.Resize:
            self.viewport._position_colour_legend()
        if target is self.viewport.canvas.event_widget:
            kind = event.type()
            from ...geometry.sketching import FaceSketchTask
            task=self.viewport.construction_task
            if isinstance(task,FaceSketchTask) and self._sketch_event(target,event,task):return True
            if kind in {QEvent.MouseButtonPress, QEvent.MouseButtonDblClick}:
                if event.button() == Qt.MiddleButton:
                    self._camera_drag = True
                    self.viewport._begin_interaction_scene()
                else:
                    # Picking and context menus require the complete owner map.
                    self.viewport._cancel_interaction_restore()
                    self.viewport._restore_full_scene()
            elif kind == QEvent.MouseMove:
                if event.buttons() & (Qt.RightButton | Qt.MiddleButton):
                    if event.buttons() & Qt.MiddleButton or (self._right_press is not None and
                            (event.position().toPoint() - self._right_press).manhattanLength() >= QApplication.startDragDistance()):
                        self._camera_drag = True
                        self.viewport._begin_interaction_scene()
            elif kind == QEvent.MouseButtonRelease and event.button() in {Qt.RightButton, Qt.MiddleButton}:
                if self._camera_drag:
                    self._camera_drag = False
                    self.viewport._schedule_full_scene()
            elif kind == QEvent.Wheel:
                self.viewport._begin_interaction_scene()
                self.viewport._schedule_full_scene()
            elif kind in {QEvent.FocusOut, QEvent.Hide}:
                self._camera_drag = False
                self.viewport._schedule_full_scene()
            if (event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton
                    and self.viewport._line_construction_requested()):
                from ...selection import owner_to_ref
                ratio = target.devicePixelRatioF()
                hits = self.viewport.canvas.query_point(round(event.position().x()*ratio), round(event.position().y()*ratio))
                ref = next((candidate for hit in hits
                            if (candidate := owner_to_ref(hit.owner)) is not None and candidate.kind == "vertex"), None)
                if ref is not None:
                    self.viewport._window.guarded(lambda: self.viewport.line_endpoint(ref))()
                    return True
            draft = getattr(self.viewport, "plate_draft", None)
            if draft is not None and event.type() in {QEvent.MouseButtonPress, QEvent.MouseButtonDblClick} and event.button() == Qt.LeftButton:
                self._plate_left_down = True
                # A double-click is not a second boundary point. Closure is
                # an explicit click on the starting point.
                if event.type() == QEvent.MouseButtonDblClick:return True
                from ...selection import owner_to_ref
                ratio = target.devicePixelRatioF()
                hits = self.viewport.canvas.query_point(round(event.position().x()*ratio), round(event.position().y()*ratio))
                refs = [owner_to_ref(hit.owner) for hit in hits]
                draft.add_vertex(next((ref for ref in refs if ref is not None and ref.kind == "vertex"), None))
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton and (draft is not None or self._plate_left_down):
                self._plate_left_down = False
                return True
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.RightButton:
                self._right_press = event.position().toPoint()
                self._right_dragged = False
            elif event.type() == QEvent.MouseMove and self._right_press is not None:
                if (event.position().toPoint() - self._right_press).manhattanLength() >= QApplication.startDragDistance():
                    self._right_dragged = True
            elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.RightButton:
                position = event.position().toPoint()
                clicked = (self._right_press is not None and not self._right_dragged
                           and (position - self._right_press).manhattanLength() < QApplication.startDragDistance()
                           and event.modifiers() in {Qt.NoModifier, Qt.ShiftModifier})
                self._right_press = None
                if clicked:
                    # Let the viewer finish its orbit gesture before opening a menu.
                    preserve = event.modifiers() == Qt.ShiftModifier
                    QTimer.singleShot(0, target, lambda: self.viewport.context_menu_at(target, position, preserve_selection=preserve))
            elif event.type() in {QEvent.FocusOut, QEvent.Hide}:
                self._right_press = None
        if target is self.viewport.widget and event.type()==QEvent.Show:
            self.viewport._schedule_automatic_backend()
        draft = getattr(self.viewport, "plate_draft", None)
        if draft is not None and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:self.viewport.cancel_plate();return True
            if (event.key() in {Qt.Key_Return, Qt.Key_Enter}
                    and event.modifiers() in {Qt.NoModifier, Qt.KeypadModifier}
                    and QApplication.activePopupWidget() is None):
                if not event.isAutoRepeat():draft.commit()
                return True
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape and not self.viewport.construction_active:
            if self.viewport.line_start is not None:
                self.viewport.cancel_line_construction(status=True)
                return True
            self.viewport.dismiss_feature_highlight()
            self.viewport.selection.clear()
        if event.type()==QEvent.KeyPress and self.viewport.construction_active and event.modifiers() & Qt.ControlModifier and event.key() in (Qt.Key_Z,Qt.Key_Y):
            self.viewport._window.panels['Construction'].local_undo(event.key()==Qt.Key_Y or bool(event.modifiers() & Qt.ShiftModifier));return True
        if event.type() == QEvent.KeyPress and self.viewport.construction_active:
            if event.key() == Qt.Key_Escape:
                self.viewport._window.panels["Construction"].escape()
                return True
            if (event.key() in {Qt.Key_Return, Qt.Key_Enter}
                    and event.modifiers() in {Qt.NoModifier, Qt.KeypadModifier}
                    and QApplication.activePopupWidget() is None):
                if not event.isAutoRepeat():self.viewport._window.guarded(self.viewport._window.panels["Construction"].enter)()
                return True
        if (event.type() == QEvent.KeyPress and event.key() in {Qt.Key_Return, Qt.Key_Enter}
                and event.modifiers() in {Qt.NoModifier, Qt.KeypadModifier}
                and QApplication.activePopupWidget() is None):
            window = self.viewport._window
            name = window.details.tabText(window.details.currentIndex())
            panel = window.panels.get(name)
            from .editors import CommandEditor
            from .generators import GeneratorTask
            from .construction import ConstructionPanel
            if isinstance(panel,ConstructionPanel):
                if not event.isAutoRepeat():panel.idle_enter_button().click()
                return True
            if isinstance(panel, (CommandEditor, GeneratorTask)):
                if name in {"Geometry", "Generators"}:window._begin_modelling()
                if not event.isAutoRepeat():panel.apply.click()
                return True
        return False

    def _sketch_event(self,target,event,task):
        kind=event.type();viewport=self.viewport;controller=viewport.sketcher
        if kind in (QEvent.MouseButtonPress,QEvent.MouseButtonDblClick) and event.button()==Qt.LeftButton:
            if kind==QEvent.MouseButtonDblClick:return True
            ratio=target.devicePixelRatioF();x=event.position().x()*ratio;y=event.position().y()*ratio
            if task.tool=='reference':
                # Pick model geometry through the viewer's owner query; the sketch itself is not hit-tested.
                def pick():
                    ref=viewport.pick_reference_source(x,y)
                    if ref is not None:viewport._window.panels['Construction'].import_references([ref])
                viewport._window.guarded(pick)()
                return True
            key=viewport.sketch_point_at(x,y,9*ratio)
            if key is None and task.tool in ('select','dimension'):
                for entity in task.entities:
                    if entity.kind not in ('arc','circle','fillet'):continue
                    q=viewport.project_point(viewport._point3d(*viewport.sketch_radius_label_position(entity)))
                    if q is not None and (q[0]-x)**2+(q[1]-y)**2<(12*ratio)**2:
                        controller.select_dimension(entity=entity.id);return True
                import numpy as np
                for c in task.constraints:
                    if c.kind!='distance' or c.second is None:continue
                    a=task.key_world(c.first);b=task.key_world(c.second)
                    q=viewport.project_point(viewport._point3d(*((np.asarray(a)+b)/2)))
                    if q is not None and (q[0]-x)**2+(q[1]-y)**2<(12*ratio)**2:
                        controller.select_dimension(constraint=c);return True
            entity=viewport.sketch_entity_at(x,y,8*ratio) if key is None and task.tool in ('select','dimension','fillet') else None
            controller.press((event.position().x(),event.position().y()),point_key=key,entity_id=entity,shift=bool(event.modifiers() & Qt.ShiftModifier));return True
        if kind==QEvent.MouseMove:
            ratio=target.devicePixelRatioF();world=viewport.project_to_workplane(event.position().x()*ratio,event.position().y()*ratio,viewport._construction_workplane,viewport._construction_coordinate_systems)
            viewport._window.guarded(lambda:controller.move(world,position=(event.position().x(),event.position().y()),left_down=bool(event.buttons() & Qt.LeftButton),threshold=QApplication.startDragDistance()))()
            return controller.session.pressed and bool(event.buttons() & Qt.LeftButton)
        if kind==QEvent.MouseButtonRelease and event.button()==Qt.LeftButton:
            # Flush the final pointer position before ending its single undo gesture.
            if controller.session.dragging:
                ratio=target.devicePixelRatioF();world=viewport.project_to_workplane(event.position().x()*ratio,event.position().y()*ratio,viewport._construction_workplane,viewport._construction_coordinate_systems)
                viewport._window.guarded(lambda:controller.move(world,position=(event.position().x(),event.position().y()),left_down=True,threshold=QApplication.startDragDistance()))()
            if controller.release():
                ratio=target.devicePixelRatioF();viewport._window.guarded(lambda:viewport.construction_click(event.position().x()*ratio,event.position().y()*ratio))()
            return True
        return False


class QtViewport(SceneViewport):
    def __init__(self, parent, selection, *, backend="auto"):
        self.widget = QWidget(parent)
        self.layout = QVBoxLayout(self.widget)
        self.layout.setContentsMargins(0,0,0,0)
        self.initialize_state(selection)
        self._point3d = Point3D
        self._requested_backend = backend
        self._commercial_interaction_requested = True
        self._automatic_pending = backend=="auto"
        self._closed = False
        self.plate_draft = None
        self.line_start = None
        self._plane_preview = None
        self._plane_signature = ()
        self._reference_plane_ids = ()
        self._feature_ids = frozenset()
        self._collection_refs = frozenset()
        self.visibility = SelectionVisibility()
        self._visibility_scene = None
        self._visibility_interaction_scene = None
        self._visibility_mesh = None
        self._visibility_job = None
        self._window = parent
        self.sketcher=getattr(parent,"sketcher",None)
        self._sketch_timer=QTimer(self.widget);self._sketch_timer.setSingleShot(True);self._sketch_timer.timeout.connect(self.refresh_construction_overlay)
        # Probe OpenGL only once Qt has exposed the containing window. A hidden
        # startup widget can otherwise leave an invalid composition surface.
        self.canvas = create_viewer(self.widget, backend="software" if backend=="auto" else backend, host=QtViewerHostAdapter())
        self.layout.addWidget(self.canvas)
        self._configure_canvas(self.canvas)
        self._keys = _ConstructionKeys(self)
        self.widget.installEventFilter(self._keys)
        self.canvas.event_widget.installEventFilter(self._keys)
        from .sketch_coordinates import SketchCoordinates
        self._sketch_coordinates = SketchCoordinates(self)
        self._sketch_coordinates.bind()
        self._watch_backend(parent)
        self.selection.add_listener(self._apply_highlight)

    def pick_reference_source(self,x,y):
        """Model vertex, edge or face under a pixel for the reference tool.

        An explicit vertex/edge/face selection filter is passed to the viewer for
        this one query, so the reference tool works whatever filter the user's
        normal selection currently has, and that normal filter is never changed.
        Vertices win over edges, edges over faces.
        """
        from ...selection import owner_to_ref
        kinds=frozenset(('vertex','edge','face'))
        api=getattr(self,'_selection_api',None)
        if api is None or 'SelectionFilter' not in api:
            raise RuntimeError('The installed viewer has no semantic selection filter; model references cannot be picked')
        # Same qualified kind strings as SceneViewport._selection_config; the
        # user's normal filter is neither consulted nor replaced.
        flt=api['SelectionFilter'](kinds=frozenset(f'geometry.{kind}' for kind in kinds))
        hits=self.canvas.query_point(round(x),round(y),selection_filter=flt)
        refs=[c for hit in hits if (c:=owner_to_ref(hit.owner)) is not None and c.kind in kinds]
        for kind in ('vertex','edge','face'):
            found=next((c for c in refs if c.kind==kind),None)
            if found is not None:return found
        return None

    def _schedule_automatic_backend(self):
        if not self._automatic_pending:return
        self._automatic_pending=False
        def initialize():
            if not self._closed and self._requested_backend=="auto":
                self.switch_backend("auto")
        QTimer.singleShot(0,self.widget,initialize)

    def set_feature_highlight(self,identifiers):
        identifiers=frozenset(identifiers)
        if identifiers == self._feature_ids:return
        self._feature_ids=identifiers
        self._apply_highlight()

    def show(self, scene, *, interaction_scene=None, reset_view=False):
        from ...presentation.interaction_scene import camera_scene
        systems=getattr(getattr(self._window,"project",None),"coordinate_systems",{})
        self._reference_plane_ids=tuple(identifier for identifier,system in systems.items()
                                        if identifier!="global" and system.kind=="cartesian")
        interaction_scene = camera_scene(scene if interaction_scene is None else interaction_scene)
        mesh=self._window.mesh;job=self._window.active_job_id
        if mesh is not self._visibility_mesh or job != self._visibility_job:
            # Node/element IDs belong to one mesh/result realization.
            self.visibility.hidden={key for key in self.visibility.hidden if key[0] in {"vertex","edge","face","plane"}}
            self._visibility_mesh=mesh;self._visibility_job=job
        self._visibility_scene = scene
        self._visibility_interaction_scene = interaction_scene
        visible = self.visibility.filter_scene(scene)
        # Scene.copy deliberately drops transient preparation. Only an exact,
        # unfiltered copy can consume the producer's already prepared batches.
        prepared = scene.__dict__.pop('_prepared_result_batches', None)
        if not self.visibility.hidden and prepared is not None:
            visible._prepared_result_batches = prepared
        super().show(visible,
            interaction_scene=None if interaction_scene is None else self.visibility.filter_scene(interaction_scene),
            reset_view=reset_view)

    def refresh_visibility(self):
        if self._visibility_scene is not None:
            self.show(self._visibility_scene, interaction_scene=self._visibility_interaction_scene)

    def _refresh_visualization_scene(self):
        # Applying appearance must not replace the original scene with its
        # filtered copy: hidden entities must remain available to Show.
        if self._visibility_scene is not None:
            self.refresh_visibility()
        else:
            super()._refresh_visualization_scene()

    def reset_visibility(self):
        self.visibility.hidden.clear()
        self._visibility_scene = None
        self._visibility_interaction_scene = None
        self._visibility_mesh = None
        self._visibility_job = None

    def clear(self):
        self.cancel_plate()
        self._plane_preview = None
        self._visibility_scene = None
        self._visibility_interaction_scene = None
        super().clear()

    def _handle_selection_event(self,event):
        self.dismiss_feature_highlight()
        super()._handle_selection_event(event)

    def context_menu_at(self, surface, position, *, preserve_selection=False):
        if self._closed or surface is not self.canvas.event_widget or self.construction_active:
            return
        actions=getattr(self._window,"geometry_actions",None)
        if actions is None or self._window._view_mode != "geometry" or not self._window.canvas_action_menu.isChecked():
            return
        self._cancel_interaction_restore()
        self._restore_full_scene()
        ratio = surface.devicePixelRatioF()
        point = (round(position.x() * ratio), round(position.y() * ratio))
        hits = () if preserve_selection else self.canvas.query_point(*point)[:1]
        if not hits and not preserve_selection:
            return
        from ...selection import owner_to_ref
        selected=set(self.selection.items)
        # The actual picked owner must already be selected; an arbitrary member
        # of a grouped render chunk must not keep an unrelated selection.
        if any(owner_to_ref(hit.owner) in selected for hit in hits):preserve_selection=True
        if not preserve_selection:
            self.dismiss_feature_highlight()
            self.selection.apply_owners([hit.owner for hit in hits], "replace")
        actions.canvas_context_menu(SimpleNamespace(hits=hits, end=point, preserve_selection=preserve_selection))

    def _line_construction_requested(self):
        window = self._window
        panel = window.panels.get("Geometry")
        return (not self._closed and not self.construction_active and self.plate_draft is None
                and window._view_mode == "geometry"
                and window.details.currentWidget().widget() is panel
                and panel.choice.currentData() == "AddLine")

    def cancel_line_construction(self, *, status=False):
        if self.line_start is None:
            return
        self.line_start = None
        if status:
            self._window.set_status("Line construction cancelled")

    def line_endpoint(self, ref):
        """Use two vertex clicks to create one direct line (beam-axis) edit."""
        from anygeometry.entities import EntityRef
        from ... import commands
        if not isinstance(ref, EntityRef) or ref.kind != "vertex":
            return False
        window = self._window
        if self.line_start is None:
            self.cancel_plate()
            self.cancel_construction()
            self.line_start = ref
            window.selection.set_mode("vertex")
            window.selection.restore((ref,))
            window.set_status("Line start selected; click the end point")
            return True
        edge = window.run(commands.AddLine(self.line_start.id, ref.id))
        self.line_start = None
        window.selection.set_mode("edge")
        window.selection.restore((EntityRef("edge", edge),))
        window.set_status("Line constructed")
        return True

    def cancel_plate(self):
        if self.plate_draft is not None:self.plate_draft.reject()

    def request_sketch_preview(self):
        if not self._sketch_timer.isActive():self._sketch_timer.start(16)

    def refresh_construction_overlay(self):
        self._sketch_timer.stop()
        return super().refresh_construction_overlay()

    def end_construction(self):
        self._sketch_timer.stop()
        super().end_construction()
        self._sketch_coordinates.sync()

    def begin_construction(self, *args, **kwargs):
        self.cancel_plate()
        self._plane_preview = None
        return super().begin_construction(*args, **kwargs)

    def preview_construction_plane(self,workplane,name):
        """Display a reference grid without adding structural faces or a draft."""
        workplane.resolve(self._window.panels["Construction"].coordinate_systems())
        self._plane_preview=(workplane,name)
        scene=self._scene
        if scene is None:
            from ...presentation.scene import Scene
            scene=Scene()
        self.show(scene,interaction_scene=self._interaction_scene)

    def refresh_plane_preview(self):
        systems=self._window.panels["Construction"].coordinate_systems()
        signature=tuple((identifier,system.name,tuple(system.origin),tuple(system.axis),tuple(system.reference))
                        for identifier,system in self._window.project.coordinate_systems.items()
                        if identifier!="global" and system.kind=="cartesian")
        changed=signature!=self._plane_signature
        self._plane_signature=signature
        if self._plane_preview is not None and self._plane_preview[0].coordinate_system_id not in systems:
            self._plane_preview=None;changed=True
        if changed and self._window._view_mode=="geometry":
            from ...presentation.scene import Scene
            self.show(self._scene if self._scene is not None else Scene(),interaction_scene=self._interaction_scene)

    def _draw_plane_preview(self):
        if not hasattr(self._window,"project"):return
        from ...model.workplanes import Workplane
        systems=self._window.panels["Construction"].coordinate_systems()
        if self._window._view_mode!="geometry":return
        planes={identifier:(Workplane(identifier),system.name) for identifier,system in self._window.project.coordinate_systems.items()
                if identifier!="global" and system.kind=="cartesian"}
        if self._plane_preview is not None:
            plane,name=self._plane_preview
            if plane.coordinate_system_id in systems:planes[plane.coordinate_system_id]=(plane,name)
        for identifier,(plane,name) in planes.items():
            if ("plane",identifier) in self.visibility.hidden:continue
            if self.construction_active and identifier==self._construction_workplane.coordinate_system_id:continue
            self._draw_reference_plane(plane,name,systems)

    def _draw_reference_plane(self,plane,name,systems):
        frame=plane.resolve(systems)
        spacing=plane.grid_spacing
        for index in range(-5,6):
            for first,last in (((index*spacing,-5*spacing),(index*spacing,5*spacing)),
                               ((-5*spacing,index*spacing),(5*spacing,index*spacing))):
                self.canvas.add_line(self._point3d(*frame.world_position(first)),self._point3d(*frame.world_position(last)),
                                     color=overlay("grid"),width=1,layer=40,draw_overlay=True)
        for direction,label,color in ((frame.x_axis,"u",overlay("u")),(frame.y_axis,"v",overlay("v")),(frame.normal,"n",overlay("n"))):
            end=frame.origin+spacing*direction
            self.canvas.add_line(self._point3d(*frame.origin),self._point3d(*end),color=color,width=2,layer=46,draw_overlay=True)
            self.canvas.add_text(self._point3d(*end),label,color=color,layer=48,draw_overlay=True)
        self.canvas.add_text(self._point3d(*frame.origin),name,color=overlay("n"),layer=48,draw_overlay=True)

    def sketch_radius_label_position(self,entity):
        import numpy as np
        from anygeometry import sample_sketch_entity
        task=self.construction_task
        local=task.sample_entity(entity,count=5)[2]
        point=np.asarray(task.plane.world(local),dtype=float)
        axis=np.asarray(task.plane.y_axis,dtype=float)
        a=self.project_point(self._point3d(*point));b=self.project_point(self._point3d(*(point+axis)))
        scale=float(np.linalg.norm(np.asarray(b[:2])-a[:2])) if a is not None and b is not None else 0.
        ratio=self.canvas.event_widget.devicePixelRatioF()
        return point+axis*(16*ratio/scale if scale>1e-10 else self._construction_workplane.grid_spacing*.25)

    def sketch_point_at(self,x,y,radius=9):
        task=self.construction_task
        candidates=[]
        # Fixed reference points are pickable (selection only; dragging is refused by the task).
        for key,point in list(zip(task.point_keys,task.points))+[(k,task.reference_world(k)) for k in getattr(task,'reference_points',())]:
            q=self.project_point(self._point3d(*point))
            if q is not None:candidates.append(((q[0]-x)**2+(q[1]-y)**2,key))
        nearest=min(candidates,default=(float('inf'),None))
        return nearest[1] if nearest[0]<=radius**2 else None

    def sketch_entity_at(self,x,y,radius=8):
        import numpy as np
        from anygeometry import sample_sketch_entity
        task=self.construction_task;definition=task.definition(0,allow_incomplete=True);nearest=(float('inf'),None)
        for entity in task.entities:
            samples=[self.project_point(self._point3d(*task.plane.world(p))) for p in task.sample_entity(entity)]
            for a,b in zip(samples,samples[1:]):
                if a is None or b is None:continue
                a=np.asarray(a[:2]);d=np.asarray(b[:2])-a;q=np.asarray((x,y));den=float(d@d);t=0 if den==0 else float(np.clip((q-a)@d/den,0,1));distance=float(np.linalg.norm(q-a-t*d))
                if distance<nearest[0]:nearest=(distance,entity.id)
        return nearest[1] if nearest[0]<=radius else None

    def _draw_construction_overlay(self):
        super()._draw_construction_overlay()
        self._sketch_coordinates.bind()
        self._sketch_coordinates.sync()
        self._draw_plane_preview()
        from ...geometry.sketching import FaceSketchTask
        task=self.construction_task
        if isinstance(task,FaceSketchTask):
            self._draw_sketch_references(task)
            selected=[task.key_world(key) for key in task.selected if key in task.point_keys or key in task.reference_points]
            if selected:self.canvas.add_markers([self._point3d(*p) for p in selected],colors=['#e65100']*len(selected),size=[13]*len(selected),layer=49)
            from anygeometry import sample_sketch_entity,sketch_entity_radius
            definition=task.definition(0,allow_incomplete=True)
            for entity in task.entities:
                if entity.id in task.selected_entities:
                    samples=[task.plane.world(p) for p in task.sample_entity(entity)]
                    for a,b in zip(samples,samples[1:]):self.canvas.add_line(self._point3d(*a),self._point3d(*b),color='#e65100',width=5,layer=49,draw_overlay=True)
                if entity.kind in ('arc','circle','fillet'):
                    point=self.sketch_radius_label_position(entity)
                    self.canvas.add_text(self._point3d(*point),'R '+self._window.project.units.format(sketch_entity_radius(definition,entity),'length'),color=overlay('label'),layer=48,draw_overlay=True)
                if entity.kind=='nurbs':
                    control=[task.points[task.point_keys.index(k)] for k in entity.points]
                    for a,b in zip(control,control[1:]):self.canvas.add_line(self._point3d(*a),self._point3d(*b),color='#95a5b7',width=1,layer=44,draw_overlay=True)
            for number,point in enumerate(task.points,1):
                self.canvas.add_text(self._point3d(*point),str(number),color=overlay("label"),layer=48,draw_overlay=True)
            plane=self._construction_workplane
            frame=plane.resolve(self._construction_coordinate_systems)
            for coordinates,label,color in (((plane.grid_spacing,0),"u",overlay("u")),((0,plane.grid_spacing),"v",overlay("v"))):
                end=frame.world_position(coordinates)
                self.canvas.add_line(self._point3d(*frame.origin),self._point3d(*end),color=color,width=2,layer=46,draw_overlay=True)
                self.canvas.add_text(self._point3d(*end),label,color=color,layer=48,draw_overlay=True)
        draft = self.plate_draft
        if draft is None:return
        try:draft.check_binding()
        except (ValueError, PermissionError):
            self.cancel_plate();return
        points = [self._window.project.geometry.vertices[identifier].position for identifier in draft.ids]
        pairs = list(zip(points, points[1:]))
        if len(points) >= 3:pairs.append((points[-1], points[0]))
        for start, end in pairs:
            self.canvas.add_line(self._point3d(*start), self._point3d(*end), color=overlay("n"), width=3, layer=45, draw_overlay=True)
        for index, point in enumerate(points):
            self.canvas.add_text(self._point3d(*point), str(index+1), color=overlay("n"), layer=47, draw_overlay=True)

    def _draw_sketch_references(self,task):
        """Stippled fixed references: green links, yellow detached snapshots."""
        references=list(task.references)+list(task.axis_references.values())
        if not references:return
        from PySide6.QtGui import QColor
        dark=QColor(self.canvas.bg).lightness()<128
        for item in references:
            intrinsic=item.id in task.axis_references
            linked=intrinsic or task.is_linked(item.id)
            color=('#55dd77' if dark else '#16752b') if linked else ('#ffda44' if dark else '#947000')
            # A section stores one independent 2-point curve per clipped interval
            # ('<id>:<n>'); intervals and holes are never joined to each other.
            for name,curve in task.reference_curves.items():
                if name!=item.id and not name.startswith(item.id+':'):continue
                world=[task.plane.world(p) for p in curve]
                self._draw_reference_stipple(world,color)
            world=[task.reference_world(key) for key in item.keys]
            self.canvas.add_markers([self._point3d(*p) for p in world],colors=[color]*len(world),size=[11]*len(world),layer=47)
            label=('Global origin' if item.id=='global-axis-origin' else item.id.removeprefix('global-axis-')+' axis ∩ plane') if intrinsic else item.id+('' if linked else ' (detached)')
            self.canvas.add_text(self._point3d(*world[0]),label,color=color,layer=48,draw_overlay=True)
        formatter=getattr(self,'_construction_length_formatter',None)
        for c in task.constraints:
            if c.kind!='distance' or (c.first not in task.reference_points and c.second not in task.reference_points):continue
            mid=(task.key_world(c.first)+task.key_world(c.second))/2
            self.canvas.add_text(self._point3d(*mid),formatter(float(c.value)) if callable(formatter) else f'{float(c.value):.6g} m',color=overlay('label'),layer=47,draw_overlay=True)

    def _draw_reference_stipple(self,world,color):
        import numpy as np
        # Both viewer backends expose solid lines. Emit short, separated spans
        # with a continuous phase across the sampled curve. Bound their count
        # for long/offscreen references to keep sketch redraws responsive.
        points=[np.asarray(p,dtype=float) for p in world]
        projected=[self.project_point(self._point3d(*p)) for p in points]
        lengths=[float(np.linalg.norm(np.asarray(b[:2])-a[:2])) if a is not None and b is not None else 0.
                 for a,b in zip(projected,projected[1:])]
        period=max(12*self.canvas.event_widget.devicePixelRatioF(),sum(lengths)/256)
        phase=0.
        for a,b,length in zip(points,points[1:],lengths):
            offset=0.
            while offset<length:
                in_period=(phase+offset)%period
                painted=in_period<period*.6
                step=min(length-offset,(period*.6 if painted else period)-in_period)
                if step<1e-8:
                    offset+=1e-8;continue
                if painted:
                    first=a+(b-a)*(offset/length);last=a+(b-a)*((offset+step)/length)
                    self.canvas.add_line(self._point3d(*first),self._point3d(*last),color=color,width=3,layer=44,draw_overlay=True)
                offset+=step
            phase=(phase+length)%period

    def set_collection_highlight(self,references):
        references=frozenset(references)
        if references == self._collection_refs:return
        self._collection_refs=references
        self._apply_highlight()

    def dismiss_feature_highlight(self):
        changed=bool(self._feature_ids or self._collection_refs)
        self._collection_refs=frozenset()
        self._feature_ids=frozenset()
        if changed:self._apply_highlight()
        tree=getattr(self._window,"tree",None)
        if tree is not None:tree.dismiss_feature_selection()

    def _highlight_tags(self):
        tags=set(super()._highlight_tags())
        if (self._feature_ids or self._collection_refs) and self._scene is not None:
            from anygeometry import feature_entity_owners
            owners=feature_entity_owners(self._window.project.geometry) if self._feature_ids else {}
            for group in (self._scene.faces,self._scene.lines,self._scene.points):
                for item in group:
                    refs=item.owners or (() if item.ref is None else (item.ref,))
                    from ...selection import entity_tag
                    tags.update(entity_tag(ref) for ref in refs if ref in self._collection_refs or owners.get(ref) in self._feature_ids)
        return sorted(tags)

    def _watch_backend(self,parent):
        self.canvas.render_error.connect(lambda message:parent.set_status(message,error=True))
        self.canvas.backend_changed.connect(lambda _:self.canvas.event_widget.installEventFilter(self._keys))
        self._sketch_coordinates.bind()
        self.canvas.backend_changed.connect(lambda _:self._sketch_coordinates.bind())

    def switch_backend(self, backend):
        if backend not in {"auto","gpu","software"}:
            raise ValueError("backend must be auto, gpu or software")
        self.cancel_plate()
        self._cancel_interaction_restore()
        # Restore the surviving canvas before attempting a backend that may
        # fail. Otherwise failed creation leaves sampled picking data behind.
        self._restore_full_scene()
        old = self.canvas
        old_retained = (getattr(self, "_retained_result", None),
                        getattr(self, "_retained_result_canvas", None))
        old_opacity = getattr(self, '_retained_opacity', None)
        old_styles = getattr(self, '_retained_styles', None)
        old_retained_scene = getattr(self, "_retained_scene", None)
        state = old.export_view_state()
        candidate = create_viewer(self.widget, backend=backend, host=QtViewerHostAdapter())
        try:
            self.canvas = candidate
            self._configure_canvas(candidate)
            candidate.apply_view_state(state, redraw=False)
            if self._scene is not None:
                self._render_scene(self._scene)
            candidate.event_widget.installEventFilter(self._keys)
        except BaseException:
            self.canvas = old
            self._retained_result, self._retained_result_canvas = old_retained
            self._retained_opacity = old_opacity
            self._retained_styles = old_styles
            self._retained_scene = old_retained_scene
            candidate.destroy()
            self._sketch_coordinates.bind()
            self._sketch_coordinates.sync()
            raise
        self.layout.replaceWidget(old, candidate)
        if old_retained_scene is not None and not old_retained_scene[2].removed:
            old_retained_scene[2].remove()
        old.destroy()
        candidate.show()
        candidate.redraw()
        self._watch_backend(self.widget.parent())
        self._requested_backend = backend
        return self.active_backend

    def _position_colour_legend(self):
        label=getattr(self,"_colour_legend",None)
        data=getattr(self,"_colour_legend_data",None)
        if label is None or data is None:return
        from html import escape
        width=max(80,min(self.visualization.legend_width,self.widget.width()-24))
        height=max(24,self.widget.height()-24)
        label.setFixedWidth(width)
        entries=data.get("entries",())
        count=min(12,len(entries))
        def render(count):
            rows=['<b>'+escape(str(data.get("title","")))+'</b>']
            rows += ['<span style="color:'+escape(color)+'">■</span> '+escape(text) for text,color in entries[:count]]
            if len(entries)>count:rows.append('+'+str(len(entries)-count)+' more categories')
            if data.get("note"):rows.append(escape(str(data["note"])))
            label.setText('<br>'.join(rows))
            return max(1,label.heightForWidth(width))
        needed=render(count)
        while needed>height and count>0:
            count-=1;needed=render(count)
        if needed>height:
            # Even the title/note can exceed a very small canvas at large fonts.
            # Keep a short honest summary; the full key remains on the toolbar.
            from PySide6.QtGui import QFont, QFontMetrics
            bold=QFont(label.font());bold.setBold(True)
            short=QFontMetrics(bold).elidedText(str(data.get("title","")),Qt.ElideRight,max(1,width-2*label.margin()-12))
            summary='+'+str(len(entries))+' more'
            label.setText('<b>'+escape(short)+'</b><br>'+escape(summary))
            needed=max(1,label.heightForWidth(width))
        label.setFixedHeight(min(height,needed))
        label.setProperty("visible_colour_entries",count)
        label.move(max(8,self.widget.width()-width-12),12);label.raise_()

    def _draw_legend(self, scene):
        entries=(scene.legend or {}).get("entries")
        label=getattr(self,"_colour_legend",None)
        if entries is None or not self.visualization.show_legend:
            self._colour_legend_data=None
            if label is not None:label.hide()
            return super()._draw_legend(scene)
        self.canvas.clear_thickness_legend()
        if label is None:
            label=QLabel(self.widget);label.setObjectName("ModelColourLegend")
            label.setAttribute(Qt.WA_TransparentForMouseEvents);label.setWordWrap(True)
            label.setAutoFillBackground(True);label.setMargin(9)
            label.setAlignment(Qt.AlignLeft|Qt.AlignTop)
            self._colour_legend=label
        font=label.font();font.setPointSize(self.visualization.legend_font_size);label.setFont(font)
        self._colour_legend_data=scene.legend
        label.setToolTip('\n'.join([scene.legend.get("title","")]+[text for text,_ in entries]))
        self._position_colour_legend();label.show()

    def close(self):
        self.cancel_plate()
        self._closed=True
        self._retained_result = self._retained_result_canvas = None
        self._retained_opacity = None
        self._retained_styles = None
        cached = getattr(self, "_retained_scene", None)
        if cached is not None and not cached[2].removed:
            cached[2].remove()
        self._retained_scene = None
        self.selection.remove_listener(self._apply_highlight)
        self._cancel_interaction_restore()
        self.canvas.destroy()
