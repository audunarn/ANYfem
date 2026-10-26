import os
import json
from dataclasses import asdict
from types import SimpleNamespace
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt opt-in',allow_module_level=True)
from PySide6.QtWidgets import QWidget
from test_qt_geometry_actions import qapp
from anyfem import Project
from anyfem.model import WetPressure
from anyfem.application.irregular_wave_input import IrregularWaveInput
from anyfem.application.hydro_wave_input import RegularWaveInput
from anyfem.ui.qt.wave_load import WaveLoadDialog
from anyloads.spectral_pressure_history import SpectralMappedPressureHistory


def panel_for(project):
    panel=QWidget();panel.app=SimpleNamespace(project=project,session=SimpleNamespace(read_only=False),mesh=None)
    panel.case_name=lambda:'default'
    return panel


def test_mode_default_fields_integer_refusal_and_long_history(qapp):
    panel=panel_for(Project());dialog=WaveLoadDialog(panel);dialog.show();qapp.processEvents()
    assert dialog.settings()==RegularWaveInput()
    assert dialog.wave_type.count()==2
    dialog.wave_type.setCurrentIndex(1);qapp.processEvents()
    assert dialog.settings().component_count==32 and dialog.settings().seed==0
    assert dialog.inputs['phase'].isHidden() and not dialog.inputs['gamma'].isHidden()
    dialog.inputs['duration'].setText('10800');assert len(dialog.settings().times)==108001
    dialog.inputs['component_count'].setText('2.5')
    with pytest.raises(ValueError):dialog.settings()
    dialog.close();panel.close()


def test_edit_restores_saved_spectrum_and_readonly_refuses(qapp):
    p=Project();c=IrregularWaveInput(name='Saved sea',component_count=2,seed=44,significant_height=2.)
    r=c.realization();h=SpectralMappedPressureHistory(c.times,(42,),tuple(x.omega for x in r.components),
        ((1.,),(2.,)),((0.,),(0.,)),0.,'a'*64,'b'*64,json.dumps(r.to_dict()),json.dumps(dict(settings=asdict(c))))
    owner=p.add_load_series(c.name,c.times);owner.wet_pressures.append(WetPressure(history=h,mapping_key='element'))
    panel=panel_for(p);dialog=WaveLoadDialog(panel)
    assert dialog.settings()==c and dialog.wave_type.currentData()=='irregular' and dialog.convert.isChecked()
    panel.app.mesh=object();panel.app.session.read_only=True
    dialog.start();assert dialog.runner is None and 'read-only' in dialog.status.text()
    dialog.close();panel.close()


