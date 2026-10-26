from dataclasses import replace
import json,math
import numpy as np
import pytest
from anyfem import Project,steel
from anyfem.model import WetPressure,WetSurface
from anyfem.document import canonical_hash
from anyfem.io.project_file import project_to_dict,project_from_dict
from anyfem.application import pressure_transfer as bridge
from anyfem.application.hydro_wave_pressure import _source_mesh,target_hull_cells
from anyhydro.pressure_bank import PressureResponseBank
from anyloads.spectral_pressure_history import SpectralMappedPressureHistory,build_pressure_mapping_plan,map_pressure_bank
from anyloads.pressure_history import MappedPressureHistory,pressure_history_from_dict
from anyloads.source_pressure_history import SourceAwarePressureHistory
from anyloads.sea_state import WaveRealization
from anyloads.marine import AiryComponent
from anymesher import Mesh
from anymesher.serialize import mesh_to_dict


def project_and_meshes():
    p=Project();p.add_material(steel());p.add_plate_section('plate',.01,'S355')
    rectangles={
        'bottom':((0,-2.5,0),(0,5,0),(60,0,0)),
        'port':((0,-2.5,0),(60,0,0),(0,0,1.5)),
        'starboard':((0,2.5,0),(0,0,1.5),(60,0,0)),
        'end0':((0,-2.5,0),(0,0,1.5),(0,5,0)),
        'end60':((60,-2.5,0),(0,5,0),(0,0,1.5))}
    faces={}
    for name,(origin,u,v) in rectangles.items():
        origin,u,v=map(np.array,(origin,u,v));points=[origin,origin+u,origin+u+v,origin+v]
        vertices=p.geometry.add_points(points)
        face=p.geometry.add_face(p.geometry.add_polyline(vertices,close=True));faces[name]=face
        p.assign_plate(face,'plate')
        p.add_wet_surface(WetSurface(ref=p.face(face),name=name,fluid_side='away_from_point',interior_point=(30,0,1)))
    def mesh(divisions,start):
        m=Mesh();nid=start;eid=start
        for name,(origin,u,v) in rectangles.items():
            origin,u,v=map(np.array,(origin,u,v));elements=[]
            for i in range(divisions):
                points=[origin+u*s+v*t for s,t in ((i/divisions,0),((i+1)/divisions,0),((i+1)/divisions,1),(i/divisions,1))]
                nodeids=tuple(range(nid,nid+4));nid+=4
                for node,point in zip(nodeids,points):m.nodes[node]=point.astype(float)
                m.quads[eid]=nodeids;elements.append(eid);eid+=1
            m.elements_of_face[faces[name]]=elements
        return m
    return p,mesh(1,10),mesh(2,100)


def source_bank(k=2):
    v,f,labels=_source_mesh();polygons=v[f]
    av=np.cross(polygons[:,1]-polygons[:,0],polygons[:,2]-polygons[:,0])/2
    av+=np.cross(polygons[:,2]-polygons[:,0],polygons[:,3]-polygons[:,0])/2
    areas=np.linalg.norm(av,axis=1);centers=polygons.mean(axis=1)
    pressure=(centers[:,0]+3)+1j*(centers[:,1]+4)
    return PressureResponseBank(v,f,labels,centers,av/areas[:,None],areas,[.7,1.1][:k],
        np.stack([pressure*(i+1j) for i in range(1,k+1)]))


def original_history(project,mesh,*,regular=False):
    bank=source_bank(1 if regular else 2);exterior={s.name:s.ref.id for s in project.wet_surfaces}
    cells=target_hull_cells(project,mesh,exterior);meshhash=canonical_hash(mesh_to_dict(mesh))
    plan=build_pressure_mapping_plan(bank,*cells,mesh_content_sha256=meshhash)
    fields,_=map_pressure_bank(bank,plan,*cells[1:4],surface_ids=cells[4],mesh_content_sha256=meshhash)
    r=WaveRealization(tuple(AiryComponent(.5,w,phase=.3) for w in bank.omegas))
    times=tuple(np.arange(101)/10)
    if regular:
        phasors=fields[0]*.5*np.exp(.3j)
        mapped=MappedPressureHistory(times,tuple(cells[0]),tuple(phasors.real),tuple(phasors.imag),.7,meshhash,bank.bank_id)
    else:
        mapped=SpectralMappedPressureHistory(times,tuple(cells[0]),tuple(bank.omegas),fields.real,fields.imag,
            bank.heading,meshhash,bank.bank_id,json.dumps(r.to_dict()))
    return bridge.retain_pressure_source(mapped,bank,r,project),bank,r


