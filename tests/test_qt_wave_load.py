"""Actual primary-button, input, owned-process and saved-series workflow."""
import os,time
from pathlib import Path
import numpy as np
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt opt-in',allow_module_level=True)
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_qt_geometry_actions import qapp,window
from anyfem.io.project_file import load_project,project_to_dict
from anyfem.document import canonical_hash

SOURCE=Path('C:/ANYworkspaceWork/barge-pressure-pipeline-20261026/mapped-verified/barge-wave-pressure.anyfem')


def wait(qapp,predicate,seconds=40):
    deadline=time.monotonic()+seconds
    while not predicate():
        if time.monotonic()>deadline:raise AssertionError('Qt wave calculation did not finish')
        QTest.qWait(50);qapp.processEvents()


def opened(window,qapp):
    if not SOURCE.is_file():pytest.skip('retained verified barge fixture unavailable')
    window.open_project(str(SOURCE));wait(qapp,lambda:window.mesh is not None)
    window.notebook.select('Loads & BC');qapp.processEvents()
    return window.panels['Loads & BC']


def test_primary_button_real_child_mapping_one_case_saved(window,qapp,tmp_path):
    panel=opened(window,qapp);mesh=window.mesh
    QTest.mouseClick(panel.wave_button,Qt.LeftButton);qapp.processEvents()
    dialog=panel._wave_dialog
    assert dialog.inputs['height'].text()=='1.0' and dialog.inputs['period'].text()=='6.0'
    assert dialog.convert.isChecked()
    QTest.mouseClick(dialog.apply_button,Qt.LeftButton)
    wait(qapp,lambda:dialog.runner is not None and not dialog.runner.isRunning())
    qapp.processEvents()
    owners=[c for c in window.project.load_cases.values() if c.parent is None]
    assert len(owners)==1,dialog.status.text()
    parent=owners[0];assert len(parent.times)==101 and len(parent.wet_pressures)==1
    assert window.mesh is mesh
    assert len(window.project.analyses)==1
    assert window.panels['Solve'].analysis.currentText()=='Batch linear static'
    assert len(window.panels['Solve'].settings()['load_cases'])==101
    h=parent.wet_pressures[0].history
    old=np.load(SOURCE.parent/'pressure-mapping.npz')
    assert h.element_ids==tuple(old['element_ids'])
    assert np.allclose(np.array([h.evaluate(t) for t in h.times]),old['pressures'],rtol=1e-13,atol=1e-8)
    data=project_to_dict(window.project)
    assert len(data['load_cases'])==1 and data['anyfem']['format']==15
    path=tmp_path/'series.anyfem';window.save_project(path=str(path))
    reopened=load_project(path)
    assert len([c for c in reopened.load_cases.values() if c.parent is None])==1
    assert len(reopened.series_children(parent.name))==101
    ids=(parent.id,next(iter(window.project.analyses)))
    window.undo();assert len(window.project.load_cases)==101
    window.redo();assert (window.project.load_cases[parent.name].id,next(iter(window.project.analyses)))==ids


def test_cancel_and_stale_input_do_not_apply(window,qapp):
    panel=opened(window,qapp)
    before=canonical_hash(project_to_dict(window.project))
    QTest.mouseClick(panel.wave_button,Qt.LeftButton);qapp.processEvents();dialog=panel._wave_dialog
    QTest.mouseClick(dialog.apply_button,Qt.LeftButton)
    dialog.reject()
    wait(qapp,lambda:not dialog.runner.isRunning())
    assert canonical_hash(project_to_dict(window.project))==before
    QTest.mouseClick(panel.wave_button,Qt.LeftButton);qapp.processEvents();dialog=panel._wave_dialog
    QTest.mouseClick(dialog.apply_button,Qt.LeftButton)
    window.project.name='Changed during wave input'
    changed=canonical_hash(project_to_dict(window.project))
    wait(qapp,lambda:not dialog.runner.isRunning());qapp.processEvents()
    assert canonical_hash(project_to_dict(window.project))==changed
    assert len(window.project.load_cases)==101
    assert 'changed' in dialog.status.text().lower()


def test_missing_selector_falls_back_to_unique_existing_history_owner(qapp):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QWidget
    from anyfem import Project
    from anyfem.model import WetPressure
    from anyloads.pressure_history import MappedPressureHistory
    from anyfem.ui.qt.wave_load import WaveLoadDialog
    import json
    p=Project();parent=p.add_load_series('Sea test',(0.,1.))
    history=MappedPressureHistory((0.,1.),(1,),(2.,),(0.,),1.,'a'*64,'fixture',
        json.dumps({'settings':{'height':2.,'period':7.}}))
    parent.wet_pressures.append(WetPressure(history=history,mapping_key='element'))
    panel=QWidget();panel.app=SimpleNamespace(project=p);panel.case_name=lambda:'default'
    dialog=WaveLoadDialog(panel)
    assert dialog.inputs['name'].text()=='Sea test'
    assert dialog.inputs['height'].text()=='2.0' and dialog.inputs['period'].text()=='7.0'
    assert dialog.convert.isChecked() and not dialog.convert.isHidden()
    assert dialog.runner is None
    dialog.close();panel.close()
