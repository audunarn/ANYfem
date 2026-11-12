"""Real document/UI review, explicit preview, refusal and undo regressions."""
import os
from copy import deepcopy
import time

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("Qt opt-in", allow_module_level=True)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from anygeometry import EntityRef
from anyfem import commands
from anyfem.io.project_file import project_to_dict
from anyfem.model.project import Project
from anyfem.model.units import unit_profile
from test_qt_geometry_actions import qapp, window


def wait_until(predicate, *, timeout=20):
    deadline = time.perf_counter() + timeout
    while not predicate() and time.perf_counter() < deadline:
        QTest.qWait(10)
    assert predicate(), "Timed out waiting for the diagnosis worker to finish."


def scan(dialog, qapp):
    dialog.review()
    wait_until(lambda: dialog.runner is None)
    qapp.processEvents()


def model_content(document):
    # Owner undo restores design/IDs while advancing its monotonic revision.
    result = deepcopy(document)
    result["geometry"].pop("revision")
    result["geometry"].pop("checksum")
    return result


def install(window, coordinates=((0.00004, 0, 0), (1, 0, 0)), *, millimetres=False):
    project = Project("Cleaner fixture")
    if millimetres:
        project.units = unit_profile("SI-mm-N-MPa")
    vertices = project.geometry.add_points(coordinates)
    window._set_project(project)
    window.show_geometry(reset_view=True)
    return vertices


def select_finding(dialog, kind, vertex=None):
    for row in range(dialog.findings.rowCount()):
        finding = dialog.findings.item(row, 0).data(Qt.UserRole)
        if finding.kind == kind and (vertex is None or EntityRef("vertex", vertex) in finding.refs):
            dialog.findings.selectRow(row)
            return finding
    pytest.fail(f"Missing {kind} finding")


def test_review_reports_defects_without_mutation_and_restores_selection(window, qapp):
    vertices = install(window, ((0, 0, 0), (1, 0, 0), (1, 0.00001, 0), (0, 0.00001, 0), (3, 0, 0), (4, 0, 0)))
    window.project.geometry.add_plate(vertices[:4])
    window.project.geometry.add_line(*vertices[4:])
    window.selection.restore((EntityRef("vertex", vertices[0]),))
    before = project_to_dict(window.project)
    history = window.commands.history()
    revision = window.session.revision
    dialog = window.open_geometry_cleaner()
    dialog.review_button.click()
    wait_until(lambda: dialog.runner is None)
    assert dialog.report is not None, dialog.feedback.text()
    kinds = {finding.kind for finding in dialog.report.findings}
    assert {"sliver_face", "short_edge", "near_vertices", "disconnected_region"} <= kinds
    select_finding(dialog, "sliver_face")
    assert any(ref.kind == "face" for ref in window.selection.items)
    assert project_to_dict(window.project) == before
    assert window.commands.history() == history
    assert window.session.revision == revision
    dialog.close_button.click()
    qapp.processEvents()
    assert window.selection.items == [EntityRef("vertex", vertices[0])]


def test_remove_requires_preview_and_is_one_document_undo_action(window, qapp):
    vertices = install(window)
    dialog = window.open_geometry_cleaner()
    scan(dialog, qapp)
    before = project_to_dict(window.project)
    old_revision = window.session.revision.id
    select_finding(dialog, "unused_vertex", vertices[0])
    dialog.options_tabs.setCurrentIndex(1)
    dialog.remove_unused.setChecked(True)
    assert not dialog.apply_button.isEnabled()
    dialog.preview_button.click()
    assert dialog.plan and dialog.plan.changes
    assert project_to_dict(window.project) == before
    assert "Remove isolated point" in dialog.preview_text.toPlainText()
    dialog.apply_button.click()
    assert vertices[0] not in window.project.geometry.vertices
    assert window.session.dirty
    assert window.session.revision.id != old_revision
    assert len(window.commands.history()) == 1
    after = project_to_dict(window.project)
    window.undo()
    assert model_content(project_to_dict(window.project)) == model_content(before)
    window.redo()
    assert model_content(project_to_dict(window.project)) == model_content(after)
    dialog.reject()


