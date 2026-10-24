"""Sparse world-coordinate ticks on the visible edges of a sketch viewport."""
import math

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, QLineF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .theme import overlay


class SketchCoordinates(QWidget):
    """Camera-dependent presentation; never creates scene geometry or timers.

    The sibling overlay follows viewer paints, including programmatic camera
    changes. Its own paint uses cached labels, so it cannot schedule another
    viewer repaint. Input continues to reach the canvas underneath.
    """

    def __init__(self, viewport):
        super().__init__(viewport.widget)
        self.viewport = viewport
        self.surface = None
        self.samples = ()
        self._signature = None
        self.heading = ""
        self.global_origin = None
        self.global_axes = ()
        self.origin_off_plane = False
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.hide()

    def bind(self):
        surface = self.viewport.canvas.event_widget
        if surface is not self.surface:
            if self.surface is not None:
                # A renderer switch can already have destroyed its old surface.
                from shiboken6 import isValid
                if isValid(self.surface):
                    self.surface.removeEventFilter(self)
            self.surface = surface
            surface.installEventFilter(self)
            self._signature = None

    def eventFilter(self, watched, event):
        if watched is self.surface and event.type() in (QEvent.Paint, QEvent.Resize):
            self.sync()
        return False

    def sync(self):
        from ...geometry.sketching import FaceSketchTask

        viewport = self.viewport
        if not isinstance(viewport.construction_task, FaceSketchTask):
            self.samples = ()
            self.global_origin = None
            self.global_axes = ()
            self._signature = None
            self.hide()
            return
        surface = self.surface
        rect = QRect(surface.mapTo(viewport.widget, QPoint()), surface.size())
        if self.geometry() != rect:
            self.setGeometry(rect)
        width, height = self.width(), self.height()
        units = viewport._window.project.units
        frame = viewport._construction_workplane.resolve(viewport._construction_coordinate_systems)
        ratio = surface.devicePixelRatioF()
        # A constant number of rays and labels, independent of grid density,
        # model size and the number of sketch entities. Coordinates correspond
        # to the tick itself, not the displaced text box or the local u/v axes.
        samples = []
        for edge, x, y in (("bottom", width*.4, height-1),
                           ("bottom", width*.8, height-1),
                           ("left", 0, height*.3),
                           ("left", 0, height*.65)):
            point = viewport.canvas.unproject_to_plane(
                x*ratio, y*ratio, tuple(frame.origin), tuple(frame.normal))
            if point is None:
                continue  # edge-on or behind the camera: no invented position
            world = frame.project(tuple(point))
            values = units.to_display(world, "length")
            # Suppress floating-point signed zero without rounding small models
            # to zero or changing the model's actual geometry.
            text = "  ".join(f"{axis}={float(value) if value != 0 else 0:.5g}"
                             for axis, value in zip("XYZ", values))
            samples.append((edge, round(x), round(y), text))
        heading = f"World coordinates ({units.symbol('length')})"
        origin=viewport.project_point(viewport._point3d(0,0,0))
        origin=tuple(float(v)/ratio for v in origin[:2]) if origin is not None else None
        if origin is not None and not all(math.isfinite(v) for v in origin):origin=None
        # Axis directions use the local camera projection at the sketch centre
        # so orientation remains readable even when the true origin is offscreen.
        basis=(0.,0.,0.) if origin is not None and 0<=origin[0]<width and 0<=origin[1]<height else frame.origin
        centre=viewport.project_point(viewport._point3d(*basis))
        axes=[]
        for index,name in enumerate('XYZ'):
            point=list(basis);point[index]+=max(viewport._construction_workplane.grid_spacing,1.)
            end=viewport.project_point(viewport._point3d(*point))
            if centre is None or end is None:continue
            dx,dy=end[0]-centre[0],end[1]-centre[1]
            length=math.hypot(dx,dy)
            if math.isfinite(length):axes.append((name,dx/length,dy/length) if length>1e-6 else (name,0.,0.))
        off_plane=abs(float(frame.normal_distance((0,0,0))))>1e-9
        signature = (rect.getRect(), heading, tuple(samples), overlay("label"),origin,tuple(axes),off_plane)
        if signature != self._signature:
            self._signature = signature
            self.samples = tuple(samples)
            self.heading = heading
            self.global_origin = origin
            self.global_axes = tuple(axes)
            self.origin_off_plane = off_plane
            self.update()
        self.show()
        self.raise_()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        metrics = painter.fontMetrics()
        foreground = QColor(overlay("label"))
        background = self.viewport.widget.palette().window().color()
        background.setAlpha(235)
        occupied = []

        origin=self.global_origin
        visible=origin is not None and 0<=origin[0]<self.width() and 0<=origin[1]<self.height()
        axis_colors={'X':'#b75e65','Y':'#509b70','Z':'#638fc8'}
        painter.setRenderHint(QPainter.Antialiasing)
        # Thin, low-contrast global axes through the actual projected origin.
        # A compact orientation triad stays visible when that origin is outside.
        anchor=origin if visible else (self.width()-62.,65.)
        for name,dx,dy in self.global_axes:
            color=QColor(axis_colors[name]);color.setAlpha(95)
            painter.setPen(QPen(color,1.))
            if dx==0 and dy==0:
                painter.drawEllipse(QPointF(*anchor),3.,3.)
                color.setAlpha(190);painter.setPen(color)
                painter.drawText(QPointF(anchor[0]-12,anchor[1]-10),name)
                continue
            if visible:
                span=math.hypot(self.width(),self.height())
                painter.drawLine(QLineF(anchor[0]-dx*span,anchor[1]-dy*span,
                                        anchor[0]+dx*span,anchor[1]+dy*span))
            else:
                painter.drawLine(QLineF(anchor[0],anchor[1],anchor[0]+dx*28,anchor[1]+dy*28))
            color.setAlpha(190);painter.setPen(color)
            painter.drawText(QPointF(anchor[0]+dx*32,anchor[1]+dy*32),name)

        def label(x, y, text):
            rect = QRect(x, y, metrics.horizontalAdvance(text)+12, metrics.height()+6)
            rect.moveLeft(max(3, min(rect.left(), self.width()-rect.width()-3)))
            if (rect.right() >= self.width() or rect.bottom() >= self.height()
                    or any(rect.intersects(other) for other in occupied)):
                return False
            occupied.append(rect.adjusted(-3, -3, 3, 3))
            painter.fillRect(rect, background)
            painter.setPen(foreground)
            painter.drawText(rect.adjusted(6, 3, -6, -3), Qt.AlignCenter, text)
            return True

        label(8, 8, self.heading)
        text='Global (0,0,0)'
        if self.origin_off_plane:text+=' · off plane'
        if visible:
            x,y=origin
            painter.setPen(QPen(foreground,1.))
            painter.drawEllipse(QPointF(x,y),4.,4.)
            label(round(x+9),max(36,min(round(y+8),self.height()-55)),text)
        else:
            if origin is None:
                x,y=10,40;text+=' · behind view'
            else:
                x=max(12,min(round(origin[0]),self.width()-metrics.horizontalAdvance(text)-24))
                y=max(40,min(round(origin[1]),self.height()-60))
                # Direction to the real origin; the badge is never a fake origin.
                dx,dy=origin[0]-(x+6),origin[1]-(y+6);length=math.hypot(dx,dy)
                if length:
                    arrow=('→','↘','↓','↙','←','↖','↑','↗')[round(math.atan2(dy,dx)/(math.pi/4))%8]
                    text=arrow+' '+text
                text+=' · offscreen'
            label(x,y,text)
        for edge, x, y, text in self.samples:
            text_width = metrics.horizontalAdvance(text)+12
            if edge == "bottom":
                shown = label(x-text_width//2, y-metrics.height()-16, text)
                if shown:
                    painter.drawLine(x, y-7, x, y)
            else:
                shown = label(10, y-metrics.height()//2-3, text)
                if shown:
                    painter.drawLine(0, y, 7, y)
