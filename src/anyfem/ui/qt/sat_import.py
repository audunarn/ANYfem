"""Atomic, cancellable SAT geometry import for the Qt workbench."""
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QLabel, QLineEdit, QProgressDialog, QVBoxLayout,
)

from ...application.sat_import import OPT_IN_FIELD_LABELS, import_sat_project, opt_in_hint, parse_tolerance_text


class _Signals(QObject):
    finished = Signal(object, object)


class SatImportOptionsDialog(QDialog):
    """Two optional reader tolerances; empty means the opt-in is off."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SAT import options")
        self.vertex_relocation = QLineEdit()
        self.approximate_curve = QLineEdit()
        for field in (self.vertex_relocation, self.approximate_curve):
            field.setPlaceholderText("off")
        self.error = QLabel("")
        self.error.setWordWrap(True)
        form = QFormLayout()
        form.addRow(OPT_IN_FIELD_LABELS["vertex_relocation_tolerance_m"], self.vertex_relocation)
        form.addRow(OPT_IN_FIELD_LABELS["approximate_curve_tolerance_m"], self.approximate_curve)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Leave a field empty to keep the strict default."))
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def options(self):
        values = {
            "vertex_relocation_tolerance_m": parse_tolerance_text(
                self.vertex_relocation.text(), OPT_IN_FIELD_LABELS["vertex_relocation_tolerance_m"]),
            "approximate_curve_tolerance_m": parse_tolerance_text(
                self.approximate_curve.text(), OPT_IN_FIELD_LABELS["approximate_curve_tolerance_m"]),
        }
        return {key: value for key, value in values.items() if value is not None}

    def accept(self):
        try:
            self.options()
        except ValueError as error:
            self.error.setText(str(error))
            return
        super().accept()


class _Reader(QRunnable):
    def __init__(self, path, options=None):
        super().__init__()
        self.path = path
        self.options = dict(options or {})
        self.cancelled = Event()
        self.signals = _Signals()

    def run(self):
        try:
            result = import_sat_project(self.path, cancelled=self.cancelled.is_set, **self.options)
        except Exception as error:
            detail = str(error) or type(error).__name__
            diagnostics = tuple(getattr(error, "diagnostics", ()))
            field = opt_in_hint(detail) or next((opt_in_hint(str(d)) for d in diagnostics if opt_in_hint(str(d))), None)
            if diagnostics:
                detail += "\n\n" + "\n".join(map(str, diagnostics[:5]))
                if len(diagnostics) > 5:
                    detail += f"\n… {len(diagnostics)-5} further diagnostics"
            if field:
                detail += f"\n\nTo allow this, set '{field}' in the SAT import options and import again."
            self.signals.finished.emit(None, detail)
        else:
            self.signals.finished.emit(result, None)


class SatImport(QObject):
    def __init__(self, window, path, options=None):
        super().__init__(window)
        self.window = window
        self.source_project = window.project
        self.revision = window.session.revision
        self.cancelled = False
        self.dialog = QProgressDialog("Reading SAT geometry…", "Cancel", 0, 0, window)
        self.dialog.setWindowTitle("Import SAT geometry")
        self.dialog.setWindowModality(Qt.WindowModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.canceled.connect(self.cancel)
        self.reader = _Reader(path, options)
        self.reader.signals.finished.connect(self.finished)
        self.dialog.show()
        QThreadPool.globalInstance().start(self.reader)

    @Slot()
    def cancel(self):
        self.cancelled = True
        self.reader.cancelled.set()

    @Slot(object, object)
    def finished(self, result, error):
        window = self.window
        self.dialog.canceled.disconnect(self.cancel)
        self.dialog.close()
        window._sat_import = None
        if self.cancelled or getattr(window, "_closing", False):
            self.deleteLater()
            return
        if error is not None:
            window.dialogs.show_error("SAT import", error)
        elif window.project is not self.source_project or window.session.revision != self.revision:
            window.set_status("SAT import finished after the document changed. Import the file again.")
        else:
            project, report = result
            window._set_project(project)
            window.session.dirty = True
            window._last_sat_import_report = report
            window.show_geometry(reset_view=True)
            window.set_status(
                f"Imported {len(project.geometry.faces)} SAT faces and "
                f"{len(project.geometry.edges)} edges. Assign materials and sections for stock takeoff."
            )
            window._schedule_autosave()
        self.deleteLater()


def open_sat_import(window, path=None, options=None):
    if getattr(window, "_sat_import", None) is not None:
        raise ValueError("A SAT import is already running.")
    if path is None:
        path, _ = QFileDialog.getOpenFileName(window, "Import SAT geometry", "", "ACIS SAT (*.sat *.SAT)")
        if not path:
            return
        dialog = SatImportOptionsDialog(window)
        if dialog.exec() != QDialog.Accepted:
            return
        options = dialog.options()
    if not window._confirm_discard():
        return
    window._sat_import = SatImport(window, Path(path), options)
