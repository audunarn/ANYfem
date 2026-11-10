from dataclasses import replace

import numpy as np
import pytest
from anymesher.refinement import Refinement
from anymesher.errors import MeshError
from anymesher.serialize import mesh_to_dict

from anyfem import Project, steel
from anyfem.io.project_file import load_project
from anyfem.mesh_jobs import MeshSettings
from anyfem.mesh_session import ProjectMeshSession
from anyfem.mesh_surface_update import SurfaceUpdateError
from anyfem.mesh_whole_plate_update import WholePlatePlan, generate_planar_update
from anyfem.regional_mesh import RegionalUpdateError
from anyfem.model.records import OutputRequest
from anyfem.model.regions import ManualRegion, MeshEntityRef, Region, RegionRef


def plate(**kwargs):
    project = Project("planar boundary edit")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", thickness=.01, material="S355")
    g = project.geometry
    vertices = g.add_points([(0., 0., 0.), (4., 0., 0.), (4., 4., 0.), (0., 4., 0.)])
    edges = g.add_polyline(vertices, close=True)
    face = g.add_face(edges)
    project.assign_plate(face, "plate")
    settings = MeshSettings.create(1., strategy="mapped", element_order="linear", **kwargs)
    session = ProjectMeshSession(project)
    session.generate_mesh(settings)
    return session, settings, edges


def test_saved_boundary_expansion_regenerates_once_and_keeps_history(tmp_path, monkeypatch):
    session, settings, edges = plate()
    parent = session.mesh_record_id
    path = session.save(tmp_path / "boundary.anyfem")
    session = ProjectMeshSession(load_project(path), path)
    before = mesh_to_dict(session.mesh)
    allocation = session.project.mesh_numbering
    edited = replace(settings, overrides=((edges[0], 8),))
    plan = session.preview_update(edited)
    assert isinstance(plan, WholePlatePlan)
    assert "new permanent IDs" in plan.description and "outer boundary" in plan.reason
    original = Project.generate_mesh
    calls = []

    def record(project, *args, **kwargs):
        calls.append(project)
        return original(project, *args, **kwargs)

    monkeypatch.setattr(Project, "generate_mesh", record)
    new = session.update_mesh(edited)
    assert len(calls) == 1 and calls[0] is not session.project
    assert min(new.nodes) > allocation.node_high_water
    assert min(new.quads) > allocation.element_high_water
    assert len(new.quads) == 32 and len(new.nodes_of_edge[edges[0]]) == 9
    record = session.project.mesh_records[session.mesh_record_id]
    receipt = record.summary["regional_baseline"]["last_update"]
    assert receipt["operation"] == "anyfem.whole_plate_update" and receipt["retained_nodes"] == 0
    assert receipt["retained_elements"] == 0 and receipt["reason"] == plan.reason
    session.save()
    session = ProjectMeshSession(load_project(path), path)
    assert session.mesh.quads == new.quads
    assert session.preview_update(edited).is_noop
    from anyfem.application.mesh_publication import retained_mesh
    assert mesh_to_dict(retained_mesh(session, parent)) == before


def test_ambiguous_source_and_changed_parent_refuse_before_full_generation(monkeypatch):
    session, settings, edges = plate()
    edited = replace(settings, overrides=((edges[0], 6),))
    baseline = session._baseline()
    mesh = mesh_to_dict(session.mesh)
    from anymesher.serialize import mesh_from_dict
    corrupt = mesh_from_dict(mesh)
    corrupt.nodes[min(corrupt.nodes)] = np.array([10., 10., 10.])
    monkeypatch.setattr(Project, "generate_mesh", lambda *_a, **_k: pytest.fail("unexpected full generation"))
    with pytest.raises(SurfaceUpdateError, match="parent mesh"):
        generate_planar_update(session.project, edited, baseline, corrupt, node_high_water=100, element_high_water=100)
    stale = {**baseline, "pins": {}}
    with pytest.raises(SurfaceUpdateError, match="build pins changed"):
        generate_planar_update(session.project, edited, stale, mesh, node_high_water=100, element_high_water=100)
    session.project.add_refinement(Refinement(.5, center=(2., 2., 0.), radius=.25))
    with pytest.raises(SurfaceUpdateError, match="independent interior edits"):
        session.update_mesh(edited)


