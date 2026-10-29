"""PySide6 workbench consuming the shared application and viewer contracts."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt, QSettings, QSize, QObject, QEvent, QPoint, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QStandardItemModel, QStandardItem
from PySide6.QtWidgets import (QApplication, QComboBox, QDockWidget, QHBoxLayout, QMainWindow,
    QPlainTextEdit, QPushButton, QTabWidget, QTabBar, QTableView, QVBoxLayout, QWidget,
    QInputDialog, QLabel, QCheckBox, QScrollArea, QMenu, QStyle, QToolButton)
from shiboken6 import isValid

from ...application.controller import WorkbenchController, default_project
from ...application.workflow import WorkbenchWorkflow,_job_progress_text
from ...application.worker import JobWorkerFacade
from ...diagnostics import ErrorDiagnostic
from ...scripting import ScriptRunner
from .ports import QtSchedulerPort, QtDialogPort, QtClipboardPort, QtStatusPort
from .runtime_diagnostics import extend_report_with_qt_runtime
from .viewport import QtViewport
from .editors import CommandEditor,GeometryTask
from .tree import QtModelTree
from .tasks import MeshTask, SolveTask, ResultsTask, VisualizationTask, SectioningTask, ExportTask


def _worker_process_job_manager(project):
    """Solves run in a separate worker process so the window stays responsive.

    A solver thread shares the GUI's interpreter lock and froze the window for seconds
    at a time during long runs (see anyfem.job_process).  ``ANYFEM_JOB_EXECUTOR=thread``
    runs jobs in-process instead, for diagnosis.
    """
    import os
    from ...jobs import JobManager
    return JobManager(project,executor=os.environ.get("ANYFEM_JOB_EXECUTOR","process"))


def _worker_process_mesh_manager():
    """Meshing runs in a worker process too: the mesh thread alone froze the window for ~10 s
    after pressing Generate mesh on the barge example (same cause as for solves)."""
    import os
    from ...mesh_jobs import MeshTaskManager
    return MeshTaskManager(executor=os.environ.get("ANYFEM_JOB_EXECUTOR","process"))


class _Value:
    def __init__(self,value):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value


class _DockFooter(QObject):
    """Share the native bottom tab row without taking ownership of its tab bar."""

    EVENTS=frozenset({QEvent.Show,QEvent.Hide,QEvent.Resize,QEvent.Move,QEvent.LayoutRequest,QEvent.ChildAdded})

    def __init__(self,window,status):
        super().__init__(window)
        self.window=window;self.status=status;self.tab_bar=None
        self._busy=False;self.in_tab_row=False;self._watched=set()
        status.setObjectName("DockFooter")
        status.setSizeGripEnabled(False)
        # Filtered events arrive while Qt is building, reparenting or destroying children:
        # ChildAdded is sent from inside the child's own constructor.  Walking the window's
        # children from that dispatch wraps half-built objects (the hosted Windows PySide 6.10
        # lane aborted there), so events only request one coalesced sync from the event loop.
        self._timer=QTimer(self);self._timer.setSingleShot(True);self._timer.setInterval(0)
        self._timer.timeout.connect(self.sync)
        window.installEventFilter(self)
        self.sync()

    def eventFilter(self,watched,event):
        if event.type() in self.EVENTS:self.schedule()
        return False

    def schedule(self):
        if self._busy or getattr(self.window,"_closing",True) or not isValid(self._timer):return
        if not self._timer.isActive():self._timer.start()

    def sync(self):
        if self._busy or not isValid(self.window) or self.window._closing:return
        self._busy=True
        try:
            # Qt may destroy and recreate dock tab bars; forget the deleted ones.
            self._watched={bar for bar in self._watched if isValid(bar)}
            if self.tab_bar is not None and not isValid(self.tab_bar):self.tab_bar=None
            names={dock.windowTitle() for dock in self.window._workspace_docks[2:]
                   if not dock.isFloating() and self.window.dockWidgetArea(dock)==Qt.BottomDockWidgetArea}
            bars=[bar for bar in self.window.findChildren(QTabBar)
                  if bar.parentWidget() is self.window]
            for bar in bars:
                if bar not in self._watched:
                    bar.installEventFilter(self);self._watched.add(bar)
            bar=next((bar for bar in bars if bar.isVisible()
                      and any(bar.tabText(i) in names for i in range(bar.count()))),None)
            self.tab_bar=bar
            if bar is None:
                if self.in_tab_row:
                    QMainWindow.setStatusBar(self.window,self.status)
                    self.in_tab_row=False
                self.status.show()
                return
            if not self.in_tab_row:
                # Reparenting removes the status bar from QMainWindow's layout.
                # Keep it owned by the window: Qt can destroy/recreate dock bars.
                self.status.setParent(None)
                self.status.setParent(self.window)
                self.in_tab_row=True
            right=max((bar.tabRect(i).right()+1 for i in range(bar.count())),default=0)
            origin=bar.mapTo(self.window,QPoint(min(right+8,bar.width()),0))
            self.status.setGeometry(origin.x(),origin.y(),max(0,bar.width()-right-8),bar.height())
            self.status.show();self.status.raise_()
        finally:self._busy=False


class _Workspace(QTabWidget):
    def __init__(self):
        super().__init__();self.panels={};self.on_reselect=None;self.setUsesScrollButtons(True)
    def select(self,page):
        panel=self.panels[page] if isinstance(page,str) else page
        for index in range(self.count()):
            widget=self.widget(index)
            if widget is panel or isinstance(widget,QScrollArea) and widget.widget() is panel:
                same=self.currentIndex()==index
                self.setCurrentIndex(index)
                if same and self.on_reselect is not None:self.on_reselect(index)
                return

    def addTab(self,panel,name):
        layout=panel.layout()
        if (isinstance(layout,QVBoxLayout) and not getattr(panel,"FILLS_HEIGHT",False)
                and (layout.count()==0 or layout.itemAt(layout.count()-1).spacerItem() is None)):
            # Keep controls at their natural height and collect spare room at the bottom.
            layout.addStretch()
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
        self.setSelectionBehavior(self.SelectionBehavior.SelectRows)
        self.verticalHeader().hide();self.horizontalHeader().setStretchLastSection(True)
        self._rows={};self.doubleClicked.connect(self.open_result)
        self.setContextMenuPolicy(Qt.CustomContextMenu);self.customContextMenuRequested.connect(self.context_menu)

    def refresh(self):
        # The workbench calls this on every job-poll tick.  Rows depend only on
        # the job records, the committed revision and the active mesh record, so
        # skip the model/column work while none of them has changed.
        signature=(self.app.session.revision.id,getattr(self.app,"mesh_record_id",""),
                   tuple((job.id,str(job.name),str(getattr(job.status,"value",job.status))) for job in self.app.project.jobs.values()))
        if signature==getattr(self,"_signature",None):return
        self._signature=signature
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

    EMPTY_TEXT="No analysis jobs yet. Generate a mesh, then choose Solve. Double-click a finished job to open its results."

    def paintEvent(self,event):
        super().paintEvent(event)
        if self._model.rowCount():return
        from PySide6.QtGui import QPainter,QColor
        painter=QPainter(self.viewport());from .theme import overlay;painter.setPen(QColor(overlay("muted")))
        painter.drawText(self.viewport().rect().adjusted(16,8,-16,-8),Qt.AlignLeft|Qt.AlignVCenter|Qt.TextWordWrap,self.EMPTY_TEXT)
        painter.end()

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
    FILLS_HEIGHT=True
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

    def recent_commands(self):
        stream=self.command_stream;document=getattr(stream,'document',None)
        if callable(document):
            block=document().lastBlock();rows=[]
            while block.isValid() and len(rows)<50:
                rows.append(block.text()[:1000]);block=block.previous()
            return tuple(reversed(rows))
        return tuple(stream.toPlainText().splitlines()[-50:])

    def copy_diagnosis(self):
        return _copy_current_diagnosis(self.app,ScriptTask.recent_commands(self))


def _copy_current_diagnosis(app,recent_commands=()):
    """Both actions copy current state, independent of script-panel history."""
    from ...diagnostics import build_diagnostic_report
    try:
        report=app.diagnostic_report(recent_commands=recent_commands)
        if not isinstance(report,str) or not report.strip():raise ValueError('The report route returned no text')
    except Exception as error:
        report=build_diagnostic_report(getattr(app,'project',None),context={'report_route_error':type(error).__name__+': '+str(error)[:500]})
    app.clipboard.copy_text(report)
    try:app.set_status('Current project diagnosis copied to clipboard.')
    except Exception:pass  # A partial/headless status adapter cannot undo a successful copy.
    return report


class QtFemWindow(WorkbenchWorkflow, QMainWindow):
    """One window, one workbench controller, no Tk runtime."""

    def statusBar(self):
        # The native status-bar slot is empty while the dock tabs host it.
        status=getattr(self,"_footer_statusbar",None)
        return super().statusBar() if status is None else status

    JOB_EVENT_BATCH_LIMIT=100
    # Geometry operations in the order shown, under these headings.
    GEOMETRY_GROUPS=(
        ("Create",("AddPoint","AddLine","AddPolyline","AddArc","AddPlate","AddFace",
                   "AddCylinder","AddCone","AddStiffenedPanel")),
        ("Sketch and planes",("AddSketch","AddConstructionPlane")),
        ("Move, copy and pattern",("MovePoint","CopyEntities","LinearPattern","CircularPattern",
                                   "MirrorEntities","Extrude","Loft","Revolve","ReverseEntity")),
        ("Split and repair",("SplitEdge","SplitFace","StripFace","SetFaceCorners","JoinSheet",
                             "FragmentPlateOverlaps","TriangleToQuads","ButterflyHoleDecomposition","NeutralTrimHole")),
        ("Features and delete",("EditFeature","SuppressFeature","DeleteFeature","DeleteEntity")),
        ("Inspect",("MeasureGeometry",)),
    )
    # Workflow order: model, properties, analysis, tools.
    WORKSPACE_GROUP_STARTS=frozenset({"Sections","Mesh","Scripts"})
    WORKSPACE_SUMMARIES={
        "Geometry":"Create and edit points, lines, plates and features.",
        "Construction":"Sketch profiles on construction planes or flat faces.",
        "Generators":"Create editable parametric structures such as panels and hulls.",
        "Sections":"Assign materials, plate thicknesses and beam profiles.",
        "Loads & BC":"Apply loads, supports, masses and load cases.",
        "Mesh":"Set the element size and generate the finite element mesh.",
        "Solve":"Choose an analysis and run it on the current mesh.",
        "Results":"Inspect, animate and export solved fields.",
        "Visualization":"Adjust appearance, contours and comparison views.",
        "Sectioning":"Inspect inside the model with section planes, section boxes and cut surfaces.",
        "Export":"Capture images and export animations of the selected result frame interval.",
        "Scripts":"Run Python commands as one undoable edit.",
        "Definitions":"Sets, unit profile and other project definitions.",
    }
    def __init__(self, project=None, *, viewer_backend="auto"):
        QMainWindow.__init__(self)
        self.workbench=WorkbenchController(project if project is not None else default_project(),
                                           job_manager_factory=_worker_process_job_manager,
                                           mesh_task_manager_factory=_worker_process_mesh_manager)
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
        self._last_refreshed_revision=None
        from .theme import apply_theme, saved_dark, _state
        _state["dark"]=saved_dark()
        apply_theme(self)
        self.resize(1400,900)
        from ...sketcher import SketchController
        self.sketcher=SketchController()
        self.viewport=QtViewport(self,self.selection,backend=viewer_backend)
        from .theme import sync_viewport
        sync_viewport(self)
        from .wordmark import Wordmark
        self._wordmark=Wordmark(self.viewport.widget)
        self.setCentralWidget(self.viewport.widget)
        self.viewport.set_pick_handler(self._on_pick)
        self.tree=QtModelTree(self)
        from .geometry_actions import GeometryActions
        self.geometry_actions=GeometryActions(self)
        self.left_tabs=QTabWidget()
        model_scroll=QScrollArea();model_scroll.setWidgetResizable(True);model_scroll.setWidget(self.tree.panel())
        self.left_tabs.addTab(model_scroll,"Model")
        model_dock=self._dock("Model",self.left_tabs,Qt.LeftDockWidgetArea)
        self.details=self.notebook=_Workspace()
        from .construction import ConstructionPanel
        from .definitions import DefinitionsTask
        from .generators import GeneratorTask
        from .engineering import SectionTask,LoadTask
        self.panels={
            "Geometry":GeometryTask(self,[name for _,names in self.GEOMETRY_GROUPS for name in names]),
            "Construction":ConstructionPanel(self),
            "Generators":GeneratorTask(self),
            "Sections":SectionTask(self),
            "Loads & BC":LoadTask(self),
            "Mesh":MeshTask(self),"Solve":SolveTask(self),"Results":ResultsTask(self),
            "Visualization":VisualizationTask(self),
        }
        self.panels["Sectioning"]=SectioningTask(self,self.panels["Visualization"].presentation_controls)
        self.panels["Export"]=ExportTask(self,self.panels["Visualization"].presentation_controls)
        self.panels.update({"Scripts":ScriptTask(self),"Definitions":DefinitionsTask(self)})
        from .result_browser import QtResultBrowser
        self.result_browser=QtResultBrowser(self);self.left_tabs.addTab(self.result_browser,"Results")
        from .navigation import install_option_icons,ActionIcons
        selectors={"Geometry":"choice","Construction":"mode","Generators":"kind","Sections":"choice",
                   "Loads & BC":"choice","Mesh":"strategy","Solve":"analysis","Results":"field",
                   "Definitions":"choice"}
        for name,attribute in selectors.items():
            install_option_icons(self.panels[name],getattr(self.panels[name],attribute),
                                 groups=self.GEOMETRY_GROUPS if name=="Geometry" else None)
        # The grouped palette is tall: keep the chosen operation's fields and Apply directly
        # under the operation list, with the palette below them.
        geometry=self.panels["Geometry"];palette=geometry.option_palette;layout=geometry.layout()
        layout.removeWidget(palette);layout.insertWidget(layout.indexOf(geometry.apply)+1,palette)
        scripts=self.panels["Scripts"]
        scripts.option_icons=ActionIcons(scripts.findChildren(QPushButton),scripts)
        scripts.layout().insertWidget(1,scripts.option_icons)
        self.details.panels=self.panels
        for name,panel in self.panels.items():self.details.addTab(panel,name)
        task_workspace=QWidget();task_workspace.setObjectName("TaskWorkspace")
        task_layout=QVBoxLayout(task_workspace);task_layout.setContentsMargins(12,10,12,10);task_layout.setSpacing(2)
        self.workspace_title=QLabel();self.workspace_title.setObjectName("WorkspaceTitle");task_layout.addWidget(self.workspace_title)
        self.workspace_summary=QLabel();self.workspace_summary.setObjectName("WorkspaceSummary");self.workspace_summary.setWordWrap(True)
        task_layout.addWidget(self.workspace_summary);task_layout.addSpacing(6)
        self.details.tabBar().hide();task_layout.addWidget(self.details)
        self.details.currentChanged.connect(self._update_workspace_heading)
        self._update_workspace_heading(self.details.currentIndex())
        self.details.currentChanged.connect(self._workspace_changed)
        self.details.on_reselect=self._workspace_changed
        for name, selector in (("Geometry", "choice"), ("Construction", "mode"), ("Generators", "kind")):
            getattr(self.panels[name], selector).activated.connect(self._begin_modelling)
        tasks_dock=self._dock("Tasks",task_workspace,Qt.RightDockWidgetArea)
        self.job_status=_JobView(self)
        jobs_dock=self._dock("Jobs",self.job_status,Qt.BottomDockWidgetArea)
        self.log=QPlainTextEdit();self.log.setReadOnly(True)
        messages_dock=self._dock("Messages",self.log,Qt.BottomDockWidgetArea)
        self.tabifyDockWidget(jobs_dock,messages_dock);jobs_dock.raise_()
        from .run_progress import QtRunMonitor
        self.run_monitor=QtRunMonitor(self)
        self.run_dock=self._dock("Run monitor",self.run_monitor,Qt.BottomDockWidgetArea)
        self.tabifyDockWidget(jobs_dock,self.run_dock)
        self.run_dock.hide();jobs_dock.raise_()
        self._workspace_docks=(model_dock,tasks_dock,jobs_dock,messages_dock,self.run_dock)
        self.resizeDocks([model_dock,tasks_dock],[280,380],Qt.Horizontal)
        self.resizeDocks([jobs_dock,messages_dock],[140,140],Qt.Vertical)
        self._actions={}
        self._menus()
        self._default_workspace_state=self.saveState()
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
        from .button_help import ButtonHelp
        self.button_help=ButtonHelp(self)
        self.panels["Visualization"].presentation_controls.reset_project()
        if viewer_backend=='auto':
            label='WebGPU' if self.viewport.canvas.backend_name=='webgpu' else self.viewport.active_backend
            details='; '.join(self.viewport.backend_diagnostics)
            self.set_status(f'Renderer: {label}'+(f' · {details}' if details else ''))

    def _dock(self,title,widget,area):
        dock=QDockWidget(title,self);dock.setObjectName(title)
        dock.setWidget(widget);self.addDockWidget(area,dock)
        return dock

    def reset_workspace_layout(self):
        self.restoreState(self._default_workspace_state)
        model,tasks,jobs,messages,run=self._workspace_docks
        self.resizeDocks([model,tasks],[280,390],Qt.Horizontal)
        self.resizeDocks([jobs],[150],Qt.Vertical)
        jobs.raise_()

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
        self._action(edit,"Cut plate with sketch…",self.cut_plate_with_sketch)
        from .loft import construct_selected_loft, open_loft
        self._action(edit,"Construct loft from selection",lambda:construct_selected_loft(self))
        self._action(edit,"Edit loft settings…",lambda:open_loft(self))
        self._action(edit,"Select all",self._select_all,"Ctrl+A")
        self._action(edit,"Delete selected",self._delete_selection,"Delete")
        self._action(edit,"Create geometry set…",self.geometry_actions.new_set,"Ctrl+G")
        self._action(edit,"Add selection to set…",self.geometry_actions.add_to_set)
        self._action(edit,"Copy geometry…",lambda:self.geometry_actions.vector_dialog("Copy"))
        self._action(edit,"Move geometry…",lambda:self.geometry_actions.vector_dialog("Move"))
        self._action(edit,"Geometry properties…",self.geometry_actions.properties)
        self._action(edit,"Commands",self.show_command_palette,"Ctrl+P")
        view=self.menuBar().addMenu("View")
        self._action(view,"Hide selected",lambda:self.set_selection_hidden(True),"Ctrl+H")
        self._action(view,"Show selected",lambda:self.set_selection_hidden(False),"Ctrl+Shift+H")
        self._action(view,"Show all",self.show_all_items)
        self.canvas_action_menu=self._action(view,"Actions on canvas right-click",lambda:None)
        self.canvas_action_menu.setCheckable(True);self.canvas_action_menu.setChecked(True)
        for name,callback in [("Geometry",self.show_geometry),("Mesh",self.show_mesh),("Results",self.panels["Results"].show_results),("Fit",self.viewport.fit)]:
            self._action(view,name,callback)
        self._action(view,"Frame selection",self.viewport.frame_selection,"F")
        attributes=self._action(view,"Attributes / imperfections",lambda:self._set_show_attributes(attributes.isChecked()))
        attributes.setCheckable(True);attributes.setChecked(self._show_attributes.get())
        for name in ["iso","top","front","side"]:
            self._action(view,name.capitalize(),lambda name=name:self.viewport.set_view(name))
        view.addSeparator()
        from .theme import is_dark, set_dark
        dark_mode=self._action(view,"Dark mode",lambda:set_dark(self,dark_mode.isChecked()),"Ctrl+Shift+D")
        dark_mode.setCheckable(True);dark_mode.setChecked(is_dark());self._dark_mode_action=dark_mode
        for dock in self.findChildren(QDockWidget):view.addAction(dock.toggleViewAction())
        self._action(view,"Reset workspace layout",self.reset_workspace_layout)
        analysis=self.menuBar().addMenu("Analysis")
        self._action(analysis,"Generate mesh",self.panels["Mesh"].start)
        self._action(analysis,"Solve",self.panels["Solve"].start)
        examples=self.menuBar().addMenu("Examples")
        action=self._action(examples,"Floating barge section…",self.open_barge_example)
        action.setToolTip("Create an S355 barge section: 700 mm stiffeners, 4000 mm girders, fixed end and bending load for approximately 200 MPa")
        action.setStatusTip(action.toolTip())
        for kind,label in (("distributing","Floating barge section — end moment on a distributing connection…"),
                           ("rigid","Floating barge section — end moment on a rigid connection…")):
            action=self._action(examples,label,lambda kind=kind:self.open_barge_example(kind))
            action.setToolTip(f"The same barge, with the bending moment applied once at the centre of the deck and bottom end lines through a {kind} connection")
            action.setStatusTip(action.toolTip())
        action=self._action(examples,"Floating barge section — seaway time history (600 load cases)…",lambda:self.open_barge_example("seaway"))
        action.setToolTip("The barge in a travelling wave: wave pressure on the hull and a time-varying moment on a rigid end connection, 600 load cases over 60 s")
        action.setStatusTip(action.toolTip())
        action=self._action(examples,"Floating barge section — 15 m with bulkhead openings…",lambda:self.open_barge_example("large"))
        action.setToolTip("Three times the length: two bulkheads with three access openings each, a flat bar FB 120×10 along every opening edge, same bending load")
        action.setStatusTip(action.toolTip())
        action=self._action(examples,"Drawing barge — 60 m structural example…",lambda:self.open_barge_example("drawing60"))
        action.setToolTip("60 m hull with centreline and transverse bulkheads, framed end structures to 6 m, and an illustrative linear-static load")
        action.setStatusTip(action.toolTip())
        help_menu=self.menuBar().addMenu("Help")
        action=self._action(help_menu,"Copy diagnosis",self.copy_diagnosis)
        action.setToolTip("Copy the support report, with an on-demand Qt runtime snapshot, to the clipboard")
        action.setStatusTip(action.toolTip())
        for action in self._actions.values():
            if not action.shortcut().isEmpty() and not action.toolTip().endswith(")"):
                action.setToolTip(f"{action.text()} ({action.shortcut().toString(QKeySequence.NativeText)})")
        toolbar=self.addToolBar("Workbench");toolbar.setObjectName("Workbench")
        toolbar.setIconSize(QSize(20,20));toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        icons={"New":QStyle.SP_FileIcon,"Open":QStyle.SP_DirOpenIcon,"Save":QStyle.SP_DialogSaveButton,
            "Undo":QStyle.SP_ArrowBack,"Redo":QStyle.SP_ArrowForward,"Fit":QStyle.SP_TitleBarMaxButton,
            "Generate mesh":QStyle.SP_FileDialogDetailedView,"Solve":QStyle.SP_MediaPlay}
        for name in icons:self._actions[name].setIcon(self.style().standardIcon(icons[name]))
        for name in ["New","Open","Save","Undo","Redo"]:
            if name=="Undo":toolbar.addSeparator()
            toolbar.addAction(self._actions[name])
        toolbar.addSeparator()
        for name in ["Generate mesh","Solve"]:
            toolbar.addAction(self._actions[name])
            toolbar.widgetForAction(self._actions[name]).setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toolbar.widgetForAction(self._actions["Solve"]).setObjectName("PrimaryToolButton")
        from .navigation import navigation_icon
        workspaces=self.addToolBar("Workspaces");workspaces.setObjectName("Workspaces")
        workspaces.setIconSize(QSize(22,22));workspaces.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        workspace_menu=self.menuBar().insertMenu(self.menuBar().actions()[-1],QMenu("Workspace",self)).menu()
        self.workspace_actions={};self.workspace_action_group=QActionGroup(self)
        for index,name in enumerate(self.panels):
            if name in self.WORKSPACE_GROUP_STARTS:workspaces.addSeparator();workspace_menu.addSeparator()
            action=QAction(navigation_icon(name),name.replace("&","&&"),self);action.setCheckable(True)
            action.setChecked(index==self.details.currentIndex())
            if index<9:action.setShortcut(f"Ctrl+{index+1}")
            shortcut=f" ({action.shortcut().toString(QKeySequence.NativeText)})" if index<9 else ""
            action.setToolTip(f"{name}{shortcut}: {self.WORKSPACE_SUMMARIES.get(name,'')}".rstrip(": "))
            action.setStatusTip(self.WORKSPACE_SUMMARIES.get(name,""))
            action.triggered.connect(lambda checked=False,index=index:self._select_workspace(index))
            self.workspace_action_group.addAction(action);workspaces.addAction(action);workspace_menu.addAction(action)
            button=workspaces.widgetForAction(action);button.setMinimumWidth(64);button.setAccessibleName(name+" workspace")
            self.workspace_actions[name]=action
        from PySide6.QtWidgets import QWidget, QSizePolicy
        spacer=QWidget();spacer.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Preferred);workspaces.addWidget(spacer)
        from .navigation import navigation_icon as _icon
        dark_toggle=QAction(self);self._dark_toggle=dark_toggle
        def sync_dark_toggle(*_):
            dark=self._dark_mode_action.isChecked()
            dark_toggle.setText("Light mode" if dark else "Dark mode");dark_toggle.setIcon(_icon("theme_light" if dark else "theme_dark"))
            dark_toggle.setToolTip(("Switch to light appearance" if dark else "Switch to dark appearance")+" (Ctrl+Shift+D)")
        dark_toggle.triggered.connect(lambda checked=False:self._dark_mode_action.trigger())
        self._dark_mode_action.toggled.connect(sync_dark_toggle);sync_dark_toggle()
        workspaces.addAction(dark_toggle)
        dark_button=workspaces.widgetForAction(dark_toggle);dark_button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        dark_button.setObjectName("DarkModeButton");dark_button.setAccessibleName("Dark mode toggle");dark_button.setMinimumWidth(64)
        self.details.currentChanged.connect(self._sync_workspace_icons)
        self.addToolBarBreak()
        toolbar=self.addToolBar("Selection and renderer");toolbar.setObjectName("Selection and renderer")
        heading=QLabel("Select");heading.setObjectName("ToolbarLabel");toolbar.addWidget(heading)
        self.quick_selection_actions={}
        self.quick_selection_group=QActionGroup(self);self.quick_selection_group.setExclusive(False)
        for label,mode in (("Point","vertex"),("Line","edge"),("Edge","plate_edge"),("Face","face")):
            action=QAction(label,self);action.setCheckable(True);action.setChecked(mode==self.selection.mode)
            action.setToolTip('Select plate perimeter and hole edges' if mode=='plate_edge' else f"Select {label.lower()} geometry")
            action.triggered.connect(lambda checked=False,mode=mode:self._quick_select(mode))
            self.quick_selection_group.addAction(action);toolbar.addAction(action)
            self.quick_selection_actions[mode]=action
        self.plane_menu=QMenu(self)
        self.plane_menu.aboutToShow.connect(self._refresh_plane_menu)
        plane_action=QAction("Plane",self)
        plane_action.setToolTip("Select a construction plane to display and use for sketching, or create a new plane.")
        plane_action.setMenu(self.plane_menu)
        toolbar.addAction(plane_action)
        toolbar.widgetForAction(plane_action).setPopupMode(QToolButton.InstantPopup)
        self.quick_selection_actions["plane"]=plane_action
        toolbar.addSeparator()
        from .widgets import ValueCombo
        self.selection_mode=ValueCombo((("vertex","Points"),("edge","Lines"),("plate_edge","Plate edges"),("face","Faces"),
                                        ("node","Mesh nodes"),("element","Mesh elements")))
        self.selection_mode.currentIndexChanged.connect(lambda _index:self._quick_select(self.selection_mode.value()))
        self.selection_tool=ValueCombo((("box","Box"),("single","Click"),("lasso","Lasso")))
        self.selection_depth=ValueCombo((("visible","Visible only"),("through","See-through")))
        self.selection_operation=ValueCombo((("replace","Replace"),("add","Add"),("remove","Remove"),("toggle","Toggle")))
        for control,label,tip in ((self.selection_mode,"Pick","What a click or box picks"),
                                  (self.selection_tool,"Tool","Box drag, single click or freehand lasso"),
                                  (self.selection_depth,"Depth","Pick only visible items or everything under the cursor"),
                                  (self.selection_operation,"Mode","How a new pick combines with the current selection")):
            if control is not self.selection_mode:
                control.currentIndexChanged.connect(self._selection_policy)
            caption=QLabel(label);caption.setObjectName("ToolbarCaption");caption.setBuddy(control)
            toolbar.addWidget(caption);toolbar.addWidget(control)
            control.setToolTip(tip);control.setAccessibleName({"Pick":"Entity type","Tool":"Selection tool",
                "Depth":"Selection depth","Mode":"Selection operation"}[label])
        toolbar.addSeparator()
        for name in ["Fit","Iso","Top","Front","Side"]:
            toolbar.addAction(self._actions[name])
        toolbar.widgetForAction(self._actions["Fit"]).setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._actions["Fit"].setToolTip("Fit the model in the view")
        for name in ["Iso","Top","Front","Side"]:self._actions[name].setToolTip(f"{name} view")
        self._build_model_colour_buttons(toolbar)
        spacer2=QWidget();spacer2.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Preferred);toolbar.addWidget(spacer2)
        self._build_view_mode_buttons(toolbar)
        self.renderer=ValueCombo((("auto","Automatic"),("gpu","GPU (OpenGL)"),
                                  ("webgpu","WebGPU"),("software","Software")))
        self.renderer.set_value(self._viewer_backend)
        self.renderer.currentIndexChanged.connect(lambda _index:self.guarded(lambda:self.switch_viewer_backend(self.renderer.value()))())
        self.renderer.setToolTip("Automatic uses WebGPU (Vulkan or Metal), or Software if unavailable. GPU (OpenGL) selects the legacy GPU renderer.");self.renderer.setAccessibleName("Viewport renderer")
        renderer_box=QWidget();renderer_layout=QHBoxLayout(renderer_box);renderer_layout.setContentsMargins(0,0,6,0);renderer_layout.setSpacing(4)
        caption=QLabel("Renderer");caption.setObjectName("StatusCaption");renderer_layout.addWidget(caption);renderer_layout.addWidget(self.renderer)
        self._footer_statusbar=super().statusBar()
        self._footer_statusbar.addPermanentWidget(renderer_box)
        self._footer_statusbar.showMessage("Ready")
        self._dock_footer=_DockFooter(self,self._footer_statusbar)

    def _select_workspace(self,index):
        self.details.setCurrentIndex(index)
        self._workspace_changed(index)
        self._workspace_docks[1].show();self._workspace_docks[1].raise_()

    def _update_workspace_heading(self,index):
        name=self.details.tabText(index)
        self.workspace_title.setText(name)
        self.workspace_summary.setText(self.WORKSPACE_SUMMARIES.get(name,""))

    def _workspace_changed(self,index):
        if self.details.tabText(index) in {"Geometry", "Construction", "Generators"}:
            self._begin_modelling()

    def _begin_modelling(self, *_):
        # Imported mesh-only documents have no generating geometry to return to.
        if self.imported is not None:
            return
        if self.mesh is None and self._inspection_mesh is None and self._view_mode not in {"mesh", "inspection_mesh", "results"}:
            return
        self._invalidate_downstream()
        self.selection.set_mode(getattr(self, "_geometry_selection_mode", "face"))

    def _sync_workspace_icons(self,index):
        for position,action in enumerate(self.workspace_actions.values()):action.setChecked(position==index)
        if self.details.tabText(index)=="Results":self.show_result_browser()

    def _selection_policy(self,*_):
        self.viewport.configure_selection(tool=self.selection_tool.value(),
            depth=self.selection_depth.value(),operation=self.selection_operation.value())

    def _quick_select(self,mode):
        self.viewport.dismiss_feature_highlight()
        if mode=='plate_edge':
            from ...selection import SelectionFilter, plate_boundary_edge_ids
            self.selection.set_filter(SelectionFilter(kinds=frozenset({'edge'}),flavor='plate_edge',eligible_ids=plate_boundary_edge_ids(self.project,self.tree.exploded_feature_ids)))
        else:
            self.selection.set_mode(mode)
        self._sync_selection_controls()
        if self._view_mode=='geometry':self.show_geometry()

    def _refresh_plane_menu(self):
        self.plane_menu.clear()
        panel=self.panels["Construction"]
        current=self.session.active_workplane.coordinate_system_id
        for identifier,system in panel.coordinate_systems().items():
            label="XY (Global)" if identifier=="global" else system.name
            action=self.plane_menu.addAction(label)
            action.setData(identifier);action.setCheckable(True);action.setChecked(identifier==current)
            action.setEnabled(not self.viewport.construction_active)
            action.triggered.connect(lambda checked=False,identifier=identifier:self.guarded(lambda:self._choose_plane(identifier))())
        self.plane_menu.addSeparator()
        action=self.plane_menu.addAction("Add construction plane…")
        action.setEnabled(not self.viewport.construction_active and not self.session.read_only)
        action.triggered.connect(self._open_plane_construction)

    def _choose_plane(self,identifier):
        if self.viewport.construction_active:raise ValueError("Finish or cancel the current construction first")
        panel=self.panels["Construction"]
        system=panel.coordinate_systems()[identifier]
        self._begin_modelling()
        panel.select_plane(system)
        self.notebook.select("Construction")
        self.set_status(f"Selected plane {system.name}. Enter starts a sketch on this plane.")

    def cut_plate_with_sketch(self, sketch_feature_id=None):
        from .sketch_cut import cut_plate_with_sketch
        return cut_plate_with_sketch(self, sketch_feature_id)

    def _open_plane_construction(self):
        self.notebook.select("Geometry")
        panel=self.panels["Geometry"]
        panel.choice.setCurrentIndex(panel.choice.findData("AddConstructionPlane"))
        panel.fields["name"][0].setFocus()

    def _sync_selection_controls(self):
        active='plate_edge' if getattr(self.selection.filter,'flavor','')=='plate_edge' else self.selection.mode
        index=self.selection_mode.findData(active)
        if index>=0:
            self.selection_mode.blockSignals(True)
            self.selection_mode.setCurrentIndex(index)
            self.selection_mode.blockSignals(False)
        for mode,action in self.quick_selection_actions.items():
            if action.isCheckable():action.setChecked(mode==active)

    def _set_show_attributes(self,visible):
        self._show_attributes.set(bool(visible))
        self.refresh_views()

    def refresh_views(self):
        if self._closing:return
        if self._view_mode=="results" and self.panels["Results"].use_retained() and self.active_job_id in self.result_datasets:
            self.panels["Results"].show_results()
        else:super().refresh_views()

    VIEW_MODES=(("Model","geometry",("geometry","mesh-layout-preview"),"mode_model","Show the geometry model"),
                ("Mesh","mesh",("mesh","inspection_mesh"),"mode_mesh","Show the mesh"),
                ("Results","results",("results",),"mode_results","Show solved results"))

    def _build_model_colour_buttons(self, toolbar):
        from .navigation import navigation_icon
        group=QActionGroup(self);group.setExclusive(True)
        self._model_colour_actions={}
        for key,label,icon,tip in (
            ("thickness","Thickness","mode_model","Colour plates by actual thickness; grey means unassigned"),
            ("section","Section","sections","Colour structural members by beam section; grey means unassigned"),
            ("density","Density","mode_mesh","Model: requested target size sampled on geometry. Mesh: actual shell √area or beam length"),
            ("default","Default","geometry","Restore default model and mesh colours")):
            action=QAction(navigation_icon(icon),label,self);action.setCheckable(True);action.setToolTip(tip)
            action.setProperty("colour_base_tip",tip)
            group.addAction(action);toolbar.addAction(action);self._model_colour_actions[key]=action
            button=toolbar.widgetForAction(action);button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon);button.setAccessibleName({"thickness":"Plate thickness","section":"Beam section","density":"Mesh density","default":"Default"}[key]+" colours")
            action.triggered.connect(lambda checked=False,key=key:self.guarded(lambda:self.set_model_colour_mode(key))())
        self._model_colour_actions["default"].setChecked(True)

    def _build_view_mode_buttons(self,toolbar):
        from .navigation import navigation_icon
        controls=self.panels['Visualization'].presentation_controls
        self.split_screen_action=QAction(navigation_icon('split_screen'),'Split screen',self)
        self.split_screen_action.setCheckable(True)
        self.split_screen_action.setChecked(controls.compare.isChecked())
        self.split_screen_action.setToolTip('Show or hide a second viewport. Choose its result in Visualization > Compare results.')
        self.split_screen_action.triggered.connect(lambda checked:controls.compare.setChecked(checked))
        controls.compare.toggled.connect(lambda checked:self.split_screen_action.setChecked(checked))
        toolbar.addAction(self.split_screen_action)
        button=toolbar.widgetForAction(self.split_screen_action)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setAccessibleName('Split screen toggle')
        self.reset_view_action=QAction(navigation_icon('reset_view'),'Reset view',self)
        self.reset_view_action.setToolTip('Restore default dock layout, appearance and an isometric fitted camera; clear sectioning, split screen and inspection overlays.')
        self.reset_view_action.triggered.connect(lambda checked=False:self.guarded(controls.reset_view)())
        toolbar.addAction(self.reset_view_action)
        button=toolbar.widgetForAction(self.reset_view_action)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setAccessibleName('Reset view')
        self._view_mode_actions={};group=QActionGroup(self);group.setExclusive(True)
        callbacks={"geometry":lambda:self.show_geometry(),"mesh":lambda:self.show_mesh(),
                   "results":lambda:self.panels["Results"].show_results()}
        for label,key,_modes,icon,tip in self.VIEW_MODES:
            action=QAction(navigation_icon(icon),label,self);action.setCheckable(True);action.setToolTip(tip)
            action.triggered.connect(lambda checked=False,key=key:(self.guarded(callbacks[key])(),self._sync_view_mode_buttons()))
            group.addAction(action);toolbar.addAction(action);self._view_mode_actions[key]=action
            toolbar.widgetForAction(action).setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            toolbar.widgetForAction(action).setAccessibleName(f"{label} visualization mode")
        self._view_mode_timer=QTimer(self);self._view_mode_timer.setInterval(250)
        self._view_mode_timer.timeout.connect(self._sync_view_mode_buttons);self._view_mode_timer.start()
        self._sync_view_mode_buttons()

    def _sync_view_mode_buttons(self):
        self._update_wordmark()
        mode=getattr(self,"_view_mode",None)
        for key,action in getattr(self,"_model_colour_actions",{}).items():
            action.setEnabled(mode in {"geometry","mesh"})
            action.setChecked(key==getattr(self,"_model_colour_mode","default"))
            legend=getattr(self.viewport,"_scene",None)
            entries=(getattr(legend,"legend",None) or {}).get("entries",()) if action.isChecked() else ()
            base=action.property("colour_base_tip") or ""
            action.setToolTip(base+("\n"+"\n".join(text for text,_ in entries) if entries else ""))
        for _label,key,modes,_icon,_tip in self.VIEW_MODES:
            action=self._view_mode_actions.get(key)
            if action is not None and action.isChecked()!=(mode in modes):action.setChecked(mode in modes)

    def _update_wordmark(self):
        if self.viewport.construction_active:
            self._wordmark.set_active(False)
            return
        # startup only: once the model gains geometry (or a project is opened) the wordmark is gone for the session
        if not getattr(self,"_wordmark_done",False):
            try:
                empty=(len(self.project.geometry.vertices)==0
                       and not self.project.mesh_records and not self.result_datasets)
            except Exception:empty=False
            try:
                viewport=self.viewport
                busy=(viewport.construction_active or getattr(viewport,"plate_draft",None) is not None
                      or self.commands.can_undo)
            except Exception:busy=False
            empty=empty and not busy
            self._wordmark_done=not empty
            self._wordmark.set_active(empty)
        elif self._wordmark.isVisibleTo(self):
            self._wordmark.set_active(False)

    def refresh_all(self):
        if self._closing or self._refresh_suspended or self._refreshing:return
        revision=(self.session,self.session.revision.id)
        if self.session._transaction_depth and not getattr(self,"_committed_refresh",False):
            # Stack notifications precede commit. A successful revision refresh
            # consumes this request; rollback still gets a queued refresh.
            self._queue_refresh()
            return
        self._refreshing=True
        self._refresh_pending=False
        try:
            self.run_monitor.bind_document()
            self.tree.refresh();self.refresh_panels();self.job_status.refresh()
            self._actions["Undo"].setEnabled(self.commands.can_undo and not self.session.read_only)
            self._actions["Redo"].setEnabled(self.commands.can_redo and not self.session.read_only)
            self._update_window_title()
            self._update_wordmark()
            self._last_refreshed_revision=revision
        finally:self._refreshing=False

    def _on_revision_changed(self,revision):
        self._committed_refresh=True
        try:
            super()._on_revision_changed(revision)
            self.refresh_all()
        finally:self._committed_refresh=False

    def refresh_panels(self):
        for panel in self.panels.values():panel.refresh()
        self.result_browser.refresh()

    def show_result_browser(self):
        switching=self.left_tabs.currentWidget() is not self.result_browser
        self.left_tabs.setCurrentWidget(self.result_browser)
        self._workspace_docks[0].show()
        if switching:self.resizeDocks([self._workspace_docks[0]],[370],Qt.Horizontal)

    def _on_workbench_event(self,event):
        if event.kind=="state_changed" and event.field=="shape_index":
            panel=getattr(self,"panels",{}).get("Results")
            if getattr(panel,"playback_active",False):
                # Playback updates its frame/details directly; document trees
                # and every other workbench panel did not change with this frame.
                return
        if event.kind=="state_changed" and event.field=="geometry_selection_mode":
            # This is a navigation preference. Selection listeners already
            # update picking, highlights, tree selection and status directly.
            return
        if (event.kind in {"command_executed","commands_executed"}
                and not self._refreshing
                and self._last_refreshed_revision==(self.session,event.snapshot.revision.id)):
            return
        self._queue_refresh()

    def _queue_refresh(self):
        if self._closing or self._refresh_pending:return
        self._refresh_pending=True
        def refresh():
            if not self._refresh_pending:return
            self._refresh_pending=False
            self.refresh_all()
        self.scheduler.call_later(0,refresh)

    def _on_selection_changed(self):
        if self._closing:return
        self._sync_selection_controls()
        if not getattr(self.tree,"_tree_driven",False):self.viewport.dismiss_feature_highlight()
        if getattr(self.selection.domain,"value","geometry")=="geometry":
            self._geometry_selection_mode=self.selection.mode
        self.tree.sync_from_selection()
        self.statusBar().showMessage(f"{len(self.selection.items)} selected · {self._status.get()}")

    def _observe_run_event(self,kind,event):
        monitor=getattr(self,"run_monitor",None)
        if monitor is None:return
        records=self.project.mesh_records if kind=="mesh" else self.project.jobs
        record=records.get(event.job_id)
        if record is None:return
        try:
            stale=(record.source_model_hash!=self.session.revision.model_hash if kind=="mesh" else self._job_is_stale(record))
            monitor.observe(kind,event,record.name,stale=stale)
            if event.kind in {"queued","started"}:
                self.run_dock.show();self.run_dock.raise_()
                self.resizeDocks([self.run_dock],[300],Qt.Vertical)
        except Exception as error:
            self.set_status(f"Run monitor update failed: {error}",error=True)

    def set_selection_hidden(self, hidden):
        refs=tuple(dict.fromkeys((*self.selection.ordered_items,*self.geometry_actions.references())))
        planes={value[1] for index in self.tree.selectionModel().selectedRows()
                if isinstance(value:=index.data(Qt.UserRole),(tuple,list)) and value[0]=="coordinate_systems"
                and value[1]!="global" and self.project.coordinate_systems[value[1]].kind=="cartesian"}
        if not refs and not planes:
            self.set_status("Select items in the canvas or model tree first.")
            return
        self.viewport.visibility.set_hidden(refs,hidden)
        keys={("plane",identifier) for identifier in planes}
        if hidden:self.viewport.visibility.hidden.update(keys)
        else:self.viewport.visibility.hidden.difference_update(keys)
        self.viewport.refresh_visibility()
        self.tree.refresh_visibility()
        self.set_status(f"{'Hidden' if hidden else 'Shown'} {len(refs)+len(planes)} selected items.")

    def show_all_items(self):
        self.viewport.visibility.hidden.clear()
        self.viewport.refresh_visibility()
        self.tree.refresh_visibility()
        self.set_status("All items shown.")

    def _update_window_title(self):
        self.setWindowTitle(f"{'*' if self.session.dirty else ''}{self.project.name} — ANYfem{' [read-only]' if self.session.read_only else ''}")

    def open_barge_example(self,end_loading="line_loads"):
        from PySide6.QtWidgets import QMessageBox
        from ...application.examples import (BARGE_CONNECTION_DESCRIPTION, BARGE_DESCRIPTION, BARGE_LARGE_DESCRIPTION,
            BARGE_DRAWING_60M_DESCRIPTION, barge_seaway_description, drawing_barge_60m,
            floating_barge_large, floating_barge_seaway, floating_barge_section)
        large=end_loading=="large"
        drawing=end_loading=="drawing60"
        extra="" if large or drawing else barge_seaway_description() if end_loading=="seaway" else BARGE_CONNECTION_DESCRIPTION.format(kind=end_loading)
        description=(BARGE_DRAWING_60M_DESCRIPTION if drawing else BARGE_LARGE_DESCRIPTION if large
                     else BARGE_DESCRIPTION+("" if end_loading=="line_loads" else "\n\n"+extra))
        answer=QMessageBox.question(self,"Floating barge section",description+"\n\nCreate this example as a new project?",
                                   QMessageBox.Ok|QMessageBox.Cancel,QMessageBox.Cancel)
        if answer!=QMessageBox.Ok or not self._confirm_discard():return
        project=(drawing_barge_60m() if drawing else floating_barge_large() if large
                 else floating_barge_seaway() if end_loading=="seaway" else floating_barge_section(end_loading))
        self._set_project(project)
        self.session.dirty=True
        self.refresh_all()
        self.show_geometry(reset_view=True)
        self.set_status("60 m drawing barge: illustrative geometry and load; generate mesh, then solve linear static" if drawing
                        else "S355 barge in a seaway: 600 load cases over 60 s; generate mesh, then Batch linear static" if end_loading=="seaway"
                        else "S355 barge, 15 m: bulkhead openings with FB 120×10 edges; fixed X=0; My at X=15; generate mesh (250 mm), then solve" if large
                        else "S355 barge: plates 12/10/8 mm; fixed X=0; My≈41.46 MN·m at X=5; generate mesh, then solve")
        self._schedule_autosave()

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
        self._status_revision=getattr(self,'_status_revision',0)+1
        self._status.set(text)
        self.statusBar().showMessage(text)
        if hasattr(self,"log"):self.log.appendPlainText(("Error: " if error else "")+text)
        if error:
            self._error_diagnostics.append(ErrorDiagnostic.capture(text,details=diagnostic,
                project_id=str(getattr(self.project,"document_id","")),view=self._view_mode))
            del self._error_diagnostics[:-10]

    def _on_progress_batch(self,events):
        entries=[(_job_progress_text(event.message,event.payload),event.payload) for event in events]
        if not entries:return
        # Keep the complete ordered messages, while changing live labels once.
        if len(entries)>1:self.log.appendPlainText("\n".join(text for text,_ in entries[:-1]))
        self.set_status(entries[-1][0])
        panel=self.panels.get("Solve")
        if panel is not None:
            panel.show_progress(entries[-1][0])
            panel.append_progress_batch(entries)

    def _set_project(self,*args,**kwargs):
        self.viewport.cancel_plate()
        self.panels["Scripts"].reset()
        self.panels["Results"].reset()
        self.panels["Solve"].reset()
        super()._set_project(*args,**kwargs)
        self.panels["Visualization"].presentation_controls.reset_project()
        self.tree.cancel_pending_search()
        self.tree.search_edit.setText(self.tree.query)
        self.refresh_all()

    def switch_viewer_backend(self,backend):
        stop_playback=getattr(self.panels["Results"],"stop_playback",None)
        if stop_playback is not None:stop_playback(clear_prepared=True)
        try:
            active=self.viewport.switch_backend(backend)
        except BaseException:
            self.renderer.blockSignals(True);self.renderer.set_value(self._viewer_backend);self.renderer.blockSignals(False)
            raise
        self._viewer_backend=backend
        self.renderer.blockSignals(True)
        try:self.renderer.set_value(backend)
        finally:self.renderer.blockSignals(False)
        api=getattr(getattr(self.viewport,"canvas",None),"backend_name",active)
        label="WebGPU" if api=="webgpu" else "GPU (OpenGL)" if api=="gpu" else active
        self.set_status(f"Renderer: {label}"+(f" · {'; '.join(self.viewport.backend_diagnostics)}" if self.viewport.backend_diagnostics else ""))
        return active

    def diagnostic_report(self, *, recent_commands=()):
        """The toolkit-neutral report plus one on-demand Qt runtime snapshot."""
        report=super().diagnostic_report(recent_commands=recent_commands)
        return extend_report_with_qt_runtime(report,self)

    def copy_diagnosis(self):
        """Always create a fresh current-state Help report, even without scripts."""
        panel=getattr(self,'panels',{}).get('Scripts')
        recent=panel.recent_commands() if panel is not None and callable(getattr(panel,'recent_commands',None)) else ()
        return _copy_current_diagnosis(self,recent)

    def show_command_palette(self):
        choices=list(self._actions)+list(self.panels)
        choice,accepted=QInputDialog.getItem(self,"Commands","Action",choices,editable=False)
        if accepted:
            if choice in self.panels:self.notebook.select(choice)
            else:self._actions[choice].trigger()

    def confirm_untrusted_code(self,items):
        """Show pressure code that came from a file and ask before it may run."""
        from PySide6.QtWidgets import QMessageBox
        box=QMessageBox(self);box.setIcon(QMessageBox.Warning);box.setWindowTitle("Run pressure code from this project?")
        box.setText("This project defines pressure with Python code. It runs with the same access as ANYfem, so only continue if you trust where the project came from.")
        blocks=[f"# {case}: {name}"+chr(10)+code for case,name,code in items]
        box.setDetailedText((chr(10)*2).join(blocks))
        box.setStandardButtons(QMessageBox.Yes|QMessageBox.No);box.setDefaultButton(QMessageBox.No)
        box.button(QMessageBox.Yes).setText("Trust and run");box.button(QMessageBox.No).setText("Cancel")
        return box.exec()==QMessageBox.Yes

    def closeEvent(self,event):
        if self._closing:event.accept();return
        if not self._confirm_discard():event.ignore();return
        try:
            self.flush_project_writes()
        except Exception as error:
            self.set_status(f"Cannot close: recovery snapshot failed: {error}", error=True)
            event.ignore()
            return
        self.tree.cancel_pending_search()
        self.geometry_actions.close()
        settings=QSettings("ANY","ANYfem-Qt")
        settings.setValue("geometry",self.saveGeometry());settings.setValue("docks",self.saveState())
        self._closing=True
        self.run_monitor.stop()
        self.button_help.close()
        self.panels["Solve"].graph_timer.stop()
        self.panels["Scripts"].reset()
        self.panels["Results"].reset()
        self.panels["Visualization"].presentation_controls.close()
        self.close_result_access()
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


def main(*, viewer_backend="auto"):
    import sys
    app=QApplication.instance() or QApplication(sys.argv)
    window=QtFemWindow() if viewer_backend=="auto" else QtFemWindow(viewer_backend=viewer_backend)
    window.show()
    return app.exec()
