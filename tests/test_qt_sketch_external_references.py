"""Real-widget route for fixed sketch references and their source links.

The PICK / SELECT / CONSTRAINT / DRAG interactions below are delivered as real
Qt mouse events on the viewer surface and real button clicks; helpers only
build the model and convert world positions to pixels.
"""
import os
import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from anygeometry.entities import EntityRef
from anyfem import commands as cmd
from anyfem.ui.qt.app import QtFemWindow

REF = "ref:r1"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    # Some Windows offscreen runtimes omit Qt's bundled fonts; use an installed one.
    from PySide6.QtGui import QFontDatabase, QFont
    from pathlib import Path
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
            app.setFont(QFont("Segoe UI", 9))
    return app


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from anyfem.io import recovery
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    window = QtFemWindow(viewer_backend=os.environ.get("ANYFEM_QT_TEST_BACKEND", "software"))
    window.show()
    qapp.processEvents()
    yield window
    window.session.mark_saved()
    window.close()
    qapp.processEvents()


# ------------------------------------------------------------------ helpers
def start_with_selected_vertex(window):
    vertex = window.run(cmd.AddPoint(0.25, 0.5, 0.0))
    window.selection.set_mode("vertex")
    window.selection.restore((EntityRef("vertex", vertex),))
    panel = window.panels["Construction"]
    panel.sketch_on_plane()
    return vertex, panel, panel.controller


def top_view(window, qapp):
    window.viewport.fit()
    window.panels["Construction"].view_plane()
    qapp.processEvents()


def surface_of(window):
    return window.viewport.canvas.event_widget


def pixel(window, world):
    surface = surface_of(window)
    projected = window.viewport.project_point(window.viewport._point3d(*[float(v) for v in world]))
    assert projected is not None, f"{world} does not project"
    ratio = surface.devicePixelRatioF()
    point = QPoint(round(projected[0] / ratio), round(projected[1] / ratio))
    assert surface.rect().contains(point), f"{world} is outside the viewer ({point})"
    return point


def click(window, qapp, world, modifier=Qt.NoModifier):
    QTest.mouseClick(surface_of(window), Qt.LeftButton, modifier, pos=pixel(window, world))
    qapp.processEvents()


def choose_tool(qapp, panel, key):
    QTest.mouseClick(panel.tool_buttons[key], Qt.LeftButton)
    qapp.processEvents()
    assert panel.controller.task.tool == key


def press_button(qapp, button):
    QTest.mouseClick(button, Qt.LeftButton)
    qapp.processEvents()


def draw_line(window, qapp, panel, start, end):
    choose_tool(qapp, panel, "line")
    click(window, qapp, start)
    click(window, qapp, end)
    QTest.keyClick(surface_of(window), Qt.Key_Escape)
    qapp.processEvents()


def state(window):
    geometry = window.project.geometry
    return (tuple(window.commands.history()), len(geometry.vertices), len(geometry.features.records),
            {key: tuple(np.asarray(geometry.vertex_position(key))) for key in geometry.vertices})


# -------------------------------------------------------------------- tests
def test_selected_geometry_becomes_fixed_reference_and_is_read_only(window):
    vertex, panel, controller = start_with_selected_vertex(window)
    task = controller.task
    assert [item.id for item in task.references] == ["r1"] and task.is_linked("r1")
    assert task.reference_points[REF] == pytest.approx((0.25, 0.5))
    assert task.move_point(REF, (1.0, 1.0, 0.0)) is False
    assert "fixed" in task.diagnostic.lower()
    assert panel.reference_list.count() == 1
    controller.cancel()


@pytest.mark.parametrize("action", ["move", "delete"])
def test_source_command_detaches_unsaved_working_copy_immediately(window, qapp, action):
    _, panel, controller = start_with_selected_vertex(window)
    task = controller.task
    before = dict(task.reference_points)
    source = next(record for record in window.project.geometry.features.records if record.kind == "geometry.point")
    if action == "move":
        window.run(cmd.EditFeature(source.feature_id, parameters={**source.parameters, "position": (3, 3, 0)}))
    else:
        window.run(cmd.DeleteFeature(source.feature_id))
    qapp.processEvents()
    assert not task.is_linked("r1")
    assert task.reference_points == before
    assert "detached" in panel.reference_list.item(0).text().lower()
    assert window.viewport.construction_active
    assert any(value["text"] == "r1 (detached)" for value in window.viewport.canvas._world_text)
    controller.cancel()


