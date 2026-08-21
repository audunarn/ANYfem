"""Headless acceptance checks for the explicit undoable Join Sheet command."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from anygeometry import GeometryError, GeometryModel, Orientation, OrientedEdge
from anygeometry.entities import EntityRef
from anygeometry.features import FeatureOutputRef
from anygeometry.serialization import to_dict as geometry_to_dict

from anyfem.commands import (
    AddFace,
    AddLine,
    AddPoint,
    CommandStack,
    DeleteFeature,
    EditFeature,
    JoinSheet,
)
from anyfem.document import DocumentSession
from anyfem.io import ProjectFileError, project_from_dict, project_to_dict
from anyfem.model.attributes import Support
from anyfem.model.project import Project, ProjectError
from anyfem.selection import Selection


def _same_sense_adjacent_faces() -> tuple[GeometryModel, int, int, int]:
    geometry = GeometryModel()
    p0, p1, p2, p3, p4, p5 = geometry.add_points(
        (
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (1.0, 1.0, 0.0),
            (0.0, 1.0, 0.0),
            (2.0, 0.0, 0.0),
            (2.0, 1.0, 0.0),
        )
    )
    lower_left = geometry.add_line(p0, p1)
    shared = geometry.add_line(p1, p2)
    upper_left = geometry.add_line(p2, p3)
    left = geometry.add_line(p3, p0)
    upper_right = geometry.add_line(p2, p5)
    right = geometry.add_line(p5, p4)
    lower_right = geometry.add_line(p4, p1)
    first = geometry.add_face_from_loop(
        tuple(
            OrientedEdge(edge_id, True)
            for edge_id in (lower_left, shared, upper_left, left)
        )
    )
    second = geometry.add_face_from_loop(
        tuple(
            OrientedEdge(edge_id, True)
            for edge_id in (shared, upper_right, right, lower_right)
        )
    )
    return geometry, first, second, shared


def _owned_project(*, frozen: bool = False) -> tuple[Project, int, int, int | None]:
    geometry, first, second, _shared = _same_sense_adjacent_faces()
    feature_id = None
    if frozen:
        adopted = geometry.features.adopt_frozen(
            geometry,
            kind="vendor.base.adjacent-plates",
            name="Imported adjacent plates",
            outputs={
                "left": EntityRef("face", first),
                "right": EntityRef("face", second),
            },
        )
        feature_id = adopted.feature_id
    geometry.add_sheet((first,), name=f"assigned plate {first}")
    geometry.add_sheet((second,), name=f"assigned plate {second}")
    return Project("joined", geometry=geometry), first, second, feature_id


def _active_structural_records(geometry: GeometryModel) -> dict[str, dict]:
    return {
        name: dict(getattr(geometry, name))
        for name in ("parts", "sheets", "face_uses", "coedges")
    }


def test_join_sheet_preserves_geometry_feature_targets_and_is_exactly_undoable() -> None:
    project, first, second, feature_id = _owned_project(frozen=True)
    assert feature_id is not None
    references = (EntityRef("face", first), EntityRef("face", second))
    region_ref = project.singleton_region(references[0])
    support = project.add_support(
        Support(
            "plate target",
            references[0],
            {"uz": 0.0},
            region=region_ref,
        )
    )
    selection = Selection("face")
    selection.select_many(references)
    stack = CommandStack(project, selection)

    faces_before = deepcopy(geometry_to_dict(project.geometry)["faces"])
    structural_before = _active_structural_records(project.geometry)
    feature_before = project.geometry.features.get(feature_id)
    region_before = project.regions[region_ref.id].definition

    joined_id = stack.run(JoinSheet(references, name="Hull plating"))

    assert set(project.geometry.sheets) == {joined_id}
    joined = project.geometry.sheets[joined_id]
    uses = [project.geometry.face_uses[item] for item in joined.face_use_ids]
    assert [use.face_id for use in uses] == [first, second]
    assert [use.orientation for use in uses] == [
        Orientation.FORWARD,
        Orientation.REVERSED,
    ]
    assert geometry_to_dict(project.geometry)["faces"] == faces_before
    assert project.geometry.features.get(feature_id).outputs == feature_before.outputs
    assert project.geometry.features.validate_materialization(
        feature_id, project.geometry
    ) is None
    assert project.regions[region_ref.id].definition == region_before
    assert project.regions[region_ref.id].definition.anchors == (
        FeatureOutputRef(feature_id, "left", "face"),
    )
    assert project.supports == [support]
    assert project.supports[0].id == support.id
    assert project.supports[0].ref == references[0]
    assert selection.items == list(references)
    assert project.geometry._validate_structural() == ()  # noqa: SLF001

    assert stack.undo()
    assert _active_structural_records(project.geometry) == structural_before
    assert geometry_to_dict(project.geometry)["faces"] == faces_before
    assert project.geometry.features.get(feature_id) == feature_before
    assert project.regions[region_ref.id].definition == region_before
    assert project.supports == [support]
    assert selection.items == list(references)

    assert stack.redo()
    assert set(project.geometry.sheets) == {joined_id}
    assert project.geometry.sheets[joined_id] == joined
    assert selection.items == list(references)


@pytest.mark.parametrize("failure", ["missing", "disconnected"])
def test_join_sheet_rejects_unresolved_or_non_exact_connectivity_without_mutation(
    failure: str,
) -> None:
    project, first, second, _feature_id = _owned_project()
    if failure == "missing":
        # A present plate is deliberately not substituted for this missing
        # exact identity, even though it is the obvious nearby candidate.
        references = (
            EntityRef("face", first),
            EntityRef("face", second + 1000),
        )
        message = "missing face"
    else:
        a, b, c, d = project.geometry.add_points(
            (
                (10.0, 0.0, 0.0),
                (11.0, 0.0, 0.0),
                (11.0, 1.0, 0.0),
                (10.0, 1.0, 0.0),
            )
        )
        isolated = project.geometry.add_plate((a, b, c, d))
        project.geometry.add_sheet((isolated,), name="isolated")
        references = (EntityRef("face", first), EntityRef("face", isolated))
        message = "connected exact-edge topology"
    retired = project.geometry.add_point(99.0, 99.0, 0.0)
    project.geometry.remove_vertex(retired)
    replacement_log_before = project.geometry.replacement_log()
    assert replacement_log_before
    before = project_to_dict(project)

    with pytest.raises(GeometryError, match=message):
        CommandStack(project).run(JoinSheet(references))

    assert project_to_dict(project) == before
    assert project.geometry.replacement_log() == replacement_log_before


def test_join_sheet_refuses_faces_already_in_a_multi_face_owner_atomically() -> None:
    project, first, second, _feature_id = _owned_project()
    references = (EntityRef("face", first), EntityRef("face", second))
    stack = CommandStack(project)
    joined_id = stack.run(JoinSheet(references))
    before = project_to_dict(project)

    with pytest.raises(GeometryError, match="already belongs to joined Sheet"):
        stack.run(JoinSheet(references))

    assert project_to_dict(project) == before
    assert set(project.geometry.sheets) == {joined_id}


def _feature_authored_project() -> tuple[Project, tuple[int, int], tuple[int, int]]:
    project = Project("feature-owned join")
    stack = CommandStack(project)
    coordinates = (
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
        (2.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
    )
    vertices: list[int] = []
    point_features: list[int] = []
    for coordinate in coordinates:
        command = AddPoint(*coordinate)
        vertices.append(stack.run(command))
        assert command._feature_id is not None  # noqa: SLF001 - identity assertion
        point_features.append(command._feature_id)  # noqa: SLF001
    p0, p1, p2, p3, p4, p5 = vertices
    edge_pairs = (
        (p0, p1),
        (p1, p2),
        (p2, p3),
        (p3, p0),
        (p2, p5),
        (p5, p4),
        (p4, p1),
    )
    edges = [stack.run(AddLine(start, end)) for start, end in edge_pairs]
    lower_left, shared, upper_left, left, upper_right, right, lower_right = edges
    first = stack.run(AddFace((lower_left, shared, upper_left, left)))
    second = stack.run(AddFace((shared, upper_right, right, lower_right)))
    project.geometry.add_sheet((first,), name="left singleton")
    project.geometry.add_sheet((second,), name="right singleton")
    stack.run(
        JoinSheet(
            (EntityRef("face", first), EntityRef("face", second)),
            name="Persistent joined plating",
        )
    )
    return project, (first, second), (point_features[0], point_features[3])


def _assert_one_joined_sheet(project: Project, intent_id: str) -> None:
    intent = project.sheet_join_intents[intent_id]
    resolved = tuple(
        project.geometry.features.resolve(anchor, project.geometry)[0]
        if isinstance(anchor, FeatureOutputRef)
        else project.geometry.resolve_ref(anchor)[0]
        for anchor in intent.anchors
    )
    uses = [
        use
        for use in project.geometry.face_uses.values()
        if use.face_id in {reference.id for reference in resolved}
    ]
    assert len(uses) == 2
    assert len({use.sheet_id for use in uses}) == 1
    sheet = project.geometry.sheets[uses[0].sheet_id]
    assert sheet.name == "Persistent joined plating"
    assert len(sheet.face_use_ids) == 2
    assert project.geometry._validate_structural() == ()  # noqa: SLF001


def test_sheet_join_intent_round_trips_and_survives_full_feature_replays() -> None:
    project, faces, point_features = _feature_authored_project()
    assert len(project.sheet_join_intents) == 1
    intent_id = next(iter(project.sheet_join_intents))
    intent = project.sheet_join_intents[intent_id]
    assert all(isinstance(anchor, FeatureOutputRef) for anchor in intent.anchors)
    encoded = project_to_dict(project)
    assert encoded["ownership"]["sheet_joins"] == [intent.to_dict()]

    reopened = project_from_dict(deepcopy(encoded))
    assert project_to_dict(reopened) == encoded
    assert next(iter(reopened.sheet_join_intents)) == intent_id
    _assert_one_joined_sheet(reopened, intent_id)

    # Editing the earliest point forces replay from the beginning of feature
    # history.  The geometry baseline predates structural ownership, so this
    # specifically proves the feature-independent registry reapplies the join.
    CommandStack(reopened).run(
        EditFeature(
            point_features[0],
            parameters={"position": (-0.25, 0.0, 0.0)},
        )
    )
    _assert_one_joined_sheet(reopened, intent_id)

    # The public Project regeneration entry point has the same atomic owner
    # restoration contract for headless callers that edit intent directly.
    reopened.geometry.features.update(
        point_features[1], parameters={"position": (0.0, 1.25, 0.0)}
    )
    report = reopened.regenerate_geometry_features()
    assert report.success, report.diagnostic
    _assert_one_joined_sheet(reopened, intent_id)
    assert len(reopened.geometry.faces) == len(faces)

    # Removing an unrelated last feature marks the preceding record for a
    # true baseline replay, rather than the incremental path exercised above.
    replay_stack = CommandStack(reopened)
    extra = AddPoint(9.0, 9.0, 0.0)
    replay_stack.run(extra)
    assert extra._feature_id is not None  # noqa: SLF001
    before_full_replay = DocumentSession(reopened).revision
    replay_stack.run(DeleteFeature(extra._feature_id))  # noqa: SLF001
    after_full_replay = DocumentSession(reopened).revision
    _assert_one_joined_sheet(reopened, intent_id)
    restored_intent = reopened.sheet_join_intents[intent_id]
    resolved = tuple(
        reopened.geometry.features.resolve(anchor, reopened.geometry)[0]
        for anchor in restored_intent.anchors
    )
    owner = next(
        use
        for use in reopened.geometry.face_uses.values()
        if use.face_id == resolved[0].id
    )
    sheet = reopened.geometry.sheets[owner.sheet_id]
    actual = {
        reopened.geometry.face_uses[item].face_id:
        reopened.geometry.face_uses[item].orientation
        for item in sheet.face_use_ids
    }
    assert actual == {
        reference.id: orientation
        for reference, orientation in zip(
            resolved, restored_intent.orientations
        )
    }
    assert sheet.policy == restored_intent.policy
    assert replay_stack.undo()
    undone = DocumentSession(reopened).revision
    assert (undone.document_hash, undone.model_hash) == (
        before_full_replay.document_hash,
        before_full_replay.model_hash,
    )
    assert replay_stack.redo()
    redone = DocumentSession(reopened).revision
    assert (redone.document_hash, redone.model_hash) == (
        after_full_replay.document_hash,
        after_full_replay.model_hash,
    )


def test_v7_requires_registry_while_v6_safely_infers_explicit_join() -> None:
    project, _faces, _point_features = _feature_authored_project()
    document = project_to_dict(project)
    document.pop("ownership")

    with pytest.raises(ProjectFileError, match="format 7 ownership is required"):
        project_from_dict(deepcopy(document))

    missing_joins = project_to_dict(project)
    missing_joins["ownership"].pop("sheet_joins")
    with pytest.raises(
        ProjectFileError, match="format 7 ownership.sheet_joins is required"
    ):
        project_from_dict(missing_joins)

    document["anyfem"]["format"] = 6
    migrated = project_from_dict(document)

    assert len(migrated.sheet_join_intents) == 1
    intent_id = next(iter(migrated.sheet_join_intents))
    _assert_one_joined_sheet(migrated, intent_id)
    assert project_to_dict(migrated)["ownership"]["sheet_joins"]


def test_unresolved_persistent_join_anchor_rolls_feature_command_back_exactly() -> None:
    project, _faces, _point_features = _feature_authored_project()
    intent = next(iter(project.sheet_join_intents.values()))
    anchor = intent.anchors[0]
    assert isinstance(anchor, FeatureOutputRef)
    before = project_to_dict(project)

    with pytest.raises(GeometryError, match="could not restore structural Sheet"):
        CommandStack(project).run(DeleteFeature(anchor.feature_id))

    assert project_to_dict(project) == before


def test_overlapping_sheet_join_intents_fail_before_owner_mutation() -> None:
    project, _faces, _point_features = _feature_authored_project()
    intent = next(iter(project.sheet_join_intents.values()))
    project.add_sheet_join_intent(replace(intent, id=str(uuid4())))
    geometry_before = geometry_to_dict(project.geometry)
    intents_before = dict(project.sheet_join_intents)

    with pytest.raises(GeometryError, match="both claim exact face"):
        project.reapply_sheet_join_intents()

    assert geometry_to_dict(project.geometry) == geometry_before
    assert project.sheet_join_intents == intents_before


def test_join_sheet_rejects_part_semantics_it_cannot_replay_losslessly() -> None:
    geometry, first, second, _shared = _same_sense_adjacent_faces()
    primary_part = geometry.add_part(
        name="Hull primary", metadata={"role": "watertight"}
    )
    geometry.add_sheet((first,), part_id=primary_part, name="left")
    geometry.add_sheet((second,), name="right")
    project = Project("part semantics", geometry=geometry)
    before = project_to_dict(project)

    with pytest.raises(
        GeometryError, match="name or metadata.*cannot preserve"
    ):
        CommandStack(project).run(
            JoinSheet((EntityRef("face", first), EntityRef("face", second)))
        )

    assert project_to_dict(project) == before


def test_legacy_inference_skips_unnamed_multi_face_sheet() -> None:
    geometry, first, second, _shared = _same_sense_adjacent_faces()
    geometry.add_sheet(
        (first, second),
        orientations=(Orientation.FORWARD, Orientation.REVERSED),
    )
    project = Project("unnamed legacy owner", geometry=geometry)

    assert project.infer_existing_sheet_join_intents() == ()
    assert project.sheet_join_intents == {}


def test_raw_feature_bypass_is_blocked_from_save_mesh_and_solve() -> None:
    project, _faces, _point_features = _feature_authored_project()
    anchor = next(iter(project.sheet_join_intents.values())).anchors[0]
    assert isinstance(anchor, FeatureOutputRef)
    project.geometry.features.remove(anchor.feature_id, cascade=False)
    report = project.geometry.regenerate_features()
    assert report.success

    with pytest.raises(ProjectFileError, match="Sheet ownership intent"):
        project_to_dict(project)
    with pytest.raises(ProjectError, match="Sheet Join"):
        project.validate(require_loads=False, require_supports=False)
    with pytest.raises(ProjectError, match="Sheet ownership intent"):
        project.generate_mesh(0.5)


def test_sheet_join_hash_ignores_label_uuid_but_tracks_orientation() -> None:
    project, _faces, _point_features = _feature_authored_project()
    intent_id, intent = next(iter(project.sheet_join_intents.items()))
    baseline = DocumentSession(project).revision

    renamed = replace(intent, id=str(uuid4()), name="Engineer label")
    project.sheet_join_intents.clear()
    project.add_sheet_join_intent(renamed)
    project.reapply_sheet_join_intents()
    label_only = DocumentSession(project).revision
    assert label_only.document_hash != baseline.document_hash
    assert label_only.model_hash == baseline.model_hash

    project.sheet_join_intents[renamed.id] = replace(
        renamed,
        orientations=tuple(-int(value) for value in renamed.orientations),
    )
    project.reapply_sheet_join_intents()
    reoriented = DocumentSession(project).revision
    assert reoriented.model_hash != label_only.model_hash