def test_snap_explicit_units_bounds_noop_and_cancel(window, qapp):
    vertices = install(window, millimetres=True)
    dialog = window.open_geometry_cleaner()
    scan(dialog, qapp)
    before = project_to_dict(window.project)
    dialog.options_tabs.setCurrentIndex(1)
    window.selection.restore((EntityRef("vertex", vertices[0]),))
    dialog.capture_button.click()
    dialog.snap.setChecked(True)
    dialog.grid_size.setText("1 mm")
    dialog.max_displacement.setText("0.001 mm")
    dialog.preview_button.click()
    assert "maximum" in dialog.feedback.text().lower()
    assert not dialog.apply_button.isEnabled()
    assert project_to_dict(window.project) == before
    dialog.max_displacement.setText("0.1 mm")
    dialog.preview_button.click()
    assert dialog.apply_button.isEnabled()
    dialog.close_button.click()
    qapp.processEvents()
    assert project_to_dict(window.project) == before and not window.commands.history()
    dialog = window.open_geometry_cleaner()
    scan(dialog, qapp)
    window.selection.restore((EntityRef("vertex", vertices[0]),))
    dialog.capture_points()
    dialog.snap.setChecked(True)
    dialog.grid_size.setText("1 mm")
    dialog.max_displacement.setText("0.1 mm")
    dialog.preview()
    dialog.apply()
    assert tuple(window.project.geometry.vertex_position(vertices[0])) == (0, 0, 0)
    assert len(window.commands.history()) == 1
    window.undo()
    assert model_content(project_to_dict(window.project)) == model_content(before)
    dialog.reject()


def test_stale_document_and_changed_thresholds_invalidate_pending_repair(window, qapp):
    vertices = install(window)
    dialog = window.open_geometry_cleaner()
    scan(dialog, qapp)
    select_finding(dialog, "unused_vertex", vertices[0])
    dialog.remove_unused.setChecked(True)
    dialog.preview()
    assert dialog.plan
    dialog.short_edge.setText("2 mm")
    assert dialog.plan is None and dialog.report is None and not dialog.apply_button.isEnabled()
    scan(dialog, qapp)
    select_finding(dialog, "unused_vertex", vertices[0])
    dialog.preview()
    window.run(commands.AddPoint(5, 5))
    before = project_to_dict(window.project)
    dialog.apply()
    assert "changed" in dialog.feedback.text().lower()
    assert project_to_dict(window.project) == before
    dialog.reject()


def test_read_only_can_review_and_cannot_apply_even_after_preview(window, qapp):
    vertices = install(window)
    dialog = window.open_geometry_cleaner()
    scan(dialog, qapp)
    select_finding(dialog, "unused_vertex", vertices[0])
    dialog.remove_unused.setChecked(True)
    dialog.preview()
    before = project_to_dict(window.project)
    window.session.read_only = True
    dialog.apply()
    assert "read-only" in dialog.feedback.text()
    assert project_to_dict(window.project) == before and not window.commands.history()
    scan(dialog, qapp)
    assert dialog.report is not None and not dialog.preview_button.isEnabled()
    dialog.reject()


def test_feature_model_is_reviewable_and_explains_repair_refusal(window, qapp):
    window.run(commands.AddPoint(0.00004, 0))
    before = project_to_dict(window.project)
    dialog = window.open_geometry_cleaner()
    scan(dialog, qapp)
    assert dialog.report is not None
    assert "feature" in dialog.feedback.text().lower()
    assert not dialog.preview_button.isEnabled()
    assert project_to_dict(window.project) == before
    dialog.reject()


def test_invalid_scan_options_and_empty_repair_leave_document_unchanged(window, qapp):
    install(window)
    before = project_to_dict(window.project)
    dialog = window.open_geometry_cleaner()
    dialog.sliver_quality.setText("nan")
    scan(dialog, qapp)
    assert dialog.report is None and dialog.feedback.text()
    dialog.sliver_quality.setText("0.01")
    scan(dialog, qapp)
    dialog.preview()
    assert dialog.plan.is_noop and not dialog.apply_button.isEnabled()
    assert not window.commands.history() and project_to_dict(window.project) == before
    dialog.reject()


def test_new_project_requires_reopening_and_menu_is_accessible(window, qapp):
    install(window)
    window._actions["Review and clean geometry…"].trigger()
    dialog = window._geometry_cleaner_dialog
    assert dialog is not None and dialog.isVisible()
    scan(dialog, qapp)
    install(window, ((2, 0, 0),))
    before = project_to_dict(window.project)
    scan(dialog, qapp)
    assert dialog.report is None and "reopen" in dialog.feedback.text().lower()
    assert project_to_dict(window.project) == before and not window.commands.history()
    dialog.reject()


