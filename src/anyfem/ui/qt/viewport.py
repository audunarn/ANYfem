"""Qt ownership of the shared ANYfem scene presenter and ANY3dView host."""
from PySide6.QtCore import QObject, QEvent, Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
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

    def eventFilter(self, target, event):
        if target is self.viewport.canvas.event_widget:
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
                           and event.modifiers() == Qt.NoModifier)
                self._right_press = None
                if clicked:
                    # Let the viewer finish its orbit gesture before opening a menu.
                    QTimer.singleShot(0, target, lambda: self.viewport.context_menu_at(target, position))
            elif event.type() in {QEvent.FocusOut, QEvent.Hide}:
                self._right_press = None
        if target is self.viewport.widget and event.type()==QEvent.Show:
            self.viewport._schedule_automatic_backend()
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape and not self.viewport.construction_active:
            self.viewport.dismiss_feature_highlight()
            self.viewport.selection.clear()
        if event.type() == QEvent.KeyPress and self.viewport.construction_active:
            if event.key() == Qt.Key_Escape:
                self.viewport.cancel_construction()
                return True
            if event.key() in {Qt.Key_Return, Qt.Key_Enter}:
                self.viewport._handle_construction_enter()
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
        self._feature_ids = frozenset()
        self._collection_refs = frozenset()
        self.visibility = SelectionVisibility()
        self._visibility_scene = None
        self._visibility_interaction_scene = None
        self._visibility_mesh = None
        self._visibility_job = None
        self._window = parent
        # Probe OpenGL only once Qt has exposed the containing window. A hidden
        # startup widget can otherwise leave an invalid composition surface.
        self.canvas = create_viewer(self.widget, backend="software" if backend=="auto" else backend, host=QtViewerHostAdapter())
        self.layout.addWidget(self.canvas)
        self._configure_canvas(self.canvas)
        self._keys = _ConstructionKeys(self)
        self.widget.installEventFilter(self._keys)
        self.canvas.event_widget.installEventFilter(self._keys)
        self._watch_backend(parent)
        self.selection.add_listener(self._apply_highlight)

    def _schedule_automatic_backend(self):
        if not self._automatic_pending:return
        self._automatic_pending=False
        def initialize():
            if not self._closed and self._requested_backend=="auto":
                self.switch_backend("auto")
        QTimer.singleShot(0,self.widget,initialize)

    def set_feature_highlight(self,identifiers):
        self._feature_ids=frozenset(identifiers)
        self._apply_highlight()

    def show(self, scene, *, interaction_scene=None, reset_view=False):
        mesh=self._window.mesh;job=self._window.active_job_id
        if mesh is not self._visibility_mesh or job != self._visibility_job:
            # Node/element IDs belong to one mesh/result realization.
            self.visibility.hidden={key for key in self.visibility.hidden if key[0] in {"vertex","edge","face"}}
            self._visibility_mesh=mesh;self._visibility_job=job
        self._visibility_scene = scene
        self._visibility_interaction_scene = interaction_scene
        super().show(self.visibility.filter_scene(scene),
            interaction_scene=None if interaction_scene is None else self.visibility.filter_scene(interaction_scene),
            reset_view=reset_view)

    def refresh_visibility(self):
        if self._visibility_scene is not None:
            self.show(self._visibility_scene, interaction_scene=self._visibility_interaction_scene)

    def reset_visibility(self):
        self.visibility.hidden.clear()
        self._visibility_scene = None
        self._visibility_interaction_scene = None
        self._visibility_mesh = None
        self._visibility_job = None

    def clear(self):
        self._visibility_scene = None
        self._visibility_interaction_scene = None
        super().clear()

    def _handle_selection_event(self,event):
        self.dismiss_feature_highlight()
        super()._handle_selection_event(event)

    def context_menu_at(self, surface, position):
        if self._closed or surface is not self.canvas.event_widget or self.construction_active:
            return
        actions=getattr(self._window,"geometry_actions",None)
        if actions is None or self._window._view_mode != "geometry" or not self._window.canvas_action_menu.isChecked():
            return
        ratio = surface.devicePixelRatioF()
        point = (round(position.x() * ratio), round(position.y() * ratio))
        hits = self.canvas.query_point(*point)[:1]
        if not hits:
            return
        self.dismiss_feature_highlight()
        self.selection.apply_owners([hit.owner for hit in hits], "replace")
        actions.canvas_context_menu(SimpleNamespace(hits=hits, end=point))

    def set_collection_highlight(self,references):
        self._collection_refs=frozenset(references)
        self._apply_highlight()

    def dismiss_feature_highlight(self):
        self._collection_refs=frozenset()
        self.set_feature_highlight(())
        tree=getattr(self._window,"tree",None)
        if tree is not None:tree.dismiss_feature_selection()

    def _apply_highlight(self):
        super()._apply_highlight()
        if (self._feature_ids or self._collection_refs) and self._scene is not None:
            from anygeometry import feature_entity_owners
            owners=feature_entity_owners(self._window.project.geometry)
            tags=set(self.selection.tags())
            for group in (self._scene.faces,self._scene.lines,self._scene.points):
                for item in group:
                    refs=item.owners or (() if item.ref is None else (item.ref,))
                    if any(ref in self._collection_refs or owners.get(ref) in self._feature_ids for ref in refs):tags.add(item.tag)
            self.canvas.set_highlight(sorted(tags))

    def _watch_backend(self,parent):
        self.canvas.render_error.connect(lambda message:parent.set_status(message,error=True))
        self.canvas.backend_changed.connect(lambda _:self.canvas.event_widget.installEventFilter(self._keys))

    def switch_backend(self, backend):
        if backend not in {"auto","gpu","software"}:
            raise ValueError("backend must be auto, gpu or software")
        old = self.canvas
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
            candidate.destroy()
            raise
        self.layout.replaceWidget(old, candidate)
        old.destroy()
        candidate.show()
        candidate.redraw()
        self._watch_backend(self.widget.parent())
        self._requested_backend = backend
        return self.active_backend

    def close(self):
        self._closed=True
        self.selection.remove_listener(self._apply_highlight)
        self._cancel_interaction_restore()
        self.canvas.destroy()
