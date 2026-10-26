"""Mesh, analysis and results adapters for the shared application workflow."""
import json
import inspect
import math
from .theme import overlay
from dataclasses import replace, fields,asdict

from PySide6.QtCore import Qt, QSignalBlocker, QTimer
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox,
    QLineEdit, QPushButton, QPlainTextEdit, QSpinBox, QCheckBox, QLabel,QColorDialog,
    QListWidget, QListWidgetItem, QAbstractItemView, QSlider, QProgressBar)

from ...application.workflow import ANALYSES, _solution_report
from ...post.fields import available_fields
from ...presentation.visualization import VisualizationStyle
from ...presentation.scene import RESULT_COLORMAPS
from ...presentation.live_progress import LiveProgressData, GRAPH_CHOICES
from ...presentation.result_display import converted_series
from ..numeric_input import parse_numeric_sequence
from .plot import HistoryPlot
from .forms import OptionForm
from .table import ResultTable


class MeshTask(QWidget):
    def __init__(self,app):
        super().__init__();self.app=app
        from .widgets import Collapsible,section_label
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.size=QLineEdit(str(app.project.target_size or 0.25));form.addRow("Target size (m)",self.size)
        self.size.setToolTip("Typical element edge length in metres.")
        self.strategy=QComboBox();self.strategy.addItems(["quad_first","auto","mapped","native","SmartMapped"]);form.addRow("Strategy",self.strategy)
        # Visible labels keep the historic tokens; SmartMapped is shown exactly
        # as "SmartMapped" and maps to the persisted token "smart_mapped".
        for index,token in enumerate(("quad_first","auto","mapped","native","smart_mapped")):
            self.strategy.setItemData(index,token,Qt.UserRole)
        for index,tip in enumerate(("Quadrilaterals first, triangles only where needed.",
                                    "Chooses a suitable method, including SmartMapped, without generating trial meshes for applicability checks.",
                                    "Structured quadrilaterals on mappable plates.",
                                    "Native unstructured triangulation.",
                                    "Preserves regular quadrilaterals on planar plates, with local transitions around holes and angled boundaries.")):
            self.strategy.setItemData(index,tip,Qt.ToolTipRole)
        self.strategy.setItemData(4,"SmartMapped",Qt.AccessibleTextRole)
        from anymesher.structured import StructurePreference
        self.preference=QComboBox();self.preference.addItems([item.value for item in StructurePreference]);form.addRow("Structure preference",self.preference)
        self.options=QPlainTextEdit("{}");self.options.setMaximumHeight(90)
        self.options.setToolTip("Extra keyword options as a JSON object. Leave {} unless instructed.")
        from ...mesh_controls import MeshControls,StructuredMeshControls
        self.native_controls=OptionForm.record(MeshControls,MeshControls.display_values(app.project.native_mesh_settings))
        self.structured_controls=OptionForm.record(StructuredMeshControls)
        from anymesher import MeshAutomationOptions,MeshQualityPolicy
        from anymesher.quad.options import QuadMeshingOptions
        from ...quad_first import sg1_quad_options
        self.quality_controls=OptionForm.record(MeshQualityPolicy)
        self.quad_controls=OptionForm.record(QuadMeshingOptions,asdict(sg1_quad_options()))
        self.automation_controls=OptionForm.record(MeshAutomationOptions)
        self.order=QComboBox();self.order.addItems(["linear","quadratic"]);self.order.setCurrentText(app.project.element_order);form.addRow("Element order",self.order)
        actions=QHBoxLayout();actions.setSpacing(6)
        self.generate=QPushButton("Generate mesh");self.generate.setProperty("primary",True)
        self.generate.clicked.connect(app.guarded(self.start));actions.addWidget(self.generate,1)
        cancel=QPushButton("Cancel mesh")
        cancel.clicked.connect(app.guarded(app.cancel_mesh));actions.addWidget(cancel)
        layout.addLayout(actions)
        self.summary=QLabel();self.summary.setObjectName("Hint");self.summary.setWordWrap(True);layout.addWidget(self.summary)
        advanced=QFormLayout()
        self.backend=QComboBox();self.backend.addItems(["auto","python","native"]);self.backend.setCurrentText(app.project.native_triangulation_backend);advanced.addRow("Triangulation backend",self.backend)
        self.layout_policy=QComboBox();self.layout_policy.addItems(["adaptive","existing"]);advanced.addRow("Quad-first layout policy",self.layout_policy)
        self.t3_minimum_angle=QLineEdit("15");advanced.addRow("Triangle minimum angle (deg)",self.t3_minimum_angle)
        self.t3_angle_action=QComboBox();self.t3_angle_action.addItems(["error","warning"]);advanced.addRow("Below-angle action",self.t3_angle_action)
        self.t3_angle_action.setToolTip("Warning retains thin triangles and records their count and minimum angle; strict certification still refuses this mode.")
        self.execution=QComboBox();self.execution.addItems(["Off","Automatic","Custom count"])
        self.execution.setCurrentText("Automatic")
        self.execution.setToolTip("Component-parallel meshing through ANYmesh's public parallel API.\n"
            "Off: serial meshing, nothing parallel is imported.\n"
            "Automatic (default): proposes a bounded worker count; ANYmesh still applies its independence and size gates.\n"
            "Custom count: an explicit worker count bypasses the size gates, never the safety gates.")
        self.workers=QSpinBox();self.workers.setRange(1,61);self.workers.setValue(4);self.workers.setEnabled(False)
        self.workers.setToolTip("Worker count for 'Custom count' parallel meshing (execution-only; never affects the mesh hash).")
        self.execution.currentTextChanged.connect(lambda text:self.workers.setEnabled(text=="Custom count"))
        advanced.addRow("Parallel meshing",self.execution)
        advanced.addRow("Mesh workers",self.workers)
        def buttons(section,entries):
            for label,callback in entries:
                button=QPushButton(label);button.clicked.connect(app.guarded(callback));section.addWidget(button)
        seeding=Collapsible("Seeding and local refinement",hint="Fix divisions on selected lines or refine around selected geometry.")
        seeding_form=QFormLayout();seeding.addLayout(seeding_form)
        self.divisions=QSpinBox();self.divisions.setRange(1,100000);self.divisions.setValue(4);seeding_form.addRow("Selected line divisions",self.divisions)
        self.refine_size=QLineEdit("0.1");self.refine_radius=QLineEdit("0.5")
        seeding_form.addRow("Refinement size (m)",self.refine_size);seeding_form.addRow("Refinement radius (m)",self.refine_radius)
        buttons(seeding,[("Pin selected lines",self.pin),("Clear seeding pins",self.clear_pins),
                         ("Refine around selection",self.refine),("Clear refinements",self.clear_refinements)])
        layout.addWidget(seeding)
        preview_section=Collapsible("Layout preview",hint="Preview the structured layout before meshing.")
        buttons(preview_section,[("Preview layout",self.preview),("Commit layout preview",app.commit_mesh_layout_preview),
                                 ("Discard layout preview",app.clear_mesh_layout_preview)])
        layout.addWidget(preview_section)
        settings=Collapsible("Advanced settings",hint="Backend, quality, quad-first and recovery controls. Defaults suit most models.")
        settings.addLayout(advanced)
        for label,widget in [("Native meshing controls",self.native_controls),("Structured layout controls",self.structured_controls),
                             ("Element quality policy",self.quality_controls),("Quad-first controls",self.quad_controls),
                             ("Mesh recovery policy",self.automation_controls)]:
            settings.addWidget(section_label(label));settings.addWidget(widget)
        settings.addWidget(section_label("Additional mesh options"));settings.addWidget(self.options)
        layout.addWidget(settings)
        tools=Collapsible("Diagnostics and tools")
        buttons(tools,[("Copy mesh diagnosis",self.copy_diagnosis),("Open standalone ANYmesher",self.open_mesher)])
        layout.addWidget(tools)
        self.sections={"seeding":seeding,"preview":preview_section,"advanced":settings,"tools":tools}
        layout.addStretch()

    def strategy_token(self):
        """The strategy token behind the visible label (SmartMapped -> smart_mapped)."""
        token=self.strategy.currentData(Qt.UserRole)
        return str(token) if token is not None else self.strategy.currentText()

    def set_strategy(self,token):
        index=self.strategy.findData(str(token),Qt.UserRole)
        self.strategy.setCurrentIndex(index) if index>=0 else self.strategy.setCurrentText(str(token))

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
        if self.strategy_token()=="auto":options.setdefault("quality_policy",MeshQualityPolicy(**self.quality_controls.values()).to_dict())
        if self.strategy_token() in ("quad_first","smart_mapped"):
            from anymesher.quad.options import QuadMeshingOptions
            values=self.quad_controls.values()
            if values["line_search"]=="off":values["max_local_optimizations"]=0
            options.setdefault("quad_options",QuadMeshingOptions(**values));options.setdefault("layout_policy",self.layout_policy.currentText())
            options.setdefault("quad_t3_minimum_angle",float(self.t3_minimum_angle.text()))
            options.setdefault("quad_t3_angle_action",self.t3_angle_action.currentText())
        from ...mesh_execution import MeshExecutionPolicy
        mode=self.execution.currentText()
        options["execution_policy"]=(MeshExecutionPolicy.off() if mode=="Off"
            else MeshExecutionPolicy.auto() if mode=="Automatic"
            else MeshExecutionPolicy.explicit(self.workers.value()))
        return dict(target_size=float(self.size.text()),strategy=self.strategy_token(),structure_preference=self.preference.currentText(),**options)

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
        payload=self.app.mesh_diagnostic_report(draft={
            "target_size_text":self.size.text(),
            "strategy":self.strategy_token(),
            "triangulation_backend":self.backend.currentText(),
            "element_order":self.order.currentText(),
            "layout_policy":self.layout_policy.currentText(),
        })
        self.app.clipboard.copy_text(payload)
        self.app.set_status(f"Mesh diagnosis copied ({len(payload):,} characters)")

    def reset_mesh_drafts(self):
        from ...mesh_controls import MeshControls,StructuredMeshControls
        self.size.setText(str(self.app.project.target_size or 0.25))
        settings=self.app.project.native_mesh_settings
        self.native_controls.set_values(MeshControls.display_values(settings))
        self.structured_controls.set_values(StructuredMeshControls.display_values(settings))
        self.set_strategy(self.app._project_mesh_strategy(self.app.project,None))
        self.preference.setCurrentText(self.app._project_structure_preference(self.app.project,None))
        self.options.setPlainText("{}")
        from anymesher import MeshAutomationOptions,MeshQualityPolicy
        from ...quad_first import effective_quad_options
        # to_dict thaws nested values; raw parameters hold quad_options as a frozen object
        parameters={} if settings is None else settings.to_dict()["parameters"]
        self.quality_controls.set_values(MeshQualityPolicy.create({key.removeprefix("mesh_quality_"):value for key,value in parameters.items() if key.startswith("mesh_quality_")}).to_dict())
        self.quad_controls.set_values(asdict(effective_quad_options(parameters.get("quad_options"))))
        self.automation_controls.set_values(asdict(MeshAutomationOptions()))
        self.backend.setCurrentText(self.app.project.native_triangulation_backend)
        self.order.setCurrentText(self.app.project.element_order)
        self.layout_policy.setCurrentText(parameters.get("layout_policy","adaptive"))
        self.t3_minimum_angle.setText(str(parameters.get("quad_t3_minimum_angle",15.0)))
        self.t3_angle_action.setCurrentText(parameters.get("quad_t3_angle_action","error"))
        self.execution.setCurrentText("Automatic");self.workers.setValue(4)

    def refresh(self):
        self.generate.setEnabled(not self.app.mesh_job_running and not self.app.session.read_only)
        mesh=self.app.mesh
        if mesh is None:
            self.summary.setText("No active mesh")
        else:
            summary=f"{mesh.num_nodes} nodes · {mesh.num_elements} elements"
            record=self.app.project.mesh_records.get(getattr(self.app,"mesh_record_id",None))
            if record is not None:
                target=record.summary.get("target_size")
                if target is not None:
                    import math
                    try:
                        usable=type(target) in (int,float) and target > 0 and math.isfinite(target)
                    except OverflowError:
                        usable=False
                    if usable:
                        summary+=f" · generated target {target:g} m"
                    else:
                        summary+=" · generated target unavailable"
                summary+=f" · {self.app.mesh_record_state(record)}"
            rule=(getattr(mesh,"hybrid_diagnostics",{}) or {}).get("t3_angle_rule",{})
            warnings=int(rule.get("warning_count",0)) if isinstance(rule,dict) else 0
            if warnings:
                summary+=f" · Warning: {warnings} triangles below {rule['minimum_angle']:g}° (uncertified)"
            self.summary.setText(summary)


