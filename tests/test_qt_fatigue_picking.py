"""Real Results viewport clicks on a small shell; no meshing or solver execution."""

import os
from types import SimpleNamespace

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for Qt viewport checks", allow_module_level=True)

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog
from any3dview import SelectionFilter
from anygeometry.entities import EntityRef
from anymesher import Mesh

from anyfem.application.fatigue import FatigueMeshEdgeRef
from anyfem.mesh_jobs import clone_mesh_for_job
from anyfem.post.fields import Field
from anyfem.selection import MeshEntityRef, owner_to_ref
from anyfem.ui.qt.fatigue_picking import FatiguePicking
from anyfem.ui.scene import build_result_scene
from test_qt_result_browser import click_entry, qapp, retained, window


@pytest.fixture
def contour(window, qapp, retained):
    mesh = window.retained_result_mesh(window.active_job_id)
    mesh._anyfem_mesh_record_id = "submitted"
    mesh.node_of_vertex[71] = 1
    mesh.nodes_of_edge[81] = [1, 2]
    click_entry(window, qapp, ("field", "stress", "yy"))
    window.viewport.set_view("top")
    window.viewport.fit()
    qapp.processEvents()
    assert window.solution is None and window.mesh is None
    return mesh


def _pixel(window, position):
    view = window.viewport
    surface = view.canvas.event_widget
    projected = view.project_point(view._point3d(*position))
    ratio = surface.devicePixelRatioF()
    pixel = QPoint(round(projected[0] / ratio), round(projected[1] / ratio))
    assert surface.rect().contains(pixel)
    return pixel


def _click(window, position):
    QTest.mouseClick(window.viewport.canvas.event_widget, Qt.LeftButton, pos=_pixel(window, position))


def _center_pointer_area(window, qapp):
    # The Qt offscreen platform's desktop shrinks in logical pixels at high
    # DPI. QTest.mouseMove uses the system cursor, which cannot leave it.
    surface = window.viewport.canvas.event_widget
    center = surface.screen().availableGeometry().center()
    window.move(window.pos() + center - surface.mapToGlobal(surface.rect().center()))
    qapp.processEvents()


def _move(surface, pixel):
    # The widget overload warps the global cursor and can deliver a different
    # local position on fractional-DPI offscreen desktops. The window overload
    # delivers logical coordinates through Qt's normal widget event routing.
    window = surface.window()
    QTest.mouseMove(window.windowHandle(), surface.mapTo(window, pixel))


def test_actual_hover_shift_capture_and_reference_filter(window, qapp, contour):
    canvas = window.viewport.canvas
    surface = canvas.event_widget
    polygon = window.viewport._scene.faces[0].polygons[0]
    cad = EntityRef("edge", 81)
    segment = FatigueMeshEdgeRef((2, 3), "submitted")
    shell = MeshEntityRef("element", 1, "submitted")
    received, missed, cancelled = [], [], []

    def keep(ref, additive):
        received.append((ref, additive))
        picker.set_selected((*picker._selected, ref) if additive else (ref,))

    picker = FatiguePicking(window, lambda _: pytest.fail("legacy one-shot callback"),
                            lambda: cancelled.append(True), on_selection=keep, on_miss=missed.append)
    tracking = surface.hasMouseTracking()
    try:
        _center_pointer_area(window, qapp)
        picker.start("line", contour, element_ids=(1,), selected=(shell,))
        at_edge = _pixel(window, polygon[:2].mean(axis=0)) + QPoint(0, 8)
        _move(surface, at_edge)
        qapp.processEvents()
        assert picker._hover_ref == cad
        assert picker._highlights.testAttribute(Qt.WA_TransparentForMouseEvents)
        assert picker._selected == (shell,)
        index = picker._index
        QTest.mouseClick(surface, Qt.LeftButton, pos=at_edge)
        at_side = _pixel(window, polygon[1:3].mean(axis=0))
        _move(surface, at_side)
        assert picker._hover_ref == segment and picker._index is index
        QTest.mouseClick(surface, Qt.LeftButton, Qt.ShiftModifier, at_side)
        assert received == [(cad, False), (segment, True)] and picker.active
        assert picker._selected == (cad, segment)
        picker.clear_preview()
        QTest.mouseClick(surface, Qt.LeftButton, pos=_pixel(window, polygon.mean(axis=0)))
        assert missed and picker.active and len(received) == 2
        qapp.sendEvent(surface, QEvent(QEvent.Leave))
        assert picker._hover_ref is None and picker._selected == (cad, segment)
        picker.start("line", contour, element_ids=(1,), through_node=(1, 2), selected=(cad,))
        assert {ref for ref, _, _ in picker._targets} == {cad}
        QTest.mouseClick(surface, Qt.LeftButton, pos=at_side)
        assert len(missed) == 2 and picker.active
        QTest.keyClick(surface, Qt.Key_Escape)
        assert cancelled == [True] and picker._selected == (cad,) and not picker.active
        picker.set_selected((cad, shell, segment))
        assert len(picker._selected) == 3 and picker._highlights.isVisible()
        assert surface.hasMouseTracking() == tracking
        picker.clear()
        assert picker._highlights is None and picker._display is None
    finally:
        picker.clear()


