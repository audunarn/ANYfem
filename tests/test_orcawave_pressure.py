import json
import numpy as np
import pytest
from test_wet_pressure import _project,_total_force
from anyfem.model import WetSurface,WetPressure
from anyfem.solve.build import build_fe_model
from anyfem.document import canonical_hash
from anyfem.io.project_file import project_to_dict,project_from_dict
from anyfem.application.orcawave_pressure import prepare_prescribed_pressure,ApplyPrescribedPressure,effective_prescribed_history


def fixture():
    from anyhydro.orcawave_pressure import ImportedPressureBank
    from anyloads.orcawave_spectrum import regular_realization
    project,face=_project([(0,0,-1),(2,0,-1),(2,1,-1),(0,1,-1)])
    surface=WetSurface(ref=project.face(face),fluid_side='positive_normal',name='wet')
    project.add_wet_surface(surface)
    mesh=project.generate_mesh(.5)
    bank=ImportedPressureBank(vertices=np.array([(0,0,-1),(2,0,-1),(2,1,-1),(0,1,-1)]),faces=np.array([[0,1,2,3]]),
        centers=np.array([[1,.5,-1]]),normals=np.array([[0,0,1]]),areas=np.array([2.]),omegas=np.array([1.]),responses=np.array([[100+20j]]),
        panel_ids=(0,),surface_ids=('body0',),heading=0.,composition='fixed_diffraction',body_id=0,water_depth=20.,rho=1025.,gravity=9.81,provenance_json='{}')
    r=regular_realization(bank,omega=1.,height=2.,phase=0.)
    h=prepare_prescribed_pressure(project,mesh,bank,r,times=(0.,.5,1.),surfaces=(surface.id,),source_to_project=np.eye(4),
        limits=dict(gap_m=1e-8,max_angle=.01,coverage_tolerance=1e-6,convergence_tolerance=1e-5),
        force_relative_limit=1e-6,moment_relative_limit=1e-6,force_absolute_limit=1e-8,moment_absolute_limit=1e-8)
    return project,mesh,h


def sheet_fixture(*,reverse_target=False,split_references=False,triangular=False):
    from dataclasses import replace
    from anyhydro.orcawave_pressure import ImportedPressureBank
    from anygeometry.panel_pressure_projection import validate_panel_geometry
    from anyloads.orcawave_spectrum import regular_realization
    project,mesh,old=fixture();bank=ImportedPressureBank.from_dict(json.loads(old.source_payload_json))
    if triangular or reverse_target:
        points=np.array([(0,0,-1),(2,0,-1),(0,1,-1)] if triangular else [(0,0,-1),(2,0,-1),(2,1,-1),(0,1,-1)])
        if reverse_target:points=points[::-1]
        project,face=_project(points);project.add_wet_surface(WetSurface(ref=project.face(face),fluid_side='positive_normal',name='sheet'))
        if triangular:
            from anygeometry.surfaces import Plane
            project.geometry.set_face_surface(face,Plane(points[0],points[1]-points[0],points[2]-points[0]))
            bank=replace(bank,vertices=np.array([(0,0,-1),(2,0,-1),(0,1,-1)]),faces=np.array([[0,1,2,2]]),
                         areas=np.array([1.]),centers=np.array([[2/3,1/3,-1]]),bank_id='')
        mesh=project.generate_mesh(.5)
    if split_references:
        polygons=np.array([[(0,0,-1),(.8,0,-1),(.8,1,-1),(0,1,-1)],[(.8,0,-1),(2,0,-1),(2,1,-1),(.8,1,-1)]])
        polygons[1]=polygons[1][::-1]
        _,normals,centers,areas=validate_panel_geometry(polygons)
        bank=replace(bank,vertices=polygons.reshape(-1,3),faces=np.arange(8).reshape(2,4),normals=normals,centers=centers,
            areas=areas,panel_ids=(0,1),surface_ids=('sheet','sheet'),responses=np.array([[100+20j,-100-20j]]),bank_id='')
    bank=replace(bank,panel_kinds=('dipole_jump',)*len(bank.panel_ids),bank_id='')
    kw=dict(times=(0.,np.pi/2),surfaces=tuple(s.id for s in project.wet_surfaces),dipole_surfaces=tuple(s.id for s in project.wet_surfaces),
        source_to_project=np.eye(4),limits=dict(gap_m=1e-8,max_angle=.01,coverage_tolerance=1e-6,convergence_tolerance=1e-5),
        force_relative_limit=1e-7,moment_relative_limit=1e-7,force_absolute_limit=1e-8,moment_absolute_limit=1e-8)
    h=prepare_prescribed_pressure(project,mesh,bank,regular_realization(bank,omega=1.,height=2.),**kw)
    return project,mesh,bank,h,kw


