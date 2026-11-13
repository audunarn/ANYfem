"""Pressure-source choice: ANYhydro family bank and OrcaWave stored result feed the same mapping gates."""
import json
from pathlib import Path
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys

import numpy as np
import pytest

from test_orcawave_pressure import fixture, job_fixture, raw_document
from anyfem.application import orcawave_pressure as app


def _family_cache(tmp_path, with_rao=False):
    """One-panel retained family whose hull pressure equals the ``fixture()`` bank (omega 1)."""
    from anyhydro import floating_radiation_fields as ff
    _, _, history = fixture()
    from anyhydro.orcawave_pressure import ImportedPressureBank
    bank = ImportedPressureBank.from_dict(json.loads(history.source_payload_json))
    request = ff.family_request(bank.vertices, bank.faces, ['p0'], heading=0., water_depth=20., rho=1025.,
                                gravity=9.81, Q0=np.eye(3), cg_world=(0., 0., -2.))
    rng = np.random.default_rng(1)
    ppr = rng.normal(size=(6, 1)) + 1j * rng.normal(size=(6, 1))
    snap = ff.FloatingFamilySnapshot(request, 1., bank.vertices, bank.faces, ('p0',), ppr, np.zeros(1) + 0j,
                                     np.eye(6), np.eye(6), np.ones(6) + 0j, {'backend': 'synthetic'},
                                     panel_pressure_radiation=ppr,
                                     panel_pressure_incident_diffraction=bank.responses[0],
                                     centers=bank.centers, normals=bank.normals, areas=bank.areas)
    cache = tmp_path / 'cache'
    ff.save_family_snapshot(cache, snap)
    (tmp_path / 'request.json').write_text(json.dumps({'request': request}))
    spec = dict(type=app.FAMILY_SPEC, cache='cache', request='request.json', omegas=[1.])
    if with_rao:
        np.savez(tmp_path / 'rao.npz', omega=np.array([1.]), rao=np.array([[1 + 1j, 0, 2, 0, 0.5j, 0]]))
        spec['rao_file'] = 'rao.npz'
    (tmp_path / 'spec.json').write_text(json.dumps(spec))
    return tmp_path / 'spec.json', bank, ppr, snap


def test_family_spec_bank_feeds_the_same_mapping_gates(tmp_path):
    spec, original, _, _ = _family_cache(tmp_path)
    data = app.inspect_orcawave_file(spec)
    from anyhydro.orcawave_pressure import ImportedPressureBank
    bank = ImportedPressureBank.from_dict(data)
    assert bank.composition == 'fixed_diffraction' and np.array_equal(bank.responses, original.responses)
    project, mesh, kw = job_fixture()
    kw['source'] = data
    result = app.prepare_orcawave_job(**kw)
    reference = app.prepare_orcawave_job(**job_fixture()[2])
    assert result['history']['responses_real'] == reference['history']['responses_real'] if 'responses_real' in result['history'] \
        else result['history']['responses'] == reference['history']['responses']


def test_family_spec_total_with_rao_and_refusals(tmp_path):
    spec, original, ppr, snap = _family_cache(tmp_path, with_rao=True)
    bank = app.bank_from_family_spec(spec)
    rao = np.array([1 + 1j, 0, 2, 0, 0.5j, 0])
    assert bank.composition == 'total_with_source_motion'
    assert np.allclose(bank.responses[0], original.responses[0] + rao @ ppr[:, 0], rtol=0, atol=1e-13)
    mismatch = json.loads(spec.read_text()); mismatch['omegas'] = [1.5]
    (tmp_path / 'm.json').write_text(json.dumps(mismatch))
    with pytest.raises(Exception):
        app.bank_from_family_spec(tmp_path / 'm.json')      # frequency not in the cache: never solved
    np.savez(tmp_path / 'rao.npz', omega=np.array([1.0000001]), rao=np.ones((1, 6)))
    with pytest.raises(ValueError, match='exactly'):
        app.bank_from_family_spec(spec)
    both = json.loads(spec.read_text()); both['omegas_file'] = 'rao.npz'
    (tmp_path / 'b.json').write_text(json.dumps(both))
    with pytest.raises(ValueError, match='exactly one'):
        app.bank_from_family_spec(tmp_path / 'b.json')