def test_click_elements_points_and_shell_source_edges_on_retained_contour(window, contour):
    received, cancelled = [], []
    picker = FatiguePicking(window, received.append, lambda: cancelled.append(True))
    scene = window.viewport._scene
    polygon = scene.faces[0].polygons[0]
    window.selection.set_mode("face")
    original = (window.selection.filter, tuple(window.selection.items), scene.legend.copy())
    try:
        for kind, position, expected in (
            ("element", polygon.mean(axis=0), MeshEntityRef("element", 1, "submitted")),
            ("line", polygon[:2].mean(axis=0), EntityRef("edge", 81)),
            ("point", polygon[0], EntityRef("vertex", 71)),
            ("point", polygon[1], MeshEntityRef("node", 2, "submitted")),
        ):
            picker.start(kind, contour)
            _click(window, position)
            assert received[-1] == expected and not picker.active
        assert len(received) == 4 and not cancelled
        assert window._view_mode == "results" and window.viewport._scene is scene
        assert (window.selection.filter, tuple(window.selection.items), scene.legend) == original
    finally:
        picker.clear()


def test_cancel_and_changed_identity_never_apply_the_old_hit(window, qapp, contour):
    received, cancelled = [], []
    picker = FatiguePicking(window, received.append, lambda: cancelled.append(True))
    surface = window.viewport.canvas.event_widget
    position = window.viewport._scene.faces[0].polygons[0].mean(axis=0)
    try:
        picker.start("point", contour)
        QTest.keyClick(surface, Qt.Key_Escape)
        assert cancelled == [True] and not picker.active
        command = QDialog(window)
        command.show()
        command.activateWindow()
        qapp.processEvents()
        picker.start("point", contour)
        QTest.keyClick(command, Qt.Key_Escape)
        assert command.isVisible() and not picker.active and len(cancelled) == 2
        command.close()
        picker.cancel()
        picker.clear()
        assert len(cancelled) == 2
        copy = clone_mesh_for_job(contour)
        picker.start("element", copy)
        window.active_job_id = "other-job"
        _click(window, position)
        assert not received and picker._display is None and len(cancelled) == 2
        # Redrawing the old contour while a second job is loading is not a
        # publication by that job, even when both jobs use the same mesh.
        window.viewport.refresh_visibility()
        with pytest.raises(ValueError, match="Display a contour"):
            picker.start("element", contour)
        window.active_job_id = "synthetic"
        copy.nodes[1] = copy.nodes[1] + [0., 0., 2.]
        with pytest.raises(ValueError, match="different meshes"):
            picker.start("point", copy)
        assert picker._display is None
    finally:
        picker.clear()


