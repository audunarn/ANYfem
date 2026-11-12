"""Vector preview and printable A3 drawing sheets in the Qt workbench."""
import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPageLayout, QPageSize, QPainter, QPainterPath, QPdfWriter, QPen
from PySide6.QtWidgets import QWidget


INK, TEAL, FAINT = "#172f40", "#087c82", "#576f7a"


def _text(p, x, y, text, size, color=INK, bold=False):
    font = QFont("Segoe UI")
    font.setPixelSize(size)
    font.setBold(bold)
    p.setFont(font)
    p.setPen(QColor(color))
    p.drawText(QPointF(x,y), text)


def _transform(rect, view):
    low, high = view.extent()
    span = np.maximum(high-low, 1e-12)
    scale = min(rect.width()/span[0], rect.height()/span[1])
    centre = (high+low)/2
    def xy(pt):
        return QPointF(rect.center().x()+(pt[0]-centre[0])*scale,
                       rect.center().y()-(pt[1]-centre[1])*scale)
    return xy


def _draw(p, rect, view, *, preview=False):
    xy = _transform(rect, view)
    for curve in view.paths:
        if len(curve.points) < 2:
            continue
        color = TEAL if curve.layer in {"STIFFENERS","SECTION","CUT_PLANE"} else INK
        pen = QPen(QColor(color))
        pen.setWidthF(1.25 if preview else 1.65)
        if curve.layer=="STIFFENERS":
            pen.setStyle(Qt.PenStyle.DashLine)
        if curve.layer=="CUT_PLANE":
            pen.setStyle(Qt.PenStyle.DashDotLine)
        p.setPen(pen)
        path = QPainterPath(xy(curve.points[0]))
        for point in curve.points[1:]:
            path.lineTo(xy(point))
        if curve.closed:
            path.closeSubpath()
        p.drawPath(path)
        if curve.layer=="CUT_PLANE" and curve.label:
            for point in (curve.points[0],curve.points[-1]):
                position = xy(point)
                _text(p,position.x()+6,position.y()-6,curve.label,13,TEAL,True)


class PaperPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.view = None
        self.prompt = "Choose a drawing source"
        self.setMinimumHeight(150)
        self.setMaximumHeight(215)
        self.setAccessibleName("Drawing preview")

    def display(self, view, prompt="Choose a drawing source"):
        self.view, self.prompt = view,prompt
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1,1,-1,-1)
        p.setBrush(QColor("#f9fbfa"))
        p.setPen(QPen(QColor("#cad8d8"),1))
        p.drawRoundedRect(r,6,6)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.view:
            _draw(p,r.adjusted(15,14,-15,-34),self.view,preview=True)
            _text(p,12,r.bottom()-10,self.view.title,11,INK,True)
        else:
            p.setPen(QColor(FAINT))
            p.drawText(r.adjusted(8,4,-8,-4),Qt.AlignCenter | Qt.TextWordWrap,self.prompt)
        p.end()


def _font_metrics(size):
    font = QFont("Segoe UI")
    font.setPixelSize(size)
    return QFontMetricsF(font)


def _wrap(value, size, width):
    metrics = _font_metrics(size)
    lines = []
    for paragraph in str(value).splitlines():
        line = ""
        for word in paragraph.split():
            joined = (line + " " + word).strip()
            if line and metrics.horizontalAdvance(joined) > width:
                lines.append(line)
                line = ""
            # Long user marks can contain no whitespace.
            while metrics.horizontalAdvance(word) > width:
                n = max(1, int(len(word)*width/metrics.horizontalAdvance(word)))
                while n > 1 and metrics.horizontalAdvance(word[:n]) > width:
                    n -= 1
                lines.append(word[:n])
                word = word[n:]
            line = (line + " " + word).strip()
        if line:
            lines.append(line)
    return lines


def _number(value):
    """Dimensions in mm, normally to the nearest tenth."""
    digits = 3 if 0 < abs(value) < 1 else 1
    return f"{value:,.{digits}f}".rstrip("0").rstrip(".")


