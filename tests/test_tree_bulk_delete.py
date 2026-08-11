"""Bulk-delete acceptance for the extended model tree."""

from __future__ import annotations

import tkinter as tk

import pytest

from anyfem import Project, commands as cmd, steel
from anyfem.document import DocumentSession
from anyfem.model.records import (
    AnalysisDefinition,
    JobRecord,
    JobStatus,
    MeshRecord,
)


def _mesh(name: str) -> MeshRecord:
    return MeshRecord(
        name=name,
        source_model_hash="model",
        mesh_input_hash="settings",
        mesh_hash=f"hash-{name}",
        status="stale",
    )


def test_multiple_mesh_records_delete_as_one_undo_item():
    project = Project(name="bulk")
    records = [_mesh(f"Mesh {index}") for index in range(4)]
    for record in records:
        project.add_mesh_record(record)
    session = DocumentSession(project)

    session.execute_many(
        [cmd.DeleteProjectRecord("mesh", item.id) for item in records[:3]],
        label="delete 3 meshes",
        solver_affecting=False,
    )
    assert tuple(project.mesh_records) == (records[3].id,)
    assert session.commands.undo_label == "delete 3 meshes"
    assert session.undo()
    assert set(project.mesh_records) == {item.id for item in records}
    assert session.redo()
    assert tuple(project.mesh_records) == (records[3].id,)


def test_bulk_delete_rolls_back_when_one_definition_has_dependencies():
    project = Project(name="definitions")
    project.add_material(steel("S355", 0.010))
    project.add_material(steel("S275", 0.010))
    project.add_plate_section("plate", 0.010, "S355")
    session = DocumentSession(project)

    with pytest.raises(ValueError, match="used by"):
        session.execute_many(
            [
                cmd.DeleteProjectRecord("material", "S275"),
                cmd.DeleteProjectRecord("material", "S355"),
            ],
            label="delete materials",
        )
    assert set(project.materials) == {"S355", "S275"}
    assert not session.commands.can_undo


def test_analysis_with_retained_job_fails_closed():
    project = Project(name="jobs")
    analysis = project.add_analysis(AnalysisDefinition("Static"))
    project.add_job(JobRecord(analysis_id=analysis.id, name="Static job"))

    with pytest.raises(ValueError, match="delete those jobs first"):
        cmd.DeleteProjectRecord("analysis", analysis.id).do(project)
    assert analysis.id in project.analyses


def test_real_tree_context_delete_uses_every_highlighted_mesh_row():
    from anyfem.ui.app import AnyFemApp

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for tkinter")
    app = AnyFemApp(root)
    try:
        records = [app.run(cmd.AddMeshRecord(_mesh(f"Mesh {index}"))) for index in range(4)]
        root.update()
        selected = tuple(f"mesh:{item.id}" for item in records[:3])
        app.tree.tree.selection_set(selected)
        app._tree_action("delete", tuple(app.tree.tree.selection()))
        root.update()

        assert tuple(app.project.mesh_records) == (records[3].id,)
        assert "deleted 3 selected mesh" in app._status.cget("text")
        app.undo()
        assert set(app.project.mesh_records) == {item.id for item in records}
    finally:
        app.destroy()
        root.update()
        root.destroy()


def test_tree_delete_analysis_removes_completed_jobs_and_results_atomically():
    from anyfem.ui.app import AnyFemApp

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for tkinter")
    app = AnyFemApp(root)
    try:
        analysis = app.project.add_analysis(AnalysisDefinition("Nonlinear static"))
        job = app.project.add_job(
            JobRecord(
                analysis_id=analysis.id,
                name="Nonlinear static job",
                status=JobStatus.COMPLETED,
                result_artifact_id="result-artifact",
            )
        )
        app.refresh_all()
        root.update()

        app.tree.tree.selection_set(f"analysis:{analysis.id}")
        app._tree_action("delete", tuple(app.tree.tree.selection()))
        root.update()

        assert analysis.id not in app.project.analyses
        assert job.id not in app.project.jobs
        assert "dependent job(s)/result(s)" in app._status.cget("text")
        app.undo()
        assert analysis.id in app.project.analyses
        assert job.id in app.project.jobs
        assert app.project.jobs[job.id].result_artifact_id == "result-artifact"
    finally:
        app.destroy()
        root.update()
        root.destroy()


def test_tree_delete_analysis_and_selected_child_job_does_not_duplicate_command():
    from anyfem.ui.app import AnyFemApp

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for tkinter")
    app = AnyFemApp(root)
    try:
        analysis = app.project.add_analysis(AnalysisDefinition("Static"))
        job = app.project.add_job(
            JobRecord(
                analysis_id=analysis.id,
                name="Static job",
                status=JobStatus.COMPLETED,
            )
        )
        app.refresh_all()
        root.update()

        # Child first deliberately exercises Treeview ordering independently
        # of the cascade order.
        keys = (f"job:{job.id}", f"analysis:{analysis.id}")
        app._delete_tree_items(keys)

        assert analysis.id not in app.project.analyses
        assert job.id not in app.project.jobs
        app.undo()
        assert analysis.id in app.project.analyses
        assert job.id in app.project.jobs
    finally:
        app.destroy()
        root.update()
        root.destroy()


def test_tree_delete_analysis_refuses_to_remove_a_running_job():
    from anyfem.ui.app import AnyFemApp

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for tkinter")
    app = AnyFemApp(root)
    try:
        analysis = app.project.add_analysis(AnalysisDefinition("Running"))
        job = app.project.add_job(
            JobRecord(
                analysis_id=analysis.id,
                name="Running job",
                status=JobStatus.RUNNING,
            )
        )
        app.refresh_all()
        root.update()

        app.tree.tree.selection_set(f"analysis:{analysis.id}")
        app._tree_action("delete", tuple(app.tree.tree.selection()))
        root.update()

        assert analysis.id in app.project.analyses
        assert job.id in app.project.jobs
        assert "cannot delete a running job" in app._status.cget("text")
    finally:
        app.destroy()
        root.update()
        root.destroy()
