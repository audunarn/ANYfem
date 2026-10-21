"""Public planar trim-edge B2 ownership and conservative application admission."""
import numpy as np
import pytest
from anygeometry.entities import OrientedEdge
from anygeometry.operations import trim_face
from anygeometry.structural import AttachmentKind, AttachmentTargetKind
from anymesher.quad.public_integration import QuadPublicUnsupported
from anyfem.application.examples import BulkheadOpening, floating_barge_section, floating_barge_large
from anyfem.io import project_to_dict, project_from_dict
from anyfem.native_meshing import NativeMeshSettings


def small_project():
    return floating_barge_section(length=3., breadth=4., bulkheads=(1.5,),
        girders=(0.,), stiffener_spacing=2.,
        openings=(BulkheadOpening(0., 1.25, 1.4, 1.1, .25),), target_size=.5)


def opening_edges(project):
    return [use.edge for face in project.geometry.faces.values()
            for loop in face.holes for use in loop]


def check_bars(project, mesh):
    shell_nodes = {node for element in mesh.shells for node in mesh.corners_of(element)}
    couplings = {c.beam_node: c for c in mesh.couplings.values()}
    assert len(couplings) == len(mesh.couplings)
    for edge in opening_edges(project):
        axis = mesh.nodes_of_edge[edge]
        offsets = mesh.offset_nodes_of_edge[edge]
        assert len(axis) == len(offsets) >= 2
        assert set(axis) <= shell_nodes
        beams = [e for e in mesh.elements_of_edge[edge] if e in mesh.beams]
        assert len(beams) == len(axis)-1
        assert all(len(mesh.beams[e]) == 2 for e in beams)
        assert {frozenset(mesh.beams[e]) for e in beams} == {
            frozenset((a, b)) for a, b in zip(offsets, offsets[1:])}
        for plate, beam in zip(axis, offsets):
            c = couplings[beam]
            point = sum((float(w)*mesh.nodes[n] for n, w in zip(c.plate_nodes, c.weights)), np.zeros(3))
            np.testing.assert_allclose(point, mesh.nodes[plate], rtol=0, atol=1e-10)
            np.testing.assert_allclose(mesh.nodes[beam]-point, c.eccentricity, rtol=0, atol=1e-10)
            np.testing.assert_allclose(c.eccentricity, project.beam_offsets[edge], rtol=0, atol=1e-10)


def reverse_openings(project):
    g = project.geometry
    for edge in opening_edges(project):
        for member in g.members_using_edge(edge):
            g.reverse_member(member)
    for face in tuple(g.faces.values()):
        if face.holes:
            trim_face(g, face.id, [tuple(OrientedEdge(u.edge, not u.forward)
                                       for u in reversed(loop)) for loop in face.holes])


