"""Shell axis orientation check (direction programme D1).

The check must report the frames the solver's stress recovery actually uses,
at every recovery point, for the solver model a solve would build (Codex D1
review rounds 1 and 2).

Most tests replace ``build_fe_model`` by the real shell part of the build
(``build._add_shells`` on a small stand-in project) so they need no meshed
project; the imperfection and barge tests use the full build.
"""

from __future__ import annotations

import dataclasses
import warnings
from types import SimpleNamespace

import numpy as np
import pytest

from anyfem.mesh_orientation import model_orientation_report, project_orientation_report
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


class _ShearedPlane:
    """Plane z = 0 parameterised as r(u, v) = (u, v + g(u), 0), g' = 0 for u < 3, tan 80 deg after."""

    slope = np.tan(np.radians(80.0))

    def _g(self, u):
        return np.where(u < 3.0, 0.0, (u - 3.0) * self.slope)

    def project_to_face_many(self, face_id, xyz):
        xyz = np.asarray(xyz, dtype=float)
        points = xyz.copy()
        points[:, 2] = 0.0
        uv = np.column_stack([xyz[:, 0], xyz[:, 1] - self._g(xyz[:, 0])])
        return points, uv, np.abs(xyz[:, 2])

    def face_derivatives_many(self, face_id, uv):
        du = np.zeros((len(uv), 3))
        du[:, 0] = 1.0
        du[:, 1] = np.where(uv[:, 0] < 3.0, 0.0, self.slope)
        dv = np.tile([0.0, 1.0, 0.0], (len(uv), 1))
        return du, dv


def _stand_in(geometry=None, policy=None):
    return SimpleNamespace(
        geometry=_Plane() if geometry is None else geometry,
        shell_formulation_policy=ShellFormulationPolicy.current_default() if policy is None else policy,
        plate_section_of=lambda face_id: SimpleNamespace(material="steel", thickness=0.01),
    )


def _shell_model(project, mesh, material=None):
    """The shell part of the solve build: real FEModel, real ``_add_shells``."""
    from anyfesolver.fe_core import FEModel, Material

    from anyfem.solve import build

    model = FEModel(name="axes")
    model.register_material(material or Material(name="steel", elastic_modulus=210e9, poisson_ratio=.3))
    for node_id, xyz in sorted(mesh.nodes.items()):
        model.add_node(int(node_id), *map(float, xyz))
    build._add_shells(project, mesh, model)
    return model


