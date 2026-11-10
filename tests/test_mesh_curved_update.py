from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem import mesh_curved_update as mcu
from anygeometry import Cone, Cylinder, Plane


def _project(surface):
    face = SimpleNamespace(surface=surface)
    return SimpleNamespace(geometry=SimpleNamespace(faces={1: face}))


def _cyl():
    return Cylinder(np.zeros(3), (0, 0, 1), (1, 0, 0), 1.0, 2.0)


def _cone():
    return Cone(np.zeros(3), (0, 0, 1), (1, 0, 0), 1.0, 0.5, 2.0)


def _chart(sweep=math.pi, start=0.3, height=2.0):
    record = {
        "origin": [float(x).hex() for x in (0, 0, 0)], "axis": [float(x).hex() for x in (0, 0, 1)],
        "radial": [float(x).hex() for x in (1, 0, 0)], "circumferential": [float(x).hex() for x in (0, 1, 0)],
        "radius": (1.0).hex(), "height": float(height).hex(),
        "start": float(start).hex(), "sweep": float(sweep).hex(),
    }
    return mcu._Cyl(record, 1.0e-9)


# ------------------------------------------------------------------ refusals


@pytest.mark.parametrize("surface,word", [(_cyl(), "cylinder"), (_cone(), "cone")])
def test_curved_family_without_a_baseline_is_refused_with_exact_reason(surface, word):
    project = _project(surface)
    assert mcu.capture_curved_baseline(project, None, None) is None
    plan = mcu.plan_curved_update(project, None, None)
    assert plan.applicable is False
    assert word in plan.description and "no verified owner chart route" in plan.description
    with pytest.raises(mcu.CurvedUpdateUnsupported):
        mcu.generate_curved_update(project, None, None, None, node_high_water=0, element_high_water=0)


def test_planar_is_not_a_curved_case():
    plane = Plane(np.zeros(3), (1, 0, 0), (0, 1, 0))
    assert mcu.classify_curved_support(_project(plane)) == {}
    assert mcu.plan_curved_update(_project(plane), None, None).applicable is False


# ------------------------------------------------- chart / ring math (no mesh)


def test_chart_round_trip_and_periodic_gap_is_rejected():
    cyl = _chart()
    for u, v in ((0.0, 0.0), (0.25, 0.5), (1.0, 1.0), (0.6, 0.1)):
        back = cyl.uv(cyl.xyz(u, v))
        assert back[0] == pytest.approx(u, abs=1e-9) and back[1] == pytest.approx(v, abs=1e-9)
        assert back[2] < 1e-12
    # A point in the gap (opposite side of the support) must not wrap into [0, 1].
    gap = cyl.origin + cyl.radius * (math.cos(cyl.start - 0.5 * (2 * math.pi - abs(cyl.sweep))) * cyl.r
                                     + math.sin(cyl.start - 0.5 * (2 * math.pi - abs(cyl.sweep))) * cyl.c)
    u, _, _ = cyl.uv(gap)
    assert not (0.0 <= u <= 1.0)


def test_nonconvex_cell_is_not_ring_eligible():
    assert mcu._convexity([(0, 0), (1, 0), (0.2, 0.2), (0, 1)]) == 0.0


def test_corner_jacobian_sign_follows_owner_normal():
    cyl = _chart()
    quad = [cyl.xyz(0.4, 0.4), cyl.xyz(0.5, 0.4), cyl.xyz(0.5, 0.5), cyl.xyz(0.4, 0.5)]
    j = mcu._corner_jacobians(quad, cyl, 1)
    assert abs(j) > 0.5
    assert mcu._corner_jacobians(quad, cyl, -1) == pytest.approx(-j)


# ------------------------------------------------------ end-to-end (real app path)
# Real ANYgeometry cylinder-patch contract fixture (single open, UNSPLIT 60-degree sector) meshed by
# the production mesher through Project.generate_mesh; no hand-built grid.

_RADIUS, _HEIGHT, _TARGET = 1.0, 1.0, 0.1


