"""Actual Prepare/save/offline re-gate; synthetic coherent families, no BEM."""
import numpy as np
import pytest
from floating_pipeline_helpers import case,settings,synthetic_snapshot,PANEL,fp,fsrc,ff,fa
from anyfesolver.floating_response import rigid_motion_world


def setup_history(case,tmp_path,monkeypatch):
    project,mesh,built,props,rec,comp,_=case
    comp=fp.FloatingComponent(omega=comp.omega,amplitude=1.,phase=np.pi/2,heading=comp.heading)
    pose=fp._pose(rec)
    target=fsrc.prepare_target_cells(project,mesh,pose)
    source=fsrc.prepare_source(project,mesh,pose,target,panel_size=PANEL)
    cache=tmp_path/'families';ff.save_family_snapshot(cache,synthetic_snapshot(source,rec,comp))
    def fields(snaps,source,Hq,evaluator):
        points=source.vertices[np.abs(source.vertices[:,2])<1e-12]
        dz=rigid_motion_world(points,rec['cg_world_m'],np.array(rec['rotation_body_to_world']),Hq[0])['displacement'][:,2]
        return dict(points=points,eta_total=np.array([dz+2.]))
    monkeypatch.setattr(fp,'_waterline_eta',fields)
    monkeypatch.setattr(fp,'_clearance_for_settings',lambda *args:.1)
    def no_bem(*args,**kwargs):pytest.fail('offline waterline revalidation initiated BEM')
    monkeypatch.setattr(ff,'solve_family_snapshot',no_bem)
    return project,mesh,built,props,rec,comp,cache


def test_saved_phase_admission_persists_and_old_gate_revalidates_offline(case,tmp_path,monkeypatch):
    p,m,b,props,rec,comp,cache=setup_history(case,tmp_path,monkeypatch)
    st=settings(comp,times=(0.,.01))
    out=fp.prepare_from_pose(p,m,st,rec,cache_dir=cache,built=b,props=props)
    assert out.diagnostics['waterline_envelope_m']==pytest.approx(2.)
    report=out.diagnostics['waterline_history']
    assert report['admitted'] and report['minimum_lower_bound_m']>-.1
    assert report['interval_s']==[0.,.01]
    from anyfem.application.floating_job import _summary
    assert _summary(out,tmp_path/'record.npz')['waterline_history']==report
    out.stage_keys['restoring_gate']='old-amplitude-sum-gate'
    path=fa.save_prepared(out,tmp_path/'legacy.npz')
    assert fa.stale_stage(out,props=props,mesh_id=fp._mesh_hash(m),settings=st,project=p)=='gates'
    reopened=fa.reopen_or_remap(p,m,st,path,cache_dir=tmp_path/'absent')
    assert reopened.stage_keys['restoring_gate']==fp.RESTORING_GATE_MARKER
    assert reopened.history.array_sha256==out.history.array_sha256
    assert reopened.diagnostics['waterline_history']==report


def test_actual_selected_interval_violation_refuses_at_production_boundary(case,tmp_path,monkeypatch):
    p,m,b,props,rec,comp,cache=setup_history(case,tmp_path,monkeypatch)
    with pytest.raises(fp.FloatingPrepareError,match='waterline excursion admission') as failure:
        fp.prepare_from_pose(p,m,settings(comp,times=(0.,4.)),rec,cache_dir=cache,built=b,props=props)
    assert failure.value.stage=='response'


