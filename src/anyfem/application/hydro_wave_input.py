"""Regular-wave input service and one atomic, undoable load-series application."""
from dataclasses import dataclass,asdict
from pathlib import Path
import hashlib,json,math
import numpy as np
from ..commands import Command
from ..document import canonical_hash,DocumentSession
from ..io.project_file import project_to_dict,project_from_dict


@dataclass(frozen=True)
class RegularWaveInput:
    name: str='Regular wave'
    height: float=1.
    period: float=6.
    heading: float=0.
    depth: float=200.
    phase: float=0.
    duration: float=10.
    dt: float=.1
    wave_type: str='regular'
    include_hydrostatic: bool=False
    follow_surface: bool=False

    def __post_init__(self):
        if self.wave_type!='regular':raise ValueError('only regular waves are currently available')
        if type(self.include_hydrostatic) is not bool:raise ValueError('include_hydrostatic must be a boolean')
        if type(self.follow_surface) is not bool:raise ValueError('follow_surface must be a boolean')
        # Following the instantaneous surface implies (and locks) the hydrostatic inclusion.
        if self.follow_surface:object.__setattr__(self,'include_hydrostatic',True)
        if not self.name.strip():raise ValueError('a wave load needs a name')
        if not all(math.isfinite(x) for x in (self.height,self.period,self.heading,self.depth,self.phase,self.duration,self.dt)):
            raise ValueError('wave inputs must be finite SI values')
        if self.height<0 or min(self.period,self.depth,self.duration,self.dt)<=0 or self.depth<=1.5:
            raise ValueError('height must be nonnegative; period, duration and dt positive; depth greater than draft')
        if self.duration/self.dt>10000:raise ValueError('at most10001 wave samples are admitted')

    @property
    def times(self):
        t=np.arange(int(math.floor(self.duration/self.dt))+1,dtype=float)*self.dt
        if self.duration-t[-1]>1e-10:t=np.r_[t,self.duration]
        else:t[-1]=self.duration
        return tuple(t.tolist())


def source_for_settings(settings,cache_path=None):
    """Reuse a verified same-physics source; changed frequency requires new BEM."""
    from anyhydro.regular_wave_pressure import RegularWavePressure,solve_regular_wave_pressure
    from .hydro_wave_pressure import _source_mesh
    cache_digest=None
    source=None
    if cache_path:
        path=Path(cache_path)
        before=path.read_bytes();cache_digest=hashlib.sha256(before).hexdigest()
        with np.load(path,allow_pickle=False) as p:
            source=RegularWavePressure(p['vertices'],p['faces'],tuple(str(x) for x in p['surface_ids']),
                p['centers'],p['normals'],p['areas'],p['pressure'],float(p['omega']),float(p['heading']),
                float(p['water_depth']),float(p['rho']),str(p['source_id']),str(p['provider']),p['excitation_wrench'])
        source.validate()
        v,f,s=_source_mesh()
        if not np.array_equal(source.vertices,v) or not np.array_equal(source.faces,f) or source.surface_ids!=s or source.rho!=1025.:
            raise ValueError('cached source hull/mesh/fluid identity differs from admitted barge')
        if path.read_bytes()!=before:raise ValueError('source cache changed during read')
    same=(source is not None and source.omega==2*np.pi/settings.period
        and source.heading==math.radians(settings.heading) and source.water_depth==settings.depth)
    if not same:
        source=solve_regular_wave_pressure(*_source_mesh(),period=settings.period,
            heading=math.radians(settings.heading),water_depth=settings.depth)
    return source,cache_digest,bool(same)