def _cylinder_project(*, declare_corners=False, u_limits=(0., 1.)):
    from anyfem import Project, steel
    from anygeometry.entities import OrientedEdge

    p = Project("cylinder ring")
    p.add_material(steel("S355", 0.01))
    p.add_plate_section("plate", thickness=0.01, material="S355")
    g = p.geometry
    surface = Cylinder((0, 0, 0), (0, 0, 1), (1, 0, 0), _RADIUS, _HEIGHT, 0, math.pi / 3)
    def point(u, v):
        return surface.evaluate(u_limits[0] + u * (u_limits[1] - u_limits[0]), v)

    corners = {}
    for uv in ((0, 0), (1, 0), (1, 1), (0, 1)):
        corners[uv] = g.add_point(*point(*uv))
    loop = []
    for a, b in (((0, 0), (1, 0)), ((1, 0), (1, 1)), ((1, 1), (0, 1)), ((0, 1), (0, 0))):
        if a[1] == b[1]:  # constant v: circular arc through the mid-u point
            mid = g.add_point(*point(0.5, a[1]))
            edge = g.add_arc(corners[a], mid, corners[b])
        else:
            edge = g.add_line(corners[a], corners[b])
        loop.append(OrientedEdge(edge, True))
    face = g.add_face_from_loop(tuple(loop), corners=(0, 1, 2, 3) if declare_corners else None,
                                surface=surface)
    p.assign_plate(face, "plate")
    return p, face, surface


def test_added_point_zone_rings_interior_cells_of_a_real_cylinder_mesh():
    from anymesher.serialize import mesh_from_dict, mesh_to_dict
    from anymesher.refinement import Refinement, SizeField
    from anyfem.mesh_jobs import MeshSettings
    from anyfem.mesh_surface_update import _binding
    import json

    project, face, surface = _cylinder_project(declare_corners=True)
    settings = MeshSettings.create(_TARGET, strategy="mapped", element_order="linear", quality_policy={})
    mesh = project.generate_mesh(_TARGET, strategy="mapped", order="linear")
    assert len(mesh.quads) > 0 and not mesh.tris
    baseline = mcu.capture_curved_baseline(project, settings, mesh)
    assert baseline is not None, mcu.explain_curved_ineligibility(project, settings, mesh)
    centre = tuple(float(x) for x in surface.evaluate(0.5, 0.5))
    zone = Refinement(size=0.06, radius=0.1, center=centre, growth=1.5)
    project.add_refinement(zone)
    before = mesh_to_dict(mesh)
    node_hw, element_hw = max(mesh.nodes) + 1000, max(mesh.quads) + 1000
    plan = mcu.plan_curved_update(project, settings, baseline)
    assert plan.applicable, plan.description
    result = mcu.generate_curved_update(project, settings, baseline, before,
                                        node_high_water=node_hw, element_high_water=element_hw)
    assert mesh_to_dict(mesh) == before
    assert len(result.mesh.quads) > len(mesh.quads)
    assert min(set(result.mesh.quads) - set(mesh.quads)) > element_hw
    assert min(set(result.mesh.nodes) - set(mesh.nodes)) > node_hw
    for e in result.retained_elements:
        assert tuple(result.mesh.quads[e]) == tuple(mesh.quads[e])
    assert result.provenance["route"] == mcu.ROUTE
    assert result.provenance["size_field"] == "anymesher.refinement.SizeField"
    # at least one new cell meets the owner SizeField value at the zone centre
    centre_target = float(SizeField(project.geometry, _TARGET, (zone,)).size_at(centre)[0])
    assert result.provenance["centre_target_size"] == centre_target
    assert result.provenance["new_cells_meeting_centre_target"] >= 1

    # reopen: baseline through JSON, mesh through the owner codec; global and numbered binding agree
    reopened_baseline = json.loads(json.dumps(result.baseline))
    reopened_mesh = mesh_from_dict(mesh_to_dict(result.mesh))
    assert reopened_baseline["global"] == mcu._curved_global(project, settings)
    assert _binding(reopened_mesh, int(face)) == reopened_baseline["mesh"]
    assert mcu.capture_curved_baseline(project, settings, reopened_mesh) is not None
    assert mcu.plan_curved_update(project, settings, reopened_baseline).is_noop


