"""Working-copy construction and face sketches on the shared viewport."""
from dataclasses import replace,asdict
from copy import deepcopy
import json
import math
from uuid import uuid4
from PySide6.QtCore import QTimer, Qt, QEvent
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox,
    QLineEdit, QCheckBox, QPushButton, QLabel, QPlainTextEdit, QToolButton, QGridLayout, QListWidget)
from anygeometry import GeometryError, face_sketch_plane, SketchConstraint,SketchDefinition, SketchPlane, plane_sketch_plane

from ... import commands
from ...geometry.construction import ConstructionTask, ConstructionMode
from ...geometry.sketching import FaceSketchTask
from ...geometry.snapping import geometry_snap_data
from ...model.coordinates import CoordinateSystem
from ...model.workplanes import Workplane
from ..numeric_input import parse_numeric_sequence
from .editors import apply_on_enter


class ConstructionPanel(QWidget):
    def __init__(self, app):
        super().__init__();self.app=app;self.support=None;self.editing_feature_id=None;self.editing_plane=None
        layout=QVBoxLayout(self)
        heading=QLabel("Sketcher");heading.setStyleSheet("font-size: 16px; font-weight: 600;");layout.addWidget(heading)
        self.summary=QLabel("Choose a plane or select one flat face, then Start sketch. Draw points in order; Enter finishes and Escape cancels.")
        self.summary.setWordWrap(True);layout.addWidget(self.summary)
        actions=QHBoxLayout();layout.addLayout(actions)
        self.buttons={}
        self.last_sketch_feature_id=None
        def button(label,callback,target=layout):
            control=QPushButton(label);control.clicked.connect(app.guarded(callback));target.addWidget(control)
            self.buttons[label]=control
            return control
        button("Start sketch",self.start_sketch_workflow,actions).setProperty("primary",True)
        button("Apply",self.apply,actions).setText("Finish sketch")
        button("Cancel",self.cancel,actions)
        button("Cut plate",lambda:app.cut_plate_with_sketch(self.last_sketch_feature_id),actions).setText("Cut plate with sketch…")
        self.tool_widget=QWidget();tool_grid=QGridLayout(self.tool_widget);tool_grid.setContentsMargins(0,0,0,0)
        self.tool_buttons={}
        for i,(key,label,tip) in enumerate((('select','Select','Click a point; Shift adds to selection. Drag a point to move it.'),('line','Line','Click start and end; existing endpoints are reused.'),('connected','Connected lines','Click points in order; Enter completes the chain.'),('arc','Arc','Click start, a point on the arc, and end.'),('circle','Circle','Click centre and a point on the circle.'),('spline','Spline','Click fit or control points; Enter completes the curve.'),('fillet','Rounded corner','Select two connected straight segments, then set radius.'),('dimension','Dimension','Select two points or one curve, then enter its dimension.'))):
            control=QToolButton();control.setText(label);control.setCheckable(True);control.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            control.setIcon(self.tool_icon(key));control.setToolTip(tip);control.clicked.connect(app.guarded(lambda checked=False,k=key:self.choose_tool(k)))
            tool_grid.addWidget(control,i//4,i%4);self.tool_buttons[key]=control
        layout.addWidget(self.tool_widget)
        self.spline_box=QWidget();spline_form=QFormLayout(self.spline_box);spline_form.setContentsMargins(0,0,0,0)
        self.spline_kind=QComboBox();self.spline_kind.addItem('Fit-point spline','spline');self.spline_kind.addItem('Control-point NURBS','nurbs');spline_form.addRow('Spline mode',self.spline_kind)
        self.spline_degree=QLineEdit('3');self.spline_degree.setToolTip('Curve degree; at most the number of points minus one.');spline_form.addRow('Degree',self.spline_degree)
        self.spline_weights=QLineEdit();self.spline_weights.setPlaceholderText('optional, one per point');spline_form.addRow('NURBS weights',self.spline_weights)
        layout.addWidget(self.spline_box)
        edit_actions=QHBoxLayout();layout.addLayout(edit_actions)
        button('Undo sketch',lambda:self.local_undo(),edit_actions);button('Redo sketch',lambda:self.local_undo(True),edit_actions)
        button('Close loop',self.close_loop,edit_actions)
        form=QFormLayout();layout.addLayout(form)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.form=form;self.length_labels={};self.length_forms={}
        self.sketch_name=QLineEdit("Sketch");form.addRow("Sketch name",self.sketch_name)
        self.support_choice=QComboBox()
        self.support_choice.addItem("Construction plane","plane");self.support_choice.addItem("Selected flat face","face")
        form.addRow("Sketch on",self.support_choice)
        self.support_info=QLabel();self.support_info.setWordWrap(True);form.addRow(self.support_info)
        self.system=QComboBox();form.addRow("Construction plane",self.system)
        self.offset=QLineEdit("0");self.length_row("Plane offset",self.offset)
        self.closed=QCheckBox("Closed profile");self.closed.setChecked(True);form.addRow(self.closed)
        self.coordinates=QLineEdit();self.length_row("Add local point (u, v)",self.coordinates)
        self.coordinates.setPlaceholderText("For example: 1 m, 0.5 m — Enter adds the point")
        point_actions=QHBoxLayout();layout.addLayout(point_actions)
        button("Add local point",self.add_point,point_actions);button("Remove last point",self.backspace,point_actions)
        button("View sketch plane",self.view_plane)
        self.extrusion=QLineEdit("0");self.length_row("Sketch extrusion",self.extrusion)
        self.extrusion.setToolTip("Optional extrusion along the plane normal. Leave at zero to create profile edges only.")
        plane_body,plane_layout=self.section(layout,"Create a construction plane")
        plane_form=QFormLayout();plane_layout.addLayout(plane_form)
        self.plane_name=QLineEdit("Plane");plane_form.addRow("Name",self.plane_name)
        self.plane_origin=QLineEdit("0, 0, 0");self.length_row("Origin (X, Y, Z)",self.plane_origin,plane_form)
        self.plane_normal=QLineEdit("0, 0, 1");plane_form.addRow("Normal (X, Y, Z)",self.plane_normal)
        self.plane_x_axis=QLineEdit("1, 0, 0");plane_form.addRow("X direction (X, Y, Z)",self.plane_x_axis)
        button("Create construction plane",self.create_plane,plane_layout)
        self.plane_body=plane_body
        _,snap_layout=self.section(layout,"Grid and snapping")
        snap_form=QFormLayout();snap_layout.addLayout(snap_form)
        self.spacing=QLineEdit("1 m");self.length_row("Grid spacing",self.spacing,snap_form)
        self.tolerance=QLineEdit("0.05 m");self.length_row("Snap tolerance",self.tolerance,snap_form)
        self.grid=QCheckBox("Snap to grid and axes");self.grid.setChecked(True);snap_layout.addWidget(self.grid)
        self.objects=QCheckBox("Snap to endpoints, midpoints and intersections");self.objects.setChecked(True);snap_layout.addWidget(self.objects)
        self.snap_body=self.grid.parentWidget()
        _,constraint_layout=self.section(layout,"Dimensions and constraints")
        constraint_form=QFormLayout();constraint_layout.addLayout(constraint_form)
        self.constraint_summary=QLabel("No constraints");constraint_layout.addWidget(self.constraint_summary)
        self.pair=QLineEdit();self.distance=QLineEdit("1 m")
        self.radius=QLineEdit("1 m");self.length_row("Radius",self.radius,constraint_form)
        self.diameter=QCheckBox("Use diameter");constraint_form.addRow(self.diameter)
        button("Set radius",self.set_radius,constraint_layout)
        self.constraint_list=QListWidget();self.constraint_list.setMaximumHeight(110);constraint_layout.addWidget(self.constraint_list)
        button("Remove selected constraint",self.remove_selected_constraint,constraint_layout)
        constraint_form.addRow("Point numbers",self.pair);self.length_row("Distance",self.distance,constraint_form)
        constraint_grid=QGridLayout();constraint_layout.addLayout(constraint_grid)
        self.constraint_buttons={}
        for i,(kind,label,tip) in enumerate((("distance","Set distance","Fix the distance between two selected points to the value above."),("coincident","Join points","Make two selected points one point."),("horizontal","Horizontal","Keep two selected points at the same height."),("vertical","Vertical","Keep two selected points at the same horizontal position."),("fixed","Fix point","Lock one selected point in place."))):
            control=button(label,lambda checked=False,k=kind:self.add_constraint(k),constraint_grid);constraint_grid.removeWidget(control);constraint_grid.addWidget(control,i//2,i%2)
            control.setToolTip(tip);self.constraint_buttons[kind]=control
        constraint_form.setRowVisible(self.pair,False)
        self.constraint_body=self.pair.parentWidget()
        _,raw_layout=self.section(constraint_layout,"Advanced constraint records")
        self.constraints=QPlainTextEdit("[]");self.constraints.setMaximumHeight(90);raw_layout.addWidget(self.constraints)
        self.constraints.setToolTip("Constraint records use SI metres for dimensional values.")
        _,other_layout=self.section(layout,"Other construction tools")
        other_form=QFormLayout();other_layout.addLayout(other_form)
        self.mode=QComboBox()
        for item in ConstructionMode:self.mode.addItem("Connected lines" if item.value=="polyline" else item.value.title(),item.value)
        other_form.addRow("Construction",self.mode)
        self.close_polyline=QCheckBox("Close polyline");other_form.addRow(self.close_polyline)
        button("Start construction",self.start,other_layout)
        button("Construct plate from existing vertices",lambda:app.geometry_actions.start_plate(),other_layout)
        self.closed.toggled.connect(self.closed_changed)
        self.spline_kind.currentIndexChanged.connect(lambda *_:self.reselect_spline())
        self.support_choice.currentIndexChanged.connect(self.sync_controls)
        self.diameter.toggled.connect(lambda:self.updated(self.app.viewport.construction_task,None) if isinstance(self.app.viewport.construction_task,FaceSketchTask) else None)
        for control in self.findChildren(QWidget):control.installEventFilter(self)
        for owner in self.findChildren(QWidget):
            nested=owner.layout()
            if nested is not None:
                for index in range(nested.count()):
                    child=nested.itemAt(index).layout()
                    if isinstance(child,QFormLayout):
                        child.setRowWrapPolicy(QFormLayout.WrapLongRows)
                        child.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addStretch();self.sync_controls()

    @staticmethod
    def tool_icon(key):
        pixmap=QPixmap(28,28);pixmap.fill(Qt.transparent);p=QPainter(pixmap);p.setRenderHint(QPainter.Antialiasing);p.setPen(QPen(QColor('#2762a8'),2))
        if key in ('line','connected','dimension'):p.drawLine(4,23,23,5)
        elif key=='circle':p.drawEllipse(5,5,18,18)
        elif key in ('arc','fillet'):p.drawArc(4,4,21,21,0,110*16)
        elif key=='spline':
            from PySide6.QtGui import QPainterPath
            path=QPainterPath();path.moveTo(3,23);path.cubicTo(8,-8,20,36,25,4);p.drawPath(path)
        else:p.drawLine(6,3,6,24);p.drawLine(6,3,21,17);p.drawLine(6,24,12,16);p.drawLine(12,16,21,17)
        if key in ('line','connected','dimension'):
            p.setBrush(QColor('white'));p.drawEllipse(2,21,4,4);p.drawEllipse(21,3,4,4)
        p.end();return QIcon(pixmap)

    def choose_tool(self,key):
        task=self.app.viewport.construction_task
        if not isinstance(task,FaceSketchTask):return
        if key=='spline':
            key=self.spline_kind.currentData();task.spline_degree=int(self.spline_degree.text());task.spline_weights=tuple(parse_numeric_sequence(self.spline_weights.text(),float)) if self.spline_weights.text().strip() else ()
        task.choose_tool(key);self.app.viewport.refresh_construction_overlay();self.updated(task,None)
        if key in ('dimension','fillet'):self.constraint_body.section_toggle.setChecked(True)

    def reselect_spline(self):
        task=self.app.viewport.construction_task
        if isinstance(task,FaceSketchTask) and task.tool in ('spline','nurbs') and not task.pending:self.app.guarded(lambda:self.choose_tool('spline'))()

    def focus_dimension(self,field=None):
        from PySide6.QtWidgets import QScrollArea
        field=self.distance if field is None else field
        parent=field.parentWidget()
        while parent is not None:
            if isinstance(parent,QScrollArea):parent.ensureWidgetVisible(field)
            parent=parent.parentWidget()
        QTimer.singleShot(0,field,lambda:(field.setFocus(Qt.OtherFocusReason),field.selectAll()))

    def enter(self):
        task=self.app.viewport.construction_task
        if isinstance(task,FaceSketchTask) and task.pending:
            task.complete_tool();self.app.viewport.refresh_construction_overlay();self.updated(task,None)
        elif task is not None and task.ready:self.apply()

    def escape(self):
        task=self.app.viewport.construction_task
        if isinstance(task,FaceSketchTask):task.escape_tool();self.app.viewport.refresh_construction_overlay();self.updated(task,None)
        else:self.cancel()

    def close_loop(self):
        task=self.app.viewport.construction_task
        if isinstance(task,FaceSketchTask):task.complete_tool(closed=True);self.app.viewport.refresh_construction_overlay();self.updated(task,None)

    def local_undo(self,redo=False):
        task=self.app.viewport.construction_task
        if isinstance(task,FaceSketchTask):
            (task.redo_local if redo else task.undo_local)();self.app.viewport.refresh_construction_overlay();self.updated(task,None)

    def remove_selected_constraint(self):
        task=self.app.viewport.construction_task;index=self.constraint_list.currentRow()
        if isinstance(task,FaceSketchTask) and 0<=index<len(task.constraints):task.checkpoint();task.constraints.pop(index);self.app.viewport.refresh_construction_overlay();self.updated(task,None)

    def set_radius(self):
        from anygeometry import set_sketch_radius,round_sketch_corner
        task=self.app.viewport.construction_task
        if not isinstance(task,FaceSketchTask):return
        definition=task.definition(0,allow_incomplete=True);radius=self.length(self.radius.text())/(2 if self.diameter.isChecked() else 1)
        if len(task.selected_entities)==2:definition=round_sketch_corner(definition,*task.selected_entities,radius)
        elif len(task.selected_entities)==1:definition=set_sketch_radius(definition,task.selected_entities[0],radius)
        else:raise ValueError('Select one arc/circle or two connected straight segments')
        from anygeometry import solve_sketch_edit
        result=solve_sketch_edit(definition,task.plane)
        if not result.converged:raise GeometryError("Radius conflicts with existing constraints")
        before_ids={e.id for e in task.entities}
        task.checkpoint();replacement=FaceSketchTask(task.plane,task.snap_tolerance,definition)
        task._restore(replacement._state());task.selected_entities[:]=[e.id for e in task.entities if e.id not in before_ids] or task.selected_entities
        self.app.viewport.refresh_construction_overlay();self.updated(task,None)

    @staticmethod
    def section(layout,title):
        toggle=QPushButton(title);toggle.setCheckable(True);layout.addWidget(toggle)
        body=QWidget();content=QVBoxLayout(body);content.setContentsMargins(8,0,0,4)
        layout.addWidget(body);body.hide();toggle.toggled.connect(body.setVisible)
        body.section_toggle=toggle
        return body,content

    def coordinate_systems(self):
        systems={
            "sketch-xz":CoordinateSystem("XZ",id="sketch-xz",axis=(0,-1,0),reference=(1,0,0)),
            "sketch-yz":CoordinateSystem("YZ",id="sketch-yz",axis=(1,0,0),reference=(0,1,0)),
        }
        systems.update(self.app.project.coordinate_systems)
        return systems

    def sync_controls(self,*_):
        active=self.app.viewport.construction_active
        update_wordmark=getattr(self.app,"_update_wordmark",None)
        if callable(update_wordmark):update_wordmark()
        task=self.app.viewport.construction_task
        plane=self.support_choice.currentData()=="plane"
        for control in (self.support_choice,self.plane_body,self.snap_body,self.buttons["Start sketch"],self.buttons["Start construction"]):
            control.setEnabled(not active and not self.app.session.read_only)
        self.system.setEnabled(not active and plane);self.offset.setEnabled(not active and plane)
        self.form.setRowVisible(self.support_choice,not active)
        self.form.setRowVisible(self.system,plane and not active)
        self.form.setRowVisible(self.offset,plane and not active)
        for key in ("Undo sketch","Redo sketch","Close loop"):self.buttons[key].setVisible(active and isinstance(task,FaceSketchTask))
        self.buttons["Cut plate"].setEnabled(not active and not self.app.session.read_only)
        self.support_info.setVisible(active)
        self.support_info.setText(getattr(self,"_support_label",""))
        self.tool_widget.setVisible(active and isinstance(task,FaceSketchTask))
        for key,control in self.tool_buttons.items():control.setChecked(active and isinstance(task,FaceSketchTask) and (task.tool==key or key=='spline' and task.tool=='nurbs'))
        self.buttons["Apply"].setEnabled(active and task.ready and not self.app.session.read_only)
        self.buttons["Start sketch"].setVisible(not active)
        self.buttons["Apply"].setVisible(active)
        self.buttons["Cancel"].setEnabled(active)
        self.buttons["Cancel"].setVisible(active)
        sketching=active and isinstance(task,FaceSketchTask)
        for key in ("Add local point","Remove last point","View sketch plane"):
            self.buttons[key].setEnabled(active);self.buttons[key].setVisible(active)
        self.form.setRowVisible(self.coordinates,active)
        self.constraint_body.section_toggle.setVisible(sketching)
        if not sketching:self.constraint_body.section_toggle.setChecked(False)
        self.spline_box.setVisible(sketching and task.tool in ('spline','nurbs'))

    def start_sketch_workflow(self):
        if self.support_choice.currentData()=="face":self.sketch()
        else:self.sketch_on_plane()

    def cancel(self):
        self.app.viewport.cancel_construction()
        self.support=None;self.editing_feature_id=None;self.editing_plane=None
        self.sync_controls()

    def closed_changed(self,closed):
        task=self.app.viewport.construction_task
        if isinstance(task,FaceSketchTask):
            task.preview_closed=closed
            self.app.viewport.refresh_construction_overlay();self.updated(task,None)

    def view_plane(self,workplane=None,systems=None):
        workplane=self.app.viewport._construction_workplane if workplane is None else workplane
        if workplane is None:return
        systems=self.app.viewport._construction_coordinate_systems if systems is None else systems
        frame=workplane.resolve(systems)
        normal=frame.normal
        azimuth=math.degrees(math.atan2(normal[1],normal[0]))
        if abs(normal[2])>0.999999:azimuth=math.degrees(math.atan2(-frame.x_axis[0],frame.x_axis[1]))
        canvas=self.app.viewport.canvas
        canvas.set_view(azimuth,math.degrees(math.asin(max(-1.0,min(1.0,float(normal[2]))))))
        canvas.set_camera_target(self.app.viewport._point3d(*frame.origin))

    def eventFilter(self, watched, event):
        if self.app.viewport.construction_active:
            if event.type()==QEvent.KeyPress and event.key() in (Qt.Key_Return,Qt.Key_Enter) and event.modifiers() in (Qt.NoModifier,Qt.KeypadModifier):
                if not event.isAutoRepeat():
                    callback=self.add_point if watched is self.coordinates else (lambda:self.add_constraint('distance')) if watched is self.distance else self.set_radius if watched is self.radius else self.enter
                    self.app.guarded(callback)()
                return True
            if event.type()==QEvent.KeyPress and event.key()==Qt.Key_Escape:self.escape();return True
        elif watched in (self.plane_name,self.plane_origin,self.plane_normal,self.plane_x_axis,self.buttons["Create construction plane"]):
            if apply_on_enter(watched,event,self.buttons["Create construction plane"]):return True
        return super().eventFilter(watched,event)

    def idle_enter_button(self):
        return self.buttons["Create construction plane"] if self.plane_body.isVisible() and self.plane_body.isEnabled() else self.buttons["Start sketch"]

    def length_row(self,label,field,form=None):
        form=self.form if form is None else form
        form.addRow(f"{label} ({self.app.project.units.symbol('length')})",field)
        self.length_labels[field]=label
        self.length_forms[field]=form

    def length(self,text):
        return self.app.project.units.parse(text,"length")

    def vector(self, text, label, *, units=False):
        values=parse_numeric_sequence(
            text, self.length if units else float, units=units,
        )
        if len(values)!=3:raise ValueError(f"{label} needs exactly three components")
        return tuple(values)

    def create_plane(self):
        if self.app.viewport.construction_active:raise ValueError("Finish or cancel the current construction first")
        name=self.plane_name.text().strip()
        if not name:raise ValueError("Enter a name for the construction plane")
        system=self.app.run(commands.AddConstructionPlane(
            name=name,
            origin=self.vector(self.plane_origin.text(),"Plane origin",units=True),
            normal=self.vector(self.plane_normal.text(),"Plane normal"),
            x_direction=self.vector(self.plane_x_axis.text(),"Plane X direction"),
        ))
        self.select_plane(system)
        self.app.set_status(f"Created construction plane {system.name}")

    def select_plane(self,system):
        self.plane_body.section_toggle.setChecked(False)
        self.offset.setText("0")
        self.refresh()
        index=self.system.findData(system.id)
        if index>=0:self.system.setCurrentIndex(index)
        self.support_choice.setCurrentIndex(0)
        plane=self.workplane()
        self.app.session.active_workplane=plane
        self.app.viewport.preview_construction_plane(plane,system.name)
        self.view_plane(plane,self.coordinate_systems())

    def workplane(self):
        objects=self.objects.isChecked()
        return Workplane(self.system.currentData(),self.length(self.offset.text()),self.length(self.spacing.text()),
                         self.length(self.tolerance.text()),self.grid.isChecked(),self.grid.isChecked(),objects,objects,objects)

    def begin(self,task,plane,systems):
        if self.app.session.read_only:raise PermissionError("This project is read-only")
        if self.app.viewport.construction_active:raise ValueError("Finish or cancel the current construction first")
        self.app._begin_modelling()
        self.app.session.active_workplane=plane
        self.app.viewport.begin_construction(task,plane,systems,
            snap_data=lambda:geometry_snap_data(self.app.project.geometry),
            update_handler=self.updated,apply_handler=self.app.guarded(self.apply),
            length_formatter=lambda value:self.app.project.units.format(value,"length"))
        self.buttons["Apply"].setText("Finish sketch" if isinstance(task,FaceSketchTask) else "Apply construction")
        if isinstance(task,FaceSketchTask):self.view_plane()
        self.updated(task,None)

    def start(self):
        if self.app.viewport.construction_active:raise ValueError("Finish or cancel the current construction first")
        self.support=None;self.editing_feature_id=None;self.editing_plane=None
        self.begin(ConstructionTask(self.mode.currentData(),close=self.close_polyline.isChecked() and self.mode.currentData()=="polyline"),
                   self.workplane(),self.coordinate_systems())

    def sketch(self):
        refs=self.app.selection.ordered_items
        if len(refs)!=1 or refs[0].kind!="face":raise ValueError("Select one flat plate for its sketch")
        self.start_sketch(refs[0])

    def sketch_on_plane(self):
        if self.app.viewport.construction_active:raise ValueError("Finish or cancel the current construction first")
        self.support_choice.setCurrentIndex(0)
        self._support_label=f"Support: {self.system.currentText()} · offset {self.offset.text()}"
        self.support=None;self.editing_feature_id=None;self.editing_plane=None
        workplane=self.workplane()
        frame=workplane.resolve(self.coordinate_systems())
        plane=SketchPlane(frame.origin,frame.x_axis,frame.y_axis,frame.normal,(),())
        self.extrusion.setText(self.app.project.units.format(0,"length",precision=17))
        task=FaceSketchTask(plane,workplane.snap_tolerance);task.close=self.closed.isChecked()
        self.begin(task,workplane,self.coordinate_systems())

    def start_sketch(self,support,definition=None,feature_id=None):
        if self.app.viewport.construction_active:raise ValueError("Finish or cancel the current construction first")
        self.support_choice.setCurrentIndex(1)
        self._support_label=f"Support: flat face {support.id}"
        self.support=support;self.editing_feature_id=feature_id;self.editing_plane=None
        plane=face_sketch_plane(self.app.project.geometry,self.support.id)
        system=CoordinateSystem("Sketch plane",origin=plane.origin,axis=plane.normal,reference=plane.x_axis)
        systems=self.coordinate_systems();systems[system.id]=system
        workplane=replace(self.workplane(),coordinate_system_id=system.id,offset=0)
        self.closed.setChecked(definition.closed if definition is not None else True)
        self.extrusion.setText(self.app.project.units.format(definition.extrusion if definition is not None else 0,"length",precision=17))
        self.begin(FaceSketchTask(plane,workplane.snap_tolerance,definition),workplane,systems)

    def edit_sketch(self,feature_id):
        record=self.app.project.geometry.features.get(feature_id)
        if record.kind not in {"geometry.sketch.extrude","geometry.sketch.plane"}:return False
        if self.app.viewport.construction_active:raise ValueError("Finish or cancel the current construction first")
        self.sketch_name.setText(record.name)
        if record.kind=="geometry.sketch.plane":
            self.support_choice.setCurrentIndex(0)
            self._support_label="Support: saved sketch plane"
            raw=record.parameters.get("plane",{})
            if not isinstance(raw,dict):raise ValueError("Sketch plane is invalid")
            plane=plane_sketch_plane(raw["origin"],raw["normal"],raw["x_axis"])
            system=CoordinateSystem("Sketch plane",origin=plane.origin,axis=plane.normal,reference=plane.x_axis)
            systems=self.coordinate_systems();systems[system.id]=system
            self.support=None;self.editing_feature_id=record.feature_id;self.editing_plane=raw
            definition=SketchDefinition.from_parameters(record.parameters)
            self.closed.setChecked(definition.closed)
            self.extrusion.setText(self.app.project.units.format(definition.extrusion,"length",precision=17))
            workplane=replace(self.workplane(),coordinate_system_id=system.id,offset=0)
            self.begin(FaceSketchTask(plane,workplane.snap_tolerance,definition),workplane,systems)
            self.app.notebook.select("Construction");return True
        if record.kind!="geometry.sketch.extrude":return False
        refs=record.inputs.get("support_face",())
        if len(refs)!=1:raise ValueError("Sketch support is invalid")
        resolved=self.app.project.geometry.features.resolve(refs[0],self.app.project.geometry)
        if len(resolved)!=1 or resolved[0].kind!="face":raise ValueError("Sketch support is unresolved")
        self.start_sketch(resolved[0],SketchDefinition.from_parameters(record.parameters),record.feature_id)
        self.app.notebook.select("Construction");return True

    def add_constraint(self,kind):
        task=self.app.viewport.construction_task
        if not isinstance(task,FaceSketchTask):raise ValueError("Start a sketch first")
        indices=tuple(task.point_keys.index(key) for key in task.selected) if task.selected else tuple(value-1 for value in parse_numeric_sequence(self.pair.text(),int))
        if (len(indices)!= (1 if kind=="fixed" else 2) or len(set(indices))!=len(indices) or min(indices)<0 or max(indices)>=len(task.point_keys)):raise ValueError("Select one point for Fixed, or two points for this constraint: click a point, then Shift-click the second")
        task.checkpoint()
        extrusion=self.length(self.extrusion.text()) if task.ready else None
        previous_constraints=list(task.constraints)
        try:
            if kind=="distance":
                keys={task.point_keys[i] for i in indices}
                task.constraints[:]=[c for c in task.constraints if c.kind!="distance" or {c.first,c.second}!=keys]
                task.add_distance(*indices,self.length(self.distance.text()))
            elif kind=="coincident":task.add_coincidence(*indices)
            else:task.constraints.append(SketchConstraint(kind,task.point_keys[indices[0]],task.point_keys[indices[1]] if len(indices)>1 else None,id="c"+uuid4().hex))
            if task.ready:
                from anygeometry import solve_sketch_edit
                definition=task.definition(extrusion,allow_incomplete=True)
                task.constraints[:]=definition.constraints
                result=solve_sketch_edit(definition,task.plane,fixed_points=(task.point_keys[indices[0]],)) if kind=='distance' else solve_sketch_edit(definition,task.plane)
                if not result.converged:result=solve_sketch_edit(definition,task.plane)
                if not result.converged:raise GeometryError('Dimension conflicts with existing constraints')
                task._points[:]=[tuple(task.plane.world(result.points[k])) for k in task.point_keys]
                task.diagnostic=f'{result.degrees_of_freedom} degrees of freedom'
        except Exception:
            # Owner validation leaves points unchanged on failure. Restore the
            # constraints (including any replaced distance) and drop the undo
            # step of the attempt so the visible working copy stays valid.
            task.constraints[:]=previous_constraints
            if task._undo:task._undo.pop()
            raise
        self.app.viewport.refresh_construction_overlay();self.updated(task,None)

    def remove_constraint(self):
        task=self.app.viewport.construction_task
        if not isinstance(task,FaceSketchTask):raise ValueError("Start a sketch first")
        task.remove_last_constraint();self.app.viewport.refresh_construction_overlay();self.updated(task,None)

    def add_point(self):
        task=self.app.viewport.construction_task
        if task is None:raise ValueError("Start construction before adding a point")
        u,v=parse_numeric_sequence(self.coordinates.text(),self.length,units=True)
        frame=self.app.viewport._construction_workplane.resolve(self.app.viewport._construction_coordinate_systems)
        task.add_plane_coordinates(u,v,frame)
        self.app.viewport.refresh_construction_overlay();self.updated(task,None)

    def backspace(self):
        task=self.app.viewport.construction_task
        if task is not None:
            task.backspace();self.app.viewport.refresh_construction_overlay();self.updated(task,None)

    def apply(self):
        if self.app.session.read_only:raise PermissionError("This project is read-only")
        task=self.app.viewport.construction_task
        if isinstance(task,FaceSketchTask):
            name=self.sketch_name.text().strip()
            if not name:raise ValueError("Enter a sketch name")
            constraints=json.loads(self.constraints.toPlainText())
            # Validate the proposed edit separately. A failed owner solve or
            # project command must leave the displayed working preview intact.
            preview=deepcopy(task)
            if preview.pending:preview.complete_tool()
            preview.constraints[:]=[SketchConstraint(**item) for item in constraints]
            preview.close=self.closed.isChecked()
            preview.preview_closed=preview.close
            definition=preview.solve_preview(self.length(self.extrusion.text()))
            if self.editing_feature_id is None:
                if self.support is not None:feature=self.app.run(commands.AddSketch(self.support,definition,name))
                else:
                    frame=self.app.viewport._construction_workplane.resolve(self.app.viewport._construction_coordinate_systems)
                    feature=self.app.run(commands.AddPlaneSketch(definition,frame.origin,frame.normal,frame.x_axis,name))
            elif self.editing_plane is not None:
                feature=self.app.run(commands.EditFeature(self.editing_feature_id,name=name,parameters={**definition.to_parameters(),"plane":self.editing_plane}))
            else:feature=self.app.run(commands.EditFeature(self.editing_feature_id,name=name,parameters=definition.to_parameters()))
            self.last_sketch_feature_id=feature.feature_id
            self.app.viewport.end_construction()
            self.editing_feature_id=None;self.editing_plane=None
            self.support=None
            made=tuple(ref for key,ref in feature.outputs.items() if key.startswith("extrusion/face/"))
            if made:
                self.app.selection.set_mode("face")
                self.app.selection.restore(made)
            else:
                self.app.selection.set_mode("edge")
                self.app.selection.restore(tuple(dict.fromkeys(ref for key,ref in feature.outputs.items() if key.startswith(("profile/edge/","entity/")) and ref.kind=="edge")))
        else:
            result=self.app.viewport.finish_construction(self.app.run)
            made=result.edges or result.vertices
            if made:
                self.app.selection.set_mode("edge" if result.edges else "vertex")
                self.app.selection.restore(made)
        self.summary.setText("Sketch finished. Choose a support and Start sketch to draw another profile.")
        self.sync_controls()
        self.app.set_status("Construction applied")

    def updated(self,task,snap):
        if task.cancelled:
            self.summary.setText("Sketch cancelled. Choose a support and Start sketch to begin again.")
            self.support=None;self.editing_feature_id=None;self.editing_plane=None
            QTimer.singleShot(0,self.sync_controls)
            return
        if isinstance(task,FaceSketchTask):
            self.constraints.setPlainText(json.dumps([asdict(item) for item in task.constraints]))
            self.constraint_summary.setText(f"{len(task.selected)} selected · {len(task.constraints)} constraints · {task.diagnostic}")
            current=self.constraint_list.currentRow();self.constraint_list.clear()
            for c in task.constraints:self.constraint_list.addItem(f"{c.kind.replace('_',' ').title()}: {c.first}"+(f" → {c.second}" if c.second else '')+(f" = {self.app.project.units.format(c.value,'length')}" if c.value is not None else ''))
            self.constraint_list.setCurrentRow(current)
            if len(task.selected)==2:
                a,b=(task.points[task.point_keys.index(k)] for k in task.selected)
                self.distance.setText(self.app.project.units.format(math.dist(a,b),'length',precision=12))
            if task.selected or task.selected_entities:self.constraint_body.section_toggle.setChecked(True)
            if len(task.selected_entities)==1:
                from anygeometry import sketch_entity_radius
                entity=next(e for e in task.entities if e.id==task.selected_entities[0])
                if entity.kind in {'arc','circle','fillet'}:self.radius.setText(self.app.project.units.format(sketch_entity_radius(task.definition(0,allow_incomplete=True),entity)*(2 if self.diameter.isChecked() else 1),'length',precision=12))
        self.sync_controls()
        if isinstance(task,FaceSketchTask):
            picked=len(task.selected)
            for kind,control in self.constraint_buttons.items():control.setEnabled(picked==(1 if kind=="fixed" else 2))
            instructions={"select":"Click to select; Shift adds points or segments. Drag a point to move it.","line":"Click start and end. Existing points are reused.","connected":"Click connected points. Enter completes; Close loop closes the profile.","arc":"Click start, a point on the arc, and end.","circle":"Click centre and a point on the circle.","spline":"Click fit/control points; Enter completes the spline.","nurbs":"Click control points; Enter completes the NURBS.","dimension":"Select two points or a curve, then enter a dimension.","fillet":"Shift-select two connected segments, then set Radius."}
            self.summary.setText(instructions.get(task.tool,"")+" · Enter finishes the tool, then the sketch. Escape returns to Select.");return
        self.summary.setText(f"{len(task.points)} point(s) · {'Enter or Finish sketch to save' if task.ready else 'click points in the canvas'} · Backspace removes the last point; Escape cancels."+
                             (f" · {snap.kind.value} snap" if snap is not None and snap.kind is not None else ""))

    def refresh(self):
        for field,label in self.length_labels.items():
            self.length_forms[field].labelForField(field).setText(f"{label} ({self.app.project.units.symbol('length')})")
        systems=self.coordinate_systems()
        values=[("XY (Global)" if identifier=="global" else system.name,identifier) for identifier,system in systems.items()]
        values.sort(key=lambda item:0 if item[1]=="global" else 1)
        if values!=[(self.system.itemText(i),self.system.itemData(i)) for i in range(self.system.count())]:
            current=self.system.currentData();self.system.clear()
            for name,identifier in values:self.system.addItem(name,identifier)
            index=self.system.findData(current)
            if index>=0:self.system.setCurrentIndex(index)
        self.sync_controls()
        self.app.viewport.refresh_plane_preview()