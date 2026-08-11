"""Focused desktop acceptance tests for identity, locks and navigation."""

from __future__ import annotations

import json
import socket
import tkinter as tk
from types import SimpleNamespace

import pytest

from anygeometry.entities import EntityRef

from anyfem import commands as cmd
from anyfem.jobs import analysis_hash
from anyfem.io.artifacts import ArtifactStore
from anyfem.io.recovery import LockOwner, ProjectLock
from anyfem.model.attributes import Support
from anyfem.model.project import Project
from anyfem.model.records import (
    AnalysisDefinition,
    ArtifactRef,
    JobRecord,
    JobStatus,
    MeshRecord,
)

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


def test_tree_rows_use_persisted_ids_and_keyboard_shortcuts_are_live(app, root):
    point_id = app.run(cmd.AddPoint(0.0, 0.0, 0.0))
    ref = EntityRef("vertex", point_id)
    support = app.run(cmd.AddSupport(Support("restraint", ref, {"ux": 0.0})))
    mass = app.run(cmd.AddMass(ref, 12.0))
    load = app.run(cmd.AddPointLoad(ref, force=(1.0, 2.0, 3.0)))
    root.update()

    assert app.tree.tree.exists(f"support:{support.id}")
    assert app.tree.tree.exists(f"mass:{mass.id}")
    assert app.tree.tree.exists(f"load:point:{load.id}")
    app.tree.refresh()
    assert app.tree.tree.exists(f"support:{support.id}")
    assert app.tree.tree.exists(f"mass:{mass.id}")
    assert app.tree.tree.exists(f"load:point:{load.id}")

    sequences = {sequence for sequence, _identifier in app._root_bindings}
    assert {"<Control-z>", "<Control-y>"} <= sequences
    app._undo_shortcut()
    assert not app.project.load_case().point_loads
    app._redo_shortcut()
    assert app.project.load_case().point_loads[0].id == load.id


def test_tree_edit_hydrates_actual_support_values_and_delete_is_undoable(app, root):
    first = app.run(cmd.AddPoint(0.0, 0.0, 0.0))
    second = app.run(cmd.AddPoint(0.0, 1.0, 0.0))
    edge = app.run(cmd.AddLine(first, second))
    support = app.run(
        cmd.AddSupport(
            Support("driven edge", EntityRef("edge", edge), {"uy": 0.050})
        )
    )
    root.update()
    key = f"support:{support.id}"

    app._tree_action("edit", (key,))
    root.update()
    panel = app.panels["Loads & BC"]
    assert panel._component_values["uy"].get() == "50"
    assert panel._dofs["uy"].get()
    assert panel._editing_attribute.id == support.id

    panel._component_values["uy"].set("25")
    panel._add_support()
    root.update()
    assert len(app.project.supports) == 1
    assert app.project.supports[0].id == support.id
    assert app.project.supports[0].constraints == {"uy": 0.025}
    assert "uy=25 mm" in app.tree.tree.item(key, "text")

    app._tree_action("delete", (key,))
    root.update()
    assert not app.project.supports
    app.undo()
    root.update()
    assert app.project.supports[0].id == support.id
    assert app.project.supports[0].constraints == {"uy": 0.025}


def test_solve_transcript_has_run_log_and_submitted_inputs_tabs(app, root):
    panel = app.panels["Solve"]
    app.details.select("Solve")
    panel.begin_job(
        "Nonlinear static",
        "117ea193-0000-0000-0000-000000000000",
        '{"constraints_SI": {"uy": 0.05}, "value_mm": 50}',
    )
    root.update()

    assert panel._transcript_tabs.tab(0, "text") == "Run monitor"
    assert panel._transcript_tabs.tab(1, "text") == "Submitted inputs"
    assert "queued" in panel._report.get("1.0", "end")
    inputs = panel._submitted_inputs.get("1.0", "end")
    assert '"uy": 0.05' in inputs
    assert '"value_mm": 50' in inputs

    panel.append_progress(
        "Increment trial 1 | load factor 0.1 / 1 | increment 0.1 | "
        "Newton iteration 1 | residual 2e3"
    )
    panel.append_progress(
        "converged increment 1: load factor 0.1 / target 1 (10%); "
        "max |u| 0.002 m, max PEEQ 0.0001, load increment 0.1"
    )
    panel._live_graph_choice.set("Load factor path")
    panel._refresh_live_plot()
    root.update()
    assert panel._live_plot.series_names == ["Load factor path"]
    assert "converged increment 1" in panel._report.get("1.0", "end")
    assert panel._live_plot.canvas.winfo_height() > 40
    assert panel._report.winfo_height() > 40

    app.active_job_id = "117ea193-0000-0000-0000-000000000000"
    app.submitted_input_reports[app.active_job_id] = inputs
    results = app.panels["Results"]
    results.refresh()
    assert results._readout_tabs.tab(0, "text") == "Path / increments"
    assert results._readout_tabs.tab(1, "text") == "Probe / details"
    assert results._readout_tabs.tab(2, "text") == "Submitted inputs"
    assert '"uy": 0.05' in results._result_inputs.get("1.0", "end")


