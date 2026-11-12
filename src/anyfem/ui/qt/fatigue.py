"""Nonmodal fatigue assessment for the active Results source."""

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...application.fatigue import load_fatigue_input, prepare_fatigue_locations
from ...selection import MeshEntityRef
from .fatigue_picking import FatiguePicking


def _choice(*entries):
    widget = QComboBox()
    for label, value in entries:
        widget.addItem(label, value)
    return widget


def _button(text, callback, layout):
    button = QPushButton(text)
    button.clicked.connect(callback)
    layout.addWidget(button)
    return button


class FatigueDialog(QDialog):
    def __init__(self, panel):
        super().__init__(panel.window())
        from anyfatigue.sn_curves import ENVIRONMENTS, curve_names

        self.panel = panel
        self.app = panel.app
        self.setWindowTitle("Fatigue assessment")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(580, 590)
        self.data = None
        self.prepared = None
        self.result = None
        self.element_ids = ()
        self._plate_error = None
        self.targets = ()
        self.reference_edge = None
        self._example_preset = None
        self._owner = None
        self._generation = 0
        self._closed = False
        self._syncing = True
        self._channel = f"fatigue-{id(self)}"
        self._capture = None
        self.picking = FatiguePicking(self.app, self._picked, self._pick_cancelled,
                                     on_selection=self._picked, on_miss=self._pick_missed)

        layout = QVBoxLayout(self)
        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        pick_page = QWidget()
        self.tabs.addTab(pick_page, "Location")
        form = QFormLayout(pick_page)
        self.location_kind = _choice(("Direct shell stress", "direct"), ("Weld-line hot spot", "line"),
                                     ("Point hot spot", "point"))
        form.addRow("Assess", self.location_kind)
        buttons = QHBoxLayout()
        self.pick_shell = _button("Pick shell", lambda: self.start_pick("element"), buttons)
        self.add_shell = _button("Add shell", lambda: self.start_pick("element", add=True), buttons)
        self.clear_selection = _button("Clear", self._clear_selection, buttons)
        form.addRow("Shell selection", buttons)
        buttons = QHBoxLayout()
        self.pick_target = _button("Pick weld line", lambda: self.start_pick(self.location_kind.currentData()), buttons)
        self.pick_reference = _button("Pick reference line", lambda: self.start_pick("reference"), buttons)
        self.cancel_pick = _button("Finish picking", self._cancel_pick, buttons)
        self.pick_hint = QLabel()
        self.pick_hint.setWordWrap(True)
        form.addRow(buttons)
        self.selection = QLabel()
        self.selection.setWordWrap(True)
        form.addRow("Selected", self.selection)
        form.addRow(self.pick_hint)
        self.surface = _choice(("Top (+director)", "top"), ("Bottom (−director)", "bottom"))
        form.addRow("Shell surface", self.surface)
        self.thickness = QLineEdit()
        self.thickness.setPlaceholderText("Enter thickness if unavailable from the result")
        self.thickness.setToolTip("The submitted plate thickness can be overridden for this assessment.")
        form.addRow("Plate thickness (mm)", self.thickness)
        self.side = _choice(("Detect from plate", "auto"), ("+1 (normal × weld)", 1),
                            ("−1 (opposite)", -1))
        form.addRow("Readout side", self.side)
        self.direction = QLineEdit()
        self.direction.setPlaceholderText("Optional global x, y, z into plate")
        form.addRow("Readout direction", self.direction)
        self.probe_rule = QLabel("Method A samples at 0.5 and 1.5 plate thicknesses from the weld toe.")
        self.probe_rule.setWordWrap(True)
        form.addRow(self.probe_rule)
        self.reset_direction = QPushButton("Use automatic direction")
        self.reset_direction.clicked.connect(self._reset_direction)
        form.addRow(self.reset_direction)
        self.preview_details = QPlainTextEdit()
        self.preview_details.setReadOnly(True)
        self.preview_details.setPlaceholderText("Preview shows the selected plate and sampling positions.")
        form.addRow(self.preview_details)

        analysis_page = QWidget()
        self.tabs.addTab(analysis_page, "Assessment")
        form = QFormLayout(analysis_page)
        self.method = _choice(("Simplified (Weibull)", "simplified"), ("Time series (rainflow)", "time_series"))
        form.addRow("Method", self.method)
        self.curve = QComboBox()
        self.curve.addItems(curve_names())
        self.curve.setCurrentText("D")
        self.environment = QComboBox()
        self.environment.addItems(ENVIRONMENTS)
        pair = QHBoxLayout()
        pair.addWidget(self.curve)
        pair.addWidget(self.environment)
        form.addRow("S–N curve / environment", pair)
        self.measure = QComboBox()
        form.addRow("Stress measure", self.measure)
        self.life = QLineEdit("25")
        self.dff = QLineEdit("1")
        self.scf = QLineEdit("1")
        form.addRow("Design life (years)", self.life)
        form.addRow("Design fatigue factor", self.dff)
        form.addRow("Stress concentration factor", self.scf)

        self.simple_page = QWidget()
        simple = QFormLayout(self.simple_page)
        simple.setContentsMargins(0, 0, 0, 0)
        self.case = QComboBox()
        simple.addRow("Load case", self.case)
        self.range_choice = _choice(("Fully reversed amplitude (range = 2 × stress)", 2.0),
                                    ("Full stress range", 1.0))
        simple.addRow("Case represents", self.range_choice)
        self.weibull = QLineEdit("1")
        self.n0 = QLineEdit("10000")
        self.period = QLineEdit("10")
        simple.addRow("Weibull shape h", self.weibull)
        simple.addRow("Return count n₀ (cycles)", self.n0)
        simple.addRow("Mean zero-crossing period (s)", self.period)
        form.addRow(self.simple_page)

        self.series_page = QWidget()
        series = QFormLayout(self.series_page)
        series.setContentsMargins(0, 0, 0, 0)
        self.case_series = QListWidget()
        series.addRow("Cases in record order", self.case_series)
        buttons = QHBoxLayout()
        self.up = _button("Move up", lambda: self._move_case(-1), buttons)
        self.down = _button("Move down", lambda: self._move_case(1), buttons)
        series.addRow(buttons)
        self.duration = QLineEdit()
        self.duration.setPlaceholderText("Use first and last case times")
        series.addRow("Record duration (s)", self.duration)
        form.addRow(self.series_page)

        result_page = QWidget()
        self.tabs.addTab(result_page, "Results")
        results = QVBoxLayout(result_page)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        results.addWidget(self.summary)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Station / surface", "Chainage (m)", "Miner damage", "Usage (D × DFF)"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        results.addWidget(self.table)
        self.warnings = QPlainTextEdit()
        self.warnings.setReadOnly(True)
        results.addWidget(self.warnings)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.retry_button = _button("Reload result", self._reload, actions)
        self.retry_button.hide()
        self.preview_button = _button("Preview location", self.preview, actions)
        self.run_button = _button("Calculate fatigue", self.calculate, actions)
        self.close_button = _button("Close", self.close, actions)
        layout.addLayout(actions)

        self.location_kind.currentIndexChanged.connect(self._target_changed)
        self.method.currentIndexChanged.connect(self._method_changed)
        for widget in (self.surface, self.side, self.curve, self.environment, self.measure,
                       self.case, self.range_choice):
            widget.currentIndexChanged.connect(self._changed)
        self.direction.textChanged.connect(self._direction_edited)
        for widget in (self.thickness, self.life, self.dff, self.scf,
                       self.weibull, self.n0, self.period, self.duration):
            widget.textChanged.connect(self._changed)
        self.case_series.itemChanged.connect(self._changed)
        self._target_changed()
        self._method_changed()
        self._syncing = False
        self._watch = QTimer(self)
        self._watch.setInterval(200)
        self._watch.timeout.connect(self.sync_source)
        self._watch.start()
        self._update_buttons()

    def _binding(self):
        app = self.app
        job = app.active_job_id
        saved = self.panel.use_retained()
        source = app.result_datasets.get(job) if saved else app.solution
        return (id(app.session), id(app.project), job, saved, id(source),
                getattr(app, "_result_source_epoch", 0))

    def _reload(self):
        self._owner = None
        self.sync_source()

    def sync_source(self):
        if self._closed:
            return
        owner = self._binding()
        if owner == self._owner:
            if self._capture is not None and not self.picking.active:
                self._pick_cancelled()
            return
        self._owner = owner
        self.data = None
        self.element_ids = ()
        self._plate_error = None
        self.targets = ()
        self._forget_direction()
        self.thickness.clear()
        self.retry_button.hide()
        self._invalidate(clear_binding=True)
        job, saved = owner[2:4]
        dataset = self.app.result_datasets.get(job) if saved else None
        solution = None if saved else self.app.solution
        source_text = "saved result" if saved else "live result"
        self.source_label.setText(f"Job {job} · {source_text}" if job is not None else "No active result job")
        if solution is None and dataset is None:
            self.status.setText("Open or run a shell analysis, then select its result.")
            return
        project = self.app.project

        def load(cancel):
            mesh = self.app.retained_result_mesh(job)
            return load_fatigue_input(solution=solution, dataset=dataset, mesh=mesh, project=project)

        self._submit(load, self._loaded, "Loading result mesh and cases…")

    def _loaded(self, data):
        self.data = data
        self._syncing = True
        try:
            self.case.clear()
            self.case.addItems(data.source.case_labels)
            self.case_series.clear()
            for label, time in zip(data.source.case_labels, data.source.case_times):
                item = QListWidgetItem(label if time is None else f"{label}   ({time:g} s)")
                item.setData(Qt.UserRole, label)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
                self.case_series.addItem(item)
        finally:
            self._syncing = False
        self.status.setText("Pick shell elements in the Results view.")
        self._update_buttons()
        pending, self._example_preset = self._example_preset, None
        if pending is not None and pending[0] == self._owner:
            self.configure_weld_history(pending[1])

    def configure_weld_history(self, preset):
        """Start a rainflow preview on a known weld of this submitted result."""
        if self.data is None:
            self._example_preset = (self._binding(), preset)
            return
        from anygeometry.entities import EntityRef
        element, edge = preset["element_id"], preset["edge_id"]
        mesh = self.data.mesh
        if element not in mesh.shells or edge not in mesh.nodes_of_edge:
            raise ValueError("The example weld or shell is missing from its submitted mesh")
        if "face_id" in preset and element not in mesh.elements_of_face.get(preset["face_id"], ()):
            raise ValueError("The example shell does not belong to the saved bracket face")
        thickness = self.data.thickness_mm_for((element,))
        if thickness is None:
            raise ValueError("The example result does not specify its bracket thickness")
        self._invalidate()
        self._forget_direction()
        self._plate_error = None
        self._syncing = True
        try:
            self.location_kind.setCurrentIndex(self.location_kind.findData("line"))
            self.method.setCurrentIndex(self.method.findData("time_series"))
            self.measure.setCurrentIndex(self.measure.findData("normal_to_weld"))
            self.surface.setCurrentIndex(self.surface.findData(preset["surface"]))
            self.side.setCurrentIndex(self.side.findData(preset["side"]))
            self.thickness.setText(f"{thickness:.12g}")
            if preset.get("direction") is not None:
                self.direction.setText(", ".join(f"{value:.12g}" for value in preset["direction"]))
            if "duration_s" in preset:
                self.duration.setText(str(preset["duration_s"]))
            self.element_ids = (element,)
            self._plate_error = None
            self.targets = (EntityRef("edge", edge),)
        finally:
            self._syncing = False
        self._update_buttons()
        self.preview()

    def _submit(self, operation, publish, message):
        generation = self._generation
        owner = self._owner
        self.status.setText(message)

        def current():
            return not self._closed and self._generation == generation and self._binding() == owner

        def load(cancel):
            if cancel.is_set():
                return None, None
            try:
                return operation(cancel), None
            except Exception as error:
                return None, str(error)

        def ready(payload):
            if not current():
                return
            value, error = payload
            if error is not None:
                self.status.setText(error)
                self.retry_button.setVisible(self.data is None)
            elif value is not None:
                publish(value)

        def cancelled():
            if current():
                self.status.setText("Result request cancelled.")
                self.retry_button.setVisible(self.data is None)

        dataset = self.app.result_datasets.get(owner[2]) if owner[3] else None
        self.app._request_result(load, ready, dataset=dataset, channel=self._channel, on_cancel=cancelled)

    def _invalidate(self, *, keep_pick=False, clear_binding=False):
        self._generation += 1
        if not keep_pick:
            self._capture = None
            self.picking.cancel()
        if clear_binding:
            self.picking.clear()
        else:
            self.picking.clear_preview()
        loader = getattr(self.app, "_result_loader", None)
        if loader is not None:
            loader.cancel_channel(self._channel)
        self.prepared = self.result = None
        self.preview_details.clear()
        self.table.setRowCount(0)
        self.warnings.clear()
        self.summary.clear()
        self._update_buttons()

    @property
    def target(self):
        """First target for callers displaying one prepared example."""
        return self.targets[0] if self.targets else None

    @staticmethod
    def _ref_label(ref):
        if ref.kind == "mesh_edge":
            return "mesh edge " + "–".join(map(str, ref.node_ids))
        return f"{ref.kind} {ref.id}"

    def _element_refs(self):
        mesh_id = getattr(self.data.mesh, "_anyfem_mesh_record_id", None)
        return tuple(MeshEntityRef("element", eid, mesh_id) for eid in self.element_ids)

    def _selected_refs(self):
        reference = (self.reference_edge,) if self.reference_edge is not None else ()
        return (*self._element_refs(), *self.targets, *reference)

    def _target_key(self, ref):
        if ref.kind == "vertex":
            return "node", self.data.mesh.node_of_vertex.get(ref.id)
        if ref.kind == "mesh_edge":
            return ref.kind, tuple(ref.node_ids)
        return ref.kind, ref.id

    def _update_buttons(self):
        hot = self.location_kind.currentData() != "direct"
        ready = self.data is not None
        self.tabs.setEnabled(ready)
        parts = ["Shells " + ", ".join(map(str, self.element_ids)) if self.element_ids else "No shells"]
        if self.targets:
            parts.append(", ".join(self._ref_label(ref) for ref in self.targets))
        if self.reference_edge is not None:
            parts.append("reference " + self._ref_label(self.reference_edge))
        self.selection.setText(" · ".join(parts))
        point = self.location_kind.currentData() == "point"
        self.pick_target.setEnabled(hot and bool(self.element_ids) and not self._plate_error)
        self.pick_reference.setVisible(point)
        self.pick_reference.setEnabled(point and bool(self.targets))
        self.pick_reference.setToolTip("A picked reference line must pass through every selected toe point.")
        self.side.setEnabled(hot and not self.direction.text().strip())
        self.reset_direction.setVisible(hot and bool(self.reference_edge or self.direction.text()))
        self.cancel_pick.setEnabled(self._capture is not None)
        complete = ready and bool(self.element_ids) and (not hot or bool(self.targets))
        self.preview_button.setEnabled(complete)
        self.run_button.setEnabled(complete)
        if self._capture is None:
            hint = ""
        elif self._capture[0] == "reference":
            hint = "Click a reference line through every selected toe."
        else:
            entity = {"element": "shells", "line": "weld lines", "point": "toe points"}[self._capture[0]]
            hint = f"Click to replace selected {entity}. Shift-click to add. Esc finishes picking."
        if point and len(self.targets) > 1:
            hint += " Multiple toes can share a reference line or a typed direction."
        self.pick_hint.setText(hint.strip())
        self.pick_hint.setVisible(bool(hint))

    def _target_changed(self, *_):
        kind = self.location_kind.currentData()
        self.targets = ()
        self._forget_direction()
        hot = kind != "direct"
        self.probe_rule.setVisible(hot)
        self.pick_target.setText("Pick toe point" if kind == "point" else "Pick weld line")
        self.direction.setEnabled(hot)
        measure = self.measure.currentData()
        self.measure.blockSignals(True)
        self.measure.clear()
        if hot:
            self.measure.addItem("Normal to weld", "normal_to_weld")
        for component in ("xx", "yy", "zz", "xy", "yz", "xz"):
            self.measure.addItem(f"Global {component}", f"component:{component}")
        if not hot and measure is not None:
            self.measure.setCurrentIndex(max(0, self.measure.findData(measure)))
        self.measure.blockSignals(False)
        self._plate_error = None
        if hot and self.data is not None and self.element_ids:
            try:
                self.data.plate_scope_for(self.element_ids)
            except ValueError as error:
                self._plate_error = f"{error} Pick a shell to choose a new supporting plate."
        self._changed()

    def _method_changed(self, *_):
        simple = self.method.currentData() == "simplified"
        self.simple_page.setVisible(simple)
        self.series_page.setVisible(not simple)
        self._changed()

    def _changed(self, *_):
        if self._syncing or self.data is None:
            return
        self._invalidate()
        self.picking.set_selected(self._selected_refs())
        self.status.setText(self._plate_error or "Preview or calculate with the current settings.")

    def _move_case(self, step):
        row = self.case_series.currentRow()
        new = row + step
        if row >= 0 and 0 <= new < self.case_series.count():
            item = self.case_series.takeItem(row)
            self.case_series.insertItem(new, item)
            self.case_series.setCurrentRow(new)
            self._changed()

    def _clear_selection(self):
        self.element_ids = ()
        self._plate_error = None
        self.targets = ()
        self._forget_direction()
        with QSignalBlocker(self.thickness):
            self.thickness.clear()
        self._changed()
        self.status.setText("Selection cleared. Pick a shell element in the Results view.")

    def _forget_direction(self):
        self.reference_edge = None
        with QSignalBlocker(self.direction):
            self.direction.clear()

    def _reset_direction(self):
        self._forget_direction()
        self._changed()

    def _direction_edited(self):
        self.reference_edge = None
        self._changed()

    def start_pick(self, kind, add=False):
        self.sync_source()
        if self.data is None:
            return
        self._invalidate()
        self._capture = (kind, add)
        try:
            scope = ()
            through_node = None
            if kind != "element":
                _, scope = self.data.plate_scope_for(self.element_ids)
                if kind == "reference":
                    if not self.targets:
                        raise ValueError("Select the toe points before picking their reference line.")
                    nodes = tuple(self._target_key(toe)[1] for toe in self.targets)
                    through_node = nodes[0] if len(nodes) == 1 else nodes
            self.picking.start("line" if kind == "reference" else kind, self.data.mesh,
                               element_ids=scope, selected=self._selected_refs(), through_node=through_node)
        except (KeyError, ValueError, RuntimeError) as error:
            self._capture = None
            self.status.setText(str(error))
        else:
            noun = {"element": "shell element", "line": "weld line", "point": "toe point",
                    "reference": "reference line through the toe point"}[kind]
            self.status.setText(f"Pick a {noun} in the Results view.")
        self._update_buttons()

    def _pick_missed(self, reason):
        if self._capture is not None:
            self.status.setText(reason)

    def _reference_covers(self, edge, toes):
        nodes = edge.node_ids if edge.kind == "mesh_edge" else self.data.mesh.nodes_of_edge.get(edge.id, ())
        missing = [self._ref_label(toe) for toe in toes if self._target_key(toe)[1] not in nodes]
        if missing:
            self._pick_missed(f"{self._ref_label(edge)} misses {', '.join(missing)}. "
                              "Pick a line through all toes or enter a common direction.")
        return not missing

    def _picked(self, ref, additive=False):
        if self._capture is None or self._closed or self._owner != self._binding():
            return
        kind, add_first = self._capture
        add = additive or add_first
        if kind == "element":
            if ref.kind != "element" or ref.id not in self.data.mesh.shells:
                self._pick_missed("Pick a shell element in the displayed mesh.")
                return
            if add and ref.id in self.element_ids:
                return
            elements = (*self.element_ids, ref.id) if add else (ref.id,)
            if self.location_kind.currentData() != "direct":
                try:
                    _, scope = self.data.plate_scope_for(elements)
                except ValueError as error:
                    self._pick_missed(str(error))
                    return
                if self.targets and not set(self.element_ids).issubset(scope):
                    self.targets = ()
                    self._forget_direction()
            self._plate_error = None
            self.element_ids = elements
            submitted = self.data.thickness_mm_for(elements)
            with QSignalBlocker(self.thickness):
                self.thickness.setText("" if submitted is None else f"{submitted:.12g}")
        elif kind == "reference":
            if not self._reference_covers(ref, self.targets):
                return
            self._forget_direction()
            self.reference_edge = ref
        else:
            if add and any(self._target_key(old) == self._target_key(ref) for old in self.targets):
                return
            if add and self.reference_edge is not None and not self._reference_covers(self.reference_edge, (ref,)):
                return
            if not add and self.targets and not any(self._target_key(t) == self._target_key(ref) for t in self.targets):
                self._forget_direction()
            self.targets = (*self.targets, ref) if add else (ref,)
        self._capture = (kind, False)
        self.picking.set_selected(self._selected_refs())
        if kind == "reference":
            self._cancel_pick()
        self._update_buttons()
        if self.preview_button.isEnabled():
            self.preview()
        else:
            self._invalidate(keep_pick=True)
            self.status.setText("Pick the weld lines or toe points on the selected plate.")

    def _pick_cancelled(self):
        self._capture = None
        self.status.setText("Pick cancelled. Current selection retained.")
        self._update_buttons()

    def _cancel_pick(self):
        self.picking.cancel()
        self._capture = None
        self.status.setText("Picking finished.")
        self._update_buttons()

    def _thickness_mm(self, *, required=True):
        raw = self.thickness.text().strip()
        if not raw and not required:
            return None
        try:
            value = float(raw)
        except ValueError:
            value = 0.0
        if not 0 < value < float("inf"):
            raise ValueError("Enter the selected plate thickness in millimetres (a positive number).")
        return value

    def _location_args(self):
        direction = None
        if self.target is not None and self.direction.text().strip():
            components = self.direction.text().replace(",", " ").split()
            if len(components) != 3:
                raise ValueError("Enter three global readout direction components (x, y, z).")
            direction = tuple(float(v) for v in components)
        return dict(targets=self.targets, surface=self.surface.currentData(), method="hotspot_a",
                    thickness_mm=self._thickness_mm(required=self.target is not None), side=self.side.currentData(),
                    direction=direction, reference_edge=self.reference_edge)

    def _settings(self):
        from anyfatigue.measures import StressMeasure
        from anyfatigue.simplified import LoadCondition, SimplifiedSettings
        from anyfatigue.sn_curves import get_curve
        from anyfatigue.timeseries import LoadBlock, TimeSeriesSettings

        common = dict(curve=get_curve(self.curve.currentText(), self.environment.currentText()),
                      thickness_mm=self._thickness_mm(), dff=float(self.dff.text()),
                      scf=float(self.scf.text()), design_life_years=float(self.life.text()),
                      measure=StressMeasure.parse(self.measure.currentData()))
        if self.method.currentData() == "simplified":
            condition = LoadCondition("Design condition", self.case.currentText(),
                                      float(self.weibull.text()), float(self.period.text()),
                                      range_factor=self.range_choice.currentData())
            return SimplifiedSettings(**common, n0=float(self.n0.text()), conditions=(condition,))
        items = [self.case_series.item(i) for i in range(self.case_series.count())]
        cases = tuple(item.data(Qt.UserRole) for item in items if item.checkState() == Qt.Checked)
        duration = self.duration.text().strip()
        block = LoadBlock("Stress record", cases, duration_s=float(duration) if duration else None)
        return TimeSeriesSettings(**common, blocks=(block,))

    def preview(self):
        self._assess(False)

    def calculate(self):
        self._assess(True)

    def _assess(self, calculate):
        self.sync_source()
        if self.data is None:
            return
        if not self.element_ids:
            self.status.setText("Pick at least one shell element in the Results view.")
            return
        self._invalidate(keep_pick=True)
        if self.location_kind.currentData() != "direct" and self.target is None:
            self.status.setText("Pick the weld line or toe point on the selected plate.")
            return
        try:
            args = self._location_args()
            settings = self._settings() if calculate else None
        except (ValueError, TypeError) as error:
            self.status.setText(str(error))
            return
        data, element_ids, method = self.data, self.element_ids, self.method.currentData()

        def compute(cancel):
            from anyfatigue.project import AnalysisDefinition
            selected_data = data
            if settings is not None:
                if method == "simplified":
                    cases = [case for condition in settings.conditions
                             for case in (condition.case, condition.reference_case) if case is not None]
                else:
                    cases = [case for block in settings.blocks for case in block.cases]
                selected_data = data.for_cases(cases, cancelled=cancel.is_set)
            selection = prepare_fatigue_locations(selected_data, element_ids, **args)
            result = None
            if settings is not None and not cancel.is_set():
                analysis = AnalysisDefinition(method, settings, selection.locations)
                result = analysis.run(selection.source, keep_series=False)
            return selection, result

        self._submit(compute, self._show_result, "Calculating fatigue…" if calculate else "Locating samples…")

    def _show_result(self, payload):
        prepared, result = payload
        governing = result.critical if result is not None else None
        try:
            if governing is None:
                self.picking.preview(self.data.mesh, prepared.element_ids, prepared.stations)
            else:
                # Each selected location uses one surface. Both lists follow selection order.
                index = next(i for i, point in enumerate(result.points) if point is governing)
                self.picking.preview(self.data.mesh, prepared.element_ids, prepared.stations,
                                     governing_index=index)
        except (RuntimeError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.picking.set_selected(self._selected_refs())
        self.prepared, self.result = prepared, result
        count = len(prepared.element_ids)
        header = f"Selected shell elements · {count}"
        if prepared.face_id is not None:
            readout = prepared.location.readout
            faces = self.data.face_groups.get(prepared.face_id, (prepared.face_id,))
            plate_label = (f"Receiving sheet · {len(faces)} connected faces · {count} shells" if len(faces) > 1
                           else f"Eligible shell elements on plate face {prepared.face_id} · {count}")
            header = (f"{plate_label}\n"
                      f"{self.surface.currentData()} · method A (0.5t, 1.5t) · "
                      f"t = {readout.thickness_mm:g} mm")
        def vector(values):
            return "(" + ", ".join(f"{v:.6g}" for v in values) + ")"
        rows = [header]
        for i, station in enumerate(prepared.stations):
            line = f"{i + 1}. {vector(station.xyz)} m"
            if station.direction is not None:
                line += f" · direction {vector(station.direction)}"
                line += " · probes " + ", ".join(vector(p) for p in station.probes) + " m"
            rows.append(line)
        self.preview_details.setPlainText("\n".join(rows))
        if result is None:
            self.status.setText("Location preview ready.")
            return
        self.table.setRowCount(len(result.points))
        for row, point in enumerate(result.points):
            chainage = "—" if point.chainage is None else f"{point.chainage:.6g}"
            for col, value in enumerate((point.label, chainage, f"{point.damage:.6g}", f"{point.usage:.6g}")):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.selectRow(index)
        self.table.scrollToItem(self.table.item(index, 0))
        label = governing.label
        if governing.chainage is not None:
            label += f" at {governing.chainage:.6g} m"
        self.summary.setText(f"{self.method.currentText()} · {self.source_label.text()}\n"
                             f"Governing station · {label} · usage {governing.usage:.6g}")
        notes = list(result.warnings)
        if prepared.face_id is not None:
            notes.append(f"Nodal stress: {prepared.source.nodal_origin}")
        self.warnings.setPlainText("\n".join(notes))
        self.tabs.setCurrentIndex(2)
        self.status.setText("Assessment complete.")

    def done(self, code):
        if not self._closed:
            self._closed = True
            self._watch.stop()
            self._invalidate(clear_binding=True)
            self.picking.deleteLater()
        super().done(code)
