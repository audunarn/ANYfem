"""Closed-end 60 m example with fixed-body regular-wave pressures.

No structural solutions or hydrostatic loads are produced by this module.
"""
from pathlib import Path
import json,math
import numpy as np

from .examples import drawing_barge_60m
from .. import commands
from ..model import WetSurface
from ..native_meshing import NativeMeshSettings


def _imprint_waterline(project, face_id):
    """Factory-only owner operation; canonical regions resolve replacements.

    The generic FEM GeometryCommand adapter duplicates section assignments for
    this structural imprint, so the new-project factory uses the atomic owner
    operation directly. No existing user project is mutated.
    """
    from anygeometry import query_intersection, plan_imprint, apply_imprint, ConnectionIntent
    geometry = project.geometry
    before = set(geometry.faces)
    y = float(geometry.face_point(face_id,.5,.5)[1])
    points = geometry.add_points([(-.1,y-.1,1.5),(60.1,y-.1,1.5),
                                  (60.1,y+.1,1.5),(-.1,y+.1,1.5)])
    cutter = geometry.add_face(geometry.add_polyline(points,close=True))
    result = query_intersection(geometry, geometry.handle('face',face_id),geometry.handle('face',cutter))
    plan = plan_imprint(geometry,result,policy=ConnectionIntent.CONNECT)
    apply_imprint(geometry,plan,policy=ConnectionIntent.CONNECT)
    from anygeometry import EntityRef
    descendants = geometry.resolve_ref(EntityRef('face',face_id))
    cutter_faces = {item.id for item in geometry.resolve_ref(EntityRef('face',cutter)) if item.kind == 'face'}
    cutter_sheets = {use.sheet_id for use in geometry.face_uses.values() if use.face_id in cutter_faces}
    for sid in cutter_sheets:
        if any(geometry.face_uses[u].face_id not in cutter_faces for u in geometry.sheets[sid].face_use_ids):
            raise ValueError('construction cutter unexpectedly shares structural ownership')
    for jid,junction in tuple(geometry.junctions.items()):
        if cutter_sheets.intersection(junction.sheet_ids):
            geometry.remove_junction(jid)
    for aid,attachment in tuple(geometry.attachments.items()):
        if (attachment.sheet_id in cutter_sheets or
                (attachment.source_kind == 'sheet' and attachment.source_id in cutter_sheets)):
            geometry.remove_attachment(aid)
    for sid in cutter_sheets:
        geometry.remove_sheet(sid)
    for fid in cutter_faces:
        geometry.remove_face(fid)
    return tuple(item.id for item in descendants if item.kind == 'face')