@pytest.mark.parametrize('reverse_target,split_references',[(False,False),(True,False),(False,True),(True,True)])
def test_dipole_sign_phase_and_arbitrary_reference_invariance(reverse_target,split_references):
    from dataclasses import replace
    from anyloads.orcawave_spectrum import regular_realization
    project,mesh,bank,h,kw=sheet_fixture(reverse_target=reverse_target,split_references=split_references)
    xyz=np.array([mesh.nodes[n] for n in h.node_ids])
    for time,p in [(0.,100.),(np.pi/2,20.)]:
        nodal=h.evaluate(time)
        assert nodal[:,:3].sum(axis=0)==pytest.approx([0,0,-2*p],abs=1e-7)
        assert (np.cross(xyz,nodal[:,:3])+nodal[:,3:]).sum(axis=0)==pytest.approx([-p,2*p,0],abs=1e-7)
        assert np.allclose(np.abs(h.preview_pressures(time)),p)
    faces=bank.faces[:,[0,3,2,1]]
    flipped=replace(bank,faces=faces,normals=-bank.normals,responses=-bank.responses,bank_id='')
    other=prepare_prescribed_pressure(project,mesh,flipped,regular_realization(flipped,omega=1.,height=2.),**kw)
    np.testing.assert_allclose(other.responses,h.responses,atol=1e-10)
    assert 'dipole_jump' in json.loads(h.binding_json)['frequency_evidence'][0]['by_kind']


def test_dipole_irregular_replay_remap_roles_and_native_triangle_work():
    from dataclasses import replace
    from anyloads.orcawave_spectrum import native_frequency_realization
    project,mesh,bank,h,kw=sheet_fixture(triangular=True)
    assert np.linalg.norm(h.responses[:,:,3:])>1e-6
    bank=replace(bank,omegas=np.array([.6,1.1,1.7]),responses=np.tile(bank.responses,(3,1)),bank_id='')
    r,report=native_frequency_realization(bank,significant_height=2.,peak_period=6.,seed=47)
    kw.update(times=(0.,.2,.4),spectrum_report=report)
    h=prepare_prescribed_pressure(project,mesh,bank,r,**kw);before=project_to_dict(project)
    cmd=ApplyPrescribedPressure(h,'Jump',canonical_hash(before));cmd.do(project)
    reopened=project_from_dict(project_to_dict(project));replay=reopened.load_cases['Jump'].wet_pressures[0].history
    np.testing.assert_array_equal(replay.evaluate(.2),h.evaluate(.2))
    newmesh=project.generate_mesh(.25);remap=effective_prescribed_history(project,newmesh,h)
    np.testing.assert_allclose(remap.evaluate(.2)[:,:3].sum(axis=0),h.evaluate(.2)[:,:3].sum(axis=0),atol=1e-8)
    b=json.loads(h.binding_json);b['dipole_surfaces']=[]
    changed=replace(h,binding_json=json.dumps(b),content_sha256='')
    with pytest.raises(ValueError,match='binding changed'):effective_prescribed_history(project,mesh,changed)
    cmd.undo(project);assert project_to_dict(project)==before
    with pytest.raises(ValueError,match='kind'):
        prepare_prescribed_pressure(project,mesh,bank,r,**{**kw,'dipole_surfaces':()})


def test_self_equilibrated_sheet_pressure_retains_nonzero_local_work():
    from dataclasses import replace
    from anygeometry import validate_panel_geometry
    from anyloads.orcawave_spectrum import regular_realization
    project,mesh,bank,_,kw=sheet_fixture()
    polygons=[np.array([[x,0,-1],[x+.5,0,-1],[x+.5,1,-1],[x,1,-1]]) for x in (0.,.5,1.,1.5)]
    _,normals,centers,areas=validate_panel_geometry(polygons)
    bank=replace(bank,vertices=np.concatenate(polygons),faces=np.arange(16).reshape(4,4),normals=normals,centers=centers,areas=areas,
        responses=np.array([[100.,-100.,-100.,100.]]),panel_ids=(0,1,2,3),surface_ids=('sheet',)*4,panel_kinds=('dipole_jump',)*4,bank_id='')
    h=prepare_prescribed_pressure(project,mesh,bank,regular_realization(bank,omega=1.,height=2.),**kw)
    loads=h.evaluate(0.);xyz=np.array([mesh.nodes[n] for n in h.node_ids])
    assert np.linalg.norm(loads)>1.
    np.testing.assert_allclose(loads[:,:3].sum(axis=0),0,atol=1e-8)
    np.testing.assert_allclose((np.cross(xyz,loads[:,:3])+loads[:,3:]).sum(axis=0),0,atol=1e-8)
    # A spatially varying virtual displacement still performs nonzero work.
    assert abs(loads[:,2]@(xyz[:,0]**2))>1.


