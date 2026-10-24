"""Reusable application-owned mesh-size definitions over canonical refinements."""
from dataclasses import dataclass,field,replace
from uuid import uuid4,UUID
from anygeometry import EntityRef
from .mesh.refinement import Refinement
from .commands import Command


def _uuid(value):
    try:return str(UUID(str(value)))
    except (ValueError,TypeError,AttributeError):raise ValueError('Mesh size identity must be a UUID') from None


@dataclass(frozen=True)
class MeshSizeProperty:
    name: str
    size: float
    radius: float = 0.
    growth: float = 1.5
    id: str = field(default_factory=lambda:str(uuid4()))

    def __post_init__(self):
        if not isinstance(self.name,str) or not self.name.strip():raise ValueError('Enter a mesh size name')
        owner=Refinement(self.size,radius=self.radius,growth=self.growth,center=(0.,0.,0.),name=self.name.strip())
        object.__setattr__(self,'name',self.name.strip());object.__setattr__(self,'id',_uuid(self.id))
        for key in ('size','radius','growth'):object.__setattr__(self,key,getattr(owner,key))


@dataclass(frozen=True)
class NamedRefinement(Refinement):
    property_id: str = ''

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self,'property_id',_uuid(self.property_id))
        if self.ref is None or self.ref.kind not in {'edge','face'} or self.center is not None:raise ValueError('Named mesh sizes require geometry faces or lines')


def global_target(project):
    settings=project.native_mesh_settings
    target=settings.get('target_size') if isinstance(settings,dict) else getattr(settings,'target_size',None)
    return project.target_size if target is None else target


def _check_global(project,size):
    target=global_target(project)
    if target is not None and size>target:raise ValueError(f'Local mesh size {size:g} m exceeds the global target {target:g} m; reduce the property size or increase the global target')


def validate_mesh_sizes(project):
    names=set();assigned=set()
    for key,value in project.mesh_size_properties.items():
        if not isinstance(value,MeshSizeProperty) or key!=value.id:raise ValueError('Invalid named mesh size definition')
        if value.name.casefold() in names:raise ValueError('Mesh size names must be unique')
        names.add(value.name.casefold())
    for item in project.refinements:
        if not isinstance(item,NamedRefinement):continue
        definition=project.mesh_size_properties.get(item.property_id)
        if definition is None:raise ValueError('Named mesh size assignment references an unknown property')
        if any(getattr(item,key)!=getattr(definition,key) for key in ('size','radius','growth','name')):raise ValueError('Named mesh size assignment differs from its definition')
        if project.mesh_only:raise ValueError('Named mesh sizes cannot target a mesh-only document')
        for ref in project.resolve_geometry_attachment(item.ref):
            if ref in assigned:raise ValueError('A geometry target has more than one named mesh size')
            assigned.add(ref)


def _snapshot(project):
    return (dict(project.mesh_size_properties),list(project.refinements),dict(project.geometry_attachment_regions),tuple(project.regions),dict(getattr(project,'_singleton_region_cache',{})),getattr(project,'_singleton_region_cache_size',-1))


def _restore(project,state):
    definitions,refinements,attachments,regions,cache,count=state
    project.mesh_size_properties.clear();project.mesh_size_properties.update(definitions);project.refinements[:]=refinements
    project.geometry_attachment_regions.clear();project.geometry_attachment_regions.update(attachments)
    wanted={region.id:region for region in regions}
    for region in tuple(project.regions):
        if region.id not in wanted:project.regions.remove(region.id,cascade=True)
    for key,region in wanted.items():
        if key not in project.regions:project.regions.add(region)
    project._singleton_region_cache=dict(cache);project._singleton_region_cache_size=count


class _MeshSizeCommand(Command):
    def do(self,project):
        self._before=_snapshot(project)
        try:result=self.apply(project);validate_mesh_sizes(project)
        except BaseException:_restore(project,self._before);raise
        self._after=_snapshot(project);return result
    def undo(self,project):_restore(project,self._before)
    def redo(self,project):_restore(project,self._after)


@dataclass(eq=False)
class SetMeshSizeProperty(_MeshSizeCommand):
    definition: MeshSizeProperty
    label: str = 'set mesh size property'

    def apply(self,project):
        if not isinstance(self.definition,MeshSizeProperty):raise TypeError('Expected MeshSizeProperty')
        if any(value.id!=self.definition.id and value.name.casefold()==self.definition.name.casefold() for value in project.mesh_size_properties.values()):raise ValueError('A mesh size with this name already exists')
        active=any(isinstance(item,NamedRefinement) and item.property_id==self.definition.id for item in project.refinements)
        if active:_check_global(project,self.definition.size)
        project.mesh_size_properties[self.definition.id]=self.definition
        project.refinements[:]=[replace(item,**{key:getattr(self.definition,key) for key in ('size','radius','growth','name')}) if isinstance(item,NamedRefinement) and item.property_id==self.definition.id else item for item in project.refinements]
        return self.definition


@dataclass(eq=False)
class AssignMeshSize(_MeshSizeCommand):
    property_id: str
    references: tuple
    label: str = 'apply named mesh size to selection'

    def apply(self,project):
        if project.mesh_only:raise ValueError('Select geometry plates or lines; imported mesh entities are not supported')
        definition=project.mesh_size_properties.get(self.property_id)
        if definition is None:raise ValueError('This named mesh size is no longer available')
        refs=tuple(dict.fromkeys(self.references))
        if not refs:raise ValueError('Select geometry plates or lines first')
        for ref in refs:
            if not isinstance(ref,EntityRef) or ref.kind not in {'edge','face'}:raise ValueError('Named mesh sizes accept only geometry plates and lines')
            project._require_entity(ref)
        _check_global(project,definition.size)
        selected=set(refs);kept=[]
        for item in project.refinements:
            if not isinstance(item,NamedRefinement):kept.append(item);continue
            targets=tuple(project.resolve_geometry_attachment(item.ref))
            if not selected.intersection(targets):kept.append(item)
            else:kept.extend(replace(item,ref=target) for target in targets if target not in selected)
        project.refinements[:]=kept
        made=[]
        for ref in refs:
            item=NamedRefinement(size=definition.size,radius=definition.radius,growth=definition.growth,name=definition.name,ref=ref,property_id=definition.id)
            made.append(project.add_refinement(item))
        project.adopt_geometry_attachment_regions()
        return tuple(made)


@dataclass(eq=False)
class DeleteMeshSizeProperty(_MeshSizeCommand):
    property_id: str
    label: str = 'delete mesh size property'

    def apply(self,project):
        if any(isinstance(item,NamedRefinement) and item.property_id==self.property_id for item in project.refinements):raise ValueError('This mesh size is assigned; remove or reassign its targets before deleting it')
        try:return project.mesh_size_properties.pop(self.property_id)
        except KeyError:raise ValueError('This mesh size no longer exists') from None
