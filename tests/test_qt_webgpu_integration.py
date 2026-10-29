"""Opt-in real workbench integration; stored fixtures require no numerical jobs."""
import os
import time

import pytest

if os.environ.get("ANYFEM_TEST_WEBGPU") != "1":
    pytest.skip("set ANYFEM_TEST_WEBGPU=1 for hardware integration", allow_module_level=True)

import numpy as np
from PySide6.QtWidgets import QApplication
from anyfem.ui.qt.app import QtFemWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from anyfem.io import recovery
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    window = QtFemWindow(viewer_backend="webgpu")
    window.resize(1200, 800)
    window.show()
    qapp.processEvents()
    assert window.viewport.canvas.export_view_state().shading_enabled
    yield window
    window.session.mark_saved()
    window.close()
    qapp.processEvents()


def wait(qapp, predicate):
    deadline = time.monotonic() + 10
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.01)
    assert predicate(), "bounded workbench request did not complete"


def settled(window):
    loader = getattr(window, "_result_loader", None)
    return ((loader is None or not loader._records)
            and not getattr(window,"_result_metadata_preparations",None)
            and not window.panels["Results"]._display_pending and not window._refresh_pending)


def test_reset_view_retires_native_canvas_before_deletion(window,qapp):
    from rendercanvas.qt import loop
    from PySide6.QtCore import QCoreApplication,QEvent
    canvas=window.viewport.canvas
    # Reset synchronously before yielding to native presentation callbacks.
    canvas.set_present_method('native');native=canvas.surface
    assert not native.get_closed()
    window.reset_view_action.trigger()
    assert native.get_closed()
    assert native not in loop.get_canvases(close_closed=True)
    QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    native.close()  # Former rendercanvas app-shutdown crash.
    qapp.processEvents()
    assert window.viewport.canvas is canvas and not canvas._closed


def test_reset_view_ignores_saved_workspace_and_preserves_model(window,qapp):
    from dataclasses import replace
    from copy import deepcopy
    from anyfem.ui.qt import theme
    from anyfem.presentation.visualization import VisualizationStyle
    controls=window.panels['Visualization'].presentation_controls
    canvas=window.viewport.canvas
    canvas.apply_view_state(replace(canvas.export_view_state(),fov=.4,axis_indicator=False))
    window.viewport.set_visualization(replace(window.viewport.visualization,surface_opacity=.2,lighting='Flat'))
    controls.box_enabled.setChecked(True);controls.compare.setChecked(True)
    controls.save_workspace()
    saved=deepcopy(window.project.presentation['workspace'])
    assert saved['comparison']['enabled'] and saved['box']['enabled']
    model_hash=window.session.revision.model_hash
    project=window.project;canvas=window.viewport.canvas
    from PySide6.QtCore import Qt
    model,tasks,jobs,messages,run=window._workspace_docks
    tasks.setFloating(True);model.hide()
    window.addDockWidget(Qt.LeftDockWidgetArea,jobs)
    qapp.processEvents()
    window.reset_view_action.trigger()
    for _ in range(6):qapp.processEvents()
    assert not tasks.isFloating() and not model.isHidden()
    assert window.dockWidgetArea(model)==Qt.LeftDockWidgetArea
    assert window.dockWidgetArea(tasks)==Qt.RightDockWidgetArea
    assert window.dockWidgetArea(jobs)==Qt.BottomDockWidgetArea
    assert messages in window.tabifiedDockWidgets(jobs)
    assert run.isHidden()
    expected=VisualizationStyle()
    if theme.is_dark():expected=replace(expected,**theme.VIEWPORT['dark'])
    assert window.viewport.visualization==expected
    assert window.project is project and window.session.revision.model_hash==model_hash
    assert window.viewport.canvas is canvas
    assert not window.split_screen_action.isChecked() and not controls.compare.isChecked()
    assert canvas.section_box is None
    assert canvas.export_view_state().fov==window.viewport._default_view_state.fov
    assert canvas.export_view_state().axis_indicator==window.viewport._default_view_state.axis_indicator
    assert not controls.annotations_visible.isChecked()
    assert window.project.presentation['workspace']['comparison']['enabled'] is False
    assert window.project.presentation['workspace']['box'] is None
    controls.reset_project();qapp.processEvents()
    assert not window.split_screen_action.isChecked()  # Old project workspace cannot return.


