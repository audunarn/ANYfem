"""Members that lie on a plate's interior, for the submodel box cut.

ANYgeometry's volume cut partitions *seam* members (axis edges that are edges of a
face the box reaches) and refuses every attachment that targets a face near the box.
A stiffener standing on a large plate is neither: its axis edge has no face-loop
incidence, and an exact ``MEMBER_ON_FACE`` attachment ties it to the plate.  Two
things follow, handled here without relaxing the owner's refusals:

* **Remote structure is filtered, with proof.**  Attachments and junctions of a
  member none of whose edges reaches the box (conservative edge extents, the
  owner's own test) cannot involve the cut; they are removed from the detached
  clone the cut runs on, and counted.  The parent is never touched.
* **A straight member on a plane is carried as detached geometry.**  Such a member
  that does reach the box is taken out of the clone before the cut, then rebuilt in
  the child: the exact clip of its axis against the box, one child edge per
  reaching parent edge, one child member, and one exact ``MEMBER_ON_FACE``
  attachment to the single child face that descends from its parent face.  The
  mapping child edge -> parent edge and child member -> parent member is recorded
  in the cut's lineage so loads, supports, sections, offsets and frames follow it.

Everything else reaching the box refuses with a typed code: curved or multi-edge
non-contiguous axes, more than one attachment, another target kind, approximate
evidence, a junction or an endpoint attachment on the member, a member lying in a
box plane, a touch without length, an ambiguous child face.  Members with no
attachments are not handled here (free-standing beams refuse in ``submodel_cut``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from .submodel import SubmodelError

__all__ = ["AttachedCarry", "AttachedPlan", "carry_attached_members", "plan_attached_members"]

_CODE = "CUT_ATTACHED_UNSUPPORTED"


@dataclass(frozen=True)
class _Piece:
    """The part of one parent edge inside the box, as parameters of its start->end direction."""

    edge_id: int
    t0: float
    t1: float
    forward: bool  # the member runs along the edge's own direction


@dataclass(frozen=True)
class _Interior:
    member_id: int
    attachment_id: int
    face_id: int
    pieces: Tuple[_Piece, ...]
    clipped: bool


@dataclass(frozen=True)
class AttachedPlan:
    """What to do before the cut: the geometry to cut and the members to carry afterwards."""

    geometry: Any  # parent.geometry itself, or a detached clone with the filtered relations removed
    interior: Tuple[_Interior, ...]
    cleared_edges: frozenset  # axis edges whose member left the clone (carried or proven clear of the box)
    filtered_attachments: int
    filtered_junctions: int
    cleared_members: Tuple[int, ...]  # members removed from the clone because the exact clip misses the box

    @property
    def edge_ids(self) -> frozenset:
        return self.cleared_edges


@dataclass(frozen=True)
class AttachedCarry:
    """Evidence for one carried interior member (in memory only)."""

    parent_member: int
    parent_attachment: int
    parent_face: int
    child_member: int
    child_face: int
    child_edges: Tuple[int, ...]
    parent_edges: Tuple[int, ...]
    parameter_ranges: Tuple[Tuple[float, float], ...]  # per child edge, on its parent edge


def _refuse(message: str, code: str = _CODE) -> None:
    raise SubmodelError(code, message)


def _clip(volume: Any, a: np.ndarray, b: np.ndarray, tolerance: float) -> Optional[Tuple[float, float]]:
    """Clip at the physical box planes, allowing roundoff for parallel segments."""

    qa, qb = (volume.local(point)[0] for point in (a, b))
    direction = qb - qa
    t0, t1 = 0.0, 1.0
    for axis in range(3):
        low, high = volume.lower[axis], volume.upper[axis]
        if abs(direction[axis]) < 1e-300:
            if qa[axis] < low - tolerance or qa[axis] > high + tolerance:
                return None
            continue
        first, second = (low - qa[axis]) / direction[axis], (high - qa[axis]) / direction[axis]
        if first > second:
            first, second = second, first
        t0, t1 = max(t0, first), min(t1, second)
        if t0 > t1:
            return None
    return t0, t1


def plan_attached_members(parent: Any, volume: Any, tolerance: float) -> AttachedPlan:
    """Classify the members with attachments against ``volume``; build the geometry to cut.

    Raises :class:`SubmodelError` for an attached member that reaches the box and cannot
    be carried.  Returns ``parent.geometry`` unchanged when nothing needs filtering.
    """

    from anygeometry.curves import Straight
    from anygeometry.errors import GeometryError
    from anygeometry.structural import AttachmentEvidence, AttachmentKind, AttachmentTargetKind, ConnectionIntent
    from anygeometry.surfaces import Plane
    from anygeometry.volume_cut import _edge_extent

    geometry = parent.geometry
    if not geometry.attachments and not geometry.junctions:
        return AttachedPlan(geometry, (), frozenset(), 0, 0, ())

    def reaches(point: np.ndarray) -> bool:
        return bool(volume.reaches((point, point), tolerance))

    # ---- which members reach the box (conservative extents, as the owner's plan does)
    uses_of: Dict[int, list] = {}
    hits: Dict[int, List[int]] = {}  # member -> indices of reaching edge uses
    for member_id, member in geometry.members.items():
        uses = [geometry.member_edge_uses[u] for u in member.edge_use_ids]
        uses_of[member_id] = uses
        hits[member_id] = [i for i, use in enumerate(uses)
                           if volume.reaches(_edge_extent(geometry, use.edge_id, volume), tolerance)]
    member_attachments = {member_id: [geometry.attachments[a] for a in sorted(geometry.attachments_for_member(member_id))]
                          for member_id in geometry.members}

    interior: List[_Interior] = []
    cleared_members: List[int] = []
    for member_id in sorted(geometry.members):
        if not hits[member_id]:
            continue
        uses = uses_of[member_id]
        reaching = [uses[i] for i in hits[member_id]]
        if all(geometry.faces_using_edge(use.edge_id) for use in reaching) or not member_attachments[member_id]:
            continue  # seam members and unattached members are the owner's / submodel_cut's business
        label = f"member {member_id} ({geometry.members[member_id].name!r})"
        if any(geometry.faces_using_edge(use.edge_id) for use in reaching):
            _refuse(f"{label} reaches the box along both plate-edge and free-standing axis edges")
        # ``parent_range`` is the interval the whole edge occupies on the MEMBER axis, not a clip of
        # the edge: the uses must tile the member 0..1 without gaps or overlap, and every edge is
        # used whole.  Anything else is a member this carry cannot rebuild.
        expected_start = 0.0
        for use in uses:
            if abs(use.parent_range.start - expected_start) > 1e-9:
                _refuse(f"{label} has edge uses that do not tile its axis contiguously (at edge {use.edge_id})")
            expected_start = use.parent_range.end
        if abs(expected_start - 1.0) > 1e-9:
            _refuse(f"{label} has edge uses that do not cover its whole axis")
        indices = hits[member_id]
        if indices != list(range(indices[0], indices[-1] + 1)):
            _refuse(f"{label} enters the box more than once")
        for use in reaching:
            edge = geometry.edges[use.edge_id]
            if not isinstance(edge.curve, Straight):
                _refuse(f"{label} runs on the curved edge {use.edge_id}; only straight axes are carried")
            if use.edge_id not in parent.edge_sections:
                _refuse(f"{label} has no beam section on edge {use.edge_id}")
            if use.edge_id in parent.seeding_overrides:
                _refuse(f"edge {use.edge_id} carries a seeding override, which cannot follow a partial edge",
                        "CUT_ATTACHED_SEEDING")
        found = member_attachments[member_id]
        if len(found) != 1:
            _refuse(f"{label} has {len(found)} attachments; exactly one exact face attachment is carried")
        attachment = found[0]
        if (attachment.kind is not AttachmentKind.MEMBER_ON_FACE or attachment.target_kind is not AttachmentTargetKind.FACE
                or attachment.evidence is not AttachmentEvidence.EXACT
                or attachment.connection_intent is not ConnectionIntent.CONNECT
                or attachment.member_range.start != 0.0 or attachment.member_range.end != 1.0):
            _refuse(f"{label} is attached by {attachment.kind.value} to a {attachment.target_kind.value} "
                    f"({attachment.evidence.value}); only an exact member_on_face attachment over the whole axis is carried")
        face = geometry.faces.get(attachment.target_id)
        if face is None or not isinstance(getattr(face, "surface", None), Plane):
            _refuse(f"{label} is attached to face {attachment.target_id}, which is not a plane")
        reference = geometry.members[member_id].orientation_reference
        if reference is not None and tuple(reference) != ("face", attachment.target_id):
            _refuse(f"{label} is oriented by {tuple(reference)}, which is not its plate")
        for junction_id, junction in geometry.junctions.items():
            if any(use.member_id == member_id for use in junction.member_uses):
                _refuse(f"{label} takes part in junction {junction_id}")
        for other_id, other in geometry.attachments.items():
            if other_id != attachment.id and (other.target_kind is AttachmentTargetKind.MEMBER
                                              and other.target_id == member_id):
                _refuse(f"attachment {other_id} targets {label}")
        # the exact clip of every reaching edge
        pieces: List[_Piece] = []
        clipped = False
        for position, use in enumerate(reaching):
            edge = geometry.edges[use.edge_id]
            a, b = (np.asarray(geometry.vertex_position(v), dtype=float) for v in (edge.start, edge.end))
            length = float(np.linalg.norm(b - a))
            span = _clip(volume, a, b, tolerance) if length > 0.0 else None
            if span is None:
                continue
            t0, t1 = span
            if t0 * length <= tolerance:
                t0 = 0.0
            if (1.0 - t1) * length <= tolerance:
                t1 = 1.0
            if (t1 - t0) * length <= 2.0 * tolerance:
                _refuse(f"{label} only touches the box along edge {use.edge_id} (no length inside)", "CUT_ATTACHED_TOUCH")
            local = [volume.local(a + t * (b - a))[0] for t in (t0, t1)]
            for axis in range(3):
                for bound in (volume.lower[axis], volume.upper[axis]):
                    if all(abs(q[axis] - bound) <= tolerance for q in local):
                        _refuse(f"{label} lies in a box plane along edge {use.edge_id}", "CUT_ATTACHED_ON_CUT")
            clipped = clipped or t0 > 0.0 or t1 < 1.0
            pieces.append(_Piece(use.edge_id, t0, t1, use.orientation.value > 0))
        if not pieces:
            cleared_members.append(member_id)  # the extents overlap the box, the exact axis does not
            continue
        for first, second in zip(pieces, pieces[1:]):
            # along the member: a forward piece is left at t1 and entered at t0; a reversed one the opposite
            leaves = first.t1 if first.forward else first.t0
            enters = second.t0 if second.forward else second.t1
            if leaves != (1.0 if first.forward else 0.0) or enters != (0.0 if second.forward else 1.0):
                _refuse(f"{label} leaves and re-enters the box between edges {first.edge_id} and {second.edge_id}")
        interior.append(_Interior(member_id, attachment.id, attachment.target_id, tuple(pieces), clipped))

    # ---- relations of members that are clear of the box can be removed from the clone
    reaching_members = {m for m, h in hits.items() if h} - set(cleared_members)
    removed_members = {i.member_id for i in interior} | set(cleared_members)
    removable_members = {m for m, h in hits.items() if not h} | set(cleared_members)
    junction_ids = [j for j, junction in sorted(geometry.junctions.items())
                    if all(use.member_id in removable_members for use in junction.member_uses)]
    attachment_ids = []
    for attachment_id, attachment in sorted(geometry.attachments.items()):
        if attachment.target_kind.value == "member" and attachment.target_id in reaching_members | removed_members:
            continue  # the owner refuses it (or it was refused above)
        if attachment.member_id is not None:
            clear = attachment.member_id in removable_members
        elif attachment.source_kind == "vertex":
            clear = not reaches(np.asarray(geometry.vertex_position(attachment.source_id), dtype=float))
        else:
            clear = False
        if clear or attachment_id in {i.attachment_id for i in interior}:
            attachment_ids.append(attachment_id)
    if not (junction_ids or attachment_ids or removed_members):
        return AttachedPlan(geometry, (), frozenset(), 0, 0, ())

    clone = geometry.clone(include_features=False)
    filtered_junctions = filtered_attachments = 0
    try:
        for junction_id in junction_ids:
            clone.remove_junction(junction_id)
            filtered_junctions += 1
        carried_attachments = {i.attachment_id for i in interior}
        for attachment_id in attachment_ids:
            try:
                clone.remove_attachment(attachment_id)
            except GeometryError:
                if attachment_id in carried_attachments:
                    raise
                continue  # still referenced by a relation we did not filter: the owner decides
            if attachment_id not in carried_attachments:
                filtered_attachments += 1
        for member_id in sorted(removed_members):
            clone.remove_member(member_id)
    except GeometryError as error:
        raise SubmodelError("CUT_ATTACHED_PREPARE", f"the attached members could not be separated: {error}") from error
    cleared_edges = frozenset(use.edge_id for m in removed_members for use in uses_of[m])
    return AttachedPlan(clone, tuple(interior), cleared_edges, filtered_attachments, filtered_junctions,
                        tuple(sorted(cleared_members)))


# ----------------------------------------------------------------------
def _polygon(child: Any, face_id: int) -> np.ndarray:
    """Vertices of a planar child face with one straight outer loop and no holes."""

    from anygeometry.curves import Straight

    face = child.faces[face_id]
    if getattr(face, "holes", ()):
        _refuse(f"child face {face_id} has holes; the attached member cannot be located in it")
    points = []
    for item in face.loop:
        edge = child.edges[item.edge]
        if not isinstance(edge.curve, Straight):
            _refuse(f"child face {face_id} has a curved boundary; the attached member cannot be located in it")
        points.append(np.asarray(child.vertex_position(edge.start if item.forward else edge.end), dtype=float))
    return np.asarray(points)


def _in_polygon(polygon: np.ndarray, point: np.ndarray, tolerance: float) -> bool:
    """Closed point-in-polygon test for a planar polygon in 3-D."""

    normal = np.zeros(3)
    for i in range(len(polygon)):
        normal += np.cross(polygon[i], polygon[(i + 1) % len(polygon)])
    axis = int(np.argmax(np.abs(normal)))
    keep = [a for a in range(3) if a != axis]
    ring = polygon[:, keep]
    x, y = point[keep]
    inside = False
    for i in range(len(ring)):
        p, q = ring[i], ring[(i + 1) % len(ring)]
        edge = q - p
        length2 = float(edge @ edge)
        s = 0.0 if length2 == 0.0 else min(1.0, max(0.0, float((np.array([x, y]) - p) @ edge) / length2))
        if float(np.linalg.norm(p + s * edge - np.array([x, y]))) <= tolerance:
            return True
        if (p[1] > y) != (q[1] > y) and x < (q[0] - p[0]) * (y - p[1]) / (q[1] - p[1]) + p[0]:
            inside = not inside
    return inside


def _segment_in_polygon(polygon: np.ndarray, a: np.ndarray, b: np.ndarray, tolerance: float) -> bool:
    """Whether the whole segment ``a->b`` lies in the closed planar polygon (no endpoint-only test).

    The polygon may be non-convex.  The segment is cut at every parameter where it meets the
    boundary (proper crossings, touches of a vertex, collinear overlaps); between two consecutive
    cut parameters it is either wholly inside or wholly outside, so testing the cut points and the
    midpoints of the pieces with the closed point test is exact up to ``tolerance``.
    """

    normal = np.zeros(3)
    for i in range(len(polygon)):
        normal += np.cross(polygon[i], polygon[(i + 1) % len(polygon)])
    keep = [axis for axis in range(3) if axis != int(np.argmax(np.abs(normal)))]
    ring = polygon[:, keep]
    p0, d = a[keep], (b - a)[keep]
    dd = float(d @ d)
    cuts = [0.0, 1.0]
    for i in range(len(ring)):
        p, q = ring[i], ring[(i + 1) % len(ring)]
        e = q - p
        denominator = d[0] * e[1] - d[1] * e[0]
        w = p - p0
        if abs(denominator) > 1e-14 * max(1.0, float(np.linalg.norm(d) * np.linalg.norm(e))):
            s = (w[0] * e[1] - w[1] * e[0]) / denominator
            u = (w[0] * d[1] - w[1] * d[0]) / denominator
            if -1e-9 <= u <= 1.0 + 1e-9 and -1e-9 <= s <= 1.0 + 1e-9:
                cuts.append(min(1.0, max(0.0, float(s))))
        if dd > 0.0:  # vertices on / near the segment (touches, collinear overlaps)
            for vertex in (p, q):
                s = float((vertex - p0) @ d) / dd
                if 0.0 <= s <= 1.0 and float(np.linalg.norm(p0 + s * d - vertex)) <= max(tolerance, 1e-12):
                    cuts.append(s)
    cuts = sorted(set(cuts))
    points = list(cuts) + [(x + y) / 2.0 for x, y in zip(cuts, cuts[1:])]
    return all(_in_polygon(polygon, a + s * (b - a), tolerance) for s in points)


def carry_attached_members(parent: Any, cut: Any, volume: Any, tolerance: float,
                           plan: AttachedPlan) -> Tuple[AttachedCarry, ...]:
    """Rebuild the interior members in the cut's child geometry and extend the cut's lineage."""

    if not plan.interior:
        return ()
    from anygeometry.entities import OrientedEdge
    from anygeometry.errors import GeometryError
    from anygeometry.structural import AttachmentKind, ParameterRange

    child = cut.closure.working_model
    geometry = parent.geometry
    source_of = {target.key: source.key for target, source in cut.closure.work_to_source.items()}
    carried: List[AttachedCarry] = []
    try:
        for item in plan.interior:
            member = geometry.members[item.member_id]
            attachment = geometry.attachments[item.attachment_id]
            candidates = sorted(f for f, sources in cut.source_faces.items() if item.face_id in sources)
            if len(candidates) != 1:
                _refuse(f"member {item.member_id}: parent face {item.face_id} has {len(candidates)} child faces "
                        f"{candidates} in the box; exactly one is required", "CUT_ATTACHED_AMBIGUOUS")
            child_face = candidates[0]
            if type(getattr(child.faces[child_face], "surface", None)).__name__ != "Plane":
                _refuse(f"member {item.member_id}: child face {child_face} is not a plane", "CUT_ATTACHED_AMBIGUOUS")
            polygon = _polygon(child, child_face)
            sheets = sorted({use.sheet_id for use in child.face_uses.values() if use.face_id == child_face})
            if len(sheets) != 1:
                _refuse(f"member {item.member_id}: child face {child_face} is in {len(sheets)} sheets", "CUT_ATTACHED_AMBIGUOUS")
            if attachment.sheet_id is not None and source_of.get(("sheet", sheets[0])) != ("sheet", attachment.sheet_id):
                _refuse(f"member {item.member_id}: the child sheet does not descend from sheet {attachment.sheet_id}",
                        "CUT_ATTACHED_AMBIGUOUS")
            parts = sorted(key[1] for key, value in source_of.items()
                           if key[0] == "part" and value == ("part", member.part_id))
            if parts:
                part_id = parts[0]
            else:
                part = geometry.parts[member.part_id]
                part_id = child.add_part(name=part.name, metadata=dict(part.metadata))

            vertices: Dict[int, int] = {}
            edges: List[int] = []
            ends: List[np.ndarray] = []
            oriented = []
            for piece in item.pieces:
                edge = geometry.edges[piece.edge_id]
                a, b = (np.asarray(geometry.vertex_position(v), dtype=float) for v in (edge.start, edge.end))
                made = []
                for parent_vertex, t, position in ((edge.start, piece.t0, a + piece.t0 * (b - a)),
                                                   (edge.end, piece.t1, a + piece.t1 * (b - a))):
                    if t in (0.0, 1.0):
                        if parent_vertex not in vertices:
                            existing = [vid for (kind, vid), sources in cut.lineage.items()
                                        if kind == "vertex" and parent_vertex in sources]
                            if len(existing) > 1:
                                _refuse(f"Vertex {parent_vertex} has multiple child descendants", "CUT_ATTACHED_AMBIGUOUS")
                            vertex_id = existing[0] if existing else child.add_point(*position)
                            vertices[parent_vertex] = vertex_id
                            if not existing:
                                cut.lineage[("vertex", vertex_id)] = (parent_vertex,)
                        made.append(vertices[parent_vertex])
                    else:
                        made.append(child.add_point(*position))
                    ends.append(position)
                if not _segment_in_polygon(polygon, a + piece.t0 * (b - a), a + piece.t1 * (b - a), tolerance):
                    _refuse(f"member {item.member_id}: its axis leaves child face {child_face}", "CUT_ATTACHED_AMBIGUOUS")
                edges.append(child.add_line(made[0], made[1]))
                oriented.append(OrientedEdge(edges[-1], piece.forward))
            child_member = child.add_member(
                oriented, part_id=part_id, name=member.name, metadata=dict(member.metadata),
                orientation_reference=None if member.orientation_reference is None else ("face", child_face))
            for new_use, piece in zip(child.members[child_member].edge_use_ids, item.pieces):
                original = next(u for u in geometry.members[item.member_id].edge_use_ids
                                if geometry.member_edge_uses[u].edge_id == piece.edge_id)
                metadata = geometry.member_edge_uses[original].metadata
                if metadata:
                    child._put_structural("member_edge_use", replace(child.member_edge_uses[new_use],
                                                                     metadata=dict(metadata)))
            ranges = [[], []]
            for position in ends:
                _point, uv, distance = child.project_to_face(child_face, position)
                if distance > max(tolerance, 1e-9):
                    _refuse(f"member {item.member_id}: a carried end is {distance:.3g} off child face {child_face}",
                            "CUT_ATTACHED_AMBIGUOUS")
                for axis in (0, 1):
                    ranges[axis].append(min(1.0, max(0.0, float(uv[axis]))))
            child.add_attachment(
                child_member, AttachmentKind.MEMBER_ON_FACE, "face", child_face, ParameterRange(0.0, 1.0),
                tuple(ParameterRange(min(values), max(values)) for values in ranges),
                connection_intent=attachment.connection_intent, evidence=attachment.evidence,
                max_residual=attachment.max_residual,
                tolerance_used=max(attachment.tolerance_used, attachment.max_residual),
                sheet_id=sheets[0], part_id=None if attachment.part_id is None else part_id,
                metadata=dict(attachment.metadata),
                provenance={"submodel_cut": "attached_member", "source_member": item.member_id,
                            "source_attachment": item.attachment_id})
            cut.lineage[("member", child_member)] = (item.member_id,)
            for child_edge, piece in zip(edges, item.pieces):
                cut.lineage[("edge", child_edge)] = (piece.edge_id,)
            carried.append(AttachedCarry(
                item.member_id, item.attachment_id, item.face_id, child_member, child_face, tuple(edges),
                tuple(p.edge_id for p in item.pieces), tuple((p.t0, p.t1) for p in item.pieces)))
    except GeometryError as error:
        raise SubmodelError("CUT_ATTACHED_PREPARE", f"an attached member could not be rebuilt in the child: {error}") from error
    return tuple(carried)


def check_cut_beam_connections(definition, mesh) -> None:
    """Each attached beam end on a box cut must follow its driven plate trace."""
    from anygeometry.volume_cut import BoxVolume

    child = definition.child
    geometry = child.geometry
    if not geometry.attachments:
        return
    box = BoxVolume(**{key: definition.volume[key] for key in ("lower", "upper", "transform")})
    tolerance = geometry.tolerance.effective_length(box.diagonal)
    trace = {side: {node for edge in edges for node in mesh.nodes_of_edge.get(edge, ())}
             for side, edges in definition.cut_faces.items()}
    couplings = {tie.beam_node: tie for tie in mesh.couplings.values()}
    for attachment in geometry.attachments.values():
        if attachment.kind.value != "member_on_face":
            continue
        for use_id in geometry.members[attachment.member_id].edge_use_ids:
            edge_id = geometry.member_edge_uses[use_id].edge_id
            if edge_id not in child.edge_sections or geometry.faces_using_edge(edge_id):
                continue
            edge = geometry.edges[edge_id]
            bases = mesh.nodes_of_edge.get(edge_id, ())
            beams = mesh.offset_nodes_of_edge.get(edge_id, ())
            if not len(beams):
                beams = bases
            for vertex in (edge.start, edge.end):
                point = np.asarray(geometry.vertex_position(vertex))
                local = box.local(point)[0]
                sides = [side for side in box.sides_of((local, local), tolerance) if side in trace]
                if not sides:
                    continue
                stations = [i for i, node in enumerate(bases)
                            if np.linalg.norm(mesh.nodes[node] - point) <= tolerance]
                if len(stations) != 1 or len(beams) != len(bases):
                    _refuse(f"Beam edge {edge_id} has no unique end station on the cut.", "SUBMODEL_BEAM_CUT_TIE")
                beam = beams[stations[0]]
                tie = couplings.get(beam)
                masters = ({beam} if tie is None else
                           {n for n, weight in zip(tie.plate_nodes, tie.weights) if abs(weight) > 1e-12})
                if not masters or any(not masters.issubset(trace[side]) for side in sides):
                    _refuse(f"Beam edge {edge_id} end is not coupled to the driven plate edge.", "SUBMODEL_BEAM_CUT_TIE")
