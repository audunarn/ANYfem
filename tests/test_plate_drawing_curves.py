import numpy as np
import pytest
from scipy.interpolate import BPoly

from anygeometry import GeometryModel
from anyfem.application.plate_curves import edge_spans, project_span, signed_area, trace


def test_trimmed_reversed_rational_curve_keeps_source_parameterization():
    NurbsCurve = pytest.importorskip("anygeometry.nurbs").NurbsCurve
    curve = NurbsCurve(2, (0, 0, 0, 0.4, 1, 1, 1),
                       ((0, 0, 0), (.5, .9, 0), (1, .3, 0), (1.2, 0, 0)),
                       (.9, .6, 1, .8), (.83, .17))
    g = GeometryModel()
    start, end = g.add_points(curve.evaluate(np.array([0., 1.])))
    edge = g.add_curve(start, end, curve)
    spans = edge_spans(g, edge)
    assert len(spans) == 2
    junction = (.83 - .4)/(.83 - .17)
    for span, u0, u1 in zip(spans, (0., junction), (junction, 1.)):
        homogeneous = BPoly(span[:, None, :], [0., 1.])(np.linspace(0., 1., 33))
        actual = homogeneous[:, :3]/homogeneous[:, -1:]
        np.testing.assert_allclose(actual, g.evaluate_edge_many(edge, np.linspace(u0, u1, 33)), atol=1e-13)


def test_reflex_arc_integral_and_trace():
    g = GeometryModel()
    a, via, b = g.add_points([(1, 0, 0), (-1, 0, 0), (0, -1, 0)])
    edge = g.add_arc(a, via, b)
    spans = edge_spans(g, edge)
    assert len(spans) == 3
    planar = [project_span(span, np.zeros(3), np.eye(3)[:, :2]) for span in spans]
    assert sum(signed_area(s) for s in planar) == pytest.approx(3*np.pi/4, abs=1e-13)
    assert sum(signed_area(s[::-1]) for s in planar) == pytest.approx(-3*np.pi/4, abs=1e-13)
    for s in planar:
        points = trace(s, 1e-4)
        np.testing.assert_allclose(np.linalg.norm(points, axis=1), 1., atol=1e-13)
