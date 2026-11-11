"""Regional planner/generator on two real disconnected plates (mapped strategy)."""
from __future__ import annotations

import copy
import json
from dataclasses import replace

import numpy as np
import pytest

from anyfem import Project, regional_mesh, steel
from anyfem.mesh_jobs import MeshSettings
from anyfem.mesh_numbering import MeshNumbering, number_full_mesh
from anyfem.regional_mesh import (
    RegionalUpdateUnsupported,
    capture_regional_baseline,
    generate_regional_update,
    plan_regional_update,
)


def two_plates(widths=(1.0, 1.0), *, clockwise_first=False):
    """Two unit-height plates at x=0 and x=5; construction order fixes every entity id."""
    project = Project("two plates")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", thickness=0.01, material="S355")
    faces, edges = [], []
    for x, width in zip((0, 5), widths):
        corners = [(x, 0, 0), (x + width, 0, 0), (x + width, 1, 0), (x, 1, 0)]
        if clockwise_first and not faces:
            corners = [corners[0], *corners[:0:-1]]
        points = project.geometry.add_points(corners)
        boundary = project.geometry.add_polyline(points, close=True)
        face = project.geometry.add_face(boundary)
        project.assign_plate(face, "plate")
        faces.append(int(face))
        edges.append([int(e) for e in boundary])
    return project, faces, edges


def settings(strategy="mapped", **extra):
    return MeshSettings.create(0.5, element_order="linear", strategy=strategy, **extra)


def full(project, strategy="mapped"):
    base = settings(strategy)
    mesh = project.generate_mesh(0.5, strategy=strategy)
    mesh = number_full_mesh(mesh, MeshNumbering())
    return base, mesh, capture_regional_baseline(project, base, mesh)


def cells(mesh, face):
    table = {**mesh.quads, **mesh.tris}
    return {int(e): tuple(table[e]) for e in mesh.elements_of_face[face]}


def face_nodes(mesh, face):
    return {n for c in cells(mesh, face).values() for n in c}


def reopen(baseline, mesh):
    """What a saved project hands back: JSON baseline and the mesh codec payload."""
    from anymesher.serialize import mesh_to_dict

    return json.loads(json.dumps(baseline)), mesh_to_dict(mesh)


def test_baseline_is_serializable_and_binds_exact_numbers():
    project, faces, _ = two_plates()
    _, mesh, baseline = full(project)
    assert baseline is not None
    json.dumps(baseline)
    assert [c["faces"] for c in baseline["components"]] == [[f] for f in faces]
    assert baseline["original_geometry"]
    for component, face in zip(baseline["components"], faces):
        assert component["mesh"]["element_ids"] == sorted(cells(mesh, face))
        assert component["mesh"]["node_ids"] == sorted(face_nodes(mesh, face))
        assert component["mesh"]["digest"]
    assert set(baseline["pins"]) == {"baseline_schema", "anyfem", "anymesher", "anygeometry"}


def test_ineligible_returns_none_for_native_strategy():
    project, _, _ = two_plates()
    mesh = number_full_mesh(project.generate_mesh(0.5, strategy="mapped"), MeshNumbering())
    native = MeshSettings.create(0.5, element_order="linear", strategy="native")
    assert capture_regional_baseline(project, native, mesh) is None


def test_automatic_strategy_is_eligible_only_when_every_face_was_mapped():
    project, faces, _ = two_plates()
    _, mesh, baseline = full(project, strategy="auto")
    chosen = mesh.hybrid_diagnostics["strategy_by_face"]
    assert {str(getattr(v, "value", v)) for v in chosen.values()} == {"mapped"}
    assert baseline is not None and baseline["route"]["mapped_faces"] == sorted(faces)


