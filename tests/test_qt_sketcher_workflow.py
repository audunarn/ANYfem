"""Exercise the sketch entry point, canvas input and persistent command path."""
import os
import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from anyfem import commands
from test_qt_geometry_actions import qapp, window


def enter_sketch(window, qapp, plane="global"):
    editor=window.panels["Geometry"]
    index=editor.choice.findData("AddSketch")
    assert index>=0
    QTest.mouseClick(editor.option_icons.buttons[index],Qt.LeftButton)
    qapp.processEvents()
    assert editor.choice.currentData()=="AddSketch" and editor.option_icons.isVisible()
    assert not editor.fields  # Drawing has replaced raw support/definition fields.
    editor.sketch_plane.setCurrentIndex(editor.sketch_plane.findData(plane))
    QTest.mouseClick(editor.apply,Qt.LeftButton);qapp.processEvents()
    assert window.details.tabText(window.details.currentIndex())=="Construction"
    assert window.viewport.construction_active
    assert not window._wordmark.isVisibleTo(window)
    return window.panels["Construction"]


@pytest.mark.parametrize("plane",["global","sketch-xz","sketch-yz"])
def test_sketch_icon_to_canvas_profile_and_undo(window,qapp,plane):
    panel=enter_sketch(window,qapp,plane)
    panel.sketch_name.setText("Bracket profile")
    assert not panel.constraints.isVisible() and not panel.plane_body.isVisible()
    assert not panel.system.isEnabled() and not panel.buttons["Start sketch"].isEnabled()
    assert not panel.buttons["Apply"].isEnabled()
    task=window.viewport.construction_task
    surface=window.viewport.canvas.event_widget
    coordinates=((1,1),(3,1),(3,3),(1,3))
    for point in coordinates:
        world=task.plane.world(point)
        x,y,_=window.viewport.canvas.project_point(world)
        ratio=surface.devicePixelRatioF()
        QTest.mouseClick(surface,Qt.LeftButton,pos=QPoint(round(x/ratio),round(y/ratio)))
        qapp.processEvents()
    assert len(task.points)==4 and panel.buttons["Apply"].isEnabled()
    for actual,local in zip(task.points,coordinates):
        np.testing.assert_allclose(actual,task.plane.world(local),atol=1e-9)
    QTest.keyClick(surface,Qt.Key_Return);qapp.processEvents()
    assert window.viewport.construction_active  # Enter completes the drawing tool first.
    QTest.keyClick(surface,Qt.Key_Return);qapp.processEvents()
    assert not window.viewport.construction_active and panel.system.isEnabled()
    record=window.project.geometry.features.records[-1]
    assert record.kind=="geometry.sketch.plane" and record.name=="Bracket profile"
    assert len(window.project.geometry.edges)==4
    assert len(window.selection.ordered_items)==4
    window.undo()
    assert not window.project.geometry.vertices and not window.project.geometry.edges


def test_open_profile_enter_cancel_and_restart(window,qapp):
    panel=enter_sketch(window,qapp)
    panel.closed.setChecked(False)
    for point in ("0, 0","2, 0"):
        panel.coordinates.setText(point);QTest.keyClick(panel.coordinates,Qt.Key_Return)
    QTest.keyClick(window.viewport.canvas.event_widget,Qt.Key_Return);qapp.processEvents()
    QTest.keyClick(window.viewport.canvas.event_widget,Qt.Key_Return);qapp.processEvents()
    assert len(window.project.geometry.edges)==1
    panel.buttons["Start sketch"].click();qapp.processEvents()
    panel.coordinates.setText("4, 4");QTest.keyClick(panel.coordinates,Qt.Key_Return)
    revision=window.project.geometry.revision
    QTest.keyClick(window.viewport.canvas.event_widget,Qt.Key_Escape);qapp.processEvents()
    assert window.viewport.construction_active and window.viewport.construction_task.tool=="select"
    panel.buttons["Cancel"].click();qapp.processEvents()
    assert not window.viewport.construction_active
    assert panel.buttons["Start sketch"].isEnabled() and not panel.buttons["Apply"].isEnabled()
    assert window.project.geometry.revision==revision
    assert "cancelled" in panel.summary.text()


