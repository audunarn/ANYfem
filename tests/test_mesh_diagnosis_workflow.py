"""Mesh-specific reports and explicit size submission, without numerical jobs."""
import json
from types import MethodType, SimpleNamespace

import pytest

from anyfem.application.workflow import WorkbenchWorkflow
from anyfem.document import DocumentSession
from anyfem.model.project import Project
from anyfem.model.records import MeshRecord
from anyfem.native_meshing import NativeMeshControl, NativeMeshSettings


def application():
    project = Project("mesh diagnosis")
    project.target_size = .5
    project.set_native_mesh_settings(NativeMeshSettings.create(.5, backend="quad_first",
        controls=[NativeMeshControl.create("local refinement", target_size=.25)]))
    app = SimpleNamespace(project=project, session=DocumentSession(project), mesh=None,
                          seeding_overrides={1: 4}, statuses=[], submissions=[])
    app.set_status = lambda text: app.statuses.append(text)
    app.refresh_panels = lambda: None
    app.mesh_task_manager = SimpleNamespace(busy=False, submit=lambda *args: app.submissions.append(args))
    for name in ("_store_mesh_strategy", "mesh_record_state", "mesh_diagnostic_report"):
        setattr(app, name, MethodType(getattr(WorkbenchWorkflow, name), app))
    app._project_mesh_strategy = WorkbenchWorkflow._project_mesh_strategy
    app._project_structure_preference = WorkbenchWorkflow._project_structure_preference
    return app


def report_data(app, **kwargs):
    report = app.mesh_diagnostic_report(**kwargs)
    return json.loads(report[report.index("{"):])


@pytest.mark.parametrize("strategy", ["auto", "mapped", "native", "quad_first"])
@pytest.mark.parametrize("size", [.5, 1.])
def test_global_size_reaches_settings_snapshot_and_record(strategy, size):
    app = application()
    record = WorkbenchWorkflow.generate_mesh_async(app, size, strategy=strategy)
    _, snapshot, settings = app.submissions[0]
    assert settings.target_size == size
    assert settings.strategy == strategy
    assert record.summary["strategy_requested"] == strategy
    restored = NativeMeshSettings.from_dict(snapshot.document["meshing"]["native"])
    assert restored.target_size == size
    assert restored.backend.value == ("automatic" if strategy == "auto" else strategy)
    assert restored.controls[0].target_size == .25
    assert record.summary["target_size"] == size
    assert app.project.target_size == size
    assert app.project.native_mesh_settings.target_size == size
    assert app.project.native_mesh_settings.controls[0].target_size == .25
    assert dict(settings.overrides) == {1: 4}
    assert app.project.seeding_overrides == {1: 4}
    # Snapshot is detached; later GUI edits cannot alter the submitted request.
    app.project.target_size = 2.
    assert settings.target_size == size
    assert snapshot.document["meshing"]["target_size"] == size


@pytest.mark.parametrize("state", ["failed", "cancelled", "stale", "running"])
def test_report_preserves_job_evidence_and_separates_active_mesh(state):
    app = application()
    record = MeshRecord("Latest", "", "input", "", status=state,
                        summary={"target_size": 1., "nodes": 999},
                        diagnostics=[{"type": "MeshError", "message": "evidence"}])
    app.project.mesh_records[record.id] = record
    app._mesh_details_record_id = record.id
    app.mesh = SimpleNamespace(num_nodes=12, num_elements=8, shells=[1]*8, beams=[],
                               hybrid_diagnostics={"backend": "python"})
    app.mesh_record_id = "older-published"
    data = report_data(app, draft={"target_size_text": "invalid draft"})
    assert data["selected_job"]["status"] == state
    assert data["selected_job"]["diagnostics"] == record.diagnostics
    assert data["active_mesh"]["nodes"] == 12
    assert data["active_mesh"]["record_id"] == "older-published"
    assert data["draft_not_yet_submitted"]["target_size_text"] == "invalid draft"
    assert data["persisted_settings"]["target_size"] == .5


def test_report_before_first_mesh_needs_no_widgets_or_native_import():
    app = application()
    data = report_data(app)
    assert data["active_mesh"] is None
    assert data["selected_job"] is None
    assert data["seeding_pins"] == {"1": 4}
    assert data["runtime"]["python_executable"]