def closed_barge_wave_project():
    """Retain the drawing example/supports, close ends and split at waterline."""
    project = drawing_barge_60m()
    project.name = '60 m closed barge — fixed-body wave pressure'
    project.load_cases.clear()
    project.combinations.clear()
    project.analyses.clear()
    geometry = project.geometry
    # Select authored exterior plates by actual plane coordinates, never section
    # names (bulkheads intentionally share the hull plating section).
    exterior = {}
    for fid in tuple(project.face_sections):
        points = np.array([geometry.vertex_position(v) for v in geometry.face_corner_vertices(fid)])
        if np.allclose(points[:, 2], 0):
            exterior['bottom'] = fid
        elif np.allclose(points[:, 1], -2.5) and points[:, 2].max() <= 2.:
            exterior['port'] = fid
        elif np.allclose(points[:, 1], 2.5) and points[:, 2].max() <= 2.:
            exterior['starboard'] = fid
    if set(exterior) != {'bottom', 'port', 'starboard'}:
        raise ValueError('drawing exterior plate identity changed')
    stack = commands.CommandStack(project)
    for label in ('port', 'starboard'):
        fid = exterior[label]
        # Owner operation handles section, region, support and attachment remap.
        # Port local v follows z; starboard local u follows z.
        axis = 1 if label == 'port' else 0
        made = _imprint_waterline(project,fid)
        project.resolve_section_assignments(strict=True)
        wet = []
        for child in made:
            z = [geometry.vertex_position(v)[2] for v in geometry.face_corner_vertices(child)]
            if max(z) <= 1.5+1e-8:
                wet.append(child)
        if len(wet) != 1:
            raise ValueError(f'{label} waterline split failed')
        exterior[label] = wet[0]
    section = project.face_sections[exterior['bottom']]
    for label, x, reverse in (('end0', 0., True), ('end60', 60., False)):
        # Full-height end plate split at draft through explicitly authored rings.
        for suffix, lo, hi in (('wet', 0., 1.5), ('dry', 1.5, 2.)):
            points = [(x,-2.5,lo),(x,2.5,lo),(x,2.5,hi),(x,-2.5,hi)]
            if reverse:
                points.reverse()
            existing = {tuple(geometry.vertex_position(v)):v for v in geometry.vertices}
            vertices = [existing.get(tuple(p)) or geometry.add_point(*p) for p in points]
            from anygeometry import OrientedEdge
            edge_lookup = {tuple(sorted((e.start,e.end))):identifier for identifier,e in geometry.edges.items()}
            ring = []
            for a,b in zip(vertices,vertices[1:]+vertices[:1]):
                key=tuple(sorted((a,b)))
                edge_id=edge_lookup.get(key)
                if edge_id is None:
                    edge_id=geometry.add_line(a,b)
                    edge_lookup[key]=edge_id
                ring.append(OrientedEdge(edge_id,geometry.edges[edge_id].start==a))
            face = geometry.add_face_from_loop(tuple(ring),(0,1,2,3))
            project.assign_plate(face, section)
            if suffix == 'wet':
                exterior[label] = face
    project.wet_surfaces.clear()
    for label, fid in exterior.items():
        project.add_wet_surface(WetSurface(ref=project.face(fid),
            fluid_side='away_from_point', interior_point=(30.,0.,1.), name=label))
    project.set_native_mesh_settings(NativeMeshSettings.create(
        .5, element_order='quadratic', backend='quad_first',
        parameters={'layout_policy':'adaptive', 'quad_t3_angle_action':'warning'}))
    return project, exterior


def _source_mesh():
    """Exact authored rectangular hull lineage, 236 flat BEM quadrilaterals."""
    vertices, faces, labels, point_ids = [], [], [], {}
    def rectangle(label, origin, u, v, nu, nv):
        origin, u, v = map(np.asarray, (origin, u, v))
        for i in range(nu):
            for j in range(nv):
                ring = [origin+u*s+v*t for s,t in
                        ((i/nu,j/nv),((i+1)/nu,j/nv),((i+1)/nu,(j+1)/nv),(i/nu,(j+1)/nv))]
                ids=[]
                for point in ring:
                    key=tuple(point)
                    if key not in point_ids:
                        point_ids[key]=len(vertices)
                        vertices.append(point)
                    ids.append(point_ids[key])
                faces.append(ids)
                labels.append(label)
    rectangle('bottom',(0,-2.5,-1.5),(0,5,0),(60,0,0),5,24)
    rectangle('port',(0,-2.5,-1.5),(60,0,0),(0,0,1.5),24,2)
    rectangle('starboard',(0,2.5,-1.5),(0,0,1.5),(60,0,0),2,24)
    rectangle('end0',(0,-2.5,-1.5),(0,0,1.5),(0,5,0),2,5)
    rectangle('end60',(60,-2.5,-1.5),(0,5,0),(0,0,1.5),5,2)
    return np.asarray(vertices), np.asarray(faces), tuple(labels)


def hydro_to_project_points(points):
    """Convert this admitted hull's WORLD0 points to project coordinates.

    Mathematical inverse (subject to floating-point roundoff) of target_hull_cells' z_hydro=z_project-1.5; vectors and
    pressure signs are unchanged. This does not admit arbitrary hull geometry.
    """
    values=np.asarray(points)
    if values.ndim<1 or values.shape[-1]!=3 or values.dtype.kind not in 'iuf' or not np.isfinite(values).all():
        raise ValueError('hydrodynamic points must be finite real (...,3) coordinates')
    return values+np.array((0.,0.,1.5))


