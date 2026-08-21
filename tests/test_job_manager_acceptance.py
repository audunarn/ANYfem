"""Acceptance coverage for immutable queued numerical jobs."""

from __future__ import annotations

from threading import Event

from anyfem.document import DocumentSession
from anyfem.jobs import JobManager
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition, JobStatus


def test_jobs_queue_fifo_keep_both_results_and_stale_only_the_old_revision():
    project = Project("queued jobs")
    session = DocumentSession(project)
    manager = JobManager(project)
    analysis = AnalysisDefinition("Static")
    first_started = Event()
    release_first = Event()

    def solve(*, project, progress, cancellation_token, label):
        progress(f"running {label}")
        if label == "first":
            first_started.set()
            assert release_first.wait(5.0)
        return label, len(project.geometry.vertices)

    first = manager.submit(
        analysis,
        session.snapshot(),
        solve,
        kwargs={"label": "first"},
    )
    assert first_started.wait(5.0)

    # Editing stays enabled while the first immutable snapshot is running.
    with session.transaction("add point"):
        project.geometry.add_point(1.0, 2.0, 3.0)
    second = manager.submit(
        analysis,
        session.snapshot(),
        solve,
        kwargs={"label": "second"},
    )

    assert manager.active_job_id == first.id
    assert manager.queued == (second.id,)
    assert first.status == JobStatus.RUNNING
    assert second.status == JobStatus.QUEUED

    release_first.set()
    assert manager.wait(first.id, timeout=5.0).status == JobStatus.COMPLETED
    assert manager.wait(second.id, timeout=5.0).status == JobStatus.COMPLETED
    assert manager.result(first.id) == ("first", 0)
    assert manager.result(second.id) == ("second", 1)
    assert tuple(project.jobs) == (first.id, second.id)

    current_model_hash = session.revision.model_hash
    analysis_hash = second.analysis_hash
    assert first.stale_against(
        model_hash=current_model_hash,
        mesh_hash="",
        analysis_hash=analysis_hash,
    )
    assert not second.stale_against(
        model_hash=current_model_hash,
        mesh_hash="",
        analysis_hash=analysis_hash,
    )


def test_active_and_queued_jobs_are_cancelled_without_losing_history():
    project = Project("cancel jobs")
    session = DocumentSession(project)
    manager = JobManager(project)
    analysis = AnalysisDefinition("Static")
    started = Event()
    pause = Event()

    def cooperative_solve(*, project, progress, cancellation_token):
        del project
        progress("waiting at a cancellation-safe point")
        started.set()
        while not cancellation_token.is_cancelled:
            pause.wait(0.01)
        cancellation_token.raise_if_cancelled("acceptance test")

    active = manager.submit(analysis, session.snapshot(), cooperative_solve)
    assert started.wait(5.0)
    queued = manager.submit(analysis, session.snapshot(), cooperative_solve)

    assert manager.cancel(queued.id)
    assert manager.wait(queued.id, timeout=5.0).status == JobStatus.CANCELLED
    assert manager.cancel(active.id)
    assert active.status == JobStatus.CANCELLING
    assert manager.wait(active.id, timeout=5.0).status == JobStatus.CANCELLED

    assert tuple(project.jobs) == (active.id, queued.id)
    assert active.diagnostics
    assert not queued.diagnostics
    cancelled_ids = {
        event.job_id for event in manager.poll() if event.kind == "cancelled"
    }
    assert cancelled_ids == {active.id, queued.id}


def test_analysis_label_and_uuid_do_not_change_solver_affecting_hash():
    project = Project("analysis labels")
    session = DocumentSession(project)
    manager = JobManager(project)

    def solve(*, project, progress, cancellation_token):
        del project, progress, cancellation_token
        return "ok"

    first = manager.submit(
        AnalysisDefinition("Engineer label A", settings={"solver": "auto"}),
        session.snapshot(),
        solve,
    )
    assert manager.wait(first.id, timeout=5.0).status == JobStatus.COMPLETED
    second = manager.submit(
        AnalysisDefinition("Engineer label B", settings={"solver": "auto"}),
        session.snapshot(),
        solve,
    )
    assert manager.wait(second.id, timeout=5.0).status == JobStatus.COMPLETED
    assert first.analysis_hash == second.analysis_hash


def test_only_selected_loading_changes_the_analysis_hash():
    project = Project("selected loading hash")
    selected = project.load_case("selected")
    unused = project.load_case("unused")
    analysis = AnalysisDefinition(
        "Static", target_kind="load_case", target_id="selected"
    )
    session = DocumentSession(project)
    manager = JobManager(project)

    def solve(*, project, progress, cancellation_token):
        del project, progress, cancellation_token
        return "ok"

    first = manager.submit(analysis, session.snapshot(), solve)
    assert manager.wait(first.id, timeout=5.0).status == JobStatus.COMPLETED
    baseline_model = session.revision.model_hash

    with session.transaction("edit unused loading"):
        unused.set_acceleration(0.0, 0.0, -9.81)
    second = manager.submit(analysis, session.snapshot(), solve)
    assert manager.wait(second.id, timeout=5.0).status == JobStatus.COMPLETED
    assert session.revision.model_hash == baseline_model
    assert second.analysis_hash == first.analysis_hash

    with session.transaction("edit selected loading"):
        selected.set_acceleration(0.0, 0.0, -9.81)
    third = manager.submit(analysis, session.snapshot(), solve)
    assert manager.wait(third.id, timeout=5.0).status == JobStatus.COMPLETED
    assert session.revision.model_hash == baseline_model
    assert third.analysis_hash != first.analysis_hash