def test_programming_errors_are_not_swallowed(monkeypatch):
    project, _, _ = two_plates()
    mesh = number_full_mesh(project.generate_mesh(0.5, strategy="mapped"), MeshNumbering())

    def broken(*_args, **_kwargs):
        raise TypeError("bug")

    monkeypatch.setattr(regional_mesh, "_component_record", broken)
    with pytest.raises(TypeError):
        capture_regional_baseline(project, settings(), mesh)


def test_unchanged_project_is_a_noop_that_retains_everything():
    project, _, _ = two_plates()
    base, mesh, baseline = full(project)
    plan = plan_regional_update(project, base, baseline)
    assert plan.is_noop and "2 independent" in plan.description
    saved_baseline, payload = reopen(baseline, mesh)
    result = generate_regional_update(
        project, base, saved_baseline, payload,
        node_high_water=max(mesh.nodes), element_high_water=max(mesh.quads))
    assert result.plan.is_noop
    assert set(result.mesh.nodes) == set(mesh.nodes)
    assert set(result.retained_elements) == set(mesh.quads)
    assert result.baseline == saved_baseline


def test_only_changed_plate_is_regenerated(monkeypatch):
    import anymesher.mapped as mapped

    project, faces, edges = two_plates()
    base, mesh, baseline = full(project)
    old_elements = cells(mesh, faces[1])
    old_nodes = face_nodes(mesh, faces[1])
    calls = []
    original = mapped.generate_mesh

    def spy(geometry, **kwargs):
        calls.append(tuple(kwargs["face_ids"]))
        return original(geometry, **kwargs)

    monkeypatch.setattr(mapped, "generate_mesh", spy)
    edited = replace(base, overrides=tuple((e, 4) for e in edges[0]))
    plan = plan_regional_update(project, edited, baseline)
    assert [f for _, f in plan.changed] == [(faces[0],)]
    node_high, element_high = max(mesh.nodes), max(mesh.quads)
    saved_baseline, payload = reopen(baseline, mesh)
    result = generate_regional_update(
        project, edited, saved_baseline, payload,
        node_high_water=node_high, element_high_water=element_high)
    assert calls == [(faces[0],)]
    new = result.mesh
    assert set(result.retained_elements) == set(old_elements)
    assert cells(new, faces[1]) == old_elements
    for node in old_nodes:
        np.testing.assert_array_equal(new.nodes[node], mesh.nodes[node])
    fresh = set(new.nodes) - set(mesh.nodes)
    assert fresh and min(fresh) > node_high
    assert min(set(new.quads) - set(old_elements)) > element_high
    assert len(new.elements_of_face[faces[0]]) == 16
    # Absence is preserved, and no previous diagnostics or preparation is carried over.
    assert new.thickness_of_face == mesh.thickness_of_face
    assert not new.structural_preparation and not new.hybrid_diagnostics
    assert result.baseline is not None
    assert result.plan.changed == plan.changed


def test_geometry_edit_of_one_plate_keeps_the_other_cells_after_reopen():
    project, faces, _ = two_plates()
    base, mesh, baseline = full(project)
    saved_baseline, payload = reopen(baseline, mesh)
    untouched = cells(mesh, faces[1])

    from anyfem.io.project_file import project_from_dict, project_to_dict

    edited = project_from_dict(project_to_dict(project))
    edited.geometry.move_point(2, 1.5, 0)
    edited.geometry.move_point(3, 1.5, 1)
    plan = plan_regional_update(edited, base, saved_baseline)
    assert [f for _, f in plan.changed] == [(faces[0],)]
    assert plan.reasons[0] == "geometry changed"
    first = generate_regional_update(
        edited, base, saved_baseline, payload,
        node_high_water=max(mesh.nodes), element_high_water=max(mesh.quads))
    assert cells(first.mesh, faces[1]) == untouched
    assert max(first.mesh.nodes[n][0] for n in face_nodes(first.mesh, faces[0])) == pytest.approx(1.5)
    assert first.baseline is not None

    # A second regional update accepts the metadata of the first one.
    again = project_from_dict(project_to_dict(edited))
    again.geometry.move_point(6, 6.5, 0)
    again.geometry.move_point(7, 6.5, 1)
    saved_baseline, payload = reopen(first.baseline, first.mesh)
    kept_first_plate = cells(first.mesh, faces[0])
    second = generate_regional_update(
        again, base, saved_baseline, payload,
        node_high_water=max(first.mesh.nodes), element_high_water=max(first.mesh.quads))
    assert cells(second.mesh, faces[0]) == kept_first_plate
    assert max(second.mesh.nodes[n][0] for n in face_nodes(second.mesh, faces[1])) == pytest.approx(6.5)


