"""Decomposition through the command stack, and solving what it produces.

Splitting deletes faces and rewrites loops in place, so undo here exercises a
lot more than the create-only commands do.
"""

from __future__ import annotations

from copy import deepcopy

import numpy as np
import pytest
from anygeometry import GeometryError
from anygeometry.features import FeatureOutputRef

from anyfem import Project, pinned, solve_linear_static, steel
from anyfem import commands as cmd
from anyfem.geometry.entities import EntityRef
from anyfem.mesh import refine_around
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.model import BeamSection, Mass, plate_mode
from anyfem.selection import Selection


@pytest.fixture
def project():
    project = Project(name="decomposition")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    return project


@pytest.fixture
def stack(project):
    return cmd.CommandStack(project)


def boundary_edges(project):
    """Lines used by exactly one plate, i.e. the free boundary."""

    return [
        edge_id
        for edge_id in sorted(project.geometry.edges)
        if len(project.geometry.faces_using_edge(edge_id)) == 1
    ]


def plate(stack, width=4.0, height=2.0):
    points = [
        stack.run(cmd.AddPoint(x, y))
        for x, y in ((0, 0), (width, 0), (width, height), (0, height))
    ]
    face = stack.run(cmd.AddPlate(points))
    stack.run(cmd.AssignPlate(face, "plate"))
    return points, face


# ----------------------------------------------------------------------
# undo across operations that delete and rewrite
# ----------------------------------------------------------------------
def test_split_edge_undo_restores_the_face_loop(stack, project):
    _points, face = plate(stack)
    face_ref = EntityRef("face", face)
    edges = sorted(project.geometry.edges)
    before_loop = project.geometry.faces[face].loop
    before_corners = project.geometry.faces[face].corners

    stack.run(cmd.SplitEdge(edges[0], 0.5))
    (resolved_face,) = project.geometry.resolve_ref(face_ref)
    assert len(project.geometry.faces[resolved_face.id].loop) == 5

    stack.undo()
    assert project.geometry.faces[face].loop == before_loop
    assert project.geometry.faces[face].corners == before_corners
    assert sorted(project.geometry.edges) == edges


def test_split_face_undo_brings_the_original_plate_back(stack, project):
    _points, face = plate(stack)
    snapshot = sorted(project.geometry.entity_keys())

    stack.run(cmd.SplitFace(face, axis=0, fraction=0.5))
    assert face not in project.geometry.faces
    assert len(project.geometry.faces) == 2

    stack.undo()
    assert face in project.geometry.faces
    assert sorted(project.geometry.entity_keys()) == snapshot

    stack.redo()
    assert len(project.geometry.faces) == 2


def test_decomposition_redo_reproduces_the_same_ids(stack, project):
    _points, face = plate(stack)
    stack.run(cmd.StripFace(face, axis=0, count=3))
    after = sorted(project.geometry.entity_keys())

    stack.undo()
    stack.redo()
    assert sorted(project.geometry.entity_keys()) == after