def test_mixed_nearby_body_sheet_roles_and_separate_resultant_checks():
    from dataclasses import replace
    from anygeometry import validate_panel_geometry
    from anyloads.orcawave_spectrum import regular_realization
    project,_,body_history=fixture()
    from anyhydro.orcawave_pressure import ImportedPressureBank
    body=ImportedPressureBank.from_dict(json.loads(body_history.source_payload_json))
    points=body.vertices+np.array([0,0,.01])
    ids=project.geometry.add_points(points);edges=project.geometry.add_polyline(ids,close=True)
    face=project.geometry.add_face(edges);project.assign_plate(face,'plate')
    sheet=WetSurface(ref=project.face(face),fluid_side='positive_normal',name='sheet');project.add_wet_surface(sheet)
    mesh=project.generate_mesh(.5)
    _,normals,centers,areas=validate_panel_geometry([body.vertices,points])
    bank=replace(body,vertices=np.concatenate([body.vertices,points]),faces=np.arange(8).reshape(2,4),normals=normals,centers=centers,areas=areas,
        responses=np.array([[100+20j,-100-20j]]),panel_ids=(0,1),surface_ids=('body','sheet'),panel_kinds=('body_pressure','dipole_jump'),bank_id='')
    binding=json.loads(body_history.binding_json)
    keys=('times','surfaces','source_to_project','limits','force_relative_limit','moment_relative_limit','force_absolute_limit','moment_absolute_limit')
    kwargs={k:binding[k] for k in keys if k in binding};kwargs.update(times=(0.,.2),surfaces=[s.id for s in project.wet_surfaces],dipole_surfaces=[sheet.id])
    h=prepare_prescribed_pressure(project,mesh,bank,regular_realization(bank,omega=1.,height=2.),**kwargs)
    records=json.loads(h.binding_json)['frequency_evidence'][0]
    assert records['force']['reference_norm']==pytest.approx(0.)
    for kind in ('body_pressure','dipole_jump'):
        assert records['by_kind'][kind]['force']['reference_norm']>200
        assert records['by_kind'][kind]['force']['absolute']<1e-8
    assert np.max(h.preview_pressures(0.))==pytest.approx(100)
    assert np.min(h.preview_pressures(0.))==pytest.approx(-100)


def native_anyhydro_bank():
    from dataclasses import replace
    import anyhydro as ah
    from anyhydro.fixtures import make_fixture_bodies,make_fixture_request,make_two_body_fixture
    from anyhydro.excitation_source import attach_excitation_source
    bodies=make_fixture_bodies();request=make_fixture_request();ds=make_two_body_fixture()
    bid=bodies[0].body_id
    semantics=ah.SurfaceSemantics('native-sheet',bid,'thin_sheet','potential_flow','native-geometry',minus_side='lower',plus_side='upper')
    sheet=ah.HydroPanelMesh(np.array([[0.,0.,-1.],[2.,0.,-1.],[2.,1.,-1.],[0.,1.,-1.]]),
        np.array([[0,1,2],[0,2,3]]),np.array([3,3]),bid,False,('native-geometry',),'',surface=semantics)
    bodies=(replace(bodies[0],attached_surfaces=(sheet,)),*bodies[1:])
    ds.attrs.update(schema_version=ah.SURFACE_DATASET_SCHEMA,surface_identity=json.dumps(ah.surface_identity(bodies)),
        sheet_source_provenance=json.dumps(dict(backend='synthetic-schema-test-only',version='test-v1',source_commit='test-source',
        capability_evidence_sha256='1'*64,coupling='full_hull_sheet_and_cross_body',
        pressure_jump_convention='p_minus-p_plus;normal_minus_to_plus',source_capability_status='verified')))
    ds=ds.assign_coords(sheet_panel=[0,1],sheet_panel_surface_id=('sheet_panel',['native-sheet']*2),sheet_panel_face=('sheet_panel',[0,1]))
    ds['pressure_jump']=(('omega','wave_direction','sheet_panel'),np.full((len(request.omegas),len(request.wave_directions),2),100+20j))
    ds.attrs[ah.PRESSURE_JUMP_CONTRACT_ATTR]=dict(quantity='total_excitation_pressure_jump',unit='Pa/m',wave_amplitude='unit_complex_incident_amplitude_1_m',
        coordinate_frame='world',phasor_convention=ds.attrs['phasor_convention'],sign='p_minus-p_plus;normal_minus_to_plus')
    ds=attach_excitation_source(ds,request,bodies,convention_evidence='synthetic native sign and phase oracle')
    return ah.bank_from_anyhydro_pressure_jump(ds,bodies,request,body_id=bid,heading=float(request.wave_directions[0]))


