"""Ordinary authored panels through the same generation route as the mesh session."""
from dataclasses import replace

import numpy as np
import pytest
from anygeometry import CoonsSurface, RuledSurface
from anygeometry.entities import OrientedEdge
from anygeometry.extrusions import BezierDirectrix
from anygeometry.nurbs import CurveOnSurface, NurbsCurve, NurbsSurface
from anygeometry.surfaces import ExtrudedSurface

from anyfem import Project, steel
from anyfem.mesh_controls import MeshControls
from anyfem.mesh_jobs import MeshSettings
from anyfem.native_meshing import NativeMeshSettings


def panel(family, *, second=False, rise=.125):
    project = Project("ordinary authored panel")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", .01, "S355")
    g = project.geometry
    corners = ((0., 0., 0.), (1., 0., 0.), (1., 1., rise), (0., 1., 0.))
    uv = ((0., 0.), (1., 0.), (1., 1.), (0., 1.))
    if family == "extruded":
        controls = ((0., 0., 0.), (.5, 0., .0625), (1., 0., 0.))
        top = tuple((x, y + 1., z) for x, y, z in controls)
        lower_ids, upper_ids = g.add_points(controls), g.add_points(top)
        edges = (g.add_spline(lower_ids[0], lower_ids[1:-1], lower_ids[-1]),
                 g.add_line(lower_ids[-1], upper_ids[-1]),
                 g.add_spline(upper_ids[-1], upper_ids[1:-1], upper_ids[0]),
                 g.add_line(upper_ids[0], lower_ids[0]))
        support = ExtrudedSurface(BezierDirectrix(controls), (0., 1., 0.))
    elif family == "nurbs":
        knots = (0., 0., 1., 1.)
        support = NurbsSurface(1, 1, knots, knots, ((corners[0], corners[3]), (corners[1], corners[2])))
        pts = g.add_points(corners)
        edges = [g.add_curve(pts[i], pts[(i+1)%4], CurveOnSurface(support, NurbsCurve(1, knots,
                 (point, uv[(i+1)%4])))) for i, point in enumerate(uv)]
    else:
        a, b, c, d = corners
        support = (RuledSurface((a, b), (d, c)) if family == "ruled" else
                   CoonsSurface((a, b), (b, c), (d, c), (a, d)))
        edges = g.add_polyline(g.add_points(corners), close=True)
    face = g.add_face_from_loop(tuple(OrientedEdge(e, True) for e in edges), corners=(0, 1, 2, 3), surface=support)
    g.add_sheet((face,))
    project.assign_plate(face, "plate")
    if second:
        pts = g.add_points(((4, 0, 2), (5, 0, 2), (5, 1, 2), (4, 1, 2)))
        knots = (0., 0., 1., 1.)
        flat = NurbsSurface(1, 1, knots, knots, (((4.,0.,2.),(4.,1.,2.)),((5.,0.,2.),(5.,1.,2.))))
        ring = [g.add_curve(pts[i], pts[(i+1)%4], CurveOnSurface(flat, NurbsCurve(1, knots,
                (point, uv[(i+1)%4])))) for i, point in enumerate(uv)]
        side = g.add_face(ring, surface=flat)
        g.add_sheet((side,))
        project.assign_plate(side, "plate")
    mapped = family == "extruded"
    project.set_native_triangulation_backend("python")
    project.native_mesh_settings = NativeMeshSettings.create(.5, backend="native",
        parameters={"geometry_approximation_error": .02, "recombine": mapped})
    settings = MeshSettings.create(.5, strategy="mapped" if mapped else "native", element_order="linear",
        controls=MeshControls(recombine=mapped, point_placement="legacy_lattice" if mapped else "frontal_delaunay"))
    return project, settings, face, tuple(edges)


