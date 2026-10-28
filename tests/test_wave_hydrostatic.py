"""Optional hydrostatic pressure in the wave application (headless; no BEM, no structural solve)."""
from dataclasses import asdict,fields,replace
from types import SimpleNamespace
import copy
import json
import math
import numpy as np
import pytest
from test_wave_force_input import retained_force_record
from test_pressure_remap import project_and_meshes as barge_meshes,original_history
from anyfem.application import wave_hydrostatic as hs
from anyfem.application.hydro_wave_input import (RegularWaveInput,ApplyWavePressure,select_prepared_wave_window)
from anyfem.application.irregular_wave_input import IrregularWaveInput
from anyfem.application.wave_force_input import (_same_inputs,compact_retained_preview,migrate_retained_preview,
    prepare_force_preview,recover_selected_wave_input,validate_force_preview)
from anyfem.document import canonical_hash,DocumentSession
from anyfem.io.project_file import project_to_dict,project_from_dict
from anyfem.model import WetPressure
from anyfem.model.attributes import LoadCase
from anyfem.model.wetfield import field_pressure
from anyfem.solve import build as builder
from anymesher.serialize import mesh_to_dict

BOTTOM=1025.*9.81*1.5


def regular_retained(tmp_path):
    p,old,new=barge_meshes();history,bank,realization=original_history(p,old,regular=True)
    config=RegularWaveInput(height=1.,period=2*math.pi/.7,phase=math.degrees(.3))
    payload=migrate_retained_preview(history,asdict(config),cache_directory=tmp_path)
    return p,old,new,config,payload


def irregular_retained(tmp_path):
    p,old,new,config,v3=retained_force_record(tmp_path)
    return p,old,new,config,compact_retained_preview(v3,asdict(config))


def prepare(p,new,config,payload,tmp_path):
    return prepare_force_preview(document=project_to_dict(p),mesh_data=mesh_to_dict(new),settings=asdict(config),
        retained_preview=payload,source_bank_cache_directory=tmp_path)


def apply_series(p,new,config,v3,tmp_path,*,replace_legacy=False,start=6.,end=8.):
    payload=compact_retained_preview(v3,asdict(config))
    prepared=prepare(p,new,config,payload,tmp_path)
    selected=select_prepared_wave_window(prepared,start,end)
    result=recover_selected_wave_input(document=project_to_dict(p),mesh_data=mesh_to_dict(new),prepared=selected,
        start=start,end=end,offline_history=v3.to_dict())
    command=ApplyWavePressure(result,replace_legacy=replace_legacy)
    return command,command.do(p)


@pytest.mark.parametrize('cls',[RegularWaveInput,IrregularWaveInput])
def test_flag_defaults_false_keeps_its_old_position_and_is_strictly_boolean(cls):
    names=[f.name for f in fields(cls)]
    # follow_surface is the later additive field: old positional include_hydrostatic compatibility is unchanged.
    assert cls().include_hydrostatic is False and names[-2:]==['include_hydrostatic','follow_surface']
    assert cls(include_hydrostatic=True).include_hydrostatic is True
    for bad in (1,0,'true',None,np.bool_(True),1.):
        with pytest.raises(ValueError,match='boolean'):cls(include_hydrostatic=bad)
    legacy={k:v for k,v in asdict(cls()).items() if k!='include_hydrostatic'}
    assert cls(**legacy).include_hydrostatic is False


def test_pressure_sign_head_waterline_and_dry_clamp_use_existing_evaluation():
    p,old,new=barge_meshes();dynamic=WetPressure(history=original_history(p,old,regular=True)[0],mapping_key='element')
    static=hs.make_static_pressure(dynamic)
    assert static.kind=='expression' and static.expression==hs.EXPRESSION and static.scale==1.
    z=np.array([0.,.75,1.5,2.,10.])
    magnitude=field_pressure(LoadCase('c'),static,np.stack([np.zeros(5),np.zeros(5),z],axis=1))
    np.testing.assert_allclose(magnitude,[BOTTOM,1025.*9.81*.75,0.,0.,0.],rtol=1e-14)
    assert BOTTOM==pytest.approx(15082.875) and hs.BOTTOM_PRESSURE==BOTTOM
    # fluid-side convention comes from the existing wet surfaces: signs equal those of a unit pressure
    unit=builder.wet_element_pressures(p,new,LoadCase('u',wet_pressures=[WetPressure(value=1.)]))
    case=LoadCase('s',wet_pressures=[static])
    got=builder.wet_element_pressures(p,new,case)
    assert len(got)==len(unit)
    for a,b in zip(got,unit):
        assert a.element_id==b.element_id
        assert a.pressure==pytest.approx(b.pressure*1025.*9.81*max(1.5-b.centroid[2],0.),rel=1e-13)
    assert any(abs(item.pressure)>0 for item in got)


