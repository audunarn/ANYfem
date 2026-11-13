"""Prescribed external panel pressures: geometry-bound preparation and application.

No hydrodynamic calculation, gravity, motion RAO or inertia is added here.
"""
from dataclasses import asdict
import json
import numpy as np
from ..commands import Command
from ..document import canonical_hash


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _binding(project, surfaces, dipole_surfaces=()):
    if project.imperfections:
        raise ValueError('prescribed pressure mapping on imperfect geometry is not admitted; prepare a matching reference geometry')
    from ..io.project_file import project_to_dict
    document=project_to_dict(project)  # adopt attachment regions before binding their values
    selected = [s for s in project.wet_surfaces if s.id in surfaces]
    if len(selected) != len(set(surfaces)) or not selected:
        raise ValueError('select existing wet structural surfaces')
    records=[dict(id=s.id, face=s.ref.id, region=None if s.region is None else s.region.id, fluid_side=s.fluid_side,
        interior_point=None if s.interior_point is None else list(s.interior_point),
        corners=[list(project.geometry.vertex_position(v)) for v in
                 project.geometry.face_corner_vertices(s.ref.id)]) for s in sorted(selected,key=lambda s:s.id)]
    payload=dict(surfaces=records,geometry=document['geometry'],regions=_region_binding(project),
                 shell_formulations=project.shell_formulation_policy.to_dict())
    if dipole_surfaces:payload['dipole_surfaces']=sorted(dipole_surfaces)
    return canonical_hash(payload)


def _region_binding(project):
    return dict(regions=project.regions.to_list(),attachments=[(r.kind,r.id,b.id) for r,b in
        sorted(project.geometry_attachment_regions.items(),key=lambda item:(item[0].kind,item[0].id))])


def validate_prescribed_scope(project,load):
    expected=set(json.loads(load.history.binding_json)['surfaces'])
    actual=set(load.surfaces) if load.surfaces else {s.id for s in project.wet_surfaces}
    if actual!=expected:raise ValueError('prescribed pressure surface selection changed; prepare again')


def _transform(value):
    a=np.asarray(value,dtype=float)
    if (a.shape!=(4,4) or not np.isfinite(a).all() or
        not np.allclose(a[3], [0,0,0,1],rtol=0,atol=1e-12) or
        not np.allclose(a[:3,:3].T@a[:3,:3],np.eye(3),rtol=0,atol=1e-10) or
        not np.isclose(np.linalg.det(a[:3,:3]),1.,rtol=0,atol=1e-10)):
        raise ValueError('source_to_project must be a proper rigid 4 by 4 transform')
    return a