def test_owr_refused_without_orcfxapi_and_portable_json_still_reads(tmp_path, monkeypatch):
    loaded_before = 'OrcFxAPI' in sys.modules
    monkeypatch.setattr(app, 'orcfxapi_installed', lambda: False)
    owr = tmp_path / 'x.owr'; owr.write_bytes(b'not an owr')
    with pytest.raises(ValueError, match='OrcFxAPI.*licence'):
        app.inspect_orcawave_file(owr)
    project, mesh, kw = job_fixture()
    portable = tmp_path / 'p.json'
    from anyfileio.orcawave import OrcaWavePressureDocument
    OrcaWavePressureDocument.from_dict(kw['source']).save(portable)
    assert app.inspect_orcawave_file(portable) == kw['source']
    assert ('OrcFxAPI' in sys.modules) == loaded_before


def test_unreadable_owr_with_orcfxapi_present_explains_licence(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'orcfxapi_installed', lambda: True)
    owr = tmp_path / 'x.owr'; owr.write_bytes(b'not an owr')
    with pytest.raises(ValueError, match='(?s)could not be read.*licence'):
        app.inspect_orcawave_file(owr)


# ---------------------------------------------------------------- Qt
def _qt():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _wave_dialog():
    from types import SimpleNamespace
    from PySide6.QtWidgets import QWidget, QLineEdit
    from anyfem.ui.qt.wave_load import WaveLoadDialog
    project, mesh, _ = fixture()
    panel = QWidget(); panel.case = QLineEdit(); panel.case_name = lambda: ''
    panel.app = SimpleNamespace(project=project, mesh=mesh, run=lambda c: c.do(project), set_status=lambda s: None,
                                hydro=None)
    return panel, WaveLoadDialog(panel)


def test_selector_default_and_states(monkeypatch):
    _qt()
    loaded_before = 'OrcFxAPI' in sys.modules
    panel, dialog = _wave_dialog()
    combo = dialog.pressure_source
    assert [combo.itemData(i) for i in range(combo.count())] == ['anyhydro', 'orcawave']
    assert combo.currentData() == 'anyhydro' and dialog.native_panel.isEnabled() and dialog.prepare_button.isEnabled()
    assert dialog.orcawave_dialog is None and not dialog.pressure_source_note.isVisibleTo(dialog)
    assert dialog.findChild(type(combo), 'PressureSource') is combo
    from PySide6.QtWidgets import QPushButton
    assert dialog.findChild(QPushButton, 'OpenOrcaWavePressure') is None     # separate button replaced

    monkeypatch.setattr(app, 'orcfxapi_installed', lambda: False)
    combo.setCurrentIndex(1)
    assert not dialog.native_panel.isEnabled() and not dialog.prepare_button.isEnabled()
    assert dialog.orcawave_dialog is not None and dialog.orcawave_dialog.isVisible()
    assert 'OrcFxAPI is not installed' in dialog.pressure_source_note.text()
    assert 'portable pressure bank' in dialog.pressure_source_note.text()
    assert ('OrcFxAPI' in sys.modules) == loaded_before      # selecting never imports it

    combo.setCurrentIndex(0)
    assert dialog.native_panel.isEnabled() and dialog.prepare_button.isEnabled()
    assert not dialog.orcawave_dialog.isVisible() and not dialog.pressure_source_note.isVisibleTo(dialog)
    dialog.reject(); panel.close()