def test_results_workspace_gives_path_and_text_tabs_useful_height(app, root):
    app.details.select("Results")
    root.update()
    panel = app.panels["Results"]

    assert panel._readout_tabs.winfo_height() >= 180
    assert panel.plot.canvas.winfo_height() >= 95
    assert panel._readout_tabs.tab(0, "text") == "Path / increments"
    assert panel._outcome_values["Start"].get() == "—"


def test_job_and_result_rows_use_uuids_and_show_stale_revision(app, root):
    analysis = AnalysisDefinition("Static")
    with app.session.transaction("analysis", solver_affecting=False):
        app.project.analyses[analysis.id] = analysis
    job = JobRecord(
        analysis_id=analysis.id,
        name="Static job",
        model_hash=app.session.revision.model_hash,
        mesh_hash="",
        analysis_hash=analysis_hash(analysis),
        status=JobStatus.COMPLETED,
        result_artifact_id="01ec0e70-88e8-4eea-98d5-079ea7045ebf",
    )
    with app.session.transaction("job", solver_affecting=False):
        app.project.jobs[job.id] = job
    root.update()

    job_row = f"job:{job.id}"
    result_row = f"result:{job.result_artifact_id}"
    assert app.tree.tree.exists(job_row)
    assert app.tree.tree.exists(result_row)
    assert "stale" not in app.tree.tree.item(job_row, "text")

    app.run(cmd.AddPoint(1.0, 0.0, 0.0))
    root.update()
    assert "stale" in app.tree.tree.item(job_row, "text")
    assert "stale" in app.tree.tree.item(result_row, "text")


def test_opening_a_mesh_only_project_finishes_in_mesh_view(
    app, root, monkeypatch, tmp_path
):
    from anyfem.ui import app as app_module

    artifact = ArtifactRef(
        id="2a442cc2-da37-4a58-aef0-7769462fa3d3",
        kind="mesh",
        uri="meshes/imported.anymesh.h5",
    )
    mesh_record = MeshRecord(
        id="c1b1e6cb-bbf7-4602-a5c1-889052c903f2",
        name="Imported mesh",
        kind="imported",
        source_model_hash="model",
        mesh_input_hash="input",
        mesh_hash="mesh",
        artifact_id=artifact.id,
    )
    loaded = Project("mesh only", mesh_only=True)
    loaded.artifacts[artifact.id] = artifact
    loaded.mesh_records[mesh_record.id] = mesh_record
    sentinel_mesh = SimpleNamespace(
        is_quadratic=False,
        num_nodes=0,
        shells={},
        beams={},
    )
    calls: list[str] = []
    monkeypatch.setattr(app_module, "load_project", lambda _path: loaded)
    monkeypatch.setattr(ArtifactStore, "read_mesh", lambda *_args: sentinel_mesh)
    monkeypatch.setattr(app, "show_geometry", lambda *args, **kwargs: calls.append("geometry"))
    monkeypatch.setattr(app, "show_mesh", lambda: calls.append("mesh"))
    monkeypatch.setattr(app.viewport, "fit", lambda: calls.append("fit"))

    app.open_project(str(tmp_path / "mesh-only.anyfem"))
    root.update()

    assert app.mesh is sentinel_mesh
    assert calls[-2:] == ["mesh", "fit"]


@pytest.mark.parametrize(
    ("answer", "expected_read_only"),
    [(False, True), (True, False)],
)
def test_stale_lock_prompt_can_open_read_only_or_take_over(
    app, root, monkeypatch, tmp_path, answer, expected_read_only
):
    from anyfem.ui import app as app_module

    source = tmp_path / f"locked-{answer}.anyfem"
    lock = ProjectLock(source)
    stale = LockOwner(
        pid=2_147_483_647,
        hostname=socket.gethostname(),
        acquired_utc="2026-08-01T10:00:00Z",
        token="stale-owner",
        process_start="old-process",
    )
    lock.path.write_text(json.dumps(stale.to_dict()), encoding="utf-8")
    prompts: list[str] = []
    monkeypatch.setattr(app_module, "load_project", lambda _path: Project("locked"))
    monkeypatch.setattr(
        app_module.messagebox,
        "askyesnocancel",
        lambda title, _message, **_kwargs: prompts.append(title) or answer,
    )

    app.open_project(str(source))
    root.update()

    assert prompts == ["Stale ANYfem project lock"]
    assert app.session.read_only is expected_read_only
    if answer:
        assert app._project_lock is not None
        assert app._project_lock.inspect().state == "owned"
    else:
        assert app._project_lock is None
        assert lock.inspect().state == "stale"


def test_cancelling_stale_lock_prompt_keeps_current_document(
    app, root, monkeypatch, tmp_path
):
    from anyfem.ui import app as app_module

    source = tmp_path / "cancelled.anyfem"
    lock = ProjectLock(source)
    stale = LockOwner(
        pid=2_147_483_647,
        hostname=socket.gethostname(),
        acquired_utc="2026-08-01T10:00:00Z",
        token="stale-owner",
        process_start="old-process",
    )
    lock.path.write_text(json.dumps(stale.to_dict()), encoding="utf-8")
    original = app.project
    monkeypatch.setattr(
        app_module.messagebox, "askyesnocancel", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        app_module,
        "load_project",
        lambda _path: pytest.fail("cancel must not read or replace the project"),
    )

    app.open_project(str(source))
    root.update()

    assert app.project is original
    assert lock.inspect().state == "stale"
