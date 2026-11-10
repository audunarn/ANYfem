from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem import mesh_source_chart_update as msc
from anygeometry import Cone, CoonsSurface, Cylinder, ExtrudedSurface, Plane, RuledSurface


def _project(surface):
    return SimpleNamespace(geometry=SimpleNamespace(faces={1: SimpleNamespace(surface=surface)}))


def _chart(sweep=math.pi / 3, start=0.3, r0=1.0, r1=0.7, height=1.0):
    surface = Cone(np.zeros(3), (0, 0, 1), (1, 0, 0), r0, r1, height, start, sweep)
    return msc._Cone(msc._support_record(surface), 1.0e-9)


# ---------------------------------------------------------- family capability / holds


def test_every_named_family_has_an_exact_recorded_capability_or_hold():
    ruled = RuledSurface(np.array([[0, 0, 0], [1, 0, 0.0]]), np.array([[0, 1, 0], [1, 1, 0.0]]))
    for surface, word in ((ruled, "ruled surface held"), (CoonsSurface(), "Coons surface held")):
        hold = msc.source_chart_hold(_project(surface))
        assert word in hold
        plan = msc.plan_source_chart_update(_project(surface), None, None)
        assert plan.applicable is False and word in plan.description
        with pytest.raises(msc.SourceChartUnsupported):
            msc.generate_source_chart_update(_project(surface), None, None, None,
                                             node_high_water=0, element_high_water=0)
    assert "extruded surface held" in msc.FAMILY_HOLDS["ExtrudedSurface"]
    assert "NURBS source chart held" in msc.FAMILY_HOLDS["NurbsSurface"]
    assert msc.FAMILY_HOLDS["Cone"] is None  # implemented
    assert msc.source_chart_hold(_project(Plane(np.zeros(3), (1, 0, 0), (0, 1, 0)))) is None
    assert msc.source_chart_hold(_project(Cone(np.zeros(3), (0, 0, 1), (1, 0, 0), 1.0, 0.5, 1.0))) is None
    assert msc.source_chart_hold(_project(Cylinder(np.zeros(3), (0, 0, 1), (1, 0, 0), 1.0, 1.0))) \
        == "face 1: " + msc.FAMILY_HOLDS["Cylinder"]
    assert ExtrudedSurface.__name__ in msc.FAMILY_HOLDS


# ---------------------------------------------------------- chart math (no mesh)


def test_cone_source_chart_and_upper_radius_metric_follow_the_owner():
    surface = Cone(np.zeros(3), (0, 0, 1), (1, 0, 0), 1.0, 0.7, 1.0, 0.3, math.pi / 3)
    chart = _chart()
    for u, v in ((0.0, 0.0), (0.25, 0.5), (1.0, 1.0), (0.6, 0.1)):
        point = chart.xyz(u, v)
        assert np.allclose(point, surface.evaluate(u, v), atol=1e-12)
        back = chart.uv(point)
        assert back[0] == pytest.approx(u, abs=1e-9) and back[1] == pytest.approx(v, abs=1e-9) and back[2] < 1e-12
    # A straight segment of the scaled parameter rectangle bounds the chord
    # on the cone since it bounds the local metric everywhere on that path.
    a, b = np.array([.2, .1]), np.array([.8, .9])
    metric = np.array([abs(chart.sweep) * max(chart.radius(a[1]), chart.radius(b[1])), chart.slant])
    assert np.linalg.norm(chart.xyz(*a) - chart.xyz(*b)) <= np.linalg.norm((b - a) * metric)


def test_degenerate_or_cylinder_like_cones_are_ineligible():
    for r0, r1 in ((1.0, 1.0), (0.0, 1.0)):
        surface = Cone(np.zeros(3), (0, 0, 1), (1, 0, 0), r0, r1, 1.0, 0.0, 1.0)
        with pytest.raises(msc._Ineligible):
            msc._Cone(msc._support_record(surface), 0.0)


# ---------------------------------------------------- end-to-end (real app path)
# Exact public full mesher through Project.generate_mesh on an unsplit declared-corner cone patch.

_TARGET = 0.1


def _cone_project(*, spline_generator=False):
    from anyfem import Project, steel
    from anygeometry.entities import OrientedEdge

    p = Project("cone patch")
    p.add_material(steel("S355", 0.01))
    p.add_plate_section("plate", thickness=0.01, material="S355")
    g = p.geometry
    surface = Cone((0, 0, 0), (0, 0, 1), (1, 0, 0), 1.0, 0.8, 1.0, 0.0, math.pi / 3)
    corners = {uv: g.add_point(*surface.evaluate(*uv)) for uv in ((0, 0), (1, 0), (1, 1), (0, 1))}
    loop = []
    for a, b in (((0, 0), (1, 0)), ((1, 0), (1, 1)), ((1, 1), (0, 1)), ((0, 1), (0, 0))):
        if a[1] == b[1]:
            mid = g.add_point(*surface.evaluate(0.5, a[1]))
            edge = g.add_arc(corners[a], mid, corners[b])
        elif spline_generator and a[0] == 1:
            mid = g.add_point(*surface.evaluate(a[0], .5))
            edge = g.add_spline(corners[a], [mid], corners[b])
        else:
            edge = g.add_line(corners[a], corners[b])
        loop.append(OrientedEdge(edge, True))
    face = g.add_face_from_loop(tuple(loop), corners=(0, 1, 2, 3), surface=surface)
    p.assign_plate(face, "plate")
    return p, surface