def prepare_prescribed_pressure(project,mesh,bank,realization,*,times,surfaces,
        source_to_project,limits,force_relative_limit,moment_relative_limit,
        force_absolute_limit=0.,moment_absolute_limit=0.,cancel=None,spectrum_report=None,dipole_surfaces=()):
    """Map a canonical bank once. Limits are caller-declared, never enlarged."""
    from anygeometry.panel_pressure_projection import MappingLimits,prepare_panel_projection
    from anygeometry.floating_overlap import polygon_area,polygon_plane
    from anyloads.prescribed_pressure import PrescribedPressureHistory
    from anyloads.hydro_pressure import pressure_wrench
    from anymesher.serialize import mesh_to_dict
    from anyfesolver.floating_pressure_regions import region_nodal_vectors
    from .floating_source import MeanPose,prepare_target_cells
    from ..solve.build import build_fe_model,_wet_elements
    def checkpoint():
        if cancel is not None:
            if hasattr(cancel,'raise_if_cancelled'):cancel.raise_if_cancelled('OrcaWave pressure mapping')
            elif cancel():raise ValueError('OrcaWave pressure mapping cancelled')
    checkpoint();bank.validate();bank.validate_geometry()
    transform=_transform(source_to_project);rotation=transform[:3,:3];offset=transform[:3,3]
    # Existing target preparation calls its hydrodynamic frame "world".
    pose=MeanPose(rotation.T,-rotation.T@offset)
    surfaces=tuple(surfaces);dipole_surfaces=tuple(dipole_surfaces)
    if len(set(dipole_surfaces))!=len(dipole_surfaces) or not set(dipole_surfaces)<=set(surfaces):
        raise ValueError('dipole surfaces must be a distinct subset of selected structural surfaces')
    domain=_binding(project,surfaces,dipole_surfaces)
    from ..io.project_file import project_to_dict,project_from_dict
    selected=project_from_dict(project_to_dict(project))
    selected.wet_surfaces[:]=[s for s in selected.wet_surfaces if s.id in surfaces]
    selected.load_cases.clear();selected.analyses.clear()
    cells=prepare_target_cells(selected,mesh,pose)
    owners,_=_wet_elements(selected,mesh,selected.wet_surfaces)
    target_kinds=tuple('dipole_jump' if owners[e].id in dipole_surfaces else 'body_pressure' for e in cells.element_ids)
    if set(bank.kinds)!=set(target_kinds):
        raise ValueError('every source pressure kind requires matching nonempty target surface roles')
    target_normals=np.array([polygon_plane(p)[0] for p in cells.polygons_wet])
    polygons=[bank.vertices[list(dict.fromkeys(map(int,face)))] for face in bank.faces]
    ml=limits if isinstance(limits,MappingLimits) else MappingLimits(**limits)
    def cancelled():
        checkpoint()
        return False
    projection=prepare_panel_projection(polygons,cells.polygons_wet,limits=ml,cancel=cancelled,
        source_regions=bank.kinds,target_regions=target_kinds,
        two_sided_regions=('dipole_jump',) if dipole_surfaces else (),
        source_coverage_regions=('dipole_jump',) if dipole_surfaces else ())
    model=build_fe_model(selected,mesh,require_loads=False,require_supports=False,
                         apply_imperfections=False).fe_model
    node_ids=tuple(sorted(mesh.nodes));node_row={n:i for i,n in enumerate(node_ids)}
    from scipy.sparse import coo_matrix
    if len(bank.omegas)*(6*len(node_ids)+len(cells.element_ids))*32>256*1024**2:
        raise ValueError('mapped pressure responses exceed256MiB preparation budget')
    rows=[];columns=[];values=[];arows=[];acolumns=[];avalues=[];proof={}
    for j,eid in enumerate(cells.element_ids):
        checkpoint()
        mask=np.asarray(projection.target_indices)==j
        triangles=projection.triangles[mask]
        indices=projection.source_indices[mask]
        physical=np.asarray([pose.body(t) for t in triangles])
        element=model.mesh.elements[eid]
        areas=np.array([polygon_area(t) for t in triangles])
        for source_index in np.unique(indices):
            chosen=indices==source_index
            orientation=(-1. if bank.normals[source_index]@target_normals[j]<0 else 1.) if bank.kinds[source_index]=='dipole_jump' else 1.
            rv=region_nodal_vectors(element,model.mesh,physical[chosen],
                lambda points,ti:np.ones(len(points)),sign=float(cells.sign[j]*orientation),kernel_proof=proof)
            for n,v in zip(rv.node_ids,rv.vectors):
                for axis,value in enumerate(v):
                    if value:
                        rows.append(node_row[n]*6+axis);columns.append(int(source_index));values.append(float(value))
            arows.append(j);acolumns.append(int(source_index));avalues.append(float(orientation*areas[chosen].sum()/cells.area_element[j]))
    operator=coo_matrix((values,(rows,columns)),shape=(len(node_ids)*6,len(bank.panel_ids))).tocsr()
    average_operator=coo_matrix((avalues,(arows,acolumns)),shape=(len(cells.element_ids),len(bank.panel_ids))).tocsr()
    response=np.asarray(operator@bank.responses.T).T.reshape(len(bank.omegas),len(node_ids),6)
    averages=np.asarray(average_operator@bank.responses.T).T
    xyz=np.array([mesh.nodes[n] for n in node_ids])
    # Compare about source origin, transformed to project coordinates.
    evidence=[]
    def compare(k,p,nodal,mask):
        sw=pressure_wrench(bank.centers[mask],bank.normals[mask],bank.areas[mask],p[mask])
        sw=np.r_[rotation@sw[:3],rotation@sw[3:]]
        tw=np.r_[nodal[:,:3].sum(axis=0),(np.cross(xyz-offset,nodal[:,:3])+nodal[:,3:]).sum(axis=0)]
        record=dict(source_real=sw.real.tolist(),source_imag=sw.imag.tolist(),target_real=tw.real.tolist(),target_imag=tw.imag.tolist())
        for name,sl,rel,absolute in [('force',slice(0,3),force_relative_limit,force_absolute_limit),
                                     ('moment',slice(3,6),moment_relative_limit,moment_absolute_limit)]:
            if not np.isfinite([rel,absolute]).all() or min(rel,absolute)<0:
                raise ValueError('finite nonnegative transfer error limits required')
            error=float(np.linalg.norm(tw[sl]-sw[sl]));reference=float(np.linalg.norm(sw[sl]))
            record[name]=dict(absolute=error,reference_norm=reference,relative=None if reference==0 else error/reference)
            if error>absolute+rel*reference:
                raise ValueError(f'{name} transfer error exceeds declared limit at omega={bank.omegas[k]}: {record[name]}')
        return record
    for k,p in enumerate(bank.responses):
        record=dict(omega=float(bank.omegas[k]),**compare(k,p,response[k],np.ones(len(p),bool)))
        record['by_kind']={}
        for kind in sorted(set(bank.kinds)):
            mask=np.array(bank.kinds)==kind
            nodal=np.asarray(operator[:,mask]@p[mask]).reshape(len(node_ids),6)
            record['by_kind'][kind]=compare(k,p,nodal,mask)
        evidence.append(record)
    binding=dict(surfaces=list(surfaces),domain=domain,source_to_project=transform.tolist(),limits=asdict(ml),
        force_relative_limit=force_relative_limit,moment_relative_limit=moment_relative_limit,
        force_absolute_limit=force_absolute_limit,moment_absolute_limit=moment_absolute_limit,
        dipole_surfaces=list(dipole_surfaces),source_panel_kinds=list(bank.kinds),target_panel_kinds=list(target_kinds),
        projection=dict(projection.diagnostics),frequency_evidence=evidence,
        spectrum_report=spectrum_report,preview_element_ids=list(cells.element_ids),preview_pressure_real=averages.real.tolist(),preview_pressure_imag=averages.imag.tolist(),
        preview_sign=cells.sign.tolist())
    checkpoint()
    return PrescribedPressureHistory(times=tuple(times),node_ids=node_ids,omegas=tuple(bank.omegas),responses=response,
        realization_json=_json(realization.to_dict()),mesh_content_sha256=canonical_hash(mesh_to_dict(mesh)),
        source_payload_json=_json(bank.to_dict()),binding_json=_json(binding))


