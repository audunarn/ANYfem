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


def test_tripod_mesh_preset_and_seam_policy_are_stored_on_the_model(tripod):
    from anyfem.structural_preparation import seam_policy_of
    settings = tripod.native_mesh_settings
    assert (settings.target_size, settings.element_order, settings.backend.value) == (.5, "linear", "quad_first")
    assert seam_policy_of(settings) == "ring_split"
    assert (tripod.target_size, tripod.element_order) == (.5, "linear")
    restored = project_from_dict(project_to_dict(tripod))
    assert restored.native_mesh_settings == settings and seam_policy_of(restored.native_mesh_settings) == "ring_split"
    assert "seam_policy" in tripod_description() or "ring-split" in tripod_description()


@pytest.mark.parametrize("webs", [36, 48, 60])
def test_support_domains_start_half_a_web_pitch_before_each_half(webs):
    # Workaround for the open ANYgeometry seam defect: only the parameter seam moves, never the shape.
    project = floating_tripod(TripodDimensions(webs_per_turn=webs))
    starts = {round(face.surface.start_angle, 12) for name, ids in project.geometry.groups.items()
              if name.endswith(" skin") for face in (project.geometry.faces[i.id] for i in ids)}
    assert starts == {round(-math.pi / webs, 12), round(math.pi - math.pi / webs, 12)}


def test_support_start_does_not_change_the_shape():
    project = floating_tripod()
    points = np.array([project.geometry.vertex_position(v) for v in project.geometry.vertices])
    assert points.shape[0] and np.isfinite(points).all()
    for name, ids in project.geometry.groups.items():
        if name.endswith(" skin"):
            for ref in ids:
                face = project.geometry.faces[ref.id]
                # every face boundary vertex lies on its (full-turn) support cylinder
                surface = face.surface
                axis = np.array(surface.axis, float); axis /= np.linalg.norm(axis)
                origin = np.array(surface.origin, float)
                for use in face.loop:
                    edge = project.geometry.edges[use.edge]
                    for vertex in (edge.start, edge.end):
                        p = np.asarray(project.geometry.vertex_position(vertex), float) - origin
                        assert abs(np.linalg.norm(p - axis * (p @ axis)) - surface.radius) < 1e-6


@pytest.mark.parametrize("change", [dict(webs_per_turn=10), dict(webs_per_turn=0),
                                    dict(web_depth=0.), dict(web_depth=6.)])
def test_tripod_rejects_invalid_web_layout(change):
    with pytest.raises(ValueError):
        floating_tripod(TripodDimensions(**change))


@pytest.mark.skipif(os.environ.get("ANYFEM_RUN_TRIPOD_MESH") != "1",
                    reason="set ANYFEM_RUN_TRIPOD_MESH=1 (about 10-15 minutes, one core busy; run under the machine slot)")
def test_tripod_example_meshes_with_qualified_s3_after_save_and_reopen(tmp_path):
    """The example as the menu creates it, saved and reopened, meshes with qualified S3 ADMITTED and no legacy fallback.

    Time limit: the mesh job must finish within 1000 s (owner's slowdown guard; override with ANYFEM_TRIPOD_MESH_CAP).
    No environment variable is set: the seam policy comes from the model.  Uses whatever ANYgeometry / ANYmesh are on the
    path; the pinned ones are ANYgeometry 5f68177 and the ANYmesh merge named in pyproject.toml."""
    import time
    from anyfem.jobs import _cancellation_token
    from anyfem.mesh_execution import MeshExecutionPolicy
    from anyfem.mesh_jobs import MeshSettings, run_mesh_job
    from anyfem.io.project_file import project_from_dict, project_to_dict
    from anyfem.structural_preparation import seam_policy_of
    assert "ANYGEOMETRY_SEAM_POLICY" not in os.environ
    from anyfem.io.project_file import SEAM_POLICY_FORMAT, load_project, save_project
    path = save_project(floating_tripod(), tmp_path / "tripod.anyfem")
    import json
    assert json.loads(path.read_text(encoding="utf-8"))["anyfem"]["format"] == SEAM_POLICY_FORMAT
    project = load_project(path)
    assert seam_policy_of(project.native_mesh_settings) == "ring_split"
    settings = project.native_mesh_settings
    import anymesher.hybrid as hybrid
    heave = {}
    real_execute = hybrid._quad_first_execute

    def observed(geometry, *args, **kwargs):
        short = []
        for edge in geometry.edges.values():
            if edge.start == edge.end:
                continue
            p, q = (np.asarray(geometry.vertex_position(v), float) for v in (edge.start, edge.end))
            if abs(p[2] + 16.) < 1e-6 and abs(q[2] + 16.) < 1e-6 and np.linalg.norm(p - q) < .125:
                short.append(float(np.linalg.norm(p - q)))
        heave["short"] = short
        return real_execute(geometry, *args, **kwargs)

    hybrid._quad_first_execute = observed
    began = time.perf_counter()
    try:
        result = run_mesh_job(project, "m", MeshSettings.create(
            settings.target_size, element_order=settings.element_order, strategy=settings.backend.value,
            execution_policy=MeshExecutionPolicy.off()), lambda *a: None, _cancellation_token())
    finally:
        hybrid._quad_first_execute = real_execute
    wall = time.perf_counter() - began
    # The seam policy is an attempt per face: prove the RESULT, not the request.
    assert heave["short"] == [], "heave-plate edges shorter than 0.125 m: %s" % heave["short"]
    mesh = result.mesh
    counts = {name: sum(1 for nodes in mesh.shells.values() if len(nodes) == size) for name, size in (("Q4", 4), ("T3", 3))}
    qualified = mesh.structural_preparation["qualified_s3"]
    print("tripod mesh wall %.1f s" % wall, counts, {key: qualified[key] for key in ("status", "legacy_fallback")})
    assert qualified["status"] == "ADMITTED" and qualified["legacy_fallback"] == "FORBIDDEN"
    assert qualified["element_ids"]
    # Owner's limit (2026-11-24): 1000 s, a slowdown guard (about 850 s measured under load), not a speed target.
    assert wall < float(os.environ.get("ANYFEM_TRIPOD_MESH_CAP", "1000"))
    assert "ANYGEOMETRY_SEAM_POLICY" not in os.environ


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