def test_pick_model_edge_with_a_different_normal_filter_keeps_that_filter(window, qapp):
    first = window.run(cmd.AddPoint(0.5, 0.5, -1.0))
    second = window.run(cmd.AddPoint(1.5, 1.5, 1.0))
    window.run(cmd.AddLine(first, second))
    window.selection.set_mode("face")
    window.selection.clear()
    mode, normal_filter = window.selection.mode, window.selection.filter
    panel = window.panels["Construction"]
    panel.sketch_on_plane()
    controller = panel.controller
    assert not controller.task.references
    choose_tool(qapp, panel, "reference")
    click(window, qapp, (1.0, 1.0, 0.0))  # the edge crosses z=0 here; the normal filter is "face"
    task = controller.task
    assert [item.id for item in task.references] == ["r1"]
    assert task.references[0].kind == "crossing"
    assert task.reference_points[task.references[0].keys[0]] == pytest.approx((1.0, 1.0))
    assert task.is_linked("r1")
    assert window.selection.mode == mode == "face"
    assert window.selection.filter == normal_filter
    controller.cancel()


def test_pick_failure_is_reported_not_silently_replaced_by_the_user_filter(window, qapp, monkeypatch):
    vertex, panel, controller = start_with_selected_vertex(window)
    choose_tool(qapp, panel, "reference")
    with monkeypatch.context() as patch:
        patch.setattr(window.viewport, "_selection_api", None)
        with pytest.raises(RuntimeError, match="selection filter"):
            window.viewport.pick_reference_source(10, 10)
    controller.cancel()


@pytest.mark.parametrize("kind", ["distance", "coincident"])
def test_reference_plus_sketch_point_distance_and_coincidence_through_the_ui(window, qapp, kind):
    vertex, panel, controller = start_with_selected_vertex(window)
    top_view(window, qapp)
    task = controller.task
    draw_line(window, qapp, panel, (1.0, 1.0, 0.0), (2.0, 1.0, 0.0))
    assert len(task.points) == 2
    first = task.point_keys[0]
    choose_tool(qapp, panel, "select")
    click(window, qapp, task.points[0])
    click(window, qapp, task.reference_world(REF), Qt.ShiftModifier)
    assert list(task.selected) == [first, REF]
    panel.distance.setText("2 m")
    press_button(qapp, panel.constraint_buttons[kind])
    assert any(c.kind == kind and {c.first, c.second} == {first, REF} for c in task.constraints)
    if kind == "distance":
        assert np.linalg.norm(np.asarray(task.key_world(first)) - task.key_world(REF)) == pytest.approx(2.0)
        # A positive distance and coincidence conflict. The UI must refuse the
        # second constraint without moving points or deleting the valid distance.
        before_constraints, before_points = tuple(task.constraints), tuple(task.points)
        click(window, qapp, task.points[0])
        click(window, qapp, task.reference_world(REF), Qt.ShiftModifier)
        press_button(qapp, panel.constraint_buttons["coincident"])
        assert tuple(task.constraints) == before_constraints
        assert tuple(task.points) == before_points
    else:
        assert np.allclose(task.key_world(first), task.key_world(REF))
    assert task.reference_points[REF] == pytest.approx((0.25, 0.5))
    controller.cancel()


def test_dragging_a_reference_leaves_its_anchor_fixed(window, qapp):
    vertex, panel, controller = start_with_selected_vertex(window)
    top_view(window, qapp)
    task = controller.task
    choose_tool(qapp, panel, "select")
    start = pixel(window, task.reference_world(REF))
    surface = surface_of(window)
    QTest.mousePress(surface, Qt.LeftButton, Qt.NoModifier, start)
    for step in (15, 30, 60):
        QTest.mouseMove(surface, start + QPoint(step, step))
        qapp.processEvents()
    QTest.mouseRelease(surface, Qt.LeftButton, Qt.NoModifier, start + QPoint(60, 60))
    qapp.processEvents()
    assert task.reference_points[REF] == pytest.approx((0.25, 0.5))
    assert "fixed" in task.diagnostic.lower()
    controller.cancel()