def _mm(metres):
    return _number(1000*metres)


def _line(p, a, b, color=FAINT, width=1.):
    p.setPen(QPen(QColor(color), width))
    p.drawLine(QPointF(*a), QPointF(*b))


def _dimensions(p, view, rect):
    """Dimension measured plot axes; attach witnesses to actual boundary points."""
    xy = _transform(rect, view)
    stock = [c for c in view.paths if c.layer in {"OUTLINE", "VISIBLE"}]
    if not stock:
        return
    points = np.concatenate([np.asarray(c.points) for c in stock])
    low, high = points.min(axis=0), points.max(axis=0)
    bottom = xy(low).y() + 34
    left = xy(low).x() - 37
    for axis in (0, 1):
        distance = high[axis]-low[axis]
        if distance < 1e-9:
            continue
        ends = []
        for limit in (low[axis], high[axis]):
            at_limit = points[np.abs(points[:,axis]-limit)<1e-9]
            point = xy(at_limit[np.argmin(at_limit[:,1-axis])])
            x, y = point.x(), point.y()
            if axis == 0:
                _line(p, (x, y+3), (x,bottom+6), "#78939a", .8)
                ends.append((x,bottom))
            else:
                _line(p, (x-3,y), (left-6,y), "#78939a", .8)
                ends.append((left,y))
        _line(p, *ends)
        for x,y in ends:
            _line(p, (x-3.5,y+3.5), (x+3.5,y-3.5), FAINT, 1.6)
        a,b = ends
        label = _mm(distance)
        w = _font_metrics(17).horizontalAdvance(label)
        if axis == 0:
            _text(p, (a[0]+b[0]-w)/2,bottom+21,label,17,INK,True)
        else:
            p.save()
            p.translate(left-9, (a[1]+b[1])/2)
            p.rotate(-90)
            _text(p,-w/2,0,label,17,INK,True)
            p.restore()


@dataclass
class _Reference:
    key: str
    path: object
    lines: list


def _references(view):
    """Link each opening and labelled member to its view geometry."""
    low, _ = view.extent()
    outline = [np.asarray(c.points) for c in view.paths if c.layer=="OUTLINE"]
    if outline:
        low = np.concatenate(outline).min(axis=0)
    refs, labels = [], set()
    counters = {"H": 0, "M": 0, "S": 0}
    for path in view.paths:
        if path.layer=="CUT_PLANE":
            continue
        if not path.points:
            continue
        if path.layer == "HOLES" and outline:
            lo, hi = np.min(path.points,axis=0), np.max(path.points,axis=0)
            centre = (lo+hi)/2-low
            lines = [f"{_mm(hi[0]-lo[0])} × {_mm(hi[1]-lo[1])} mm",
                     f"Centre   X {_mm(centre[0])}    Y {_mm(centre[1])}"]
            prefix = "H"
        elif path.label and path.label not in labels:
            labels.add(path.label)
            lines = _wrap(path.label, 16, 301)
            prefix = "M" if path.layer=="STIFFENERS" else "S"
        else:
            continue
        counters[prefix] += 1
        refs.append(_Reference(f"{prefix}{counters[prefix]}", path, lines))
    return refs


def _reference_pages(refs):
    pages, current, used = [], [], 0
    for ref in refs:
        # Extremely long identifiers continue without shrinking print size.
        for offset in range(0, len(ref.lines), 14):
            piece = _Reference(ref.key, ref.path, ref.lines[offset:offset+14])
            height = 23 + 21*len(piece.lines)
            if current and (used+height > 473 or len(current)==8):
                pages.append(current)
                current, used = [], 0
            current.append(piece)
            used += height
    return pages + [current]


