"""Conservative waterline clearance: the LOW-side freeboard governs under heel/trim, never the global highest node."""
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.application import floating_prepare as fp
from anyfem.application.floating_source import MeanPose


def stub_project(L=10.0, B=4.0, H=3.0):
    verts = {0: (0, -B / 2, 0), 1: (L, -B / 2, 0), 2: (L, B / 2, 0), 3: (0, B / 2, 0),
             4: (0, -B / 2, H), 5: (L, -B / 2, H), 6: (L, B / 2, H), 7: (0, B / 2, H)}
    faces = {10: (0, 1, 2, 3), 11: (0, 1, 5, 4), 12: (3, 2, 6, 7), 13: (0, 3, 7, 4), 14: (1, 2, 6, 5)}
    geometry = SimpleNamespace(vertex_position=lambda v: np.array(verts[v], float), face_corner_vertices=lambda f: faces[f])
    return SimpleNamespace(geometry=geometry, wet_surfaces=[SimpleNamespace(ref=SimpleNamespace(id=f)) for f in faces]), verts


def rot_x(angle):
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def test_upright_clearance_is_min_of_keel_and_freeboard():
    project, _ = stub_project()
    assert fp._vertical_clearance(project, MeanPose(np.eye(3), np.array([0.0, 0.0, -1.2]))) == pytest.approx(1.2)
    assert fp._vertical_clearance(project, MeanPose(np.eye(3), np.array([0.0, 0.0, -2.5]))) == pytest.approx(0.5)


def test_heel_uses_the_low_side_freeboard_not_the_global_highest_node():
    project, verts = stub_project()
    Q0, t = rot_x(np.deg2rad(3.0)), np.array([0.0, 0.0, -2.0])
    pose = MeanPose(Q0, t)
    clearance = fp._vertical_clearance(project, pose)
    z_world = np.array([pose.world(np.array(v, float)[None, :])[0, 2] for v in verts.values()])
    global_highest = z_world.max()
    low_side_top = min(z for z in z_world if z > 0)
    assert clearance == pytest.approx(low_side_top) and clearance < global_highest - 0.1   # unequal freeboard: 3 deg on 4 m beam
    assert clearance <= low_side_top + 1e-12                                                  # never overstates


def rot_y(angle):
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def test_low_draft_combined_roll_and_trim_uses_the_shallowest_keel_corner():  # admitted ONLY with every keel corner submerged
    """parent-review-15 oracle: 5x4x2 box, roll 0.03 / trim -0.04, body origin z=-0.4. All bottom corners stay submerged and the
    freeboard is large, so the keel governs; the per-face maximum of deepest corners (old code, ~0.26 m) overstates the exact
    shallowest bottom-corner depth (~0.14 m)."""
    project, verts = stub_project(L=5.0, B=4.0, H=2.0)
    Q0 = rot_x(0.03) @ rot_y(-0.04)                       # combined roll + trim (rotation order is immaterial to the bound)
    pose = MeanPose(Q0, np.array([0.0, 0.0, -0.4]))
    z_bottom = np.array([pose.world(np.array(verts[i], float)[None, :])[0, 2] for i in range(4)])
    assert np.all(z_bottom < 0.0)                                                          # every bottom corner submerged
    exact = float((-z_bottom).min())                                                       # shallowest bottom corner depth
    assert exact < 0.2                                                                     # genuinely low draft
    clearance = fp._vertical_clearance(project, pose)
    assert clearance <= exact + 1e-12                                                      # never overstates the shallow keel
    # the old rule (minimum over faces of the DEEPEST corner depth) is demonstrably larger than the exact bound
    faces = {10: (0, 1, 2, 3), 11: (0, 1, 5, 4), 12: (3, 2, 6, 7), 13: (0, 3, 7, 4), 14: (1, 2, 6, 5)}
    old = min(-min(pose.world(np.array(verts[v], float)[None, :])[0, 2] for v in f) for f in faces.values())
    assert old > exact + 0.05


def test_mean_flat_keel_emergence_or_zero_draft_is_refused_never_bounded_or_read_as_plating():
    project, verts = stub_project(L=5.0, B=4.0, H=2.0)
    Q0 = rot_x(0.03) @ rot_y(-0.04)
    with pytest.raises(fp.FloatingPrepareError):                                           # every corner above the waterplane
        fp._vertical_clearance(project, MeanPose(Q0, np.array([0.0, 0.0, 0.5])))
    # one bottom corner emerged (z = +0.31 at x=5,y=2) while another is still submerged: PARTIAL keel emergence is refused
    with pytest.raises(fp.FloatingPrepareError, match='keel'):
        fp._vertical_clearance(project, MeanPose(Q0, np.array([0.0, 0.0, 0.05])))
    # zero draft at a keel point (the shallowest bottom corner exactly on z = 0) is refused as well
    pose = MeanPose(np.eye(3), np.array([0.0, 0.0, 0.0]))                                  # upright, bottom face on the waterplane
    with pytest.raises(fp.FloatingPrepareError, match='keel'):
        fp._vertical_clearance(project, pose)


def test_a_hull_without_a_horizontal_flat_keel_face_is_unsupported():
    project, _ = stub_project()
    project.wet_surfaces = [s for s in project.wet_surfaces if s.ref.id != 10]              # no bottom face among the wet surfaces
    with pytest.raises(fp.FloatingPrepareError, match='unsupported'):
        fp._vertical_clearance(project, MeanPose(np.eye(3), np.array([0.0, 0.0, -1.2])))


def test_no_plate_crossing_the_waterplane_is_refused():
    project, _ = stub_project()
    with pytest.raises(fp.FloatingPrepareError):
        fp._vertical_clearance(project, MeanPose(np.eye(3), np.array([0.0, 0.0, -10.0])))     # fully submerged: no freeboard
