"""Application-owned engineering defaults for editable structural generators."""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import replace
from uuid import uuid5,NAMESPACE_URL
import math
from anygeometry import EntityRef
from anygeometry.errors import GeometryError
from .model.sections import PlateSection
from .mesh.refinement import Refinement

GENERATOR_KINDS=frozenset('plate bulkhead frame girder stiffener stiffened_panel cylinder cone'.split())
FIELDS=frozenset(('beam_section','plate_section','plate_thickness','material','mesh_size','mesh_size_mode'))
# mesh_size_mode: "override" (the default for a size set now) meshes each output
# plate and beam at mesh_size, finer or coarser than the global target.
# "refine" is the refine-only spatial zone and must be chosen explicitly.  Saved
# files without the key predate the mode and were advertised as the generator's
# own size, so they load as "override" (see load_generator_properties).
MESH_SIZE_MODES=frozenset(('refine','override'))

class GeneratorControlError(ValueError):
    """A generator's advertised mesh size cannot be turned into a control."""

def properties(values):
    if values is not None and not isinstance(values,Mapping):raise ValueError("Generator properties must be a mapping")
    values=dict(values or {})
    if set(values)-FIELDS-{'_managed'}:raise ValueError('Unknown generator engineering option')
    for key in ('plate_thickness','mesh_size'):
        if values.get(key) is not None:
            if isinstance(values[key],bool):raise ValueError(key+" must be a positive number")
            value=float(values[key])
            if not math.isfinite(value) or value<=0:raise ValueError(key.replace('_',' ')+' must be finite and positive')
            values[key]=value
    if values.get('mesh_size_mode') is not None and values['mesh_size_mode'] not in MESH_SIZE_MODES:raise ValueError('mesh size mode must be refine or override')
    for key in ('beam_section','plate_section','material'):
        if values.get(key) is not None and (not isinstance(values[key],str) or not values[key].strip()):raise ValueError(key+' must name an existing definition')
    if '_managed' in values:
        if not isinstance(values['_managed'],dict):raise ValueError('Invalid generator assignment provenance')
        for slot,state in values['_managed'].items():
            if not isinstance(slot,str) or len(slot.split('/'))!=2 or slot.split('/')[0] not in {'edge','face'} or not slot.split('/')[1].isdigit():raise ValueError('Invalid generator output slot')
            if not isinstance(slot,str) or not isinstance(state,dict) or set(state)!={'id','section_id'} or any(not isinstance(v,str) or not v for v in state.values()):raise ValueError('Invalid generator assignment provenance')
    return {key:value for key,value in values.items() if value is not None}

def member_edges(geometry,record):
    """Owner Member chains distinguish structural axes from shell boundaries."""
    if record.kind in {'generator.cylinder','generator.cone'} and not any(record.parameters.get(k) is not None for k in ('longitudinal_spacing','ring_spacing')):return set()
    candidates={ref.id for ref in record.outputs.values() if ref.kind=='edge'}
    return {use.edge_id for use in geometry.member_edge_uses.values() if use.edge_id in candidates}

def output_slots(geometry,record):
    members=member_edges(geometry,record)
    return {key:ref for key,ref in record.outputs.items() if ref.kind=='face' or (ref.kind=='edge' and ref.id in members)}

