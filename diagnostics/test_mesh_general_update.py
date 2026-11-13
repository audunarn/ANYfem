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












def test_one_edited_source_retains_exact_disconnected_plate(tmp_path):
    from anyfem.io.project_file import load_project

    project, settings, retained, _ = panel("nurbs", second=True)
    session = project.mesh_session()
    old = session.generate_mesh(settings)
    path = session.save(tmp_path / "ordinary.anyfem")
    session.close()
    reopened = load_project(path)
    session = reopened.mesh_session(path)
    from anyfem.model.regions import ElementFaceRef, ManualRegion, Region, RegionRef
    element = old.elements_of_face[retained][0]
    parent = session.mesh_record_id
    region = Region("retained pressure", "mesh", "element",
                    ManualRegion((ElementFaceRef(parent, element, 0),)), mesh_id=parent)
    reopened.regions.add(region)
    reopened.load_case().add_pressure(reopened.face(retained), 20., region=RegionRef(region.id))
    changed, = set(reopened.geometry.faces) - {retained}
    edge = reopened.geometry.faces[changed].loop[0].edge
    edited = replace(settings, overrides=((edge, 4),))
    plan = session.preview_update(edited)
    assert plan.retained == (retained,) and plan.regenerated == (changed,)
    assert plan.reuse_candidates == (retained,)
    from anymesher import regional_source_faces as reuse_owner
    from unittest.mock import patch
    original = reuse_owner.retained_core
    bypassed = []

    def observe(geometry, face, *args, **kwargs):
        core = original(geometry, face, *args, **kwargs)
        if core is not None:
            bypassed.append(face)
        return core

    with patch.object(reuse_owner, "retained_core", observe):
        new = session.update_mesh(edited)
    assert bypassed == [retained]
    advanced = reopened.regions[region.id]
    assert advanced.mesh_id == session.mesh_record_id
    assert advanced.definition.anchors == (ElementFaceRef(session.mesh_record_id, element, 0),)
    assert session.preview_update(edited).is_noop
    assert len(new.nodes_of_edge[edge]) == 5
    assert new.elements_of_face[retained] == old.elements_of_face[retained]
    for elem in new.elements_of_face[retained]:
        assert new.shells[elem] == old.shells[elem]
        for node in old.shells[elem]:
            np.testing.assert_array_equal(old.nodes[node], new.nodes[node])
    assert set(new.elements_of_face[changed]).isdisjoint(old.shells)
    evidence = reopened.mesh_records[session.mesh_record_id].summary["regional_baseline"]["last_update"]
    assert evidence["reused_source_faces"] == [retained]
    assert evidence["generated_faces"] == [changed]











def test_bezier_automatic_approximation_publishes_regional_child_after_reopen(tmp_path):
    from anyfem.io.project_file import load_project

    project, _, face, _ = panel("extruded")
    project.native_mesh_settings = None
    session = project.mesh_session()
    initial = MeshSettings.create(.25, strategy="auto", element_order="linear")
    old = session.generate_mesh(initial)
    assert old.hybrid_diagnostics["geometry_approximation"]["mode"] == "automatic"
    path = session.save(tmp_path / "bezier.anyfem")

    session = load_project(path).mesh_session()
    requested = replace(initial, target_size=.20)
    assert session.preview_update(requested).regenerated == (face,)
    child = session.update_mesh(requested)
    assert child.hybrid_diagnostics["geometry_approximation"]["mode"] == "automatic"
    assert set(child.elements_of_face[face]) == set(child.shells)
    assert min(child.shells) > max(old.shells)
    assert session.preview_update(requested).is_noop
    session.save()
    assert load_project(path).mesh_session().mesh.shells == child.shells