def test_fingerprint_binds_full_definition_not_samples():
    project, faces, _ = two_plates()
    geometry = project.geometry
    document = regional_mesh._document(geometry)
    record = regional_mesh._component_record(geometry, document, [faces[0]], settings())
    same = regional_mesh._component_record(geometry, document, [faces[0]], settings())
    assert record["fingerprint"] == same["fingerprint"]
    # Identical sampled geometry (same points, bounds and edges) but another support definition.
    tampered = copy.deepcopy(document)
    tampered["faces"][faces[0]]["surface"] = {"type": "other-support"}
    other = regional_mesh._component_record(geometry, tampered, [faces[0]], settings())
    assert other["bounds"] == record["bounds"]
    assert other["fingerprint"] != record["fingerprint"]
    edge = record["edges"][0]
    tampered = copy.deepcopy(document)
    tampered["edges"][edge]["curve"] = {"type": "other-curve"}
    assert regional_mesh._component_record(
        geometry, tampered, [faces[0]], settings())["fingerprint"] != record["fingerprint"]


def test_global_setting_change_is_unsupported():
    project, _, _ = two_plates()
    base, _, baseline = full(project)
    finer = replace(base, target_size=0.25)
    with pytest.raises(RegionalUpdateUnsupported, match="global"):
        plan_regional_update(project, finer, baseline)


def many_plates(xs):
    """Unit plates at the given x; ids are sequential so a prefix shares every id."""
    project = Project("many plates")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", thickness=0.01, material="S355")
    faces, edges = [], []
    for x in xs:
        points = project.geometry.add_points([(x, 0, 0), (x + 1, 0, 0), (x + 1, 1, 0), (x, 1, 0)])
        boundary = project.geometry.add_polyline(points, close=True)
        face = project.geometry.add_face(boundary)
        project.assign_plate(face, "plate")
        faces.append(int(face))
        edges.append([int(e) for e in boundary])
    return project, faces, edges


def remove_last_plate(project, face):
    from anyfem.commands import DeleteEntity
    from anyfem.io.project_file import project_from_dict, project_to_dict
    from anygeometry import EntityRef

    edited = project_from_dict(project_to_dict(project))
    geometry = edited.geometry
    edges = [use.edge for use in geometry.faces[face].loop]
    vertices = [geometry.edges[edge].start for edge in edges]
    DeleteEntity(EntityRef("face", face)).do(edited)
    for edge in edges:
        if edge in geometry.edges:
            DeleteEntity(EntityRef("edge", edge)).do(edited)
    for vertex in vertices:
        if vertex in geometry.vertices:
            DeleteEntity(EntityRef("vertex", vertex)).do(edited)
    return edited


def add_plate(project, x):
    points = project.geometry.add_points([(x, 0, 0), (x + 1, 0, 0), (x + 1, 1, 0), (x, 1, 0)])
    face = int(project.geometry.add_face(project.geometry.add_polyline(points, close=True)))
    project.assign_plate(face, "plate")
    return face


def test_added_plate_is_planned_with_fresh_identity_and_impact():
    project, faces, _ = two_plates()
    base, _, baseline = full(project)
    new_face = add_plate(project, 10)
    plan = plan_regional_update(project, base, baseline)
    assert plan.added == ((new_face,),) and not plan.deleted and not plan.changed
    assert plan.retained == (0, 1) and not plan.is_noop
    assert plan.impact == {"retained": [[f] for f in faces], "regenerated": [],
                           "added": [[new_face]], "removed": []}
    assert "add new plate" in plan.description


