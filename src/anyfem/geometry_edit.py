"""Selected-only engineering edits over canonical sections and mesh controls."""
from dataclasses import dataclass,fields,replace,is_dataclass
from uuid import uuid4
from copy import copy
from anygeometry import EntityRef
from . import commands
from .mesh_sizes import MeshSizeProperty,NamedRefinement,SetMeshSizeProperty,AssignMeshSize,_check_global
from .property_assignment import AtomicPropertyAssignment

class _Mixed:
    def __str__(self):return 'Mixed'
    def __repr__(self):return 'MIXED'

MIXED=_Mixed()


def _value(value):
    if hasattr(value,'tolist'):return _value(value.tolist())
    if isinstance(value,(list,tuple)):return tuple(_value(item) for item in value)
    if isinstance(value,dict):return tuple((key,_value(item)) for key,item in sorted(value.items()))
    if is_dataclass(value):return (type(value).__name__,tuple((field.name,_value(getattr(value,field.name))) for field in fields(value)))
    return value
BEAM_FIELDS=('section','material','profile','web_height','web_thickness','flange_width','flange_thickness','rotation_deg','attachment_side','offset_mode','eccentricity','web_direction','mesh_size_id','mesh_size')
PLATE_FIELDS=('section','material','thickness','mesh_size_id','mesh_size')


def _shape(record):
    return tuple((field.name,_value(getattr(record,field.name))) for field in fields(record) if field.name not in {'id','name'})


def _unique_name(records,base):
    names={name.casefold() for name in records};name=base;index=2
    while name.casefold() in names:name=f'{base} {index}';index+=1
    return name


