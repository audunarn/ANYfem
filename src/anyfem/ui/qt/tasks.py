"""Mesh, analysis and results adapters for the shared application workflow."""
import json
import inspect
from dataclasses import replace, fields,asdict

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QComboBox,
    QLineEdit, QPushButton, QPlainTextEdit, QSpinBox, QCheckBox, QLabel,QColorDialog)

from ...application.workflow import ANALYSES, _solution_report
from ...post.fields import available_fields
from ...presentation.visualization import VisualizationStyle
from ...presentation.scene import RESULT_COLORMAPS
from ...presentation.live_progress import LiveProgressData, GRAPH_CHOICES
from ...presentation.result_display import converted_series
from .plot import HistoryPlot
from .forms import OptionForm
from .table import ResultTable


class MeshTask(QWidget):
    def __init__(self,app):
        super().__init__();self.app=app
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.size=QLineEdit(str(app.project.target_size or 0.25));form.addRow("Target size (m)",self.size)
        self.strategy=QComboBox();self.strategy.addItems(["quad_first","auto","mapped","native"]);form.addRow("Strategy",self.strategy)
        from anymesher.structured import StructurePreference
        self.preference=QComboBox();self.preference.addItems([item.value for item in StructurePreference]);form.addRow("Structure preference",self.preference)
        self.options=QPlainTextEdit("{}")
        form.addRow("Additional mesh options",self.options)
        from ...mesh_controls import MeshControls,StructuredMeshControls
        self.native_controls=OptionForm.record(MeshControls,MeshControls.display_values(app.project.native_mesh_settings))
        self.structured_controls=OptionForm.record(StructuredMeshControls)
        from anymesher import MeshAutomationOptions,MeshQualityPolicy
        from anymesher.quad.options import QuadMeshingOptions
        from ...quad_first import sg1_quad_options
        self.quality_controls=OptionForm.record(MeshQualityPolicy)
        self.quad_controls=OptionForm.record(QuadMeshingOptions,asdict(sg1_quad_options()))
        self.automation_controls=OptionForm.record(MeshAutomationOptions)
        self.backend=QComboBox();self.backend.addItems(["auto","python","native"]);self.backend.setCurrentText(app.project.native_triangulation_backend);form.addRow("Triangulation backend",self.backend)
        self.order=QComboBox();self.order.addItems(["linear","quadratic"]);self.order.setCurrentText(app.project.element_order);form.addRow("Element order",self.order)
        self.layout_policy=QComboBox();self.layout_policy.addItems(["existing","adaptive"]);form.addRow("Quad-first layout policy",self.layout_policy)
        layout.addWidget(QLabel("Native meshing controls"));layout.addWidget(self.native_controls)
        layout.addWidget(QLabel("Structured layout controls"));layout.addWidget(self.structured_controls)
        for label,widget in [("Element quality policy",self.quality_controls),("Quad-first controls",self.quad_controls),("Mesh recovery policy",self.automation_controls)]:layout.addWidget(QLabel(label));layout.addWidget(widget)
        self.divisions=QSpinBox();self.divisions.setRange(1,100000);self.divisions.setValue(4);form.addRow("Selected line divisions",self.divisions)
        self.refine_size=QLineEdit("0.1");self.refine_radius=QLineEdit("0.5")
        form.addRow("Refinement size (m)",self.refine_size);form.addRow("Refinement radius (m)",self.refine_radius)
        for label,callback in [("Pin selected lines",self.pin),("Clear seeding pins",self.clear_pins),("Refine around selection",self.refine),("Clear refinements",self.clear_refinements),("Discard layout preview",app.clear_mesh_layout_preview),("Copy mesh diagnosis",self.copy_diagnosis),("Open standalone ANYmesher",self.open_mesher)]:
            button=QPushButton(label);button.clicked.connect(app.guarded(callback));layout.addWidget(button)
        self.generate=QPushButton("Generate mesh");self.generate.clicked.connect(app.guarded(self.start));layout.addWidget(self.generate)
        cancel=QPushButton("Cancel mesh");cancel.clicked.connect(app.guarded(app.cancel_mesh));layout.addWidget(cancel)
        preview=QPushButton("Preview layout");preview.clicked.connect(app.guarded(self.preview));layout.addWidget(preview)
        commit=QPushButton("Commit layout preview");commit.clicked.connect(app.guarded(app.commit_mesh_layout_preview));layout.addWidget(commit)
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary);layout.addStretch()

    def open_mesher(self):
        """Launch the owner's standalone tool in its own process."""
        import subprocess,sys
        subprocess.Popen([sys.executable,"-m","anymesher.gui"])
        self.app.set_status("Opened standalone ANYmesher; save its neutral mesh from that window")

    def settings(self):
        options=json.loads(self.options.toPlainText())
        if not isinstance(options,dict):
            raise ValueError("Mesh options must be an object")
        from ...mesh_controls import MeshControls,StructuredMeshControls
        for name,record in [("mesh_controls",MeshControls),("structured_controls",StructuredMeshControls)]:
            raw=options.get(name)
            options[name]=record(**raw) if isinstance(raw,dict) else raw or record(**(self.native_controls if name=="mesh_controls" else self.structured_controls).values())
        from anymesher import MeshAutomationOptions,MeshQualityPolicy
        options.setdefault("native_backend",self.backend.currentText())
        raw=options.get("automation")
        options["automation"]=MeshAutomationOptions(**raw) if isinstance(raw,dict) else raw or MeshAutomationOptions(**self.automation_controls.values())
        if self.strategy.currentText()=="auto":options.setdefault("quality_policy",MeshQualityPolicy(**self.quality_controls.values()).to_dict())
        if self.strategy.currentText()=="quad_first":
            from anymesher.quad.options import QuadMeshingOptions
            values=self.quad_controls.values()
            if values["line_search"]=="off":values["max_local_optimizations"]=0
            options.setdefault("quad_options",QuadMeshingOptions(**values));options.setdefault("layout_policy",self.layout_policy.currentText())
        return dict(target_size=float(self.size.text()),strategy=self.strategy.currentText(),structure_preference=self.preference.currentText(),**options)

    def start(self):
        options=self.settings()
        if self.order.currentText()!=self.app.project.element_order:
            from ...commands import SetElementOrder
            self.app.run(SetElementOrder(self.order.currentText()))
        self.app.generate_mesh_async(**options)

    def preview(self):
        options=self.settings()
        self.app.preview_mesh_layout(options["target_size"],structure_preference=options["structure_preference"],structured_controls=options["structured_controls"],quality_policy=options.get("quality_policy"))

    def pin(self):
        refs=list(self.app.selection.items)
        if not refs or any(ref.kind!="edge" for ref in refs):raise ValueError("Select geometry lines to pin")
        self.app.seeding_overrides.update({ref.id:self.divisions.value() for ref in refs})
        self.app.set_status(f"Pinned {len(refs)} lines to {self.divisions.value()} divisions")

    def clear_pins(self):
        self.app.seeding_overrides.clear();self.app.set_status("Cleared seeding pins")

    def refine(self):
        from ...commands import AddRefinement
        from ...mesh.refinement import refine_around
        refs=list(self.app.selection.items)
        if not refs:raise ValueError("Select geometry to refine")
        self.app.run_many(AddRefinement(refine_around(ref,float(self.refine_size.text()),float(self.refine_radius.text()))) for ref in refs)

    def clear_refinements(self):
        from ...commands import ClearRefinements
        self.app.run(ClearRefinements())

    def copy_diagnosis(self):
        self.app.clipboard.copy_text(self.app.diagnostic_report())

    def reset_mesh_drafts(self):
        from ...mesh_controls import MeshControls,StructuredMeshControls
        self.size.setText(str(self.app.project.target_size or 0.25))
        settings=self.app.project.native_mesh_settings
        self.native_controls.set_values(MeshControls.display_values(settings))
        self.structured_controls.set_values(StructuredMeshControls.display_values(settings))
        self.strategy.setCurrentText(self.app._project_mesh_strategy(self.app.project,None))
        self.preference.setCurrentText(self.app._project_structure_preference(self.app.project,None))
        self.options.setPlainText("{}")
        from anymesher import MeshAutomationOptions,MeshQualityPolicy
        from ...quad_first import effective_quad_options
        parameters={} if settings is None else dict(settings.parameters)
        self.quality_controls.set_values(MeshQualityPolicy.create({key.removeprefix("mesh_quality_"):value for key,value in parameters.items() if key.startswith("mesh_quality_")}).to_dict())
        self.quad_controls.set_values(asdict(effective_quad_options(parameters.get("quad_options"))))
        self.automation_controls.set_values(asdict(MeshAutomationOptions()))
        self.backend.setCurrentText(self.app.project.native_triangulation_backend)
        self.order.setCurrentText(self.app.project.element_order)
        self.layout_policy.setCurrentText(parameters.get("layout_policy","existing"))

    def refresh(self):
        self.generate.setEnabled(not self.app.mesh_job_running and not self.app.session.read_only)
        mesh=self.app.mesh
        self.summary.setText("No active mesh" if mesh is None else f"{mesh.num_nodes} nodes · {mesh.num_elements} elements")


