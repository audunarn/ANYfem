"""Hydrostatic option in the wave load dialog (opt-in Qt; stubbed calculation, no BEM)."""
import os
import json
import threading
import time
from dataclasses import asdict,replace
from types import SimpleNamespace
import numpy as np
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt opt-in',allow_module_level=True)
from PySide6.QtCore import QThread,Signal
from PySide6.QtWidgets import QWidget,QLineEdit
from test_qt_geometry_actions import qapp
from anyfem import Project,steel
from anyfem.model import WetPressure,WetSurface
from anyfem.document import DocumentSession,canonical_hash
from anyfem.io.project_file import project_to_dict,project_from_dict
from anyfem.application import wave_hydrostatic as hs
from anyfem.application.irregular_wave_input import IrregularWaveInput
from anyfem.ui.qt.wave_load import WaveLoadDialog
import anyfem.ui.qt.wave_load as ui
from anyloads.spectral_pressure_history import SpectralMappedPressureHistory


def panel_for(project):
    panel=QWidget();panel.app=SimpleNamespace(project=project,session=SimpleNamespace(read_only=False),mesh=None)
    panel.case_name=lambda:'default'
    return panel


@pytest.mark.parametrize('mean',[-135745875.,135745875.,0.])
@pytest.mark.parametrize('size',[(620,440),(900,700)])
def test_extrema_labels_stay_inside_and_apart_for_near_flat_signed_edge_traces(qapp,monkeypatch,mean,size):
    from anyfem.ui.qt.wave_preview_plot import WaveResultantPlot
    plot=WaveResultantPlot();plot.resize(*size);plot.show()
    t=np.arange(101)*.1;values=np.stack([1e3*np.sin(t+k) for k in range(6)],axis=1)
    values[:,4]=mean+2e3*np.sin(3*t)  # near-flat: max and min sit at the same signed edge
    plot.set_composition(True);plot.set_data(t,values,(t[0],t[-1]))
    seen=[];original=plot._label_rect
    def spy(point,size,rect,above,taken):
        box=original(point,size,rect,above,taken);seen.append((rect,box,list(taken)));return box
    monkeypatch.setattr(plot,'_label_rect',spy);plot.grab()
    by_rect={}
    for rect,box,_ in seen:by_rect.setdefault((rect.x(),rect.y()),(rect,[]))[1].append(box)
    assert len(by_rect)==6
    for rect,boxes in by_rect.values():
        assert len(boxes)==2 and all(rect.contains(b) for b in boxes) and not boxes[0].intersects(boxes[1])
    # raw values, occurrences and symmetric axes are untouched
    lo,tlo,hi,thi=plot.visible_extrema()[4]
    assert lo==values[:,4].min()/1000. and hi==values[:,4].max()/1000. and tlo==t[int(np.argmin(values[:,4]))]
    assert plot.y_bounds()[4]==pytest.approx(np.abs(values[:,4]).max()/1000.*1.08)
    assert 'steady hydrostatic' in plot.composition_label and len(plot.composition_label)<40
    plot.close()


def spectral(config,provenance=None):
    r=config.realization()
    return SpectralMappedPressureHistory(config.times,(42,),tuple(x.omega for x in r.components),((1.,),(2.,)),((0.,),(0.,)),
        0.,'a'*64,'b'*64,json.dumps(r.to_dict()),json.dumps(dict(settings=asdict(config)) if provenance is None else provenance))


def saved_project(config,provenance=None):
    p=Project();owner=p.add_load_series(config.name,config.times)
    owner.wet_pressures.append(WetPressure(history=spectral(config,provenance),mapping_key='element'));return p


def test_checkbox_default_settings_note_and_new_record_reset(qapp):
    panel=panel_for(Project());dialog=WaveLoadDialog(panel);dialog.show();qapp.processEvents()
    box=dialog.hydrostatic
    assert box.objectName()=='wave_include_hydrostatic' and box.text()=='Include hydrostatic pressure (mean water level)'
    assert not box.isChecked() and dialog.settings().include_hydrostatic is False
    for token in ('1.5 m','1025 kg/m3','9.81 m/s2'):assert token in dialog.hydrostatic_note.text()
    assert dialog.plot.composition_label=='dynamic incident + diffraction (dynamic-only)'
    box.setChecked(True);assert dialog.settings().include_hydrostatic is True
    dialog.wave_type.setCurrentIndex(1);assert dialog.settings().include_hydrostatic is True
    assert 'hydrostatic' in dialog.plot.composition_label
    dialog._settings_problem='saved settings missing';dialog.start_new_record()
    assert not box.isChecked() and dialog.settings().include_hydrostatic is False
    dialog.close();panel.close()