def test_strip_undo_removes_the_beam_sections_it_assigned(stack, project):
    project.add_beam_section(
        BeamSection(
            name="stiff", profile="T-bar", material="S355",
            web_height=0.2, web_thickness=0.01,
            flange_width=0.1, flange_thickness=0.012,
        )
    )
    _points, face = plate(stack, 6.0, 2.0)

    def binding_state():
        return {
            "assignments": dict(project.section_assignments),
            "regions": tuple(project.regions),
            "face_sections": dict(project.face_sections),
            "edge_sections": dict(project.edge_sections),
            "face_assignment_ids": dict(project.face_assignment_ids),
            "edge_assignment_ids": dict(project.edge_assignment_ids),
            "singleton_cache": dict(project._singleton_region_cache),
            "singleton_cache_size": project._singleton_region_cache_size,
            "members": dict(project.geometry.members),
            "member_edge_uses": dict(project.geometry.member_edge_uses),
        }

    before = binding_state()

    strips, dividers = stack.run(
        cmd.StripFace(face, axis=0, count=3, section="stiff")
    )
    assert len(strips) == 3
    assert set(project.beam_edges) == set(dividers)
    strip_feature = project.geometry.features.records[-1]
    expected_anchors = {
        FeatureOutputRef(strip_feature.feature_id, key, "edge")
        for key in strip_feature.outputs
        if key.startswith("divider/")
    }
    beam_assignments = {
        identifier: assignment
        for identifier, assignment in project.section_assignments.items()
        if assignment.kind == "beam"
    }
    assigned_anchors = {
        anchor
        for assignment in beam_assignments.values()
        for anchor in project.regions[assignment.region.id].definition.anchors
    }
    assert assigned_anchors == expected_anchors
    assert {
        project.edge_assignment_ids[divider] for divider in dividers
    } == set(beam_assignments)
    assert {
        use.edge_id for use in project.geometry.member_edge_uses.values()
    } == set(dividers)
    after = binding_state()

    stack.undo()
    assert binding_state() == before

    stack.redo()
    assert binding_state() == after


def test_sectioned_strip_divider_remains_editable_through_feature_replay(
    stack, project
):
    project.add_beam_section(
        BeamSection(
            name="stiff", profile="T-bar", material="S355",
            web_height=0.2, web_thickness=0.01,
            flange_width=0.1, flange_thickness=0.012,
        )
    )
    _points, face = plate(stack, 6.0, 2.0)
    _strips, dividers = stack.run(
        cmd.StripFace(face, axis=0, count=3, section="stiff")
    )
    divider_ref = EntityRef("edge", dividers[0])
    assignment_id = project.edge_assignment_ids[dividers[0]]
    strip_feature = project.geometry.features.records[-1]
    assert "section" not in strip_feature.parameters

    def ownership_state():
        return {
            "assignments": dict(project.section_assignments),
            "regions": tuple(project.regions),
            "face_sections": dict(project.face_sections),
            "edge_sections": dict(project.edge_sections),
            "face_assignment_ids": dict(project.face_assignment_ids),
            "edge_assignment_ids": dict(project.edge_assignment_ids),
            "singleton_cache": dict(project._singleton_region_cache),
            "singleton_cache_size": project._singleton_region_cache_size,
            "members": dict(project.geometry.members),
            "member_edge_uses": dict(project.geometry.member_edge_uses),
        }

    before_split = ownership_state()
    stack.run(cmd.SplitEdge(dividers[0], 0.5))
    halves = tuple(item.id for item in project.geometry.resolve_ref(divider_ref))

    assert len(halves) == 2
    assert all(project.edge_sections[edge_id] == "stiff" for edge_id in halves)
    assert all(
        project.edge_assignment_ids[edge_id] == assignment_id
        for edge_id in halves
    )
    assert {
        use.edge_id for use in project.geometry.member_edge_uses.values()
    } == set(project.beam_edges)
    assert all(
        member.metadata.get("anyfem.section_assignment_owner") == "beam"
        for member in project.geometry.members.values()
    )
    split_feature = project.geometry.features.records[-1]
    assert "section" not in split_feature.parameters
    after_split = ownership_state()

    stack.undo()
    assert ownership_state() == before_split

    stack.redo()
    assert ownership_state() == after_split


def test_punching_a_hole_carries_the_section_to_the_patches(stack, project):
    _points, face = plate(stack, 4.0, 3.0)
    assert project.face_sections[face] == "plate"

    patches, _arcs = stack.run(cmd.PunchHole(face, (2.0, 1.5, 0.0), 0.6))
    # The plate the user sectioned became four; none of them should be left
    # without a thickness.
    assert all(project.face_sections[patch] == "plate" for patch in patches)
    assert face not in project.face_sections

    stack.undo()
    assert project.face_sections == {face: "plate"}