def _section(project,record,options,kind):
    registry=project.plate_sections if kind=='plate' else project.beam_sections
    source=options.get(kind+'_section');material=options.get('material')
    if material is not None and material not in project.materials:raise ValueError('Unknown material '+material)
    if source is not None and source not in registry:raise ValueError('Unknown '+kind+' section '+source)
    base=registry[source] if source is not None else None
    if kind=='plate':
        thickness=options.get('plate_thickness',base.thickness if base else None)
        if thickness is None:return None
        resolved_material=material or (base.material if base else None)
        if resolved_material is None:raise ValueError('Choose a material for the plate thickness')
        if base and thickness==base.thickness and resolved_material==base.material:return source
        candidate=PlateSection(f'Generator {record.feature_id} plate',thickness,resolved_material,id=str(uuid5(NAMESPACE_URL,f'{project.document_id}:generator:{record.feature_id}:plate')))
    else:
        if base is None:return None
        if material is None or material==base.material:return source
        candidate=replace(base,name=f'Generator {record.feature_id} beam',material=material,id=str(uuid5(NAMESPACE_URL,f'{project.document_id}:generator:{record.feature_id}:beam')))
    name=candidate.name
    base_name=name;number=0
    while name in registry and registry[name].id!=candidate.id:
        number+=1;name=f'{base_name} {candidate.id[:8]}-{number}'
    candidate=replace(candidate,name=name)
    registry[name]=candidate
    return name

def reapply_generator_properties(project):
    """Fill new output slots; authored singleton overrides remain authoritative."""
    if not project.generator_properties:return
    for identifier,raw in tuple(project.generator_properties.items()):
        record=project.geometry.features.get(int(identifier));options=properties(raw)
        if record.kind not in {'generator.'+kind for kind in GENERATOR_KINDS}:raise ValueError('Engineering options require a supported generator')
        if record.suppressed:continue
        slots=output_slots(project.geometry,record);managed=options.setdefault('_managed',{})
        if (options.get('plate_section') or options.get('plate_thickness')) and not any(r.kind=='face' for r in slots.values()):raise ValueError('Plate properties do not apply to this line-only generator')
        for key,state in tuple(managed.items()):
            if key not in slots:
                assignment=project.section_assignments.get(state['id'])
                if assignment is not None and assignment.section_id==state['section_id']:project.section_assignments.pop(assignment.id)
                managed.pop(key,None)
        sections={'face':_section(project,record,options,'plate'),'edge':_section(project,record,options,'beam')}
        if options.get('beam_section') and not any(ref.kind=='edge' for ref in slots.values()):raise ValueError('This generator has no structural members; enable stiffener spacing first')
        if options.get('material') and not any(sections.values()):raise ValueError('Choose a plate thickness/section or beam section to assign the material')
        # Resolve current assignments only after obsolete managed scopes are removed.
        project.resolve_section_assignments(strict=False,adopt_legacy=False)
        for key,ref in slots.items():
            section=sections[ref.kind]
            if section is None:continue
            kind='plate' if ref.kind=='face' else 'beam'
            current=(project.face_assignment_ids if kind=='plate' else project.edge_assignment_ids).get(ref.id)
            previous=managed.get(key)
            assignment=project.section_assignments.get(current)
            if assignment is not None and (previous is None or assignment.id!=previous['id'] or assignment.section_id!=previous['section_id']):continue
            assignment=project._assign_singleton(ref,kind,section,resolve_existing=False,materialize=False)
            managed[key]={'id':assignment.id,'section_id':assignment.section_id}
        tag=f'generator:{record.feature_id}:mesh'
        project.refinements[:]=[r for r in project.refinements if r.name!=tag]
        if options.get('mesh_size') is not None:
            mode=options.get('mesh_size_mode','override');options['mesh_size_mode']=mode
            project.refinements.extend(Refinement(size=options['mesh_size'],ref=ref,name=tag,mode=mode) for ref in dict.fromkeys(slots.values()))
        else:options.pop('mesh_size_mode',None)
        project.generator_properties[int(identifier)]=options
    project.adopt_geometry_attachment_regions()
    project.resolve_section_assignments(strict=False,adopt_legacy=False)

