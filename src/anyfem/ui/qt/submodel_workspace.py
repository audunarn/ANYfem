"""Submodelling workspace: the docked task panel for shell cuts and shell-solid boundaries.

``SubmodelWorkspace(app)`` is a plain widget (the window hosts it in ``panels['Submodelling']``).
It owns no document state: everything stateful (preview, create/replace/delete, background run,
cancellation, history) lives in the window's ``SubmodelTask`` (see ``submodel_task``), which the
Analysis ▸ Submodel dialog shares.

Design rules
------------
* Constructing the widget touches nothing of the window except ``app.guarded``; the task, the
  project lists and the history are bound on first show/``activate()`` (lazy), so it can be built
  before the toolbar, the session or the history exist.
* A global ``refresh()`` is cheap: it compares a signature and only reloads lists / asks for
  history staleness (a worker job that hashes files) when something relevant changed *and* the
  workspace is live (shown).
* Every form edit invalidates a ready or in-flight preview (``task.invalidate_preview``), so a
  stale preview can never be created/replaced.  ``create`` and ``replace`` additionally compare the
  current form request with the request the preview answered.
* The box is shown through the window's real section box (axis-aligned only).  The prior clip
  state is captured on first use and restored when the preview is switched off, the workspace is
  hidden, or it is disposed.  Presentation controls are told the clip state as "seen", so the
  preview box is never written into the project.
* A solid shown for replacement is edited in "replace" mode, never mixed with "create": the id,
  the original source job/case and all earlier runs are preserved by ``SubmodelTask.replace``.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QTimer, Qt
from PySide6.QtGui import QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)
from shiboken6 import isValid

from .submodel_task import (POLICIES, ROW_LIMIT, SKINS, SubmodelRequest, _design, describe_error, source_choices,
                            submodel_task)
from .table import ResultTable

MODES = (("shell", "Shell cut"), ("solid", "Shell–solid boundary"))
BOX_DEBOUNCE_MS = 120
DIAGRAM_DEBOUNCE_MS = 200
_IDENTITY = (0.0, 0.0, 0.0, 1.0)
NO_CASE = "No load case yet"
NO_SOURCE = "No qualifying solved job"


# ----------------------------------------------------------------------
# pure helpers
# ----------------------------------------------------------------------
def plate_thickness(project: Any, face_id: Any) -> Optional[float]:
    """Thickness of the plate section assigned to a face, or None when it has none."""

    try:
        name = project.face_sections.get(int(face_id))
        section = project.plate_sections.get(name) if name is not None else None
        value = getattr(section, "thickness", None)
        return float(value) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def plate_choices(project: Any) -> List[Tuple[int, str]]:
    """(face id, readable label) of the plates a solid can be built on.

    Faces with a plate section come first-class; a model whose faces carry none lists all faces so
    the backend, not the form, decides (and says why).
    """

    try:
        faces = dict(project.geometry.faces)
    except (AttributeError, TypeError):
        return []
    sectioned = [fid for fid in faces if plate_thickness(project, fid) is not None]
    out = []
    for fid in sorted(sectioned or faces):
        face = faces[fid]
        name = getattr(face, "name", None) or getattr(face, "label", None) or ""
        thickness = plate_thickness(project, fid)
        label = f"{name} · face {fid}" if name else f"Plate · face {fid}"
        if thickness is not None:
            label += f" · t {thickness:g}"
        out.append((int(fid), label))
    return out


def axis_permutation(orientation: Sequence[float], tolerance: float = 1e-6) -> Optional[List[List[int]]]:
    """Signed permutation matrix of a unit quaternion (x, y, z, w), or None for a genuinely oblique rotation.

    Every world-aligned multiple of 90° maps the axes onto the axes (up to sign), so such a section box
    is still an axis-aligned box in world space once its half-extents are permuted.
    """

    q = [float(v) for v in orientation]
    norm = math.sqrt(sum(v * v for v in q)) if len(q) == 4 else 0.0
    if not norm > 0.0:
        return None
    x, y, z, w = (v / norm for v in q)
    matrix = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
              [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
              [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    rounded = [[int(round(v)) for v in row] for row in matrix]
    if any(abs(v - r) > tolerance for row, rrow in zip(matrix, rounded) for v, r in zip(row, rrow)):
        return None
    return rounded


def orientation_is_axis_aligned(orientation: Sequence[float]) -> bool:
    """True for the identity and for every world-aligned 90° rotation (q and -q are the same rotation)."""

    return axis_permutation(orientation) is not None


def section_box_bounds(center: Sequence[float], half: Sequence[float], orientation: Sequence[float]
                       ) -> Tuple[List[float], List[float]]:
    """World (lower, upper) of a section box; the half-extents are permuted by a 90°-rotated orientation."""

    matrix = axis_permutation(orientation)
    if matrix is None:
        raise ValueError("The section box is rotated obliquely; only axis-aligned (or 90°-rotated) boxes are supported "
                         "here")
    world = [sum(abs(matrix[i][j]) * float(half[j]) for j in range(3)) for i in range(3)]
    return [float(c) - h for c, h in zip(center, world)], [float(c) + h for c, h in zip(center, world)]


def pad_flat_axes(low: Sequence[float], high: Sequence[float], thickness: float
                  ) -> Tuple[List[float], List[float]]:
    """Give every zero-span axis (a flat XY model, one plate) the plate thickness on both sides."""

    low, high = [float(v) for v in low], [float(v) for v in high]
    scale = max(1.0, *(abs(v) for v in (*low, *high)))
    for axis in range(3):
        if high[axis] - low[axis] <= 1e-9 * scale:
            low[axis] -= thickness
            high[axis] += thickness
    return low, high


def _sub(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a: Sequence[float]) -> float:
    return math.sqrt(sum(c * c for c in a))


def body_frame(footprint: Sequence[Sequence[float]], director: Optional[Sequence[float]] = None
               ) -> Tuple[float, float, Tuple[float, float, float]]:
    """(length along edge 0→1, length along edge 0→3, unit director) of a rectangular footprint."""

    p = [tuple(float(c) for c in q) for q in footprint]
    if len(p) != 4:
        raise ValueError("a footprint needs four corner points")
    u, v = _sub(p[1], p[0]), _sub(p[3], p[0])
    n = _cross(u, v)
    scale = _norm(n)
    if not scale > 0.0:
        raise ValueError("the footprint is degenerate")
    unit = tuple(c / scale for c in n)
    if director is not None and _norm(director) > 0.0:
        d = tuple(float(c) / _norm(director) for c in director)
        unit = d
    return _norm(u), _norm(v), unit  # type: ignore[return-value]


def body_corners(footprint: Sequence[Sequence[float]], thickness: float,
                 director: Optional[Sequence[float]] = None) -> List[Tuple[float, float, float]]:
    """Eight corners of the physical plate body: the footprint offset ±thickness/2 along the director.

    Indices 0-3 are the lower (−director) corners, 4-7 the upper ones, in footprint order.
    """

    thickness = float(thickness)
    if not (thickness > 0.0 and math.isfinite(thickness)):
        raise ValueError("the plate thickness must be positive")
    _l1, _l2, n = body_frame(footprint, director)
    half = thickness / 2.0
    p = [tuple(float(c) for c in q) for q in footprint]
    lower = [tuple(c - half * k for c, k in zip(q, n)) for q in p]
    upper = [tuple(c + half * k for c, k in zip(q, n)) for q in p]
    return lower + upper  # type: ignore[return-value]


def box_around_body(footprint: Sequence[Sequence[float]], thickness: float,
                    director: Optional[Sequence[float]] = None) -> Tuple[Tuple[float, ...], Tuple[float, ...]]:
    """Axis-aligned (lower, upper) bounds of the plate body, a non-degenerate box on every axis."""

    corners = body_corners(footprint, thickness, director)
    lower = tuple(min(c[i] for c in corners) for i in range(3))
    upper = tuple(max(c[i] for c in corners) for i in range(3))
    return lower, upper


def footprint_is_axis_aligned(footprint: Sequence[Sequence[float]]) -> bool:
    p = [tuple(float(c) for c in q) for q in footprint]
    for a, b in zip(p, p[1:] + p[:1]):
        edge = _sub(b, a)
        length = _norm(edge)
        if length > 0.0 and max(abs(c) for c in edge) < (1.0 - 1e-6) * length:
            return False
    return True


def body_scene(footprint: Sequence[Sequence[float]], thickness: float, director: Optional[Sequence[float]] = None) -> Any:
    """Viewport scene of the extruded plate body (six quads, element-less)."""

    import numpy as np

    from ..scene import COLOR_MESH_EDGE, COLOR_MESH_FILL, FacePatch, Scene

    c = np.asarray(body_corners(footprint, thickness, director), dtype=float)
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    patch = FacePatch(None, [c[list(q)] for q in quads], [COLOR_MESH_FILL] * len(quads), outline=COLOR_MESH_EDGE,
                      polygon_node_ids=[tuple(int(i) + 1 for i in q) for q in quads])
    return Scene(faces=[patch], show_element_edges=True)


def definition_body(definition: Any) -> Dict[str, Any]:
    """Footprint, thickness and director stored in a solid definition."""

    reconstruction = definition.reconstruction
    return {"footprint": tuple(tuple(p) for p in definition.footprint),
            "thickness": float(reconstruction["thickness"]), "director": tuple(reconstruction["director"])}


def describe_body(body: Dict[str, Any]) -> str:
    l1, l2, _n = body_frame(body["footprint"], body.get("director"))
    return f"{l1:.4g} × {l2:.4g} footprint, thickness {body['thickness']:.4g} (±{body['thickness'] / 2:.4g} about the plate)"


# ----------------------------------------------------------------------
# diagram
# ----------------------------------------------------------------------
class BodyDiagram(QWidget):
    """True-proportion oblique drawing of the extruded plate body, thickness labelled.

    The thickness is drawn to scale but never thinner than a few pixels so it stays visible; the
    caption always carries the exact numbers.
    """

    MIN_THICKNESS_PX = 4.0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.body: Optional[Dict[str, Any]] = None
        self.note = "No body to show"
        self.setMinimumHeight(110)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setAccessibleName("Plate body diagram")

    def sizeHint(self):  # noqa: N802 - Qt
        from PySide6.QtCore import QSize

        return QSize(180, 120)

    def set_body(self, body: Optional[Dict[str, Any]], note: str = "") -> None:
        self.body, self.note = body, note or ("No body to show" if body is None else "")
        self.update()

    def geometry_points(self) -> Optional[List[Tuple[float, float]]]:
        """Screen-plane polygon corners (pixels) of the front, right and top faces, for tests/painting."""

        if self.body is None:
            return None
        l1, l2, _n = body_frame(self.body["footprint"], self.body.get("director"))
        t = float(self.body["thickness"])
        kx, ky = 0.5 * math.cos(math.radians(35.0)), 0.5 * math.sin(math.radians(35.0))
        width, height = l1 + l2 * kx, t + l2 * ky
        w, h = max(self.width() - 16, 20), max(self.height() - 30, 20)
        scale = min(w / width, h / height) if width > 0 and height > 0 else 1.0
        hscale = scale * max(1.0, self.MIN_THICKNESS_PX / (t * scale)) if t * scale > 0 else scale

        def project(s: float, d: float, up: float) -> Tuple[float, float]:
            return (8.0 + (s + kx * d) * scale, 8.0 + h - (up * hscale + ky * d * scale))

        return [project(0, 0, 0), project(l1, 0, 0), project(l1, 0, t), project(0, 0, t),  # front
                project(l1, l2, 0), project(l1, l2, t),  # right edge back
                project(0, l2, t)]  # top back-left

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            palette = self.palette()
            pts = self.geometry_points()
            if pts is None:
                painter.setPen(palette.color(QPalette.PlaceholderText))
                painter.drawText(self.rect().adjusted(6, 6, -6, -6), Qt.AlignCenter | Qt.TextWordWrap, self.note)
                return
            base = palette.color(QPalette.Highlight)
            front, right, top = [QPolygonF([QPointF(*p) for p in poly]) for poly in (
                (pts[0], pts[1], pts[2], pts[3]), (pts[1], pts[4], pts[5], pts[2]), (pts[3], pts[2], pts[5], pts[6]))]
            painter.setPen(QPen(palette.color(QPalette.WindowText), 1.0))
            for polygon, color in ((front, base.darker(115)), (right, base.darker(145)), (top, base.lighter(130))):
                painter.setBrush(color)
                painter.drawPolygon(polygon)
            painter.setPen(palette.color(QPalette.WindowText))
            painter.drawText(self.rect().adjusted(6, 0, -6, -4), Qt.AlignBottom | Qt.AlignLeft | Qt.TextWordWrap, self.note)
        finally:
            painter.end()


# ----------------------------------------------------------------------
# workspace
# ----------------------------------------------------------------------
class SubmodelWorkspace(QWidget):
    """Task panel for defining, previewing, creating, editing, running and inspecting submodels."""
    FILLS_HEIGHT = True

    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self.setObjectName("SubmodelWorkspace")
        self.setMinimumWidth(0)
        self._task: Optional[Any] = None
        self._links: List[Tuple[Any, Callable[..., Any]]] = []
        self._loading = False
        self._live = False
        self._disposed = False
        self._dirty = True
        self._project: Any = None
        self._list_signature: Any = None
        self._history_signature: Any = None
        self._editing_id: Optional[str] = None
        # what the solid under edit looked like when editing began (see begin_replace)
        self._edit_footprint: Optional[Tuple[Tuple[float, ...], ...]] = None
        self._footprint_key = None
        self._footprint_value = None
        self._edit_face: Optional[int] = None
        self._edit_box: Optional[Tuple[Any, Any]] = None
        self._edit_record: Optional[Dict[str, Any]] = None
        self._source_refusals: Dict[str, Tuple[str, str]] = {}  # job id -> (label, why it is refused)
        self._saved_clip: Optional[Tuple[Any, ...]] = None
        self._box_wire = None
        self._parent_camera = None
        self._local_message = ""
        self._seen_history: Any = None
        self._seen_inspection: Any = None
        self._box_timer = self._timer(BOX_DEBOUNCE_MS, self._apply_box_view)
        self._diagram_timer = self._timer(DIAGRAM_DEBOUNCE_MS, self.refresh_design_body)
        self._report_text = ""
        self._build()

    # -- construction ----------------------------------------------------
    def _timer(self, interval: int, slot: Callable[[], None]) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(interval)
        timer.timeout.connect(slot)
        self._links.append((timer.timeout, slot))
        return timer

    def _link(self, signal: Any, slot: Callable[..., Any]) -> None:
        signal.connect(slot)
        self._links.append((signal, slot))

    def _guard(self, function: Callable[[], Any]) -> Callable[..., Any]:
        guarded = getattr(self.app, "guarded", None)
        if callable(guarded):
            return guarded(function)

        def run(*_: Any) -> None:
            try:
                function()
            except Exception as error:  # noqa: BLE001 - the user sees the refusal text
                self._say(str(error))

        return run

    def _button(self, text: str, slot: Callable[[], Any], tip: str = "") -> QPushButton:
        button = QPushButton(text)
        button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        button.setMinimumWidth(0)
        if tip:
            button.setToolTip(tip)
        self._link(button.clicked, self._guard(slot))
        return button

    @staticmethod
    def _combo() -> QComboBox:
        combo = QComboBox()
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(8)
        combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        return combo

    def _page(self, title: str) -> QVBoxLayout:
        """A top-level tab: its own scroll area, so each tab opens at its own top and is usable at once."""

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(0)
        inner = QWidget()
        inner.setMinimumWidth(0)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(6)
        scroll.setWidget(inner)
        self.tabs.addTab(scroll, title)
        return layout

    def show_tab(self, name: str) -> None:
        """Bring the 'define', 'children' or 'results' tab forward."""

        index = {"define": 0, "children": 1, "results": 2}[name]
        self.tabs.setCurrentIndex(index)

    def current_tab(self) -> str:
        return ("define", "children", "results")[self.tabs.currentIndex()]

    @staticmethod
    def _group(title: str, parent_layout: QVBoxLayout) -> QVBoxLayout:
        box = QGroupBox(title)
        inner = QVBoxLayout(box)
        inner.setContentsMargins(6, 6, 6, 6)
        parent_layout.addWidget(box)
        inner.box = box  # type: ignore[attr-defined]
        return inner

    @staticmethod
    def _form() -> QFormLayout:
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)  # label beside the field; wraps only when it cannot fit
        form.setContentsMargins(0, 0, 0, 0)
        return form

    @staticmethod
    def _wrapped(text: str = "") -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        return label

    @staticmethod
    def _spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-1e9, 1e9)
        spin.setDecimals(6)
        spin.setSingleStep(0.1)
        spin.setKeyboardTracking(False)
        spin.setMinimumWidth(0)
        spin.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        return spin

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        outer.addWidget(self.tabs)
        define_page = self._page("Define")
        children_page = self._page("Children")
        results_page = self._page("Results")
        root = define_page

        # -- definition: mode, source, name ------------------------------
        define = self._group("Submodel", define_page)
        form = self._form()
        define.addLayout(form)
        self.mode = self._combo()
        for token, label in MODES:
            self.mode.addItem(label, token)
        self.mode.setToolTip("Shell cut: a shell model of the part inside the box. Shell–solid boundary: a solid "
                             "block of one plate, driven by the shell result.")
        form.addRow("Type", self.mode)
        self.source = self._combo()
        form.addRow("Source job", self.source)
        self.case = self._wrapped(NO_CASE)
        form.addRow("Load case", self.case)
        self.name = QLineEdit("submodel")
        self.name.setMinimumWidth(0)
        form.addRow("Name", self.name)
        self.source_note = self._wrapped("")  # why no source can be used / why the chosen one is refused
        self.source_note.setVisible(False)
        define.addWidget(self.source_note)
        self.edit_banner = self._wrapped("")
        self.edit_banner.setVisible(False)
        define.addWidget(self.edit_banner)

        # -- box (both modes) --------------------------------------------
        box = self._group("Box", root)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        box.addLayout(grid)
        grid.addWidget(QLabel(""), 0, 0)
        grid.addWidget(QLabel("Min"), 0, 1)
        grid.addWidget(QLabel("Max"), 0, 2)
        self.box_min: List[QDoubleSpinBox] = []
        self.box_max: List[QDoubleSpinBox] = []
        for row, axis in enumerate("XYZ", start=1):
            low, high = self._spin(), self._spin()
            low.setValue(0.0)
            high.setValue(1.0)
            low.setToolTip(f"Lower {axis} bound (m)")
            high.setToolTip(f"Upper {axis} bound (m)")
            grid.addWidget(QLabel(axis), row, 0)
            grid.addWidget(low, row, 1)
            grid.addWidget(high, row, 2)
            self.box_min.append(low)
            self.box_max.append(high)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        row = QGridLayout()
        box.addLayout(row)
        self.use_selection_box_button = self._button(
            "From selection", self.use_selection_box,
            "Take the bounds of the selected points, lines or plates (a flat selection gets the plate thickness)")
        self.use_view_box_button = self._button("From section box", self.use_view_box,
                                                "Take the bounds of the view's axis-aligned section box")
        self.fit_box_button = self._button("Fit to model", self.fit_box, "Central half of the model geometry")
        row.addWidget(self.use_selection_box_button, 0, 0)
        row.addWidget(self.use_view_box_button, 0, 1)
        row.addWidget(self.fit_box_button, 1, 0)
        self.show_box = QCheckBox("Show box in view")
        self.show_box.setToolTip("Show the chosen box in the model view.")
        row.addWidget(self.show_box, 1, 1)
        self.box_note = self._wrapped("")
        box.addWidget(self.box_note)
        # preview / create feedback sits with the box it answers
        self.status = self._wrapped("")
        box.addWidget(self.status)

        # -- shell options -----------------------------------------------
        shell = self._group("Shell options", root)
        self.shell_group = shell.box  # type: ignore[attr-defined]
        form = self._form()
        shell.addLayout(form)
        self.policy = self._combo()
        for token, label in POLICIES:
            self.policy.addItem(label, token)
        form.addRow("Cut-face policy", self.policy)

        # -- solid options -----------------------------------------------
        solid = self._group("Solid options", root)
        self.solid_group = solid.box  # type: ignore[attr-defined]
        form = self._form()
        solid.addLayout(form)
        self.plate = self._combo()
        form.addRow("Plate", self.plate)
        self.use_selection_button = self._button("Use selected plate", self.use_selected_plate,
                                                 "Pick the plate selected in the model tree or view")
        solid.addWidget(self.use_selection_button)
        self.layers = QSpinBox()
        self.layers.setRange(1, 1000)
        self.layers.setValue(1)
        self.layers.setMinimumWidth(0)
        form.addRow("Layers", self.layers)
        self.order = self._combo()
        self.order.addItem("P1 (linear)", 1)
        self.order.addItem("P2 (quadratic)", 2)
        form.addRow("Element order", self.order)
        self.pressure_skin = self._combo()
        for value, label in SKINS:
            self.pressure_skin.addItem(label, value)
        self.pressure_skin.setToolTip("The skin that carries the plate's uniform pressure; required when the plate is "
                                      "loaded, refused when it is not.")
        form.addRow("Pressure skin", self.pressure_skin)
        self.design_diagram = BodyDiagram()
        solid.addWidget(self.design_diagram)

        # -- sizing (both) -----------------------------------------------
        sizing = self._form()
        define.addLayout(sizing)
        self.target_size = self._spin()
        self.target_size.setRange(1e-9, 1e9)
        self.target_size.setDecimals(6)
        self.target_size.setValue(0.1)
        sizing.addRow("Target size (m)", self.target_size)

        # -- actions -----------------------------------------------------
        actions = self._group("Preview and create", root)
        row = QHBoxLayout()
        actions.addLayout(row)
        self.preview_button = self._button("Preview", self.preview, "Cut on a snapshot; nothing is stored")
        self.create_button = self._button("Create", self.create, "Keep the previewed submodel in the project")
        self.replace_button = self._button("Replace solid", self.replace,
                                           "Replace the edited solid by the previewed one (same id, source and runs)")
        self.cancel_edit_button = self._button("Cancel edit", self.cancel_edit, "Leave replace mode")
        for button in (self.preview_button, self.create_button, self.replace_button, self.cancel_edit_button):
            row.addWidget(button)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(110)
        self.report.setPlaceholderText("Cut report: carried, dropped, skipped, refused")
        actions.addWidget(self.report)

        # -- children and run --------------------------------------------
        define_page.addStretch(1)
        children = self._group("Submodels and run", children_page)
        self.child_list = self._combo()
        children.addWidget(self.child_list)
        self.child_summary = self._wrapped("")
        children.addWidget(self.child_summary)
        self.child_diagram = BodyDiagram()
        children.addWidget(self.child_diagram)
        row = QHBoxLayout()
        children.addLayout(row)
        self.edit_button = self._button("Edit", self.edit_selected, "Shell: open its editor. Solid: edit and replace.")
        self.delete_button = self._button("Delete", self.delete_selected, "Delete the selected solid (undoable)")
        self.body_button = self._button("Show body", self.show_selected_body, "Draw the plate body in the view")
        for button in (self.edit_button, self.delete_button, self.body_button):
            row.addWidget(button)
        row = QHBoxLayout()
        children.addLayout(row)
        self.run_button = self._button("Run", self.run_selected, "Run in the background")
        self.cancel_button = self._button("Cancel", self.cancel_run, "Cancel the preview or run")
        self.restore_button = self._button("Restore view", self.restore_view)
        for button in (self.run_button, self.cancel_button, self.restore_button):
            row.addWidget(button)
        self.run_state = self._wrapped("")
        children.addWidget(self.run_state)

        # -- saved results -----------------------------------------------
        children_page.addStretch(1)
        results = self._group("Saved results", results_page)
        self.runs = QTableWidget(0, 3)
        self.runs.setHorizontalHeaderLabels(["Run", "Submodel", "Status"])
        self.runs.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.runs.setSelectionMode(QAbstractItemView.SingleSelection)
        self.runs.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.runs.horizontalHeader().setStretchLastSection(True)
        self.runs.setMaximumHeight(140)
        self.runs.setMinimumWidth(0)
        results.addWidget(self.runs)
        self.view_side = self._combo()
        self.view_side.addItem("Child (submodel)", "child")
        self.view_side.addItem("Parent (source job)", "parent")
        results.addWidget(self.view_side)
        self.field = self._combo()
        results.addWidget(self.field)
        row = QHBoxLayout()
        results.addLayout(row)
        self.show_mesh_button = self._button("Show child mesh", self.show_child_mesh)
        self.refresh_button = self._button("Refresh status", self.refresh_history)
        row.addWidget(self.show_mesh_button)
        row.addWidget(self.refresh_button)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(130)
        results.addWidget(self.details)
        self.table = ResultTable()
        self.table.setMinimumHeight(120)
        results.addWidget(self.table)
        results_page.addStretch(1)

        # -- wiring ------------------------------------------------------
        self._link(self.mode.currentIndexChanged, self._mode_changed)
        self._link(self.source.currentIndexChanged, self._source_changed)
        self._link(self.name.textChanged, self._edited)
        self._link(self.policy.currentIndexChanged, self._edited)
        self._link(self.plate.currentIndexChanged, self._edited)
        self._link(self.layers.valueChanged, self._edited)
        self._link(self.order.currentIndexChanged, self._edited)
        self._link(self.pressure_skin.currentIndexChanged, self._edited)
        self._link(self.target_size.valueChanged, self._edited)
        for spin in (*self.box_min, *self.box_max):
            self._link(spin.valueChanged, self._box_edited)
        self._link(self.show_box.toggled, self._show_box_toggled)
        self._link(self.child_list.currentIndexChanged, self._child_changed)
        self._link(self.runs.itemSelectionChanged, self._guard(self._select_run))
        self._link(self.view_side.currentIndexChanged, self._guard(self._select_run))
        self._link(self.field.activated, self._guard(self._field_chosen))
        self._apply_mode()
        self.sync()

    # -- lifecycle -------------------------------------------------------
    @property
    def task(self) -> Any:
        """The window's one SubmodelTask, bound on first use."""

        if self._task is None:
            self._task = submodel_task(self.app)
            self._task.add_listener(self._on_task)
        return self._task

    def _on_task(self) -> None:
        if self._disposed or not isValid(self):
            if self._task is not None:
                self._task.remove_listener(self._on_task)
            return
        self._local_message = ""
        self.sync()

    def showEvent(self, event: Any) -> None:  # noqa: N802 - Qt
        super().showEvent(event)
        self.activate()

    def hideEvent(self, event: Any) -> None:  # noqa: N802 - Qt
        super().hideEvent(event)
        self.deactivate()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt
        self.dispose()
        super().closeEvent(event)

    def activate(self) -> None:
        """Become live: bind the task and bring lists and history up to date (idempotent)."""

        if self._disposed:
            return
        self._live = True
        self.reload()
        if self.show_box.isChecked():
            self._apply_box_view()

    def deactivate(self) -> None:
        """Leave the panel: the prior clipping returns, nothing else changes."""

        self._live = False
        self._box_timer.stop()
        self._restore_clip()

    def dispose(self) -> None:
        """Release the task listener, timers, signal links and the clip state (idempotent)."""

        if self._disposed:
            return
        self._disposed = True
        self._live = False
        for timer in (self._box_timer, self._diagram_timer):
            timer.stop()
        self._restore_clip()
        if self._task is not None:
            self._task.remove_listener(self._on_task)
        for signal, slot in self._links:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._links.clear()

    # -- environment -----------------------------------------------------
    def _project_now(self) -> Any:
        return getattr(self.app, "project", None)

    def _session(self) -> Any:
        return getattr(self.app, "session", None)

    def read_only(self) -> bool:
        return bool(getattr(self._session(), "read_only", False))

    def _revision(self) -> Any:
        return getattr(getattr(self._session(), "revision", None), "id", None)

    def _ready(self) -> bool:
        return self._project_now() is not None and self._session() is not None

    def _say(self, text: str) -> None:
        self._local_message = text
        self.status.setText(text)

    # -- global refresh (Workbench) -------------------------------------
    def refresh(self) -> None:
        """Called on every global refresh: cheap unless something relevant changed while live."""

        if self._disposed or not self._ready():
            return
        project = self._project_now()
        if project is not self._project:
            self._project_replaced(project)
        task = self._task
        if task is not None and task.preview is not None and task.preview[0] != self._revision():
            task.invalidate_preview("The document changed; preview again")
        if not self._live:
            self._dirty = True
            return
        signature = self._lists_signature()
        if signature != self._list_signature:
            self.reload_lists()
        history = (id(project), self._revision(), tuple(project.submodel_runs))
        if history != self._history_signature:
            self._history_signature = history
            self.refresh_history(quiet=True)
        self.sync()

    def _lists_signature(self) -> Any:
        project = self._project_now()
        # The committed revision covers geometry, sections and submodel edits, so plates are not rescanned here.
        jobs = tuple((j.id, str(getattr(j.status, "value", j.status))) for j in project.jobs.values())
        return (id(project), self._revision(), jobs, tuple(project.submodels))

    def _project_replaced(self, project: Any) -> None:
        """A different document (open/new): nothing of the old one may survive in the form."""

        self._project = project
        self._parent_camera = None
        self._editing_id = None
        self._list_signature = None
        self._history_signature = None
        self._seen_history = self._seen_inspection = None
        if self._task is not None:
            self._task.invalidate_preview("The project changed")
        self._restore_clip()
        blocked = self.show_box.blockSignals(True)  # the box preview belongs to the document that is gone
        self.show_box.setChecked(False)
        self.show_box.blockSignals(blocked)
        self._set_edit_banner()
        self._dirty = True

    def reload(self) -> None:
        if self._disposed or not self._ready():
            self.sync()
            return
        _ = self.task  # bind the listener now that the window is ready
        project = self._project_now()
        if project is not self._project:
            self._project_replaced(project)
        self.reload_lists()
        self._history_signature = (id(project), self._revision(), tuple(project.submodel_runs))
        self.refresh_history(quiet=True)
        if self._dirty:
            self._seed_box()
        self._dirty = False
        self.sync()

    def reload_lists(self) -> None:
        project = self._project_now()
        if project is None:
            return
        self._list_signature = self._lists_signature()
        self._loading = True
        try:
            current = self.source.currentData()
            self.source.blockSignals(True)
            self.source.clear()
            self._source_refusals = {}
            for job_id, label, case, refusal in source_choices(project):
                self.source.addItem(label, job_id)
                index = self.source.count() - 1
                self.source.setItemData(index, case, Qt.UserRole + 1)
                if refusal:
                    self.source.model().item(index).setEnabled(False)
                    self.source.setItemData(index, refusal, Qt.ToolTipRole)
                    self._source_refusals[job_id] = (label, refusal)
            usable = [i for i in range(self.source.count()) if self.source.model().item(i).isEnabled()]
            found = self.source.findData(current) if current is not None else -1
            if found in usable:  # a refused source is never kept or defaulted to
                self.source.setCurrentIndex(found)
            elif usable:
                self.source.setCurrentIndex(usable[0])
            else:  # a visible, disabled placeholder instead of a blank dropdown
                self.source.addItem(NO_SOURCE, None)
                placeholder = self.source.count() - 1
                self.source.model().item(placeholder).setEnabled(False)
                self.source.setCurrentIndex(placeholder)
            self.source.blockSignals(False)
            self._show_case()

            chosen = self.plate.currentData()
            self.plate.blockSignals(True)
            self.plate.clear()
            for face_id, label in plate_choices(project):
                self.plate.addItem(label, face_id)
            if chosen is not None and self.plate.findData(chosen) >= 0:
                self.plate.setCurrentIndex(self.plate.findData(chosen))
            elif self.plate.count():
                self.plate.setCurrentIndex(0)  # the only plate is the default
            self.plate.blockSignals(False)

            chosen = self.child_list.currentData()
            self.child_list.blockSignals(True)
            self.child_list.clear()
            for definition_id, label in self.task.definitions():
                self.child_list.addItem(label, definition_id)
            if chosen is not None and self.child_list.findData(chosen) >= 0:
                self.child_list.setCurrentIndex(self.child_list.findData(chosen))
            self.child_list.blockSignals(False)
        finally:
            self._loading = False
            self.source.blockSignals(False)
            self.plate.blockSignals(False)
            self.child_list.blockSignals(False)
        if self._editing_id is not None and self._editing_id not in project.submodels:
            self._editing_id = None  # the solid under edit was deleted elsewhere (undo, script)
            self._set_edit_banner()
        self._child_changed()
        self.schedule_design_body()

    def _seed_box(self) -> None:
        """First box: the view's axis-aligned section box, else the central half of the model geometry, else 0..1."""

        try:
            user = self._user_box()
        except ValueError:
            user = None
        if user is None:
            try:
                user = self._central_half()
            except Exception:  # noqa: BLE001 - seeding is a convenience; the form keeps its 0..1 default
                user = None
        if user is not None:
            self._set_box(*user, notify=False)

    # -- geometry bounds (the project's own geometry, never scene decorations) -------
    def _geometry_refs(self, kinds: Tuple[str, ...] = ("face", "edge", "vertex")) -> List[Any]:
        """Persistent refs of every entity of the first kind the geometry has (faces, else edges, else vertices)."""

        from anygeometry.entities import EntityRef

        geometry = getattr(self._project_now(), "geometry", None)
        for kind in kinds:
            table = getattr(geometry, {"face": "faces", "edge": "edges", "vertex": "vertices"}[kind], None)
            if table:
                return [EntityRef(kind, int(i)) for i in table]
        return []

    def _reference_thickness(self, face_ids: Sequence[int] = ()) -> float:
        """Thickness used to pad a flat box: the selected plates', else the chosen plate's, else the largest."""

        project = self._project_now()
        candidates = [plate_thickness(project, f) for f in face_ids]
        if not any(candidates):
            candidates = [plate_thickness(project, self.plate.currentData())] if self.plate.currentData() is not None else []
        if not any(candidates):
            candidates = [plate_thickness(project, f) for f, _ in plate_choices(project)]
        values = [c for c in candidates if c]
        return max(values) if values else 1e-3

    def _bounds_of(self, refs: Sequence[Any], face_ids: Sequence[int] = ()) -> Tuple[List[float], List[float]]:
        """(lower, upper) of ``geometry.bounds(refs)`` with flat axes padded by the plate thickness."""

        geometry = getattr(self._project_now(), "geometry", None)
        bounds = geometry.bounds(list(refs)) if refs and geometry is not None else None
        if bounds is None:
            raise ValueError("The model has no geometry to take bounds from")
        xmin, ymin, zmin, xmax, ymax, zmax = (float(v) for v in bounds)
        return pad_flat_axes((xmin, ymin, zmin), (xmax, ymax, zmax), self._reference_thickness(face_ids))

    def _central_half(self) -> Tuple[List[float], List[float]]:
        """Central half of the model geometry on every axis that has extent; flat axes get the plate thickness."""

        refs = self._geometry_refs()
        if not refs:
            raise ValueError("The model has no geometry to fit the box to")
        geometry = self._project_now().geometry
        bounds = geometry.bounds(refs)
        low, high = [float(v) for v in bounds[:3]], [float(v) for v in bounds[3:]]
        scale = max(1.0, *(abs(v) for v in (*low, *high)))
        flat = [h - l <= 1e-9 * scale for l, h in zip(low, high)]
        low, high = pad_flat_axes(low, high, self._reference_thickness())
        span = [h - l for l, h in zip(low, high)]
        return ([l if f else l + s / 4 for l, s, f in zip(low, span, flat)],
                [h if f else h - s / 4 for h, s, f in zip(high, span, flat)])

    # -- form -----------------------------------------------------------
    def kind(self) -> str:
        return str(self.mode.currentData())

    def box(self) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        lower = tuple(float(s.value()) for s in self.box_min)
        upper = tuple(float(s.value()) for s in self.box_max)
        if not all(a < b for a, b in zip(lower, upper)):
            raise ValueError("Box min must be below box max on every axis")
        return lower, upper  # type: ignore[return-value]

    def _set_box(self, lower: Sequence[float], upper: Sequence[float], notify: bool = True) -> None:
        blocked = [s.blockSignals(True) for s in (*self.box_min, *self.box_max)]
        try:
            for spin, value in zip((*self.box_min, *self.box_max), (*lower, *upper)):
                spin.setValue(float(value))
        finally:
            for spin, was in zip((*self.box_min, *self.box_max), blocked):
                spin.blockSignals(was)
        if notify:
            self._box_edited()

    # -- source ------------------------------------------------------------
    def source_usable(self) -> bool:
        """True when the chosen source is a real, enabled (qualifying) solved job."""

        index = self.source.currentIndex()
        if index < 0 or not self.source.currentData():
            return False
        item = self.source.model().item(index)
        return item is None or item.isEnabled()

    def source_problem(self) -> str:
        """Why Preview is not possible for the source, by source; empty when it is usable."""

        if self.source_usable():
            return ""
        job_id = self.source.currentData()
        if job_id and job_id in self._source_refusals:
            label, why = self._source_refusals[job_id]
            return f"Source {label} is refused: {why}."
        refused = list(self._source_refusals.values())
        text = ("No qualifying solved job. Solve a linear static load case first (a completed, non-partial job), "
                "then choose it here.")
        if refused:
            text += " Refused: " + "; ".join(f"{label}: {why}" for label, why in refused) + "."
        return text

    def _show_case(self) -> None:
        self.case.setText(self.source.currentData(Qt.UserRole + 1) or NO_CASE)
        self._show_source_note()

    def _show_source_note(self) -> None:
        problem = self.source_problem()
        self.source_note.setText(problem)
        self.source_note.setVisible(bool(problem))

    def _solid_footprint(self, face: int, lower: Sequence[float], upper: Sequence[float]
                         ) -> Tuple[Tuple[float, ...], ...]:
        """The solid's footprint for this plate and box.

        While a stored solid is being replaced and neither the plate nor the box was edited, the EXACT
        stored footprint (order, rotation and round-off included) is kept; any explicit box or plate
        edit regenerates it from the box.  Layers, element order, skin, size or name never touch it.
        """

        if (self._editing_id is not None and self._edit_footprint is not None and self._edit_face == int(face)
                and self._edit_box == (tuple(lower), tuple(upper))):
            return self._edit_footprint
        project = self._project_now()
        key = (id(project), self._revision(), getattr(project.geometry, "revision", None),
               face, plate_thickness(project, face), tuple(lower), tuple(upper))
        if key != self._footprint_key:
            footprint = _design().solid_box_footprint(project, int(face), lower, upper)
            self._footprint_value = tuple(tuple(float(c) for c in p) for p in footprint)
            self._footprint_key = key
        return self._footprint_value

    def request(self) -> SubmodelRequest:
        job_id = self.source.currentData()
        if not job_id or not self.source_usable():
            raise ValueError("Choose a completed source job: " + (self.source_problem() or "none is selected"))
        case = self.source.currentData(Qt.UserRole + 1)
        size = float(self.target_size.value())
        if not size > 0.0:
            raise ValueError("Target size must be positive")
        lower, upper = self.box()
        common = dict(job_id=job_id, case=case, target_size=size, name=self.name.text().strip() or "submodel",
                      lower=lower, upper=upper)
        if self.kind() == "solid":
            face = self.plate.currentData()
            if face is None:
                raise ValueError("Choose a plate")
            footprint = self._solid_footprint(int(face), lower, upper)
            return SubmodelRequest(kind="solid", face_id=int(face), footprint=footprint, layers=self.layers.value(),
                                   order=int(self.order.currentData()), pressure_skin=self.pressure_skin.currentData(),
                                   **common)
        return SubmodelRequest(kind="shell", policy=self.policy.currentData(), **common)

    def _apply_mode(self) -> None:
        solid = self.kind() == "solid"
        self.solid_group.setVisible(solid)
        self.shell_group.setVisible(not solid)

    def _mode_changed(self, *_: Any) -> None:
        self._apply_mode()
        self._edited()

    def _source_changed(self, *_: Any) -> None:
        self._show_case()
        self._edited()

    def _edited(self, *_: Any) -> None:
        """Any form edit: the preview it answered is stale; nothing heavy runs here."""

        if self._loading or self._disposed:
            return
        if self._task is not None:
            self._task.invalidate_preview("The form changed; preview again")
        self.schedule_design_body()
        self.sync()

    def _box_edited(self, *_: Any) -> None:
        if self._loading or self._disposed:
            return
        if self.show_box.isChecked() and self._live:
            self._box_timer.start()
        self._edited()

    # -- box in the real view --------------------------------------------
    def _canvas(self) -> Any:
        return getattr(getattr(self.app, "viewport", None), "canvas", None)

    def _view_box(self) -> Any:
        canvas = self._canvas()
        exporter = getattr(canvas, "export_view_state", None)
        state = exporter() if callable(exporter) else None
        return getattr(state, "section_box", None)

    @staticmethod
    def _box_tuple(box: Any) -> Tuple[Any, ...]:
        center = tuple(float(c) for c in box.center.to_tuple())
        half = tuple(float(c) for c in box.half_extents.to_tuple())
        return (center, half, tuple(float(c) for c in box.orientation), bool(box.enabled))

    def _user_box(self) -> Optional[Tuple[List[float], List[float]]]:
        """The user's own axis-aligned section box (never our preview), as (lower, upper)."""

        if self._saved_clip is not None:
            saved = self._saved_clip
            if saved[0] == "none":
                return None
            center, half, orientation, enabled = saved
        else:
            box = self._view_box()
            if box is None:
                return None
            center, half, orientation, enabled = self._box_tuple(box)
        if not enabled:
            return None
        return section_box_bounds(center, half, orientation)  # refuses a genuinely oblique box

    def use_view_box(self) -> None:
        user = self._user_box()
        if user is None:
            raise ValueError("The view has no section box; enable one in Sectioning or use Fit to model")
        self._set_box(*user)
        self._say("Box taken from the section box")

    def fit_box(self) -> None:
        low, high = self._central_half()
        self._set_box(low, high)
        self._say("Box set to the central half of the model geometry")

    def use_selection_box(self) -> None:
        """Box from the selected points, lines and plates; flat axes get the plate thickness."""

        selection = getattr(self.app, "selection", None)
        refs = [r for r in list(getattr(selection, "items", ()) or ())
                if getattr(r, "kind", None) in ("vertex", "edge", "face")]
        if not refs:
            raise ValueError("Select points, lines or plates in the model first")
        faces = [int(r.id) for r in refs if r.kind == "face"]
        low, high = self._bounds_of(refs, faces)
        self._set_box(low, high)
        self._say("Box taken from the selection")

    def _capture_clip(self) -> None:
        if self._saved_clip is not None:
            return
        box = self._view_box()
        self._saved_clip = ("none",) if box is None else self._box_tuple(box)

    def _tell_presentation(self) -> None:
        """Mark the new clip state as already seen so the Sectioning controls never persist our preview."""

        canvas = self._canvas()
        exporter = getattr(canvas, "export_view_state", None)
        if not callable(exporter):
            return
        state = exporter()
        for panel in list(getattr(self.app, "panels", {}).values()):
            controls = getattr(panel, "presentation_controls", None)
            if controls is not None and hasattr(controls, "_seen_clip"):
                controls._seen_clip = (state.section_box, state.section_plane)
                controls._clip_candidate = None

    def _show_box_toggled(self, on: bool) -> None:
        self._box_timer.stop()
        if on:
            self._apply_box_view()
        else:
            self._restore_clip()

    def _apply_box_view(self) -> None:
        if self._disposed or not self.show_box.isChecked():
            return
        canvas = self._canvas()
        try:
            lower, upper = self.box()
        except ValueError as error:
            if self._box_wire is not None:
                self._restore_clip()
            self.box_note.setText(str(error))
            return
        if callable(getattr(canvas, "add_mesh_arrays", None)):
            from itertools import product
            import numpy as np
            from any3dview import MeshArrays

            corners = np.asarray(list(product(*zip(lower, upper))))
            edges = [(i, i ^ bit) for i in range(8) for bit in (1, 2, 4) if i < i ^ bit]
            lines = MeshArrays(corners, np.empty((0, 3), dtype=np.uint32), lines=np.asarray(edges, dtype=np.uint32))
            self._restore_clip()
            self._box_wire = canvas.add_mesh_arrays(lines, color="#1976cf", line_color="#1976cf",
                                                    line_width=2, draw_overlay=True, pickable=False)
            self.box_note.clear()
            return
        setter = getattr(canvas, "set_section_box", None)
        if not callable(setter):
            self.box_note.setText("This renderer cannot show a section box.")
            return
        self._capture_clip()
        center = [(a + b) / 2.0 for a, b in zip(lower, upper)]
        half = [(b - a) / 2.0 for a, b in zip(lower, upper)]
        setter(center, half, orientation=_IDENTITY, enabled=True)
        redraw = getattr(canvas, "redraw", None)
        if callable(redraw):
            redraw()
        self._tell_presentation()
        self.box_note.setText("")

    def _restore_clip(self) -> None:
        if self._box_wire is not None:
            self._box_wire.remove()
            self._box_wire = None
        saved, self._saved_clip = self._saved_clip, None
        if saved is None:
            return
        canvas = self._canvas()
        try:
            if saved[0] == "none":
                clear = getattr(canvas, "clear_section_box", None)
                if callable(clear):
                    clear()
            else:
                center, half, orientation, enabled = saved
                canvas.set_section_box(center, half, orientation=orientation, enabled=enabled)
            redraw = getattr(canvas, "redraw", None)
            if callable(redraw):
                redraw()
            self._tell_presentation()
        except Exception as error:  # noqa: BLE001 - leaving a panel must not raise
            self._local_message = f"Could not restore the section box: {error}"

    # -- plates ----------------------------------------------------------
    def use_selected_plate(self) -> None:
        selection = getattr(self.app, "selection", None)
        items = list(getattr(selection, "items", ()) or ())
        ids = [getattr(i, "id", None) for i in items if getattr(i, "kind", None) in ("face", "plate")]
        for face_id in ids:
            index = self.plate.findData(face_id)
            if index >= 0:
                self.plate.setCurrentIndex(index)
                self._say("Plate taken from the selection")
                return
        raise ValueError("Select a plate in the model tree or the view first")

    # -- design diagram --------------------------------------------------
    def schedule_design_body(self) -> None:
        if not self._disposed and self._live and self.kind() == "solid":
            self._diagram_timer.start()

    def design_body(self) -> Tuple[Optional[Dict[str, Any]], str]:
        """(body, caption) of the form: the previewed solid when ready, else box ∩ plate with the plate thickness."""

        task = self._task
        if task is not None and task.preview is not None and task.preview_request is not None:
            definition = task.preview[1].definition
            if getattr(definition, "child_kind", "shell") == "solid":
                body = definition_body(definition)
                return body, "Previewed: " + describe_body(body)
        project = self._project_now()
        face = self.plate.currentData()
        if project is None or face is None:
            return None, "Choose a plate"
        thickness = plate_thickness(project, face)
        if thickness is None:
            return None, "The plate has no thickness (no plate section)"
        try:
            lower, upper = self.box()
            footprint = self._solid_footprint(int(face), lower, upper)
        except Exception as error:  # noqa: BLE001 - shown in the diagram, not raised
            return None, describe_error(error)
        body = {"footprint": tuple(tuple(float(c) for c in p) for p in footprint), "thickness": thickness,
                "director": None}
        try:
            return body, "Box ∩ plate: " + describe_body(body)
        except ValueError as error:
            return None, str(error)

    def refresh_design_body(self) -> None:
        if self._disposed or self.kind() != "solid" or not self._ready():
            return
        body, caption = self.design_body()
        self.design_diagram.set_body(body, caption)

    # -- preview / create / replace --------------------------------------
    def _check_edit_unchanged(self) -> None:
        """Refuse to preview/replace when the solid under edit was changed or removed since editing began."""

        if self._editing_id is None:
            return
        project = self._project_now()
        current = None if project is None else project.submodels.get(self._editing_id)
        if current is None:
            problem = "The solid being edited no longer exists; cancel the edit"
        elif self._edit_record is not None and current.to_dict() != self._edit_record:
            problem = "The solid was changed elsewhere since you began editing it; cancel the edit and reopen it"
        else:
            return
        if self._task is not None:
            self._task.invalidate_preview(problem)
        raise ValueError(problem)

    def preview(self) -> None:
        self._check_edit_unchanged()
        request = self.request()
        self.report.clear()
        self.task.start_preview(request)

    def _checked_preview(self) -> None:
        task = self.task
        if task.preview is None or task.preview_request is None:
            raise ValueError("Preview first")
        if task.preview_request != self.request():
            task.invalidate_preview("The form changed; preview again")
            raise ValueError("The form changed since the preview; preview again")

    def create(self) -> str:
        if self._editing_id is not None:
            raise ValueError("A solid is being edited: use Replace, or Cancel edit to create a new submodel")
        self._checked_preview()
        definition_id = self.task.create()
        self.reload_lists()
        index = self.child_list.findData(definition_id)
        if index >= 0:
            self.child_list.setCurrentIndex(index)
        self.report.appendPlainText(f"\nCreated submodel {definition_id}")
        self.show_tab("children")  # the next step is running it
        self.sync()
        return definition_id

    def replace(self) -> str:
        if self._editing_id is None:
            raise ValueError("Edit a solid first")
        if self.kind() != "solid":
            raise ValueError("Only a solid can be replaced")
        self._check_edit_unchanged()
        self._checked_preview()
        definition_id = self.task.replace(self._editing_id, expected_record=self._edit_record)
        self._editing_id = None
        self._set_edit_banner()
        self.reload_lists()
        index = self.child_list.findData(definition_id)
        if index >= 0:
            self.child_list.setCurrentIndex(index)
        self.show_tab("children")
        self.sync()
        return definition_id

    # -- children: select / edit / delete / run ---------------------------
    def selected_id(self) -> Optional[str]:
        return self.child_list.currentData()

    def selected_definition(self) -> Any:
        definition_id = self.selected_id()
        project = self._project_now()
        return None if definition_id is None or project is None else project.submodels.get(definition_id)

    def _child_changed(self, *_: Any) -> None:
        definition = self.selected_definition()
        solid = definition is not None and getattr(definition, "child_kind", "shell") == "solid"
        self.child_diagram.setVisible(solid)
        self.body_button.setVisible(solid)
        self.delete_button.setEnabled(solid)
        if definition is None:
            self.child_summary.setText("No submodel yet")
        elif solid:
            try:
                body = definition_body(definition)
                self.child_diagram.set_body(body, describe_body(body))
                self.child_summary.setText(
                    f"Solid · plate face {definition.face_id} · P{definition.order} · {definition.layers} layers · "
                    f"skin {definition.pressure_skin or 'none'} · size {definition.target_size:g}")
            except Exception as error:  # noqa: BLE001
                self.child_diagram.set_body(None, describe_error(error))
        else:
            self.child_summary.setText(f"Shell · {definition.policy} · size {definition.target_size:g}")
        self.sync()

    def edit_selected(self) -> None:
        definition = self.selected_definition()
        if definition is None:
            raise ValueError("Select a submodel to edit")
        if getattr(definition, "child_kind", "shell") == "solid":
            self.begin_replace(definition)
            return
        from .submodel_child_editor import open_shell_child_editor

        open_shell_child_editor(self.app, definition.id)

    def begin_replace(self, definition: Any) -> None:
        """Load a stored solid into the form; Replace stays disabled until the change is previewed."""

        if self.read_only():
            raise ValueError("The document is read-only")
        job_id = definition.source.job_id
        job_index = self.source.findData(job_id)
        if job_index < 0:
            raise ValueError(f"The source job {job_id[:8]} of this solid is no longer available")
        if not self.source.model().item(job_index).isEnabled():
            why = self._source_refusals.get(job_id, ("", "it no longer qualifies"))[1]
            raise ValueError(f"The source job {job_id[:8]} of this solid is refused: {why}")
        plate_index = self.plate.findData(int(definition.face_id))
        if plate_index < 0:
            raise ValueError(f"Plate face {definition.face_id} is no longer in the model")
        body = definition_body(definition)
        lower, upper = box_around_body(body["footprint"], body["thickness"], body["director"])
        self._loading = True
        try:
            self.mode.setCurrentIndex(self.mode.findData("solid"))
            self._apply_mode()
            self.source.setCurrentIndex(job_index)
            self._show_case()
            self.plate.setCurrentIndex(plate_index)
            self.name.setText(definition.name)
            self.target_size.setValue(float(definition.target_size))
            self.layers.setValue(int(definition.layers))
            self.order.setCurrentIndex(max(0, self.order.findData(int(definition.order))))
            self.pressure_skin.setCurrentIndex(next(
                (i for i in range(self.pressure_skin.count())
                 if self.pressure_skin.itemData(i) == definition.pressure_skin), 0))
        finally:
            self._loading = False
        self._set_box(lower, upper, notify=False)
        self._editing_id = definition.id
        # Snapshot what editing began from: the exact footprint, the box the spin boxes now show (after their
        # rounding), the plate, and the stored record (to detect a concurrent edit before preview/replace).
        self._edit_footprint = body["footprint"]
        self._edit_face = int(definition.face_id)
        try:
            self._edit_box = self.box()
        except ValueError:
            self._edit_box = None
        self._edit_record = definition.to_dict()
        if self._task is not None:
            self._task.invalidate_preview("Editing a solid; preview the changes")
        self._set_edit_banner()
        note = "" if footprint_is_axis_aligned(body["footprint"]) else (
            " The rotated footprint is preserved until you change its plate or box.")
        self._say("Editing the solid: change what you need, Preview, then Replace." + note)
        self.schedule_design_body()
        if self.show_box.isChecked():
            self._box_timer.start()
        self.show_tab("define")  # editing starts at the form
        self.sync()

    def cancel_edit(self) -> None:
        self._editing_id = None
        if self._task is not None:
            self._task.invalidate_preview("Edit cancelled")
        self._set_edit_banner()
        self._say("Edit cancelled")
        self.sync()

    def _set_edit_banner(self) -> None:
        editing = self._editing_id is not None
        if not editing:
            self._edit_footprint = self._edit_face = self._edit_box = self._edit_record = None
        project = self._project_now()
        definition = None if project is None or not editing else project.submodels.get(self._editing_id)
        self.edit_banner.setVisible(editing)
        if editing:
            name = getattr(definition, "name", self._editing_id)
            self.edit_banner.setText(f"Replacing solid {name!r}: it keeps its id, source job and earlier runs.")
        for widget in (self.mode, self.source):
            widget.setEnabled(not editing)

    def delete_selected(self) -> None:
        definition = self.selected_definition()
        if definition is None:
            raise ValueError("Select a solid submodel to delete")
        dialogs = getattr(self.app, "dialogs", None)
        if dialogs is None or not dialogs.confirm(
                "Delete solid submodel", f"Delete {definition.name!r}? Its stored runs stay in the project; "
                                         "Undo restores the definition."):
            self._say("Delete cancelled")
            return
        was_editing = self._editing_id == definition.id
        self.task.remove(definition.id)
        if was_editing:
            self._editing_id = None
            self._set_edit_banner()
        self.reload_lists()
        self.sync()

    def run_selected(self) -> None:
        definition_id = self.selected_id()
        if not definition_id:
            raise ValueError("Create a submodel first")
        self.task.start_run(definition_id)

    def cancel_run(self) -> None:
        self.task.cancel()

    def restore_view(self) -> None:
        self.task.restore_view()
        if self._parent_camera is not None:
            from ...presentation.workspace import apply_camera
            apply_camera(self._canvas(), self._parent_camera)
            self._parent_camera = None

    def show_selected_body(self) -> None:
        definition = self.selected_definition()
        if definition is None or getattr(definition, "child_kind", "shell") != "solid":
            raise ValueError("Select a solid submodel")
        body = definition_body(definition)
        if self._parent_camera is None:
            from ...presentation.workspace import camera_record
            self._parent_camera = camera_record(self._canvas())
        self.app.viewport.show(body_scene(body["footprint"], body["thickness"], body["director"]))
        self.app.viewport.fit()

    # -- saved results -----------------------------------------------------
    def refresh_history(self, quiet: bool = False) -> None:
        if self._disposed or not self._ready():
            return
        self.task.refresh_history()

    def selected_run(self) -> str:
        rows = self.runs.selectionModel().selectedRows()
        if not rows:
            raise ValueError("Select a run")
        return self.runs.item(rows[0].row(), 0).data(Qt.UserRole)

    def show_child_mesh(self) -> None:
        self.task.show_child_mesh(self.selected_run())

    def _select_run(self) -> None:
        try:
            run_id = self.selected_run()
        except ValueError:
            return
        if self.view_side.currentData() == "parent":
            try:
                job = self.task.open_parent_result(run_id)
                self.details.setPlainText(f"Parent result of job {job} opened in the Results workspace.")
            except Exception as error:  # noqa: BLE001
                self.details.setPlainText(f"Parent result unavailable: {describe_error(error)}")
            return
        self.task.inspect(run_id)

    def _field_chosen(self) -> None:
        self.task.inspect(self.selected_run(), self.field.currentText())

    # -- sync -------------------------------------------------------------
    def sync(self) -> None:
        if self._disposed:
            return
        task = self._task
        busy = bool(task is not None and task.busy)
        read_only = self.read_only()
        editing = self._editing_id is not None
        ready = task is not None and task.preview is not None
        has_job = self.source_usable()
        child = self.selected_definition()
        solid_child = child is not None and getattr(child, "child_kind", "shell") == "solid"
        self._show_source_note()
        self.preview_button.setEnabled(not busy and has_job and self._ready())
        self.preview_button.setToolTip("Cut on a snapshot; nothing is stored" if has_job else self.source_problem())
        self.create_button.setEnabled(ready and not busy and not read_only and not editing)
        self.create_button.setVisible(not editing)
        self.replace_button.setVisible(editing)
        self.replace_button.setEnabled(ready and not busy and not read_only and editing and self.kind() == "solid")
        self.cancel_edit_button.setVisible(editing)
        self.edit_button.setEnabled(child is not None and not busy and not read_only)
        self.delete_button.setEnabled(solid_child and not busy and not read_only)
        self.run_button.setEnabled(child is not None and not busy and not read_only)
        self.cancel_button.setEnabled(bool(task is not None and task.state in (task.RUNNING, task.PREVIEWING)))
        self.use_selection_button.setEnabled(self.kind() == "solid")
        self.status.setText(self._local_message or (task.message if task is not None else "")
                            or ("" if has_job or not self._ready() else self.source_problem()))
        if task is not None:
            self.run_state.setText(f"State: {task.state}" + (f" · last run {task.last_run_id}" if task.last_run_id else "")
                                   + (" · read-only document" if read_only else ""))
            if task.preview is not None:
                text = task.preview_text()
                if text != self._report_text:
                    self._report_text = text
                    self.report.setPlainText(text)
            if self.kind() == "solid" and self._live and task.preview is not None:
                self.refresh_design_body()
            if task.history is not self._seen_history:
                self._seen_history = task.history
                self._fill_history()
            if task.inspection is not self._seen_inspection:
                self._seen_inspection = task.inspection
                self._fill_inspection()

    def _fill_history(self) -> None:
        task = self.task
        try:
            keep = self.selected_run()
        except ValueError:
            keep = None
        self.runs.blockSignals(True)
        try:
            self.runs.setRowCount(len(task.history))
            for row, entry in enumerate(task.history):
                run = entry["run"]
                definition = self.app.project.submodels.get(run.definition_id)
                reasons = entry["reasons"]
                values = [run.id[:8], definition.name if definition else "(definition deleted)",
                          "current" if not reasons else "stale: " + ", ".join(reasons)]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    if column == 0:
                        item.setData(Qt.UserRole, run.id)
                    self.runs.setItem(row, column, item)
                if run.id == keep:
                    self.runs.selectRow(row)
        finally:
            self.runs.blockSignals(False)
        self.runs.resizeColumnsToContents()

    def _fill_inspection(self) -> None:
        import json

        info = self.task.inspection
        if info is None or self.view_side.currentData() != "child":
            return
        lines = [f"Run {info['run_id']}", f"Identity: {info.get('identity') or 'unavailable'}",
                 f"Child mesh: {info.get('mesh') or 'unavailable'}"]
        lines += [f"  {key}: {value}" for key, value in (info.get("record") or {}).items()]
        lines += [f"PROBLEM: {p}" for p in info.get("problems", ())]
        lines.append("Diagnostics:\n" + json.dumps(info.get("diagnostics", {}), indent=2, default=str))
        lines.append("Fields:")
        lines += [f"  {f['key']} [{f['location']}, {f['unit']}] shape {f['shape']} min {f['min']} max {f['max']}"
                  for f in info.get("fields", ())]
        self.details.setPlainText("\n".join(lines))
        keys = [f["key"] for f in info.get("fields", ())]
        if keys != [self.field.itemText(i) for i in range(self.field.count())]:
            self.field.blockSignals(True)
            self.field.clear()
            self.field.addItems(keys)
            self.field.blockSignals(False)
        table = info.get("table")
        if table is not None:
            if table["key"] in keys:
                self.field.setCurrentText(table["key"])
            self.table.show_rows(table["headers"], table["rows"])
            if table["truncated"]:
                self.details.appendPlainText(f"Table shows the first {ROW_LIMIT} of {table['total']} rows.")
        else:
            self.table.show_rows([], [])
