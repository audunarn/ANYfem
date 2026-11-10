"""Submodel desktop entry point: action, preview/create, background run, cancellation, history.

Real Qt widgets, gated like the other Qt tests (ANYFEM_RUN_QT_TESTS=1).  The first groups
exercise the GUI contracts (thread of each call, state machine, discard on cancel and on
shutdown, transaction on create) with recording stand-ins for staging and publication.
The last test drives a real window end to end: a shell and a solid submodel are cut,
run, saved, reopened, inspected and displayed with the production backends.
"""
import os
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets", allow_module_level=True)
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from anyfem.io import recovery
from anyfem.model.project import Project
from anyfem.model.submodels import SourceReference, SubmodelDefinition
from anyfem.ui.qt import submodel_task as module
from anyfem.ui.qt.app import QtFemWindow
from anyfem.ui.qt.submodel_task import (SKINS, SOLID_FIELDS, SubmodelRequest, SubmodelTask, format_report,
                                        open_submodel_dialog, parse_footprint, parse_point, shutdown_submodel,
                                        solid_field_table, submodel_task)


def close_window(win):
    """Close without the unsaved-changes prompt (a modal dialog would block the test run)."""

    shutdown_submodel(win)
    win.session.mark_saved()
    win.close()
    win._qt_app.processEvents()


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("ANYFEM_JOB_EXECUTOR", "thread")
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    app = QApplication.instance() or QApplication([])
    win = QtFemWindow(None, viewer_backend="software")
    win._qt_app = app
    try:
        yield win
    finally:
        close_window(win)


