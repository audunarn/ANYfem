"""Geometry overlap ownership and focused generator-form contracts."""

from __future__ import annotations

from anygeometry import find_coplanar_overlaps

from anyfem import commands as cmd
from anyfem.model.project import Project
from anyfem.ui.panels import GeometryPanel


def _plate(stack: cmd.CommandStack, coordinates) -> int:
    points = [stack.run(cmd.AddPoint(x, y, z)) for x, y, z in coordinates]
    return stack.run(cmd.AddPlate(points))


def test_overlap_fragment_command_is_feature_backed_atomic_and_undoable():
    project = Project("overlap")
    stack = cmd.CommandStack(project)
    first = _plate(
        stack, ((0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0))
    )
    second = _plate(
        stack, ((1, 0, 0), (3, 0, 0), (3, 1, 0), (1, 1, 0))
    )

    feature = stack.run(cmd.FragmentPlateOverlaps((first, second)))

    assert feature.kind == "geometry.fragment.overlaps"
    assert len(feature.outputs) == 3
    assert sum(key.startswith("overlap/") for key in feature.outputs) == 1
    assert find_coplanar_overlaps(project.geometry) == ()
    assert len(project.geometry.faces) == 3
    assert stack.undo()
    assert len(project.geometry.faces) == 2
    assert find_coplanar_overlaps(project.geometry)
    assert stack.redo()
    assert len(project.geometry.faces) == 3
    assert find_coplanar_overlaps(project.geometry) == ()


def test_each_generator_type_exposes_only_applicable_inputs():
    expected = {
        "Plate": {"length", "width", "group", "origin", "u", "v"},
        "Bulkhead": {"length", "width", "group", "origin", "u", "v"},
        "Frame": {"length", "width", "group", "origin", "u", "v"},
        "Stiffened panel": {
            "length", "width", "longitudinal", "transverse", "group", "origin"
        },
        "Cylinder": {
            "length", "radius_start", "longitudinal", "transverse",
            "segments", "origin", "axis",
        },
        "Cone": {
            "length", "radius_start", "radius_end", "longitudinal",
            "transverse", "segments", "origin", "axis",
        },
    }
    for kind, fields in expected.items():
        actual, explanation = GeometryPanel.generator_form_spec(kind)
        assert set(actual) == fields
        assert len(explanation) > 40