def test_background_diagnosis_keeps_events_alive_and_exports_all_pages(window, qapp, tmp_path):
    from PySide6.QtCore import QTimer
    import json
    install(window, tuple((float(x), 0., 0.) for x in range(25)))
    before = project_to_dict(window.project)
    dialog = window.open_geometry_cleaner()
    dialog.PAGE_SIZE = 3
    heartbeats = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: heartbeats.append(time.perf_counter()))
    timer.start()
    scan(dialog, qapp)
    timer.stop()
    assert len(heartbeats) >= 3
    assert max(b - a for a, b in zip(heartbeats, heartbeats[1:])) < .5
    assert dialog.report.complete
    if os.name == "nt":
        priority = dialog.report.source["process_priority"]
        assert priority["applied"] and priority["actual_class"] == 0x20
    assert dialog.page.maximum() > 1
    assert dialog.findings.rowCount() <= 3
    dialog.page.setValue(dialog.page.maximum())
    assert dialog.page_summary.text().endswith(str(len(dialog.report.findings)))
    target = tmp_path / "all-findings.json"
    dialog.save_report(target)
    wait_until(lambda: dialog.exporter is None)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert len(data["findings"]) == len(dialog.report.findings) > 3
    assert data["document"]["current_document_matches_snapshot"]
    assert project_to_dict(window.project) == before and not window.commands.history()
    dialog.reject()


def test_cancel_and_close_retire_actual_worker_without_mutation(window, qapp):
    install(window)
    before = project_to_dict(window.project)
    dialog = window.open_geometry_cleaner()
    dialog.review()
    worker = dialog.runner
    assert worker is not None
    wait_until(lambda: worker.process is not None)
    started = time.perf_counter()
    dialog.cancel_button.click()
    wait_until(lambda: dialog.runner is None, timeout=6)
    assert time.perf_counter() - started < 3
    assert worker.finished_run and worker.retired
    assert not worker.process.is_alive()
    assert dialog.report is not None and not dialog.report.complete and not dialog.report.clean
    assert "cancelled" in dialog.summary.text().lower()
    assert project_to_dict(window.project) == before and not window.commands.history()
    dialog.review()
    next_worker = dialog.runner
    dialog.reject()
    wait_until(lambda: window._geometry_cleaner_dialog is None, timeout=6)
    assert next_worker.finished_run and next_worker.retired
    assert project_to_dict(window.project) == before


def test_changed_document_discards_background_result(window, qapp):
    install(window)
    dialog = window.open_geometry_cleaner()
    dialog.review()
    window.run(commands.AddPoint(4, 0))
    before = project_to_dict(window.project)
    wait_until(lambda: dialog.runner is None)
    assert dialog.report is None
    assert "changed" in dialog.summary.text().lower()
    assert not dialog.export_button.isEnabled()
    assert project_to_dict(window.project) == before
    dialog.reject()


def test_cleanup_failure_cannot_claim_worker_retired(window, qapp, monkeypatch):
    from anyfem.ui.qt.geometry_cleaner import DiagnosisWorker
    from anyfem.geometry_diagnosis_worker import snapshot_project_for_diagnosis
    from anygeometry.cleaner import CleanerOptions
    from anyfem import job_process
    def failed(**kwargs):
        error = job_process.WorkerProcessError("cleanup was not verified", remote_type="WorkerCleanupFailed")
        error.cleanup_failures = ["owned descendant retirement unverified"]
        raise error
    monkeypatch.setattr(job_process, "run_in_worker_process", failed)
    worker = DiagnosisWorker(snapshot_project_for_diagnosis(window.project), CleanerOptions())
    messages = []
    worker.failed.connect(messages.append)
    worker.run()
    assert worker.finished_run and not worker.retired
    assert worker.cleanup_failures == ("owned descendant retirement unverified",)
    assert messages
    worker.deleteLater()


def test_missing_anchor_is_reported_without_selecting_nonexistent_geometry(window, qapp):
    from anyfem.model.attributes import Support
    install(window, ((0, 0, 0),))
    window.project.supports.append(Support("missing", EntityRef("vertex", 9999), {"ux": 0.}))
    dialog = window.open_geometry_cleaner()
    scan(dialog, qapp)
    select_finding(dialog, "missing_project_anchor", 9999)
    assert not window.selection.items
    assert "missing geometry" in dialog.feedback.text()
    assert not dialog.report.clean
    assert window.project.supports[0].ref == EntityRef("vertex", 9999)
    dialog.reject()