@dataclass(frozen=True)
class GeometryEditDraft:
    project: object
    references: tuple
    skipped: int
    geometry_revision: int
    state: tuple
    mesh_bindings: dict

    @classmethod
    def capture(cls,project,references):
        original=tuple(dict.fromkeys(references))
        if project.mesh_only:raise ValueError('Select model plates or beam axes; imported mesh entities cannot be edited here.')
        if any(not isinstance(ref,EntityRef) for ref in original):raise ValueError('Select geometry plates or lines, not mesh entities.')
        refs=tuple(ref for ref in original if ref.kind in {'face','edge'})
        if not refs:raise ValueError('Select one or more model plates or beam axes.')
        for ref in refs:project._require_entity(ref)
        bindings=cls._bindings(project,refs);state=cls._state(project,refs,bindings)
        return cls(project,refs,len(original)-len(refs),project.geometry.revision,state,bindings)

    @staticmethod
    def _bindings(project,refs):
        result={ref:[] for ref in refs}
        for item in project.refinements:
            if item.ref is None:continue
            for target in project.resolve_geometry_attachment(item.ref):
                if target in result:result[target].append(item)
        return {ref:tuple(items) for ref,items in result.items()}

    @staticmethod
    def _state(project,refs,bindings=None):
        target=tuple((ref,project.face_sections.get(ref.id) if ref.kind=='face' else project.edge_sections.get(ref.id)) for ref in refs)
        definitions=tuple((kind,name,record.id,_shape(record)) for kind in ('plate_sections','beam_sections') for name,record in sorted(getattr(project,kind).items()))
        mesh=tuple(sorted(project.mesh_size_properties.items()))
        binding_map=GeometryEditDraft._bindings(project,refs) if bindings is None else bindings
        bindings=tuple((ref,tuple(_value(item) for item in items)) for ref,items in binding_map.items())
        return target,definitions,mesh,bindings

    @property
    def beam_refs(self):return tuple(ref for ref in self.references if ref.kind=='edge')
    @property
    def plate_refs(self):return tuple(ref for ref in self.references if ref.kind=='face')
    @property
    def counts(self):return {'beam':len(self.beam_refs),'plate':len(self.plate_refs),'skipped':self.skipped}

    def mesh_controls(self,ref):
        return self.mesh_bindings[ref]

    def shared(self,kind,field):
        refs=self.beam_refs if kind in {'beam','edge'} else self.plate_refs
        registry=self.project.beam_sections if kind in {'beam','edge'} else self.project.plate_sections
        assignments=self.project.edge_sections if kind in {'beam','edge'} else self.project.face_sections
        values=[]
        for ref in refs:
            name=assignments.get(ref.id);record=registry.get(name)
            if field=='section':value=name
            elif field in {'mesh_size_id','mesh_size'}:
                controls=self.mesh_controls(ref);named=[item for item in controls if isinstance(item,NamedRefinement)]
                if field=='mesh_size_id':value=named[-1].property_id if named else None
                else:
                    sizes=[item.size for item in controls]
                    value=sizes[0] if sizes and all(size==sizes[0] for size in sizes) else MIXED if sizes else None
            else:value=getattr(record,field,None)
            values.append(_value(value))
        return values[0] if values and all(value==values[0] for value in values) else MIXED if values else None

    def validate(self,current_project=None,*,read_only=False):
        project=self.project if current_project is None else current_project
        if read_only or project.read_only_reason:raise PermissionError('This project is read-only')
        if project is not self.project or project.geometry.revision!=self.geometry_revision or self._state(project,self.references)!=self.state:raise ValueError('The project, selection assignments or definitions changed; reopen Edit.')
        for ref in self.references:project._require_entity(ref)
        return project

    def command(self,*,beam=None,plate=None,current_project=None,read_only=False):
        project=self.validate(current_project,read_only=read_only)
        patches={'beam':dict(beam or {}),'plate':dict(plate or {})};children=[]
        # Candidate registries let all validation and variant reuse finish before mutation.
        candidates={'beam':dict(project.beam_sections),'plate':dict(project.plate_sections)}
        sizes=dict(project.mesh_size_properties)
        for kind,refs,allowed in (('beam',self.beam_refs,BEAM_FIELDS),('plate',self.plate_refs,PLATE_FIELDS)):
            patch=patches[kind]
            unknown=set(patch)-set(allowed)
            if unknown:raise ValueError('Unknown '+kind+' inputs: '+', '.join(sorted(unknown)))
            if patch and not refs:raise ValueError('No selected '+kind+' targets for these inputs.')
            if not patch:continue
            registry=candidates[kind];original=project.beam_sections if kind=='beam' else project.plate_sections
            assignments=project.edge_sections if kind=='beam' else project.face_sections
            explicit=patch.get('section');base_name=explicit
            if explicit is not None and explicit not in registry:
                base_name=next((name for name,record in registry.items() if record.id==explicit),None)
                if base_name is None:raise ValueError('Choose an existing '+kind+' section.')
            changes={key:value for key,value in patch.items() if key not in {'section','mesh_size_id','mesh_size'}}
            if 'material' in changes and changes['material'] not in project.materials:raise ValueError('Choose an existing material.')
            for ref in refs:
                current=assignments.get(ref.id);name=base_name if explicit is not None else current;record=registry.get(name)
                if changes:
                    if record is None:raise ValueError('Unassigned '+kind+' target: choose a base section before changing section inputs.')
                    if kind=='beam' and 'eccentricity' in changes and changes.get('offset_mode',record.offset_mode)!='manual':raise ValueError('Manual eccentricity requires Offset mode = manual.')
                    candidate=replace(record,**changes)
                    if _shape(candidate)!=_shape(record):
                        match=next((item for item in registry.values() if _shape(item)==_shape(candidate)),None)
                        if match is None:
                            candidate=replace(candidate,name=_unique_name(registry,record.name+' (selection)'),id=str(uuid4()))
                            registry[candidate.name]=candidate;children.append(commands.AddBeamSection(candidate) if kind=='beam' else commands.AddPlateSection(candidate));match=candidate
                        name=match.name
                if kind=='beam' and name is not None and ('web_direction' in changes or explicit is not None and registry[name].web_direction is not None):
                    view=copy(project);view.beam_sections=registry;view.edge_sections=dict(project.edge_sections);view.edge_sections[ref.id]=name
                    view.beam_section_frame(ref.id)
                if name is not None and name!=current:
                    children.append(commands.AssignBeam(ref.id,name) if kind=='beam' else commands.AssignPlate(ref.id,name))
            if 'mesh_size' in patch and 'mesh_size_id' in patch:raise ValueError('Choose a named mesh size or a numeric target, not both.')
            if 'mesh_size' in patch:
                size=float(patch['mesh_size']);_check_global(project,size)
                # Validate even a no-op numeric value through the canonical owner.
                MeshSizeProperty('Selection size',size)
                for ref in refs:
                    controls=self.mesh_controls(ref);legacy=[item for item in controls if not isinstance(item,NamedRefinement)];named=[item for item in controls if isinstance(item,NamedRefinement)]
                    if legacy:
                        if all(item.size==size for item in controls):continue
                        raise ValueError('This target uses a legacy/generator mesh control. Edit or remove that control in Mesh before replacing its target here.')
                    source=named[-1] if named else None
                    radius=source.radius if source is not None else 0.;growth=source.growth if source is not None else 1.5
                    definition=next((item for item in sizes.values() if (item.size,item.radius,item.growth)==(size,radius,growth)),None)
                    if definition is None:
                        base=(source.name+' (selection)') if source is not None else 'Selection size'
                        definition=MeshSizeProperty(_unique_name({item.name:item for item in sizes.values()},base),size,radius,growth)
                        sizes[definition.id]=definition;children.append(SetMeshSizeProperty(definition))
                    if [item.property_id for item in named]!=[definition.id]:children.append(AssignMeshSize(definition.id,(ref,)))
            elif 'mesh_size_id' in patch:
                definition=sizes.get(patch['mesh_size_id'])
                if definition is None:raise ValueError('Choose an existing named mesh size.')
                _check_global(project,definition.size);pending=[]
                for ref in refs:
                    controls=self.mesh_controls(ref);existing=[item.property_id for item in controls if isinstance(item,NamedRefinement)]
                    if existing==[definition.id]:continue
                    if any(not isinstance(item,NamedRefinement) for item in controls):raise ValueError('This target uses a legacy/generator mesh control. Edit or remove that control in Mesh before assigning a named preset here.')
                    pending.append(ref)
                if pending:children.append(AssignMeshSize(definition.id,tuple(pending)))
        return AtomicPropertyAssignment(tuple(children),'edit selected geometry properties') if children else None