@pytest.fixture
def shell_build(monkeypatch):
    """Route ``project_orientation_report`` through the real shell build."""
    from anyfem.solve import build

    calls = []

    def fake(project, mesh, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(fe_model=_shell_model(project, mesh))
    monkeypatch.setattr(build, "build_fe_model", fake)
    return calls


def _report(mesh, geometry=None, policy=None, **kwargs):
    return project_orientation_report(_stand_in(geometry, policy), mesh, **kwargs)


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


def _s3_normals(monkeypatch, normals):
    from anyfem.solve import build

    unit = {k: np.asarray(v, float) / np.linalg.norm(v) for k, v in normals.items()}
    monkeypatch.setattr(build, "_qualified_s3_owner_normals", lambda *_: (sorted(unit), unit))
    monkeypatch.setattr(build, "assert_s3_admissible", lambda *a, **k: None)


# --- ordinary behaviour --------------------------------------------------

@pytest.mark.parametrize("quadratic", [True, False])
def test_consistent_plate_reports_nothing(shell_build, quadratic):
    report = _report(_grid_mesh(quadratic=quadratic))
    assert report.consistent
    (face,) = report.faces
    assert len(face.aligned) == 12 and not face.turned
    assert face.normal_with == 12 and face.normal_against == 0
    assert "(consistent)" in report.summary()
    # The build is asked for the model only: no loads, no supports needed.
    assert shell_build[-1] == dict(load_case=None, require_loads=False, require_supports=False)


@pytest.mark.parametrize("quadratic", [True, False])
def test_turned_and_reversed_elements_are_found(shell_build, quadratic):
    mesh = _grid_mesh(quadratic=quadratic)
    mesh.quads[2] = _turn(mesh.quads[2], quadratic)
    mesh.quads[5] = _turn(mesh.quads[5], quadratic)
    mesh.quads[7] = _reverse(mesh.quads[7], quadratic)
    report = _report(mesh)
    assert not report.consistent
    assert report.turned == (2, 5)
    assert report.reversed == (7,)
    # A 180 degree in-plane turn changes no tensor component and is not reported.
    mesh.quads[2] = _turn(mesh.quads[2], quadratic)
    assert _report(mesh).turned == (5,)


def test_planar_qualified_q4_frame_is_the_equation7_frame(shell_build):
    from anyfesolver.e4_pl_element import equation7_frame

    coords = np.array([[0, 0, 0], [1, .2, 0], [1.1, 1, 0], [0, .9, 0]], float)
    report = _report(_single(coords))
    axes = report.element_axes[1]
    assert axes.element_class == "QualifiedE4PLShellElement"
    frame = equation7_frame(coords)[0]
    _assert_frames(axes.frames, [(frame[:, 0], frame[:, 2])] * 4)


def test_element_off_its_face_is_unchecked_not_consistent(shell_build):
    mesh = _grid_mesh()
    lifted = []
    for n in mesh.quads[3]:  # own copies, so neighbours stay on the face
        new = max(mesh.nodes) + 1
        mesh.nodes[new] = mesh.nodes[n] + np.array([0, 0, 5.0])
        lifted.append(new)
    mesh.quads[3] = tuple(lifted)
    report = _report(mesh)
    assert not report.consistent
    assert set(report.unchecked) == {3}
    assert "off the face" in report.unchecked[3]


def test_empty_report_is_not_consistent():
    mesh = SimpleNamespace(nodes={}, quads={}, tris={}, elements_of_face={})
    report = model_orientation_report(_Plane(), mesh, SimpleNamespace(mesh=None, materials={}))
    assert not report.consistent and "NOT consistent" in report.summary()


def test_r3_f3_face_without_elements_is_not_consistent():
    mesh = SimpleNamespace(nodes={}, quads={}, tris={}, elements_of_face={1: []})
    report = model_orientation_report(_Plane(), mesh, SimpleNamespace(mesh=SimpleNamespace(elements={}),
                                                                     materials={}))
    assert len(report.faces) == 1 and not report.consistent
    assert "NOT consistent" in report.summary()


# --- round 1, finding 1: warped qualified Q4 recovers through ShellElement --

def test_r1_f1_warped_q4_reports_the_shell_element_gauss_frames(shell_build):
    coords = np.array([[0, 0, 0], [2, 0, 0], [2, 1, .6], [0, 1, 0]], float)
    report = _report(_single(coords))
    _assert_frames(report.element_axes[1].frames, _legacy_frames(coords))


@pytest.mark.parametrize("count", [8, 6])
def test_r1_f1_higher_order_frames_match_the_legacy_gauss_frames(shell_build, count):
    rng = np.random.default_rng(count)
    if count == 8:
        corners = np.array([[0, 0, 0], [1.3, .1, .05], [1.2, 1.1, .12], [-.1, .9, .03]], float)
        mids = [(corners[k] + corners[(k + 1) % 4]) / 2 for k in range(4)]
    else:
        corners = np.array([[0, 0, 0], [1.2, .2, .04], [.3, 1.1, .09]], float)
        mids = [(corners[k] + corners[(k + 1) % 3]) / 2 for k in range(3)]
    coords = np.vstack([corners, mids]) + rng.normal(scale=.02, size=(count, 3))
    report = _report(_single(coords, "quads" if count == 8 else "tris"))
    _assert_frames(report.element_axes[1].frames, _legacy_frames(coords))


# --- round 1, finding 2: a folded higher-order element is not consistent ----

def test_r1_f2_folded_q8_is_inconsistent_within_the_element(shell_build):
    coords = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                       [.5, 0, 0], [1, .5, 0], [2, 1, 0], [0, .5, 0]], float)
    oracle = _legacy_frames(coords)
    assert len({int(np.sign(n[2])) for _x, n in oracle}) == 2  # the counterexample folds
    report = _report(_single(coords))
    assert not report.consistent
    assert report.inconsistent == (1,)
    assert "1 inconsistent within the element" in report.summary()