@pytest.mark.parametrize("strategy", ["mapped", "auto"])
def test_saved_curved_session_publishes_owner_sized_q4_with_gui_automation(tmp_path, strategy):
    from anyfem.mesh_jobs import MeshSettings
    from anyfem.io.project_file import load_project
    from anymesher.refinement import Refinement
    from anymesher.recovery import MeshAutomationOptions

    project, face, support = _cylinder_project(declare_corners=True)
    settings = MeshSettings.create(_TARGET, strategy=strategy, element_order="linear",
                                   quality_policy={}, automation=MeshAutomationOptions())
    session = project.mesh_session()
    before = session.generate_mesh(settings)
    assert session._baseline()["schema"] == mcu.SCHEMA
    path = session.save(tmp_path/"sector.anyfem")
    reopened = load_project(path).mesh_session(path)
    old_id = reopened.mesh_record_id
    assert not before.tris
    centre = support.evaluate(.5,.5)
    reopened.project.add_refinement(Refinement(.06, center=tuple(centre), radius=.1, growth=1.5))
    updated = reopened.update_mesh(settings)
    assert reopened.mesh_record_id != old_id
    reopened.save()
    saved = load_project(path).mesh_session(path)
    assert saved._baseline()["schema"] == mcu.SCHEMA and saved.preview_update(settings).is_noop
    assert saved.mesh.quads == updated.quads and not saved.mesh.tris
    kept = set(before.quads) & set(saved.mesh.quads)
    created = set(saved.mesh.quads) - kept
    assert kept and created and min(created) > max(before.quads)
    for eid in kept:
        assert saved.mesh.quads[eid] == before.quads[eid]
        for n in before.quads[eid]:
            assert np.array_equal(before.nodes[n], saved.mesh.nodes[n])
    near = [np.array([saved.mesh.nodes[n] for n in saved.mesh.quads[e]]) for e in created]
    owner_tol = float.fromhex(saved._baseline()["source"]["tolerance"])
    assert any(np.linalg.norm(xyz.mean(axis=0)-centre) <= .1 and
               np.max(np.linalg.norm(np.roll(xyz,1,axis=0)-xyz,axis=1)) <= .06 + owner_tol for xyz in near)


def test_unflagged_mixed_source_sector_refuses_and_never_publishes():
    from anyfem.mesh_jobs import MeshSettings
    project, _, _ = _cylinder_project()
    settings = MeshSettings.create(_TARGET, strategy="auto", element_order="linear")
    prior = project.generate_mesh(_TARGET, strategy="auto", order="linear")
    assert prior.tris and prior.quads
    assert mcu.capture_curved_baseline(project, settings, prior) is None
    assert "mixed mesh" in mcu.explain_curved_ineligibility(project, settings, prior)
    with pytest.raises(mcu._Ineligible, match="four mapped source corners"):
        mcu._source(project, settings)

def test_trimmed_parameter_domain_and_reversed_parent_normals_are_refused():
    from anyfem.mesh_jobs import MeshSettings

    settings = MeshSettings.create(_TARGET, strategy="mapped", element_order="linear")
    trimmed, _, _ = _cylinder_project(declare_corners=True, u_limits=(.2, .8))
    with pytest.raises(mcu._Ineligible, match="not the full support rectangle"):
        mcu._source(trimmed, settings)

    project, _, _ = _cylinder_project(declare_corners=True)
    mesh = project.generate_mesh(_TARGET, strategy="mapped", order="linear")
    for eid, conn in mesh.quads.items():
        mesh.quads[eid] = tuple(reversed(conn))
    problems, _ = mcu._coverage(mesh, mcu._source(project, settings))
    assert any("source chart orientation" in reason for reason in problems)
    assert mcu.capture_curved_baseline(project, settings, mesh) is None