def test_source_baseline_is_the_exact_owner_wrench_about_world0():
    base=np.array(hs.source_baseline())
    np.testing.assert_allclose(base,[0,0,4524862.5,0,-135745875.,0],rtol=1e-12,atol=1e-6)
    from anyhydro.hydrostatics_exact import buoyancy_baseline_wrench
    from anyfem.application.hydro_wave_pressure import _source_mesh
    v,f,_=_source_mesh()
    assert hs.source_baseline()==tuple(float(x) for x in buoyancy_baseline_wrench(v,f,(0.,0.,0.),1025.,9.81))


@pytest.mark.parametrize('kind',['regular','irregular'])
def test_force_preview_total_minus_dynamic_is_exactly_one_baseline(kind,tmp_path,monkeypatch):
    p,old,new,config,payload=(regular_retained if kind=='regular' else irregular_retained)(tmp_path)
    import anyhydro.excitation_cache as hydro
    from anyhydro.pressure_bank import PressureResponseBank
    import anyloads.spectral_pressure_history as spatial
    def forbidden(*a,**k):raise AssertionError('toggle must not solve, recover or map pressures')
    for owner,name in ((hydro,'prepare_excitation_bank'),(hydro,'recover_pressure_bank'),(PressureResponseBank,'from_dict'),
            (PressureResponseBank,'load'),(spatial,'build_pressure_mapping_plan')):monkeypatch.setattr(owner,name,forbidden)
    before=copy.deepcopy(payload);document_hash=canonical_hash(project_to_dict(p))
    dynamic=prepare(p,new,config,payload,tmp_path)
    total=prepare(p,new,replace(config,include_hydrostatic=True),payload,tmp_path)
    assert payload==before and canonical_hash(project_to_dict(p))==document_hash
    difference=np.array(total['preview_resultants'])-np.array(dynamic['preview_resultants'])
    np.testing.assert_allclose(difference,np.broadcast_to(hs.source_baseline(),difference.shape),rtol=1e-12,atol=1e-6)
    assert total['preview_times']==dynamic['preview_times'] and total['preview_source_bank_id']==dynamic['preview_source_bank_id']
    assert (dynamic['preview_composition'],total['preview_composition'])==('dynamic-only','dynamic+hydrostatic')
    assert dynamic['preview_hydrostatic_baseline'] is None and total['preview_hydrostatic_baseline']==list(hs.source_baseline())
    assert total['force_preview']['settings']['include_hydrostatic'] is True
    # the saved dynamic bank / realization are untouched (phases and responses stay purely dynamic)
    assert total['force_preview']['excitation_bank']==dynamic['force_preview']['excitation_bank']
    assert total['force_preview']['realization']==dynamic['force_preview']['realization']
    assert total['preview_content_hash']!=dynamic['preview_content_hash']
    assert total['input_settings_hash']!=dynamic['input_settings_hash']
    validate_force_preview(dynamic);validate_force_preview(total)
    # a retained total is never summed again: re-preparing from the total's payload stays one baseline
    again=prepare(p,new,replace(config,include_hydrostatic=True),total['force_preview'],tmp_path)
    assert again['preview_resultants']==total['preview_resultants']


def test_hydrostatic_is_not_a_dynamic_source_input():
    base={'a':1,'name':'x'}
    assert _same_inputs({**base,'include_hydrostatic':True,'name':'y'},{'a':1})
    assert _same_inputs({**base,'include_hydrostatic':False},{**base,'include_hydrostatic':True})
    assert not _same_inputs({**base,'include_hydrostatic':True},{'a':2})


def test_tampered_or_inconsistent_total_preview_is_refused(tmp_path):
    p,old,new,config,payload=regular_retained(tmp_path)
    total=prepare(p,new,replace(config,include_hydrostatic=True),payload,tmp_path)
    for mutate in (lambda r:r['preview_resultants'][0].__setitem__(2,0.),
            lambda r:r.__setitem__('preview_hydrostatic_baseline',[0.]*6),
            lambda r:r.__setitem__('preview_composition','dynamic-only'),
            lambda r:r['force_preview']['settings'].__setitem__('include_hydrostatic',False)):
        bad=copy.deepcopy(total);mutate(bad)
        with pytest.raises(ValueError,match='changed'):select_prepared_wave_window(bad,1.,2.)
    dynamic=prepare(p,new,config,payload,tmp_path)
    bad=copy.deepcopy(dynamic);bad['force_preview']['settings']['include_hydrostatic']=True
    with pytest.raises(ValueError,match='changed'):validate_force_preview(bad)


