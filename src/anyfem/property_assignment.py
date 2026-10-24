"""Captured tree property actions over an exact geometry selection."""
from dataclasses import dataclass,replace
from anygeometry import EntityRef
from . import commands

PROPERTY_KINDS={'plate_sections':'face','beam_sections':'edge','refinements':None,'mesh_size_properties':None}

@dataclass(frozen=True)
class SelectionAssignment:
    project: object
    attribute: str
    record: object
    references: tuple
    geometry_revision: int

    @classmethod
    def capture(cls,project,attribute,key,references):
        if attribute not in PROPERTY_KINDS:raise ValueError('Unsupported selection property')
        if attribute=='refinements':record=next((r for r in project.refinements if id(r)==key),None)
        else:record=next((r for r in getattr(project,attribute).values() if r.id==key),None)
        if record is None:raise ValueError('The chosen property is no longer available')
        kind=PROPERTY_KINDS[attribute]
        allowed={'edge','face'} if attribute=='mesh_size_properties' else {kind} if kind else {'vertex','edge','face'}
        refs=tuple(dict.fromkeys(ref for ref in references if isinstance(ref,EntityRef) and ref.kind in allowed))
        return cls(project,attribute,record,refs,project.geometry.revision)

    @property
    def explanation(self):
        return {'plate_sections':'Select one or more geometry plates first.','beam_sections':'Select one or more geometry lines/beams first.','refinements':'Select geometry points, lines or plates first.','mesh_size_properties':'Select one or more geometry plates or lines first.'}[self.attribute]

    def validate(self,current_project,*,read_only=False,require_selection=True):
        if read_only:raise PermissionError('This project is read-only')
        if current_project is not self.project or current_project.geometry.revision!=self.geometry_revision:raise ValueError('The project or geometry changed; open the menu again')
        records=current_project.refinements if self.attribute=='refinements' else getattr(current_project,self.attribute).values()
        if not any(record is self.record for record in records):raise ValueError('The chosen property changed; open the menu again')
        if require_selection and not self.references:raise ValueError(self.explanation)
        for reference in self.references:current_project._require_entity(reference)

    def command(self,current_project,*,read_only=False):
        self.validate(current_project,read_only=read_only)
        if self.attribute=='mesh_size_properties':
            from .mesh_sizes import AssignMeshSize
            return AssignMeshSize(self.record.id,self.references)
        if self.attribute=='plate_sections':children=[commands.AssignPlate(ref.id,self.record.name) for ref in self.references]
        elif self.attribute=='beam_sections':children=[commands.AssignBeam(ref.id,self.record.name) for ref in self.references]
        else:children=[commands.AddRefinement(replace(self.record,ref=ref,center=None)) for ref in self.references]
        return AtomicPropertyAssignment(tuple(children),label='apply '+('mesh size' if self.attribute=='refinements' else self.attribute.replace('_',' '))+' to selection')


@dataclass(eq=False)
class AtomicPropertyAssignment(commands.Command):
    """Canonical child commands with a complete property/ownership inverse."""
    children: tuple
    label: str = 'apply property to selection'

    def do(self,project):
        from .model.feature_bindings import _attribute_snapshot,_restore_attributes
        self._before_geometry=project.geometry.design_snapshot();self._before_attributes=_attribute_snapshot(project)
        try:
            result=commands.CompositeCommand(self.children,self.label).do(project)
        except BaseException:
            project.geometry.restore_design(self._before_geometry);_restore_attributes(project,self._before_attributes);raise
        self._after_geometry=project.geometry.design_snapshot();self._after_attributes=_attribute_snapshot(project)
        return result

    def undo(self,project):
        from .model.feature_bindings import _restore_attributes
        project.geometry.restore_design(self._before_geometry);_restore_attributes(project,self._before_attributes)

    def redo(self,project):
        from .model.feature_bindings import _restore_attributes
        project.geometry.restore_design(self._after_geometry);_restore_attributes(project,self._after_attributes)
