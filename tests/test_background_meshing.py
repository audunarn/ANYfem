"""Desktop acceptance tests for nonblocking meshing and terminal job logs."""

from __future__ import annotations

from pathlib import Path
import threading
import time
import tkinter as tk

import pytest

from anyfem import commands as cmd
from anyfem.io.artifacts import ArtifactStore
from anyfem.model.project import Project
from anyfem.model.records import AnalysisDefinition

pytest.importorskip("anytk3d", reason="the viewport needs ANYfem[gui]")


@pytest.fixture(scope="module")
def root():
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for tkinter")
    window.geometry("1200x800+40+40")
    window.update()
    yield window
    window.destroy()


@pytest.fixture
def app(root):
    from anyfem.ui.app import AnyFemApp

    widget = AnyFemApp(root)
    root.update()
    yield widget
    widget.destroy()
    root.update()


def _plate(app) -> int:
    points = [
        app.run(cmd.AddPoint(x, y))
        for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))
    ]
    face = app.run(cmd.AddPlate(points))
    app.run(cmd.AssignPlate(face, "plate"))
    return face


def _wait(root, predicate, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("background operation did not finish in time")


def test_mesh_panel_submits_nonblocking_and_retains_stale_quality(
    app, root, monkeypatch
):
    _plate(app)
    original = Project.generate_mesh
    entered = threading.Event()
    release = threading.Event()

    def slow_mesh(project, *args, **kwargs):
        entered.set()
        if not release.wait(5.0):
            raise TimeoutError("test did not release the mesher")
        return original(project, *args, **kwargs)

    monkeypatch.setattr(Project, "generate_mesh", slow_mesh)
    panel = app.panels["Mesh"]
    started = time.perf_counter()
    panel._generate()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.25
    assert entered.wait(2.0)
    root.update()
    record = app.project.mesh_records[app._active_mesh_task_id]
    assert record.status == "running"
    assert str(panel._generate_button["state"]) == "disabled"

    # Editing remains available.  The completed mesh belongs to its submitted
    # snapshot and must never be attached to this newer document revision.
    app.run(cmd.AddPoint(2.0, 2.0))
    release.set()
    _wait(root, lambda: record.status == "stale")

    assert app.mesh is None
    assert record.mesh_hash.startswith("sha256:")
    quality = record.summary["quality"]
    assert quality["num_shell_elements"] > 0
    assert quality["max_aspect_ratio"] >= 1.0
    assert quality["max_warp"] >= 0.0
    assert "stale" in panel._stats["text"]


def test_mesh_cancellation_waits_for_safe_phase_without_attaching(
    app, root, monkeypatch
):
    _plate(app)
    original = Project.generate_mesh
    entered = threading.Event()
    release = threading.Event()

    def slow_mesh(project, *args, **kwargs):
        entered.set()
        if not release.wait(5.0):
            raise TimeoutError("test did not release the mesher")
        return original(project, *args, **kwargs)

    monkeypatch.setattr(Project, "generate_mesh", slow_mesh)
    record = app.generate_mesh_async(0.25)
    assert entered.wait(2.0)
    assert app.cancel_mesh()
    root.update()
    assert record.status == "cancelling"
    assert "cancelling" in app.panels["Mesh"]._stats["text"]

    release.set()
    _wait(root, lambda: record.status == "cancelled")
    assert app.mesh is None
    assert not app.mesh_job_running


def test_synchronous_mesh_api_remains_available_and_records_quality(app, root):
    _plate(app)
    mesh = app.generate_mesh(0.25)
    root.update()

    record = app.project.mesh_records[app.mesh_record_id]
    assert mesh is app.mesh
    assert record.status == "completed"
    assert record.summary["quality"]["num_shell_elements"] == len(mesh.shells)


def test_background_mesh_error_is_retained_in_details(app, root, monkeypatch):
    _plate(app)

    def broken_mesh(_project, *_args, **_kwargs):
        raise ValueError("deliberately invalid mapped partition")

    monkeypatch.setattr(Project, "generate_mesh", broken_mesh)
    record = app.generate_mesh_async(0.25)
    _wait(root, lambda: record.status == "failed")

    assert record.mesh_hash == ""
    assert record.diagnostics[-1]["type"] == "ValueError"
    assert "deliberately invalid" in app.panels["Mesh"]._stats["text"]


def test_terminal_job_log_is_written_off_thread_and_survives_save_as(
    app, root, monkeypatch, tmp_path: Path
):
    source = tmp_path / "source.anyfem"
    destination = tmp_path / "copy.anyfem"
    app.save_project(path=str(source))
    analysis = AnalysisDefinition("Log check")

    class Result:
        def summary(self):
            return "done"

    def execute(*, project, progress, cancellation_token=None):
        progress("assembly complete")
        return Result()

    # This acceptance case is about terminal-log storage; result conversion is
    # covered independently by the result-artifact tests.
    monkeypatch.setattr(app, "_on_solved", lambda *_args, **_kwargs: None)
    record = app.job_manager.submit(analysis, app.session.snapshot(), execute)
    _wait(root, lambda: record.log_artifact_id is not None)

    artifact = app.project.artifacts[record.log_artifact_id]
    assert record.log_artifact_id != record.result_artifact_id
    entries = ArtifactStore(source).read_log(artifact)
    assert [entry["kind"] for entry in entries] == [
        "queued",
        "started",
        "progress",
        "completed",
    ]

    app.save_project(path=str(destination))
    copied = app.project.artifacts[record.log_artifact_id]
    assert ArtifactStore(destination).read_log(copied) == entries


def test_recovery_writes_are_nonblocking_and_coalesce_to_latest_revision(
    app, root, monkeypatch
):
    from anyfem.ui import app as app_module

    entered = threading.Event()
    release = threading.Event()
    calls: list[tuple[dict, dict]] = []
    active = 0
    max_active = 0
    lock = threading.Lock()

    def slow_autosave(document, **keywords):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        calls.append((document, keywords))
        entered.set()
        if len(calls) == 1 and not release.wait(5.0):
            raise TimeoutError("test did not release recovery storage")
        with lock:
            active -= 1
        return object()

    monkeypatch.setattr(app_module, "write_autosave", slow_autosave)
    app.run(cmd.AddPoint(0.0, 0.0))
    started = time.perf_counter()
    app._write_recovery()
    elapsed = time.perf_counter() - started
    assert elapsed < 0.25
    assert entered.wait(2.0)

    # A second dirty revision replaces any older pending request.  The writer
    # remains single-threaded, then immediately stores this newest snapshot.
    app.run(cmd.AddPoint(1.0, 0.0))
    latest_revision = app.session.revision.id
    app._write_recovery()
    release.set()
    _wait(
        root,
        lambda: len(calls) == 2
        and app._recovery_future is None
        and app._recovery_pending is None,
    )

    assert max_active == 1
    assert calls[-1][1]["revision_id"] == latest_revision
    vertices = calls[-1][0]["geometry"]["vertices"]
    assert len(vertices) == 2
