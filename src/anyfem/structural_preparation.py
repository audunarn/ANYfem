"""Deterministic structural connection preparation on a detached geometry.

ANYgeometry owns intersection intent and topology mutation; ANYmesher consumes
only declared Sheets, Members, Attachments and Junctions.  This module is the
workflow bridge.  It operates exclusively on an immutable mesh-job closure,
uses the public query -> plan -> apply contract, and returns provenance that
ANYfem persists with the mesh record.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Callable, Iterable, Mapping

from anygeometry import (
    ConnectionIntent,
    IntersectionDimension,
    IntersectionKind,
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
    "source_edge_paths",
    "seam_policy_of",
    "require_resolved_seam_policy",
    "SEAM_POLICIES",
]

#: The owner's intersection seam policies a model may select (``None``: the owner's default, unchanged).
SEAM_POLICIES = ("baseline", "ring_split")


def seam_policy_of(settings) -> str | None:
    """The per-model seam policy stored in ``NativeMeshSettings.parameters['seam_policy']`` (None when unset).

    Anything but a known policy name is refused (fail closed); an unset model keeps every hash and key unchanged."""
    if settings is None:
        return None
    parameters = dict(settings.parameters)
    if "seam_policy" not in parameters:
        return None
    value = parameters["seam_policy"]  # an explicit null is not "unset": the only unset form is the missing key
    if type(value) is not str or value not in SEAM_POLICIES:
        raise StructuralPreparationError(f"seam_policy must be one of {SEAM_POLICIES} or unset, not {value!r}")
    return value


def require_resolved_seam_policy(seam_policy) -> None:
    """The one policy-level check: an explicit ``ring_split`` the owner resolves to anything else is a typed refusal."""
    if seam_policy != "ring_split":
        return
    from ._batch_prepare import resolved_route
    resolved = resolved_route(seam_policy)[1]
    if resolved != "ring_split":
        raise StructuralPreparationError(
            f"seam_policy 'ring_split' was requested but the owner resolves {resolved!r} in this planning context "
            "(a budget or a non-default planning context such as decision tracing forces the baseline seam policy)")


class StructuralPreparationError(ValueError):
    """A candidate connection could not be classified or prepared safely."""

    #: The workflow stage that failed: shared connection preparation, which runs before any element generation.
    phase = "preparing structural connections"

    def to_diagnostic(self):
        """Structured evidence for the mesh-job transport (the owner's pair/residual/tolerance details are kept).

        The owner's typed refusal (for example ANYgeometry's plane-chart ``to_diagnostic``) is the ``__cause__``
        of this error; the message-only wrapper used to drop it.
        """
        payload = {"phase": self.phase, "element_generation_started": False}
        cause = self.__cause__
        seen = set()
        while (cause is not None and id(cause) not in seen
               and not callable(getattr(cause, 'to_diagnostic', None))):
            seen.add(id(cause))
            cause = cause.__cause__
        provider = getattr(cause, "to_diagnostic", None)
        if callable(provider):
            try:
                details = provider()
            except Exception as error:  # diagnostics must not swallow process interrupts
                payload["cause_diagnostic_error"] = f"{type(error).__name__}: {error}"
            else:
                if isinstance(details, Mapping):
                    payload["cause"] = dict(details)
        if cause is not None:
            payload["cause_type"] = type(cause).__name__
        return payload


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
    source_edge_paths: dict[int, tuple[tuple[int, bool], ...]] = field(default_factory=dict)

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
            "source_edge_paths": {str(key): [[edge, forward] for edge, forward in path]
                                  for key, path in sorted(self.source_edge_paths.items())},
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
            # Reusing the exact edge definition is already explicit,
            # conformal topology.  It must not be sent back through the
            # geometric intersection classifier (which would at best be
            # redundant and, for a generic Coons parameterisation, may be
            # unavailable even though the topology is exact).
            if face_edges[face_id] & face_edges[other]:
                continue
            # Faces intentionally grouped into one Sheet are already one
            # structural shell.  This also prevents conservative curved-face
            # bounds (for example opposite cylinder panels) from producing
            # redundant self-connections.  Independently authored Sheets may
            # still meet again elsewhere in the connected topology: adjacent
            # edge extrusions are the common case, and their coincident free
            # edges must be imprinted rather than hidden by a broad connected-
            # component shortcut.
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
    seam_policy: str | None = None,
) -> StructuralPreparationReport:
    """Declare every qualified plate/beam connection on ``geometry``.

    ``seam_policy`` is the per-model owner seam policy (None: the owner's default); see ``seam_policy_of``.  ``ring_split``
    is an attempt per face (the owner may decline an individual ring seam); an explicit ``ring_split`` that the owner
    resolves to another policy in this process (budgets, decision tracing, non-default planning contexts) is refused.

    Coplanar area overlap is intentionally not assigned an implicit owner.
    Engineers must run ANYfem's previewable Fragment Overlaps geometry command
    first.  Any unclassified local candidate likewise blocks instead of being
    silently welded or ignored.  ``member_ids`` limits beam connectivity to
    active FE members; generated but unassigned construction members remain
    available in the editable model without entering this mesh job.
    """

    # Keep headless project imports available with published owner versions.
    # The new batch capability is required only when preparing a mesh job;
    # it must fail explicitly rather than substitute different weld semantics.
    try:
        from anygeometry import (
            IntersectionBatchPolicy, plan_intersections, apply_intersections,
        )
    except ImportError as error:
        raise StructuralPreparationError(
            "ANYgeometry lacks the batch-intersection API required for structural "
            "preparation; install the coordinated ANYgeometry owner candidate"
        ) from error

    require_resolved_seam_policy(seam_policy)
    report = StructuralPreparationReport(
        source_model_id=str(source_model_id or geometry.model_id),
        source_revision=int(
            geometry.revision if source_revision is None else source_revision
        ),
        working_model_id=str(geometry.model_id),
    )
    _check(cancellation_check, "structural intersection batch planning")
    active_members = tuple(sorted(geometry.members if member_ids is None else set(member_ids)))
    if not active_members and geometry.faces:
        # A proved edge-only contact inside one authored Sheet is already
        # connected. The control-hull proof covers every other pair as well.
        from anygeometry.surfaces import CoonsSurface, ExtrudedSurface, RuledSurface
        if (not geometry.junctions and not geometry.attachments and len(geometry.faces) > 1
                and all(type(f.surface) in (CoonsSurface, ExtrudedSurface, RuledSurface)
                        for f in geometry.faces.values())):
            sheets = _face_sheet_map(geometry)
            if len(geometry.face_uses) == len(geometry.faces) and set(sheets) == set(geometry.faces):
                try:
                    from anygeometry.tensor_interfaces import tensor_boundary_contacts
                except ModuleNotFoundError as error:
                    if error.name != 'anygeometry.tensor_interfaces':
                        raise
                else:
                    contacts = tensor_boundary_contacts(geometry)
                    if contacts and all(sheets[a] == sheets[b] for a, b in contacts):
                        report.working_revision = geometry.revision
                        report.diagnostics.append(
                            f"Certified {len(contacts)} shared tensor boundaries within their authored Sheets")
                        return report
        from anygeometry import query_trimmed_surface_charts
        from anygeometry.surfaces import CoonsSurface, Cone, RuledSurface
        if all(isinstance(face.surface, (CoonsSurface, Cone, RuledSurface))
               for face in geometry.faces.values()):
            from anygeometry.nurbs import NurbsCurve
            # Service selection only: the mesher independently proves every
            # original pair before finalizing this private raw working closure.
            raw_multiface_profile = (
                len(geometry.faces) > 1 and not geometry.sheets and not geometry.members
                and all(type(face.surface) is CoonsSurface and face.parameterization is None
                        for face in geometry.faces.values())
                and any(type(geometry.edges[use.edge].curve) is NurbsCurve
                        for face in geometry.faces.values() for use in face.loop))
            unavailable = set()
            if not raw_multiface_profile:
                for face in geometry.faces:
                    try:
                        query_trimmed_surface_charts(geometry, (geometry.handle("face", face),))
                    except GeometryError as error:
                        if (str(error) == f"face {face} has a non-planar support"
                                and isinstance(geometry.faces[face].surface, RuledSurface)):
                            from anygeometry import source_tensor_chart
                            if source_tensor_chart(geometry, face) is not None:
                                unavailable.add(face)
                                continue
                        if str(error) not in (
                                f"face {face} has curved Coons boundaries",
                                f"face {face} is not planar",
                                f"face {face} has an unsupported intersection surface"):
                            raise StructuralPreparationError(str(error)) from error
                        unavailable.add(face)
            if raw_multiface_profile or unavailable == set(geometry.faces):
                # Preserve established curved authoring/discretization. The
                # mesher performs its qualified ownership audit on a detached
                # candidate; general interior Coons joints are not inferred.
                from anymesher.preparation import prepare_structural_closure
                from anymesher.errors import MeshError
                try:
                    candidate, owner_report = prepare_structural_closure(
                        geometry, face_ids=tuple(geometry.faces),
                        # This geometry is the private mesh-job closure. Use
                        # the owner's existing temporary declaration policy;
                        # ordinary extrusions need not pollute the design
                        # model with persistent Sheet records to be meshed.
                        options={"declare_missing_owners": True},
                        cancellation_check=cancellation_check,
                        reuse_working_copy=True)
                except (MeshError, GeometryError) as error:
                    raise StructuralPreparationError(str(error)) from error
                geometry.restore_topology(candidate.topology_snapshot())
                temporary = tuple(getattr(owner_report, "temporary_sheet_ids", ()))
                if temporary:
                    report.diagnostics.append(
                        f"private mesh closure declared {len(temporary)} temporary Sheet owner(s): "
                        + ", ".join(str(item) for item in temporary[:20])
                    )
                report.working_revision = int(geometry.revision)
                return report
    owned_faces = {geometry.face_uses[use].face_id for sheet in geometry.sheets.values()
                   for use in sheet.face_use_ids}
    operands = (*[geometry.handle("sheet", sheet) for sheet in sorted(geometry.sheets)],
                *[geometry.handle("face", face) for face in sorted(set(geometry.faces) - owned_faces)],
                *[geometry.handle("member", member) for member in active_members])
    # The bounded NURBS owner accepts complete face scopes. A Sheet selector
    # otherwise dispatches this exact same geometry to the legacy bilinear
    # contact service. This selector hint grants no geometric authority: the
    # complete public batch plan qualifies exact supports, references and every
    # original face/pair. Raw topology Coons profiles acquire their exact chart
    # in that owner route, so the generic chart service cannot precede it.
    from anygeometry import Plane, CoonsSurface
    from anygeometry.nurbs import NurbsSurface, NurbsCurve
    bounded_hint = (not active_members
        and sum(isinstance(face.surface, Plane) for face in geometry.faces.values()) == 1
        and any(isinstance(face.surface, NurbsSurface)
            or isinstance(face.parameterization, NurbsSurface)
            or isinstance(face.surface, CoonsSurface) and any(
                isinstance(geometry.edges[use.edge].curve, NurbsCurve)
                for use in face.loop)
            for face in geometry.faces.values()))
    if (not active_members and not geometry.junctions and not geometry.attachments
            and len(geometry.face_uses) == len(geometry.faces)
            and set(_face_sheet_map(geometry)) == set(geometry.faces)
            and {type(face.surface) for face in geometry.faces.values()} == {Plane, NurbsSurface}):
        try:
            from anygeometry.tensor_interfaces import disjoint_plane_nurbs_faces
        except ModuleNotFoundError as error:
            if error.name != 'anygeometry.tensor_interfaces':
                raise
        else:
            if disjoint_plane_nurbs_faces(geometry):
                report.working_revision = geometry.revision
                report.diagnostics.append("Separated finite planar and NURBS source faces by their control hulls")
                return report
    faces = tuple(geometry.faces.values())
    # Necessary family hint only: the public batch owner still proves the
    # complete material relation, source binding and actual CONNECT ownership.
    same_support_hint = (not active_members and len(faces) >= 2
        and all(type(face.surface) is NurbsSurface
            and (face.parameterization is None or face.parameterization is face.surface)
            for face in faces)
        and all(face.surface == faces[0].surface for face in faces))
    if bounded_hint or same_support_hint:
        operands = tuple(geometry.handle('face', face) for face in sorted(geometry.faces))
    from . import thread_route
    from ._batch_prepare import plan_apply_phase, report_rows_phase
    from ._preparation_context import _record_preparation_plan
    try:
        # Opt-in child-process route (off by default): the child plans, this process adopts the plan through the
        # owner's verified adoption API and applies it; None means: run the ordinary in-process path below.
        state = thread_route.try_prepare(geometry, operands, cancellation_check=cancellation_check, seam_policy=seam_policy)
        if state is None:
            state = plan_apply_phase(geometry, operands, cancellation_check=cancellation_check,
                                     record_plan=_record_preparation_plan, seam_policy=seam_policy)
    except GeometryError as error:
        if "positive-area" in str(error):
            raise StructuralPreparationError(
                "coplanar plate overlap must be resolved with Fragment Overlaps before meshing: "
                + str(error)) from error
        raise StructuralPreparationError(f"cannot prepare structural intersection batch: {error}") from error
    rows = report_rows_phase(geometry, state)
    report.connections.extend(PreparedConnection(first, second, "intersection_curve",
        "batch_arrangement", rows.reused,
        tuple(dict.fromkeys(rows.pair_relations.get((first,second), rows.relations))))
        for first, second in sorted(rows.pairs))
    report.working_revision = int(geometry.revision)
    # Report construction is part of this preparation call. Preserve the
    # obligation if it fails or is interrupted after the owner apply succeeds.
    from ._preparation_context import (
        preparation_completed, require_preparation, requires_preparation,
    )
    required_before_completion = requires_preparation(geometry)
    try:
        preparation_completed(geometry)
        return report
    except BaseException:
        if required_before_completion:
            require_preparation(geometry)
        raise


def source_edge_paths(closure: ModelClosure) -> dict[int, tuple[tuple[int, bool], ...]]:
    """Consume owner-authenticated shared-boundary occurrences, never proximity.

    An original edge can remain active but orphaned after its face reuses a
    canonical boundary. Replacement lineage alone cannot describe that reuse.
    Polynomial preimages additionally provide exact source-parametric order.
    Arc provenance does not qualify parameter transport and is not used here.
    Returned paths are derived application data, not transferable owner proof.
    """
    import anygeometry
    query = getattr(anygeometry, "query_prepared_edge_subcurve_preimages", None)
    if not callable(query):
        # Older supported owners retain their replacement-lineage route.
        # Missing capability never licenses reconstructing aliases ourselves.
        return {}
    geometry = closure.working_model
    try:
        binding = query(geometry)
    except GeometryError as error:
        from ._preparation_context import requires_preparation
        if requires_preparation(geometry):
            raise StructuralPreparationError(
                'transported geometry requires fresh owner preparation before source-edge mapping') from error
        if str(error) in ("edge subcurve provenance requires a complete current preparation",
                          "edge subcurve provenance is unavailable"):
            return {}
        raise
    occurrences: dict[int, dict[tuple[Fraction, Fraction], tuple[int, bool]]] = {}
    for row in (*binding.records, *binding.alias_records):
        if row.ancestor.model_id != geometry.model_id:
            continue
        edge = row.edge_id
        if not geometry.faces_using_edge(edge) and not geometry.members_using_edge(edge):
            continue
        first, last = (Fraction(*value) for value in row.interval)
        interval = min(first, last), max(first, last)
        paths = occurrences.setdefault(row.ancestor.definition.edge_id, {})
        candidate = (edge, last > first)
        if interval in paths and candidate != paths[interval]:
            raise StructuralPreparationError(
                f"source edge {row.ancestor.definition.edge_id} has ambiguous prepared occurrences")
        paths[interval] = candidate
    result = {}
    for source, initial in closure.source_to_work.items():
        if source.kind != "edge" or initial.id not in occurrences:
            continue
        rows = sorted(occurrences[initial.id].items())
        # Partial evidence must not silently truncate a loaded design line.
        if rows[0][0][0] != 0 or rows[-1][0][1] != 1 or any(
                first[0][1] != second[0][0] for first, second in zip(rows, rows[1:])):
            raise StructuralPreparationError(f"source edge {source.id} has incomplete or overlapping prepared occurrences")
        result[source.id] = tuple(value for interval, value in rows)
    return result


def source_work_mapping(closure: ModelClosure, *, edge_paths=None) -> dict[str, tuple[str, ...]]:
    """Current exact lineage from source handles to prepared work handles."""

    geometry = closure.working_model
    mapping: dict[str, tuple[str, ...]] = {}
    edge_paths = source_edge_paths(closure) if edge_paths is None else edge_paths
    for source, initial in sorted(
        closure.source_to_work.items(), key=lambda item: (item[0].kind, item[0].id)
    ):
        if source.kind == "edge" and source.id in edge_paths:
            values = tuple(f"edge:{edge}" for edge, forward in edge_paths[source.id])
        elif source.kind in ("vertex", "edge", "face"):
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


def _descendant_to_sources(
    closure: ModelClosure, kind: str
) -> dict[int, tuple[int, ...]]:
    geometry = closure.working_model
    made: dict[int, list[int]] = {}
    for source, initial in closure.source_to_work.items():
        if source.kind != kind:
            continue
        resolved = geometry.resolve_ref(EntityRef(kind, initial.id))
        if not resolved and initial.id in getattr(geometry, f"{kind}s"):
            resolved = (EntityRef(kind, initial.id),)
        for item in resolved:
            owners = made.setdefault(item.id, [])
            if source.id not in owners:
                owners.append(source.id)
    return {
        working_id: tuple(sorted(source_ids))
        for working_id, source_ids in made.items()
    }


def _aggregate(
    mapping: Mapping[int, Iterable[int]],
    owners: Mapping[int, tuple[int, ...]],
):
    result: dict[int, list[int]] = {}
    for working_id, values in mapping.items():
        source_ids = owners.get(int(working_id), ())
        if not source_ids:
            continue
        materialized = [int(item) for item in values]
        for source_id in source_ids:
            result.setdefault(source_id, []).extend(materialized)
    return {
        # Preserve boundary-node order.  Sorting node IDs puts both endpoint
        # nodes before the later-allocated interior nodes and turns a straight
        # 1 m path into a backtracking polyline.  Dict insertion order retains
        # each prepared child edge's parametric sequence while removing only
        # shared junction duplicates.
        key: list(dict.fromkeys(values)) for key, values in sorted(result.items())
    }


_ROUTE_FACE_LISTS = ("quad_first_face_ids", "legacy_face_ids")


def _publish_route_face_ownership(mesh, face_owner: Mapping[int, tuple[int, ...]]) -> None:
    """Move the quad-first/legacy merge record onto source faces with ``elements_of_face``.

    ANYmesh's merge writes ``quad_first_face_ids``/``legacy_face_ids`` and one
    ``"<face id>"`` backend record per legacy face, all on the faces it meshed.
    Here those are prepared work faces, while ``elements_of_face`` has just been
    aggregated onto source faces; permanent numbering checks the two against
    each other.  Translate only when the source record is exact: every work face
    has an owner, no source face is split across both routes and the legacy
    records of one source face agree.  Otherwise the record is left as it was
    and permanent numbering refuses it, as before.
    """

    record = getattr(mesh, "hybrid_diagnostics", None)
    if not isinstance(record, Mapping) or not all(key in record for key in _ROUTE_FACE_LISTS):
        return
    owned: dict[str, set[int]] = {}
    for key in _ROUTE_FACE_LISTS:
        sources: set[int] = set()
        for working_id in record[key]:
            if type(working_id) is not int or not face_owner.get(working_id):
                return
            sources.update(face_owner[working_id])
        owned[key] = sources
    if owned["quad_first_face_ids"] & owned["legacy_face_ids"]:
        return
    backend: dict[str, Any] = {}
    for key, value in record.items():
        if not (isinstance(key, str) and key.isascii() and key.isdecimal()):
            continue
        sources = face_owner.get(int(key))
        if not sources:
            return
        for source_id in sources:
            if str(source_id) in backend and backend[str(source_id)] != value:
                return
            backend[str(source_id)] = value
    if {int(key) for key in backend} != owned["legacy_face_ids"]:
        return
    translated: dict[Any, Any] = {}
    for key, value in record.items():
        if key in _ROUTE_FACE_LISTS:
            translated[key] = sorted(owned[key])
        elif not (isinstance(key, str) and key.isascii() and key.isdecimal()):
            translated[key] = value
    translated.update(sorted(backend.items(), key=lambda item: int(item[0])))
    mesh.hybrid_diagnostics = translated


_PLANAR_HIGH_ORDER_ROW = {
    "geometry_family": "planar",
    "chart_kind": "PlanarQuadDomain",
    "boundary_projection": "source-edge-parameter-midpoint",
    "interior_projection": "chord-midpoint",
    "validity_status": "CERTIFIED_POSITIVE",
}


def _source_high_order_certificate(mesh, certificate, source_geometry, working_model_id):
    """Re-certify a prepared planar quadratic shell's midsides on source geometry.

    The quad-first promotion certifies Q8/T6 midsides against the prepared work
    faces and work edges.  Element validity does not depend on how elements are
    grouped into faces, so it carries over unchanged.  The geometry claims do
    not: they are measured again here on each source face and source edge.  A
    boundary midside must be the source-edge parameter midpoint of its station
    and an interior midside the chord midpoint, both within a tight tolerance.
    Returns ``None`` (no translation) whenever any claim cannot be re-established
    exactly; permanent numbering then refuses the work-face certificate.
    """

    import numpy as np
    from anymesher.errors import MeshError
    from anymesher.quad.domain import PlanarQuadDomain
    from anymesher.quad.high_order import (
        HighOrderBoundaryMidside, HighOrderGeometryReport, HighOrderMeshCertificate, ValidityStatus,
    )

    if not isinstance(certificate, Mapping) or certificate.get("status") != "CERTIFIED_POSITIVE":
        return None
    rows = certificate.get("reports")
    if (certificate.get("model_id") != str(working_model_id) or certificate.get("order") != "quadratic"
            or not isinstance(rows, (list, tuple))):
        return None
    for row in rows:
        if not isinstance(row, Mapping) or row.get("model_id") != str(working_model_id) or any(
                row.get(key) != value for key, value in _PLANAR_HIGH_ORDER_ROW.items()):
            return None
    if (certificate.get("q8_count"), certificate.get("t6_count")) != (len(mesh.quads), len(mesh.tris)):
        return None

    corners_of = {int(e): tuple(int(n) for n in body[:4]) for e, body in mesh.quads.items()}
    corners_of.update({int(e): tuple(int(n) for n in body[:3]) for e, body in mesh.tris.items()})
    midside_of: dict[tuple[int, int], int] = {}
    faces_of_edge: dict[tuple[int, int], set[int]] = {}
    for element, body in ((int(e), b) for table in (mesh.quads, mesh.tris) for e, b in table.items()):
        corners = corners_of[element]
        for index, (first, second) in enumerate(zip(corners, corners[1:] + corners[:1])):
            key = (min(first, second), max(first, second))
            midside = int(body[len(corners) + index])
            if midside_of.setdefault(key, midside) != midside:
                return None
    if len(midside_of) != certificate.get("unique_midside_count"):
        return None
    for face_id, elements in mesh.elements_of_face.items():
        for element in elements:
            corners = corners_of[int(element)]
            for first, second in zip(corners, corners[1:] + corners[:1]):
                faces_of_edge.setdefault((min(first, second), max(first, second)), set()).add(int(face_id))

    nodes = {int(k): np.asarray(v, dtype=float) for k, v in mesh.nodes.items()}
    scale = max(1.0, max((float(np.max(np.abs(p))) for p in nodes.values()), default=1.0))
    tolerance = 1.0e-10 * scale

    boundary_by_face: dict[int, list] = {}
    boundary_keys: set[tuple[int, int]] = set()
    # Coincident source edges (e.g. a beam line on a plate boundary) were one
    # work edge, so after aggregation they carry the same shell segments.  Each
    # midside is certified once, by the longest source chain that contains it;
    # a chain whose segments are all certified already is skipped as a whole
    # and a partial overlap is not an exact source record.
    chains = sorted(((-len(chain), int(edge_id), [int(node) for node in chain])
                     for edge_id, chain in mesh.nodes_of_edge.items() if chain))
    for _length, edge_id, chain in chains:
        segments = [(chain[i], chain[i + 1], chain[i + 2]) for i in range(0, len(chain) - 2, 2)]
        keys = [(min(a, b), max(a, b)) for a, _mid, b in segments]
        owned = [key in faces_of_edge for key in keys]
        if not any(owned):
            continue  # a beam-only line carries no shell midside
        if len(chain) % 2 != 1 or not all(owned):
            return None
        covered = [key in boundary_keys for key in keys]
        if all(covered):
            continue
        if any(covered):
            return None
        curve = type(source_geometry.edges[int(edge_id)].curve).__name__
        curvature = "straight" if curve == "Straight" else "analytic_curved" if curve == "Arc" else "sampled"
        for (first, middle, second), key in zip(segments, keys):
            if midside_of.get(key) != middle:
                return None
            _p0, t0, _d0 = source_geometry.closest_edge_point(int(edge_id), nodes[first])
            _p1, t1, _d1 = source_geometry.closest_edge_point(int(edge_id), nodes[second])
            lower, upper = sorted((float(t0), float(t1)))
            exact = np.asarray(source_geometry.sample_edge(int(edge_id), np.asarray([0.5 * (lower + upper)]))[0],
                               dtype=float)
            if float(np.linalg.norm(exact - nodes[middle])) > tolerance:
                return None
            _pm, parameter, residual = source_geometry.closest_edge_point(int(edge_id), nodes[middle])
            try:
                record = HighOrderBoundaryMidside(
                    canonical_edge=(int(edge_id), round(lower, 15), round(upper, 15)),
                    source_edge_id=int(edge_id), station_interval=(lower, upper),
                    parameter=float(parameter), node_id=middle, residual=float(residual),
                    curvature_class=curvature)
            except ValueError:
                return None
            boundary_keys.add(key)
            for face_id in faces_of_edge[key]:
                boundary_by_face.setdefault(face_id, []).append(record)

    reports = []
    for face_id in sorted(int(f) for f in mesh.elements_of_face):
        try:
            domain = PlanarQuadDomain.from_geometry(source_geometry, face_id)
        except (MeshError, KeyError, TypeError, ValueError):
            return None
        origin, normal = np.asarray(domain.origin, dtype=float), np.asarray(domain.normal, dtype=float)
        elements = [int(e) for e in mesh.elements_of_face[face_id]]
        edges = {(min(a, b), max(a, b)) for e in elements
                 for a, b in zip(corners_of[e], corners_of[e][1:] + corners_of[e][:1])}
        boundary = tuple(sorted(boundary_by_face.get(face_id, ()), key=lambda item: repr(item.canonical_edge)))
        residuals = [float(item.residual) for item in boundary]
        interior = sorted(edges - boundary_keys)
        for first, second in interior:
            midside = nodes[midside_of[(first, second)]]
            if float(np.linalg.norm(midside - 0.5 * (nodes[first] + nodes[second]))) > tolerance:
                return None
            residuals.append(abs(float(np.dot(midside - origin, normal))))
        # A preparation seam (e.g. a plate intersection imprinted on the work
        # model) has no source edge: its midside is interior to every face
        # meeting there.  It is measured on each of those planes above but
        # counted once, by the lowest face id, so the certificate's unique
        # midside total stays exact.
        interior_count = sum(1 for key in interior if min(faces_of_edge[key]) == face_id)
        for item in boundary:
            residuals.append(abs(float(np.dot(nodes[item.node_id] - origin, normal))))
        q8 = sum(e in mesh.quads for e in elements)
        t6 = sum(e in mesh.tris for e in elements)
        try:
            reports.append(HighOrderGeometryReport(
                model_id=str(source_geometry.model_id), revision=int(source_geometry.revision),
                face_id=face_id, chart_origin=tuple(float(v) for v in origin),
                q8_count=q8, t6_count=t6, certified_elements=q8 + t6, total_elements=q8 + t6,
                boundary_midsides=boundary,
                edge_curvature_classes=tuple(sorted({item.curvature_class for item in boundary})),
                interior_midside_count=interior_count, max_geometry_residual=max(residuals, default=0.0),
                **{key: value for key, value in _PLANAR_HIGH_ORDER_ROW.items() if key != "validity_status"},
            ))
        except ValueError:
            return None
    try:
        result = HighOrderMeshCertificate(
            status=ValidityStatus.CERTIFIED_POSITIVE, model_id=str(source_geometry.model_id),
            revision=int(source_geometry.revision), reports=tuple(reports),
            q8_count=len(mesh.quads), t6_count=len(mesh.tris), unique_midside_count=len(midside_of),
            unique_boundary_midside_count=len({item.canonical_edge for r in reports for item in r.boundary_midsides}),
            max_geometry_residual=max((r.max_geometry_residual for r in reports), default=0.0),
            order="quadratic", target_size=float(certificate.get("target_size", 0.0)),
            route=str(certificate.get("route", "quad-first")),
        )
    except ValueError:
        return None
    return result.to_dict()


def _publish_source_high_order_certificate(mesh, source_geometry, working_model_id) -> None:
    """Replace a work-face high-order certificate (flat and wrapped copies) by its source re-certification."""

    record = getattr(mesh, "hybrid_diagnostics", None)
    if source_geometry is None or not getattr(mesh, "is_quadratic", False) or not isinstance(record, Mapping):
        return
    certificate = record.get("high_order_geometry")
    nested = record.get("quad_first")
    wrapped = isinstance(nested, Mapping) and "high_order_geometry" in nested
    if certificate is None or (wrapped and nested["high_order_geometry"] != certificate):
        return
    source = _source_high_order_certificate(mesh, certificate, source_geometry, working_model_id)
    if source is None:
        return
    translated = dict(record)
    translated["high_order_geometry"] = source
    if wrapped:
        translated["quad_first"] = {**nested, "high_order_geometry": copy.deepcopy(source)}
    mesh.hybrid_diagnostics = translated


def remap_mesh_to_source(mesh, closure: ModelClosure, *, edge_paths=None,
                         source_geometry=None, cancellation_check=None, qualification=None) -> None:
    """Publish prepared mesh associations against immutable source handles."""

    # Generic closure maps do not prove curved coverage or parameter transport.
    # The owner-bound route below performs fresh source qualification instead
    # of rewriting a prepared S3 record's IDs.
    triangles, quadrilaterals = getattr(mesh, 'tris', {}), getattr(mesh, 'quads', {})
    shell_ids = set(triangles) | set(quadrilaterals)
    if set(triangles) & set(quadrilaterals):
        raise StructuralPreparationError('prepared shell element IDs overlap')
    associated = set()
    for cells in mesh.elements_of_face.values():
        for cell in cells:
            if cell not in shell_ids or cell in associated:
                raise StructuralPreparationError('prepared shell face associations are invalid or duplicated')
            associated.add(cell)
    if associated != shell_ids:
        raise StructuralPreparationError('prepared shell face associations are incomplete')
    for face_id, cells in mesh.elements_of_face.items():
        if cells and face_id not in closure.working_model.faces:
            raise StructuralPreparationError('prepared mesh references a face outside the working closure')
    # Geometry and live generation bindings choose the required route; mutable
    # face labels cannot choose whether source-cell proof is necessary.
    try:
        from anymesher import requires_material_source_transfer
    except ImportError as error:
        raise StructuralPreparationError(
            'the installed mesher lacks the material source-transfer routing contract') from error
    if shell_ids and requires_material_source_transfer(closure.working_model, mesh):
        if source_geometry is None:
            raise StructuralPreparationError(
                'curvilinear source publication requires live source geometry '
                'and live owner source-cell authority')
        try:
            from anymesher import publish_model_closure_mesh_to_source
        except ImportError as error:
            raise StructuralPreparationError(
                'the installed mesher lacks the owner source-publication service') from error
        from anymesher.errors import MeshError
        try:
            publish_model_closure_mesh_to_source(
                source_geometry, closure, mesh,
                cancellation_check=cancellation_check,
                qualification=qualification)
        except (MeshError, GeometryError) as error:
            raise StructuralPreparationError(str(error)) from error
        # The owner proves coverage/parameter transfer and publishes all source
        # associations atomically. Generic ID aggregation below cannot replace
        # that proof and must not rewrite the owner-published record.
        return

    qualified_authority: dict[str, Any] | None = None
    preparation = getattr(mesh, "structural_preparation", None)
    if isinstance(preparation, Mapping) and "qualified_s3" in preparation:
        qualified_s3 = preparation["qualified_s3"]
        if not isinstance(qualified_s3, dict):
            raise StructuralPreparationError(
                "qualified-S3 preparation record must be a mutable mapping"
            )
        authority = qualified_s3.get("authority_model")
        if not isinstance(authority, dict):
            raise StructuralPreparationError(
                "qualified-S3 preparation record lacks model authority"
            )
        qualified_authority = authority

    face_owner = _descendant_to_sources(closure, "face")
    edge_owner = _descendant_to_sources(closure, "edge")
    vertex_owner = _descendant_to_sources(closure, "vertex")
    edge_paths = source_edge_paths(closure) if edge_paths is None else edge_paths
    for source_id, path in edge_paths.items():
        for edge, forward in path:
            edge_owner[edge] = tuple(sorted(set(edge_owner.get(edge, ())) | {source_id}))
    edge_tables = {}
    for name in ("nodes_of_edge", "offset_nodes_of_edge", "elements_of_edge"):
        original = getattr(mesh, name)
        table = _aggregate(original, edge_owner)
        for source_id, path in edge_paths.items():
            present = [bool(original.get(edge)) for edge, forward in path]
            if name == "nodes_of_edge" and any(present) and not all(present):
                raise StructuralPreparationError(
                    f"source edge {source_id} has incomplete prepared {name} associations")
            values = []
            for edge, forward in path:
                sequence = list(original.get(edge, ()))
                if not forward and name != "elements_of_edge":
                    sequence.reverse()
                if values and sequence and name == "nodes_of_edge" and values[-1] != sequence[0]:
                    raise StructuralPreparationError(
                        f"source edge {source_id} has disconnected prepared {name} associations")
                # Offsets may exist on only some children. They are an
                # independent support-node association, never a load path.
                shared = values and sequence and name != "elements_of_edge" and values[-1] == sequence[0]
                values.extend(sequence[1:] if shared else sequence)
            if values:
                table[source_id] = list(dict.fromkeys(values)) if name == "elements_of_edge" else values
        edge_tables[name] = table
    mesh.elements_of_face = _aggregate(mesh.elements_of_face, face_owner)
    _publish_route_face_ownership(mesh, face_owner)
    for name, table in edge_tables.items():
        setattr(mesh, name, table)
    seeding = getattr(mesh, "seeding", None)
    if seeding is not None:
        source_divisions: dict[int, int] = {}
        source_classes: dict[int, int] = {}
        for working_id, divisions in seeding.divisions.items():
            for source_id in edge_owner.get(int(working_id), ()):
                source_divisions[source_id] = (
                    source_divisions.get(source_id, 0) + int(divisions)
                )
                if working_id in seeding.classes:
                    source_classes.setdefault(
                        source_id, int(seeding.classes[working_id])
                    )
        for source_id, path in edge_paths.items():
            divisions = sum(int(seeding.divisions.get(edge, 0)) for edge, forward in path)
            if divisions:
                source_divisions[source_id] = divisions
                for edge, forward in path:
                    if edge in seeding.classes:
                        source_classes[source_id] = int(seeding.classes[edge])
                        break
        seeding.divisions = source_divisions
        seeding.classes = source_classes
    mesh.node_of_vertex = {
        source_id: node_id
        for working_id, node_id in mesh.node_of_vertex.items()
        for source_id in vertex_owner.get(working_id, ())
    }
    # A prepared source face may be represented by several work-face grids;
    # the neutral Mesh fallback derives its nodes from aggregated elements.
    mesh.grid_of_face = {}
    mesh.thickness_of_face = {
        source_id: value
        for working_id, value in mesh.thickness_of_face.items()
        for source_id in face_owner.get(working_id, ())
    }
    mesh.geometry_model_id = closure.source_model_id
    mesh.geometry_revision = closure.source_revision
    _publish_source_high_order_certificate(mesh, source_geometry, closure.working_model.model_id)
    if qualified_authority is not None:
        qualified_authority["source_model_id"] = str(closure.source_model_id)
        qualified_authority["source_revision"] = int(closure.source_revision)
