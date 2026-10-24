"""Continue drawing and dragging with a closed curved shell in the scene."""
import os
import time
import numpy as np
import pytest

if os.environ.get('ANYFEM_RUN_QT_TESTS') != '1':
    pytest.skip('set ANYFEM_RUN_QT_TESTS=1', allow_module_level=True)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_qt_geometry_actions import qapp, window
from test_qt_sketcher_workflow import enter_sketch, click_uv, screen
from test_closed_spline_extrusion_acceptance import closed_shell, assert_display_follows_closed_profile


def test_draw_and_drag_keeps_existing_shell_retained(window, qapp, monkeypatch, record_property, tmp_path):
    closed_shell(window.project)
    window.show_geometry()
    qapp.processEvents()
    assert_display_follows_closed_profile(window.project)
    window.viewport.set_view('iso')
    window.viewport.fit()
    qapp.processEvents()
    image = window.viewport.capture_png(tmp_path / 'closed-shell.png')
    record_property('finished_shell_image', str(image))
    window.selection.set_mode('face')
    window.selection.restore(tuple(window.project.geometry.entity_ref('face', f) for f in window.project.geometry.faces))
    qapp.processEvents()
    selected = window.viewport.capture_png(tmp_path / 'closed-shell-selected.png')
    record_property('selected_shell_image', str(selected))
    window.selection.restore(())
    panel = enter_sketch(window, qapp)
    panel.closed.setChecked(False)
    panel.grid.setChecked(False)
    panel.objects.setChecked(False)
    click_uv(window, qapp, (1, 1))
    click_uv(window, qapp, (3, 1))
    panel.enter()
    panel.choose_tool('select')
    qapp.processEvents()
    task = window.sketcher.task
    widget = window.viewport.canvas.event_widget
    geometry = window.project.geometry
    revision = geometry.revision
    calls = []
    original = window.viewport.canvas.clear
    def clear(*args, **kwargs):
        calls.append('full clear')
        return original(*args, **kwargs)
    monkeypatch.setattr(window.viewport.canvas, 'clear', clear)
    started = time.perf_counter()
    for uv in ((2, 2), (2.5, 2), (3, 2), (3.5, 2)):
        QTest.mouseMove(widget, screen(window, task, uv), 1)
        QTest.qWait(20)
        qapp.processEvents()
    start = screen(window, task, (1, 1))
    end = screen(window, task, (2, 2))
    QTest.mousePress(widget, Qt.LeftButton, pos=start)
    QTest.mouseMove(widget, end, 1)
    QTest.mouseRelease(widget, Qt.LeftButton, pos=end)
    qapp.processEvents()
    record_property('hover_and_drag_seconds', time.perf_counter() - started)
    assert not calls, 'Pointer previews must not clear and resubmit the accepted shell'
    assert geometry.revision == revision
    assert len(task._undo) >= 2
    np.testing.assert_allclose(task.plane.local(task.points[0]), (2, 2), atol=.05)
    panel.cancel()
    qapp.processEvents()
    assert not window.viewport.construction_active
    assert_display_follows_closed_profile(window.project)