@pytest.fixture
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_qt_mesh_copy_uses_real_clipboard_and_confirms(qapp):
    from anyfem.ui.qt.tasks import MeshTask
    from anyfem.ui.qt.ports import QtClipboardPort
    app = application()
    app.clipboard = QtClipboardPort()
    widget = SimpleNamespace(app=app, size=SimpleNamespace(text=lambda: "1"), strategy_token=lambda: "native")
    for name, value in (("strategy", "native"), ("backend", "auto"),
                        ("order", "linear"), ("layout_policy", "adaptive")):
        setattr(widget, name, SimpleNamespace(currentText=lambda value=value: value))
    MeshTask.copy_diagnosis(widget)
    text = qapp.clipboard().text()
    assert text.startswith("ANYfem mesh diagnosis")
    assert '"target_size_text": "1"' in text
    assert app.statuses[-1].startswith("Mesh diagnosis copied")


def test_qt_clipboard_failed_write_is_not_success(monkeypatch, qapp):
    from anyfem.ui.qt import ports
    clipboard = SimpleNamespace(setText=lambda text: None, text=lambda: "old")
    monkeypatch.setattr(ports, "QApplication", SimpleNamespace(clipboard=lambda: clipboard))
    with pytest.raises(RuntimeError, match="did not retain"):
        ports.QtClipboardPort().copy_text("new")


def test_qt_mesh_copy_failure_does_not_confirm(qapp):
    from anyfem.ui.qt.tasks import MeshTask
    app = application()
    app.mesh_diagnostic_report = lambda **kwargs: "diagnosis"
    def fail(text):
        raise RuntimeError("clipboard refused")
    app.clipboard = SimpleNamespace(copy_text=fail)
    widget = SimpleNamespace(app=app, size=SimpleNamespace(text=lambda: "1"), strategy_token=lambda: "draft")
    for name in ("strategy", "backend", "order", "layout_policy"):
        setattr(widget, name, SimpleNamespace(currentText=lambda: "draft"))
    with pytest.raises(RuntimeError, match="clipboard refused"):
        MeshTask.copy_diagnosis(widget)
    assert app.statuses == []


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from anyfem.io import recovery
    from anyfem.ui.qt.app import QtFemWindow
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    window = QtFemWindow(viewer_backend="software")
    yield window
    window.session.mark_saved()
    window.close()
    qapp.processEvents()


def test_real_mesh_copy_button_copies_job_report_and_status(window, qapp):
    from PySide6.QtWidgets import QPushButton
    record = MeshRecord("Failed mesh", "", "input", "", status="failed",
                        summary={"target_size": 1., "native_backend_requested": "python",
                                 "strategy_requested": "native"},
                        diagnostics=[{"type": "SizingFailure", "message": "mesh evidence"}])
    window.project.mesh_records[record.id] = record
    window._mesh_details_record_id = record.id
    panel = window.panels["Mesh"]
    panel.size.setText("0.5")
    panel.strategy.setCurrentText("auto")
    button = next(button for button in panel.findChildren(QPushButton)
                  if button.text() == "Copy mesh diagnosis")
    button.click()
    text = qapp.clipboard().text()
    assert text.startswith("ANYfem mesh diagnosis")
    data = json.loads(text[text.index("{"):])
    assert data["selected_job"]["id"] == record.id
    assert data["selected_job"]["summary"]["target_size"] == 1.
    assert data["selected_job"]["diagnostics"][0]["type"] == "SizingFailure"
    assert data["draft_not_yet_submitted"]["target_size_text"] == "0.5"
    assert data["draft_not_yet_submitted"]["strategy"] == "auto"
    assert data["selected_job"]["summary"]["strategy_requested"] == "native"
    assert window.statusBar().currentMessage().startswith("Mesh diagnosis copied")


