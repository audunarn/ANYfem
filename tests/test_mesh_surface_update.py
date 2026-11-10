"""Application/source to authentic planar kernel, without injected geometry or mesh."""
import json

import numpy as np
import pytest

from anygeometry.entities import OrientedEdge
from anygeometry.operations import trim_face
from anymesher.refinement import Refinement
from anymesher.serialize import mesh_to_dict
from anyfem import Project, steel
from anyfem.mesh_jobs import MeshSettings
from anyfem.mesh_session import ProjectMeshSession
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.mesh_surface_update import (
    SurfaceBoundaryMismatch, SurfaceUpdateError, capture_surface_baseline,
    explain_surface_ineligibility, generate_surface_update, plan_surface_update,
)


def plate(*, prepared=True):
    p = Project("surface patch")
    p.add_material(steel("S355", 0.01))
    p.add_plate_section("plate", thickness=0.01, material="S355")
    g = p.geometry
    corners = g.add_points([(0, 0, 0), (8, 0, 0), (8, 8, 0), (0, 8, 0)])
    face = g.add_face(g.add_polyline(corners, close=True))
    p.assign_plate(face, "plate")
    settings = MeshSettings.create(1., strategy="mapped", element_order="linear", quality_policy={})
    if prepared:
        mesh = p.generate_mesh(1., strategy="mapped", order="linear", quality_policy={})
    else:
        # The same source-owned mapped path used for independent components.
        from anyfem.regional_mesh import _generate_component
        mesh = _generate_component(p, settings, {"faces": [face], "local": {"overrides": []}})
    baseline = capture_surface_baseline(p, settings, mesh)
    assert baseline is not None, explain_surface_ineligibility(p, settings, mesh)
    return p, face, settings, mesh, json.loads(json.dumps(baseline))


def patch(project, settings, baseline, old):
    return generate_surface_update(project, settings, baseline, mesh_to_dict(old),
                                   node_high_water=1000, element_high_water=2000)


def assert_exterior(old, result):
    assert result.retained_elements and result.retained_nodes
    for e in result.retained_elements:
        assert tuple(old.quads[e]) == tuple(result.mesh.quads[e])
        for n in old.quads[e]:
            assert np.array_equal(old.nodes[n], result.mesh.nodes[n])
    assert min(set(result.mesh.quads) - set(old.quads)) > 2000
    assert min(set(result.mesh.nodes) - set(old.nodes)) > 1000
    assert not result.provenance["kernel_injected"]


@pytest.mark.parametrize("prepared", [False, True], ids=["mapped-component", "full-project"])
def test_polygon_hole_changes_area_and_preserves_exterior_after_reopen(prepared):
    project, face, settings, old, baseline = plate(prepared=prepared)
    g = project.geometry
    corners = g.add_points([(3, 3, 0), (3, 5, 0), (5, 5, 0), (5, 3, 0)])
    edges = g.add_polyline(corners, close=True)
    trim_face(g, face, [tuple(OrientedEdge(e, True) for e in edges)])
    project = project_from_dict(json.loads(json.dumps(project_to_dict(project))))
    g = project.geometry
    plan = plan_surface_update(project, settings, baseline)
    assert plan.applicable, plan.description
    original = mesh_to_dict(old)
    result = patch(project, settings, baseline, old)
    assert mesh_to_dict(old) == original
    assert_exterior(old, result)
    area = 0.
    for connectivity in result.mesh.quads.values():
        points = np.asarray([result.mesh.nodes[n][:2] for n in connectivity[:4]])
        x, y = points.T
        area += abs(x @ np.roll(y, -1) - y @ np.roll(x, -1)) / 2
        centre = points.mean(axis=0)
        assert not all(3 < value < 5 for value in centre)
    assert area == pytest.approx(60.)
    assert all(len(result.mesh.nodes_of_edge[e]) == 3 for e in edges)
    assert all(len(result.mesh.elements_of_edge[e]) == 2 for e in edges)
    assert str(result.mesh.geometry_model_id) == str(g.model_id)
    assert result.mesh.geometry_revision == g.revision
    assert plan_surface_update(project, settings, json.loads(json.dumps(result.baseline))).is_noop


def test_local_size_change_uses_owner_and_keeps_exterior():
    project, _, settings, old, baseline = plate()
    project.add_refinement(Refinement(0.5, center=(4, 4, 0), radius=0.35, growth=2.0))
    result = patch(project, settings, baseline, old)
    assert_exterior(old, result)
    assert len(result.mesh.quads) > len(old.quads)
    assert plan_surface_update(project, settings, result.baseline).is_noop


def test_refinement_reaching_boundary_and_wrong_old_mesh_fail_closed():
    project, _, settings, old, baseline = plate()
    project.add_refinement(Refinement(0.5, center=(0.1, 1., 0.), radius=0.5))
    with pytest.raises(SurfaceBoundaryMismatch):
        patch(project, settings, baseline, old)
    project.refinements.clear()
    project.add_refinement(Refinement(0.5, center=(4., 4., 0.), radius=0.35))
    old.nodes[min(old.nodes)] = np.array([99., 0., 0.])
    with pytest.raises(SurfaceUpdateError, match="does not match the baseline"):
        patch(project, settings, baseline, old)