def test_clicking_a_reference_while_drawing_makes_an_ordinary_coincident_endpoint(window, qapp):
    vertex, panel, controller = start_with_selected_vertex(window)
    top_view(window, qapp)
    task = controller.task
    choose_tool(qapp, panel, "line")
    click(window, qapp, task.reference_world(REF))
    click(window, qapp, (2.0, 1.0, 0.0))
    assert len(task.entities) == 1
    assert not any("ref" in key for entity in task.entities for key in entity.points)
    assert any(c.kind == "coincident" and REF in (c.first, c.second) for c in task.constraints)
    assert np.allclose(task.points[0], (0.25, 0.5, 0.0))
    assert task.reference_points[REF] == pytest.approx((0.25, 0.5))
    controller.cancel()


def test_cancel_after_pick_and_drawing_leaves_live_geometry_unchanged(window, qapp):
    vertex, panel, controller = start_with_selected_vertex(window)
    top_view(window, qapp)
    before = state(window)
    draw_line(window, qapp, panel, (1.0, 1.0, 0.0), (2.0, 1.0, 0.0))
    choose_tool(qapp, panel, "select")
    click(window, qapp, controller.task.points[0])
    click(window, qapp, controller.task.reference_world(REF), Qt.ShiftModifier)
    press_button(qapp, panel.constraint_buttons["coincident"])
    press_button(qapp, panel.buttons["Cancel"])
    assert not controller.active
    assert state(window) == before


def test_finish_edit_save_reopen_keeps_fixed_snapshot_and_link_state(window, qapp, tmp_path):
    vertex, panel, controller = start_with_selected_vertex(window)
    top_view(window, qapp)
    task = controller.task
    draw_line(window, qapp, panel, (1.0, 1.0, 0.0), (2.0, 1.0, 0.0))
    choose_tool(qapp, panel, "select")
    click(window, qapp, task.points[0])
    click(window, qapp, task.reference_world(REF), Qt.ShiftModifier)
    press_button(qapp, panel.constraint_buttons["coincident"])
    press_button(qapp, panel.buttons["Apply"])
    assert not controller.active
    feature_id = window.project.geometry.features.records[-1].feature_id
    record = window.project.geometry.features.get(feature_id)
    assert record.inputs == {} and not any("ref" in key for key in record.outputs)
    assert len(record.parameters["reference_links"]) == 1

    path = tmp_path / "linked.anyfem"
    window.save_project(path=str(path))
    window.new_project()
    window.open_project(str(path))
    panel = window.panels["Construction"]
    assert panel.edit_sketch(feature_id)
    assert panel.controller.task.is_linked("r1")
    assert panel.controller.task.reference_points[REF] == pytest.approx((0.25, 0.5))
    panel.controller.cancel()

    source = next(r for r in window.project.geometry.features.records if r.kind == "geometry.point")
    window.run(cmd.EditFeature(source.feature_id, parameters={**source.parameters, "position": (3.0, 3.0, 0.0)}))
    record = window.project.geometry.features.get(feature_id)
    assert "reference_links" not in record.parameters and record.state == "ok"

    path = tmp_path / "detached.anyfem"
    window.save_project(path=str(path))
    window.new_project()
    window.open_project(str(path))
    panel = window.panels["Construction"]
    assert panel.edit_sketch(feature_id)
    assert not panel.controller.task.is_linked("r1")
    assert panel.controller.task.reference_points[REF] == pytest.approx((0.25, 0.5))
    panel.controller.cancel()