def test_butterfly_hole_is_replayable_after_persistence(stack, project):
    _points, face = plate(stack, 4.0, 3.0)
    patches, boundaries = stack.run(
        cmd.PunchHole(face, (2.0, 1.5, 0.0), 0.6)
    )
    feature = project.geometry.features.records[-1]

    assert feature.kind == "anyfem.mesh.butterfly_hole"
    assert feature.materialization_checksum is not None
    assert {item.id for item in feature.outputs.values() if item.kind == "face"} == set(
        patches
    )
    assert {
        item.id for item in feature.outputs.values() if item.kind == "edge"
    } == set(boundaries)

    encoded = project_to_dict(project)
    reopened = project_from_dict(deepcopy(encoded))
    reopened_feature = reopened.geometry.features.get(feature.feature_id)
    assert reopened_feature.state == "frozen"

    replay = reopened.regenerate_geometry_features()
    assert replay.success, replay.diagnostic
    replayed = reopened.geometry.features.get(feature.feature_id)
    assert replayed.state == "ok"
    assert len([item for item in replayed.outputs.values() if item.kind == "face"]) == 4

    def design_semantics(model):
        payload = project_to_dict(model)
        payload["geometry"].pop("checksum")
        payload["geometry"].pop("id_state")
        payload["geometry"].pop("revision")
        return payload

    replay_stack = cmd.CommandStack(reopened)
    before = design_semantics(reopened)
    replay_stack.run(
        cmd.EditFeature(
            feature.feature_id,
            parameters={"centre": (2.0, 1.5, 0.0), "radius": 0.5},
        )
    )
    assert reopened.geometry.features.get(feature.feature_id).parameters["radius"] == 0.5
    assert replay_stack.undo()
    assert design_semantics(reopened) == before
    assert replay_stack.redo()
    assert reopened.geometry.features.get(feature.feature_id).parameters["radius"] == 0.5


def test_explicit_member_name_collision_is_never_assignment_provenance(
    stack, project
):
    project.add_beam_section(
        BeamSection(
            name="stiff", profile="T-bar", material="S355",
            web_height=0.2, web_thickness=0.01,
            flange_width=0.1, flange_thickness=0.012,
        )
    )
    _points, face = plate(stack, 6.0, 2.0)
    _strips, dividers = stack.run(
        cmd.StripFace(face, axis=0, count=3, section="stiff")
    )
    edge_id = dividers[0]
    owned = next(
        member
        for member in project.geometry.members.values()
        if any(
            project.geometry.member_edge_uses[use_id].edge_id == edge_id
            for use_id in member.edge_use_ids
        )
    )
    part_id = owned.part_id
    project.geometry.remove_member(owned.id)
    if part_id in project.geometry.parts:
        project.geometry.remove_part(part_id)
    explicit_id = project.geometry.add_member(
        (edge_id,), name=f"assigned beam {edge_id}"
    )

    # The label deliberately collides with compatibility-owner labels.  Only
    # immutable metadata grants deletion authority, so replay must preserve it
    # or fail closed without changing the live document.
    report = project.regenerate_geometry_features()

    assert report.success, report.diagnostic
    assert explicit_id in project.geometry.members
    assert not project.geometry.members[explicit_id].metadata
    assert [
        member.id
        for member in project.geometry.members.values()
        if any(
            project.geometry.member_edge_uses[use_id].edge_id == edge_id
            for use_id in member.edge_use_ids
        )
    ] == [explicit_id]