def effective_prescribed_history(project,mesh,history):
    from anymesher.serialize import mesh_to_dict
    from anyhydro.orcawave_pressure import ImportedPressureBank
    from anyloads.sea_state import WaveRealization
    b=json.loads(history.binding_json)
    bank=ImportedPressureBank.from_dict(json.loads(history.source_payload_json))
    bank.validate_geometry()
    if tuple(bank.omegas)!=tuple(history.omegas):raise ValueError('retained bank frequencies differ from pressure history')
    if tuple(b.get('source_panel_kinds',bank.kinds))!=bank.kinds:
        raise ValueError('retained source pressure kinds changed')
    if _binding(project,b['surfaces'],b.get('dipole_surfaces',()))!=b['domain']:
        raise ValueError('prescribed pressure wet geometry or fluid-side binding changed; prepare again')
    if canonical_hash(mesh_to_dict(mesh))==history.mesh_content_sha256:return history
    keys=('surfaces','source_to_project','limits','force_relative_limit','moment_relative_limit','spectrum_report',
          'force_absolute_limit','moment_absolute_limit')
    return prepare_prescribed_pressure(project,mesh,bank,
        WaveRealization.from_dict(json.loads(history.realization_json)),times=history.times,
        dipole_surfaces=b.get('dipole_surfaces',()),**{k:b[k] for k in keys})


