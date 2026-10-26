from dataclasses import asdict,replace
import json
import math
import numpy as np
import pytest
from anyfem.application.irregular_wave_input import IrregularWaveInput,pressure_bank_for_settings
from anyfem.application.hydro_wave_input import RegularWaveInput,ApplyWavePressure
from anyfem.document import canonical_hash
from anyfem.io.project_file import project_to_dict,project_from_dict
from anyloads.spectral_pressure_history import SpectralMappedPressureHistory


@pytest.fixture
def complete_backend(monkeypatch):
    import anyhydro
    import anyfem.application.irregular_wave_input as service
    import anyfem.application.hydro_wave_pressure as hull
    from anyhydro import PressureResponseBank
    calls=[]
    source=[np.array([[0.,0,0],[1,0,0],[1,1,0],[0,1,0]]),np.array([[0,1,2,3]]),('bottom',)]
    fingerprint=dict(backend='capytaine',version='3.0.0',path='test/capytaine/__init__.py',code_sha256={'producer':'original'})
    monkeypatch.setattr(hull,'_source_mesh',lambda:tuple(source))
    monkeypatch.setattr(service,'_backend_fingerprint',lambda:dict(fingerprint))
    def prepare(*args,**kwargs):
        calls.append((args,kwargs))
        provenance=dict(backend='capytaine',version=fingerprint['version'],path=fingerprint['path'],
            method='indirect',green_function='default',engine='default',
            effective_settings=dict(method='indirect',linear_solver='lu_decomposition'),n_threads=1,jobs=1)
        return PressureResponseBank(vertices=args[0],faces=args[1],surface_ids=args[2],
            centers=[[.5,.5,0]],normals=[[0,0,1]],areas=[1.],omegas=args[3],
            responses=np.array([[w+1j] for w in args[3]]),heading=kwargs['heading'],water_depth=kwargs['water_depth'],
            provenance_json=json.dumps(provenance))
    monkeypatch.setattr(anyhydro,'prepare_pressure_bank',prepare)
    return calls,source,fingerprint


def test_settings_band_seed_and_actual_sample_cap():
    c=IrregularWaveInput();r=c.realization();wp=math.tau/6;dw=wp*2.5/32
    assert len(c.times)==101 and len(r.components)==32
    assert r.components[0].omega==wp*.5+.5*dw
    assert r.components[-1].omega==wp*.5+31.5*dw
    assert r.to_dict()==c.realization().to_dict()
    assert r.content_hash!=replace(c,seed=1).realization().content_hash
    assert len(replace(c,duration=10800.).times)==108001
    assert len(replace(c,duration=1.05,dt=.1).times)==12
    for changes in (dict(duration=10800.01),dict(component_count=True),dict(component_count=201),
            dict(seed=-1),dict(lower_frequency_ratio=3),dict(gamma=0),dict(dt=0)):
        with pytest.raises(ValueError):replace(c,**changes)
    assert len(RegularWaveInput().times)==101 and RegularWaveInput().wave_type=='regular'


def test_source_frequency_inventory_unchanged_by_seed_height_time(tmp_path,complete_backend):
    from anyfem.application.irregular_wave_input import _frequency_cache_directory
    calls,_,fingerprint=complete_backend
    c=IrregularWaveInput(component_count=2)
    for changed in (c,replace(c,seed=20,significant_height=2,duration=100),replace(c,peak_period=7)):
        bank,r=pressure_bank_for_settings(changed,cache_directory=tmp_path)
        assert bank.bank_id and len(r.components)==2
    assert len(calls)==2 and calls[0][0][3]!=calls[1][0][3]
    assert calls[0][1]['cache_directory']==_frequency_cache_directory(tmp_path,fingerprint)
    assert calls[0][1]['cache_directory']==calls[1][1]['cache_directory']
    assert calls[0][1]['max_missing_solves']==2 and calls[0][1]['max_wall_seconds']==120.