def _reference_column(p, view, rect, refs):
    """Place text in the margin and terminate each numbered leader on its curve."""
    xy = _transform(rect, view)
    x0 = 1018.
    _line(p,(x0,145),(x0,742),"#c7d4d6")
    _text(p,x0+22,167,"Drawing references",21,INK,True)
    top, right = [], []
    for ref in refs:
        pts = np.asarray(ref.path.points)
        # Label longitudinal axes at their top end and transverse axes at
        # their right end. Parallel members then get separate leaders.
        vertical = ref.path.layer=="STIFFENERS" and np.ptp(pts[:,1]) > np.ptp(pts[:,0])
        axis = 1 if vertical else 0
        extremes = pts[np.isclose(pts[:,axis],pts[:,axis].max(),rtol=0,atol=1e-10)]
        target = .5*(pts[:,1-axis].min()+pts[:,1-axis].max())
        anchor = xy(extremes[np.argmin(abs(extremes[:,1-axis]-target))])
        (top if vertical else right).append((anchor, ref))
    for marks, above in ((top,True),(right,False)):
        marks.sort(key=lambda item: item[0].x() if above else item[0].y())
        desired = [point.x() if above else point.y() for point,_ in marks]
        # Space neighbouring tags, then pull a crowded end back onto the sheet.
        lo,hi,gap = (115.,895.,46.) if above else (176.,695.,30.)
        for i in range(len(desired)):
            desired[i] = max(desired[i], desired[i-1]+gap if i else lo)
        for i in reversed(range(len(desired))):
            desired[i] = min(desired[i], desired[i+1]-gap if i+1<len(desired) else hi)
        for (a,ref),pos in zip(marks,desired):
            if above:
                tag_x, tag_y = pos, rect.top()-28
                end = (tag_x,tag_y+8)
            else:
                tag_x, tag_y = rect.right()+64, pos
                end = (tag_x-18,tag_y)
            _line(p,(a.x(),a.y()),end,"#79989a",1.)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(TEAL))
            p.drawEllipse(a,2.7,2.7)
            p.setBrush(Qt.BrushStyle.NoBrush)
            w = _font_metrics(16).horizontalAdvance(ref.key)
            _text(p,tag_x-w/2,tag_y+5,ref.key,16,TEAL,True)

    y = 205.
    for ref in refs:
        _text(p,1038,y,ref.key,17,TEAL,True)
        for j,line in enumerate(ref.lines):
            _text(p,1093,y+j*21,line,16)
        y += 23+21*len(ref.lines)
    if any(r.key.startswith("H") for r in refs):
        _text(p,1040,715,"Opening width × height, viewed in mm.",15,FAINT)
        _text(p,1040,739,"Centres from lower-left plate extents.",15,FAINT)


def _table_pages(rows):
    columns = [
        ("Plate","Plate",174), ("Material","Material",153), ("Thickness mm","t / mm",111),
        ("Holes","Holes",81), ("Quantity required","Qty",82),
        ("Net mass per plate (kg)","kg each",136), ("Extended net mass (kg)","kg total",156),
        ("Lower edge Z above support base (mm)","Lower Z / mm",192),
        ("Upper edge Z above support base (mm)","Upper Z / mm",196),
    ]
    pages, page, height = [], [], 0
    for row in rows:
        cells = []
        for key,_,width in columns:
            value = row[key]
            if key in {"Net mass per plate (kg)","Extended net mass (kg)"}:
                value = f"{float(value):,.3f}"
            if key.startswith(("Lower edge Z","Upper edge Z")):
                value = _number(float(value))
            cells.append(_wrap(value,17,width-10))
        n = max(1, max(map(len,cells)))
        for i in range(0,n,20):
            piece = [cell[i:i+20] for cell in cells]
            h = max(34, max(map(len,piece))*22+12)
            if page and height+h > 650:
                pages.append(page)
                page,height = [],0
            page.append((piece,h))
            height += h
    if page:
        pages.append(page)
    return columns,pages


def _table(p, columns, rows, quantity):
    p.fillRect(QRectF(39,136,1362,47),QColor("#e6efed"))
    x = 53
    for _,title,width in columns:
        _text(p,x,166,title,17,TEAL,True)
        x += width
    y = 192
    for cells,h in rows:
        x = 53
        for cell,(_,_,width) in zip(cells,columns):
            for j,line in enumerate(cell):
                _text(p,x,y+19+22*j,line,17)
            x += width
        y += h
        _line(p,(40,y-3),(1400,y-3),"#dce5e3")
    _text(p,38,888,f"Required for {quantity} assemblies. Z = 0 at the lowest material edge.",17,INK,True)


