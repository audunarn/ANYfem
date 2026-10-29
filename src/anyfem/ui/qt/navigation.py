"""Vector pictograms and compact shortcuts to existing workspace controls."""
from functools import lru_cache
import re

from PySide6.QtCore import QByteArray, QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFormLayout, QGridLayout, QLayout, QSizePolicy, QToolButton, QWidget


_DRAWINGS={
    "theme_dark": '<path d="M24 20A11 11 0 0 1 12 5 11 11 0 1 0 24 20Z" fill="#dbeafe"/><path d="M22 5V9 M20 7H24" opacity=".7"/>',
    "theme_light": '<circle cx="16" cy="16" r="6" fill="#ffe08a"/><path d="M16 3V7 M16 25V29 M3 16H7 M25 16H29 M6.8 6.8 9.6 9.6 M22.4 22.4 25.2 25.2 M6.8 25.2 9.6 22.4 M22.4 9.6 25.2 6.8"/>',
    "reset_view": '<path d="M7 9A11 11 0 1 1 5 20 M3 4V12H11"/><path d="M11 16 17 12 23 16V23L17 27 11 23Z M11 16 17 20 23 16 M17 20V27"/>',
    "split_screen": '<rect x="3" y="5" width="26" height="22" rx="2"/><path d="M16 5V27"/><path d="M6 8H12V24H6Z M20 8H26V24H20Z" fill="#dbeafe"/>',
    "mode_model": '<path d="M5 10 16 4 27 10 27 23 16 29 5 23Z" fill="#dbeafe"/><path d="M5 10 16 16 27 10 M16 16V29"/><path d="M5 10 16 16V29 5 23Z" fill="#b9d0f0"/>',
    "mode_mesh": '<path d="M5 10 16 4 27 10 27 23 16 29 5 23Z M5 10 16 16 27 10 M16 16V29 M10.5 7 21.5 13 M21.5 7 10.5 13 M10.5 19.5 10.5 26 M21.5 19.5 21.5 26 M5 16.5 16 22.5 27 16.5"/>',
    "mode_results": '<path d="M5 10 16 4 27 10 27 23 16 29 5 23Z" fill="#7fb2f0"/><path d="M16 4 27 10 27 23 16 16Z" fill="#f2c14e"/><path d="M27 10 27 23 21.5 26 21.5 13Z" fill="#e8604c"/><path d="M5 10 16 16 27 10 M16 16V29"/>',
    "geometry": '<path d="M5 10 16 4 27 10 27 23 16 29 5 23Z M5 10 16 16 27 10 M16 16V29 M16 4V16"/>',
    "construction": '<path d="M4 8H23 M4 16H23 M8 4V25 M16 4V25 M13 26 26 13 29 16 16 29Z"/>',
    "generators": '<ellipse cx="14" cy="7" rx="9" ry="4"/><path d="M5 7V24C5 30 23 30 23 24V7 M23 18H30 M26 15V21"/>',
    "sections": '<path d="M7 4H25V9H19V23H25V28H7V23H13V9H7Z"/>',
    "loads": '<path d="M3 24H29 M7 27 4 31H10Z M25 27 22 31H28Z M16 3V20 M11 14 16 20 21 14"/>',
    "mesh": '<path d="M4 4H28V28H4Z M4 12H28 M4 20H28 M12 4V28 M20 4V28 M4 4 28 28 M4 28 28 4"/>',
    "solve": '<path d="M10 5 27 16 10 27Z" fill="#dbeafe"/><path d="M4 8V24"/>',
    "results": '<path d="M4 28V4 M4 28H29 M7 23 13 17 19 20 27 7"/><circle cx="13" cy="17" r="2"/><circle cx="27" cy="7" r="2"/>',
    "visualization": '<path d="M2 16Q16-3 30 16Q16 35 2 16Z"/><circle cx="16" cy="16" r="6"/><circle cx="16" cy="16" r="2" fill="#2563b4"/>',
    "sectioning": '<path d="M5 10 16 4 27 10V23L16 29 5 23Z M5 10 16 16 27 10 M16 16V29"/><path d="M2 19 13 12 30 17 19 25Z" fill="#dbeafe" fill-opacity=".85" stroke-dasharray="3 2"/>',
    "export": '<rect x="3" y="8" width="26" height="21" rx="2"/><path d="M5 25 12 17 17 22 21 18 27 25 M16 18V2 M11 7 16 2 21 7"/>',
    "scripts": '<path d="M11 8 3 16 11 24 M21 8 29 16 21 24 M19 5 13 27"/>',
    "definitions": '<path d="M7 3V29 M16 3V29 M25 3V29"/><path d="M3 9H11V15H3Z M12 19H20V25H12Z M21 6H29V12H21Z" fill="#dbeafe"/>',
    "point": '<path d="M4 16H28 M16 4V28" opacity=".35"/><circle cx="16" cy="16" r="4" fill="#2563b4"/>',
    "line": '<path d="M5 26 27 6"/><circle cx="5" cy="26" r="3" fill="#dbeafe"/><circle cx="27" cy="6" r="3" fill="#dbeafe"/>',
    "plate": '<path d="M3 22 11 7 29 11 21 27Z" fill="#dbeafe"/>',
    "arc": '<path d="M4 26Q4 3 27 6"/><circle cx="4" cy="26" r="2"/><circle cx="27" cy="6" r="2"/>',
    "polyline": '<path d="M3 25 10 8 20 22 29 5"/><circle cx="10" cy="8" r="2"/><circle cx="20" cy="22" r="2"/>',
    "cylinder": '<ellipse cx="16" cy="7" rx="10" ry="4"/><path d="M6 7V25C6 31 26 31 26 25V7 M6 25C6 19 26 19 26 25"/>',
    "cone": '<path d="M16 3 5 25C5 31 27 31 27 25Z"/><path d="M5 25C5 19 27 19 27 25"/>',
    "sketch": '<path d="M4 7H22V28H4Z M10 23 25 8 29 12 14 27Z M22 11 26 15"/>',
    "plane": '<path d="M3 23 10 12 28 16 21 28Z" fill="#dbeafe"/><path d="M16 20V3 M12 8 16 3 20 8"/>',
    "material": '<path d="M5 6H27V27H5Z M5 15H27 M15 6V27 M5 22 12 15 M16 12 22 6 M19 27 27 19"/>',
    "support": '<path d="M16 5V15 M16 15 5 26H27Z M4 30H28"/>',
    "pressure": '<path d="M3 26H29 M7 4V22 M3 17 7 22 11 17 M16 4V22 M12 17 16 22 20 17 M25 4V22 M21 17 25 22 29 17"/>',
    "mass": '<path d="M9 12H23L28 28H4Z"/><circle cx="16" cy="8" r="4"/>',
    "coordinate": '<path d="M7 25V4 M7 25H29 M7 25 21 11 M3 9 7 4 11 9 M24 21 29 25 24 29"/>',
    "region": '<path d="M4 6H28V27H4Z" stroke-dasharray="3 3"/><circle cx="16" cy="16" r="7" fill="#dbeafe"/>',
    "pattern": '<path d="M3 4H11V12H3Z M21 4H29V12H21Z M3 22H11V30H3Z M21 22H29V30H21Z M12 8H20 M16 4 20 8 16 12"/>',
    "mirror": '<path d="M16 2V30" stroke-dasharray="3 3"/><path d="M3 8 11 13 11 25 3 20Z M29 8 21 13 21 25 29 20Z"/>',
    "extrude": '<path d="M3 18 13 12 23 18 13 24Z M3 18V9L13 3 23 9V18 M13 3V12 M26 25V5 M23 9 26 5 29 9"/>',
    "revolve": '<path d="M16 3V29 M7 7Q29 1 29 16Q29 31 7 25 M7 20V25H12 M8 8H13V24H8Z"/>',
    "split": '<path d="M3 6H29V26H3Z M15 2 18 9 13 16 18 23 15 30"/>',
    "measure": '<path d="M3 10H29V24H3Z M8 10V16 M13 10V20 M18 10V16 M23 10V20"/>',
    "stop": '<rect x="6" y="6" width="20" height="20" rx="2" fill="#dbeafe"/>',
    "clear": '<path d="M7 9H25 M11 9V28H23V9 M13 5H21 M15 13V24 M19 13V24"/>',
    "copy": '<path d="M11 9H27V29H11Z M21 9V3H5V23H11"/>',
    "wireframe": '<path d="M4 7H28V27H4Z M4 7 28 27 M4 27 28 7 M16 7V27"/>',
}