def generator_mesh_controls(project):
    """Incremental-runtime controls matching the generator-owned mesh sizes.

    Same ids, same sizes and same mode as the ``generator:<id>:mesh``
    refinements, so the NativeMeshControl path and the project mesh path agree:
    ``override`` controls own their component size (finer or coarser) and
    ``refine`` controls keep the old can-only-refine meaning.
    """
    from .native_meshing import ControlScope,NativeMeshControl
    controls=[]
    for identifier,raw in sorted(project.generator_properties.items()):
        try:options=properties(raw)
        except ValueError as error:raise GeneratorControlError(f'generator {identifier}: {error}') from error
        size=options.get('mesh_size')
        if size is None:continue
        # Only genuinely absent or suppressed generator intent contributes no
        # control; every other failure is a typed non-success.
        try:record=project.geometry.features.get(int(identifier))
        except LookupError:continue
        if record.suppressed:continue
        try:
            handles=tuple(project.geometry.handle(ref.kind,ref.id) for ref in dict.fromkeys(output_slots(project.geometry,record).values()))
            controls.append(NativeMeshControl.create(f'generator:{identifier}:mesh',scope=ControlScope.create(handles,include_descendants=True),target_size=size,size_mode=options.get('mesh_size_mode','override')))
        except (ValueError,TypeError,KeyError,GeometryError) as error:raise GeneratorControlError(f'generator {identifier}: cannot derive its mesh control: {error}') from error
    return tuple(controls)

def _migrate_generator_mesh_refinements(project):
    """Turn old generator-owned refine-only sizes into entity overrides.

    Only a refinement that matches a real, unsuppressed generator record's own
    output slot and its recorded ``mesh_size`` exactly (same tag, no centre, the
    zone defaults, a slot the generator actually owns) is migrated.  Any other
    zone, including a manual one that merely carries a generator-looking name,
    is left untouched.
    """
    for identifier,options in project.generator_properties.items():
        size=options.get('mesh_size')
        if size is None or options.get('mesh_size_mode')!='override':continue
        record=project.geometry.features.get(int(identifier))
        if record.suppressed:continue
        owned=set(output_slots(project.geometry,record).values())
        tag=f'generator:{identifier}:mesh'
        for index,item in enumerate(project.refinements):
            if item.name==tag and item.mode=='refine' and item.center is None and item.ref in owned and item.size==size and item.radius==0.0 and item.growth==1.5:
                project.refinements[index]=replace(item,mode='override')

def load_generator_properties(project,raw):
    if not isinstance(raw,dict):raise ValueError('generator_properties must be a mapping')
    result={}
    for key,value in raw.items():
        identifier=int(key);record=project.geometry.features.get(identifier)
        if record.kind not in {'generator.'+kind for kind in GENERATOR_KINDS}:raise ValueError('Generator properties reference an unsupported feature')
        option=properties(value)
        # A generator's engineering mesh size has always been offered as the
        # size of its own plates and beams, so files written before the mode
        # existed (no key) are entity overrides too; only an explicit
        # "refine" keeps the spatial refine-only meaning.
        if option.get('mesh_size') is not None:option.setdefault('mesh_size_mode','override')
        for field,registry in (('material',project.materials),('plate_section',project.plate_sections),('beam_section',project.beam_sections)):
            if option.get(field) is not None and option[field] not in registry:raise ValueError('Generator properties reference an unknown '+field)
        result[identifier]=option
    project.generator_properties=result
    _migrate_generator_mesh_refinements(project)


def remove_generator_properties(project,identifier):
    raw=project.generator_properties.pop(int(identifier),{})
    for state in raw.get('_managed',{}).values():
        assignment=project.section_assignments.get(state['id'])
        if assignment is not None and assignment.section_id==state['section_id']:project.section_assignments.pop(assignment.id)
    project.refinements[:]=[r for r in project.refinements if r.name!=f'generator:{identifier}:mesh']
    project.adopt_geometry_attachment_regions()
    project.resolve_section_assignments(strict=False,adopt_legacy=False)
    for kind,registry in (('plate',project.plate_sections),('beam',project.beam_sections)):
        owned=str(uuid5(NAMESPACE_URL,f'{project.document_id}:generator:{identifier}:{kind}'))
        for name,section in tuple(registry.items()):
            if section.id==owned and not any(a.section_id==owned for a in project.section_assignments.values()):registry.pop(name)
