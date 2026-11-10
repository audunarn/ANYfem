"""Project lifetime numbers for published meshes. Generation is always provisional."""

from dataclasses import asdict, dataclass, replace
from numbers import Integral


MAX_NUMBER = (1 << 63) - 1


def _counter(value):
    if isinstance(value, bool) or not isinstance(value, Integral) or not 0 <= value <= MAX_NUMBER:
        raise ValueError("mesh numbering requires a nonnegative signed 64-bit integer")
    return int(value)


@dataclass(frozen=True)
class MeshNumbering:
    node_high_water: int = 0
    element_high_water: int = 0
    generation: int = 0
    active_mesh_id: str | None = None

    def __post_init__(self):
        for name in ("node_high_water", "element_high_water", "generation"):
            object.__setattr__(self, name, _counter(getattr(self, name)))
        if self.active_mesh_id is not None and not isinstance(self.active_mesh_id, str):
            raise ValueError("invalid active mesh identity")

    def to_dict(self):
        return asdict(self)


def element_numbers(mesh):
    """Beam, shell and coupling numbers share the project element namespace."""
    elements = set()
    for family in (mesh.quads, mesh.tris, mesh.beams, mesh.couplings):
        if not elements.isdisjoint(family):
            raise ValueError("one element number occurs in multiple mesh families")
        elements.update(family)
    return elements


def raise_high_water(state, mesh):
    return replace(state,
                   node_high_water=max(state.node_high_water, max(mesh.nodes, default=0)),
                   element_high_water=max(state.element_high_water, max(element_numbers(mesh), default=0)))


def _fresh_ids(keys, high_water, first_mesh):
    labels = sorted(_counter(key) for key in keys)
    if first_mesh and not high_water and 0 not in labels:
        return dict(zip(labels, labels))
    if len(labels) > MAX_NUMBER - high_water:
        raise OverflowError("mesh number exceeds the positive signed 64-bit range")
    return dict(zip(labels, range(high_water + 1, high_water + 1 + len(labels))))


def _numbered_boundary_registry(registry, nodes):
    from anygeometry.identity import EntityHandle
    from anymesher.boundary import GlobalEdgeBoundaryRegistry

    numbered = GlobalEdgeBoundaryRegistry(registry.view)
    for entry in registry.entries():
        node = entry.node_id
        if node is not None and node not in nodes:
            raise ValueError("boundary station references an absent mesh node")
        for owner in entry.owners or (None,):
            # The mesher records geometry handles and MemberRegistry's
            # ("member", id) tags. An opaque owner could contain mesh IDs.
            if owner is not None and not isinstance(owner, EntityHandle) and not (
                type(owner) is tuple and len(owner) == 2 and owner[0] == "member"
                and type(owner[1]) is int
            ):
                raise ValueError("cannot translate an unknown boundary station owner")
            numbered.register(entry.key.edge_id, entry.key.parameter, entry.point,
                              node_id=nodes[node] if node is not None else None, owner=owner)
    return numbered


def number_full_mesh(mesh, state, *, source_geometry=None):
    """Assign fresh permanent IDs while the generating source is available."""
    nodes = _fresh_ids(mesh.nodes, state.node_high_water, state.generation == 0)
    elements = _fresh_ids(element_numbers(mesh), state.element_high_water, state.generation == 0)
    if all(key == value for key, value in nodes.items()) and all(key == value for key, value in elements.items()):
        # Already a detached generator result. No references need rewriting.
        return mesh
    return number_mesh_with_maps(mesh, nodes, elements, source_geometry=source_geometry)


def number_mesh_with_maps(mesh, nodes, elements, *, source_geometry):
    """Translate the generator's complete label maps with their source evidence."""
    from anymesher import requires_material_source_transfer
    from anymesher.boundary import GlobalEdgeBoundaryRegistry
    try:
        from anymesher.regional_update import remap_mesh_ids
    except ModuleNotFoundError as error:
        if error.name != "anymesher.regional_update":
            raise
        raise ValueError("permanent mesh numbering requires the ANYmesh regional-update revision "
                         "pinned in the ANYfem caller workflow") from error
    registry = getattr(mesh, "boundary_registry", None)
    if registry is not None:
        if type(registry) is not GlobalEdgeBoundaryRegistry:
            raise ValueError("cannot translate an unknown live boundary registry")
        registry.view.assert_current()
        if source_geometry is None:
            source_geometry = registry.view.source
    if mesh.tris or mesh.quads:
        if mesh.geometry_model_id is not None and (
            source_geometry is None or str(mesh.geometry_model_id) != str(source_geometry.model_id)
            or mesh.geometry_revision != source_geometry.revision
        ):
            raise ValueError("permanent numbering requires the shell mesh's current source geometry")
        if source_geometry is not None and requires_material_source_transfer(source_geometry, mesh):
            from anymesher import remap_source_mesh_ids
            return remap_source_mesh_ids(mesh, nodes, elements, source_geometry=source_geometry)
        if (registry is not None and registry.view.source is not source_geometry
                and requires_material_source_transfer(registry.view.source, None)):
            raise ValueError("permanent numbering requires live material source authority to be rebound")
    numbered = remap_mesh_ids(mesh, nodes, elements)
    if registry is not None:
        numbered.boundary_registry = _numbered_boundary_registry(registry, nodes)
    return numbered


def published_state(state, mesh, record_id, retained_nodes=(), retained_elements=()):
    """A regional producer may retain numbers only with an explicit receipt."""
    for keys, retained, high in ((mesh.nodes, retained_nodes, state.node_high_water),
                                 (element_numbers(mesh), retained_elements, state.element_high_water)):
        retained = set(retained)
        if not retained.issubset(keys) or any(key > high for key in retained):
            raise ValueError("invalid retained mesh number in candidate")
        for key in keys:
            if _counter(key) == 0 or (key <= high and key not in retained):
                raise ValueError("candidate reuses a retired or unproven mesh number")
    return replace(raise_high_water(state, mesh), generation=state.generation + 1,
                   active_mesh_id=record_id)


class PublishMesh:
    """Undo selects a previously published artifact. Its numbers stay allocated."""

    label = "publish mesh"

    def __init__(self, expected, allocated, previous_id, record, references):
        self.expected = expected
        self.allocated = allocated
        self.previous_id = previous_id
        self.record = record
        self.references = references

    def do(self, project):
        if project.mesh_numbering != self.expected:
            raise ValueError("mesh publication was superseded")
        if self.references is not None:
            self.references.apply(project)
        project.mesh_records[self.record.id] = self.record
        project.mesh_numbering = self.allocated

    def undo(self, project):
        if self.references is not None:
            self.references.undo(project)
        project.mesh_numbering = replace(project.mesh_numbering,
                                         active_mesh_id=self.previous_id,
                                         generation=project.mesh_numbering.generation + 1)

    def redo(self, project):
        if self.references is not None:
            self.references.apply(project)
        project.mesh_numbering = replace(project.mesh_numbering,
                                         active_mesh_id=self.record.id,
                                         generation=project.mesh_numbering.generation + 1)
