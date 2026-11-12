"""Atomic, cancellable SAT geometry import for the Qt workbench."""
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import QFileDialog, QProgressDialog

from ...application.sat_import import import_sat_project


class _Signals(QObject):
    finished = Signal(object, object)


class _Reader(QRunnable):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.cancelled = Event()
        self.signals = _Signals()

    def run(self):
        try:
            result = import_sat_project(self.path, cancelled=self.cancelled.is_set)
        except Exception as error:
            detail = str(error) or type(error).__name__
            diagnostics = tuple(getattr(error, "diagnostics", ()))
            if diagnostics:
                detail += "\n\n" + "\n".join(map(str, diagnostics[:5]))
                if len(diagnostics) > 5:
                    detail += f"\n… {len(diagnostics)-5} further diagnostics"
            self.signals.finished.emit(None, detail)
        else:
            self.signals.finished.emit(result, None)


class SatImport(QObject):
    def __init__(self, window, path):
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
        self.reader = _Reader(path)
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


def open_sat_import(window, path=None):
    if getattr(window, "_sat_import", None) is not None:
        raise ValueError("A SAT import is already running.")
    if path is None:
        path, _ = QFileDialog.getOpenFileName(window, "Import SAT geometry", "", "ACIS SAT (*.sat *.SAT)")
        if not path:
            return
    if not window._confirm_discard():
        return
    window._sat_import = SatImport(window, Path(path))
