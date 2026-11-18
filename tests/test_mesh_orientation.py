"""Shell axis orientation check (direction programme D1).

The check must report the frames the solver's stress recovery actually uses,
at every recovery point, for the elements the solve build would construct
(Codex D1 review round 1, findings 1-7).
"""

from __future__ import annotations

import dataclasses
import warnings
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.mesh_orientation import project_orientation_report
from anyfem.model.formulations import ShellFormulationPolicy


class _Plane:
    """Face 1 = plane z = 0; u direction at ``angle_deg`` from global x."""

    def __init__(self, angle_deg=0.0):
        a = np.radians(angle_deg)
        self.u = np.array([np.cos(a), np.sin(a), 0.0])
        self.v = np.array([-np.sin(a), np.cos(a), 0.0])

    def project_to_face_many(self, face_id, xyz):
        xyz = np.asarray(xyz, dtype=float)
        points = xyz.copy()
        points[:, 2] = 0.0
        uv = np.column_stack([xyz @ self.u, xyz @ self.v])
        return points, uv, np.abs(xyz[:, 2])

    def face_derivatives_many(self, face_id, uv):
        n = len(uv)
        return np.tile(self.u, (n, 1)), np.tile(self.v, (n, 1))


def _project(geometry=None, policy=None):
    return SimpleNamespace(
        geometry=_Plane() if geometry is None else geometry,
        shell_formulation_policy=ShellFormulationPolicy.current_default() if policy is None else policy,
        plate_section_of=lambda face_id: SimpleNamespace(material="steel", thickness=0.01),
    )


def _grid_mesh(nx=4, ny=3, quadratic=True):
    """Structured plate on z = 0, every element with local x along +x."""

    nodes, quads = {}, {}
    ident = iter(range(1, 10_000))
    index = {}

    def node(x, y):
        key = (round(x, 9), round(y, 9))
        if key not in index:
            index[key] = next(ident)
            nodes[index[key]] = np.array([x, y, 0.0])
        return index[key]

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


def _single(coords, kind="quads"):
    nodes = {k + 1: np.asarray(c, dtype=float) for k, c in enumerate(coords)}
    cells = {1: tuple(range(1, len(coords) + 1))}
    return SimpleNamespace(nodes=nodes, quads=cells if kind == "quads" else {},
                           tris=cells if kind == "tris" else {}, elements_of_face={1: [1]})


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


def _legacy_frames(coords):
    """Independent oracle: ShellElement's own frame at each of its Gauss points."""
    from anyfesolver.elements import ShellElement

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        element = ShellElement(1, list(range(1, len(coords) + 1)), "steel", thickness=.01)
    frames = []
    for xi, eta in element.gauss_points:
        _n, dxi, deta = element.compute_shape_functions(float(xi), float(eta))
        frame = element._local_frame_and_derivatives(np.asarray(coords, float), dxi, deta)[0]
        frames.append((frame[:, 0], frame[:, 2]))
    return frames


def _assert_frames(report_frames, oracle):
    assert len(report_frames) == len(oracle)
    for got, (x_ref, n_ref) in zip(report_frames, oracle):
        assert abs(float(np.dot(got.x_axis, x_ref))) == pytest.approx(1.0, abs=1e-9)
        np.testing.assert_allclose(got.normal, n_ref, atol=1e-9)


# --- ordinary behaviour --------------------------------------------------

@pytest.mark.parametrize("quadratic", [True, False])
def test_consistent_plate_reports_nothing(quadratic):
    report = project_orientation_report(_project(), _grid_mesh(quadratic=quadratic))
    assert report.consistent
    (face,) = report.faces
    assert len(face.aligned) == 12 and not face.turned
    assert face.normal_with == 12 and face.normal_against == 0
    assert "(consistent)" in report.summary()


@pytest.mark.parametrize("quadratic", [True, False])
def test_turned_and_reversed_elements_are_found(quadratic):
    mesh = _grid_mesh(quadratic=quadratic)
    mesh.quads[2] = _turn(mesh.quads[2], quadratic)
    mesh.quads[5] = _turn(mesh.quads[5], quadratic)
    mesh.quads[7] = _reverse(mesh.quads[7], quadratic)
    report = project_orientation_report(_project(), mesh)
    assert not report.consistent
    assert report.turned == (2, 5)
    assert report.reversed == (7,)
    # A 180 degree in-plane turn changes no tensor component and is not reported.
    mesh.quads[2] = _turn(mesh.quads[2], quadratic)
    assert project_orientation_report(_project(), mesh).turned == (5,)


def test_planar_qualified_q4_frame_is_the_equation7_frame():
    from anyfesolver.e4_pl_element import equation7_frame

    coords = np.array([[0, 0, 0], [1, .2, 0], [1.1, 1, 0], [0, .9, 0]], float)
    report = project_orientation_report(_project(), _single(coords))
    axes = report.element_axes[1]
    assert axes.element_class == "QualifiedE4PLShellElement"
    frame = equation7_frame(coords)[0]
    _assert_frames(axes.frames, [(frame[:, 0], frame[:, 2])] * 4)