def test_selected_face_entry_and_missing_face_refusal(window,qapp):
    editor=window.panels["Geometry"]
    editor.choice.setCurrentIndex(editor.choice.findData("AddSketch"))
    editor.sketch_support.setCurrentIndex(1)
    revision=window.project.geometry.revision
    with pytest.raises(ValueError,match="Select one flat plate"):
        editor.execute()
    assert not window.viewport.construction_active and window.project.geometry.revision==revision
    vertices=[window.run(commands.AddPoint(x,y,0)) for x,y in ((0,0),(3,0),(3,3),(0,3))]
    face=window.run(commands.AddPlate(vertices))
    window.selection.set_mode("face")
    window.selection.restore((window.project.geometry.entity_ref("face",face),))
    editor.execute();qapp.processEvents()
    panel=window.panels["Construction"]
    assert panel.support.id==face
    for point in ("0.5, 0.5","1.5, 0.5","1.5, 1.5"):
        panel.coordinates.setText(point);panel.add_point()
    panel.buttons["Apply"].click();qapp.processEvents()
    assert window.project.geometry.features.records[-1].kind=="geometry.sketch.extrude"


def test_custom_plane_save_reopen_edit_and_failed_finish(window,qapp,tmp_path):
    panel=window.panels["Construction"]
    window.notebook.select("Construction")
    panel.plane_name.setText("Inclined plane")
    panel.plane_origin.setText("1, 2, 3")
    panel.plane_normal.setText("0, 1, 1")
    panel.create_plane()
    panel.sketch_on_plane()
    for point in ("0, 0","2, 0","0, 2"):
        panel.coordinates.setText(point);panel.add_point()
    task=window.viewport.construction_task
    before=task.points
    panel.extrusion.setText("invalid")
    with pytest.raises(ValueError):panel.apply()
    assert task.points==before and not window.project.geometry.vertices
    panel.extrusion.setText("0");panel.apply()
    record=window.project.geometry.features.records[-1]
    path=tmp_path/"inclined-sketch.anyfem"
    window.save_project(path=str(path));window.flush_project_writes()
    window.new_project();window.open_project(str(path));qapp.processEvents()
    panel=window.panels["Construction"]
    assert panel.edit_sketch(record.feature_id)
    np.testing.assert_allclose(window.viewport.construction_task.points,before,atol=1e-9)
    panel.sketch_name.setText("Edited profile");panel.apply()
    edited=window.project.geometry.features.get(record.feature_id)
    assert edited.name=="Edited profile" and edited.parameters["plane"]==record.parameters["plane"]


def test_geometry_construction_plane_entry_preview_undo_and_sketch(window,qapp,tmp_path,monkeypatch):
    labels=[]
    add_text=window.viewport.canvas.add_text
    def record_text(point,text,**options):
        labels.append(text)
        return add_text(point,text,**options)
    monkeypatch.setattr(window.viewport.canvas,"add_text",record_text)
    editor=window.panels["Geometry"]
    index=editor.choice.findData("AddConstructionPlane")
    assert index>=0
    QTest.mouseClick(editor.option_icons.buttons[index],Qt.LeftButton);qapp.processEvents()
    editor.fields["name"][0].setText("Deck reference")
    editor.fields["origin"][0].setText("1 m, 2 m, 3 m")
    editor.fields["normal"][0].setText("0, 0, 2")
    editor.fields["x_direction"][0].setText("1, 0, 0")
    QTest.keyClick(editor.fields["origin"][0],Qt.Key_Return);qapp.processEvents()
    panel=window.panels["Construction"]
    identifier=panel.system.currentData()
    system=window.project.coordinate_systems[identifier]
    assert system.name=="Deck reference"
    np.testing.assert_allclose(system.origin,(1,2,3))
    np.testing.assert_allclose(system.axis,(0,0,1))
    assert not window.project.geometry.faces and not window.viewport.construction_active
    assert window.viewport._plane_preview[0].coordinate_system_id==identifier
    assert "Deck reference" in labels and "n" in labels
    editor.choice.setCurrentIndex(editor.choice.findData("AddSketch"))
    assert editor.sketch_plane.currentData()==identifier
    assert editor.sketch_support.currentData()=="plane"
    window.undo();qapp.processEvents()
    assert identifier not in window.project.coordinate_systems
    assert window.viewport._plane_preview is None
    window.redo();qapp.processEvents()
    assert identifier in window.project.coordinate_systems
    path=tmp_path/"construction-plane.anyfem"
    window.save_project(path=str(path));window.flush_project_writes()
    window.new_project();window.open_project(str(path));qapp.processEvents()
    assert window.project.coordinate_systems[identifier].name=="Deck reference"
    editor=window.panels["Geometry"]
    editor.choice.setCurrentIndex(editor.choice.findData("AddSketch"))
    editor.sketch_plane.setCurrentIndex(editor.sketch_plane.findData(identifier))
    editor.execute();qapp.processEvents()
    np.testing.assert_allclose(window.viewport.construction_task.plane.origin,(1,2,3))