def compute_wave_input(*,project=None,document,mesh_data,settings,cache_path=None,
                       source_bank_cache_directory=None,progress=None,cancellation_token=None):
    """Executed only in an owned child process by the Qt input controller."""
    from anymesher.serialize import mesh_from_dict
    from anyloads.hydro_pressure import transfer_panel_pressure
    from anyloads.pressure_history import MappedPressureHistory
    from .hydro_wave_pressure import target_hull_cells
    def checkpoint():
        if cancellation_token is not None:cancellation_token.raise_if_cancelled('wave pressure mapping')
    checkpoint()
    from .irregular_wave_input import IrregularWaveInput
    config=(IrregularWaveInput if settings.get('wave_type','regular')=='irregular' else RegularWaveInput)(**settings)
    if config.follow_surface:
        from .wave_surface_pressure import prepare_surface_preview,select_surface_window
        prepared=prepare_surface_preview(document=document,mesh_data=mesh_data,settings=settings,
            source_bank_cache_directory=source_bank_cache_directory,progress=progress,cancellation_token=cancellation_token)
        return select_surface_window(prepared)
    model=project_from_dict(document);mesh=mesh_from_dict(mesh_data)
    exterior={s.name:s.ref.id for s in model.wet_surfaces}
    if set(exterior)!={'bottom','port','starboard','end0','end60'}:
        raise ValueError('prepare/open the closed60m barge with its exact five wet hull surfaces first')
    # Explicit authored geometry contract: current project, no replacement model.
    bounds={'bottom':((0,-2.5,0),(60,2.5,0)), 'port':((0,-2.5,0),(60,-2.5,1.5)),
        'starboard':((0,2.5,0),(60,2.5,1.5)), 'end0':((0,-2.5,0),(0,2.5,1.5)),
        'end60':((60,-2.5,0),(60,2.5,1.5))}
    for name,fid in exterior.items():
        corners=np.array([model.geometry.vertex_position(v) for v in model.geometry.face_corner_vertices(fid)])
        lo,hi=bounds[name]
        if not np.allclose(corners.min(axis=0),lo,atol=1e-8,rtol=0.) or not np.allclose(corners.max(axis=0),hi,atol=1e-8,rtol=0.):
            raise ValueError('current hull wet geometry/draft is outside the admitted60m/1.5m contract')
    cells=target_hull_cells(model,mesh,exterior)
    if config.wave_type=='irregular':
        from .irregular_wave_input import pressure_bank_for_settings
        from anyloads.spectral_pressure_history import (build_pressure_mapping_plan,
            map_pressure_bank,SpectralMappedPressureHistory)
        bank,realization=pressure_bank_for_settings(config,cache_directory=source_bank_cache_directory,
            cancellation_token=cancellation_token,progress=progress)
        checkpoint()
        mesh_hash=canonical_hash(mesh_data)
        plan=build_pressure_mapping_plan(bank,*cells,mesh_content_sha256=mesh_hash)
        mapped,evidence=map_pressure_bank(bank,plan,*cells[1:4],surface_ids=cells[4],mesh_content_sha256=mesh_hash)
        provenance=json.dumps(dict(settings=asdict(config),source_provider=json.loads(bank.provenance_json),
            source_bank_id=bank.bank_id,mapping_algorithm=plan.algorithm,
            mapping_plan_id=plan.plan_id,
            target_geometry_sha256=plan.target_geometry_sha256,mapping_max_distance=plan.max_distance,
            frequency_transfer_evidence=evidence,draft_m=1.5,normalization='declared finite frequency band'),
            sort_keys=True,allow_nan=False)
        history=SpectralMappedPressureHistory(config.times,tuple(cells[0]),tuple(bank.omegas),
            mapped.real,mapped.imag,bank.heading,mesh_hash,bank.bank_id,
            json.dumps(realization.to_dict()),provenance)
        from .pressure_transfer import retain_pressure_source
        history=retain_pressure_source(history,bank,realization,model,surfaces=[s.id for s in model.wet_surfaces])
        checkpoint()
        return {'name':config.name,'history':history.to_dict(),'surfaces':[s.id for s in model.wet_surfaces],
            'source_cache_path':None,'source_cache_sha256':None,
            'input_document_hash':canonical_hash(document),'mesh_hash':mesh_hash}
    source,digest,reused=source_for_settings(config,cache_path)
    checkpoint()
    transfer=transfer_panel_pressure(source,*cells)
    phasors=.5*config.height*np.exp(1j*math.radians(config.phase))*transfer.phasors
    provenance=json.dumps({'settings':asdict(config),'source_provider':source.provider,
        'source_cache_sha256':digest,'source_reused':reused,'draft_m':1.5,
        'force_transfer_error':float(np.linalg.norm(transfer.target_wrench[:3]-transfer.source_wrench[:3])/np.linalg.norm(transfer.source_wrench[:3])),
        'moment_transfer_error':float(np.linalg.norm(transfer.target_wrench[3:]-transfer.source_wrench[3:])/np.linalg.norm(transfer.source_wrench[3:]))},sort_keys=True)
    history=MappedPressureHistory(config.times,tuple(cells[0]),tuple(phasors.real),tuple(phasors.imag),
        source.omega,canonical_hash(mesh_data),source.source_id,provenance)
    from .pressure_transfer import retain_pressure_source,bank_from_regular
    from anyloads.sea_state import WaveRealization
    from anyloads.marine import AiryComponent
    bank=bank_from_regular(source)
    realization=WaveRealization((AiryComponent(.5*config.height,source.omega,math.degrees(bank.heading),math.radians(config.phase)),))
    history=retain_pressure_source(history,bank,realization,model,surfaces=[s.id for s in model.wet_surfaces])
    checkpoint()
    return {'name':config.name,'history':history.to_dict(),'surfaces':[s.id for s in model.wet_surfaces],
        'source_cache_path':None if cache_path is None else str(cache_path),'source_cache_sha256':digest,
        'input_document_hash':canonical_hash(document),'mesh_hash':canonical_hash(mesh_data)}


