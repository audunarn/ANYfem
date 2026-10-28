"""Floating linear-response mode in the wave dialog: exclusivity with Follow surface, restoring the fixed-body choices, strict kwargs.

Opt-in Qt node (same gate and fixture as test_qt_wave_load).  No worker is started here; the Prepare/Apply worker path is covered
headlessly by tests/test_floating_pipeline.py and the gated production node.
"""
import os
from pathlib import Path

import pytest

if os.environ.get('ANYFEM_RUN_QT_TESTS') != '1':
    pytest.skip('Qt opt-in', allow_module_level=True)
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_qt_geometry_actions import qapp, window
from test_qt_wave_load import opened, wait


def test_floating_mode_locks_follow_restores_fixed_choices_and_builds_strict_worker_kwargs(window, qapp, tmp_path):
    panel = opened(window, qapp)
    QTest.mouseClick(panel.wave_button, Qt.LeftButton); qapp.processEvents()
    d = panel._wave_dialog
    d.hydrostatic.setChecked(False)
    d.body_mode.setCurrentIndex(d.body_mode.findData('floating')); qapp.processEvents()
    assert d.floating_selected() and not d.follow.isChecked() and not d.follow.isEnabled()
    assert d.hydrostatic.isChecked() and not d.hydrostatic.isEnabled()          # floating owns static + gravity + inertia
    d.floating_fields['equilibrium_python'].setText('python-runtime')
    d.floating_fields['owner_wheel'].setText('owner.whl')
    d.floating_fields['envelope_extra_face_ids'].setText('3, 4')
    d.floating_dev.setChecked(True)
    window.path = str(tmp_path / 'p.anyfem')
    kwargs = d.floating_kwargs(d.settings())
    floating = kwargs['settings']['floating']
    assert floating['envelope_extra_face_ids'] == (3, 4) and floating['allow_development'] is True
    assert floating['allow_native_solve'] is False and kwargs['settings']['wave']['follow_surface'] is False
    d.body_mode.setCurrentIndex(d.body_mode.findData('fixed')); qapp.processEvents()
    assert not d.floating_selected() and d.follow.isEnabled() and not d.hydrostatic.isChecked()   # user's choice restored


def test_relative_project_path_yields_an_absolute_record_path_and_an_enterable_publication_workspace(window, qapp, tmp_path, monkeypatch):
    from anyfem.application.floating_job import owned_publication_workspace
    panel = opened(window, qapp)
    QTest.mouseClick(panel.wave_button, Qt.LeftButton); qapp.processEvents()
    d = panel._wave_dialog
    d.body_mode.setCurrentIndex(d.body_mode.findData('floating')); qapp.processEvents()
    (tmp_path / 'rel').mkdir()
    monkeypatch.chdir(tmp_path)
    window.path = os.path.join('rel', 'p.anyfem')                       # a project opened from a RELATIVE path
    kwargs = d.floating_kwargs(d.settings())
    record = Path(kwargs['record_path'])
    assert record.is_absolute() and record.parent == (tmp_path / 'rel').resolve()
    with owned_publication_workspace(record) as workspace:             # previously refused: "must be absolute"
        assert workspace.is_dir() and workspace.parent == record.parent
    assert not workspace.exists()
