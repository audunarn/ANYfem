"""Job orchestration follows ANYsolver's common outcome contract."""

from __future__ import annotations

from types import SimpleNamespace

from anysolver import SolveOutcome

from anyfem.document import DocumentSession
from anyfem.jobs import JobManager
from anyfem.model import AnalysisDefinition, JobStatus, Project


def _submit(outcome: SolveOutcome):
    project = Project("outcome")
    manager = JobManager(project)
    analysis = AnalysisDefinition("case", type="linear_static")

    def solve(**_kwargs):
        return SimpleNamespace(outcome=outcome, summary=lambda: "usable result")

    record = manager.submit(analysis, DocumentSession(project).snapshot(), solve)
    manager.wait(record.id, timeout=5.0)
    events = manager.poll()
    return manager, record, events


def test_completed_outcome_finishes_and_persists_termination():
    manager, record, events = _submit(
        SolveOutcome.success("target_load_factor_reached")
    )

    assert record.status is JobStatus.COMPLETED
    assert record.outcome["termination"] == "target_load_factor_reached"
    assert manager.result(record.id).outcome.target_reached
    assert events[-1].kind == "completed"


def test_partial_outcome_retains_usable_result_without_claiming_completion():
    manager, record, events = _submit(
        SolveOutcome.stopped("minimum_increment_reached", has_results=True)
    )

    assert record.status is JobStatus.PARTIAL
    assert record.partial
    assert manager.result(record.id).outcome.has_results
    assert events[-1].kind == "partial"


def test_failed_outcome_is_not_published_as_a_result():
    manager, record, events = _submit(
        SolveOutcome.failure("nonconvergence", has_results=False)
    )

    assert record.status is JobStatus.FAILED
    assert record.outcome["termination"] == "nonconvergence"
    assert events[-1].kind == "failed"
    try:
        manager.result(record.id)
    except KeyError:
        pass
    else:  # pragma: no cover - guards accidental result publication
        raise AssertionError("failed result was published")