def check_quadratic_bars(project, mesh):
    """Prove owner-built canonical shell/beam midsides, not just element counts.

    Solver B3 beams stay straight-sided, so on an arc the shell midside (on the
    true curve) and the beam midside (chord midpoint) differ.  Every opening
    B3 bar must (a) have two corner nodes coupled to the exact shell corner
    stations of its source edge, in order, with the authored offset; (b) have
    its midside node at the chord midpoint of the two beam corner nodes (no
    curved B3); and (c) have its midside coupling reconstruct the *shell
    midside node* of an element owning that very segment, with eccentricity =
    authored offset + the geometric chord-arc correction (shell chord midpoint
    minus shell midside).  The shell midside itself lies on the true curve.
    """
    assert mesh.is_quadratic
    nodes = mesh.nodes
    shell_corners = {n for e in mesh.shells for n in mesh.corners_of(e)}
    shell_all = {n for nodes_of in mesh.shells.values() for n in nodes_of}
    assert shell_corners < shell_all  # Q8/T6 shells really carry midsides
    shell_list = np.array(sorted(shell_all))
    shell_positions = np.asarray([nodes[n] for n in shell_list])
    couplings = {c.beam_node: c for c in mesh.couplings.values()}
    assert len(couplings) == len(mesh.couplings)

    def plate_of(beam_node):
        c = couplings[beam_node]
        point = sum((float(w)*nodes[n] for n, w in zip(c.plate_nodes, c.weights)), np.zeros(3))
        near = shell_list[np.linalg.norm(shell_positions-point, axis=1) <= 1e-10]
        assert len(near) == 1, (beam_node, point)
        return int(near[0])

    corrections = []
    for edge in opening_edges(project):
        offset = np.asarray(project.beam_offsets[edge], dtype=float)
        axis = [n for n in mesh.nodes_of_edge[edge] if n in shell_corners]
        corner_offsets = [o for o in mesh.offset_nodes_of_edge[edge] if plate_of(o) in shell_corners]
        assert len(axis) == len(corner_offsets) >= 2
        assert [plate_of(o) for o in corner_offsets] == axis
        beams = [e for e in mesh.elements_of_edge[edge] if e in mesh.beams]
        assert len(beams) == len(axis)-1
        stations = {frozenset(pair) for pair in zip(corner_offsets, corner_offsets[1:])}
        seen_mid = set()
        for element in beams:
            beam_nodes = tuple(mesh.beams[element])
            assert len(beam_nodes) == 3
            corners = [n for n in beam_nodes if plate_of(n) in shell_corners]
            mids = [n for n in beam_nodes if plate_of(n) not in shell_corners]
            assert len(corners) == 2 and len(mids) == 1
            assert frozenset(corners) in stations
            stations.discard(frozenset(corners))
            a, b = (plate_of(n) for n in corners)
            mid_plate = plate_of(mids[0])
            assert mid_plate in shell_all and mid_plate not in shell_corners
            assert mids[0] not in seen_mid
            seen_mid.add(mids[0])
            # The coupled midside is the midside of a shell element that owns
            # exactly this source-edge segment.
            owners = [e for e, ns in mesh.shells.items()
                      if {a, b} <= set(mesh.corners_of(e)) and mid_plate in ns]
            assert owners, (edge, a, b, mid_plate)
            _, _, residual = project.geometry.closest_edge_point(edge, nodes[mid_plate])
            assert residual <= 1e-10
            # Corners retain the authored offset exactly.
            for beam_node in corners:
                c = couplings[beam_node]
                np.testing.assert_allclose(c.eccentricity, offset, rtol=0, atol=1e-10)
                np.testing.assert_allclose(nodes[beam_node]-nodes[plate_of(beam_node)],
                                           offset, rtol=0, atol=1e-10)
            # B3 is straight-sided: the beam midside is the beam chord midpoint.
            np.testing.assert_allclose(nodes[mids[0]], 0.5*(nodes[corners[0]]+nodes[corners[1]]),
                                       rtol=0, atol=1e-10)
            # Source-owned shell station/coupling reconstruction of the midside.
            c = couplings[mids[0]]
            reconstructed = sum((float(w)*nodes[n] for n, w in zip(c.plate_nodes, c.weights)),
                                np.zeros(3))
            np.testing.assert_allclose(reconstructed, nodes[mid_plate], rtol=0, atol=1e-10)
            np.testing.assert_allclose(nodes[mids[0]]-reconstructed, c.eccentricity, rtol=0, atol=1e-10)
            # Midside eccentricity = authored offset + geometric chord-arc correction.
            correction = 0.5*(nodes[a]+nodes[b]) - nodes[mid_plate]
            np.testing.assert_allclose(c.eccentricity, offset+correction, rtol=0, atol=1e-10)
            corrections.append(float(np.linalg.norm(correction)))
        assert not stations
    # At least one opening edge is a true arc, where the correction is nonzero
    # (and the midside eccentricity is therefore NOT the unchanged offset).
    assert corrections and max(corrections) > 1e-6


def test_full_example_opening_intent_and_other_attachments_preserved():
    project = floating_barge_large()
    edges = opening_edges(project)
    assert len(edges) == 48
    boundary = [a for a in project.geometry.attachments.values()
                if a.kind is AttachmentKind.MEMBER_ON_FACE_BOUNDARY]
    assert len(boundary) == 48
    assert {a.target_id for a in boundary} == set(edges)
    assert all(a.target_kind is AttachmentTargetKind.EDGE for a in boundary)
    assert any(a.kind is AttachmentKind.MEMBER_ON_FACE for a in project.geometry.attachments.values())
    assert sum(len(f.holes) for f in project.geometry.faces.values()) == 6
    assert not project.geometry.validate_topology()


