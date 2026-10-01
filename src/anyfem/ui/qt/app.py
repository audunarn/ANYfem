"""PySide6 workbench consuming the shared application and viewer contracts."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QAction, QStandardItemModel, QStandardItem
from PySide6.QtWidgets import (QApplication, QComboBox, QDockWidget, QMainWindow,
    QPlainTextEdit, QPushButton, QTabWidget, QTableView, QVBoxLayout, QWidget,
    QInputDialog, QLabel, QCheckBox, QScrollArea, QMenu)

from ...application.controller import WorkbenchController, default_project
from ...application.workflow import WorkbenchWorkflow
from ...application.worker import JobWorkerFacade
from ...diagnostics import ErrorDiagnostic
from ...scripting import ScriptRunner
from .ports import QtSchedulerPort, QtDialogPort, QtClipboardPort, QtStatusPort
from .viewport import QtViewport
from .editors import CommandEditor,GeometryTask
from .tree import QtModelTree
from .tasks import MeshTask, SolveTask, ResultsTask, VisualizationTask


class _Value:
    def __init__(self,value):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value


class _Workspace(QTabWidget):
    def __init__(self):
        super().__init__();self.panels={};self.setUsesScrollButtons(True)
    def select(self,page):
        panel=self.panels[page] if isinstance(page,str) else page
        for index in range(self.count()):
            widget=self.widget(index)
            if widget is panel or isinstance(widget,QScrollArea) and widget.widget() is panel:self.setCurrentIndex(index);return

    def addTab(self,panel,name):
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(panel)
        return super().addTab(scroll,name)
    def set_hint(self,text):self.setToolTip(text)


class _JobView(QTableView):
    def __init__(self,app):
        super().__init__(app);self.app=app
        self._model=QStandardItemModel()
        self._model.setHorizontalHeaderLabels(["Job","Status","Inputs"])
        self.setModel(self._model)
        self.setEditTriggers(self.EditTrigger.NoEditTriggers)
        self._rows={};self.doubleClicked.connect(self.open_result)
        self.setContextMenuPolicy(Qt.CustomContextMenu);self.customContextMenuRequested.connect(self.context_menu)

    def refresh(self):
        for key in tuple(self._rows):
            if key not in self.app.project.jobs:self._model.removeRow(self._rows.pop(key)[0].row())
        for job in self.app.project.jobs.values():
            values=[str(job.name),str(getattr(job.status,"value",job.status)),"stale" if self.app._job_is_stale(job) else "current"]
            if job.id not in self._rows:
                items=[QStandardItem(value) for value in values];items[0].setData(job.id,Qt.UserRole)
                self._model.appendRow(items);self._rows[job.id]=items
            else:
                for item,value in zip(self._rows[job.id],values):
                    if item.text()!=value:item.setText(value)
        self.resizeColumnsToContents()

    def open_result(self,index):
        job_id=index.siblingAtColumn(0).data(Qt.UserRole)
        if job_id:self.app.guarded(lambda:self.app.panels["Results"].activate_job(job_id))()

    def context_menu(self,position):
        index=self.indexAt(position)
        if not index.isValid():return
        job_id=index.siblingAtColumn(0).data(Qt.UserRole);menu=QMenu(self)
        menu.addAction("Open retained result",lambda:self.open_result(index))
        menu.addAction("Cancel job",lambda:self.app.guarded(lambda:self.app.job_manager.cancel(job_id))())
        menu.addAction("Inspect submitted inputs / diagnostics",lambda:self.inspect_job(job_id))
        menu.addAction("Delete job",lambda:self.app.guarded(lambda:self.app._delete_tree_items((f"job:{job_id}",)))())
        menu.exec(self.viewport().mapToGlobal(position))

    def inspect_job(self,job_id):
        import json
        job=self.app.project.jobs[job_id]
        report=self.app.submitted_input_reports.get(job_id,"")
        self.app.panels["Solve"].transcript.setPlainText(str(report)+"\n"+json.dumps(job.diagnostics,indent=2,default=str))
        self.app.notebook.select("Solve")


class ScriptTask(QWidget):
    def __init__(self,app):
        super().__init__();self.app=app;self.runner=None;self.task=None
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel("Scripts run against a snapshot and commit as one undoable edit."))
        self.source=QPlainTextEdit("# Example: commands.run(commands.AddPoint(1, 0, 0))\n")
        layout.addWidget(self.source)
        run=QPushButton("Run script");run.clicked.connect(app.guarded(self.start));layout.addWidget(run)
        cancel=QPushButton("Cancel script");cancel.clicked.connect(self.cancel);layout.addWidget(cancel)
        self.output=QPlainTextEdit();self.output.setReadOnly(True);layout.addWidget(self.output)
        self.recording=QCheckBox("Record GUI commands");self.recording.setChecked(True);layout.addWidget(self.recording)
        self.command_stream=QPlainTextEdit();self.command_stream.setReadOnly(True);layout.addWidget(self.command_stream)
        for label,callback in [("Copy commands to editor",self.copy_commands_to_editor),("Clear commands",self.command_stream.clear),("Clear output",self.output.clear),("Copy diagnosis",self.copy_diagnosis)]:
            button=QPushButton(label);button.clicked.connect(callback);layout.addWidget(button)
        action=QAction("Run script",self);action.setShortcut("Ctrl+Return");action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        action.triggered.connect(app.guarded(self.start));self.addAction(action)
        self._observed_stack=None;self._observed_session=None;self._expected_revision_label=None
        self.refresh()

    def start(self):
        if self.task is not None:raise ValueError("A script is already running")
        if self.runner is None:self.runner=ScriptRunner(self.app.session)
        self.task=self.runner.submit(self.source.toPlainText())
        self.app.scheduler.call_later(50,self.poll)

    def poll(self):
        if self.task is None or self.app._closing:return
        if not self.task.done():
            self.app.scheduler.call_later(50,self.poll);return
        task=self.task;self.task=None
        try:
            result=task.result()
            outcome=self.runner.commit(result)
            self.output.appendPlainText(outcome.stdout+outcome.stderr)
            if outcome.return_value is not None:self.output.appendPlainText(f"result = {outcome.return_value!r}")
            if outcome.committed:
                self.output.appendPlainText("Applied as one undo item.")
                if outcome.meshes_changed:self.app.mesh=self.app.session.mesh_cache.get("active")
            self.app.refresh_all()
        except Exception as error:
            self.output.appendPlainText(getattr(error,"stdout","")+getattr(error,"stderr","")+getattr(error,"traceback_text","")+f"Rejected: {error}")
            self.app.set_status(str(error),error=True)

    def cancel(self):
        if self.task is not None:self.task.cancel()

    def reset(self):
        self.cancel()
        if self.runner is not None:self.runner.shutdown(wait=False)
        self.runner=None;self.task=None
        self.detach()

    def detach(self):
        if self._observed_stack is not None:self._observed_stack.remove_action_listener(self._on_command_event)
        if self._observed_session is not None:self._observed_session.remove_listener(self._on_revision_event)
        self._observed_stack=None;self._observed_session=None;self._expected_revision_label=None

    def refresh(self):
        if self._observed_stack is self.app.commands and self._observed_session is self.app.session:return
        self.detach();self._observed_stack=self.app.commands;self._observed_session=self.app.session
        self._observed_stack.add_action_listener(self._on_command_event)
        self._observed_session.add_listener(self._on_revision_event)

    def _on_command_event(self,event):
        from ...command_recording import command_event_to_python
        self._expected_revision_label={"run":event.command.label,"undo":f"undo {event.command.label}","redo":f"redo {event.command.label}"}.get(event.action)
        if self.recording.isChecked():self.command_stream.appendPlainText(command_event_to_python(event))

    def _on_revision_event(self,revision):
        label=str(getattr(revision,"label","edit"))
        if label!=self._expected_revision_label and self.recording.isChecked():self.command_stream.appendPlainText(f"# GUI transaction: {label}")
        self._expected_revision_label=None

    def copy_commands_to_editor(self):
        self.source.appendPlainText(self.command_stream.toPlainText())

    def copy_diagnosis(self):
        self.app.clipboard.copy_text(self.app.diagnostic_report(recent_commands=self.command_stream.toPlainText().splitlines()[-50:]))


class QtFemWindow(WorkbenchWorkflow, QMainWindow):
    """One window, one workbench controller, no Tk runtime."""
    def __init__(self, project=None, *, viewer_backend="auto"):
        QMainWindow.__init__(self)
        self.workbench=WorkbenchController(project if project is not None else default_project())
        self.scheduler=QtSchedulerPort(self)
        self.dialogs=QtDialogPort(self)
        self.clipboard=QtClipboardPort()
        self._status_port=QtStatusPort(self.set_status)
        self.initialize_workflow()
        self._viewer_backend=viewer_backend
        self._show_attributes=_Value(True)
        self._status=_Value("")
        self._refreshing=False
        self._refresh_pending=False
        self.resize(1400,900)
        self.viewport=QtViewport(self,self.selection,backend=viewer_backend)
        self.setCentralWidget(self.viewport.widget)
        self.viewport.set_pick_handler(self._on_pick)
        self.tree=QtModelTree(self)
        model_dock=self._dock("Model",self.tree.panel(),Qt.LeftDockWidgetArea)
        self.details=self.notebook=_Workspace()
        from .construction import ConstructionPanel
        from .definitions import DefinitionsTask
        from .generators import GeneratorTask
        from .engineering import SectionTask,LoadTask
        self.panels={
            "Geometry":GeometryTask(self,["AddPoint","MovePoint","AddLine","AddPlate","AddFace","AddArc","AddPolyline","AddFeature","AddCylinder","AddCone","AddStiffenedPanel","AddSketch","EditFeature","SuppressFeature","DeleteFeature","DeleteEntity","CopyEntities","LinearPattern","CircularPattern","MirrorEntities","Extrude","Revolve","ReverseEntity","SplitEdge","SplitFace","StripFace","SetFaceCorners","JoinSheet","FragmentPlateOverlaps","TriangleToQuads","ButterflyHoleDecomposition","NeutralTrimHole","MeasureGeometry"]),
            "Construction":ConstructionPanel(self),
            "Generators":GeneratorTask(self),
            "Sections":SectionTask(self),
            "Loads & BC":LoadTask(self),
            "Mesh":MeshTask(self),"Solve":SolveTask(self),"Results":ResultsTask(self),
            "Visualization":VisualizationTask(self),"Scripts":ScriptTask(self),
            "Definitions":DefinitionsTask(self),
        }
        self.details.panels=self.panels
        for name,panel in self.panels.items():self.details.addTab(panel,name)
        tasks_dock=self._dock("Tasks",self.details,Qt.RightDockWidgetArea)
        self.job_status=_JobView(self)
        jobs_dock=self._dock("Jobs",self.job_status,Qt.BottomDockWidgetArea)
        self.log=QPlainTextEdit();self.log.setReadOnly(True)
        messages_dock=self._dock("Messages",self.log,Qt.BottomDockWidgetArea)
        self.resizeDocks([model_dock,tasks_dock],[220,360],Qt.Horizontal)
        self.resizeDocks([jobs_dock,messages_dock],[140,140],Qt.Vertical)
        self._actions={}
        self._menus()
        self.worker=JobWorkerFacade(self.job_manager)
        self.commands.add_listener(self.refresh_all)
        self.session.add_listener(self._on_revision_changed)
        self.selection.add_listener(self._on_selection_changed)
        self.workbench.add_listener(self._on_workbench_event)
        self._job_poll=self.scheduler.call_later(self.worker.POLL_MS,self._poll_jobs)
        self.refresh_all()
        self.show_geometry(reset_view=True)
        settings=QSettings("ANY", "ANYfem-Qt")
        if settings.contains("geometry"):self.restoreGeometry(settings.value("geometry"))
        if settings.contains("docks"):self.restoreState(settings.value("docks"))

    def _dock(self,title,widget,area):
        dock=QDockWidget(title,self);dock.setObjectName(title)
        dock.setWidget(widget);self.addDockWidget(area,dock)
        return dock

    def _action(self,menu,name,callback,shortcut=None):
        action=QAction(name,self)
        if shortcut:action.setShortcut(shortcut)
        action.triggered.connect(lambda _checked=False:self.guarded(callback)())
        menu.addAction(action);self._actions[name]=action
        return action

    def _menus(self):
        file=self.menuBar().addMenu("File")
        self._action(file,"New",lambda:self.new_project(confirm=True),"Ctrl+N")
        self._action(file,"Open",lambda:self.open_project(confirm=True),"Ctrl+O")
        self._recent_menu=file.addMenu("Recent projects");self._refresh_recent_menu()
        self._action(file,"Save",self.save_project,"Ctrl+S")
        self._action(file,"Save as",lambda:self.save_project(ask=True),"Ctrl+Shift+S")
        self._action(file,"Recover autosave",self.recover_autosave)
        self._action(file,"Open file inspector",self.open_file_inspector)
        self._action(file,"Import SESAM model",self.import_sesam_model)
        self._action(file,"Import CalculiX results",self.import_calculix_result)
        self._action(file,"Import SESAM results",self.import_sesam_result)
        self._action(file,"Export CalculiX deck",self.export_deck)
        self._action(file,"Close",self.close,"Ctrl+Q")
        edit=self.menuBar().addMenu("Edit")
        self._action(edit,"Undo",self.undo,"Ctrl+Z")
        self._action(edit,"Redo",self.redo,"Ctrl+Y")
        self._action(edit,"Select all",self._select_all,"Ctrl+A")
        self._action(edit,"Delete selected",self._delete_selection,"Delete")
        self._action(edit,"Commands",self.show_command_palette,"Ctrl+P")
        view=self.menuBar().addMenu("View")
        for name,callback in [("Geometry",self.show_geometry),("Mesh",self.show_mesh),("Results",self.panels["Results"].show_results),("Fit",self.viewport.fit)]:
            self._action(view,name,callback)
        self._action(view,"Frame selection",self.viewport.frame_selection,"F")
        attributes=self._action(view,"Attributes / imperfections",lambda:self._set_show_attributes(attributes.isChecked()))
        attributes.setCheckable(True);attributes.setChecked(self._show_attributes.get())
        for name in ["iso","top","front","side"]:
            self._action(view,name.capitalize(),lambda name=name:self.viewport.set_view(name))
        view.addSeparator()
        for dock in self.findChildren(QDockWidget):view.addAction(dock.toggleViewAction())
        toolbar=self.addToolBar("Workbench");toolbar.setObjectName("Workbench")
        for name in ["New","Open","Save","Undo","Redo","Fit"]:toolbar.addAction(self._actions[name])
        self.selection_mode=QComboBox();self.selection_mode.addItems(["vertex","edge","face","node","element"])
        self.selection_mode.currentTextChanged.connect(self.selection.set_mode);toolbar.addWidget(self.selection_mode)
        self.selection_tool=QComboBox();self.selection_tool.addItems(["box","single","lasso"])
        self.selection_depth=QComboBox();self.selection_depth.addItems(["visible","through"])
        self.selection_operation=QComboBox();self.selection_operation.addItems(["replace","add","remove","toggle"])
        for control in (self.selection_tool,self.selection_depth,self.selection_operation):
            control.currentTextChanged.connect(self._selection_policy);toolbar.addWidget(control)
        self.renderer=QComboBox();self.renderer.addItems(["auto","gpu","software"]);self.renderer.setCurrentText(self._viewer_backend)
        self.renderer.currentTextChanged.connect(lambda backend:self.guarded(lambda:self.switch_viewer_backend(backend))());toolbar.addWidget(self.renderer)

    def _selection_policy(self,*_):
        self.viewport.configure_selection(tool=self.selection_tool.currentText(),
            depth=self.selection_depth.currentText(),operation=self.selection_operation.currentText())

    def _set_show_attributes(self,visible):
        self._show_attributes.set(bool(visible))
        self.refresh_views()

    def refresh_views(self):
        if self._closing:return
        if self._view_mode=="results" and self.solution is None and self.active_job_id in self.result_datasets:
            self.panels["Results"].show_results()
        else:super().refresh_views()

    def refresh_all(self):
        if self._closing or self._refresh_suspended or self._refreshing:return
        self._refreshing=True
        try:
            self.tree.refresh();self.refresh_panels();self.job_status.refresh()
            self._actions["Undo"].setEnabled(self.commands.can_undo and not self.session.read_only)
            self._actions["Redo"].setEnabled(self.commands.can_redo and not self.session.read_only)
            self._update_window_title()
        finally:self._refreshing=False

    def refresh_panels(self):
        for panel in self.panels.values():panel.refresh()

    def _on_workbench_event(self,event):
        if self._closing or self._refresh_pending:return
        self._refresh_pending=True
        def refresh():
            self._refresh_pending=False
            self.refresh_all()
        self.scheduler.call_later(0,refresh)

    def _on_selection_changed(self):
        if self._closing:return
        if getattr(self.selection.domain,"value","geometry")=="geometry":
            self._geometry_selection_mode=self.selection.mode
        self.tree.sync_from_selection()
        self.statusBar().showMessage(f"{len(self.selection.items)} selected · {self._status.get()}")

    def _update_window_title(self):
        self.setWindowTitle(f"{'*' if self.session.dirty else ''}{self.project.name} — ANYfem (Qt){' [read-only]' if self.session.read_only else ''}")

    def _update_view_label(self):pass

    def _refresh_recent_menu(self):
        menu=getattr(self,"_recent_menu",None)
        if menu is None:return
        menu.clear()
        for path in self._recent_paths:
            action=menu.addAction(path)
            action.triggered.connect(lambda _checked=False,path=path:self.guarded(lambda:self.open_project(path,confirm=True))())
        if not self._recent_paths:menu.addAction("No recent projects").setEnabled(False)

    def open_file_inspector(self,path=None):
        from .inspector import FileInspector
        inspector=FileInspector(self,path);inspector.show();return inspector

    def set_status(self,text,*,error=False,diagnostic=None):
        self._status.set(text)
        self.statusBar().showMessage(text)
        if hasattr(self,"log"):self.log.appendPlainText(("Error: " if error else "")+text)
        if error:
            self._error_diagnostics.append(ErrorDiagnostic.capture(text,details=diagnostic,
                project_id=str(getattr(self.project,"document_id","")),view=self._view_mode))
            del self._error_diagnostics[:-10]

    def _set_project(self,*args,**kwargs):
        self.panels["Scripts"].reset()
        self.panels["Results"].reset()
        self.panels["Solve"].reset()
        super()._set_project(*args,**kwargs)
        self.refresh_all()

    def switch_viewer_backend(self,backend):
        try:
            active=self.viewport.switch_backend(backend)
        except BaseException:
            self.renderer.blockSignals(True);self.renderer.setCurrentText(self._viewer_backend);self.renderer.blockSignals(False)
            raise
        self._viewer_backend=backend
        self.set_status(f"Renderer: {active}"+(f" · {'; '.join(self.viewport.backend_diagnostics)}" if self.viewport.backend_diagnostics else ""))
        return active

    def show_command_palette(self):
        choices=list(self._actions)+list(self.panels)
        choice,accepted=QInputDialog.getItem(self,"Commands","Action",choices,editable=False)
        if accepted:
            if choice in self.panels:self.notebook.select(choice)
            else:self._actions[choice].trigger()

    def closeEvent(self,event):
        if self._closing:event.accept();return
        if not self._confirm_discard():event.ignore();return
        settings=QSettings("ANY","ANYfem-Qt")
        settings.setValue("geometry",self.saveGeometry());settings.setValue("docks",self.saveState())
        self.flush_project_writes()
        self._closing=True
        self.panels["Scripts"].reset()
        self.panels["Results"].reset()
        self.scheduler.close()
        self.worker.stop();self.workbench.close()
        self.workbench.remove_listener(self._on_workbench_event)
        self.commands.remove_listener(self.refresh_all)
        self.session.remove_listener(self._on_revision_changed)
        self.selection.remove_listener(self._on_selection_changed)
        self.viewport.close()
        if self._project_lock is not None:self._project_lock.release();self._project_lock=None
        self._recovery_pending=None
        self._recovery_executor.shutdown(wait=False,cancel_futures=True)
        self._artifact_executor.shutdown(wait=False,cancel_futures=True)
        event.accept()


def main():
    import sys
    app=QApplication.instance() or QApplication(sys.argv)
    window=QtFemWindow()
    window.show()
    return app.exec()