def test_strict_growth_periodic_seam_and_changed_support_preserve_parent(tmp_path):
    from dataclasses import replace
    from anyfem.mesh_controls import StructuredMeshControls
    from anyfem.mesh_jobs import MeshSettings
    from anymesher.refinement import Refinement
    from anymesher.serialize import mesh_to_dict

    project, face, support = _cylinder_project(declare_corners=True)
    settings = MeshSettings.create(_TARGET, strategy="mapped", element_order="linear", quality_policy={},
                                   structured_controls=StructuredMeshControls(max_element_growth=1.15))
    session = project.mesh_session()
    prior = session.generate_mesh(settings)
    from anyfem.io.project_file import load_project
    path = session.save(tmp_path / "refusal.anyfem")
    session = load_project(path).mesh_session(path)
    project = session.project
    record_id = session.mesh_record_id
    payload = mesh_to_dict(prior)

    project.add_refinement(Refinement(.06, center=tuple(support.evaluate(.01,.5)), radius=.1))
    with pytest.raises((ValueError, RuntimeError), match="not wholly inside the face"):
        session.update_mesh(settings)
    assert session.mesh_record_id == record_id
    project.refinements.clear()
    project.add_refinement(Refinement(.06, center=tuple(support.evaluate(.5,.5)), radius=.1))
    with pytest.raises((ValueError, RuntimeError), match="growth violations"):
        session.update_mesh(settings)
    assert session.mesh_record_id == record_id

    project.geometry.set_face_surface(face, replace(support, radial_direction=(0.,1.,0.), start_angle=-math.pi/2))
    assert not mcu.plan_curved_update(project, settings, session._baseline()).applicable
    with pytest.raises((ValueError, RuntimeError)):
        session.update_mesh(settings)
    assert session.mesh_record_id == record_id
    assert mesh_to_dict(prior) == payload


@pytest.mark.parametrize("family", ["cylinder", "cone"])
def test_saved_open_sector_rim_reseed_replaces_one_whole_component(tmp_path, family):
    from dataclasses import replace
    from anyfem.application.mesh_publication import retained_mesh
    from anyfem.io.project_file import load_project
    from anyfem.mesh_jobs import MeshSettings
    from anyfem.mesh_preview import mesh_update_preview
    from anyfem.mesh_whole_plate_update import WholePlatePlan
    from anymesher.serialize import mesh_to_dict
    from test_mesh_source_chart_update import _cone_project

    project = (_cylinder_project(declare_corners=True)[0] if family == "cylinder" else _cone_project()[0])
    settings = MeshSettings.create(.12, strategy="mapped", element_order="linear", quality_policy={})
    session = project.mesh_session()
    before = session.generate_mesh(settings)
    parent = session.mesh_record_id
    path = session.save(tmp_path / f"rim-{family}.anyfem")
    session = load_project(path).mesh_session(path)
    source = session._baseline()["source"]
    rim = [edge for edge, _ in source["loop"]["edges"]]
    arc, opposite = rim[0], rim[2]
    count = len(before.nodes_of_edge[arc]) + 2
    new_settings = replace(settings, overrides=((arc, count),))
    plan = session.preview_update(new_settings)
    assert isinstance(plan, WholePlatePlan) and f"whole open {family} sector" in plan.description
    assert "new permanent IDs" in plan.description
    assert not mesh_update_preview(plan, session._baseline(), before, parent).review_prepared_cells
    ledger = session.project.mesh_numbering
    after = session.update_mesh(new_settings)
    assert after.quads and not (after.tris or after.beams or after.couplings)
    assert min(after.quads) > ledger.element_high_water and min(after.nodes) > ledger.node_high_water
    assert len(after.nodes_of_edge[arc]) == len(after.nodes_of_edge[opposite]) == count + 1
    expected = session.project.geometry.sample_edge(arc, np.linspace(0., 1., count + 1))
    np.testing.assert_allclose([after.nodes[n] for n in after.nodes_of_edge[arc]], expected, rtol=0.,
                               atol=float.fromhex(source["tolerance"]))
    assert session._baseline()["last_update"]["retired_elements"] == len(before.quads)
    session.save()
    session = load_project(path).mesh_session(path)
    assert session.preview_update(new_settings).is_noop
    assert session.mesh.quads == after.quads and session.mesh.geometry_revision == before.geometry_revision
    assert mesh_to_dict(retained_mesh(session, parent)) == mesh_to_dict(before)
    baseline, current = session._baseline(), session.project.mesh_numbering
    # Two conflicting counts cannot be assigned to opposite edges of this mapped sector.
    with pytest.raises((RuntimeError, ValueError), match="division|seeding|overrid|counts"):
        session.update_mesh(replace(settings, overrides=((arc, count), (opposite, count + 3))))
    assert session.project.mesh_numbering == current and session._baseline() == baseline