@pytest.mark.parametrize('reverse', [False, True])
def test_public_quad_linear_boundary_bars_roundtrip(reverse):
    project = small_project()
    if reverse:
        g = project.geometry
        for edge in opening_edges(project):
            for member in g.members_using_edge(edge):
                g.reverse_member(member)
        for face in tuple(g.faces.values()):
            if face.holes:
                trim_face(g, face.id, [tuple(OrientedEdge(u.edge, not u.forward)
                                           for u in reversed(loop)) for loop in face.holes])
    project.set_native_mesh_settings(NativeMeshSettings.create(.5, element_order='quadratic',
        backend='quad_first', parameters={'layout_policy': 'adaptive', 'quad_t3_angle_action': 'warning'}))
    reopened = project_from_dict(project_to_dict(project))
    before = repr(reopened.geometry.topology_snapshot())
    revision = reopened.geometry.revision
    mesh = reopened.generate_mesh(order='linear')
    check_bars(reopened, mesh)
    assert reopened.geometry.revision == revision
    assert repr(reopened.geometry.topology_snapshot()) == before
    # Effective call override must not alter persisted quadratic settings.
    assert reopened.native_mesh_settings.element_order == 'quadratic'
    assert reopened.mesh_records == {}


def test_explicit_arc_pin_remains_authoritative():
    project = small_project()
    arc = opening_edges(project)[1]
    reference = project.generate_mesh(.5, order='linear', strategy='quad_first',
        layout_policy='adaptive', quad_t3_angle_action='warning')
    # Pin an already admitted station count, rather than assuming arbitrary
    # extra arc subdivisions remain compatible with mandatory S3 quality.
    divisions = len(reference.nodes_of_edge[arc])-1
    mesh = project.generate_mesh(.5, order='linear', strategy='quad_first',
        layout_policy='adaptive', overrides={arc: divisions}, quad_t3_angle_action='warning')
    assert len(mesh.nodes_of_edge[arc]) == divisions+1
    check_bars(project, mesh)


def test_dense_arc_pin_truthfully_refuses_qualified_s3_without_publication():
    from anymesher.s3_repair import S3RepairError
    project = small_project()
    before = repr(project.geometry.topology_snapshot())
    arc = opening_edges(project)[1]
    with pytest.raises(S3RepairError, match='bounded qualified-S3 repair') as caught:
        project.generate_mesh(.5, order='linear', strategy='quad_first',
            layout_policy='adaptive', overrides={arc: 4}, quad_t3_angle_action='warning')
    error = caught.value
    assert error.admission is not None and not error.admission.admitted
    assert error.admission.violations
    assert error.quality_policy is not None
    assert error.attempts[-1].action == 'adjudication'
    assert error.attempts[-1].status == 'rejected'
    assert project.mesh_records == {}
    assert repr(project.geometry.topology_snapshot()) == before


QUAD_PARAMETERS = {'layout_policy': 'adaptive', 'quad_t3_angle_action': 'warning'}


@pytest.mark.parametrize('reverse', [False, True])
def test_quadratic_effective_order_override_admits_canonical_midsides(reverse):
    project = small_project()
    if reverse:
        reverse_openings(project)
    project.set_native_mesh_settings(NativeMeshSettings.create(.5, element_order='linear',
        backend='quad_first', parameters=QUAD_PARAMETERS))
    reopened = project_from_dict(project_to_dict(project))
    before = repr(reopened.geometry.topology_snapshot())
    revision = reopened.geometry.revision
    mesh = reopened.generate_mesh(order='quadratic')
    check_quadratic_bars(reopened, mesh)
    assert reopened.geometry.revision == revision
    assert repr(reopened.geometry.topology_snapshot()) == before
    # The call override is effective only; persisted linear intent is unchanged.
    assert reopened.native_mesh_settings.element_order == 'linear'
    assert reopened.mesh_records == {}