# --- round 1, findings 3 and 4: V2D triangles use the build's reference normal

def _faceted(reference_surface):
    return dataclasses.replace(ShellFormulationPolicy.current_default(),
                               s3_reference_surface=reference_surface)


_TRIANGLE = [(0, 0, 0), (2, 0, 0), (0, 1, 0)]


def test_r1_f3_faceted_s3_reports_the_facet_normal_the_solver_uses(shell_build, monkeypatch):
    _s3_normals(monkeypatch, {1: (0, 1, 1)})
    report = _report(_single(_TRIANGLE, "tris"), policy=_faceted("faceted"))
    for frame in report.element_axes[1].frames:
        np.testing.assert_allclose(frame.normal, [0, 0, 1], atol=1e-12)
        assert abs(frame.x_axis[0]) == pytest.approx(1.0)
    assert report.consistent


def test_r1_f4_s3_reference_normal_the_solver_rejects_is_unchecked(shell_build, monkeypatch):
    _s3_normals(monkeypatch, {1: (0, 1, 1)})
    report = _report(_single(_TRIANGLE, "tris"), policy=_faceted("source"))
    assert not report.consistent
    assert set(report.unchecked) == {1}
    assert "not facet-normal" in report.unchecked[1]


# --- round 1, finding 5: no S3 normal authority is never "consistent" -------

def test_r1_f5_s3_without_owner_normals_is_unchecked(shell_build):
    mesh = _single([(0, 0, 0), (2, 0, 0), (2, .2, 0)], "tris")  # no structural_preparation
    report = _report(mesh)
    assert not report.consistent
    assert set(report.unchecked) == {1}
    assert "solve build refused" in report.unchecked[1]
    assert "structural preparation" in report.unchecked[1]
    assert "NOT consistent" in report.summary() and "1 unchecked" in report.summary()


# --- round 1, finding 6 / round 2, finding 1: no parameterisation dependence -

@pytest.mark.parametrize("u_angle", [0.0, 45.0, 30.0, -60.0])
def test_r1_f6_turned_elements_found_whatever_the_u_direction(shell_build, u_angle):
    mesh = _grid_mesh()
    mesh.quads[2] = _turn(mesh.quads[2])
    mesh.quads[9] = _turn(mesh.quads[9])
    report = _report(mesh, _Plane(u_angle))
    assert not report.consistent
    assert report.turned == (2, 9)
    assert not report.ambiguous


def test_r2_f1_spatially_varying_parameterisation_does_not_hide_turns(shell_build):
    mesh = _grid_mesh()
    for e in (10, 11, 12):  # the last column, where g' = tan 80 deg
        mesh.quads[e] = _turn(mesh.quads[e])
    for geometry in (_Plane(), _ShearedPlane()):
        report = _report(mesh, geometry)
        assert report.turned == (10, 11, 12) and not report.consistent


def test_r1_f6_near_45_degrees_is_ambiguous_not_consistent(shell_build):
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
    report = _report(mesh)
    assert report.ambiguous == (4,)
    assert not report.consistent


def test_r4_even_split_is_mixed_not_unchecked(shell_build):
    mesh = _grid_mesh(nx=2, ny=1)
    mesh.quads[2] = _turn(mesh.quads[2])
    report = _report(mesh)
    assert not report.consistent
    assert report.mixed == (1, 2) and not report.unchecked
    assert report.faces[0].dominant_angle_deg is None
    assert "2 on 1 mixed faces" in report.summary()
    assert set(report.element_axes) == {1, 2}  # compared, so drawn by the overlay


def test_r4_normals_are_still_compared_on_a_mixed_face(shell_build):
    mesh = _grid_mesh(nx=2, ny=2)
    mesh.quads[3] = _turn(mesh.quads[3])
    mesh.quads[4] = _turn(mesh.quads[4])
    mesh.quads[2] = _reverse(mesh.quads[2])
    mesh.quads[1] = _reverse(mesh.quads[1])
    mesh.quads[4] = _reverse(mesh.quads[4])
    report = _report(mesh)
    (face,) = report.faces
    assert face.dominant_angle_deg is None
    assert face.normal_with + face.normal_against == 4
    assert report.reversed == (3,)