def test_feature_edit_and_rejected_delete_restore_complete_assignment_state(
    stack, project
):
    project.add_beam_section(
        BeamSection(
            name="stiff", profile="T-bar", material="S355",
            web_height=0.2, web_thickness=0.01,
            flange_width=0.1, flange_thickness=0.012,
        )
    )
    _points, face = plate(stack, 6.0, 2.0)
    stack.run(cmd.StripFace(face, axis=0, count=3, section="stiff"))
    point_feature = project.geometry.features.records[0]
    strip_feature = project.geometry.features.records[-1]

    def owner_state():
        return {
            "assignments": dict(project.section_assignments),
            "regions": tuple(project.regions),
            "compatibility": project._section_compatibility_snapshot(),
            "cache": dict(project._singleton_region_cache),
            "cache_size": project._singleton_region_cache_size,
            "members": dict(project.geometry.members),
            "uses": dict(project.geometry.member_edge_uses),
        }

    before = owner_state()
    stack.run(
        cmd.EditFeature(
            point_feature.feature_id,
            parameters={"position": (-0.1, 0.0, 0.0)},
        )
    )
    after = owner_state()
    assert after != before
    assert stack.undo()
    assert owner_state() == before
    assert stack.redo()
    assert owner_state() == after

    frozen = project_to_dict(project)
    with pytest.raises(
        GeometryError,
        match="(?:canonical section ownership|cannot preserve feature lineage)",
    ):
        stack.run(cmd.DeleteFeature(strip_feature.feature_id))
    assert project_to_dict(project) == frozen


def test_revolve_through_the_command_stack(stack, project):
    start = stack.run(cmd.AddPoint(2.0, 0.0, 0.0))
    end = stack.run(cmd.AddPoint(2.0, 0.0, 3.0))
    edge = stack.run(cmd.AddLine(start, end))

    faces = stack.run(
        cmd.Revolve(
            edge_ids=[edge],
            axis_point=(0, 0, 0),
            axis_direction=(0, 0, 1),
            angle=2.0 * np.pi,
        )
    )
    assert len(faces) == 4

    stack.undo()
    assert not project.geometry.faces
    stack.redo()
    assert len(project.geometry.faces) == 4


def test_set_corners_is_undoable(stack, project):
    _points, face = plate(stack)
    face_ref = EntityRef("face", face)
    edges = sorted(project.geometry.edges)
    stack.run(cmd.SplitEdge(edges[0], 0.5))
    (split_face,) = project.geometry.resolve_ref(face_ref)
    before = project.geometry.faces[split_face.id].corners

    stack.run(cmd.SetFaceCorners(face, (0, 1, 2, 3)))
    (updated_face,) = project.geometry.resolve_ref(face_ref)
    assert project.geometry.faces[updated_face.id].corners == (0, 1, 2, 3)
    stack.undo()
    (restored_face,) = project.geometry.resolve_ref(face_ref)
    assert project.geometry.faces[restored_face.id].corners == before


def test_triangle_command_round_trips(stack, project):
    points = [
        stack.run(cmd.AddPoint(x, y)) for x, y in ((0, 0), (2, 0), (1, 1.6))
    ]
    edges = stack.run(cmd.AddPolyline(points, close=True))

    faces = stack.run(cmd.TriangleToQuads(edge_ids=edges))
    assert len(faces) == 3
    stack.undo()
    assert not project.geometry.faces
    assert sorted(project.geometry.edges) == sorted(edges)


# ----------------------------------------------------------------------
# seeding under heavy decomposition
# ----------------------------------------------------------------------
def test_seeding_settles_on_a_butterfly(stack, project):
    _points, face = plate(stack, 4.0, 3.0)
    stack.run(cmd.PunchHole(face, (2.0, 1.5, 0.0), 0.6))

    mesh = project.generate_mesh(0.3)
    assert mesh.seeding is not None
    assert mesh.seeding.sweeps <= 5
    for face_id in project.geometry.faces:
        sides = project.geometry.faces[face_id].sides()
        assert mesh.seeding.side_divisions(sides[0]) == mesh.seeding.side_divisions(
            sides[2]
        )
        assert mesh.seeding.side_divisions(sides[1]) == mesh.seeding.side_divisions(
            sides[3]
        )