def test_anyhydro_portable_source_production_assembly_and_reopen(tmp_path):
    from anyfem.application.orcawave_pressure import inspect_orcawave_file,prepare_orcawave_job
    from anyloads import PrescribedPressureHistory
    project,mesh,job=job_fixture();bank=native_anyhydro_bank();path=tmp_path/'native-bank.json';bank.save(path)
    job['source']=inspect_orcawave_file(path)
    job['settings'].update(body_id=bank.body_id,heading=float(np.rad2deg(bank.heading)),composition=bank.composition,
        omega=float(bank.omegas[0]),dipole_surfaces=[s.id for s in project.wet_surfaces],name='Native sheet')
    result=prepare_orcawave_job(**job);history=PrescribedPressureHistory.from_dict(result['history'])
    ApplyPrescribedPressure(history,result['name'],result['input_document_hash']).do(project)
    child=project.series_children(result['name'])[0]
    assert _total_force(build_fe_model(project,mesh,child.name))==pytest.approx([0,0,200],abs=1e-8)
    reopened=project_from_dict(project_to_dict(project))
    assert _total_force(build_fe_model(reopened,mesh,child.name))==pytest.approx([0,0,200],abs=1e-8)
    retained=json.loads(history.source_payload_json)
    assert json.loads(retained['provenance_json'])['qualification_status']=='development_fixture'
    for key,value in [('body_id',99),('heading',23.),('composition','total_with_source_motion')]:
        settings={**job['settings'],key:value}
        with pytest.raises(ValueError,match='fixed'):prepare_orcawave_job(**{**job,'settings':settings})


def test_production_assembly_undo_reopen_and_remesh():
    project,mesh,h=fixture();before=project_to_dict(project)
    command=ApplyPrescribedPressure(h,'Imported',canonical_hash(before));command.do(project)
    child=project.series_children('Imported')[0]
    built=build_fe_model(project,mesh,child.name)
    assert _total_force(built)==pytest.approx([0,0,-200],abs=1e-8)
    assert not hasattr(built.load_case,'_floating_compound')
    reopened=project_from_dict(project_to_dict(project))
    assert _total_force(build_fe_model(reopened,mesh,child.name))==pytest.approx([0,0,-200],abs=1e-8)
    command.undo(project);assert project_to_dict(project)==before
    command.redo(project)
    newmesh=project.generate_mesh(.25)
    remapped=effective_prescribed_history(project,newmesh,h)
    assert remapped.mesh_content_sha256!=h.mesh_content_sha256
    assert remapped.evaluate(0.)[:,:3].sum(axis=0)==pytest.approx([0,0,-200],abs=1e-8)


def test_stale_command_and_wrong_geometry_refused():
    project,mesh,h=fixture();command=ApplyPrescribedPressure(h,'Imported','invalid')
    with pytest.raises(ValueError,match='changed'):command.do(project)
    project.wet_surfaces.clear()
    with pytest.raises(ValueError):effective_prescribed_history(project,mesh,h)


def test_pressure_scale_and_preview():
    from anyfem.application.orcawave_pressure import prescribed_element_pressures
    project,mesh,h=fixture();case=project.load_case('pressure');case.time=.5
    case.wet_pressures.append(WetPressure(history=h,mapping_key='element',scale=2.))
    p=100*np.cos(.5)+20*np.sin(.5)
    assert _total_force(build_fe_model(project,mesh,case.name))==pytest.approx([0,0,-4*p],abs=1e-8)
    ids,pressure=prescribed_element_pressures(project,mesh,h,.5)
    assert np.allclose(pressure,-p)


def raw_document(bank):
    from anyfileio.orcawave import OrcaWavePressureDocument
    poly=bank.vertices[bank.faces][:,[0,3,2,1]]
    z=np.conjugate(bank.responses)[None,None,:,:]
    return OrcaWavePressureDocument(poly,bank.centers,-bank.normals,bank.areas,(0,),(0,),(1,),(0,),
        2*np.pi/bank.omegas,[0.],(bank.composition,),z.real,z.imag,json.dumps(dict(source_sha256='0'*64,
        phasor='exp(+i omega t)',normals='out of fluid',phase_origin=[0.,0.,0.],
        units=dict(length='m',mass='kg',force='N'),water_depth=20.,rho=1025.,gravity=9.81)))


