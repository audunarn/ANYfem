"""World coordinates remain tied to visible sketch-edge ticks and camera state."""
import os

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

from PySide6.QtCore import Qt, QEvent, QObject
from PySide6.QtTest import QTest
from anygeometry import SketchPlane
from anyfem.model.coordinates import CoordinateSystem
from anyfem.model.units import unit_profile
from anyfem.model.workplanes import Workplane
from anyfem.sketcher import FaceSketchTask
from test_qt_geometry_actions import qapp, window
from test_qt_sketcher_workflow import enter_sketch, click_uv


def numbers(sample):
    return np.array([float(item.split("=")[1]) for item in sample[3].split()])


def test_label_contrast_tracks_canvas_even_with_opposite_native_palette(window,qapp):
    from PySide6.QtGui import QColor,QPalette
    from anyfem.ui.qt import theme
    from test_qt_legend_contrast import luminance
    panel=enter_sketch(window,qapp)
    viewport=window.viewport;ruler=viewport._sketch_coordinates
    original_theme=theme.is_dark();original_palette=viewport.widget.palette()
    try:
        for dark in (False,True,False):
            theme.set_dark(window,dark,persist=False)
            palette=viewport.widget.palette()
            palette.setColor(QPalette.Window,QColor('white' if dark else '#202020'))
            viewport.widget.setPalette(palette)
            qapp.processEvents();ruler.sync()
            image=ruler.grab().toImage()
            ratio=image.devicePixelRatio()
            background=image.pixelColor(round(10*ratio),round(10*ratio))
            foreground,_=ruler._label_colors()
            values=sorted((luminance(background.getRgb()[:3]),luminance(foreground.getRgb()[:3])))
            assert (values[1]+.05)/(values[0]+.05)>=7
            assert background.lightness()<80 if dark else background.lightness()>230
            assert foreground.lightness()>180 if dark else foreground.lightness()<90
            # Confirm this is painted text, rather than just contrasting helpers.
            rgb=np.array(foreground.getRgb()[:3])
            pixels=[image.pixelColor(x,y).getRgb()[:3] for x in range(round(14*ratio),round(130*ratio)) for y in range(round(12*ratio),round(27*ratio))]
            assert np.min(np.linalg.norm(np.asarray(pixels)-rgb,axis=1))<20
            if os.environ.get('ANYFEM_SKETCH_CONTRAST_IMAGES')=='1':
                image.save('C:/ANYworkspaceWork/sketch-label-'+('dark' if dark else 'light')+'-20261026.png')
    finally:
        viewport.widget.setPalette(original_palette)
        theme.set_dark(window,original_theme,persist=False)
        panel.cancel()


def assert_tick_positions(window):
    viewport = window.viewport
    ruler = viewport._sketch_coordinates
    assert ruler.isVisible() and len(ruler.samples) == 4
    assert ruler.global_origin is not None
    assert ruler.global_axes
    ratio = viewport.canvas.event_widget.devicePixelRatioF()
    for _, x, y, text in ruler.samples:
        assert all(f"{axis}=" in text for axis in "XYZ")
        world = window.project.units.to_si(numbers((None, x, y, text)), "length")
        # Independent camera round trip: displayed world numbers project back
        # to the edge tick (allow only the five-significant-digit text rounding).
        projected = viewport.project_point(viewport._point3d(*world))
        assert np.array(projected[:2])/ratio == pytest.approx((x, y), abs=.6)
        frame = viewport._construction_workplane.resolve(viewport._construction_coordinate_systems)
        assert abs(frame.normal_distance(world)) < 5e-4


@pytest.mark.parametrize("axis,reference,fixed", [
    ((0, 0, 1), (1, 0, 0), 2),
    ((0, -1, 0), (1, 0, 0), 1),
    ((1, 0, 0), (0, 1, 0), 0),
    ((1, 2, 3), (1, -1, 0), None),
])
def test_translated_offset_principal_and_oblique_planes(window, qapp, axis, reference, fixed):
    panel = window.panels["Construction"]
    system = CoordinateSystem("Translated", origin=(11, -7, 4), axis=axis, reference=reference)
    systems = {system.id: system}
    workplane = Workplane(system.id, offset=2.5)
    frame = workplane.resolve(systems)
    plane = SketchPlane(frame.origin, frame.x_axis, frame.y_axis, frame.normal, (), ())
    panel.begin(FaceSketchTask(plane, .05), workplane, systems)
    qapp.processEvents()
    assert_tick_positions(window)
    assert window.viewport._sketch_coordinates.origin_off_plane
    if fixed is not None:
        assert [numbers(sample)[fixed] for sample in window.viewport._sketch_coordinates.samples] == pytest.approx([frame.origin[fixed]]*4)
    panel.cancel()