def test_saved_flag_restores_and_legacy_without_flag_is_dynamic_only(qapp):
    c=IrregularWaveInput(name='Saved sea',component_count=2,seed=44,include_hydrostatic=True)
    panel=panel_for(saved_project(c));dialog=WaveLoadDialog(panel)
    assert dialog.hydrostatic.isChecked() and dialog.settings()==c and dialog._editing_settings==asdict(c)
    assert 'hydrostatic' in dialog.plot.composition_label
    dialog.close();panel.close()
    off=replace(c,include_hydrostatic=False)
    legacy={k:v for k,v in asdict(off).items() if k!='include_hydrostatic'}
    panel=panel_for(saved_project(off,dict(settings=legacy)));dialog=WaveLoadDialog(panel)
    assert not dialog.hydrostatic.isChecked() and dialog.settings()==off
    dialog.close();panel.close()


class Calc(QThread):
    """Stub worker: dynamic source resultants (+ steady baseline when enabled); optional gate."""
    completed=Signal(object);failed=Signal(str);progress_changed=Signal(str)
    calls=[];gate=None;ids=()
    def __init__(self,kwargs,parent=None):
        super().__init__(parent);self.kwargs=kwargs;self.cancelled=False;Calc.calls.append(kwargs)
    def run(self):
        from dataclasses import replace as rep
        from test_pressure_remap import source_bank
        from anyloads.source_pressure_history import SourceAwarePressureHistory
        from anyloads.wave_preview import source_resultants
        if Calc.gate is not None:Calc.gate.wait(10)
        k=self.kwargs;c=IrregularWaveInput(**k['settings']);r=c.realization();n=len(Calc.ids)
        mesh_hash=k.get('mesh_content_hash') or canonical_hash(k['mesh_data'])
        document_hash=k.get('document_content_hash') or canonical_hash(k['document'])
        h=SpectralMappedPressureHistory(c.times,Calc.ids,tuple(x.omega for x in r.components),((1.,)*n,(2.,)*n),((0.,)*n,(.1,)*n),
            0.,mesh_hash,'b'*64,json.dumps(r.to_dict()),json.dumps(dict(settings=asdict(c))))
        bank=rep(source_bank(),omegas=tuple(x.omega for x in r.components),bank_id='')
        h=SourceAwarePressureHistory(h,json.dumps(bank.to_dict()),json.dumps(r.to_dict()),json.dumps(dict(source_bank_id=bank.bank_id)))
        preview=source_resultants(bank,r,c.times);values=hs.total_resultants(preview.values,c.include_hydrostatic)
        result=dict(name=c.name,history=h.to_dict(),surfaces=[s.id for s in self.project.wet_surfaces],
            input_document_hash=document_hash,mesh_hash=mesh_hash,source_cache_path=None,
            input_settings_hash=canonical_hash(k['settings']),preview_times=preview.times.tolist(),
            preview_resultants=values.tolist(),preview_source_bank_id=bank.bank_id,preview_moment_origin=preview.moment_origin)
        result['preview_content_hash']=canonical_hash({key:result[key] for key in
            ('preview_times','preview_resultants','preview_source_bank_id','preview_moment_origin')})
        if not self.cancelled:self.completed.emit(result)
    def cancel(self):
        self.cancelled=True
        if Calc.gate is not None:Calc.gate.set()
    def shutdown(self):self.cancel();self.wait(1000)


def world(qapp,monkeypatch,project=None):
    p=project or Project()
    if not p.wet_surfaces:
        p.add_material(steel());p.add_plate_section('plate',.01,'S355')
        g=p.geometry;vertices=g.add_points([(0,0,0),(1,0,0),(1,1,0),(0,1,0)])
        face=g.add_face(g.add_polyline(vertices,close=True));p.assign_plate(face,'plate')
        p.add_wet_surface(WetSurface(ref=p.face(face),fluid_side='positive_normal'))
    mesh=p.generate_mesh(1.);face=next(iter(mesh.elements_of_face))
    Calc.calls=[];Calc.gate=None;Calc.ids=tuple(sorted(mesh.elements_of_face[face]))
    monkeypatch.setattr(Calc,'project',p,raising=False);monkeypatch.setattr(ui,'WaveCalculation',Calc)
    panel=panel_for(p);panel.case=QLineEdit();commands=[]
    panel.app.mesh=mesh;panel.app.session=DocumentSession(p);panel.app.panels={};panel.app.path=None
    panel.app.set_status=lambda value:None
    def run(command):commands.append(command);return command.do(p)
    panel.app.run=run
    return p,panel,commands


def prepare(qapp,dialog):
    dialog.start();deadline=time.monotonic()+10
    while (dialog.prepared is None or dialog.runner.isRunning()) and time.monotonic()<deadline:
        qapp.processEvents();time.sleep(.01)
    qapp.processEvents();assert dialog.prepared is not None,dialog.status.text()


