"""Parent integration check of the actual workbench button and command stack."""
from test_qt_free_floating import panel, qapp  # noqa: F401


def test_workbench_turbine_load_button_and_real_undo_redo(panel, qapp, tmp_path):
    window = panel.app
    loads = window.panels["Loads & BC"]
    loads.turbine_button.click()
    qapp.processEvents()
    editor = loads._turbine_dialog
    editor.fields["name"].setText("Workbench turbine")
    editor.fields["hub_node"].setText("1")
    editor.wind_mode.setCurrentIndex(editor.wind_mode.findData("kaimal"))
    editor.fields["dt"].setText("0.5")
    editor.fields["duration"].setText("20")
    record = editor.save()
    assert record is not None, editor.status.text()
    assert window.project.wind_turbine_load(record.id) == record
    window.undo()
    assert record.id not in window.project.wind_turbine_loads
    window.redo()
    assert window.project.wind_turbine_load(record.id) == record
    # Offscreen Qt does not discover Windows UI fonts automatically.
    # Register one for this capture only; product font settings are unchanged.
    from pathlib import Path
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QWidget
    font_path = Path("C:/Windows/Fonts/segoeui.ttf")
    if font_path.is_file():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        assert font_id >= 0
        family = QFontDatabase.applicationFontFamilies(font_id)[0]
        for widget in [editor, *editor.findChildren(QWidget)]:
            widget.setFont(QFont(family, 10))
    editor.ensurePolished()
    editor.layout().activate()
    editor.resize(780, editor.sizeHint().height())
    editor.grab().save(str(tmp_path / "wind-turbine-editor.png"))
    editor.close()
