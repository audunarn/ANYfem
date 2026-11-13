"""Ordinary curved source charts connected along an authored common edge."""
from dataclasses import replace

import numpy as np
import pytest
from anygeometry import RuledSurface
from anygeometry.entities import OrientedEdge
from anyfem import Project, steel
from anyfem.mesh_controls import MeshControls
from anyfem.mesh_jobs import MeshSettings
from anyfem.native_meshing import NativeMeshSettings


def network():
    project = Project("curved adjacent plates")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", .01, "S355")
    g = project.geometry
    points = {}
    edges = {}
    faces = []
    for x0, z0, z1 in ((0., 0., .0625), (1., .0625, -.0625), (4., 0., .0625)):
        a, b, c, d = ((x0, 0., 0.), (x0+1, 0., 0.), (x0+1, 1., z1), (x0, 1., z0))
        loop = []
        for start, end in zip((a,b,c,d), (b,c,d,a)):
            for xyz in (start,end):
                if xyz not in points:
                    points[xyz] = g.add_point(*xyz)
            if (end,start) in edges:
                loop.append(OrientedEdge(edges[end,start], False))
            else:
                edge = edges[start,end] = g.add_line(points[start], points[end])
                loop.append(OrientedEdge(edge, True))
        face = g.add_face_from_loop(tuple(loop), corners=(0,1,2,3), surface=RuledSurface((a,b), (d,c)))
        faces.append(face)
    g.add_sheet(tuple(faces[:2]))
    g.add_sheet((faces[-1],))
    for face in faces:
        project.assign_plate(face, "plate")
    project.set_native_triangulation_backend("python")
    project.native_mesh_settings = NativeMeshSettings.create(.5, backend="mapped",
        parameters={"geometry_approximation_error": .02})
    settings = MeshSettings.create(.5, strategy="mapped", element_order="linear", controls=MeshControls())
    shared, = set(u.edge for u in g.faces[faces[0]].loop) & set(u.edge for u in g.faces[faces[1]].loop)
    return project, settings, faces, shared


def common_chart_network():
    from anygeometry.nurbs import CurveOnSurface, NurbsCurve, NurbsSurface
    project = Project("shared curved source chart")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", .01, "S355")
    project.add_plate_section("stiff", .012, "S355")
    g = project.geometry
    knot = (0.,0.,1.,1.)
    chart = NurbsSurface(1,1,knot,knot,(((0.,0.,0.),(0.,1.,0.)),((4.,0.,0.),(4.,1.,.125))))
    vertices, edges, faces = {}, {}, []
    for u in (0., .25, .75):
        trim = ((u,0.),(u+.25,0.),(u+.25,1.),(u,1.))
        loop = []
        for a,b in zip(trim, (*trim[1:],trim[0])):
            for uv in (a,b):
                if uv not in vertices:
                    vertices[uv] = g.add_point(*chart.evaluate(*uv))
            if (b,a) in edges:
                loop.append(OrientedEdge(edges[b,a], False))
            else:
                edge = edges[a,b] = g.add_curve(vertices[a],vertices[b],CurveOnSurface(chart,NurbsCurve(1,knot,(a,b))))
                loop.append(OrientedEdge(edge,True))
        face = g.add_face_from_loop(tuple(loop), corners=(0,1,2,3), surface=chart)
        g.update_face_metadata(face, material_assignment="stiff" if u == .25 else "plate")
        faces.append(face)
    g.add_sheet(tuple(faces[:2]))
    g.add_sheet((faces[-1],))
    for face, section in zip(faces, ("plate","stiff","plate")):
        project.assign_plate(face,section)
    project.set_native_triangulation_backend("python")
    seam = edges[(.25,0.),(.25,1.)]
    project.native_mesh_settings = NativeMeshSettings.create(1.5, backend="native",
        parameters={"geometry_approximation_error": .02})
    settings = MeshSettings.create(1.5, strategy="native", element_order="linear", overrides={seam: 2},
        controls=MeshControls(point_placement="frontal_delaunay", recombine=False))
    return project,settings,faces,seam


@pytest.fixture(scope="module", params=[network, common_chart_network], ids=["ruled","nurbs"])
def published(request, tmp_path_factory):
    project, settings, faces, seam = request.param()
    session = project.mesh_session()
    parent = session.generate_mesh(settings)
    assert set(parent.elements_of_face) == set(faces)
    assert project.mesh_records[session.mesh_record_id].summary["regional_baseline"]["source"]["components"] == [
        faces[:2], [faces[-1]]]
    path = session.save(tmp_path_factory.mktemp(request.param.__name__) / "network.anyfem")
    session.close()
    return path, settings, faces, seam


def _check_sides(mesh, faces, seam):
    stations = mesh.nodes_of_edge[seam]
    assert len(stations) >= 3
    for a, b in zip(stations, stations[1:]):
        sides = []
        for f in faces[:2]:
            hits = []
            for e in mesh.elements_of_face[f]:
                cell = mesh.shells[e]
                hits.extend((i,j) for i,j in zip(cell, cell[1:]+cell[:1]) if {i,j} == {a,b})
            side, = hits
            sides.append(side)
        assert sides[0] == sides[1][::-1]


