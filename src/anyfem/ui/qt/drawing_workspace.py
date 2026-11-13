"""Docked drawing workspace for parts, selected plates and bounded section planes."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QProcess, QRunnable, QSettings, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLayout, QLineEdit, QPushButton, QSizePolicy, QSpinBox, QStyle, QTabBar,
    QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from ...commands import Command
from .drawing_paper import PaperPreview
from .open_cad_studio import find_studio


class SetThicknessSide(Command):
    """Set one plate's drawing thickness side; undo restores the previous overrides exactly."""

    label = "set plate thickness side"

    def __init__(self, sheet_id, choice):
        self.sheet_id, self.choice = sheet_id, choice
        self._before = None

    def do(self, project):
        from ...application.thickness_side import set_override
        self._before = dict(project.drawing_thickness_sides)
        set_override(project, self.sheet_id, self.choice)

    def undo(self, project):
        project.drawing_thickness_sides = dict(self._before)


class _Signals(QObject):
    done = Signal(int,object,str)
    exported = Signal(object,object,str)


class _ExportJob(QRunnable):
    def __init__(self,filename,snapshot,views,options,signals):
        super().__init__()
        self.filename,self.snapshot,self.views = filename,snapshot,views
        self.options,self.signals = options,signals

    def run(self):
        from .drawing_issue import publish
        try:
            folder = publish(self.filename,self.snapshot,self.views,**self.options)
            self.signals.exported.emit(self.snapshot.revision,folder,"")
        except Exception as exc:
            self.signals.exported.emit(self.snapshot.revision,None,str(exc))


class _PreviewJob(QRunnable):
    def __init__(self, sequence, snapshot, request, signals):
        super().__init__()
        self.sequence,self.snapshot,self.request,self.signals = sequence,snapshot,request,signals

    def run(self):
        try:
            p = self.snapshot.thaw()
            mode, args, options = self.request
            if mode=="Assembly":
                from ...application.drawing_assembly import assembly_views
                # A part-only extent (plate sheets and/or index) has no assembly views to preview.
                views = assembly_views(p,*args,**options) if options.get("views") else ()
            elif mode=="Plate":
                from ...application.plate_view import build_plate_view
                views = (build_plate_view(p,*args,**options),)
            else:
                from ...application.drawing_sections import build_plane_view
                views = (build_plane_view(p,*args,**options),)
            for view in views:
                view.extent()
            self.signals.done.emit(self.sequence,views,"")
        except Exception as exc:
            self.signals.done.emit(self.sequence,None,str(exc))


def _number(value=0, *, minimum=-1e10, maximum=1e10, decimals=2):
    box = QDoubleSpinBox()
    box.setRange(minimum,maximum)
    box.setDecimals(decimals)
    box.setValue(value)
    box.setKeyboardTracking(False)
    return box


def _form():
    widget = QWidget()
    widget.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Minimum)
    form = QFormLayout(widget)
    form.setSizeConstraint(QLayout.SetMinimumSize)
    form.setContentsMargins(0,5,0,4)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    return widget,form


