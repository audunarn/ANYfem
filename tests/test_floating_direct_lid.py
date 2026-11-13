"""Explicit persisted owner route; no native solve during settings/reopen identities."""
from dataclasses import replace, asdict
import hashlib,json
import numpy as np
import pytest
from anyfem.application import floating_prepare as fp
from anyhydro import floating_radiation_fields as ff
from anyhydro.direct_lid_profile import DirectLidProfile


@pytest.fixture(autouse=True)
def neutral_profile_contract(monkeypatch):
    monkeypatch.setattr('anyhydro.direct_lid_profile.verify_canonical_profile',lambda *a,**k:None)


def fixture():
    v=np.array([[0,0,-1],[0,1,-1],[1,1,-1],[1,0,-1.]],float);f=np.array([[0,1,2,3]])
    pose=dict(rotation_body_to_world=np.eye(3).tolist(),cg_world_m=[0,0,-.4])
    class Source:
        vertices=v;faces=f;surface_ids=('hull',)
    settings=fp.FloatingSettings(components=(fp.FloatingComponent(.8,1.,0.,0.),),times=(0.,1.),water_depth=50.)
    hull=fp.family_requests(settings,Source(),pose)[0]
    lid=v.copy();lid[:,2]=-.1
    profile=DirectLidProfile(hull['geometry'],lid,f,np.vstack([v,lid]),np.vstack([f,f+4]),np.array([True,False]),-.1,2.)
    return settings,Source(),pose,profile


def test_absent_profile_keeps_prior_settings_digest():
    settings,_,_,_=fixture();fields=asdict(settings);fields.pop('source_profile');fields.pop('direct_lid_profile')
    assert settings.digest()==hashlib.sha256(json.dumps(fields,sort_keys=True,default=str).encode()).hexdigest()


def test_explicit_lid_request_binding_and_offline_missing_count(tmp_path):
    settings,source,pose,p=fixture();new=replace(settings,direct_lid_profile=p.to_dict())
    request=fp.family_requests(new,source,pose)[0]
    assert request['recipe']==ff.DIRECT_LID_RECIPE
    assert request['direct_lid_profile']['profile_hash']==p.content_hash()
    assert new.digest()!=settings.digest()
    assert new.realization_digest()==settings.realization_digest()
    assert fp.missing_solve_count(tmp_path,new,source,pose)==7
    with pytest.raises(fp.FloatingPrepareError,match='missing'):
        fp.collect_snapshots(tmp_path,new,source,pose)
    with pytest.raises(fp.FloatingPrepareError,match='cancelled'):
        fp.collect_snapshots(tmp_path,new,source,pose,cancel=lambda:True)


def test_saved_lid_is_not_a_different_physical_hull():
    settings,source,pose,p=fixture();new=replace(settings,direct_lid_profile=p.to_dict())
    source.vertices=source.vertices+np.array([.01,0,0])
    with pytest.raises(ff.WaveFieldError,match='different hull'):fp.family_requests(new,source,pose)


def test_stale_profile_and_dialog_restore_preserve_explicit_selection(monkeypatch):
    from types import SimpleNamespace
    from anyfem.application import floating_apply as fa
    settings,_,_,p=fixture();settings=replace(settings,direct_lid_profile=p.to_dict(),wave_input={'seed':0})
    props=SimpleNamespace(mass=10.,cg=np.zeros(3),inertia_cg=np.eye(3))
    keys=dict(mass_numeric=dict(mass=10.,cg=[0,0,0],inertia=np.eye(3).tolist()),mesh='mesh',
              realization=settings.realization_digest(),equilibrium_controls=json.dumps(settings.equilibrium_controls,sort_keys=True,default=str),
              restoring_gate=fp.RESTORING_GATE_MARKER)
    prepared=SimpleNamespace(stage_keys=keys,settings=asdict(settings))
    assert fa.stale_stage(prepared,props=props,mesh_id='mesh',settings=replace(settings,direct_lid_profile=None))=='source'
    changed=p.to_dict();changed['faces_max_radius']=1.
    assert fa.stale_stage(prepared,props=props,mesh_id='mesh',settings=replace(settings,direct_lid_profile=changed))=='source'
    monkeypatch.setattr(fa,'recover_prepared',lambda payload:prepared)
    restored=fa.restore_dialog_settings(SimpleNamespace(payload=b'test'))
    assert restored['floating']['direct_lid_profile']==p.to_dict()
    assert restored['wave']=={'seed':0}