def test_preview_uses_displayed_deformation_and_releases_only_its_own_graphics(window, contour):
    scene = window.viewport._scene
    origin = np.array([.5, 0., 0.])
    station = SimpleNamespace(xyz=origin, normal=(0., 0., 1.), direction=(0., 1., 0.),
                              probes=((.5, .2, 0.), (.5, .5, 0.)))
    picker = FatiguePicking(window, lambda ref: None, lambda: None)
    try:
        picker.preview(contour, [1], [station])
        handles = tuple(picker._preview_group.handles)
        assert len(handles) == 5
        rim, normal, direction, stations, probes = (h.mesh for h in handles)
        polygon = scene.faces[0].polygons[0]
        displayed_origin = (polygon[0] + polygon[1]) / 2
        np.testing.assert_allclose(stations.positions, [displayed_origin])
        np.testing.assert_allclose(probes.positions, np.array(station.probes) + displayed_origin - origin)
        np.testing.assert_allclose(normal.positions[0], displayed_origin)
        np.testing.assert_allclose(direction.positions[0], displayed_origin)
        assert len(rim.lines) == 4
        picker.start("line", contour)
        picker.cancel()
        assert all(not handle.removed for handle in handles)
        picker.clear()
        picker.clear()
        assert all(handle.removed for handle in handles)
        assert window.viewport._scene is scene and scene.result_mesh is contour
        with pytest.raises(ValueError, match="absent"):
            picker.preview(contour, [999], [station])
    finally:
        picker.clear()


@pytest.mark.parametrize("backend", ["software", "gpu", "webgpu"])
def test_live_result_picks_follow_its_deformed_shape(window, qapp, backend):
    if backend != "software":
        from any3dview.errors import GPUUnavailableError
        try:
            window.viewport.switch_backend(backend)
        except GPUUnavailableError as error:
            pytest.skip(str(error))
    nodes = {1: np.array([0., 0., 0.]), 2: np.array([1., 0., 0.]),
             3: np.array([1., 1., 0.]), 4: np.array([0., 1., 0.])}
    for i, (a, b) in enumerate(((1, 2), (2, 3), (3, 4), (4, 1)), start=5):
        nodes[i] = (nodes[a] + nodes[b]) / 2
    mesh = Mesh(nodes=nodes, quads={32: tuple(range(1, 9))}, elements_of_face={3: [32]},
                nodes_of_edge={6: [1, 5, 2]}, node_of_vertex={5: 1})
    mesh._anyfem_mesh_record_id = "linear"
    drawn = np.asarray([[3., 1., 0.], [5., 1., 0.], [5., 2., 1.], [3., 2., 1.]])
    drawn = np.vstack((drawn, (drawn + np.roll(drawn, -1, axis=0)) / 2))
    shape = SimpleNamespace(built=SimpleNamespace(mesh=mesh, project=window.project),
                            deformed_positions=lambda scale: drawn)
    field = Field("stress", "Pa", element_values={32: 15.})
    window.solutions["live"] = shape
    window.active_job_id = "live"
    window._view_mode = "results"
    window.viewport.show(build_result_scene(shape, values=field))
    window.viewport.set_view("top")
    window.viewport.fit()
    qapp.processEvents()
    if backend == "webgpu":
        assert window.viewport._retained_result is not None
    received = []
    picker = FatiguePicking(window, received.append, lambda: None)
    station = SimpleNamespace(xyz=(.5, 0., 0.), normal=(0., 0., 1.), direction=(0., 1., 0.),
                              probes=((.5, .25, 0.), (.5, .5, 0.)))
    try:
        picker.preview(mesh, [32], [station])
        _, normal, direction, origins, probes = [h.mesh for h in picker._preview_group.handles]
        np.testing.assert_allclose(origins.positions, [[4., 1., 0.]], atol=1e-12)
        np.testing.assert_allclose(probes.positions, [[4., 1.25, .25], [4., 1.5, .5]])
        np.testing.assert_allclose(np.diff(normal.positions[:2], axis=0)[0] @ (0., 1., 1.), 0., atol=1e-12)
        assert np.diff(direction.positions[:2], axis=0)[0] @ (0., 1., 1.) > 0
        picker.start("element", mesh)
        _click(window, drawn.mean(axis=0))
        picker.start("line", mesh)
        surface = window.viewport.canvas.event_widget
        at_weld = _pixel(window, drawn[:2].mean(axis=0)) + QPoint(0, 8)
        QTest.mouseMove(surface, QPoint(3, 3))
        QTest.mouseMove(surface, at_weld)
        qapp.processEvents()
        assert picker._hover_ref == EntityRef("edge", 6)
        center = _pixel(window, drawn[:2].mean(axis=0))
        highlight = surface.grab(QRect(center.x() - 7, center.y() - 7, 15, 15)).toImage()
        assert any(
            c.red() < 60 and c.green() > 200 and c.blue() > 220
            for y in range(highlight.height()) for x in range(highlight.width())
            if (c := highlight.pixelColor(x, y)).isValid()
        )
        _click(window, drawn[:2].mean(axis=0))
        assert received == [MeshEntityRef("element", 32, "linear"), EntityRef("edge", 6)]
        assert window.viewport._scene.result_field is field
        window.show_geometry()
        QTest.qWait(150)
        assert picker._display is None and picker._preview_group is None
    finally:
        picker.clear()