def test_split_screen_toolbar_toggle_allocates_both_panes(window, qapp):
    controls=window.panels['Visualization'].presentation_controls
    action=window.split_screen_action
    assert not action.isChecked() and not controls.compare.isChecked()
    assert controls.reference is None
    primary=window.viewport;scene=primary._scene
    from PySide6.QtWidgets import QToolBar
    toolbar=next(bar for bar in window.findChildren(QToolBar)
                 if bar.objectName()=='Selection and renderer')
    actions=toolbar.actions()
    assert actions[actions.index(action)+1] is window.reset_view_action
    assert actions[actions.index(action)+2] is window._view_mode_actions['geometry']
    for _ in range(2):
        action.trigger()
        for _ in range(6):qapp.processEvents()
        assert controls.compare.isChecked() and action.isChecked()
        assert controls.reference.widget.isVisible()
        assert min(controls.splitter.sizes())>=160
        assert primary.widget.width()>=160 and controls.reference.widget.width()>=160
        assert primary._scene is scene
        controls.compare.setChecked(False)
        for _ in range(4):qapp.processEvents()
        assert not action.isChecked() and not controls.reference.widget.isVisible()
        assert primary.widget.width()>=320
    action.trigger();qapp.processEvents()
    controls.reset_project();qapp.processEvents()
    assert not action.isChecked() and not controls.compare.isChecked()


def test_visual_presets_follow_theme_and_groups_remain_usable(window, qapp):
    from anyfem.ui.qt import theme
    from anyfem.ui.qt.widgets import Collapsible
    from PySide6.QtWidgets import QPushButton
    original=theme.is_dark();visual=window.panels['Visualization']
    try:
        window.workspace_actions['Visualization'].trigger()
        for dark in (True,False,True):
            theme.set_dark(window,dark,persist=False)
            for name in ('Model','Simple','Results','Presentation'):
                visual.preset.setCurrentText(name);visual.apply_preset();qapp.processEvents()
                if dark:
                    assert window.viewport.visualization.background==theme.VIEWPORT['dark']['background']
                    assert window.viewport.visualization.edge_color==theme.VIEWPORT['dark']['edge_color']
                else:
                    assert window.viewport.visualization.background!=theme.VIEWPORT['dark']['background']
            visual.reset()
            assert window.viewport.visualization.background==theme.VIEWPORT['dark' if dark else 'light']['background']
        sections=visual.findChildren(Collapsible)
        assert len(sections)==10
        assert all(not section.is_expanded() for section in sections)
        assert not any('image' in button.text().lower() for button in visual.findChildren(QPushButton))
        quality=next(section for section in sections if section.toggle.text()=='Lighting and quality')
        quality.toggle.click();qapp.processEvents()
        assert quality.is_expanded() and visual.appearance['lighting'].isVisible()
        visual.appearance['lighting'].setCurrentText('Flat')
        assert window.viewport.visualization.lighting=='Flat'
        quality.toggle.click();qapp.processEvents()
        for _ in range(4):qapp.processEvents()
        import os
        from pathlib import Path
        if os.environ.get('ANY3DVIEW_CHECK_DIR'):
            window.grab().save(str(Path(os.environ['ANY3DVIEW_CHECK_DIR'])/'visualization-dark.png'))
            (Path(os.environ['ANY3DVIEW_CHECK_DIR'])/'section-sizes.txt').write_text('\n'.join(str((section.toggle.text(),section.height(),section.sizeHint().height(),section.minimumSizeHint().height(),section.body.isHidden())) for section in sections))
    finally:theme.set_dark(window,original,persist=False)


def test_simple_preset_applies_low_quality_and_preserves_view_state(window, qapp):
    visual=window.panels['Visualization'];controls=visual.presentation_controls
    controls.shadows.setCurrentText('High');controls.apply_renderer()
    canvas=window.viewport.canvas
    assert canvas._shadows[0]=='High'
    old=window.viewport.visualization
    before=(old.surface_opacity,old.show_result_loads,old.result_colormap)
    visual.preset.setCurrentText('Simple');visual.apply_preset();qapp.processEvents()
    style=window.viewport.visualization
    assert (style.surface_opacity,style.show_result_loads,style.result_colormap)==before
    assert (style.antialiasing,style.lighting,style.ambient_occlusion)==('Off','Flat','Off')
    assert canvas.renderer_diagnostics['ambient_occlusion']=='Off'
    assert not canvas.renderer_diagnostics['feature_edges']
    assert not canvas.export_view_state().shading_enabled
    assert canvas._shadows[0]=='Off' and controls.shadows.currentText()=='Off'
    assert window.project.presentation['workspace']['renderer']['shadows']=='Off'


