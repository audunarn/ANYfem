"""Actual Qt route: Load cases / Loads & BC 'Visualize on mesh' before any result exists."""
import os
import pytest

if os.environ.get('ANYFEM_RUN_QT_TESTS') != '1':
    pytest.skip('Qt opt-in', allow_module_level=True)
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtCore import QItemSelectionModel
from test_qt_geometry_actions import qapp, window  # noqa: F401
from test_mesh_pressure_preview import series, colours
from anyfem.document import canonical_hash
from anyfem.io.project_file import project_to_dict
from anyfem.presentation import mesh_pressure_preview as preview
from anyfem.ui.qt.record_table import RecordTableDialog


def prepared(window, qapp, monkeypatch, regular=True):
    p, old, new, history = series(regular)
    window._set_project(p)
    window.mesh = old
    assert window.solution is None
    panel = window.panels['Loads & BC']
    panel.refresh()
    panel.case.setCurrentText('wave')
    window.notebook.select('Loads & BC')
    qapp.processEvents()
    shown = []
    original = window.viewport.show
    monkeypatch.setattr(window.viewport, 'show', lambda scene, *a, **k: (shown.append(scene), original(scene, *a, **k))[1])
    return p, old, new, panel, shown


def test_loads_panel_button_selects_step_and_shows_signed_mesh_without_results(window, qapp, monkeypatch):
    p, old, _new, panel, shown = prepared(window, qapp, monkeypatch)
    before = canonical_hash(project_to_dict(p))
    assert window.solution is None and 'step 1 of 101' in panel.preview_label.text()
    panel.preview_step.setValue(4)
    assert 'step 4 of 101' in panel.preview_label.text() and 't = ' in panel.preview_label.text()
    QTest.mouseClick(panel.preview_button, Qt.LeftButton)
    qapp.processEvents()
    assert window._view_mode == 'mesh-pressure-preview' and window.solution is None
    expected = preview.build_mesh_pressure_scene(p, old, 'wave', 4)[2]['values']
    assert window._pressure_preview_info['values'] == expected
    assert shown[-1].legend['kind'] == preview.LEGEND_KIND and 'step 4/101' in shown[-1].legend['title']
    assert set(colours(shown[-1])) == set(old.quads)
    assert canonical_hash(project_to_dict(p)) == before
    panel.preview_step.setValue(6)
    QTest.mouseClick(panel.preview_button, Qt.LeftButton)
    assert 'step 6/101' in shown[-1].legend['title']


def test_load_cases_table_button_uses_selected_row_and_series_step(window, qapp, monkeypatch):
    p, old, _new, panel, shown = prepared(window, qapp, monkeypatch, regular=False)
    panel.preview_step.setValue(3)  # a different step in Loads & BC must not leak into the table action
    table = RecordTableDialog(window, 'Load cases')
    names = [row.key for row in table.model.rows]
    row = next(i for i, r in enumerate(table.model.rows) if r.record.name == 'wave')
    table.view.selectionModel().select(table.proxy.mapFromSource(table.model.index(row, 0)),
                                       QItemSelectionModel.Select | QItemSelectionModel.Rows)
    qapp.processEvents()
    assert table.visualize_step.isEnabled() and 'step 1 of 101' in table.visualize_label.text()
    table.visualize_step.setValue(5)
    time5 = p.series_children('wave')[4].time
    assert 'step 5 of 101' in table.visualize_label.text() and f't = {time5:g} s' in table.visualize_label.text()
    QTest.mouseClick(table.visualize_button, Qt.LeftButton)
    qapp.processEvents()
    assert names and 'step 5/101' in shown[-1].legend['title'] and f't = {time5:g} s' in shown[-1].legend['title']
    assert 'plain mesh colour: unloaded or zero' in shown[-1].legend['title']  # rendered text carries the absent-colour meaning
    assert window._pressure_preview_info['values'] == preview.build_mesh_pressure_scene(p, old, 'wave', 5)[2]['values']
    assert window.solution is None and not window.project.jobs
    # A single step row selects itself, not the step number chosen in Loads & BC.
    step = p.series_children('wave')[1].name
    step_row = next(i for i, r in enumerate(table.model.rows) if r.record.name == step)
    table.view.selectionModel().clearSelection()
    table.view.selectionModel().select(table.proxy.mapFromSource(table.model.index(step_row, 0)),
                                       QItemSelectionModel.Select | QItemSelectionModel.Rows)
    qapp.processEvents()
    assert not table.visualize_step.isEnabled() and 'step 2 of 101' in table.visualize_label.text()
    QTest.mouseClick(table.visualize_button, Qt.LeftButton)
    assert 'step 2/101' in shown[-1].legend['title']  # derived row ignores both step selectors