def test_governing_station_is_distinct_and_survives_overlay_redraw(window, contour):
    picker = FatiguePicking(window, lambda ref: None, lambda: None)
    canvas = window.viewport.canvas
    contour_revision = canvas._software_revision
    stations = [SimpleNamespace(xyz=(x, 0., 0.), normal=(0., 0., 1.),
                                direction=(0., 1., 0.), probes=()) for x in (0., 1.)]
    polygon = window.viewport._scene.faces[0].polygons[0]
    try:
        picker.preview(contour, [1], stations, governing_index=1)
        for draw in range(2):
            markers = [(mark.mesh.positions, mark.appearance) for mark in picker._preview_group.handles
                       if mark.mesh.point_indices is not None and len(mark.mesh.point_indices)]
            ordinary, governing = markers[-2:]
            np.testing.assert_allclose(ordinary[0], [polygon[0]])
            np.testing.assert_allclose(governing[0], [polygon[1]])
            assert governing[1]["point_size"] > ordinary[1]["point_size"]
            assert governing[1]["point_color"] != ordinary[1]["point_color"]
            assert governing[1]["point_outline"] and not governing[1]["pickable"]
            if draw == 0:
                picker._preview_group.handles[-1].remove()
                picker._refresh()
        assert canvas._software_revision == contour_revision
        with pytest.raises(ValueError, match="governing station index"):
            picker.preview(contour, [1], stations, governing_index=2)
        assert picker._preview_group is None
    finally:
        picker.clear()