def test_every_patch_stays_conformal_with_its_neighbours(stack, project):
    _points, face = plate(stack, 4.0, 3.0)
    patches, _arcs = stack.run(cmd.PunchHole(face, (2.0, 1.5, 0.0), 0.6))
    mesh = project.generate_mesh(0.3)

    # Neighbouring patches meet only along the spokes they share; any node in
    # two patches must belong to an edge they both use.
    for first in patches:
        for second in patches:
            if first >= second:
                continue
            shared_nodes = set(
                mesh.nodes_on(project.geometry.entity_ref("face", first))
            ) & set(mesh.nodes_on(project.geometry.entity_ref("face", second)))
            shared_edges = {
                item.edge for item in project.geometry.faces[first].loop
            } & {item.edge for item in project.geometry.faces[second].loop}
            expected = set()
            for edge_id in shared_edges:
                expected |= set(
                    mesh.nodes_on(project.geometry.entity_ref("edge", edge_id))
                )
            assert shared_nodes == expected


def test_seeding_propagates_through_a_strip_stack(stack, project):
    _points, face = plate(stack, 8.0, 2.0)
    strips, dividers = stack.run(cmd.StripFace(face, axis=0, count=4))

    mesh = project.generate_mesh(0.5)
    counts = {mesh.seeding[divider] for divider in dividers}
    # Every divider spans the plate the same way, so they share a count.
    assert len(counts) == 1
    assert set(mesh.seeding.divisions) == set(mesh.nodes_of_edge)
    assert set(mesh.seeding.classes) == set(mesh.nodes_of_edge)
    assert set(mesh.seeding.classes.values()) <= set(mesh.nodes_of_edge)
    assert len(mesh.quads) > 0


# ----------------------------------------------------------------------
# solving decomposed models
# ----------------------------------------------------------------------
def test_a_holed_plate_solves(stack, project):
    _points, face = plate(stack, 4.0, 3.0)
    outer = sorted(project.geometry.edges)
    stack.run(cmd.PunchHole(face, (2.0, 1.5, 0.0), 0.6))

    for edge in outer:
        project.add_support(pinned(project.edge(edge)))
    for patch in project.geometry.faces:
        project.load_case().add_pressure(project.face(patch), 10_000.0)

    solution = solve_linear_static(project, target_size=0.3)
    _node, magnitude = solution.max_translation()
    assert np.isfinite(magnitude)
    assert magnitude > 0.0


def test_a_holed_plate_is_softer_than_a_solid_one(stack, project):
    """Removing material has to increase the deflection, not decrease it."""

    _points, face = plate(stack, 4.0, 3.0)
    outer = sorted(project.geometry.edges)
    for edge in outer:
        project.add_support(pinned(project.edge(edge)))
    project.load_case().add_pressure(project.face(face), 10_000.0)
    solid = solve_linear_static(project, target_size=0.25).max_translation()[1]

    holed_project = Project(name="holed")
    holed_project.add_material(steel("S355", 0.010))
    holed_project.add_plate_section("plate", thickness=0.010, material="S355")
    holed_stack = cmd.CommandStack(holed_project)
    _points, holed_face = plate(holed_stack, 4.0, 3.0)
    holed_outer = sorted(holed_project.geometry.edges)
    holed_stack.run(cmd.PunchHole(holed_face, (2.0, 1.5, 0.0), 0.6))
    for edge in holed_outer:
        holed_project.add_support(pinned(holed_project.edge(edge)))
    for patch in holed_project.geometry.faces:
        holed_project.load_case().add_pressure(holed_project.face(patch), 10_000.0)
    holed = solve_linear_static(holed_project, target_size=0.25).max_translation()[1]

    assert holed > solid


