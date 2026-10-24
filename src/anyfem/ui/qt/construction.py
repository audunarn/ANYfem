"""Working-copy construction and face sketches on the shared viewport."""
from dataclasses import replace,asdict
import json
import math
from PySide6.QtCore import QTimer, Qt, QEvent
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox,
    QLineEdit, QCheckBox, QPushButton, QLabel, QPlainTextEdit, QToolButton, QGridLayout, QListWidget)
from anygeometry import (GeometryError, SketchDefinition, SketchPlane, face_sketch_plane,
    plane_sketch_plane, reference_links_from_parameters)

from ... import commands
from ...geometry.construction import ConstructionTask, ConstructionMode
from ...sketcher import FaceSketchTask, SketchPorts
from ...geometry.snapping import geometry_snap_data
from ...model.coordinates import CoordinateSystem
from ...model.workplanes import Workplane
from ..numeric_input import parse_numeric_sequence
from .editors import apply_on_enter


class ConstructionPanel(QWidget):
    @property
    def support(self):return self.controller.session.support
    @support.setter
    def support(self,value):self.controller.configure(support=value)

    @property
    def editing_feature_id(self):return self.controller.session.editing_feature_id
    @editing_feature_id.setter
    def editing_feature_id(self,value):self.controller.configure(editing_feature_id=value)

    @property
    def editing_plane(self):return self.controller.session.editing_plane
    @editing_plane.setter
    def editing_plane(self,value):self.controller.configure(editing_plane=value)

    @property
    def last_sketch_feature_id(self):return self.controller.session.last_sketch_feature_id
    @last_sketch_feature_id.setter
    def last_sketch_feature_id(self,value):self.controller.configure(last_sketch_feature_id=value)

    def __init__(self, app):
        super().__init__();self.app=app;self.controller=app.sketcher;self.support=None;self.editing_feature_id=None;self.editing_plane=None
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
        for i,(key,label,tip) in enumerate((('select','Select','Click a point; Shift adds to selection. Drag a point to move it.'),('line','Line','Click start and end; existing endpoints are reused.'),('connected','Connected lines','Click points in order; Enter completes the chain.'),('arc','Arc','Click start, a point on the arc, and end.'),('circle','Circle','Click centre and a point on the circle.'),('spline','Spline','Click fit or control points; Enter completes the curve.'),('fillet','Rounded corner','Select two connected straight segments, then set radius.'),('dimension','Dimension','Select two points or one curve, then enter its dimension.'),('reference','Pick references','Click model vertices, edges or flat plates that meet the sketch plane to add them as fixed reference geometry.'))):
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
        button("View sketch plane",self.view_plane,edit_actions).setText("View plane")
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
        self.coordinates=QLineEdit();self.length_row("Point u, v",self.coordinates)
        self.coordinates.setPlaceholderText("For example: 1 m, 0.5 m — Enter adds the point")
        self.point_row=QWidget();point_actions=QHBoxLayout(self.point_row);point_actions.setContentsMargins(0,0,0,0);form.addRow(self.point_row)
        button("Add local point",self.add_point,point_actions);button("Remove last point",self.backspace,point_actions)
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
        self.reference_body,reference_layout=self.section(layout,"Reference geometry")
        self.reference_help=QLabel("References are fixed copies of existing geometry that meets the sketch plane. Select vertices or edges in the model, then use them. They never move, and can be used for Join points, Set distance and Fix point.")
        self.reference_help.setWordWrap(True);reference_layout.addWidget(self.reference_help)
        self.reference_list=QListWidget();self.reference_list.setMaximumHeight(90);reference_layout.addWidget(self.reference_list)
        button("Use selected geometry as references",self.import_references,reference_layout).setToolTip("Capture the selected model vertices and edges where they meet the sketch plane as fixed, read-only reference points.")
        button("Remove selected reference",self.remove_references,reference_layout).setToolTip("Select a reference point in the sketch first. Constraints using it are removed with it.")
        button("Refresh reference from selection",self.refresh_reference,reference_layout).setToolTip("Select one reference point in the sketch and one model vertex or edge, then capture the new geometry into that reference.")
        _,raw_layout=self.section(constraint_layout,"Advanced constraint records")
        self.constraints=QPlainTextEdit("[]");self.constraints.setMaximumHeight(90);raw_layout.addWidget(self.constraints)
        self.constraints.setToolTip("Constraint records use SI metres for dimensional values.")
        self.other_body,other_layout=self.section(layout,"Other construction tools")
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
        self.controller.bind(SketchPorts(begin_preview=self._begin_preview,end_preview=app.viewport.end_construction,
            changed=self._controller_changed,preview=app.viewport.request_sketch_preview,
            execute=app.run,can_edit=lambda:not app.session.read_only,accepted=self._accepted,
            focus=lambda kind:self.focus_dimension(self.radius if kind=='radius' else self.distance),finish_requested=self.apply,
            geometry=lambda:self.app.project.geometry))
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
        if not self.controller.active:return
        if key=='spline':key=self.spline_kind.currentData()
        self.controller.choose_tool(key,degree=int(self.spline_degree.text()),weights=tuple(parse_numeric_sequence(self.spline_weights.text(),float)) if self.spline_weights.text().strip() else ())
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
        if self.controller.active:return self.controller.enter()
        task=self.app.viewport.construction_task
        if task is not None and task.ready:self.apply()

    def escape(self):
        if self.controller.active:self.controller.escape()
        else:self.cancel()

    def close_loop(self):
        if self.controller.active:self.controller.close_loop()

    def local_undo(self,redo=False):
        if self.controller.active:self.controller.undo(redo)

    def remove_selected_constraint(self):
        if self.controller.active:self.controller.remove_constraint(self.constraint_list.currentRow())

    def set_radius(self):
        if self.controller.active:self.controller.enter(field="radius",value=self.length(self.radius.text()),diameter=self.diameter.isChecked())

    @staticmethod
    def section(layout,title):
        toggle=QPushButton("\u25B8  "+title);toggle.setCheckable(True);toggle.setFlat(True);layout.addWidget(toggle)
        toggle.setStyleSheet("QPushButton{text-align:left;font-weight:600;border:none;border-bottom:1px solid palette(mid);padding:4px 2px;}")
        toggle.toggled.connect(lambda on:toggle.setText(("\u25BE  " if on else "\u25B8  ")+title))
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
        self.buttons["View sketch plane"].setVisible(active)
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
        for key in ("Add local point","Remove last point","View sketch plane"):self.buttons[key].setEnabled(active)
        self.form.setRowVisible(self.coordinates,active);self.form.setRowVisible(self.point_row,active)
        for body in (self.plane_body,self.snap_body,self.other_body):
            body.section_toggle.setVisible(not active)
            if active:body.section_toggle.setChecked(False)
        self.constraint_body.section_toggle.setVisible(sketching)
        self.reference_body.section_toggle.setVisible(sketching)
        if not sketching:self.constraint_body.section_toggle.setChecked(False);self.reference_body.section_toggle.setChecked(False)
        for label in ("Use selected geometry as references","Remove selected reference","Refresh reference from selection"):self.buttons[label].setEnabled(sketching and not self.app.session.read_only)
        self.spline_box.setVisible(sketching and task.tool in ('spline','nurbs'))

    def start_sketch_workflow(self):
        if self.support_choice.currentData()=="face":self.sketch()
        else:self.sketch_on_plane()

    def cancel(self):
        if self.controller.active:self.controller.cancel()
        else:self.app.viewport.cancel_construction()
        self.sync_controls()

    def closed_changed(self,closed):
        if self.controller.active:self.controller.set_closed(closed)

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
        if isinstance(task,FaceSketchTask):return self.controller.begin(task,plane,systems)
        return self._begin_preview(task,plane,systems)

    def _controller_changed(self,task,snap):
        self.app.viewport.refresh_construction_overlay();self.updated(task,snap)

    def _accepted(self,feature):
        made=tuple(ref for key,ref in feature.outputs.items() if key.startswith('extrusion/face/'))
        self.app.selection.set_mode('face' if made else 'edge')
        self.app.selection.restore(made or tuple(dict.fromkeys(ref for key,ref in feature.outputs.items() if key.startswith(('profile/edge/','entity/')) and ref.kind=='edge')))

    def _begin_preview(self,task,plane,systems):
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
        self.import_references(quiet=True)  # model geometry selected before Start becomes fixed references

    def start_sketch(self,support,definition=None,feature_id=None,links=None):
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
        self.begin(FaceSketchTask(plane,workplane.snap_tolerance,definition,links=links),workplane,systems)
        # The support plate itself is the sketch plane, not a reference.
        if definition is None:self.import_references([s for s in self._selected_sources() if s!=support],quiet=True)

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
            self.begin(FaceSketchTask(plane,workplane.snap_tolerance,definition,links=self._saved_links(record)),workplane,systems)
            self.app.notebook.select("Construction");return True
        if record.kind!="geometry.sketch.extrude":return False
        refs=record.inputs.get("support_face",())
        if len(refs)!=1:raise ValueError("Sketch support is invalid")
        resolved=self.app.project.geometry.features.resolve(refs[0],self.app.project.geometry)
        if len(resolved)!=1 or resolved[0].kind!="face":raise ValueError("Sketch support is unresolved")
        self.start_sketch(resolved[0],SketchDefinition.from_parameters(record.parameters),record.feature_id,self._saved_links(record))
        self.app.notebook.select("Construction");return True

    @staticmethod
    def _saved_links(record):
        """Optional saved source links; the controller detaches any that no longer match."""
        return {item.reference_id:item for item in reference_links_from_parameters(record.parameters)}

    def _selected_sources(self):
        return [ref for ref in self.app.selection.ordered_items if ref.kind in ('vertex','edge','face')]

    def import_references(self,sources=None,*,quiet=False):
        """Capture selected vertices/edges as fixed references; each failure is reported, never approximated."""
        from ...sketch_references import describe_reference_error
        self.controller._task()
        sources=self._selected_sources() if sources is None else list(sources)
        if not sources:
            if quiet:return 0
            raise ValueError('Select model vertices, edges or flat plates that meet the sketch plane and try again')
        added=0;problems=[]
        for source in sources:
            try:self.controller.add_references([source]);added+=1
            except (GeometryError,ValueError) as error:problems.append(describe_reference_error(error))
        if problems and not added and not quiet:raise ValueError(problems[0])
        self.app.set_status(f"Added {added} fixed reference(s)"+(f"; {len(problems)} skipped: {problems[0]}" if problems else ""))
        return added

    def remove_references(self):
        task=self.controller._task()
        ids=[]
        for key in task.selected:
            item=task.reference_of(key)
            if item is not None and item.id not in ids:ids.append(item.id)
        if not ids:raise ValueError('Select a reference point in the sketch first')
        if any(identifier in task.axis_references for identifier in ids):raise ValueError('Global axis reference points cannot be removed')
        for identifier in ids:self.controller.remove_reference(identifier)

    def refresh_reference(self):
        """Explicit user action: capture the selected model geometry into the selected reference."""
        task=self.controller._task()
        ids=[item.id for key in task.selected for item in (task.reference_of(key),) if item is not None]
        sources=self._selected_sources()
        if len(set(ids))!=1 or len(sources)!=1:raise ValueError('Select one reference point in the sketch and one model vertex or edge')
        self.controller.relink_reference(ids[0],sources[0])

    def add_constraint(self,kind):
        task=self.controller._task()
        indices=tuple(value-1 for value in parse_numeric_sequence(self.pair.text(),int)) if not task.selected else None
        if kind=='distance':self.controller.enter(field='distance',value=self.length(self.distance.text()),indices=indices,extrusion=self.length(self.extrusion.text()) if task.ready else 0)
        else:self.controller.constraint(kind,indices=indices,extrusion=self.length(self.extrusion.text()) if task.ready else 0)

    def remove_constraint(self):
        self.controller.remove_constraint()

    def add_point(self):
        task=self.app.viewport.construction_task
        if task is None:raise ValueError('Start construction before adding a point')
        u,v=parse_numeric_sequence(self.coordinates.text(),self.length,units=True)
        frame=self.app.viewport._construction_workplane.resolve(self.app.viewport._construction_coordinate_systems)
        if self.controller.active:self.controller.enter(field='coordinates',value=frame.world_position((u,v)))
        else:task.add_plane_coordinates(u,v,frame);self.app.viewport.refresh_construction_overlay();self.updated(task,None)

    def backspace(self):
        if self.controller.active:self.controller.undo()
        else:
            task=self.app.viewport.construction_task
            if task is not None:task.backspace();self.app.viewport.refresh_construction_overlay();self.updated(task,None)

    def apply(self):
        if self.app.session.read_only:raise PermissionError('This project is read-only')
        if self.controller.active:
            self.controller.finish(name=self.sketch_name.text(),extrusion=self.length(self.extrusion.text()),closed=self.closed.isChecked(),constraint_records=json.loads(self.constraints.toPlainText()))
        else:
            result=self.app.viewport.finish_construction(self.app.run);made=result.edges or result.vertices
            if made:self.app.selection.set_mode('edge' if result.edges else 'vertex');self.app.selection.restore(made)
        self.summary.setText('Sketch finished. Choose a support and Start sketch to draw another profile.')
        self.sync_controls();self.app.set_status('Construction applied')

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
            self.reference_list.clear()
            for item in task.references:
                self.reference_list.addItem(f"{item.id} · {item.kind} · "+("Fixed (source linked)" if task.is_linked(item.id) else "Fixed (detached from source)"))
            if len(task.selected)==2:
                a,b=(task.reference_world(k) if k in task.reference_points else task.points[task.point_keys.index(k)] for k in task.selected)
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
            instructions={"select":"Click to select; Shift adds points or segments. Drag a point to move it.","line":"Click start and end. Existing points are reused.","connected":"Click connected points. Enter completes; Close loop closes the profile.","arc":"Click start, a point on the arc, and end.","circle":"Click centre and a point on the circle.","spline":"Click fit/control points; Enter completes the spline.","nurbs":"Click control points; Enter completes the NURBS.","dimension":"Select two points or a curve, then enter a dimension.","fillet":"Shift-select two connected segments, then set Radius.","reference":"Click model vertices or edges that meet the sketch plane; each becomes a fixed reference. Escape returns to Select."}
            self.summary.setText(instructions.get(task.tool,"")+" Escape returns to Select; Finish sketch saves.");return
        self.summary.setText(f"{len(task.points)} point(s) · {'Enter or Finish sketch to save' if task.ready else 'click points in the canvas'} · Backspace removes the last point; Escape cancels."+
                             (f" · {snap.kind.value} snap" if snap is not None and snap.kind is not None else ""))

    def refresh(self):
        # Source history may change without a new rendered model (for example,
        # deleting its last feature). Invalidate unsaved references on document
        # refresh as well, while retaining their fixed snapshot coordinates.
        if self.controller.active and self.controller.refresh_references():
            self._controller_changed(self.controller.task,None)
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
