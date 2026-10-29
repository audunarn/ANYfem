"""Recovered workbench controls exercise real widgets and production callbacks."""
import os
import pytest

if os.environ.get('ANYFEM_RUN_QT_TESTS') != '1':
    pytest.skip('set ANYFEM_RUN_QT_TESTS=1', allow_module_level=True)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from anyfem.io.project_file import project_to_dict
from anyfem.ui.qt.widgets import Collapsible
from test_qt_cad_visualization import qapp, window


def button(panel, text):
    return next(item for item in panel.findChildren(QPushButton) if item.text() == text)


def test_grouped_visualization_and_separate_export_share_controller(window, qapp):
    visual = window.panels['Visualization']
    export = window.panels['Export']
    controls = visual.presentation_controls
    assert export.presentation_controls is controls
    assert export.isAncestorOf(controls.export_controls)
    assert not visual.isAncestorOf(controls.export_controls)
    sections = {section.toggle.text(): section for section in visual.findChildren(Collapsible)}
    assert {'Model visibility', 'Colours and edges', 'Lighting and quality',
            'Results and legend', 'Contours and thresholds', 'Compare results'} <= sections.keys()
    assert all(not section.is_expanded() for section in sections.values())
    window.notebook.select('Export'); qapp.processEvents()
    assert controls.capture_size.isVisible() and controls.animation_format.isVisible()
    assert button(export, 'Capture pane(s) PNG').isVisible()
    assert button(export, 'Export selected frame interval').isVisible()
    assert button(export, 'Cancel animation export').isVisible()


def test_split_toolbar_and_comparison_toggle_stay_synchronized(window, qapp):
    controls = window.panels['Visualization'].presentation_controls
    before = window.session.revision.model_hash
    window.split_screen_action.trigger(); qapp.processEvents()
    assert controls.compare.isChecked() and window.split_screen_action.isChecked()
    assert controls.reference.widget.isVisible()
    assert controls.reference.selection is not window.selection
    assert window.viewport.widget.width() >= 160 and controls.reference.widget.width() >= 160
    assert not controls.splitter.childrenCollapsible()
    controls.compare.setChecked(False); qapp.processEvents()
    assert not window.split_screen_action.isChecked() and controls.reference.widget.isHidden()
    window.split_screen_action.trigger(); qapp.processEvents()
    window.split_screen_action.trigger(); qapp.processEvents()
    assert not controls.compare.isChecked() and controls._reference_access is None
    assert window.session.revision.model_hash == before


def test_reset_toolbar_clears_display_state_and_preserves_engineering_data(window, qapp):
    visual = window.panels['Visualization']; controls = visual.presentation_controls
    face = next(iter(window.project.geometry.faces))
    ref = window.project.geometry.entity_ref('face', face)
    before = project_to_dict(window.project); before.pop('presentation', None)
    before_hash = window.session.revision.model_hash
    visual.render.setCurrentText('Wireframe'); visual.transparency.setValue(65)
    controls.compare.setChecked(True)
    controls.box_enabled.setChecked(True)
    window.selection.set_mode('face'); window.selection.select(ref); window.set_selection_hidden(True)
    controls.save_workspace()
    window.reset_view_action.trigger(); qapp.processEvents()
    after = project_to_dict(window.project); after.pop('presentation', None)
    # Presentation metadata raises the saved-file format, without changing the model.
    assert after.pop('anyfem')['document_id'] == before.pop('anyfem')['document_id']
    assert after == before and window.session.revision.model_hash == before_hash
    assert not controls.compare.isChecked() and not window.split_screen_action.isChecked()
    assert not controls.box_enabled.isChecked()
    assert window.viewport.canvas.section_box is None
    assert not window.selection.items and not window.viewport.visibility.hidden
    assert window.viewport.visualization.surface_opacity == 1
    assert window.viewport.visualization.render_mode == 'Shaded with edges'
    assert not window.project.presentation['workspace']['comparison']['enabled']
    assert window.project.presentation['workspace']['appearance']['surface_opacity'] == 1


def test_export_workspace_png_button_writes_native_image(window, qapp, tmp_path, monkeypatch):
    from PIL import Image
    path = tmp_path / 'viewport.png'
    controls = window.panels['Visualization'].presentation_controls
    window.notebook.select('Export'); qapp.processEvents()
    monkeypatch.setattr(window.dialogs, 'save_file', lambda **kwargs: str(path))
    QTest.mouseClick(button(window.panels['Export'], 'Capture pane(s) PNG'), Qt.LeftButton)
    with Image.open(path) as result:
        assert result.width > 0 and result.height > 0
        assert f'{result.width} × {result.height}' in controls.capture_status.text()
    assert not list(tmp_path.glob('.viewport.png*'))


def test_cancelled_image_dialog_and_capture_failure_preserve_previous_output(window, tmp_path, monkeypatch):
    controls = window.panels['Visualization'].presentation_controls
    monkeypatch.setattr(window.dialogs, 'save_file', lambda **kwargs: '')
    monkeypatch.setattr(controls, 'capture_composite', lambda: pytest.fail('cancelled capture started'))
    controls.capture()
    path = tmp_path / 'existing.png'; path.write_bytes(b'previous image')
    monkeypatch.setattr(window.dialogs, 'save_file', lambda **kwargs: str(path))
    def refuse():
        raise ValueError('Renderer did not return a native image')
    monkeypatch.setattr(controls, 'capture_composite', refuse)
    with pytest.raises(ValueError, match='native image'):
        controls.capture()
    assert path.read_bytes() == b'previous image'
    assert not list(tmp_path.glob('.existing.png*'))


def test_simple_preset_applies_live_without_engineering_changes(window):
    visual = window.panels['Visualization']; controls = visual.presentation_controls
    before = window.session.revision.model_hash
    controls.shadows.setCurrentText('High')
    visual.preset.setCurrentText('Simple'); visual.apply_preset()
    style = window.viewport.visualization
    assert (style.antialiasing, style.lighting, style.ambient_occlusion) == ('Off', 'Flat', 'Off')
    assert not style.background_gradient and not style.feature_edges
    assert controls.shadows.currentText() == 'Off'
    assert window.session.revision.model_hash == before
