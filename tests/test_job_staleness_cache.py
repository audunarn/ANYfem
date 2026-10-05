"""The job table's staleness answer is cached per committed revision."""

from __future__ import annotations

from types import SimpleNamespace

from anyfem.application.workflow import WorkbenchWorkflow
from anyfem.jobs import analysis_hash
from anyfem.model.records import AnalysisDefinition


def _document(load):
    return {"load_cases": [{"name": "default", "loads": [load]}]}


class _Host:
    """Minimal stand-in carrying the attributes ``_job_is_stale`` reads."""

    _job_is_stale = WorkbenchWorkflow._job_is_stale

    def __init__(self):
        self.definition = AnalysisDefinition(name="Linear static 1")
        self.document = _document(1)
        self.revision = SimpleNamespace(
            id="r1", document_hash="d1", model_hash="m1"
        )
        self.snapshots = 0
        self.session = SimpleNamespace(
            revision=self.revision, snapshot=self._snapshot
        )
        self.project = SimpleNamespace(
            analyses={self.definition.id: self.definition},
            output_requests=None,
            mesh_records={},
        )
        self.mesh_record_id = ""

    def _snapshot(self):
        self.snapshots += 1
        return SimpleNamespace(document=dict(self.document))

    def job(self, *, document=None, model_hash="m1"):
        submitted = analysis_hash(
            self.definition, None, document=document or self.document
        )
        return SimpleNamespace(
            model_hash=model_hash,
            mesh_hash="",
            analysis_id=self.definition.id,
            analysis_hash=submitted,
        )

    def commit(self, revision_id, document_hash, document):
        self.document = document
        self.revision = SimpleNamespace(
            id=revision_id, document_hash=document_hash, model_hash="m1"
        )
        self.session.revision = self.revision


def test_repeated_poll_ticks_serialize_the_project_once_per_revision():
    host = _Host()
    job = host.job()

    answers = [host._job_is_stale(job) for _ in range(50)]

    assert answers == [False] * 50
    assert host.snapshots == 1


def test_a_new_revision_recomputes_and_detects_a_changed_document():
    host = _Host()
    job = host.job()
    assert host._job_is_stale(job) is False

    host.commit("r2", "d2", _document(2))

    assert host._job_is_stale(job) is True
    assert host.snapshots == 2
    for _ in range(10):
        assert host._job_is_stale(job) is True
    assert host.snapshots == 2


def test_distinct_jobs_on_one_revision_do_not_share_an_answer():
    host = _Host()
    current = host.job()
    old = host.job(document=_document(0))

    assert host._job_is_stale(current) is False
    assert host._job_is_stale(old) is True
    assert host._job_is_stale(current) is False
    assert host._job_is_stale(old) is True


def test_model_hash_mismatch_is_still_decided_without_a_snapshot():
    host = _Host()

    assert host._job_is_stale(host.job(model_hash="other")) is True
    assert host.snapshots == 0
