import os,time
from dataclasses import asdict
from types import SimpleNamespace
import numpy as np
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt opt-in',allow_module_level=True)
from PySide6.QtCore import QThread,Signal,Qt,QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget,QLineEdit
from test_qt_geometry_actions import qapp
from test_wave_window_preview import irregular_record
from anyfem.application.hydro_wave_input import ApplyWavePressure,select_prepared_wave_window
from anyfem.application.irregular_wave_input import IrregularWaveInput
from anyfem.document import DocumentSession,canonical_hash
from anyfem.io.project_file import project_to_dict,project_from_dict
from anyfem.ui.qt.wave_load import WaveLoadDialog


def wait(qapp,predicate):
    deadline=time.monotonic()+15
    while not predicate():
        if time.monotonic()>deadline:raise AssertionError('fast retained preview did not finish')
        qapp.processEvents();time.sleep(.01)
    qapp.processEvents()


def test_retained_late_window_drag_numeric_rename_and_stale_guards(qapp,monkeypatch,tmp_path):
    import anyfem.ui.qt.wave_load as ui
    import anyfem.application.hydro_wave_input as service
    p,old,new,config,h,result=irregular_record()
    owner=ApplyWavePressure(select_prepared_wave_window(result,6.,8.1)).do(p)
    def forbidden(*args,**kwargs):raise AssertionError('must use retained source/phases without generation')
    monkeypatch.setattr(service,'compute_wave_input',forbidden)
    monkeypatch.setattr(IrregularWaveInput,'realization',forbidden)
    class RetainedCalculation(QThread):
        completed=Signal(object);failed=Signal(str);progress_changed=Signal(str)
        def __init__(self,kwargs,parent=None):
            super().__init__(parent);self.kwargs=dict(kwargs);self.stage=self.kwargs.pop('_wave_stage','preview');self.cancelled=False
        def run(self):
            try:self.completed.emit((service.recover_prepared_wave_input if self.stage=='apply' else service.prepare_wave_preview)(**self.kwargs))
            except Exception as error:self.failed.emit(str(error))
        def cancel(self):self.cancelled=True
        def shutdown(self):self.wait(1000)
    monkeypatch.setattr(ui,'WaveCalculation',RetainedCalculation)
    panel=QWidget();panel.case=QLineEdit();panel.case_name=lambda:owner.name;commands=[]
    def apply(command):commands.append(command);return command.do(p)
    panel.app=SimpleNamespace(project=p,mesh=new,session=DocumentSession(p),panels={},path=None,
        run=apply,set_status=lambda message:None,wave_solution_cache_directory=tmp_path)
    dialog=WaveLoadDialog(panel);dialog.show();qapp.processEvents()
    dialog.start();wait(qapp,lambda:dialog.runner is not None and not dialog.runner.isRunning())
    assert dialog.prepared is not None,dialog.status.text()
    assert dialog.plot.interval==(6.,8.1) and not commands
    before=canonical_hash(project_to_dict(p));first_values=np.array(dialog.prepared['preview_resultants'])
    for edit,text in ((dialog.window_start,'7.04'),(dialog.window_end,'9.06')):
        edit.setFocus();QTest.keyClick(edit,Qt.Key_A,Qt.ControlModifier);QTest.keyClicks(edit,text)
        QTest.keyClick(edit,Qt.Key_Tab);qapp.processEvents()
    assert dialog.plot.interval==(7.,9.1)
    rect=dialog.plot.plot_rects()[0]
    start=QPoint(round(dialog.plot._x(7.,rect)),round(rect.center().y()))
    target=QPoint(round(dialog.plot._x(6.4,rect)),round(rect.center().y()))
    QTest.mousePress(dialog.plot,Qt.LeftButton,Qt.NoModifier,start)
    QTest.mouseMove(dialog.plot,target,10);QTest.mouseRelease(dialog.plot,Qt.LeftButton,Qt.NoModifier,target)
    qapp.processEvents();chosen=dialog.plot.interval
    assert chosen[0]==pytest.approx(6.4,abs=.1) and chosen[0] in config.times
    assert canonical_hash(project_to_dict(p))==before
    # Clearing a name is a normal intermediate edit; no validation from slot.
    dialog.inputs['name'].setText('');qapp.processEvents();assert dialog.prepared is None
    dialog.inputs['name'].setText('Selected event B');dialog.start()
    wait(qapp,lambda:not dialog.runner.isRunning())
    assert dialog.prepared is not None,dialog.status.text()
    assert dialog.plot.interval==chosen and not dialog.convert.isChecked()
    np.testing.assert_array_equal(dialog.prepared['preview_resultants'],first_values)
    # Invalid interval refuses without mutation, then a corrected interval applies.
    dialog.window_start.setText('9.1');dialog.window_end.setText('9.1');dialog.numeric_window()
    assert not dialog.apply_button.isEnabled() and canonical_hash(project_to_dict(p))==before
    dialog.set_window(*chosen);dialog.apply_result();wait(qapp,lambda:not dialog.runner.isRunning())
    assert len(commands)==1
    selected=p.load_cases['Selected event B'].wet_pressures[0].history
    assert selected.selected_interval==chosen and selected.full_record_times==config.times
    assert selected.times[0]>0 and selected.realization_json==h.realization_json
    reopened=project_from_dict(project_to_dict(p))
    check=WaveLoadDialog(panel);assert check.settings().wave_type=='irregular';check.close()
    assert reopened.load_cases['Selected event B'].wet_pressures[0].history.selected_interval==chosen
    commands[0].undo(p);assert 'Selected event B' not in p.load_cases
    commands[0].redo(p);assert 'Selected event B' in p.load_cases
    dialog.close();panel.close()