@pytest.mark.parametrize("normal,x_direction",[("0,0,0","1,0,0"),("0,0,1","0,0,1"),("nan,0,1","1,0,0")])
def test_invalid_construction_plane_is_non_mutating(window,normal,x_direction):
    editor=window.panels["Geometry"]
    editor.choice.setCurrentIndex(editor.choice.findData("AddConstructionPlane"))
    editor.fields["normal"][0].setText(normal)
    editor.fields["x_direction"][0].setText(x_direction)
    history=window.commands.history()
    systems=dict(window.project.coordinate_systems)
    with pytest.raises(ValueError):editor.execute()
    assert window.commands.history()==history
    assert tuple(window.project.coordinate_systems)==tuple(systems)
    assert window.viewport._plane_preview is None


@pytest.mark.parametrize("key",[Qt.Key_Return,Qt.Key_Enter])
def test_construction_plane_canvas_enter_and_quick_selector(window,qapp,key):
    window.notebook.select("Construction")
    panel=window.panels["Construction"]
    panel.plane_body.section_toggle.setChecked(True)
    panel.plane_name.setText("Upper deck")
    panel.plane_origin.setText("0, 0, 4")
    surface=window.viewport.canvas.event_widget
    QTest.keyClick(surface,key);qapp.processEvents()
    identifier=panel.system.currentData()
    assert window.project.coordinate_systems[identifier].name=="Upper deck"
    assert len(window.project.coordinate_systems)==2
    assert not window.viewport.construction_active
    assert "plane" in window.quick_selection_actions
    window._refresh_plane_menu()
    choices={action.data():action for action in window.plane_menu.actions() if action.data()}
    assert {"global","sketch-xz","sketch-yz",identifier}.issubset(choices)
    choices["sketch-xz"].trigger();qapp.processEvents()
    assert panel.system.currentData()=="sketch-xz"
    assert window.viewport._plane_preview[0].coordinate_system_id=="sketch-xz"
    choices[identifier].trigger();qapp.processEvents()
    assert panel.system.currentData()==identifier
    QTest.keyClick(surface,key);qapp.processEvents()
    assert window.viewport.construction_active
    np.testing.assert_allclose(window.viewport.construction_task.plane.origin,(0,0,4))
    assert len(window.project.coordinate_systems)==2


def test_plane_creation_enter_rejects_modifiers_and_repeat(window,qapp):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    window.notebook.select("Construction")
    panel=window.panels["Construction"]
    panel.plane_body.section_toggle.setChecked(True)
    QTest.keyClick(panel.plane_normal,Qt.Key_Return,Qt.ControlModifier)
    QApplication.sendEvent(window.viewport.canvas.event_widget,
        QKeyEvent(QEvent.KeyPress,Qt.Key_Return,Qt.NoModifier,"\r",True,2))
    assert len(window.project.coordinate_systems)==1
    panel.plane_normal.setText("0,0,0")
    QTest.keyClick(window.viewport.canvas.event_widget,Qt.Key_Return);qapp.processEvents()
    assert len(window.project.coordinate_systems)==1 and not window.viewport.construction_active