def prescribed_element_pressures(project,mesh,history,time):
    from anyloads.sea_state import WaveRealization
    history=effective_prescribed_history(project,mesh,history)
    history.evaluate(time)  # Validate admitted absolute time.
    b=json.loads(history.binding_json);r=WaveRealization.from_dict(json.loads(history.realization_json))
    p=np.asarray(b['preview_pressure_real'])+1j*np.asarray(b['preview_pressure_imag'])
    weights=np.zeros(len(history.omegas),complex)
    for c in r.components:weights[list(history.omegas).index(c.omega)]+=c.amplitude*np.exp(1j*(c.phase-c.omega*time))
    return tuple(b['preview_element_ids']),np.asarray(b['preview_sign'])*np.real(weights@p)


FAMILY_SPEC='anyhydro_family_pressure_source_v1'
OWR_REFUSAL=('Reading an OrcaWave .owr result needs the separately installed OrcFxAPI and a valid Orcina licence. '
             'Without them, choose a portable pressure bank (.json) created beforehand with '
             'anyfileio.read_orcawave_pressure(path).save(path). ANYfem never starts OrcaWave.')


def orcfxapi_installed():
    """Availability only; OrcFxAPI is imported solely when an .owr file is actually read."""
    from importlib.util import find_spec
    return find_spec('OrcFxAPI') is not None


def bank_from_family_spec(spec_path):
    """ANYhydro native pressure bank from a retained family cache (never solves, never imports Capytaine/Qt).

    The JSON spec holds ``cache`` (family cache folder), ``request`` (a request JSON file, or a JSON object with a
    ``request`` key such as BAND-MANIFEST.json), frequencies as ``omegas`` or ``omegas_file`` (an .npz with ``omega``),
    and optionally ``rao_file`` (an .npz with ``omega`` and complex ``rao`` of shape (n, 6) at exactly those
    frequencies; absent means fixed diffraction).  Relative paths resolve against the spec file.
    """
    from pathlib import Path
    from anyhydro.family_pressure_bank import floating_family_to_pressure_bank
    spec_path=Path(spec_path);spec=json.loads(spec_path.read_text(encoding='utf8'))
    if spec.get('type')!=FAMILY_SPEC:raise ValueError('not an ANYhydro family pressure source spec')
    def at(value):
        p=Path(value);return p if p.is_absolute() else spec_path.parent/p
    request=json.loads(at(spec['request']).read_text(encoding='utf8'))
    request=request.get('request',request)
    if ('omegas' in spec)==('omegas_file' in spec):raise ValueError('give exactly one of omegas and omegas_file')
    if 'omegas' in spec:omegas=np.asarray(spec['omegas'],float)
    else:
        with np.load(at(spec['omegas_file']),allow_pickle=False) as z:omegas=np.asarray(z['omega'],float)
    rao=None
    if spec.get('rao_file'):
        with np.load(at(spec['rao_file']),allow_pickle=False) as z:rao_omega=np.asarray(z['omega'],float);rao=np.asarray(z['rao'],complex)
        if rao_omega.shape!=omegas.shape or not np.array_equal(rao_omega,omegas):
            raise ValueError('RAO frequencies must equal the pressure frequencies exactly; no interpolation is done')
    identity=dict(spec=dict(name=spec_path.name,sha256=file_sha256(spec_path)),
                  request=dict(name=at(spec['request']).name,sha256=file_sha256(at(spec['request']))))
    if 'omegas_file' in spec:identity['omegas']=dict(name=at(spec['omegas_file']).name,sha256=file_sha256(at(spec['omegas_file'])))
    if spec.get('rao_file'):identity['rao']=dict(name=at(spec['rao_file']).name,sha256=file_sha256(at(spec['rao_file'])))
    bank=floating_family_to_pressure_bank(at(spec['cache']),request,omegas,rao,body_id=int(spec.get('body_id',0)),input_files=identity)
    bank.validate_geometry()
    return bank


