"""Box cuts through members attached to a plate's interior (the basic 5 m barge section).

Geometry-only: the parent is never solved; a stub job id stands in for the source job
because the cut only checks that the job exists.  NOT YET RUN by the author (no shell
in the authoring session); the coordinator runs them on the recorded dependencies.

The deck stiffener that the box crosses is a multi-edge member: each of its edges occupies
a sub-interval of the member axis (``MemberEdgeUse.parent_range``), which is not a clip of
the edge.  The barge is located by its structure (a free-standing, sectioned, face-attached
straight edge along x spanning the box), not by hard-coded ids.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from anygeometry.serialization import to_dict
from anygeometry.structural import AttachmentEvidence, AttachmentKind, AttachmentTargetKind, ParameterRange
from anygeometry.volume_cut import BoxVolume
from anyfem.application.examples import floating_barge_section
from anyfem.model.submodels import SourceReference
from anyfem.submodel import SubmodelError
from anyfem.submodel_attached_cut import _clip, _in_polygon, _segment_in_polygon
from anyfem.submodel_cut import cut_submodel

X0, X1 = 1.0, 1.75  # the box in x; the stiffener edge spans it


def stiffened_box(y: float, z: float) -> BoxVolume:
    return BoxVolume((X0, y - 0.25, z - 0.02), (X1, y + 0.25, z + 0.02))


def shipped_barge():
    """``(project, (member_id, edge_id), (y, z))`` of the attached deck stiffener crossing the box."""

    project = floating_barge_section()
    geometry = project.geometry
    found = []
    for member_id, member in geometry.members.items():
        if not list(geometry.attachments_for_member(member_id)):
            continue
        for use_id in member.edge_use_ids:
            edge_id = geometry.member_edge_uses[use_id].edge_id
            if edge_id not in project.edge_sections or geometry.faces_using_edge(edge_id):
                continue
            edge = geometry.edges[edge_id]
            a, b = (np.asarray(geometry.vertex_position(v), dtype=float) for v in (edge.start, edge.end))
            lo, hi = sorted((a[0], b[0]))
            if abs(a[1] - b[1]) < 1e-9 and abs(a[2] - b[2]) < 1e-9 and lo <= X0 and hi >= X1:
                found.append((abs(a[1] - 0.1), member_id, edge_id, float(a[1]), float(a[2])))
    assert found, "the 5 m barge section has a deck stiffener edge along x across x = 1..1.75"
    _, member_id, edge_id, y, z = sorted(found)[0]
    return project, (member_id, edge_id), (y, z)


def clear_bay_box(project, y: float, z: float) -> BoxVolume:
    """A box on the deck between stiffeners, away from every stiffener axis."""

    geometry = project.geometry
    ys = set()
    for edge_id in project.edge_sections:
        if edge_id in geometry.edges and not geometry.faces_using_edge(edge_id):
            edge = geometry.edges[edge_id]
            a, b = (np.asarray(geometry.vertex_position(v), dtype=float) for v in (edge.start, edge.end))
            ys.add(round(float(a[1]), 9))
            ys.add(round(float(b[1]), 9))
    ordered = sorted(ys)
    gaps = [(low, high) for low, high in zip(ordered, ordered[1:]) if high - low >= 0.5]
    assert gaps, f"no 0.5 m gap between stiffener lines {ordered}"
    low, high = min(gaps, key=lambda gap: abs(gap[0] - y))
    centre = 0.5 * (low + high)
    return BoxVolume((X0, centre - 0.15, z - 0.02), (1.5, centre + 0.15, z + 0.02))


def run_cut(project, volume):
    """``cut_submodel`` on the project itself, with a stub source job (geometry-only)."""

    project.jobs["stub-job"] = object()
    case = next(iter(project.load_cases))
    snapshot = SimpleNamespace(thaw=lambda: project)
    return cut_submodel(snapshot, volume, source=SourceReference("stub-job", case), policy="drive_all",
                        target_size=0.25)


def checksum(project):
    return to_dict(project.geometry)["checksum"]["value"]


def test_the_shipped_stiffener_is_free_of_face_loops_and_attached_to_the_deck():
    project, (member_id, edge_id), _ = shipped_barge()
    geometry = project.geometry
    assert not geometry.faces_using_edge(edge_id)
    (attachment,) = [geometry.attachments[a] for a in geometry.attachments_for_member(member_id)]
    assert attachment.kind is AttachmentKind.MEMBER_ON_FACE and attachment.evidence is AttachmentEvidence.EXACT
    assert edge_id in project.edge_sections
    # parent_range tiles the member axis; it is not a clip of the edge
    uses = [geometry.member_edge_uses[u] for u in geometry.members[member_id].edge_use_ids]
    assert uses[0].parent_range.start == 0.0 and uses[-1].parent_range.end == 1.0
    for first, second in zip(uses, uses[1:]):
        assert first.parent_range.end == pytest.approx(second.parent_range.start)


def test_a_box_across_the_attached_stiffener_carries_it_into_the_child():
    project, (member_id, edge_id), (y, z) = shipped_barge()
    deck = project.geometry.attachments[next(iter(project.geometry.attachments_for_member(member_id)))].target_id
    before = checksum(project)
    cut = run_cut(project, stiffened_box(y, z))
    assert checksum(project) == before  # the parent is immutable
    child = cut.definition.child
    geometry = child.geometry
    (carry,) = cut.report.attached
    assert carry.parent_member == member_id and carry.parent_edges == (edge_id,) and carry.parent_face == deck
    assert len(geometry.members) == 1 and len(carry.child_edges) == 1
    (child_edge,) = carry.child_edges
    assert not geometry.faces_using_edge(child_edge)  # still a plate-interior axis, as in the parent
    ends = sorted(tuple(np.round(geometry.vertex_position(v), 9))
                  for v in (geometry.edges[child_edge].start, geometry.edges[child_edge].end))
    assert ends == [(X0, round(y, 9), round(z, 9)), (X1, round(y, 9), round(z, 9))]
    # lineage, section, face
    assert cut.report.cut.lineage[("edge", child_edge)] == (edge_id,)
    assert cut.report.cut.lineage[("member", carry.child_member)] == (member_id,)
    assert child.edge_sections[child_edge] == project.edge_sections[edge_id]
    assert cut.definition.source_faces[carry.child_face] == (deck,)
    (child_attachment,) = [geometry.attachments[a] for a in geometry.attachments_for_member(carry.child_member)]
    assert child_attachment.kind is AttachmentKind.MEMBER_ON_FACE and child_attachment.target_id == carry.child_face
    assert child_attachment.target_kind is AttachmentTargetKind.FACE
    assert child_attachment.evidence is AttachmentEvidence.EXACT
    # the child member is a single use that tiles its own axis 0..1
    (child_use,) = [geometry.member_edge_uses[u] for u in geometry.members[carry.child_member].edge_use_ids]
    assert (child_use.parent_range.start, child_use.parent_range.end) == (0.0, 1.0)
    # frame and offset carried (the cut itself refuses otherwise)
    for a, b in zip(project.beam_section_frame(edge_id), child.beam_section_frame(child_edge)):
        assert np.allclose(a, b)
    assert np.allclose(project.beam_offset_vector(edge_id), child.beam_offset_vector(child_edge))
    # the remote stiffeners and joints were filtered, with a count, and the artificial cut is the box on the deck
    assert cut.report.dropped_outside["remote_attachment_relations"] > 0
    assert cut.report.carried["attached_members"] == 1 and cut.report.carried["beam_assignments"] == 1
    assert set(cut.definition.cut_faces) == {"x0", "x1", "y0", "y1"}
    from anyfem.submodel_attached_cut import check_cut_beam_connections

    mesh = child.generate_mesh(cut.definition.target_size)
    check_cut_beam_connections(cut.definition, mesh)
    end_node = mesh.offset_nodes_of_edge[child_edge][0]
    key = next(key for key, tie in mesh.couplings.items() if tie.beam_node == end_node)
    del mesh.couplings[key]
    with pytest.raises(SubmodelError, match="SUBMODEL_BEAM_CUT_TIE"):
        check_cut_beam_connections(cut.definition, mesh)


def test_a_clear_bay_next_to_the_stiffener_carries_no_member():
    project, _, (y, z) = shipped_barge()
    cut = run_cut(project, clear_bay_box(project, y, z))
    child = cut.definition.child
    assert not cut.report.attached and not child.geometry.members and not child.edge_sections
    assert cut.report.dropped_outside["beam_assignments"] > 0
    assert cut.report.dropped_outside["remote_attachment_relations"] > 0


def test_a_free_standing_beam_across_the_box_still_refuses():
    project, (_member, edge_id), (y, z) = shipped_barge()
    geometry = project.geometry
    line = geometry.add_line(geometry.add_point(1.1, y + 0.1, z), geometry.add_point(1.6, y + 0.1, z))
    project.assign_beam(line, project.edge_sections[edge_id])
    with pytest.raises(SubmodelError, match="CUT_BEAMS_UNSUPPORTED"):
        run_cut(project, stiffened_box(y, z))


def test_a_second_attachment_on_the_crossing_member_refuses():
    project, (member_id, _edge), (y, z) = shipped_barge()
    geometry = project.geometry
    attachment = geometry.attachments[next(iter(geometry.attachments_for_member(member_id)))]
    geometry.add_attachment(member_id, AttachmentKind.MEMBER_ON_FACE, "face", attachment.target_id,
                            ParameterRange(0.0, 1.0), attachment.target_parameters,
                            evidence=AttachmentEvidence.EXACT, tolerance_used=1e-9)
    with pytest.raises(SubmodelError, match="CUT_ATTACHED_UNSUPPORTED"):
        run_cut(project, stiffened_box(y, z))


def test_an_approximate_attachment_on_the_crossing_member_refuses():
    project, (member_id, _edge), (y, z) = shipped_barge()
    geometry = project.geometry
    attachment = geometry.attachments[next(iter(geometry.attachments_for_member(member_id)))]
    geometry.remove_attachment(attachment.id)
    geometry.add_attachment(member_id, AttachmentKind.MEMBER_ON_FACE, "face", attachment.target_id,
                            ParameterRange(0.0, 1.0), attachment.target_parameters,
                            evidence=AttachmentEvidence.VERIFIED_APPROXIMATE, tolerance_used=1e-6)
    with pytest.raises(SubmodelError, match="CUT_ATTACHED_UNSUPPORTED"):
        run_cut(project, stiffened_box(y, z))


def test_a_remote_endpoint_attachment_on_a_crossing_member_refuses():
    """Another member ending on the crossing stiffener is a junction the cut cannot carry."""

    project, (member_id, _edge), (y, z) = shipped_barge()
    geometry = project.geometry
    other = next(m for m in geometry.members if m != member_id)
    geometry.add_attachment(other, AttachmentKind.MEMBER_ENDPOINT_ON_MEMBER, AttachmentTargetKind.MEMBER, member_id,
                            ParameterRange.point(0.0), (ParameterRange.point(0.5),),
                            evidence=AttachmentEvidence.EXACT, tolerance_used=1e-9)
    with pytest.raises(SubmodelError, match="CUT_ATTACHED_UNSUPPORTED"):
        run_cut(project, stiffened_box(y, z))


def test_clip_is_exact_and_closed():
    box = BoxVolume((X0, -0.15, 2.48), (X1, 0.35, 2.52))
    a, b = np.array([0.0, 0.1, 2.5]), np.array([3.0, 0.1, 2.5])
    t0, t1 = _clip(box, a, b, 1e-9)
    assert (t0, t1) == pytest.approx((1.0 / 3.0, 1.75 / 3.0))
    assert _clip(box, np.array([0.0, 1.0, 2.5]), np.array([3.0, 1.0, 2.5]), 1e-9) is None
    assert _clip(box, np.array([0.0, 0.1, 2.5]), np.array([1.0, 0.1, 2.5]), 0.0) == pytest.approx((1.0, 1.0))


def test_point_in_polygon_is_closed_and_planar():
    square = np.array([[0.0, 0.0, 1.0], [2.0, 0.0, 1.0], [2.0, 1.0, 1.0], [0.0, 1.0, 1.0]])
    assert _in_polygon(square, np.array([1.0, 0.5, 1.0]), 1e-9)
    assert _in_polygon(square, np.array([2.0, 0.5, 1.0]), 1e-9)  # on the boundary
    assert not _in_polygon(square, np.array([2.1, 0.5, 1.0]), 1e-9)


def test_a_segment_across_a_notch_is_not_inside_a_non_convex_polygon():
    """U-shaped face: both ends are inside, the straight segment between them crosses the notch."""

    u_shape = np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [3.0, 2.0, 0.0], [2.0, 2.0, 0.0],
                        [2.0, 1.0, 0.0], [1.0, 1.0, 0.0], [1.0, 2.0, 0.0], [0.0, 2.0, 0.0]])
    left, right = np.array([0.5, 1.5, 0.0]), np.array([2.5, 1.5, 0.0])
    assert _in_polygon(u_shape, left, 1e-9) and _in_polygon(u_shape, right, 1e-9)
    assert not _segment_in_polygon(u_shape, left, right, 1e-9)
    # below the notch, and along the boundary, the segment stays inside (closed polygon)
    assert _segment_in_polygon(u_shape, np.array([0.5, 0.5, 0.0]), np.array([2.5, 0.5, 0.0]), 1e-9)
    assert _segment_in_polygon(u_shape, np.array([1.0, 1.0, 0.0]), np.array([2.0, 1.0, 0.0]), 1e-9)
    assert _segment_in_polygon(u_shape, np.array([0.0, 0.0, 0.0]), np.array([3.0, 0.0, 0.0]), 1e-9)
    # leaving through the outside
    assert not _segment_in_polygon(u_shape, np.array([0.5, 0.5, 0.0]), np.array([3.5, 0.5, 0.0]), 1e-9)
