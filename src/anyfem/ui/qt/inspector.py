"""Qt file inspector: async reads, source records, diagnostics and canonical FEM."""
import json
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QPushButton,QPlainTextEdit,QLabel
from ...presentation.file_inspection import inspect_file
from .table import ResultTable


class FileInspector(QDialog):
    def __init__(self,app,path=None):
        super().__init__(app);self.app=app;self.result=None;self._epoch=0;self._closed=False
        self.setAttribute(Qt.WA_DeleteOnClose);self.setWindowTitle("File inspector");self.resize(900,700)
        layout=QVBoxLayout(self);self.status=QLabel("Open a structural file to inspect it");layout.addWidget(self.status)
        self.summary=QPlainTextEdit();self.summary.setReadOnly(True);layout.addWidget(self.summary)
        self.records=ResultTable();layout.addWidget(self.records);self.diagnostics=ResultTable();layout.addWidget(self.diagnostics)
        for label,callback in [("Open file",self.open),("Save JSON report",self.save_report),("Write canonical FEM",self.canonicalize)]:
            button=QPushButton(label);button.clicked.connect(app.guarded(callback));layout.addWidget(button)
        if path:self.load(path)

    def open(self):
        path=self.app.dialogs.open_file(filetypes=[("Structural files","*.fem *.FEM *.sif *.SIF *.inp *.frd *.dat")])
        if path:self.load(path)

    def load(self,path):
        self._epoch+=1;epoch=self._epoch;self.result=None;self.status.setText(f"Reading {path}")
        future=self.app._artifact_executor.submit(inspect_file,path)
        def poll():
            if self._closed or self.app._closing or epoch!=self._epoch:return
            if not future.done():self.app.scheduler.call_later(50,poll);return
            def apply():
                self.result=future.result();self.summary.setPlainText(json.dumps(self.result.summary,indent=2,default=str))
                self.records.show_rows(["Record","Source lines","First values"],self.result.records)
                self.diagnostics.show_rows(["Severity","Code","Line","Message"],[(item.severity,item.code,item.line_start or "",item.message) for item in self.result.diagnostics])
                self.status.setText(f"{self.result.kind}: {len(self.result.diagnostics)} diagnostics; {len(self.result.records)} records previewed")
            self.app.guarded(apply)()
        self.app.scheduler.call_later(50,poll)

    def save_report(self):
        if self.result is None:raise ValueError("Open a file first")
        path=self.app.dialogs.save_file(filetypes=[("JSON report","*.json")],defaultextension=".json")
        if path:Path(path).write_text(json.dumps(self.result.report(),indent=2,default=str)+"\n",encoding="utf-8")

    def canonicalize(self):
        if self.result is None or self.result.document is None:raise ValueError("Open a SESAM FEM/SIF document first")
        path=self.app.dialogs.save_file(filetypes=[("SESAM FEM","*.FEM")],defaultextension=".FEM")
        if path:
            from anyfileio.sesam.exporter import write_sesam_fem_document
            report=write_sesam_fem_document(self.result.document,path,overwrite=True)
            self.status.setText(f"Wrote {report.records_written} records ({report.bytes_written} bytes)")

    def closeEvent(self,event):
        self._closed=True;self._epoch+=1;super().closeEvent(event)
