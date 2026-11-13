"""Portable Qt preparation preview; no Orcina installation or licence needed."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication,QWidget,QLineEdit
from PySide6.QtCore import QObject,Signal
from test_orcawave_pressure import job_fixture
from anyfem.ui.qt.orcawave_pressure import OrcaWavePressureDialog
from anyfem.application.orcawave_pressure import prepare_orcawave_job


def test_portable_ui_preview_apply_and_stale_callback(monkeypatch,tmp_path):
    qapp=QApplication.instance() or QApplication([])
    from PySide6.QtGui import QFont,QFontDatabase
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf');qapp.setFont(QFont('Segoe UI',9))
    project,mesh,kw=job_fixture();panel=QWidget();panel.case=QLineEdit()
    panel.app=SimpleNamespace(project=project,mesh=mesh,run=lambda c:c.do(project),set_status=lambda s:None)
    dialog=OrcaWavePressureDialog(panel);dialog.loaded(kw['source'])
    dialog.surfaces.item(0).setSelected(True)
    for key,value in [('gap_m','0.000001'),('angle','1'),('force_relative_limit','0.000001'),('moment_relative_limit','0.000001'),
                      ('height','2'),('duration','1'),('dt','.5'),('force_absolute_limit','0.00000001'),('moment_absolute_limit','0.00000001')]:
        dialog.inputs[key].setText(value)
    dialog.pending_settings=dialog.settings();kw['settings']=dialog.pending_settings
    result=prepare_orcawave_job(**kw);dialog.ready(result)
    assert dialog.apply_button.isEnabled()
    dialog.show();qapp.processEvents();dialog.grab().save(str(tmp_path/'orcawave-dialog.png'))
    dialog.time_slider.setValue(1);assert '0.5' in dialog.snapshot_label.text()
    class FakeJob(QObject):
        completed=Signal(object);failed=Signal(str);finished=Signal()
        def __init__(self,*a):super().__init__();self.cancelled=False
        def isRunning(self):return False
        def start(self):pass
        def cancel(self):self.cancelled=True
        def wait(self,*a):pass
    monkeypatch.setattr('anyfem.ui.qt.orcawave_pressure._Job',FakeJob)
    called=[];dialog.launch(lambda:None,{},called.append);job=dialog.job
    dialog.path.setText('changed.json');job.completed.emit({'old':True})
    assert not called and not dialog.apply_button.isEnabled()
    dialog.loaded(kw['source']);dialog.pending_settings=dialog.settings()
    # Restore matching preparation and exercise application through the dialog.
    dialog.pending_settings=dialog.settings();dialog.ready(result);dialog.apply()
    assert project.load_cases[result['name']].times==(0.,.5,1.)
    dialog.reject();panel.close()


def test_native_portable_bank_ui_sheet_roles_and_qualification():
    from test_orcawave_pressure import native_anyhydro_bank
    qapp=QApplication.instance() or QApplication([])
    project,mesh,kw=job_fixture();panel=QWidget();panel.case=QLineEdit()
    panel.app=SimpleNamespace(project=project,mesh=mesh,run=lambda c:c.do(project),set_status=lambda s:None)
    bank=native_anyhydro_bank();dialog=OrcaWavePressureDialog(panel);dialog.loaded(bank.to_dict())
    assert dialog.body.count()==dialog.heading.count()==dialog.composition.count()==1
    assert 'development_fixture' in dialog.status.text()
    dialog.surfaces.item(0).setSelected(True);dialog.dipole_surfaces.item(0).setSelected(True)
    for key,value in [('gap_m','0.000001'),('angle','1'),('force_relative_limit','0.000001'),('moment_relative_limit','0.000001'),
                      ('height','2'),('duration','1'),('dt','.5'),('force_absolute_limit','0.00000001'),('moment_absolute_limit','0.00000001')]:
        dialog.inputs[key].setText(value)
    dialog.pending_settings=dialog.settings();kw.update(source=bank.to_dict(),settings=dialog.pending_settings)
    result=prepare_orcawave_job(**kw);dialog.ready(result)
    assert dialog.apply_button.isEnabled()
    assert 'signed sheet jump' in dialog.snapshot_label.text()
    assert 'sheets only' in dialog.preview.toPlainText() and 'development_fixture' in dialog.preview.toPlainText()
    dialog.dipole_surfaces.item(0).setSelected(False)
    assert not dialog.apply_button.isEnabled()
    dialog.reject();panel.close()
