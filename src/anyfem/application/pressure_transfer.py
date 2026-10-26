"""Retain original hydro fields and reproject to the actual structural mesh.

Effective remapping never edits the project or restamps old element values.
The existing exact target-mesh guard runs on the effective load afterwards.
"""
from collections import OrderedDict
from dataclasses import replace
import hashlib,json,math
import zipfile
from pathlib import Path
import numpy as np

_CACHE=OrderedDict()
_CACHE_BYTES=0
_CACHE_LIMIT=512*1024**2
_CACHE_ENTRIES=4


class PressureRemapError(ValueError):
    pass


def _json(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def _domain(project,surfaces=()):
    expected={'bottom':((0,-2.5,0),(60,2.5,0)),'port':((0,-2.5,0),(60,-2.5,1.5)),
        'starboard':((0,2.5,0),(60,2.5,1.5)),'end0':((0,-2.5,0),(0,2.5,1.5)),
        'end60':((60,-2.5,0),(60,2.5,1.5))}
    wet={s.name:s for s in project.wet_surfaces}
    if set(wet)!=set(expected) or len(project.wet_surfaces)!=5:
        raise PressureRemapError('wave remapping requires the admitted five-surface closed60m barge')
    if surfaces and set(surfaces)!={s.id for s in wet.values()}:
        raise PressureRemapError('wave wet-surface selection changed; regenerate wave input')
    records=[]
    for name in sorted(wet):
        surface=wet[name]
        corners=np.array([project.geometry.vertex_position(v) for v in project.geometry.face_corner_vertices(surface.ref.id)])
        lo,hi=expected[name]
        if not np.allclose(corners.min(axis=0),lo,rtol=0,atol=1e-8) or not np.allclose(corners.max(axis=0),hi,rtol=0,atol=1e-8):
            raise PressureRemapError('wet hull geometry/draft changed outside admitted wave source; regenerate wave input')
        records.append(dict(name=name,wet_id=surface.id,face_id=surface.ref.id,corners=corners.tolist(),
            fluid_side=surface.fluid_side,interior_point=(None if surface.interior_point is None
                else np.asarray(surface.interior_point).tolist())))
    return json.loads(_json(dict(kind='fixed_barge60_draft1.5_v1',surfaces=records)))


def bank_from_regular(source):
    from anyhydro.pressure_bank import PressureResponseBank
    source.validate()
    return PressureResponseBank(source.vertices,source.faces,source.surface_ids,source.centers,source.normals,
        source.areas,[source.omega],np.asarray(source.pressure)[None,:],source.heading%math.tau,
        source.water_depth,source.rho,provenance_json=_json(dict(original_source_id=source.source_id,
            regular_provider=source.provider,kind='validated_regular_pressure_owner_projection')))


def _validate_source(bank):
    from .hydro_wave_pressure import _source_mesh
    bank.validate();v,f,labels=_source_mesh()
    if not np.array_equal(bank.vertices,v) or not np.array_equal(bank.faces,f) or bank.surface_ids!=labels:
        raise PressureRemapError('original pressure source does not match admitted wet hull')


def retain_pressure_source(history,bank,realization,project,*,surfaces=(),certificate=None):
    from anyloads.source_pressure_history import SourceAwarePressureHistory
    _validate_source(bank)
    lineage=dict(domain=_domain(project,surfaces),source_bank_id=bank.bank_id,original_source_id=history.source_id,
        recovery_certificate=certificate)
    return SourceAwarePressureHistory(history,_json(bank.to_dict()),_json(realization.to_dict()),_json(lineage))


def retained_regular_settings(history):
    """Restore exact retained one-component parameters, refusing other grids."""
    from anyloads.source_pressure_history import SourceAwarePressureHistory
    from anyloads.pressure_history import MappedPressureHistory
    from anyloads.sea_state import WaveRealization
    from anyhydro.pressure_bank import PressureResponseBank
    from .hydro_wave_input import RegularWaveInput
    if not isinstance(history,SourceAwarePressureHistory) or type(history.mapped_history) is not MappedPressureHistory:
        raise PressureRemapError('saved wave inputs unavailable; recreate this history using explicit wave inputs')
    bank=PressureResponseBank.from_dict(json.loads(history.source_payload_json));_validate_source(bank)
    r=WaveRealization.from_dict(json.loads(history.realization_json))
    if len(r.components)!=1 or len(bank.omegas)!=1 or r.components[0].omega!=history.mapped_history.omega:
        raise PressureRemapError('saved regular wave component inventory differs; recreate explicit wave inputs')
    c=r.components[0]
    if len(history.times)<2 or history.times[0]!=0.:
        raise PressureRemapError('retained time grid cannot be represented by duration/dt inputs; recreate explicit wave inputs')
    try:
        settings=RegularWaveInput(height=2*c.amplitude,period=math.tau/c.omega,
            heading=c.direction_degrees,depth=bank.water_depth,phase=math.degrees(c.phase),
            duration=history.times[-1],dt=history.times[1]-history.times[0])
        predicted=settings.times
    except ValueError as error:
        raise PressureRemapError('retained time grid exceeds regular input limits; recreate explicit wave inputs') from error
    if len(predicted)!=len(history.times) or not np.allclose(predicted,history.times,rtol=0.,
            atol=1e-12*max(1.,history.times[-1])):
        raise PressureRemapError('retained nonuniform time grid cannot be represented by duration/dt; recreate explicit wave inputs')
    from dataclasses import asdict
    return asdict(settings)


def effective_wave_pressure(project,mesh,load):
    """Shared preview/solve hook, returning an effective immutable load clone."""
    from anyloads.source_pressure_history import SourceAwarePressureHistory
    history=load.history
    if not isinstance(history,SourceAwarePressureHistory):return load
    try:
        from anyloads.spectral_pressure_history import build_pressure_mapping_plan,map_pressure_bank,SpectralMappedPressureHistory
        from anyhydro.pressure_bank import PressureResponseBank
        from anymesher.serialize import mesh_to_dict
        from ..document import canonical_hash
        from .hydro_wave_pressure import target_hull_cells
        domain=_domain(project,load.surfaces);lineage=json.loads(history.domain_json)
        if lineage['domain']!=domain:
            raise PressureRemapError('wave source wet geometry/selection changed; regenerate wave input')
        mesh_hash=canonical_hash(mesh_to_dict(mesh))
        key=(history.content_sha256,mesh_hash,hashlib.sha256(_json(domain).encode()).hexdigest())
        global _CACHE_BYTES
        if key in _CACHE:
            made,charge=_CACHE.pop(key);_CACHE[key]=(made,charge)
            return replace(load,history=made,mesh_content_sha256=made.mesh_content_sha256)
        bank=PressureResponseBank.from_dict(json.loads(history.source_payload_json));_validate_source(bank)
        if bank.bank_id!=lineage['source_bank_id']:
            raise PressureRemapError('retained pressure source identity changed')
        if mesh_hash==history.mesh_content_sha256:
            # Still validate retained source/domain before accepting the original
            # mapped record; no projection is needed on its producing mesh.
            made=history
        else:
            exterior={s.name:s.ref.id for s in project.wet_surfaces}
            cells=target_hull_cells(project,mesh,exterior)
            plan=build_pressure_mapping_plan(bank,*cells,mesh_content_sha256=mesh_hash)
            mapped,evidence=map_pressure_bank(bank,plan,*cells[1:4],surface_ids=cells[4],mesh_content_sha256=mesh_hash)
            try:provenance=json.loads(history.provenance) if history.provenance else {}
            except (ValueError,TypeError):provenance={'retained_provenance':history.provenance}
            if not isinstance(provenance,dict):provenance={'retained_provenance':history.provenance}
            provenance.update(remapped_from_mesh=history.mesh_content_sha256,source_bank_id=bank.bank_id,
                mapping_plan_id=plan.plan_id,mapping_algorithm=plan.algorithm,frequency_transfer_evidence=evidence)
            current=SpectralMappedPressureHistory(history.times,tuple(cells[0]),tuple(bank.omegas),
                mapped.real,mapped.imag,bank.heading,mesh_hash,bank.bank_id,history.realization_json,_json(provenance))
            made=SourceAwarePressureHistory(current,history.source_payload_json,history.realization_json,history.domain_json)
        frequency_count=len(bank.omegas);element_count=len(made.element_ids)
        charge=128*frequency_count*element_count+len(history.source_payload_json.encode())+16*len(history.times)
        while _CACHE and (_CACHE_BYTES+charge>_CACHE_LIMIT or len(_CACHE)>=_CACHE_ENTRIES):
            _,(_,old_charge)=_CACHE.popitem(last=False);_CACHE_BYTES-=old_charge
        if charge<=_CACHE_LIMIT:
            _CACHE[key]=(made,charge);_CACHE_BYTES+=charge
        return replace(load,history=made,mesh_content_sha256=made.mesh_content_sha256)
    except PressureRemapError:raise
    except ImportError as error:
        raise PressureRemapError('retained wave source needs the optional ANYhydro data reader; '
            'enable that dependency before pressure remapping') from error
    except (ValueError,KeyError,TypeError) as error:
        raise PressureRemapError(f'cannot remap original wave source: {error}; regenerate wave input on current mesh') from error


def _legacy_source(path,source_id):
    try:
        from anyhydro.regular_wave_pressure import RegularWavePressure
        path=Path(path);before=path.read_bytes()
        with np.load(path,allow_pickle=False) as p:
            source=RegularWavePressure(p['vertices'],p['faces'],tuple(str(x) for x in p['surface_ids']),
                p['centers'],p['normals'],p['areas'],p['pressure'],float(p['omega']),float(p['heading']),
                float(p['water_depth']),float(p['rho']),str(p['source_id']),str(p['provider']),p['excitation_wrench'])
        source.validate()
        if source.source_id!=source_id or path.read_bytes()!=before:
            raise PressureRemapError('legacy wave source identity changed')
        return source,hashlib.sha256(before).hexdigest()
    except PressureRemapError:raise
    except ImportError as error:
        raise PressureRemapError('legacy wave recovery needs the optional ANYhydro data reader; '
            'original mapped load is unchanged') from error
    except (zipfile.BadZipFile,EOFError,ValueError,OSError,KeyError,TypeError,IndexError) as error:
        raise PressureRemapError(f'legacy pressure source is malformed or unreadable: {error}') from error


def recover_legacy_history(project,history,source,old_mesh,*,surfaces=(),source_digest=''):
    """Exact global-complex coefficient recovery, never a fitted approximation."""
    from anyloads.pressure_history import MappedPressureHistory
    from anyloads.hydro_pressure import transfer_panel_pressure
    from anyloads.marine import AiryComponent
    from anyloads.sea_state import WaveRealization
    from anymesher.serialize import mesh_to_dict
    from ..document import canonical_hash
    from .hydro_wave_pressure import target_hull_cells
    if type(history) is not MappedPressureHistory or source.source_id!=history.source_id or source.omega!=history.omega:
        raise PressureRemapError('legacy wave source/frequency lineage mismatch')
    if canonical_hash(mesh_to_dict(old_mesh)).removeprefix('sha256:')!=history.mesh_content_sha256.removeprefix('sha256:'):
        raise PressureRemapError('legacy producing structural mesh identity mismatch')
    _domain(project,surfaces)
    cells=target_hull_cells(project,old_mesh,{s.name:s.ref.id for s in project.wet_surfaces})
    transfer=transfer_panel_pressure(source,*cells)
    positions={eid:i for i,eid in enumerate(transfer.element_ids)}
    if set(positions)!=set(history.element_ids):raise PressureRemapError('legacy producing element inventory differs')
    unit=transfer.phasors[[positions[eid] for eid in history.element_ids]]
    old=np.asarray(history.real)+1j*np.asarray(history.imag)
    index=int(np.argmax(np.abs(unit)))
    if abs(unit[index])==0:raise PressureRemapError('legacy coefficient is ambiguous: unit source is zero')
    alpha=old[index]/unit[index]
    scale=max(1.,float(np.max(np.abs(old))),float(abs(alpha)*np.max(np.abs(unit))))
    residual=float(np.max(np.abs(alpha*unit-old)));bound=1e-12*scale
    if not np.isfinite(alpha) or not math.isfinite(residual) or residual>bound:
        raise PressureRemapError('legacy phasors do not certify one exact source coefficient; regenerate wave input')
    bank=bank_from_regular(source)
    realization=WaveRealization((AiryComponent(float(abs(alpha)),source.omega,
        math.degrees(bank.heading),float(np.angle(alpha))),))
    certificate=dict(kind='exact_global_complex_coefficient_v1',alpha_real=float(alpha.real),alpha_imag=float(alpha.imag),
        residual_max=residual,roundoff_bound=bound,scale=scale,pivot_element_id=history.element_ids[index],
        original_source_id=source.source_id,source_file_sha256=source_digest,
        old_target_mesh=history.mesh_content_sha256)
    return retain_pressure_source(history,bank,realization,project,surfaces=surfaces,certificate=certificate)


def upgrade_legacy_wave_sources(project,project_path):
    """Atomic in-memory path-aware upgrade; never writes the user's file.

    A missing recovery context leaves old records valid on their original mesh.
    Any attempted upgrade failure likewise leaves every original load unchanged.
    """
    from anyloads.pressure_history import MappedPressureHistory
    from ..io.artifacts import ArtifactStore
    candidates=[(case,index,load) for case in project.load_cases.values() if case.parent is None
        for index,load in enumerate(case.wet_pressures) if type(load.history) is MappedPressureHistory]
    if not candidates:return project
    path=Path(project_path);source_path=path.parent/'prepared-source.npz'
    if not source_path.is_file():return project
    replacements=[];store=ArtifactStore(path)
    try:
        for case,index,load in candidates:
            history=load.history;source,digest=_legacy_source(source_path,history.source_id)
            matches=[record for record in project.mesh_records.values() if record.artifact_id and
                record.mesh_hash.removeprefix('sha256:')==history.mesh_content_sha256.removeprefix('sha256:')]
            if not matches:raise PressureRemapError('original pressure-transfer mesh artifact is unavailable')
            meshes=[store.read_mesh(project.artifacts[record.artifact_id]) for record in matches]
            upgraded=recover_legacy_history(project,history,source,meshes[0],surfaces=load.surfaces,source_digest=digest)
            replacements.append((case,index,replace(load,history=upgraded)))
    except (ValueError,OSError,KeyError) as error:
        # Keep the document readable and unmodified on its producing mesh; a
        # later mismatching target is refused by the existing caller guard.
        project._wave_source_recovery_error=str(error)
        return project
    for case,index,load in replacements:case.wet_pressures[index]=load
    return project