@pytest.mark.parametrize('regular',[True,False])
def test_source_aware_two_real_target_meshes_preview_once_and_persistence(regular,monkeypatch):
    from anyfem.solve.build import wet_element_pressures
    import anyloads.spectral_pressure_history as spectral
    p,old,new=project_and_meshes();history,bank,r=original_history(p,old,regular=regular)
    load=WetPressure(history=history,mapping_key='element',name='keep-name',scale=2.)
    owner=p.add_load_series('wave',history.times);owner.wet_pressures.append(load)
    old_project=canonical_hash(project_to_dict(p))
    bridge._CACHE.clear();bridge._CACHE_BYTES=0;calls=[];build=spectral.build_pressure_mapping_plan
    monkeypatch.setattr(spectral,'build_pressure_mapping_plan',lambda *a,**kw:(calls.append(1) or build(*a,**kw)))
    for time in history.times:
        effective=bridge.effective_wave_pressure(p,old,load)
        np.testing.assert_array_equal(effective.history.evaluate(time),history.evaluate(time))
    for time in history.times:
        current=bridge.effective_wave_pressure(p,new,load)
        assert current.mesh_content_sha256==canonical_hash(mesh_to_dict(new))
        assert current.id==load.id and current.name==load.name and current.scale==load.scale
    assert len(calls)==1 and current.history.element_ids!=history.element_ids
    cells=target_hull_cells(p,new,{s.name:s.ref.id for s in p.wet_surfaces})
    expected=[]
    for center,label in zip(cells[1],cells[4]):
        candidates=np.flatnonzero(np.array(bank.surface_ids)==label)
        ix=candidates[np.argmin(np.linalg.norm(bank.centers[candidates]-center,axis=1))]
        expected.append(sum(np.real(bank.responses[j,ix]*c.amplitude*np.exp(1j*(c.phase-c.omega*.31))) for j,c in enumerate(r.components)))
    np.testing.assert_allclose(current.history.evaluate(.31),expected,atol=1e-11)
    case=p.resolve_case(p.series_children('wave')[3]);records=wet_element_pressures(p,new,case)
    lookup=dict(zip(current.history.element_ids,current.history.evaluate(case.time)));outward=dict(zip(cells[0],cells[2]))
    for record in records:
        np.testing.assert_allclose(record.pressure*record.normal,-2*lookup[record.element_id]*np.array(outward[record.element_id]),atol=1e-11)
    assert len(calls)==1 and canonical_hash(project_to_dict(p))==old_project
    reopened=project_from_dict(project_to_dict(p));retained=reopened.load_cases['wave'].wet_pressures[0]
    assert isinstance(retained.history,SourceAwarePressureHistory)
    again=bridge.effective_wave_pressure(reopened,new,retained)
    np.testing.assert_array_equal(again.history.evaluate(.31),current.history.evaluate(.31))
    # Additive wrapper keeps original persisted v1 payload untouched.
    assert pressure_history_from_dict(history.to_dict()).mapped_history.to_dict()==history.mapped_history.to_dict()
    data=history.to_dict();data['source_payload_json']=data['source_payload_json'].replace('Pa/m','kPa/m')
    with pytest.raises(ValueError):pressure_history_from_dict(data)
    changed=replace(p.wet_surfaces[0],fluid_side='positive_normal',interior_point=None);p.wet_surfaces[0]=changed
    with pytest.raises(bridge.PressureRemapError):bridge.effective_wave_pressure(p,new,load)


def test_unbound_wrong_target_still_refuses_and_owner_record_roundtrip():
    from anyfem.solve.build import wet_element_pressures
    p,old,new=project_and_meshes();history,bank,r=original_history(p,old,regular=True)
    np.testing.assert_array_equal(PressureResponseBank.from_dict(bank.to_dict()).responses,bank.responses)
    owner=p.add_load_series('wave',history.times)
    owner.wet_pressures.append(WetPressure(history=history.mapped_history,mapping_key='element'))
    case=p.resolve_case(p.series_children('wave')[0])
    with pytest.raises(ValueError,match='regenerate wave input'):wet_element_pressures(p,new,case)


