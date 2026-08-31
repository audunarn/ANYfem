"""Deterministic structural connection preparation on a detached geometry.

ANYgeometry owns intersection intent and topology mutation; ANYmesher consumes
only declared Sheets, Members, Attachments and Junctions.  This module is the
workflow bridge.  It operates exclusively on an immutable mesh-job closure,
uses the public query -> plan -> apply contract, and returns provenance that
ANYfem persists with the mesh record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from anygeometry import (
    ConnectionIntent,
    IntersectionKind,
    apply_imprint,
    plan_imprint,
    query_intersection,
)
from anygeometry.closure import ModelClosure
from anygeometry.entities import EntityRef
from anygeometry.errors import GeometryError


__all__ = [
    "StructuralPreparationError",
    "StructuralPreparationReport",
    "prepare_structural_connectivity",
    "remap_mesh_to_source",
    "source_work_mapping",
]


class StructuralPreparationError(ValueError):
    """A candidate connection could not be classified or prepared safely."""


@dataclass(frozen=True)
class PreparedConnection:
    first: str
    second: str
    intersection: str
    operation: str
    reused: bool
    relations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "first": self.first,
            "second": self.second,
            "intersection": self.intersection,
            "operation": self.operation,
            "reused": bool(self.reused),
            "relations": list(self.relations),
        }


@dataclass
class StructuralPreparationReport:
    source_model_id: str
    source_revision: int
    working_model_id: str
    working_revision: int = 0
    connections: list[PreparedConnection] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    source_to_working: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def created_count(self) -> int:
        return sum(not item.reused for item in self.connections)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "anyfem.structural-preparation",
            "version": 1,
            "source_model_id": self.source_model_id,
            "source_revision": int(self.source_revision),
            "working_model_id": self.working_model_id,
            "working_revision": int(self.working_revision),
            "created_count": self.created_count,
            "connections": [item.to_dict() for item in self.connections],
            "diagnostics": list(self.diagnostics),
            "source_to_working": {
                key: list(values)
                for key, values in sorted(self.source_to_working.items())
            },
        }


def _label(handle) -> str:
    return f"{handle.kind}:{handle.id}"


def _check(
    cancellation_check: Callable[[str], None] | None, phase: str
) -> None:
    if cancellation_check is not None:
        cancellation_check(phase)


def _face_sheet_map(geometry) -> dict[int, int]:
    return {
        geometry.face_uses[use_id].face_id: sheet.id
        for sheet in geometry.sheets.values()
        for use_id in sheet.face_use_ids
    }


def _candidate_pairs(
    geometry,
    *,
    member_ids: Iterable[int] | None = None,
) -> tuple[tuple[Any, Any], ...]:
    """Spatially bounded structural parent candidates in stable order."""

    face_sheets = _face_sheet_map(geometry)
    face_edges = {
        face_id: {
            item.edge
            for loop in (face.loop,) + face.holes
            for item in loop
        }
        for face_id, face in geometry.faces.items()
    }
    sheet_edges: dict[int, set[int]] = {}
    for face_id, sheet_id in face_sheets.items():
        sheet_edges.setdefault(sheet_id, set()).update(face_edges[face_id])
    # Exact shared-edge topology already forms one conformal shell component,
    # even when independently authored faces deliberately retain separate
    # Sheet owners.  Do not send non-neighbouring faces from that same
    # component through a broad curved-surface AABB query: a conservative
    # Coons bound can overlap the opposite side of a closed cylinder.  Any
    # self-intersection of one conformal component belongs to geometry
    # validation, not to creation of redundant structural attachments.
    component_parent = {face_id: face_id for face_id in face_edges}

    def find(face_id: int) -> int:
        root = face_id
        while component_parent[root] != root:
            root = component_parent[root]
        while component_parent[face_id] != face_id:
            following = component_parent[face_id]
            component_parent[face_id] = root
            face_id = following
        return root

    edge_faces: dict[int, list[int]] = {}
    for face_id, edges in face_edges.items():
        for edge_id in edges:
            edge_faces.setdefault(edge_id, []).append(face_id)
    for connected in edge_faces.values():
        if len(connected) < 2:
            continue
        first_root = find(connected[0])
        for face_id in connected[1:]:
            second_root = find(face_id)
            if first_root != second_root:
                component_parent[second_root] = first_root

    face_pairs: set[tuple[int, int]] = set()
    for face_id in sorted(geometry.faces):
        bounds = geometry.entity_bounds_many((('face', face_id),))[0]
        if bounds is None:
            continue
        for kind, other in geometry.spatial_candidates(
            bounds[:3], bounds[3:], kinds=("face",)
        ):
            if other <= face_id:
                continue
            first_sheet = face_sheets.get(face_id)
            second_sheet = face_sheets.get(other)
            if first_sheet is not None and first_sheet == second_sheet:
                continue
            if find(face_id) == find(other):
                continue
            # Reusing the exact edge definition is already explicit,
            # conformal topology.  It must not be sent back through the
            # geometric intersection classifier (which would at best be
            # redundant and, for a generic Coons parameterisation, may be
            # unavailable even though the topology is exact).
            if face_edges[face_id] & face_edges[other]:
                continue
            face_pairs.add((face_id, other))

    active_members = (
        None if member_ids is None else {int(identifier) for identifier in member_ids}
    )
    edge_members = {
        use.edge_id: use.member_id
        for use in geometry.member_edge_uses.values()
        if active_members is None or use.member_id in active_members
    }
    member_vertices: dict[int, set[int]] = {}
    for edge_id, member_id in edge_members.items():
        edge = geometry.edges[edge_id]
        member_vertices.setdefault(member_id, set()).update(
            (edge.start, edge.end)
        )
    member_pairs: set[tuple[int, int]] = set()
    member_sheet_pairs: set[tuple[int, int]] = set()
    for edge_id, member_id in sorted(edge_members.items()):
        bounds = geometry.entity_bounds_many((('edge', edge_id),))[0]
        if bounds is None:
            continue
        for kind, other in geometry.spatial_candidates(
            bounds[:3], bounds[3:], kinds=("edge", "face")
        ):
            if kind == "edge":
                other_member = edge_members.get(other)
                if other_member is not None and other_member != member_id:
                    # Members that already use the same persistent topology
                    # vertex are conformal.  Their beam meshes reuse that
                    # vertex directly; sending the pair through the imprint
                    # planner would create a redundant junction and, for a
                    # segmented member axis, can produce an ambiguous
                    # multi-component intersection plan.
                    if member_vertices.get(member_id, set()) & member_vertices.get(
                        other_member, set()
                    ):
                        continue
                    member_pairs.add(tuple(sorted((member_id, other_member))))
            else:
                sheet_id = face_sheets.get(other)
                if sheet_id is not None:
                    # A member defined on a face boundary edge already shares
                    # the shell edge registry and therefore the shell nodes.
                    # ANYgeometry's strict audit treats this exact shared edge
                    # as the connectivity witness; no attachment or MPC is
                    # needed merely to prove coincidence.
                    if edge_id in sheet_edges.get(sheet_id, ()):
                        continue
                    member_sheet_pairs.add((member_id, sheet_id))

    pairs: list[tuple[Any, Any]] = []
    pairs.extend(
        (
            geometry.handle("face", first),
            geometry.handle("face", second),
        )
        for first, second in sorted(face_pairs)
    )
    pairs.extend(
        (
            geometry.handle("member", first),
            geometry.handle("member", second),
        )
        for first, second in sorted(member_pairs)
    )
    pairs.extend(
        (
            geometry.handle("member", member),
            geometry.handle("sheet", sheet),
        )
        for member, sheet in sorted(member_sheet_pairs)
    )
    return tuple(pairs)


def prepare_structural_connectivity(
    geometry,
    *,
    source_model_id: str | None = None,
    source_revision: int | None = None,
    member_ids: Iterable[int] | None = None,
    cancellation_check: Callable[[str], None] | None = None,
) -> StructuralPreparationReport:
    """Declare every qualified plate/beam connection on ``geometry``.

    Coplanar area overlap is intentionally not assigned an implicit owner.
    Engineers must run ANYfem's previewable Fragment Overlaps geometry command
    first.  Any unclassified local candidate likewise blocks instead of being
    silently welded or ignored.  ``member_ids`` limits beam connectivity to
    active FE members; generated but unassigned construction members remain
    available in the editable model without entering this mesh job.
    """

    report = StructuralPreparationReport(
        source_model_id=str(source_model_id or geometry.model_id),
        source_revision=int(
            geometry.revision if source_revision is None else source_revision
        ),
        working_model_id=str(geometry.model_id),
    )
    max_changes = max(
        100,
        10 * (len(geometry.faces) + len(geometry.members) + len(geometry.sheets)),
    )
    changes = 0
    while True:
        _check(cancellation_check, "structural intersection query")
        restarted = False
        for first, second in _candidate_pairs(geometry, member_ids=member_ids):
            result = query_intersection(geometry, first, second)
            kind = result.kind
            if kind is IntersectionKind.DISJOINT:
                continue
            if kind is IntersectionKind.OVERLAP_REGION:
                raise StructuralPreparationError(
                    f"coplanar plate overlap between {_label(first)} and "
                    f"{_label(second)} must be resolved with Fragment Overlaps "
                    "before meshing"
                )
            if not result.classified or kind in {
                IntersectionKind.CAPABILITY_MISSING,
                IntersectionKind.UNCLASSIFIED,
                IntersectionKind.UNSUPPORTED,
            }:
                detail = "; ".join(result.diagnostics) or kind.value
                raise StructuralPreparationError(
                    f"cannot qualify structural connection {_label(first)} <-> "
                    f"{_label(second)}: {detail}"
                )
            _check(cancellation_check, "structural imprint planning")
            plan = plan_imprint(
                geometry, result, policy=ConnectionIntent.CONNECT
            )
            revision = geometry.revision
            try:
                application = apply_imprint(
                    geometry, plan, policy=ConnectionIntent.CONNECT
                )
            except GeometryError as error:
                raise StructuralPreparationError(
                    f"failed to prepare structural connection {_label(first)} "
                    f"<-> {_label(second)}: {error}"
                ) from None
            report.connections.append(
                PreparedConnection(
                    _label(first),
                    _label(second),
                    kind.value,
                    plan.operation.value,
                    bool(application.reused),
                    tuple(_label(item) for item in application.relations),
                )
            )
            if geometry.revision != revision:
                changes += 1
                if changes > max_changes:
                    raise StructuralPreparationError(
                        "structural preparation exceeded its deterministic "
                        "change bound; inspect coincident/duplicate topology"
                    )
                restarted = True
                break
        if not restarted:
            break
    report.working_revision = int(geometry.revision)
    return report


def source_work_mapping(closure: ModelClosure) -> dict[str, tuple[str, ...]]:
    """Current exact lineage from source handles to prepared work handles."""

    geometry = closure.working_model
    mapping: dict[str, tuple[str, ...]] = {}
    for source, initial in sorted(
        closure.source_to_work.items(), key=lambda item: (item[0].kind, item[0].id)
    ):
        if source.kind in ("vertex", "edge", "face"):
            current = geometry.resolve_ref(EntityRef(initial.kind, initial.id))
            if not current and initial.id in {
                "vertex": geometry.vertices,
                "edge": geometry.edges,
                "face": geometry.faces,
            }[initial.kind]:
                current = (EntityRef(initial.kind, initial.id),)
            values = tuple(f"{item.kind}:{item.id}" for item in current)
        else:
            store = getattr(geometry, f"{source.kind}s", {})
            values = (
                (f"{initial.kind}:{initial.id}",)
                if initial.id in store
                else ()
            )
        mapping[_label(source)] = values
    return mapping


def _descendant_to_source(
    closure: ModelClosure, kind: str
) -> dict[int, int]:
    geometry = closure.working_model
    made: dict[int, int] = {}
    for source, initial in closure.source_to_work.items():
        if source.kind != kind:
            continue
        resolved = geometry.resolve_ref(EntityRef(kind, initial.id))
        if not resolved and initial.id in getattr(geometry, f"{kind}s"):
            resolved = (EntityRef(kind, initial.id),)
        for item in resolved:
            previous = made.setdefault(item.id, source.id)
            if previous != source.id:
                raise StructuralPreparationError(
                    f"prepared {kind} {item.id} descends from multiple source "
                    "owners; resolve plate overlap explicitly"
                )
    return made


def _aggregate(mapping: Mapping[int, Iterable[int]], owners: Mapping[int, int]):
    result: dict[int, list[int]] = {}
    for working_id, values in mapping.items():
        source_id = owners.get(int(working_id))
        if source_id is None:
            continue
        result.setdefault(source_id, []).extend(int(item) for item in values)
    return {
        # Preserve boundary-node order.  Sorting node IDs puts both endpoint
        # nodes before the later-allocated interior nodes and turns a straight
        # 1 m path into a backtracking polyline.  Dict insertion order retains
        # each prepared child edge's parametric sequence while removing only
        # shared junction duplicates.
        key: list(dict.fromkeys(values)) for key, values in sorted(result.items())
    }


def remap_mesh_to_source(mesh, closure: ModelClosure) -> None:
    """Publish prepared mesh associations against immutable source handles."""

    face_owner = _descendant_to_source(closure, "face")
    edge_owner = _descendant_to_source(closure, "edge")
    vertex_owner = _descendant_to_source(closure, "vertex")
    mesh.elements_of_face = _aggregate(mesh.elements_of_face, face_owner)
    mesh.elements_of_edge = _aggregate(mesh.elements_of_edge, edge_owner)
    mesh.nodes_of_edge = _aggregate(mesh.nodes_of_edge, edge_owner)
    mesh.offset_nodes_of_edge = _aggregate(
        mesh.offset_nodes_of_edge, edge_owner
    )
    mesh.node_of_vertex = {
        vertex_owner[working_id]: node_id
        for working_id, node_id in mesh.node_of_vertex.items()
        if working_id in vertex_owner
    }
    # A prepared source face may be represented by several work-face grids;
    # the neutral Mesh fallback derives its nodes from aggregated elements.
    mesh.grid_of_face = {}
    mesh.thickness_of_face = {
        face_owner[working_id]: value
        for working_id, value in mesh.thickness_of_face.items()
        if working_id in face_owner
    }
    mesh.geometry_model_id = closure.source_model_id
    mesh.geometry_revision = closure.source_revision
