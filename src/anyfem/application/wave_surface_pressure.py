"""Wave-following pressure: application orchestration, target manifest, preview and frame loads.

Adopted physics (z relative to the mean waterline, eta/pd incident+diffraction, components summed BEFORE the
wet/dry operation) lives in ``anyloads.wave_surface_pressure``; clipping in ``anygeometry``; consistent partial
region integration in ``anysolver.pressure_regions``; retained same-solution fields in
``anyhydro.wave_surface_fields``.  This module only binds them to the admitted 60 m barge:

* the BEM source (236 submerged panels, old 5-surface domain) is unchanged;
* a SEPARATE full-height target manifest adds the existing exterior upper port/starboard/end plating to the five
  wet faces; bulkheads, deck and invented plating carry no pressure; overtopping/deck water is reported excluded;
* keel emergence is refused; no structural remesh; no BEM on Apply or reopening.
"""
from dataclasses import asdict,dataclass
import hashlib,json,math,warnings
import numpy as np

RHO=1025.
GRAVITY=9.81
DRAFT=1.5
DECK_TOP_HYDRO=2.0-DRAFT
ORIGIN_PROJECT=np.array((0.,0.,DRAFT))
WET_LABELS=('bottom','port','starboard','end0','end60')
UPPER_LABELS=('port_upper','starboard_upper','end0_upper','end60_upper')
OUTWARD={'bottom':(0,0,-1),'port':(0,-1,0),'starboard':(0,1,0),'end0':(-1,0,0),'end60':(1,0,0),
    'port_upper':(0,-1,0),'starboard_upper':(0,1,0),'end0_upper':(-1,0,0),'end60_upper':(1,0,0)}
Z_RANGE={**{l:(0.,DRAFT) for l in WET_LABELS},**{l:(DRAFT,2.) for l in UPPER_LABELS}}
KIND='wave_surface_v1'
LABEL='Wave-following pressure (hydrostatic + incident/diffraction, wet/dry at instantaneous surface)'


class SurfaceTargetError(ValueError):
    """The admitted barge target manifest cannot be certified."""


