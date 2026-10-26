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
            and not window.panels["Results"]._display_pending and not window._refresh_pending)


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
        window.show()
        wait(qapp, lambda: len(requested) >= (3 if unavailable else 2))
        assert requested == (["software", "webgpu", "software"] if unavailable else ["software", "webgpu"])
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