def prepare_wave_preview(*,project=None,document,mesh_data,settings,cache_path=None,
        source_bank_cache_directory=None,progress=None,cancellation_token=None,retained_history=None,retained_preview=None,
        document_content_hash=None,mesh_content_hash=None,preview_context_version=None,repair_cache=False):
    """Force-only stage; pressure recovery/mapping are deferred until Apply."""
    if settings.get('follow_surface') is True:
        # Separate additive mode: explicit field preparation + corrected applied-resultant preview.
        from .wave_surface_pressure import prepare_surface_preview
        return prepare_surface_preview(project=project,document=document,mesh_data=mesh_data,settings=settings,
            source_bank_cache_directory=source_bank_cache_directory,progress=progress,cancellation_token=cancellation_token,
            document_content_hash=document_content_hash,mesh_content_hash=mesh_content_hash)
    from .wave_force_input import prepare_force_preview
    return prepare_force_preview(project=project,document=document,mesh_data=mesh_data,settings=settings,
        cache_path=cache_path,source_bank_cache_directory=source_bank_cache_directory,progress=progress,
        cancellation_token=cancellation_token,retained_history=retained_history,retained_preview=retained_preview,
        document_content_hash=document_content_hash,mesh_content_hash=mesh_content_hash,preview_context_version=preview_context_version,
        repair_cache=repair_cache)


def recover_prepared_wave_input(*,project=None,document,mesh_data,prepared,start=None,end=None,
        offline_history=None,progress=None,cancellation_token=None):
    """Apply-stage owned-child entry; cached solution recovery, no new BEM."""
    if prepared.get('preview_kind')=='wave_surface_v1':
        from .wave_surface_pressure import recover_surface_selection
        return recover_surface_selection(document=document,mesh_data=mesh_data,prepared=prepared,start=start,end=end)
    from .wave_force_input import recover_selected_wave_input
    return recover_selected_wave_input(project=project,document=document,mesh_data=mesh_data,prepared=prepared,
        start=start,end=end,offline_history=offline_history,progress=progress,cancellation_token=cancellation_token)


def select_prepared_wave_window(result,start=None,end=None):
    """Choose original absolute sample times, retaining the prepared full source."""
    from dataclasses import replace
    from anyloads.pressure_history import pressure_history_from_dict
    from anyloads.source_pressure_history import SourceAwarePressureHistory
    from anyloads.wave_preview import select_sample_window
    if result.get('preview_kind')=='wave_surface_v1':
        from .wave_surface_pressure import select_surface_window
        return select_surface_window(result,start,end)
    if result.get('preview_kind')=='force_only_v1':
        from .wave_force_input import validate_force_preview
        validate_force_preview(result)
        window=select_sample_window(result['force_preview']['record_times'],start,end)
        return {**result,'selected_window':window.interval,'selected_times':window.times}
    history=pressure_history_from_dict(result['history'])
    if not isinstance(history,SourceAwarePressureHistory):
        raise ValueError('window selection requires a prepared source-aware wave record')
    if 'preview_source_bank_id' in result:
        from .pressure_transfer import validate_preview_source
        validate_preview_source(history,result['preview_source_bank_id'])
        if result.get('preview_content_hash')!=canonical_hash({key:result[key] for key in
                ('preview_times','preview_resultants','preview_source_bank_id','preview_moment_origin')}):
            raise ValueError('prepared resultant preview content changed; prepare again')
    window=select_sample_window(history.full_record_times,start,end)
    mapped=replace(history.mapped_history,times=window.times,content_sha256='')
    selected=SourceAwarePressureHistory(mapped,history.source_payload_json,history.realization_json,
        history.domain_json,record_times=window.record_times,selected_window=window.interval,force_payload_json=history.force_payload_json)
    return {**result,'history':selected.to_dict()}