def test_apply_writes_one_owned_constant_companion_over_selected_times(tmp_path):
    p,old,new,config,v3=retained_force_record(tmp_path)
    config=replace(config,include_hydrostatic=True);before=dict(p.load_cases)
    command,owner=apply_series(p,new,config,v3,tmp_path)
    dynamic,static=owner.wet_pressures
    assert owner.times==config.times[60:81] and dynamic.history is not None and hs.is_owned_static(static,dynamic)
    assert static.id==hs.static_pressure_id(dynamic.id)!=dynamic.id and hs.history_includes_hydrostatic(dynamic.history)
    assert json.loads(dynamic.history.provenance)['settings']['include_hydrostatic'] is True
    steps=p.series_children(owner.name);assert len(steps)==21
    reference=builder.wet_element_pressures(p,new,LoadCase('u',wet_pressures=[WetPressure(value=1.)]))
    first=None
    for step in steps[::10]:
        resolved=p.resolve_case(step)
        only_static=copy.copy(resolved);only_static.wet_pressures=[static]
        only_dynamic=copy.copy(resolved);only_dynamic.wet_pressures=[dynamic]
        s=builder.wet_element_pressures(p,new,only_static);d=builder.wet_element_pressures(p,new,only_dynamic)
        t=builder.wet_element_pressures(p,new,resolved)
        ids=[x.element_id for x in reference]
        assert [x.element_id for x in s]==[x.element_id for x in d]==[x.element_id for x in t]==ids
        for a,b,c,u in zip(s,d,t,reference):
            assert c.pressure==pytest.approx(a.pressure+b.pressure,rel=1e-12,abs=1e-9)
            assert a.pressure==pytest.approx(u.pressure*1025.*9.81*max(1.5-u.centroid[2],0.),rel=1e-13)
        values=[a.pressure for a in s];first=first or values
        assert values==first
    # reopen keeps the companion, ownership and intent
    reopened=project_from_dict(project_to_dict(p));loaded=reopened.load_cases[owner.name]
    assert hs.owned_pair(loaded) is not None and hs.history_includes_hydrostatic(loaded.wet_pressures[0].history)
    # undo/redo are atomic and leave structural inputs alone
    model_hash=DocumentSession(p).revision.model_hash
    command.undo(p);assert dict(p.load_cases)==before
    command.redo(p);assert hs.owned_pair(p.load_cases[owner.name]) is not None
    assert DocumentSession(p).revision.model_hash==model_hash


def test_off_default_and_legacy_provenance_add_no_companion(tmp_path):
    p,old,new,config,v3=retained_force_record(tmp_path)
    command,owner=apply_series(p,new,config,v3,tmp_path)
    assert len(owner.wet_pressures)==1 and not hs.history_includes_hydrostatic(owner.wet_pressures[0].history)
    assert hs.history_includes_hydrostatic(SimpleNamespace(provenance=json.dumps({'settings':{'name':'x'}}))) is False
    assert hs.history_includes_hydrostatic(SimpleNamespace(provenance='')) is False
    with pytest.raises(ValueError,match='boolean'):
        hs.history_includes_hydrostatic(SimpleNamespace(provenance=json.dumps({'settings':{'include_hydrostatic':1}})))


def test_toggle_reapply_never_accumulates_baselines_and_series_keep_distinct_identities(tmp_path):
    p,old,new,config,v3=retained_force_record(tmp_path)
    on=replace(config,include_hydrostatic=True)
    apply_series(p,new,on,v3,tmp_path)
    count=lambda owner:sum(load.name==hs.NAME for load in owner.wet_pressures)
    for flag in (True,False,True,True):
        _,owner=apply_series(p,new,replace(config,include_hydrostatic=flag),v3,tmp_path,replace_legacy=True)
        assert count(owner)==int(flag) and len(owner.wet_pressures)==1+int(flag)
        assert sum(load.name==hs.NAME for c in p.load_cases.values() for load in c.wet_pressures)==int(flag)*(1)
    _,other=apply_series(p,new,replace(on,name='Second sea'),v3,tmp_path)
    first=p.load_cases[config.name]
    assert hs.owned_pair(first) and hs.owned_pair(other)
    assert first.wet_pressures[1].id!=other.wet_pressures[1].id
    assert len([a for a in p.analyses.values() if a.target_id==config.name])==1