class DrawingWorkspace(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings = QSettings()
        self.bound = None
        self.revision = None
        self.face = None
        self.sequence = 0
        self.views = ()
        self.preview_ready = False
        self.saved_views = []
        self.source = None
        self.last_file = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.export_pool = QThreadPool(self)
        self.exporting = False
        self.export_rel = None
        self.signals = _Signals(self)
        self.signals.done.connect(self._received,Qt.QueuedConnection)
        self.signals.exported.connect(self._exported,Qt.QueuedConnection)
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        layout.setContentsMargins(5,4,5,8)
        layout.setSpacing(8)
        self.mode = QTabBar()
        self.mode.setAccessibleName("Drawing source")
        self.mode.setExpanding(True)
        self.mode.setDrawBase(False)
        for name in ("Assembly","Plate","Plane"):
            self.mode.addTab(name)
        layout.addWidget(self.mode)
        self.paper = PaperPreview()
        layout.addWidget(self.paper)
        assembly,form = _form()
        self.part = QComboBox()
        self.quantity = QSpinBox()
        self.quantity.setRange(1,1_000_000)
        self.basis = QComboBox()
        self.basis.addItems(["Assigned material density","Nominal density override"])
        self.density = _number(7850,minimum=.001,maximum=1e6,decimals=3)
        self.density.setSuffix(" kg/m³")
        form.addRow("Structural part",self.part)
        form.addRow("Quantity",self.quantity)
        form.addRow("Weight basis",self.basis)
        form.addRow("Density",self.density)
        self.elevation_datum = QComboBox()
        self.elevation_datum.addItem("Support lower edge · Z = 0", "support_base")
        self.elevation_datum.addItem("Model global Z", "global")
        self.elevation_datum.setAccessibleName("Assembly elevation datum")
        selected_datum = self.elevation_datum.findData(self.settings.value("drawings/elevation_datum", "support_base"))
        self.elevation_datum.setCurrentIndex(max(0, selected_datum))
        form.addRow("Elevation datum",self.elevation_datum)
        self.sides = QTableWidget(0,3)
        self.sides.setAccessibleName("Plate thickness sides")
        self.sides.setHorizontalHeaderLabels(["Plate","Thickness side","Resolved"])
        self.sides.verticalHeader().hide()
        self.sides.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.sides.horizontalHeader().setStretchLastSection(True)
        self.sides.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sides.setSelectionMode(QAbstractItemView.NoSelection)
        self.sides.setMaximumHeight(180)
        self.sides.setToolTip("Auto keeps a plate's free face on the modelled surface and puts its thickness "
                              "toward attached plates of the same part; Centre, Front (+n) or Back (-n) override it.")
        self._side_rows = {}
        form.addRow(QLabel("Thickness sides"))
        form.addRow(self.sides)
        self.density_label = form.labelForField(self.density)
        layout.addWidget(assembly)
        plate,form = _form()
        self.plate_label = QLabel("Pick a plate in the model")
        self.plate_label.setWordWrap(True)
        self.pick = QPushButton("Pick plate in model")
        self.pick.clicked.connect(self._enable_pick)
        self.stiffeners = QCheckBox("Include attached stiffeners")
        self.stiffeners.setChecked(True)
        self.intersections = QCheckBox("Show intersecting plates")
        self.intersections.setChecked(True)
        form.addRow("Plate",self.plate_label)
        form.addRow(self.pick)
        form.addRow(self.stiffeners)
        form.addRow(self.intersections)
        layout.addWidget(plate)
        plane,form = _form()
        self.plane = QComboBox()
        self.plane.addItems(["XY · horizontal","XZ · front","YZ · side","Active workplane","Picked planar face","Custom plane"])
        form.addRow("Section plane",self.plane)
        self.pick_plane = QPushButton("Pick planar face in model")
        self.pick_plane.clicked.connect(self._enable_pick)
        form.addRow(self.pick_plane)
        self.custom = QWidget()
        grid = QGridLayout(self.custom)
        grid.setContentsMargins(0,0,0,0)
        self.xyz, self.normal = [], []
        for j,letter in enumerate("XYZ",1):
            grid.addWidget(QLabel(letter),0,j)
            for row,items,val in ((1,self.xyz,0),(2,self.normal,1 if j==3 else 0)):
                spin = _number(val,decimals=3 if row==1 else 5)
                spin.setAccessibleName(("Plane origin " if row==1 else "Plane normal ")+letter)
                spin.setMinimumWidth(48)
                spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
                grid.addWidget(spin,row,j)
                items.append(spin)
        grid.addWidget(QLabel("Origin mm"),1,0)
        grid.addWidget(QLabel("Normal"),2,0)
        form.addRow(self.custom)
        self.offset = _number()
        self.offset.setSuffix(" mm")
        form.addRow("Normal offset",self.offset)
        self.plane_hint = QLabel()
        self.plane_hint.setObjectName("Hint")
        self.plane_hint.setWordWrap(True)
        form.addRow(self.plane_hint)
        self.limited = QCheckBox("Limit section to a rectangle")
        form.addRow(self.limited)
        self.bounds_widget = QWidget()
        grid = QGridLayout(self.bounds_widget)
        grid.setContentsMargins(0,0,0,0)
        self.bounds = []
        for j,text in enumerate(("Minimum mm","Maximum mm"),1):
            grid.addWidget(QLabel(text),0,j)
        for i,axis in enumerate(("U · across","V · up"),1):
            grid.addWidget(QLabel(axis),i,0)
            for j,value in enumerate((-500.,500.),1):
                spin = _number(value)
                spin.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
                spin.setMinimumWidth(54)
                spin.setAccessibleName(axis+(" minimum" if j==1 else " maximum"))
                grid.addWidget(spin,i,j)
                self.bounds.append(spin)
        form.addRow(self.bounds_widget)
        layout.addWidget(plane)
        self.forms = (assembly,plate,plane)
        common,form = _form()
        self.representation = QComboBox()
        self.representation.addItem("Solid plates", "solid")
        self.representation.addItem("2D mid-surface · no thickness", "midsurface_2d")
        self.representation.setAccessibleName("Drawing representation")
        chosen = self.representation.findData(self.settings.value("drawings/representation", "solid"))
        self.representation.setCurrentIndex(max(0, chosen))
        form.addRow("Representation",self.representation)
        self._build_extent(form)
        layout.addWidget(common)
        self.preview_btn = QPushButton("Generate preview")
        self.preview_btn.setStyleSheet("QPushButton { background:#126b70; color:white; border:0; border-radius:4px;"
                                       "padding:6px 12px; font-weight:600 } QPushButton:hover { background:#105b60 }"
                                       "QPushButton:disabled { background:#e5ebed; color:#64767d }")
        self.preview_btn.clicked.connect(self._preview)
        layout.addWidget(self.preview_btn)
        self.view_choice = QComboBox()
        self.view_choice.setAccessibleName("Preview view")
        self.view_choice.currentIndexChanged.connect(self._show_view)
        self.zoom = QToolButton()
        self.zoom.setIcon(self.style().standardIcon(QStyle.SP_TitleBarMaxButton))
        self.zoom.setText("Enlarge preview")
        self.zoom.setToolTip("Enlarge drawing preview")
        self.zoom.setAccessibleName("Enlarge drawing preview")
        self.zoom.clicked.connect(self._enlarge)
        line = QHBoxLayout()
        line.addWidget(self.view_choice,1)
        line.addWidget(self.zoom)
        layout.addLayout(line)
        self.added = QWidget()
        line = QHBoxLayout(self.added)
        line.setContentsMargins(0,0,0,0)
        self.detail_name = QLineEdit()
        self.detail_name.setPlaceholderText("Sheet name (optional)")
        self.keep = QPushButton("Add sheet")
        self.keep.clicked.connect(self._keep)
        line.addWidget(self.detail_name,1)
        line.addWidget(self.keep)
        layout.addWidget(self.added)
        self.queue = QWidget()
        line = QHBoxLayout(self.queue)
        line.setContentsMargins(0,0,0,0)
        self.queue_choice = QComboBox()
        self.queue_choice.setAccessibleName("Additional sheets in the export")
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        line.addWidget(self.queue_choice,1)
        line.addWidget(remove)
        layout.addWidget(self.queue)
        self.format = QComboBox()
        self.format.addItems(["DXF + PDF","DWG + DXF + PDF"])
        self.format.setAccessibleName("Output format")
        self.export_btn = QPushButton("Export drawings…")
        self.export_btn.clicked.connect(self._export)
        line = QHBoxLayout()
        line.addWidget(self.format,1)
        line.addWidget(self.export_btn)
        layout.addLayout(line)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        self.feedback.setObjectName("Feedback")
        layout.addWidget(self.feedback)
        self.studio_notice = QWidget()
        notice = QHBoxLayout(self.studio_notice)
        notice.setContentsMargins(0,0,0,0)
        self.studio_message = QLabel()
        self.studio_message.setWordWrap(True)
        self.studio_message.setStyleSheet("color:#684d12;background:#fff3cf;border-radius:4px;padding:6px")
        notice.addWidget(self.studio_message,1)
        layout.insertWidget(1,self.studio_notice)
        line = QHBoxLayout()
        self.open_btn = QPushButton("Open last export in OpenCAD Studio")
        self.open_btn.clicked.connect(self._open)
        self.locate_btn = QPushButton("Locate…")
        self.locate_btn.clicked.connect(self._locate)
        notice.addWidget(self.locate_btn)
        line.addWidget(self.open_btn,1)
        layout.addLayout(line)
        self.mode.currentChanged.connect(self._mode_changed)
        for control in (self.quantity,self.density,self.offset,*self.bounds,*self.xyz,*self.normal):
            control.valueChanged.connect(self._invalidate)
        for control in (self.basis,self.part,self.plane):
            control.currentIndexChanged.connect(self._invalidate)
        self.stiffeners.toggled.connect(self._invalidate)
        self.intersections.toggled.connect(self._invalidate)
        self.elevation_datum.currentIndexChanged.connect(self._datum_changed)
        self.representation.currentIndexChanged.connect(self._representation_changed)
        self.extent_preset.currentIndexChanged.connect(self._extent_preset_changed)
        for box in self.extent_checks.values():
            box.toggled.connect(self._extent_box_toggled)
        self.part.currentIndexChanged.connect(self._fill_sides)
        self.limited.toggled.connect(self._invalidate)
        self._mode_changed()
        self._studio()

    def _studio(self):
        self.studio = find_studio(self.settings.value("drawings/studio",""))
        self.studio_message.setText("" if self.studio else
            "OpenCAD Studio was not found. Locate it for DWG export and viewing. DXF and PDF are available.")
        self.studio_notice.setVisible(self.studio is None)
        self.open_btn.setEnabled(bool(self.studio and self.last_file))

    def _locate(self):
        name,_ = QFileDialog.getOpenFileName(self,"Locate OpenCAD Studio executable")
        if name:
            self.settings.setValue("drawings/studio",name)
            self._studio()

    def _open(self):
        self._studio()
        if self.studio and self.last_file:
            ok,_ = QProcess.startDetached(str(self.studio),[str(self.last_file)])
            if not ok:
                self.feedback.setText("OpenCAD Studio could not launch. Check its installation.")

    def _enable_pick(self):
        self.app.show_geometry()
        self.app._quick_select("face")
        self.feedback.setText("Click a plate face in the model.")

    def _selection(self):
        selected = [r.id for r in self.app.selection.ordered_items if r.kind=="face"]
        face = selected[-1] if selected else None
        if self.face == face:
            return
        self.face = face
        g = self.app.project.geometry
        names = {g.sheets[u.sheet_id].name for u in g.face_uses.values() if u.face_id==face}
        mark = next(iter(names)) if len(names)==1 else ""
        label = f"{mark} · face {face}" if mark else f"Face {face}"
        self.plate_label.setText(label if face is not None else "Pick a plate in the model")
        if self.mode.currentIndex()==1 or (self.mode.currentIndex()==2 and self.plane.currentIndex()==4):
            self._invalidate()
            if face is not None and self.isVisible():
                self._preview()

    def refresh(self):
        key = (id(self.app.project),self.app.session.revision.document_hash)
        if self.revision==key:
            return
        if self.bound is not self.app.selection:
            if self.bound is not None:
                self.bound.remove_listener(self._selection)
            self.bound = self.app.selection
            self.bound.add_listener(self._selection)
        changed_project = self.revision is None or self.revision[0]!=key[0]
        self.revision = key
        previous = self.part.currentData()
        self.part.blockSignals(True)
        self.part.clear()
        for part in self.app.project.geometry.parts.values():
            if part.sheet_ids:
                self.part.addItem(f"{part.name or 'Part'}  ·  {part.id}",part.id)
        index = self.part.findData(previous)
        if index>=0:
            self.part.setCurrentIndex(index)
        self.part.blockSignals(False)
        plane_id = self.plane.currentData()
        self.plane.blockSignals(True)
        while self.plane.count()>6:
            self.plane.removeItem(6)
        for key,system in self.app.project.coordinate_systems.items():
            if key!="global" and getattr(system,"kind","cartesian")=="cartesian":
                self.plane.addItem(f"{system.name} · XY",key)
        if plane_id and self.plane.findData(plane_id)>=0:
            self.plane.setCurrentIndex(self.plane.findData(plane_id))
        self.plane.blockSignals(False)
        self.saved_views.clear()
        self.queue_choice.clear()
        self.last_file = None
        self.open_btn.setEnabled(False)
        if changed_project:
            self.quantity.setValue(1)
            self.basis.setCurrentIndex(0)
            self.face = None
            self.plate_label.setText("Pick a plate in the model")
        self._selection()
        self._fill_sides()
        self._invalidate()

    def side_row(self, sheet_id):
        return self._side_rows[sheet_id]

    def side_choice(self, sheet_id):
        return self.sides.cellWidget(self._side_rows[sheet_id],1)

    def _fill_sides(self,*_):
        from ...application.thickness_side import override_choices, resolve_thickness_sides
        self.sides.setRowCount(0)
        self._side_rows = {}
        project = self.app.project
        part = project.geometry.parts.get(self.part.currentData())
        if part is None:
            return
        try:
            resolved, problem = resolve_thickness_sides(project, part.id), ""
            choices = override_choices(project)
        except (ValueError, ImportError) as error:
            resolved, problem, choices = {}, str(error), {}
        g = project.geometry
        for row,sheet_id in enumerate(part.sheet_ids):
            self.sides.insertRow(row)
            self._side_rows[sheet_id] = row
            self.sides.setItem(row,0,QTableWidgetItem(g.sheets[sheet_id].name or f"Sheet {sheet_id}"))
            box = QComboBox()
            for text,data in (("Auto","auto"),("Centre","centre"),("Front · +n","front"),("Back · -n","back")):
                box.addItem(text,data)
            box.setCurrentIndex(max(0,box.findData(choices.get(sheet_id,"auto"))))
            box.setAccessibleName(f"Thickness side of {g.sheets[sheet_id].name or sheet_id}")
            # Edit after the signal returns: the commit refreshes (rebuilds) this table.
            box.currentIndexChanged.connect(lambda index,sheet=sheet_id,widget=box: self._defer_side(
                sheet,widget.itemData(index)))
            self.sides.setCellWidget(row,1,box)
            side,source,reason = resolved.get(sheet_id,("unresolved","",problem))
            self.sides.setItem(row,2,QTableWidgetItem(f"{side} · {source}: {reason}" if source else
                                                      f"{side} · {reason}"))

    def _defer_side(self,sheet_id,choice):
        QTimer.singleShot(0,lambda: self._side_changed(sheet_id,choice))

    def _side_changed(self,sheet_id,choice):
        project = self.app.project
        if sheet_id not in project.geometry.sheets:
            return
        try:
            self.app.session.execute(SetThicknessSide(sheet_id,choice),solver_affecting=False)
        except (PermissionError, ValueError) as error:
            self.feedback.setText(str(error))
            self._fill_sides()
            return
        self.feedback.setText("Thickness side changed. Generate a new preview.")

    _EXTENT_LABELS = (("isometric","Isometric"),("plan","Plan"),("front","Front"),("right","Right"),
                      ("sections","Sections"),("plate_profiles","Plate profiles"),
                      ("index_and_takeoff","Index and takeoff"))

    def _build_extent(self,form):
        """Selectable generation extent: a preset plus one check box per part, persisted like the datum."""
        import json
        from ...application.drawing_extent import DrawingExtent
        self._extent_busy = True
        self.extent_preset = QComboBox()
        self.extent_preset.addItem("All views","all_views")
        self.extent_preset.addItem("Isometric only","isometric_only")
        self.extent_preset.addItem("Custom","custom")
        self.extent_preset.setAccessibleName("Drawing extent")
        form.addRow("Extent",self.extent_preset)
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0,0,0,0)
        self.extent_checks = {}
        for n,(key,label) in enumerate(self._EXTENT_LABELS):
            box = QCheckBox(label)
            box.setAccessibleName(f"Extent {label}")
            self.extent_checks[key] = box
            grid.addWidget(box,n//2,n%2)
        form.addRow(holder)
        state = DrawingExtent().to_dict()
        try:
            state.update(json.loads(self.settings.value("drawings/extent","{}")))
        except (TypeError, ValueError):
            pass
        chosen = set(state.get("views",())) if isinstance(state.get("views"),list) else set(DrawingExtent().views)
        for key,box in self.extent_checks.items():
            box.setChecked(key in chosen if key in DrawingExtent().views else bool(state.get(key,True)))
        self._sync_extent_preset()
        self._extent_busy = False

    def _extent_state(self):
        return {"views":[k for k in ("isometric","plan","front","right") if self.extent_checks[k].isChecked()],
                **{k:self.extent_checks[k].isChecked() for k in ("sections","plate_profiles","index_and_takeoff")}}

    def extent(self):
        """The selected :class:`DrawingExtent`; refuses a selection with nothing to generate."""
        from ...application.drawing_extent import DrawingExtent
        return DrawingExtent.from_dict(self._extent_state())

    def _sync_extent_preset(self):
        from ...application.drawing_extent import DrawingExtent
        state = self._extent_state()
        key = "custom"
        for name,preset in (("all_views",DrawingExtent.all_views()),("isometric_only",DrawingExtent.isometric_only())):
            if state == preset.to_dict():
                key = name
        self.extent_preset.setCurrentIndex(self.extent_preset.findData(key))

    def _extent_preset_changed(self,*_):
        if self._extent_busy:
            return
        from ...application.drawing_extent import DrawingExtent
        key = self.extent_preset.currentData()
        if key != "custom":
            preset = DrawingExtent.all_views() if key == "all_views" else DrawingExtent.isometric_only()
            self._extent_busy = True
            for name,box in self.extent_checks.items():
                box.setChecked(name in preset.views if name in DrawingExtent().views else getattr(preset,name))
            self._extent_busy = False
        self._extent_changed()

    def _extent_box_toggled(self,*_):
        if self._extent_busy:
            return
        self._extent_busy = True
        self._sync_extent_preset()
        self._extent_busy = False
        self._extent_changed()

    def _extent_changed(self):
        import json
        state = self._extent_state()
        self.settings.setValue("drawings/extent",json.dumps(state))
        kept = []
        for original,named in self.saved_views:
            kind = original.projection
            if ((kind in state["views"]) if kind in ("isometric","plan","front","right") else
                    state["sections"] if kind == "section" else state["plate_profiles"] if kind == "plate" else True):
                kept.append((original,named))
        dropped = len(kept) != len(self.saved_views)
        self.saved_views[:] = kept
        self.queue_choice.clear()
        for _,view in self.saved_views:
            self.queue_choice.addItem(view.title)
        self._invalidate()
        if dropped:
            self.feedback.setText("Extent changed. Queued sheets outside the extent were removed.")

    def _representation_changed(self,*_):
        self.settings.setValue("drawings/representation",self.representation.currentData())
        dropped = bool(self.saved_views)
        self.saved_views.clear()
        self.queue_choice.clear()
        self._invalidate()
        if dropped:
            self.feedback.setText("Representation changed. Regenerate the queued sheets in the new representation.")

    def activate(self):
        self.refresh()
        self._studio()
        if self.mode.currentIndex()==1:
            self.app.selection.set_mode("face")
            if self.face is not None and not self.views and not self.source:
                self._preview()

    def _mode_changed(self,*_):
        mode = self.mode.currentIndex()
        for i,form in enumerate(self.forms):
            form.setVisible(i==mode)
        if self.isVisible() and mode==1:
            self._enable_pick()
        self._invalidate()
        if self.isVisible() and mode==1 and self.face is not None:
            self._preview()

    def _datum_changed(self,*_):
        self.settings.setValue("drawings/elevation_datum", self.elevation_datum.currentData())
        previous = len(self.saved_views)
        self.saved_views[:] = [(original,named) for original,named in self.saved_views
                               if original.projection not in {"isometric","plan","front","right"}]
        self.queue_choice.clear()
        for _,view in self.saved_views:
            self.queue_choice.addItem(view.title)
        self.queue.setVisible(bool(self.saved_views))
        self._invalidate()
        if previous != len(self.saved_views):
            self.feedback.setText("Elevation datum changed. Regenerate the queued assembly sheets using the new datum.")

    def _invalidate(self,*_):
        self.sequence += 1
        self.pool.clear()
        self.views = ()
        self.preview_ready = False
        self.source = None
        mode = self.mode.currentIndex()
        nominal = self.basis.currentIndex()==1
        self.density.setVisible(nominal)
        self.density_label.setVisible(nominal)
        self.custom.setVisible(self.plane.currentIndex()==5)
        self.pick_plane.setVisible(self.plane.currentIndex()==4)
        self.plane_hint.setText(("Origin at model centre. U and V are measured in the section plane."
                                 if self.plane.currentIndex()<3 else "Limits and offset are relative to the chosen plane."))
        self.bounds_widget.setVisible(self.limited.isChecked())
        ready = (self.part.currentData() is not None if mode==0 else self.face is not None
                 if mode==1 or self.plane.currentIndex()==4 else True)
        self.preview_btn.setEnabled(ready)
        self.export_btn.setEnabled(False)
        self.feedback.clear()
        self.paper.display(None,"Generate the assembly views" if mode==0 else
                          "Select a plate to see its drawing" if mode==1 else "Preview the section at the chosen plane")
        self.view_choice.hide()
        self.zoom.hide()
        self.added.hide()
        self.queue.setVisible(bool(self.saved_views))

    def _plane_request(self):
        project = self.app.project
        i = self.plane.currentIndex()
        if i<3:
            points = np.array([v.position for v in project.geometry.vertices.values()])
            if not len(points):
                raise ValueError("Select a model containing geometry.")
            origin = .5*(points.min(axis=0)+points.max(axis=0))
            normal,up = [((0,0,1),(0,1,0)),((0,-1,0),(0,0,1)),((1,0,0),(0,0,1))][i]
        elif i==3:
            frame = self.app.session.active_workplane.resolve(project.coordinate_systems)
            origin,normal,up = frame.origin,frame.normal,frame.y_axis
        elif i==4:
            from ...application.drawing_sections import face_plane
            if self.face is None:
                raise ValueError("Pick a planar face.")
            origin,normal,up = face_plane(project,self.face)
        elif i>=6:
            from ...model.workplanes import Workplane
            frame = Workplane(coordinate_system_id=self.plane.currentData()).resolve(project.coordinate_systems)
            origin,normal,up = frame.origin,frame.normal,frame.y_axis
        else:
            origin = np.array([v.value() for v in self.xyz])*.001
            normal = np.array([v.value() for v in self.normal])
            up = (0,1,0) if abs(normal[2])>.99*np.linalg.norm(normal) else (0,0,1)
        normal = np.asarray(normal,dtype=float)
        if np.linalg.norm(normal)==0:
            raise ValueError("A plane needs a nonzero normal.")
        normal = normal/np.linalg.norm(normal)
        origin = np.asarray(origin)+normal*self.offset.value()*.001
        bounds = tuple(v.value()*.001 for v in self.bounds) if self.limited.isChecked() else None
        if bounds and (bounds[0]>=bounds[1] or bounds[2]>=bounds[3]):
            raise ValueError("Each maximum extent must exceed its minimum.")
        return (origin,normal,up),{"bounds":bounds}

    def _request(self):
        mode = self.mode.tabText(self.mode.currentIndex())
        representation = self.representation.currentData()
        extent = self.extent()
        if mode=="Assembly":
            if not (extent.views or extent.plate_profiles or extent.index_and_takeoff):
                raise ValueError("Select at least one assembly view, the plate profiles or the index to preview.")
            return mode,(self.part.currentData(),),dict(quantity=self.quantity.value(),views=extent.views,
                        density=self.density.value() if self.basis.currentIndex() else None,
                        elevation_datum=self.elevation_datum.currentData(),
                        thickness_sides="auto",representation=representation)
        if mode=="Plate":
            if self.face is None:
                raise ValueError("Select a plate in the model.")
            return mode,(self.face,),dict(include_stiffeners=self.stiffeners.isChecked(),
                                        include_plate_intersections=self.intersections.isChecked(),
                                        thickness_sides="auto",representation=representation)
        args, options = self._plane_request()
        return mode,args,{**options,"thickness_sides":"auto","representation":representation}

    def _preview(self):
        self._invalidate()
        try:
            req = self._request()
            snapshot = self.app.session.snapshot()
            self.source = (snapshot,req)
            self.feedback.setText("Generating drawing…")
            self.preview_btn.setEnabled(False)
            self.pool.start(_PreviewJob(self.sequence,snapshot,req,self.signals))
        except Exception as exc:
            self.feedback.setText(str(exc))

    @Slot(int,object,str)
    def _received(self,seq,views,error):
        if seq!=self.sequence:
            return
        self.preview_btn.setEnabled(True)
        if error:
            self.feedback.setText(error)
            return
        self.views = views
        self.preview_ready = True
        if not views:
            self.paper.display(None,"No assembly views selected")
            self.view_choice.hide()
            self.zoom.hide()
            self.added.hide()
            self.export_btn.setEnabled(not self.exporting)
            self.feedback.setText("No assembly views are selected. Export issues the selected plate sheets "
                                  "and index for this Part.")
            return
        self.view_choice.blockSignals(True)
        self.view_choice.clear()
        self.view_choice.addItems([v.title for v in views])
        self.view_choice.blockSignals(False)
        self._show_view(0)
        self.view_choice.setVisible(len(views)>1)
        self.zoom.setToolButtonStyle(Qt.ToolButtonIconOnly if len(views)>1 else Qt.ToolButtonTextBesideIcon)
        self.zoom.show()
        self.added.setVisible(self.mode.currentIndex()!=0)
        self.export_btn.setEnabled(not self.exporting)

    def _show_view(self,index):
        if 0<=index<len(self.views):
            v = self.views[index]
            self.paper.display(v)
            self.feedback.setText("\n".join(v.notes))

    def _enlarge(self):
        if not self.views:
            return
        view = self.views[self.view_choice.currentIndex()]
        dialog = QDialog(self)
        dialog.setWindowTitle(view.title)
        form = QVBoxLayout(dialog)
        paper = PaperPreview()
        paper.setMaximumHeight(16777215)
        paper.display(view)
        form.addWidget(paper,1)
        details = QLabel("\n".join(view.notes))
        details.setWordWrap(True)
        form.addWidget(details)
        dialog.resize(870,600)
        dialog.exec()

    def _keep(self):
        if self.views:
            v = self.views[self.view_choice.currentIndex()]
            if self.detail_name.text().strip():
                v = replace(v,title=self.detail_name.text().strip())
            self.saved_views.append((self.views[self.view_choice.currentIndex()],v))
            self.queue_choice.addItem(v.title)
            self.queue_choice.setCurrentIndex(len(self.saved_views)-1)
            self.queue.show()
            self.detail_name.clear()

    def _remove(self):
        i = self.queue_choice.currentIndex()
        if i>=0:
            self.saved_views.pop(i)
            self.queue_choice.removeItem(i)
            self.queue.setVisible(bool(self.saved_views))

    def _export(self):
        if not self.source or not self.preview_ready or self.exporting:
            return
        snapshot,(mode,args,options) = self.source
        if snapshot.revision.document_hash != self.app.session.revision.document_hash:
            self._invalidate()
            self.feedback.setText("The model changed. Generate a new preview.")
            return
        converter = None
        backend = "oda"
        if self.format.currentIndex():
            self._studio()
            if self.studio:
                converter,backend = self.studio,"studio"
            else:
                converter = self.settings.value("drawings/oda","")
            if not converter or not Path(converter).is_file():
                converter,_ = QFileDialog.getOpenFileName(self,"Select ODA File Converter")
                if not converter:
                    return
                self.settings.setValue("drawings/oda",converter)
        filename,_ = QFileDialog.getSaveFileName(self,"Name the new drawing folder","Drawings",
                                                  options=QFileDialog.DontConfirmOverwrite)
        if not filename:
            return
        retained = {id(original): named for original,named in self.saved_views}
        views = [retained.get(id(current),current) for current in self.views]
        views.extend(named for original,named in self.saved_views if all(original is not v for v in self.views))
        opts = dict(part_id=args[0] if mode=="Assembly" else None,
                    quantity=options.get("quantity",1),density=options.get("density"),converter=converter,backend=backend)
        opts["elevation_datum"] = options.get("elevation_datum", "support_base")
        opts["include_plate_intersections"] = self.intersections.isChecked()
        opts["representation"] = options.get("representation","solid")
        opts["thickness_sides"] = options.get("thickness_sides","auto")
        opts["extent"] = self.extent()
        number = self.view_choice.currentIndex()+1
        self.export_rel = ("Drawings.pdf" if not self.views else
                           f"DWG-views/View-{number:02d}.dwg" if converter else f"views/View-{number:02d}.dxf")
        self.exporting = True
        self.export_btn.setEnabled(False)
        self.feedback.setText("Writing drawing sheets and CAD files…")
        self.export_pool.start(_ExportJob(filename,snapshot,views,opts,self.signals))

    @Slot(object,object,str)
    def _exported(self,revision,out,error):
        self.exporting = False
        self.export_btn.setEnabled(self.preview_ready)
        message = error or f"Saved drawings to {out}"
        if revision.document_hash==self.app.session.revision.document_hash:
            if out:
                self.last_file = out/self.export_rel
                self._studio()
            self.feedback.setText(message)
        self.app.set_status(message,error=bool(error))

    def dispose(self):
        self.sequence += 1
        self.pool.clear()
        if self.bound is not None:
            self.bound.remove_listener(self._selection)
            self.bound = None