def test_added_point_zone_refines_interior_cells_of_a_real_cone_mesh():
    from anymesher.refinement import Refinement
    from anymesher.serialize import mesh_to_dict
    from anyfem.mesh_jobs import MeshSettings

    project, surface = _cone_project()
    settings = MeshSettings.create(_TARGET, strategy="mapped", element_order="linear", quality_policy={})
    mesh = project.generate_mesh(_TARGET, strategy="mapped", order="linear")
    assert len(mesh.quads) > 0 and not mesh.tris
    baseline = msc.capture_source_chart_baseline(project, settings, mesh)
    assert baseline is not None, msc.explain_source_chart_ineligibility(project, settings, mesh)
    assert msc.plan_source_chart_update(project, settings, baseline).is_noop

    centre = tuple(float(x) for x in surface.evaluate(0.5, 0.5))
    project.add_refinement(Refinement(size=0.06, radius=0.1, center=centre, growth=1.5))
    before = mesh_to_dict(mesh)
    node_hw, element_hw = max(mesh.nodes) + 1000, max(mesh.quads) + 1000
    plan = msc.plan_source_chart_update(project, settings, baseline)
    assert plan.applicable, plan.description
    result = msc.generate_source_chart_update(project, settings, baseline, before,
                                              node_high_water=node_hw, element_high_water=element_hw)
    assert mesh_to_dict(mesh) == before
    new = result.mesh
    assert len(new.quads) > len(mesh.quads) and not new.tris
    assert result.retained_elements and set(result.retained_elements) <= set(mesh.quads)
    for eid in result.retained_elements:
        assert tuple(new.quads[eid]) == tuple(mesh.quads[eid])
    added = set(new.quads) - set(mesh.quads)
    assert added and min(added) > element_hw
    assert min(set(new.nodes) - set(mesh.nodes)) > node_hw
    residual_tolerance = float.fromhex(baseline["source"]["surface_tolerance"])
    assert all(np.linalg.norm(node - surface.evaluate(*surface.local_uv(node))) <= residual_tolerance
               for node in new.nodes.values())
    assert result.baseline is not None and result.baseline["schema"] == msc.SCHEMA
    assert result.provenance["family"] == "Cone"


def test_spline_generator_connecting_two_correct_rim_corners_is_refused():
    from anyfem.mesh_jobs import MeshSettings

    project, _ = _cone_project(spline_generator=True)
    settings = MeshSettings.create(_TARGET, strategy="mapped", element_order="linear")
    with pytest.raises(msc._Ineligible, match="two circular arcs"):
        msc._source(project, settings)


def test_reopened_cone_session_refuses_a_boundary_zone_then_publishes_an_interior_update(tmp_path):
    from anyfem.io.project_file import load_project
    from anyfem.mesh_jobs import MeshSettings
    from anymesher.recovery import MeshAutomationOptions
    from anymesher.refinement import Refinement
    from anymesher.serialize import mesh_to_dict

    project, surface = _cone_project()
    settings = MeshSettings.create(_TARGET, strategy="mapped", element_order="linear",
                                   automation=MeshAutomationOptions(), quality_policy={})
    session = project.mesh_session()
    prior = session.generate_mesh(settings)
    before = mesh_to_dict(prior)
    path = session.save(tmp_path / "frustum.anyfem")
    session = load_project(path).mesh_session(path)
    parent = session.mesh_record_id
    allocation = session.project.mesh_numbering
    assert session._baseline()["schema"] == msc.SCHEMA
    session.project.add_refinement(Refinement(.06, center=tuple(surface.evaluate(.01, .5)), radius=.1))
    with pytest.raises((ValueError, RuntimeError), match="not wholly inside"):
        session.update_mesh(settings)
    assert session.project.mesh_numbering == allocation and mesh_to_dict(session.mesh) == before
    session.project.refinements.clear()
    center = surface.evaluate(.5, .5)
    session.project.add_refinement(Refinement(.06, center=tuple(center), radius=.1))
    candidate = session.update_mesh(settings)
    session.save()
    reopened = load_project(path).mesh_session(path)
    assert reopened.mesh_record_id != parent and reopened.preview_update(settings).is_noop
    assert not candidate.tris and reopened.mesh.quads == candidate.quads
    kept = set(prior.quads) & set(candidate.quads)
    fresh = set(candidate.quads) - kept
    assert kept and fresh and min(fresh) > allocation.element_high_water
    for e in kept:
        assert prior.quads[e] == reopened.mesh.quads[e]
        for n in prior.quads[e]:
            np.testing.assert_array_equal(reopened.mesh.nodes[n], prior.nodes[n])
    tol = float.fromhex(reopened._baseline()["source"]["tolerance"])
    coords = [np.asarray([reopened.mesh.nodes[n] for n in candidate.quads[e]]) for e in fresh]
    assert any(np.linalg.norm(cell.mean(axis=0) - center) <= .1 and
               np.max(np.linalg.norm(np.roll(cell, -1, axis=0) - cell, axis=1)) <= .06 + tol
               for cell in coords)
