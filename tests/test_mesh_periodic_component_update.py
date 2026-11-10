"""Complete cylindrical shells have a shared source edge at every meridian."""
import math

import pytest

from anyfem import Project, steel
from anyfem.mesh_jobs import MeshSettings
from anygeometry import Cylinder, CylinderAtlasStatus, OrientedEdge, query_cylinder_atlas
from anymesher.quad.options import QuadMeshingOptions


def ring_project(sector_count=8, *, total_sectors=8):
    project = Project("Cylindrical pressure shell")
    project.add_material(steel("S355", .01))
    project.add_plate_section("shell", .01, "S355")
    project.set_native_triangulation_backend("python")
    g = project.geometry
    with g.transaction():
        rings = []
        for z in (0., 2.):
            ring = []
            for i in range(2*total_sectors):
                angle = i * math.pi / total_sectors
                ring.append(g.add_point(math.cos(angle), math.sin(angle), z))
            rings.append(ring)
        bottom, top = rings
        seams = [g.add_line(bottom[2*i], top[2*i]) for i in range(total_sectors)]
        faces = []
        for i in range(sector_count):
            j = (i + 1) % total_sectors
            lower_arc = g.add_arc(bottom[2*i], bottom[2*i+1], bottom[2*j])
            upper_arc = g.add_arc(top[2*i], top[2*i+1], top[2*j])
            loop = [OrientedEdge(lower_arc, True), OrientedEdge(seams[j], True),
                    OrientedEdge(upper_arc, False), OrientedEdge(seams[i], False)]
            cylinder = Cylinder((0., 0., 0.), (0., 0., 1.), (1., 0., 0.), 1., 2.,
                                start_angle=i * math.tau / total_sectors, sweep_angle=math.tau / total_sectors)
            faces.append(g.add_face_from_loop(loop, surface=cylinder))
        part = g.add_part(name="Shell")
        sheet = g.add_sheet(faces, part_id=part)
    for face in faces:
        project.assign_plate(face, "shell")
    uses = tuple(g.handle("face_use", u) for u in g.sheets[sheet].face_use_ids)
    if sector_count == total_sectors:
        atlas = query_cylinder_atlas(g, uses, reference_face_use=uses[0], expected_revision=g.revision)
        assert atlas.status is CylinderAtlasStatus.QUALIFIED and atlas.certificate.complete
    return project, tuple(faces), tuple(seams)


def ring_settings(order, size=.5):
    return MeshSettings.create(size, strategy="quad_first", quad_options=QuadMeshingOptions(), element_order=order)


@pytest.mark.parametrize("order", ("linear", "quadratic"))
def test_full_component_source_and_corrupt_seam(order):
    from anyfem.document import DocumentSession
    from anyfem.mesh_jobs import _cancellation_token, run_mesh_job
    from anyfem.mesh_periodic_component_update import _capture, PeriodicUpdateUnsupported
    from anymesher.serialize import mesh_from_dict, mesh_to_dict
    project, _, seams = ring_project()
    settings = ring_settings(order)
    snapshot = DocumentSession(project).snapshot()
    detached = snapshot.thaw()
    full = run_mesh_job(detached, snapshot.revision.model_hash, settings, lambda *_: None, _cancellation_token()).mesh
    baseline = _capture(detached, settings, full, lambda _: None)
    assert baseline["coverage"]["seam"] in seams and baseline["coverage"]["interfaces"] == 8
    bad = mesh_from_dict(mesh_to_dict(full))
    chain = bad.nodes_of_edge[seams[0]]
    assert len(chain) >= 4
    chain[1], chain[2] = chain[2], chain[1]
    with pytest.raises(PeriodicUpdateUnsupported, match="unordered|corner/midside"):
        _capture(detached, settings, bad, lambda _: None)


@pytest.mark.parametrize("change", ("orientation", "namespace"))
def test_changed_working_certificate_refuses(change):
    from anyfem.document import DocumentSession
    from anyfem.mesh_jobs import _cancellation_token, run_mesh_job
    from anyfem.mesh_periodic_component_update import _capture, PeriodicUpdateUnsupported
    project, _, _ = ring_project()
    snapshot = DocumentSession(project).snapshot()
    detached = snapshot.thaw()
    settings = ring_settings("quadratic")
    mesh = run_mesh_job(detached, snapshot.revision.model_hash, settings, lambda *_: None, _cancellation_token()).mesh
    if change == "namespace":
        mesh.hybrid_diagnostics["high_order_geometry"]["model_id"] = str(detached.geometry.model_id)
        with pytest.raises(PeriodicUpdateUnsupported, match="bound to its actual prepared geometry"):
            _capture(detached, settings, mesh, lambda _: None)
    else:
        # Reverse every shell and preserve its own midside orientation. This
        # remains a closed, on-surface tiling and must fail the FaceUse check.
        for family in (mesh.quads, mesh.tris):
            for e, conn in tuple(family.items()):
                k = len(conn)//2
                family[e] = (conn[0], *conn[k-1:0:-1], *conn[:k-1:-1])
        with pytest.raises(PeriodicUpdateUnsupported, match="contradicts its source FaceUse"):
            _capture(detached, settings, mesh, lambda _: None)