@pytest.mark.parametrize("strategy", ["auto", "mapped", "native", "quad_first"])
def test_real_generate_button_submits_changed_global_size(window, qapp, monkeypatch, strategy):
    submissions = []
    monkeypatch.setattr(window.mesh_task_manager, "submit", lambda *args: submissions.append(args))
    panel = window.panels["Mesh"]
    assert {panel.strategy.itemText(i) for i in range(panel.strategy.count())} == {
        "auto", "mapped", "native", "quad_first", "SmartMapped"}
    panel.strategy.setCurrentText(strategy)
    assert panel.strategy.currentText() == strategy
    for size in (.5, 1.):
        panel.size.setText(str(size))
        panel.generate.click()
        assert len(submissions) == (1 if size == .5 else 2)
        job_id, snapshot, settings = submissions[-1]
        assert settings.target_size == size
        assert settings.strategy == strategy
        submitted = NativeMeshSettings.from_dict(snapshot.document["meshing"]["native"])
        assert submitted.backend.value == ("automatic" if strategy == "auto" else strategy)
        assert submitted.target_size == size
        assert snapshot.document["meshing"]["target_size"] == size
        assert window.project.mesh_records[job_id].summary["target_size"] == size
    assert submissions[0][2].target_size == .5
    assert submissions[0][2].strategy == strategy
    assert submissions[0][1].document["meshing"]["target_size"] == .5


def test_large_diagnosis_samples_without_scanning_or_mutating():
    from collections.abc import Mapping
    import numpy as np

    class LargeTable(Mapping):
        reads = 0
        iterations = 0
        def __len__(self):
            return 1_000_000
        def __iter__(self):
            for index in range(1_000_000):
                self.iterations += 1
                assert self.iterations <= 16
                yield index
        def __getitem__(self, key):
            self.reads += 1
            assert self.reads <= 32
            if type(key) is not int:
                raise KeyError(key)
            return {"status": "valid", "detail": "x" * 100_000}

    table = LargeTable()
    app = application()
    diagnostic = {"status": "certified", "per_element": table,
                  "samples": np.arange(1_000_000), "message": "💠" * 100_000}
    app.mesh = SimpleNamespace(num_nodes=500_000, num_elements=1_000_000,
        shells=range(1_000_000), beams=[], hybrid_diagnostics=diagnostic)
    payload = app.mesh_diagnostic_report()
    data = json.loads(payload[payload.index("{"):])
    assert len(payload.encode("utf-8")) <= 128 * 1024
    assert data["active_mesh"]["nodes"] == 500_000
    assert data["active_mesh"]["diagnostics"]["status"] == "certified"
    assert any(item["total"] == 1_000_000 and item["included"] <= 16
               for item in data["report_limits"]["omission_paths"])
    assert table.iterations <= 16 and table.reads <= 32
    assert diagnostic["message"] == "💠" * 100_000
    assert diagnostic["samples"].size == 1_000_000
    assert diagnostic["samples"][-1] == 999_999


def test_report_bounds_errors_and_settings_with_explicit_omissions():
    app = application()
    record = MeshRecord("Failure", "", "input", "", status="failed",
        diagnostics=[{"type": "HugeError", "message": "failure" * 100_000}] * 100_000)
    app.project.mesh_records[record.id] = record
    data = report_data(app)
    assert data["selected_job"]["status"] == "failed"
    assert data["selected_job"]["diagnostics"][0]["type"] == "HugeError"
    assert len(data["selected_job"]["diagnostics"]) == 4
    assert data["report_limits"]["omission_count"] > 0
    assert len(record.diagnostics) == 100_000
    assert len(record.diagnostics[0]["message"]) == 700_000


@pytest.mark.parametrize("target,expected", [(.5,"0.5 m"), (1,"1 m"),
    ("0.5","unavailable"), ({},"unavailable"), (True,"unavailable"),
    (float("nan"),"unavailable"), (float("inf"),"unavailable"), (-1.,"unavailable"),
    (10**400,"unavailable")])
def test_mesh_refresh_tolerates_open_ended_persisted_target(target, expected, qapp):
    from anyfem.ui.qt.tasks import MeshTask
    app = application()
    app.mesh_job_running = False
    app.mesh = SimpleNamespace(num_nodes=12,num_elements=8,hybrid_diagnostics={})
    record = MeshRecord("Persisted mesh", "", "", "", summary={"target_size":target})
    app.project.mesh_records[record.id] = record
    app.mesh_record_id = record.id
    texts = []
    widget = SimpleNamespace(app=app, generate=SimpleNamespace(setEnabled=lambda enabled: None),
                             summary=SimpleNamespace(setText=texts.append))
    MeshTask.refresh(widget)
    assert f"generated target {expected}" in texts[-1]
    assert "12 nodes" in texts[-1]
