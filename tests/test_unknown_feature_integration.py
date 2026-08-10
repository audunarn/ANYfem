"""Frozen future geometry is usable only while its checksum validates."""

from __future__ import annotations

from copy import deepcopy

import pytest
from anygeometry import from_dict, to_dict

from anyfem import Project, ProjectError, steel


def _future_plate(*, tamper: bool = False) -> Project:
    source = Project("future feature")
    source.geometry.features.capture_baseline(source.geometry)
    source.geometry.features.append(
        "generator.plate", parameters={"length": 2.0, "width": 1.0}
    )
    assert source.geometry.regenerate_features().success
    document = to_dict(source.geometry)
    document["features"]["records"][0]["kind"] = "vendor.future.plate"
    if tamper:
        document["vertices"][0]["position"][0] += 0.125

    project = Project("future feature")
    project.geometry = from_dict(deepcopy(document))
    project.add_material(steel("S355", 0.01))
    project.add_plate_section("plate", thickness=0.01, material="S355")
    face_id = next(iter(project.geometry.faces))
    if tamper:
        # Keep construction possible so the mesh boundary itself proves that
        # an invalid frozen feature cannot be used, even through a legacy map.
        project.face_sections[face_id] = "plate"
    else:
        project.assign_plate(face_id, "plate")
    return project


def test_verified_future_feature_can_mesh_but_cannot_regenerate() -> None:
    project = _future_plate()

    mesh = project.generate_mesh(0.5)

    assert mesh.num_elements > 0
    before = to_dict(project.geometry, include_features=False)
    report = project.geometry.regenerate_features()
    assert not report.success
    assert to_dict(project.geometry, include_features=False) == before


def test_bad_future_feature_checksum_blocks_meshing() -> None:
    project = _future_plate(tamper=True)

    with pytest.raises(ProjectError, match="checksum does not match"):
        project.generate_mesh(0.5)
