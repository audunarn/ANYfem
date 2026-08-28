"""Commercial modeling commands backed by current ANYgeometry owner APIs."""

from __future__ import annotations

import json

import numpy as np
import pytest
from anygeometry import EntityRef, GeometryError, to_dict

from anyfem import commands as cmd
from anyfem.model.project import Project


def _geometry_bytes(project: Project) -> str:
    document = dict(to_dict(project.geometry))
    # Undo restores the exact design while ANYgeometry deliberately keeps
    # revision and allocator high-water marks monotonic so stale handles can
    # never alias newly created entities.
    document.pop("checksum", None)
    document.pop("id_state", None)
    document.pop("revision", None)
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def _exact_geometry_bytes(project: Project) -> str:
    return json.dumps(to_dict(project.geometry), sort_keys=True, separators=(",", ":"))


def _plate(stack: cmd.CommandStack, width: float = 4.0, height: float = 3.0) -> int:
    points = [
        stack.run(cmd.AddPoint(x, y))
        for x, y in ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height))
    ]
    return stack.run(cmd.AddPlate(points))


def test_copy_mirror_and_patterns_are_semantic_atomic_features():
    project = Project("duplication")
    stack = cmd.CommandStack(project)
    face = _plate(stack)
    selected = (EntityRef("face", face),)

    before_copy = _geometry_bytes(project)
    copied = stack.run(cmd.CopyEntities(selected, translation=(5.0, 0.0, 0.0)))
    copied_face = copied.entity_map[selected[0]]
    assert project.geometry.features.records[-1].kind == "geometry.copy"
    assert copied_face in project.geometry.group("shell") or copied_face.id in project.geometry.faces
    assert len(project.geometry.faces) == 2

    assert stack.undo()
    assert _geometry_bytes(project) == before_copy
    assert stack.redo()
    assert copied_face.id in project.geometry.faces

    mirrored = stack.run(
        cmd.MirrorEntities(selected, plane_point=(0, 0, 0), plane_normal=(1, 0, 0))
    )
    assert mirrored.entity_map[selected[0]].id in project.geometry.faces
    assert project.geometry.features.records[-1].kind == "geometry.mirror"

    linear = stack.run(
        cmd.LinearPattern(selected, direction=(0, 1, 0), spacing=4.0, count=2)
    )
    assert len(linear.instances) == 2
    assert project.geometry.features.records[-1].kind == "geometry.pattern.linear"

    circular = stack.run(
        cmd.CircularPattern(
            selected,
            axis_point=(0, 0, 0),
            axis_direction=(0, 0, 1),
            angle_step=np.pi / 2,
            count=3,
        )
    )
    assert len(circular.instances) == 3
    record = project.geometry.features.records[-1]
    assert record.kind == "geometry.pattern.circular"
    assert any(key.startswith("instance/2/") for key in record.outputs)
    assert project.geometry.regenerate_features().success


def test_failed_owner_operation_is_byte_for_byte_non_mutating():
    project = Project("atomic mirror")
    stack = cmd.CommandStack(project)
    face = _plate(stack)
    before = _exact_geometry_bytes(project)
    history = stack.history()

    with pytest.raises(GeometryError, match="normal must be non-zero"):
        stack.run(
            cmd.MirrorEntities(
                (EntityRef("face", face),), plane_normal=(0.0, 0.0, 0.0)
            )
        )

    assert _exact_geometry_bytes(project) == before
    assert stack.history() == history


def test_reverse_face_normal_is_feature_recorded_and_exactly_undoable():
    project = Project("orientation")
    stack = cmd.CommandStack(project)
    face = _plate(stack)
    reference = EntityRef("face", face)
    before = _geometry_bytes(project)
    normal = project.geometry.face_normal(face, 0.5, 0.5)

    assert stack.run(cmd.ReverseEntity(reference)) == reference
    np.testing.assert_allclose(
        project.geometry.face_normal(face, 0.5, 0.5), -normal, atol=1.0e-12
    )
    assert project.geometry.features.records[-1].kind == "geometry.reverse"

    assert stack.undo()
    assert _geometry_bytes(project) == before


def test_measurements_are_typed_queries_and_do_not_pollute_undo():
    project = Project("measure")
    stack = cmd.CommandStack(project)
    face = _plate(stack, width=4.0, height=3.0)
    edges = tuple(item.edge for item in project.geometry.faces[face].loop)
    history = stack.history()

    length = stack.query(cmd.MeasureGeometry(EntityRef("edge", edges[0]), "length"))
    area = stack.query(cmd.MeasureGeometry(EntityRef("face", face), "area"))
    perimeter = stack.query(
        cmd.MeasureGeometry(EntityRef("face", face), "perimeter")
    )
    normal = stack.query(cmd.MeasureGeometry(EntityRef("face", face), "normal"))
    angle = stack.query(
        cmd.MeasureGeometry(
            (EntityRef("edge", edges[0]), EntityRef("edge", edges[1])), "angle"
        )
    )

    assert (length.kind, length.unit, length.value) == ("length", "m", 4.0)
    assert (area.kind, area.unit, area.value) == ("area", "m^2", pytest.approx(12.0))
    assert perimeter.value == pytest.approx(14.0)
    assert normal.kind == "normal" and len(normal.value) == 3
    assert angle.value == pytest.approx(np.pi / 2)
    assert stack.history() == history


def test_structural_generators_create_editable_semantic_features():
    cases = (
        (
            cmd.AddStiffenedPanel(4.0, 2.0, 0.5, 1.0, semantic_group="deck"),
            "generator.stiffened_panel",
            "deck",
        ),
        (cmd.AddCylinder(1.0, 2.0, circumferential_segments=8), "generator.cylinder", "shell"),
        (cmd.AddCone(1.0, 0.5, 2.0, circumferential_segments=8), "generator.cone", "shell"),
    )
    for command, kind, group in cases:
        project = Project(kind)
        stack = cmd.CommandStack(project)
        before = _geometry_bytes(project)
        feature = stack.run(command)

        assert feature.kind == kind
        assert feature.state == "ok"
        assert feature.outputs
        assert project.geometry.group(group)
        after = _geometry_bytes(project)
        assert after != before
        assert stack.undo()
        assert _geometry_bytes(project) == before
        assert stack.redo()
        assert _geometry_bytes(project) == after


def test_neutral_trim_hole_keeps_one_face_and_is_not_butterfly_decomposition():
    project = Project("trim")
    stack = cmd.CommandStack(project)
    face = _plate(stack)
    before = _geometry_bytes(project)

    retained, arcs = stack.run(cmd.NeutralTrimHole(face, (2.0, 1.5, 0.0), 0.5))
    assert retained == face
    assert len(arcs) == 4
    assert list(project.geometry.faces) == [face]
    assert len(project.geometry.faces[face].holes) == 1
    feature = project.geometry.features.records[-1]
    assert feature.kind == "geometry.trim_hole"
    assert set(feature.outputs) == {
        "face", "boundary/0", "boundary/1", "boundary/2", "boundary/3"
    }
    assert project.geometry.regenerate_features().success
    regenerated_face = project.geometry.features.records[-1].outputs["face"]
    assert len(project.geometry.faces[regenerated_face.id].holes) == 1
    assert stack.undo()
    assert _geometry_bytes(project) == before
