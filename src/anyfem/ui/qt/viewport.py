"""Qt ownership of the shared ANYfem scene presenter and ANY3dView host."""
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout

from ...presentation.viewport import SceneViewport
from any3dview import create_viewer, Point3D
from any3dview.qt import QtViewerHostAdapter


class _ConstructionKeys(QObject):
    def __init__(self, viewport):
        super().__init__(viewport.widget)
        self.viewport = viewport

    def eventFilter(self, target, event):
        if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape and not self.viewport.construction_active:
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
        self.canvas = create_viewer(self.widget, backend=backend, host=QtViewerHostAdapter())
        self.layout.addWidget(self.canvas)
        self._configure_canvas(self.canvas)
        self._keys = _ConstructionKeys(self)
        self.canvas.event_widget.installEventFilter(self._keys)
        self._watch_backend(parent)
        self.selection.add_listener(self._apply_highlight)

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
        self._watch_backend(self.widget.parent())
        self._requested_backend = backend
        return self.active_backend

    def close(self):
        self.selection.remove_listener(self._apply_highlight)
        self._cancel_interaction_restore()
        self.canvas.destroy()
