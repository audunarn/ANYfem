"""Geometry review and explicit, revision-bound cleanup in the workbench."""
from __future__ import annotations

import time
from threading import Event
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QSpinBox, QFileDialog,
)

from anygeometry import EntityRef
from anygeometry.cleaner import CleanerOptions
from ...geometry_cleaner import (
    command_for_project_repair, preview_project_repair, repair_disabled_reason,
    review_project, diagnosis_summary,
)


_active_workers = set()


class DiagnosisWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    progress_changed = Signal(str)

    def __init__(self, snapshot, options):
        super().__init__()
        from anysolver import CancellationToken
        self.snapshot = snapshot
        self.options = options
        self.token = CancellationToken()
        self.cancelled = False
        self.process = None
        self.tree = None
        self.finished_run = False
        self.retired = False
        self.cleanup_failures = ()

    def _own_tree(self, process, tree):
        self.process = process
        self.tree = tree
        if self.cancelled:
            tree.terminate()

    def run(self):
        try:
            from ...job_process import run_in_worker_process
            from ...geometry_diagnosis_worker import diagnose_snapshot_project
            submitted_at = time.perf_counter()
            self.progress_changed.emit("Preparing model for diagnosis")
            snapshot = self.snapshot.for_worker()
            self.progress_changed.emit("Starting diagnosis")
            result = run_in_worker_process(
                function=diagnose_snapshot_project, kwargs={"options": self.options, "submitted_at": submitted_at},
                snapshot=snapshot, project_override=None,
                progress=lambda value: self.progress_changed.emit(str(value)), token=self.token,
                containment=True, on_tree=self._own_tree, terminate_on_cancel=True,
                worker_priority="normal",
            )
            if not self.cancelled:
                self.completed.emit(result)
        except BaseException as error:
            self.cleanup_failures = tuple(getattr(error, "cleanup_failures", ()))
            if getattr(error, "remote_type", None) == "WorkerCleanupFailed" and not self.cleanup_failures:
                self.cleanup_failures = (str(error),)
            if not self.cancelled:
                self.failed.emit(str(error))
        finally:
            self.retired = ((self.process is None or not self.process.is_alive())
                            and (self.tree is None or not self.tree.cleanup_failures)
                            and not self.cleanup_failures)
            self.finished_run = True

    def cancel(self):
        self.cancelled = True
        self.token.cancel("Geometry diagnosis cancelled")
        # Stop only this invocation's contained process tree, including any
        # audit pool. The established process runner verifies its retirement.
        if self.tree is not None:
            self.tree.terminate()


class ReportExportWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, report, path, document):
        super().__init__()
        self.report, self.path, self.document = report, path, document
        self._cancelled = Event()

    def run(self):
        from ...geometry_diagnosis_worker import write_diagnosis_report
        try:
            result = write_diagnosis_report(self.report, self.path, document=self.document,
                                           cancelled=self._cancelled.is_set)
            self.completed.emit(result)
        except BaseException as error:
            self.failed.emit(str(error))

    def cancel(self):
        self._cancelled.set()


def _retain_worker(worker):
    # A running QThread must outlive its dialog, including application shutdown.
    _active_workers.add(worker)
    def release():
        _active_workers.discard(worker)
        worker.deleteLater()
    worker.finished.connect(release)


def _shutdown_workers():
    workers = tuple(_active_workers)
    for worker in workers:
        worker.cancel()
    deadline = time.perf_counter() + 6
    for worker in workers:
        worker.wait(max(0, int(1000 * (deadline - time.perf_counter()))))