def test_saved_sketch_and_plane_stay_in_tree_and_scene(window,qapp,tmp_path,monkeypatch):
    from PySide6.QtCore import QItemSelectionModel
    from anyfem.presentation.scene import build_geometry_scene
    labels=[]
    original=window.viewport.canvas.add_text
    def record(point,text,**options):
        labels.append(text)
        return original(point,text,**options)
    monkeypatch.setattr(window.viewport.canvas,"add_text",record)
    system=window.run(commands.AddConstructionPlane("Deck plane",origin=(0,0,2)))
    panel=window.panels["Construction"]
    panel.select_plane(system);panel.sketch_on_plane()
    panel.sketch_name.setText("Deck opening")
    for point in ("1,1","3,1","3,3","1,3"):
        panel.coordinates.setText(point);panel.add_point()
    panel.apply();qapp.processEvents()
    sketch=window.project.geometry.features.records[-1]
    assert sketch.feature_id in window.tree._rows["Sketches"]
    assert system.id in window.tree._rows["Construction planes"]
    assert window.tree.isExpanded(window.tree._groups["Sketches"].index())
    expected={ref for key,ref in sketch.outputs.items() if key.startswith("profile/edge/")}
    assert expected.issubset({line.ref for line in build_geometry_scene(window.project).lines})
    labels.clear();window.show_geometry();qapp.processEvents()
    assert "Deck plane" in labels  # The grid survives Finish sketch and redraw.
    item=window.tree._rows["Construction planes"][system.id][0]
    window.tree.selectionModel().select(item.index(),QItemSelectionModel.ClearAndSelect|QItemSelectionModel.Rows)
    labels.clear();window.set_selection_hidden(True);qapp.processEvents()
    assert ("plane",system.id) in window.viewport.visibility.hidden
    assert "Deck plane" not in labels
    labels.clear();window.set_selection_hidden(False);qapp.processEvents()
    assert "Deck plane" in labels
    item=window.tree._rows["Sketches"][sketch.feature_id][0]
    window.tree.selectionModel().select(item.index(),QItemSelectionModel.ClearAndSelect|QItemSelectionModel.Rows)
    assert sketch.feature_id in window.viewport._feature_ids
    window.undo();qapp.processEvents()
    assert sketch.feature_id not in window.tree._rows["Sketches"]
    assert system.id in window.tree._rows["Construction planes"]
    window.redo();qapp.processEvents()
    assert sketch.feature_id in window.tree._rows["Sketches"]
    path=tmp_path/"visible-sketch-and-plane.anyfem"
    window.save_project(path=str(path));window.flush_project_writes()
    window.new_project();window.open_project(str(path));qapp.processEvents()
    assert sketch.feature_id in window.tree._rows["Sketches"]
    assert system.id in window.tree._rows["Construction planes"]
    assert expected.issubset({line.ref for line in build_geometry_scene(window.project).lines})
    labels.clear();window.show_geometry();qapp.processEvents()
    assert "Deck plane" in labels


def screen(window,task,uv):
    surface=window.viewport.canvas.event_widget
    x,y,_=window.viewport.project_point(window.viewport._point3d(*task.plane.world(uv)))
    ratio=surface.devicePixelRatioF()
    return QPoint(round(x/ratio),round(y/ratio))


def click_uv(window,qapp,uv,modifier=Qt.NoModifier):
    task=window.viewport.construction_task
    QTest.mouseClick(window.viewport.canvas.event_widget,Qt.LeftButton,modifier,pos=screen(window,task,uv));qapp.processEvents()


@pytest.mark.parametrize('reverse', [False, True])
def test_finish_arc_and_chord_profile(window,qapp,tmp_path,reverse):
    panel=enter_sketch(window,qapp)
    panel.choose_tool('arc')
    for uv in ((1,2),(3,4),(4,1)):click_uv(window,qapp,uv)
    panel.choose_tool('line')
    endpoints=((1,2),(4,1)) if reverse else ((4,1),(1,2))
    for uv in endpoints:click_uv(window,qapp,uv)
    task=window.viewport.construction_task
    assert len(task.point_keys)==3
    assert len(task.wires)==1 and task.wires[0].closed
    QTest.mouseClick(panel.buttons['Apply'],Qt.LeftButton);qapp.processEvents()
    assert not window.viewport.construction_active
    feature=window.project.geometry.features.get(panel.last_sketch_feature_id)
    assert len(feature.parameters['entities'])==2
    path=tmp_path/'arc-and-chord.anyfem'
    window.save_project(path=str(path));window.new_project();window.open_project(str(path))
    assert window.panels['Construction'].edit_sketch(feature.feature_id)
    assert len(window.viewport.construction_task.entities)==2
    window.panels['Construction'].cancel()


