"""Transient screen-space inspection callout over any Qt viewer backend."""
import math

from PySide6.QtCore import Qt, QPointF, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


class LocationCallout(QWidget):
    def __init__(self, viewport):
        super().__init__(viewport.widget)
        self.viewport = viewport
        self.reference = None
        self.world = None
        self.text = ''
        self._geometry = None
        self._revision = None
        self.setObjectName('GeometryLocationCallout')
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self.refresh)
        self.hide()

    def locate(self, reference, world, text):
        self.reference, self.world, self.text = reference, tuple(world), text
        self._geometry = self.viewport._window.project.geometry
        self._revision = self._geometry.revision
        self.refresh()
        self.show()
        self.raise_()
        self._timer.start()

    def dismiss(self):
        self._timer.stop()
        self.reference = self.world = None
        self.text = ''
        self._geometry = self._revision = None
        self.hide()

    def refresh(self):
        geometry = self.viewport._window.project.geometry
        if self.reference is not None and (geometry is not self._geometry or
                                          geometry.revision != self._revision):
            self.dismiss()
            return
        self.setGeometry(self.parentWidget().rect())
        self.update()

    def paintEvent(self, event):
        if self.world is None:
            return
        canvas = self.viewport.canvas
        projected = canvas.project_point(self.world)
        target = None
        inside = False
        if projected is not None and all(math.isfinite(value) for value in projected):
            x, y, depth = projected
            surface = canvas.event_widget
            offset = self.mapFromGlobal(surface.mapToGlobal(surface.rect().topLeft()))
            ratio = surface.devicePixelRatioF()
            x, y = x / ratio + offset.x(), y / ratio + offset.y()
            inside = depth > 0 and 0 <= x < self.width() and 0 <= y < self.height()
            target = QPointF(min(max(x, 8), max(8, self.width()-8)),
                             min(max(y, 8), max(8, self.height()-8)))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        text = self.text + ('' if inside else '\nOutside current view')
        metrics = painter.fontMetrics()
        lines = text.splitlines()
        width = min(max(metrics.horizontalAdvance(line) for line in lines)+20,
                    max(40, self.width()-16))
        height = metrics.height()*len(lines)+16
        left = min(max(8, target.x()+45), max(8, self.width()-width-8)) if target else 8
        top = min(max(8, target.y()-height-35), max(8, self.height()-height-8)) if target else 8
        box = QRectF(left, top, width, height)
        start = QPointF(box.center().x(), box.bottom())
        painter.setPen(QPen(QColor('#d97706'), 2.5))
        if target is not None:
            painter.drawLine(start, target)
        dx, dy = (start.x()-target.x(), start.y()-target.y()) if target else (0., 0.)
        length = math.hypot(dx, dy)
        if length:
            dx, dy = dx/length, dy/length
            painter.setBrush(QColor('#d97706'))
            painter.drawPolygon(QPolygonF((target,
                target+QPointF(12*dx-5*dy, 12*dy+5*dx),
                target+QPointF(12*dx+5*dy, 12*dy-5*dx))))
        painter.setBrush(QColor('#fff7ed'))
        painter.drawRoundedRect(box, 5, 5)
        painter.setPen(QColor('#7c2d12'))
        painter.drawText(box.adjusted(10, 8, -8, -6), Qt.AlignLeft | Qt.AlignTop, text)