@pytest.mark.parametrize("order", ("linear", "quadratic"))
def test_public_closed_ring_density_update_allocates_new_permanent_ids(order, tmp_path):
    import numpy as np
    from anyfem.mesh_periodic_component_update import SCHEMA
    project, faces, seams = ring_project()
    from anyfem.native_meshing import NativeMeshSettings
    project.set_native_mesh_settings(NativeMeshSettings.create(.5, backend="native", element_order=order))
    session = project.mesh_session()
    parent = session.generate_mesh(ring_settings(order))
    baseline = project.mesh_records[session.mesh_record_id].summary["regional_baseline"]
    assert baseline["schema"] == SCHEMA
    from anyfem.io.project_file import load_project
    path = session.save(tmp_path / "cylinder.anyfem")
    session.close()
    project = load_project(path)
    session = project.mesh_session(path)
    from dataclasses import replace
    project.set_native_mesh_settings(replace(project.native_mesh_settings, target_size=.39))
    project.target_size = .39
    edited = ring_settings(order, .39)
    plan = session.preview_update(edited)
    assert plan.impact["regenerated"] == [list(faces)] and "periodic" in plan.description
    child = session.update_mesh(edited)
    assert len(child.shells) > len(parent.shells)
    assert max(parent.shells) < min(child.shells) and max(parent.nodes) < min(child.nodes)
    assert session.preview_update(edited).is_noop
    for seam in seams:
        chain = child.nodes_of_edge[seam]
        for face in project.geometry.faces_using_edge(seam):
            owned = {n for e in child.elements_of_face[face] for n in child.shells[e]}
            assert set(chain) <= owned
        # Source axial chains in this model run from z=0 to z=2.
        assert all(child.nodes[b][2] > child.nodes[a][2] for a,b in zip(chain, chain[1:]))
    xyz = np.array(list(child.nodes.values()))
    np.testing.assert_allclose(np.linalg.norm(xyz[:,:2], axis=1), 1., atol=1e-10, rtol=0.)
    from anyfem.solve import build_fe_model
    built = build_fe_model(project, child, require_loads=False, require_supports=False)
    assert set(built.fe_model.mesh.elements) == set(child.shells)
    assert all(tuple(built.fe_model.mesh.elements[e].node_ids) == tuple(conn) for e,conn in child.shells.items())
    print("Actual solver element constructors", sorted({type(e).__name__ for e in built.fe_model.mesh.elements.values()}))
    if order == "quadratic":
        for width in (6, 8):
            sample = next(e for e in built.fe_model.mesh.elements.values() if len(e.node_ids) == width)
            material = built.fe_model.get_material(sample.material_name)
            local_matrix = sample.compute_stiffness_matrix(built.fe_model.mesh, material)
            assert np.isfinite(local_matrix).all() and local_matrix.shape == (width*6, width*6)
        report = child.hybrid_diagnostics["high_order_geometry"]
        coverage = project.mesh_records[session.mesh_record_id].summary["regional_baseline"]["coverage"]
        assert report["model_id"] == coverage["working_model"] and report["revision"] == coverage["working_revision"]
        assert coverage["source_model"] == str(project.geometry.model_id) and coverage["source_revision"] == project.geometry.revision
        assert report["status"] == "CERTIFIED_POSITIVE" and report["q8_count"] == len(child.quads)


def test_open_chain_of_seven_sectors_has_no_periodic_authority():
    from anyfem.mesh_periodic_component_update import _source, PeriodicUpdateUnsupported, periodic_ring_family
    project, _, _ = ring_project(sector_count=7)
    assert not periodic_ring_family(project)
    with pytest.raises(PeriodicUpdateUnsupported, match="closed shared-edge cycle"):
        _source(project, ring_settings("linear"), lambda _: None)


def test_four_sector_complete_component_remeshes():
    from anyfem.mesh_periodic_component_update import SCHEMA, periodic_ring_family
    project, faces, seams = ring_project(4, total_sectors=4)
    assert periodic_ring_family(project)
    session = project.mesh_session()
    before = session.generate_mesh(ring_settings("linear"))
    edited = ring_settings("linear", .39)
    assert session.preview_update(edited).impact["regenerated"] == [list(faces)]
    after = session.update_mesh(edited)
    assert max(before.nodes) < min(after.nodes) and max(before.shells) < min(after.shells)
    baseline = project.mesh_records[session.mesh_record_id].summary["regional_baseline"]
    assert baseline["schema"] == SCHEMA and baseline["coverage"]["interfaces"] == 4
    assert baseline["coverage"]["seam"] in seams and session.preview_update(edited).is_noop