def test_added_plate_alone_is_assembled_without_retiring_anything():
    project, faces, _ = two_plates()
    base, mesh, baseline = full(project)
    kept = {f: cells(mesh, f) for f in faces}
    new_face = add_plate(project, 10)
    saved_baseline, payload = reopen(baseline, mesh)
    node_high, element_high = max(mesh.nodes), max(mesh.quads)
    result = generate_regional_update(project, base, saved_baseline, payload,
                                      node_high_water=node_high, element_high_water=element_high)
    final = result.mesh
    assert set(mesh.nodes) <= set(final.nodes) and set(mesh.quads) <= set(final.quads)
    for face in faces:
        assert cells(final, face) == kept[face]
    added = cells(final, new_face)
    assert added and min(added) > element_high and min(face_nodes(final, new_face)) > node_high
    assert set(result.retained_elements) == set(mesh.quads)
    assert len(result.baseline["components"]) == 3


def test_removed_plate_alone_is_assembled_and_numbers_are_not_reused():
    project, faces, _ = many_plates((0, 5, 10))
    base, mesh, baseline = full(project)
    kept = {f: cells(mesh, f) for f in faces[:2]}
    gone_nodes, gone_elements = face_nodes(mesh, faces[2]), set(cells(mesh, faces[2]))
    smaller = remove_last_plate(project, faces[2])
    saved_baseline, payload = reopen(baseline, mesh)
    result = generate_regional_update(smaller, base, saved_baseline, payload,
                                      node_high_water=max(mesh.nodes), element_high_water=max(mesh.quads))
    final = result.mesh
    assert faces[2] not in final.elements_of_face
    assert not gone_nodes & set(final.nodes) and not gone_elements & set(final.quads)
    assert set(final.nodes) <= set(mesh.nodes) and set(final.quads) <= set(mesh.quads)  # nothing new
    for face in faces[:2]:
        assert cells(final, face) == kept[face]
    assert len(result.baseline["components"]) == 2


def test_removed_and_added_plate_over_the_same_material_is_refused():
    project, faces, _ = many_plates((0, 5, 10))
    base, _, baseline = full(project)
    smaller = remove_last_plate(project, faces[2])
    add_plate(smaller, 10)  # fresh ids exactly where the removed plate was
    with pytest.raises(RegionalUpdateUnsupported, match="overlaps|reuses"):
        plan_regional_update(smaller, base, baseline)


def test_simultaneous_remove_and_add_of_separate_plates_is_assembled():
    project, faces, _ = many_plates((0, 5, 10))
    base, mesh, baseline = full(project)
    smaller = remove_last_plate(project, faces[2])
    new_face = add_plate(smaller, 20)
    saved_baseline, payload = reopen(baseline, mesh)
    node_high, element_high = max(mesh.nodes), max(mesh.quads)
    result = generate_regional_update(smaller, base, saved_baseline, payload,
                                      node_high_water=node_high, element_high_water=element_high)
    assert faces[2] not in result.mesh.elements_of_face
    assert min(cells(result.mesh, new_face)) > element_high
    assert min(face_nodes(result.mesh, new_face)) > node_high


def test_added_plate_overlapping_an_existing_plate_is_refused():
    project, _, _ = two_plates()
    base, _, baseline = full(project)
    add_plate(project, 0)  # coincident with the first plate: not an independent plate
    with pytest.raises(RegionalUpdateUnsupported):
        plan_regional_update(project, base, baseline)


