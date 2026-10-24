"""Real widget routes share one toolkit-neutral sketch controller."""
import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('set ANYFEM_RUN_QT_TESTS=1',allow_module_level=True)
from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from test_qt_geometry_actions import qapp,window
from test_qt_sketcher_workflow import enter_sketch,click_uv,screen
from anyfem.sketcher import FaceSketchTask

def test_single_controller_panel_canvas_and_compatibility(window,qapp,monkeypatch):
    from anyfem.geometry.sketching import FaceSketchTask as Old
    assert Old is FaceSketchTask
    panel=enter_sketch(window,qapp);controller=window.sketcher
    assert panel.controller is controller is window.viewport.sketcher
    assert controller.task is window.viewport.construction_task
    calls=[];original=controller.point
    def point(*args,**kwargs):calls.append('point');return original(*args,**kwargs)
    monkeypatch.setattr(controller,'point',point)
    click_uv(window,qapp,(1,1))
    panel.coordinates.setText('3, 1');QTest.keyClick(panel.coordinates,Qt.Key_Return);qapp.processEvents()
    assert calls==['point','point']
    panel.enter();assert controller.task.tool=='select'
    assert controller.session.support is panel.support
    panel.cancel();assert not controller.active and not window.viewport.construction_active


def test_hover_uses_preview_without_panel_rebuild_and_drag_flush(window,qapp,monkeypatch):
    panel=enter_sketch(window,qapp);controller=window.sketcher;task=controller.task
    click_uv(window,qapp,(1,1));click_uv(window,qapp,(3,1))
    changed=[];original=panel.updated
    def updated(*args):changed.append(1);return original(*args)
    monkeypatch.setattr(panel,'updated',updated)
    surface=window.viewport.canvas.event_widget
    for uv in ((2,2),(2.5,2),(3,2)):QTest.mouseMove(surface,screen(window,task,uv),1)
    assert not changed
    panel.enter();panel.choose_tool('select');before=len(task._undo)
    start=screen(window,task,(1,1));end=screen(window,task,(2,2))
    QTest.mousePress(surface,Qt.LeftButton,pos=start);QTest.mouseMove(surface,end,1);QTest.mouseRelease(surface,Qt.LeftButton,pos=end);qapp.processEvents()
    assert not controller.session.dragging and len(task._undo)==before+1
    ratio=surface.devicePixelRatioF()
    final=window.viewport.project_to_workplane(end.x()*ratio,end.y()*ratio,window.viewport._construction_workplane,window.viewport._construction_coordinate_systems)
    assert task.points[0]==pytest.approx(tuple(final),abs=1e-10)
    panel.cancel()


def test_overlay_batches_and_cancel_discards_scheduled_preview(window,qapp,monkeypatch):
    panel=enter_sketch(window,qapp);task=window.sketcher.task
    click_uv(window,qapp,(1,1));click_uv(window,qapp,(3,1));click_uv(window,qapp,(3,3))
    batches=[];canvas=window.viewport.canvas;original=canvas.add_mesh_arrays
    def add(mesh,**options):
        if options.get('layer') in (40,45):batches.append((options['layer'],len(mesh.lines)))
        return original(mesh,**options)
    monkeypatch.setattr(canvas,'add_mesh_arrays',add)
    window.viewport.refresh_construction_overlay()
    assert len([b for b in batches if b[0]==40])==1
    assert len([b for b in batches if b[0]==45])==1
    assert sum(count for layer,count in batches if layer==40)>2
    window.sketcher.move(task.plane.world((2,4)))
    assert window.viewport._sketch_timer.isActive()
    revision=window.project.geometry.revision
    panel.cancel();assert not window.viewport._sketch_timer.isActive()
    QTest.qWait(30);qapp.processEvents()
    assert not window.sketcher.active and not window.viewport.construction_active
    assert window.project.geometry.revision==revision


@pytest.mark.parametrize('mode,coordinates',[('point',((1,1),)),('line',((1,1),(3,1))),('polyline',((1,1),(3,1),(3,2)))])
def test_generic_construction_canvas_path_remains_independent(window,qapp,mode,coordinates):
    window.notebook.select('Construction');panel=window.panels['Construction']
    panel.mode.setCurrentIndex(panel.mode.findData(mode));panel.close_polyline.setChecked(False);panel.start()
    assert not window.sketcher.active
    surface=window.viewport.canvas.event_widget;ratio=surface.devicePixelRatioF()
    frame=window.viewport._construction_workplane.resolve(window.viewport._construction_coordinate_systems)
    for uv in coordinates:
        world=frame.world_position(uv);x,y,_=window.viewport.canvas.project_point(world)
        QTest.mouseClick(surface,Qt.LeftButton,pos=QPoint(round(x/ratio),round(y/ratio)));qapp.processEvents()
    assert len(window.viewport.construction_task.points)==len(coordinates)
    panel.apply();assert not window.viewport.construction_active
    assert len(window.project.geometry.vertices)==len(coordinates)