def test_select_existing_shift_dimension_and_drag(window,qapp):
    panel=enter_sketch(window,qapp);task=window.viewport.construction_task
    click_uv(window,qapp,(1,1));click_uv(window,qapp,(3,1));click_uv(window,qapp,(3,3))
    panel.enter();assert task.tool=='select'
    click_uv(window,qapp,(1,1));click_uv(window,qapp,(3,1),Qt.ShiftModifier)
    assert len(task.points)==3 and len(task.selected)==2
    panel.distance.setText('3 m');QTest.keyClick(panel.distance,Qt.Key_Return);qapp.processEvents()
    assert len(task.constraints)==1 and task.constraints[0].kind=='distance'
    np.testing.assert_allclose(task.plane.local(task.points[0]),(1,1),atol=1e-8)
    assert np.linalg.norm(np.asarray(task.points[1])-task.points[0])==pytest.approx(3)
    task.select(task.point_keys[2]);surface=window.viewport.canvas.event_widget
    start=screen(window,task,(3,3));end=screen(window,task,(4,4));before=task.points[2];undo_count=len(task._undo)
    QTest.mousePress(surface,Qt.LeftButton,pos=start);QTest.mouseMove(surface,end,30);QTest.mouseRelease(surface,Qt.LeftButton,pos=end);qapp.processEvents()
    assert task.points[2]!=before and len(task._undo)==undo_count+1
    panel.local_undo();np.testing.assert_allclose(task.points[2],before)
    panel.local_undo(True);assert task.points[2]!=before
    panel.cancel()


def test_click_threshold_reuse_line_and_fixed_drag(window,qapp):
    panel=enter_sketch(window,qapp);task=window.viewport.construction_task
    panel.choose_tool('line');click_uv(window,qapp,(1,1));click_uv(window,qapp,(3,1))
    assert len(task.entities)==1
    click_uv(window,qapp,(3,1));click_uv(window,qapp,(3,3))
    assert len(task.points)==3 and len(task.entities)==2
    panel.choose_tool('select');click_uv(window,qapp,(1,1));panel.add_constraint('fixed')
    surface=window.viewport.canvas.event_widget;start=screen(window,task,(1,1));end=screen(window,task,(2,2));before=task.points[0]
    QTest.mousePress(surface,Qt.LeftButton,pos=start);QTest.mouseMove(surface,end,30);QTest.mouseRelease(surface,Qt.LeftButton,pos=end);qapp.processEvents()
    np.testing.assert_allclose(task.points[0],before)
    assert len(task.points)==3
    panel.cancel()


@pytest.mark.parametrize('tool,points,count',[('arc',[(1,1),(2,2),(3,1)],1),('circle',[(1,1),(2,1)],1),('spline',[(0,0),(1,1),(2,0),(3,1)],1)])
def test_curve_tools_finish_save_reopen(window,qapp,tmp_path,tool,points,count):
    panel=enter_sketch(window,qapp);task=window.viewport.construction_task
    panel.closed.setChecked(False);panel.choose_tool(tool)
    for uv in points:click_uv(window,qapp,uv)
    if task.pending:panel.enter()
    assert len(task.entities)==count and task.preview_segments
    panel.apply();feature=window.project.geometry.features.get(panel.last_sketch_feature_id)
    assert feature.parameters['version']==2
    path=tmp_path/f'{tool}.anyfem';window.save_project(path=str(path));window.new_project();window.open_project(str(path))
    panel=window.panels['Construction'];assert panel.edit_sketch(feature.feature_id)
    assert window.viewport.construction_task.tool=='select'
    assert window.viewport.construction_task.entities[0].id==feature.parameters['entities'][0]['id']
    panel.cancel()


def test_tool_escape_preserves_sketch_and_radius(window,qapp):
    panel=enter_sketch(window,qapp);task=window.viewport.construction_task
    panel.choose_tool('circle');click_uv(window,qapp,(1,1));click_uv(window,qapp,(2,1))
    task.selected_entities[:]=[task.entities[0].id];panel.radius.setText('2 m');panel.set_radius()
    from anygeometry import sketch_entity_radius
    assert sketch_entity_radius(task.definition(0),task.entities[0])==pytest.approx(2)
    panel.choose_tool('line');click_uv(window,qapp,(4,4));count=len(task.entities)
    QTest.keyClick(window.viewport.canvas.event_widget,Qt.Key_Escape);qapp.processEvents()
    assert task.tool=='select' and not task.pending and len(task.entities)==count
    assert len(task.points)==2
    panel.cancel()