def test_mesh_mode_shows_element_edges_with_default_feature_filter(window, qapp, tmp_path):
    """A real WebGPU mesh must show the FE grid, not just its outer boundary."""
    from dataclasses import replace
    from test_io import write_sesam_plate
    from anyfem.ui.scene import Scene

    source=tmp_path/'grid.FEM';write_sesam_plate(source,nx=8,ny=8,side=8)
    window.import_sesam_model(str(source));qapp.processEvents()
    assert window._view_mode=='mesh' and len(window.mesh.shells)==64
    canvas=window.viewport.canvas;style=window.viewport.visualization
    assert style.feature_edges and style.render_mode=='Shaded with edges'
    assert window.viewport._scene.show_element_edges
    assert not canvas.renderer_diagnostics['feature_edges']
    original_owners=[tuple(owners) for patch in window.viewport._scene.faces
                     for owners in patch.polygon_owners]
    assert len(original_owners)==64
    canvas.surface.resize(800,600);canvas.set_top_view();canvas.fit_to_scene(redraw=False)
    edged=np.asarray(canvas.capture_image(width=800,height=600)).copy()
    line_counts=[draw[4] for entry in canvas._entries.values()
                 if entry['handle'].mesh.lines is not None
                 for draw in canvas._webgpu.cache[id(entry['handle'])][2]
                 if draw[5]=='line-list']
    assert sum(line_counts)==256*6  # All polygon boundaries, not just 32 exterior edges.

    # The user's explicit surface-only mode still suppresses outlines.
    window.viewport.set_visualization(replace(style,render_mode='Shaded'))
    shaded=np.asarray(canvas.capture_image(width=800,height=600)).copy()
    assert np.count_nonzero(np.any(edged[:,:,:3]!=shaded[:,:,:3],axis=2))>1000

    # Wireframe is another explicit edged mode: the grid must remain visible
    # against a uniform background when the surface fill is absent.
    window.viewport.set_visualization(replace(style,render_mode='Wireframe',
                                              background_gradient=False,ambient_occlusion='Off'))
    wire=np.asarray(canvas.capture_image(width=800,height=600)).copy()
    background=wire[0,0,:3].astype(int)
    foreground=np.max(np.abs(wire[150:450,200:600,:3].astype(int)-background),axis=2)>5
    assert np.count_nonzero(foreground)>500

    # Returning to normal geometry and then the mesh must switch the effective
    # policy in both directions, without changing the stored preference/owners.
    window.viewport.set_visualization(style)
    window.viewport.show(Scene())
    assert canvas.renderer_diagnostics['feature_edges']
    window.show_mesh()
    assert not canvas.renderer_diagnostics['feature_edges']
    assert style.feature_edges and window.viewport.visualization.feature_edges
    assert [tuple(owners) for patch in window.viewport._scene.faces
            for owners in patch.polygon_owners]==original_owners

    # Candidate inspection meshes use the same rendering contract.
    mesh=window.mesh;window.mesh=None;window._inspection_mesh=mesh
    window.show_mesh()
    assert window._view_mode=='inspection_mesh'
    assert window.viewport._scene.show_element_edges
    assert not canvas.renderer_diagnostics['feature_edges']
    window.mesh=mesh;window._inspection_mesh=None