def inspect_orcawave_file(path,*,kind=None,project=None,cancellation_token=None,progress=None):
    """Read one stored source; ``kind`` ('anyhydro'/'orcawave') makes the selected source authoritative."""
    if kind is not None:
        data=load_pressure_source(kind,path,cancellation_token=cancellation_token)
        return data
    from anyhydro.orcawave_pressure import ImportedPressureBank,SCHEMA,MIXED_SCHEMA
    from pathlib import Path
    def cancel():
        if cancellation_token is not None:cancellation_token.raise_if_cancelled('OrcaWave import')
        return False
    cancel()
    if Path(path).suffix.lower()!='.owr':
        data=json.loads(Path(path).read_text(encoding='utf8'))
        if data.get('type')==FAMILY_SPEC:
            bank=bank_from_family_spec(path);cancel()
            return bank.to_dict()
        if data.get('type') in (SCHEMA,MIXED_SCHEMA):
            bank=ImportedPressureBank.from_dict(data);bank.validate_geometry();cancel()
            return bank.to_dict()
        # Other JSON is a portable OrcaWave document; it needs no OrcFxAPI.
        from anyfileio.orcawave import read_orcawave_pressure
        return read_orcawave_pressure(path,cancel=cancel).to_dict()
    if not orcfxapi_installed():raise ValueError(OWR_REFUSAL)
    from anyfileio.orcawave import read_orcawave_pressure
    try:return read_orcawave_pressure(path,cancel=cancel).to_dict()
    except Exception as error:
        if 'cancel' in type(error).__name__.lower():raise
        raise ValueError(f'OrcaWave result could not be read: {error}. '+OWR_REFUSAL) from error


PRESSURE_SOURCES=('anyhydro','orcawave')


def pressure_source_origin(data):
    """'anyhydro' for ANYhydro-made banks (family cache or stored jumps), 'orcawave' for OrcaWave results/banks."""
    from anyhydro.orcawave_pressure import SCHEMA,MIXED_SCHEMA
    if data.get('type') in (SCHEMA,MIXED_SCHEMA):
        provenance=json.loads(data.get('provenance_json') or '{}')
        if provenance.get('source')=='anyhydro_floating_family_cache' or provenance.get('provider')=='ANYhydro':return 'anyhydro'
        return 'orcawave'
    return 'orcawave'


def load_pressure_source(kind,path,*,cancellation_token=None):
    """Read one stored pressure source chosen by ``kind``; the file must really be of that kind.

    ``anyhydro``: an ANYhydro family-spec JSON or portable ANYhydro bank.  ``orcawave``: a stored ``.owr`` (OrcFxAPI +
    licence, imported only here) or a portable OrcaWave bank/document.  Nothing is calculated or launched.
    """
    from pathlib import Path
    if kind not in PRESSURE_SOURCES:raise ValueError(f'pressure source must be one of {PRESSURE_SOURCES}')
    if kind=='anyhydro' and Path(path).suffix.lower()=='.owr':
        raise ValueError('an OrcaWave .owr result is not an ANYhydro source; choose the OrcaWave pressure source')
    data=inspect_orcawave_file(path,cancellation_token=cancellation_token)
    origin=pressure_source_origin(data)
    if origin!=kind:
        raise ValueError(f'{Path(path).name} is an {origin} pressure source but {kind} was selected')
    return data


def file_sha256(path):
    import hashlib
    from pathlib import Path
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1<<20),b''):digest.update(block)
    return digest.hexdigest()


