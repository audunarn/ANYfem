from dataclasses import replace,asdict
from pathlib import Path
import numpy as np
import pytest
from anyfem.application.hydro_wave_input import RegularWaveInput,source_for_settings,compute_wave_input
from anyfem.io.project_file import load_project,project_to_dict
from anyloads.pressure_history import MappedPressureHistory,pressure_history_from_dict
import json

ROOT=Path('C:/ANYworkspaceWork/barge-pressure-pipeline-20261026/mapped-verified')


def fixture():
    if not (ROOT/'prepared-source.npz').is_file():pytest.skip('retained source fixture unavailable')
    return ROOT/'prepared-source.npz'


def test_height_and_phase_edits_reuse_source_but_period_edit_requires_new_field(monkeypatch):
    cache=fixture();base=RegularWaveInput()
    source,_,reused=source_for_settings(replace(base,height=2.,phase=45.,duration=2.,dt=.2),cache)
    assert reused and source.omega==2*np.pi/6.
    import anyhydro.regular_wave_pressure as hydro
    calls=[]
    def solve(*args,**kwargs):calls.append(kwargs);return 'new frequency field'
    monkeypatch.setattr(hydro,'solve_regular_wave_pressure',solve)
    new,_,reused=source_for_settings(replace(base,period=7.),cache)
    assert new=='new frequency field' and not reused and calls[0]['period']==7.


def test_height_edit_real_mapping_and_mutated_cache_refusal(tmp_path):
    cache=fixture()
    project=load_project(ROOT/'prepared-geometry.anyfem')
    mesh_data=json.loads((ROOT/'prepared-mesh.json').read_text())
    result=compute_wave_input(document=project_to_dict(project),mesh_data=mesh_data,
        settings=asdict(RegularWaveInput(height=2.)),cache_path=str(cache))
    history=pressure_history_from_dict(result['history'])
    old=np.load(ROOT/'pressure-mapping.npz')
    assert np.allclose(np.array([history.evaluate(t) for t in history.times]),2.*old['pressures'],rtol=1e-13,atol=1e-8)
    with np.load(cache) as data:bad={k:data[k] for k in data.files}
    bad['pressure']=bad['pressure']*1.01
    badfile=tmp_path/'changed.npz';np.savez(badfile,**bad)
    with pytest.raises(ValueError,match='producer identity'):
        source_for_settings(RegularWaveInput(),badfile)


@pytest.mark.parametrize('change',[{'height':-1},{'period':0},{'dt':0},{'duration':float('nan')},{'wave_type':'unavailable'}])
def test_invalid_inputs_refuse(change):
    with pytest.raises(ValueError):RegularWaveInput(**change)