def test_a_revolved_cylinder_solves(stack, project):
    start = stack.run(cmd.AddPoint(2.0, 0.0, 0.0))
    end = stack.run(cmd.AddPoint(2.0, 0.0, 3.0))
    edge = stack.run(cmd.AddLine(start, end))
    faces = stack.run(
        cmd.Revolve(
            edge_ids=[edge],
            axis_point=(0, 0, 0),
            axis_direction=(0, 0, 1),
            angle=2.0 * np.pi,
        )
    )
    for face in faces:
        stack.run(cmd.AssignPlate(face, "plate"))

    # Clamp the bottom rim and push the shell outwards.
    bottom = [
        edge_id
        for edge_id in project.geometry.edges
        if all(
            abs(project.geometry.vertex_position(vertex)[2]) < 1e-9
            for vertex in (
                project.geometry.edges[edge_id].start,
                project.geometry.edges[edge_id].end,
            )
        )
    ]
    assert bottom
    for edge_id in bottom:
        project.add_support(pinned(project.edge(edge_id)))
    for face in faces:
        project.load_case().add_pressure(project.face(face), 50_000.0)

    solution = solve_linear_static(project, target_size=0.5)
    _node, magnitude = solution.max_translation()
    assert np.isfinite(magnitude)
    assert magnitude > 0.0


def test_a_stiffened_plate_is_stiffer_than_a_bare_one(stack, project):
    """The point of the stiffener tooling: the stiffener has to do something."""

    project.add_beam_section(
        BeamSection(
            name="stiff", profile="T-bar", material="S355",
            web_height=0.20, web_thickness=0.010,
            flange_width=0.10, flange_thickness=0.012,
            web_direction=(0.0, 0.0, 1.0),
        )
    )
    _points, face = plate(stack, 6.0, 2.0)
    strips, dividers = stack.run(cmd.StripFace(face, axis=0, count=3))
    for strip in strips:
        stack.run(cmd.AssignPlate(strip, "plate"))
    # Support the outer boundary: the lines used by exactly one plate.
    for edge in boundary_edges(project):
        project.add_support(pinned(project.edge(edge)))
    for strip in strips:
        project.load_case().add_pressure(project.face(strip), 10_000.0)

    mesh = project.generate_mesh(0.25)
    bare = solve_linear_static(project, mesh=mesh).max_translation()[1]

    for divider in dividers:
        project.assign_beam(divider, "stiff")
    stiffened = solve_linear_static(
        project, target_size=0.25
    ).max_translation()[1]

    assert stiffened < bare


# ----------------------------------------------------------------------
# attributes survive being cut up
# ----------------------------------------------------------------------
def test_a_support_on_a_split_line_follows_both_halves(stack, project):
    _points, face = plate(stack)
    edge = sorted(project.geometry.edges)[0]
    stack.run(cmd.AddSupport(pinned(project.edge(edge))))

    vertex = stack.run(cmd.SplitEdge(edge, 0.5))
    assert vertex in project.geometry.vertices

    halves = [
        edge_id
        for edge_id in project.geometry.edges
        if vertex in (
            project.geometry.edges[edge_id].start,
            project.geometry.edges[edge_id].end,
        )
    ]
    supported = {support.ref.id for support in project.supports}
    assert supported == set(halves)

    stack.undo()
    assert [support.ref.id for support in project.supports] == [edge]


def test_a_pressure_on_a_split_plate_follows_both_halves(stack, project):
    _points, face = plate(stack)
    project.load_case().add_pressure(project.face(face), 10_000.0)

    faces = stack.run(cmd.SplitFace(face, axis=0, fraction=0.5))
    pressures = project.load_case().pressures
    assert {load.ref.id for load in pressures} == set(faces)
    # Each half keeps the same intensity, because a pressure is per area.
    assert all(load.value == 10_000.0 for load in pressures)

    stack.undo()
    assert [load.ref.id for load in project.load_case().pressures] == [face]


def test_a_beam_section_on_a_split_line_follows_both_halves(stack, project):
    project.add_beam_section(
        BeamSection(
            name="fb", profile="Flatbar", material="S355",
            flange_width=0.05, flange_thickness=0.02,
        )
    )
    _points, _face = plate(stack)
    edge = sorted(project.geometry.edges)[0]
    stack.run(cmd.AssignBeam(edge, "fb"))

    stack.run(cmd.SplitEdge(edge, 0.4))
    assert len(project.beam_edges) == 2
    assert all(
        project.edge_sections[edge_id] == "fb" for edge_id in project.beam_edges
    )

    stack.undo()
    assert project.beam_edges == [edge]