def settle(window, task, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window._qt_app.processEvents()
        if not task.pump():
            return
        time.sleep(0.005)
    raise AssertionError(f"background work did not finish; state={task.state} message={task.message}")


def definition(name="sub"):
    """A definition ``add_submodel`` accepts: a detached child that owns its ``child_case``."""

    child = Project(name="child")
    child.load_case()  # creates the "default" load case the definition names
    made = SubmodelDefinition(
        name=name, volume={"kind": "box", "lower": [0, 0, 0], "upper": [1, 1, 1], "transform": None},
        target_size=0.1, policy="drive_all", source=SourceReference("job-1", "default"), child_case="default",
        child=child, source_faces={1: (1,)}, cut_faces={"x_min": (1,)})
    made.validate()  # fail here, not inside the code under test, if the helper is ever wrong
    return made


def test_inspect_run_reads_the_identity_as_a_string_and_the_record_as_fields():
    from anyfem.model.submodels import SubmodelRun

    run = SubmodelRun(
        id="run-1", definition_id="def-1", definition_hash="sha256:" + "d" * 64, child_case="default",
        source={"job_id": "job-1", "case": "default", "mesh_selection": "explicit"},
        child={"child_kind": "shell", "model_hash": "m", "loading_hash": "l", "mesh_fingerprint": "f",
               "mesh_artifact": {"id": "mesh-gone"}, "result_artifact": {"id": "result-gone"}},
        identity="sha256:" + "a" * 64, diagnostics={"k": 1}, producer_versions={"job": {"a": "1"}, "run": {"b": "2"}},
        created_utc="2026-11-10T00:00:00+00:00")
    info = module.inspect_run(SimpleNamespace(), {}, run)  # nothing registered: reported, not raised
    assert info["identity"] == run.identity and isinstance(info["identity"], str)
    assert info["record"]["definition_hash"] == run.definition_hash and info["record"]["source_job"] == "job-1"
    assert info["record"]["child_model_hash"] == "m" and info["diagnostics"] == {"k": 1}
    assert info["problems"] == ["a child artifact is no longer registered"]
    run.child.pop("mesh_artifact")
    assert "no child artifact descriptor" in module.inspect_run(SimpleNamespace(), {}, run)["problems"][0]


# ---- parsing ---------------------------------------------------------
def test_point_and_footprint_parsing_refuses_malformed_input():
    assert parse_point("1, 2 3", "p") == (1.0, 2.0, 3.0)
    with pytest.raises(ValueError, match="three coordinates"):
        parse_point("1 2", "p")
    with pytest.raises(ValueError, match="finite"):
        parse_point("1 2 nan", "p")
    with pytest.raises(ValueError, match="four corner points"):
        parse_footprint("0 0 0\n1 0 0\n1 1 0")
    assert len(parse_footprint("0 0 0\n1 0 0\n1 1 0\n0 1 0")) == 4


def test_report_text_lists_carried_dropped_and_skipped_without_geometry():
    from anyfem.submodel_cut import CutReport

    report = CutReport(carried={"supports": 2}, dropped_outside={"loads": 1}, skipped_cases={"wave": "series"},
                       cut_edges={"x_min": 3}, original_boundary_edges=4, notes=("n",), cut=object())
    text = format_report(report)
    assert "supports: 2" in text and "loads: 1" in text and "wave: series" in text and "object" not in text


def test_pressure_skin_is_a_named_skin_not_a_number():
    assert [value for value, _label in SKINS] == [None, "start", "end"]
    assert SubmodelRequest(kind="solid", job_id="j", case="c", target_size=0.1, pressure_skin="end").pressure_skin == "end"
    for bad in ((0.0, 1.0), "middle", 1.0):
        with pytest.raises(ValueError, match="pressure skin"):
            SubmodelRequest(kind="solid", job_id="j", case="c", target_size=0.1, pressure_skin=bad)


def test_solid_field_table_labels_rows_by_stored_cell_ids_and_quadrature_points():
    cells, points = 2, 3
    coordinates = np.arange(cells * points * 3, dtype=float).reshape(cells, points, 3)
    stress = np.arange(cells * points * 9, dtype=float).reshape(cells, points, 3, 3) * 1.5
    weights = np.linspace(0.1, 0.6, cells * points).reshape(cells, points)
    tensor = ["xx", "xy", "xz", "yx", "yy", "yz", "zx", "zy", "zz"]
    result = {
        "recovery_cell_ids": np.array([40, 17]), "recovery_coordinates": coordinates,
        "recovery_stress": stress, "recovery_weights": weights,
        "displacement_node_ids": np.array([5, 3]), "displacement": np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]),
        "solid_fields": {
            "recovery_stress": {"unit": "Pa", "components": tensor, "location": "integration_point"},
            "recovery_weights": {"unit": "m3", "components": ["weight"], "location": "integration_point"},
            "recovery_coordinates": {"unit": "m", "components": ["x", "y", "z"], "location": "integration_point"},
        },
    }
    table = solid_field_table(result, "recovery_stress")
    assert table["headers"] == ["Cell", "Point", "x", "y", "z", *tensor] and table["total"] == cells * points
    row = table["rows"][4]  # cell index 1, point 1
    assert row[:2] == [17, 1]
    assert [float(v) for v in row[2:5]] == list(coordinates[1, 1])
    assert [float(v) for v in row[5:]] == list(stress[1, 1].reshape(9))
    assert [r[0] for r in solid_field_table(result, "recovery_weights")["rows"]] == [40, 40, 40, 17, 17, 17]
    located = solid_field_table(result, "recovery_coordinates")
    assert located["headers"] == ["Cell", "Point", "x", "y", "z"] and [float(v) for v in located["rows"][5][2:]] == list(coordinates[1, 2])
    displacement = solid_field_table(result, "displacement")
    assert displacement["rows"][1][0] == 3 and displacement["headers"] == ["Node", "ux", "uy", "uz"]
    with pytest.raises(ValueError, match="not a stored solid quantity"):
        solid_field_table(result, "mystery")
    result["recovery_cell_ids"] = np.array([1, 2, 3])
    with pytest.raises(ValueError, match="share one"):
        solid_field_table(result, "recovery_stress")


# ---- the installed action --------------------------------------------
def test_analysis_menu_action_opens_the_dialog_and_reuses_it(window):
    action = window._actions["Submodel…"]
    analysis = next(a.menu() for a in window.menuBar().actions() if a.text() == "Analysis")
    assert action in analysis.actions()
    action.trigger()
    dialog = window._submodel_dialog
    assert dialog.isVisible() and dialog.tabs.count() == 3
    assert dialog.task is submodel_task(window)
    action.trigger()
    assert window._submodel_dialog is dialog
    # no completed jobs in a new project: nothing selectable, preview refuses cleanly
    assert dialog.source.currentData() is None
    with pytest.raises(ValueError, match="completed source job"):
        dialog.request()


