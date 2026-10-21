"""Meshed "ANYfem" wordmark shown over the canvas while the model has no geometry."""
import math

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF)
from PySide6.QtWidgets import QWidget

from .theme import is_dark

TEXT="ANYfem"
TAGLINE="Finite element modelling & analysis"
_PALETTE={False:{"a":QColor("#1f5fae"),"b":QColor("#19a7b8"),"mesh":QColor(10,20,40,140),
                 "outline":QColor("#1a3f7a"),"tag":QColor("#68768a")},
          True:{"a":QColor("#3a79c7"),"b":QColor("#3cc6d6"),"mesh":QColor(8,14,26,170),
                "outline":QColor("#cfe6ff"),"tag":QColor("#93a0b3")}}


_STOPS=((0.0,(36,52,170)),(0.2,(30,120,230)),(0.4,(30,200,190)),(0.55,(70,205,90)),
        (0.72,(240,220,50)),(0.86,(250,140,30)),(1.0,(225,40,35)))
# smooth synthetic "stress" field: (u, v, strength, spread) hotspots over a gentle left-to-right ramp
_HOTSPOTS=((0.08,0.35,0.55,0.07),(0.27,0.7,0.5,0.08),(0.46,0.3,0.35,0.07),(0.62,0.55,0.75,0.09),
           (0.8,0.25,0.65,0.08),(0.93,0.7,0.8,0.07))


def _stress(u,v):
    value=0.12+0.2*u
    for hu,hv,strength,spread in _HOTSPOTS:
        distance=((u-hu)/spread)**2+((v-hv)/(spread*2.6))**2
        value+=strength*math.exp(-distance)
    return max(0.0,min(1.0,value))


def _stress_color(t):
    for (a,ca),(b,cb) in zip(_STOPS,_STOPS[1:]):
        if t<=b:
            k=(t-a)/(b-a)
            return QColor(*(int(ca[n]+(cb[n]-ca[n])*k) for n in range(3)))
    return QColor(*_STOPS[-1][1])


def _glyph_path(size):
    font=QFont("Segoe UI");font.setPixelSize(int(size));font.setWeight(QFont.Black);font.setLetterSpacing(QFont.AbsoluteSpacing,size*0.04)
    path=QPainterPath();metrics=QFontMetricsF(font)
    path.addText(0,0,font,TEXT)
    bounds=path.boundingRect()
    path.translate(-bounds.center())
    return path,metrics


class Wordmark(QWidget):
    """Transparent, mouse-transparent overlay filling its parent; repaints in the active theme."""

    def __init__(self,parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self._cache=None
        parent.installEventFilter(self)
        self._fit()

    def eventFilter(self,target,event):
        if target is self.parent() and event.type() in (QEvent.Resize,QEvent.Show,QEvent.ChildAdded,QEvent.LayoutRequest):
            self._fit()
        return False

    def _fit(self):
        self.setGeometry(self.parent().rect())
        self.raise_()

    def set_active(self,active):
        if self.isVisibleTo(self.parent())!=bool(active):
            self.setVisible(bool(active))
            if active:self._fit()

    def _build(self,width):
        size=max(40.0,min(width*0.2,self.height()*0.28))
        path,_=_glyph_path(size)
        bounds=path.boundingRect()
        step=max(4.0,size/15.0);height=step*math.sqrt(3)/2
        triangles=[]
        rows=int(bounds.height()//height)+3;cols=int(bounds.width()//step)+4
        for row in range(-1,rows):
            y0=bounds.top()+row*height;y1=y0+height
            for col in range(-2,cols):
                x=bounds.left()+col*step+(row%2)*step/2
                # up-pointing and down-pointing triangle of the lattice cell
                for points in ((QPointF(x,y0),QPointF(x+step,y0),QPointF(x+step/2,y1)),
                               (QPointF(x+step/2,y1),QPointF(x+step*1.5,y1),QPointF(x+step,y0))):
                    cx=sum(p.x() for p in points)/3;cy=sum(p.y() for p in points)/3
                    if path.contains(QPointF(cx,cy)):
                        u=(cx-bounds.left())/bounds.width();v=(cy-bounds.top())/bounds.height()
                        triangles.append((QPolygonF(list(points)),_stress_color(_stress(u,v))))
        return {"size":size,"path":path,"triangles":triangles,"bounds":bounds,"step":step,"width":width}

    def paintEvent(self,event):
        if self.width()<200 or self.height()<160:return
        if self._cache is None or self._cache["width"]!=self.width() or self._cache.get("h")!=self.height():
            self._cache=self._build(self.width());self._cache["h"]=self.height()
        c=self._cache;colors=_PALETTE[is_dark()]
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(self.width()/2,self.height()*0.46)
        bounds=c["bounds"];path=c["path"]
        # soft glow behind the letters
        for width,alpha in ((c["size"]*0.12,10),(c["size"]*0.06,20),(c["size"]*0.025,40)):
            glow=QColor(colors["outline"]);glow.setAlpha(alpha)
            painter.setBrush(Qt.NoBrush);painter.setPen(QPen(glow,width,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
            painter.drawPath(path)
        # stress-coloured triangles with thin mesh edges
        painter.save();painter.setClipPath(path)
        edge=QPen(colors["mesh"],max(0.5,c["step"]/26));edge.setJoinStyle(Qt.MiterJoin)
        for polygon,color in c["triangles"]:
            painter.setBrush(color);painter.setPen(edge);painter.drawPolygon(polygon)
        painter.restore()
        painter.setBrush(Qt.NoBrush);painter.setPen(QPen(colors["outline"],max(1.2,c["size"]/80),Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
        painter.drawPath(path)
        font=QFont("Segoe UI");font.setPixelSize(max(11,int(c["size"]/6)));font.setLetterSpacing(QFont.AbsoluteSpacing,2.0)
        painter.setFont(font);painter.setPen(colors["tag"])
        painter.drawText(QRectF(-self.width()/2,bounds.bottom()+c["size"]*0.18,self.width(),c["size"]*0.3),Qt.AlignHCenter|Qt.AlignTop,TAGLINE)
        painter.end()