def test_complete_cache_hit_never_prepares_and_reports_inventory(tmp_path,complete_backend,monkeypatch):
    import anyhydro
    calls,_,_=complete_backend;c=IrregularWaveInput(component_count=2)
    first,_=pressure_bank_for_settings(c,cache_directory=tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('warm complete bank must not initialize BEM')
    monkeypatch.setattr(anyhydro,'prepare_pressure_bank',forbidden)
    reports=[]
    second,_=pressure_bank_for_settings(replace(c,seed=4,significant_height=2,duration=100),
        cache_directory=tmp_path,progress=reports.append)
    assert first.bank_id==second.bank_id and len(calls)==1
    assert any('2/2' in message and 'complete bank verified' in message for message in reports)


def test_complete_cache_invalidation_exact_settings_body_and_backend(tmp_path,complete_backend):
    calls,source,fingerprint=complete_backend;c=IrregularWaveInput(component_count=2)
    pressure_bank_for_settings(c,cache_directory=tmp_path)
    pressure_bank_for_settings(replace(c,heading=30),cache_directory=tmp_path)
    pressure_bank_for_settings(replace(c,depth=100),cache_directory=tmp_path)
    source[0]=source[0]+np.array([1.,0,0])
    pressure_bank_for_settings(c,cache_directory=tmp_path)
    fingerprint['code_sha256']={'producer':'changed'}
    pressure_bank_for_settings(c,cache_directory=tmp_path)
    assert len(calls)==5
    assert calls[0][1]['cache_directory']==calls[3][1]['cache_directory']
    assert calls[3][1]['cache_directory']!=calls[4][1]['cache_directory']
    assert calls[4][1]['cache_directory'].parent==tmp_path/'frequency-banks'


def test_complete_cache_corruption_and_cancellation_refuse(tmp_path,complete_backend):
    from anyfem.application.irregular_wave_input import CompletePressureBankCacheError
    calls,_,_=complete_backend;c=IrregularWaveInput(component_count=2)
    pressure_bank_for_settings(c,cache_directory=tmp_path)
    class Cancel:
        def __init__(self,limit):self.calls=0;self.limit=limit
        def raise_if_cancelled(self,message):
            self.calls+=1
            if self.calls>=self.limit:raise RuntimeError('cancelled')
    for limit in (1,3):
        with pytest.raises(RuntimeError,match='cancelled'):
            pressure_bank_for_settings(c,cache_directory=tmp_path,cancellation_token=Cancel(limit))
    assert len(calls)==1
    path=next((tmp_path/'complete-banks').glob('*.npz'));path.write_bytes(b'corrupt')
    with pytest.raises(CompletePressureBankCacheError):pressure_bank_for_settings(c,cache_directory=tmp_path)
    assert len(calls)==1


def test_complete_cache_missing_payload_and_manifest_corruption_refuse(tmp_path,complete_backend):
    from anyfem.application.irregular_wave_input import CompletePressureBankCacheError
    calls,_,_=complete_backend;c=IrregularWaveInput(component_count=2)
    bank,_=pressure_bank_for_settings(c,cache_directory=tmp_path)
    manifest_path=next((tmp_path/'complete-banks').glob('*.json'));original=manifest_path.read_text()
    for bad in ('null','[]','broken'):
        manifest_path.write_text(bad)
        with pytest.raises(CompletePressureBankCacheError):pressure_bank_for_settings(c,cache_directory=tmp_path)
    manifest_path.write_text(original)
    (tmp_path/'complete-banks'/(bank.bank_id+'.npz')).unlink()
    with pytest.raises(CompletePressureBankCacheError):pressure_bank_for_settings(c,cache_directory=tmp_path)
    assert len(calls)==1


def test_complete_cache_interleaved_publish_is_consistent(tmp_path,complete_backend,monkeypatch):
    import anyhydro
    import anyfem.application.irregular_wave_input as service
    calls,_,_=complete_backend;c=IrregularWaveInput(component_count=2)
    prepare=anyhydro.prepare_pressure_bank;publish=service._atomic_json;nested=[False]
    def varied(*args,**kwargs):
        bank=prepare(*args,**kwargs)
        return replace(bank,responses=bank.responses*len(calls),bank_id='')
    monkeypatch.setattr(anyhydro,'prepare_pressure_bank',varied)
    def interleaved(path,data):
        if not nested[0]:
            nested[0]=True
            service.pressure_bank_for_settings(c,cache_directory=tmp_path)
        publish(path,data)
    monkeypatch.setattr(service,'_atomic_json',interleaved)
    first,_=service.pressure_bank_for_settings(c,cache_directory=tmp_path)
    restored,_=service.pressure_bank_for_settings(c,cache_directory=tmp_path)
    assert restored.bank_id==first.bank_id and len(calls)==2
    assert len(list((tmp_path/'complete-banks').glob('*.npz')))==2


def test_one_compact_parent_undo_redo_reopen_and_replacement():
    from anyfem import Project
    p=Project();c=IrregularWaveInput(component_count=2,duration=1.)
    r=c.realization();omegas=tuple(x.omega for x in r.components)
    h=SpectralMappedPressureHistory(c.times,(42,),omegas,((1.,),(2.,)),((.1,),(.2,)),0.,
        'a'*64,'b'*64,json.dumps(r.to_dict()),json.dumps(dict(settings=asdict(c))))
    result=dict(name=c.name,history=h.to_dict(),surfaces=[],input_document_hash=canonical_hash(project_to_dict(p)))
    cmd=ApplyWavePressure(result);parent=cmd.do(p)
    assert len(p.load_cases)==12 and len(parent.wet_pressures)==1 and len(parent.times)==11
    assert len(project_to_dict(p)['load_cases'])==1
    assert len(p.series_children(parent.name))==11
    h2=project_from_dict(project_to_dict(p)).load_cases[parent.name].wet_pressures[0].history
    np.testing.assert_array_equal(h2.evaluate(.13),h.evaluate(.13))
    assert h2.evaluate(.13)[0]!=h2.evaluate(.31)[0]
    cmd.undo(p);assert not p.load_cases
    cmd.redo(p);assert p.load_cases[parent.name].id==parent.id
    newer=dict(result,input_document_hash=canonical_hash(project_to_dict(p)))
    replacement=ApplyWavePressure(newer,replace_legacy=True);replacement.do(p)
    assert len(p.load_cases)==12
    p.name='stale'
    with pytest.raises(ValueError,match='changed'):ApplyWavePressure(newer).do(p)


def test_production_pressure_caller_uses_actual_times_and_mesh_binding():
    from anyfem import Project,steel
    from anyfem.model import WetSurface,WetPressure
    from anyfem.solve.build import wet_element_pressures
    from anymesher.serialize import mesh_to_dict
    p=Project();p.add_material(steel());p.add_plate_section('plate',.01,'S355')
    g=p.geometry;vertices=g.add_points([(0,0,0),(1,0,0),(1,1,0),(0,1,0)])
    face=g.add_face(g.add_polyline(vertices,close=True));p.assign_plate(face,'plate')
    p.add_wet_surface(WetSurface(ref=p.face(face),fluid_side='positive_normal'))
    mesh=p.generate_mesh(1.)
    ids=tuple(sorted(mesh.elements_of_face[face]));n=len(ids)
    c=IrregularWaveInput(component_count=2,duration=1.);r=c.realization()
    h=SpectralMappedPressureHistory(c.times,ids,tuple(x.omega for x in r.components),
        ((1.,)*n,(2.,)*n),((.1,)*n,(.2,)*n),0.,canonical_hash(mesh_to_dict(mesh)),
        'b'*64,json.dumps(r.to_dict()))
    owner=p.add_load_series('Sea',(.13,.31));owner.wet_pressures.append(WetPressure(history=h,mapping_key='element'))
    children=p.series_children(owner.name)
    a=wet_element_pressures(p,mesh,p.resolve_case(children[0]));b=wet_element_pressures(p,mesh,p.resolve_case(children[1]))
    assert [x.pressure for x in a]!=[x.pressure for x in b]
    assert len(a)==len(ids)
    # Fluid on the positive shell-normal side pushes opposite that normal.
    expected=dict(zip(ids,h.evaluate(.13)))
    np.testing.assert_allclose([x.pressure for x in a],[-expected[x.element_id] for x in a],atol=1e-12)
    changed=p.generate_mesh(.5)
    with pytest.raises(ValueError,match='mesh identity'):wet_element_pressures(p,changed,p.resolve_case(children[0]))


def test_compute_service_maps_all_frequencies_once_and_is_self_contained(monkeypatch,tmp_path):
    import anyfem.application.hydro_wave_input as service
    import anyfem.application.hydro_wave_pressure as hull
    import anyfem.application.irregular_wave_input as irregular
    import anymesher.serialize as mesh_codec
    import anyloads.spectral_pressure_history as spectral
    from anyhydro.pressure_bank import PressureResponseBank
    from types import SimpleNamespace
    # Existing authored contract checked against minimal exact five rectangles.
    bounds={'bottom':((0,-2.5,0),(60,2.5,0)),'port':((0,-2.5,0),(60,-2.5,1.5)),
        'starboard':((0,2.5,0),(60,2.5,1.5)),'end0':((0,-2.5,0),(0,2.5,1.5)),
        'end60':((60,-2.5,0),(60,2.5,1.5))}
    corners={name:(lo,hi) for name,(lo,hi) in bounds.items()}
    geometry=SimpleNamespace(face_corner_vertices=lambda fid:corners[fid],vertex_position=lambda vertex:vertex)
    model=SimpleNamespace(geometry=geometry,wet_surfaces=[SimpleNamespace(name=name,ref=SimpleNamespace(id=name),
        id=name,fluid_side='away_from_point',interior_point=(30,0,1)) for name in bounds])
    monkeypatch.setattr(service,'project_from_dict',lambda document:model)
    monkeypatch.setattr(mesh_codec,'mesh_from_dict',lambda data:object())
    c=IrregularWaveInput(component_count=2,duration=1.);r=c.realization()
    v,f,labels=hull._source_mesh();polygons=v[f]
    av=(np.cross(polygons[:,1]-polygons[:,0],polygons[:,2]-polygons[:,0])+
        np.cross(polygons[:,2]-polygons[:,0],polygons[:,3]-polygons[:,0]))/2
    areas=np.linalg.norm(av,axis=1)
    bank=PressureResponseBank(v,f,labels,polygons.mean(axis=1),av/areas[:,None],areas,
        tuple(x.omega for x in r.components),np.array([[1+2j]*len(f),[3-1j]*len(f)]))
    monkeypatch.setattr(hull,'target_hull_cells',lambda *args:(tuple(range(len(f))),bank.centers,bank.normals,bank.areas,bank.surface_ids))
    monkeypatch.setattr(irregular,'pressure_bank_for_settings',lambda *args,**kwargs:(bank,r))
    calls=[];transfer=spectral.transfer_panel_pressure
    monkeypatch.setattr(spectral,'transfer_panel_pressure',lambda *args,**kwargs:(calls.append(1) or transfer(*args,**kwargs)))
    result=service.compute_wave_input(document={},mesh_data={},settings=asdict(c),source_bank_cache_directory=tmp_path)
    assert len(calls)==1 and result['source_cache_path'] is None
    from anyloads.pressure_history import pressure_history_from_dict
    h=pressure_history_from_dict(result['history'])
    assert h.source_id==bank.bank_id and h.evaluate(.13)[0]!=h.evaluate(.31)[0]
    evidence=json.loads(h.provenance)['frequency_transfer_evidence']
    assert len(evidence)==2 and all(e['force']['absolute']==0 for e in evidence)
    class Cancel:
        def raise_if_cancelled(self,message):raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        service.compute_wave_input(document={},mesh_data={},settings=asdict(c),cancellation_token=Cancel())
