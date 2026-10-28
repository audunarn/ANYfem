"""Bounded six-resultant QPainter preview: shared zoom, extrema markers and draggable original-sample window."""
import numpy as np
from PySide6.QtCore import Qt,Signal,QRectF,QPointF
from PySide6.QtGui import QPainter,QColor,QPen,QPainterPath,QPolygonF,QFontMetricsF
from PySide6.QtWidgets import QWidget,QPushButton
from anyloads.wave_preview import pixel_extrema,select_sample_window

NAMES=('Fx','Fy','Fz','Mx','My','Mz')
HANDLE_PX=8.;MIN_DRAG_PX=5.
HINT='Drag in a plot: right = zoom in, left = zoom out. Drag blue bars to change the interval.'


class WaveResultantPlot(QWidget):
    selectionChanged=Signal(float,float)

    def __init__(self,parent=None):
        super().__init__(parent);self.setObjectName('WaveResultantPreview')
        self.setMinimumSize(620,440);self.times=None;self.values=None;self.interval=None;self._drag=None
        self._zoom=None;self._view=None;self._view_times=None
        self.setMouseTracking(True);self.composition_label='dynamic incident + diffraction'
        self.fit_all_button=QPushButton('Reset / fit all',self);self.fit_all_button.setObjectName('WavePlotFitAll')
        self.fit_all_button.setToolTip('Show the whole record in all six graphs. The selected interval is unchanged.')
        self.fit_selected_button=QPushButton('Fit selected interval',self);self.fit_selected_button.setObjectName('WavePlotFitSelected')
        self.fit_selected_button.setToolTip('Zoom all six graphs to the selected (blue) interval. The interval is unchanged.')
        self.fit_all_button.clicked.connect(self.fit_all);self.fit_selected_button.clicked.connect(self.fit_selected)
        for button in (self.fit_all_button,self.fit_selected_button):button.setFixedHeight(22)
        self.setToolTip(HINT);self._layout_buttons();self._sync()

    def _layout_buttons(self):
        self.fit_all_button.move(64,52);self.fit_selected_button.move(64+self.fit_all_button.sizeHint().width()+8,52)

    def _sync(self):
        enabled=self.times is not None and self.values is not None and self.interval is not None and len(self.times)>=2
        for button in (self.fit_all_button,self.fit_selected_button):
            if button.isEnabled()!=enabled:button.setEnabled(enabled)

    def update(self,*args):
        self._sync();super().update(*args)

    def resizeEvent(self,event):
        self._layout_buttons();super().resizeEvent(event)

    def set_data(self,times,values,interval=None):
        times=np.asarray(times,dtype=float);values=np.asarray(values,dtype=float)
        if values.shape!=(len(times),6) or not np.isfinite(values).all():raise ValueError('finite six-resultant record required')
        window=select_sample_window(times,*(interval or (None,None)))
        self.times=times;self.values=values/1000.;self.interval=window.interval
        self._view=None;self._zoom=None;self._drag=None;self.update()

    def set_composition(self,hydrostatic,follow=False):
        """Explicit pressure composition shown in the title: dynamic-only, dynamic + hydrostatic, or wave-following."""
        self.follow_surface=bool(follow)
        if getattr(self,'floating_caption',None):
            self.source_label='Applied floating hull pressure'
            self.composition_label='wave + motion + mean hydrostatic'
            self.update();return
        if follow:
            # Combined mode: the plotted 6-vector is the applied hull resultant of the wet/dry frame loads.
            self.source_label='Applied hull resultant'
            self.composition_label='wave-following wet/dry pressure (hydrostatic included)'
        else:
            self.source_label='Original hydro source'
            self.composition_label=('dynamic wave + steady hydrostatic'
                if hydrostatic else 'dynamic incident + diffraction (dynamic-only)')
        self.update()

    def set_floating_caption(self,text):
        """Truthful origin/basis label for the floating compound resultants (None restores the fixed-body captions)."""
        self.floating_caption=text;self.update()

    def moment_origin_caption(self):
        if getattr(self,'floating_caption',None):return self.floating_caption
        return ('Hydro origin (0, 0, 1.5) m in project coordinates' if getattr(self,'follow_surface',False)
            else 'WORLD0 moment origin (0, 0, 0) m')

    def set_interval(self,start,end):
        if self.times is None:return
        self.interval=select_sample_window(self.times,start,end).interval;self.update()

    # ----- shared time viewport -----
    @property
    def view_interval(self):
        """Visible absolute-time span shared by all six graphs, or None without data."""
        if self.times is None or self.values is None or len(self.times)<2:return None
        full=(float(self.times[0]),float(self.times[-1]))
        if self._view_times is not self.times:self._view=None;self._view_times=self.times
        if self._view is None or not full[0]<=self._view[0]<self._view[1]<=full[1]:self._view=full
        return self._view

    def _set_view(self,start,end):
        """Clamp to the record, keep at least two original samples visible."""
        if self.view_interval is None:return
        t=self.times;a=max(float(t[0]),float(min(start,end)));b=min(float(t[-1]),float(max(start,end)))
        i=int(np.searchsorted(t,a,'left'));j=int(np.searchsorted(t,b,'right'))
        if j-i<2:
            i=min(i,len(t)-2);a=min(a,float(t[i]));b=max(b,float(t[i+1]))
        self._view=(a,b) if b>a else (float(t[0]),float(t[-1]));self._zoom=None;self.update()

    def fit_all(self):
        if self.view_interval is not None:self._view=None;self.update()

    def fit_selected(self):
        if self.view_interval is not None and self.interval is not None:self._set_view(*self.interval)

    def _visible_slice(self):
        a,b=self.view_interval
        return int(np.searchsorted(self.times,a,'left')),int(np.searchsorted(self.times,b,'right'))

    def visible_extrema(self):
        """Per graph (min, t_min, max, t_max) over raw original samples in the viewport; earliest tie wins."""
        if self.view_interval is None:return []
        i,j=self._visible_slice();out=[]
        for k in range(6):
            v=self.values[i:j,k];lo=int(np.argmin(v));hi=int(np.argmax(v))
            out.append((float(v[lo]),float(self.times[i+lo]),float(v[hi]),float(self.times[i+hi])))
        return out

    def y_bounds(self):
        """Symmetric vertical half-range of each graph, fitted to its own visible samples."""
        if self.view_interval is None:return []
        i,j=self._visible_slice()
        return [max(float(np.max(np.abs(self.values[i:j,k])))*1.08,1e-9) for k in range(6)]

    def plot_rects(self):
        left=64.;top=106.;gapx=72.;gapy=47.
        width=max(60.,(self.width()-left-24.-gapx)/2)
        height=max(45.,(self.height()-top-42.-2*gapy)/3)
        # Forces left, moments right, each signed component on its own scale.
        return [QRectF(left+(i//3)*(width+gapx),top+(i%3)*(height+gapy),width,height) for i in range(6)]

    def _x(self,time,rect):
        a,b=self.view_interval or (0.,1.)
        return rect.left()+(time-a)/(b-a)*rect.width()

    def _time_at(self,x,rect):
        a,b=self.view_interval;return a+max(0.,min(1.,(x-rect.left())/rect.width()))*(b-a)

    def _label_rect(self,point,size,rect,prefer_above,taken):
        """First candidate beside the marker that stays in the plot and avoids earlier labels."""
        w,h=size;cands=[]
        for above in (prefer_above,not prefer_above):
            for left in (point.x()>rect.center().x(),point.x()<=rect.center().x()):
                x=point.x()-w-6 if left else point.x()+6;y=point.y()-h-3 if above else point.y()+3
                cands.append(QRectF(min(max(x,rect.left()+1),rect.right()-w-1),y,w,h))
        fits=[c for c in cands if rect.contains(c) and not any(c.intersects(t) for t in taken)]
        if fits:return fits[0]
        # Near-flat traces at a signed edge: stack labels in free rows of the plot, nearest to the marker first.
        rows=[rect.top()+1+k*(h+1) for k in range(max(0,int((rect.height()-2+1)//(h+1))))]
        for y in sorted(rows,key=lambda r:abs(r-point.y())):
            for left in (point.x()>rect.center().x(),point.x()<=rect.center().x()):
                x=point.x()-w-6 if left else point.x()+6
                c=QRectF(min(max(x,rect.left()+1),rect.right()-w-1),y,w,h)
                if rect.contains(c) and not any(c.intersects(t) for t in taken):return c
        c=QRectF(rect.right()-w-1,rect.top()+1 if prefer_above else rect.bottom()-h-1,w,h);return c

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.fillRect(self.rect(),QColor('#f7f9fc'))
        p.setPen(QColor('#18334f'))
        p.drawText(QRectF(15,8,self.width()-30,24),Qt.AlignCenter,QFontMetricsF(p.font()).elidedText(
            getattr(self,'source_label','Original hydro source')+' · '+self.composition_label,Qt.ElideRight,max(0.,self.width()-30)))
        p.setPen(QColor('#526477'));p.drawText(QRectF(15,29,self.width()-30,19),Qt.AlignCenter,
            QFontMetricsF(p.font()).elidedText(self.moment_origin_caption()+' · absolute record time · signed resultants',
                Qt.ElideRight,max(0.,self.width()-30)))
        self._sync()
        if self.view_interval is None:
            p.drawText(self.rect(),Qt.AlignCenter,'Prepare a wave record to preview forces and moments.');return
        font=p.font();hint_font=p.font();hint_font.setPointSizeF(max(6.,font.pointSizeF()-1.5));p.setFont(hint_font)
        hx=self.fit_selected_button.geometry().right()+10
        p.drawText(QRectF(hx,52,max(0.,self.width()-hx-12),22),Qt.AlignLeft|Qt.AlignVCenter,
            QFontMetricsF(hint_font).elidedText('Drag right: zoom in · drag left: zoom out · blue bars: interval',Qt.ElideRight,max(0.,self.width()-hx-12)))
        p.setFont(font)
        colors=('#1976b8','#16a085','#d47b22','#7e57a5','#b14b69','#506f9d')
        v0,v1=self.view_interval;i0,j0=self._visible_slice();i0=max(i0-1,0);j0=min(j0+1,len(self.times))
        label_font=p.font();label_font.setPointSizeF(max(6.,font.pointSizeF()-1.));metrics=QFontMetricsF(label_font)
        unit_names=('kN',)*3+('kNm',)*3
        for i,(rect,bound,ext) in enumerate(zip(self.plot_rects(),self.y_bounds(),self.visible_extrema())):
            def y(value):return rect.center().y()-value/bound*rect.height()/2
            p.fillRect(rect,QColor('white'))
            p.save();p.setClipRect(rect)
            a,b=self.interval;xa=self._x(a,rect);xb=self._x(b,rect)
            p.fillRect(QRectF(xa,rect.top(),xb-xa,rect.height()),QColor(48,131,202,28))
            p.setPen(QPen(QColor('#dce4ee'),1));p.drawLine(QPointF(rect.left(),y(0)),QPointF(rect.right(),y(0)))
            pixels=max(1,int(rect.width()))
            # Short traces keep one neighbour per side so the line reaches the edges; envelopes use viewport samples only.
            s0,s1=(i0,j0) if j0-i0<=2*pixels else self._visible_slice()
            x,low,high=pixel_extrema(self.times[s0:s1],self.values[s0:s1,i],pixels)
            p.setPen(QPen(QColor(colors[i]),1.25))
            if s1-s0>2*pixels:
                for time,lo,hi in zip(x,low,high):p.drawLine(QPointF(self._x(time,rect),y(lo)),QPointF(self._x(time,rect),y(hi)))
            else:
                path=QPainterPath();path.moveTo(self._x(x[0],rect),y(low[0]))
                for time,value in zip(x[1:],low[1:]):path.lineTo(self._x(time,rect),y(value))
                p.drawPath(path)
            p.restore()
            p.setPen(QPen(QColor('#dce4ee'),1));p.setBrush(Qt.NoBrush);p.drawRect(rect)
            for endpoint in self.interval:
                if not v0<=endpoint<=v1:continue
                p.setPen(QPen(QColor('#1676c3'),2));xx=self._x(endpoint,rect)
                p.drawLine(QPointF(xx,rect.top()),QPointF(xx,rect.bottom()))
                p.fillRect(QRectF(xx-3,rect.top()-4,6,8),QColor('#1676c3'))
            self._paint_extrema(p,rect,y,ext,unit_names[i],metrics,label_font)
            if self._zoom is not None and self._zoom[0]==rect:
                _,xs,xe=self._zoom;p.fillRect(QRectF(min(xs,xe),rect.top(),abs(xe-xs),rect.height()),QColor(230,150,40,50))
            p.setPen(QColor('#243d57'));label=NAMES[i]
            p.drawText(QRectF(rect.left(),rect.top()-22,rect.width(),18),Qt.AlignLeft,f'{label} ({unit_names[i]})')
            p.setPen(QColor('#61758b'))
            p.drawText(QRectF(0 if i<3 else rect.left()-65,rect.top()-4,60,18),Qt.AlignRight,f'{bound:.3g}')
            p.drawText(QRectF(0 if i<3 else rect.left()-65,rect.bottom()-15,60,18),Qt.AlignRight,f'{-bound:.3g}')
            p.drawText(QRectF(rect.left()-6,rect.bottom()+5,70,18),Qt.AlignLeft,f'{v0:.6g}')
            p.drawText(QRectF(rect.right()-70,rect.bottom()+5,76,18),Qt.AlignRight,f'{v1:.6g} s')

    def _paint_extrema(self,p,rect,y,ext,unit,metrics,font):
        lo,tlo,hi,thi=ext;taken=[]
        pmax=QPointF(self._x(thi,rect),y(hi));pmin=QPointF(self._x(tlo,rect),y(lo))
        items=[('max',pmax,hi,thi,True,'#c0392b')]
        flat=lo==hi
        items=[('min = max',pmax,hi,thi,True,'#8e44ad')] if flat else items+[('min',pmin,lo,tlo,False,'#1f3fbf')]
        p.setFont(font)
        for name,point,value,time,above,color in items:
            c=QColor(color);p.setPen(QPen(c,1));p.setBrush(c);s=5.
            sign=1 if name=='min' else -1  # max triangle points down onto the sample, min points up
            if flat:p.drawPolygon(QPolygonF([point+QPointF(0,-s),point+QPointF(s,0),point+QPointF(0,s),point+QPointF(-s,0)]))
            else:p.drawPolygon(QPolygonF([point,point+QPointF(-s,sign*1.8*s),point+QPointF(s,sign*1.8*s)]))
            text=f'{name} {value:.4g} {unit} @ {time:.6g} s'
            size=(metrics.horizontalAdvance(text)+6,metrics.height()+1)
            box=self._label_rect(point,size,rect,above,taken);taken.append(box)
            p.setPen(Qt.NoPen);p.setBrush(QColor(255,255,255,215));p.drawRect(box)
            p.setPen(c);p.drawText(box,Qt.AlignCenter,text)
        p.setBrush(Qt.NoBrush)

    # ----- mouse: handle drag wins, otherwise body drag zooms -----
    def _handle_at(self,x,rect):
        v0,v1=self.view_interval;best=None
        for index,endpoint in enumerate(self.interval):
            if v0<=endpoint<=v1:
                d=abs(x-self._x(endpoint,rect))
                if d<=HANDLE_PX and (best is None or d<best[0]):best=(d,index)
        return None if best is None else best[1]

    def mousePressEvent(self,event):
        if self.view_interval is None or event.button()!=Qt.LeftButton:return
        pos=event.position()
        for rect in self.plot_rects():
            if rect.adjusted(-HANDLE_PX,-8,HANDLE_PX,8).contains(pos):
                index=self._handle_at(pos.x(),rect)
                if index is not None:self._drag=(index,rect);event.accept();return
                if rect.contains(pos):self._zoom=[rect,pos.x(),pos.x()];event.accept();return

    def _move_selection(self,x):
        if self._drag is None:return
        index,rect=self._drag;time=self._time_at(x,rect);ends=list(self.interval);ends[index]=time
        try:window=select_sample_window(self.times,*ends)
        except ValueError:return
        self.interval=window.interval;self.selectionChanged.emit(*self.interval);self.update()

    def mouseMoveEvent(self,event):
        if self._drag is not None:self._move_selection(event.position().x());event.accept()
        elif self._zoom is not None:
            rect=self._zoom[0];self._zoom[2]=max(rect.left(),min(rect.right(),event.position().x()));self.update();event.accept()

    def _finish_zoom(self):
        rect,xs,xe=self._zoom;self._zoom=None;dx=xe-xs
        if self.view_interval is None or abs(dx)<MIN_DRAG_PX:self.update();return
        if dx>0:self._set_view(self._time_at(xs,rect),self._time_at(xe,rect));return
        a,b=self.view_interval;full=(float(self.times[0]),float(self.times[-1]))
        span=min((b-a)/(abs(dx)/rect.width()),full[1]-full[0]);center=self._time_at((xs+xe)/2,rect)
        start=min(max(center-span/2,full[0]),full[1]-span);self._set_view(start,start+span)

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton:
            self._drag=None
            if self._zoom is not None:
                self._zoom[2]=max(self._zoom[0].left(),min(self._zoom[0].right(),event.position().x()));self._finish_zoom()
        super().mouseReleaseEvent(event)
