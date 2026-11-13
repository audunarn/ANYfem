"""Plate-profile export from the active ANYfem document."""
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QSpinBox,
)

from ...application.plate_drawings import export_plate_drawings


def create_plate_drawings(window):
    parts = [part for part in window.project.geometry.parts.values() if part.sheet_ids]
    if not parts:
        raise ValueError("This model has no structural Part with plate Sheets.")
    dialog = QDialog(window)
    dialog.setWindowTitle("Create plate drawings")
    form = QFormLayout(dialog)
    choice = QComboBox(dialog)
    for part in sorted(parts, key=lambda item: item.id):
        choice.addItem(f"{part.name or 'Part'}  (ID {part.id})", part.id)
    quantity = QSpinBox(dialog)
    quantity.setRange(1, 1_000_000)
    override = QCheckBox("Override assigned material density for takeoff", dialog)
    density = QDoubleSpinBox(dialog)
    density.setRange(0.001, 1_000_000.0)
    density.setDecimals(3)
    density.setValue(7850.0)
    density.setSuffix(" kg/m³")
    density.setEnabled(False)
    override.toggled.connect(density.setEnabled)
    form.addRow("Structural part", choice)
    form.addRow("Number of assemblies", quantity)
    form.addRow(override)
    form.addRow("Takeoff density", density)
    datum = QComboBox(dialog)
    datum.addItem("Support lower edge · Z = 0", "support_base")
    datum.addItem("Model global Z", "global")
    form.addRow("Elevation datum", datum)
    intersections = QCheckBox("Show intersecting plates", dialog)
    intersections.setChecked(True)
    form.addRow(intersections)
    dwg = QCheckBox("Include DWG copies using ODA File Converter", dialog)
    form.addRow(dwg)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
    buttons.button(QDialogButtonBox.Ok).setText("Choose output…")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    if dialog.exec() != QDialog.Accepted:
        return
    converter = None
    if dwg.isChecked():
        converter, _ = QFileDialog.getOpenFileName(window, "Select the ODA File Converter executable")
        if not converter:
            return
    destination, _ = QFileDialog.getSaveFileName(
        window, "Name the new drawing folder", str(Path.home() / f"plate-drawings-{choice.currentData()}"),
        options=QFileDialog.DontConfirmOverwrite,
    )
    if not destination:
        return
    output = export_plate_drawings(window.session.snapshot(), choice.currentData(), destination,
                                   quantity=quantity.value(),
                                   density_kg_m3=density.value() if override.isChecked() else None,
                                   dwg_converter=converter, elevation_datum=datum.currentData(),
                                   include_plate_intersections=intersections.isChecked())
    window.set_status(f"Plate drawings and weight takeoff saved in {output}")
