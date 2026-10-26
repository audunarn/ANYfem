"""Loft integrates the geometry owner with document history and persistence."""
import pytest
import numpy as np

from anyfem import commands
from anyfem.model.project import Project
from anyfem.io import project_from_dict, project_to_dict


def profiles(project):
    geometry = project.geometry
    points = [geometry.add_point(*p) for p in ((0, 0, 0), (2, 0, 0), (0, 1, 1), (3, 1, 1))]
    return geometry.add_line(*points[:2]), geometry.add_line(*points[2:])


def test_loft_command_history_persistence_edit():
    project = Project()
    first, second = profiles(project)
    source = [first]
    command = commands.Loft(source, (second,))
    source.clear()
    assert command.first_edges == (first,)
    faces = command.do(project)
    assert faces
    record = project.geometry.features.records[-1]
    assert record.kind == "geometry.loft"
    assert set((first, second)) <= set(project.geometry.edges)
    command.undo(project)
    assert not project.geometry.faces
    assert set(project.geometry.edges) == {first, second}
    command.redo(project)
    assert set(project.geometry.faces) == set(faces)
    from anyfem import steel, Support
    from anygeometry import EntityRef
    project.add_material(steel())
    project.add_plate_section("loft plate", .01, "S355")
    project.assign_plates(faces, "loft plate")
    connector = project.geometry.faces[faces[0]].loop[1].edge
    project.add_support(Support("loft edge", EntityRef("edge", connector), {"uz": 0.0}))
    original_slots = dict(record.outputs)
    original_edge_points = {key: project.geometry.sample_edge(ref.id, np.linspace(0, 1, 5))
                            for key, ref in original_slots.items() if ref.kind == "edge"}
    original_normal = project.geometry.face_normal(faces[0], .5, .5)
    restored = project_from_dict(project_to_dict(project))
    assert restored.geometry.features.records[-1].kind == "geometry.loft"
    edit = commands.EditFeature(record.feature_id, parameters={**record.parameters, "flip_normal": True})
    edit.do(restored)
    assert restored.geometry.features.get(record.feature_id).parameters["flip_normal"] is True
    current_slots = dict(restored.geometry.features.get(record.feature_id).outputs)
    assert current_slots.keys() == original_slots.keys()
    assert {first, second} <= set(restored.geometry.edges)
    for key, old in original_slots.items():
        assert tuple(restored.geometry.resolve_ref(old)) == (current_slots[key],)
    for key, points in original_edge_points.items():
        np.testing.assert_array_equal(restored.geometry.sample_edge(current_slots[key].id, np.linspace(0, 1, 5)), points)
    changed_face = restored.geometry.resolve_ref(EntityRef("face", faces[0]))[0]
    np.testing.assert_allclose(restored.geometry.face_normal(changed_face.id, .5, .5), -original_normal)
    for face in faces:
        replacement = restored.geometry.resolve_ref(EntityRef("face", face))[0]
        assert restored.face_sections[replacement.id] == "loft plate"
    from anyfem.solve.build import _attribute_targets
    support = restored.supports[0]
    expected_connector = restored.geometry.resolve_ref(EntityRef("edge", connector))[0]
    assert _attribute_targets(restored, support.region, support.ref) == (expected_connector,)
    assert dict(support.constraints) == {"uz": 0.0}
    reopened = project_from_dict(project_to_dict(restored))
    assert reopened.face_sections == restored.face_sections
    from dataclasses import replace
    assert reopened.supports == [replace(restored.supports[0], ref=expected_connector)]
    support = reopened.supports[0]
    assert _attribute_targets(reopened, support.region, support.ref) == (expected_connector,)
    from anyfem.solve.build import build_fe_model
    mesh = reopened.generate_mesh(.5)
    built = build_fe_model(reopened, mesh, load_case=None, require_loads=False, require_supports=False)
    conditions = [bc for bc in built.fe_model.boundary_conditions if dict(bc.dof_constraints) == {"uz": 0.0}]
    assert len(conditions) == 1
    assert set(conditions[0].node_ids) == set(mesh.nodes_of_edge[expected_connector.id])
    for key, old in original_slots.items():
        assert tuple(reopened.geometry.resolve_ref(old)) == (current_slots[key],)
    edit.undo(restored)
    assert restored.geometry.features.get(record.feature_id).parameters["flip_normal"] is False
    assert dict(restored.geometry.features.get(record.feature_id).outputs) == original_slots
    assert restored.supports[0].ref == EntityRef("edge", connector)
    assert all(restored.face_sections[face] == "loft plate" for face in faces)


def test_invalid_loft_does_not_publish_a_feature():
    project = Project()
    first, second = profiles(project)
    before = project_to_dict(project)
    with pytest.raises((ValueError, RuntimeError)):
        commands.Loft((first,), ()).do(project)
    assert project_to_dict(project) == before


def test_invalid_existing_loft_edit_preserves_complete_project():
    from anyfem import steel, Support
    from anygeometry import EntityRef
    project = Project()
    first, second = profiles(project)
    faces = commands.Loft((first,), (second,)).do(project)
    record = project.geometry.features.records[-1]
    project.add_material(steel())
    project.add_plate_section("plate", .01, "S355")
    project.assign_plates(faces, "plate")
    connector = project.geometry.faces[faces[0]].loop[1].edge
    project.add_support(Support("fixed", EntityRef("edge", connector), {"uz": 0.0}))
    before = project_to_dict(project)
    with pytest.raises((ValueError, RuntimeError)):
        commands.EditFeature(record.feature_id, inputs={**record.inputs, "second_edges": ()}).do(project)
    assert project_to_dict(project) == before


def test_source_topology_change_blocks_dependent_loft_without_remapping():
    from anygeometry import EntityRef
    project = Project()
    geometry = project.geometry
    points = [geometry.add_point(*p) for p in ((0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 1), (2, 1, 1))]
    second = geometry.add_line(*points[3:])
    first = commands.AddPolyline(points[:3]).do(project)
    source = geometry.features.records[-1]
    old_faces = commands.Loft(first, (second,)).do(project)
    loft_id = geometry.features.records[-1].feature_id
    before = project_to_dict(project)
    edit = commands.EditFeature(source.feature_id, inputs={"vertices": tuple(
        EntityRef("vertex", identifier) for identifier in points[:2]
    )})
    edit.do(project)
    blocked = geometry.features.get(loft_id)
    assert blocked.state == "blocked", (blocked.state, blocked.diagnostic)
    assert blocked.diagnostic
    assert not blocked.outputs
    assert not geometry.faces
    assert all(not geometry.resolve_ref(EntityRef("face", face)) for face in old_faces)
    edit.undo(project)
    after = project_to_dict(project)
    # Undo restores intent/topology, while the owner deliberately retains
    # allocator high-water marks rather than reusing retired entity IDs.
    assert after["geometry"]["revision"] >= before["geometry"]["revision"]
    assert after["geometry"]["id_state"].keys() == before["geometry"]["id_state"].keys()
    for kind, counter in before["geometry"]["id_state"].items():
        assert after["geometry"]["id_state"][kind] >= counter
    for document in (before, after):
        for key in ("checksum", "revision", "id_state"):
            document["geometry"].pop(key, None)
    assert after == before