def _symbol(name):
    key=re.sub(r"[^a-z]","",str(name).lower())
    if key=="loadsbc":return "loads"
    if key in _DRAWINGS:return key
    for words,symbol in (
        (("constructionplane",),"plane"),
        (("pointload","acceleration"),"loads"), (("pressure","traction"),"pressure"),
        (("coordinate","workplane"),"coordinate"), (("support","constraint"),"support"),
        (("material",),"material"), (("section","beam","girder","stiffener","frame"),"sections"),
        (("mass",),"mass"), (("cylinder",),"cylinder"), (("cone",),"cone"),
        (("pattern","combination"),"pattern"), (("mirror",),"mirror"), (("extrude",),"extrude"),
        (("revolve","reverse"),"revolve"), (("split","fragment","strip","trim","hole"),"split"),
        (("measure",),"measure"), (("sketch",),"sketch"), (("polyline",),"polyline"),
        (("arc",),"arc"), (("point","vertex"),"point"), (("line","edge"),"line"),
        (("plate","face","bulkhead"),"plate"), (("region","boolean"),"region"),
        (("wireframe",),"wireframe"), (("shaded",),"visualization"),
        (("quad","mapped","native","auto"),"mesh"),
        (("feature",),"geometry"), (("linearstatic","nonlinearstatic","batchlinearstatic"),"loads"),
        (("modal","transient","arclength"),"results"), (("buckling","capacity"),"sections"),
        (("run","solve","impact"),"solve"),
        (("cancel","stop",),"stop"), (("clear","delete"),"clear"), (("copy",),"copy"),
        (("script","command"),"scripts"), (("loadcase",),"loads"),
        (("displacement","stress","force","magnitude","rotation","strain","energy"),"results"),
    ):
        if any(word in key for word in words):return symbol
    return "definitions"


