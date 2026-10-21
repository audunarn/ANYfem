"""Preview a linked sketch opening before publishing a document command."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QListWidget, QListWidgetItem,
)

from ... import commands
from ...ui.scene import build_geometry_scene


class SketchCutDialog(QDialog):
    def __init__(self, app, sketch_feature_id=None):
        super().__init__(app)
        self.app = app
        self.command = None
        self._scene = app.viewport._scene
        self._interaction_scene = app.viewport._interaction_scene
        self.setWindowTitle("Cut plate with sketch")
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        hint = QLabel("Choose a plate and a closed sketch. Preview the opening, then Cut plate.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        layout.addLayout(form)
        self.sketch = QComboBox()
        self.plate = QComboBox()
        self.name = QLineEdit("Plate cut")
        self.contours = QListWidget()
        self.contours.setMaximumHeight(110)
        for record in app.project.geometry.features.records:
            if record.kind in {"geometry.sketch.plane", "geometry.sketch.extrude"} and not record.suppressed:
                self.sketch.addItem(record.name or f"Sketch {record.feature_id}", record.feature_id)
        for identifier in app.project.geometry.faces:
            self.plate.addItem(f"Plate {identifier}", identifier)
        if sketch_feature_id is not None:
            self.sketch.setCurrentIndex(self.sketch.findData(int(sketch_feature_id)))
        for ref in app.selection.ordered_items:
            if ref.kind == "face":
                self.plate.setCurrentIndex(self.plate.findData(ref.id))
                break
        form.addRow("Sketch", self.sketch)
        form.addRow("Plate", self.plate)
        form.addRow("Closed contours", self.contours)
        form.addRow("Cut name", self.name)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        preview = QPushButton("Preview opening")
        preview.clicked.connect(self.preview)
        layout.addWidget(preview)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.apply_button = self.buttons.button(QDialogButtonBox.Ok)
        self.apply_button.setText("Cut plate")
        self.apply_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.sketch.currentIndexChanged.connect(self.refresh_contours)
        self.plate.currentIndexChanged.connect(self.invalidate)
        self.contours.itemChanged.connect(self.invalidate)
        self.name.textEdited.connect(self.invalidate)
        self.refresh_contours()

    def refresh_contours(self, *_):
        from anygeometry import SketchDefinition, upgrade_sketch

        self.contours.blockSignals(True)
        self.contours.clear()
        identifier = self.sketch.currentData()
        if identifier is not None:
            source = self.app.project.geometry.features.get(int(identifier))
            definition = upgrade_sketch(SketchDefinition.from_parameters(source.parameters))
            number = 0
            entities = {entity.id: entity for entity in definition.entities}
            for wire in definition.wires:
                if wire.closed:
                    number += 1
                    description = (entities[wire.entities[0]].kind.title()
                                   if len(wire.entities) == 1 else f"{len(wire.entities)} segments")
                    item = QListWidgetItem(f"Contour {number} ({description})", self.contours)
                    item.setData(Qt.UserRole, wire.id)
                    item.setCheckState(Qt.Checked)
        self.contours.blockSignals(False)
        self.invalidate()

    def invalidate(self, *_):
        self.command = None
        self.apply_button.setEnabled(False)
        self.restore_view()
        self.status.setText("Preview the selected plate and sketch to validate the opening.")

    def restore_view(self):
        if self._scene is not None:
            self.app.viewport.show(self._scene, interaction_scene=self._interaction_scene)

    def preview(self):
        from anygeometry import EntityRef

        self.command = None
        self.apply_button.setEnabled(False)
        try:
            if self.sketch.currentData() is None or self.plate.currentData() is None:
                raise ValueError("Create a closed sketch and a plate first")
            if not self.name.text().strip():
                raise ValueError("Enter a cut name")
            wires = tuple(self.contours.item(i).data(Qt.UserRole)
                          for i in range(self.contours.count())
                          if self.contours.item(i).checkState() == Qt.Checked)
            if not wires:
                raise ValueError("Select at least one closed contour")
            command = commands.CutPlateWithSketch(
                EntityRef("face", int(self.plate.currentData())),
                int(self.sketch.currentData()), wire_ids=wires, name=self.name.text().strip(),
            )
            staged = commands._stage_feature_project(self.app.project)
            feature = command.do(staged)
            self.app.viewport.show(build_geometry_scene(staged))
            self.command = commands.CutPlateWithSketch(
                command.target_face, command.sketch_feature_id,
                wire_ids=tuple(feature.parameters["wire_ids"]), name=command.name,
            )
            self.status.setText("Opening preview is valid. Editing the source sketch will update this cut.")
            self.apply_button.setEnabled(True)
        except (ValueError, KeyError, RuntimeError) as error:
            self.restore_view()
            self.status.setText(str(error))

    def done(self, result):
        self.restore_view()
        super().done(result)


def cut_plate_with_sketch(app, sketch_feature_id=None):
    if app.session.read_only:
        raise PermissionError("This project is read-only")
    if app.viewport.construction_active:
        raise ValueError("Finish the sketch before cutting a plate")
    dialog = SketchCutDialog(app, sketch_feature_id)
    try:
        if dialog.exec() != QDialog.Accepted or dialog.command is None:
            return None
        app._begin_modelling()
        feature = app.run(dialog.command)
        app.show_geometry()
        app.set_status(f"Created {feature.name}. Edit its source sketch to change the opening.")
        return feature
    finally:
        dialog.deleteLater()
