"""Command-backed geometry sets and canvas actions for the Qt workbench."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QInputDialog,
                              QLabel, QLineEdit, QMenu, QPlainTextEdit, QVBoxLayout)
from anygeometry import feature_entity_owners
from anygeometry.entities import EntityRef

from ... import commands
from ...model.regions import ManualRegion, Region, RegionDomain


from ...presentation.sets import is_geometry_set,is_user_set,set_references,set_geometry_references


class VectorDialog(QDialog):
    """A displacement vector in project units, applied as one command."""

    def __init__(self, actions, operation, refs):
        super().__init__(actions.app)
        self.actions, self.operation, self.references = actions, operation, tuple(refs)
        self.binding = actions.binding()
        self.setWindowTitle(f"{operation} geometry")
        self.setObjectName("GeometryVectorDialog")
        self.setMinimumWidth(380)
        self.setAttribute(Qt.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        units = actions.app.project.units
        guidance = QLabel(f"Displacement vector ({units.symbol('length')}). Explicit unit suffixes are accepted.\n"
                          "Shared points affect connected geometry.")
        guidance.setWordWrap(True)
        layout.addWidget(guidance)
        form = QFormLayout()
        self.fields = {}
        for axis in ("X", "Y", "Z"):
            field = QLineEdit("0", self)
            field.setObjectName(f"Displacement{axis}")
            field.setAccessibleName(f"Displacement {axis}")
            form.addRow(f"Δ{axis}", field)
            self.fields[axis] = field
        layout.addLayout(form)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText(operation)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self):
        try:
            self.actions.assert_binding(self.binding)
            profile = self.actions.app.project.units
            vector = tuple(profile.parse(self.fields[axis].text(), "length") for axis in ("X", "Y", "Z"))
            command = commands.CopyEntities if self.operation == "Copy" else commands.MoveEntities
            self.actions.app.run(command(self.references, translation=vector))
        except (ValueError, RuntimeError, PermissionError) as error:
            self.feedback.setText(str(error))
            return
        self.accept()


class GeometryActions:
    def __init__(self, app):
        self.app = app
        self.menu = None
        self.dialogs = []

    def binding(self):
        geometry = self.app.project.geometry
        return self.app.project.document_id, geometry.model_id, geometry.revision

    def assert_binding(self, binding):
        if self.binding() != binding:
            raise ValueError("The project or geometry changed. Select the geometry again.")

    def references(self):
        viewport = self.app.viewport
        refs = [ref for ref in self.app.selection.ordered_items if isinstance(ref, EntityRef)]
        refs.extend(viewport._collection_refs)
        if viewport._feature_ids:
            refs.extend(ref for ref, owner in feature_entity_owners(self.app.project.geometry).items()
                        if owner in viewport._feature_ids)
        return tuple(dict.fromkeys(refs))

    def new_set(self, refs=None, name=None):
        binding=self.binding();project=self.app.project;session=self.app.session
        if session.read_only or project.read_only_reason:raise PermissionError('This project is read-only')
        refs = self.references() if refs is None else tuple(refs)
        if name is None:
            name, accepted = QInputDialog.getText(self.app, "New geometry set", "Set name")
            if not accepted:
                return None
        self.assert_binding(binding)
        if self.app.project is not project or self.app.session is not session:raise ValueError('The project changed; create the set again.')
        if session.read_only or project.read_only_reason:raise PermissionError('This project is read-only')
        name = name.strip()
        if not name:
            raise ValueError("A set needs a name.")
        if any(region.name == name for region in self.app.project.regions):
            raise ValueError(f"A set or region named {name!r} already exists.")
        geometry = self.app.project.geometry
        refs = tuple(dict.fromkeys(geometry.entity_ref(ref.kind, ref.id) for ref in refs))
        kinds = {ref.kind for ref in refs}
        region = Region(name, RegionDomain.GEOMETRY,
                        next(iter(kinds)) if len(kinds) == 1 else "mixed", ManualRegion(tuple(commands._feature_anchor(geometry,ref) for ref in refs)))
        self.app.run(commands.AddRegion(region))
        self.app.set_status(f"Created set {name}: {len(refs)} entities")
        return region

    def add_to_set(self, refs=None):
        binding=self.binding()
        refs = self.references() if refs is None else tuple(refs)
        if not refs:
            raise ValueError("Select geometry to add to a set.")
        regions = [region for region in self.app.project.regions if is_geometry_set(region)]
        if not regions:
            return self.new_set(refs)
        name, accepted = QInputDialog.getItem(self.app, "Add to geometry set", "Set",
                                            [region.name for region in regions], editable=False)
        if accepted:
            self.assert_binding(binding)
            region = regions[[region.name for region in regions].index(name)]
            combined = tuple(dict.fromkeys((*set_references(self.app.project, region.id), *refs)))
            return self.app.run(commands.EditGeometrySet(region.id, references=combined))

    def rename_set(self, identifier):
        binding=self.binding()
        region = self.app.project.regions[identifier]
        if not is_geometry_set(region):raise ValueError('This set is defined by a Boolean, query or mesh scope; edit its source definition in Sets tools.')
        name, accepted = QInputDialog.getText(self.app, "Rename geometry set", "Set name", text=region.name)
        if accepted:
            self.assert_binding(binding)
            return self.app.run(commands.EditGeometrySet(identifier, name=name))

    def vector_dialog(self, operation, refs=None):
        refs = self.references() if refs is None else tuple(refs)
        if not refs:
            raise ValueError("Select geometry first.")
        dialog = VectorDialog(self, operation, refs)
        self._open(dialog)
        return dialog

    def _open(self, dialog):
        self.dialogs.append(dialog)
        dialog.finished.connect(lambda _result: self.dialogs.remove(dialog) if dialog in self.dialogs else None)
        dialog.open()

    def inspect_set(self,identifier):
        region=self.app.project.regions[identifier]
        if not is_user_set(region):raise ValueError('Internal assignment scopes are not user sets.')
        if is_geometry_set(region):
            refs=set_geometry_references(self.app.project,identifier)
            if refs:return self.properties(refs)
        dialog=QDialog(self.app);dialog.setWindowTitle('Set: '+region.name);dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.resize(480,320);layout=QVBoxLayout(dialog)
        try:
            refs=set_references(self.app.project,identifier)
            detail=f'{len(refs)} member(s)'
        except (ValueError,KeyError) as error:detail='Unavailable targets: '+str(error)
        info=QLabel(f'Name: {region.name}\nDomain: {region.domain.value}\nKind: {region.entity_kind}\nDefinition: {type(region.definition).__name__.removesuffix("Region")}\n{detail}\n\nBoolean, query and mesh membership follows its source definition. Use Sets tools in Definitions to construct a new scope; dependent assignments remain unchanged.')
        info.setWordWrap(True);layout.addWidget(info)
        buttons=QDialogButtonBox(QDialogButtonBox.Close,dialog);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        self._open(dialog);return dialog

    def edit(self,refs=None):
        from .geometry_edit import GeometryEditDialog
        refs=self.references() if refs is None else tuple(refs)
        dialog=GeometryEditDialog(self.app,refs);self._open(dialog);return dialog

    def properties(self, refs=None):
        refs = self.references() if refs is None else tuple(refs)
        if not refs:
            raise ValueError("Select geometry first.")
        project, geometry = self.app.project, self.app.project.geometry
        owners = feature_entity_owners(geometry)
        lines = [f"Geometry entities: {len(refs)}", f"Units: {project.units.name}", ""]
        for ref in refs[:128]:
            geometry.entity_ref(ref.kind, ref.id)
            lines.append(f"{ref.kind.capitalize()} {ref.id}")
            owner = owners.get(ref)
            if owner is not None:
                lines.append(f"  Feature: {geometry.features.get(owner).name} ({owner})")
            if ref.kind == "vertex":
                vector = geometry.vertex_position(ref.id)
                lines.append("  Position: " + ", ".join(project.units.format(value, "length") for value in vector))
            elif ref.kind == "edge":
                edge = geometry.edges[ref.id]
                lines.append(f"  Endpoints: vertex {edge.start}, vertex {edge.end}")
                lines.append("  Length: " + project.units.format(geometry.edge_length(ref.id), "length"))
                lines.append(f"  Curve: {type(edge.curve).__name__}")
                section = project.edge_sections.get(ref.id)
                if section:
                    lines.append(f"  Beam section: {section}")
            elif ref.kind == "face":
                face = geometry.faces[ref.id]
                lines.append(f"  Surface: {type(face.surface).__name__ if face.surface is not None else 'Planar'}")
                lines.append(f"  Boundary edges: {', '.join(str(use.edge) for use in face.loop)}")
                lines.append(f"  Holes: {len(face.holes)}")
                section = project.face_sections.get(ref.id)
                if section:
                    lines.append(f"  Plate section: {section}")
            lines.append("")
        if len(refs) > 128:
            lines.append(f"Showing the first 128 of {len(refs)} entities.")
        dialog = QDialog(self.app)
        dialog.setWindowTitle("Geometry properties")
        dialog.setObjectName("GeometryPropertiesDialog")
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.resize(480, 420)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit("\n".join(lines), dialog)
        text.setReadOnly(True)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self._open(dialog)
        return dialog

    def menu_for(self, refs, parent=None):
        refs = tuple(refs)
        binding = self.binding()
        menu = QMenu(parent or self.app)
        menu.setObjectName("GeometryActionsMenu")
        for label in ("Copy", "Move", "Edit", "Properties"):
            def activate(_checked=False, label=label):
                self.assert_binding(binding)
                return self.properties(refs) if label == "Properties" else self.edit(refs) if label == "Edit" else self.vector_dialog(label, refs)
            action = menu.addAction(label)
            action.triggered.connect(self.app.guarded(activate))
            if label != "Properties":
                action.setEnabled(not (self.app.session.read_only or self.app.project.read_only_reason) and (label!="Edit" or any(ref.kind in {"edge","face"} for ref in refs)))
        kinds = {ref.kind for ref in refs}
        if len(refs) == 1 and kinds == {"face"}:
            action = menu.addAction("Cut")
            def cut(_checked=False):
                self.assert_binding(binding)
                return self.cut_plate(refs)
            action.triggered.connect(self.app.guarded(cut))
            action.setEnabled(not (self.app.session.read_only or self.app.project.read_only_reason))
        if refs and kinds in ({"vertex"}, {"edge"}):
            plate = kinds == {"vertex"}
            action = menu.addAction("Add plate" if plate else "Add beam")
            def construct(_checked=False):
                self.assert_binding(binding)
                return self.start_plate(refs) if plate else self.assign_beams(refs)
            action.triggered.connect(self.app.guarded(construct))
            action.setEnabled(not (self.app.session.read_only or self.app.project.read_only_reason))
        menu.addSeparator()
        for label, callback in (("Create set…", lambda: self.new_set(refs)),
                                ("Add to set…", lambda: self.add_to_set(refs))):
            def activate_set(_checked=False, callback=callback):
                self.assert_binding(binding)
                return callback()
            action = menu.addAction(label)
            action.triggered.connect(self.app.guarded(activate_set))
            action.setEnabled(not bool(self.app.project.read_only_reason))
        return menu

    def canvas_context_menu(self, event):
        if self.app._view_mode != "geometry" or not self.app.canvas_action_menu.isChecked():
            return
        if not getattr(event,"hits",()) and not getattr(event,"preserve_selection",False):
            return
        refs = tuple(ref for ref in self.app.selection.ordered_items if isinstance(ref, EntityRef))
        if not refs:
            return
        if self.menu is not None:
            self.menu.close()
            self.menu.deleteLater()
        self.menu = self.menu_for(refs)
        surface = self.app.viewport.canvas.event_widget
        ratio = surface.devicePixelRatioF()
        x, y = event.end
        self.menu.popup(surface.mapToGlobal(QPoint(round(x / ratio), round(y / ratio))))

    def start_plate(self, refs=()):
        from .plate_construction import PlateDraft
        if self.app.session.read_only or self.app.project.read_only_reason:raise PermissionError("This project is read-only")
        if any(ref.kind != "vertex" for ref in refs):raise ValueError("Select vertices for a plate boundary.")
        self.app._begin_modelling()
        self.app.viewport.cancel_plate()
        self.app.viewport.cancel_construction()
        self.app.selection.set_mode("vertex")
        draft = PlateDraft(self, refs)
        self.app.viewport.plate_draft = draft
        draft.update_preview()
        self.dialogs.append(draft)
        draft.finished.connect(lambda _result: self.dialogs.remove(draft) if draft in self.dialogs else None)
        draft.show()
        return draft

    def assign_beams(self, refs):
        binding = self.binding()
        sections = list(self.app.project.beam_sections)
        if not sections:raise ValueError("Create a beam section in Sections before assigning beams.")
        section, accepted = QInputDialog.getItem(self.app, "Add beam", "Beam section", sections, 0, False)
        if not accepted:return
        self.assert_binding(binding)
        if self.app.session.read_only or self.app.project.read_only_reason:raise PermissionError("This project is read-only")
        from ...presentation.beams import ordered_beam_chain
        ids = tuple(ref.id for ref in refs)
        geometry = self.app.project.geometry
        try:chain = ordered_beam_chain(geometry, ids)
        except ValueError:chain = ()
        if chain and not any(geometry.members_using_edge(use.edge) for use in chain):
            self.app.run(commands.AddBeamMember(ids, section))
        else:
            self.app.run(commands.CompositeCommand([commands.AssignBeam(ref.id, section) for ref in refs], label="assign beams"))

    def cut_plate(self, refs=None):
        if self.app.session.read_only or self.app.project.read_only_reason:
            raise PermissionError("This project is read-only")
        refs = self.references() if refs is None else tuple(refs)
        if len(refs) != 1 or refs[0].kind != "face":
            raise ValueError("Select one target plate face before cutting.")
        from .plate_cut import PlateCutDialog
        dialog = PlateCutDialog(self, refs[0].id)
        self.dialogs.append(dialog)
        dialog.finished.connect(lambda _result: self.dialogs.remove(dialog) if dialog in self.dialogs else None)
        dialog.show()
        return dialog

    def create_beam_from_edges(self, refs=None):
        if self.app.session.read_only or self.app.project.read_only_reason:
            raise PermissionError("This project is read-only")
        refs = self.references() if refs is None else tuple(refs)
        if not refs or any(ref.kind != "edge" for ref in refs):
            raise ValueError("Select a cut edge or one connected edge chain.")
        from ...presentation.beams import ordered_beam_chain
        ordered_beam_chain(self.app.project.geometry, tuple(ref.id for ref in refs))
        binding = self.binding()
        sections = list(self.app.project.beam_sections)
        if not sections:
            raise ValueError("Create a beam section in Sections before creating a beam.")
        section, accepted = QInputDialog.getItem(self.app, "Create beam from edge", "Beam section", sections, 0, False)
        if not accepted:return
        self.assert_binding(binding)
        if self.app.session.read_only or self.app.project.read_only_reason:
            raise PermissionError("This project is read-only")
        self.app.run(commands.AddBeamMember(tuple(ref.id for ref in refs), section))

    def close(self):
        if self.menu is not None:
            self.menu.close()
            self.menu.deleteLater()
            self.menu = None
        for dialog in tuple(self.dialogs):
            dialog.reject()