def test_planar_face_reference_draws_its_section_line_not_only_endpoints(window, qapp, monkeypatch):
    vertices = [window.run(cmd.AddPoint(x, y, z)) for x, y, z in
                ((0, -1, -1), (0, 1, -1), (0, 1, 1), (0, -1, 1))]
    face = window.run(cmd.AddPlate(vertices))
    panel = window.panels["Construction"]
    window.selection.clear()
    panel.sketch_on_plane()
    controller = panel.controller
    controller.add_references([EntityRef("face", face)])
    task = controller.task
    assert any(name.startswith("r1:") or name == "r1" for name in task.reference_curves)
    top_view(window,qapp)
    canvas = window.viewport.canvas
    lines = []
    real = canvas.add_line

    def record(*args, **kwargs):
        lines.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(canvas, "add_line", record)
    window.viewport._draw_sketch_references(task)
    section = [item for item in lines if item[1].get("layer") == 44 and item[1].get("width") == 3]
    assert 1 < len(section) <= 256
    assert {item[1]['color'] for item in section} <= {'#55dd77','#16752b'}
    # The section remains geometrically fixed, with actual gaps between spans.
    def xyz(p):return np.asarray((p.x,p.y,p.z),dtype=float)
    painted=sum(np.linalg.norm(xyz(args[1])-xyz(args[0])) for args,_ in section)
    assert 0 < painted < 2.0
    before=dict(task.reference_points)
    window.run(cmd.MoveEntities((EntityRef('face',face),),(1,0,0)))
    qapp.processEvents()
    lines.clear()
    window.viewport._draw_sketch_references(task)
    detached=[item for item in lines if item[1].get('layer')==44]
    assert detached and {item[1]['color'] for item in detached} <= {'#ffda44','#947000'}
    assert task.reference_points==before and not task.is_linked('r1')
    controller.cancel()


@pytest.mark.parametrize('action',['snap','dimension'])
@pytest.mark.parametrize('offset',[0.,5.])
def test_global_axis_anchor_through_real_canvas_interaction(window,qapp,tmp_path,action,offset):
    panel=window.panels['Construction'];window.selection.clear()
    if offset:
        from anyfem.model.coordinates import CoordinateSystem
        from anyfem.model.workplanes import Workplane
        from anyfem.sketcher import FaceSketchTask
        from anygeometry import plane_sketch_plane
        system=CoordinateSystem('Offset',origin=(0,0,offset))
        panel.begin(FaceSketchTask(plane_sketch_plane((0,0,offset),(0,0,1),(1,0,0)),.05),
                    Workplane(system.id),{system.id:system})
    else:panel.sketch_on_plane()
    controller=panel.controller;task=controller.task
    top_view(window,qapp)
    key=task.axis_references['global-axis-Z' if offset else 'global-axis-origin'].keys[0]
    assert any(item['text']==('Z axis ∩ plane' if offset else 'Global origin') for item in window.viewport.canvas._world_text)
    if action=='snap':
        choose_tool(qapp,panel,'line')
        # Click close to the marker, rather than exactly at a grid point.
        QTest.mouseClick(surface_of(window),Qt.LeftButton,Qt.NoModifier,
                         pixel(window,(0,0,offset))+QPoint(3,2))
        qapp.processEvents();click(window,qapp,(1,1,offset))
        assert np.allclose(task.points[0],(0,0,offset))
        assert any(c.kind=='coincident' and key in (c.first,c.second) for c in task.constraints)
    else:
        draw_line(window,qapp,panel,(1,1,offset),(2,1,offset))
        choose_tool(qapp,panel,'select')
        click(window,qapp,task.points[0]);click(window,qapp,(0,0,offset),Qt.ShiftModifier)
        assert key in task.selected
        panel.distance.setText('2 m');press_button(qapp,panel.constraint_buttons['distance'])
        assert any(c.kind=='distance' and key in (c.first,c.second) for c in task.constraints)
        assert np.linalg.norm(task.key_world(task.point_keys[0])-task.key_world(key))==pytest.approx(2.)
    assert task.reference_world(key)==pytest.approx((0,0,offset))
    press_button(qapp,panel.buttons['Apply'])
    feature=window.project.geometry.features.records[-1]
    assert not feature.inputs and not feature.parameters.get('reference_links')
    path=tmp_path/'axis-datum.anyfem';window.save_project(path=str(path))
    window.new_project();window.open_project(str(path))
    panel=window.panels['Construction'];assert panel.edit_sketch(feature.feature_id)
    assert panel.controller.task.reference_world(key)==pytest.approx((0,0,offset))
    assert any(key in (c.first,c.second) for c in panel.controller.task.constraints)
    panel.controller.cancel()