def job_fixture():
    from anyhydro.orcawave_pressure import ImportedPressureBank
    from anymesher.serialize import mesh_to_dict
    project,mesh,h=fixture();bank=ImportedPressureBank.from_dict(json.loads(h.source_payload_json));b=json.loads(h.binding_json)
    settings={k:b[k] for k in ('surfaces','source_to_project','limits','force_relative_limit','moment_relative_limit','force_absolute_limit','moment_absolute_limit')}
    settings.update(body_id=0,heading=0.,composition='fixed_diffraction',wave_type='regular',omega=1.,height=2.,phase=0.,duration=1.,dt=.5,name='Imported')
    return project,mesh,dict(document=project_to_dict(project),mesh_data=mesh_to_dict(mesh),source=raw_document(bank).to_dict(),settings=settings)


def test_worker_entry_actual_token_and_cancellation():
    from anyfesolver import CancellationToken
    from anyfem.application.orcawave_pressure import prepare_orcawave_job
    project,mesh,kwargs=job_fixture();token=CancellationToken()
    result=prepare_orcawave_job(**kwargs,cancellation_token=token)
    assert result['target_polygons'] and result['source_centers']
    token.cancel('test')
    with pytest.raises(Exception,match='cancel|test'):prepare_orcawave_job(**kwargs,cancellation_token=token)


def test_actual_contained_worker_import_and_prepare(tmp_path):
    from anyfesolver import CancellationToken
    from anyfem.job_process import run_in_worker_process
    from anyfem.application.orcawave_pressure import inspect_orcawave_file,prepare_orcawave_job
    from anyfileio.orcawave import OrcaWavePressureDocument
    project,mesh,kwargs=job_fixture();path=tmp_path/'pressure.json'
    OrcaWavePressureDocument.from_dict(kwargs['source']).save(path)
    def run(function,arguments):
        return run_in_worker_process(function=function,kwargs=arguments,snapshot=None,project_override={},
            progress=lambda v:None,token=CancellationToken(),containment=True,on_tree=lambda p,t:None)
    imported=run(inspect_orcawave_file,{'path':str(path)})
    assert imported==kwargs['source']
    kwargs['source']=imported
    prepared=run(prepare_orcawave_job,kwargs)
    from anyloads.prescribed_pressure import PrescribedPressureHistory
    history=PrescribedPressureHistory.from_dict(prepared['history'])
    assert history.evaluate(0.)[:,:3].sum(axis=0)==pytest.approx([0,0,-200],abs=1e-8)


def test_total_irregular_worker_portable_replay():
    from dataclasses import replace
    from anyhydro.orcawave_pressure import ImportedPressureBank
    from anyloads.prescribed_pressure import PrescribedPressureHistory
    from anyfem.application.orcawave_pressure import prepare_orcawave_job
    project,mesh,kwargs=job_fixture()
    _,_,old=fixture();bank=ImportedPressureBank.from_dict(json.loads(old.source_payload_json))
    bank=replace(bank,omegas=np.array([.6,1.,1.7]),responses=np.array([[100+20j],[50-30j],[80+10j]]),
        composition='total_with_source_motion',bank_id='')
    kwargs['source']=raw_document(bank).to_dict()
    kwargs['settings'].update(composition=bank.composition,wave_type='irregular',height=2.,peak_period=6.,gamma=1.,seed=42)
    prepared=prepare_orcawave_job(**kwargs);h=PrescribedPressureHistory.from_dict(prepared['history'])
    report=prepared['spectrum'];wp=2*np.pi/6.
    fraction=np.exp(-1.25*(wp/1.7)**4)-np.exp(-1.25*(wp/.6)**4)
    assert report['retained_variance']==pytest.approx(.25*fraction,rel=1e-8)
    ApplyPrescribedPressure(h,'Imported',canonical_hash(project_to_dict(project))).do(project)
    reopened=project_from_dict(project_to_dict(project))
    replay=reopened.load_cases['Imported'].wet_pressures[0].history
    assert replay.realization_json==h.realization_json
    assert replay.binding_json==h.binding_json
    realization=json.loads(h.realization_json)
    for child in reopened.series_children('Imported'):
        expected=-2*sum(c['amplitude']*np.real(bank.responses[k,0]*np.exp(1j*(c['phase']-c['omega']*child.time)))
                        for k,c in enumerate(realization['components']))
        assert _total_force(build_fe_model(reopened,mesh,child.name))==pytest.approx([0,0,expected],abs=1e-8)


