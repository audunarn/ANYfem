"""The menu's floating tripod is a connected, persistable and exactly mirrored model."""
import math
import os
from collections import Counter

import numpy as np
import pytest

from anyfem.application.tripod_example import (
    TRIPOD_DIMENSIONS, TRIPOD_NAME, TripodDimensions, floating_tripod, tripod_description)
from anyfem.io.project_file import project_from_dict, project_to_dict


@pytest.fixture(scope="module")
def tripod():
    return floating_tripod()


def test_tripod_dimensions_sections_and_groups(tripod):
    geometry = tripod.geometry
    assert not geometry.validate_topology()
    assert len(geometry.faces) == len(tripod.face_sections) == 453
    assert Counter(tripod.face_sections.values()) == {
        "Longitudinal web 14 mm": 432, "Column 22 mm": 6, "Transition 20 mm": 6,
        "Crossbeam 18 mm": 6, "Heave plate 25 mm": 3}
    assert {name: section.thickness for name, section in tripod.plate_sections.items()} == {
        "Longitudinal web 14 mm": .014, "Column 22 mm": .022, "Transition 20 mm": .020,
        "Crossbeam 18 mm": .018, "Heave plate 25 mm": .025}
    assert len(tripod.wet_surfaces) == 6
    groups = geometry.groups
    assert len(groups) == 24
    for arm in (1, 2, 3):
        assert {name: len(faces) for name, faces in groups.items() if name.startswith(f"Arm {arm} ")} == {
            f"Arm {arm} - column skin": 2, f"Arm {arm} - column longitudinals": 48,
            f"Arm {arm} - inclined transition skin": 2, f"Arm {arm} - inclined transition longitudinals": 48,
            f"Arm {arm} - horizontal crossbeam skin": 2, f"Arm {arm} - horizontal crossbeam longitudinals": 48,
            f"Arm {arm} - heave plate": 1}
    positions = np.array([geometry.vertex_position(v) for v in geometry.vertices])
    assert positions[:, 2].min() == -TRIPOD_DIMENSIONS.draft
    plan = np.hypot(positions[:, 0], positions[:, 1])
    assert plan.max() <= TRIPOD_DIMENSIONS.column_centre_radius + TRIPOD_DIMENSIONS.heave_plate_circumradius + 1e-9


def test_tripod_is_mirror_symmetric_about_the_arm_1_plane(tripod):
    # Arm 1 lies on y = 0 and arm 3 mirrors arm 2. The heave plates
    # are mirrored only to rounding, so match every mirrored vertex within 1e-9 m. The 12 split
    # points of the z = 0 waterline reference curves are not structural and not mirrored.
    geometry = tripod.geometry
    points = np.array([geometry.vertex_position(v) for v in geometry.vertices])
    waterline = points[:, 2] == 0.
    assert waterline.sum() == 12
    points = points[~waterline]
    mirrored = points * (1., -1., 1.)
    nearest = np.min(np.linalg.norm(mirrored[:, None, :] - points[None, :, :], axis=2), axis=1)
    assert nearest.max() < 1e-9


def test_tripod_description_states_the_main_dimensions():
    text = tripod_description()
    for part in ("12 m diameter", "46 m radius", "draft 16 m", "z = 14 m", "48 longitudinal plate webs",
                 "300 × 14 mm", "S355", "columns 22 mm", "transitions 20 mm", "crossbeams 18 mm",
                 "heave plates 25 mm", "No supports or loads"):
        assert part in text, part
    assert TRIPOD_NAME == "Floating tripod — 12 m columns, 16 m draft"


def test_tripod_roundtrip(tripod):
    restored = project_from_dict(project_to_dict(tripod))
    assert len(restored.geometry.faces) == 453
    assert restored.face_sections == tripod.face_sections
    assert restored.plate_sections == tripod.plate_sections
    assert len(restored.wet_surfaces) == len(tripod.wet_surfaces)
    assert {name: set(faces) for name, faces in restored.geometry.groups.items()} == {
        name: set(faces) for name, faces in tripod.geometry.groups.items()}


@pytest.mark.parametrize("change", [dict(webs_per_turn=10), dict(webs_per_turn=0),
                                    dict(web_depth=0.), dict(web_depth=6.)])
def test_tripod_rejects_invalid_web_layout(change):
    with pytest.raises(ValueError):
        floating_tripod(TripodDimensions(**change))


@pytest.mark.skipif(os.environ.get("ANYFEM_RUN_TRIPOD_PREPARATION") != "1",
                    reason="set ANYFEM_RUN_TRIPOD_PREPARATION=1 (about 2 minutes)")
def test_tripod_structural_preparation_completes(tripod):
    # Regression for the two ANYgeometry refusals fixed in ANYgeometry PR #46 (hub tangency, face 101)
    # and PR #47 (rotated-arm mitre contacts, face 153): preparation must reach mesh generation.
    from anyfem.jobs import _cancellation_token
    from anyfem.mesh_execution import MeshExecutionPolicy
    from anyfem.mesh_jobs import MeshSettings, run_mesh_job

    class Prepared(Exception):
        pass

    def progress(stage, message, fraction):
        if stage == "generation":
            raise Prepared

    with pytest.raises(Prepared):
        run_mesh_job(tripod, "m", MeshSettings.create(.5, element_order="linear",
                                                      execution_policy=MeshExecutionPolicy.off()),
                     progress, _cancellation_token())
