"""Stage the advance of mesh-bound references from a parent mesh to a child mesh.

References that name mesh integer IDs (mesh-domain region anchors, direct mesh
references on attribute records, wind turbine ``hub_node_id`` /
``attachment_element_id``) keep their meaning across a regional remesh only for
entities with the *same retained identity*: listed in the producer's retained
receipt, same family, same connectivity, same local face orientation and the
same coordinates in both meshes.  Nothing is ever matched by proximity.

``stage_mesh_references`` is pure: it reads the project and returns an immutable
:class:`StagedMeshReferences`.  A non-empty ``diagnostics`` tuple blocks
publication.  ``apply`` / ``undo`` swap whole record objects (originals are
never mutated), so undo restores the exact previous objects.

Explicit ``repairs`` map an old reference to a new one.  Keys / values may be
``anyfem.selection.MeshEntityRef``, ``model.regions.MeshEntityRef`` /
``ElementFaceRef`` (key mesh_id == parent, value mesh_id == child), or
``("wind_turbine", turbine_id, "hub_node_id" | "attachment_element_id") -> int``.
A repair must keep the target kind and name an existing child entity/face.
Historical results are not consulted.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Mapping

import numpy as np

from .model.regions import (
    BooleanRegion,
    ElementFaceRef,
    ManualRegion,
    MeshEntityRef as RegionMeshRef,
    Region,
    RegionDomain,
    RegionError,
)
from .selection import MeshEntityRef as SelMeshRef

__all__ = ["StagedMeshReferences", "stage_mesh_references"]

_FAMILIES = ("quads", "tris", "beams", "couplings")
_CASE_LISTS = ("point_loads", "pressures", "line_loads", "surface_tractions")
_TURBINE_FIELDS = ("hub_node_id", "attachment_element_id")


# ---------------------------------------------------------------- staged object
@dataclass(frozen=True)
class _Op:
    kind: str                 # "region" | "item" | "turbine"
    path: tuple
    index: int
    old: Any
    new: Any

    def get(self, project):
        if self.kind == "region":
            return project.regions[self.path[0]]
        if self.kind == "turbine":
            return project.wind_turbine_loads[self.path[0]]
        return _container(project, self.path)[self.index]

    def put(self, project, value) -> None:
        if self.kind == "region":
            project.regions.replace(value)
        elif self.kind == "turbine":
            project.wind_turbine_loads[self.path[0]] = value
        else:
            _container(project, self.path)[self.index] = value

    def present(self, project):
        try:
            return self.get(project)
        except (KeyError, IndexError, RegionError, AttributeError):
            return _MISSING


_MISSING = object()


def _container(project, path):
    if path[0] == "case":
        return getattr(project.load_cases[path[1]], path[2])
    return getattr(project, path[0])


@dataclass(frozen=True)
class StagedMeshReferences:
    diagnostics: tuple[str, ...]
    selection_after: tuple
    parent_id: str | None
    child_id: str
    _ops: tuple = ()

    @property
    def ok(self) -> bool:
        return not self.diagnostics

    @property
    def changed(self) -> bool:
        return bool(self._ops)

    def apply(self, project) -> None:
        if self.diagnostics:
            raise ValueError("mesh references cannot be advanced: " + "; ".join(self.diagnostics))
        for op in self._ops:
            if op.present(project) is not op.old:
                raise ValueError("project references changed since they were staged; stage again")
        done: list[_Op] = []
        try:
            for op in self._ops:
                op.put(project, op.new)
                done.append(op)
        except BaseException:
            for op in reversed(done):
                op.put(project, op.old)
            raise

    def undo(self, project) -> None:
        for op in self._ops:
            if op.present(project) is not op.new:
                raise ValueError("project references changed after the mesh update; cannot undo")
        done: list[_Op] = []
        try:
            for op in reversed(self._ops):
                op.put(project, op.old)
                done.append(op)
        except BaseException:
            for op in reversed(done):
                op.put(project, op.new)
            raise


# ---------------------------------------------------------------- mesh helpers
def _family(mesh, element_id):
    for name in _FAMILIES:
        if element_id in getattr(mesh, name, {}):
            return name
    return None


def _connectivity(mesh, element_id):
    name = _family(mesh, element_id)
    if name is None:
        return None
    raw = getattr(mesh, name)[element_id]
    if name == "couplings":
        if hasattr(raw, "beam_node"):
            return (int(raw.beam_node), tuple(int(n) for n in raw.plate_nodes),
                    tuple(float(w) for w in raw.weights),
                    tuple(float(e) for e in raw.eccentricity))
        return tuple(int(n) for n in raw)
    return tuple(int(n) for n in raw)


def _nodes(mesh, element_id):
    name = _family(mesh, element_id)
    conn = _connectivity(mesh, element_id)
    if name == "couplings":
        if conn and isinstance(conn[0], int) and len(conn) == 4 and isinstance(conn[1], tuple):
            return (conn[0],) + conn[1]
        return tuple(conn)
    return tuple(conn)


def _corners(mesh, element_id):
    name = _family(mesh, element_id)
    conn = _connectivity(mesh, element_id)
    if name == "quads":
        return conn[:4]
    if name == "tris":
        return conn[:3]
    return None


def _same_coords(old, new, node):
    return np.array_equal(np.asarray(old.nodes[node], dtype=float), np.asarray(new.nodes[node], dtype=float))


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- normalisation
def _to_sel(ref):
    """Region anchor / selection ref -> selection ref (mesh_id dropped), else None."""
    if isinstance(ref, SelMeshRef):
        return SelMeshRef(ref.kind, ref.id)
    if isinstance(ref, RegionMeshRef):
        return SelMeshRef(ref.kind, int(ref.id))
    if isinstance(ref, ElementFaceRef):
        return SelMeshRef("element_face", (int(ref.element_id), int(ref.local_face)))
    return None


def _mesh_id_of(ref):
    return getattr(ref, "mesh_id", None)


def _to_anchor(sel, mesh_id):
    if sel.kind == "element_face":
        return ElementFaceRef(mesh_id, sel.element_id, sel.local_face)
    return RegionMeshRef(mesh_id, sel.kind, int(sel.id))


# ---------------------------------------------------------------- the stager
class _Stager:
    def __init__(self, project, parent_id, child_id, old, new, nodes, elements, repairs):
        self.project = project
        self.parent_id, self.child_id = parent_id, child_id
        self.old, self.new = old, new
        self.nodes, self.elements = frozenset(nodes), frozenset(elements)
        self.first = parent_id is None
        self.problems: list[str] = []
        self.ops: list[_Op] = []
        self.sel_repairs: dict = {}
        self.turbine_repairs: dict = {}
        self.used: set = set()
        self._parse_repairs(repairs or {})

    # -- repairs
    def _parse_repairs(self, repairs):
        for key, value in dict(repairs).items():
            if isinstance(key, tuple) and len(key) == 3 and key[0] == "wind_turbine":
                if key[2] not in _TURBINE_FIELDS or _as_int(value) is None or isinstance(value, bool):
                    self.problems.append(f"invalid wind turbine repair {key!r} -> {value!r}")
                else:
                    self.turbine_repairs[(str(key[1]), key[2])] = int(value)
                continue
            skey, svalue = _to_sel(key), _to_sel(value)
            if skey is None or svalue is None:
                self.problems.append(f"unsupported repair {key!r} -> {value!r}")
                continue
            if _mesh_id_of(key) not in (None, self.parent_id):
                self.problems.append(f"repair key {key!r} is not a reference to parent mesh {self.parent_id}")
                continue
            if _mesh_id_of(value) not in (None, self.child_id):
                self.problems.append(f"repair target {value!r} is not on child mesh {self.child_id}")
                continue
            if skey.kind != svalue.kind:
                self.problems.append(
                    f"repair of {skey.kind} {skey.id} must stay a {skey.kind}, not a {svalue.kind}")
                continue
            self.sel_repairs[skey] = svalue

    # -- validation of a child target
    def _target_problem(self, sel):
        new = self.new
        if sel.kind == "node":
            return None if sel.id in new.nodes else f"node {sel.id} does not exist in the new mesh"
        eid = sel.element_id
        if _family(new, eid) is None:
            return f"element {eid} does not exist in the new mesh"
        if sel.kind == "element_face":
            corners = _corners(new, eid)
            if corners is None:
                return f"element {eid} has no shell faces in the new mesh"
            if sel.local_face != 0:
                return f"local face {sel.local_face} is not a face published by shell {eid}; select its surface (0)"
        return None

    def _retained_problem(self, sel):
        """None when ``sel`` keeps the same identity in the child mesh."""
        problem = self._target_problem(sel)
        if problem:
            return problem
        if self.first:
            return None
        old, new = self.old, self.new
        if old is None:
            return "the parent mesh is unavailable, so retained identity cannot be proven"
        if sel.kind == "node":
            n = int(sel.id)
            if n not in self.nodes or n not in old.nodes:
                return f"node {n} was retired by the remesh"
            if not _same_coords(old, new, n):
                return f"node {n} moved in the remesh"
            return None
        e = sel.element_id
        if e not in self.elements or _family(old, e) is None:
            return f"element {e} was retired by the remesh"
        if _family(old, e) != _family(new, e):
            return f"element {e} changed family"
        if _connectivity(old, e) != _connectivity(new, e):
            return f"element {e} changed connectivity"
        for n in _nodes(new, e):
            if n not in self.nodes or n not in old.nodes or not _same_coords(old, new, n):
                return f"element {e} has a retired or moved node {n}"
        # For shell surface 0, the ordered corners, midsides and their positions
        # above determine the surface and its normal on both artifacts.
        return None

    def advance(self, sel):
        """-> (new_sel | None, problem | None).  Repairs win and are validated."""
        if sel in self.sel_repairs:
            self.used.add(sel)
            target = self.sel_repairs[sel]
            problem = self._target_problem(target)
            return (None, f"repair target {target.kind} {target.id}: {problem}") if problem else (target, None)
        problem = self._retained_problem(sel)
        return (None, problem) if problem else (sel, None)

    # -- records
    def run(self):
        project = self.project
        referenced = self._referenced_regions()
        self._geometry_scopes(referenced)
        self._regions(referenced)
        self._direct_refs()
        self._turbines()
        self._wet_mappings()

    def _geometry_scopes(self, referenced):
        """Ask the geometry owner to resolve current descendants for live attributes."""
        from anygeometry import EntityRef
        from anygeometry.errors import GeometryError
        from .model.feature_bindings import _resolve_attachment_scope

        project = self.project
        if project.mesh_only:
            return
        for rid in referenced:
            if rid not in project.regions:
                self.problems.append(f"referenced region {rid} is missing")
                continue
            region = project.regions[rid]
            if region.domain is RegionDomain.MESH:
                continue
            try:
                targets, inactive = _resolve_attachment_scope(project, rid)
                if not targets and not inactive:
                    self.problems.append(f"geometry region {region.name!r} has no surviving owner")
            except (GeometryError, RegionError, KeyError) as error:
                self.problems.append(f"geometry region {region.name!r} requires re-selection: {error}")
        for _, _, record, label in self._scoped_records():
            ref = getattr(record, "ref", None)
            if (not isinstance(ref, EntityRef) or getattr(record, "region", None) is not None
                    or ref in project.geometry_attachment_regions):
                continue
            try:
                if not project.geometry.resolve_ref(ref):
                    self.problems.append(f"{label} has no surviving geometry owner for {ref}")
            except (GeometryError, KeyError) as error:
                self.problems.append(f"{label} requires re-selection of {ref}: {error}")

    def finish(self):
        """Report repairs that matched nothing (typos must not pass silently)."""
        for key in set(self.sel_repairs) - self.used:
            self.problems.append(f"repair of {key.kind} {key.id} matches no reference or selection")
        for key in set(self.turbine_repairs) - self.used:
            self.problems.append(f"repair of wind turbine {key[0]} {key[1]} matches no turbine")

    def _scoped_records(self):
        project = self.project
        for attr in ("supports", "masses", "wet_surfaces", "connections"):
            for i, record in enumerate(getattr(project, attr, ())):
                yield (attr,), i, record, f"{attr[:-1].replace('_', ' ')} {getattr(record, 'name', '') or getattr(record, 'id', '')}"
        for name, case in project.load_cases.items():
            for attr in _CASE_LISTS:
                for i, record in enumerate(getattr(case, attr, ())):
                    yield ("case", name, attr), i, record, f"load case {name!r} {attr[:-1].replace('_', ' ')} {getattr(record, 'id', '')}"

    def _referenced_regions(self):
        ids = set()
        for _p, _i, record, _label in self._scoped_records():
            region = getattr(record, "region", None)
            if region is not None:
                ids.add(region.id)
        for request in getattr(self.project, "output_requests", {}).values():
            ids.add(request.region.id)
        for region in getattr(self.project, "geometry_attachment_regions", {}).values():
            ids.add(region.id)
        frontier = set(ids)
        while frontier:
            nxt = set()
            for rid in frontier:
                if rid in self.project.regions:
                    definition = self.project.regions[rid].definition
                    if isinstance(definition, BooleanRegion):
                        nxt.update(set(definition.region_ids) - ids)
            ids |= nxt
            frontier = nxt
        return ids

    def _regions(self, referenced):
        regions = self.project.regions
        memo: dict[str, Any] = {}

        def build(region):
            """-> (new Region | None, [problems]); None with no problems = leave."""
            if region.id in memo:
                return memo[region.id]
            memo[region.id] = (None, ["cyclic region"])
            problems: list[str] = []
            label = f"region {region.name!r} ({region.id})"
            definition = region.definition
            if isinstance(definition, ManualRegion):
                anchors = []
                for anchor in definition.anchors:
                    sel = _to_sel(anchor)
                    if sel is None:
                        anchors.append(anchor)
                        continue
                    if _mesh_id_of(anchor) != self.parent_id:
                        problems.append(f"{label} anchor {anchor!r} belongs to another mesh")
                        continue
                    target, problem = self.advance(sel)
                    if problem:
                        problems.append(f"{label}: {sel.kind} {sel.id} - {problem}; repick it or supply an explicit repair")
                    else:
                        anchors.append(_to_anchor(target, self.child_id))
                result = None if problems else replace(region, definition=ManualRegion(anchors), mesh_id=self.child_id)
            elif isinstance(definition, BooleanRegion):
                for child in definition.region_ids:
                    if child not in regions:
                        problems.append(f"{label} references missing region {child}")
                        continue
                    sub = regions[child]
                    if sub.domain is not RegionDomain.MESH or sub.mesh_id != self.parent_id:
                        problems.append(f"{label} combines region {child} that is not on parent mesh {self.parent_id}")
                        continue
                    built, sub_problems = build(sub)
                    problems.extend(sub_problems)
                result = None if problems else replace(region, mesh_id=self.child_id)
            else:
                problems.append(f"{label} is a query region over mesh IDs and cannot be advanced; recreate it on the new mesh")
                result = None
            memo[region.id] = (result, problems)
            return memo[region.id]

        for region in list(regions):
            if region.domain is not RegionDomain.MESH:
                continue
            used = region.id in referenced
            if region.mesh_id == self.child_id:
                self.problems.append(
                    f"region {region.name!r} ({region.id}) already uses mesh ID {self.child_id}; mesh IDs are never recycled")
                continue
            if region.mesh_id != self.parent_id:
                if used:
                    self.problems.append(
                        f"region {region.name!r} ({region.id}) is bound to foreign mesh {region.mesh_id}, "
                        f"not parent {self.parent_id}; rebuild it on the current mesh")
                continue
            result, problems = build(region)
            if problems:
                if used:
                    self.problems.extend(dict.fromkeys(problems))
                continue
            self.ops.append(_Op("region", (region.id,), 0, region, result))

    def _direct_refs(self):
        for path, index, record, label in self._scoped_records():
            ref = getattr(record, "ref", None)
            if not isinstance(ref, SelMeshRef) or getattr(record, "region", None) is not None:
                continue
            if ref.mesh_id is not None and ref.mesh_id != self.parent_id:
                self.problems.append(f"{label} belongs to foreign mesh {ref.mesh_id}")
                continue
            target, problem = self.advance(_to_sel(ref))
            if problem:
                self.problems.append(f"{label}: {ref.kind} {ref.id} - {problem}; repick it or supply an explicit repair")
            else:
                scoped = SelMeshRef(target.kind, target.id, self.child_id)
                self.ops.append(_Op("item", path, index, record, replace(record, ref=scoped)))

    def _turbines(self):
        for tid, turbine in getattr(self.project, "wind_turbine_loads", {}).items():
            label = f"wind turbine {turbine.name!r} ({tid})"
            values = {}
            bad = False
            for name in _TURBINE_FIELDS:
                current = getattr(turbine, name)
                if current is None:
                    values[name] = None
                    continue
                key = (str(turbine.id), name)
                kind = "node" if name == "hub_node_id" else "element"
                if key in self.turbine_repairs:
                    self.used.add(key)
                    target = SelMeshRef(kind, self.turbine_repairs[key])
                    problem = self._target_problem(target)
                    new_value = None if problem else int(target.id)
                    if problem:
                        problem = f"repair target: {problem}"
                else:
                    sel = SelMeshRef(kind, int(current))
                    problem = self._retained_problem(sel)
                    new_value = None if problem else int(current)
                if problem:
                    bad = True
                    what = "hub node" if name == "hub_node_id" else "attachment element"
                    self.problems.append(
                        f"{label}: {what} {current} - {problem}; pick a new {what} (explicit repair)")
                values[name] = new_value
            if bad:
                continue
            hub, element = values["hub_node_id"], values["attachment_element_id"]
            if element is not None and hub not in _nodes(self.new, element):
                self.problems.append(f"{label}: hub node {hub} does not belong to attachment element {element}")
                continue
            if hub != turbine.hub_node_id or element != turbine.attachment_element_id:
                self.ops.append(_Op("turbine", (tid,), 0, turbine,
                                    replace(turbine, hub_node_id=hub, attachment_element_id=element)))

    def _wet_mappings(self):
        for name, case in self.project.load_cases.items():
            for load in getattr(case, "wet_pressures", ()):
                if (load.mapping is None or load.mapping_key != "element"
                        or load.mesh_content_sha256 is not None):
                    continue
                for eid in load.mapping:
                    problem = self._retained_problem(SelMeshRef("element", int(eid)))
                    if problem:
                        self.problems.append(
                            f"load case {name!r} wet pressure {load.name!r}: element {eid} - {problem}")


def _binding_problems(project, parent_id, child_id, old, new):
    problems = []
    if new is None:
        return ["the new mesh is missing"]
    if child_id is None or not str(child_id):
        problems.append("the new mesh has no producing mesh ID")
    if parent_id is not None and parent_id == child_id:
        problems.append(f"new mesh ID {child_id} equals the parent ID; mesh IDs are never recycled")
    existing = getattr(project, "mesh_records", {}).get(child_id)
    if existing is not None and (getattr(existing, "status", None) != "running" or existing.mesh_hash):
        problems.append(f"mesh ID {child_id} is already published; mesh IDs are never recycled")
    active = getattr(getattr(project, "mesh_numbering", None), "active_mesh_id", None)
    if active is not None and parent_id != active:
        problems.append(f"parent mesh {parent_id} is not the active mesh {active}; the remesh is stale")
    for label, mesh, expected in (("parent", old, parent_id), ("new", new, child_id)):
        tag = getattr(mesh, "_anyfem_mesh_record_id", None) if mesh is not None else None
        if tag is not None and expected is not None and tag != expected:
            problems.append(f"{label} mesh is tagged {tag} but was bound as {expected}; stale mesh binding")
    return problems


def stage_mesh_references(project, *, parent_id, child_id, old_mesh, new_mesh,
                          retained_node_ids, retained_element_ids, selection=(),
                          repairs=None) -> StagedMeshReferences:
    """Stage advancing every mesh-bound reference from ``parent_id`` to ``child_id``."""

    problems = _binding_problems(project, parent_id, child_id, old_mesh, new_mesh)
    if new_mesh is None:
        return StagedMeshReferences(tuple(problems), tuple(selection), parent_id, child_id)
    retained_nodes = frozenset(int(n) for n in retained_node_ids)
    retained_elements = frozenset(int(e) for e in retained_element_ids)
    if old_mesh is not None:
        missing_n = sorted(n for n in retained_nodes if n not in old_mesh.nodes or n not in new_mesh.nodes)
        missing_e = sorted(e for e in retained_elements
                           if _family(old_mesh, e) is None or _family(new_mesh, e) is None)
        if missing_n:
            problems.append(f"retained receipt names nodes absent from a mesh: {missing_n[:5]}")
        if missing_e:
            problems.append(f"retained receipt names elements absent from a mesh: {missing_e[:5]}")
    stager = _Stager(project, parent_id, child_id, old_mesh, new_mesh,
                     retained_nodes, retained_elements, repairs)
    stager.run()

    after = []
    for item in selection:
        if isinstance(item, SelMeshRef):
            if item.mesh_id is not None and item.mesh_id != parent_id:
                problems.append(f"selection {item.kind} {item.id} belongs to foreign mesh {item.mesh_id}; clear or reselect it")
                continue
            target, problem = stager.advance(SelMeshRef(item.kind, item.id))
            if problem is None:
                after.append(SelMeshRef(target.kind, target.id, child_id))
            elif item.mesh_id is not None:
                problems.append(f"selection {item.kind} {item.id}: {problem}; clear or reselect it")
        else:
            after.append(item)           # geometry refs are not mesh-bound
    stager.finish()
    problems.extend(stager.problems)
    return StagedMeshReferences(
        tuple(dict.fromkeys(problems)),
        tuple(dict.fromkeys(after)),
        parent_id,
        child_id,
        tuple(stager.ops),
    )