class SolveTask(QWidget):
    def __init__(self,app):
        super().__init__();self.app=app
        from .widgets import Collapsible,section_label
        layout=QVBoxLayout(self)
        form=QFormLayout();layout.addLayout(form)
        self.analysis=QComboBox();self.analysis.addItems(list(ANALYSES));form.addRow("Analysis",self.analysis)
        self.case=QComboBox();form.addRow("Load case",self.case)
        self.case.setToolTip("Load case or load combination to solve")
        self.controls_layout=QVBoxLayout();layout.addLayout(self.controls_layout)
        self.controls=None;self.record_controls={}
        actions=QHBoxLayout();actions.setSpacing(6)
        self.submit=QPushButton("Solve");self.submit.setProperty("primary",True)
        self.submit.clicked.connect(app.guarded(self.start));actions.addWidget(self.submit,1)
        cancel=QPushButton("Cancel solve");cancel.clicked.connect(app.cancel_solve);actions.addWidget(cancel)
        layout.addLayout(actions)
        self.submit_hint=QLabel("Generate a mesh first; Solve runs on the current mesh.");self.submit_hint.setObjectName("Hint")
        self.submit_hint.setWordWrap(True);layout.addWidget(self.submit_hint)
        self.progress=QLabel();self.progress.setWordWrap(True);layout.addWidget(self.progress)
        self.material_response=QLabel();self.material_response.setWordWrap(True);layout.addWidget(self.material_response)
        self.output_section=Collapsible("Output requests",hint="Select stored output requests to include in this run.")
        self.output_requests=QListWidget();self.output_requests.setMaximumHeight(140)
        self.output_requests.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.output_requests.itemSelectionChanged.connect(self._update_output_title)
        self.output_section.addWidget(self.output_requests);layout.addWidget(self.output_section)
        self._output_request_signature=None
        resources=Collapsible("Solver resources",hint="Thread limits for the native solver.")
        from anysolver import ResourceConfig
        self.use_resources=QCheckBox("Use explicit solver resource policy");resources.addWidget(self.use_resources)
        import os
        gui_threads=min(2,max(1,(os.cpu_count() or 2)-1))
        self.use_resources.setChecked(True)
        self.resources=OptionForm.record(ResourceConfig,exclude=("metadata",));resources.addWidget(self.resources)
        self.resources.set_values({"solver_threads":gui_threads})
        self.use_resources.setToolTip("Default native solver thread limit keeps CPU capacity available for the workbench. Adjust it here or disable the limit.")
        layout.addWidget(resources)
        advanced=Collapsible("Advanced analysis options",hint="Extra keyword options as a JSON object. Leave {} unless instructed.")
        self.options=QPlainTextEdit("{}");self.options.setMaximumHeight(90);advanced.addWidget(self.options)
        layout.addWidget(advanced)
        self.sections={"outputs":self.output_section,"resources":resources,"advanced":advanced}
        self.analysis.currentTextChanged.connect(self.rebuild_controls);self.rebuild_controls()
        self.analysis.currentTextChanged.connect(self.refresh)
        layout.addWidget(section_label("Run log"))
        self.transcript=QPlainTextEdit();self.transcript.setReadOnly(True);layout.addWidget(self.transcript)
        self.transcript.setPlaceholderText("Progress and solver messages appear here while a job runs.")
        self.transcript.setMinimumHeight(120)
        self.transcript.document().setMaximumBlockCount(1500)
        self.progress_data=LiveProgressData()
        layout.addWidget(section_label("Convergence"))
        self.graph=QComboBox();self.graph.addItems(list(GRAPH_CHOICES));layout.addWidget(self.graph)
        self.plot=HistoryPlot();layout.addWidget(self.plot)
        self.graph.currentTextChanged.connect(self.update_graph)
        self.graph_timer=QTimer(self);self.graph_timer.setSingleShot(True);self.graph_timer.setInterval(100)
        self.graph_timer.timeout.connect(self.update_graph)

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
        if self.analysis.currentText()=="Batch linear static":
            selected=self.app.project.load_cases.get(self.case.currentText())
            cases=(self.app.project.series_children(selected.name) if selected is not None and selected.is_template
                   else [case for case in self.app.project.load_cases.values() if not case.is_template])
            options.setdefault('load_cases',[case.name for case in cases])
        if self.use_resources.isChecked():
            from anysolver import ResourceConfig
            key="resources" if self.analysis.currentText() in {"Nonlinear static","Capacity"} else "resource_config"
            raw=options.get(key)
            options[key]=ResourceConfig(**raw) if isinstance(raw,dict) else raw or ResourceConfig(**self.resources.values())
        options["output_request_ids"] = tuple(
            item.data(Qt.UserRole) for item in self.output_requests.selectedItems()
        )
        return options

    def refresh(self):
        from ...presentation.engineering_summary import material_response
        text,color=material_response(self.app.project,self.analysis.currentText())
        self.material_response.setText(text);self.material_response.setStyleSheet(f"color: {color}")
        requests=sorted(self.app.project.output_requests.values(),key=lambda item:(item.label,item.id))
        signature=tuple((item.id,item.label,item.quantity_keys,item.location) for item in requests)
        if signature!=self._output_request_signature:
            selected={item.data(Qt.UserRole) for item in self.output_requests.selectedItems()}
            self.output_requests.clear()
            for request in requests:
                item=QListWidgetItem(f"{request.label} · {', '.join(request.quantity_keys)} · {request.location}")
                item.setData(Qt.UserRole,request.id)
                self.output_requests.addItem(item)
                item.setSelected(request.id in selected)
            self._output_request_signature=signature
            self._update_output_title()
        current=self.case.currentText();choices=list(self.app.project.load_cases)+["combination: "+name for name in self.app.project.combinations]
        if [self.case.itemText(i) for i in range(self.case.count())]!=choices:
            self.case.clear();self.case.addItems(choices)
            if current in choices:self.case.setCurrentText(current)
        self.submit.setEnabled(self.app.mesh is not None and not self.app.session.read_only)
        self.submit_hint.setVisible(self.app.mesh is None)

    def _update_output_title(self):
        total=self.output_requests.count()
        selected=len(self.output_requests.selectedItems())
        self.output_section.toggle.setText(f"Output requests ({selected} of {total} selected)" if total else "Output requests (none defined)")

    def begin_job(self,name,job_id,report):
        self.graph_timer.stop()
        self.progress_data.clear()
        self.update_graph()
        self.transcript.setPlainText(f"{name}\nJob {job_id}\n{report}")

    def append_progress(self,text,payload=None):
        self.append_progress_batch(((text,payload),))

    def append_progress_batch(self,entries):
        if not entries:return
        self.transcript.appendPlainText("\n".join(text for text,_ in entries))
        changed=False
        for text,payload in entries:
            changed=self.progress_data.ingest(text,payload) or changed
        if changed and not self.graph_timer.isActive():self.graph_timer.start()

    def update_graph(self,*_):
        choice=self.graph.currentText();choices=self.progress_data.graph_choices
        if tuple(self.graph.itemText(i) for i in range(self.graph.count()))!=choices:
            with QSignalBlocker(self.graph):self.graph.clear();self.graph.addItems(list(choices));self.graph.setCurrentText(choice)
        series=self.progress_data.series(self.graph.currentText())
        self.plot.show_series([] if series is None else [series])

    def append_report(self,text):
        self.transcript.appendPlainText(text)

    def show_progress(self,text):
        self.progress.setText(text)

    def reset(self):
        self.graph_timer.stop()
        self.progress.clear();self.transcript.clear()
        self.progress_data.clear();self.plot.show_series([])
        self.update_graph()