def write_sheets(path, views, *, index_path=None, quantity=1):
    """Print each view at scale-to-fit with indexed detail and stock pages."""
    rows = []
    if index_path:
        with Path(index_path).open(encoding="utf-8",newline="") as f:
            rows = list(csv.DictReader(f))
    by_mark = {r["Plate"]:r for r in rows}
    columns, tables = _table_pages(rows)
    pages = []
    for view in views:
        notes = list(view.notes)
        row = by_mark.get(view.title)
        if row and any(c.layer=="OUTLINE" for c in view.paths):
            notes.append(f"Required quantity {row['Quantity required']}    /    Net mass "
                         f"{float(row['Net mass per plate (kg)']):,.3f} kg each    /    "
                         f"{float(row['Extended net mass (kg)']):,.3f} kg total")
        wrapped = [line for note in notes for line in _wrap(note,17,1330)]
        for refs in _reference_pages(_references(view)):
            pages.append(("drawing",view,refs,wrapped[:4]))
        for i in range(4,len(wrapped),26):
            pages.append(("notes",view,[],wrapped[i:i+26]))
    pages.extend(("table",None,table,[]) for table in tables)
    if not pages:
        raise ValueError("Select a view to print.")
    pdf = QPdfWriter(str(path))
    pdf.setTitle(views[0].title if views else "Plate index")
    pdf.setPageSize(QPageSize(QPageSize.A3))
    pdf.setPageOrientation(QPageLayout.Landscape)
    pdf.setResolution(144)
    p = QPainter(pdf)
    if not p.isActive():
        raise OSError(f"Could not open {path} for printing")
    try:
        for i,(kind,view,items,lines) in enumerate(pages):
            if i and not pdf.newPage():
                raise OSError("Could not add a drawing sheet")
            p.save()
            try:
                scale = min(pdf.width()/1440, pdf.height()/1000)
                w, h = round(1440*scale), round(1000*scale)
                p.setViewport((pdf.width()-w)//2,(pdf.height()-h)//2,w,h)
                p.setWindow(0,0,1440,1000)
                p.fillRect(QRectF(0,0,1440,1000),QColor("white"))
                p.setRenderHint(QPainter.Antialiasing)
                _text(p,38,37,"ANYfem    /    ENGINEERING DRAWINGS",15,TEAL,True)
                title = view.title if view else "Plate index"
                _text(p,38,85,_font_metrics(32).elidedText(title, Qt.ElideRight, 1275),32,INK,True)
                _line(p,(38,105),(1402,105),"#98b3b5")
                if kind=="table":
                    _table(p,columns,items,quantity)
                elif kind=="notes":
                    _text(p,38,155,"Drawing notes continued",20,TEAL,True)
                    for j,line in enumerate(lines):
                        _text(p,38,201+j*26,line,17)
                else:
                    dimensioned = (view.projection in {"plan","front","right"} or
                                   any(c.layer=="OUTLINE" for c in view.paths))
                    rect = QRectF(137,169,753 if items else 1165,530)
                    _draw(p,rect,view)
                    if dimensioned:
                        _dimensions(p,view,rect)
                    if items:
                        _reference_column(p,view,rect,items)
                    _line(p,(38,780),(1402,780),"#c7d4d6")
                    for j,line in enumerate(lines):
                        _text(p,38,815+j*27,line,17)
                _line(p,(38,932),(1402,932),"#98b3b5")
                _text(p,38,963,"Dimensions in mm    /    PDF scaled to fit A3    /    CAD model space 1:1",15,FAINT)
                _text(p,1270,963,f"{i+1:02d}   /   {len(pages):02d}",16,TEAL,True)
            finally:
                p.restore()
    finally:
        p.end()
