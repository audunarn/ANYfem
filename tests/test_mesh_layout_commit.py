from __future__ import annotations

import numpy as np
import pytest

from anygeometry.entities import EntityRef, OrientedEdge
from anygeometry.serialization import to_dict
from anygeometry.surfaces import Plane
from anymesher import plan_structured_layout

from anyfem.commands import CommitStructuredLayout
from anyfem.mesh_jobs import MeshSettings, mesh_semantic_hash
from anyfem.model.project import Project


def _neutral_rectangle() -> tuple[Project, int]:
    project = Project()
    geometry = project.geometry
    vertices = geometry.add_points(((0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)))
    edges = tuple(
        geometry.add_line(vertices[index], vertices[(index + 1) % 4])
        for index in range(4)
    )
    face = geometry.add_face_from_loop(
        tuple(OrientedEdge(edge, True) for edge in edges),
        surface=Plane(np.zeros(3), np.array((1.0, 0.0, 0.0)), np.array((0.0, 1.0, 0.0))),
    )
    return project, face


def _semantic(document: dict) -> dict:
    made = dict(document)
    made.pop("revision", None)
    made.pop("checksum", None)
    made.pop("id_state", None)
    return made


def test_whole_layout_commit_is_one_frozen_feature_and_exactly_undoable() -> None:
    project, face = _neutral_rectangle()
    before = to_dict(project.geometry)
    plan = plan_structured_layout(project.geometry, target_size=0.25)
    command = CommitStructuredLayout(plan)

    report = command.do(project)

    record = project.geometry.features.records[-1]
    assert report.status == "committed"
    assert record.kind == "geometry.mesh_partition.frozen"
    assert record.state == "frozen"
    assert record.materialization_checksum
    assert project.geometry.resolve_ref(EntityRef("face", face))

    command.undo(project)
    assert _semantic(to_dict(project.geometry)) == _semantic(before)

    command.redo(project)
    assert project.geometry.features.records[-1].feature_id == record.feature_id


def test_stale_layout_commit_fails_without_semantic_geometry_change() -> None:
    project, _face = _neutral_rectangle()
    plan = plan_structured_layout(project.geometry, target_size=0.25)
    project.geometry.add_point(3.0, 0.0, 0.0)
    before = to_dict(project.geometry)

    with pytest.raises(Exception, match="stale"):
        CommitStructuredLayout(plan).do(project)

    after = to_dict(project.geometry)
    assert _semantic(after) == _semantic(before)


def test_commit_then_exact_undo_restores_the_mesh_semantic_hash() -> None:
    project, _face = _neutral_rectangle()
    settings = MeshSettings.create(0.25, element_order="linear", strategy="auto")

    first = project.generate_mesh(0.25, strategy="auto")
    first_hash = mesh_semantic_hash(
        first,
        model_hash="same-model-semantics",
        mesh_input_hash=settings.input_hash,
        structural_preparation=project._last_mesh_preparation,
    )
    plan = plan_structured_layout(project.geometry, target_size=0.25)
    command = CommitStructuredLayout(plan)
    command.do(project)
    command.undo(project)

    second = project.generate_mesh(0.25, strategy="auto")
    second_hash = mesh_semantic_hash(
        second,
        model_hash="same-model-semantics",
        mesh_input_hash=settings.input_hash,
        structural_preparation=project._last_mesh_preparation,
    )

    assert second_hash == first_hash