def test_local_reseed_couples_component_and_requires_certified_physical_quality(published, tmp_path):
    from anyfem.io.project_file import load_project
    from anyfem.mesh_preview import mesh_update_preview
    from anyfem.solve import build_fe_model

    path, settings, faces, seam = published
    project = load_project(path)
    session = project.mesh_session()
    old, parent = session.mesh, session.mesh_record_id
    before = project.mesh_records[parent].summary["regional_baseline"]
    loop = project.geometry.faces[faces[0]].loop
    changed = (loop[0].edge, loop[2].edge)
    divisions = max(len(old.nodes_of_edge[e]) for e in changed) + (settings.strategy == "mapped")
    edited = replace(settings, overrides=tuple(sorted((*settings.overrides, *((e, divisions) for e in changed)))))
    plan = session.preview_update(edited)
    assert plan.regenerated == tuple(faces[:2]) and plan.retained == (faces[-1],)
    assert plan.impact["regenerated"] == [faces[:2]] and "Expand" in plan.description
    owners = mesh_update_preview(plan, before, old, parent).owners
    assert [(r.kind, r.id) for r in owners] == [("face",f) for f in faces[:2]]
    allocation = project.mesh_numbering
    if settings.strategy == "native":
        from anyfem.mesh_general_update import GeneralSurfaceUnsupported
        from anymesher.errors import StructuredQualityRejected

        _check_sides(old, faces, seam)
        assembled = build_fe_model(project, old, require_loads=False, require_supports=False)
        assert {e: tuple(item.node_ids) for e, item in assembled.fe_model.mesh.elements.items()} == old.shells
        with pytest.raises(GeneralSurfaceUnsupported, match="failed physical quality.*growth") as refused:
            session.update_mesh(edited)
        assert isinstance(refused.value.__cause__, StructuredQualityRejected)
        assert project.mesh_numbering == allocation and session.mesh_record_id == parent
        assert session.mesh.shells == old.shells and session.preview_update(settings).is_noop
        changed = sorted({edge.edge for face in faces[:2] for edge in project.geometry.faces[face].loop})
        divisions = 2
        edited = replace(settings, overrides=tuple((edge, divisions) for edge in changed))
        plan = session.preview_update(edited)
        assert plan.regenerated == tuple(faces[:2]) and plan.retained == (faces[-1],)
    child = session.update_mesh(edited)
    _check_sides(child, faces, seam)
    remote = old.elements_of_face[faces[-1]]
    assert child.elements_of_face[faces[-1]] == remote
    assert set(child.shells) & set(old.shells) == set(remote)
    keep_nodes = {n for e in remote for n in old.shells[e]}
    assert set(old.nodes) & set(child.nodes) == keep_nodes
    for e in remote:
        assert child.shells[e] == old.shells[e]
    for n in keep_nodes:
        np.testing.assert_array_equal(child.nodes[n], old.nodes[n])
    assert min(set(child.shells) - set(remote)) > allocation.element_high_water
    assert min(set(child.nodes) - keep_nodes) > allocation.node_high_water
    assert all(len(child.nodes_of_edge[edge]) == divisions + 1 for edge in changed)
    if settings.strategy == "mapped":
        remote_xyz = [np.array([child.nodes[n] for n in child.shells[e]]) for e in remote]
        assert min(max(np.linalg.norm(xyz - np.roll(xyz, 1, axis=0), axis=1)) for xyz in remote_xyz) > settings.target_size
    result = project.mesh_records[session.mesh_record_id].summary["regional_baseline"]["last_update"]
    assert result["impact"] == plan.impact and result["quality"]["min_scaled_jacobian"] > 0
    assert result["reused_source_faces"] == list(plan.reuse_candidates)
    assert result["shared_interfaces"][str(seam)] == {"faces": faces[:2], "stations": len(child.nodes_of_edge[seam]),
                                                       "segments": len(child.nodes_of_edge[seam])-1}
    assert not project.geometry.junctions
    built = build_fe_model(project, child, require_loads=False, require_supports=False)
    assert {e: tuple(item.node_ids) for e,item in built.fe_model.mesh.elements.items()} == child.shells
    if child.quads:
        for face in faces:
            sample = built.fe_model.mesh.elements[child.elements_of_face[face][0]]
            matrix = sample.compute_stiffness_matrix(built.fe_model.mesh, built.fe_model.get_material(sample.material_name))
            assert matrix.shape == (24,24) and np.isfinite(matrix).all()
    reopened = load_project(session.save(tmp_path/"updated.anyfem")).mesh_session()
    assert reopened.mesh.shells == child.shells
    assert reopened.preview_update(edited).is_noop
    _check_sides(reopened.mesh, faces, seam)
    if settings.strategy == "mapped":
        g = reopened.project.geometry
        remote_face = faces[-1]
        corners = [g.oriented_start_vertex(u) for u in g.faces[remote_face].loop]
        a, b, c, d = (tuple(g.vertex_position(v)) for v in corners)
        c = (c[0], c[1], 0.)
        g.move_point(corners[2], *c)
        g.set_face_surface(remote_face, RuledSurface((a,b), (d,c)))
        next_plan = reopened.preview_update(edited)
        assert next_plan.retained == tuple(faces[:2]) and next_plan.regenerated == (remote_face,)
        current = reopened.update_mesh(edited)
        assert current.nodes_of_edge[seam] == child.nodes_of_edge[seam]
        for f in faces[:2]:
            assert current.elements_of_face[f] == child.elements_of_face[f]
            for e in current.elements_of_face[f]:
                assert current.shells[e] == child.shells[e]
                for n in current.shells[e]:
                    np.testing.assert_array_equal(current.nodes[n], child.nodes[n])