def test_expansion_respects_user_quality_and_cancellation():
    session, settings, edges = plate(quality_policy={"maximum_aspect_ratio": 1.1})
    before = session.project.mesh_numbering
    edited = replace(settings, overrides=((edges[0], 8),))
    with pytest.raises((MeshError, RegionalUpdateError), match="quality|aspect ratio"):
        session.update_mesh(edited)
    assert session.project.mesh_numbering == before
    stages = []

    def cancel(stage):
        stages.append(stage)
        if stage == "mesh element generation":
            raise InterruptedError("cancelled in owner generation")

    with pytest.raises(InterruptedError, match="cancelled in owner generation"):
        generate_planar_update(session.project, edited, session._baseline(), session.mesh,
                               node_high_water=100, element_high_water=100, cancellation_check=cancel)
    assert stages.count("whole plate expansion") == 1 and session.project.mesh_numbering == before


def test_retired_mesh_reference_blocks_publication():
    session, settings, edges = plate()
    parent = session.mesh_record_id
    node = min(session.mesh.nodes)
    region = Region("point", "mesh", "node", ManualRegion((MeshEntityRef(parent, "node", node),)), mesh_id=parent)
    session.project.regions.add(region)
    session.project.output_requests["point"] = OutputRequest(("displacement",), RegionRef(region.id), "node")
    with pytest.raises(ValueError, match="require re-selection"):
        session.update_mesh(replace(settings, overrides=((edges[0], 8),)))
    assert session.mesh_record_id == parent and session.project.regions[region.id] == region


def test_boundary_zone_expansion_checks_the_requested_size():
    session, settings, _ = plate()
    project = session.project
    project.add_refinement(Refinement(.5, center=(.1, .1, 0.), radius=1., growth=2.))
    assert isinstance(session.preview_update(settings), WholePlatePlan)
    parent = session.mesh_record_id
    with pytest.raises(SurfaceUpdateError, match="0.5 edge target"):
        session.update_mesh(settings)
    assert session.mesh_record_id == parent
    project.refinements.clear()
    project.add_refinement(Refinement(.5, center=(.1, .1, 0.), radius=6., growth=2.))
    child = session.update_mesh(settings)
    lengths = []
    for conn in child.quads.values():
        pts = [child.nodes[n] for n in conn]
        if np.linalg.norm(np.mean(pts, axis=0) - [.1, .1, 0.]) <= 1.:
            lengths.append(max(np.linalg.norm(a - b) for a, b in zip(pts, pts[1:] + pts[:1])))
    assert lengths and min(lengths) <= .5 + project.geometry.tolerance.effective_length(4.)


def test_changed_plate_geometry_uses_current_revision(tmp_path):
    session, settings, _ = plate()
    path = session.save(tmp_path / "resized-plate.anyfem")
    session = ProjectMeshSession(load_project(path), path)
    old = session.mesh
    geometry = session.project.geometry
    for v in tuple(geometry.vertices):
        x, y, z = geometry.vertex_position(v)
        if x == 4.:
            geometry.move_point(v, 5., y, z)
    plan = session.preview_update(settings)
    assert isinstance(plan, WholePlatePlan)
    child = session.update_mesh(settings)
    assert child.geometry_revision == geometry.revision and child.geometry_revision != old.geometry_revision
    assert len(child.quads) == 20 and set(old.nodes).isdisjoint(child.nodes)
    assert max(p[0] for p in child.nodes.values()) == pytest.approx(5.)


def test_interior_owner_refusal_does_not_expand(monkeypatch):
    from anyfem import mesh_surface_update
    session, settings, _ = plate()
    session.project.add_refinement(Refinement(.5, center=(2., 2., 0.), radius=.1, growth=2.))
    assert not isinstance(session.preview_update(settings), WholePlatePlan)
    parent = session.mesh_record_id

    def fail_patch(*_args, **_kwargs):
        raise mesh_surface_update.SurfaceBoundaryMismatch("owner could not retain its interface")

    monkeypatch.setattr(mesh_surface_update, "generate_surface_update", fail_patch)
    monkeypatch.setattr(Project, "generate_mesh", lambda *_a, **_k: pytest.fail("unreported expansion"))
    with pytest.raises(mesh_surface_update.SurfaceBoundaryMismatch, match="retain its interface"):
        session.update_mesh(settings)
    assert session.mesh_record_id == parent
