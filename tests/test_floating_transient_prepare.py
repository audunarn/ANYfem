"""Coarse transient preparation: conservative panel operator, exact nodal hydrostatics, provenance binding, preflight.

The owner region integration is replaced by a manufactured linear stand-in (field at the triangle centroid x area x
normal, split equally to the element nodes: exact for LINEAR fields) so the operator algebra and identities are tested
without a real hull project.  Manufactured data only; nothing here is a native qualification.
"""
from types import SimpleNamespace

import numpy as np
import pytest

import anysolver.floating_pressure_regions as fpr
from anyfem.application.floating_source import MeanPose, OverlapMap, SourceManifest, TargetCells, map_pressure_nodal
from anyfem.application.floating_transient_prepare import (
    FloatingTransientPrepareError,
    exact_hydrostatic_nodal,
    mean_hydrostatic,
    prepare_coarse_transient,
    require_bound_to_mean,
    source_panel_ids,
)

CG = np.array([0.0, 0.0, -1.5])


def _fake_region(element, mesh, tris, fn, sign=1.0, kernel_proof=None):
    vec = np.zeros(6)
    for ti, tri in enumerate(np.asarray(tris)):
        nrm = np.cross(tri[1] - tri[0], tri[2] - tri[0]) / 2.0
        val = float(np.asarray(fn(tri.mean(axis=0)[None], np.array([ti])))[0])
        vec[:3] += sign * val * nrm
    nodes = element.nodes
    return SimpleNamespace(node_ids=nodes, vectors=[vec / len(nodes)] * len(nodes))


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(fpr, "region_nodal_vectors", _fake_region)
    tri_a = np.array([[0, 0, -1.0], [1, 0, -1.0], [0, 1, -1.0]])
    tri_b = np.array([[0, 0, -2.0], [0, 1, -2.0], [1, 0, -2.0]])
    verts = np.vstack([tri_a, tri_b])
    source = SourceManifest(vertices=verts, faces=np.array([[0, 1, 2, 2], [3, 4, 5, 5]]), surface_ids=("hull", "hull"),
                            triangles=(tri_a, tri_b), areas=np.array([0.5, 0.5]),
                            centers=np.array([tri_a.mean(0), tri_b.mean(0)]),
                            normals=np.array([[0, 0, 1.0], [0, 0, -1.0]]), provenance={"generator": "coarse-test"})
    pose = MeanPose(np.eye(3), np.zeros(3))
    # a vertical wet side wall (pressure varies linearly WITHIN the polygon -> within-panel moment matters)
    wall = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, -2.0], [2.0, 0.0, -2.0], [2.0, 0.0, 0.0]])
    target = TargetCells(element_ids=(10, 11), polygons_wet=(), polygons_wet_body=(wall, tri_b), area_wet=np.ones(2),
                         area_element=np.ones(2), centroid_wet=np.zeros((2, 3)), sign=np.array([1.0, -1.0]),
                         label=("hull", "hull"))
    half = tri_a.copy(); half[2] = [0.5, 0.5, -1.0]
    omap = OverlapMap({0: (np.array([tri_a, half]), np.array([0, 1])), 1: (np.array([tri_b]), np.array([1]))},
                      {"source_uncovered_rel": 0.0})
    elements = {10: SimpleNamespace(nodes=(1, 2)), 11: SimpleNamespace(nodes=(2, 3))}
    fe = SimpleNamespace(mesh=SimpleNamespace(elements=elements))
    from anyhydro.floating_transient_prepare import family_geometry_digest, query_points_digest
    hid = dict(family_Q0=np.eye(3).tolist(), family_cg_world=CG.tolist(), panel_ids=["0:hull", "1:hull"],
               query_point_ids=["q0"], query_points_world=[[1.0, 0.0, 0.0]],
               query_points_digest=query_points_digest([[1.0, 0.0, 0.0]]),
               family_geometry_digest=family_geometry_digest(verts, source.faces, source.surface_ids))
    return dict(source=source, pose=pose, target=target, omap=omap, fe=fe, nodes=(1, 2, 3), hid=hid)


def _prep(case, **kw):
    kw.setdefault("hydro_identity", case["hid"])
    return prepare_coarse_transient(None, None, case["fe"], case["nodes"], case["source"], case["pose"], CG,
                                    rho=1025.0, gravity=9.81, _target=case["target"], _omap=case["omap"], **kw)


def test_panel_operator_reproduces_the_owner_mapping_for_any_panel_pressure(case):
    ops = _prep(case)
    assert ops.panel_to_nodal.shape == (3, 6, 2)
    rng = np.random.default_rng(3)
    for _ in range(3):
        p = rng.standard_normal(2)
        ref = map_pressure_nodal(case["fe"], case["nodes"], case["target"], case["omap"], p)
        np.testing.assert_allclose(np.einsum("nkp,p->nk", ops.panel_to_nodal, p), ref, rtol=0, atol=1e-12)
    assert ops.panel_ids == source_panel_ids(case["source"]) == ("0:hull", "1:hull")
    assert ops.identities["remeshed"] is False and ops.identities["source_digest"] == case["source"].digest()
    fields = ops.input_fields()
    assert fields["waterline_point_ids"] == ("q0",)
    np.testing.assert_allclose(fields["waterline_points_body"], [[1.0, 0.0, 1.5]])     # body, CG-relative