class SolveTask(QWidget):
    def __init__(self,app):
        super().__init__();self.app=app
        layout=QVBoxLayout(self)
        form=QFormLayout();layout.addLayout(form)
        self.analysis=QComboBox();self.analysis.addItems(list(ANALYSES));form.addRow("Analysis",self.analysis)
        self.case=QComboBox();form.addRow("Load case or combination",self.case)
        self.options=QPlainTextEdit("{}");form.addRow("Advanced analysis options",self.options)
        self.controls_layout=QVBoxLayout();layout.addLayout(self.controls_layout)
        self.controls=None;self.record_controls={}
        self.analysis.currentTextChanged.connect(self.rebuild_controls);self.rebuild_controls()
        from anysolver import ResourceConfig
        self.use_resources=QCheckBox("Use explicit solver resource policy");layout.addWidget(self.use_resources)
        self.resources=OptionForm.record(ResourceConfig,exclude=("metadata",));layout.addWidget(self.resources)
        self.submit=QPushButton("Solve");self.submit.clicked.connect(app.guarded(self.start));layout.addWidget(self.submit)
        cancel=QPushButton("Cancel solve");cancel.clicked.connect(app.cancel_solve);layout.addWidget(cancel)
        self.progress=QLabel();self.progress.setWordWrap(True);layout.addWidget(self.progress)
        self.material_response=QLabel();self.material_response.setWordWrap(True);layout.addWidget(self.material_response)
        self.analysis.currentTextChanged.connect(self.refresh)
        self.transcript=QPlainTextEdit();self.transcript.setReadOnly(True);layout.addWidget(self.transcript)
        self.progress_data=LiveProgressData()
        self.graph=QComboBox();self.graph.addItems(list(GRAPH_CHOICES));layout.addWidget(self.graph)
        self.plot=HistoryPlot();layout.addWidget(self.plot)
        self.graph.currentTextChanged.connect(self.update_graph)

    def start(self):
        self.app.solve(self.analysis.currentText(),**self.settings())

    def rebuild_controls(self,*_):
        while self.controls_layout.count():
            item=self.controls_layout.takeAt(0)
            if item.widget():item.widget().deleteLater()
        name=self.analysis.currentText();function=ANALYSES[name]
        excluded={"project","built","mesh","target_size","overrides","load_case","progress","cancellation_token","imperfection","fracture","resources","control","collision","contact","damage","plastic_damage","config"}
        parameters=[(key,p.annotation,p.default) for key,p in inspect.signature(function).parameters.items() if key not in excluded and p.kind not in (p.VAR_KEYWORD,p.VAR_POSITIONAL)]
        if name in {"Nonlinear static","Arc length","Capacity"}:
            from anysolver import solve_static_nonlinear,solve_static_arc_length
            signature=inspect.signature(solve_static_arc_length if name=="Arc length" else solve_static_nonlinear)
            wanted={"max_iterations","tolerance","num_layers","min_step_fraction","arc_tolerance","kinematics","corotational_tangent"}
            if name=="Arc length":wanted.discard("min_step_fraction")
            if name=="Capacity":wanted={"max_iterations","tolerance","num_layers"}
            parameters.extend(("nonlinear_"+key if name=="Capacity" else key,p.annotation,p.default) for key,p in signature.parameters.items() if key in wanted)
        elif name=="Transient":
            parameters.extend([(key,typ,default) for key,typ,default in [("beta","float",0.25),("gamma","float",0.5),("hht_alpha","float",0.0),("include_stress_history","bool",False)]])
        self.controls=OptionForm(parameters);self.controls_layout.addWidget(self.controls);self.record_controls={}
        records={}
        if name=="Arc length":
            from anysolver import ArcLengthControl
            records["control"]=ArcLengthControl
        if name=="Impact":
            from ...model.collision import Collision
            from anysolver import SphereContactConfig
            records.update(collision=Collision,contact=SphereContactConfig)
        for key,record in records.items():
            self.controls_layout.addWidget(QLabel(key.capitalize()))
            widget=OptionForm.record(record);self.record_controls[key]=(record,widget);self.controls_layout.addWidget(widget)

    def settings(self):
        options=json.loads(self.options.toPlainText())
        if not isinstance(options,dict):raise ValueError("Analysis options must be an object")
        values=self.controls.values();values.update(options);options=values
        for key,(record,widget) in self.record_controls.items():
            raw=options.get(key)
            options[key]=record(**raw) if isinstance(raw,dict) else raw or record(**widget.values())
        if self.analysis.currentText() not in {"Modal","Impact","Batch linear static"} and "combination" not in options:
            choice=self.case.currentText() or "default"
            if choice.startswith("combination: "):options.setdefault("combination",choice.split(": ",1)[1])
            else:options.setdefault("load_case",choice)
        if self.analysis.currentText()=="Batch linear static":options.setdefault("load_cases",list(self.app.project.load_cases))
        if self.use_resources.isChecked():
            from anysolver import ResourceConfig
            options["resources" if self.analysis.currentText() in {"Nonlinear static","Capacity"} else "resource_config"]=ResourceConfig(**self.resources.values())
        return options

    def refresh(self):
        from ...presentation.engineering_summary import material_response
        text,color=material_response(self.app.project,self.analysis.currentText())
        self.material_response.setText(text);self.material_response.setStyleSheet(f"color: {color}")
        current=self.case.currentText();choices=list(self.app.project.load_cases)+["combination: "+name for name in self.app.project.combinations]
        if [self.case.itemText(i) for i in range(self.case.count())]!=choices:
            self.case.clear();self.case.addItems(choices)
            if current in choices:self.case.setCurrentText(current)
        self.submit.setEnabled(self.app.mesh is not None and not self.app.session.read_only)

    def begin_job(self,name,job_id,report):
        self.progress_data.clear()
        self.transcript.setPlainText(f"{name}\nJob {job_id}\n{report}")

    def append_progress(self,text,payload=None):
        self.transcript.appendPlainText(text)
        if self.progress_data.ingest(text,payload):self.update_graph()

    def update_graph(self,*_):
        series=self.progress_data.series(self.graph.currentText())
        self.plot.show_series([] if series is None else [series])

    def append_report(self,text):
        self.transcript.appendPlainText(text)

    def show_progress(self,text):
        self.progress.setText(text)

    def reset(self):
        self.progress.clear();self.transcript.clear()
        self.progress_data.clear();self.plot.show_series([])


