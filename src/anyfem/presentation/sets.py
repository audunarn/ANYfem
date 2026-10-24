"""Visible user sets over unchanged canonical region records."""
from anygeometry import EntityRef
from ..model.regions import ManualRegion,RegionDomain


def is_user_set(region):
    return not region.hidden


def is_geometry_set(region):
    """Only records admitted by the canonical geometry-set edit command."""
    return is_user_set(region) and region.domain is RegionDomain.GEOMETRY and isinstance(region.definition,ManualRegion)


def set_references(project,identifier):
    return tuple(project.regions.resolve(identifier,geometry=project.geometry,
        feature_resolver=lambda anchor:project.geometry.features.resolve(anchor,project.geometry)))


def set_geometry_references(project,identifier):
    return tuple(ref for ref in set_references(project,identifier) if isinstance(ref,EntityRef))


from dataclasses import dataclass


@dataclass(frozen=True)
class SetMembershipRequest:
    project: object
    region: object
    region_state: tuple
    geometry_revision: int
    references: tuple
    remove: bool = False

    @classmethod
    def capture(cls,project,identifier,references,*,remove=False):
        region=project.regions[identifier]
        refs=tuple(dict.fromkeys(ref for ref in references if isinstance(ref,EntityRef)))
        return cls(project,region,(region.name,region.definition,region.entity_kind),project.geometry.revision,refs,remove)

    @property
    def explanation(self):
        if not is_geometry_set(self.region):return 'Membership follows this Boolean, query or mesh definition; only manual geometry sets can be changed here.'
        if not self.references:return 'Select geometry in the canvas or worktree first.'
        return 'Remove selected members only.' if self.remove else 'Add all selected geometry without duplicates.'

    def command(self,current_project,*,read_only=False):
        if read_only or self.project.read_only_reason:raise PermissionError('This project is read-only')
        if current_project is not self.project or current_project.geometry.revision!=self.geometry_revision:raise ValueError('The project or geometry changed; open the set menu again.')
        current=current_project.regions[self.region.id]
        if current is not self.region or (current.name,current.definition,current.entity_kind)!=self.region_state:raise ValueError('The set changed; open its menu again.')
        if not is_geometry_set(current):raise ValueError(self.explanation)
        if not self.references:raise ValueError(self.explanation)
        selected=tuple(current_project.geometry.entity_ref(ref.kind,ref.id) for ref in self.references)
        existing=set_geometry_references(current_project,current.id)
        selected_set=set(selected)
        result=tuple(ref for ref in existing if ref not in selected_set) if self.remove else tuple(dict.fromkeys((*existing,*selected)))
        if set(result)==set(existing):return None
        from ..commands import EditGeometrySet
        return EditGeometrySet(current.id,references=result,label='remove from set' if self.remove else 'add to set')
