"""Selection-driven, read-only geometry measurement workspace."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QLabel, QPushButton

from ... import commands


TOOLS = (
    ("Auto", "auto", "Select one point, line or plate, or two geometry entities for distance."),
    ("Distance", "distance", "Select exactly two geometry entities. Point-to-point distance is exact; other distances are sampled minimum estimates."),
    ("Angle", "angle", "Select exactly two lines. Reports the unsigned angle between midpoint tangents (0–90°, independent of line direction)."),
    ("Coordinates", "coordinates", "Select one geometry point. Reports signed world X, Y and Z."),
    ("Length", "length", "Select one line."),
    ("Area", "area", "Select one plate. Curved areas are estimated from sampled geometry."),
    ("Perimeter", "perimeter", "Select one plate. Includes outer and hole boundaries."),
    ("Radius", "radius", "Select one circular arc. Other curves do not support radius measurement."),
    ("Centroid", "centroid", "Select one geometry entity. Curved line and plate centroids are numerical estimates."),
    ("Normal", "normal", "Select one plate. Reports its midpoint unit normal in world XYZ."),
)


def format_measurement(result, units):
    """Format SI owner quantities at the project display boundary only."""
    label = "Coordinates" if result.kind in ("position", "coordinates") else result.kind.capitalize()
    if result.unit == "m^2":
        return f"{label}: {float(result.value) / units.factor('length') ** 2:.8g} {units.symbol('length')}²"
    if result.unit == "rad":
        return f"{label}: {units.format(result.value, 'angle', precision=8)}"
    if isinstance(result.value, (tuple, list)):
        values = result.value
        if result.unit == "m":
            values = units.to_display(values, "length")
        components = ", ".join(f"{axis} = {float(value):.8g}" for axis, value in zip("XYZ", values))
        suffix = f" {units.symbol('length')}" if result.unit == "m" else " (dimensionless)"
        return f"{label}: {components}{suffix}"
    if result.unit == "m":
        return f"{label}: {units.format(result.value, 'length', precision=8)}"
    raise ValueError(f"Unsupported measurement unit: {result.unit}")


class MeasureTask(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self._source = None
        self.measurement = None
        layout = QVBoxLayout(self)
        self.choice = QComboBox(self)
        self.choice.setAccessibleName("Measurement tool")
        for label, key, _ in TOOLS:
            self.choice.addItem(label, key)
        layout.addWidget(self.choice)
        self.guidance = QLabel(); self.guidance.setWordWrap(True)
        layout.addWidget(self.guidance)
        self.selected = QLabel(); self.selected.setWordWrap(True)
        layout.addWidget(self.selected)
        self.apply = QPushButton("Measure selection")
        self.apply.clicked.connect(self.measure)
        layout.addWidget(self.apply)
        self.result = QLabel("Select geometry, then Measure selection.")
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result.setAccessibleName("Measurement result")
        layout.addWidget(self.result)
        self.note = QLabel("Curved areas and centroids, and distances involving lines or plates, are estimates. Measuring does not change the model.")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        layout.addStretch()
        self.choice.currentIndexChanged.connect(self.refresh)
        app.selection.add_listener(self.refresh)
        self.refresh()

    def activate(self):
        # Enter geometry picking without the modelling workflow's downstream
        # invalidation. Mixed geometry refs restored by the tree stay intact.
        from ...selection import SelectionFilter
        self.app.selection.set_filter(SelectionFilter(kinds=frozenset({'vertex', 'edge', 'face'})))
        self.app.show_geometry()
        self.refresh()

    def signature(self):
        return (self.app.session, self.app.session.revision.id,
                self.app.project.geometry.revision,
                tuple(sorted(self.app.project.units.units.items())),
                str(self.app.selection.domain), tuple(self.app.selection.ordered_items),
                self.choice.currentData())

    def refresh(self, *_):
        refs = tuple(self.app.selection.ordered_items)
        self.guidance.setText(TOOLS[self.choice.currentIndex()][2] + " Use the model tree or the geometry selection controls; Shift-click adds a second entity.")
        geometry_domain = getattr(self.app.selection.domain, "value", "geometry") == "geometry"
        names = ", ".join(f"{ref.kind.capitalize()} {ref.id}" for ref in refs if hasattr(ref, "kind"))
        self.selected.setText("Selected: " + (names if geometry_domain and names else "no geometry entities"))
        self.apply.setEnabled(not self.app._closing)
        if self._source is not None and self._source != self.signature():
            self.measurement = None
            self._source = None
            self.result.setText("Measurement cleared because its source, selection, tool or units changed. Measure again.")

    def measure(self):
        self.measurement = None
        self._source = None
        try:
            if getattr(self.app.selection.domain, "value", "geometry") != "geometry":
                raise ValueError("Select geometry entities; mesh nodes and elements cannot be measured here.")
            refs = tuple(self.app.selection.ordered_items)
            if not refs or len(refs) > 2 or any(getattr(ref, "kind", None) not in ("vertex", "edge", "face") for ref in refs):
                raise ValueError("Select one or two geometry entities as instructed above.")
            quantity = self.choice.currentData()
            expected = 2 if quantity in ("distance", "angle") else None if quantity == "auto" else 1
            if expected is not None and len(refs) != expected:
                raise ValueError(f"{self.choice.currentText()} needs exactly {expected} selected geometry {'entity' if expected == 1 else 'entities'}.")
            result = self.app.commands.query(commands.MeasureGeometry(refs, quantity))
            text = format_measurement(result, self.app.project.units)
            if result.kind == "distance" and any(ref.kind != "vertex" for ref in refs):
                text += " (sampled minimum estimate)"
            if result.kind == "angle":
                text += " (unsigned midpoint-tangent angle)"
            self.measurement = result
            self._source = self.signature()
            self.result.setText(text)
        except (ValueError, KeyError, TypeError) as error:
            self.result.setText(f"Cannot measure: {error}")