def test_out_of_admission_times_refuse_apply_evaluation_acceleration_and_relief(case,tmp_path,monkeypatch):
    from anyfem.document import canonical_hash
    from anyfem.io.project_file import project_to_dict
    from anyfem.solve import floating_batch
    p,m,b,props,rec,comp,cache=setup_history(case,tmp_path,monkeypatch)
    st=settings(comp,times=(0.,.01))
    out=fp.prepare_from_pose(p,m,st,rec,cache_dir=cache,built=b,props=props)
    record=fa.save_prepared(out,tmp_path/'record.npz');before=canonical_hash(project_to_dict(p))
    with pytest.raises(ValueError,match='admitted interval'):
        fa.ApplyFloatingLoad(dict(name='unsafe',record_path=str(record),selected_times=[1.96],window=[1.96,1.96],
            array_sha256=out.history.array_sha256,input_document_hash=before,replace=False)).do(p)
    assert canonical_hash(project_to_dict(p))==before
    for action in (lambda:fa.floating_nodal_load(out.history,1.96),
                   lambda:fa.primary_acceleration_at(out,b.fe_model,1.96),
                   lambda:fa.floating_load_case(out.history,b.fe_model,1.96)):
        with pytest.raises(ValueError,match='admitted interval'):action()
    monkeypatch.setattr(floating_batch,'iter_residual_relief',lambda model,ids,frames,**kw:iter(tuple(frames)))
    with pytest.raises(ValueError,match='admitted interval'):
        list(fa.run_floating_relief(out,b.fe_model,[1.96]))


def test_cancelled_waterline_has_distinct_stage_and_publishes_nothing(case,tmp_path,monkeypatch):
    p,m,b,props,rec,comp,cache=setup_history(case,tmp_path,monkeypatch)
    from anyfesolver.floating_waterline import WaterlineCancelled
    def cancelled(*args,**kwargs):raise WaterlineCancelled('cancelled')
    monkeypatch.setattr(fp,'relative_waterline_history',cancelled)
    with pytest.raises(fp.FloatingPrepareError) as failure:
        fp.prepare_from_pose(p,m,settings(comp,times=(0.,.01)),rec,cache_dir=cache,built=b,props=props)
    assert failure.value.stage=='cancelled'
    assert not (tmp_path/'record.npz').exists()


def test_production_waterline_field_path_matches_incident_airy_phase(case):
    # Real ANYhydro field postprocessing with an analytic potential callback;
    # _waterline_eta itself is not patched and no BEM or field fit is performed.
    from scipy.optimize import brentq
    from floating_pipeline_helpers import G,DEPTH
    p,m,b,props,rec,comp,_=case
    pose=fp._pose(rec);target=fsrc.prepare_target_cells(p,m,pose)
    source=fsrc.prepare_source(p,m,pose,target,panel_size=PANEL)
    snap=synthetic_snapshot(source,rec,comp);wave_number=brentq(lambda k:G*k*np.tanh(k*DEPTH)-comp.omega**2,1e-9,1.)
    incident_points=[]
    def potential(snapshot,points,radiation_index):
        if radiation_index is not None:return np.zeros(len(points),complex)
        incident_points.append(points.copy())
        kx=wave_number*(np.cos(comp.heading)*points[:,0]+np.sin(comp.heading)*points[:,1])
        return -1j*G/comp.omega*np.exp(1j*kx)*np.cosh(wave_number*(points[:,2]+DEPTH))/np.cosh(wave_number*DEPTH)
    result=fp._waterline_eta([snap],source,np.zeros((1,6),complex),evaluator=potential)
    points=incident_points[-1];t=3.4
    phase=wave_number*(np.cos(comp.heading)*points[:,0]+np.sin(comp.heading)*points[:,1])+comp.phase-comp.omega*t
    actual=np.real(comp.amplitude*np.exp(1j*(comp.phase-comp.omega*t))*result['eta_total'][0])
    np.testing.assert_allclose(actual,comp.amplitude*np.cos(phase),atol=1e-13,rtol=1e-13)