def test_batch_remap_reused_and_surface_scope_refused(monkeypatch):
    from anyfem.solve.build import _BatchLoadContext,_build_load_case
    import anyfem.application.orcawave_pressure as app
    project,mesh,h=fixture()
    ApplyPrescribedPressure(h,'Imported',canonical_hash(project_to_dict(project))).do(project)
    newmesh=project.generate_mesh(.25)
    bare=project_from_dict(project_to_dict(project));bare.load_cases.clear();bare.analyses.clear()
    built=build_fe_model(bare,newmesh,require_loads=False,require_supports=False);built.project=project
    context=_BatchLoadContext(built);calls=[];original=app.prepare_prescribed_pressure
    def counted(*a,**kw):calls.append(1);return original(*a,**kw)
    monkeypatch.setattr(app,'prepare_prescribed_pressure',counted)
    for child in project.series_children('Imported'):
        _build_load_case(project,newmesh,child,_load_context=context)
    context.verify_unchanged()
    assert len(calls)==1
    from dataclasses import replace
    parent=project.load_cases['Imported'];parent.wet_pressures[0]=replace(parent.wet_pressures[0],surfaces=('absent',))
    with pytest.raises(ValueError,match='selection|surface'):
        build_fe_model(project,newmesh,project.series_children('Imported')[0].name)


def test_follower_and_imperfect_cases_refused():
    project,mesh,h=fixture();case=project.load_case();case.time=0.;case.follower_pressure=True
    case.wet_pressures.append(WetPressure(history=h,mapping_key='element'))
    with pytest.raises(ValueError,match='dead loads'):build_fe_model(project,mesh)
    case.follower_pressure=False;project.imperfections.append(object())
    with pytest.raises(ValueError,match='imperfect'):effective_prescribed_history(project,mesh,h)


def test_same_mesh_region_redefinition_invalidates_pressure_and_batch_cache():
    from anyfem.model.regions import ManualRegion,RegionRegistry
    from dataclasses import replace
    from anyfem.solve.build import _BatchLoadContext,_build_load_case
    project,mesh,h=fixture()
    ApplyPrescribedPressure(h,'Imported',canonical_hash(project_to_dict(project))).do(project)
    bare=project_from_dict(project_to_dict(project));bare.load_cases.clear();bare.analyses.clear()
    built=build_fe_model(bare,mesh,require_loads=False,require_supports=False);built.project=project
    context=_BatchLoadContext(built);child=project.series_children('Imported')[0]
    _build_load_case(project,mesh,child,_load_context=context)
    region_id=project.wet_surfaces[0].region.id
    project.regions=RegionRegistry(replace(r,definition=ManualRegion(())) if r.id==region_id else r for r in project.regions)
    with pytest.raises(ValueError,match='binding changed'):effective_prescribed_history(project,mesh,h)
    with pytest.raises(ValueError,match='binding changed'):_build_load_case(project,mesh,child,_load_context=context)


