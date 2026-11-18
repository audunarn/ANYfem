"""Shell axis orientation check (direction programme D1)."""

from __future__ import annotations

import warnings
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.mesh_orientation import (
    AXIS_ACROSS_U,
    AXIS_ALONG_U,
    element_axes,
    shell_orientation_report,
)
from anyfem.model.formulations import ShellFormulationPolicy


class _PlaneZ0:
    """Face 1 = plane z = 0 with u along x and v along y."""

    def project_to_face_many(self, face_id, xyz):
        xyz = np.asarray(xyz, dtype=float)
        points = xyz.copy()
        points[:, 2] = 0.0
        return points, xyz[:, :2].copy(), np.abs(xyz[:, 2])

    def face_derivatives_many(self, face_id, uv):
        n = len(uv)
        return np.tile([1.0, 0.0, 0.0], (n, 1)), np.tile([0.0, 1.0, 0.0], (n, 1))


def _grid_mesh(nx=4, ny=3, quadratic=True):
    """Structured plate on z = 0, every element with local x along +x."""

    nodes, quads = {}, {}
    ident = iter(range(1, 10_000))

    def node(x, y):
        key = (round(x, 9), round(y, 9))
        if key not in index:
            index[key] = next(ident)
            nodes[index[key]] = np.array([x, y, 0.0])
        return index[key]

    index = {}
    eid = 0
    for i in range(nx):
        for j in range(ny):
            c = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
            ids = [node(*p) for p in c]
            if quadratic:
                ids += [node((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                        for a, b in zip(c, c[1:] + c[:1])]
            eid += 1
            quads[eid] = tuple(ids)
    return SimpleNamespace(nodes=nodes, quads=quads, tris={},
                           elements_of_face={1: sorted(quads)})


def _turn(ids, quadratic=True):
    """Start the element one corner later: local x turns 90 degrees."""
    corners = ids[1:4] + ids[:1]
    return tuple(corners + (ids[5:8] + ids[4:5] if quadratic else ()))


def _reverse(ids, quadratic=True):
    """Mirror the element: local x keeps its direction, the normal flips."""
    c = ids[:4]
    corners = (c[3], c[2], c[1], c[0])
    if not quadratic:
        return corners
    m = ids[4:]
    return corners + (m[2], m[1], m[0], m[3])


def _solver_frame(coords, centre):
    from anyfesolver.elements import ShellElement

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        element = ShellElement(1, list(range(1, len(coords) + 1)), "steel", thickness=.01)
    _n, dxi, deta = element.compute_shape_functions(*centre)
    frame = element._local_frame_and_derivatives(np.asarray(coords, float), dxi, deta)[0]
    return frame[:, 0], frame[:, 2]


@pytest.mark.parametrize("count,centre", [(8, (0.0, 0.0)), (6, (1 / 3, 1 / 3)),
                                          (3, (1 / 3, 1 / 3)), (4, (0.0, 0.0))])
def test_element_axes_match_solver_frame(count, centre):
    rng = np.random.default_rng(count)
    if count in (4, 8):
        corners = np.array([[0, 0, 0], [1.3, .1, .05], [1.2, 1.1, .12], [-.1, .9, .03]], float)
        mids = [(corners[k] + corners[(k + 1) % 4]) / 2 for k in range(4)]
        base = np.vstack([corners] + ([np.array(mids)] if count == 8 else []))
    else:
        corners = np.array([[0, 0, 0], [1.2, .2, .04], [.3, 1.1, .09]], float)
        mids = [(corners[k] + corners[(k + 1) % 3]) / 2 for k in range(3)]
        base = np.vstack([corners] + ([np.array(mids)] if count == 6 else []))
    coords = base + rng.normal(scale=.02, size=base.shape)  # curved/warped
    mesh = SimpleNamespace(nodes={k + 1: coords[k] for k in range(count)},
                           quads={7: tuple(range(1, count + 1))} if count in (4, 8) else {},
                           tris={7: tuple(range(1, count + 1))} if count in (3, 6) else {})
    policy = ShellFormulationPolicy.legacy_compatible()
    axes = element_axes(mesh, 7, policy)
    x_ref, n_ref = _solver_frame(coords, centre)
    np.testing.assert_allclose(axes.x_axis, x_ref, atol=1e-12)
    np.testing.assert_allclose(axes.normal, n_ref, atol=1e-12)


def test_qualified_q4_uses_solver_equation7_frame():
    from anyfesolver.e4_pl_element import equation7_frame

    coords = np.array([[0, 0, 0], [1, .2, 0], [1.1, 1, 0], [0, .9, 0]], float)
    mesh = SimpleNamespace(nodes={k + 1: coords[k] for k in range(4)}, quads={1: (1, 2, 3, 4)}, tris={})
    axes = element_axes(mesh, 1, ShellFormulationPolicy.current_default())
    frame = equation7_frame(coords)[0]
    np.testing.assert_array_equal(axes.x_axis, frame[:, 0])
    np.testing.assert_array_equal(axes.normal, frame[:, 2])


@pytest.mark.parametrize("quadratic", [True, False])
def test_consistent_plate_reports_nothing(quadratic):
    mesh = _grid_mesh(quadratic=quadratic)
    report = shell_orientation_report(_PlaneZ0(), mesh)
    assert report.consistent
    (face,) = report.faces
    assert face.axis_classes == {AXIS_ALONG_U: 12}
    assert face.normal_with == 12 and face.normal_against == 0


@pytest.mark.parametrize("quadratic", [True, False])
def test_turned_and_reversed_elements_are_found(quadratic):
    mesh = _grid_mesh(quadratic=quadratic)
    mesh.quads[2] = _turn(mesh.quads[2], quadratic)
    mesh.quads[5] = _turn(mesh.quads[5], quadratic)
    mesh.quads[7] = _reverse(mesh.quads[7], quadratic)
    report = shell_orientation_report(_PlaneZ0(), mesh)
    assert not report.consistent
    assert report.axis_minority == (2, 5)
    assert report.normal_minority == (7,)
    assert report.faces[0].axis_classes == {AXIS_ALONG_U: 10, AXIS_ACROSS_U: 2}
    # A 180 degree in-plane turn changes no tensor component and is not reported.
    mesh.quads[2] = _turn(mesh.quads[2], quadratic)  # now 180 degrees from the start
    assert shell_orientation_report(_PlaneZ0(), mesh).axis_minority == (5,)


def test_element_off_its_face_is_unchecked_not_consistent():
    mesh = _grid_mesh()
    lifted = []
    for n in mesh.quads[3]:  # own copies, so neighbours stay on the face
        new = max(mesh.nodes) + 1
        mesh.nodes[new] = mesh.nodes[n] + np.array([0, 0, 5.0])
        lifted.append(new)
    mesh.quads[3] = tuple(lifted)
    report = shell_orientation_report(_PlaneZ0(), mesh)
    assert not report.consistent
    assert set(report.unchecked) == {3}
    assert "off the face" in report.unchecked[3]


def test_s3_v2d_without_reference_normal_is_unchecked_when_ambiguous():
    # Right triangle: edge 0-1 along x, edge 0-2 along y, so the two candidate
    # local x directions fall in different classes.
    nodes = {1: np.array([0., 0, 0]), 2: np.array([1., 0, 0]), 3: np.array([0., 1, 0])}
    mesh = SimpleNamespace(nodes=nodes, quads={}, tris={1: (1, 2, 3)}, elements_of_face={1: [1]})
    policy = ShellFormulationPolicy.current_default()
    report = shell_orientation_report(_PlaneZ0(), mesh, policy=policy)
    assert set(report.unchecked) == {1}
    # With the reference normal the solver's choice is known.
    up = shell_orientation_report(_PlaneZ0(), mesh, policy=policy,
                                  s3_reference_normals={1: (0, 0, 1)})
    assert up.consistent and up.faces[0].axis_classes == {AXIS_ALONG_U: 1}
    down = shell_orientation_report(_PlaneZ0(), mesh, policy=policy,
                                    s3_reference_normals={1: (0, 0, -1)})
    assert down.faces[0].axis_classes == {AXIS_ACROSS_U: 1}
    assert down.faces[0].normal_against == 1


def test_drawing_barge_60m_deck_patches_are_found():
    """Regression for the 2026-11-18 finding: grey 'membrane xx' deck squares.

    144 of the 864 main-deck quads between the end houses have local x across
    the barge, in three blocks at x 4.98-7.80, 27.20-32.80 and 52.20-55.02 m.
    """
    from anyfem.application.examples import drawing_barge_60m
    from anyfem.jobs import _cancellation_token
    from anyfem.mesh_jobs import MeshSettings, run_mesh_job
    from anyfem.mesh_orientation import project_orientation_report
    from anymesher import MeshAutomationOptions

    project = drawing_barge_60m()
    settings = MeshSettings.create(.5, element_order="quadratic", strategy="quad_first",
                                   layout_policy="adaptive",
                                   automation=MeshAutomationOptions(strict_method=False,
                                                                    allow_inspection=True))
    mesh = run_mesh_job(project, "barge60-axes", settings, lambda *a: None,
                        _cancellation_token()).mesh
    report = project_orientation_report(project, mesh)
    # The end houses also use "Deck 6 mm"; the main deck is the one at z = 2.
    deck = next(f for f, s in project.face_sections.items() if s == "Deck 6 mm"
                and all(abs(mesh.nodes[n][2] - 2.0) < 1e-9
                        for e in mesh.elements_of_face[f] for n in mesh.quads.get(e, ())))
    (deck_report,) = [face for face in report.faces if face.face_id == deck]
    turned = [e for e in deck_report.axis_minority if e in mesh.quads]
    centres = np.array([np.mean([mesh.nodes[n] for n in mesh.quads[e][:4]], axis=0)
                        for e in turned])
    # Measured 2026-11-18 with the pinned mesher: 880 of 4640 shell elements
    # turned across all faces, none with a reversed normal.
    assert not report.normal_minority
    between = (centres[:, 0] > 5) & (centres[:, 0] < 55)
    assert int(between.sum()) == 144
    blocks = sorted({(5 if x < 10 else 30 if x < 40 else 55) for x in centres[between, 0]})
    assert blocks == [5, 30, 55]
    assert not deck_report.unchecked
