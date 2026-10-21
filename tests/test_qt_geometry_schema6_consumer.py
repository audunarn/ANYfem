"""Visible Qt consumer checks against the exact owner-supplied documents."""
from __future__ import annotations

import os
import time

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets", allow_module_level=True)
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from anyfem.ui.qt.app import QtFemWindow
from test_geometry_schema6_consumer import MANIFEST, encoded, owner_project


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def settle(qapp, seconds=0.2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def pick_rendered_entity(window, qapp, kind, items, joints):
    """Pick a representative owner entity; expose hidden joints by rotating."""
    canvas = window.viewport.canvas
    for view in (None, (135, 35), (225, 35)):
        if view is not None:
            canvas.set_view(*view)
            settle(qapp)
        for item in items:
            if kind == "edge" and joints and item.ref.id not in joints:
                continue
            points = item.points if kind == "edge" else item.polygons[0]
            locations = ([points[len(points) // 4], points[len(points) // 2],
                          points[3 * len(points) // 4]] if kind == "edge"
                         else [np.asarray(points).mean(axis=0)])
            for location in locations:
                screen = window.viewport.project_point(location)
                if screen is None:
                    continue
                x, y = round(screen[0]), round(screen[1])
                hits = canvas.query_point(x, y)
                if item.tag not in {hit.key for hit in hits}:
                    continue
                ratio = canvas.event_widget.devicePixelRatioF()
                # A tangent joint can coincide with an existing generator edge.
                # Repeated clicks use the viewer's public candidate cycling.
                for _ in range(len(hits) + 1):
                    QTest.mouseClick(canvas.event_widget, Qt.LeftButton, Qt.NoModifier,
                                     QPoint(round(x / ratio), round(y / ratio)))
                    settle(qapp, 0.03)
                    if item.ref in window.selection.items:
                        break
                else:
                    continue
                assert item.tag in canvas.highlighted_tags()
                window.tree.search(str(item.ref.id))
                assert any(index.data(Qt.UserRole) == item.ref
                           for index in window.tree.selectionModel().selectedRows())
                return item.ref
    return None


@pytest.mark.parametrize("name", MANIFEST)
def test_schema6_visible_qt_pick_highlight_and_reopen(name, qapp, tmp_path, monkeypatch):
    from anyfem.io import recovery

    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    project, document = owner_project(name, "prepared")
    window = QtFemWindow(project, viewer_backend="auto")
    try:
        window.show()
        settle(qapp)
        expected = os.environ.get("ANYFEM_QT_TEST_BACKEND", "software")
        assert window.viewport.active_backend == expected
        for backend in dict.fromkeys((expected, "software")):
            window.switch_viewer_backend(backend)
            window.viewport.fit()
            settle(qapp)
            assert window.viewport.active_backend == backend
            canvas = window.viewport.canvas
            image = canvas.event_widget.grab().toImage().scaled(100, 100)
            colors = {image.pixelColor(x, y).rgb() for x in range(100) for y in range(100)}
            assert len(colors) > 10, "first visible frame must contain rendered geometry"
            for kind, items in (("edge", window.viewport._scene.lines),
                                ("face", window.viewport._scene.faces)):
                window.selection_mode.set_value(kind)
                window.selection_tool.set_value("single")
                settle(qapp)
                joints = set(MANIFEST[name]["joint_edge_ids"])
                picked = pick_rendered_entity(window, qapp, kind, items, joints)
                assert picked is not None, f"no visible {kind} pick for {name}/{backend}"
            window.viewport.capture_png(tmp_path / f"{name}-{backend}.png")
            assert encoded(project.geometry) == document
        saved = tmp_path / f"{name}.anyfem"
        window.save_project(path=str(saved))
        assert saved.is_file()
        window.new_project()
        window.open_project(path=str(saved))
        settle(qapp)
        assert encoded(window.project.geometry) == document
        assert window.viewport._scene.faces and window.viewport._scene.lines
    finally:
        window.session.mark_saved()
        window.close()
        qapp.processEvents()