def irregular_dialog(panel):
    dialog=WaveLoadDialog(panel);dialog.wave_type.setCurrentIndex(1);dialog.inputs['component_count'].setText('2');return dialog


def test_combined_preview_title_values_and_selected_window_survive_toggle(qapp,monkeypatch):
    p,panel,commands=world(qapp,monkeypatch);dialog=irregular_dialog(panel)
    prepare(qapp,dialog);dynamic=np.array(dialog.prepared['preview_resultants'])
    assert dialog.plot.composition_label.endswith('(dynamic-only)') and 'dynamic-only' in dialog.record_info.text()
    dialog.set_window(3.,7.);interval=tuple(dialog.plot.interval)
    dialog.hydrostatic.setChecked(True)
    assert dialog.prepared is None and not dialog.apply_button.isEnabled() and 'Prepare a new preview' in dialog.status.text()
    assert tuple(dialog._restored_interval)==interval
    prepare(qapp,dialog)
    total=np.array(dialog.prepared['preview_resultants'])
    np.testing.assert_allclose(total-dynamic,np.broadcast_to(hs.source_baseline(),total.shape),rtol=1e-12,atol=1e-6)
    assert 'steady hydrostatic' in dialog.plot.composition_label and 'dynamic + steady hydrostatic' in dialog.record_info.text()
    assert tuple(dialog.plot.interval)==interval and Calc.calls[-1]['settings']['include_hydrostatic'] is True
    # the unchanged dynamic source is retained for the toggle, not rebuilt from fresh wave inputs
    assert 'retained_history' in Calc.calls[-1] or 'retained_preview' in Calc.calls[-1]
    assert not commands and not p.load_cases
    dialog.close();panel.close()


def test_controls_disabled_while_running_and_toggle_during_run_never_applies(qapp,monkeypatch):
    p,panel,commands=world(qapp,monkeypatch);dialog=irregular_dialog(panel)
    Calc.gate=threading.Event();dialog.start()
    assert not dialog.hydrostatic.isEnabled() and not dialog.wave_type.isEnabled() and not dialog.prepare_button.isEnabled()
    dialog.hydrostatic.setChecked(True)  # programmatic change while the stale job is still running
    Calc.gate.set();deadline=time.monotonic()+10
    while dialog.runner.isRunning() and time.monotonic()<deadline:qapp.processEvents();time.sleep(.01)
    qapp.processEvents()
    assert dialog.prepared is None and not dialog.apply_button.isEnabled() and 'changed' in dialog.status.text()
    assert dialog.hydrostatic.isEnabled() and not commands and not p.load_cases
    # cancel path
    Calc.gate=threading.Event();dialog.start();dialog.runner.cancel();deadline=time.monotonic()+10
    while dialog.runner.isRunning() and time.monotonic()<deadline:qapp.processEvents();time.sleep(.01)
    qapp.processEvents()
    assert dialog.prepared is None and not commands and not p.load_cases and dialog.hydrostatic.isEnabled()
    dialog.close();panel.close()


def companions(project):
    return [load for case in project.load_cases.values() for load in case.wet_pressures if load.name==hs.NAME]


def test_apply_edit_toggle_off_on_keeps_exactly_one_owned_companion_with_undo_redo(qapp,monkeypatch):
    p,panel,commands=world(qapp,monkeypatch);dialog=irregular_dialog(panel)
    dialog.hydrostatic.setChecked(True);prepare(qapp,dialog);dialog.apply_result()
    assert len(commands)==1,dialog.status.text()
    owner=next(c for c in p.load_cases.values() if c.parent is None and c.times is not None)
    assert hs.owned_pair(owner) is not None and len(companions(p))==1
    model_hash=DocumentSession(p).revision.model_hash
    reopened=project_from_dict(project_to_dict(p));assert hs.owned_pair(reopened.load_cases[owner.name]) is not None
    for flag in (False,True):
        panel2=panel_for(p);panel2.case=QLineEdit();panel2.app=panel.app
        edit=WaveLoadDialog(panel2)
        assert edit.convert.isChecked() and edit.hydrostatic.isChecked()==(not flag) and edit._editing_settings is not None
        edit.hydrostatic.setChecked(flag);prepare(qapp,edit);edit.apply_result()
        assert len(commands)==2+int(flag),edit.status.text()
        assert len(companions(p))==int(flag) and len(p.load_cases[owner.name].wet_pressures)==1+int(flag)
        assert DocumentSession(p).revision.model_hash==model_hash
        edit.close();panel2.close()
    commands[-1].undo(p);assert len(companions(p))==0 and len(p.load_cases[owner.name].wet_pressures)==1
    commands[-1].redo(p);assert len(companions(p))==1 and hs.owned_pair(p.load_cases[owner.name]) is not None
    dialog.close();panel.close()
