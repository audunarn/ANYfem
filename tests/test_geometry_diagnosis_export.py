"""Lossless report export and safe publication failures, independent of Qt."""
import json

import pytest

from anyfem.geometry_cleaner import diagnose_project
from anyfem.geometry_diagnosis_worker import write_diagnosis_report, snapshot_project_for_diagnosis
from anyfem.model.project import Project


def report():
    project = Project()
    project.geometry.add_points(((0, 0, 0), (1e-6, 0, 0), (1, 0, 0)))
    return diagnose_project(project)


def test_exports_retain_every_finding_raw_owner_provenance_and_status(tmp_path):
    diagnosis = report()
    context = {"project": "Test", "document_revision": "captured", "current_document_matches_snapshot": False}
    path = write_diagnosis_report(diagnosis, tmp_path / "report.json", document=context)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["findings"] == diagnosis.to_dict()["findings"]
    assert data["owner_audit"] == diagnosis.audit.to_dict()
    assert data["complete"] == diagnosis.complete and data["clean"] == diagnosis.clean
    assert data["document"] == context
    markdown = write_diagnosis_report(diagnosis, tmp_path / "report.md", document=context).read_text(encoding="utf-8")
    assert all(item.id in markdown for item in diagnosis.findings)
    assert "current_document_matches_snapshot: False" in markdown


def test_cancel_or_write_failure_does_not_replace_existing_report(tmp_path, monkeypatch):
    diagnosis = report()
    target = tmp_path / "report.json"
    target.write_text("original", encoding="utf-8")
    with pytest.raises(InterruptedError):
        write_diagnosis_report(diagnosis, target, cancelled=lambda: True)
    assert target.read_text() == "original"
    import anyfem.geometry_diagnosis_worker as worker
    monkeypatch.setattr(worker.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("injected disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        write_diagnosis_report(diagnosis, target)
    assert target.read_text() == "original"
    assert list(tmp_path.iterdir()) == [target]


def test_incomplete_report_exports_incomplete_status_and_uncertain_checks(tmp_path):
    project = Project()
    project.geometry.add_point(0, 0)
    diagnosis = diagnose_project(project, cancelled=lambda: True)
    assert not diagnosis.complete and not diagnosis.clean
    data = json.loads(write_diagnosis_report(diagnosis, tmp_path / "cancelled.json").read_text())
    assert not data["complete"] and not data["clean"]
    assert any(check["status"] != "completed" for check in data["checks"])


def test_snapshot_is_detached_without_normalizing_live_assignments():
    from anyfem.application.controller import default_project
    from anyfem.model.attributes import Support
    from anygeometry import EntityRef
    from anygeometry import to_dict
    project = default_project()
    vertex = project.geometry.add_point(0, 0)
    project.supports.append(Support("missing", EntityRef("vertex", 9999), {"ux": 0.}))
    before_regions = tuple(project.regions)
    before_geometry = to_dict(project.geometry)
    snapshot = snapshot_project_for_diagnosis(project)
    assert tuple(project.regions) == before_regions
    assert to_dict(project.geometry) == before_geometry
    first, second = snapshot.thaw(), snapshot.thaw()
    assert first.geometry.model_id == project.geometry.model_id
    first.geometry.move_point(vertex, 1, 0, 0)
    first.supports.clear()
    assert tuple(project.geometry.vertex_position(vertex)) == (0, 0, 0)
    assert tuple(second.geometry.vertex_position(vertex)) == (0, 0, 0)
    assert project.supports and second.supports
    diagnosis = diagnose_project(second)
    assert any(item.kind == "missing_project_anchor" for item in diagnosis.findings)


def test_worker_transport_round_trip_preserves_project_and_model_identity():
    from anyfem.application.controller import default_project
    from anyfem.job_process import dumps, loads
    from anygeometry import to_dict
    project = default_project()
    project.geometry.add_point(1, 2, 3)
    source = to_dict(project.geometry)
    transport = snapshot_project_for_diagnosis(project).for_worker()
    restored = loads(dumps(transport)).thaw()
    assert to_dict(restored.geometry) == source
    assert restored.document_id == project.document_id
    assert restored.materials == project.materials
    restored.geometry.move_point(1, 2, 2, 3)
    assert to_dict(project.geometry) == source


def test_direct_call_does_not_change_calling_process_priority(monkeypatch):
    from anyfem import geometry_diagnosis_worker as worker
    monkeypatch.setattr(worker.multiprocessing, "parent_process", lambda: None)
    outcome = worker._interactive_process_priority()
    assert not outcome["applied"] and outcome["reason"] == "not a worker process"