def test_real_load_edit_closes_old_preview_and_new_preview_uses_edited_scale(window, qapp, monkeypatch):
    p, old, _new, panel, shown = prepared(window, qapp, monkeypatch)
    panel.preview_step.setValue(4)
    QTest.mouseClick(panel.preview_button, Qt.LeftButton)
    qapp.processEvents()
    before = dict(window._pressure_preview_info['values'])
    assert window._view_mode == 'mesh-pressure-preview' and before
    record = p.load_case('wave').wet_pressures[0]
    assert record.scale == 2.
    panel.edit_existing(record)
    qapp.processEvents()
    panel.record_fields['replacement'][1]['scale'][0].setText('3')
    panel.execute()
    qapp.processEvents()
    assert p.load_case('wave').wet_pressures[0].scale == 3.
    # The old colours must not stay on screen after the load edit.
    assert window._view_mode != 'mesh-pressure-preview' and window.solution is None
    window.mesh = old
    QTest.mouseClick(panel.preview_button, Qt.LeftButton)
    qapp.processEvents()
    after = window._pressure_preview_info['values']
    assert set(after) == set(before)
    for element, value in before.items():
        assert after[element] == pytest.approx(value * 1.5, rel=1e-9, abs=1e-9)
    assert not window.project.jobs and window.solution is None


def test_series_time_grid_change_refreshes_both_selectors_before_click(window, qapp, monkeypatch):
    from anyfem import commands
    p, old, _new, panel, shown = prepared(window, qapp, monkeypatch)
    assert panel.preview_label.text()  # initial refresh must not leave the label empty
    times = tuple(p.load_cases['wave'].times)
    window.run(commands.SetLoadSeriesTimes('wave', times=times[:3]))
    qapp.processEvents()
    table = RecordTableDialog(window, 'Load cases')
    row = next(i for i, r in enumerate(table.model.rows) if r.record.name == 'wave')
    table.view.selectionModel().select(table.proxy.mapFromSource(table.model.index(row, 0)),
                                       QItemSelectionModel.Select | QItemSelectionModel.Rows)
    qapp.processEvents()
    panel.preview_step.setValue(2)
    table.visualize_step.setValue(2)
    assert panel.preview_step.maximum() == 3 and table.visualize_step.maximum() == 3
    window.run(commands.SetLoadSeriesTimes('wave', times=times[:5]))
    qapp.processEvents()
    qapp.processEvents()  # table refresh is scheduled on the event loop
    for step, label in ((panel.preview_step, panel.preview_label), (table.visualize_step, table.visualize_label)):
        assert step.maximum() == 5 and step.value() == 2  # new range, chosen step preserved
        assert 'step 2 of 5' in label.text() and f't = {times[1]:g} s' in label.text()
    # Select the new last step BEFORE any click, then preview its actual time.
    panel.preview_step.setValue(5)
    assert 'step 5 of 5' in panel.preview_label.text() and f't = {times[4]:g} s' in panel.preview_label.text()
    window.mesh = old
    QTest.mouseClick(panel.preview_button, Qt.LeftButton)
    qapp.processEvents()
    assert 'step 5/5' in shown[-1].legend['title'] and f't = {times[4]:g} s' in shown[-1].legend['title']
    assert window.solution is None and not window.project.jobs


def test_missing_mesh_refuses_visibly_and_mesh_change_refreshes_or_closes(window, qapp, monkeypatch):
    p, old, new, panel, shown = prepared(window, qapp, monkeypatch)
    QTest.mouseClick(panel.preview_button, Qt.LeftButton)
    assert window._view_mode == 'mesh-pressure-preview'
    window.mesh = new
    window.refresh_views()
    assert window._view_mode == 'mesh-pressure-preview' and set(colours(shown[-1])) == set(new.quads)
    window.mesh = None
    window.refresh_views()  # refuses clearly and leaves the preview, never stale colours
    assert window._view_mode != 'mesh-pressure-preview'
    QTest.mouseClick(panel.preview_button, Qt.LeftButton)
    assert 'No structural mesh' in panel.preview_label.text()