def test_selector_with_orcfxapi_present_mentions_licence(monkeypatch):
    _qt()
    monkeypatch.setattr(app, 'orcfxapi_installed', lambda: True)
    panel, dialog = _wave_dialog()
    dialog.pressure_source.setCurrentIndex(1)
    assert 'Orcina licence' in dialog.pressure_source_note.text()
    dialog.reject(); panel.close()


def test_owr_read_refused_in_dialog_without_starting_a_job(monkeypatch, tmp_path):
    _qt()
    from types import SimpleNamespace
    from PySide6.QtWidgets import QWidget, QLineEdit
    from anyfem.ui.qt.orcawave_pressure import OrcaWavePressureDialog
    project, mesh, _ = fixture()
    panel = QWidget(); panel.case = QLineEdit()
    panel.app = SimpleNamespace(project=project, mesh=mesh, run=lambda c: c.do(project), set_status=lambda s: None)
    dialog = OrcaWavePressureDialog(panel)
    owr = tmp_path / 'r.owr'; owr.write_bytes(b'x')
    monkeypatch.setattr(app, 'orcfxapi_installed', lambda: False)
    monkeypatch.setattr('anyfem.ui.qt.orcawave_pressure.QFileDialog.getOpenFileName', lambda *a, **k: (str(owr), ''))
    dialog.read_source()
    assert 'OrcFxAPI' in dialog.status.text() and 'never starts OrcaWave' in dialog.status.text()
    assert dialog.job is None and dialog.source is None
    dialog.reject(); panel.close()


# ---------------------------------------------------------------- review round 1
def test_repository_doc_holds_no_restricted_numbers_or_workstation_paths():
    import re
    text = (Path(__file__).resolve().parents[1] / 'docs' / 'ORCAWAVE_PRESSURE.md').read_text(encoding='utf8')
    assert not re.search(r'(?<![A-Za-z])[A-Za-z]:[/' + chr(92) * 2 + ']', text)
    assert 'OrcFxAPI 11' not in text and not re.search(r'\d\.\d+e-\d+', text)


def test_switching_source_cancels_running_native_work_and_ignores_its_completion(monkeypatch):
    _qt()
    from PySide6.QtCore import QObject, Signal
    panel, dialog = _wave_dialog()

    class FakeRunner(QObject):
        completed = Signal(object); failed = Signal(str); progress_changed = Signal(str); finished = Signal()
        def __init__(self): super().__init__(); self.cancelled = False; self.running = True
        def isRunning(self): return self.running
        def cancel(self): self.cancelled = True
        def shutdown(self): self.cancel()
    seen = []
    monkeypatch.setattr(dialog, 'prepared_result', lambda r: seen.append(('prepared', r)))
    monkeypatch.setattr(dialog, 'recovered_result', lambda r: seen.append(('recovered', r)))
    monkeypatch.setattr(dialog, 'failure', lambda m: seen.append(('failure', m)))
    for stage in ('preview', 'apply'):
        runner = FakeRunner()
        dialog._bind_runner(runner, stage)
        dialog.pressure_source.setCurrentIndex(1)
        assert runner.cancelled and not dialog.apply_button.isEnabled()
        runner.completed.emit({'late': True}); runner.failed.emit('late'); runner.finished.emit()
        assert seen == [] and not dialog.apply_button.isEnabled()
        dialog.pressure_source.setCurrentIndex(0)
        assert dialog.prepared is None and not dialog.apply_button.isEnabled()
    # work bound while OrcaWave is selected is never current either
    dialog.pressure_source.setCurrentIndex(1)
    late = FakeRunner(); dialog._bind_runner(late, 'preview'); late.completed.emit({'x': 1})
    assert seen == []
    dialog.reject(); panel.close()