# --- round 1, finding 7: missing element references are unchecked ----------

def test_r1_f7_face_reference_to_a_missing_element_is_unchecked():
    """Model level: the full build itself refuses a missing reference."""
    mesh = _grid_mesh()
    model = _shell_model(_stand_in(), mesh)
    mesh.elements_of_face[1] = list(mesh.elements_of_face[1]) + [999]
    report = model_orientation_report(_Plane(), mesh, model)
    assert not report.consistent
    assert set(report.unchecked) == {999}
    assert "missing from the solver model" in report.unchecked[999]
    assert report.faces[0].element_count == 13


def test_r1_f7_build_refusal_of_a_missing_reference_is_unchecked(shell_build):
    mesh = _grid_mesh()
    mesh.elements_of_face[1] = list(mesh.elements_of_face[1]) + [999]
    report = _report(mesh)
    assert not report.consistent and len(report.unchecked) == 13
    assert "missing shell element 999" in report.unchecked[1]


# --- round 4: scope A (static-solve model, solve-equivalent normalisation) --

def _legacy_map_project():
    """One-face project assigned only through the legacy face_sections map."""
    from anygeometry import OrientedEdge
    from anymesher import Mesh

    from anyfem import Project, steel

    project = Project("legacy map")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", .01, "S355")
    g = project.geometry
    edges = g.add_polyline(g.add_points(((0., 0., 0.), (2., 0., 0.), (2., 1., 0.), (0., 1., 0.))),
                           close=True)
    face = g.add_face_from_loop(tuple(OrientedEdge(e, True) for e in edges), corners=(0, 1, 2, 3))
    project.face_sections[face] = "plate"
    mesh = Mesh()
    mesh.nodes = {1: np.array([0., 0, 0]), 2: np.array([2., 0, 0]), 3: np.array([2., 1, 0]),
                  4: np.array([0., 1, 0])}
    mesh.quads = {1: (1, 2, 3, 4)}
    mesh.elements_of_face = {face: [1]}
    return project, mesh


def _section_state(project):
    """UUID-free view of what section-assignment normalisation touches."""
    return (len(project.section_assignments), len(project.regions), len(project.geometry.sheets),
            len(getattr(project.geometry, "parts", ())), dict(project.face_sections),
            dict(project.edge_sections),
            sorted((a.kind, project._section_name(a.kind, a.section_id))
                   for a in project.section_assignments.values()))


def test_r4_check_normalises_exactly_like_a_static_solve_build():
    from anyfem.solve.build import build_fe_model

    checked, mesh = _legacy_map_project()
    solved, solved_mesh = _legacy_map_project()
    before = _section_state(checked)
    report = project_orientation_report(checked, mesh)
    build_fe_model(solved, solved_mesh, load_case=None, require_loads=False, require_supports=False)
    assert _section_state(checked) != before  # the solve-equivalent normalisation happened
    assert _section_state(checked) == _section_state(solved)
    assert report.consistent and "Static-solve model" in report.summary()


def test_r4_cancel_stops_the_check(shell_build):
    import threading

    from anyfem.mesh_orientation import OrientationCancelled

    event = threading.Event()
    event.set()
    with pytest.raises(OrientationCancelled):
        _report(_grid_mesh(), cancel=event)


# --- round 4: speed - solver-frame route, cross-checked by the probe ------