@pytest.mark.parametrize('rotate',[False,True])
def test_nonuniform_panel_boundary_moments_and_rigid_frame(rotate):
    from dataclasses import replace
    from anyhydro.orcawave_pressure import ImportedPressureBank
    from anyloads.orcawave_spectrum import regular_realization
    from anygeometry.panel_pressure_projection import validate_panel_geometry
    project,mesh,old=fixture();bank=ImportedPressureBank.from_dict(json.loads(old.source_payload_json))
    polygons=np.array([[(0,0,-1),(.8,0,-1),(.8,1,-1),(0,1,-1)],[(.8,0,-1),(2,0,-1),(2,1,-1),(.8,1,-1)]])
    _,normals,centers,areas=validate_panel_geometry(polygons)
    bank=replace(bank,vertices=polygons.reshape(-1,3),faces=np.arange(8).reshape(2,4),normals=normals,centers=centers,
        areas=areas,panel_ids=(0,1),surface_ids=('body0','body0'),responses=np.array([[100.,60+25j]]),bank_id='')
    transform=np.eye(4)
    if rotate:
        from scipy.spatial.transform import Rotation
        transform[:3,:3]=Rotation.from_euler('xyz',[.3,.2,.5]).as_matrix();transform[:3,3]=[3,4,2]
        points=np.array([(0,0,-1),(2,0,-1),(2,1,-1),(0,1,-1)])@transform[:3,:3].T+transform[:3,3]
        project,face=_project(points);project.add_wet_surface(WetSurface(ref=project.face(face),fluid_side='positive_normal',name='wet'))
        mesh=project.generate_mesh(.5)
    h=prepare_prescribed_pressure(project,mesh,bank,regular_realization(bank,omega=1.,height=2.),times=(0.,np.pi/2),
        surfaces=(project.wet_surfaces[0].id,),source_to_project=transform,
        limits=dict(gap_m=1e-8,max_angle=.01,coverage_tolerance=1e-6,convergence_tolerance=1e-5),
        force_relative_limit=1e-7,moment_relative_limit=1e-7,force_absolute_limit=1e-8,moment_absolute_limit=1e-8)
    coords=np.array([mesh.nodes[n] for n in h.node_ids])-transform[:3,3]
    for time,force,moment in [(0.,[0,0,-152.],[-76.,132.8,0]),(np.pi/2,[0,0,-30.],[-15.,42.,0])]:
        nodal=h.evaluate(time)
        assert nodal[:,:3].sum(axis=0)==pytest.approx(transform[:3,:3]@force,abs=1e-7)
        assert (np.cross(coords,nodal[:,:3])+nodal[:,3:]).sum(axis=0)==pytest.approx(transform[:3,:3]@moment,abs=1e-7)


def test_native_triangle_rotational_pressure_work():
    from dataclasses import replace
    from anyhydro.orcawave_pressure import ImportedPressureBank
    from anyloads.orcawave_spectrum import regular_realization
    _,_,old=fixture();bank=ImportedPressureBank.from_dict(json.loads(old.source_payload_json))
    points=np.array([(0.,0.,-1.),(2.,0.,-1.),(0.,1.,-1.)])
    project,face=_project(points);project.add_wet_surface(WetSurface(ref=project.face(face),fluid_side='positive_normal',name='wet'))
    from anygeometry.surfaces import Plane
    project.geometry.set_face_surface(face,Plane(points[0],points[1]-points[0],points[2]-points[0]))
    mesh=project.generate_mesh(.5)
    bank=replace(bank,vertices=points,faces=np.array([[0,1,2,2]]),centers=points.mean(axis=0)[None],areas=np.array([1.]),bank_id='')
    h=prepare_prescribed_pressure(project,mesh,bank,regular_realization(bank,omega=1.,height=2.),times=(0.,.5),
        surfaces=(project.wet_surfaces[0].id,),source_to_project=np.eye(4),
        limits=dict(gap_m=1e-8,max_angle=.01,coverage_tolerance=1e-6,convergence_tolerance=1e-5),
        force_relative_limit=1e-7,moment_relative_limit=1e-7,force_absolute_limit=1e-8,moment_absolute_limit=1e-8)
    assert np.linalg.norm(h.responses[:,:,3:])>1e-6


def test_constrained_pressure_only_structural_solution():
    from anyfem import solve_linear_static
    project,mesh,h=fixture();case=project.load_case('pressure');case.time=0.
    case.wet_pressures.append(WetPressure(history=h,mapping_key='element'))
    result=solve_linear_static(project,mesh=mesh,load_case=case.name)
    reference=project.load_case('uniform reference');reference.wet_pressures.append(WetPressure(value=100.))
    expected=solve_linear_static(project,mesh=mesh,load_case=reference.name)
    assert np.isfinite(result.displacements).all() and np.linalg.norm(result.displacements)>0
    np.testing.assert_allclose(result.displacements,expected.displacements,rtol=1e-10,atol=1e-14)