def test_receiver_scope_accepts_weld_junction_and_rejects_occluded_node(window, qapp):
    # Shell 32 and inclined shell 33 share 2--3. Shell 34 is a separate
    # canopy hiding corner 1 of shell 32. Node 5 is visible on shell 33 only.
    coords = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
              (2, 0, .35), (2, 1, .35), (-.3, -.3, .5), (.3, -.3, .5),
              (.3, .3, .5), (-.3, .3, .5)]
    xyz = np.array(coords, dtype=float)
    mesh = Mesh(nodes=dict(enumerate(xyz, 1)),
                quads={32: (1, 2, 3, 4), 33: (2, 5, 6, 3), 34: (7, 8, 9, 10)},
                elements_of_face={1: [32], 2: [33], 3: [34]},
                nodes_of_edge={81: [2, 3]}, node_of_vertex={71: 2})
    mesh._anyfem_mesh_record_id = "junction"
    shape = SimpleNamespace(built=SimpleNamespace(mesh=mesh, project=window.project),
                            deformed_positions=lambda scale: xyz)
    field = Field("stress", "Pa", element_values={eid: 15. for eid in mesh.quads})
    window.solutions["junction"] = shape
    window.active_job_id = "junction"
    window._view_mode = "results"
    window.viewport.show(build_result_scene(shape, values=field))
    window.viewport.set_view("top")
    window.viewport.fit()
    qapp.processEvents()
    received, missed = [], []
    picker = FatiguePicking(window, None, lambda: None,
                            on_selection=lambda ref, add: received.append(ref), on_miss=missed.append)
    surface = window.viewport.canvas.event_widget
    try:
        picker.start("point", mesh, element_ids=(32,))
        for i, expected in ((4, None), (1, EntityRef("vertex", 71)), (0, None)):
            pos = _pixel(window, xyz[i])
            QTest.mouseMove(surface, pos)
            assert picker._hover_ref == expected
            QTest.mouseClick(surface, Qt.LeftButton, pos=pos)
        assert received == [EntityRef("vertex", 71)] and len(missed) == 2 and picker.active
        # Force the legitimate renderer tie-break to the other joined shell.
        # The receiver-side index still contains only shell 32.
        picker._front_shell = lambda x, y: 33
        QTest.mouseMove(surface, _pixel(window, xyz[1]))
        assert picker._hover_ref == EntityRef("vertex", 71)
        picker.start("line", mesh, element_ids=(32,))
        at_weld = _pixel(window, xyz[[1, 2]].mean(axis=0))
        QTest.mouseMove(surface, at_weld)
        assert picker._hover_ref == EntityRef("edge", 81)
    finally:
        picker.clear()


def test_software_culled_query_and_preview_match_displayed_section(window, qapp, contour):
    canvas = window.viewport.canvas
    polygon = window.viewport._scene.faces[0].polygons[0]
    picker = FatiguePicking(window, lambda ref: None, lambda: None)
    surface = canvas.event_widget
    station = SimpleNamespace(xyz=(0., 0., 0.), normal=(0., 0., 1.),
                              direction=(1., 0., 0.), probes=((.8, 0., 0.),))
    stations = [station, SimpleNamespace(xyz=(1., 0., 0.), normal=station.normal,
                                         direction=station.direction, probes=())]
    try:
        picker.start("line", contour, element_ids=(1,))
        picker.preview(contour, [1], stations, governing_index=1)
        for clip in ("none", "plane", "box"):
            middle = polygon.mean(axis=0)
            if clip == "plane":
                canvas.set_section_plane(normal=(1, 0, 0), offset=middle[0])
            if clip == "box":
                canvas.clear_section_plane()
                canvas.set_section_box((polygon[1, 0], middle[1], middle[2]), (.49, 2., 2.))
            picker._refresh()
            for weight in (.2, .8):
                world = polygon[0] * (1 - weight) + polygon[2] * weight
                pixel = canvas.project_point(world)
                xy = tuple(round(v) for v in pixel[:2])
                hits = canvas.query_point(*xy, selection_filter=SelectionFilter(kinds={"mesh.element"}), radius=0)
                canonical = next((owner_to_ref(h.owner).id for h in hits if h.visible), None)
                assert picker._front_shell(*xy) == canonical
                assert canonical == (1 if clip == "none" or weight > .5 else None)
            # The station at the clipped corner is absent from the painted
            # preview; the retained governing station is inside the section.
            surface.grab()
            img = picker._preview_group._image.toImage()
            a, b = (canvas.project_point(p) for p in polygon[:2])
            assert bool(img.pixelColor(round(a[0]), round(a[1])).alpha()) == (clip == "none")
            assert img.pixelColor(round(b[0]), round(b[1])).name() == "#97003c"
        assert picker._software_query is not None
    finally:
        picker.clear()