@lru_cache(maxsize=256)
def navigation_icon(name):
    """Own SVG artwork rasterized at several sizes for Qt's high-DPI icon engine."""
    from .field_icons import field_drawing
    body=_DRAWINGS.get(name) or field_drawing(name)
    if body is None:body=_DRAWINGS[_symbol(name)]
    key=str(name).lower()
    if key.startswith("add"):
        body+='<circle cx="25" cy="25" r="6" fill="white"/><path d="M22 25H28 M25 22V28" stroke="#16804a"/>'
    elif any(word in key for word in ("delete","suppress","remove")):
        body+='<circle cx="25" cy="25" r="6" fill="white"/><path d="M22 22 28 28 M28 22 22 28" stroke="#b43b3b"/>'
    elif any(word in key for word in ("edit","move","assign")):
        body+='<path d="M20 28 27 21 30 24 23 31Z" fill="white"/>'
    svg=QByteArray((f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><g fill="none" stroke="#245fab" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round">{body}</g></svg>').encode())
    renderer=QSvgRenderer(svg);icon=QIcon()
    for size in (24,32,48,64,96):
        pixmap=QPixmap(size,size);pixmap.fill(Qt.transparent)
        painter=QPainter(pixmap);renderer.render(painter);painter.end();icon.addPixmap(pixmap)
    return icon


class OptionIcons(QWidget):
    """Wrapping square buttons that select the existing combo, never execute edits.

    Optional ``groups`` [(heading, keys), ...] show the buttons under small headings;
    button order and indices still follow the combo exactly.
    """
    groups=();headings=();sections=None
    def __init__(self,combo,parent=None,groups=None):
        super().__init__(parent);self.combo=combo;self.buttons=[];self._pending=False;self._columns=0
        self.groups=tuple(groups or ());self.headings=[]
        self.setObjectName("WorkspaceOptionIcons")
        self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Minimum)
        self.grid=QGridLayout(self);self.grid.setContentsMargins(0,2,0,2);self.grid.setSpacing(4)
        self.grid.setSizeConstraint(QLayout.SetNoConstraint)
        self.grid.setAlignment(Qt.AlignLeft|Qt.AlignTop)
        combo.currentIndexChanged.connect(self.sync)
        for signal in (combo.model().rowsInserted,combo.model().rowsRemoved,combo.model().modelReset,combo.model().dataChanged):signal.connect(self.schedule_rebuild)
        combo.installEventFilter(self);self.rebuild()

    def schedule_rebuild(self,*_):
        if self._pending:return
        self._pending=True;QTimer.singleShot(0,self,self.rebuild)

    def rebuild(self):
        self._pending=False
        for button in self.buttons:self.grid.removeWidget(button);button.deleteLater()
        for heading in self.headings:self.grid.removeWidget(heading);heading.deleteLater()
        self.buttons=[];self.headings=[];self._columns=0
        for index in range(self.combo.count()):
            button=QToolButton(self);button.setCheckable(True);button.setFixedSize(36,36);button.setIconSize(QSize(24,24))
            label=self.combo.itemText(index);key=self.combo.itemData(index) or label
            button.setIcon(navigation_icon(str(key)));button.setToolTip(f"Select {label} in this workspace.");button.setAccessibleName(label)
            button.clicked.connect(lambda checked=False,index=index:self.choose(index))
            self.buttons.append(button)
        self.sections=self._sections()
        for title,_ in self.sections:
            if title:
                from .widgets import section_label
                self.headings.append(section_label(title,self))
        self.reflow();self.sync()

    def _sections(self):
        if not self.groups:return [("",list(range(len(self.buttons))))]
        keys=[str(self.combo.itemData(index) or self.combo.itemText(index)) for index in range(self.combo.count())]
        sections=[];placed=set()
        for title,members in self.groups:
            indices=[index for index,key in enumerate(keys) if key in members and index not in placed]
            if indices:sections.append((title,indices));placed.update(indices)
        rest=[index for index in range(len(keys)) if index not in placed]
        if rest:sections.append(("Other",rest))
        return sections

    def choose(self,index):
        self.combo.setCurrentIndex(index);self.combo.activated.emit(index);self.sync()

    def sync(self,*_):
        for index,button in enumerate(self.buttons):
            button.setChecked(index==self.combo.currentIndex());button.setEnabled(self.combo.isEnabled())

    def reflow(self):
        columns=max(1,self.width()//40)
        if columns==self._columns:return
        self._columns=columns
        sections=getattr(self,"sections",None) or [("",list(range(len(self.buttons))))]
        headings=iter(self.headings);row=0;height=4
        for title,indices in sections:
            if title:
                heading=next(headings);self.grid.removeWidget(heading)
                self.grid.addWidget(heading,row,0,1,columns);row+=1;height+=24
            for position,index in enumerate(indices):
                button=self.buttons[index]
                self.grid.removeWidget(button);self.grid.addWidget(button,row+position//columns,position%columns)
            rows=(len(indices)+columns-1)//columns;row+=rows;height+=rows*40
        self.setMinimumHeight(height)

    def resizeEvent(self,event):
        super().resizeEvent(event);self.reflow()

    def eventFilter(self,target,event):
        if event.type()==QEvent.EnabledChange:self.sync()
        return False


class ActionIcons(OptionIcons):
    """Shortcuts to existing push buttons for a workspace without a selector."""
    def __init__(self,actions,parent=None):
        QWidget.__init__(self,parent);self.buttons=[];self._columns=0;self.sources=actions
        self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Minimum)
        self.grid=QGridLayout(self);self.grid.setContentsMargins(0,2,0,2);self.grid.setSpacing(4)
        self.grid.setSizeConstraint(QLayout.SetNoConstraint)
        self.grid.setAlignment(Qt.AlignLeft|Qt.AlignTop)
        for source in actions:
            button=QToolButton(self);button.setFixedSize(36,36);button.setIconSize(QSize(24,24))
            from .button_help import button_description
            button.setIcon(navigation_icon(source.text()));button.setToolTip(button_description(source));button.setAccessibleName(source.text())
            button.clicked.connect(source.click);self.buttons.append(button)
            source.installEventFilter(self)
        self.reflow();self.sync()

    def sync(self,*_):
        for source,button in zip(self.sources,self.buttons):button.setEnabled(source.isEnabled())


def install_option_icons(panel,combo,groups=None):
    strip=OptionIcons(combo,panel,groups=groups)
    placed=strip
    if groups:
        # A long grouped palette can be folded away so the operation's fields sit near the top.
        from .widgets import Collapsible
        placed=Collapsible("All operations",panel,expanded=True,hint="Every operation in this workspace, grouped by purpose.")
        placed.addWidget(strip);panel.option_palette=placed
    def insert(layout):
        if isinstance(layout,QFormLayout):
            row,role=layout.getWidgetPosition(combo)
            if row>=0:layout.insertRow(row+1,placed);return True
        for index in range(layout.count()):
            item=layout.itemAt(index)
            if item.widget() is combo:layout.insertWidget(index+1,placed);return True
            if item.layout() is not None and insert(item.layout()):return True
            widget=item.widget()
            if widget is not None and widget is not placed and widget.layout() is not None and insert(widget.layout()):return True
        return False
    if not insert(panel.layout()):raise ValueError("workspace selector has no layout")
    panel.option_icons=strip
    return strip