def test_kind_switch_enables_the_matching_inputs(window):
    window._actions["Submodel…"].trigger()
    dialog = window._submodel_dialog
    assert dialog.policy.currentData() == "drive_all"  # shell default
    assert dialog.box_min.isEnabled() and not dialog.footprint.isEnabled() and not dialog.pressure_skin.isEnabled()
    dialog.kind.setCurrentIndex(dialog.kind.findData("solid"))
    assert dialog.footprint.isEnabled() and dialog.face_id.isEnabled() and not dialog.box_min.isEnabled()
    assert dialog.pressure_skin.isEnabled() and [dialog.pressure_skin.itemData(i) for i in range(3)] == [None, "start", "end"]
    dialog.kind.setCurrentIndex(dialog.kind.findData("shell"))
    assert not dialog.order.isEnabled() or dialog.order.currentData() == 1


# ---- preview and create ----------------------------------------------
def test_preview_is_read_only_and_create_is_one_undoable_transaction(window, monkeypatch):
    task = submodel_task(window)
    made = definition()
    calls = []

    def cut(snapshot, request):
        calls.append(threading.get_ident())
        return SimpleNamespace(definition=made, report=SimpleNamespace(carried={"supports": 1}))

    monkeypatch.setattr(SubmodelTask, "_cut", staticmethod(cut))
    request = SubmodelRequest(kind="shell", job_id="job-1", case="default", target_size=0.1)
    revision = window.session.revision.id
    task.start_preview(request)
    assert task.state == task.PREVIEWING
    settle(window, task)
    assert calls and calls[0] != threading.get_ident()
    assert window.session.revision.id == revision and not window.project.submodels  # preview mutates nothing
    assert "supports: 1" in task.preview_text()

    sequence = window.session.revision.sequence
    new_id = task.create()
    assert window.project.submodels[new_id] is made and window.session.revision.id != revision
    assert window.session.revision.sequence == sequence + 1  # exactly one revision
    assert window.session.commands.undo_label == "add submodel"
    window.undo()
    assert not window.project.submodels and window.session.commands.can_redo
    window.redo()
    assert list(window.project.submodels) == [new_id] and window.project.submodels[new_id] is made
    window.undo()
    assert not window.project.submodels


def test_create_refuses_a_stale_preview(window, monkeypatch):
    task = submodel_task(window)
    monkeypatch.setattr(SubmodelTask, "_cut", staticmethod(
        lambda snapshot, request: SimpleNamespace(definition=definition(), report=SimpleNamespace())))
    task.start_preview(SubmodelRequest(kind="shell", job_id="j", case="c", target_size=0.1))
    settle(window, task)
    with window.session.transaction("unrelated edit"):
        window.project.name = "changed"
    with pytest.raises(ValueError, match="preview again"):
        task.create()
    assert not window.project.submodels


def test_refused_cut_is_reported_not_swallowed(window, monkeypatch):
    from anyfem.submodel import SubmodelError

    def refuse(snapshot, request):
        raise SubmodelError("CUT_NO_CUT_EDGES", "the box contains whole parent faces")

    monkeypatch.setattr(SubmodelTask, "_cut", staticmethod(refuse))
    task = submodel_task(window)
    task.start_preview(SubmodelRequest(kind="shell", job_id="j", case="c", target_size=0.1))
    settle(window, task)
    assert task.preview is None and "CUT_NO_CUT_EDGES" in task.message and task.state == task.IDLE


# ---- background run ---------------------------------------------------
def prepared_task(window, monkeypatch):
    task = submodel_task(window)
    monkeypatch.setattr(SubmodelTask, "_cut", staticmethod(
        lambda snapshot, request: SimpleNamespace(definition=definition(), report=SimpleNamespace())))
    task.start_preview(SubmodelRequest(kind="shell", job_id="j", case="c", target_size=0.1))
    settle(window, task)
    definition_id = task.create()
    monkeypatch.setattr(task, "store", lambda: SimpleNamespace(ensure=lambda: None))
    monkeypatch.setattr(task, "refresh_history", lambda: None)
    return task, definition_id


