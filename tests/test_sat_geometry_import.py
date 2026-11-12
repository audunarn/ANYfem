"""Regression coverage for the trimmed analytic faces admitted by SAT import."""
import math

import numpy as np
import pytest

from anygeometry import GeometryModel, OrientedEdge
from anygeometry.exact_curves import EllipticArc
from anygeometry.surfaces import Cylinder

from anyfem.ui.scene import face_display_polygons


@pytest.mark.parametrize("sweep", [math.tau, -math.tau])
def test_raw_cylindrical_face_display_respects_its_trim(sweep):
    model = GeometryModel()
    radius, angle, height = 2., .04, .1
    lower = EllipticArc((0.,0.,0.), (radius,0.,0.), (0.,radius,0.), -angle, 2*angle)
    upper = EllipticArc((0.,0.,height), (radius,0.,0.), (0.,radius,0.), angle, -2*angle)
    points = model.add_points([lower.evaluate(0.), lower.evaluate(1.), upper.evaluate(0.), upper.evaluate(1.)])
    edges = [model.add_curve(points[0],points[1],lower), model.add_line(points[1],points[2]),
             model.add_curve(points[2],points[3],upper), model.add_line(points[3],points[0])]
    surface = Cylinder((0.,0.,0.), (0.,0.,1.), (1.,0.,0.), radius, 1., start_angle=-sweep/2, sweep_angle=sweep)
    face = model.add_face_from_loop([OrientedEdge(edge, True) for edge in edges], surface=surface)
    polygons = face_display_polygons(model,face,divisions=4)
    displayed = np.vstack(polygons)
    assert len(polygons) > 2
    assert np.linalg.norm(displayed[:,:2],axis=1) == pytest.approx(radius)
    assert displayed[:,0].min() >= radius*math.cos(angle)-1e-12
    assert displayed[:,1].min() >= -radius*math.sin(angle)-1e-12
    assert displayed[:,1].max() <= radius*math.sin(angle)+1e-12
    assert displayed[:,2].min() == pytest.approx(0.)
    assert displayed[:,2].max() == pytest.approx(height)


def test_cutting_plane_exports_its_labels_and_line_style(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    from anyfem.application.drawing_views import DrawingPath, DrawingView, write_view_dxf

    view = DrawingView("Section position", (
        DrawingPath("VISIBLE", ((0.,0.),(1.,0.),(1.,1.),(0.,1.)), True),
        DrawingPath("CUT_PLANE", ((.2,.2),(.8,.8)), label="C-C"),
    ), projection="front")
    target = tmp_path / "cut.dxf"
    write_view_dxf(view,target)
    drawing = ezdxf.readfile(target)
    labels = [text.dxf.text for text in drawing.modelspace().query("TEXT")]
    assert labels.count("C-C") == 2
    assert "R1" not in labels
    assert drawing.layers.get("CUT_PLANE").dxf.linetype == "CUT_LINE"
