"""Atomic application of canonical attribute commands to a captured selection."""
from dataclasses import dataclass
from anygeometry import EntityRef
from . import commands


@dataclass(frozen=True)
class LoadSelection:
    project: object
    revision: int
    refs: tuple
    imported: object = None
    mesh: object = None
    groups: tuple = ()

    @classmethod
    def capture(cls,project,refs,kinds,*,imported=None,mesh=None):
        captured=tuple(dict.fromkeys(ref for ref in refs if isinstance(ref,EntityRef) and ref.kind in kinds))
        groups=tuple(sorted(imported.groups.items())) if project.mesh_only and imported is not None else ()
        return cls(project,project.geometry.revision,captured,imported,mesh,groups)

    def validate(self,project,*,read_only=False,imported=None,mesh=None):
        if read_only:raise PermissionError('This project is read-only')
        if project is not self.project or project.geometry.revision!=self.revision:raise ValueError('The project or geometry changed; select targets again')
        if not self.refs:raise ValueError('Select compatible geometry before applying')
        if project.mesh_only:
            if (imported is None or imported is not self.imported or mesh is not self.mesh
                    or imported.mesh is not mesh or tuple(sorted(imported.groups.items()))!=self.groups):
                raise ValueError('Imported group authority changed; select current targets again')
            for ref in self.refs:
                if ref not in imported.groups.values() or not mesh.nodes_on(ref):
                    raise ValueError('Target is not a populated group in the current imported mesh')
        else:
            for ref in self.refs:project._require_entity(ref)


def _snapshot(project):
    return {
        'regions':tuple(project.regions),
        'attachments':dict(project.geometry_attachment_regions),
        'cache':dict(getattr(project,'_singleton_region_cache',{})),
        'cache_size':getattr(project,'_singleton_region_cache_size',-1),
        'supports':list(project.supports),'masses':list(project.masses),'wet_surfaces':list(project.wet_surfaces),
        'cases':dict(project.load_cases),
        'loads':{name:{kind:list(getattr(case,kind)) for kind in ('point_loads','pressures','line_loads','surface_tractions','wet_pressures','connection_loads')} for name,case in project.load_cases.items()},
    }


def _restore(project,state):
    desired={region.id:region for region in state['regions']}
    for region in tuple(project.regions):
        if region.id not in desired:project.regions.remove(region.id,cascade=True)
    for key,region in desired.items():
        if key not in project.regions:project.regions.add(region)
    project.geometry_attachment_regions.clear();project.geometry_attachment_regions.update(state['attachments'])
    project._singleton_region_cache=dict(state['cache']);project._singleton_region_cache_size=state['cache_size']
    for name in ('supports','masses','wet_surfaces'):getattr(project,name)[:]=state[name]
    project.load_cases.clear();project.load_cases.update(state['cases'])
    for name,loads in state['loads'].items():
        for kind,records in loads.items():getattr(project.load_cases[name],kind)[:]=records


@dataclass(eq=False)
class ApplyLoadAttributes(commands.Command):
    children: tuple
    label: str = 'apply loads and boundary conditions'

    def do(self,project):
        allowed=(commands.AddSupport,commands.AddPointLoad,commands.AddLineLoad,commands.AddPressure,commands.AddSurfaceTraction,commands.AddMass,commands.AddWetSurface)
        if any(not isinstance(child,allowed) for child in self.children):raise TypeError('Only load and boundary-condition commands belong in this batch')
        self._before=_snapshot(project)
        try:result=commands.CompositeCommand(self.children,self.label).do(project)
        except BaseException:
            _restore(project,self._before);raise
        self._after=_snapshot(project)
        return result

    def undo(self,project):_restore(project,self._before)
    def redo(self,project):_restore(project,self._after)


def attribute_targets(project,record):
    """Resolve durable attribute scopes without consulting the pick filter."""
    from .model.regions import MeshEntityRef as RegionMeshRef,ElementFaceRef
    from .selection import MeshEntityRef
    region=getattr(record,'region',None) or project.raw_geometry_attachment_region(record)
    if region is not None:
        refs=project.regions.resolve(region.id,geometry=project.geometry,feature_resolver=lambda anchor:project.geometry.features.resolve(anchor,project.geometry))
    elif hasattr(record,'ref'):
        refs=(record.ref,) if project.mesh_only else project.geometry.resolve_ref(record.ref)
    elif hasattr(record,'connection'):
        connection=next(item for item in project.connections if item.id==record.connection)
        refs=project.connection_targets(connection)
    elif hasattr(record,'surfaces'):
        wanted=set(record.surfaces)
        return tuple(dict.fromkeys(ref for surface in project.wet_surfaces if not wanted or surface.id in wanted for ref in attribute_targets(project,surface)))
    elif hasattr(record,'surface'):
        surface=next(item for item in project.wet_surfaces if item.id==record.surface or item.name==record.surface)
        return attribute_targets(project,surface)
    else:return ()
    return tuple(dict.fromkeys(MeshEntityRef(ref.kind,ref.id) if isinstance(ref,RegionMeshRef) else MeshEntityRef('element_face',(ref.element_id,ref.local_face)) if isinstance(ref,ElementFaceRef) else ref for ref in refs))


@dataclass(frozen=True)
class AttributeEdit:
    project: object
    record: object
    address: tuple

    @classmethod
    def capture(cls,project,record):
        address,_container,_index,current=commands._attribute_container(project,record.id)
        if current is not record:raise ValueError('This attribute changed; select its tree row again')
        return cls(project,record,address)

    def validate(self,project,*,read_only=False):
        if read_only:raise PermissionError('This project is read-only')
        if project is not self.project:raise ValueError('Project changed; select the attribute again')
        address,_container,_index,current=commands._attribute_container(project,self.record.id)
        if current is not self.record or address!=self.address:raise ValueError('This attribute changed; select its tree row again')

    def command(self,project,replacement,*,read_only=False):
        self.validate(project,read_only=read_only)
        if type(replacement) is not type(self.record) or replacement.id!=self.record.id:raise ValueError('An edit must retain attribute type and identity')
        if getattr(replacement,'region',None)!=getattr(self.record,'region',None) or getattr(replacement,'ref',None)!=getattr(self.record,'ref',None):raise ValueError('Existing attribute targets cannot be changed from this editor')
        return commands.EditAttribute(replacement)