class GeometryCleanerDialog(QDialog):
    """A review never edits geometry. Only an explicitly previewed plan can apply."""

    PAGE_SIZE = 200

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.report = None
        self.plan = None
        self.runner = None
        self.exporter = None
        self._pending_close = None
        self._report_context = None
        self._started = None
        self._progress_text = ""
        self._binding = None
        self._snap_vertices = ()
        self._dialog_session = app.session
        self._dialog_project = app.project
        self._did_highlight = False
        self._original_mesh = app.mesh
        self._highlight_binding = (app.session, app.project, tuple(app.selection.items))
        self.setObjectName("GeometryCleanerDialog")
        self.setWindowTitle("Review and clean geometry")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(920, 740)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Review the model, select findings to locate them, then preview an explicit repair. "
            "Close coordinates and separate regions may be intentional. Slivers and "
            "structural duplicates require engineering review."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        thresholds = QGroupBox("Review thresholds")
        form = QFormLayout(thresholds)
        defaults = CleanerOptions()
        unit = app.project.units.symbol("length")
        self.short_edge = QLineEdit(app.project.units.format(defaults.short_edge, "length", precision=12))
        self.near_vertex = QLineEdit(app.project.units.format(defaults.near_vertex, "length", precision=12))
        self.small_face = QLineEdit(str(defaults.small_face))
        self.sliver_quality = QLineEdit(str(defaults.sliver_quality))
        for name, label, field in (
            ("short_edge", f"Short edge ({unit}; explicit units accepted)", self.short_edge),
            ("near_vertex", f"Nearby points ({unit}; explicit units accepted)", self.near_vertex),
            ("small_face", "Small face area (m²)", self.small_face),
            ("sliver_quality", "Sliver quality limit (4πA / perimeter²)", self.sliver_quality),
        ):
            field.setObjectName(name)
            field.setAccessibleName(label)
            field.textChanged.connect(self.invalidate_review)
            form.addRow(label, field)
        self.options_tabs = QTabWidget()
        self.options_tabs.addTab(thresholds, "Review thresholds")
        layout.addWidget(self.options_tabs)
        row = QHBoxLayout()
        self.review_button = QPushButton("Diagnose model")
        self.review_button.setObjectName("ReviewGeometry")
        self.review_button.clicked.connect(self.review)
        row.addWidget(self.review_button)
        self.cancel_button = QPushButton("Cancel diagnosis")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_diagnosis)
        row.addWidget(self.cancel_button)
        self.export_button = QPushButton("Export full report…")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_report)
        row.addWidget(self.export_button)
        self.summary = QLabel("No review has run.")
        self.summary.setWordWrap(True)
        row.addWidget(self.summary, 1)
        layout.addLayout(row)
        self.findings = QTableWidget(0, 5)
        self.findings.setObjectName("GeometryFindings")
        self.findings.setHorizontalHeaderLabels(["Finding", "Geometry", "Measurement", "Severity", "Details / recommended action"])
        self.findings.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.findings.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.findings.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.findings.setMinimumHeight(200)
        self.findings.horizontalHeader().setStretchLastSection(True)
        self.findings.itemSelectionChanged.connect(self.highlight)
        layout.addWidget(self.findings, 1)
        pages = QHBoxLayout()
        pages.addWidget(QLabel("Findings page"))
        self.page = QSpinBox()
        self.page.setRange(1, 1)
        self.page.valueChanged.connect(self.render_findings)
        pages.addWidget(self.page)
        self.page_summary = QLabel()
        pages.addWidget(self.page_summary, 1)
        layout.addLayout(pages)
        self.coverage = QLabel()
        self.coverage.setWordWrap(True)
        layout.addWidget(self.coverage)
        self.checks_table = QTableWidget(0, 3)
        self.checks_table.setHorizontalHeaderLabels(["Check", "Status", "Evidence / coverage"])
        self.checks_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.checks_table.horizontalHeader().setStretchLastSection(True)
        repairs = QGroupBox("Selected repairs")
        repair_form = QFormLayout(repairs)
        self.remove_unused = QCheckBox("Remove selected isolated, unannotated points")
        self.remove_unused.toggled.connect(self.invalidate_plan)
        repair_form.addRow(self.remove_unused)
        self.snap = QCheckBox("Snap captured points to grid within the displacement limit")
        self.snap.toggled.connect(self.invalidate_plan)
        repair_form.addRow(self.snap)
        self.capture_button = QPushButton("Capture selected model points")
        self.capture_button.clicked.connect(self.capture_points)
        self.captured = QLabel("No points captured.")
        capture_row = QHBoxLayout()
        capture_row.addWidget(self.capture_button)
        capture_row.addWidget(self.captured, 1)
        repair_form.addRow(capture_row)
        self.grid_size = QLineEdit(app.project.units.format(0.001, "length", precision=12))
        self.max_displacement = QLineEdit(app.project.units.format(0.0001, "length", precision=12))
        for label, field in ((f"Grid spacing ({unit})", self.grid_size),
                             (f"Maximum movement ({unit})", self.max_displacement)):
            field.textChanged.connect(self.invalidate_plan)
            repair_form.addRow(label, field)
        self.options_tabs.addTab(repairs, "Repair options")
        self.options_tabs.addTab(self.checks_table, "Diagnosis coverage")
        self.grouped_findings = QTableWidget(0, 4)
        self.grouped_findings.setObjectName("GeometryDiagnosisGroups")
        self.grouped_findings.setHorizontalHeaderLabels(["Category", "Finding kind", "Rows", "Affected geometry (distinct)"])
        self.grouped_findings.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.grouped_findings.horizontalHeader().setStretchLastSection(True)
        self.options_tabs.addTab(self.grouped_findings, "Finding groups")
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(110)
        self.preview_text.setPlaceholderText("Preview lists every proposed change before Apply is enabled.")
        layout.addWidget(self.preview_text)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        buttons = QHBoxLayout()
        self.preview_button = QPushButton("Preview selected repairs")
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self.preview)
        self.apply_button = QPushButton("Apply previewed repairs")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        for button in (self.preview_button, self.apply_button, self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(250)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

    def invalidate_plan(self, *_):
        self.plan = None
        if hasattr(self, "apply_button"):
            self.apply_button.setEnabled(False)
            self.preview_text.clear()

    def invalidate_review(self, *_):
        self.invalidate_plan()
        if self.runner is not None:
            self.cancel_diagnosis()
        self.report = None
        self._binding = None
        self._snap_vertices = ()
        if hasattr(self, "captured"):
            self.captured.setText("No points captured.")
        if hasattr(self, "preview_button"):
            self.preview_button.setEnabled(False)
            self.summary.setText("Thresholds changed. Review the model again.")
            self.findings.setRowCount(0)
            self.coverage.clear()
            self.export_button.setEnabled(False)
            self.checks_table.setRowCount(0)

    def _options(self):
        units = self.app.project.units
        return CleanerOptions(
            short_edge=units.parse(self.short_edge.text(), "length"),
            near_vertex=units.parse(self.near_vertex.text(), "length"),
            small_face=float(self.small_face.text()),
            sliver_quality=float(self.sliver_quality.text()),
        )

    def _assert_current(self):
        if not self._binding_is_current():
            raise ValueError("The document changed. Review the model again before repairing it.")
        if self.app.viewport.construction_active:
            raise ValueError("Finish the active sketch before repairing geometry.")

    def _binding_is_current(self):
        return (self._binding is not None and self._binding[0] is self.app.session
                and self._binding[1] is self.app.project
                and self._binding[2] == self.app.session.revision.id
                and self._binding[3] == self.app.project.geometry.revision)

    def _disabled_reason(self):
        if self.app.session.read_only:
            return "This project is read-only. Geometry review is available."
        return repair_disabled_reason(self.app.project)

    def review(self):
        if self.runner is not None:
            return
        self.invalidate_plan()
        self.report = None
        self.preview_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.findings.setRowCount(0)
        self.checks_table.setRowCount(0)
        self.coverage.clear()
        try:
            if self.app.session is not self._dialog_session or self.app.project is not self._dialog_project:
                raise ValueError("The active project changed. Close and reopen the geometry cleaner.")
            self._snap_vertices = ()
            self.captured.setText("No points captured.")
            self._requested_options = self._options()
            self._binding = (self.app.session, self.app.project, self.app.session.revision.id,
                             self.app.project.geometry.revision)
            self._started = time.perf_counter()
            from ...geometry_diagnosis_worker import snapshot_project_for_diagnosis
            snapshot = snapshot_project_for_diagnosis(self.app.project)
            self.snapshot_seconds = time.perf_counter() - self._started
            self._report_context = {"project": self.app.project.name,
                                    "document_revision": self.app.session.revision.id,
                                    "model_id": str(self.app.project.geometry.model_id),
                                    "geometry_revision": self.app.project.geometry.revision,
                                    "project_path": str(self.app.session.path or "Unsaved project"),
                                    "snapshot_seconds": self.snapshot_seconds}
            worker = DiagnosisWorker(snapshot, self._requested_options)
            self.runner = worker
            worker.completed.connect(lambda report, job=worker: self._received(job, report))
            worker.failed.connect(lambda message, job=worker: self._failed(job, message))
            worker.progress_changed.connect(lambda value, job=worker: self._progress(job, value))
            worker.finished.connect(lambda job=worker: self._finished(job))
            _retain_worker(worker)
            self.review_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.options_tabs.setEnabled(False)
            self._progress_text = "Starting complete diagnosis"
            self._update_elapsed()
            self._elapsed_timer.start()
            worker.start()
        except Exception as error:
            self.summary.setText("Diagnosis did not start.")
            self.feedback.setText(str(error))

    def _progress(self, worker, value):
        if worker is self.runner and not worker.cancelled:
            self._progress_text = value
            self._update_elapsed()

    def _update_elapsed(self):
        if self.runner is not None and self._started is not None:
            self.summary.setText(f"{self._progress_text} — {time.perf_counter() - self._started:.1f} s")

    def _received(self, worker, report):
        if worker is not self.runner or worker.cancelled:
            return
        if not self._binding_is_current():
            self.summary.setText("The document changed during diagnosis. Run diagnosis again.")
            self.feedback.setText("The stale result was discarded; the model was not changed.")
            return
        self.report = report
        self._elapsed_timer.stop()
        self.delivery_seconds = time.perf_counter() - self._started
        pages = max(1, (len(report.findings) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page.setRange(1, pages)
        self.page.setValue(1)
        self.render_findings()
        self.checks_table.setRowCount(len(report.checks))
        for row, check in enumerate(report.checks):
            for column, text in enumerate((check.name.replace("_", " "), check.status,
                                            f"{check.detail} ({check.candidates} candidate(s))")):
                self.checks_table.setItem(row, column, QTableWidgetItem(text))
        self.checks_table.setColumnWidth(0, 170)
        self.checks_table.setColumnWidth(1, 100)
        summary, groups = diagnosis_summary(report)
        self.summary.setText(summary + f" Delivered in {self.delivery_seconds:.2f} s.")
        self.grouped_findings.setRowCount(len(groups))
        for row, (category, kind, count, affected) in enumerate(groups):
            for column, value in enumerate((category, kind.replace('_', ' '), str(count), affected)):
                self.grouped_findings.setItem(row, column, QTableWidgetItem(value))
        self.grouped_findings.resizeColumnsToContents()
        self.coverage.setText(f"{len(report.checks)} check groups; "
                              f"{report.counts['incomplete_checks']} incomplete. "
                              "Checks authored geometry before mesh connectivity preparation; this is not a verdict on the prepared mesh. "
                              "Coverage limitations are unverified checks, not confirmed model defects. "
                              "See Diagnosis coverage and Finding groups. All raw findings remain listed; "
                              "export includes every finding and the full geometry audit.")
        reason = self._disabled_reason()
        self.feedback.setText(reason or "Locate findings, then select conservative repairs in Repair options.")
        self.preview_button.setEnabled(reason is None and report.quality is not None)
        self.export_button.setEnabled(True)

    def render_findings(self, *_):
        if self.report is None:
            self.findings.setRowCount(0)
            return
        start = (self.page.value() - 1) * self.PAGE_SIZE
        visible = self.report.findings[start:start + self.PAGE_SIZE]
        self.findings.blockSignals(True)
        try:
            self.findings.setRowCount(0)
            self.findings.setRowCount(len(visible))
            for row, finding in enumerate(visible):
                references = ", ".join(f"{ref.kind} {ref.id}" for ref in finding.refs[:10])
                if len(finding.refs) > 10:
                    references += f" (+{len(finding.refs) - 10} more)"
                details = finding.message + (" Action: " + finding.action if finding.action else "")
                unit = ({"small_face": "m²", "sliver_face": "", "short_edge": "m",
                         "near_vertices": "m", "duplicate_vertices": "m", "near_point_edge": "m",
                         "near_point_face": "m", "near_edges": "m"}.get(finding.kind, ""))
                if finding.owner_issue is not None:
                    unit = (self.report.source or {}).get("units", "source units")
                elif any(name == "gap_m" for name, _ in finding.measurements):
                    unit = "m"
                measurement = "" if finding.value is None else f"{finding.value:.8g} {unit}".strip()
                values = (finding.kind.replace("_", " "), references,
                          measurement, finding.severity, details)
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value if column == 1 else finding.message)
                    if column == 0:
                        item.setData(Qt.UserRole, finding)
                    self.findings.setItem(row, column, item)
            for column, width in enumerate((155, 210, 115, 70)):
                self.findings.setColumnWidth(column, width)
        finally:
            self.findings.blockSignals(False)
        self.page_summary.setText(f"{start + 1 if visible else 0}–{start + len(visible)} of {len(self.report.findings)}")
        self.invalidate_plan()

    def _failed(self, worker, message):
        if worker is self.runner:
            self._elapsed_timer.stop()
            self._received(worker, self._terminal_report("failed", message))
            self.feedback.setText(message)

    def _terminal_report(self, status, message):
        from anygeometry.cleaner_diagnostics import DiagnosisReport, DiagnosisCheck, DiagnosisFinding
        source = dict(self._report_context or {})
        return DiagnosisReport(
            source.get("model_id", "unavailable"), source.get("geometry_revision", 0),
            self._requested_options,
            (DiagnosisFinding("process:" + status, "diagnosis_" + status, (), message, severity="blocker"),),
            (DiagnosisCheck("diagnosis_process", status, message),),
            ("No unchecked category is a pass. This is a process-status report, not a completed geometry diagnosis.",),
            (("delivery", time.perf_counter() - self._started),), source=source,
        )

    def _finished(self, worker):
        if worker is not self.runner:
            return
        self.runner = None
        self._elapsed_timer.stop()
        self.review_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.options_tabs.setEnabled(True)
        if worker.cancelled:
            # Process cancellation deliberately discards any untransferred
            # partial result. Preserve an exportable non-success status.
            if self._binding_is_current() and self._pending_close is None:
                status = "cancelled" if worker.retired else "failed"
                message = ("Diagnosis cancelled before a complete result was received." if worker.retired
                           else "Diagnosis cancellation could not verify process cleanup; no result is accepted.")
                self.report = self._terminal_report(status, message)
                self.render_findings()
                self.export_button.setEnabled(True)
                self.checks_table.setRowCount(1)
                for column, text in enumerate(("Diagnosis process", status, message)):
                    self.checks_table.setItem(0, column, QTableWidgetItem(text))
                self.coverage.setText("Diagnosis incomplete. Unchecked categories are not passes.")
            self.summary.setText("Diagnosis cancelled. No complete diagnosis was produced." if worker.retired
                                 else "Diagnosis cleanup failed. No result is accepted.")
        self._finish_pending_close()

    def cancel_diagnosis(self):
        if self.runner is not None:
            self.runner.cancel()
            self.cancel_button.setEnabled(False)
            self._progress_text = "Cancelling diagnosis and cleaning up its process"
            self._update_elapsed()

    def export_report(self):
        if self.report is None or self.exporter is not None:
            return
        path, chosen_filter = QFileDialog.getSaveFileName(self, "Export full geometry diagnosis", "geometry-diagnosis.json",
                                             "JSON report (*.json);;Markdown report (*.md);;Text report (*.txt)")
        if path:
            if not Path(path).suffix:
                path += ".md" if "Markdown" in chosen_filter else ".txt" if "Text" in chosen_filter else ".json"
            self.save_report(path)

    def save_report(self, path):
        if self.report is None:
            raise ValueError("Run diagnosis before exporting a report.")
        if self.exporter is not None:
            raise RuntimeError("A report export is already running.")
        document = dict(self._report_context or {})
        document["current_document_matches_snapshot"] = self._binding_is_current()
        document["delivery_seconds"] = getattr(self, "delivery_seconds", None)
        worker = ReportExportWorker(self.report, path, document)
        self.exporter = worker
        _retain_worker(worker)
        worker.completed.connect(lambda target: self.feedback.setText(f"Full diagnosis report saved: {target}"))
        worker.failed.connect(lambda message: self.feedback.setText(message))
        worker.finished.connect(lambda job=worker: self._export_finished(job))
        self.export_button.setEnabled(False)
        worker.start()
        return worker

    def _export_finished(self, worker):
        if worker is self.exporter:
            self.exporter = None
            self.export_button.setEnabled(self.report is not None)
            self._finish_pending_close()

    def _finish_pending_close(self):
        if self._pending_close is not None and self.runner is None and self.exporter is None:
            result, self._pending_close = self._pending_close, None
            self.done(result)

    def selected_findings(self):
        return tuple(self.findings.item(index.row(), 0).data(Qt.UserRole)
                     for index in self.findings.selectionModel().selectedRows()
                     if self.findings.item(index.row(), 0) is not None)

    def highlight(self):
        self.invalidate_plan()
        if self.report is None:
            return
        try:
            self._assert_current()
            refs = tuple(dict.fromkeys(ref for finding in self.selected_findings() for ref in finding.refs))
            geometry = self.app.project.geometry
            stores = {"vertex": geometry.vertices, "edge": geometry.edges, "face": geometry.faces}
            display_refs = tuple(ref for ref in refs if ref.kind in stores and ref.id in stores[ref.kind])
            self.app.show_geometry()
            self.app.selection.restore(display_refs)
            self._did_highlight = True
            if refs and not display_refs:
                self.feedback.setText("These references are missing geometry or structural owners. See the full report and model tree.")
        except Exception as error:
            self.feedback.setText(str(error))

    def capture_points(self):
        self.invalidate_plan()
        try:
            self._assert_current()
            selected = tuple(self.app.selection.items)
            if not selected or any(not isinstance(ref, EntityRef) or ref.kind != "vertex" for ref in selected):
                raise ValueError("Select only model points, or select a nearby-points finding first.")
            self._snap_vertices = tuple(dict.fromkeys(ref.id for ref in selected))
            self.captured.setText(f"{len(self._snap_vertices)} point(s): "
                                  + ", ".join(str(value) for value in self._snap_vertices[:20])
                                  + (" …" if len(self._snap_vertices) > 20 else ""))
        except Exception as error:
            self.feedback.setText(str(error))

    def preview(self):
        self.invalidate_plan()
        try:
            self._assert_current()
            reason = self._disabled_reason()
            if reason:
                raise PermissionError(reason)
            if self.report is None:
                raise ValueError("Review the model first.")
            findings = self.selected_findings() if self.remove_unused.isChecked() else ()
            if any(not finding.repairable for finding in findings):
                raise ValueError("Select only removable isolated-point findings for removal.")
            if self.snap.isChecked() and not self._snap_vertices:
                raise ValueError("Capture selected model points before previewing a grid snap.")
            units = self.app.project.units
            # Detached diagnosis results carry no authority over the live model.
            # Reissue the quick quality review on the current exact source state.
            live_review = review_project(self.app.project, self._requested_options)
            plan = preview_project_repair(
                self.app.project, live_review, finding_ids=tuple(finding.id for finding in findings),
                snap_vertices=self._snap_vertices if self.snap.isChecked() else (),
                grid_size=units.parse(self.grid_size.text(), "length") if self.snap.isChecked() else None,
                max_displacement=units.parse(self.max_displacement.text(), "length") if self.snap.isChecked() else None,
            )
            self.plan = plan
            actions = {"remove_unused_vertex": "Remove isolated point", "snap_vertex": "Snap point to grid"}
            def coordinates(value):
                if value is None:
                    return "removed"
                return "(" + ", ".join(f"{units.to_display(x, 'length'):.10g}" for x in value) + ")"
            lines = [f"Coordinates in {units.symbol('length')}."] if plan.changes else []
            lines.extend(
                f"{actions[change.operation]} {change.ref.id}: {coordinates(change.before)} → {coordinates(change.after)}"
                for change in plan.changes
            )
            self.preview_text.setPlainText("\n".join(lines))
            self.feedback.setText("No geometry changes proposed." if plan.is_noop
                                  else f"{len(plan.changes)} change(s) previewed. Apply creates one Undo action.")
            self.apply_button.setEnabled(not plan.is_noop)
        except Exception as error:
            self.feedback.setText(str(error))

    def apply(self):
        try:
            self._assert_current()
            if self.plan is None:
                raise ValueError("Preview selected repairs before applying them.")
            reason = self._disabled_reason()
            if reason:
                raise PermissionError(reason)
            command = command_for_project_repair(self.app.project, self.plan, read_only=self.app.session.read_only)
            if command is None:
                self.invalidate_plan()
                self.feedback.setText("No geometry changes proposed.")
                return
            self.app.run(command)
            self.app.show_geometry()
            self._snap_vertices = ()
            self.captured.setText("No points captured.")
            self.invalidate_review()
            self.summary.setText("Repairs applied. Review again to inspect the updated model.")
            self.feedback.setText("Geometry repaired. Undo restores the previous model.")
        except Exception as error:
            self.invalidate_plan()
            self.feedback.setText(str(error))

    def done(self, result):
        if self.runner is not None or self.exporter is not None:
            self._pending_close = result
            self.cancel_diagnosis()
            if self.exporter is not None:
                self.exporter.cancel()
            return
        session, project, refs = self._highlight_binding
        if self._did_highlight and self.app.session is session and self.app.project is project:
            geometry = project.geometry
            stores = {"vertex": geometry.vertices, "edge": geometry.edges, "face": geometry.faces}
            self.app.selection.restore(
                ref for ref in refs
                if (isinstance(ref, EntityRef) and ref.kind in stores and ref.id in stores[ref.kind])
                or (not isinstance(ref, EntityRef) and self.app.mesh is not None and self.app.mesh is self._original_mesh)
            )
        super().done(result)


def open_geometry_cleaner(app):
    application = QApplication.instance()
    if application is not None and not getattr(application, "_cleaner_shutdown_connected", False):
        application.aboutToQuit.connect(_shutdown_workers)
        application._cleaner_shutdown_connected = True
    existing = getattr(app, "_geometry_cleaner_dialog", None)
    if existing is not None and existing.isVisible():
        existing.raise_()
        existing.activateWindow()
        return existing
    dialog = GeometryCleanerDialog(app)
    app._geometry_cleaner_dialog = dialog
    dialog.finished.connect(lambda _: setattr(app, "_geometry_cleaner_dialog", None))
    dialog.show()
    return dialog