def test_cancel_prevents_production_and_keeps_numbered_parent():
    project, _, settings, old, baseline = plate(prepared=False)
    project.add_refinement(Refinement(0.5, center=(4., 4., 0.), radius=0.35))
    original = mesh_to_dict(old)

    def cancel(stage):
        if stage == "surface remesh region":
            raise InterruptedError(stage)

    with pytest.raises(InterruptedError, match="surface remesh region"):
        generate_surface_update(project, settings, baseline, old, node_high_water=1000,
                                element_high_water=2000, cancellation_check=cancel)
    assert mesh_to_dict(old) == original


@pytest.mark.parametrize("same_stations", [False, True], ids=["station-change", "identical-owner-stations"])
def test_saved_spline_sizing_edit_with_identical_exact_bounds(tmp_path, same_stations):
    from anyfem.io.project_file import load_project

    project, _, settings, _, _ = plate(prepared=False)
    geometry = project.geometry
    if same_stations:
        controls = [(3.9, 3.9, 0.), (3.93, 4.01, 0.), (4.05, 4.05, 0.)]
        moved_control = (4.03, 3.94, 0.)
    else:
        controls = [(3.65, 3.8, 0.), (3.8, 4.1, 0.), (4.35, 4.2, 0.)]
        moved_control = (4.2, 3.9, 0.)
    a, control, b = geometry.add_points(controls)
    edge = geometry.add_spline(a, [control], b)
    session = ProjectMeshSession(project)
    session.generate_mesh(settings)
    path = session.save(tmp_path / "sizing-spline.anyfem")
    project.add_refinement(Refinement(.5, ref=project.edge(edge), radius=.1, growth=2.))
    assert f"sizing edge {edge} added" in session.preview_update(settings).description
    first = session.update_mesh(settings)
    session.save()
    session = ProjectMeshSession(load_project(path), path)
    geometry = session.project.geometry
    original = geometry.sample_edge(edge, np.linspace(0., 1., 101))
    first_source = session._baseline()["source"]["refinements"][0]["source"]
    # Both control polygons are coordinatewise monotone. The endpoints are
    # consequently the exact minima and maxima of each Bezier curve.
    geometry.move_point(control, *moved_control)
    moved = geometry.sample_edge(edge, np.linspace(0., 1., 101))
    for samples in (original, moved):
        np.testing.assert_array_equal(samples.min(axis=0), samples[0])
        np.testing.assert_array_equal(samples.max(axis=0), samples[-1])
    np.testing.assert_array_equal(original[[0, -1]], moved[[0, -1]])
    assert not np.array_equal(original, moved)
    preview = session.preview_update(settings)
    assert f"sizing edge {edge} removed" in preview.description and f"sizing edge {edge} added" in preview.description
    child = session.update_mesh(settings)
    current = session._baseline()["source"]["refinements"][0]["source"]
    assert (current["stations"] == first_source["stations"]) is same_stations
    assert current["vertices"] != first_source["vertices"]
    samples = session.project.refinements[0].sources(geometry)
    assert [float(v).hex() for v in samples.ravel()] == current["stations"]
    retained = child.quads.keys() & first.quads.keys()
    assert retained and set(child.quads) - set(first.quads) and set(first.quads) - set(child.quads)
    for eid in retained:
        assert child.quads[eid] == first.quads[eid]
        for node in first.quads[eid]:
            np.testing.assert_array_equal(first.nodes[node], child.nodes[node])
    assert edge not in child.nodes_of_edge and control not in child.node_of_vertex
    fine_corners = []
    for e in child.quads.keys() - retained:
        points = np.array([child.nodes[n] for n in child.quads[e]])
        if max(np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)) <= .5:
            fine_corners.extend(points)
    # A fine cell with one corner strictly inside a physical influence disc
    # has positive overlap even when its centroid lies outside the disc.
    assert fine_corners and all(min(np.linalg.norm(p - sample) for p in fine_corners) < .6 for sample in samples)
    session.save()
    restored = ProjectMeshSession(load_project(path), path)
    assert restored.preview_update(settings).is_noop
    assert restored.mesh.quads == child.quads


def test_constrained_spline_source_refuses_and_preserves_the_parent():
    from anyfem import fixed

    p, _, settings, _, _ = plate(prepared=False)
    a, c, b = p.geometry.add_points([(3., 3., 0.), (4., 3.5, 0.), (4.5, 4., 0.)])
    edge = p.geometry.add_spline(a, [c], b)
    session = ProjectMeshSession(p)
    session.generate_mesh(settings)
    parent = session.mesh_record_id
    p.add_support(fixed(p.edge(edge)))
    p.add_refinement(Refinement(.5, ref=p.edge(edge), radius=.1, growth=2.))
    with pytest.raises(SurfaceUpdateError, match="unassociated"):
        session.update_mesh(settings)
    assert session.mesh_record_id == parent