@pytest.mark.parametrize("count", [8, 6, 4, 3])
def test_r4_solver_frame_route_equals_the_probe(count):
    """For every legacy element of a curved, noisy mesh both routes give the same frames."""
    from anyfesolver.fe_core import Material

    from anyfem.mesh_orientation import (_legacy_recovery_frames, _probe_material, _same_frames,
                                         element_recovery_frames)

    rng = np.random.default_rng(10 + count)
    mesh = _grid_mesh(quadratic=count in (6, 8))
    for n, xyz in mesh.nodes.items():
        x, y, _z = xyz
        mesh.nodes[n] = np.array([x, y, .15 * x * x - .1 * x * y]) + rng.normal(scale=.01, size=3)
    if count in (3, 6):  # split every quad into two triangles
        tris = {}
        for e, ids in mesh.quads.items():
            c = ids[:4]
            if count == 3:
                tris[2 * e - 1], tris[2 * e] = (c[0], c[1], c[2]), (c[0], c[2], c[3])
            else:
                mid = ids[4:]
                diag = max(mesh.nodes) + 1
                mesh.nodes[diag] = (mesh.nodes[c[0]] + mesh.nodes[c[2]]) / 2
                tris[2 * e - 1] = (c[0], c[1], c[2], mid[0], mid[1], diag)
                tris[2 * e] = (c[0], c[2], c[3], diag, mid[2], mid[3])
        mesh = SimpleNamespace(nodes=mesh.nodes, quads={}, tris=tris, elements_of_face={1: sorted(tris)})
    policy = ShellFormulationPolicy.legacy_compatible()
    model = _shell_model(_stand_in(policy=policy), mesh)
    steel = Material(name="steel", elastic_modulus=210e9, poisson_ratio=.3)
    for element in model.mesh.elements.values():
        assert type(element).__name__ == "ShellElement"
        fast = _legacy_recovery_frames(element, model.mesh, steel)
        assert _same_frames(fast, element_recovery_frames(element, model.mesh, _probe_material()))
    report = model_orientation_report(_Plane(), mesh, model)
    assert {a.frame_source for a in report.element_axes.values()} == {"solver-frame"}


def test_r4_a_disagreeing_solver_frame_switches_everything_to_the_probe(shell_build, monkeypatch):
    import anyfem.mesh_orientation as orientation

    real = orientation._legacy_recovery_frames

    def wrong(element, fe_mesh, material):  # every frame turned 90 degrees
        return tuple(orientation.RecoveryFrame(f.y_axis, -f.x_axis, f.normal, f.natural_point)
                     for f in real(element, fe_mesh, material))
    monkeypatch.setattr(orientation, "_legacy_recovery_frames", wrong)
    mesh = _grid_mesh()
    mesh.quads[5] = _turn(mesh.quads[5])
    report = _report(mesh, policy=ShellFormulationPolicy.legacy_compatible())
    assert report.turned == (5,)
    assert {a.frame_source for a in report.element_axes.values()} == {"stress-probe"}


# --- round 5, class B: the fast route only where it is correct by construction

def test_r5_instance_override_is_probed_not_fast_tracked(shell_build, monkeypatch):
    """An element whose stress path is overridden on the instance goes through the probe."""
    import anyfem.mesh_orientation as orientation

    real = orientation.model_recovery_frames

    def patched(fe_model, mesh, **kwargs):
        element = fe_model.mesh.elements[2]  # not the sampled first element
        original = element.compute_stresses

        def doubled(*args, **kw):
            result = dict(original(*args, **kw))
            for key in ("membrane_xx", "membrane_yy", "membrane_xy"):
                result[key] = 2.0 * np.asarray(result[key])
            return result
        element.compute_stresses = doubled
        return real(fe_model, mesh, **kwargs)
    monkeypatch.setattr(orientation, "model_recovery_frames", patched)
    report = _report(_grid_mesh(nx=2, ny=1), policy=ShellFormulationPolicy.legacy_compatible())
    assert not report.consistent
    assert set(report.unchecked) == {2} and "not a rotated orthonormal frame" in report.unchecked[2]


def test_r5_coordinates_far_beyond_the_element_scale_are_unchecked(shell_build):
    """At 1e12 m from the origin the solver's own frame is off by ~0.04 deg: refuse it."""
    mesh = _grid_mesh(nx=2, ny=1)
    shift = np.array([1e12, -1e12, 0.0])
    moved = {}
    for n in mesh.quads[2]:
        new = max(mesh.nodes) + 1 + len(moved)
        moved[n] = new
    for n, new in moved.items():
        mesh.nodes[new] = mesh.nodes[n] + shift
    mesh.quads[2] = tuple(moved[n] for n in mesh.quads[2])
    report = _report(mesh, geometry=_FarPlane(), policy=ShellFormulationPolicy.legacy_compatible())
    assert 2 in report.unchecked and "element sizes from the origin" in report.unchecked[2]
    assert not report.consistent