@pytest.mark.parametrize("family", ["ruled", "extruded", "nurbs", "coons"])
def test_generation_surface_owner(family):
    project, settings, face, edges = panel(family)
    mesh = project.mesh_session().generate_mesh(settings)
    assert set(mesh.elements_of_face) == {face}
    from anyfem.mesh_general_update import _capture, generate_general_update
    baseline = _capture(project, settings, mesh)
    edited = replace(settings, target_size=.21 if family == "extruded" else .35)
    result = generate_general_update(project, edited, baseline, mesh, node_high_water=max(mesh.nodes),
                                     element_high_water=max(mesh.shells))
    assert result.plan.regenerated == (face,) and not result.retained_nodes and not result.retained_elements
    assert len(result.mesh.shells) > len(mesh.shells)
    if family == "extruded":
        independent, other_settings, _, _ = panel(family)
        direct = independent.mesh_session().generate_mesh(replace(other_settings, target_size=edited.target_size))
        direct_nodes, updated_nodes = sorted(direct.nodes), sorted(result.mesh.nodes)
        assert len(direct_nodes) == len(updated_nodes)
        node_map = dict(zip(direct_nodes, updated_nodes, strict=True))
        for n in direct_nodes:
            np.testing.assert_array_equal(direct.nodes[n], result.mesh.nodes[node_map[n]])
        assert {e: [node_map[n] for n in ids] for e, ids in direct.nodes_of_edge.items()} == result.mesh.nodes_of_edge
        assert [tuple(node_map[n] for n in direct.shells[e]) for e in direct.elements_of_face[face]] == [
            result.mesh.shells[e] for e in result.mesh.elements_of_face[face]]
    assert result.provenance["quality"]["num_shell_elements"] == len(result.mesh.shells)


def test_complete_regeneration_uses_disconnected_owners():
    from anyfem.mesh_general_update import _inputs, _mesh_owners
    project, settings, face, _ = panel("nurbs", second=True)
    mesh = project.mesh_session().generate_mesh(settings)
    records, source = _inputs(project, settings)
    assert len(source["faces"]) == 2
    _mesh_owners(mesh, records)
    assert mesh.tris and set(mesh.elements_of_face) == set(project.geometry.faces)




def test_single_source_update_uses_real_request_and_fresh_numbers():
    from anyfem.mesh_general_update import SCHEMA
    project, settings, face, _ = panel("nurbs")
    session = project.mesh_session()
    old = session.generate_mesh(settings)
    before = project.mesh_records[session.mesh_record_id].summary["regional_baseline"]
    assert before["schema"] == SCHEMA
    edited = replace(settings, target_size=.30)
    plan = session.preview_update(edited)
    assert plan.regenerated == (face,) and "full source mesh" in plan.description
    new = session.update_mesh(edited)
    assert max(old.nodes) < min(new.nodes) and max(old.shells) < min(new.shells)
    assert len(new.shells) > len(old.shells)
    assert session.preview_update(edited).is_noop


def test_unproven_parent_and_cancel_do_not_allocate_numbers():
    from anyfem.mesh_general_update import GeneralSurfaceUnsupported, generate_general_update
    from anymesher.serialize import mesh_from_dict, mesh_to_dict
    project, settings, _, _ = panel("nurbs")
    session = project.mesh_session()
    old = session.generate_mesh(settings)
    baseline = project.mesh_records[session.mesh_record_id].summary["regional_baseline"]
    state = project.mesh_numbering
    copied = mesh_from_dict(mesh_to_dict(old))
    clone = mesh_from_dict(mesh_to_dict(old))
    clone.nodes[next(iter(clone.nodes))][2] += .01
    edited = replace(settings, target_size=.3)
    kwargs = dict(node_high_water=state.node_high_water, element_high_water=state.element_high_water)
    with pytest.raises(GeneralSurfaceUnsupported, match="Parent content"):
        generate_general_update(project, edited, baseline, clone, **kwargs)
    with pytest.raises(KeyboardInterrupt):
        generate_general_update(project, edited, baseline, copied,
            cancellation_check=lambda stage: (_ for _ in ()).throw(KeyboardInterrupt()), **kwargs)
    assert project.mesh_numbering == state and session.mesh is old



