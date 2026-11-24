"""Help > Loaded packages dialog (Qt).

Kept separate from the collector so that the first Help click loads only this
small Qt module on the GUI thread; the collector, its parser imports and every
provenance read run on a worker thread.
"""
from __future__ import annotations

import os
import sys
import time

COLUMNS = ("Package", "Version", "Version source", "Loaded from", "Commit", "Notes")


def _same(path: str) -> str:
    """Lexical comparison key (no file-system access on the GUI thread)."""
    return os.path.normcase(os.path.normpath(path))


def _fill_table(table, report):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QHeaderView, QTableWidgetItem

    table.setRowCount(len(report.rows))
    for index, row in enumerate(report.rows):
        if row.loaded:
            resolved = row.real_path and _same(row.real_path) != _same(row.import_path)
            path = row.import_path + (f"\n→ {row.real_path}" if resolved else "")
            flagged = any(note.startswith("version sources disagree") for note in row.notes)
            values = (row.name, ("⚠ " if flagged else "") + row.version, row.version_source,
                      path, row.commit, "; ".join(row.notes))
        else:
            values = (row.name, "—", "not loaded in this session", "", "", "")
        for column, value in enumerate(values):
            # The commit cell names the checkout in its tooltip and the copied text.
            item = QTableWidgetItem(value.split(" in ", 1)[0] if column == 4 else value)
            item.setToolTip(value)
            if not row.loaded:
                item.setForeground(QColor(128, 128, 128))
            table.setItem(index, column, item)
    header = table.horizontalHeader()
    table.resizeColumnsToContents()
    header.setSectionResizeMode(QHeaderView.Interactive)
    header.setStretchLastSection(True)
    table.resizeRowsToContents()


def open_loaded_packages(window):
    """Show the dialog at once; collect on a worker thread and fill it in.

    Provenance reads touch the file system (``sys.path`` scans, ``.git``,
    ``pyproject.toml``), which can stall on a network share, so none of it runs
    on the GUI thread.  ``sys.modules`` is snapshotted here, before the thread
    starts.  A dialog closed early simply drops the late result.
    """
    import threading
    from html import escape

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QLabel,
                                   QPushButton, QTableWidget, QVBoxLayout)

    opened = time.perf_counter()
    modules = dict(sys.modules)
    dialog = QDialog(window)
    dialog.setObjectName("LoadedPackagesDialog")
    dialog.setWindowTitle("Loaded packages")
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.report = None
    dialog.error = None
    dialog.ready_ms = None
    layout = QVBoxLayout(dialog)
    summary = QLabel("Collecting loaded packages…", dialog)
    summary.setObjectName("LoadedPackagesSummary")
    summary.setTextFormat(Qt.RichText)  # paths are not markup: only the summary's own <br>/<i> tags
    summary.setWordWrap(True)
    summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
    layout.addWidget(summary)
    table = QTableWidget(0, len(COLUMNS), dialog)
    table.setObjectName("LoadedPackagesTable")
    table.setHorizontalHeaderLabels(COLUMNS)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setWordWrap(False)
    table.verticalHeader().setVisible(False)
    layout.addWidget(table)
    buttons = QDialogButtonBox(QDialogButtonBox.Close, dialog)
    copy = QPushButton("Copy to clipboard", dialog)
    copy.setObjectName("CopyLoadedPackages")
    copy.setEnabled(False)
    buttons.addButton(copy, QDialogButtonBox.ActionRole)
    buttons.rejected.connect(dialog.close)
    layout.addWidget(buttons)

    def copy_report():
        if dialog.report is None:
            return
        try:
            window.clipboard.copy_text(dialog.report.text())
        except Exception as error:  # e.g. the platform clipboard refused the text
            message = f"Could not copy to the clipboard: {type(error).__name__}: {str(error)[:200]}"
            copy.setToolTip(message)
            try:
                window.set_status(message)
            except Exception:
                pass
            return
        try:
            window.set_status("Loaded package list copied to clipboard.")
        except Exception:
            pass

    copy.clicked.connect(copy_report)

    # No Qt object crosses threads: the worker fills a plain box and sets an
    # event; a timer owned by the dialog polls it on the GUI thread, so the
    # poller, its closure and the dialog go away together.
    box = {}
    ready = threading.Event()

    def finished(report, error):
        if error is not None:
            dialog.error = error
            summary.setText(escape(f"Could not collect the package list: {type(error).__name__}: {str(error)[:300]}"))
            return
        dialog.report = report
        dialog.ready_ms = (time.perf_counter() - opened) * 1000.0
        summary.setText(
            f"Python {escape(report.python)} — {escape(report.executable)}<br>"
            f"Qt runtime {escape(report.qt_runtime)} — collected in {report.elapsed_ms:.0f} ms (background)<br>"
            f"<i>{escape(report.notes[0])}<br>{escape(report.notes[1])}</i>")
        _fill_table(table, report)
        copy.setEnabled(True)
        layout.activate()  # the summary grew from one line to three

    poller = QTimer(dialog)
    poller.setObjectName("LoadedPackagesPoller")
    poller.setInterval(25)

    def poll():
        if ready.is_set():
            poller.stop()
            finished(box.get("report"), box.get("error"))

    poller.timeout.connect(poll)

    def work():  # touches only the snapshot, the box and the event
        try:
            # The collector (and its parser imports) loads here, not on the GUI thread.
            from . import loaded_packages
            box["report"] = loaded_packages.collect_loaded_packages(modules)
        except Exception as caught:  # collect is guarded per row; this is the last net
            box["error"] = caught
        finally:
            ready.set()

    dialog.resize(1400, 640)
    window._loaded_packages_dialog = dialog
    dialog.show()
    dialog.shown_ms = (time.perf_counter() - opened) * 1000.0
    threading.Thread(target=work, name="anyfem-loaded-packages", daemon=True).start()
    poller.start()
    return dialog
