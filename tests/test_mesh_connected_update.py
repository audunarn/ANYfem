"""Connected-region update on two real adjoining mapped plates sharing one edge."""
from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pytest

from anyfem import Project, steel
from anyfem.mesh_connected_update import (
    ConnectedUpdateError,
    capture_connected_baseline,
    generate_connected_update,
    plan_connected_update,
)
from anyfem.mesh_jobs import MeshSettings
from anyfem.mesh_numbering import MeshNumbering, number_full_mesh


def build(*, assign=True):
    project = Project("adjoining")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", thickness=0.01, material="S355")
    geo = project.geometry
    a = geo.add_points([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    loop_a = geo.add_polyline(a, close=True)
    edges_a = [int(e) for e in loop_a]
    face_a = int(geo.add_face(loop_a))
    extra = geo.add_points([(2, 0, 0), (2, 1, 0)])
    b = [a[1], extra[0], extra[1], a[2]]
    loop_b = [*geo.add_polyline(b), edges_a[1]]
    edges_b = [int(e) for e in loop_b]
    face_b = int(geo.add_face(loop_b))
    if assign:
        project.assign_plate(face_a, "plate")
        project.assign_plate(face_b, "plate")
    assert edges_b[3] == edges_a[1], "the two plates must share one geometry edge"
    return project, (face_a, face_b), edges_a, edges_b


def settings(order="linear", **extra):
    return MeshSettings.create(0.5, element_order=order, strategy="mapped", **extra)


def full(project, order="linear"):
    base = settings(order)
    mesh = project.generate_mesh(0.5, strategy="mapped", order=order)
    mesh = number_full_mesh(mesh, MeshNumbering())
    return base, mesh, capture_connected_baseline(project, base, mesh)


def spy(monkeypatch):
    import anymesher.mapped as mapped

    calls = []
    original = mapped.generate_mesh

    def wrapper(geometry, **kwargs):
        calls.append(tuple(kwargs["face_ids"]))
        return original(geometry, **kwargs)

    monkeypatch.setattr(mapped, "generate_mesh", wrapper)
    return calls


def test_baseline_is_json_and_records_shared_edge():
    project, (fa, fb), ea, eb = build()
    base, mesh, baseline = full(project)
    assert baseline is not None
    baseline = json.loads(json.dumps(baseline))
    assert baseline["groups"] == [sorted((fa, fb))]
    assert baseline["edges"][str(ea[1])]["faces"] == sorted((fa, fb))
    assert plan_connected_update(project, base, baseline).is_noop
    baseline["declared_edges"] = []
    assert "content check" in plan_connected_update(project, base, baseline).description


def test_unchanged_is_not_applicable_noop():
    project, *_ = build()
    base, _, baseline = full(project)
    plan = plan_connected_update(project, base, baseline)
    assert plan.is_noop and not plan.applicable


def test_disconnected_model_is_ineligible():
    project = Project("apart")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", thickness=0.01, material="S355")
    for x in (0, 5):
        pts = project.geometry.add_points([(x, 0, 0), (x + 1, 0, 0), (x + 1, 1, 0), (x, 1, 0)])
        project.assign_plate(project.geometry.add_face(project.geometry.add_polyline(pts, close=True)), "plate")
    mesh = project.generate_mesh(0.5, strategy="mapped")
    assert capture_connected_baseline(project, settings(), mesh) is None


@pytest.mark.parametrize("order", ["linear", "quadratic"])
def test_frozen_interface_remeshes_only_one_plate(monkeypatch, order):
    project, (fa, fb), ea, eb = build()
    base, mesh, baseline = full(project, order)
    assert baseline is not None
    edited = replace(base, overrides=((eb[0], 4),))  # B bottom (and its opposite): not the interface
    plan = plan_connected_update(project, edited, baseline)
    assert plan.applicable and plan.affected_faces == (fb,) and not plan.grown_groups
    assert plan.interface_edges == (ea[1],) and "frozen interface" in plan.description
    calls = spy(monkeypatch)
    from anymesher.serialize import mesh_to_dict

    # Previous generations may have retired numbers above every active id.
    node_high, element_high = max(mesh.nodes) + 40, max((*mesh.quads, *mesh.tris)) + 20
    record = generate_connected_update(project, edited, baseline, mesh_to_dict(mesh),
                                       node_high_water=node_high, element_high_water=element_high)
    assert calls == [(fb,)]
    new = record.mesh
    a_elements = list(mesh.elements_of_face[fa])
    assert set(record.retained_elements) == set(a_elements)
    for e in a_elements:
        assert new.quads[e] == mesh.quads[e]
    # the shared edge keeps its exact node ids (corners and, if quadratic, mid-sides)
    assert new.nodes_of_edge[ea[1]] == mesh.nodes_of_edge[ea[1]]
    shared = {n for e in a_elements for n in mesh.quads[e]}
    for n in shared:
        np.testing.assert_array_equal(new.nodes[n], mesh.nodes[n])
    fresh = set(new.nodes) - set(mesh.nodes)
    assert fresh and min(fresh) > node_high
    assert min(set(new.quads) - set(mesh.quads)) > element_high
    assert len(new.elements_of_face[fb]) == 8 and len(new.elements_of_face[fa]) == 4
    assert new.geometry_revision == project.geometry.revision
    assert record.baseline is not None and record.plan.affected_faces == (fb,)
    assert not new.hybrid_diagnostics


def test_interface_seed_change_grows_whole_component_once(monkeypatch):
    project, (fa, fb), ea, eb = build()
    base, mesh, baseline = full(project)
    edited = replace(base, overrides=((eb[1], 4),))  # B right edge -> shared (opposite) seed changes
    plan = plan_connected_update(project, edited, baseline)
    assert plan.applicable and set(plan.affected_faces) == {fa, fb}
    assert plan.grown_groups == ((fa, fb),) and "one whole-component expansion" in plan.description.lower()
    assert plan.interface_edges == ()
    calls = spy(monkeypatch)
    update = generate_connected_update(project, edited, baseline, mesh,
                                       node_high_water=max(mesh.nodes), element_high_water=max(mesh.quads))
    assert calls == [(fa, fb)]
    assert not update.retained_nodes and not update.retained_elements
    assert set(update.mesh.nodes).isdisjoint(mesh.nodes)
    assert set(update.mesh.quads).isdisjoint(mesh.quads)
    assert update.mesh.seeding.divisions[ea[1]] == 4
    assert sum(len(ids) for ids in update.mesh.elements_of_sheet.values()) == len(update.mesh.quads)
    assert len(update.mesh.declared_plate_junction_edges) == 4


def test_added_plate_is_not_admitted():
    project, *_ = build()
    base, _, baseline = full(project)
    pts = project.geometry.add_points([(10, 0, 0), (11, 0, 0), (11, 1, 0), (10, 1, 0)])
    project.geometry.add_face(project.geometry.add_polyline(pts, close=True))
    plan = plan_connected_update(project, base, baseline)
    assert not plan.applicable


def test_interface_station_mismatch_is_refused(monkeypatch):
    import anymesher.mapped as module

    project, (fa, fb), ea, eb = build()
    base, mesh, baseline = full(project)
    edited = replace(base, overrides=((eb[0], 4),))
    original = module.generate_mesh

    def bad(geometry, **kwargs):
        kwargs["seeding"].divisions[ea[1]] = 3
        kwargs["seeding"].divisions[eb[1]] = 3
        return original(geometry, **kwargs)

    monkeypatch.setattr(module, "generate_mesh", bad)
    from anymesher.serialize import mesh_to_dict

    with pytest.raises(Exception, match="station count|frozen|division|opposite"):
        generate_connected_update(project, edited, baseline, mesh_to_dict(mesh),
                                  node_high_water=max(mesh.nodes),
                                  element_high_water=max((*mesh.quads, *mesh.tris)))


def test_stale_old_mesh_is_refused():
    project, (fa, fb), ea, eb = build()
    base, mesh, baseline = full(project)
    edited = replace(base, overrides=((eb[0], 4),))
    mesh.nodes[next(iter(mesh.nodes))] = np.array([9.0, 9.0, 9.0])
    with pytest.raises(ConnectedUpdateError, match="baseline"):
        generate_connected_update(project, edited, baseline, mesh, node_high_water=max(mesh.nodes),
                                  element_high_water=max((*mesh.quads, *mesh.tris)))


@pytest.mark.parametrize("order", ["linear", "quadratic"])
def test_owner_mapped_mesh_preserves_live_seeds(order):
    from anymesher.mapped import generate_mesh

    project, (fa, fb), ea, eb = build(assign=False)
    base = settings(order)
    old = generate_mesh(project.geometry, target_size=.5, order=order)
    baseline = capture_connected_baseline(project, base, old)
    edited = replace(base, overrides=((eb[0], 4),))
    result = generate_connected_update(project, edited, baseline, old,
                                       node_high_water=max(old.nodes), element_high_water=max(old.quads))
    assert result.plan.affected_faces == (fb,)
    assert result.mesh.seeding.divisions[eb[0]] == 4
    assert result.mesh.seeding.divisions[ea[1]] == old.seeding.divisions[ea[1]]
    assert list(result.mesh.elements_of_face[fa]) == list(old.elements_of_face[fa])


def test_unexplained_junction_and_changed_geometry_refuse():
    project, _, ea, _ = build()
    base, mesh, baseline = full(project)
    mesh.declared_plate_junction_edges += (tuple(mesh.nodes_of_edge[ea[0]][:2]),)
    assert capture_connected_baseline(project, base, mesh) is None
    project.geometry.move_point(project.geometry.edges[ea[0]].start, -.25, 0., 0.)
    plan = plan_connected_update(project, base, baseline)
    assert not plan.applicable and "geometry changed" in plan.description


def test_cancel_before_splice_leaves_published_mesh_unchanged():
    from anymesher.serialize import mesh_to_dict

    project, _, _, eb = build()
    base, mesh, baseline = full(project)
    before = mesh_to_dict(mesh)

    def cancel(stage):
        if stage == "connected assemble":
            raise InterruptedError(stage)

    with pytest.raises(InterruptedError, match="connected assemble"):
        generate_connected_update(project, replace(base, overrides=((eb[0], 4),)), baseline, mesh,
                                  node_high_water=max(mesh.nodes), element_high_water=max(mesh.quads),
                                  cancellation_check=cancel)
    assert mesh_to_dict(mesh) == before