def target_hull_cells(project,mesh,exterior):
    """Owner-certified planar wet cells in hydro coordinates, preserving identity."""
    ids, centers, normals, areas, labels = [], [], [], [], []
    outward = {'bottom':(0,0,-1),'port':(0,-1,0),'starboard':(0,1,0),
               'end0':(-1,0,0),'end60':(1,0,0)}
    for label,fid in exterior.items():
        if label not in outward:
            raise ValueError('unsupported physical hull surface')
        for eid in sorted(mesh.elements_of_face[fid]):
            p=np.array([mesh.nodes[n] for n in mesh.corners_of(eid)])
            if p[:,2].max()>1.5+1e-8 or p[:,2].min() < -1e-8:
                raise ValueError('target element crosses wet domain boundary')
            area=0.;weighted=np.zeros(3);av=np.zeros(3)
            for j in range(1,len(p)-1):
                tri=p[[0,j,j+1]];v=np.cross(tri[1]-tri[0],tri[2]-tri[0])/2.
                a=np.linalg.norm(v);area+=a;av+=v;weighted+=a*tri.mean(axis=0)
            if not area or abs(np.dot(av/np.linalg.norm(av),outward[label]))<1.-1e-8:
                raise ValueError('target shell plane differs from physical source surface')
            ids.append(int(eid));centers.append(weighted/area-(0,0,1.5))
            normals.append(outward[label]);areas.append(area);labels.append(label)
    return ids,centers,normals,areas,labels