def test_stored_stress_roundtrip_and_renderer_switch(window, qapp, tmp_path, monkeypatch):
    from test_interop_results import SHELL_SIF
    source = tmp_path / "source.FEM"
    source.write_text("\n".join(line for line in SHELL_SIF.splitlines()
                                if not line.startswith("RVSTRESS")) + "\n")
    result = tmp_path / "stress.SIF"
    result.write_text(SHELL_SIF)
    window.import_sesam_model(str(source))
    window.import_sesam_result(str(result))
    wait(qapp, lambda: settled(window))
    assert not window._wordmark.isVisibleTo(window)
    job_id = window.active_job_id
    path = tmp_path / "stored.anyfem"
    window.save_project(path=str(path))
    window.flush_project_writes()
    dataset = window.result_datasets[job_id]
    expected = {key: dataset.field(key).read(0).copy() for key in dataset.field_keys}
    assert expected and any(np.any(value) for value in expected.values())
    window.new_project()
    source.unlink()
    result.unlink()
    window.open_project(str(path))
    panel = window.panels["Results"]
    panel.activate_job(job_id)
    wait(qapp, lambda: settled(window))
    key = next(iter(expected))
    from anyfem.presentation.result_browser import ResultEntry
    panel.select_entry(ResultEntry((), key, "field", key))
    wait(qapp, lambda: settled(window))
    assert window.viewport.canvas.backend_name == "webgpu"
    assert window.viewport._retained_result is not None
    retained = window.viewport._retained_result
    visual = window.panels["Visualization"]
    visual.opacity.setText("0.5")
    visual.apply()
    qapp.processEvents()
    assert window.viewport.visualization.surface_opacity == .5
    assert window.viewport._retained_result is retained
    assert any(entry["appearance"].get("opacity") == .5
               for entry in window.viewport.canvas._entries.values())
    for backend in ("software", "auto", "webgpu"):
        window.switch_viewer_backend(backend)
        qapp.processEvents()
        assert window.renderer.value() == backend
        assert window.viewport.canvas.backend_name == ("webgpu" if backend == "auto" else backend)
    for name, values in expected.items():
        np.testing.assert_array_equal(window.result_datasets[job_id].field(name).read(0), values)
    csv_path = tmp_path / "field.csv"
    monkeypatch.setattr(window.dialogs, "save_file", lambda **_: str(csv_path))
    panel.export_csv()
    wait(qapp, csv_path.exists)
    assert csv_path.stat().st_size > 20
    capture = tmp_path / "results.png"
    window.viewport.capture_png(capture)
    assert capture.stat().st_size > 100


def test_failed_switch_preserves_canvas_scene_and_policy(window, qapp, monkeypatch):
    from anyfem import commands as cmd
    from anyfem.ui.qt import viewport as module
    from any3dview.errors import GPUUnavailableError
    window.run(cmd.AddPoint(1, 2, 3))
    qapp.processEvents()
    old = window.viewport.canvas
    scene = window.viewport._scene
    revision = window.session.revision
    def unavailable(*args, **kwargs):
        raise GPUUnavailableError("test unavailable hardware")
    monkeypatch.setattr(module, "create_viewer", unavailable)
    with pytest.raises(GPUUnavailableError, match="test unavailable"):
        window.switch_viewer_backend("gpu")
    assert window.viewport.canvas is old
    assert window.viewport._scene is scene
    assert window.renderer.value() == "webgpu"
    assert window.session.revision == revision
    old.redraw()
    qapp.processEvents()


@pytest.mark.parametrize("unavailable", [False, True])
def test_automatic_prefers_webgpu_and_reports_unavailability(qapp, tmp_path, monkeypatch, unavailable):
    from anyfem.io import recovery
    from anyfem.ui.qt import viewport as module
    from any3dview.errors import GPUUnavailableError
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    monkeypatch.delenv("ANY3DVIEW_DISABLE_GPU", raising=False)
    original = module.create_viewer
    requested = []
    def create(*args, **kwargs):
        requested.append(kwargs["backend"])
        if unavailable and kwargs["backend"] == "webgpu":
            raise GPUUnavailableError("test hardware unavailable")
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "create_viewer", create)
    window = QtFemWindow(viewer_backend="auto")
    try:
        expected=["webgpu","software"] if unavailable else ["webgpu"]
        assert requested==expected  # The final renderer is chosen before show().
        first=window.viewport.canvas
        first_style=window.viewport.visualization
        window.show()
        for _ in range(6):qapp.processEvents()
        assert requested==expected and window.viewport.canvas is first
        assert window.viewport.visualization==first_style
        assert window.renderer.value() == "auto"
        assert window.viewport.requested_backend == "auto"
        assert window.viewport.canvas.backend_name == ("software" if unavailable else "webgpu")
        if unavailable:
            assert "test hardware unavailable" in " ".join(window.viewport.backend_diagnostics)
            assert "test hardware unavailable" in window._status.get()
        else:
            assert "WebGPU" in window._status.get()
    finally:
        window.session.mark_saved()
        window.close()
        qapp.processEvents()