def test_legacy_exact_coefficient_recovery_and_refusal_without_mutation():
    from types import SimpleNamespace
    p,old,new=project_and_meshes();wrapped,bank,r=original_history(p,old,regular=True)
    history=replace(wrapped.mapped_history,provenance='plain provider without wave settings',content_sha256='')
    source=SimpleNamespace(vertices=bank.vertices,faces=bank.faces,surface_ids=bank.surface_ids,
        centers=bank.centers,normals=bank.normals,areas=bank.areas,pressure=bank.responses[0],
        omega=.7,heading=0.,water_depth=bank.water_depth,rho=bank.rho,source_id=history.source_id,
        provider='synthetic validated test source',validate=lambda:None)
    before=canonical_hash(project_to_dict(p))
    recovered=bridge.recover_legacy_history(p,history,source,old)
    certificate=json.loads(recovered.domain_json)['recovery_certificate']
    np.testing.assert_allclose(certificate['alpha_real']+1j*certificate['alpha_imag'],.5*np.exp(.3j),atol=1e-15)
    assert certificate['residual_max']<=certificate['roundoff_bound']
    for time in history.times:np.testing.assert_array_equal(recovered.evaluate(time),history.evaluate(time))
    effective=bridge.effective_wave_pressure(p,new,WetPressure(history=recovered,mapping_key='element'))
    assert effective.mesh_content_sha256==canonical_hash(mesh_to_dict(new))
    bad=replace(history,real=(history.real[0]+.1,)+history.real[1:],content_sha256='')
    with pytest.raises(bridge.PressureRemapError,match='do not certify'):bridge.recover_legacy_history(p,bad,source,old)
    with pytest.raises(bridge.PressureRemapError,match='mesh identity'):bridge.recover_legacy_history(p,history,source,new)
    source.source_id='wrong'
    with pytest.raises(bridge.PressureRemapError,match='lineage'):bridge.recover_legacy_history(p,history,source,old)
    assert canonical_hash(project_to_dict(p))==before