def test_irregular_apply_uses_atomic_command_and_compact_project(qapp,monkeypatch):
    import time
    import numpy as np
    from PySide6.QtCore import QThread,Signal
    from PySide6.QtWidgets import QLineEdit
    from anyfem import steel
    from anyfem.model import WetSurface
    from anyfem.document import DocumentSession,canonical_hash
    from anyfem.io.project_file import project_to_dict,project_from_dict
    from anymesher.serialize import mesh_to_dict
    import anyfem.ui.qt.wave_load as ui
    p=Project();p.add_material(steel());p.add_plate_section('plate',.01,'S355')
    g=p.geometry;vertices=g.add_points([(0,0,0),(1,0,0),(1,1,0),(0,1,0)])
    face=g.add_face(g.add_polyline(vertices,close=True));p.assign_plate(face,'plate')
    p.add_wet_surface(WetSurface(ref=p.face(face),fluid_side='positive_normal'))
    mesh=p.generate_mesh(1.);ids=tuple(sorted(mesh.elements_of_face[face]));n=len(ids)
    commands=[]
    class FastCalculation(QThread):
        completed=Signal(object);failed=Signal(str);progress_changed=Signal(str)
        def __init__(self,kwargs,parent=None):super().__init__(parent);self.kwargs=kwargs;self.cancelled=False
        def run(self):
            c=IrregularWaveInput(**self.kwargs['settings']);r=c.realization()
            h=SpectralMappedPressureHistory(c.times,ids,tuple(x.omega for x in r.components),
                ((1.,)*n,(2.,)*n),((0.,)*n,(.1,)*n),0.,canonical_hash(self.kwargs['mesh_data']),
                'b'*64,json.dumps(r.to_dict()),json.dumps(dict(settings=asdict(c))))
            self.completed.emit(dict(name=c.name,history=h.to_dict(),surfaces=[s.id for s in p.wet_surfaces],
                input_document_hash=canonical_hash(self.kwargs['document']),mesh_hash=canonical_hash(self.kwargs['mesh_data']),
                source_cache_path=None))
        def cancel(self):self.cancelled=True
        def shutdown(self):self.wait(1000)
    monkeypatch.setattr(ui,'WaveCalculation',FastCalculation)
    panel=panel_for(p);panel.case=QLineEdit()
    panel.app.mesh=mesh;panel.app.session=DocumentSession(p);panel.app.panels={};panel.app.path=None
    panel.app.set_status=lambda value:None
    def apply(command):commands.append(command);return command.do(p)
    panel.app.run=apply
    dialog=WaveLoadDialog(panel);dialog.wave_type.setCurrentIndex(1)
    dialog.inputs['component_count'].setText('2');dialog.start()
    deadline=time.monotonic()+10
    while (not commands or dialog.runner.isRunning()) and time.monotonic()<deadline:
        qapp.processEvents();time.sleep(.01)
    qapp.processEvents()
    assert len(commands)==1,dialog.status.text()
    data=project_to_dict(p);assert len(data['load_cases'])==1
    owner=next(c for c in p.load_cases.values() if c.parent is None)
    assert len(owner.wet_pressures)==1 and len(owner.times)==101
    h=owner.wet_pressures[0].history
    reopened=project_from_dict(data).load_cases[owner.name].wet_pressures[0].history
    np.testing.assert_array_equal(reopened.evaluate(.13),h.evaluate(.13))
    commands[0].undo(p);assert not p.load_cases
    commands[0].redo(p);assert len(project_to_dict(p)['load_cases'])==1
    dialog.close();panel.close()


def test_legacy_source_aware_edit_restores_actual_nondefault_parameters(qapp):
    import math,numpy as np
    from dataclasses import replace
    from anyloads.sea_state import WaveRealization
    from anyloads.marine import AiryComponent
    from test_pressure_remap import project_and_meshes,original_history
    from anyfem.application import pressure_transfer as bridge
    p,mesh,_=project_and_meshes();wrapped,bank,r=original_history(p,mesh,regular=True)
    alpha=.83*np.exp(-.61j);ratio=alpha/(.5*np.exp(.3j))
    original=wrapped.mapped_history
    phasors=(np.asarray(original.real)+1j*np.asarray(original.imag))*ratio
    mapped=replace(original,real=tuple(phasors.real),imag=tuple(phasors.imag),
        provenance='plain provider without JSON wave settings',content_sha256='')
    realization=WaveRealization((AiryComponent(.83,.7,phase=-.61),))
    retained=bridge.retain_pressure_source(mapped,bank,realization,p)
    owner=p.add_load_series('Recovered regular',retained.times)
    owner.wet_pressures.append(WetPressure(history=retained,mapping_key='element'))
    panel=panel_for(p);dialog=WaveLoadDialog(panel);actual=dialog.settings()
    assert actual.name=='Recovered regular' and actual.wave_type=='regular'
    assert actual.height==pytest.approx(1.66) and actual.period==pytest.approx(math.tau/.7)
    assert actual.phase==pytest.approx(math.degrees(-.61)) and actual.depth==bank.water_depth
    nonuniform=replace(mapped,times=(0.,.13,.31,1.),content_sha256='')
    unsupported=bridge.retain_pressure_source(nonuniform,bank,realization,p)
    with pytest.raises(bridge.PressureRemapError,match='nonuniform'):
        bridge.retained_regular_settings(unsupported)
    dialog.close();panel.close()
