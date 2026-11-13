"""Build a local, structured L01 vessel without shipping its licensed geometry.

    python examples/free_floating_l01.py --gdf LOCAL.gdf --reference LOCAL.json --output OUT

Generation performs meshing and assembled-mass verification. It does not silently solve waves or structural batches.

Optional stored pressure source (paths or environment only; no pressure data is ever copied into a repository):

    --pressure-source anyhydro|orcawave   (env ANYFEM_L01_PRESSURE_SOURCE; default anyhydro)
    --pressure-file PATH                  (env ANYFEM_L01_PRESSURE_FILE)
    --pressure-settings PATH              (env ANYFEM_L01_PRESSURE_SETTINGS; JSON of mapping limits, see pressure_mapping_check)

anyhydro: an ANYhydro family-spec JSON (see anyfem.application.orcawave_pressure.bank_from_family_spec) or a portable
ANYhydro bank.  orcawave: a stored .owr (needs OrcFxAPI + licence, imported only then) or a portable OrcaWave bank.
With a settings file the pressure is mapped onto the structural wet surfaces through the same gates as the GUI and the
force/moment transfer errors are written to pressure_mapping.json.  Only the file name and SHA-256 are recorded.
The separate wave request retains the user's Hs=1m/Tp=6s and deterministic illustrative defaults.
"""
from __future__ import annotations
import argparse,json,os,shutil,tempfile
from dataclasses import asdict
from pathlib import Path


def pressure_mapping_check(project,mesh,data,settings):
    """Map a stored pressure bank through the GUI's gates and return the transfer-error evidence (no pressure values)."""
    from anyfem.application.orcawave_pressure import prepare_orcawave_job
    from anyfem.io.project_file import project_to_dict
    from anymesher.serialize import mesh_to_dict
    from anyloads.prescribed_pressure import PrescribedPressureHistory
    settings=dict(settings)
    settings.setdefault('surfaces',[s.id for s in project.wet_surfaces])
    settings.setdefault('source_to_project',[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]])
    settings.setdefault('wave_type','regular');settings.setdefault('phase',0.);settings.setdefault('height',1.)
    settings.setdefault('duration',1.);settings.setdefault('dt',.5);settings.setdefault('name','pressure mapping check')
    result=prepare_orcawave_job(document=project_to_dict(project),mesh_data=mesh_to_dict(mesh),source=data,settings=settings)
    binding=json.loads(PrescribedPressureHistory.from_dict(result['history']).binding_json)
    return dict(projection=binding['projection'],
                limits=dict(force_relative=binding['force_relative_limit'],moment_relative=binding['moment_relative_limit'],
                            force_absolute=binding['force_absolute_limit'],moment_absolute=binding['moment_absolute_limit']),
                frequency_evidence=[dict(omega=e['omega'],force=e['force'],moment=e['moment']) for e in binding['frequency_evidence']])


def pressure_source_record(kind,path,data):
    from anyfem.application.orcawave_pressure import file_sha256
    record=dict(kind=kind,file_name=Path(path).name,sha256=file_sha256(path),
                note='Only the file name and hash are recorded; pressure data stays outside the repositories.')
    if data.get('type','').startswith('imported_pressure_bank'):
        record.update(bank_id=data['bank_id'],composition=data['composition'],panels=len(data['panel_ids']),omegas=len(data['omegas']))
    return record


