"""Exact planar drawing curves adapted from public ANYgeometry edge definitions.

Each working span is a Bezier segment in homogeneous coordinates, in metres.
The last column is its positive weight. Rational control points remain rational
through trimming, plane projection and DXF output.
"""
from __future__ import annotations

from importlib.util import find_spec
import math
import numpy as np
from scipy.integrate import quad
from scipy.interpolate import BPoly, BSpline

from anygeometry.curves import Arc, Spline, Straight, arc_frame

# Older supported ANYgeometry releases predate these two exact owner records.
_LINES = (Straight,)
if find_spec("anygeometry.line_ranges") is not None:
    from anygeometry.line_ranges import LineRange
    _LINES += (LineRange,)
_NURBS = ()
if find_spec("anygeometry.nurbs") is not None:
    from anygeometry.nurbs import NurbsCurve
    _NURBS = (NurbsCurve,)


def _homogeneous(points, weights=None):
    points = np.asarray(points, dtype=float)
    weights = np.ones(len(points)) if weights is None else np.asarray(weights, dtype=float)
    return np.column_stack((points * weights[:, None], weights))


def _nurbs_spans(curve):
    start, end = curve.domain
    lo, hi = sorted((start, end))
    degree = curve.degree
    spline = BSpline(curve.knots, _homogeneous(curve.controls, curve.weights), degree)
    breaks = sorted({lo, hi, *(k for k in curve.knots if lo < k < hi)})
    # Repeated interior knots of multiplicity p separate a degree-p spline
    # into Bezier spans sharing exactly one end control.
    for knot in breaks:
        missing = degree - np.count_nonzero(spline.t == knot)
        if missing > 0:
            spline = spline.insert_knot(knot, int(missing))
    spans = []
    for left in breaks[:-1]:
        i = np.searchsorted(spline.t, left, side="right") - 1
        spans.append(spline.c[i-degree:i+1])
    if end < start:
        return tuple(span[::-1] for span in reversed(spans))
    return tuple(spans)


def _arc_spans(frame):
    def point(angle, divisor=1.0):
        radial = math.cos(angle)*frame.e1 + math.sin(angle)*frame.e2
        return frame.center + frame.radius*radial/divisor

    count = max(1, math.ceil(abs(frame.sweep)/(math.pi/2)))
    angles = np.linspace(0.0, frame.sweep, count + 1)
    spans = []
    for start, end in zip(angles, angles[1:]):
        weight = math.cos((end - start)/2)
        points = [point(start), point((start + end)/2, weight), point(end)]
        spans.append(_homogeneous(points, (1.0, weight, 1.0)))
    return tuple(spans)


def edge_spans(geometry, edge_id):
    edge = geometry.edges[edge_id]
    curve = edge.curve
    if isinstance(curve, _LINES):
        return (_homogeneous(geometry.evaluate_edge_many(edge_id, [0., 1.])),)
    if isinstance(curve, _NURBS):
        return _nurbs_spans(curve)
    start = geometry.vertices[edge.start].position
    end = geometry.vertices[edge.end].position
    if isinstance(curve, Arc):
        via = geometry.vertices[curve.via_vertex].position
        return _arc_spans(arc_frame(start, via, end))
    if isinstance(curve, Spline):
        controls = [geometry.vertices[v].position for v in curve.control_vertices]
        return (_homogeneous([start, *controls, end]),)
    raise ValueError(f"Edge {edge_id}: unsupported plate profile curve {type(curve).__name__}")


def project_span(span, origin, axes):
    weights = span[:, -1:]
    return np.column_stack(((span[:, :-1] - origin*weights) @ axes, weights))


def trace(span, tolerance=1e-6):
    """Flatten within a metre tolerance, for validation and extents only.

    Positive rational Beziers lie in their Euclidean control hull. Subdivide
    until the entire hull is within tolerance of the finite endpoint segment.
    """
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Trace tolerance must be finite and positive")
    def visit(controls, depth):
        xyz = controls[:, :-1] / controls[:, -1:]
        delta = xyz[-1] - xyz[0]
        chord_sq = float(delta @ delta)
        relative = xyz - xyz[0]
        if chord_sq:
            relative -= np.clip((relative @ delta) / chord_sq, 0, 1)[:, None] * delta
        if np.max(np.linalg.norm(relative, axis=1)) <= tolerance:
            points.append(xyz[-1])
            return
        if depth >= 32:
            raise ValueError("Could not bound the plate curve within the drawing tolerance")
        left = [controls[0]]
        right = [controls[-1]]
        while len(controls) > 1:
            controls = (controls[:-1] + controls[1:]) * 0.5
            left.append(controls[0])
            right.append(controls[-1])
        visit(np.asarray(left), depth+1)
        visit(np.asarray(right[::-1]), depth+1)

    points = [span[0, :-1] / span[0, -1]]
    visit(span, 0)
    return np.asarray(points)


def signed_area(span):
    """Green's line integral, including the derivative of rational curves."""
    if len(span) == 2:
        a, b = span[:, :2] / span[:, -1:]
        return 0.5 * float(a[0]*b[1] - a[1]*b[0])
    polynomial = BPoly(span[:, None, :], [0., 1.])
    derivative = polynomial.derivative()

    def integrand(u):
        x, y, w = polynomial(u)
        dx, dy, _ = derivative(u)
        return (x*dy - y*dx)/(2*w*w)

    result = quad(integrand, 0., 1., epsabs=1e-14, epsrel=1e-12, full_output=True)
    if len(result) > 3:
        raise ValueError(f"Plate area integration failed: {result[3]}")
    return float(result[0])


def write_profiles_dxf(path, loops):
    try:
        import ezdxf
    except ImportError as error:
        raise ImportError("Install ANYfem[drawings] to export DXF profiles.") from error
    doc = ezdxf.new("R2010")
    doc.units = ezdxf.units.MM
    doc.header["$MEASUREMENT"] = 1
    space = doc.modelspace()
    for layer in ("OUTLINE", "HOLES"):
        doc.layers.add(layer)
    for i, loop in enumerate(loops):
        attrs = {"layer": "OUTLINE" if i == 0 else "HOLES"}
        for span in loop:
            weights = span[:, -1]
            control_xy = 1000*span[:, :2] / weights[:, None]
            controls = [(x, y, 0.) for x, y in control_xy]
            degree = len(span) - 1
            if degree == 1:
                space.add_line(*controls, dxfattribs=attrs)
            elif degree > 11:
                raise ValueError(f"AutoCAD profiles support spline degrees up to 11 (found {degree})")
            elif np.all(weights == weights[0]):
                space.add_open_spline(controls, degree=degree, dxfattribs=attrs)
            else:
                space.add_rational_spline(controls, list(weights), degree=degree, dxfattribs=attrs)
    doc.saveas(path)
