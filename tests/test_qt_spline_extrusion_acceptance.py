"""Real sketch canvas clicks produce a curved extrusion, not a control polyline."""
import os
import numpy as np
import pytest

if os.environ.get('ANYFEM_RUN_QT_TESTS') != '1':
    pytest.skip('set ANYFEM_RUN_QT_TESTS=1', allow_module_level=True)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from test_qt_geometry_actions import qapp, window
from test_qt_sketcher_workflow import enter_sketch, click_uv
from test_spline_extrusion_acceptance import assert_curved_display


@pytest.mark.parametrize('kind', ['spline', 'nurbs'])
@pytest.mark.parametrize('count', [3, 4])
def test_draw_curve_finish_extruded_sketch_and_edit(window, qapp, tmp_path, kind, count):
    panel = enter_sketch(window, qapp)
    panel.closed.setChecked(False)
    panel.grid.setChecked(False)
    panel.objects.setChecked(False)
    panel.choose_tool(kind)
    coordinates = ((1, 1), (3, 2.5), (5, 1)) if count == 3 else ((1, 1), (2, 2.5), (4, 2), (5, 1))
    for uv in coordinates:
        click_uv(window, qapp, uv)
    panel.extrusion.setText('2 m')
    QTest.keyClick(window.viewport.canvas.event_widget, Qt.Key_Return)
    qapp.processEvents()
    assert window.viewport.construction_active
    assert window.sketcher.task.entities[0].kind == kind
    QTest.mouseClick(panel.buttons['Apply'], Qt.LeftButton)
    qapp.processEvents()
    assert not window.viewport.construction_active
    geometry = window.project.geometry
    assert len(geometry.faces) == 1
    face = next(iter(geometry.faces))
    assert_curved_display(geometry, face, np.array((0., 0., 2.)))
    feature = geometry.features.get(panel.last_sketch_feature_id)
    path = tmp_path / 'extruded-spline.anyfem'
    window.save_project(path=str(path))
    window.flush_project_writes()
    window.new_project()
    window.open_project(str(path))
    qapp.processEvents()
    panel = window.panels['Construction']
    assert panel.edit_sketch(feature.feature_id)
    panel.extrusion.setText('3 m')
    QTest.mouseClick(panel.buttons['Apply'], Qt.LeftButton)
    qapp.processEvents()
    assert not window.viewport.construction_active
    edited = window.project.geometry.features.get(feature.feature_id)
    ref = edited.outputs['extrusion/face/0']
    assert len(window.project.geometry.faces) == 1
    assert_curved_display(window.project.geometry, ref.id, np.array((0., 0., 3.)))