class ApplyWavePressure(Command):
    label='Hydrodynamic wave pressure time series'
    def __init__(self,result,*,replace_legacy=False):
        self.result=result;self.replace_legacy=replace_legacy

    def do(self,project):
        from anyloads.pressure_history import pressure_history_from_dict
        from ..model import WetPressure
        from ..model.records import AnalysisDefinition
        if canonical_hash(project_to_dict(project))!=self.result['input_document_hash']:
            raise ValueError('project changed while calculating wave pressure; nothing applied')
        history=pressure_history_from_dict(self.result['history'])
        name=self.result['name']
        legacy=[c for c in project.load_cases.values() if c.series==name and c.parent is None and c.times is None
            and len(c.wet_pressures)==1 and c.wet_pressures[0].mapping is not None
            and c.wet_pressures[0].mesh_content_sha256==history.mesh_content_sha256]
        removed={c.name for c in legacy} if self.replace_legacy else set()
        existing=project.load_cases.get(name)
        replacing_owner=(existing is not None and existing.times is not None
            and any(load.history is not None for load in existing.wet_pressures) and self.replace_legacy)
        from .wave_hydrostatic import (wave_loads_without_companion,history_includes_hydrostatic,
            make_static_pressure)
        def mixed(case):
            # Only the exact validated app-owned hydrostatic companion is set aside; anything else is unrelated.
            wet=wave_loads_without_companion(case)
            return (len(wet)!=1 or bool(case.point_loads or case.pressures or
                case.line_loads or case.surface_tractions or case.connection_loads or case.parameters)
                or case.gravity is not None or case.follower_pressure
                or any(load.envelope is not None or load.scale!=1. for load in wet))
        from .wave_surface_pressure import is_surface_history
        composed=is_surface_history(history)
        # A wave-following history already contains the hydrostatic term: never append the companion (no double pressure).
        include=history_includes_hydrostatic(history) and not composed
        if self.replace_legacy and (any(mixed(case) for case in legacy) or (replacing_owner and mixed(existing))):
            raise ValueError('wave replacement would discard additional loads, parameters or envelopes; choose a new series name')
        if replacing_owner:
            removed.update(c.name for c in project.series_children(name));removed.add(name)
        children=set(project.series_step_names(name,len(history.times)))
        if (name in project.load_cases and not replacing_owner) or (children & (set(project.load_cases)-removed)):
            raise ValueError('wave series name already exists; choose a new name or explicitly convert legacy snapshots')
        if self.replace_legacy and not legacy and not replacing_owner:
            raise ValueError('no matching legacy mapped snapshot series to convert')
        self.before_cases=dict(project.load_cases);self.before_analyses=dict(project.analyses)
        structural_hash=DocumentSession(project).revision.model_hash
        try:
            for key in removed:del project.load_cases[key]
            if replacing_owner:
                for aid,old in tuple(project.analyses.items()):
                    if old.target_id==name and old.name==name+' — Batch linear static' and old.type=='batch_linear_static':
                        del project.analyses[aid]
            parent=project.add_load_series(name,history.times)
            dynamic=WetPressure(history=history,mapping_key='element',
                surfaces=tuple(self.result['surfaces']),
                name=('Wave-following pressure (hydrostatic + incident/diffraction)' if composed
                    else 'Incident + diffraction pressure history'))
            parent.wet_pressures.append(dynamic)
            # One constant companion per owning series, steady over every selected original time.
            if include:parent.wet_pressures.append(make_static_pressure(dynamic))
            analysis=AnalysisDefinition(name+' — Batch linear static',type='batch_linear_static',
                target_kind='load_cases',target_id=name,
                settings={'load_cases':[c.name for c in project.series_children(name)]})
            project.add_analysis(analysis)
            if DocumentSession(project).revision.model_hash!=structural_hash:
                raise ValueError('wave load unexpectedly changed structural mesh inputs')
            self.after_cases=dict(project.load_cases);self.after_analyses=dict(project.analyses)
            return parent
        except BaseException:
            self.undo(project);raise

    def undo(self,project):
        project.load_cases=dict(self.before_cases);project.analyses=dict(self.before_analyses)

    def redo(self,project):
        project.load_cases=dict(self.after_cases);project.analyses=dict(self.after_analyses)
        return project.load_cases[self.result['name']]
