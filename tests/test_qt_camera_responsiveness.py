"""Camera detail is temporary and never becomes model or selection truth."""
import os
import numpy as np
import pytest
from anygeometry.entities import EntityRef
from anyfem.ui.scene import Scene, FacePatch, Polyline
from anyfem.presentation.interaction_scene import camera_scene


def dense_scene():
    polygon = np.array([[0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [0., 1., 0.]])
    return Scene(faces=[FacePatch(EntityRef("face", 1), [polygon] * 600, ["#93b7c9"] * 600,
        polygon_owners=[(EntityRef("face", 1),)] * 600),
        FacePatch(EntityRef("edge", 2), [polygon] * 600, ["#c1663a"] * 600)],
        lines=[Polyline(EntityRef("edge", 2), polygon[:2])])


def test_camera_scene_is_bounded_and_preserves_sample_owners():
    full = dense_scene()
    reduced = camera_scene(full)
    assert sum(len(face.polygons) for face in reduced.faces) <= 160
    assert len(full.faces[0].polygons) == len(full.faces[1].polygons) == 600
    assert [face.ref for face in reduced.faces] == [EntityRef("face", 1)]
    assert reduced.faces[0].polygon_owners == [(EntityRef("face", 1),)] * 160
    assert reduced.lines[0] is full.lines[0]
    assert all(any(polygon is original for original in full.faces[0].polygons)
               for polygon in reduced.faces[0].polygons)


@pytest.fixture
def viewport(tmp_path, monkeypatch):
    if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
        pytest.skip("opt-in Qt widgets")
    from PySide6.QtWidgets import QApplication
    from anyfem.ui.qt.app import QtFemWindow
    from anyfem.io import recovery
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    app = QApplication.instance() or QApplication([])
    window = QtFemWindow(viewer_backend="software")
    window.show()
    window.viewport.show(dense_scene(), reset_view=True)
    app.processEvents()
    yield window.viewport, app
    window.session.mark_saved()
    window.close()
    app.processEvents()


def send(surface, kind, button, buttons, position=(40., 40.)):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    event = QMouseEvent(kind, QPointF(*position), QPointF(*position), button, buttons, Qt.NoModifier)
    QApplication.sendEvent(surface, event)


def test_camera_drag_reduces_detail_and_release_restores_full_owner_scene(viewport):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtTest import QTest
    view, app = viewport
    full = view._scene
    surface = view.canvas.event_widget
    before = view.canvas.export_view_state().camera_position
    send(surface, QEvent.MouseButtonPress, Qt.RightButton, Qt.RightButton)
    send(surface, QEvent.MouseMove, Qt.NoButton, Qt.RightButton, (100., 50.))
    assert view._interaction_scene_active
    assert view._scene is full
    assert view.canvas.export_view_state().camera_position != before
    send(surface, QEvent.MouseButtonRelease, Qt.RightButton, Qt.NoButton, (100., 50.))
    import time
    deadline = time.monotonic() + 2
    while view._interaction_scene_active and time.monotonic() < deadline:
        QTest.qWait(20)
    assert not view._interaction_scene_active
    assert view._scene is full
    assert len(view._scene.faces[1].polygons) == 600


def test_gpu_orbit_keeps_drawing_the_complete_scene(tmp_path, monkeypatch):
    if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
        pytest.skip("opt-in Qt widgets")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtWidgets import QApplication
    from anyfem.ui.qt.app import QtFemWindow
    from anyfem.io import recovery
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    app = QApplication.instance() or QApplication([])
    window = QtFemWindow(viewer_backend="gpu")
    try:
        window.show()
        view = window.viewport
        if view.active_backend != "gpu":
            pytest.skip("no OpenGL 3.3 context")
        view.show(dense_scene(), reset_view=True)
        app.processEvents()
        surface = view.canvas.event_widget
        send(surface, QEvent.MouseButtonPress, Qt.RightButton, Qt.RightButton)
        send(surface, QEvent.MouseMove, Qt.NoButton, Qt.RightButton, (100., 50.))
        assert not view._interaction_scene_active
        assert len(view._scene.faces[1].polygons) == 600
        send(surface, QEvent.MouseButtonRelease, Qt.RightButton, Qt.NoButton, (100., 50.))
    finally:
        window.session.mark_saved()
        window.close()
        app.processEvents()


def test_selection_press_restores_full_detail_before_picking(viewport):
    from PySide6.QtCore import QEvent, Qt
    view, app = viewport
    view._begin_interaction_scene()
    assert view._interaction_scene_active
    send(view.canvas.event_widget, QEvent.MouseButtonPress, Qt.LeftButton, Qt.LeftButton)
    assert not view._interaction_scene_active
    send(view.canvas.event_widget, QEvent.MouseButtonRelease, Qt.LeftButton, Qt.NoButton)


def test_backend_switch_during_motion_cancels_restore_and_keeps_complete_scene(viewport):
    view, app = viewport
    full = view._scene
    view._begin_interaction_scene()
    view._schedule_full_scene()
    view.switch_backend("software")
    assert view._scene is full and not view._interaction_scene_active
    assert view._interaction_restore_after_id is None


def test_hidden_owners_stay_hidden_in_both_camera_scenes(viewport):
    view, app = viewport
    view.visibility.set_hidden([EntityRef("face", 1)])
    view.refresh_visibility()
    assert all(face.ref != EntityRef("face", 1) for face in view._scene.faces)
    assert all(face.ref != EntityRef("face", 1) for face in view._interaction_scene.faces)
    view._begin_interaction_scene()
    view._restore_full_scene()
    assert all(face.ref != EntityRef("face", 1) for face in view._scene.faces)


def test_failed_backend_creation_during_motion_retains_full_picking(viewport, monkeypatch):
    import anyfem.ui.qt.viewport as adapter
    view, app = viewport
    canvas = view.canvas
    view._begin_interaction_scene()

    def fail(*args, **kwargs):
        raise RuntimeError("GPU unavailable")

    monkeypatch.setattr(adapter, "create_viewer", fail)
    with pytest.raises(RuntimeError, match="GPU unavailable"):
        view.switch_backend("gpu")
    assert view.canvas is canvas
    assert not view._interaction_scene_active
    assert view._interaction_restore_after_id is None