def test_offline_record_binds_selected_lid_to_retained_family_before_reopen(monkeypatch):
    from types import SimpleNamespace
    from anyfem.application import floating_apply as fa
    monkeypatch.setattr(ff,'validate_family_coherence',lambda snapshots:None)
    settings,source,pose,p=fixture();settings=replace(settings,direct_lid_profile=p.to_dict())
    request=fp.family_requests(settings,source,pose)[0]
    snapshots=[SimpleNamespace(request=request,omega=.8)]
    fa._verify_lid_record(asdict(settings),source,pose,snapshots)
    changed=asdict(settings);changed['direct_lid_profile']['faces_max_radius']=1.
    with pytest.raises(fp.FloatingPrepareError,match='selection'):fa._verify_lid_record(changed,source,pose,snapshots)
    removed=asdict(settings);removed.pop('direct_lid_profile')
    with pytest.raises(fp.FloatingPrepareError,match='selection'):fa._verify_lid_record(removed,source,pose,snapshots)
    with pytest.raises(fp.FloatingPrepareError,match='selection'):fa._verify_lid_record(asdict(settings),source,pose,[])
    legacy=fp.family_requests(replace(settings,direct_lid_profile=None),source,pose)[0]
    with pytest.raises(fp.FloatingPrepareError,match='selection'):
        fa._verify_lid_record(asdict(settings),source,pose,[SimpleNamespace(request=legacy,omega=.8)])
    with pytest.raises(fp.FloatingPrepareError,match='selection'):
        fa._verify_lid_record(asdict(settings),source,pose,[SimpleNamespace(request=request,omega=.9)])


def test_real_shared_record_roundtrip_legacy_expansion_and_apply_solve_refusal(tmp_path,monkeypatch):
    import io
    from anyloads.floating_history import FloatingCoupledLoadHistory,BINDING_KEYS
    from anyfem.application import floating_apply as fa,floating_source as fs
    settings,source,pose,p=fixture();settings=replace(settings,direct_lid_profile=p.to_dict())
    v,f=source.vertices,source.faces
    src=fs.SourceManifest(v,f,source.surface_ids,(v[f[0]],),np.ones(1),np.array([[.5,.5,-1.]]),np.array([[0,0,-1.]]),{})
    request=fp.family_requests(settings,src,pose)[0]
    phi=np.ones((6,2),complex);zero=np.zeros((6,2),complex)
    snap=ff.FloatingFamilySnapshot(request,.8,v,f,source.surface_ids,zero,zero[0],np.eye(6),np.eye(6),np.ones(6,complex),
        dict(method='direct'),panel_pressure_radiation=1j*.8*1025*phi[:,:1],panel_pressure_incident_diffraction=np.ones(1,complex),
        centers=src.centers,normals=src.normals,areas=src.areas,full_potential_radiation=phi,full_potential_diffraction=phi[0])
    history=FloatingCoupledLoadHistory([.8],[1.],[0.],{k:np.zeros((1,1,6),complex) for k in ('pressure','weight','inertia')},
        np.zeros((1,6)),(1,),'a'*64,{key:'fixture' for key in BINDING_KEYS})
    prepared=fp.FloatingPrepared(history,np.zeros((1,6),complex),pose,dict(source=src.digest(),families=[snap.snapshot_id]),
        {},10.,(0,0,0),src,(snap,),asdict(settings),(0,))
    payload=fa.prepared_to_bytes(prepared);back=fa.recover_prepared(payload)
    assert back.snapshots[0].snapshot_id==snap.snapshot_id
    assert back.settings['direct_lid_profile']==p.to_dict()
    with np.load(io.BytesIO(payload),allow_pickle=False) as data:
        arrays={k:np.array(data[k]) for k in data.files}
    meta=json.loads(str(arrays['meta']))
    assert meta['direct_lid_profile_storage']=='shared_v1' and len(meta['direct_lid_profiles'])==1
    assert set(meta['settings']['direct_lid_profile'])=={'profile_ref'}
    expanded=fa._expand_lid_profiles(json.loads(str(arrays['meta'])))
    expanded.pop('direct_lid_profiles');expanded.pop('direct_lid_profile_storage')
    stream=io.BytesIO();np.savez(stream,**dict(arrays,meta=json.dumps(expanded)))
    assert fa.recover_prepared(stream.getvalue()).snapshots[0].snapshot_id==snap.snapshot_id
    # Keep the genuine full arrays/request, drop only the saved selection.
    meta['settings'].pop('direct_lid_profile');stream=io.BytesIO();np.savez(stream,**dict(arrays,meta=json.dumps(meta)))
    forged=stream.getvalue()
    with pytest.raises(fp.FloatingPrepareError,match='selection'):fa._validate_lid_payload(forged)
    record=tmp_path/'forged.npz';record.write_bytes(forged)
    monkeypatch.setattr(fa,'canonical_hash',lambda *a:'input')
    monkeypatch.setattr(fa,'project_to_dict',lambda *a:{})
    with pytest.raises(fp.FloatingPrepareError,match='selection'):
        fa.ApplyFloatingLoad(dict(input_document_hash='input',record_path=str(record))).do(object())
    badhistory=replace(history,payload=forged,content_sha256='')
    monkeypatch.setattr(fa,'require_current_mass',lambda *a:pytest.fail('invalid payload reached mass/solve gate'))
    with pytest.raises(fp.FloatingPrepareError,match='selection'):fa.require_current_binding(badhistory,object())
