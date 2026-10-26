"""Direct two-profile construction and an optional advanced loft editor."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
)
import numpy as np

from ... import commands
from ..scene import Arrow, Polyline, build_geometry_scene, face_normal


def selected_loft_profiles(app):
    """Adapt two current selection intents without expanding circles into clicks."""
    from anygeometry import EntityRef, feature_entity_owners
    from anygeometry.loft import loft_profiles_from_sketch

    geometry = app.project.geometry
    intents = list(app.selection.ordered_items)
    active_features = getattr(app.viewport, "_feature_ids", frozenset())
    for index in app.tree.selectionModel().selectedRows():
        value = index.data(Qt.UserRole)
        if (isinstance(value, (tuple, list)) and len(value) == 2
                and value[0] == "geometry" and value[1] in active_features):
            intents.append(("geometry", int(value[1])))
    if len(intents) != 2:
        raise ValueError("Select exactly two lines, profile faces or sketch profiles, then Construct.")
    owners = feature_entity_owners(geometry)

    def sketch_profile(feature_id, edge_id=None):
        profiles = loft_profiles_from_sketch(geometry, feature_id)
        if edge_id is not None:
            profiles = tuple(item for item in profiles if edge_id in item[1])
        if len(profiles) != 1:
            raise ValueError("This sketch has more than one profile or no active profile. Select an edge of the intended chain.")
        return tuple(profiles[0][1])

    def resolve(intent):
        if isinstance(intent, tuple) and intent[0] == "geometry":
            return sketch_profile(intent[1])
        if not isinstance(intent, EntityRef):
            raise ValueError("Select geometry profiles, not mesh entities or points.")
        if intent.kind == "face":
            face = geometry.faces.get(intent.id)
            if face is None:
                raise ValueError("The selected profile face no longer exists. Select it again.")
            if face.holes:
                raise ValueError("A loft profile face cannot contain holes. Select one boundary chain instead.")
            return tuple(use.edge for use in face.loop)
        if intent.kind != "edge" or intent.id not in geometry.edges:
            raise ValueError("Select two lines, profile faces or sketch profiles; points are not profiles.")
        feature_id = owners.get(intent)
        if feature_id is not None:
            record = geometry.features.get(feature_id)
            if record.kind in {"geometry.sketch.plane", "geometry.sketch.extrude"}:
                return sketch_profile(feature_id, intent.id)
        return (intent.id,)

    first, second = (resolve(intent) for intent in intents)
    if set(first) == set(second):
        raise ValueError("Select two different profiles; both selections belong to the same profile.")
    return first, second


def construct_selected_loft(app):
    """Construct directly as one undo item; advanced editing is a separate action."""
    from anygeometry.loft import loft_profile_options

    if app.session.read_only:
        raise PermissionError("This project is read-only")
    if app.viewport.construction_active:
        raise ValueError("Finish the sketch before constructing a loft.")
    first, second = selected_loft_profiles(app)
    options = loft_profile_options(app.project.geometry, first, second)
    faces = app.run(commands.Loft(first, second, **options))
    app.show_geometry()
    app.set_status(f"Constructed loft with {len(faces)} shell surface(s). Undo reverses this operation.")
    return faces


class LoftDialog(QDialog):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.command = None
        self._preview_identity = None
        self._preview_visible = False
        self._edit_generation = 0
        self.setWindowTitle("Loft between profiles")
        self.setMinimumWidth(440)
        self.setWindowModality(Qt.NonModal)
        layout = QVBoxLayout(self)
        hint = QLabel("Select a connected chain in the viewport, then capture it as a profile. "
                      "Both profiles must be open or both closed. Start edge is zero-based and applies to closed loops.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        layout.addLayout(form)
        self.feature = QComboBox()
        self.feature.addItem("Create new loft", None)
        for record in app.project.geometry.features.records:
            if record.kind == "geometry.loft" and not record.suppressed:
                self.feature.addItem(record.name or f"Loft {record.feature_id}", record.feature_id)
        form.addRow("Feature", self.feature)
        self.profiles = []
        self.sketches = []
        self.reversals = []
        self.starts = []
        for index, title in enumerate(("First", "Second")):
            field = QLineEdit()
            field.setPlaceholderText("Ordered edge IDs, e.g. 1, 2, 3")
            self.profiles.append(field)
            row = QHBoxLayout()
            row.addWidget(field)
            capture = QPushButton("Use selection")
            capture.clicked.connect(lambda checked=False, i=index: self.capture(i))
            row.addWidget(capture)
            form.addRow(f"{title} profile", row)
            sketch = QComboBox()
            sketch.addItem("Choose a sketch chain…", None)
            self.sketches.append(sketch)
            form.addRow(f"{title} sketch chain", sketch)
            reverse = QCheckBox("Reverse direction")
            self.reversals.append(reverse)
            start = QSpinBox()
            start.setRange(0, 1000000)
            self.starts.append(start)
            controls = QHBoxLayout()
            controls.addWidget(reverse)
            controls.addWidget(QLabel("Start edge"))
            controls.addWidget(start)
            form.addRow(controls)
            field.textChanged.connect(self.invalidate)
            reverse.toggled.connect(self.invalidate)
            start.valueChanged.connect(self.invalidate)
            sketch.currentIndexChanged.connect(lambda _, i=index: self.use_sketch(i))
        self.flip = QCheckBox("Flip surface normal")
        form.addRow(self.flip)
        self.flip.toggled.connect(self.invalidate)
        self.auto_match = QCheckBox("Match directions and start points automatically")
        self.auto_match.setChecked(True)
        self.auto_match.toggled.connect(self.invalidate)
        form.addRow(self.auto_match)
        for reverse in self.reversals:
            reverse.toggled.connect(lambda _: self.auto_match.setChecked(False))
        for start in self.starts:
            start.valueChanged.connect(lambda _: self.auto_match.setChecked(False))
        self.status = QLabel("Capture both profiles, then preview their connections.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        preview = QPushButton("Preview")
        preview.clicked.connect(self.preview)
        buttons.addWidget(preview)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.apply)
        buttons.addWidget(self.apply_button)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.feature.currentIndexChanged.connect(self.load_feature)
        self.populate_sketches()

    def identity(self):
        from anygeometry import to_dict
        from anygeometry.definition_binding import definition_checksum

        # Revision alone cannot detect raw mutation of arrays or feature intent.
        # The owner serialization validates and binds the actual geometry content.
        session = self.app.session
        revision = getattr(session, "revision", None)
        return (id(self.app.project), id(self.app.project.geometry), id(session),
                getattr(revision, "sequence", None),
                definition_checksum(to_dict(self.app.project.geometry)))

    def populate_sketches(self):
        from anygeometry.loft import loft_profiles_from_sketch

        geometry = self.app.project.geometry
        for record in geometry.features.records:
            if record.kind not in {"geometry.sketch.plane", "geometry.sketch.extrude"} or record.suppressed:
                continue
            try:
                profiles = loft_profiles_from_sketch(geometry, record.feature_id)
            except (ValueError, KeyError, RuntimeError) as error:
                self.status.setText(f"Sketch {record.feature_id} unavailable: {error}")
                continue
            for wire, edges in profiles:
                for combo in self.sketches:
                    combo.addItem(f"{record.name or 'Sketch'} / {wire}", tuple(edges))

    def capture(self, index):
        self.invalidate()
        edges = tuple(ref.id for ref in self.app.selection.ordered_items if ref.kind == "edge")
        self.profiles[index].setText(", ".join(map(str, edges)))
        if not edges:
            self.status.setText("Select the profile edges in the viewport first.")

    def use_sketch(self, index):
        edges = self.sketches[index].currentData()
        if edges is not None:
            self.profiles[index].setText(", ".join(map(str, edges)))

    def restore_view(self):
        if self._preview_visible:
            self._preview_visible = False
            self.app.show_geometry()

    def invalidate(self, *_):
        self._edit_generation += 1
        self.command = None
        self._preview_identity = None
        self.apply_button.setEnabled(False)
        self.restore_view()

    def load_feature(self, *_):
        try:
            self._load_feature()
        except (ValueError, KeyError, RuntimeError) as error:
            self.invalidate()
            self.status.setText(str(error))

    def _load_feature(self):
        from anygeometry.features import FeatureOutputRef

        self.invalidate()
        identifier = self.feature.currentData()
        if identifier is None:
            return
        geometry = self.app.project.geometry
        record = geometry.features.get(identifier)
        self.auto_match.setChecked(False)
        for index, key in enumerate(("first_edges", "second_edges")):
            refs = record.inputs[key]
            edges = []
            for ref in refs:
                if isinstance(ref, FeatureOutputRef):
                    ref = geometry.features.get(ref.feature_id).outputs[ref.output_key]
                resolved = geometry.resolve_ref(ref)
                if len(resolved) != 1 or resolved[0].kind != "edge":
                    raise ValueError("A loft source changed topology; select its profile again")
                edges.append(resolved[0].id)
            self.profiles[index].setText(", ".join(map(str, edges)))
            prefix = "first" if index == 0 else "second"
            self.reversals[index].setChecked(record.parameters.get(f"reverse_{prefix}", False))
            self.starts[index].setValue(record.parameters.get(f"{prefix}_start", 0))
        self.flip.setChecked(record.parameters.get("flip_normal", False))

    def profile_command(self):
        edges = [tuple(int(value.strip()) for value in field.text().split(",") if value.strip())
                 for field in self.profiles]
        if not all(edges):
            raise ValueError("Choose both profiles before previewing")
        return commands.Loft(*edges,
            reverse_first=self.reversals[0].isChecked(), reverse_second=self.reversals[1].isChecked(),
            first_start=self.starts[0].value(), second_start=self.starts[1].value(),
            flip_normal=self.flip.isChecked())

    def make_command(self):
        command = self.profile_command()
        if self.feature.currentData() is not None:
            _, parameters, inputs = commands._geometry_feature_definition(command, self.app.project.geometry)
            return commands.EditFeature(self.feature.currentData(), parameters=parameters, inputs=inputs)
        return command

    def preview(self):
        self.invalidate()
        try:
            if self.app.session.read_only:
                raise PermissionError("This project is read-only")
            identity = self.identity()
            if self.auto_match.isChecked():
                from anygeometry.loft import loft_profile_options
                proposed = self.profile_command()
                options = loft_profile_options(self.app.project.geometry, proposed.first_edges, proposed.second_edges)
                for control, key in zip(self.reversals, ("reverse_first", "reverse_second")):
                    control.blockSignals(True)
                    control.setChecked(options[key])
                    control.blockSignals(False)
                for control, key in zip(self.starts, ("first_start", "second_start")):
                    control.blockSignals(True)
                    control.setValue(options[key])
                    control.blockSignals(False)
            command = self.make_command()
            generation = self._edit_generation
            staged = commands._stage_feature_project(self.app.project)
            result = command.do(staged)
            scene = build_geometry_scene(staged)
            if isinstance(command, commands.Loft):
                faces = result
            else:
                record = staged.geometry.features.get(self.feature.currentData())
                faces = [ref.id for ref in record.outputs.values() if ref.kind == "face"]
            # Presentation samples the owner's accepted surface; it does not construct geometry.
            for face_id in faces:
                point = lambda u, v: staged.geometry.face_point(face_id, u, v)
                for u in np.linspace(0, 1, 7):
                    scene.lines.append(Polyline(None, np.array([point(u, 0), point(u, 1)]),
                                                color="#d08020", width=1, annotation=True))
                for v, color in ((0, "#287bc1"), (1, "#a443b0")):
                    scene.arrows.append(Arrow(np.array(point(.35, v)), np.array(point(.5, v)), color))
                centre = np.array(point(.5, .5))
                scale = np.linalg.norm(np.array(point(.5, 1)) - point(.5, 0)) * .2
                scene.arrows.append(Arrow(centre, centre + scale * face_normal(staged.geometry, face_id), "#22884f"))
            self._preview_visible = True
            self.app.viewport.show(scene)
            if self.identity() != identity or self.app.session.read_only or generation != self._edit_generation:
                raise ValueError("The document changed during preview. Preview the profiles again.")
            self.command = self.make_command()
            self._preview_identity = identity
            self.apply_button.setEnabled(True)
            self.status.setText(f"Preview: {len(faces)} shell surface(s). Orange lines show connections; green arrows show normals. "
                                "Meshing support is checked separately.")
        except (ValueError, KeyError, RuntimeError, PermissionError, TypeError) as error:
            self.invalidate()
            self.status.setText(str(error))

    def apply(self):
        if self.command is None:
            return
        try:
            if self.identity() != self._preview_identity or self.app.session.read_only:
                raise ValueError("The document changed. Preview the profiles again before applying.")
            command = self.command
            self.restore_view()
            self.app._begin_modelling()
            if self.identity() != self._preview_identity or self.app.session.read_only:
                raise ValueError("The document changed before applying. Preview the profiles again.")
            self.app.run(command)
            self.app.show_geometry()
            self.app.set_status("Loft applied. Assign shell properties before meshing.")
            self.accept()
        except (ValueError, KeyError, RuntimeError, PermissionError, TypeError) as error:
            self.invalidate()
            self.status.setText(str(error))

    def done(self, result):
        self.restore_view()
        super().done(result)


def open_loft(app):
    if app.session.read_only:
        raise PermissionError("This project is read-only")
    if app.viewport.construction_active:
        raise ValueError("Finish the sketch before creating a loft")
    existing = getattr(app, "_loft_dialog", None)
    if existing is not None and existing.isVisible():
        existing.raise_()
        return existing
    dialog = LoftDialog(app)
    app._loft_dialog = dialog
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.finished.connect(lambda _: setattr(app, "_loft_dialog", None))
    dialog.show()
    return dialog
