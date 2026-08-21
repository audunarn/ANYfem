"""Canonical, region-backed section assignment contracts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from uuid import UUID

import pytest

from anyfem import DocumentSession, Project, Region, RegionRef, steel
from anyfem import commands as cmd
from anyfem.io import project_from_dict, project_to_dict
from anyfem.model import ManualRegion, ProjectError, SectionAssignment
from anygeometry import FeatureOutputRef


def _plate_project() -> tuple[Project, int]:
    project = Project("section scopes")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("deck", 0.01, "S355")
    points = project.geometry.add_points(
        ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0),
         (2.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    )
    return project, project.geometry.add_plate(points)


def test_legacy_assignment_is_a_stable_hidden_singleton_and_round_trips():
    project, face = _plate_project()
    project.assign_plate(face, "deck")

    assignment = next(iter(project.section_assignments.values()))
    UUID(assignment.id)
    assert assignment.legacy_singleton
    assert assignment.section_id == project.plate_sections["deck"].id
    assert project.regions[assignment.region.id].hidden
    assert project.face_sections == {face: "deck"}
    assert project.face_assignment_ids == {face: assignment.id}

    document = project_to_dict(project)
    assert document["assignments"]["sections"] == [assignment.to_dict()]
    restored = project_from_dict(document)
    assert restored.section_assignments == {assignment.id: assignment}
    assert restored.face_sections == {face: "deck"}
    assert restored.face_assignment_ids == {face: assignment.id}


def test_reassigning_a_reopened_feature_plate_replaces_legacy_scope():
    """Historical ``face`` output keys may normalize to ``face/0`` on load."""

    project = Project("reopened feature plate")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", 0.01, "S355")
    project.add_plate_section("replacement", 0.02, "S355")
    session = DocumentSession(project)
    points = tuple(
        session.execute(cmd.AddPoint(x, y))
        for x, y in ((0, 0), (2, 0), (2, 2), (0, 2))
    )
    face = session.execute(cmd.AddPlate(points))
    session.execute(cmd.AssignPlate(face, "plate"))

    restored = project_from_dict(project_to_dict(project))
    old_assignment = next(iter(restored.section_assignments.values()))
    old_region = old_assignment.region
    restored_session = DocumentSession(restored)

    restored_session.execute(cmd.AssignPlate(face, "replacement"))

    assert restored.face_sections == {face: "replacement"}
    assert len(restored.section_assignments) == 1
    replacement = next(iter(restored.section_assignments.values()))
    assert replacement.id == old_assignment.id
    assert replacement.region == old_region
    assert restored.resolve_section_assignments() == ()

    assert restored_session.undo()
    assert restored.face_sections == {face: "plate"}
    assert restored.section_assignments == {old_assignment.id: old_assignment}
    assert restored_session.redo()
    assert restored.face_sections == {face: "replacement"}
    assert restored.resolve_section_assignments() == ()


def test_v3_direct_assignments_migrate_deterministically_to_regions():
    project, face = _plate_project()
    project.assign_plate(face, "deck")
    legacy = project_to_dict(project)
    legacy["anyfem"]["format"] = 3
    legacy["meshing"].pop("native_backend")
    legacy["anyfem"].pop("document_id")
    legacy.pop("ownership")
    legacy.pop("assignments")
    legacy.pop("assignment_ids")
    legacy.pop("regions")

    first = project_from_dict(deepcopy(legacy))
    second = project_from_dict(deepcopy(legacy))
    first_assignment = next(iter(first.section_assignments.values()))
    second_assignment = next(iter(second.section_assignments.values()))

    assert first_assignment.id == second_assignment.id
    assert first_assignment.region.id == second_assignment.region.id
    assert first.face_sections == second.face_sections == {face: "deck"}
    assert project_to_dict(first)["anyfem"]["format"] == 7


def test_feature_output_assignment_follows_regeneration_and_blocks_suppression():
    project = Project("feature section")
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("deck", 0.01, "S355")
    session = DocumentSession(project)
    feature = session.execute(
        cmd.AddFeature(
            "generator.plate",
            parameters={"length": 2.0, "width": 1.0},
        )
    )
    output = FeatureOutputRef(feature.feature_id, "face/1", "face")
    region = project.regions.add(
        Region(
            "Feature plate",
            "geometry",
            "face",
            ManualRegion((output,)),
        )
    )
    assignment = project.assign_plate_region(RegionRef(region.id), "deck")
    original_face = next(iter(project.face_sections))

    session.execute(
        cmd.EditFeature(
            feature.feature_id,
            parameters={"length": 3.0, "width": 1.0},
        )
    )
    assert project.resolve_section_assignments() == ()
    edited_face = next(iter(project.face_sections))
    assert edited_face != original_face
    assert project.face_assignment_ids == {edited_face: assignment.id}

    session.execute(cmd.SuppressFeature(feature.feature_id))
    problems = project.resolve_section_assignments()
    assert any("is unresolved" in problem for problem in problems)
    assert project.face_sections == {}
    with pytest.raises(ProjectError, match="invalid section assignments"):
        project.generate_mesh(0.5)

    session.undo()
    assert project.resolve_section_assignments() == ()
    assert project.face_assignment_ids == {edited_face: assignment.id}


def test_singleton_assignment_expands_by_explicit_split_lineage():
    project, face = _plate_project()
    project.assign_plate(face, "deck")
    assignment_id = project.face_assignment_ids[face]
    session = DocumentSession(project)

    replacements = session.execute(cmd.SplitFace(face, axis=0, fraction=0.5))
    assert project.resolve_section_assignments() == ()
    assert set(project.face_sections) == set(replacements)
    assert set(project.face_assignment_ids.values()) == {assignment_id}
    assert set(project.section_assignments) == {assignment_id}

    assert session.undo()
    assert project.resolve_section_assignments() == ()
    assert project.face_sections == {face: "deck"}
    assert project.face_assignment_ids == {face: assignment_id}


def test_overlap_is_diagnostic_and_public_apply_is_atomic():
    project, face = _plate_project()
    project.assign_plate(face, "deck")
    region = project.regions.add(
        Region(
            "Named plate",
            "geometry",
            "face",
            ManualRegion((project.face(face),)),
        )
    )
    before = dict(project.section_assignments)
    before_maps = dict(project.face_sections)

    with pytest.raises(ProjectError, match="overlap on face"):
        project.assign_plate_region(RegionRef(region.id), "deck")

    assert project.section_assignments == before
    assert project.face_sections == before_maps

    extra = SectionAssignment(
        "plate",
        project.plate_sections["deck"].id,
        RegionRef(region.id),
        name="Overlapping deck",
    )
    project.section_assignments[extra.id] = extra
    assert any(
        "overlap on face" in problem
        for problem in project.resolve_section_assignments(strict=False)
    )
    with pytest.raises(ProjectError, match="overlap on face"):
        project.validate(require_loads=False, require_supports=False)


def test_section_label_change_keeps_binding_and_model_hash():
    project, face = _plate_project()
    project.assign_plate(face, "deck")
    assignment_id = project.face_assignment_ids[face]
    before = DocumentSession(project).revision.model_hash

    section = project.plate_sections.pop("deck")
    project.plate_sections["renamed deck"] = replace(
        section, name="renamed deck"
    )
    assert project.resolve_section_assignments() == ()
    after = DocumentSession(project).revision.model_hash

    assert project.face_sections == {face: "renamed deck"}
    assert project.face_assignment_ids == {face: assignment_id}
    assert after == before