def test_anyhydro_selection_exposes_the_stored_family_route(monkeypatch, tmp_path):
    _qt()
    spec, _, _, _ = _family_cache(tmp_path)
    panel, dialog = _wave_dialog()
    from PySide6.QtWidgets import QPushButton
    button = dialog.findChild(QPushButton, 'OpenAnyhydroFamilyPressure')
    assert button is not None and button.isVisibleTo(dialog)
    button.click()
    family = dialog.anyhydro_dialog
    assert family.source_kind == 'anyhydro' and family.isVisible()
    # that dialog reads a family spec and ends in the shared mapping/apply route
    monkeypatch.setattr('anyfem.ui.qt.orcawave_pressure.QFileDialog.getOpenFileName', lambda *a, **k: (str(spec), ''))
    captured = {}
    monkeypatch.setattr(family, 'launch', lambda function, kwargs, callback: captured.update(function=function, kwargs=kwargs))
    family.read_source()
    assert captured['function'] is app.inspect_orcawave_file and captured['kwargs'] == {'path': str(spec), 'kind': 'anyhydro'}
    family.loaded(app.inspect_orcawave_file(spec, kind='anyhydro'))
    assert family.composition.count() == 1 and family.apply_button is not None
    dialog.pressure_source.setCurrentIndex(1)
    assert not button.isVisibleTo(dialog) and not family.isVisible()
    assert dialog.orcawave_dialog.source_kind == 'orcawave'
    dialog.reject(); panel.close()


def test_inspect_enforces_the_selected_kind(tmp_path):
    spec, _, _, _ = _family_cache(tmp_path)
    assert app.inspect_orcawave_file(spec, kind='anyhydro')['type'].startswith('imported_pressure_bank')
    with pytest.raises(ValueError, match='anyhydro pressure source but orcawave was selected'):
        app.inspect_orcawave_file(spec, kind='orcawave')
    project, mesh, kw = job_fixture()
    portable = tmp_path / 'p.json'
    from anyfileio.orcawave import OrcaWavePressureDocument
    OrcaWavePressureDocument.from_dict(kw['source']).save(portable)
    with pytest.raises(ValueError, match='orcawave pressure source but anyhydro was selected'):
        app.inspect_orcawave_file(portable, kind='anyhydro')
    assert app.inspect_orcawave_file(portable, kind='orcawave') == kw['source']


def test_family_import_records_input_file_identity_into_the_applied_history(tmp_path):
    import shutil
    spec, _, _, _ = _family_cache(tmp_path)
    other = tmp_path / 'renamed-spec.json'
    shutil.copy(spec, other)
    a = app.inspect_orcawave_file(spec, kind='anyhydro')
    b = app.inspect_orcawave_file(other, kind='anyhydro')
    pa, pb = json.loads(a['provenance_json']), json.loads(b['provenance_json'])
    assert pa['input_files']['spec'] == dict(name='spec.json', sha256=app.file_sha256(spec))
    assert pb['input_files']['spec']['name'] == 'renamed-spec.json' and a['bank_id'] != b['bank_id']
    assert pa['input_files']['request']['name'] == 'request.json'
    assert '/' not in json.dumps(pa['input_files'])      # names only, no directory paths
    project, mesh, kw = job_fixture()
    kw['source'] = a
    result = app.prepare_orcawave_job(**kw)
    from anyloads.prescribed_pressure import PrescribedPressureHistory
    history = PrescribedPressureHistory.from_dict(result['history'])
    kept = json.loads(json.loads(history.source_payload_json)['provenance_json'])
    assert kept['input_files']['spec']['sha256'] == app.file_sha256(spec)


# ---------------------------------------------------------------- review round 2
def _fake_runner_class():
    from PySide6.QtCore import QObject, Signal

    class FakeRunner(QObject):
        completed = Signal(object); failed = Signal(str); progress_changed = Signal(str); finished = Signal()
        def __init__(self): super().__init__(); self.cancelled = False
        def isRunning(self): return not self.cancelled
        def cancel(self): self.cancelled = True
        def shutdown(self): self.cancel()
    return FakeRunner