def test_a_point_load_follows_one_replacement_only(stack, project):
    """A point load cannot be shared out; duplicating it would double it."""

    points, face = plate(stack)
    stack.run(cmd.AddPointLoad(project.point(points[0]), force=(0, 0, -1000.0)))
    before = len(project.load_case().point_loads)

    stack.run(cmd.SplitFace(face, axis=0, fraction=0.5))
    assert len(project.load_case().point_loads) == before


def test_a_stripped_plate_keeps_its_pressure_everywhere(stack, project):
    _points, face = plate(stack, 6.0, 2.0)
    project.load_case().add_pressure(project.face(face), 8_000.0)

    strips, _dividers = stack.run(cmd.StripFace(face, axis=0, count=3))
    loaded = {load.ref.id for load in project.load_case().pressures}
    assert loaded == set(strips)


def test_punching_a_hole_keeps_the_plate_supported_and_loaded(stack, project):
    _points, face = plate(stack, 4.0, 3.0)
    project.load_case().add_pressure(project.face(face), 10_000.0)
    stack.run(cmd.AddSupport(pinned(project.face(face))))

    patches, _arcs = stack.run(cmd.PunchHole(face, (2.0, 1.5, 0.0), 0.6))
    assert {load.ref.id for load in project.load_case().pressures} == set(patches)
    assert {support.ref.id for support in project.supports} == set(patches)
    assert all(project.face_sections[patch] == "plate" for patch in patches)


def test_every_geometry_bound_attribute_follows_a_split(stack, project):
    _points, face = plate(stack)
    reference = project.face(face)
    project.add_mass(Mass(reference, 120.0, name="payload"))
    project.add_imperfection(
        plate_mode(reference, amplitude=0.002, name="initial shape")
    )
    project.add_refinement(
        refine_around(reference, size=0.05, radius=0.2, name="detail")
    )
    project.load_case().add_surface_traction(reference, (10.0, 20.0, 30.0))

    faces = stack.run(cmd.SplitFace(face, axis=0, fraction=0.5))
    expected = set(faces)

    assert {item.ref.id for item in project.masses} == expected
    assert sum(item.value for item in project.masses) == pytest.approx(120.0)
    assert {item.ref.id for item in project.imperfections} == expected
    assert {item.ref.id for item in project.refinements} == expected
    assert {
        item.ref.id for item in project.load_case().surface_tractions
    } == expected

    stack.undo()
    assert [(item.ref.id, item.value) for item in project.masses] == [(face, 120.0)]
    assert [item.ref.id for item in project.imperfections] == [face]
    assert [item.ref.id for item in project.refinements] == [face]
    assert [
        item.ref.id for item in project.load_case().surface_tractions
    ] == [face]


def test_selection_follows_split_undo_and_redo(stack, project):
    _points, face = plate(stack)
    selection = Selection(mode="face")
    selection.select(project.face(face))
    stack.selection = selection

    faces = stack.run(cmd.SplitFace(face, axis=0, fraction=0.5))
    assert set(selection.items) == {project.face(item) for item in faces}

    stack.undo()
    assert selection.items == [project.face(face)]

    stack.redo()
    assert set(selection.items) == {project.face(item) for item in faces}


def test_selection_follows_a_cascading_strip_replacement_log(stack, project):
    _points, face = plate(stack)
    selection = Selection(mode="face")
    selection.select(project.face(face))
    stack.selection = selection

    strips, _dividers = stack.run(cmd.StripFace(face, axis=0, count=4))

    assert set(selection.items) == {project.face(item) for item in strips}
    assert all(item.id in project.geometry.faces for item in selection.items)

    stack.undo()
    assert selection.items == [project.face(face)]

    stack.redo()
    assert set(selection.items) == {project.face(item) for item in strips}