def test_element_off_its_face_is_unchecked_not_consistent():
    mesh = _grid_mesh()
    lifted = []
    for n in mesh.quads[3]:  # own copies, so neighbours stay on the face
        new = max(mesh.nodes) + 1
        mesh.nodes[new] = mesh.nodes[n] + np.array([0, 0, 5.0])
        lifted.append(new)
    mesh.quads[3] = tuple(lifted)
    report = project_orientation_report(_project(), mesh)
    assert not report.consistent
    assert set(report.unchecked) == {3}
    assert "off the face" in report.unchecked[3]


# --- finding 1: warped qualified Q4 recovers through ShellElement -----------

def test_r1_f1_warped_q4_reports_the_shell_element_gauss_frames():
    coords = np.array([[0, 0, 0], [2, 0, 0], [2, 1, .6], [0, 1, 0]], float)
    report = project_orientation_report(_project(), _single(coords))
    _assert_frames(report.element_axes[1].frames, _legacy_frames(coords))


@pytest.mark.parametrize("count", [8, 6])
def test_r1_f1_higher_order_frames_match_the_legacy_gauss_frames(count):
    rng = np.random.default_rng(count)
    if count == 8:
        corners = np.array([[0, 0, 0], [1.3, .1, .05], [1.2, 1.1, .12], [-.1, .9, .03]], float)
        mids = [(corners[k] + corners[(k + 1) % 4]) / 2 for k in range(4)]
    else:
        corners = np.array([[0, 0, 0], [1.2, .2, .04], [.3, 1.1, .09]], float)
        mids = [(corners[k] + corners[(k + 1) % 3]) / 2 for k in range(3)]
    coords = np.vstack([corners, mids]) + rng.normal(scale=.02, size=(count, 3))
    mesh = _single(coords, "quads" if count == 8 else "tris")
    report = project_orientation_report(_project(), mesh)
    _assert_frames(report.element_axes[1].frames, _legacy_frames(coords))


# --- finding 2: a folded higher-order element is not consistent -------------

def test_r1_f2_folded_q8_is_inconsistent_within_the_element():
    coords = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                       [.5, 0, 0], [1, .5, 0], [2, 1, 0], [0, .5, 0]], float)
    oracle = _legacy_frames(coords)
    assert len({int(np.sign(n[2])) for _x, n in oracle}) == 2  # the counterexample folds
    report = project_orientation_report(_project(), _single(coords))
    assert not report.consistent
    assert report.inconsistent == (1,)
    assert "1 inconsistent within the element" in report.summary()


# --- findings 3 and 4: V2D triangles use the build's reference normal -------

def _triangle_project(monkeypatch, source_normal, reference_surface):
    from anyfem.solve import build

    source = np.asarray(source_normal, float) / np.linalg.norm(source_normal)
    monkeypatch.setattr(build, "_qualified_s3_owner_normals", lambda *_: ([1], {1: source}))
    monkeypatch.setattr(build, "assert_s3_admissible", lambda *a, **k: None)
    policy = dataclasses.replace(ShellFormulationPolicy.current_default(),
                                 s3_reference_surface=reference_surface)
    return _project(policy=policy), _single([(0, 0, 0), (2, 0, 0), (0, 1, 0)], "tris")


def test_r1_f3_faceted_s3_reports_the_facet_normal_the_solver_uses(monkeypatch):
    project, mesh = _triangle_project(monkeypatch, (0, 1, 1), "faceted")
    report = project_orientation_report(project, mesh)
    frames = report.element_axes[1].frames
    for frame in frames:
        np.testing.assert_allclose(frame.normal, [0, 0, 1], atol=1e-12)
        assert abs(frame.x_axis[0]) == pytest.approx(1.0)
    assert report.consistent


def test_r1_f4_s3_reference_normal_the_solver_rejects_is_unchecked(monkeypatch):
    project, mesh = _triangle_project(monkeypatch, (0, 1, 1), "source")
    report = project_orientation_report(project, mesh)
    assert not report.consistent
    assert set(report.unchecked) == {1}
    assert "not facet-normal" in report.unchecked[1]


# --- finding 5: no S3 normal authority is never "consistent" ---------------

def test_r1_f5_s3_without_owner_normals_is_unchecked():
    mesh = _single([(0, 0, 0), (2, 0, 0), (2, .2, 0)], "tris")  # no structural_preparation
    report = project_orientation_report(_project(), mesh)
    assert not report.consistent
    assert set(report.unchecked) == {1}
    assert "structural preparation" in report.unchecked[1]
    assert "NOT consistent" in report.summary() and "1 unchecked" in report.summary()


# --- finding 6: classification does not depend on the face parameterisation -

@pytest.mark.parametrize("u_angle", [0.0, 45.0, 30.0, -60.0])
def test_r1_f6_turned_elements_found_whatever_the_u_direction(u_angle):
    mesh = _grid_mesh()
    mesh.quads[2] = _turn(mesh.quads[2])
    mesh.quads[9] = _turn(mesh.quads[9])
    report = project_orientation_report(_project(_Plane(u_angle)), mesh)
    assert not report.consistent
    assert report.turned == (2, 9)
    assert not report.ambiguous