@pytest.mark.parametrize('stage', ['preview', 'floating', 'apply'])
def test_cancel_by_switch_restores_native_controls_for_every_job_kind(stage):
    _qt()
    panel, dialog = _wave_dialog()
    runner = _fake_runner_class()()
    dialog._bind_runner(runner, stage)
    # exactly what the start paths do while a job runs
    controls = (*dialog.inputs.values(), dialog.wave_type, dialog.convert, dialog.hydrostatic, dialog.follow,
                dialog.body_mode, *dialog._floating_widgets)
    for c in controls: c.setEnabled(False)
    dialog.prepare_button.setEnabled(False); dialog.repair_button.setEnabled(False)
    dialog.pressure_source.setCurrentIndex(1)
    assert runner.cancelled
    runner.finished.emit()                       # the stale finish is discarded, as before
    dialog.pressure_source.setCurrentIndex(0)
    assert dialog.inputs['height'].isEnabled() and dialog.wave_type.isEnabled() and dialog.body_mode.isEnabled()
    assert dialog.prepare_button.isEnabled() and dialog.repair_button.isEnabled()
    assert dialog._active_stage is None and dialog.runner is runner
    dialog.reject(); panel.close()


def _fake_job_class():
    from PySide6.QtCore import QObject, Signal

    class FakeJob(QObject):
        completed = Signal(object); failed = Signal(str); finished = Signal()
        def __init__(self, *a): super().__init__(); self.cancelled = False; self.ended = False
        def isRunning(self): return not self.cancelled and not self.ended
        def start(self): pass
        def cancel(self): self.cancelled = True
        def wait(self, *a): pass
    return FakeJob


@pytest.mark.parametrize('leaving', ['anyhydro', 'orcawave'])
def test_leaving_a_source_cancels_and_invalidates_its_stored_pressure_dialog(monkeypatch, leaving):
    _qt()
    monkeypatch.setattr('anyfem.ui.qt.orcawave_pressure._Job', _fake_job_class())
    panel, dialog = _wave_dialog()
    if leaving == 'anyhydro':
        dialog.family_button.click(); child = dialog.anyhydro_dialog; other = 1
    else:
        dialog.pressure_source.setCurrentIndex(1); child = dialog.orcawave_dialog; other = 0
    called = []
    child.launch(lambda: None, {}, called.append)
    job = child.job
    child.prepared = {'history': 'prepared'}; child.apply_button.setEnabled(True)
    dialog.pressure_source.setCurrentIndex(other)
    assert job.cancelled and not child.isVisible()
    assert child.prepared is None and not child.apply_button.isEnabled()
    job.completed.emit({'late': True}); job.failed.emit('late')
    assert called == [] and child.prepared is None and not child.apply_button.isEnabled()
    # recovery: the dialog is usable again after returning, without stale results
    dialog.pressure_source.setCurrentIndex(1 - other)
    if leaving == 'anyhydro':
        dialog.family_button.click()
    assert child.isVisible() and child.prepared is None and not child.apply_button.isEnabled()
    dialog.reject(); panel.close()


def test_family_import_records_frequency_and_rao_digests(tmp_path):
    spec, _, _, _ = _family_cache(tmp_path, with_rao=True)
    np.savez(tmp_path / 'freq.npz', omega=np.array([1.]))
    data = json.loads(spec.read_text()); data.pop('omegas'); data['omegas_file'] = 'freq.npz'
    spec2 = tmp_path / 'spec2.json'; spec2.write_text(json.dumps(data))
    bank = app.inspect_orcawave_file(spec2, kind='anyhydro')
    files = json.loads(bank['provenance_json'])['input_files']
    assert files['omegas'] == dict(name='freq.npz', sha256=app.file_sha256(tmp_path / 'freq.npz'))
    assert files['rao'] == dict(name='rao.npz', sha256=app.file_sha256(tmp_path / 'rao.npz'))
    assert set(files) == {'spec', 'request', 'omegas', 'rao'}
    # changing the RAO file changes the retained identity
    np.savez(tmp_path / 'rao.npz', omega=np.array([1.]), rao=np.array([[2 + 1j, 0, 2, 0, 0.5j, 0]]))
    again = app.inspect_orcawave_file(spec2, kind='anyhydro')
    assert again['bank_id'] != bank['bank_id']
    assert json.loads(again['provenance_json'])['input_files']['rao']['sha256'] != files['rao']['sha256']


