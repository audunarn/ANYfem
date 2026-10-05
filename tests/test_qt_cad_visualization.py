"""Appearance controls exercise actual CAD drawing and cache refresh."""
import os
import pytest
if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("opt-in Qt", allow_module_level=True)
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from anyfem import commands as cmd
from anyfem.ui.qt.app import QtFemWindow
from anygeometry.entities import EntityRef


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp,tmp_path,monkeypatch):
    from anyfem.io import recovery
    monkeypatch.setattr(recovery,"default_recovery_root",lambda:tmp_path/"recovery")
    window=QtFemWindow(viewer_backend="software")
    vertices=[window.run(cmd.AddPoint(x,y)) for x,y in ((0,0),(1,0),(1,1),(0,1))]
    window.run(cmd.AddPlate(vertices));window.show_geometry(reset_view=True)
    window.show();qapp.processEvents()
    yield window
    window.session.mark_saved();window.close();qapp.processEvents()


def test_render_mode_and_model_lines_are_independent_and_live(window,monkeypatch):
    panel=window.panels["Visualization"];calls=[]
    original=window.viewport._draw_line_batches
    monkeypatch.setattr(window.viewport,"_draw_line_batches",lambda lines:(calls.append(len(lines)),original(lines))[-1])
    panel.render.setCurrentText("Shaded")
    assert window.viewport.visualization.render_mode=="Shaded"
    assert window.viewport._face_style(window.viewport._scene.faces[0])[0]==""
    assert calls[-1]>0
    calls.clear();panel.appearance["show_model_lines"].setChecked(False)
    assert calls==[] and not window.viewport.visualization.show_model_lines
    panel.render.setCurrentText("Shaded with edges")
    assert window.viewport._face_style(window.viewport._scene.faces[0])[0]
    assert calls==[]
    panel.appearance["show_model_lines"].setChecked(True)
    assert calls[-1]>0


def test_transparency_reaches_unmeshed_surface_renderer_immediately(window,monkeypatch):
    panel=window.panels["Visualization"];calls=[];original=window.viewport.canvas.add_faces
    def record(*args,**kwargs):
        calls.append(kwargs);return original(*args,**kwargs)
    monkeypatch.setattr(window.viewport.canvas,"add_faces",record)
    panel.transparency.setValue(65)
    assert window.mesh is None and calls[-1]["opacity"]==pytest.approx(.35)
    assert panel.transparency_value.text()=="65%"
    panel.render.setCurrentText("Wireframe")
    assert calls[-1]["opacity"]==0 and calls[-1]["outline"]


def test_beam_section_toggle_rebuilds_cached_cad_without_model_or_camera_change(window):
    from anyfem.application.examples import floating_barge_section
    window._set_project(floating_barge_section());window.show_geometry(reset_view=True)
    panel=window.panels["Visualization"]
    position=window.viewport.canvas.export_view_state().camera_position
    camera=(position.x,position.y,position.z)
    revision=window.project.geometry.revision
    assert any(face.ref.kind=="edge" for face in window.viewport._scene.faces)
    panel.appearance["show_beam_sections"].setChecked(False)
    assert not any(face.ref.kind=="edge" for face in window.viewport._scene.faces)
    assert window._view_mode=="geometry" and window.project.geometry.revision==revision
    position=window.viewport.canvas.export_view_state().camera_position
    assert (position.x,position.y,position.z)==pytest.approx(camera,rel=0,abs=1e-12)
    panel.appearance["show_beam_sections"].setChecked(True)
    assert any(face.ref.kind=="edge" for face in window.viewport._scene.faces)


def test_hidden_geometry_can_be_restored_after_appearance_change(window):
    face=EntityRef("face",next(iter(window.project.geometry.faces)))
    view=window.viewport;view.visibility.set_hidden([face]);view.refresh_visibility()
    panel=window.panels["Visualization"];panel.transparency.setValue(50)
    assert not view._scene.faces
    view.visibility.set_hidden([face],False);view.refresh_visibility()
    assert view._scene.faces and view._scene.faces[0].ref==face


def test_invalid_appearance_is_refused_atomically_and_sync_does_not_apply(window,monkeypatch):
    panel=window.panels["Visualization"];old=window.viewport.visualization
    panel.opacity.setText("1.5")
    with pytest.raises(ValueError,match="opacity"):panel.apply()
    assert window.viewport.visualization==old
    panel.sync_from_viewport();panel.appearance["edge_color"].setText("bad-colour")
    with pytest.raises(ValueError,match="colour"):panel.apply()
    assert window.viewport.visualization==old
    monkeypatch.setattr(window.viewport,"set_visualization",lambda style:pytest.fail("sync applied"))
    panel.sync_from_viewport()
    assert panel.opacity.text()==str(old.surface_opacity)
    assert panel.appearance["show_model_lines"].toolTip()


def test_render_icons_apply_without_apply_button(window,qapp):
    from PySide6.QtTest import QTest
    panel=window.panels["Visualization"]
    window.notebook.select("Visualization");qapp.processEvents()
    QTest.mouseClick(panel.option_icons.buttons[1],Qt.LeftButton)
    assert window.viewport.visualization.render_mode=="Shaded"


def test_hiding_model_lines_preserves_support_annotations(window,monkeypatch):
    from anyfem.model.attributes import Support
    point=EntityRef("vertex",next(iter(window.project.geometry.vertices)))
    window.run(cmd.AddSupport(Support("Fixed",point,{"ux":0,"rx":0})))
    window.show_geometry()
    calls=[];original=window.viewport._draw_line_batches
    def record(lines):
        calls.extend(lines);return original(lines)
    monkeypatch.setattr(window.viewport,"_draw_line_batches",record)
    window.panels["Visualization"].appearance["show_model_lines"].setChecked(False)
    assert len(calls)==2 and all(not line.pick_owners for line in calls)


def test_live_appearance_refresh_preserves_retained_source_and_colormap(window,qapp,tmp_path):
    from test_qt_workbench import cantilever,wait_until
    from anyfem.presentation.scene import RESULT_COLORMAPS
    window.session.mark_saved();window.new_project();cantilever(window)
    window.generate_mesh_async(.5,strategy="auto")
    wait_until(qapp,lambda:window.mesh is not None)
    window.solve();wait_until(qapp,lambda:window.solution is not None)
    job=window.active_job_id
    path=tmp_path/"retained.anyfem"
    window.save_project(path=str(path));window.flush_project_writes()
    window.session.mark_saved();window.new_project();window.open_project(str(path))
    results=window.panels["Results"];results.activate_job(job);results.show_results()
    assert results.use_retained() and window.solution is None
    field=results.field_name();frame=results.frame.value()
    visual=window.panels["Visualization"]
    visual.appearance["result_colormap"].setCurrentText("Viridis")
    assert results.colors.currentText()=="Viridis"
    assert results.colormap()==RESULT_COLORMAPS["Viridis"]
    visual.appearance["show_result_nodes"].setChecked(True)
    assert window.viewport._scene.points
    assert results.use_retained() and window.solution is None
    assert window._view_mode=="results" and window.active_job_id==job
    assert results.field_name()==field and results.frame.value()==frame
