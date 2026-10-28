"""Force-only preparation and deferred source-pressure recovery/application."""
from dataclasses import asdict
import hashlib,json,math,re,zipfile
from pathlib import Path
import numpy as np


def _json(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def _cache(directory):
    if directory is None:
        from platformdirs import user_cache_dir
        directory=Path(user_cache_dir('ANYfem','ANY'))/'wave-force-solutions-v1'
    return Path(directory)


def _cancel(token,stage):
    if token is not None:token.raise_if_cancelled(stage)
    return False


def _source_observation(path):
    path=Path(path).resolve()
    try:
        stat=path.stat()
        return dict(path=str(path),size=stat.st_size,mtime_ns=stat.st_mtime_ns,ctime_ns=stat.st_ctime_ns,
            device=stat.st_dev,inode=stat.st_ino)
    except OSError as error:raise ValueError(f'legacy source unavailable; restore or replace the file: {error}') from error


def _read_legacy_source(path):
    from .pressure_transfer import _legacy_source,PressureRemapError
    try:
        with np.load(path,allow_pickle=False) as raw:source_id=str(raw['source_id'])
        return _legacy_source(path,source_id)
    except (zipfile.BadZipFile,EOFError,OSError,KeyError,IndexError,TypeError,ValueError) as error:
        raise PressureRemapError(f'legacy source malformed; restore/replace it before preparation (no automatic BEM fallback): {error}') from error


def _config(settings):
    from .hydro_wave_input import RegularWaveInput
    from .irregular_wave_input import IrregularWaveInput
    return (IrregularWaveInput if settings.get('wave_type','regular')=='irregular' else RegularWaveInput)(**settings)


def _realization(config):
    from anyhydro.excitation_cache import canonical_heading
    from anyloads.marine import AiryComponent
    from anyloads.sea_state import WaveRealization
    if config.wave_type=='irregular':return config.realization()
    return WaveRealization((AiryComponent(config.height/2,math.tau/config.period,
        math.degrees(canonical_heading(math.radians(config.heading))),math.radians(config.phase)),))


_NON_SOURCE_KEYS=('name','include_hydrostatic')


def _same_inputs(requested,saved):
    """Dynamic source identity: name and the steady hydrostatic composition are not BEM/realization inputs."""
    # Old records have no follow_surface key: missing means False (additive field, existing modes unchanged).
    return (_json({'follow_surface':False,**{k:v for k,v in requested.items() if k not in _NON_SOURCE_KEYS}})==
        _json({'follow_surface':False,**{k:v for k,v in saved.items() if k not in _NON_SOURCE_KEYS}}))


_PREVIEW_KEYS=('force_preview','solution_cache_root','preview_times','preview_resultants','preview_source_bank_id',
    'preview_moment_origin')


def _preview_hash_payload(result,include):
    payload={k:result[k] for k in _PREVIEW_KEYS}
    if include:payload.update({k:result[k] for k in ('preview_composition','preview_hydrostatic_baseline')})
    return payload


def _admit_domain_without_loads(document):
    from ..io.project_file import project_from_dict
    from .pressure_transfer import _domain
    # Reconstruct the existing geometry owner contract without decoding any
    # pressure/history data or constructing a mapped history during preview.
    model=project_from_dict({**document,'load_cases':[],'combinations':[],'analyses':[]})
    _domain(model)


def _validate_force_source(forces,realization,config):
    if config.wave_type=='regular' and (len(realization.components)!=1 or len(forces.omegas)!=1):
        raise ValueError('regular preview requires exactly one actual component and one bank frequency')
    from .hydro_wave_pressure import _source_mesh
    from anyhydro.excitation_cache import _numeric_hash,canonical_heading
    from anyhydro.pressure_bank import CONVENTIONS
    v,f,labels=_source_mesh();request=json.loads(forces.request_json)
    geometry=_numeric_hash(dict(surface_ids=labels),dict(vertices=np.asarray(v,dtype=np.float64),faces=np.asarray(f,dtype=np.int64)))
    declared=tuple(dict.fromkeys(c.omega for c in realization.components))
    if (request['geometry']!=geometry or request['heading']!=canonical_heading(math.radians(config.heading)) or
            request['water_depth']!=config.depth or request['rho']!=1025. or request['gravity']!=9.81 or
            tuple(request['phase_origin'])!=(0.,0.,0.) or request['conventions']!=CONVENTIONS or tuple(forces.omegas)!=declared):
        raise ValueError('compact excitation source geometry/physics/frequency identity differs from admitted wave inputs')


def _force_payload(bank,realization,times,settings,*,source_history_id=None):
    return dict(version='retained_force_preview_v1',excitation_bank=bank.to_dict(),realization=realization.to_dict(),
        record_times=tuple(times),settings=settings,source_history_id=source_history_id)


def compact_retained_preview(history,settings):
    """GUI calls this for v3; it never decodes original source-pressure JSON."""
    try:saved=json.loads(history.provenance).get('settings',{})
    except (ValueError,TypeError,AttributeError):saved={}
    if not saved:
        if history.force_payload_json:
            raise ValueError('saved v3 wave settings missing; use explicit Repair / refresh to restore from the offline source')
        from .pressure_transfer import retained_regular_settings
        saved=retained_regular_settings(history)
    if not _same_inputs(settings,saved):raise ValueError('compact preview inputs differ from retained actual realization')
    if history.force_payload_json:
        return dict(version='retained_force_preview_v1',excitation_bank=json.loads(history.force_payload_json),
            realization=json.loads(history.realization_json),record_times=history.full_record_times,
            settings=dict(settings),source_history_id=history.content_sha256)
    raise ValueError('legacy history needs explicit migration before compact preview')


def migrate_retained_preview(history,settings,*,cache_directory=None,progress=None):
    from anyhydro.pressure_bank import PressureResponseBank
    from anyhydro.excitation_cache import migrate_pressure_bank
    from anyloads.sea_state import WaveRealization
    from .pressure_transfer import _validate_source
    bank=PressureResponseBank.from_dict(json.loads(history.source_payload_json));_validate_source(bank)
    forces=migrate_pressure_bank(bank,cache_directory=_cache(cache_directory),progress=progress)
    return _force_payload(forces,WaveRealization.from_dict(json.loads(history.realization_json)),
        history.full_record_times,settings,source_history_id=history.content_sha256)


def prepare_force_preview(*,document,mesh_data,settings,cache_path=None,source_bank_cache_directory=None,
        retained_preview=None,retained_history=None,progress=None,cancellation_token=None,project=None,
        document_content_hash=None,mesh_content_hash=None,preview_context_version=None,repair_cache=False):
    """No target cells, mapping, mapped history or panel-bank load on warm path."""
    if repair_cache and retained_preview is not None:
        raise ValueError('compact-only cache repair requires offline history or fresh wave inputs; omit retained_preview')
    from anyhydro.excitation_cache import (ExcitationResponseBank,prepare_excitation_bank,migrate_pressure_bank,
        backend_fingerprint,_atomic_json,canonical_heading)
    from anyloads.wave_preview import excitation_resultants
    from anyloads.sea_state import WaveRealization
    from ..document import canonical_hash
    from .hydro_wave_pressure import _source_mesh
    cache=_cache(source_bank_cache_directory);config=_config(settings)
    if document_content_hash is not None or mesh_content_hash is not None or preview_context_version is not None:
        if (preview_context_version!='wave_preview_context_v1' or mesh_data is not None or
                any(type(h) is not str or re.fullmatch('sha256:[0-9a-f]{64}',h) is None
                    for h in (document_content_hash,mesh_content_hash)) or
                any(document.get(key)!=[] for key in ('load_cases','combinations','analyses'))):
            raise ValueError('invalid attested load-free preview context; full headless snapshots require default hashes')
    _cancel(cancellation_token,'force preview')
    _admit_domain_without_loads(document)
    source_cache_path=None;source_digest=None
    if retained_preview is None and retained_history is not None:
        # Explicit one-time v1/v2 migration, never the warm v3 path.
        from anyloads.pressure_history import pressure_history_from_dict
        from .pressure_transfer import retained_regular_settings
        history=pressure_history_from_dict(retained_history)
        try:saved=json.loads(history.provenance).get('settings',{})
        except (ValueError,AttributeError,TypeError):saved={}
        if not saved and history.force_payload_json:
            raise ValueError('saved v3 settings missing; restore valid settings or recreate explicit wave inputs, no pressure fallback')
        if not saved:saved=retained_regular_settings(history)
        if not _same_inputs(settings,saved):raise ValueError('retained record inputs changed; prepare edited wave inputs')
        retained_preview=migrate_retained_preview(history,saved,cache_directory=cache,progress=progress)
    if retained_preview is not None:
        if retained_preview.get('version')!='retained_force_preview_v1' or not _same_inputs(settings,retained_preview['settings']):
            raise ValueError('retained compact record inputs changed')
        forces=ExcitationResponseBank.from_dict(retained_preview['excitation_bank'])
        realization=WaveRealization.from_dict(retained_preview['realization'])
        times=tuple(retained_preview['record_times']);payload=dict(retained_preview,settings=dict(settings))
    else:
        realization=_realization(config);times=config.times;v,f,labels=_source_mesh()
        # Explicit legacy alias is distinct from current cold provider namespace.
        alias_key=hashlib.sha256(_json(dict(geometry=hashlib.sha256(v.tobytes()+f.tobytes()+_json(labels).encode()).hexdigest(),
            omegas=[c.omega.hex() for c in realization.components],heading=canonical_heading(math.radians(config.heading)),
            depth=config.depth,backend=backend_fingerprint(),legacy_path=None if cache_path is None else str(Path(cache_path).resolve()))).encode()).hexdigest()
        alias=cache/'legacy-force-aliases'/(alias_key+'.json')
        record=None
        observation=_source_observation(cache_path) if cache_path is not None and config.wave_type=='regular' else None
        if observation is not None and alias.exists() and not repair_cache:
            try:
                record=json.loads(alias.read_text())
                if type(record) is not dict:raise ValueError('invalid legacy force alias schema')
                digest=record.pop('alias_id')
                if hashlib.sha256(_json(record).encode()).hexdigest()!=digest:raise ValueError('legacy alias integrity mismatch')
                if record['source_observation']!=observation:record=None
            except (ValueError,KeyError,TypeError,OSError) as error:
                raise ValueError('corrupt legacy force alias; use explicit Repair / refresh') from error
        if record is not None:
            forces=ExcitationResponseBank.from_dict(record['excitation_bank'])
            # Completed migration owns independent immutable solutions now;
            # its original file digest is provenance, not a live dependency.
        elif cache_path is not None and config.wave_type=='regular':
            from .pressure_transfer import bank_from_regular
            # Reuse an explicitly provided verified old pressure source once.
            # It is not represented as a current cold BEM solution.
            from .pressure_transfer import PressureRemapError
            try:source,digest=_read_legacy_source(cache_path)
            except PressureRemapError:
                if not repair_cache:raise
                if progress:progress('ignoring invalid legacy cache; preparing current wave inputs')
                source=None
            if (source is None or source.omega!=math.tau/config.period or canonical_heading(source.heading)!=canonical_heading(math.radians(config.heading)) or
                    source.water_depth!=config.depth):
                forces=prepare_excitation_bank(v,f,labels,tuple(c.omega for c in realization.components),cache_directory=cache,
                    heading=canonical_heading(math.radians(config.heading)),water_depth=config.depth,max_missing_solves=1,repair_cache=repair_cache,
                    cancel=lambda:_cancel(cancellation_token,'hydro solution preparation'),progress=(lambda state:progress(
                        f'Hydrodynamic solutions: {len(state.completed)}/{len(realization.components)}; {state.cached_count} cached')) if progress else None)
            else:
                bank=bank_from_regular(source)
                from .pressure_transfer import _validate_source
                _validate_source(bank)
                forces=migrate_pressure_bank(bank,cache_directory=cache,progress=progress)
                record=dict(excitation_bank=forces.to_dict(),migration_source_path=str(cache_path),migration_source_sha256=digest,
                    source_observation=observation)
                _atomic_json(alias,{**record,'alias_id':hashlib.sha256(_json(record).encode()).hexdigest()})
        else:
            forces=prepare_excitation_bank(v,f,labels,tuple(c.omega for c in realization.components),cache_directory=cache,
                heading=canonical_heading(math.radians(config.heading)),water_depth=config.depth,max_missing_solves=len(realization.components),repair_cache=repair_cache,
                cancel=lambda:_cancel(cancellation_token,'hydro solution preparation'),progress=(lambda state:progress(
                    f'Hydrodynamic solutions: {len(state.completed)}/{len(realization.components)}; {state.cached_count} cached')) if progress else None)
        payload=_force_payload(forces,realization,times,dict(settings))
    _validate_force_source(forces,realization,config)
    _cancel(cancellation_token,'force-only synthesis')
    preview=excitation_resultants(forces,realization,times,cancel=lambda:_cancel(cancellation_token,'force-only synthesis'))
    from .wave_hydrostatic import composition,source_baseline,total_resultants
    include=config.include_hydrostatic
    # Always start from the dynamic source resultants; the steady baseline is added exactly once.
    result=dict(preview_kind='force_only_v1',name=config.name,force_preview=payload,solution_cache_root=str(cache),
        preview_times=preview.times.tolist(),preview_resultants=total_resultants(preview.values,include).tolist(),
        preview_composition=composition(include),
        preview_hydrostatic_baseline=list(source_baseline()) if include else None,
        preview_source_bank_id=forces.bank_id,
        preview_moment_origin=preview.moment_origin,source_cache_path=source_cache_path,source_cache_sha256=source_digest,
        input_document_hash=document_content_hash or canonical_hash(document),mesh_hash=mesh_content_hash or canonical_hash(mesh_data),
        input_settings_hash=canonical_hash(settings))
    result['preview_content_hash']=canonical_hash(_preview_hash_payload(result,include))
    return result


def validate_force_preview(prepared):
    from ..document import canonical_hash
    from anyhydro.excitation_cache import ExcitationResponseBank
    from .wave_hydrostatic import composition,setting_flag,source_baseline
    if prepared.get('preview_kind')!='force_only_v1':raise ValueError('prepared force-only preview changed; prepare again')
    try:
        include=setting_flag(prepared['force_preview']['settings'])
        consistent=(prepared.get('preview_composition',composition(False))==composition(include) and
            (list(prepared['preview_hydrostatic_baseline'])==list(source_baseline()) if include
                else prepared.get('preview_hydrostatic_baseline') is None))
        changed=not consistent or prepared.get('preview_content_hash')!=canonical_hash(_preview_hash_payload(prepared,include))
    except (KeyError,TypeError,ValueError):changed=True
    if changed:
        raise ValueError('prepared force-only preview changed; prepare again')
    bank=ExcitationResponseBank.from_dict(prepared['force_preview']['excitation_bank'])
    if bank.bank_id!=prepared['preview_source_bank_id']:raise ValueError('prepared excitation identity changed')
    if tuple(prepared['preview_times'])!=tuple(prepared['force_preview']['record_times']):raise ValueError('full record identity changed')
    return bank


def recover_selected_wave_input(*,document,mesh_data,prepared,start=None,end=None,offline_history=None,
        progress=None,cancellation_token=None,project=None):
    """Owned Apply child: recover panels, then map once; never solve BEM."""
    from anyhydro.excitation_cache import recover_pressure_bank,validate_pressure_binding
    from anyhydro.pressure_bank import PressureResponseBank
    from anyloads.pressure_history import pressure_history_from_dict,MappedPressureHistory
    from anyloads.sea_state import WaveRealization
    from anyloads.wave_preview import select_sample_window
    from anyloads.spectral_pressure_history import build_pressure_mapping_plan,map_pressure_bank,SpectralMappedPressureHistory
    from anyloads.source_pressure_history import SourceAwarePressureHistory
    from anymesher.serialize import mesh_from_dict
    from ..io.project_file import project_from_dict
    from ..document import canonical_hash
    from .pressure_transfer import _domain,_validate_source,retain_pressure_source
    from .hydro_wave_pressure import target_hull_cells
    forces=validate_force_preview(prepared)
    if canonical_hash(document)!=prepared['input_document_hash'] or canonical_hash(mesh_data)!=prepared['mesh_hash']:
        raise ValueError('project/mesh changed before pressure recovery')
    _cancel(cancellation_token,'selected pressure recovery')
    payload=prepared['force_preview'];realization=WaveRealization.from_dict(payload['realization'])
    _validate_force_source(forces,realization,_config(payload['settings']))
    if start is None and end is None and prepared.get('selected_window') is not None:
        start,end=prepared['selected_window']
    window=select_sample_window(payload['record_times'],start,end)
    if offline_history is not None:
        history=pressure_history_from_dict(offline_history)
        if history.content_sha256!=payload['source_history_id'] or history.realization_json!=_json(realization.to_dict()):
            raise ValueError('offline applied source/realization identity changed')
        bank=PressureResponseBank.from_dict(json.loads(history.source_payload_json))
        validate_pressure_binding(forces,bank)
        source_provenance=json.loads(bank.provenance_json)
        request=json.loads(forces.request_json)
        bound_current=isinstance(source_provenance,dict) and source_provenance.get('excitation_bank_id')==forces.bank_id
        bound_migration=request.get('migration',{}).get('original_pressure_bank_id')==bank.bank_id
        if not (bound_current or bound_migration):
            raise ValueError('offline pressure field identity is not bound to the prepared excitation bank')
        # Applied offline source is authoritative even when solution cache gone.
        from anyloads.wave_preview import source_resultants
        # Compare unit six-response fields without a time/panel history.
        weights=-bank.normals*bank.areas[:,None]
        integrated=bank.responses@np.concatenate((weights,np.cross(bank.centers,weights)),axis=1)
        if tuple(bank.omegas)!=tuple(forces.omegas) or not np.allclose(integrated,forces.responses,rtol=1e-8,atol=1e-6):
            raise ValueError('offline source differs from prepared excitation responses')
    else:
        bank=recover_pressure_bank(forces,cache_directory=prepared['solution_cache_root'],
            cancel=lambda:_cancel(cancellation_token,'selected pressure recovery'))
    _validate_source(bank)
    model=project_from_dict(document);mesh=mesh_from_dict(mesh_data);_domain(model)
    cells=target_hull_cells(model,mesh,{s.name:s.ref.id for s in model.wet_surfaces})
    if progress:progress('Recovering selected-window source pressures and mapping current wet mesh')
    plan=build_pressure_mapping_plan(bank,*cells,mesh_content_sha256=prepared['mesh_hash'])
    fields,evidence=map_pressure_bank(bank,plan,*cells[1:4],surface_ids=cells[4],mesh_content_sha256=prepared['mesh_hash'])
    provenance=_json(dict(settings=payload['settings'],source_bank_id=bank.bank_id,excitation_bank_id=forces.bank_id,
        mapping_plan_id=plan.plan_id,mapping_algorithm=plan.algorithm,frequency_transfer_evidence=evidence,draft_m=1.5))
    if payload['settings'].get('wave_type','regular')=='regular':
        if len(realization.components)!=1 or len(bank.omegas)!=1:raise ValueError('one regular-wave component required')
        component=realization.components[0]
        phasors=fields[0]*component.amplitude*np.exp(1j*component.phase)
        current=MappedPressureHistory(window.times,tuple(cells[0]),tuple(phasors.real),tuple(phasors.imag),
            component.omega,prepared['mesh_hash'],bank.bank_id,provenance)
    else:
        current=SpectralMappedPressureHistory(window.times,tuple(cells[0]),tuple(bank.omegas),fields.real,fields.imag,
            bank.heading,prepared['mesh_hash'],bank.bank_id,_json(realization.to_dict()),provenance)
    retained=retain_pressure_source(current,bank,realization,model)
    history=SourceAwarePressureHistory(retained.mapped_history,retained.source_payload_json,retained.realization_json,
        retained.domain_json,record_times=window.record_times,selected_window=window.interval,force_payload_json=_json(forces.to_dict()))
    _cancel(cancellation_token,'completed selected wave pressure')
    return dict(prepared,history=history.to_dict(),selected_window=window.interval,selected_times=window.times,
        surfaces=[s.id for s in model.wet_surfaces])