def test_quadratic_persisted_order_admits_canonical_midsides_without_override():
    project = small_project()
    project.set_native_mesh_settings(NativeMeshSettings.create(.5, element_order='quadratic',
        backend='quad_first', parameters=QUAD_PARAMETERS))
    reopened = project_from_dict(project_to_dict(project))
    mesh = reopened.generate_mesh()
    check_quadratic_bars(reopened, mesh)
    assert reopened.native_mesh_settings.element_order == 'quadratic'


def test_quadratic_corner_stations_match_the_linear_mesh_stations():
    # Quadratic admission must not change the canonical corner stations, which
    # are the shared source-edge seeding of shells and bars.
    project = small_project()
    kwargs = dict(strategy='quad_first', layout_policy='adaptive', quad_t3_angle_action='warning')
    linear = project.generate_mesh(.5, order='linear', **kwargs)
    quadratic = project.generate_mesh(.5, order='quadratic', **kwargs)
    for edge in opening_edges(project):
        assert len(linear.nodes_of_edge[edge]) == len(
            [n for n in quadratic.nodes_of_edge[edge]
             if n in {c for e in quadratic.shells for c in quadratic.corners_of(e)}])


def test_quadratic_override_refuses_custom_parameterization_without_publication():
    project = small_project()
    g = project.geometry
    face = next(f for f in g.faces.values() if f.holes)
    g.set_face_parameterization(face.id, face.surface)
    before = repr(g.topology_snapshot())
    with pytest.raises(QuadPublicUnsupported, match='source-boundary'):
        project.generate_mesh(.5, order='quadratic', strategy='quad_first')
    assert repr(g.topology_snapshot()) == before
    assert project.mesh_records == {}


def test_quadratic_override_refuses_custom_plane_without_publication():
    from anygeometry import Plane
    class CustomPlane(Plane):
        pass
    project = small_project()
    g = project.geometry
    face = next(f for f in g.faces.values() if f.holes)
    p = face.surface
    g.set_face_surface(face.id, CustomPlane(p.origin, p.u_vector, p.v_vector))
    before = repr(g.topology_snapshot())
    with pytest.raises(QuadPublicUnsupported, match='source-boundary'):
        project.generate_mesh(.5, order='quadratic', strategy='quad_first')
    assert repr(g.topology_snapshot()) == before
    assert project.mesh_records == {}


def test_quadratic_cancelled_mesh_does_not_publish_or_mutate():
    project = small_project()
    before = repr(project.geometry.topology_snapshot())
    revision = project.geometry.revision
    marker = RuntimeError('owned quadratic cancellation marker')
    def cancel(phase):
        raise marker
    with pytest.raises(RuntimeError) as caught:
        project.generate_mesh(.5, order='quadratic', strategy='quad_first',
            layout_policy='adaptive', cancellation_check=cancel)
    assert caught.value is marker
    assert project.geometry.revision == revision
    assert repr(project.geometry.topology_snapshot()) == before
    assert project.mesh_records == {}


def test_custom_parameterization_stays_refused():
    project = small_project()
    g = project.geometry
    face = next(f for f in g.faces.values() if f.holes)
    g.set_face_parameterization(face.id, face.surface)
    with pytest.raises(QuadPublicUnsupported, match='source-boundary'):
        project.generate_mesh(.5, order='linear', strategy='quad_first')


def test_custom_plane_support_stays_refused():
    from anygeometry import Plane
    class CustomPlane(Plane):
        pass
    project = small_project()
    g = project.geometry
    face = next(f for f in g.faces.values() if f.holes)
    p = face.surface
    g.set_face_surface(face.id, CustomPlane(p.origin, p.u_vector, p.v_vector))
    with pytest.raises(QuadPublicUnsupported, match='source-boundary'):
        project.generate_mesh(.5, order='linear', strategy='quad_first')


def test_cancelled_public_mesh_does_not_publish_or_mutate():
    project = small_project()
    before = repr(project.geometry.topology_snapshot())
    marker = RuntimeError('owned cancellation marker')
    def cancel(phase):
        raise marker
    with pytest.raises(RuntimeError) as caught:
        project.generate_mesh(.5, order='linear', strategy='quad_first', cancellation_check=cancel)
    assert caught.value is marker
    assert repr(project.geometry.topology_snapshot()) == before
    assert project.mesh_records == {}