def test_camera_resize_units_and_density_update_without_scene_rebuild(window, qapp, monkeypatch):
    panel = enter_sketch(window, qapp)
    viewport = window.viewport
    ruler = viewport._sketch_coordinates
    assert ruler.testAttribute(Qt.WA_TransparentForMouseEvents)
    assert_tick_positions(window)
    before = ruler.samples
    clears = []
    monkeypatch.setattr(viewport.canvas, "clear", lambda *args, **kwargs: clears.append(1))
    viewport.canvas.set_view(-70, 65)
    qapp.processEvents()
    assert ruler.samples != before
    assert_tick_positions(window)
    window.resize(window.width()+160, window.height()+80)
    qapp.processEvents()
    assert ruler.size() == viewport.canvas.event_widget.size()
    assert_tick_positions(window)
    before = tuple(numbers(sample) for sample in ruler.samples)
    window.project.units = unit_profile("SI-mm-N-MPa")
    viewport.canvas.redraw()
    qapp.processEvents()
    assert ruler.heading == "World coordinates (mm)"
    for old, sample in zip(before, ruler.samples):
        assert numbers(sample) == pytest.approx(old*1000, rel=1e-4)
    assert_tick_positions(window)
    viewport._construction_workplane = viewport._construction_workplane.with_grid(1e-6)
    viewport.refresh_construction_overlay()
    qapp.processEvents()
    assert len(ruler.samples) == 4 and not clears
    panel.cancel()


def test_preview_lifetime_cancel_and_renderer_replacement(window, qapp):
    panel = enter_sketch(window, qapp)
    click_uv(window, qapp, (1, 1))
    click_uv(window, qapp, (2, 1))
    viewport = window.viewport
    ruler = viewport._sketch_coordinates
    counts = []
    for i in range(12):
        window.sketcher.move(window.sketcher.task.plane.world((2+i*.01, 2)))
        viewport.refresh_construction_overlay()
        qapp.processEvents()
        counts.append((len(viewport.canvas._entries), len(viewport.canvas._world_text)))
    assert len(set(counts)) == 1
    viewport.switch_backend("software")
    qapp.processEvents()
    assert ruler.surface is viewport.canvas.event_widget
    assert_tick_positions(window)
    revision = window.project.geometry.revision
    window.sketcher.move(window.sketcher.task.plane.world((3, 2)))
    assert viewport._sketch_timer.isActive()
    panel.cancel()
    qapp.processEvents()
    assert not ruler.isVisible() and not ruler.samples
    assert ruler.global_origin is None and ruler.global_axes == ()
    assert not viewport._sketch_timer.isActive()
    assert window.project.geometry.revision == revision
    window.notebook.select("Geometry")
    qapp.processEvents()
    enter_sketch(window, qapp)
    assert_tick_positions(window)
    window.panels["Construction"].cancel()
    window.notebook.select("Geometry")
    qapp.processEvents()
    enter_sketch(window, qapp, "sketch-yz")
    assert_tick_positions(window)
    window.panels["Construction"].cancel()


def test_parallel_and_behind_camera_rays_do_not_display_coordinates(window, qapp, monkeypatch):
    panel = enter_sketch(window, qapp)
    monkeypatch.setattr(window.viewport.canvas, "unproject_to_plane", lambda *args: None)
    window.viewport.canvas.redraw()
    qapp.processEvents()
    assert window.viewport._sketch_coordinates.samples == ()
    panel.cancel()


def test_failed_renderer_switch_keeps_coordinates_attached_to_surviving_canvas(window, qapp, monkeypatch):
    panel = enter_sketch(window, qapp)
    viewport = window.viewport
    original = viewport._render_scene

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("candidate renderer failed after drawing")

    with monkeypatch.context() as patch:
        patch.setattr(viewport, "_render_scene", fail)
        with pytest.raises(RuntimeError, match="candidate renderer failed"):
            viewport.switch_backend("software")
    qapp.processEvents()
    assert viewport._sketch_coordinates.surface is viewport.canvas.event_widget
    viewport.canvas.set_view(-65, 60)
    qapp.processEvents()
    assert_tick_positions(window)
    panel.cancel()


def test_coordinate_overlay_does_not_keep_idle_canvas_repainting(window, qapp):
    panel = enter_sketch(window, qapp)

    class PaintCounter(QObject):
        count = 0

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Paint:
                self.count += 1
            return False

    counter = PaintCounter(window)
    surface = window.viewport.canvas.event_widget
    surface.installEventFilter(counter)
    qapp.processEvents()
    counter.count = 0
    QTest.qWait(80)
    # A pending exposure is allowed. A paint/update feedback loop is not.
    assert counter.count <= 2
    surface.removeEventFilter(counter)
    panel.cancel()


def test_global_origin_is_actual_world_zero_and_axes_follow_camera(window,qapp,monkeypatch):
    panel=enter_sketch(window,qapp)
    viewport=window.viewport;ruler=viewport._sketch_coordinates
    ratio=viewport.canvas.event_widget.devicePixelRatioF()
    projected=viewport.project_point(viewport._point3d(0,0,0))
    assert ruler.global_origin==pytest.approx(np.asarray(projected[:2])/ratio)
    assert {name for name,_,_ in ruler.global_axes}=={'X','Y','Z'}
    before=ruler.global_axes
    viewport.canvas.set_view(-65,50);qapp.processEvents()
    assert ruler.global_axes!=before
    # Even when the real origin is beyond the canvas (or behind the camera),
    # the overlay remains alive and paints its explicitly qualified badge.
    real=viewport.project_point
    def outside(point):
        if (point.x,point.y,point.z)==(0,0,0):return (-10000.,-10000.,1.)
        return real(point)
    monkeypatch.setattr(viewport,'project_point',outside)
    ruler.sync();ruler.grab()
    assert ruler.global_origin==pytest.approx((-10000/ratio,-10000/ratio))
    assert ruler.isVisible() and ruler.global_axes
    monkeypatch.setattr(viewport,'project_point',lambda p:None)
    ruler.sync();ruler.grab()
    assert ruler.isVisible() and ruler.global_origin is None
    panel.cancel()