def test_embedded_legacy_job_revalidates_without_bem_and_preserves_target_on_abort(case,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from anyfem.application.floating_job import reopen_floating_job
    from anyfem.io.project_file import project_to_dict
    from anymesher.serialize import mesh_to_dict
    p,m,b,props,rec,comp,cache=setup_history(case,tmp_path,monkeypatch)
    st=settings(comp,times=(0.,.01));out=fp.prepare_from_pose(p,m,st,rec,cache_dir=cache,built=b,props=props)
    out.stage_keys['restoring_gate']='legacy'
    payload=fa.prepared_to_bytes(out);path=tmp_path/'record.npz';path.write_bytes(b'original target')
    kwargs=dict(document=project_to_dict(p),mesh_data=mesh_to_dict(m),settings=st,record_path=str(path),record_payload=payload)
    summary=reopen_floating_job(**kwargs)
    back=fa.recover_prepared(path)
    assert back.stage_keys['restoring_gate']==fp.RESTORING_GATE_MARKER
    assert summary['array_sha256']==out.history.array_sha256 and back.history.array_sha256==out.history.array_sha256
    before=path.read_bytes()
    with pytest.raises(fp.FloatingPrepareError,match='cancelled'):
        reopen_floating_job(**kwargs,cancellation_token=SimpleNamespace(is_cancelled=True))
    assert path.read_bytes()==before
    monkeypatch.setattr(fp,'_clearance_for_settings',lambda *args:1e-6)
    with pytest.raises(fp.FloatingPrepareError,match='waterline'):
        reopen_floating_job(**kwargs)
    assert path.read_bytes()==before


def test_prepare_job_refusal_never_publishes(case,tmp_path,monkeypatch):
    from anyfem.application import floating_job as job
    from anyfem.io.project_file import project_to_dict
    from anymesher.serialize import mesh_to_dict
    p,m,b,props,rec,comp,cache=setup_history(case,tmp_path,monkeypatch)
    st=settings(comp,times=(0.,4.));path=tmp_path/'record.npz';path.write_bytes(b'existing target')
    def prepare(project,mesh,settings,cache_directory,**kwargs):
        return fp.prepare_from_pose(project,mesh,settings,rec,cache_dir=cache,built=b,props=props)
    monkeypatch.setattr(fp,'prepare_floating',prepare)
    with pytest.raises(fp.FloatingPrepareError,match='waterline'):
        job.prepare_floating_job(document=project_to_dict(p),mesh_data=mesh_to_dict(m),settings=st,
            cache_directory=str(cache),record_path=str(path))
    assert path.read_bytes()==b'existing target'


def test_true_legacy_binding_reopens_even_when_applied_series_is_named_default(case,tmp_path,monkeypatch):
    from dataclasses import replace
    from anyfem.application.floating_job import reopen_floating_job
    from anyfem.document import canonical_hash
    from anyfem.io.project_file import project_to_dict,project_from_dict
    from anymesher.serialize import mesh_to_dict
    from anyloads.floating_history import FloatingCoupledLoadHistory,ADMITTED_INTERVAL_KEY
    p,m,b,props,rec,comp,cache=setup_history(case,tmp_path,monkeypatch)
    st=settings(comp,times=(0.,.01));out=fp.prepare_from_pose(p,m,st,rec,cache_dir=cache,built=b,props=props)
    binding=dict(out.history.binding);binding.pop(ADMITTED_INTERVAL_KEY)
    legacy=replace(out,history=replace(out.history,binding=binding,content_sha256=''),
                   stage_keys={**out.stage_keys,'restoring_gate':'legacy'})
    payload=fa.prepared_to_bytes(legacy);path=tmp_path/'record.npz';fa.save_prepared(out,path)
    project=project_from_dict(project_to_dict(p))
    parent=fa.ApplyFloatingLoad(dict(name='default',record_path=str(path),selected_times=[0.,.01],window=[0.,.01],
        array_sha256=out.history.array_sha256,input_document_hash=canonical_hash(project_to_dict(project)),replace=False)).do(project)
    parent.wet_pressures[0]=replace(parent.wet_pressures[0],history=FloatingCoupledLoadHistory.from_payload(
        payload,times=[0.,.01],selected_window=[0.,.01]))
    reopen_floating_job(document=project_to_dict(project),mesh_data=mesh_to_dict(m),settings=st,
        record_path=str(path),record_payload=payload)
    back=fa.recover_prepared(path)
    assert ADMITTED_INTERVAL_KEY in back.history.binding
    assert back.history.array_sha256!=legacy.history.array_sha256
    for contribution in ('pressure','weight','inertia'):
        np.testing.assert_array_equal(back.history.loads[contribution],legacy.history.loads[contribution])
    np.testing.assert_array_equal(back.history.static_load,legacy.history.static_load)
