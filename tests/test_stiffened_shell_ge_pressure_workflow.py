"""Installed-capable complete consumer path with real disk persistence."""
import json
from pathlib import Path
import runpy
import pytest
from types import SimpleNamespace
from anyfem.model.records import AnalysisDefinition
from anyfem.io.project_file import load_project
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis


def test_pressure_project_cancel_disk_restart_and_continue(tmp_path,monkeypatch):
    examples=Path(__file__).resolve().parents[1]/'examples'
    monkeypatch.syspath_prepend(str(examples))
    result=runpy.run_path(str(examples/'stiffened_shell_ge_pressure_consumer.py'))['workflow'](tmp_path)
    saved=json.loads((tmp_path/'summary.json').read_text(encoding='utf-8'))
    assert saved['status']=='EXPERIMENTAL_NOT_QUALIFIED' and not saved['production_qualified']
    assert saved['prefix_status']=='accepted_prefix'
    assert saved['final_status']==saved['resumed_status']==saved['linear_status']=='completed'
    assert saved['accepted_factors']==[.25,.5,1.]
    assert saved['applied_force_N'][2]==pytest.approx(200.,rel=1e-6)
    assert saved['total_potential'] is None and saved['restart_identical']
    project=load_project(tmp_path/'pressure.anyfem');record,=project.analyses.values()
    panel=point_panel_from_analysis(record)
    assert panel.actions==() and panel.load_programme is None and panel.body_load is None
    assert panel.pressure_load.identity==saved['load_identity']
    assert panel.pressure_load.model_identity==saved['model_identity']
    assert [b.sha256 for b in panel.beams]==saved['native_beam_sha256']
    assert tuple(a.element_id for a in panel.pressure_load.actions)==(1,2)
    assert all(a.pressure_pa==100. and a.configuration=='current_surface' for a in panel.pressure_load.actions)
    assert (tmp_path/'prefix.checkpoint').read_bytes()==(tmp_path/'restored-prefix.checkpoint').read_bytes()
    assert (tmp_path/'direct-final.checkpoint').read_bytes()==(tmp_path/'resumed-final.checkpoint').read_bytes()
    rows=json.loads((tmp_path/'observations.json').read_text(encoding='utf-8'))
    assert [r['factor'] for r in rows if r['branch']=='prefix']==[.25]
    for branch in ('direct','resumed'):assert [r['factor'] for r in rows if r['branch']==branch]==[.5,1.]
    assert all(r['equilibrium']<=1e-8 and r['compatibility']<=1e-9 for r in rows)


def test_workflow_second_owner_creation_failure_closes_first(tmp_path,monkeypatch):
    examples=Path(__file__).resolve().parents[1]/'examples'
    monkeypatch.syspath_prepend(str(examples))
    workflow=runpy.run_path(str(examples/'stiffened_shell_ge_pressure_consumer.py'))['workflow']
    monkeypatch.setitem(workflow.__globals__,'solve_point_panel',lambda record:
        SimpleNamespace(status='completed',pressure_load=point_panel_from_analysis(record).pressure_load))
    calls=[];closed=[];failure=RuntimeError('second owner creation failed')
    def create(self):
        calls.append(self.id)
        if len(calls)==2:raise failure
        return SimpleNamespace(close=lambda:closed.append(True))
    monkeypatch.setattr(AnalysisDefinition,'create_point_panel_analysis',create)
    with pytest.raises(RuntimeError) as caught:workflow(tmp_path)
    assert caught.value is failure and len(calls)==2 and closed==[True]