def test_waterline_clipping_partial_element_pressure_and_moment():
    from dataclasses import replace
    from anyhydro.orcawave_pressure import ImportedPressureBank
    from anyloads.orcawave_spectrum import regular_realization
    from anygeometry.panel_pressure_projection import validate_panel_geometry
    _,_,old=fixture();bank=ImportedPressureBank.from_dict(json.loads(old.source_payload_json))
    source=np.array([(0,0,-1),(2,0,-1),(2,0,0),(0,0,0)],float)
    _,normals,centers,areas=validate_panel_geometry([source])
    bank=replace(bank,vertices=source,normals=normals,centers=centers,areas=areas,bank_id='')
    target=np.array([(0,0,-1),(2,0,-1),(2,0,1),(0,0,1)],float)
    project,face=_project(target);project.add_wet_surface(WetSurface(ref=project.face(face),fluid_side='positive_normal',name='wet'))
    mesh=project.generate_mesh(3.)
    h=prepare_prescribed_pressure(project,mesh,bank,regular_realization(bank,omega=1.,height=2.),times=(0.,.5),
        surfaces=(project.wet_surfaces[0].id,),source_to_project=np.eye(4),
        limits=dict(gap_m=1e-8,max_angle=.01,coverage_tolerance=1e-6,convergence_tolerance=1e-5),
        force_relative_limit=1e-7,moment_relative_limit=1e-7,force_absolute_limit=1e-8,moment_absolute_limit=1e-8)
    nodal=h.evaluate(0.);xyz=np.array([mesh.nodes[n] for n in h.node_ids])
    assert nodal[:,:3].sum(axis=0)==pytest.approx([0,200,0],abs=1e-8)
    assert (np.cross(xyz,nodal[:,:3])+nodal[:,3:]).sum(axis=0)==pytest.approx([100,0,200],abs=1e-8)
    assert h.preview_pressures(0.)==pytest.approx([50.],abs=1e-8)


def test_independently_faceted_curved_hull_pipeline():
    from anyfem import Project,steel
    from anygeometry import OrientedEdge
    from anygeometry.panel_pressure_projection import validate_panel_geometry
    from anyhydro.orcawave_pressure import ImportedPressureBank
    from anyloads.orcawave_spectrum import regular_realization
    def facets(count):
        angle=np.linspace(0,np.pi/2,count+1)
        low=np.c_[np.cos(angle),np.sin(angle),np.full(count+1,-1.)]
        high=low.copy();high[:,2]=-.2
        return [np.array([low[i],low[i+1],high[i+1],high[i]]) for i in range(count)]
    project=Project();project.add_material(steel('S355',.01));project.add_plate_section('plate',.01,'S355')
    g=project.geometry;vertices={};edges={}
    for k,polygon in enumerate(facets(3)):
        ids=[]
        for p in polygon:
            key=tuple(p)
            if key not in vertices:vertices[key]=g.add_point(*p)
            ids.append(vertices[key])
        loop=[]
        for a,b in zip(ids,ids[1:]+ids[:1]):
            key=tuple(sorted((a,b)))
            if key not in edges:edges[key]=g.add_line(a,b)
            edge=edges[key];loop.append(OrientedEdge(edge,g.edges[edge].start==a))
        face=g.add_face_from_loop(tuple(loop),(0,1,2,3));project.assign_plate(face,'plate')
        project.add_wet_surface(WetSurface(ref=project.face(face),fluid_side='positive_normal',name=f'facet{k}'))
    mesh=project.generate_mesh(1.)
    polygons,normals,centers,areas=validate_panel_geometry(facets(5))
    bank=ImportedPressureBank(np.asarray(polygons).reshape(-1,3),np.arange(20).reshape(5,4),centers,normals,areas,
        np.array([1.]),np.full((1,5),100+0j),tuple(range(5)),('body0',)*5,0.,'fixed_diffraction',0,20.,1025.,9.81,'{}')
    h=prepare_prescribed_pressure(project,mesh,bank,regular_realization(bank,omega=1.,height=2.),times=(0.,.5),
        surfaces=tuple(s.id for s in project.wet_surfaces),source_to_project=np.eye(4),
        limits=dict(gap_m=.05,max_angle=np.deg2rad(25),coverage_tolerance=1e-6,convergence_tolerance=.03,max_depth=8,max_triangles=100000),
        force_relative_limit=1e-6,moment_relative_limit=1e-6,force_absolute_limit=1e-8,moment_absolute_limit=1e-8)
    nodal=h.evaluate(0);xyz=np.array([mesh.nodes[n] for n in h.node_ids])
    assert nodal[:,:3].sum(axis=0)==pytest.approx([-80,-80,0],abs=1e-7)
    assert (np.cross(xyz,nodal[:,:3])+nodal[:,3:]).sum(axis=0)==pytest.approx([-48,48,0],abs=1e-7)
    assert json.loads(h.binding_json)['projection']['projected_targets']>0


def test_existing_mapped_history_with_older_anyloads(monkeypatch):
    import sys
    from test_hydro_pressure_series import micro
    from anyfem.solve.build import _supported_mapped_history, wet_element_pressures

    monkeypatch.setitem(sys.modules, "anyloads.prescribed_pressure", None)
    project, mesh, history = micro()
    assert _supported_mapped_history(history)
    child = project.series_children("wave")[0]
    values = wet_element_pressures(project, mesh, project.resolve_case(child))
    assert [value.pressure for value in values] == pytest.approx(-history.evaluate(child.time))