# ---------------------------------------------------------------- review round 3: one token per job
def _ended_runner_class():
    from PySide6.QtCore import QObject, Signal

    class Runner(QObject):
        completed = Signal(object); failed = Signal(str); progress_changed = Signal(str); finished = Signal()
        def __init__(self, running=True): super().__init__(); self.cancelled = False; self.running = running
        def isRunning(self): return self.running and not self.cancelled
        def cancel(self): self.cancelled = True
        def shutdown(self): self.cancel()
    return Runner


def _record_native(monkeypatch, dialog):
    seen = []
    monkeypatch.setattr(dialog, 'prepared_result', lambda r: seen.append('prepared'))
    monkeypatch.setattr(dialog, 'recovered_result', lambda r: seen.append('recovered'))
    monkeypatch.setattr(dialog, 'failure', lambda m: seen.append('failure'))
    monkeypatch.setattr(dialog, 'finished_run', lambda *a: seen.append('finished'))
    return seen


def _fire(runner):
    runner.completed.emit({'late': 1}); runner.failed.emit('late'); runner.progress_changed.emit('late'); runner.finished.emit()


@pytest.mark.parametrize('stage', ['preview', 'floating', 'apply'])
def test_ended_runner_with_queued_completion_is_stale_after_a_source_round_trip(monkeypatch, stage):
    _qt()
    panel, dialog = _wave_dialog()
    seen = _record_native(monkeypatch, dialog)
    runner = _ended_runner_class()(running=False)          # already ended; its completion is still queued
    dialog._bind_runner(runner, stage)
    dialog.pressure_source.setCurrentIndex(1); dialog.pressure_source.setCurrentIndex(0)
    _fire(runner)
    assert seen == [] and not dialog.apply_button.isEnabled()
    dialog.reject(); panel.close()


def test_repeated_switching_and_relaunch_retire_every_earlier_native_token(monkeypatch):
    _qt()
    panel, dialog = _wave_dialog()
    seen = _record_native(monkeypatch, dialog)
    Runner = _ended_runner_class()
    old = []
    for stage in ('preview', 'floating', 'apply', 'preview'):
        r = Runner(); dialog._bind_runner(r, stage); old.append(r)
        for _ in range(3):
            dialog.pressure_source.setCurrentIndex(1); dialog.pressure_source.setCurrentIndex(0)
    current = Runner(); dialog._bind_runner(current, 'preview')
    for r in old: _fire(r)
    assert seen == []
    current.completed.emit({'x': 1}); current.finished.emit()
    assert seen == ['prepared', 'finished']
    dialog.reject(); panel.close()


def test_old_finished_during_reentrant_relaunch_keeps_the_new_native_timer(monkeypatch):
    _qt()
    panel, dialog = _wave_dialog()
    Runner = _ended_runner_class()
    first, second = Runner(), Runner()
    dialog._bind_runner(first, 'preview')
    def reenter(_result):
        dialog._bind_runner(second, 'preview'); dialog.deadline.start(100000)
    monkeypatch.setattr(dialog, 'prepared_result', reenter)
    first.completed.emit({'x': 1})
    first.finished.emit()
    assert dialog.deadline.isActive() and dialog.runner is second
    dialog.deadline.stop(); dialog.reject(); panel.close()