def _json(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def is_surface_history(history):
    from anyloads.wave_surface_pressure import WaveSurfacePressureHistory
    return type(history) is WaveSurfacePressureHistory


def _upper_faces(project):
    geometry=project.geometry;found={}
    for fid in tuple(project.face_sections):
        pts=np.array([geometry.vertex_position(v) for v in geometry.face_corner_vertices(fid)])
        if not (abs(pts[:,2].min()-DRAFT)<1e-8 and abs(pts[:,2].max()-2.)<1e-8):continue
        if np.allclose(pts[:,1],-2.5):label='port_upper'
        elif np.allclose(pts[:,1],2.5):label='starboard_upper'
        elif np.allclose(pts[:,0],0.):label='end0_upper'
        elif np.allclose(pts[:,0],60.):label='end60_upper'
        else:continue
        if label in found:raise SurfaceTargetError(f'ambiguous upper exterior plating {label}')
        found[label]=fid
    missing=[l for l in UPPER_LABELS if l not in found]
    if missing:raise SurfaceTargetError(f'existing exterior upper plating is missing ({missing}); no plating is invented')
    return found


def _newell(points):
    total=np.zeros(3)
    for i in range(len(points)):total+=np.cross(points[i],points[(i+1)%len(points)])
    return total


@dataclass
class SurfaceTarget:
    element_ids: tuple
    labels: tuple
    element_nodes: tuple          # full solver node order per element
    element_corners: tuple        # corner point indices per element
    signs: tuple                  # +1/-1: fluid-side orientation relative to the element's positive normal
    normals: np.ndarray           # (P,3) fluid-side normal of the owning face at each corner point
    points: np.ndarray            # (P,3) corner positions, hydro frame (z relative to the mean waterline)
    waterline: np.ndarray         # (P,) bool: waterline fields are evaluated (False for bottom points)
    face_ids: dict
    target_sha256: str

    @property
    def corner_z(self):return tuple(float(z) for z in self.points[:,2])



def _minimal_fe_model(project,mesh):
    """The project's ACTUAL shell formulations on the mesh nodes (existing builder helpers); no loads, no stiffness."""
    from anysolver import FEModel
    from ..solve.build import _add_shells
    fe=FEModel(name=project.name)
    for specification in project.materials.values():fe.register_material(specification.build())
    for node_id in sorted(mesh.nodes):
        x,y,z=mesh.nodes[node_id];fe.add_node(node_id,float(x),float(y),float(z))
    _add_shells(project,mesh,fe)
    return fe


class SurfaceJob:
    """Job-local evidence for one (project, mesh): certified target, ACTUAL elements and their pressure kernels.

    Never stored on the mesh or in a module global.  ``verify`` refuses reuse after the project geometry token or
    the target geometry (mesh nodes/elements) changed; it is called before every use and by the job's owner at completion.
    """
    def __init__(self,project,mesh):
        self.project=project;self.mesh=mesh
        geometry=project.geometry
        self._token=(id(geometry),geometry.model_id,geometry.revision)
        self.target=build_target(project,mesh)
        self._elements=None
        self._fp=self._fingerprint()

    def verify(self,full=False):
        geometry=self.project.geometry
        if (id(geometry),geometry.model_id,geometry.revision)!=self._token:
            raise SurfaceTargetError('project geometry changed during the wave-following job')
        if full or self._fingerprint()!=self._fp:
            try:fresh=build_target(self.project,self.mesh).target_sha256
            except SurfaceTargetError as error:
                raise SurfaceTargetError(f'mesh or target geometry changed during the wave-following job ({error})') from error
            if fresh!=self.target.target_sha256:
                raise SurfaceTargetError('mesh or target geometry changed during the wave-following job')

    def _fingerprint(self):
        t=self.target
        return hashlib.sha256(np.array([self.mesh.nodes[n] for nodes in t.element_nodes for n in nodes],dtype=float).tobytes()).hexdigest()

    def elements(self):
        """(mesh, elements, kernels, areas, corner_areas) from the actual admitted formulations."""
        if self._elements is None:
            from anysolver.pressure_regions import element_kernel
            fe=_minimal_fe_model(self.project,self.mesh)
            elements=[fe.mesh.get_element(int(e)) for e in self.target.element_ids]
            if any(e is None for e in elements):raise SurfaceTargetError('target element has no solver element')
            made=[element_kernel(e,fe.mesh) for e in elements]
            # Fluid-side sign from the ACTUAL positive-pressure direction of each element's own kernel (not from the
            # node order), so native-chart elements whose positive normal differs are handled correctly.
            signs=[]
            for kernel,label,eid in zip((m[0] for m in made),self.target.labels,self.target.element_ids):
                direction=kernel[:,:3,:].sum(axis=(0,2));norm=float(np.linalg.norm(direction))
                out=np.array(OUTWARD[label],dtype=float)
                if norm<=0. or abs(float(direction@out))<(1.-1e-6)*norm:
                    raise SurfaceTargetError(f'element {eid} pressure direction is not normal to face {label}')
                signs.append(1.0 if float(direction@out)<0. else -1.0)
            self._elements=(fe.mesh,elements,[m[0] for m in made],[m[1] for m in made],[m[2] for m in made],tuple(signs))
        return self._elements


def build_target(project,mesh):
    """Certified full-height target: five wet faces plus the existing exterior upper plating."""
    exterior={s.name:s.ref.id for s in project.wet_surfaces}
    if set(exterior)!=set(WET_LABELS):
        raise SurfaceTargetError('prepare/open the closed 60 m barge with its exact five wet hull surfaces first')
    geometry=project.geometry
    bounds={'bottom':((0,-2.5,0),(60,2.5,0)),'port':((0,-2.5,0),(60,-2.5,1.5)),'starboard':((0,2.5,0),(60,2.5,1.5)),
        'end0':((0,-2.5,0),(0,2.5,1.5)),'end60':((60,-2.5,0),(60,2.5,1.5))}
    for name,fid in exterior.items():
        corners=np.array([geometry.vertex_position(v) for v in geometry.face_corner_vertices(fid)])
        lo,hi=bounds[name]
        if not np.allclose(corners.min(axis=0),lo,atol=1e-8,rtol=0.) or not np.allclose(corners.max(axis=0),hi,atol=1e-8,rtol=0.):
            raise SurfaceTargetError('current hull wet geometry/draft is outside the admitted 60 m/1.5 m contract')
    for surface in project.wet_surfaces:
        corners=np.array([geometry.vertex_position(v) for v in geometry.face_corner_vertices(surface.ref.id)])
        if surface.fluid_side!='away_from_point' or surface.interior_point is None or float(np.dot(
                corners.mean(axis=0)-np.asarray(surface.interior_point,dtype=float),OUTWARD[surface.name]))<=0.:
            raise SurfaceTargetError(f'wet surface {surface.name} fluid side differs from the admitted outward normal')
    faces={**exterior,**_upper_faces(project)}
    if len(set(faces.values()))!=len(faces):raise SurfaceTargetError('target face references are not unique')
    ids,labels,nodes_of,corner_idx,signs,points,normals,wl=[],[],[],[],[],[],[],[]
    point_index={}
    for label in WET_LABELS+UPPER_LABELS:
        out=np.array(OUTWARD[label],dtype=float);lo,hi=Z_RANGE[label]
        for eid in sorted(mesh.elements_of_face[faces[label]]):
            nodes=mesh.quads.get(eid) if eid in mesh.quads else mesh.tris.get(eid)
            if nodes is None:raise SurfaceTargetError(f'target element {eid} is not a plate element')
            corners=list(mesh.corners_of(eid));p=np.array([mesh.nodes[n] for n in corners],dtype=float)
            if label!='bottom' and (p[:,2].min()<lo-1e-8 or p[:,2].max()>hi+1e-8):
                raise SurfaceTargetError(f'target element {eid} crosses the {label} face boundary')
            area_vector=_newell(p)/2.;area=float(np.linalg.norm(area_vector))
            if area<=0. or abs(float(np.dot(area_vector/area,out)))<1.-1e-8:
                raise SurfaceTargetError(f'target element {eid} plane differs from physical face {label}')
            ids.append(int(eid));labels.append(label);nodes_of.append(tuple(int(n) for n in nodes))
            signs.append(1.0 if float(np.dot(area_vector,out))<0. else -1.0)
            idx=[]
            for q in p:
                key=(label,)+tuple(round(float(x),9) for x in q)
                if key not in point_index:
                    point_index[key]=len(points);points.append(q-ORIGIN_PROJECT);normals.append(out);wl.append(label!='bottom')
                idx.append(point_index[key])
            corner_idx.append(tuple(idx))
    if len(set(ids))!=len(ids):raise SurfaceTargetError('an element is owned by two target faces')
    digest=hashlib.sha256(_json(dict(ids=ids,labels=labels,nodes=nodes_of,corners=corner_idx,
        points=np.round(np.asarray(points),9).tolist(),signs=signs)).encode()).hexdigest()
    return SurfaceTarget(tuple(ids),tuple(labels),tuple(nodes_of),tuple(corner_idx),tuple(signs),np.array(normals),
        np.array(points),np.array(wl,dtype=bool),faces,digest)


def resolve_job(project,mesh,job=None,expected_sha256=None):
    """A verified job-local context: a supplied one is re-verified, otherwise a fresh one is built (no global cache)."""
    if job is None:job=SurfaceJob(project,mesh)
    else:
        if job.project is not project or job.mesh is not mesh:raise SurfaceTargetError('wave job context belongs to another project/mesh')
        job.verify()
    if expected_sha256 is not None and job.target.target_sha256!=expected_sha256:
        raise SurfaceTargetError('wave surface target geometry differs from the saved history')
    return job


# ---------------------------------------------------------------- frame loads (the owner route for loads AND preview)
@dataclass(frozen=True)
class FrameLoads:
    nodal: dict                 # node id -> (6,) consistent nodal [force, direct moment] vector on the structure
    state: np.ndarray           # per target element: 0 dry, 1 partial, 2 fully wet
    wet_fraction: np.ndarray
    mean_pressure: np.ndarray   # full-area element-average pressure (normal-signed by the fluid side NOT applied)
    wrench: np.ndarray          # (6,) force/moment about the hydro origin (mean waterline, x=y=0)


def frame_loads(project,mesh,history,time,job=None):
    """Equivalent loads of one frame through ANYsolver's consistent partial-region integration.

    The ONLY integration route: Apply/assembly, preview resultants and inspection all report these numbers.
    """
    from anysolver.pressure_regions import wet_region_load
    if not is_surface_history(history):raise SurfaceTargetError('wave surface history required')
    job=resolve_job(project,mesh,job,history.target_sha256);target=job.target
    if tuple(history.element_ids)!=target.element_ids or tuple(history.element_corners)!=target.element_corners:
        raise SurfaceTargetError('history target elements differ from the current mesh target')
    shim,elements,kernels,areas,corner_areas,signs=job.elements()
    frame=history.frame(time);from anygeometry.wave_surface_clipping import clip_triangle_positive
    n=len(elements);state=np.zeros(n,dtype=np.int8);fraction=np.zeros(n);mean=np.zeros(n);nodal={}
    for i,(element,g,p) in enumerate(zip(elements,frame.levels,frame.pressures)):
        if g.max()<=0.:continue
        if g.min()>0.:
            vectors=signs[i]*np.einsum('nkc,c->nk',kernels[i],p)               # (n_nodes,6) forces AND moments
            state[i]=2;fraction[i]=1.;mean[i]=float(corner_areas[i]@p/areas[i])      # physical area average
        else:
            load=wet_region_load(element,shim,g,p,sign=signs[i],clipper=clip_triangle_positive,total_area=areas[i])
            vectors=load.vectors;state[i]=1 if load.state=='partial' else (2 if load.state=='wet' else 0)
            fraction[i]=load.wet_fraction;mean[i]=load.mean_pressure
        for node,f in zip(target.element_nodes[i],vectors):
            if f.any():nodal[node]=nodal.get(node,0.)+f
    wrench=np.zeros(6)
    for node,f in nodal.items():
        r=np.asarray(mesh.nodes[node],dtype=float)-ORIGIN_PROJECT
        wrench[:3]+=f[:3];wrench[3:]+=np.cross(r,f[:3])+f[3:]           # direct nodal moments + r x F
    return FrameLoads(nodal,state,fraction,mean,wrench)


def inspection_pressures(project,mesh,history,time,job=None):
    """{element: (normal-signed full-area average pressure, centroid, normal, wet fraction, state)}.

    Wet fraction and the full-area average come from the SAME physical integration as the assembled loads.
    """
    job=resolve_job(project,mesh,job,history.target_sha256);target=job.target
    loads=frame_loads(project,mesh,history,time,job)
    state,fraction,mean=loads.state,loads.wet_fraction,loads.mean_pressure
    out={}
    for i,eid in enumerate(target.element_ids):
        p=np.array([mesh.nodes[n] for n in mesh.corners_of(eid)],dtype=float)
        normal=_newell(p);normal/=np.linalg.norm(normal)
        out[eid]=(job.elements()[5][i]*float(mean[i]),p.mean(axis=0),normal,float(fraction[i]),int(state[i]))
    return out


# ------------------------------------------------------------------------------------------- source and history
def _config(settings):
    from .wave_force_input import _config as config
    return config(settings)


def _realization(config):
    from .wave_force_input import _realization as realization
    return realization(config)


def _omegas(realization):
    return tuple(dict.fromkeys(c.omega for c in realization.components if c.amplitude!=0))


def _field_request(config,omegas):
    from anyhydro.wave_surface_fields import field_request
    from anyhydro.excitation_cache import canonical_heading
    from .hydro_wave_pressure import _source_mesh
    v,f,labels=_source_mesh()
    return (v,f,labels),field_request(v,f,labels,heading=canonical_heading(math.radians(config.heading)),
        water_depth=config.depth,rho=RHO,gravity=GRAVITY)


def field_requirements(settings,cache_directory=None):
    """Counts shown BEFORE preparation: how many field-capable solutions are missing (no Capytaine, no BEM)."""
    from anyhydro.wave_surface_fields import missing_field_frequencies
    from .wave_force_input import _cache
    config=_config(settings);omegas=_omegas(_realization(config))
    _,request=_field_request(config,omegas)
    missing=missing_field_frequencies(_cache(cache_directory),request,omegas)
    return dict(total=len(omegas),missing=len(missing),missing_omegas=list(missing))


def prepare_snapshots(config,omegas,cache_directory,*,allow_solve,cancel=None,progress=None):
    from anyhydro.wave_surface_fields import (missing_field_frequencies,prepare_field_snapshots,recover_field_snapshot,
        WaveFieldError)
    from anyhydro.excitation_cache import canonical_heading
    from .wave_force_input import _cache
    (v,f,labels),request=_field_request(config,omegas);root=_cache(cache_directory)
    missing=missing_field_frequencies(root,request,omegas)
    if missing and not allow_solve:
        raise WaveFieldError(f'{len(missing)} field-capable hydrodynamic solution(s) are missing; prepare them explicitly '
            '(no automatic BEM on Apply or reopen)')
    if missing:
        return prepare_field_snapshots(v,f,labels,omegas,cache_directory=root,heading=canonical_heading(math.radians(config.heading)),
            water_depth=config.depth,rho=RHO,gravity=GRAVITY,max_missing=max(1,len(omegas)),cancel=cancel,
            progress=(lambda w:progress(f'Field-capable hydrodynamic solutions: solved omega={w:.4f}')) if progress else None)
    return tuple(recover_field_snapshot(root,request,w) for w in omegas)


def _evaluate_history(*,snapshots,config,realization,times,target,mesh_hash,model_hash,provenance_extra=None):
    from anyhydro.wave_surface_fields import evaluate_surface_fields,snapshot_to_dict
    from anyhydro.excitation_cache import canonical_heading
    from anyloads.wave_surface_pressure import WaveSurfacePressureHistory
    omegas=_omegas(realization);P=len(target.points)
    if not omegas:
        # True zero-amplitude input: still water.  Inert frequencies, zero weight, no hydrodynamic field is needed.
        omegas=tuple(dict.fromkeys(c.omega for c in realization.components))
        if snapshots:raise ValueError('a zero-amplitude realization has no field snapshots')
    elif tuple(s.omega for s in snapshots)!=omegas:raise ValueError('field snapshots differ from realised frequencies')
    eta=np.zeros((len(omegas),P),complex);pd0=np.zeros_like(eta);pd=np.zeros_like(eta)
    for k,snap in enumerate(snapshots):
        fields=evaluate_surface_fields(snap,target.points,target.normals,waterline=target.waterline)
        eta[k],pd0[k],pd[k]=fields.eta,fields.pd0,fields.pd
    source_id=hashlib.sha256(_json(dict(snapshots=[s.snapshot_id for s in snapshots],algorithm='wave_surface_v1')).encode()).hexdigest()
    provenance=_json(dict(settings=asdict(config),draft_m=DRAFT,composition='composed wave-following hydrostatic+incident/diffraction',
        source_snapshot_ids=[s.snapshot_id for s in snapshots],**(provenance_extra or {})))
    history=WaveSurfacePressureHistory(times=times,record_times=times,omegas=omegas,element_ids=target.element_ids,
        element_corners=target.element_corners,corner_z=target.corner_z,eta_real=eta.real.tolist(),eta_imag=eta.imag.tolist(),
        pd0_real=pd0.real.tolist(),pd0_imag=pd0.imag.tolist(),pd_real=pd.real.tolist(),pd_imag=pd.imag.tolist(),
        heading=canonical_heading(math.radians(config.heading)),rho=RHO,gravity=GRAVITY,
        realization_json=_json(realization.to_dict()),mesh_content_sha256=mesh_hash,model_content_sha256=model_hash,
        target_sha256=target.target_sha256,source_id=source_id,provenance=provenance,
        source_payload_json=_json(dict(snapshots=[snapshot_to_dict(s) for s in snapshots])))
    return history


def envelope_report(history,target):
    """Reject keel emergence; report overtopping (deck water is excluded, never loaded)."""
    low,high=float('inf'),float('-inf')
    # Keep envelope evaluation bounded independently of the saved record length.
    for first in range(0,len(history.record_times),64):
        series=history.elevation_series(history.record_times[first:first+64])[:,target.waterline]
        low=min(low,float(series.min()));high=max(high,float(series.max()))
    if low<=-DRAFT+1e-9:
        raise ValueError(f'keel emergence: realised surface reaches {low:.3f} m below the mean waterline '
            f'(draft {DRAFT} m); wave-following pressure is not admitted')
    return dict(min_eta_m=low,max_eta_m=high,deck_height_above_waterline_m=DECK_TOP_HYDRO,
        overtopping=high>DECK_TOP_HYDRO,overtopping_deck_water_excluded=high>DECK_TOP_HYDRO)


def _prepare_context(document,mesh_data):
    from anymesher.serialize import mesh_from_dict
    from ..io.project_file import project_from_dict
    from ..document import canonical_hash
    project=project_from_dict({**document,'load_cases':[],'combinations':[],'analyses':[]})
    return project,mesh_from_dict(mesh_data),canonical_hash(mesh_data),canonical_hash(
        {**document,'load_cases':[],'combinations':[],'analyses':[]})


def preview_resultants(project,mesh,history,times,*,cancel=None,job=None):
    """Applied hull resultants of the SAME frame loads that Apply assembles (not the linear excitation 6-vector)."""
    job=resolve_job(project,mesh,job,history.target_sha256);rows=[]
    for t in times:
        if cancel is not None:cancel()
        rows.append(frame_loads(project,mesh,history,t,job).wrench)
    job.verify(full=True)          # refuse publication if the mesh/project moved while the job ran
    return np.array(rows)


def prepare_surface_preview(*,document,mesh_data,settings,source_bank_cache_directory=None,progress=None,
        cancellation_token=None,allow_solve=True,project=None,document_content_hash=None,mesh_content_hash=None,
        **_unused):
    """Explicit preparation (may solve ONLY missing field-capable frequencies) and corrected resultant preview."""
    from ..document import canonical_hash
    config=_config(settings)
    if not config.follow_surface:raise ValueError('follow_surface is not enabled in these wave inputs')
    cancel=lambda:cancellation_token.raise_if_cancelled('wave surface preparation') if cancellation_token else None
    model,mesh,mesh_hash,model_hash=_prepare_context(document,mesh_data)
    import re
    if document_content_hash is not None and (type(document_content_hash) is not str
            or re.fullmatch('sha256:[0-9a-f]{64}',document_content_hash) is None
            or any(document.get(key)!=[] for key in ('load_cases','combinations','analyses'))):
        raise ValueError('invalid attested load-free context: a captured document hash needs a load-free context document')
    if mesh_content_hash is not None and (type(mesh_content_hash) is not str or mesh_content_hash!=mesh_hash):
        raise ValueError('captured mesh hash differs from the supplied real mesh')
    job=SurfaceJob(model,mesh);target=job.target
    realization=_realization(config);omegas=_omegas(realization)
    before=field_requirements(settings,source_bank_cache_directory)
    def poll():
        cancel();return False       # raises the owned cancellation exception when requested
    snapshots=prepare_snapshots(config,omegas,source_bank_cache_directory,allow_solve=allow_solve,cancel=poll,
        progress=progress)
    cancel()
    history=_evaluate_history(snapshots=snapshots,config=config,realization=realization,times=config.times,target=target,
        mesh_hash=mesh_hash,model_hash=model_hash)
    report=envelope_report(history,target)
    cancel()
    if progress:progress('Integrating applied hull resultants through the consistent frame-load route')
    values=preview_resultants(model,mesh,history,history.times,cancel=cancel,job=job)
    result=dict(preview_kind=KIND,name=config.name,history=history.to_dict(),surfaces=[s.id for s in model.wet_surfaces],
        record_history_sha256=history.record_identity(),source_id=history.source_id,
        preview_label=LABEL,preview_times=list(history.times),preview_resultants=values.tolist(),
        preview_resultant_kind='applied wave-following hull resultant (consistent frame loads), about hydro WORLD0',
        preview_moment_origin=(0.,0.,0.),preview_composition='wave-following (hydrostatic included, locked)',
        field_requirements_before=before,envelope=report,source_cache_path=None,source_cache_sha256=None,
        input_document_hash=document_content_hash or canonical_hash(document),mesh_hash=mesh_hash,
        input_settings_hash=canonical_hash(settings),
        excluded_loads='overtopping/deck water, bulkheads and deck plating are not loaded')
    result['preview_content_hash']=canonical_hash({k:result[k] for k in _HASHED})
    return result


# The content hash binds the FULL-record preview (times, resultants, full-record history identity).  Selecting a
# window changes only the history's selected times/window, never this hash, so select -> recover stays valid.
_HASHED=('preview_times','preview_resultants','record_history_sha256','source_id','input_document_hash','mesh_hash',
    'input_settings_hash','preview_composition','preview_moment_origin','preview_kind')


def validate_surface_preview(prepared):
    from ..document import canonical_hash
    if prepared.get('preview_kind')!=KIND:raise ValueError('prepared wave-following preview changed; prepare again')
    try:changed=prepared.get('preview_content_hash')!=canonical_hash({k:prepared[k] for k in _HASHED})
    except KeyError:changed=True
    if changed:raise ValueError('prepared wave-following preview content changed; prepare again')
    from anyloads.pressure_history import pressure_history_from_dict
    history=pressure_history_from_dict(prepared['history'])
    if (not is_surface_history(history) or history.source_id!=prepared['source_id']
            or tuple(history.record_times)!=tuple(prepared['preview_times'])):
        raise ValueError('prepared wave-following history differs from its preview; prepare again')
    # Full AND partial selections are bound to the original full-record field/source/settings identity.
    if history.record_identity()!=prepared['record_history_sha256']:
        raise ValueError('prepared wave-following history changed; prepare again')
    return history


def select_surface_window(result,start=None,end=None):
    from anyloads.wave_preview import select_sample_window
    history=validate_surface_preview(result)
    window=select_sample_window(history.record_times,start,end)
    selected=history.with_window(window.times,window.interval)
    return {**result,'history':selected.to_dict(),'selected_window':window.interval,'selected_times':window.times}


def recover_surface_selection(*,document,mesh_data,prepared,start=None,end=None,**_unused):
    """Apply-stage: validate bindings and select the original sample window; no BEM, no field recomputation.

    The captured document must be exactly the one prepared against (no unloaded-model fallback); reopening or
    a changed mesh is handled separately by ``remap_surface_history``.
    """
    from ..document import canonical_hash
    validate_surface_preview(prepared)
    if canonical_hash(document)!=prepared['input_document_hash']:
        raise ValueError('project/mesh changed before applying the wave-following pressure')
    if canonical_hash(mesh_data)!=prepared['mesh_hash']:
        raise ValueError('project/mesh changed before applying the wave-following pressure')
    if start is None and end is None and prepared.get('selected_window') is not None:
        start,end=prepared['selected_window']
    return select_surface_window(prepared,start,end)


# --------------------------------------------------------------------------------------------------- remap
_REMAP={}
_REMAP_LIMIT=4


def remap_surface_history(project,mesh,history):
    """Rebuild the target-point fields on a changed mesh from the RETAINED field snapshots (postprocess only)."""
    from anyhydro.wave_surface_fields import snapshot_from_dict,WaveFieldError
    from anyloads.sea_state import WaveRealization
    from ..document import canonical_hash
    from anymesher.serialize import mesh_to_dict
    target=SurfaceJob(project,mesh).target
    mesh_hash=canonical_hash(mesh_to_dict(mesh))
    if mesh_hash==history.mesh_content_sha256 and target.target_sha256==history.target_sha256:return history
    if target.target_sha256==history.target_sha256:
        # Same exterior target (e.g. unrelated elements changed): identical fields, only the mesh identity moves.
        data=history.to_dict()
        for key in ('type','algorithm','content_sha256'):data.pop(key)
        data['mesh_content_sha256']=mesh_hash
        from anyloads.wave_surface_pressure import WaveSurfacePressureHistory
        return WaveSurfacePressureHistory(**data)
    key=(history.content_sha256,mesh_hash,target.target_sha256)
    if key in _REMAP:return _REMAP[key]
    try:
        snapshots=tuple(snapshot_from_dict(d) for d in json.loads(history.source_payload_json)['snapshots'])
        saved=json.loads(history.provenance)['settings']
    except (ValueError,KeyError,TypeError,WaveFieldError) as error:
        raise ValueError(f'retained wave-following source is unusable ({error}); regenerate wave input') from error
    config=_config(saved);realization=WaveRealization.from_dict(json.loads(history.realization_json))
    # Re-evaluate from the ORIGINAL retained sources at the same absolute record; never restamp old element values.
    made=_evaluate_history(snapshots=snapshots,config=config,realization=realization,times=history.record_times,
        target=target,mesh_hash=mesh_hash,model_hash=history.model_content_sha256,
        provenance_extra=dict(remapped_from_mesh=history.mesh_content_sha256))
    if tuple(history.times)!=tuple(history.record_times):made=made.with_window(history.times,history.selected_window)
    envelope_report(made,target)
    if len(_REMAP)>=_REMAP_LIMIT:_REMAP.pop(next(iter(_REMAP)))
    _REMAP[key]=made
    return made