class _FarPlane(_Plane):
    """z = 0 plane that also reports a zero distance far from the origin."""


def test_r5_a_failing_sample_probe_switches_everything_to_the_probe(shell_build, monkeypatch):
    import anyfem.mesh_orientation as orientation

    real = orientation.element_recovery_frames
    calls = {"n": 0}

    def flaky(element, fe_mesh, material=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("transient probe failure")
        return real(element, fe_mesh, material)
    monkeypatch.setattr(orientation, "element_recovery_frames", flaky)
    report = _report(_grid_mesh(), policy=ShellFormulationPolicy.legacy_compatible())
    assert {a.frame_source for a in report.element_axes.values()} == {"stress-probe"}
    assert report.consistent and len(report.element_axes) == 12


# --- round 2, finding 2: imperfections move the solver's reference geometry -

def _plate_project(imperfection=None):
    from anygeometry import OrientedEdge

    from anyfem import Project, steel
    from anyfem.mesh_jobs import MeshSettings
    from anyfem.model.imperfections import plate_mode

    project = Project("axes plate")
    project.add_material(steel("S355", .01))
    project.add_plate_section("plate", .01, "S355")
    g = project.geometry
    edges = g.add_polyline(g.add_points(((0., 0., 0.), (2., 0., 0.), (2., 1., 0.), (0., 1., 0.))),
                           close=True)
    face = g.add_face_from_loop(tuple(OrientedEdge(e, True) for e in edges), corners=(0, 1, 2, 3))
    g.add_sheet((face,))
    project.assign_plate(face, "plate")
    if imperfection:
        project.add_imperfection(plate_mode(project.face(face), amplitude=imperfection))
    mesh = project.mesh_session().generate_mesh(MeshSettings.create(.5, strategy="quad_first",
                                                                     element_order="linear"))
    return project, mesh


def test_r2_f2_frames_include_the_imperfections_the_solve_applies():
    from anyfem.solve.build import build_fe_model

    project, mesh = _plate_project(imperfection=.1)
    report = project_orientation_report(project, mesh)
    model = build_fe_model(project, mesh, load_case=None, require_loads=False,
                           require_supports=False).fe_model
    moved = 0
    for eid, axes in report.element_axes.items():
        element = model.mesh.elements[eid]
        imperfect = np.asarray(element.get_node_coordinates(model.mesh), float)
        original = np.asarray([mesh.nodes[n] for n in mesh.quads[eid]], float)
        _assert_frames(axes.frames, _legacy_frames(imperfect))  # warped Q4: Gauss frames
        flat = _legacy_frames(original)
        moved += max(np.degrees(np.arccos(min(1., abs(float(f.normal @ n)))))
                     for f, (_x, n) in zip(axes.frames, flat)) > 1.0
    assert len(report.element_axes) == len(mesh.quads) and moved > 0


# --- round 2, findings 3 and 4: model scope and material are admitted -------

def test_r2_f3_mixed_shell_scope_the_solver_refuses_is_unchecked(monkeypatch):
    nodes = {1: (0, 0, 0), 2: (1, 0, 0), 3: (0, 1, 0), 4: (2, 0, 0), 5: (2, 1, 0), 6: (1, 1, 0)}
    mesh = SimpleNamespace(nodes={k: np.asarray(v, float) for k, v in nodes.items()},
                           tris={1: (1, 2, 3)}, quads={2: (2, 4, 5, 6)},
                           elements_of_face={1: [1, 2]})
    _s3_normals(monkeypatch, {1: (0, 0, 1)})
    policy = ShellFormulationPolicy(q4="legacy", s3="e4-pl-s3-v2d")
    model = _shell_model(_stand_in(policy=policy), mesh)
    report = model_orientation_report(_Plane(), mesh, model)
    assert not report.consistent
    assert 1 in report.unchecked and "mixed shell scope" in report.unchecked[1]


def test_r2_f4_material_the_solver_refuses_is_unchecked(monkeypatch):
    from anyfesolver.materials import OrthotropicMaterial

    _s3_normals(monkeypatch, {1: (0, 0, 1)})
    mesh = _single(_TRIANGLE, "tris")
    material = OrthotropicMaterial(
        name="steel", elastic_modulus_1=150e9, elastic_modulus_2=12e9, elastic_modulus_3=10e9,
        poisson_ratio_12=.25, poisson_ratio_13=.2, poisson_ratio_23=.3, shear_modulus_12=5e9,
        shear_modulus_13=4e9, shear_modulus_23=3.8e9, density=1600.0)
    model = _shell_model(_stand_in(), mesh, material)
    report = model_orientation_report(_Plane(), mesh, model)
    assert not report.consistent
    assert set(report.unchecked) == {1} and "isotropic" in report.unchecked[1]


# --- round 2, finding 5: a generator for faces is read once -----------------

def test_r2_f5_generator_faces_argument(shell_build):
    mesh = _grid_mesh()
    mesh.quads[2] = _turn(mesh.quads[2])
    report = _report(mesh, faces=(f for f in [1]))
    assert report.turned == (2,) and len(report.faces) == 1 and not report.consistent


# --- round 2, finding 6: unsupported connectivity becomes unchecked ---------

def test_r2_f6_unsupported_connectivity_is_unchecked_not_raised(shell_build):
    mesh = _grid_mesh(quadratic=False)
    mesh.nodes[999] = np.array([.5, .5, 0.])
    mesh.quads[1] = mesh.quads[1] + (999,)
    report = _report(mesh)
    assert not report.consistent and len(report.unchecked) == 12
    assert "5 nodes" in report.unchecked[1]


# --- round 1, finding 9 / round 2, finding 7: the D0 sign rule ---------------

def test_r1_f9_normal_flip_with_x_kept_changes_bending_xx_yy_not_bending_xy():
    """Bending, twist and in-plane shear on a legacy Q4 and its mirror (x kept, normal flipped)."""
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

    def bend(p):  # w = x^2 + x y + 2 y^2 with Kirchhoff rotations, plus in-plane shear
        x, y, _z = p
        return [y, 0, x * x + x * y + 2 * y * y, x + 4 * y, -(2 * x + y), 0]

    base = recover([0, 1, 2, 3], bend)
    # Corner order (3, 2, 1, 0): local x still along +x, normal -z, so y flips.
    mirror = recover([3, 2, 1, 0], bend)
    keys = ("bending_xx", "bending_yy", "bending_xy", "membrane_xy")
    assert min(abs(np.mean(base[k])) for k in keys) > 1e-6
    for key, sign in (("bending_xx", -1), ("bending_yy", -1), ("bending_xy", 1),
                      ("membrane_xy", -1), ("membrane_xx", 1)):
        np.testing.assert_allclose(np.mean(mirror[key]), sign * np.mean(base[key]), atol=1e-9)


def test_drawing_barge_60m_deck_patches_are_found():
    """Regression for the 2026-11-18 finding: grey 'membrane xx' deck squares.

    144 of the main-deck quads between the end houses have local x across the
    barge, in three blocks at x 4.98-7.80, 27.20-32.80 and 52.20-55.02 m.
    Numbers measured with ANYmesher 432b4ed (the pin of ANYfem main 05b13c9)
    and, identically, with 0d85b68.
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
    # Measured 2026-11-18 after the round-4 changes: 402 of 4640 turned, none
    # reversed; 968 mixed on the three bulkheads and eight end-house walls
    # whose elements split almost evenly (no dominant direction); 0 unchecked.
    assert not report.reversed and not report.ambiguous and not report.inconsistent
    assert len(report.turned) == 402 and len(report.mixed) == 968 and not report.unchecked
    assert sum(1 for face in report.faces if face.mixed) == 11
    between = (centres[:, 0] > 5) & (centres[:, 0] < 55)
    assert int(between.sum()) == 144
    blocks = sorted({(5 if x < 10 else 30 if x < 40 else 55) for x in centres[between, 0]})
    assert blocks == [5, 30, 55]
    assert not deck_report.unchecked