def test_r1_f6_near_45_degrees_is_ambiguous_not_consistent():
    mesh = _grid_mesh(quadratic=False)
    # Rotate element 4's nodes 45 degrees about its centroid: x at 45 deg.
    ids = mesh.quads[4]
    centre = np.mean([mesh.nodes[n] for n in ids], axis=0)
    c, s = np.cos(np.pi / 4), np.sin(np.pi / 4)
    rotation = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    new_ids = []
    for n in ids:
        new = max(mesh.nodes) + 1
        mesh.nodes[new] = centre + rotation @ (mesh.nodes[n] - centre)
        new_ids.append(new)
    mesh.quads[4] = tuple(new_ids)
    report = project_orientation_report(_project(), mesh)
    assert report.ambiguous == (4,)
    assert not report.consistent


def test_r1_f6_even_split_has_no_dominant_direction_and_is_unchecked():
    mesh = _grid_mesh(nx=2, ny=1)
    mesh.quads[2] = _turn(mesh.quads[2])
    report = project_orientation_report(_project(), mesh)
    assert not report.consistent
    assert set(report.unchecked) == {1, 2}
    assert report.faces[0].dominant_angle_deg is None


# --- finding 7: missing element references are unchecked -------------------

def test_r1_f7_face_reference_to_a_missing_element_is_unchecked():
    mesh = _grid_mesh()
    mesh.elements_of_face[1] = list(mesh.elements_of_face[1]) + [999]
    report = project_orientation_report(_project(), mesh)
    assert not report.consistent
    assert set(report.unchecked) == {999}
    assert "missing shell element" in report.unchecked[999]
    assert report.faces[0].element_count == 13


# --- finding 9: the sign rule documented in D0 ----------------------------

def test_r1_f9_normal_flip_with_x_kept_changes_bending_xx_not_bending_xy():
    """Pure twisting on a legacy Q4 and on its mirror (x kept, normal flipped)."""
    from anyfesolver.elements import ShellElement
    from anyfesolver.fe_core import FEMesh, Material

    corners = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
    material = Material(name="probe", elastic_modulus=1.0, poisson_ratio=0.0)

    def recover(order, field):
        mesh = FEMesh()
        for k, p in enumerate(corners):
            mesh.add_node(k + 1, *map(float, p))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            element = ShellElement(1, [k + 1 for k in order], "probe", thickness=.01)
        mesh.add_element(1, element)
        u = np.zeros(24)
        for slot, k in enumerate(order):
            u[6 * slot:6 * slot + 6] = field(corners[k])
        return element.compute_stresses(mesh, u, material)

    def bend(p):  # w = x^2 + x y with Kirchhoff rotations: bending and twist
        x, y, _z = p
        return [y, 0, x * x + x * y, x, -(2 * x + y), 0]  # plus in-plane shear

    base = recover([0, 1, 2, 3], bend)
    # Corner order (3, 2, 1, 0): local x still along +x, normal -z, so y flips.
    mirror = recover([3, 2, 1, 0], bend)
    assert min(abs(np.mean(base[k])) for k in ("bending_xx", "bending_xy", "membrane_xy")) > 1e-6
    np.testing.assert_allclose(np.mean(mirror["bending_xx"]), -np.mean(base["bending_xx"]), atol=1e-9)
    np.testing.assert_allclose(np.mean(mirror["bending_yy"]), -np.mean(base["bending_yy"]), atol=1e-9)
    np.testing.assert_allclose(np.mean(mirror["bending_xy"]), np.mean(base["bending_xy"]), atol=1e-9)
    np.testing.assert_allclose(np.mean(mirror["membrane_xy"]), -np.mean(base["membrane_xy"]), atol=1e-9)


def test_drawing_barge_60m_deck_patches_are_found():
    """Regression for the 2026-11-18 finding: grey 'membrane xx' deck squares.

    144 of the main-deck quads between the end houses have local x across the
    barge, in three blocks at x 4.98-7.80, 27.20-32.80 and 52.20-55.02 m.
    """
    from anyfem.application.examples import drawing_barge_60m
    from anyfem.jobs import _cancellation_token
    from anyfem.mesh_jobs import MeshSettings, run_mesh_job
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
    turned = [e for e in deck_report.turned if e in mesh.quads]
    centres = np.array([np.mean([mesh.nodes[n] for n in mesh.quads[e][:4]], axis=0)
                        for e in turned])
    # Measured 2026-11-18 after the round-1 fixes: 402 of 4640 turned, none
    # reversed; 968 unchecked on the three bulkheads and eight end-house walls
    # whose elements split almost evenly (no dominant direction).
    assert not report.reversed and not report.ambiguous and not report.inconsistent
    assert len(report.turned) == 402 and len(report.unchecked) == 968
    assert all("no dominant local-x direction" in r for r in report.unchecked.values())
    between = (centres[:, 0] > 5) & (centres[:, 0] < 55)
    assert int(between.sum()) == 144
    blocks = sorted({(5 if x < 10 else 30 if x < 40 else 55) for x in centres[between, 0]})
    assert blocks == [5, 30, 55]
    assert not deck_report.unchecked