def _child(monkeypatch, kind):
    _qt()
    from types import SimpleNamespace
    from PySide6.QtWidgets import QWidget, QLineEdit
    from anyfem.ui.qt.orcawave_pressure import OrcaWavePressureDialog
    monkeypatch.setattr('anyfem.ui.qt.orcawave_pressure._Job', _fake_job_class())
    project, mesh, _ = fixture()
    panel = QWidget(); panel.case = QLineEdit()
    panel.app = SimpleNamespace(project=project, mesh=mesh, run=lambda c: c.do(project), set_status=lambda s: None)
    return panel, OrcaWavePressureDialog(panel, kind)


@pytest.mark.parametrize('kind', ['anyhydro', 'orcawave'])
def test_old_finished_after_relaunch_keeps_the_replacement_timer(monkeypatch, kind):
    panel, dialog = _child(monkeypatch, kind)
    dialog.launch(lambda: None, {}, lambda r: None); first = dialog.job
    first.cancel()                                          # ended; its finished signal is still queued
    dialog.launch(lambda: None, {}, lambda r: None); second = dialog.job
    assert second is not first and dialog.deadline.isActive()
    first.finished.emit()
    assert dialog.deadline.isActive()
    second.finished.emit()                                  # the registered job's own finish does stop it
    assert not dialog.deadline.isActive()
    dialog.reject(); panel.close()


@pytest.mark.parametrize('kind', ['anyhydro', 'orcawave'])
@pytest.mark.parametrize('retire', ['deactivate', 'relaunch', 'edit', 'edit_ended'])
def test_retired_saved_pressure_job_callbacks_do_nothing(monkeypatch, kind, retire):
    panel, dialog = _child(monkeypatch, kind)
    called = []
    dialog.launch(lambda: None, {}, called.append); old = dialog.job
    dialog.status.setText('before')
    if retire == 'deactivate': dialog.deactivate()
    elif retire == 'edit': dialog.inputs['height'].setText('7')
    elif retire == 'edit_ended':
        old.ended = True                                     # finished, completion queued, never cancelled
        dialog.inputs['height'].setText('7')
    else:
        old.cancel(); dialog.launch(lambda: None, {}, called.append)
    status = dialog.status.text(); active = dialog.deadline.isActive()
    old.completed.emit({'late': 1}); old.failed.emit('late failure'); old.finished.emit()
    assert called == [] and dialog.status.text() == status and dialog.deadline.isActive() == active
    assert not dialog.apply_button.isEnabled()
    if retire == 'relaunch':                                # the replacement is the registered job and does work
        dialog.job.completed.emit({'ok': 1}); assert called == [{'ok': 1}]
    dialog.reject(); panel.close()


@pytest.mark.parametrize('kind', ['anyhydro', 'orcawave'])
def test_reentrant_launch_from_a_completion_callback(monkeypatch, kind):
    panel, dialog = _child(monkeypatch, kind)
    holder = {}
    def again(result):
        holder['first'].running_flag = False
        holder['first'].cancel()
        dialog.launch(lambda: None, {}, lambda r: None); holder['second'] = dialog.job
    dialog.launch(lambda: None, {}, again); holder['first'] = dialog.job
    holder['first'].completed.emit({'x': 1})
    assert holder['second'] is not holder['first'] and dialog.deadline.isActive()
    holder['first'].finished.emit()
    assert dialog.deadline.isActive()
    dialog.reject(); panel.close()


def test_direct_finished_run_with_a_retired_token_changes_nothing(monkeypatch):
    _qt()
    panel, dialog = _wave_dialog()
    Runner = _ended_runner_class()
    first, second = Runner(), Runner()
    dialog._bind_runner(first, 'preview'); old_token = dialog._token
    dialog._bind_runner(second, 'preview'); dialog.deadline.start(100000)
    dialog.finished_run(first, old_token)
    assert dialog._token is not None and dialog._token is not old_token and dialog.deadline.isActive()
    dialog.deadline.stop(); dialog.reject(); panel.close()