def publish_model(project,mesh,manifest,reference_file,output,*,direct_lid_profile=None,pressure=None):
    from anyfem.application.panel_hull_example import add_reference_deadweight
    from anyfem.application.irregular_wave_input import IrregularWaveInput
    from anyfem.application.floating_job import floating_settings_from_wave
    from anyfem.document import canonical_hash
    from anyfem.io.project_file import save_project,load_project
    from anymesher.serialize import mesh_to_dict
    reference=json.loads(Path(reference_file).read_text())
    body=reference['body']
    properties=add_reference_deadweight(project,mesh,manifest,body['mass_kg'],body['centre_of_mass_m'])
    manifest['reference_condition']=dict(path=str(Path(reference_file).absolute()),name=body['name'],
        limitation='Only mass and CG targets used; coefficients, RAOs and reference inertia are not injected.')
    manifest['mesh']=dict(nodes=len(mesh.nodes),quads=len(mesh.quads),tris=len(mesh.tris),beams=len(mesh.beams),
                          identity=canonical_hash(mesh_to_dict(mesh)),element_order='quadratic')
    wave=IrregularWaveInput(name='L01 longitudinal irregular sea (+X propagation)',wave_type='irregular',significant_height=1.,peak_period=6.,gamma=3.3,
                           seed=0,heading=0.,duration=120.,dt=.1,depth=100.,component_count=32,
                           include_hydrostatic=False,follow_surface=False)
    floating=dict(envelope_extra_face_ids=manifest['closing_face_ids'],panel_size=0.,max_panels=600,
                  source_profile=dict(name='faceted_quad_hull_v1',full_panel_limit=4000))
    if direct_lid_profile is not None:
        from anyhydro.direct_lid_profile import profile_from_dict
        floating['direct_lid_profile']=profile_from_dict(direct_lid_profile).to_dict()
    settings=floating_settings_from_wave(asdict(wave),**floating)
    request=dict(kind='anyfem_structured_floating_vessel_request_v1',project='structured_l01.anyfem',wave=asdict(wave),floating=floating,
                 components=[asdict(c) for c in settings.components],realization_digest=settings.realization_digest(),
                 sample_count=len(settings.times),stationary_fe_mesh=True,
                 free_free_batch=dict(constraint_mode='nullspace',inertia_relief=True,allow_unbalanced_free_free=False))
    pressure_record=pressure_mapping=None
    if pressure is not None:
        # Read and map before anything is published; a refusal (licence, kind mismatch, gate) leaves no output behind.
        from anyfem.application.orcawave_pressure import load_pressure_source
        data=load_pressure_source(pressure['kind'],pressure['path'])
        pressure_record=pressure_source_record(pressure['kind'],pressure['path'],data)
        if pressure.get('settings') is not None:
            pressure_mapping=pressure_mapping_check(project,mesh,data,pressure['settings'])
    output=Path(output).absolute()
    if output.exists():raise FileExistsError(f'preserve existing output: {output}')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.l01-model-',dir=output.parent) as temporary:
        stage=Path(temporary)
        save_project(project,stage/'structured_l01.anyfem')
        reopened=load_project(stage/'structured_l01.anyfem')
        if len(reopened.masses)!=len(project.masses) or reopened.supports or reopened.load_cases:
            raise ValueError('saved project lost explicit mass or acquired old loads/supports')
        for name,data in (('model_manifest.json',manifest),('wave_request.json',request),('mesh.json',mesh_to_dict(mesh)),
                          ('status.json',dict(state='geometry_mesh_mass_verified',floating_load_applied=False,solved=False,
                              message='Geometry, mesh, explicit mass ledger and save/reopen verified. Floating preparation pending.'))):
            (stage/name).write_text(json.dumps(data,indent=2,default=str),encoding='utf-8')
        if pressure_record is not None:
            (stage/'pressure_source.json').write_text(json.dumps(pressure_record,indent=2),encoding='utf-8')
        if pressure_mapping is not None:
            (stage/'pressure_mapping.json').write_text(json.dumps(pressure_mapping,indent=2),encoding='utf-8')
        (stage/'README.txt').write_text('Structured L01 vessel: open structured_l01.anyfem in ANYfem.\n'
            'Eight initially empty tanks, two transverse bulkheads, frames/longitudinals, two-level deckhouse.\n'
            'Modelled fixed deadweight matches reference mass/CG; actual FE inertia is authoritative.\n'
            'Scantlings and internal layout are illustrative. No liquid-pressure or sloshing model is implied.\n'
            'Hs1m/Tp6s wave request saved separately; floating loads and structural results are not yet applied.\n'
            'Licensed source geometry stays local. No redistribution or engineering qualification claim.\n',encoding='utf-8')
        # Same-volume, no-clobber directory publication. No existing output is superseded.
        os.rename(stage,output)
    return dict(output=str(output),mass_kg=float(properties.mass),cg_m=properties.cg.tolist(),mesh=manifest['mesh'])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gdf',type=Path,required=True);parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--mesh-size',type=float,default=1.5)
    parser.add_argument('--direct-lid-profile',type=Path,help='Explicit complete ANYhydro DirectLidProfile JSON; mathematical sources only')
    parser.add_argument('--pressure-source',choices=('anyhydro','orcawave'),default=os.environ.get('ANYFEM_L01_PRESSURE_SOURCE'))
    parser.add_argument('--pressure-file',type=Path,default=os.environ.get('ANYFEM_L01_PRESSURE_FILE'))
    parser.add_argument('--pressure-settings',type=Path,default=os.environ.get('ANYFEM_L01_PRESSURE_SETTINGS'))
    args=parser.parse_args(argv)
    if args.pressure_file is not None and args.pressure_source is None:args.pressure_source='anyhydro'
    if args.pressure_file is None and (args.pressure_source is not None or args.pressure_settings is not None):
        parser.error('--pressure-source/--pressure-settings (or ANYFEM_L01_PRESSURE_*) need --pressure-file; nothing was started')
    if args.output.exists():parser.error('output exists; choose a new directory to preserve it')
    from anyfem.application.panel_hull_example import build_structured_hull
    project,manifest=build_structured_hull(args.gdf,mesh_size=args.mesh_size)
    mesh=project.generate_mesh(args.mesh_size)
    profile=None if args.direct_lid_profile is None else json.loads(args.direct_lid_profile.read_text())
    pressure=None
    if args.pressure_file is not None:
        pressure=dict(kind=args.pressure_source,path=str(args.pressure_file),
                      settings=None if args.pressure_settings is None else json.loads(Path(args.pressure_settings).read_text()))
    print(json.dumps(publish_model(project,mesh,manifest,args.reference,args.output,direct_lid_profile=profile,pressure=pressure),indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