def test_replacement_refuses_unrelated_or_altered_loads_and_rolls_back(tmp_path):
    p,old,new,config,v3=retained_force_record(tmp_path)
    on=replace(config,include_hydrostatic=True)
    _,owner=apply_series(p,new,on,v3,tmp_path)
    def attempt():
        before=(dict(p.load_cases),dict(p.analyses),canonical_hash(project_to_dict(p)))
        with pytest.raises(ValueError,match='discard|already exists'):apply_series(p,new,on,v3,tmp_path,replace_legacy=True)
        assert (dict(p.load_cases),dict(p.analyses),canonical_hash(project_to_dict(p)))==before
    owner.wet_pressures.append(WetPressure(value=5.));attempt();owner.wet_pressures.pop()
    owner.gravity=np.array([0,0,-9.81]);attempt();owner.gravity=None
    dynamic,static=owner.wet_pressures
    for forged in (replace(static,scale=2.),replace(static,expression='1025.0*9.81*max(1.5-z,0.0)+1.'),
            replace(static,id='not-the-pair'),replace(static,name='user pressure'),replace(static,surfaces=('x',)),
            replace(static,envelope=None,table=None)):
        owner.wet_pressures[1]=forged
        assert hs.owned_pair(owner) is None or forged is static or forged==static
    owner.wet_pressures[1]=replace(static,scale=2.);attempt()
    owner.wet_pressures[1]=static


def test_stale_document_mesh_and_failed_application_roll_back(tmp_path,monkeypatch):
    p,old,new,config,v3=retained_force_record(tmp_path)
    on=replace(config,include_hydrostatic=True)
    prepared=prepare(p,new,on,compact_retained_preview(v3,asdict(config)),tmp_path)
    selected=select_prepared_wave_window(prepared,6.,8.)
    with pytest.raises(ValueError,match='changed before pressure recovery'):
        recover_selected_wave_input(document=project_to_dict(p),mesh_data=mesh_to_dict(old),prepared=selected,start=6.,end=8.,
            offline_history=v3.to_dict())
    result=recover_selected_wave_input(document=project_to_dict(p),mesh_data=mesh_to_dict(new),prepared=selected,start=6.,end=8.,
        offline_history=v3.to_dict())
    keys=(dict(p.load_cases),dict(p.analyses))
    def boom(dynamic):raise RuntimeError('companion failed')
    monkeypatch.setattr(hs,'make_static_pressure',boom)
    with pytest.raises(RuntimeError,match='companion failed'):ApplyWavePressure(result).do(p)
    assert (dict(p.load_cases),dict(p.analyses))==keys
    monkeypatch.undo()
    p.add_plate_section('stale-section',.02,'S355')
    with pytest.raises(ValueError,match='project changed'):ApplyWavePressure(result).do(p)
    assert (dict(p.load_cases),dict(p.analyses))==keys


def _owner_with_companion(tmp_path,count=5):
    p,old,new,config,v3=retained_force_record(tmp_path)
    _,owner=apply_series(p,new,replace(config,include_hydrostatic=True),v3,tmp_path,start=6.,end=6.+.1*(count-1))
    return p,new,owner,p.series_children(owner.name)


def test_cached_dynamic_plus_static_equals_uncached_without_per_frame_flush(tmp_path,monkeypatch):
    p,mesh,owner,steps=_owner_with_companion(tmp_path)
    drops=[];real=builder._BatchLoadContext._drop
    monkeypatch.setattr(builder._BatchLoadContext,'_drop',lambda self:(drops.append(1),real(self))[1])
    context=builder._BatchLoadContext(SimpleNamespace(project=p,mesh=mesh))
    for step in steps:
        plain=builder._build_load_case(p,mesh,step)
        cached=builder._build_load_case(p,mesh,step,_load_context=context)
        assert plain.pressure_loads and plain.pressure_loads==cached.pressure_loads
    context.verify_unchanged()
    assert drops==[] and context.reuses['mesh_hash']==len(steps)-1
    assert context.reuses['wet_elements']==2*len(steps)-1


def test_forged_or_unpaired_static_stays_untrusted_and_still_exact(tmp_path,monkeypatch):
    p,mesh,owner,steps=_owner_with_companion(tmp_path,3)
    dynamic,static=owner.wet_pressures
    for forged in (replace(static,scale=2.),replace(static,id='x'),
            WetPressure(expression=static.expression,surfaces=static.surfaces,name=static.name,id=static.id)):
        owner.wet_pressures[1]=forged
        drops=[];real=builder._BatchLoadContext._drop
        with monkeypatch.context() as m:
            m.setattr(builder._BatchLoadContext,'_drop',lambda self:(drops.append(1),real(self))[1])
            context=builder._BatchLoadContext(SimpleNamespace(project=p,mesh=mesh))
            for step in steps:
                assert (builder._build_load_case(p,mesh,step).pressure_loads==
                    builder._build_load_case(p,mesh,step,_load_context=context).pressure_loads)
        if forged is not owner.wet_pressures[1] or forged.scale==2. or forged.id=='x':assert drops
    owner.wet_pressures[1]=static
    # a static whose owning dynamic is another series' history is not bound to this project's pair
    other=SimpleNamespace(project=p,mesh=mesh)
    context=builder._BatchLoadContext(other)
    stray=hs.make_static_pressure(WetPressure(history=dynamic.history,mapping_key='element'))
    assert not context._is_owned_static(stray) and context._is_owned_static(static)
