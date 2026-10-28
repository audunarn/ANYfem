"""Qt option behaviour for the wave-following mode (opt-in Qt tests, no BEM)."""
import os
from pathlib import Path
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS') != '1': pytest.skip('Qt opt-in', allow_module_level=True)
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_qt_geometry_actions import qapp, window
from test_qt_wave_load import SOURCE, opened, wait


def dialog_for(window, qapp, tmp_path):
    window.wave_solution_cache_directory = tmp_path / 'force-solutions'
    panel = opened(window, qapp)
    QTest.mouseClick(panel.wave_button, Qt.LeftButton); qapp.processEvents()
    return panel._wave_dialog


def test_follow_checkbox_defaults_off_and_locks_then_restores_hydrostatic(window, qapp, tmp_path):
    dialog = dialog_for(window, qapp, tmp_path)
    assert dialog.follow.isChecked() is False and dialog.settings().follow_surface is False
    dialog.hydrostatic.setChecked(False)
    dialog.follow.setChecked(True)
    assert dialog.hydrostatic.isChecked() and not dialog.hydrostatic.isEnabled()      # implied and locked
    config = dialog.settings()
    assert config.follow_surface is True and config.include_hydrostatic is True
    assert 'wet/dry' in dialog.plot.composition_label and dialog.plot.source_label == 'Applied hull resultant'
    dialog.follow.setChecked(False)
    assert dialog.hydrostatic.isEnabled() and not dialog.hydrostatic.isChecked()      # previous choice restored
    assert dialog.plot.source_label == 'Original hydro source'


def test_hydrostatic_stays_checked_and_disabled_after_failure_update_and_reset(window, qapp, tmp_path):
    dialog = dialog_for(window, qapp, tmp_path)
    dialog.follow.setChecked(True)
    dialog.failure('simulated failure'); dialog.update_mode()
    assert dialog.hydrostatic.isChecked() and not dialog.hydrostatic.isEnabled()
    assert dialog.record_info.wordWrap()                                  # long caption wraps, no huge minimum width
    dialog.follow.setChecked(False)
    assert dialog.hydrostatic.isEnabled()


def test_toggling_follow_invalidates_a_prepared_preview_and_reports_missing_solutions(window, qapp, tmp_path):
    dialog = dialog_for(window, qapp, tmp_path)
    dialog.prepared = {'preview_times': [0., 1.]}
    dialog.apply_button.setEnabled(True)
    dialog.follow.setChecked(True)
    assert dialog.prepared is None and not dialog.apply_button.isEnabled()
    text = dialog.surface_requirements_text()
    assert 'field-capable' in text and ('missing' in text or 'available' in text)


def test_saved_composed_load_is_shown_read_only_and_not_lost_in_the_editor(qapp, tmp_path, monkeypatch):
    from test_wave_surface_application import barge, prepared_for, analytic_potential
    from anyfem.ui.qt.editors import PressureHistoryEdit
    from anyfem.application.hydro_wave_input import select_prepared_wave_window
    from anyloads.pressure_history import pressure_history_from_dict
    from anyloads.wave_surface_pressure import WaveSurfacePressureHistory
    monkeypatch.setattr('anyhydro.wave_surface_fields._capytaine_total_potential', analytic_potential)
    p, mesh, _ = barge()
    history = pressure_history_from_dict(select_prepared_wave_window(prepared_for(p, mesh, tmp_path))['history'])
    edit = PressureHistoryEdit()
    edit.set_history(history.to_dict())
    assert isinstance(edit.history, WaveSurfacePressureHistory) and edit.isReadOnly()
    assert 'Wave-following' in edit.text() and history.content_sha256[:16] in edit.text()
    with pytest.raises(ValueError):
        edit.setText('{}')
    assert edit.history is not None and edit.history.content_sha256 == history.content_sha256


@pytest.mark.skipif(os.environ.get('ANYFEM_NATIVE_WAVE_FIELD') != '1', reason='native field capture is scheduled by the parent')
def test_native_dialog_follow_prepare_select_apply_save_reopen(window, qapp, tmp_path):
    from anyfem.io.project_file import load_project
    from anyloads.wave_surface_pressure import WaveSurfacePressureHistory
    dialog = dialog_for(window, qapp, tmp_path)
    dialog.follow.setChecked(True)
    assert 'missing' in dialog.surface_requirements_text() or 'available' in dialog.surface_requirements_text()
    QTest.mouseClick(dialog.prepare_button, Qt.LeftButton)
    wait(qapp, lambda: dialog.runner is not None and not dialog.runner.isRunning(), seconds=150)
    assert dialog.prepared is not None and dialog.prepared['preview_kind'] == 'wave_surface_v1', dialog.status.text()
    times = dialog.prepared['preview_times']
    dialog.set_window(times[1], times[-1])                                   # selected window, first sample excluded
    QTest.mouseClick(dialog.apply_button, Qt.LeftButton); qapp.processEvents()
    owners = [c for c in window.project.load_cases.values() if c.parent is None]
    assert len(owners) == 1 and len(owners[0].wet_pressures) == 1, dialog.status.text()
    history = owners[0].wet_pressures[0].history
    assert isinstance(history, WaveSurfacePressureHistory) and history.times[0] == times[1]
    path = tmp_path / 'follow.anyfem'; window.save_project(path=str(path))
    again = load_project(path).load_cases[owners[0].name].wet_pressures[0].history
    assert again.content_sha256 == history.content_sha256
    window.undo(); assert not [c for c in window.project.load_cases.values() if c.parent is None]
    window.redo()


def test_graph_navigation_widgets_are_still_present(window, qapp, tmp_path):
    dialog = dialog_for(window, qapp, tmp_path)
    for name in ('plot', 'window_start', 'window_end', 'full_window'):
        assert hasattr(dialog, name)


def test_follow_controls_fit_scroll_viewport_and_plot_origin_is_explicit(window, qapp, tmp_path):
    from PySide6.QtWidgets import QScrollArea
    dialog = dialog_for(window, qapp, tmp_path)
    dialog.resize(1180, 680); dialog.show(); qapp.processEvents()
    scroll = dialog.findChild(QScrollArea)
    scroll.ensureWidgetVisible(dialog.follow); qapp.processEvents()
    assert scroll.widget().width() <= scroll.viewport().width()
    assert scroll.horizontalScrollBar().maximum() == 0
    dialog.follow.setChecked(True)
    assert '(0, 0, 1.5)' in dialog.plot.moment_origin_caption()
    dialog.follow.setChecked(False)
    assert 'WORLD0' in dialog.plot.moment_origin_caption()
