"""Focused contracts for the Details definition tasks and their commands."""

from __future__ import annotations

import pytest

from anyfem import Project
from anyfem import commands as cmd
from anyfem.document import DocumentSession
from anyfem.model import CoordinateSystem, ManualRegion, unit_profile
from anyfem.selection import MeshEntityRef
from anyfem.ui.definitions import (
    boolean_region,
    coordinate_system_from_values,
    region_from_selection,
    unit_profile_from_values,
)
from anygeometry.entities import EntityRef


def test_named_geometry_region_is_one_undo_item_and_keeps_id_on_redo():
    project = Project("definitions")
    session = DocumentSession(project)
    point_id = session.execute(cmd.AddPoint(1.0, 2.0, 3.0))
    region = region_from_selection(
        project,
        "lifting points",
        (EntityRef("vertex", point_id),),
        "vertex",
    )
    history_before = len(session.commands.history())

    session.execute(cmd.AddRegion(region))
    assert len(session.commands.history()) == history_before + 1
    assert project.regions[region.id] is region
    # Feature-created geometry is scoped by design identity, not only the
    # current materialized numeric topology ID.
    anchor = region.definition.anchors[0]
    assert getattr(anchor, "feature_id", None) is not None

    assert session.undo()
    assert region.id not in project.regions
    assert session.redo()
    assert project.regions[region.id] is region


def test_mesh_selection_becomes_mesh_uuid_bound_region_anchors():
    project = Project("mesh scope")
    region = region_from_selection(
        project,
        "hot nodes",
        (MeshEntityRef("node", 7), MeshEntityRef("node", 12)),
        "node",
        mesh_id="mesh-uuid",
    )
    assert region.mesh_id == "mesh-uuid"
    assert [item.id for item in region.definition.anchors] == [7, 12]
    assert all(item.mesh_id == "mesh-uuid" for item in region.definition.anchors)


def test_boolean_region_refuses_incompatible_domains_and_keeps_operand_order():
    project = Project("regions")
    first_id = project.geometry.add_point(0, 0, 0)
    second_id = project.geometry.add_point(1, 0, 0)
    first = region_from_selection(
        project, "A", (EntityRef("vertex", first_id),), "vertex"
    )
    second = region_from_selection(
        project, "B", (EntityRef("vertex", second_id),), "vertex"
    )
    difference = boolean_region("A minus B", "subtract", (first, second))
    assert difference.definition.region_ids == (first.id, second.id)

    mesh_region = region_from_selection(
        project,
        "mesh",
        (MeshEntityRef("node", 1),),
        "node",
        mesh_id="mesh",
    )
    with pytest.raises(ValueError, match="same domain"):
        boolean_region("bad", "union", (first, mesh_region))


def test_coordinate_and_unit_commands_validate_and_retain_stable_identity():
    project = Project("coordinates", units=unit_profile("SI-mm-N-MPa"))
    session = DocumentSession(project)
    system = coordinate_system_from_values(
        "Pipe",
        "Cylindrical",
        ("10", "20 mm", "0"),
        ("0", "0", "1"),
        ("1", "0", "0"),
        project.units,
    )
    assert system.origin == pytest.approx((0.010, 0.020, 0.0))
    session.execute(cmd.AddCoordinateSystem(system))
    identifier = system.id
    assert session.undo()
    assert identifier not in project.coordinate_systems
    assert session.redo()
    assert project.coordinate_systems[identifier] is system

    custom = unit_profile_from_values(
        "Fabrication",
        {
            "length": "mm",
            "force": "kN",
            "pressure": "MPa",
            "mass": "kg",
            "time": "s",
            "angle": "deg",
            "moment": "kN*m",
            "line_load": "kN/m",
            "density": "kg/m3",
            "acceleration": "mm/s2",
        },
    )
    model_hash = session.revision.model_hash
    session.execute(cmd.SetUnitProfile(custom))
    assert project.units is custom
    assert session.revision.model_hash == model_hash
    assert session.undo()
    assert project.units.name == "SI-mm-N-MPa"
    assert session.redo()
    assert project.units is custom


def test_coordinate_task_rejects_parallel_reference_direction():
    with pytest.raises(ValueError, match="parallel"):
        coordinate_system_from_values(
            "bad",
            "Cartesian",
            ("0", "0", "0"),
            ("0", "0", "1"),
            ("0", "0", "2"),
            unit_profile(),
        )