class ResultsTask(QWidget):
    def __init__(self,app):
        super().__init__();self.app=app
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.field=QComboBox();self.field.addItems(available_fields());self.field.setCurrentText("magnitude");form.addRow("Field",self.field)
        self.scale=QLineEdit("1");form.addRow("Deformation scale",self.scale)
        self.frame=QSpinBox();form.addRow("Frame",self.frame)
        self.units=QComboBox();self.units.addItems(["SI (m / Pa)","Engineering (mm / MPa)"]);form.addRow("Display units",self.units)
        self.colors=QComboBox();self.colors.addItems(list(RESULT_COLORMAPS));form.addRow("Color map",self.colors)
        self.minimum=QLineEdit();self.maximum=QLineEdit()
        form.addRow("Color minimum (blank = automatic)",self.minimum);form.addRow("Color maximum (blank = automatic)",self.maximum)
        self.component=QLineEdit();self.component.setPlaceholderText("For vector artifacts: component name, or blank for magnitude")
        form.addRow("Artifact component",self.component)
        self.envelope=QCheckBox("Envelope of retained frames");layout.addWidget(self.envelope)
        show=QPushButton("Show results");show.clicked.connect(app.guarded(self.show_results));layout.addWidget(show)
        play=QPushButton("Play retained frames");play.clicked.connect(app.guarded(self.play));layout.addWidget(play)
        stop=QPushButton("Stop playback");stop.clicked.connect(lambda:app.viewport.canvas.stop_animation());layout.addWidget(stop)
        self.report=QPlainTextEdit();self.report.setReadOnly(True);layout.addWidget(self.report)
        self.outcome=QLabel();self.outcome.setWordWrap(True);layout.addWidget(self.outcome)
        self.frame_details=QLabel();self.frame_details.setWordWrap(True);layout.addWidget(self.frame_details)
        self.submitted_inputs=QPlainTextEdit();self.submitted_inputs.setReadOnly(True)
        layout.addWidget(QLabel("Submitted inputs"));layout.addWidget(self.submitted_inputs)
        self.quantities=QComboBox();layout.addWidget(self.quantities)
        self.table=ResultTable();layout.addWidget(self.table)
        inspect_quantity=QPushButton("Inspect quantity / table");inspect_quantity.clicked.connect(app.guarded(self.inspect_quantity));layout.addWidget(inspect_quantity)
        self.plot=HistoryPlot();layout.addWidget(self.plot)
        self._gif_capture=None
        self._preview_owner=None
        for label,callback in [("Probe selection",self._probe),("Sample along selected line",self.along_line),("Plot history",self.history),("Plot at selected point",self.history_at_selection),("Export field CSV",self.export_csv),("Export report",self.export_report),("Export GIF",self.export_gif)]:
            button=QPushButton(label);button.clicked.connect(app.guarded(callback));layout.addWidget(button)
        self.frame.valueChanged.connect(self._frame_changed)

    def activate_job(self,job_id):
        solution=self.app.solutions.get(job_id)
        if solution is None and job_id not in self.app.result_datasets:raise ValueError("This job has no available retained result")
        self.reset();self.app.active_job_id=job_id;self.app.solution=solution;self.app.shape_index=0
        self.refresh();self.app.notebook.select("Results");self.show_results()

    def _frame_changed(self,value):
        self.app.shape_index=value
        self.refresh_details()

    def refresh(self):
        self.ensure_compatible_field()
        solution=self.app.solution
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        owner=(self.app.active_job_id,id(solution) if solution is not None else id(dataset) if dataset is not None else None)
        if owner!=self._preview_owner:
            self.reset();self._preview_owner=owner
        count=len(getattr(solution,"shapes",()) or (solution,)) if solution is not None else len(dataset.frames) if dataset is not None else 0
        self.frame.setMaximum(max(0,count-1))
        self.frame.setValue(self.app.shape_index)
        if solution is not None:self.report.setPlainText(_solution_report(solution))
        elif dataset is not None:
            job=self.app.project.jobs.get(self.app.active_job_id)
            state="stale" if job is not None and self.app._job_is_stale(job) else "current"
            self.report.setPlainText(f"Retained result ({state})\n{len(dataset.frames)} frame(s)\nSelect a quantity to inspect its saved fields, histories or tables.")
        elif dataset is None:
            self.report.clear();self.table.show_rows([],[]);self.plot.show_series([])
        self.refresh_details()
        choices=[]
        if dataset is not None:
            choices.extend((f"Field: {key}",("field",key)) for key in dataset.field_keys)
            choices.extend((f"History: {key}",("history",key)) for key in dataset.history_keys)
            choices.extend((f"Table: {key}",("table",key)) for key in dataset.table_keys)
        elif solution is not None:
            from ...post.solver_data import available_solution_quantities
            choices.extend((item.descriptor.label,("live",item.descriptor.quantity_id)) for item in available_solution_quantities(solution))
        if choices!=[(self.quantities.itemText(i),self.quantities.itemData(i)) for i in range(self.quantities.count())]:
            prior=self.quantities.currentText();self.quantities.clear()
            for text,data in choices:self.quantities.addItem(text,data)
            self.quantities.setCurrentText(prior)

    def refresh_details(self):
        from ...presentation.engineering_summary import outcome_text,constitutive_summary
        solution=self.app.solution;dataset=self.app.result_datasets.get(self.app.active_job_id)
        submitted=self.app.submitted_input_reports.get(self.app.active_job_id)
        if submitted is None and dataset is not None:
            provenance=dataset.metadata("provenance")
            submitted=provenance.get("submitted_inputs_text")
            if submitted is None and provenance.get("submitted_inputs") is not None:
                submitted=json.dumps(provenance["submitted_inputs"],indent=2,sort_keys=True)
        self.submitted_inputs.setPlainText(submitted or "Submitted input text is unavailable; retained artifact hashes identify its provenance.")
        if solution is None:
            self.outcome.setText("Persisted result" if dataset is not None else "Run an analysis to see its outcome.")
            self.frame_details.setText(f"Persisted frame {self.app.shape_index+1} of {len(dataset.frames)}" if dataset is not None else "")
            return
        text,color=outcome_text(solution,submitted);self.outcome.setText(text);self.outcome.setStyleSheet(f"color: {color}")
        shapes=getattr(solution,"shapes",())
        shape=self.app.current_shape();pieces=[]
        if shapes:
            pieces.append(f"Shape {self.app.shape_index+1} of {len(shapes)}; value {float(shape.value):.5g}")
            step=getattr(shape,"step",None)
            if step is not None:pieces.append(f"{getattr(step,'iterations',0)} iterations; residual {getattr(step,'residual_norm',0):.3g}; |u| {getattr(step,'displacement_norm',0):.5g} m; PEEQ {getattr(step,'max_equivalent_plastic_strain',0):.5g}")
        else:pieces.append("Final state only; save converged increment snapshots to animate a nonlinear path.")
        if shape is not None and getattr(shape.built,"fe_model",None) is not None:pieces.append(constitutive_summary(shape))
        self.frame_details.setText("\n".join(pieces))

    def inspect_quantity(self):
        import numpy as np
        kind,key=self.quantities.currentData() or (None,None)
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        if kind=="history":
            from ...post.history import Series
            x,y=dataset.history(key,rows=slice(0,2000))
            self.plot.show_series([Series(key,np.asarray(x),np.asarray(y))]);return
        if kind=="field":
            stored=dataset.field(key);descriptor=stored.descriptor
            values=stored.read(self.frame.value())
            self.report.setPlainText(f"{descriptor.label}\nLocation: {descriptor.location}\nUnits: {descriptor.unit}\nComponents: {', '.join(descriptor.components)}")
        elif kind=="table":values=dataset.table(key,rows=slice(0,2000))
        elif kind=="live":
            from ...post.solver_data import resolve_solution_quantity
            values=resolve_solution_quantity(self.app.solution,key).data
        else:raise ValueError("Select an available quantity")
        if isinstance(values,(dict,list)) and (isinstance(values,dict) or values and isinstance(values[0],dict)):
            self.report.setPlainText(json.dumps(values,indent=2,default=str));return
        values=np.asarray(values)
        if values.ndim==0:values=values.reshape(1,1)
        elif values.ndim==1:values=values.reshape(-1,1)
        else:values=values.reshape(values.shape[0],-1)
        self.table.show_rows([f"Column {i+1}" for i in range(values.shape[1])],values[:2000].tolist())
        self.app.set_status(f"{key}: previewing {min(len(values),2000)} rows")

    def guarded(self,callback):return self.app.guarded(callback)

    def _probe(self):
        from ...post.extract import probe
        shape=self.app.current_shape()
        if shape is None:raise ValueError("Select a live result before probing")
        refs=self.app.selection.items
        if not refs:raise ValueError("Select an entity to probe")
        engineering=self.display_units()=="Engineering (mm / MPa)"
        self.report.setPlainText("\n\n".join(probe(shape,ref).text(length_scale=1000 if engineering else 1,length_unit="mm" if engineering else "m",stress_scale=1e-6 if engineering else 1,stress_unit="MPa" if engineering else "Pa") for ref in refs))

    def history(self):
        from ...post.history import history_series
        if self.app.solution is None:raise ValueError("A live solution is required for history")
        curves=history_series(self.app.solution)
        self.plot.show_series(converted_series(curves,self.display_units()))

    def history_at_selection(self):
        from ...post.history import history_series
        refs=list(self.app.selection.items)
        if len(refs)!=1 or refs[0].kind!="vertex":raise ValueError("Select one geometry point")
        mesh=self.app.retained_result_mesh(self.app.active_job_id) if self.app.active_job_id is not None else None
        node=mesh.node_of_vertex.get(refs[0].id) if mesh is not None else None
        if node is None or self.app.solution is None:raise ValueError("Select a point with a node in a live result")
        curves=history_series(self.app.solution,probe=node,component=self.field_name() if self.field_name() in ("ux","uy","uz","rx","ry","rz") else "uz")
        self.plot.show_series(converted_series(curves,self.display_units()))

    def along_line(self):
        from ...post.extract import along_line
        from ...presentation.result_display import unit_transform
        refs=list(self.app.selection.items);shape=self.app.current_shape()
        if shape is None or len(refs)!=1 or refs[0].kind!="edge":raise ValueError("Select one line in a live result")
        result=along_line(shape,refs[0],self.field_name())
        xs,xunit=unit_transform("m",self.display_units());ys,yunit=unit_transform(result.unit,self.display_units())
        self.report.setPlainText(f"Distance [{xunit}], value [{yunit}]\n"+"\n".join(f"{x*xs:.6g}, {y*ys:.6g}" for x,y in zip(result.distances,result.values)))

    def export_report(self):
        from ...post.report import result_report_context,write_result_report,write_report
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        if dataset is None and self.app.solution is None:raise ValueError("Select a result to report")
        path=self.app.dialogs.save_file(filetypes=[("Markdown","*.md"),("HTML","*.html")],defaultextension=".md")
        if not path:return
        if dataset is None:
            if str(path).lower().endswith((".html",".htm")):raise ValueError("Save the project to retain the artifact before exporting HTML")
            written=write_report(self.app.solution,path)
        else:
            job=self.app.project.jobs.get(self.app.active_job_id)
            context=result_report_context(dataset,project=self.app.project,job=job,current_document_hash=self.app.session.revision.document_hash,stale=self.app._job_is_stale(job) if job else None)
            written=write_result_report(dataset,path,context=context)
        self.app.set_status(f"Report exported to {written}")

    def export_gif(self):
        if self._gif_capture is not None:raise ValueError("GIF capture is already running")
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        count=len(getattr(self.app.solution,"shapes",()) or (self.app.solution,)) if self.app.solution is not None else len(dataset.frames) if dataset is not None else 0
        if not count:raise ValueError("Select a result to export")
        path=self.app.dialogs.save_file(filetypes=[("GIF animation","*.gif")],defaultextension=".gif")
        if not path:return
        self._gif_capture=dict(session=self.app.session,job=self.app.active_job_id,path=path,count=count,index=0,original=self.app.shape_index,images=[])
        self.app.scheduler.call_later(0,self._capture_gif_frame)

    def _capture_gif_frame(self):
        state=self._gif_capture
        if state is None or self.app._closing:return
        if state["session"] is not self.app.session or state["job"]!=self.app.active_job_id:
            self._gif_capture=None;return
        try:
            if state["index"]<state["count"]:
                self.frame.setValue(state["index"]);self.show_results()
                state["images"].append(self.app.viewport.canvas.capture_image());state["index"]+=1
                self.app.scheduler.call_later(1,self._capture_gif_frame);return
            self.frame.setValue(state["original"]);self.show_results();self._gif_capture=None
            from ...presentation.result_export import save_gif
            future=self.app._artifact_executor.submit(save_gif,state["images"],state["path"])
            def poll():
                if self.app._closing:return
                if not future.done():self.app.scheduler.call_later(50,poll);return
                self.app.guarded(lambda:self.app.set_status(f"GIF exported to {future.result()}"))()
            self.app.scheduler.call_later(50,poll)
        except Exception:
            self._gif_capture=None;self.frame.setValue(state["original"])
            raise

    def reset(self):
        self._gif_capture=None
        self._preview_owner=None
        self.app.viewport.canvas.stop_animation()
        self.report.clear();self.table.show_rows([],[]);self.plot.show_series([])

    def export_csv(self):
        from ...post.report import field_to_csv,write_csv
        from ...presentation.result_export import lazy_field_to_csv
        path=self.app.dialogs.save_file(filetypes=[("CSV","*.csv")],defaultextension=".csv")
        if not path:return
        shape=self.app.current_shape()
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        if shape is not None:text=field_to_csv(shape,self.field_name())
        elif dataset is not None:text=lazy_field_to_csv(dataset,self.field_name(),frame=self.app.shape_index)
        else:raise ValueError("Select a result to export")
        write_csv(text,path);self.app.set_status(f"Field exported to {path}")

    def ensure_compatible_field(self):
        solution=self.app.solution
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        if solution is None and dataset is not None:choices=list(dataset.field_keys)
        else:
            fn=getattr(solution,"available_fields",None)
            choices=list(fn() if callable(fn) else available_fields())
        if choices and [self.field.itemText(i) for i in range(self.field.count())]!=choices:
            current=self.field.currentText();self.field.clear();self.field.addItems(choices)
            self.field.setCurrentText(current if current in choices else "magnitude" if "magnitude" in choices else choices[0])
        return self.field.currentText()

    def field_name(self):return self.field.currentText()
    def scale_value(self,shape):return float(self.scale.text())
    def colour_limits(self):
        if not self.minimum.text().strip() and not self.maximum.text().strip():return None
        low,high=float(self.minimum.text()),float(self.maximum.text())
        import math
        if not math.isfinite(low) or not math.isfinite(high) or low>=high:raise ValueError("Color limits need finite minimum < maximum")
        return low,high
    def colormap(self):return RESULT_COLORMAPS[self.colors.currentText()]
    def display_units(self):return self.units.currentText()
    def show_result_nodes(self):return self.app.viewport.visualization.show_result_nodes
    def show_result_supports(self):return self.app.viewport.visualization.show_result_supports
    def show_result_loads(self):return self.app.viewport.visualization.show_result_loads
    def show_result_masses(self):return self.app.viewport.visualization.show_result_masses
    def show_imperfect_reference(self):return self.app.viewport.visualization.show_imperfect_reference

    def field_values(self):
        if self.envelope.isChecked() and self.app.solution is not None:
            from ...post.extract import envelope
            return envelope(self.app.solution,self.field_name()).field
        return None

    def show_results(self):
        if self.app.solution is None:
            self.app.show_persisted_result(self.field_name(),frame=self.frame.value(),scale=float(self.scale.text()),component=self.component.text().strip() or None,limits=self.colour_limits())
        else:self.app.show_results()

    def play(self):
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        count=len(getattr(self.app.solution,"shapes",()) or (self.app.solution,)) if self.app.solution is not None else len(dataset.frames) if dataset is not None else 0
        if not count:raise ValueError("Select a result before playback")
        canvas=self.app.viewport.canvas
        canvas.begin_animation_cache()
        original=self.app.shape_index
        try:
            for index in range(count):
                self.app.shape_index=index;self.frame.setValue(index)
                self.show_results()
                canvas.capture_animation_frame()
        finally:
            self.app.shape_index=original;self.frame.setValue(original);self.show_results()
        canvas.play_animation()