class ResultsTask(QWidget):
    def __init__(self,app):
        super().__init__();self.app=app
        self._display_source=None;self._selected_entry=None;self._quantity_component=None
        self._live_quantity_descriptors=();self._display_pending=False;self._syncing_controls=False
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows);form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.field=QComboBox();self.field.addItems(available_fields());self.field.setCurrentText("magnitude");form.addRow("Field",self.field)
        self.scale=QLineEdit("auto");self.scale.setToolTip("Auto or a visual displacement multiplier; result values remain unchanged")
        form.addRow("Deformation scale",self.scale)
        from .result_browser import ResultFrameSelector
        self.frame=ResultFrameSelector();form.addRow("Frame",self.frame)
        self.playback_speed=QComboBox();self.playback_speed.addItems(["0.5","1","2","4","8","12","20","30"])
        self.playback_speed.setCurrentText("20");form.addRow("Playback speed (fps)",self.playback_speed)
        self.from_frame=QSpinBox();self.from_frame.setRange(1,1)
        self.to_frame=QSpinBox();self.to_frame.setRange(1,1)
        form.addRow("From frame",self.from_frame);form.addRow("To frame",self.to_frame)
        self.preprocess_first=QCheckBox("Preprocess retained frames first (separate process)")
        self.preprocess_first.setToolTip("Prepare the selected interval in an isolated process, then play from a temporary disk cache. Requires a retained field and GPU renderer.")
        form.addRow(self.preprocess_first)
        self.preparation_status=QLabel("Preprocessing starts when you press Play.")
        self.preparation_status.setWordWrap(True);self.preparation_status.hide()
        form.addRow(self.preparation_status)
        self.preparation_progress=QProgressBar();self.preparation_progress.hide()
        form.addRow(self.preparation_progress)
        self.preprocess_first.toggled.connect(self.preparation_status.setVisible)
        self._range_owner=None;self._preparation=None;self._cached_playback=None
        self.units=QComboBox();self.units.addItems(["SI (m / Pa)","Engineering (mm / MPa)"]);form.addRow("Display units",self.units)
        self.colors=QComboBox();self.colors.addItems(list(RESULT_COLORMAPS));form.addRow("Color map",self.colors)
        self.minimum=QLineEdit();self.maximum=QLineEdit()
        self.minimum.setPlaceholderText("Automatic");self.maximum.setPlaceholderText("Automatic")
        form.addRow("Color minimum",self.minimum);form.addRow("Color maximum",self.maximum)
        from .result_browser import ResultComponentSelector
        self.component=ResultComponentSelector();self.component.setPlaceholderText("Choose a saved component")
        form.addRow("Component",self.component)
        for combo in (self.field,self.units,self.colors,self.component):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon);combo.setMinimumContentsLength(8)
        from PySide6.QtWidgets import QGridLayout
        from .widgets import Collapsible,section_label
        def button_grid(entries):
            grid=QGridLayout();grid.setSpacing(6)
            for index,(label,callback) in enumerate(entries):
                button=QPushButton(label);button.clicked.connect(app.guarded(callback));grid.addWidget(button,index//2,index%2)
            layout.addLayout(grid)
        self.selection_details=QLabel();self.selection_details.setWordWrap(True);layout.addWidget(self.selection_details)
        self.envelope=QCheckBox("Envelope of all live frames");layout.addWidget(self.envelope)
        self.envelope.setToolTip("One static signed maximum-absolute contour over all live frames. The animation From/To interval does not apply. Saved retained fields do not currently support this option.")
        show=QPushButton("Show results");show.setProperty("primary",True);show.clicked.connect(app.guarded(self.show_results));layout.addWidget(show)
        playback=QHBoxLayout();playback.setSpacing(6)
        play=QPushButton("Play retained frames");play.clicked.connect(app.guarded(self.play));playback.addWidget(play)
        stop=QPushButton("Stop playback");stop.clicked.connect(self.stop_playback);playback.addWidget(stop)
        self.playback_status=QLabel("Playback idle");layout.addWidget(self.playback_status)
        layout.addLayout(playback)
        self.outcome=QLabel();self.outcome.setWordWrap(True);layout.addWidget(self.outcome)
        self.frame_details=QLabel();self.frame_details.setWordWrap(True);layout.addWidget(self.frame_details)
        self.report=QPlainTextEdit();self.report.setReadOnly(True);layout.addWidget(self.report)
        self.report.setPlaceholderText("Result summaries, probes and quantity details appear here.")
        layout.addWidget(section_label("Inspect"))
        self.quantities=QComboBox();self.quantities.setToolTip("Saved quantity to inspect");layout.addWidget(self.quantities)
        self._gif_capture=None
        self._preview_owner=None
        button_grid([("Inspect quantity / table",self.inspect_quantity),("Probe selection",self._probe),
                     ("Sample along selected line",self.along_line),("Plot history",self.history),
                     ("Plot at selected point",self.history_at_selection)])
        self.table=ResultTable();layout.addWidget(self.table)
        self.plot=HistoryPlot();layout.addWidget(self.plot)
        layout.addWidget(section_label("Export"))
        button_grid([("Export field CSV",self.export_csv),("Export quantity field CSV",self.export_quantity_csv),
                     ("Export report",self.export_report),("Export GIF",self.export_gif)])
        inputs=Collapsible("Submitted inputs",hint="The exact inputs recorded with the selected job.")
        self.submitted_inputs=QPlainTextEdit();self.submitted_inputs.setReadOnly(True);inputs.addWidget(self.submitted_inputs)
        layout.addWidget(inputs)
        self.frame.valueChanged.connect(self._frame_changed)
        self.field.currentTextChanged.connect(self._field_changed)
        self.component.currentTextChanged.connect(self._controls_changed)
        self.units.currentTextChanged.connect(self._appearance_changed)
        self.colors.currentTextChanged.connect(self._appearance_changed)
        self.scale.editingFinished.connect(self._controls_changed)
        self.minimum.editingFinished.connect(self._appearance_changed)
        self.maximum.editingFinished.connect(self._appearance_changed)
        self.quantities.currentIndexChanged.connect(lambda *_:setattr(self,"_quantity_component",None))
        self._playback=None
        self.playback_speed.currentTextChanged.connect(self._playback_input_changed)
        self.from_frame.valueChanged.connect(self._playback_input_changed)
        self.to_frame.valueChanged.connect(self._playback_input_changed)
        self.preprocess_first.toggled.connect(self._playback_input_changed)
        self.envelope.toggled.connect(self._controls_changed)
        for editor in (self.scale,self.minimum,self.maximum):editor.textChanged.connect(self._playback_input_changed)


    def activate_job(self,job_id):
        solution=self.app.solutions.get(job_id)
        if solution is None and job_id not in self.app.result_datasets:raise ValueError("This job has no available retained result")
        self.reset();self.app.active_job_id=job_id;self.app.solution=solution;self.app.shape_index=0
        self.refresh();self.app.notebook.select("Results");self.show_results()

    def _frame_changed(self,value):
        self.app.shape_index=value
        self.refresh_details()
        self._controls_changed()

    def _set_frame_index(self,value):
        # Playback/export already draws explicitly; do not queue a second draw
        # that could replace the captured frame or stop cached playback.
        blocker=QSignalBlocker(self.frame);self.frame.setValue(value)
        # Panel refresh calls this with the current shape index; writing it
        # back unchanged publishes a workbench state event that schedules a
        # full tree/panel refresh on every idle frame.
        if self.app.shape_index!=self.frame.value():self.app.shape_index=self.frame.value()
        del blocker

    def use_retained(self):
        return self.app.solution is None or self._display_source=="retained"

    def live_field_names(self):
        solution=self.app.solution
        fn=getattr(solution,"available_fields",None)
        return tuple(fn() if callable(fn) else available_fields()) if solution is not None else ()

    def _field_changed(self,*_):
        if self._syncing_controls:return
        self.update_component_choices()
        self._selected_entry=("field",self.field_name(),self.component.text().strip() or None) if self.use_retained() else ("live_field",self.field_name(),None)
        self.frame.setMaximum(max(0,self.frame_count()-1))
        self._sync_frame_interval(self.frame_count())
        self._controls_changed()

    def frame_count(self):
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        if self.use_retained() and dataset is not None:
            if self.field_name() in dataset.field_keys:
                frames=dataset.field(self.field_name()).descriptor.frames
                if frames:return len(frames)
            return len(dataset.frames)
        solution=self.app.solution
        return len(getattr(solution,"shapes",()) or (solution,)) if solution is not None else 0

    def update_component_choices(self):
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        components=tuple(dataset.field(self.field_name()).descriptor.components) if self.use_retained() and dataset is not None and self.field_name() in dataset.field_keys else ()
        previous=self.component.text();blocker=QSignalBlocker(self.component)
        self.component.clear();self.component.addItem("");self.component.addItems(components)
        self.component.setText(previous if previous in components else "")
        self.component.setEnabled(bool(components));del blocker

    def _display_selection(self):
        """Value edits cannot turn an old displayed snapshot into a new result."""
        from ...presentation.retained_results import geometry_style_key
        return (id(self.app.session),self.app.project.document_id,self.app.active_job_id,
                id(self.app.solution),id(self.app.result_datasets.get(self.app.active_job_id)),
                getattr(self.app,'_result_revision',0),getattr(self.app,'_result_source_epoch',0),
                self.use_retained(),self.field_name(),self.component.text(),self.frame.value(),
                self.scale.text(),self.envelope.isChecked(),self._selected_entry,
                geometry_style_key(self.app.viewport.visualization))

    def _appearance_changed(self,*_):
        self._controls_changed(appearance_only=True)

    def _update_display_appearance(self):
        if (getattr(self,'_displayed_selection',None)!=self._display_selection()
                or self.app._view_mode!='results'):return False
        applied=self.app.viewport.update_result_appearance(limits=self.colour_limits(),
                    display_units=self.display_units(),colormap=self.colormap(),
                    colormap_name=self.colors.currentText())
        if applied:
            visual=getattr(self.app,'panels',{}).get('Visualization')
            if visual is not None:visual.sync_from_viewport()
        return applied

    def _controls_changed(self,*_,appearance_only=False):
        inspection=self._selected_entry is not None and self._selected_entry[0] in {"field_table","live","history","table"}
        if self._syncing_controls or (self.app._view_mode!="results" and not inspection):return
        if getattr(self,"_playback",None) is not None and self._playback.active:self.stop_playback()
        if self._display_pending:
            self._display_appearance_only=getattr(self,'_display_appearance_only',False) and appearance_only
            return
        self._display_appearance_only=appearance_only
        canvas=self.app.viewport.canvas
        if canvas.is_playing_animation:canvas.stop_animation()
        self._display_pending=True
        owner=(id(self.app.session),self.app.project.document_id,self.app.active_job_id,id(self.app.solution),id(self.app.result_datasets.get(self.app.active_job_id)))
        def display():
            if not self._display_pending:return
            self._display_pending=False
            current=(id(self.app.session),self.app.project.document_id,self.app.active_job_id,id(self.app.solution),id(self.app.result_datasets.get(self.app.active_job_id)))
            if not self.app._closing and owner==current:
                def draw():
                    if self._display_appearance_only and self._update_display_appearance():return
                    if inspection:self.inspect_quantity()
                    elif self.app._view_mode=="results":self.show_results()
                self.app.guarded(draw)()
        QTimer.singleShot(0,self,display)

    def cancel_pending_display(self):
        """An explicit current-controls draw replaces its queued duplicate."""
        self._display_pending=False

    def select_entry(self,entry):
        self.stop_playback()
        self.app.notebook.select("Results")
        self._syncing_controls=True
        try:
            if entry.kind in {"live_field","field"}:
                self._display_source="retained" if entry.kind=="field" else "live"
                self._sync_envelope_control()
                self.ensure_compatible_field();self.field.setCurrentText(entry.key)
                self.update_component_choices();self.component.setText(entry.component or "")
                self.frame.setMaximum(max(0,self.frame_count()-1))
                self._sync_frame_interval(self.frame_count())
            else:
                kind="field" if entry.kind=="field_table" else entry.kind
                choice=next((i for i in range(self.quantities.count()) if tuple(self.quantities.itemData(i))==(kind,entry.key)),None)
                if choice is None:raise ValueError("This result quantity is no longer available")
                self.quantities.setCurrentIndex(choice);self._quantity_component=entry.component
            self._selected_entry=entry.identity
        finally:self._syncing_controls=False
        if entry.kind in {"live_field","field"}:self.show_results()
        else:self.inspect_quantity()
        if entry.kind not in {"live_field","field"}:self.selection_details.setText(f"{entry.label}\n{entry.unit or 'dimensionless'} · {entry.location or 'Table / history'}")
        self.app.show_result_browser();self.app.result_browser.sync_selection()

    def refresh(self):
        solution=self.app.solution
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        if dataset is not None and self.app.prepare_result_dataset(dataset):return
        owner=(self.app.active_job_id,id(solution) if solution is not None else id(dataset) if dataset is not None else None)
        if owner!=self._preview_owner:
            self.reset();self._preview_owner=owner
        self.ensure_compatible_field();self.update_component_choices()
        self._sync_envelope_control()
        count=self.frame_count()
        self._sync_frame_interval(count)
        self.frame.setMaximum(max(0,count-1))
        self._set_frame_index(self.app.shape_index)
        if solution is not None and not getattr(self,"_inspection_active",False):self.report.setPlainText(_solution_report(solution))
        elif dataset is not None and not getattr(self,"_inspection_active",False):
            job=self.app.project.jobs.get(self.app.active_job_id)
            state="stale" if job is not None and self.app._job_is_stale(job) else "current"
            summary=dataset.metadata("summary")
            deformation="Deformation available" if "displacement" in dataset.field_keys else "Deformation unavailable (undeformed display)"
            self.report.setPlainText(f"Retained result ({state})\n{len(dataset.frames)} frame(s)\n{deformation}\nStored solver summary:\n"+json.dumps(summary,indent=2,sort_keys=True)+"\nSelect a quantity to inspect its saved fields, histories or tables.")
        elif dataset is None and solution is None:
            self.report.clear();self.table.show_rows([],[]);self.plot.show_series([])
        self.refresh_details()
        self.refresh_quantities()

    def refresh_quantities(self, *, descriptors=None):
        """Publish request-local inventory, or discover current mutable sources."""
        solution=self.app.solution
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        choices=[];self._live_quantity_descriptors=()
        if dataset is not None:
            choices.extend(((f"Output: {dataset.field(key).descriptor.label}" if key.startswith("request_") else f"Field: {key}"),("field",key)) for key in dataset.field_keys)
            choices.extend((f"History: {key}",("history",key)) for key in dataset.history_keys)
            choices.extend((f"Table: {key}",("table",key)) for key in dataset.table_keys)
        elif solution is not None:
            from ...post.solver_data import solution_quantity_descriptors
            self._live_quantity_descriptors=(solution_quantity_descriptors(solution)
                if descriptors is None else descriptors)
            choices.extend((item.label,("live",item.quantity_id)) for item in self._live_quantity_descriptors)
        else:self._live_quantity_descriptors=()
        if choices!=[(self.quantities.itemText(i),tuple(self.quantities.itemData(i)) if self.quantities.itemData(i) is not None else None) for i in range(self.quantities.count())]:
            selected=self.quantities.currentData()
            prior=tuple(selected) if selected is not None else None
            self.quantities.clear()
            for text,data in choices:self.quantities.addItem(text,data)
            index=next((i for i,(_text,data) in enumerate(choices) if data==prior),0 if prior is None and choices else -1)
            self.quantities.setCurrentIndex(index)

    def refresh_details(self):
        from ...presentation.engineering_summary import outcome_text,constitutive_summary
        solution=self.app.solution;dataset=self.app.result_datasets.get(self.app.active_job_id)
        submitted=self.app.submitted_input_reports.get(self.app.active_job_id)
        if submitted is None and dataset is not None:
            provenance=dataset.metadata("provenance")
            submitted=provenance.get("submitted_inputs_text")
            if submitted is None and provenance.get("submitted_inputs") is not None:
                submitted=json.dumps(provenance["submitted_inputs"],indent=2,sort_keys=True)
        submitted=submitted or "Submitted input text is unavailable; retained artifact hashes identify its provenance."
        if submitted!=getattr(self,"_submitted_text",None):
            self.submitted_inputs.setPlainText(submitted)
            self._submitted_text=submitted
        if solution is None or self.use_retained():
            summary=dataset.metadata("summary") if dataset is not None else {}
            status=summary.get("status")
            self.outcome.setText(f"Retained result · stored status: {status}" if status is not None else "Retained result · stored status unavailable" if dataset is not None else "Run an analysis to see its outcome.")
            self.outcome.setStyleSheet(f"color: {overlay('outcome')}")
            if dataset is not None and self.field_name() in dataset.field_keys:
                descriptor=dataset.field(self.field_name()).descriptor
                values=descriptor.frames or dataset.frames
                coordinate=f" · value {values[self.frame.value()]:g}" if self.frame.value()<len(values) else ""
                self.frame_details.setText(f"{descriptor.label}: saved frame {self.frame.value()+1} of {len(values)}{coordinate}")
            else:self.frame_details.setText("")
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
        self._inspection_active=True
        # Read widget state once on the owner; worker emits plain preview actions.
        from types import SimpleNamespace
        choice=self.quantities.currentData(); frame=self.frame.value()
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        source=SimpleNamespace(result_datasets=dict(self.app.result_datasets),
                               active_job_id=self.app.active_job_id,solution=self.app.solution)
        component=self._quantity_component
        def load(cancel):
            actions=[]
            def record(name):return lambda *args:actions.append((name,args))
            source.set_status=record("status")
            proxy=SimpleNamespace(app=source,_quantity_component=component,
                quantities=SimpleNamespace(currentData=lambda:choice),frame=SimpleNamespace(value=lambda:frame),
                report=SimpleNamespace(setPlainText=record("report"),appendPlainText=record("append")),
                table=SimpleNamespace(show_rows=record("table")),plot=SimpleNamespace(show_series=record("plot")))
            type(self)._inspect_quantity_source(proxy)
            return actions
        def publish(actions):
            targets={"report":self.report.setPlainText,"append":self.report.appendPlainText,
                     "table":self.table.show_rows,"plot":self.plot.show_series,"status":self.app.set_status}
            for name,args in actions:targets[name](*args)
        self.app._request_result(load,publish,dataset=dataset if choice and choice[0]!="live" else None,channel="inspection")

    def _inspect_quantity_source(self):
        import numpy as np
        kind,key=self.quantities.currentData() or (None,None)
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        if kind=="history":
            from ...post.history import Series
            x,y=dataset.history(key,rows=slice(0,2000))
            self.plot.show_series([Series(key,np.asarray(x),np.asarray(y))]);return
        if kind=="field":
            stored=dataset.field(key);descriptor=stored.descriptor
            # Inspection follows this selected quantity's own row order. Its
            # descriptor below retains source indices/labels; contour drawing
            # separately resolves the shared root-frame coordinate.
            index=int(self.frame.value()) if descriptor.frames else None
            if index is not None and not 0<=index<len(descriptor.frames):
                raise ValueError("selected quantity-local frame is unavailable")
            values=stored.read(index)
            policy=descriptor.provenance.get("frame_policy","all")
            frame_text=f"Quantity frame {index+1}; value {descriptor.frames[index]:g}" if descriptor.frames and index is not None else "Quantity frame association unavailable"
            labels=descriptor.provenance.get("frame_labels",descriptor.provenance.get("load_cases",()))
            if len(labels)==len(descriptor.frames) and labels and index is not None:frame_text+=f"; load case: {labels[index]}"
            source_indices=descriptor.provenance.get("source_frame_indices",())
            if len(source_indices)==len(descriptor.frames) and source_indices and index is not None:frame_text+=f"; source frame index: {source_indices[index]}"
            if policy=="envelope":frame_text="Signed maximum-absolute envelope; frame coordinate 0 is synthetic"
            self.report.setPlainText(f"{descriptor.label}\nLocation: {descriptor.location}\nUnits: {descriptor.unit}\nComponents: {', '.join(descriptor.components)}\nBasis: {descriptor.basis}\nRecovery: {descriptor.recovery}\nReduction: {descriptor.reduction}\n{frame_text}\nFrame policy: {policy}")
            node_status=descriptor.provenance.get("node_recovery_status",())
            if node_status and index is not None:
                unqualified=sorted(int(node) for node,status in node_status[index].items() if status!="qualified")
                if unqualified:self.report.appendPlainText(f"Owner patch fallback/unclassified nodes: {unqualified}")
        elif kind=="table":values=dataset.table(key,rows=slice(0,2000))
        elif kind=="live":
            from ...post.access import query_solution_quantity
            resolved=query_solution_quantity(self.app.solution,key);values=resolved.data
        else:raise ValueError("Select an available quantity")
        from collections.abc import Mapping
        if isinstance(values,Mapping) or isinstance(values,(list,tuple)) and values and isinstance(values[0],Mapping):
            def plain(value):
                if isinstance(value,Mapping):return {key:plain(item) for key,item in value.items()}
                if isinstance(value,(list,tuple)):return [plain(item) for item in value]
                return value
            self.report.setPlainText(json.dumps(plain(values),indent=2,default=str));return
        values=np.asarray(values)
        selected_component=self._quantity_component
        components=tuple(descriptor.components) if kind=="field" else tuple(getattr(resolved.descriptor,"components",())) if kind=="live" else ()
        if selected_component is not None:
            if selected_component not in components or values.ndim<2 or values.shape[-1]!=len(components):
                raise ValueError("The selected component has no compatible saved column")
            values=np.take(values,components.index(selected_component),axis=-1)
        if values.ndim==0:values=values.reshape(1,1)
        elif values.ndim==1:values=values.reshape(-1,1)
        else:values=values.reshape(values.shape[0],-1)
        columns=[f"Column {i+1}" for i in range(values.shape[1])]
        if selected_component is not None:columns=[selected_component] if values.shape[1]==1 else [f"{selected_component} · {i+1}" for i in range(values.shape[1])]
        rows=values[:2000].tolist()
        if kind=="field":
            if selected_component is None and len(descriptor.components)==values.shape[1]:columns=list(descriptor.components)
            association="node_ids" if descriptor.location=="node" else "element_ids"
            association_key=f"{key}_{association}"
            if association_key in dataset.table_keys:
                ids=np.asarray(dataset.table(association_key))
                if len(ids)==len(values):
                    if ids.ndim==1:
                        columns.insert(0,association.removesuffix("s"))
                        rows=[[int(identifier),*row] for identifier,row in zip(ids,rows)]
                        if descriptor.provenance.get("node_recovery_status") and index is not None:
                            statuses=descriptor.provenance["node_recovery_status"][index]
                            columns.insert(1,"recovery_status")
                            rows=[[row[0],statuses.get(str(row[0]),"unclassified"),*row[1:]] for row in rows]
                    elif ids.ndim==2 and ids.shape[1]==2:
                        columns=["element_id","face_id",*columns]
                        rows=[[*map(int,identifier),*row] for identifier,row in zip(ids,rows)]
        self.table.show_rows(columns,rows)
        self.app.set_status(f"{key}: previewing {min(len(values),2000)} rows")

    def export_quantity_csv(self):
        """Export the selected retained view, including its own frames and IDs."""
        kind,key=self.quantities.currentData() or (None,None)
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        if kind!="field" or dataset is None:
            raise ValueError("Select a retained quantity field to export")
        path=self.app.dialogs.save_file(filetypes=[("CSV","*.csv")],defaultextension=".csv")
        if not path:return
        from ..result_export import lazy_field_to_csv
        from ...post.report import write_csv
        from ..result_export import write_client_export
        job_id=self.app.active_job_id
        self.app._request_result(lambda cancel:write_client_export(path,lazy_field_to_csv(dataset,key,frame=None),cancel,source=str(job_id)),
                                 lambda written:None,dataset=dataset,channel="export",ordered=True)

    def guarded(self,callback):return self.app.guarded(callback)

    def _probe(self):
        from ...post.extract import probe
        shape=self.app.current_shape()
        if shape is None:raise ValueError("Select a live result before probing")
        refs=self.app.selection.items
        if not refs:raise ValueError("Select an entity to probe")
        engineering=self.display_units()=="Engineering (mm / MPa)"
        self.app._request_result(lambda cancel: "\n\n".join(probe(shape,ref).text(length_scale=1000 if engineering else 1,length_unit="mm" if engineering else "m",stress_scale=1e-6 if engineering else 1,stress_unit="MPa" if engineering else "Pa") for ref in refs),self.report.setPlainText,channel="inspection")

    def history(self):
        from ...post.history import history_series
        if self.app.solution is None:raise ValueError("A live solution is required for history")
        solution,units=self.app.solution,self.display_units()
        self.app._request_result(lambda cancel:converted_series(history_series(solution),units),self.plot.show_series,channel="history")

    def history_at_selection(self):
        from ...post.history import history_series
        refs=list(self.app.selection.items)
        if len(refs)!=1 or refs[0].kind!="vertex":raise ValueError("Select one geometry point")
        mesh=self.app.retained_result_mesh(self.app.active_job_id) if self.app.active_job_id is not None else None
        node=mesh.node_of_vertex.get(refs[0].id) if mesh is not None else None
        if node is None or self.app.solution is None:raise ValueError("Select a point with a node in a live result")
        solution,units=self.app.solution,self.display_units()
        component=self.field_name() if self.field_name() in ("ux","uy","uz","rx","ry","rz") else "uz"
        self.app._request_result(lambda cancel:converted_series(history_series(solution,probe=node,component=component),units),self.plot.show_series,channel="history")

    def along_line(self):
        from ...post.extract import along_line
        from ...presentation.result_display import unit_transform
        refs=list(self.app.selection.items);shape=self.app.current_shape()
        if shape is None or len(refs)!=1 or refs[0].kind!="edge":raise ValueError("Select one line in a live result")
        name,units=self.field_name(),self.display_units()
        def load(cancel):
            result=along_line(shape,refs[0],name)
            xs,xunit=unit_transform("m",units);ys,yunit=unit_transform(result.unit,units)
            return f"Distance [{xunit}], value [{yunit}]\n"+"\n".join(f"{x*xs:.6g}, {y*ys:.6g}" for x,y in zip(result.distances,result.values))
        self.app._request_result(load,self.report.setPlainText,channel="inspection")

    def export_report(self):
        from ...post.report import result_report_context,write_result_report,write_report
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        if dataset is None and self.app.solution is None:raise ValueError("Select a result to report")
        path=self.app.dialogs.save_file(filetypes=[("Markdown","*.md"),("HTML","*.html")],defaultextension=".md")
        if not path:return
        solution,project,job_id=self.app.solution,self.app.project,self.app.active_job_id
        job=project.jobs.get(job_id);document_hash=self.app.session.revision.document_hash
        stale=self.app._job_is_stale(job) if job else None
        def load(cancel):
            if dataset is None:
                if str(path).lower().endswith((".html",".htm")):raise ValueError("Save the project to retain the artifact before exporting HTML")
                from ...post.report import report_markdown
                from ..result_export import write_client_export
                written=write_client_export(path,report_markdown(solution),cancel,source=str(job_id or "current solved source"))
            else:
                context=result_report_context(dataset,project=project,job=job,current_document_hash=document_hash,stale=stale)
                from ...post.report import result_report_markdown,result_report_html
                from ..result_export import write_client_export
                text=(result_report_html if str(path).lower().endswith((".html",".htm")) else result_report_markdown)(dataset,context=context)
                written=write_client_export(path,text,cancel,source=str(job_id))
            return written
        self.app._request_result(load,lambda written:None,dataset=dataset,channel="export",ordered=True)

    def export_gif(self):
        frames=self.animation_frames()
        if self.envelope.isChecked():raise ValueError("An envelope is a static contour; turn it off before animation")
        self.stop_playback()
        if self._gif_capture is not None:raise ValueError("GIF capture is already running")
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        count=self.frame_count()
        if not count:raise ValueError("Select a result to export")
        path=self.app.dialogs.save_file(filetypes=[("GIF animation","*.gif")],defaultextension=".gif")
        if not path:return
        self._gif_capture=dict(session=self.app.session,job=self.app.active_job_id,path=path,count=len(frames),frames=frames,index=0,original=self.app.shape_index,images=[])
        self.app.scheduler.call_later(0,self._capture_gif_frame)

    def _cancel_gif_capture(self):
        state=self._gif_capture;self._gif_capture=None
        if state is not None:state["images"].clear()

    def _capture_gif_frame(self):
        state=self._gif_capture
        if state is None or self.app._closing:return
        if state["session"] is not self.app.session or state["job"]!=self.app.active_job_id:
            ResultsTask._cancel_gif_capture(self);return
        try:
            if state["index"]<state["count"]:
                self._set_frame_index(state["frames"][state["index"]])
                def capture():
                    if self._gif_capture is not state or self.app._closing:return
                    state["images"].append(self.app.viewport.canvas.capture_image());state["index"]+=1
                    self.app.scheduler.call_later(1,self._capture_gif_frame)
                self.show_results(on_ready=capture,on_cancel=lambda:ResultsTask._cancel_gif_capture(self));return
            self._set_frame_index(state["original"]);self.show_results();self._gif_capture=None
            from ...presentation.result_export import save_gif
            future=self.app._artifact_executor.submit(save_gif,state["images"],state["path"])
            def poll():
                if self.app._closing:return
                if not future.done():self.app.scheduler.call_later(50,poll);return
                self.app.guarded(lambda:self.app.set_status(f"GIF exported to {future.result()}"))()
            self.app.scheduler.call_later(50,poll)
        except Exception:
            self._gif_capture=None;self._set_frame_index(state["original"])
            raise

    def reset(self):
        self.stop_playback(clear_prepared=True)
        self._range_owner=None
        self._inspection_active=False
        self._gif_capture=None
        self._preview_owner=None
        self._display_source=None;self._selected_entry=None;self._quantity_component=None;self._live_quantity_descriptors=()
        self.component.setText("");self.selection_details.clear()
        self.app.viewport.canvas.stop_animation()
        self.report.clear();self.table.show_rows([],[]);self.plot.show_series([])

    def export_csv(self):
        from ...post.report import field_to_csv,write_csv
        from ...presentation.result_export import lazy_field_to_csv
        path=self.app.dialogs.save_file(filetypes=[("CSV","*.csv")],defaultextension=".csv")
        if not path:return
        shape=self.app.current_shape()
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        retained,name,frame=self.use_retained(),self.field_name(),self.frame.value()
        job_id=self.app.active_job_id
        if not (retained and dataset is not None) and shape is None:raise ValueError("Select a result to export")
        def load(cancel):
            text=lazy_field_to_csv(dataset,name,frame=dataset.field(name).root_frame_index(frame)) if retained and dataset is not None else field_to_csv(shape,name)
            from ..result_export import write_client_export
            return write_client_export(path,text,cancel,source=str(job_id or "current solved source"))
        self.app._request_result(load,lambda written:None,dataset=dataset if retained else None,channel="export",ordered=True)

    def ensure_compatible_field(self):
        solution=self.app.solution
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        if solution is None and dataset is None:return self.field.currentText()
        if self.use_retained() and dataset is not None:choices=list(dataset.field_keys)
        else:
            fn=getattr(solution,"available_fields",None)
            choices=list(fn() if callable(fn) else available_fields())
        if choices and [self.field.itemText(i) for i in range(self.field.count())]!=choices:
            blocker=QSignalBlocker(self.field)
            current=self.field.currentText();self.field.clear();self.field.addItems(choices)
            self.field.setCurrentText(current if current in choices else "magnitude" if "magnitude" in choices else choices[0])
            del blocker
        return self.field.currentText()

    def field_name(self):return self.field.currentText()
    def scale_value(self,shape):
        from ...presentation.result_display import deformation_scale
        return deformation_scale(self.scale.text(),self.app.project,shape)
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

    def show_results(self, *, on_ready=None, on_cancel=None):
        if on_ready is None and on_cancel is None and not getattr(self,"_stream_request",False) and getattr(self,"_playback",None) is not None and self._playback.active:
            self.stop_playback()
        self._inspection_active=False
        selection=self._display_selection()
        ready=on_ready
        def published():
            if selection==self._display_selection():self._displayed_selection=selection
            if ready is not None:ready()
        on_ready=published
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        if self.use_retained() and dataset is not None and self.app.prepare_result_dataset(
                dataset,on_ready=lambda:self.show_results(on_ready=on_ready,on_cancel=on_cancel),on_cancel=on_cancel):return
        if self.use_retained():
            dataset=self.app.result_datasets.get(self.app.active_job_id)
            component=self.component.text().strip() or None
            if dataset is not None:
                descriptor=dataset.field(self.field_name()).descriptor
                if component is not None and component not in descriptor.components:raise ValueError("Choose a component available in this retained field")
                if descriptor.location not in {"node","element","integration_point"}:
                    from ...presentation.result_browser import ResultEntry
                    if on_cancel is not None:on_cancel()
                    self.select_entry(ResultEntry((),descriptor.label,"field_table",self.field_name(),component,descriptor.unit,descriptor.location))
                    return
            self.app.show_persisted_result(self.field_name(),frame=self.frame.value(),scale=self.scale_value(None),component=component,limits=self.colour_limits(),on_ready=on_ready,on_cancel=on_cancel)
            choice=next((i for i in range(self.quantities.count()) if tuple(self.quantities.itemData(i))==("field",self.field_name())),None)
            if choice is not None:self.quantities.setCurrentIndex(choice)
            self._quantity_component=component
        else:self.app.show_results(on_ready=on_ready,on_cancel=on_cancel)
        self._selected_entry=("field",self.field_name(),self.component.text().strip() or None) if self.use_retained() else ("live_field",self.field_name(),None)
        if self.use_retained() and dataset is not None:
            from ...presentation.result_display import unit_transform
            _,unit=unit_transform(descriptor.unit,self.display_units())
            shape=dataset._header['descriptors'][self.field_name()]['component_shape']
            selected=component or ("translation magnitude" if {"ux","uy","uz"}<=set(descriptor.components) else "component norm" if len(descriptor.components)>1 else "absolute scalar" if len(descriptor.components)==1 and len(shape)>1 else "scalar")
            self.selection_details.setText(f"{descriptor.label} · {selected}\n{unit or 'dimensionless'} · {descriptor.location} · {descriptor.basis} basis")
        else:
            from ...post.fields import field_unit
            from ...presentation.result_display import unit_transform
            _,unit=unit_transform(field_unit(self.field_name()),self.display_units())
            self.selection_details.setText(f"Live result · {self.field_name()}\n{unit or 'dimensionless'}")
        self.refresh_details()
        self.app.result_browser.refresh();self.app.show_result_browser()

    def playback_fps(self):
        import math
        value=float(self.playback_speed.currentText())
        if not math.isfinite(value) or value<=0:
            raise ValueError("playback speed must be a positive number of frames/s")
        return value

    @property
    def playback_active(self):
        return bool((getattr(self,"_playback",None) is not None and self._playback.active)
                    or (getattr(self,"_preparation",None) is not None and self._preparation.active))

    def _playback_input_changed(self,*_):
        if not self._syncing_controls and (self.playback_active or getattr(self,"_preparation",None) is not None):
            self.stop_playback(clear_prepared=True)

    def _sync_frame_interval(self,count):
        owner=(id(self.app.session),self.app.active_job_id,id(self.app.solution),id(self.app.result_datasets.get(self.app.active_job_id)))
        reset=owner!=self._range_owner
        following=self.to_frame.value()==self.to_frame.maximum()
        blockers=[QSignalBlocker(self.from_frame),QSignalBlocker(self.to_frame)]
        for control in (self.from_frame,self.to_frame):control.setRange(1,max(1,count))
        if reset:self.from_frame.setValue(1)
        if reset or following:self.to_frame.setValue(max(1,count))
        self._range_owner=owner
        del blockers

    def _sync_envelope_control(self):
        supported=self.app.solution is not None and not self.use_retained()
        self.envelope.setEnabled(supported)
        if not supported:
            with QSignalBlocker(self.envelope):self.envelope.setChecked(False)

    def animation_frames(self):
        count=self.frame_count()
        if count<=0:raise ValueError("Select a result before playback")
        first,last=self.from_frame.value()-1,self.to_frame.value()-1
        if not 0<=first<=last<count:raise ValueError("Choose From frame <= To frame within the available result frames")
        return tuple(range(first,last+1))

    def _preprocess_context(self):
        return (id(self.app.session),self.app.project.document_id,self.app.active_job_id,
            id(self.app.result_datasets.get(self.app.active_job_id)),getattr(self.app,'_result_source_epoch',0),
            self.app.session.revision,id(self.app.viewport.canvas),self.field_name(),self.component.text(),
            self.scale.text(),self.minimum.text(),self.maximum.text(),self.colors.currentText(),self.units.currentText(),
            self.app.viewport.visualization,self.app._show_attributes.get(),self.app.active_case(),self.animation_frames())

    def _playback_context(self):
        if self.app._closing:raise RuntimeError("workbench is closing")
        # The controller snapshots this after show_results has normalized controls.
        selection=self._display_selection()
        return (selection,id(self.app.viewport.canvas),id(self.app.project),
            self.app.shape_index,self.units.currentText(),self.colors.currentText(),
            self.minimum.text(),self.maximum.text(),self.playback_speed.currentText(),
            getattr(self.app.session,'revision',None))

    def _playback_changed(self):
        playback=getattr(self,"_playback",None)
        if playback is not None and playback.active:
            phase="Loading" if playback.inflight else "Playing"
            self.playback_status.setText(f"{phase} frame {self.frame.value()+1} / {playback.count} (range {playback.first+1}-{playback.last+1})")
        else:self.playback_status.setText("Playback stopped")

    def _stream_frame(self,*,on_ready,on_cancel):
        if self._cached_playback is not None:
            cache=self._cached_playback;index=self.frame.value()
            if cache.context!=self._preprocess_context():on_cancel();return
            dataset=self.app.result_datasets.get(self.app.active_job_id)
            def publish(scene):
                if cache.context!=self._preprocess_context():on_cancel();return
                access=getattr(self.app,'_live_result_access',None)
                from ...presentation.retained_results import prepared_publication_bytes,scalar_buffer_bytes
                prepared=scene._prepared_result_batches
                reservation=None
                if access is not None:
                    reservation=access.reserve_publication(scene.result_field,prepared_publication_bytes(scene.result_field,scene,
                        sum(scalar_buffer_bytes(mesh) for mesh,options in prepared[1])))
                try:
                    if self.app._view_mode!='results':self.app._view_mode='results'
                    self.app.viewport.show(scene)
                    if access is not None:access.commit_publication(reservation)
                except BaseException:
                    if access is not None:access.discard_publication()
                    raise
                self.app._persisted_geometry_key=None;self.app._persisted_frame_layout=None
                self.refresh_details();self.app.result_browser.refresh();on_ready()
            return self.app._request_result(lambda cancel:cache.load(index,cancel),publish,dataset=dataset,on_cancel=on_cancel)
        self._stream_request=True
        try:return self.show_results(on_ready=on_ready,on_cancel=on_cancel)
        finally:self._stream_request=False

    def stop_playback(self,*,clear_prepared=False):
        preparation=getattr(self,'_preparation',None)
        if preparation is not None:
            if preparation.active:self.app.invalidate_result_requests()
            preparation.stop(clear=clear_prepared)
            if clear_prepared and not preparation.active:
                self.preparation_progress.hide()
                self.preparation_status.setText("Prepared frames cleared. Press Play to prepare again.")
        self._cached_playback=None
        playback=getattr(self,"_playback",None)
        if playback is not None:playback.stop()
        else:self.app.viewport.canvas.stop_animation()
        self.playback_status.setText("Playback stopped")

    def play(self):
        fps=self.playback_fps();frames=self.animation_frames()
        if self.envelope.isChecked():raise ValueError("An envelope is a static contour; turn it off before animation")
        if self.preprocess_first.isChecked():
            return self._prepare_playback(frames,fps)
        self.stop_playback()
        self._start_playback(frames,fps)

    def _start_playback(self,frames,fps,cache=None):
        count=self.frame_count();self._cached_playback=cache
        if self._playback is None:
            from .playback import ResultPlayback
            self._playback=ResultPlayback(scheduler=self.app.scheduler,request=self._stream_frame,
                set_frame=self._set_frame_index,context=self._playback_context,
                invalidate=self.app.invalidate_result_requests,stop_canvas=lambda:self.app.viewport.canvas.stop_animation(),
                changed=self._playback_changed,failed=lambda error:self.app.set_status(f"Playback stopped: {error}",error=True))
        self.cancel_pending_display()
        # Clear legacy cached geometry once; streaming never captures snapshots.
        self.app.viewport.canvas.begin_animation_cache()
        self._playback.start(count,fps,frames[0],first=frames[0],last=frames[-1])

    def _preparation_changed(self,message):
        self.preparation_status.show()
        self.preparation_status.setText(message)
        if message=="Preprocessing stopped":self.preparation_progress.hide()

    def _preparation_progress(self,completed,total):
        bar=self.preparation_progress;bar.show();bar.setRange(0,total)
        bar.setValue(completed)
        bar.setFormat("%v / %m frames (%p%)" if total else "Preparing snapshot…")

    def _preparation_ready(self,frames,fps,cache):
        self._preparation_changed(f"Preprocessing complete — {len(frames)} frames ready for playback.")
        self._start_playback(frames,fps,cache)

    def _preparation_failed(self,error):
        self.preparation_progress.hide()
        self._preparation_changed(f"Preprocessing failed: {error}")
        self.app.set_status(f"Preprocessing stopped: {error}",error=True)

    def _prepare_playback(self,frames,fps):
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        if dataset is None or not self.use_retained():raise ValueError("Select a retained field before preprocessing")
        if self.app.viewport.active_backend!='gpu':raise ValueError("Preprocessed playback requires a GPU renderer")
        from ...presentation.frame_updates import snapshot
        view=snapshot(self.app.viewport)
        if view is None:raise ValueError("This custom viewer does not support frame preprocessing")
        context=self._preprocess_context()
        fps_interval=1000/float(fps)
        if not math.isfinite(fps_interval) or round(fps_interval)>2147483647:
            raise ValueError('playback interval exceeds the Qt timer limit')
        self.stop_playback()
        if self._preparation is not None and self._preparation.cache is not None and self._preparation.cache.context==context:
            self._preparation_progress(len(frames),len(frames))
            self._preparation_ready(frames,fps,self._preparation.cache);return
        if self._preparation is not None:self._preparation.stop(clear=True)
        from .preprocessing import FramePreparation
        self._preparation=FramePreparation(self.app.scheduler,self._preprocess_context,
            self._preparation_changed,lambda cache:self._preparation_ready(frames,fps,cache),
            self._preparation_failed,progress=self._preparation_progress)
        preparation=self._preparation;token,directory=preparation.begin()
        project=self.app.project;job=self.app.project.jobs.get(self.app.active_job_id)
        stale=job is not None and self.app._job_is_stale(job)
        options=dict(field=self.field_name(),component=self.component.text() or None,scale=self.scale_value(None),
            limits=self.colour_limits(),colormap=self.colormap(),units=self.display_units(),
            show_nodes=self.show_result_nodes(),show_beam_sections=self.app.viewport.visualization.show_beam_sections and not stale,
            attributes=bool(self.app._show_attributes.get() and not stale),case=self.app.active_case(),
            attribute_options=dict(show_supports=self.show_result_supports(),show_loads=self.show_result_loads(),
                show_masses=self.show_result_masses(),show_imperfections=self.show_imperfect_reference()))
        policy=dict(visualization=view.visualization,commercial=view._commercial_selection)
        mesh=[None]
        def request(cancel):
            from ...presentation.frame_cache import write_request
            mesh[0]=self.app.retained_result_mesh(self.app.active_job_id)
            return write_request(directory,project,mesh[0],dataset,policy,options,frames,cancel)
        def ready(path):
            if path is not None:preparation.launch(token,path,mesh[0],frames)
        self.app._request_result(request,ready,dataset=dataset,on_cancel=lambda:preparation.stop(clear=True))



class SectioningTask(QWidget):
    """Display clipping controls, sharing the saved-presentation controller."""
    def __init__(self,app,controls):
        super().__init__();self.app=app;self.presentation_controls=controls
        layout=QVBoxLayout(self);layout.addWidget(controls.sectioning_controls)
        self.plane=controls.plane;self.offset=controls.offset

    def refresh(self):pass


class VisualizationTask(QWidget):
    def __init__(self,app):
        super().__init__();self.app=app
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        from ...presentation.visualization import VISUAL_PRESETS
        self.preset=QComboBox();self.preset.addItems(VISUAL_PRESETS)
        preset_row=QWidget();preset_layout=QHBoxLayout(preset_row);preset_layout.setContentsMargins(0,0,0,0)
        preset_layout.addWidget(self.preset)
        preset_apply=QPushButton('Apply preset');preset_apply.clicked.connect(app.guarded(self.apply_preset));preset_layout.addWidget(preset_apply)
        form.addRow('Visual preset',preset_row)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        note=QLabel("Appearance applies immediately to the current CAD, mesh or result view.\nShaded hides surface outlines; model lines and beam axes have a separate switch.")
        note.setWordWrap(True);form.addRow(note)
        self.render=QComboBox();self.render.addItems(["Shaded with edges","Shaded","Wireframe"]);form.addRow("Render mode",self.render)
        self.render.setToolTip("Shaded: filled surfaces. Shaded with edges: filled surfaces and their outlines. Wireframe: outlines only.")
        self.opacity=QLineEdit("1");form.addRow("Surface opacity (0 to 1)",self.opacity)
        self.opacity.setToolTip("0 is transparent; 1 is opaque. Applies when you finish editing. Does not change lines, loads or supports.")
        self.transparency=QSlider(Qt.Horizontal);self.transparency.setRange(0,100)
        self.transparency.setTracking(False)
        self.transparency.setToolTip("Surface transparency: 0% opaque, 100% transparent. Applies when the slider is released. Model lines and beam axes remain controlled separately.")
        self.transparency_value=QLabel("0%")
        slider_row=QWidget();slider_layout=QVBoxLayout(slider_row);slider_layout.setContentsMargins(0,0,0,0)
        slider_layout.addWidget(self.transparency);slider_layout.addWidget(self.transparency_value)
        form.addRow("Transparency",slider_row)
        self.appearance={}
        from ...presentation.visualization import GEOMETRY_DETAILS
        ordered=sorted(fields(VisualizationStyle),key=lambda field:0 if field.name in {"show_model_lines","show_beam_sections","show_mesh_nodes"} else 1)
        labels={"show_model_lines":"Show model lines / beam axes","show_beam_sections":"Show beam section shapes",
                "show_mesh_nodes":"Show mesh nodes",
                "edge_color":"Surface edge colour","edge_width":"Surface edge width",
                "ao_strength":"AO strength","ao_radius":"AO radius (px)"}
        help_text={"show_model_lines":"Show or hide CAD curves and beam centre lines. Surface outlines, beam section shapes, loads and supports are separate.",
                   "ambient_occlusion":"WebGPU contact shading: Low uses half resolution, High uses full resolution and refines after camera movement. Numerical result contours always bypass this shading to preserve their colours.",
                   "ao_strength":"Ambient occlusion strength, from 0 (no shading) to 2.",
                   "ao_radius":"Ambient occlusion radius in screen pixels (1 to 256). Transparent surfaces do not cast this shading.",
                   "antialiasing":"WebGPU: 4x multisampling smooths silhouettes and lines. Off reduces GPU memory and work.",
                   "lighting":"Studio uses a soft camera-following key light and ambient fill. Flat preserves unshaded contour colours.",
                   "background_gradient":"WebGPU: use a subtle vertical background gradient.",
                   "feature_edges":"WebGPU: reduce surface tessellation lines, retaining boundaries and creases above 30 degrees. Explicit lines and result mesh outlines remain available; selection and visibility restore full outlines.",
                   "show_mesh_nodes":"Show node dots in mesh and inspection views. Enable to pick nodes in the viewport. Result nodes have a separate setting.",
                   "show_beam_sections":"Draw the physical cross-section shapes of beams. Turn off for a simpler model; their axes use the model-lines switch.",
                   "geometry_detail":"Display tessellation of curved CAD surfaces only. Does not change the engineering mesh.",
                   "edge_color":"Colour of surface outlines in Shaded with edges or Wireframe. Does not recolour model lines.",
                   "edge_width":"Surface outline width in pixels. Does not change model lines."}
        for field in ordered:
            if field.name in {"render_mode","surface_opacity"}:continue
            value=getattr(app.viewport.visualization,field.name)
            if isinstance(value,bool):
                widget=QCheckBox();widget.setChecked(value)
            elif field.name in {"geometry_detail","result_colormap","antialiasing","lighting","ambient_occlusion"}:
                choices={'geometry_detail':GEOMETRY_DETAILS,'result_colormap':list(RESULT_COLORMAPS),
                    'antialiasing':('Off','4x'),'lighting':('Flat','Studio'),'ambient_occlusion':('Off','Low','High')}
                widget=QComboBox();widget.addItems(choices[field.name]);widget.setCurrentText(value)
            else:widget=QLineEdit(str(value))
            widget.setToolTip(help_text.get(field.name,field.name.replace("_"," ").capitalize()+" appearance setting."))
            self.appearance[field.name]=widget
            label=labels.get(field.name,field.name.replace("_"," ").capitalize())
            if field.name in {"background","edge_color"}:
                row=QWidget();row_layout=QHBoxLayout(row);row_layout.setContentsMargins(0,0,0,0);row_layout.setSpacing(6)
                row_layout.addWidget(widget,1)
                button=QPushButton("Choose colour");button.clicked.connect(lambda checked=False,widget=widget:self.choose_colour(widget));row_layout.addWidget(button)
                form.addRow(label,row)
            else:form.addRow(label,widget)
        from .widgets import section_label
        def row(*buttons):
            line=QHBoxLayout();line.setSpacing(6)
            for button in buttons:line.addWidget(button)
            layout.addLayout(line)
        apply=QPushButton("Apply appearance");apply.setProperty("primary",True);apply.clicked.connect(app.guarded(self.apply))
        reset=QPushButton("Reset appearance");reset.clicked.connect(app.guarded(self.reset))
        row(apply,reset)
        sync=QPushButton("Load viewport appearance");sync.clicked.connect(self.sync_from_viewport);layout.addWidget(sync)
        layout.addWidget(section_label("Image"))
        capture=QPushButton("Save viewport image");capture.clicked.connect(app.guarded(self.capture));layout.addWidget(capture);layout.addStretch()
        from .presentation_controls import PresentationControls
        self.presentation_controls=PresentationControls(app)
        layout.addWidget(self.presentation_controls)
        self.plane=self.presentation_controls.plane;self.offset=self.presentation_controls.offset
        self.sync_from_viewport()
        self.render.currentTextChanged.connect(app.guarded(self.apply))
        self.opacity.editingFinished.connect(app.guarded(self.apply))
        self.transparency.valueChanged.connect(self._transparency_changed)
        for widget in self.appearance.values():
            signal=widget.toggled if isinstance(widget,QCheckBox) else widget.currentTextChanged if isinstance(widget,QComboBox) else widget.editingFinished
            signal.connect(app.guarded(self.apply))

    def _transparency_changed(self,value):
        self.opacity.setText(f"{1-value/100:.2f}")
        self.transparency_value.setText(f"{value}%")
        self.app.guarded(self.apply)()

    def apply_preset(self):
        from ...presentation.visualization import visual_preset
        self.app.viewport.set_visualization(visual_preset(self.preset.currentText(),self.app.viewport.visualization))
        self.sync_from_viewport()
        self.app.set_status(f'{self.preset.currentText()} visual preset applied')
        controls=getattr(self,'presentation_controls',None)
        if controls is not None and hasattr(getattr(self.app,'session',None),'transaction'):controls.save_workspace()

    def apply(self):
        values={}
        for name,widget in self.appearance.items():
            old=getattr(self.app.viewport.visualization,name)
            values[name]=widget.isChecked() if isinstance(widget,QCheckBox) else widget.currentText() if isinstance(widget,QComboBox) else int(widget.text()) if isinstance(old,int) else float(widget.text()) if isinstance(old,float) else widget.text()
        old_style=self.app.viewport.visualization
        style=replace(old_style,render_mode=self.render.currentText(),surface_opacity=float(self.opacity.text()),**values)
        for label,colour in (("background",style.background),("surface edge",style.edge_color)):
            if not QColor(colour).isValid():raise ValueError(f"invalid {label} colour {colour!r}")
        self.app.viewport.set_visualization(style)
        results=self.app.panels.get("Results")
        if results is not None and old_style.result_colormap!=style.result_colormap:
            with QSignalBlocker(results.colors):results.colors.setCurrentText(style.result_colormap)
        with QSignalBlocker(self.transparency):self.transparency.setValue(round(100*(1-style.surface_opacity)))
        self.transparency_value.setText(f"{self.transparency.value()}%")
        mode=self.app._view_mode
        geometry_changed=any(getattr(old_style,key)!=getattr(style,key) for key in ("show_beam_sections","geometry_detail"))
        if geometry_changed and mode=="geometry":self.app.show_geometry()
        elif (geometry_changed or old_style.show_mesh_nodes!=style.show_mesh_nodes) and mode=="mesh":self.app.show_mesh()
        elif (geometry_changed or old_style.show_mesh_nodes!=style.show_mesh_nodes) and mode=="inspection_mesh":self.app.show_inspection_mesh()
        elif mode=="results" and results is not None and style!=old_style:
            scene_fields=("result_colormap","show_result_nodes","show_result_supports","show_result_loads","show_result_masses","show_imperfect_reference","show_beam_sections")
            if any(getattr(old_style,key)!=getattr(style,key) for key in scene_fields if key!='result_colormap'):results.show_results()
        controls=getattr(self,'presentation_controls',None)
        if controls is not None and hasattr(getattr(self.app,'session',None),'transaction') and style!=old_style:controls.save_workspace()

    def choose_colour(self,widget):
        colour=QColorDialog.getColor(QColor(widget.text()),self,"Viewport colour")
        if colour.isValid():
            widget.setText(colour.name());self.app.guarded(self.apply)()

    def reset(self):
        self.sync_from_viewport(VisualizationStyle());self.apply()

    def sync_from_viewport(self,style=None):
        style=self.app.viewport.visualization if not isinstance(style,VisualizationStyle) else style
        blockers=[QSignalBlocker(widget) for widget in (self.render,self.opacity,self.transparency,*self.appearance.values())]
        self.render.setCurrentText(style.render_mode);self.opacity.setText(str(style.surface_opacity))
        self.transparency.setValue(round(100*(1-style.surface_opacity)));self.transparency_value.setText(f"{self.transparency.value()}%")
        for name,widget in self.appearance.items():
            value=getattr(style,name)
            if isinstance(widget,QCheckBox):widget.setChecked(value)
            elif isinstance(widget,QComboBox):widget.setCurrentText(value)
            else:widget.setText(str(value))

    def section(self):
        self.presentation_controls.apply_plane()

    def capture(self):
        path=self.app.dialogs.save_file(filetypes=[("PNG image","*.png")],defaultextension=".png")
        if path:self.app.viewport.capture_png(path)

    def refresh(self):pass