def map_barge_regular_wave(output_directory, *, source=None, prepared=None):
    """Create one mapped pressure series with101 steps and save its project."""
    from anyhydro.regular_wave_pressure import solve_regular_wave_pressure
    from anyloads.hydro_pressure import transfer_panel_pressure
    from ..io.project_file import save_project, load_project,project_to_dict,project_from_dict
    from ..model import WetPressure
    from ..document import canonical_hash, DocumentSession
    from anymesher.serialize import mesh_to_dict
    from ..io.artifacts import ArtifactStore
    from ..model.records import MeshRecord
    folder = Path(output_directory); folder.mkdir(parents=True,exist_ok=True)
    if prepared is None:
        project, exterior = closed_barge_wave_project()
        mesh = project.generate_mesh(.5, order='quadratic',strategy='quad_first')
        preparation=dict(project._last_mesh_preparation)
    else:
        project,mesh,exterior,preparation=prepared
        if project.load_cases:
            raise ValueError('prepared project must be the unloaded producing geometry')
    # Public serialization canonicalizes support aliases after owner imprint.
    # Normalize BEFORE recording producing-model provenance, never restamp an
    # unrelated model merely to suppress stale-mesh detection.
    project=project_from_dict(project_to_dict(project))
    ids,centers,normals,areas,labels=target_hull_cells(project,mesh,exterior)
    if source is None:
        source = solve_regular_wave_pressure(*_source_mesh())
    else:
        vertices, faces, surface_ids = _source_mesh()
        if (not np.array_equal(source.vertices,vertices) or not np.array_equal(source.faces,faces)
                or source.surface_ids != surface_ids or source.omega != 2*np.pi/6.
                or source.heading != 0. or source.water_depth != 200. or source.rho != 1025.):
            raise ValueError('source reuse does not match exact barge wave case')
    # Retain completed expensive preparation before load/persistence follow-up.
    save_project(project,folder/'prepared-geometry.anyfem')
    (folder/'prepared-mesh.json').write_text(json.dumps(mesh_to_dict(mesh)),encoding='utf-8')
    np.savez_compressed(folder/'prepared-source.npz',vertices=source.vertices,faces=source.faces,
        surface_ids=source.surface_ids,centers=source.centers,normals=source.normals,
        areas=source.areas,pressure=source.pressure,omega=source.omega,heading=source.heading,
        water_depth=source.water_depth,rho=source.rho,source_id=source.source_id,
        provider=source.provider,excitation_wrench=source.excitation_wrench)
    transfer = transfer_panel_pressure(source,ids,centers,normals,areas,labels)
    times = np.arange(101,dtype=float)/10.
    values = transfer.snapshots(times, amplitude=.5, omega=source.omega)
    mesh_hash = canonical_hash(mesh_to_dict(mesh))
    from anyloads.pressure_history import MappedPressureHistory
    parent=project.add_load_series('Regular wave',times)
    history=MappedPressureHistory(tuple(times),tuple(ids),
        tuple(.5*transfer.phasors.real),tuple(.5*transfer.phasors.imag),source.omega,mesh_hash,
        source.source_id,source.provider)
    from .pressure_transfer import retain_pressure_source,bank_from_regular
    from anyloads.sea_state import WaveRealization
    from anyloads.marine import AiryComponent
    bank=bank_from_regular(source)
    history=retain_pressure_source(history,bank,WaveRealization((AiryComponent(.5,source.omega,math.degrees(bank.heading)),)),
        project,surfaces=tuple(s.id for s in project.wet_surfaces))
    parent.wet_pressures.append(WetPressure(history=history,mapping_key='element',
        surfaces=tuple(s.id for s in project.wet_surfaces),name='Incident + diffraction time series'))
    from ..model.records import AnalysisDefinition
    project.add_analysis(AnalysisDefinition('Regular wave — Batch linear static',type='batch_linear_static',
        target_kind='load_cases',target_id='Regular wave',settings={'load_cases':[c.name for c in project.series_children(parent.name)]}))
    folder = Path(output_directory); folder.mkdir(parents=True,exist_ok=True)
    path = folder/'barge-wave-pressure.anyfem'
    model_hash = DocumentSession(project).revision.model_hash
    record = MeshRecord(name='Pressure transfer mesh',source_model_hash=model_hash,
                        mesh_input_hash='',mesh_hash=mesh_hash,
                        summary={'nodes':mesh.num_nodes,'elements':mesh.num_elements},
                        structural_preparation=preparation)
    artifact = ArtifactStore(path).write_mesh(mesh,mesh_id=record.id,
        document_id=project.document_id,model_hash=model_hash,mesh_hash=mesh_hash,
        structural_preparation=record.structural_preparation)
    record.artifact_id=artifact.id
    project.mesh_records[record.id]=record
    project.artifacts[artifact.id]=artifact
    path = save_project(project,path)
    reopened = load_project(path)
    if len(reopened.load_cases) != 102 or len([c for c in reopened.load_cases.values() if c.parent is None])!=1:
        raise ValueError('pressure cases did not round-trip')
    if DocumentSession(reopened).revision.model_hash != record.source_model_hash:
        raise ValueError('saved pressure mesh producing model became stale on reopen')
    stored_mesh = ArtifactStore(path).read_mesh(reopened.artifacts[artifact.id])
    if canonical_hash(mesh_to_dict(stored_mesh)) != mesh_hash:
        raise ValueError('saved pressure mesh did not round-trip')
    np.savez_compressed(folder/'pressure-mapping.npz',times=times,pressures=values,
        element_ids=ids,centers_hydro=centers,normals=normals,areas=areas,
        source_vertices=source.vertices,source_faces=source.faces,
        source_pressure=source.pressure,source_surface_ids=source.surface_ids,
        source_centers=source.centers,source_normals=source.normals,source_areas=source.areas,
        panel_indices=transfer.panel_indices,source_wrench=transfer.source_wrench,
        target_wrench=transfer.target_wrench,bem_excitation_wrench=source.excitation_wrench)
    encode = lambda x: {'real':np.real(x).tolist(),'imag':np.imag(x).tolist()}
    report = {'source_id':source.source_id,'provider':source.provider,'source_panels':len(source.faces),
        'target_elements':len(ids),'snapshots':101,'owning_load_cases':1,'stored_pressure_loads':1,
        'period_s':6.,'height_m':1.,'amplitude_m':.5,
        'draft_m':1.5,'water_depth_m':200.,'heading_rad':0.,'rho_kg_m3':1025.,
        'phasor':'exp(-i omega t)','coordinate_transform':'z_hydro=z_fem-1.5',
        'pressure':'Pa; dynamic incident+diffraction only; positive inward',
        'transfer':'nearest source panel on exact same planar physical face; not conservative',
        'max_sample_distance_m':transfer.max_distance,'source_wrench':encode(transfer.source_wrench),
        'target_wrench':encode(transfer.target_wrench),'wrench_error':encode(transfer.target_wrench-transfer.source_wrench),
        'bem_excitation_wrench':encode(source.excitation_wrench),'project':str(path),
        'wet_faces':exterior,'supports_retained':len(project.supports),
        'structural_solves':0,'qualification':'development mapping; no mesh convergence claim'}
    (folder/'mapping-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return project,mesh,report






