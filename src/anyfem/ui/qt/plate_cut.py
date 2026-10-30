"""Construction-plane or finite-plate cutter dialog."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                              QLineEdit, QComboBox, QVBoxLayout)
from ... import commands


class PlateCutDialog(QDialog):
    def __init__(self, actions, face_id):
        super().__init__(actions.app)
        self.actions, self.face_id = actions, face_id
        self.binding = actions.binding()
        self.setWindowTitle("Cut plate / create beam edge")
        self.setObjectName("PlateCutDialog")
        self.setAttribute(Qt.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        guidance = QLabel("Keep both sides of the plate and select the exact cut edge.\n"
                          "Then use Create beam from edge… and choose a section.\n"
                          "Unsupported or ambiguous intersections are refused.")
        guidance.setWordWrap(True); layout.addWidget(guidance)
        form = QFormLayout(); layout.addLayout(form)
        self.mode = QComboBox()
        self.mode.addItems(("Explicit plane", "Active construction plane", "Another plate (finite extent)"))
        form.addRow("Cut with", self.mode)
        self.origin = QLineEdit("0, 0, 0")
        form.addRow(f"Origin ({actions.app.project.units.symbol('length')})", self.origin)
        self.normal = QLineEdit("1, 0, 0"); form.addRow("Plane normal", self.normal)
        self.cutter = QComboBox()
        for identifier in actions.app.project.geometry.faces:
            if identifier != face_id:self.cutter.addItem(f"Plate face {identifier}", identifier)
        form.addRow("Cutter plate", self.cutter)
        self.mode.currentIndexChanged.connect(self.update_fields); self.update_fields()
        self.feedback = QLabel(); self.feedback.setWordWrap(True); layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Cut and select trace")
        buttons.accepted.connect(self.apply); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def update_fields(self):
        explicit = self.mode.currentIndex() == 0
        self.origin.setEnabled(explicit); self.normal.setEnabled(explicit)
        self.cutter.setEnabled(self.mode.currentIndex() == 2)

    def apply(self):
        try:
            self.actions.assert_binding(self.binding)
            app = self.actions.app
            if app.session.read_only or app.project.read_only_reason:raise PermissionError("This project is read-only")
            mode = self.mode.currentIndex()
            if mode == 2:
                if self.cutter.currentData() is None:raise ValueError("Create or select another plate first.")
                command = commands.CutPlate(self.face_id, cutter_face_id=self.cutter.currentData())
            else:
                if mode == 1:
                    from ...model.workplanes import Workplane
                    plane = app.session.active_workplane or Workplane()
                    frame = plane.resolve(app.panels["Construction"].coordinate_systems())
                    origin, normal = frame.origin, frame.normal
                else:
                    origin = tuple(app.project.units.parse(value.strip(), "length") for value in self.origin.text().split(","))
                    normal = tuple(float(value.strip()) for value in self.normal.text().split(","))
                command = commands.CutPlate(self.face_id, origin=origin, normal=normal)
            app.run(command)
            result = command._result
            app.selection.set_mode("edge")
            app.selection.clear()
            for edge in result.trace_edges:
                app.selection.select(app.project.geometry.entity_ref("edge", edge), extend=True)
            app.show_geometry()
        except (ValueError, RuntimeError, PermissionError) as error:
            self.feedback.setText(str(error)); return
        self.accept()
