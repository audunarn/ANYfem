"""Qt rendering of the existing toolkit-neutral engineering history series."""
import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class HistoryPlot(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.series=[];self.setMinimumHeight(160)
    def show_series(self,series):
        self.series=list(series);self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor("#ffffff"))
        painter.setRenderHint(QPainter.Antialiasing)
        curves=[]
        for item in self.series:
            finite=np.isfinite(item.x)&np.isfinite(item.y)
            if finite.any():curves.append((item,item.x[finite],item.y[finite]))
        if not curves:
            painter.drawText(self.rect(),Qt.AlignCenter,"No history data");return
        xmin=min(float(x.min()) for _,x,_ in curves);xmax=max(float(x.max()) for _,x,_ in curves)
        ymin=min(float(y.min()) for _,_,y in curves);ymax=max(float(y.max()) for _,_,y in curves)
        dx=xmax-xmin or 1.;dy=ymax-ymin or 1.
        left,top,right,bottom=60,25,self.width()-20,self.height()-40
        painter.setPen(QPen(QColor("#334155"),1))
        painter.drawLine(left,bottom,right,bottom);painter.drawLine(left,top,left,bottom)
        for index,(item,x,y) in enumerate(curves):
            color=["#2563eb","#dc2626","#059669","#7c3aed"][index%4]
            painter.setPen(QPen(QColor(color),1.5))
            points=[QPointF(left+(float(a)-xmin)/dx*(right-left),bottom-(float(b)-ymin)/dy*(bottom-top)) for a,b in zip(x,y)]
            for a,b in zip(points,points[1:]):painter.drawLine(a,b)
            painter.drawText(left+index*120,15,item.name)
        painter.setPen(QColor("#334155"))
        painter.drawText(left,bottom+17,f"{xmin:g}");painter.drawText(right-50,bottom+17,f"{xmax:g}")
        painter.drawText(2,top+10,f"{ymax:.3g}");painter.drawText(2,bottom,f"{ymin:.3g}")
        item=curves[0][0]
        painter.drawText(left,bottom+32,f"{item.x_label} ({item.x_unit}) · {item.y_label} ({item.y_unit})")
