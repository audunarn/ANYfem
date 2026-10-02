"""Actual native Project persistence and accepted body-load restart."""
import json
from pathlib import Path
import runpy
from anyfem.io.project_file import load_project
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis


def test_public_gravity_project_roundtrip_cancellation_and_restart(tmp_path):
    example=Path(__file__).resolve().parents[1]/'examples/stiffened_shell_ge_gravity_consumer.py'
    result=runpy.run_path(str(example))['workflow'](tmp_path)
    saved=json.loads((tmp_path/'summary.json').read_text(encoding='utf-8'))
    assert saved['status']=='EXPERIMENTAL_NOT_QUALIFIED' and not saved['production_qualified']
    assert saved['prefix_status']=='accepted_prefix'
    assert saved['final_status']==saved['resumed_status']==saved['linear_status']=='completed'
    assert saved['accepted_factors']==[.25,.5,1.]
    assert saved['mass_kg']==1256. and saved['restart_identical']
    assert saved['applied_force_N']==[0.,0.,-12321.36]
    project=load_project(tmp_path/'gravity.anyfem');record,=project.analyses.values()
    panel=point_panel_from_analysis(record)
    assert panel.actions==() and panel.load_programme is None
    assert panel.body_load.identity==saved['body_identity']
    assert panel.body_load.model_identity==saved['model_identity']
    assert panel.body_load.acceleration_m_s2==(0.,0.,-9.81)
    assert [b.sha256 for b in panel.beams]==saved['native_beam_sha256']
    assert (tmp_path/'prefix.checkpoint').read_bytes()==(tmp_path/'restored-prefix.checkpoint').read_bytes()
    assert (tmp_path/'direct-final.checkpoint').read_bytes()==(tmp_path/'resumed-final.checkpoint').read_bytes()
    assert (tmp_path/'prefix.checkpoint').read_bytes()!=(tmp_path/'direct-final.checkpoint').read_bytes()
    rows=json.loads((tmp_path/'observations.json').read_text(encoding='utf-8'))
    assert [r['factor'] for r in rows if r['branch']=='prefix']==[.25]
    for branch in ('direct','resumed'):
        assert [r['factor'] for r in rows if r['branch']==branch]==[.5,1.]
    assert all(r['equilibrium']<=1e-8 and r['compatibility']<=1e-9 for r in rows)