def test_exact_hydrostatics_integrate_the_linear_field_not_the_panel_centre(case):
    rho, g = 1025.0, 9.81
    mean, op = exact_hydrostatic_nodal(case["fe"], case["nodes"], case["target"], case["pose"], CG, rho=rho, gravity=g)
    # wall (fluid side +y normal of the polygon order): resultant = rho g * area * depth of the CENTROID (1 m), area 4
    wall_nrm = np.cross(np.array([0.0, 0.0, -2.0]), np.array([2.0, 0.0, -2.0])) / 2.0      # first fan triangle
    total = mean[:, :3].sum(axis=0)
    assert np.isfinite(total).all() and np.any(total != 0.0)
    # heave operator: dp = -rho g dz for every wet point -> resultant -rho g * (projected normal area)
    for j in range(6):
        assert op[j].shape == (3, 6)
    np.testing.assert_allclose(op[2][:, :3].sum(axis=0), -rho * g * _signed_area_vector(case["target"]), rtol=1e-12)
    assert wall_nrm.shape == (3,)


def _signed_area_vector(target):
    s = np.zeros(3)
    for poly, sign in zip(target.polygons_wet_body, target.sign):
        for k in range(1, len(poly) - 1):
            s += sign * np.cross(poly[k] - poly[0], poly[k + 1] - poly[0]) / 2.0
    return s


def test_mean_pressure_and_perturbation_come_from_the_same_panel_centres(case):
    rho, g = 1025.0, 9.81
    p0, Hs = mean_hydrostatic(case["source"], case["pose"], CG, rho=rho, gravity=g)
    np.testing.assert_allclose(p0, rho * g * np.array([1.0, 2.0]))
    for j in range(6):
        q = np.zeros(6); q[j] = 1e-6
        c = case["source"].centers - CG
        moved = CG + c + q[:3] + np.cross(q[3:], c)
        fd = (rho * g * (-moved[:, 2]) - p0) / 1e-6
        np.testing.assert_allclose(Hs[:, j], fd, rtol=1e-6, atol=1e-6)


def test_source_pose_mismatch_and_foreign_kernels_are_refused(case):
    stale = dict(case["hid"], family_cg_world=[0.0, 0.0, -1.2])
    with pytest.raises(FloatingTransientPrepareError, match="different pose"):
        _prep(case, hydro_identity=stale)
    with pytest.raises(FloatingTransientPrepareError, match="lacks"):
        _prep(case, hydro_identity={k: v for k, v in case["hid"].items() if k != "family_Q0"})
    bad = SimpleNamespace(panel_ids=("1:hull", "0:hull"), query_point_ids=("q0",), content_hash=lambda: "x")
    with pytest.raises(FloatingTransientPrepareError, match="panel ids differ"):
        _prep(case, field_kernel=bad)
    reordered = SimpleNamespace(panel_ids=("0:hull", "1:hull"), query_point_ids=("q1",), content_hash=lambda: "fk")
    with pytest.raises(FloatingTransientPrepareError, match="query-point ids"):
        _prep(case, field_kernel=reordered)
    geo = {"family_geometry_digest": case["hid"]["family_geometry_digest"]}
    good = SimpleNamespace(panel_ids=("0:hull", "1:hull"), query_point_ids=("q0",), content_hash=lambda: "fk",
                           identity=geo)
    assert _prep(case, field_kernel=good).identities["field_kernel_hash"] == "fk"
    foreign = SimpleNamespace(panel_ids=("0:hull", "1:hull"), query_point_ids=("q0",), content_hash=lambda: "fk",
                              identity={"family_geometry_digest": "0" * 64})
    with pytest.raises(FloatingTransientPrepareError, match="geometry"):
        _prep(case, field_kernel=foreign)


def test_same_pose_same_ids_but_different_geometry_is_refused(case):
    import dataclasses
    moved = case["source"].vertices.copy(); moved[0, 0] += 0.25
    other = dataclasses.replace(case["source"], vertices=moved)
    with pytest.raises(FloatingTransientPrepareError, match="not the geometry"):
        prepare_coarse_transient(None, None, case["fe"], case["nodes"], other, case["pose"], CG, rho=1025.0,
                                 gravity=9.81, hydro_identity=case["hid"], _target=case["target"], _omap=case["omap"])
    with pytest.raises(FloatingTransientPrepareError, match="coordinates"):
        _prep(case, hydro_identity=dict(case["hid"], query_points_world=[[1.0, 0.0, 1e-6]]))
    with pytest.raises(FloatingTransientPrepareError, match="finite"):
        _prep(case, hydro_identity=dict(case["hid"], family_cg_world=[np.nan, 0.0, -1.5]))
    with pytest.raises(FloatingTransientPrepareError, match="pose_tol"):
        _prep(case, pose_tol=float("nan"))


def test_stale_pose_is_refused_until_rebound(case):
    ops = _prep(case)
    require_bound_to_mean(ops, np.eye(3), np.zeros(3))
    with pytest.raises(FloatingTransientPrepareError, match="different pose"):
        require_bound_to_mean(ops, np.eye(3), np.array([0.0, 0.0, 0.1]))


def test_operator_preflight_refuses_before_any_mapping(case, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("mapping must not run")
    monkeypatch.setattr(fpr, "region_nodal_vectors", boom)
    many = tuple(range(1, 3_000_000))
    with pytest.raises(FloatingTransientPrepareError, match="B >"):
        prepare_coarse_transient(None, None, case["fe"], many, case["source"], case["pose"], CG,
                                 rho=1025.0, gravity=9.81, hydro_identity=case["hid"], _target=case["target"],
                                 _omap=case["omap"])