def prepare_orcawave_job(*,document,mesh_data,source,settings,project=None,cancellation_token=None,progress=None):
    from anyfileio.orcawave import OrcaWavePressureDocument
    from anyhydro.orcawave_pressure import import_pressure_bank,ImportedPressureBank,SCHEMA,MIXED_SCHEMA
    from anyloads.orcawave_spectrum import native_frequency_realization,regular_realization
    from anymesher.serialize import mesh_from_dict
    from ..io.project_file import project_from_dict
    if source.get('type') in (SCHEMA,MIXED_SCHEMA):
        bank=ImportedPressureBank.from_dict(source);bank.validate_geometry()
        if (settings['body_id']!=bank.body_id or settings['composition']!=bank.composition or
                settings['heading']!=float(np.rad2deg(bank.heading))):
            raise ValueError('portable bank body, heading and composition are fixed')
    else:
        source=OrcaWavePressureDocument.from_dict(source)
        bank=import_pressure_bank(source,body_id=settings['body_id'],heading_degrees=settings['heading'],composition=settings['composition'])
    if settings['wave_type']=='regular':
        realization=regular_realization(bank,omega=settings['omega'],height=settings['height'],phase=settings['phase'])
        report={'mode':'regular'}
    else:
        realization,report=native_frequency_realization(bank,significant_height=settings['height'],
            peak_period=settings['peak_period'],gamma=settings['gamma'],seed=settings['seed'])
    duration=float(settings['duration']);dt=float(settings['dt'])
    if not np.isfinite([duration,dt]).all() or min(duration,dt)<=0 or duration/dt>100000:
        raise ValueError('positive finite duration/time step and at most100001 samples required')
    times=np.arange(int(np.floor(duration/dt))+1)*dt
    if times[-1]<duration:times=np.r_[times,duration]
    kwargs={k:settings[k] for k in ('surfaces','source_to_project','limits','force_relative_limit','moment_relative_limit','force_absolute_limit','moment_absolute_limit')}
    kwargs['dipole_surfaces']=settings.get('dipole_surfaces',())
    mesh=mesh_from_dict(mesh_data)
    history=prepare_prescribed_pressure(project_from_dict(document),mesh,bank,realization,
        times=times,cancel=cancellation_token,spectrum_report=report,**kwargs)
    transform=_transform(settings['source_to_project'])
    return dict(history=history.to_dict(),input_document_hash=canonical_hash(document),name=settings['name'],
                spectrum=report,source_centers=(bank.centers@transform[:3,:3].T+transform[:3,3]).tolist(),
                target_polygons=[[list(mesh.nodes[n]) for n in mesh.corners_of(e)] for e in history.element_ids])


class ApplyPrescribedPressure(Command):
    label='Apply prescribed pressure history'
    def __init__(self,history,name,input_document_hash):
        self.history=history;self.name=name;self.input_document_hash=input_document_hash
    def do(self,project):
        from ..io.project_file import project_to_dict
        from ..model import WetPressure
        from ..model.records import AnalysisDefinition
        if canonical_hash(project_to_dict(project))!=self.input_document_hash:
            raise ValueError('project changed during pressure preparation; nothing applied')
        if not self.name.strip() or self.name in project.load_cases or set(project.series_step_names(self.name,len(self.history.times)))&set(project.load_cases):
            raise ValueError('choose an unused pressure series name')
        b=json.loads(self.history.binding_json)
        if _binding(project,b['surfaces'],b.get('dipole_surfaces',()))!=b['domain']:raise ValueError('wet geometry or sheet roles changed')
        self.before_cases=dict(project.load_cases);self.before_analyses=dict(project.analyses)
        try:
            case=project.add_load_series(self.name,self.history.times)
            case.wet_pressures.append(WetPressure(name='Saved prescribed pressure',mapping_key='element',history=self.history,surfaces=tuple(b['surfaces'])))
            project.add_analysis(AnalysisDefinition(self.name+' — Batch linear static',type='batch_linear_static',
                target_kind='load_cases',target_id=self.name,settings={'load_cases':[c.name for c in project.series_children(self.name)]}))
            self.after_cases=dict(project.load_cases);self.after_analyses=dict(project.analyses)
            return case
        except BaseException:self.undo(project);raise
    def undo(self,project):
        project.load_cases=dict(self.before_cases);project.analyses=dict(self.before_analyses)
    def redo(self,project):
        project.load_cases=dict(self.after_cases);project.analyses=dict(self.after_analyses)
        return project.load_cases[self.name]