# ---- N3: the whole job input signature belongs to the job token --------------------------------------------------
def _flip_body(d): d.body_mode.setCurrentIndex(1 - d.body_mode.currentIndex())
def _flip_hydro(d): d.hydrostatic.setChecked(not d.hydrostatic.isChecked())
def _flip_wave(d): d.wave_type.setCurrentIndex(1 - d.wave_type.currentIndex())
def _flip_source(d): d.pressure_source.setCurrentIndex(1 - d.pressure_source.currentIndex())


_CONTROLS = {'body': ('body_mode', _flip_body), 'hydrostatic': ('hydrostatic', _flip_hydro),
             'wave': ('wave_type', _flip_wave), 'source': ('pressure_source', _flip_source)}


@pytest.mark.parametrize('name', ['convert', 'floating_dev', 'floating_native'])
def test_silent_additional_job_inputs_retire_their_native_token(monkeypatch, name):
    _qt();panel,dialog=_wave_dialog();seen=_record_native(monkeypatch,dialog)
    runner=_ended_runner_class()();dialog._bind_runner(runner,'preview');dialog.deadline.start(100000)
    control=getattr(dialog,name);control.blockSignals(True);control.setChecked(not control.isChecked());control.blockSignals(False)
    _fire(runner)
    assert seen==[] and runner.cancelled and dialog._token is None and not dialog.deadline.isActive()
    assert not dialog.apply_button.isEnabled()
    dialog.reject();panel.close()


@pytest.mark.parametrize('silent', [False, True], ids=['signalled', 'silent'])
@pytest.mark.parametrize('stage', ['preview', 'apply'])
@pytest.mark.parametrize('control', list(_CONTROLS))
def test_changing_any_job_input_while_running_discards_every_late_callback(monkeypatch, control, stage, silent):
    _qt()
    panel, dialog = _wave_dialog()
    seen = _record_native(monkeypatch, dialog)
    project_keys = set(panel.app.project.load_cases)
    runner = _ended_runner_class()()
    dialog._bind_runner(runner, stage); dialog.deadline.start(100000)
    widget_name, flip = _CONTROLS[control]
    widget = getattr(dialog, widget_name)
    if silent: widget.blockSignals(True)            # no change handler runs: only the signature can notice
    flip(dialog)
    if silent: widget.blockSignals(False)
    dialog.status.setText('SENTINEL')
    runner.progress_changed.emit('late progress')
    assert dialog.status.text() == 'SENTINEL'
    runner.completed.emit({'late': 1}); runner.failed.emit('late'); runner.finished.emit()
    assert seen == [] and dialog.prepared is None and not dialog.apply_button.isEnabled()
    assert not dialog.deadline.isActive() and dialog._token is None and runner.cancelled
    assert set(panel.app.project.load_cases) == project_keys
    dialog.reject(); panel.close()


def test_a_fresh_job_after_an_input_change_is_still_accepted(monkeypatch):
    _qt()
    panel, dialog = _wave_dialog()
    seen = _record_native(monkeypatch, dialog)
    first = _ended_runner_class()()
    dialog._bind_runner(first, 'preview'); _flip_hydro(dialog)
    second = _ended_runner_class()(); dialog._bind_runner(second, 'preview')
    first.completed.emit({'x': 0}); second.completed.emit({'x': 1}); second.finished.emit()
    assert seen == ['prepared', 'finished']
    dialog.reject(); panel.close()


@pytest.mark.parametrize('control', ['body', 'hydrostatic', 'wave'])
def test_changing_a_job_input_retires_the_running_job_at_once_without_any_callback(control):
    _qt()
    panel, dialog = _wave_dialog()
    runner = _ended_runner_class()()
    dialog._bind_runner(runner, 'preview'); dialog.deadline.start(100000)
    dialog.prepare_button.setEnabled(False)
    _CONTROLS[control][1](dialog)
    assert dialog._token is None and runner.cancelled and not dialog.deadline.isActive()
    assert dialog.inputs['height'].isEnabled() and dialog.prepare_button.isEnabled()
    dialog.reject(); panel.close()