def test_added_plate_with_a_regenerated_neighbour_gets_fresh_numbers():
    project, faces, edges = two_plates()
    base, mesh, baseline = full(project)
    untouched = cells(mesh, faces[1])
    new_face = add_plate(project, 10)
    edited = replace(base, overrides=tuple((e, 4) for e in edges[0]))
    saved_baseline, payload = reopen(baseline, mesh)
    node_high, element_high = max(mesh.nodes), max(mesh.quads)
    result = generate_regional_update(project, edited, saved_baseline, payload,
                                      node_high_water=node_high, element_high_water=element_high)
    assert cells(result.mesh, faces[1]) == untouched
    assert result.baseline is not None and len(result.baseline["components"]) == 3
    added = cells(result.mesh, new_face)
    assert added and min(added) > element_high
    assert min(face_nodes(result.mesh, new_face)) > node_high
    assert set(result.retained_elements) == set(untouched)


def test_removed_plate_is_planned_from_absence_and_impact():
    project, faces, _ = many_plates((0, 5, 10))
    base, mesh, baseline = full(project)
    smaller = remove_last_plate(project, faces[2])
    plan = plan_regional_update(smaller, base, baseline)
    assert plan.deleted == (2,) and plan.removed_faces == ((faces[2],),)
    assert plan.retained == (0, 1) and not plan.changed and not plan.is_noop
    assert plan.impact["removed"] == [[faces[2]]] and "remove plate" in plan.description


def test_removed_plate_with_a_geometry_edit_retires_its_numbers():
    project, faces, _ = many_plates((0, 5, 10))
    base, mesh, baseline = full(project)
    kept = cells(mesh, faces[1])
    gone_nodes = face_nodes(mesh, faces[2])
    gone_elements = set(cells(mesh, faces[2]))
    smaller = remove_last_plate(project, faces[2])
    smaller.geometry.move_point(2, 1.5, 0)
    smaller.geometry.move_point(3, 1.5, 1)
    saved_baseline, payload = reopen(baseline, mesh)
    node_high, element_high = max(mesh.nodes), max(mesh.quads)
    result = generate_regional_update(smaller, base, saved_baseline, payload,
                                      node_high_water=node_high, element_high_water=element_high)
    final = result.mesh
    assert faces[2] not in final.elements_of_face
    assert not gone_nodes & set(final.nodes) and not gone_elements & set(final.quads)
    assert cells(final, faces[1]) == kept
    assert len(result.baseline["components"]) == 2
    assert min(set(final.nodes) - set(mesh.nodes)) > node_high  # retired numbers are not reused


def test_plate_still_present_under_its_id_is_an_edit_never_a_removal():
    project, faces, _ = many_plates((0, 5, 10))
    base, _, baseline = full(project)
    # The third plate is moved: its ids still exist, so there is no absence proof.
    project.geometry.move_point(10, 11.5, 0)
    plan = plan_regional_update(project, base, baseline)
    assert not plan.deleted and [f for _, f in plan.changed] == [(faces[2],)]


def test_section_assignment_is_local_not_global():
    project = Project("assign")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", thickness=0.01, material="S355")
    project.add_plate_section("thick", thickness=0.02, material="S355")
    faces = []
    for x in (0, 5):
        points = project.geometry.add_points([(x, 0, 0), (x + 1, 0, 0), (x + 1, 1, 0), (x, 1, 0)])
        face = int(project.geometry.add_face(project.geometry.add_polyline(points, close=True)))
        project.assign_plate(face, "plate")
        faces.append(face)
    base, _, baseline = full(project)
    project.assign_plate(faces[1], "thick")
    plan = plan_regional_update(project, base, baseline)
    assert [f for _, f in plan.changed] == [(faces[1],)]
    assert plan.reasons[1] == "local section assignment changed"


def test_build_pin_change_refuses_the_update(monkeypatch):
    project, _, _ = two_plates()
    base, _, baseline = full(project)
    stale = json.loads(json.dumps(baseline))
    stale["pins"]["anymesher"] = "other-build"
    with pytest.raises(RegionalUpdateUnsupported, match="pins"):
        plan_regional_update(project, base, stale)