def test_changed_bilinear_control_with_same_bounds_regenerates_whole_face():
    project, settings, face, edges = panel("coons")
    session = project.mesh_session()
    old = session.generate_mesh(settings)
    g = project.geometry
    a, b, c, d = (tuple(g.vertex_position(g.edges[e].start)) for e in edges)
    moved = (d[0], d[1], .0625)
    g.move_point(g.edges[edges[3]].start, *moved)
    g.set_face_surface(face, CoonsSurface((a, b), (b, c), (moved, c), (a, moved)))
    assert np.array_equal(np.ptp([a,b,c,d], axis=0), np.ptp([a,b,c,moved], axis=0))
    plan = session.preview_update(settings)
    assert plan.regenerated == (face,)
    new = session.update_mesh(settings)
    assert set(new.nodes).isdisjoint(old.nodes)
    vertex = g.edges[edges[3]].start
    np.testing.assert_array_equal(new.nodes[new.node_of_vertex[vertex]], moved)



def test_exact_nurbs_inner_rim_update_tracks_real_material_cells():
    from anygeometry.operations import trim_face
    project, settings, face, _ = panel("nurbs")
    session = project.mesh_session()
    old = session.generate_mesh(settings)
    geometry = project.geometry
    support = geometry.faces[face].surface
    hole = ((.25, .25), (.25, .75), (.75, .75), (.75, .25))
    knots = (0., 0., 1., 1.)
    vertices = geometry.add_points([support.evaluate(*p) for p in hole])
    ring = [geometry.add_curve(vertices[i], vertices[(i+1)%4], CurveOnSurface(support,
            NurbsCurve(1, knots, (p, hole[(i+1)%4])))) for i, p in enumerate(hole)]
    trim_face(geometry, face, [tuple(OrientedEdge(e, True) for e in ring)])
    plan = session.preview_update(settings)
    assert plan.regenerated == (face,)
    new = session.update_mesh(settings)
    assert set(old.nodes).isdisjoint(new.nodes)
    assert all(new.nodes_of_edge[e] for e in ring)
    for element in new.elements_of_face[face]:
        # x/y give the exact parameters for this particular test surface.
        uv = np.mean([new.nodes[n][:2] for n in new.shells[element]], axis=0)
        assert not (.25 < uv[0] < .75 and .25 < uv[1] < .75)





def test_extrusion_nominal_size_matches_independent_full_generation():
    from anyfem.mesh_general_update import _capture, generate_general_update
    project, settings, face, edges = panel("extruded")
    session = project.mesh_session()
    old = session.generate_mesh(settings)
    baseline = _capture(project, settings, old)
    edited = replace(settings, target_size=.35)
    new = generate_general_update(project, edited, baseline, old, node_high_water=max(old.nodes),
                                  element_high_water=max(old.shells)).mesh
    independent, _, _, _ = panel("extruded")
    direct = independent.mesh_session().generate_mesh(edited)
    print("segments per source edge (old, update, independent full)", [
        (len(old.nodes_of_edge[e])-1, len(new.nodes_of_edge[e])-1, len(direct.nodes_of_edge[e])-1) for e in edges])
    for edge in edges:
        np.testing.assert_array_equal([new.nodes[n] for n in new.nodes_of_edge[edge]],
                                      [direct.nodes[n] for n in direct.nodes_of_edge[edge]])
    assert len(new.elements_of_face[face]) == len(direct.elements_of_face[face])
    for a, b in zip(new.elements_of_face[face], direct.elements_of_face[face], strict=True):
        np.testing.assert_array_equal([new.nodes[n] for n in new.shells[a]], [direct.nodes[n] for n in direct.shells[b]])