class VisualizationTask(QWidget):
    def __init__(self,app):
        super().__init__();self.app=app
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.render=QComboBox();self.render.addItems(["Shaded with edges","Shaded","Wireframe"]);form.addRow("Render mode",self.render)
        self.opacity=QLineEdit("1");form.addRow("Surface opacity",self.opacity)
        self.appearance={}
        from ...presentation.visualization import GEOMETRY_DETAILS
        for field in fields(VisualizationStyle):
            if field.name in {"render_mode","surface_opacity"}:continue
            value=getattr(app.viewport.visualization,field.name)
            if isinstance(value,bool):
                widget=QCheckBox();widget.setChecked(value)
            elif field.name in {"geometry_detail","result_colormap"}:
                widget=QComboBox();widget.addItems(GEOMETRY_DETAILS if field.name=="geometry_detail" else list(RESULT_COLORMAPS));widget.setCurrentText(value)
            else:widget=QLineEdit(str(value))
            self.appearance[field.name]=widget;form.addRow(field.name.replace("_"," ").capitalize(),widget)
            if field.name in {"background","edge_color"}:
                button=QPushButton("Choose colour");button.clicked.connect(lambda checked=False,widget=widget:self.choose_colour(widget));form.addRow(button)
        self.plane=QLineEdit("1, 0, 0");form.addRow("Section plane normal",self.plane)
        self.offset=QLineEdit("0");form.addRow("Section offset (m)",self.offset)
        apply=QPushButton("Apply appearance");apply.clicked.connect(app.guarded(self.apply));layout.addWidget(apply)
        reset=QPushButton("Reset appearance");reset.clicked.connect(app.guarded(self.reset));layout.addWidget(reset)
        sync=QPushButton("Load viewport appearance");sync.clicked.connect(self.sync_from_viewport);layout.addWidget(sync)
        section=QPushButton("Enable section plane");section.clicked.connect(app.guarded(self.section));layout.addWidget(section)
        clear=QPushButton("Clear section plane");clear.clicked.connect(app.viewport.clear_section_plane);layout.addWidget(clear)
        capture=QPushButton("Save viewport image");capture.clicked.connect(app.guarded(self.capture));layout.addWidget(capture);layout.addStretch()

    def apply(self):
        values={}
        for name,widget in self.appearance.items():
            old=getattr(self.app.viewport.visualization,name)
            values[name]=widget.isChecked() if isinstance(widget,QCheckBox) else widget.currentText() if isinstance(widget,QComboBox) else int(widget.text()) if isinstance(old,int) else widget.text()
        style=replace(self.app.viewport.visualization,render_mode=self.render.currentText(),surface_opacity=float(self.opacity.text()),**values)
        self.app.viewport.set_visualization(style)

    def choose_colour(self,widget):
        colour=QColorDialog.getColor(QColor(widget.text()),self,"Viewport colour")
        if colour.isValid():widget.setText(colour.name())

    def reset(self):
        self.app.viewport.set_visualization(VisualizationStyle());self.sync_from_viewport()

    def sync_from_viewport(self):
        style=self.app.viewport.visualization
        self.render.setCurrentText(style.render_mode);self.opacity.setText(str(style.surface_opacity))
        for name,widget in self.appearance.items():
            value=getattr(style,name)
            if isinstance(widget,QCheckBox):widget.setChecked(value)
            elif isinstance(widget,QComboBox):widget.setCurrentText(value)
            else:widget.setText(str(value))

    def section(self):
        self.app.viewport.set_section_plane(normal=tuple(float(x) for x in self.plane.text().split(",")),offset=float(self.offset.text()))

    def capture(self):
        path=self.app.dialogs.save_file(filetypes=[("PNG image","*.png")],defaultextension=".png")
        if path:self.app.viewport.capture_png(path)

    def refresh(self):pass