def test_path_aware_upgrade_is_atomic_and_new_save_needs_no_sidecar(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from anyfem.model.records import MeshRecord,ArtifactRef
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.project_file import save_project,load_project
    from anyfem.solve.build import wet_element_pressures
    p,old,new=project_and_meshes();wrapped,bank,r=original_history(p,old,regular=True)
    history=wrapped.mapped_history
    owner=p.add_load_series('legacy',history.times)
    original_load=WetPressure(history=history,mapping_key='element',name='preserve')
    owner.wet_pressures.append(original_load)
    artifact=ArtifactRef('mesh','meshes/old.anymesh.h5')
    record=MeshRecord('old','','',history.mesh_content_sha256,artifact_id=artifact.id)
    p.artifacts[artifact.id]=artifact;p.mesh_records[record.id]=record
    source=SimpleNamespace(vertices=bank.vertices,faces=bank.faces,surface_ids=bank.surface_ids,
        centers=bank.centers,normals=bank.normals,areas=bank.areas,pressure=bank.responses[0],omega=.7,
        heading=0.,water_depth=bank.water_depth,rho=bank.rho,source_id=history.source_id,
        provider='synthetic validated test source',validate=lambda:None)
    monkeypatch.setattr(bridge,'_legacy_source',lambda *args:(source,'test-source-digest'))
    monkeypatch.setattr(ArtifactStore,'read_mesh',lambda *args:old)
    sidecar=tmp_path/'prepared-source.npz';sidecar.write_bytes(b'mock validated through explicit loader')
    original=project_to_dict(p);failed=project_from_dict(original);source.source_id='wrong'
    before=canonical_hash(project_to_dict(failed))
    bridge.upgrade_legacy_wave_sources(failed,tmp_path/'legacy.anyfem')
    assert canonical_hash(project_to_dict(failed))==before
    assert type(failed.load_cases['legacy'].wet_pressures[0].history) is MappedPressureHistory
    with pytest.raises(ValueError,match='regenerate wave input'):
        wet_element_pressures(failed,new,failed.resolve_case(failed.series_children('legacy')[0]))
    source.source_id=history.source_id
    bridge.upgrade_legacy_wave_sources(p,tmp_path/'legacy.anyfem')
    upgraded=p.load_cases['legacy'].wet_pressures[0]
    assert isinstance(upgraded.history,SourceAwarePressureHistory) and upgraded.id==original_load.id
    sidecar.unlink()
    saved=save_project(p,tmp_path/'self-contained.anyfem');reopened=load_project(saved)
    retained=reopened.load_cases['legacy'].wet_pressures[0]
    effective=bridge.effective_wave_pressure(reopened,new,retained)
    assert effective.mesh_content_sha256==canonical_hash(mesh_to_dict(new))
    for time in history.times:np.testing.assert_array_equal(retained.history.evaluate(time),history.evaluate(time))


def test_actual_malformed_legacy_npz_preserves_old_load_and_refuses_new_mesh(tmp_path):
    from anyfem.solve.build import wet_element_pressures
    from anyfem.io.project_file import save_project,load_project
    p,old,new=project_and_meshes();wrapped,bank,r=original_history(p,old,regular=True)
    history=wrapped.mapped_history;owner=p.add_load_series('legacy',history.times)
    owner.wet_pressures.append(WetPressure(history=history,mapping_key='element'))
    original_load=owner.wet_pressures[0];before=canonical_hash(project_to_dict(p))
    # Actual truncated ZIP-shaped NPZ: np.load enters its ZIP decoder and raises
    # BadZipFile, rather than the ValueError raised for an arbitrary text file.
    source=tmp_path/'prepared-source.npz';source.write_bytes(b'PK\x03\x04'+b'truncated-npz-payload')
    with pytest.raises(bridge.PressureRemapError,match='malformed'):
        bridge._legacy_source(source,history.source_id)
    bridge.upgrade_legacy_wave_sources(p,tmp_path/'legacy.anyfem')
    assert canonical_hash(project_to_dict(p))==before
    assert owner.wet_pressures[0] is original_load
    assert 'malformed' in p._wave_source_recovery_error
    path=save_project(p,tmp_path/'legacy.anyfem');original_bytes=path.read_bytes()
    reopened=load_project(path)
    assert path.read_bytes()==original_bytes
    assert reopened.load_cases['legacy'].wet_pressures[0].history.to_dict()==history.to_dict()
    assert 'malformed' in reopened._wave_source_recovery_error
    case=reopened.resolve_case(reopened.series_children('legacy')[0])
    assert wet_element_pressures(reopened,old,case)
    with pytest.raises(ValueError,match='regenerate wave input'):
        wet_element_pressures(reopened,new,case)


def test_plain_history_preview_and_optional_legacy_load_without_anyhydro(tmp_path,monkeypatch):
    import builtins
    from anyfem.solve.build import wet_element_pressures
    from anyfem.io.project_file import save_project,load_project
    p,old,new=project_and_meshes();wrapped,bank,r=original_history(p,old,regular=True)
    history=wrapped.mapped_history;owner=p.add_load_series('legacy',history.times)
    owner.wet_pressures.append(WetPressure(history=history,mapping_key='element'))
    path=save_project(p,tmp_path/'legacy.anyfem')
    (tmp_path/'prepared-source.npz').write_bytes(b'optional unrelated sidecar')
    original_import=builtins.__import__;blocked=[]
    def without_owner(name,*args,**kwargs):
        if name=='anyhydro' or name.startswith('anyhydro.'):
            blocked.append(name);raise ImportError('optional owner intentionally unavailable')
        return original_import(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',without_owner)
    case=p.resolve_case(p.series_children('legacy')[0])
    assert wet_element_pressures(p,old,case)
    assert blocked==[]  # Ordinary v1 evaluation must not even try this import.
    reopened=load_project(path)
    assert blocked and 'optional ANYhydro data reader' in reopened._wave_source_recovery_error
    assert reopened.load_cases['legacy'].wet_pressures[0].history.to_dict()==history.to_dict()
    assert wet_element_pressures(reopened,old,reopened.resolve_case(reopened.series_children('legacy')[0]))
    with pytest.raises(bridge.PressureRemapError,match='optional ANYhydro data reader'):
        bridge.effective_wave_pressure(p,new,WetPressure(history=wrapped,mapping_key='element'))