def test_control_nurbs_advanced_and_fillet(window,qapp):
    panel=enter_sketch(window,qapp);task=window.viewport.construction_task
    panel.closed.setChecked(False);panel.spline_kind.setCurrentIndex(1);panel.spline_weights.setText('1, 2, 1, 1');panel.choose_tool('spline')
    for uv in ((0,0),(1,1),(2,1),(3,0)):click_uv(window,qapp,uv)
    panel.enter();assert task.entities[0].kind=='nurbs' and task.entities[0].weights==(1,2,1,1)
    assert task.preview_segments
    panel.choose_tool('connected')
    for uv in ((4,0),(5,0),(5,2)):click_uv(window,qapp,uv)
    panel.enter();lines=[e for e in task.entities if e.kind=='line'];task.selected_entities[:]=[e.id for e in lines]
    panel.radius.setText('0.25 m');panel.set_radius()
    assert any(e.kind=='fillet' for e in task.entities) and task.preview_segments
    panel.local_undo();assert all(e.kind!='fillet' for e in task.entities)
    panel.cancel()


def test_dimension_label_pick_edit_and_subthreshold_click(window,qapp):
    qapp.setActiveWindow(window);qapp.processEvents()
    panel=enter_sketch(window,qapp);task=window.viewport.construction_task
    panel.choose_tool('line');click_uv(window,qapp,(1,1));click_uv(window,qapp,(3,1));panel.choose_tool('select')
    click_uv(window,qapp,(1,1));click_uv(window,qapp,(3,1),Qt.ShiftModifier)
    panel.distance.setText('2 m');panel.add_constraint('distance')
    task.select();qapp.setActiveWindow(window);click_uv(window,qapp,(2,1));QTest.qWait(20);qapp.processEvents();assert len(task.selected)==2 and panel.distance.hasFocus()
    QTest.keyClicks(qapp.focusWidget(),'4 m');QTest.keyClick(qapp.focusWidget(),Qt.Key_Return);qapp.processEvents()
    assert len(task.constraints)==1 and task.constraints[0].value==4
    task.select();surface=window.viewport.canvas.event_widget;start=screen(window,task,(1,1));before=task.points
    QTest.mousePress(surface,Qt.LeftButton,pos=start);QTest.mouseMove(surface,start+QPoint(1,1),10);QTest.mouseRelease(surface,Qt.LeftButton,pos=start+QPoint(1,1));qapp.processEvents()
    assert task.points==before and len(task.points)==2
    panel.cancel()


@pytest.mark.parametrize('tool,points',[('circle',((1,1),(2,1))),('arc',((1,1),(2,2),(3,1)))])
def test_radius_label_pick_type_enter_and_persist(window,qapp,tmp_path,tool,points):
    from anygeometry import sketch_entity_radius
    qapp.setActiveWindow(window);qapp.processEvents()
    panel=enter_sketch(window,qapp);task=window.viewport.construction_task
    panel.closed.setChecked(False);panel.choose_tool(tool)
    for point in points:click_uv(window,qapp,point)
    panel.choose_tool('select');task.select();entity=task.entities[0]
    world=window.viewport.sketch_radius_label_position(entity)
    x,y,_=window.viewport.project_point(window.viewport._point3d(*world));surface=window.viewport.canvas.event_widget;ratio=surface.devicePixelRatioF()
    qapp.setActiveWindow(window)
    QTest.mouseClick(surface,Qt.LeftButton,pos=QPoint(round(x/ratio),round(y/ratio)));QTest.qWait(20);qapp.processEvents()
    assert task.selected_entities==[entity.id] and panel.radius.hasFocus()
    QTest.keyClicks(qapp.focusWidget(),'2 m');QTest.keyClick(qapp.focusWidget(),Qt.Key_Return);qapp.processEvents()
    entity=task.entities[0];assert sketch_entity_radius(task.definition(0),entity)==pytest.approx(2)
    assert entity.radius==2
    panel.apply();feature_id=panel.last_sketch_feature_id
    path=tmp_path/f'radius-{tool}.anyfem';window.save_project(path=str(path));window.flush_project_writes();window.new_project();window.open_project(str(path))
    panel=window.panels['Construction'];panel.edit_sketch(feature_id);task=window.viewport.construction_task
    assert task.entities[0].radius==2 and sketch_entity_radius(task.definition(0),task.entities[0])==pytest.approx(2)
    key=task.entities[0].points[-1];task.begin_drag();task.move_point(key,task.plane.world((4,4)));task.end_drag()
    assert sketch_entity_radius(task.definition(0),task.entities[0])==pytest.approx(2,abs=1e-7)
    panel.cancel()