def test_run_stages_off_thread_and_publishes_on_the_gui_thread(window, monkeypatch):
    task, definition_id = prepared_task(window, monkeypatch)
    seen = {}
    staged = SimpleNamespace(run=SimpleNamespace(id="run-1234567890"), published=False)

    def stage(snap, did, store, *, mesh_record_id=None, cancellation_token=None):
        seen["stage_thread"] = threading.get_ident()
        seen["snap_thread"] = snap.thread_id
        return staged

    def publish(session, item, **_):
        seen["publish_thread"] = threading.get_ident()
        assert item is staged
        return SimpleNamespace(run=item.run, notification_error=None)

    monkeypatch.setattr(module, "stage_submodel_run", stage)
    monkeypatch.setattr(module, "publish_submodel_run", publish)
    task.start_run(definition_id)
    assert task.state == task.RUNNING and task.busy
    with pytest.raises(ValueError, match="already"):
        task.start_run(definition_id)
    settle(window, task)
    assert seen["stage_thread"] != threading.get_ident()
    assert seen["publish_thread"] == seen["snap_thread"] == threading.get_ident()
    assert task.state == task.IDLE and task.last_run_id == "run-1234567890"


def test_cancel_discards_staged_files_and_never_publishes(window, monkeypatch):
    task, definition_id = prepared_task(window, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    staged = SimpleNamespace(run=SimpleNamespace(id="r"), published=False)
    events = []

    def stage(snap, did, store, *, mesh_record_id=None, cancellation_token=None):
        entered.set()
        assert release.wait(10)
        assert cancellation_token.cancelled  # the real token reaches the stager
        return staged

    monkeypatch.setattr(module, "stage_submodel_run", stage)
    monkeypatch.setattr(module, "publish_submodel_run", lambda *a, **k: events.append("publish"))
    monkeypatch.setattr(module, "discard_staged_run", lambda item, project=None: events.append(("discard", item)))
    task.start_run(definition_id)
    assert entered.wait(10)
    assert task.cancel() and "Cancelling" in task.message
    release.set()
    settle(window, task)
    assert events == [("discard", staged)] and task.state == task.IDLE and "cancelled" in task.message


def test_shutdown_with_a_run_in_flight_removes_what_it_staged_and_publishes_nothing(window, monkeypatch, tmp_path):
    task, definition_id = prepared_task(window, monkeypatch)
    leftover = tmp_path / "staged.anyres.h5"
    leftover.write_bytes(b"staged")
    entered, release = threading.Event(), threading.Event()
    published = []
    staged = SimpleNamespace(run=SimpleNamespace(id="r"), files=(leftover,), published=False)

    def stage(snap, did, store, *, mesh_record_id=None, cancellation_token=None):
        entered.set()
        assert release.wait(10)
        return staged

    monkeypatch.setattr(module, "stage_submodel_run", stage)
    monkeypatch.setattr(module, "publish_submodel_run", lambda *a, **k: published.append(a))
    task.start_run(definition_id)
    assert entered.wait(10)
    task.shutdown()
    task.shutdown()  # idempotent
    release.set()
    deadline = time.monotonic() + 10
    while leftover.exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not leftover.exists() and not published and not task.pump()
    with pytest.raises(ValueError, match="shut down"):
        task.start_run(definition_id)


def test_closing_the_window_shuts_the_task_down(window):
    task = submodel_task(window)
    window.session.mark_saved()
    window.close()
    assert task._shut and window._closing


def test_stage_failure_and_publication_refusal_leave_nothing_behind(window, monkeypatch):
    from anyfem.submodel import SubmodelError

    task, definition_id = prepared_task(window, monkeypatch)
    monkeypatch.setattr(module, "stage_submodel_run",
                        lambda *a, **k: (_ for _ in ()).throw(SubmodelError("SOURCE_JOB", "job is partial")))
    task.start_run(definition_id)
    settle(window, task)
    assert "SOURCE_JOB" in task.message and task.state == task.IDLE

    staged = SimpleNamespace(run=SimpleNamespace(id="r"), published=False)
    discarded = []
    monkeypatch.setattr(module, "stage_submodel_run", lambda *a, **k: staged)
    monkeypatch.setattr(module, "publish_submodel_run", lambda *a, **k: (_ for _ in ()).throw(
        SubmodelError("SUBMODEL_INPUTS_CHANGED", "the document changed")))
    monkeypatch.setattr(module, "discard_staged_run", lambda item, project=None: discarded.append(item))
    task.start_run(definition_id)
    settle(window, task)
    assert discarded == [staged] and "SUBMODEL_INPUTS_CHANGED" in task.message


def test_parent_result_without_a_retained_descriptor_is_refused_not_substituted(window):
    task = submodel_task(window)
    window.project.jobs["job-x"] = SimpleNamespace(result_artifact_id=None)
    window.project.submodel_runs["run-x"] = SimpleNamespace(source={"job_id": "job-x"})
    window.solution = object()  # a current solution must never stand in for the historical parent result
    try:
        with pytest.raises(ValueError, match="retains no registered result artifact"):
            task.open_parent_result("run-x")
        with pytest.raises(ValueError, match="not in the project"):
            task.open_parent_result("missing")
        assert "job-x" not in window.result_datasets and window.active_job_id != "job-x"
    finally:
        window.project.jobs.pop("job-x", None)
        window.project.submodel_runs.pop("run-x", None)


def test_run_needs_a_saved_project_for_the_artifact_store(window):
    task = submodel_task(window)
    with pytest.raises(ValueError, match="(Save the project|Choose a created submodel)"):
        task.start_run("missing")


# ---- real window, real backends --------------------------------------
FOOTPRINT = ((0.4, 0.4, 0.0), (0.6, 0.4, 0.0), (0.6, 0.6, 0.0), (0.4, 0.6, 0.0))


def read_inspection(task, run_id, key):
    task.inspect(run_id, key)
    task.wait()
    info = task.inspection
    assert info["run_id"] == run_id and not info["problems"], info["problems"]
    assert info["table"]["key"] == key
    return info


def test_window_cuts_runs_saves_reopens_and_displays_shell_and_solid_submodels(tmp_path, monkeypatch):
    for name in ("anygeometry.shell_detail", "anymesher.structured_box", "anysolid", "anysolver.solid_submodel",
                 "anysolver.submodel_transfer"):
        pytest.importorskip(name)
    from anyfem.document import DocumentSession
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.project_file import load_project, save_project
    from anyfem.io.solid_submodel_artifact import read_solid_mesh, read_solid_result
    from test_submodel_jobs import make_job
    from test_submodel_workflow import PRESSURE, SIZE, global_project

    monkeypatch.setenv("ANYFEM_JOB_EXECUTOR", "thread")
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    app = QApplication.instance() or QApplication([])
    # The public end-to-end plate: 3x3 faces on the grid 0, .3, .7, 1 with pressure, solved and archived.
    project, gfaces = global_project()
    path = tmp_path / "parent.anyfem"
    store = ArtifactStore(path)
    job, record, _parent_mesh, _parent_solution = make_job(DocumentSession(project), store)
    windows = []

    def open_window(opened):
        win = QtFemWindow(opened, viewer_backend="software")
        win._qt_app = app
        windows.append(win)
        win.path = path
        win.mesh_record_id = record.id
        return win

    try:
        window = open_window(project)
        task = submodel_task(window)
        dialog = open_submodel_dialog(window)
        assert dialog.task is task

        # shell: the box (0.2..0.8)^2 lies inside no grid line, so it CUTS all nine parent faces
        dialog.kind.setCurrentIndex(dialog.kind.findData("shell"))
        dialog.source.setCurrentIndex(dialog.source.findData(job.id))
        assert dialog.source.currentData() == job.id and dialog.case.text() == "default"
        dialog.name.setText("crossing")
        dialog.box_min.setText("0.2 0.2 -0.1")
        dialog.box_max.setText("0.8 0.8 0.1")
        dialog.target_size.setText(str(SIZE))
        assert dialog.request().kind == "shell" and dialog.request().policy == "drive_all"
        dialog.preview()
        task.wait(300)
        assert task.preview is not None, task.message
        cut = task.preview[1]
        assert set(cut.definition.cut_faces) == {"x0", "x1", "y0", "y1"}
        assert len(cut.definition.child.geometry.faces) == 9 and cut.report.original_boundary_edges == 0
        assert cut.report.carried["pressures"] == 9 and "pressures: 9" in task.preview_text()
        dialog.create()
        shell_definition = next(d for d in window.project.submodels.values() if d.name == "crossing")
        assert getattr(shell_definition, "child_kind", "shell") == "shell"
        assert dialog.definition_list.currentData() == shell_definition.id
        dialog.run()
        task.wait(300)
        shell_id = task.last_run_id
        assert shell_id in window.project.submodel_runs, task.message

        # solid: every input entered through the dialog, explicit end skin
        dialog.kind.setCurrentIndex(dialog.kind.findData("solid"))
        dialog.name.setText("detail")
        dialog.target_size.setText("0.05")
        dialog.layers.setValue(2)
        dialog.face_id.setText(str(gfaces[1, 1]))
        dialog.footprint.setPlainText("\n".join(" ".join(str(c) for c in p) for p in FOOTPRINT))
        dialog.pressure_skin.setCurrentIndex(dialog.pressure_skin.findData("end"))
        request = dialog.request()
        assert request.pressure_skin == "end" and request.kind == "solid" and request.face_id == gfaces[1, 1]
        dialog.preview()
        task.wait()
        assert task.preview is not None, task.message
        assert "pressures: 1" in task.preview_text()
        dialog.create()
        solid_definition = next(d for d in window.project.submodels.values() if d.name == "detail")
        assert solid_definition.pressure_skin == "end" and solid_definition.child_kind == "solid"
        dialog.run()
        task.wait(300)
        solid_id = task.last_run_id
        assert solid_id in window.project.submodel_runs, task.message
        solid_run = window.project.submodel_runs[solid_id]
        assert solid_run.child["child_kind"] == "solid" and solid_run.child["pressure_skin"] == "end"
        retained = solid_run.diagnostics["retained_pressure"]
        assert retained["skin"] == "end" and retained["value"] == pytest.approx(PRESSURE)
        assert set(retained["traction"]) == {"end"}
        assert np.linalg.norm(retained["traction"]["end"]) == pytest.approx(PRESSURE)

        save_project(window.project, path)
        close_window(window)
        # the product's own open entrypoint (lock, artifact store, result-dataset registration)
        reopened = open_window(None)
        reopened.open_project(str(path))
        reopened.path = path
        reopened.mesh_record_id = record.id
        assert reopened.project is not window.project
        task = submodel_task(reopened)
        assert set(reopened.project.submodel_runs) == {shell_id, solid_id}
        history = open_submodel_dialog(reopened)  # a fresh dialog on the reopened window
        task.wait()
        assert {entry["run"].id: entry["reasons"] for entry in task.history} == {shell_id: (), solid_id: ()}

        shown = []
        original_show = reopened.viewport.show
        monkeypatch.setattr(reopened.viewport, "show",
                            lambda scene, *a, **k: (shown.append(scene), original_show(scene, *a, **k))[1])

        # shell child, through the History tab: select the stored run, read its record and field
        shell_run = reopened.project.submodel_runs[shell_id]
        assert isinstance(shell_run.identity, str)
        history.view_side.setCurrentIndex(history.view_side.findData("child"))
        history.runs.selectRow(next(r for r in range(history.runs.rowCount())
                                    if history.runs.item(r, 0).data(Qt.UserRole) == shell_id))
        task.wait()
        assert history.selected_run() == shell_id and task.inspection["run_id"] == shell_id
        assert not task.inspection["problems"], task.inspection["problems"]
        assert task.inspection["identity"] == shell_run.identity
        assert task.inspection["record"]["definition_hash"] == shell_run.definition_hash
        assert f"Identity: {shell_run.identity}" in history.details.toPlainText()
        assert history.field.count() > 0 and "displacement" in [history.field.itemText(i)
                                                                 for i in range(history.field.count())]
        history.field.setCurrentText("displacement")
        history._field_chosen()
        task.wait()
        assert task.inspection["table"]["key"] == "displacement"
        assert history.table.values.rows == task.inspection["table"]["rows"] and history.table.values.rows

        # shell child: stored displacement rows against the stored result
        info = read_inspection(task, shell_id, "displacement")
        assert info["child_kind"] == "shell"
        shell_mesh = store.read_mesh(reopened.project.artifacts[shell_run.child["mesh_artifact"]["id"]])
        assert info["mesh"]["nodes"] == len(shell_mesh.nodes)
        dataset = store.open_result(reopened.project.artifacts[shell_run.child["result_artifact"]["id"]])
        stored = np.asarray(dataset.field("displacement").read(), dtype=float)
        node_ids = np.asarray(dataset.table("displacement_node_ids")).reshape(-1)
        rows = info["table"]["rows"]
        assert len(rows) == len(node_ids) and np.abs(stored).max() > 0.0
        for row, node, values in zip(rows, node_ids, stored[0]):
            assert row[0] == int(node) and [float(v) for v in row[1:]] == pytest.approx(values, rel=1e-8, abs=1e-30)
        dataset.close()
        task.show_child_mesh(shell_id)
        assert shown[-1].faces
        assert sum(len(patch.polygons) for patch in shown[-1].faces) == len(shell_mesh.quads) + len(shell_mesh.tris)
        history.view_side.setCurrentIndex(history.view_side.findData("parent"))  # the Results workspace
        assert reopened.active_job_id == job.id and "opened in the Results workspace" in history.details.toPlainText()
        history.view_side.setCurrentIndex(history.view_side.findData("child"))
        assert task.open_parent_result(shell_id) == job.id and reopened.active_job_id == job.id

        # solid child: stored topology, stored quadrature and cell ids
        solid_run = reopened.project.submodel_runs[solid_id]
        mesh = read_solid_mesh(store, reopened.project.artifacts[solid_run.child["mesh_artifact"]["id"]])
        result = read_solid_result(store, reopened.project.artifacts[solid_run.child["result_artifact"]["id"]])
        info = read_inspection(task, solid_id, "displacement")
        assert info["child_kind"] == "solid"
        assert [f["key"] for f in info["fields"]] == list(SOLID_FIELDS)
        assert info["mesh"] == {"nodes": len(mesh.node_ids), "cells": len(mesh.cell_ids),
                                "boundary_facets": len(mesh.boundary_ids)}
        assert len(info["table"]["rows"]) == len(mesh.node_ids) and np.abs(result["displacement"]).max() > 0.0
        assert {row[0] for row in info["table"]["rows"]} == set(map(int, mesh.node_ids))
        for row, node, values in zip(info["table"]["rows"], result["displacement_node_ids"], result["displacement"]):
            assert row[0] == int(node) and [float(v) for v in row[1:]] == pytest.approx(values, rel=1e-8, abs=1e-30)

        info = read_inspection(task, solid_id, "recovery_stress")
        table = info["table"]
        cells, per_cell = result["recovery_coordinates"].shape[:2]
        assert table["total"] == len(table["rows"]) == cells * per_cell and np.abs(result["recovery_stress"]).max() > 0.0
        assert {row[0] for row in table["rows"]} == set(map(int, mesh.cell_ids))
        for index, row in enumerate(table["rows"]):
            cell, point = divmod(index, per_cell)
            assert row[:2] == [int(result["recovery_cell_ids"][cell]), point]
            assert [float(v) for v in row[2:5]] == pytest.approx(result["recovery_coordinates"][cell, point], rel=1e-8, abs=1e-30)
            assert [float(v) for v in row[5:]] == pytest.approx(result["recovery_stress"][cell, point].reshape(9),
                                                                rel=1e-8, abs=1e-12)
        weights = read_inspection(task, solid_id, "recovery_weights")["table"]["rows"]
        assert sum(float(row[-1]) for row in weights) == pytest.approx(0.2 * 0.2 * 0.01, rel=1e-6)
        force = read_inspection(task, solid_id, "interface_force")["table"]["rows"]
        assert [float(v) for v in force[0][1:]] == pytest.approx(result["interface_force"], rel=1e-8, abs=1e-12)

        task.show_child_mesh(solid_id)
        polygons = [polygon for patch in shown[-1].faces for polygon in patch.polygons]
        assert len(polygons) == len(mesh.boundary_ids)
        assert np.allclose(polygons[0], mesh.coordinates[mesh.boundary_facets[0]])
        assert task.open_parent_result(solid_id) == job.id and reopened.active_job_id == job.id
    finally:
        for win in windows:
            close_window(win)
