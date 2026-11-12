"""Submodelling workspace panel: both modes, stale-preview rules, replace/delete, box preview, lifecycles.

Real Qt widgets, gated like the other Qt tests (ANYFEM_RUN_QT_TESTS=1).  Cuts are stand-ins (the
heavy backends are covered by test_qt_submodel_task.py and the workflow tests); the widget logic,
task state machine and undoable commands are real.  ``anyfem.submodel_design`` and
``anyfem.ui.qt.submodel_child_editor`` are provided by the integrator: when absent, minimal stand-ins
with the documented signatures are installed for the test only.
"""
import dataclasses
import os
import sys
import types
from types import SimpleNamespace

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets", allow_module_level=True)
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from anyfem.commands import Command
from anyfem.io import recovery
from anyfem.model.project import Project
from anyfem.model.solid_submodels import SolidSubmodelDefinition
from anyfem.model.submodels import SourceReference, SubmodelDefinition
from anyfem.ui.qt import submodel_task as task_module
from anyfem.ui.qt import submodel_workspace as ws
from anyfem.ui.qt.app import QtFemWindow
from anyfem.ui.qt.submodel_task import SubmodelRequest, SubmodelTask, shutdown_submodel, submodel_task

FOOT = ((0.2, 0.2, 0.0), (0.6, 0.2, 0.0), (0.6, 0.5, 0.0), (0.2, 0.5, 0.0))
THICKNESS = 0.01


# ---- stand-ins for integrator-provided modules -----------------------
class _Replace(Command):
    label = "replace submodel"

    def __init__(self, previous, replacement):
        self.previous, self.replacement = previous, replacement

    def do(self, project):
        project.submodels[self.previous.id] = self.replacement

    def undo(self, project):
        project.submodels[self.previous.id] = self.previous


class _Remove(Command):
    label = "remove submodel"

    def __init__(self, previous):
        self.previous = previous

    def do(self, project):
        del project.submodels[self.previous.id]

    def undo(self, project):
        project.submodels[self.previous.id] = self.previous


def _rectangle(parent, face_id, lower, upper):
    return ((lower[0], lower[1], 0.0), (upper[0], lower[1], 0.0), (upper[0], upper[1], 0.0), (lower[0], upper[1], 0.0))


@pytest.fixture
def design(monkeypatch):
    try:
        import anyfem.submodel_design as module
    except ImportError:
        module = types.ModuleType("anyfem.submodel_design")
        module.ReplaceSubmodelDefinition, module.RemoveSubmodelDefinition = _Replace, _Remove
        monkeypatch.setitem(sys.modules, "anyfem.submodel_design", module)
        import anyfem

        monkeypatch.setattr(anyfem, "submodel_design", module, raising=False)
    # the geometry service is the owner's; the panel only needs a deterministic rectangle here
    monkeypatch.setattr(module, "solid_box_footprint", _rectangle, raising=False)
    return module


@pytest.fixture
def editor(monkeypatch):
    calls = []
    try:
        import anyfem.ui.qt.submodel_child_editor as module
    except ImportError:
        module = types.ModuleType("anyfem.ui.qt.submodel_child_editor")
        monkeypatch.setitem(sys.modules, "anyfem.ui.qt.submodel_child_editor", module)
    monkeypatch.setattr(module, "open_shell_child_editor", lambda app, definition_id: calls.append((app, definition_id)),
                        raising=False)
    return calls


def shell_definition(name="shell", job="job-1"):
    child = Project(name="child")
    child.load_case()
    made = SubmodelDefinition(
        name=name, volume={"kind": "box", "lower": [0, 0, 0], "upper": [1, 1, 1], "transform": None},
        target_size=0.1, policy="drive_all", source=SourceReference(job, "default"), child_case="default",
        child=child, source_faces={1: (1,)}, cut_faces={"x_min": (1,)})
    made.validate()
    return made


def solid_definition(name="solid", job="job-1", face_id=7, layers=2, skin="end", footprint=FOOT, size=0.05, order=1,
                     identifier=None):
    extra = {} if identifier is None else {"id": identifier}
    made = SolidSubmodelDefinition(
        name=name, source=SourceReference(job, "default"), face_id=face_id, footprint=footprint, target_size=size,
        layers=layers, order=order, pressure_skin=skin,
        geometry={"model_id": "m", "revision": 1, "checksum": "c"},
        reconstruction={"director": (0.0, 0.0, 1.0), "thickness": THICKNESS, "reference_offset": 0.0,
                        "section": {"id": "s", "name": "S"}, "material": {"name": "steel", "elastic_modulus": 2.0e11,
                                                                      "poisson_ratio": 0.3},
                        "pressure": {"value": None if skin is None else 1000.0}, "boundary_tolerance": 1e-6}, **extra)
    made.validate()
    return made


def fake_cut(snapshot, request):
    if request.kind == "solid":
        made = solid_definition(name=request.name, job=request.job_id, face_id=request.face_id, layers=request.layers,
                                skin=request.pressure_skin, footprint=request.footprint, size=request.target_size,
                                order=request.order)
    else:
        made = shell_definition(name=request.name, job=request.job_id)
    return SimpleNamespace(definition=made, report=SimpleNamespace(carried={"pressures": 1}))


# ---- fixtures --------------------------------------------------------
class FakeBox:
    def __init__(self, center, half, orientation=(0.0, 0.0, 0.0, 1.0), enabled=True):
        point = lambda v: SimpleNamespace(to_tuple=lambda v=v: tuple(v))
        self.center, self.half_extents = point(center), point(half)
        self.orientation, self.enabled = tuple(orientation), enabled


class FakeCanvas:
    def __init__(self):
        self.box = None
        self.draws = 0
        self.log = []

    def export_view_state(self):
        return SimpleNamespace(section_box=self.box, section_plane=None)

    def set_section_box(self, center, half, orientation=(0.0, 0.0, 0.0, 1.0), enabled=True):
        self.log.append(("set", tuple(center), tuple(half), tuple(orientation), enabled))
        self.box = FakeBox(center, half, orientation, enabled)

    def clear_section_box(self):
        self.log.append(("clear",))
        self.box = None

    def redraw(self):
        self.draws += 1


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("ANYFEM_JOB_EXECUTOR", "thread")
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    app = QApplication.instance() or QApplication([])
    # one completed source job without putting a fake job record into the window's project
    monkeypatch.setattr(ws, "source_choices",
                        lambda project: [("job-1", "Static · case default · completed", "default", None)])
    win = QtFemWindow(None, viewer_backend="software")
    win._qt_app = app
    try:
        yield win
    finally:
        shutdown_submodel(win)
        win.session.mark_saved()
        win.close()
        app.processEvents()


@pytest.fixture
def panel(window, monkeypatch, design):
    monkeypatch.setattr(SubmodelTask, "_cut", staticmethod(fake_cut))
    # registration is the backend's business (covered elsewhere); the panel only needs it to store the definition
    monkeypatch.setattr(task_module, "add_submodel",
                        lambda project, definition: (definition.validate(),
                                                     project.submodels.__setitem__(definition.id, definition)))
    monkeypatch.setattr(ws, "plate_choices", lambda project: [(7, "Deck · face 7 · t 0.01")])
    monkeypatch.setattr(ws, "plate_thickness", lambda project, face: THICKNESS)
    canvas = FakeCanvas()
    monkeypatch.setattr(ws.SubmodelWorkspace, "_canvas", lambda self: canvas)
    workspace = ws.SubmodelWorkspace(window)
    workspace.fake_canvas = canvas
    workspace.activate()
    try:
        yield workspace
    finally:
        workspace.dispose()


def settle(workspace):
    workspace.task.wait(30)
    QApplication.instance().processEvents()


def add_solid(window, **kwargs):
    made = solid_definition(**kwargs)
    window.project.submodels[made.id] = made
    return made


def stored_run(definition_id, identifier="run-1"):
    """A real run record (a fake object would not survive the document's own serialisation)."""

    from anyfem.model.submodels import SubmodelRun

    return SubmodelRun(
        id=identifier, definition_id=definition_id, definition_hash="sha256:" + "d" * 64, child_case="default",
        source={"job_id": "job-1", "case": "default", "mesh_selection": "explicit"},
        child={"child_kind": "solid", "model_hash": "m", "loading_hash": "l", "mesh_fingerprint": "f",
               "mesh_artifact": {"id": "mesh-gone"}, "result_artifact": {"id": "result-gone"}},
        identity="sha256:" + "a" * 64, diagnostics={}, producer_versions={"job": {}, "run": {}},
        created_utc="2026-11-10T00:00:00+00:00")


# ---- construction and lifecycle --------------------------------------
def test_workspace_builds_before_the_window_state_exists_and_refresh_is_inert():
    app = QApplication.instance() or QApplication([])
    workspace = ws.SubmodelWorkspace(SimpleNamespace())  # no project, session, viewport, guarded, toolbar or history
    workspace.refresh()
    workspace.activate()
    assert workspace.status.text() == "" and not workspace.preview_button.isEnabled()
    assert workspace._task is None
    workspace.dispose()
    workspace.dispose()  # idempotent
    app.processEvents()


def test_registered_widget_is_lazy_then_binds_the_shared_task_once(window, design):
    workspace = ws.SubmodelWorkspace(window)
    assert workspace._task is None and not workspace._live
    workspace.refresh()  # not shown: only remembers that it is out of date
    assert workspace._task is None and workspace._dirty
    workspace.activate()
    task = submodel_task(window)
    assert workspace.task is task and task.listeners.count(workspace._on_task) == 1
    workspace.activate()
    assert task.listeners.count(workspace._on_task) == 1
    workspace.dispose()
    assert workspace._on_task not in task.listeners
    assert workspace.source.count() == 1 and workspace.source.currentData() == "job-1"


def test_global_refresh_does_not_churn_history_and_hidden_panel_does_nothing(window, design, monkeypatch):
    calls = []
    monkeypatch.setattr(SubmodelTask, "refresh_history", lambda self: calls.append(1))
    workspace = ws.SubmodelWorkspace(window)
    workspace.refresh()
    assert calls == []  # hidden: nothing
    workspace.activate()
    assert calls == [1]
    for _ in range(5):
        workspace.refresh()
    assert calls == [1]  # same project, revision and runs: no history work
    with window.session.transaction("unrelated"):
        window.project.name = "renamed"
    workspace.refresh()
    assert calls == [1, 1]  # a committed edit may change staleness: once
    workspace.deactivate()
    with window.session.transaction("unrelated 2"):
        window.project.name = "renamed again"
    workspace.refresh()
    assert calls == [1, 1] and workspace._dirty
    workspace.dispose()


# ---- shell cut ---------------------------------------------------------
def test_shell_cut_preview_create_run_selection_through_the_panel(panel, window, monkeypatch):
    assert panel.kind() == "shell" and panel.mode.itemText(0) == "Shell cut" and panel.mode.itemText(1) == "Shell–solid boundary"
    assert not panel.solid_group.isVisibleTo(panel) and panel.shell_group.isVisibleTo(panel)
    panel.name.setText("crossing")
    panel._set_box((0.2, 0.2, -0.1), (0.8, 0.8, 0.1))
    request = panel.request()
    assert request.kind == "shell" and request.lower == (0.2, 0.2, -0.1) and request.upper == (0.8, 0.8, 0.1)
    assert request.job_id == "job-1" and request.case == "default" and request.policy == "drive_all"
    with pytest.raises(ValueError, match="Preview first"):
        panel.create()
    panel.preview()
    assert panel.task.state == "previewing" and not panel.preview_button.isEnabled()
    settle(panel)
    assert panel.task.preview is not None and panel.create_button.isEnabled()
    assert "pressures: 1" in panel.report.toPlainText()
    definition_id = panel.create()
    assert window.project.submodels[definition_id].name == "crossing"
    assert panel.selected_id() == definition_id and not panel.create_button.isEnabled()
    assert panel.edit_button.isEnabled() and not panel.delete_button.isEnabled()  # shells are not deleted here

    started = []
    monkeypatch.setattr(SubmodelTask, "start_run", lambda self, did: started.append(did))
    panel.run_selected()
    assert started == [definition_id]
    window.undo()
    panel.refresh()
    assert panel.child_list.count() == 0
    with pytest.raises(ValueError, match="Create a submodel first"):
        panel.run_selected()


def test_shell_edit_calls_the_integrator_editor(panel, window, editor):
    made = shell_definition("shell")
    window.project.submodels[made.id] = made
    panel.reload_lists()
    panel.edit_selected()
    assert editor == [(window, made.id)] and panel._editing_id is None


# ---- shell-solid boundary ----------------------------------------------
def test_solid_mode_uses_a_readable_plate_default_and_never_asks_for_face_ids_or_coordinates(panel, window):
    panel.mode.setCurrentIndex(panel.mode.findData("solid"))
    assert panel.solid_group.isVisibleTo(panel) and not panel.shell_group.isVisibleTo(panel)
    assert panel.plate.count() == 1 and panel.plate.currentText() == "Deck · face 7 · t 0.01"
    assert panel.plate.currentData() == 7  # the only plate is the default
    panel.name.setText("detail")
    panel._set_box((0.2, 0.2, -0.05), (0.6, 0.5, 0.05))
    panel.layers.setValue(2)
    panel.pressure_skin.setCurrentIndex(panel.pressure_skin.findData("end"))
    request = panel.request()
    assert (request.kind, request.face_id, request.layers, request.pressure_skin) == ("solid", 7, 2, "end")
    assert request.footprint == _rectangle(None, 7, (0.2, 0.2, -0.05), (0.6, 0.5, 0.05))
    panel.preview()
    settle(panel)
    assert panel.task.preview is not None
    body, caption = panel.design_body()
    assert body["thickness"] == THICKNESS and "thickness 0.01" in caption and caption.startswith("Previewed")
    panel.refresh_design_body()
    assert panel.design_diagram.body is body or panel.design_diagram.body["thickness"] == THICKNESS
    definition_id = panel.create()
    created = window.project.submodels[definition_id]
    assert created.child_kind == "solid" and created.face_id == 7 and created.pressure_skin == "end"
    # the selected solid shows its physical body next to the run controls
    assert panel.child_diagram.isVisibleTo(panel) and panel.child_diagram.body["thickness"] == THICKNESS
    assert "thickness 0.01" in panel.child_diagram.note and "layers" in panel.child_summary.text()


def test_without_a_preview_the_diagram_shows_box_intersection_plate_and_thickness(panel):
    panel.mode.setCurrentIndex(panel.mode.findData("solid"))
    panel._set_box((0.0, 0.0, -0.1), (1.0, 0.5, 0.1))
    body, caption = panel.design_body()
    assert caption.startswith("Box ∩ plate") and body["thickness"] == THICKNESS
    points = panel.design_diagram.set_body(body, caption) or panel.design_diagram.geometry_points()
    assert len(points) == 7
    panel.design_diagram.resize(240, 140)
    panel.design_diagram.grab()  # painting must not raise


def test_a_box_that_misses_the_plate_is_reported_in_the_diagram_not_raised(panel, monkeypatch, design):
    def refuse(*_):
        raise ValueError("the box does not cut the plate")

    monkeypatch.setattr(design, "solid_box_footprint", refuse)
    panel.mode.setCurrentIndex(panel.mode.findData("solid"))
    panel.refresh_design_body()
    assert panel.design_diagram.body is None and "does not cut the plate" in panel.design_diagram.note
    with pytest.raises(ValueError, match="does not cut the plate"):
        panel.preview()


# ---- stale previews ------------------------------------------------------
@pytest.mark.parametrize("edit", [
    lambda p: p.name.setText("other"),
    lambda p: p.target_size.setValue(0.2),
    lambda p: p.box_max[0].setValue(2.0),
    lambda p: p.policy.setCurrentIndex(1),
    lambda p: p.mode.setCurrentIndex(1),
])
def test_every_form_edit_invalidates_a_ready_preview(panel, edit):
    panel.preview()
    settle(panel)
    assert panel.task.preview is not None and panel.create_button.isEnabled()
    edit(panel)
    assert panel.task.preview is None and not panel.create_button.isEnabled()
    with pytest.raises(ValueError, match="Preview first"):
        panel.create()


def test_solid_field_edits_invalidate_and_an_edit_during_the_preview_discards_its_result(panel, window):
    panel.mode.setCurrentIndex(panel.mode.findData("solid"))
    panel.preview()
    settle(panel)
    for edit in (lambda: panel.layers.setValue(3), lambda: panel.order.setCurrentIndex(1),
                 lambda: panel.pressure_skin.setCurrentIndex(2)):
        panel.preview()
        settle(panel)
        assert panel.task.preview is not None
        edit()
        assert panel.task.preview is None
    panel.preview()
    assert panel.task.state == "previewing"
    panel.layers.setValue(5)  # edited while the cut runs
    settle(panel)
    assert panel.task.preview is None and not window.project.submodels
    assert panel.preview_button.isEnabled() and not panel.create_button.isEnabled()


def test_a_document_change_or_form_mismatch_cannot_create(panel, window):
    panel.preview()
    settle(panel)
    with window.session.transaction("unrelated edit"):
        window.project.name = "changed"
    panel.refresh()
    assert panel.task.preview is None  # dropped by the global refresh, not only refused at create
    panel.preview()
    settle(panel)
    # a request that differs from the previewed one (change that bypassed the signals) is refused
    panel.task.preview_request = dataclasses.replace(panel.task.preview_request, target_size=9.0)
    with pytest.raises(ValueError, match="preview again"):
        panel.create()
    assert panel.task.preview is None and not window.project.submodels


# ---- replace and delete ----------------------------------------------------
def test_editing_a_solid_prefills_previews_and_replaces_keeping_id_source_and_runs(panel, window):
    made = add_solid(window, name="detail", layers=2, skin="end", size=0.05, order=2)
    window.project.submodel_runs["run-1"] = stored_run(made.id)
    panel.reload_lists()
    assert panel.selected_id() == made.id
    panel.edit_selected()
    assert panel._editing_id == made.id and panel.kind() == "solid"
    assert panel.name.text() == "detail" and panel.layers.value() == 2 and panel.order.currentData() == 2
    assert panel.pressure_skin.currentData() == "end" and panel.target_size.value() == pytest.approx(0.05)
    assert panel.plate.currentData() == 7
    lower, upper = panel.box()
    assert lower == pytest.approx((0.2, 0.2, -THICKNESS / 2)) and upper == pytest.approx((0.6, 0.5, THICKNESS / 2))
    assert not panel.mode.isEnabled() and not panel.source.isEnabled()
    assert panel.replace_button.isVisibleTo(panel) and not panel.create_button.isVisibleTo(panel)
    assert not panel.replace_button.isEnabled()  # replace needs an explicit preview of the change
    with pytest.raises(ValueError, match="use Replace"):
        panel.create()
    with pytest.raises(ValueError, match="Preview first"):
        panel.replace()

    panel.layers.setValue(4)
    panel.preview()
    settle(panel)
    assert panel.replace_button.isEnabled()
    revision = window.session.revision.sequence
    assert panel.replace() == made.id
    now = window.project.submodels[made.id]
    assert list(window.project.submodels) == [made.id] and now is not made
    assert now.layers == 4 and now.source == made.source and now.id == made.id
    assert window.session.revision.sequence == revision + 1
    assert "run-1" in window.project.submodel_runs and panel._editing_id is None
    assert panel.mode.isEnabled() and not panel.create_button.isHidden() and panel.current_tab() == "children"
    window.undo()
    # undo reinstalls the stored record (a new object): compare the serialisation, not the identity
    assert window.project.submodels[made.id].to_dict() == made.to_dict() and "run-1" in window.project.submodel_runs


def test_edit_refuses_a_missing_plate_or_source_and_cancel_edit_returns_to_create(panel, window, monkeypatch):
    made = add_solid(window, name="gone", face_id=99)
    panel.reload_lists()
    with pytest.raises(ValueError, match="no longer in the model"):
        panel.edit_selected()
    assert panel._editing_id is None
    window.project.submodels.pop(made.id)
    other = add_solid(window, name="orphan", job="job-missing")
    panel.reload_lists()
    with pytest.raises(ValueError, match="source job"):
        panel.edit_selected()
    window.project.submodels.pop(other.id)
    ok = add_solid(window, name="ok")
    panel.reload_lists()
    panel.edit_selected()
    panel.cancel_edit()
    assert panel._editing_id is None and panel.mode.isEnabled() and not panel.replace_button.isVisibleTo(panel)


def test_delete_asks_first_is_undoable_and_keeps_stored_runs(panel, window):
    made = add_solid(window, name="detail")
    window.project.submodel_runs["run-1"] = stored_run(made.id)
    panel.reload_lists()
    asked = []
    window.dialogs.confirm = lambda title, text: (asked.append(text), False)[1]
    panel.delete_selected()
    assert asked and made.id in window.project.submodels
    window.dialogs.confirm = lambda title, text: True
    panel.delete_selected()
    assert made.id not in window.project.submodels and "run-1" in window.project.submodel_runs
    assert panel.child_list.count() == 0
    window.undo()
    assert window.project.submodels[made.id].to_dict() == made.to_dict()


def test_read_only_documents_can_preview_but_not_create_replace_delete_or_run(panel, window, monkeypatch):
    made = add_solid(window, name="detail")
    panel.reload_lists()
    panel.preview()
    settle(panel)
    monkeypatch.setattr(ws.SubmodelWorkspace, "read_only", lambda self: True)
    monkeypatch.setattr(type(window.session), "read_only", property(lambda self: True), raising=False)
    panel.sync()
    assert panel.preview_button.isEnabled()
    for button in (panel.create_button, panel.delete_button, panel.run_button, panel.edit_button):
        assert not button.isEnabled()
    with pytest.raises(ValueError, match="read-only"):
        panel.create()
    with pytest.raises(ValueError, match="read-only"):
        panel.edit_selected()


# ---- section box in the real view -------------------------------------------
def test_box_preview_uses_the_section_box_and_restores_the_prior_clip(panel, window):
    canvas = panel.fake_canvas
    canvas.box = FakeBox((1.0, 1.0, 1.0), (0.5, 0.5, 0.5))
    seen = SimpleNamespace(_seen_clip=None, _clip_candidate="pending")
    window.panels["__probe"] = SimpleNamespace(presentation_controls=seen)
    try:
        panel._set_box((0.0, 0.0, 0.0), (2.0, 4.0, 6.0))
        assert canvas.log == []  # editing alone does not touch the view
        panel.show_box.setChecked(True)
        assert canvas.log[-1][:3] == ("set", (1.0, 2.0, 3.0), (1.0, 2.0, 3.0)) and canvas.log[-1][3] == (0, 0, 0, 1)
        assert seen._seen_clip[0] is canvas.box and seen._clip_candidate is None  # never persisted as a user change
        panel.box_max[0].setValue(3.0)
        panel._box_timer.timeout.emit()  # the debounce
        assert canvas.box.center.to_tuple()[0] == pytest.approx(1.5)
        count = len(canvas.log)
        panel.show_box.setChecked(False)
        assert canvas.box.center.to_tuple() == (1.0, 1.0, 1.0) and canvas.box.half_extents.to_tuple() == (0.5, 0.5, 0.5)
        assert len(canvas.log) == count + 1
        # leaving the panel also restores
        panel.show_box.setChecked(True)
        panel.deactivate()
        assert canvas.box.center.to_tuple() == (1.0, 1.0, 1.0)
        panel.activate()
        assert canvas.box.center.to_tuple() != (1.0, 1.0, 1.0)  # shown again on return
        panel.dispose()
        assert canvas.box.center.to_tuple() == (1.0, 1.0, 1.0)
    finally:
        window.panels.pop("__probe", None)


def test_box_preview_with_no_prior_box_clears_it_again_and_rotated_boxes_are_refused(panel):
    canvas = panel.fake_canvas
    panel.show_box.setChecked(True)
    assert canvas.box is not None
    panel.show_box.setChecked(False)
    assert canvas.box is None and canvas.log[-1] == ("clear",)
    with pytest.raises(ValueError, match="no section box"):
        panel.use_view_box()
    canvas.box = FakeBox((0, 0, 0), (1, 1, 1), orientation=(0.0, 0.0, 0.38268343, 0.92387953))
    with pytest.raises(ValueError, match="axis-aligned"):
        panel.use_view_box()
    canvas.box = FakeBox((1.0, 2.0, 3.0), (0.5, 0.5, 0.5), orientation=(0.0, 0.0, 0.0, -1.0))
    panel.use_view_box()  # -q is the same rotation
    assert panel.box() == ((0.5, 1.5, 2.5), (1.5, 2.5, 3.5))
    assert ws.orientation_is_axis_aligned((0, 0, 0, 1))
    assert not ws.orientation_is_axis_aligned((0, 0, 0.38268343, 0.92387953))


def test_invalid_box_is_reported_and_not_pushed(panel):
    canvas = panel.fake_canvas
    panel.show_box.setChecked(True)
    before = len(canvas.log)
    panel.box_min[0].setValue(5.0)
    panel._box_timer.timeout.emit()
    assert len(canvas.log) == before and "min must be below" in panel.box_note.text()
    with pytest.raises(ValueError, match="min must be below"):
        panel.request()


# ---- project and window lifecycles -----------------------------------------
def headless_app(project, revision="r1"):
    app = SimpleNamespace(project=project, session=SimpleNamespace(revision=SimpleNamespace(id=revision), read_only=False),
                          set_status=lambda *a, **k: None, scheduler=SimpleNamespace(call_later=lambda *a: None))
    return app


def test_project_replacement_drops_preview_edit_state_and_box_preview(monkeypatch, design):
    QApplication.instance() or QApplication([])
    first = Project(name="first")
    app = headless_app(first)
    canvas = FakeCanvas()
    canvas.box = FakeBox((0, 0, 0), (1, 1, 1))
    monkeypatch.setattr(ws.SubmodelWorkspace, "_canvas", lambda self: canvas)
    monkeypatch.setattr(ws, "plate_choices", lambda project: [(7, "Deck · face 7")])
    made = solid_definition()
    first.submodels[made.id] = made
    workspace = ws.SubmodelWorkspace(app)
    workspace.activate()
    workspace._editing_id = made.id
    workspace.show_box.setChecked(True)
    workspace.task.preview = ("r1", SimpleNamespace(definition=made, report=SimpleNamespace()))
    workspace.task.preview_request = SubmodelRequest(kind="solid", job_id="job-1", case="default", target_size=0.1)
    app.project = Project(name="second")
    app.session = SimpleNamespace(revision=SimpleNamespace(id="r9"), read_only=False)
    workspace.refresh()
    assert workspace._editing_id is None and workspace.task.preview is None
    assert workspace.child_list.count() == 0
    assert canvas.box.center.to_tuple() == (0, 0, 0) and workspace._saved_clip is None  # clip restored
    workspace.dispose()
    assert workspace._on_task not in workspace.task.listeners


def test_closing_the_window_and_disposing_leave_no_listener_timer_or_clip(window, design):
    workspace = ws.SubmodelWorkspace(window)
    workspace.activate()
    workspace._box_timer.start()
    task = workspace.task
    workspace.dispose()
    assert not workspace._box_timer.isActive() and workspace._on_task not in task.listeners
    task._notify()  # a late notification reaches nobody and must not raise
    workspace.sync()
    workspace.refresh()
    workspace.activate()  # a disposed panel cannot come back to life
    assert not workspace._live


# ---- pure helpers -----------------------------------------------------------
def test_body_geometry_is_the_footprint_extruded_by_the_thickness():
    corners = ws.body_corners(FOOT, 0.02)
    assert len(corners) == 8
    assert {round(c[2], 12) for c in corners} == {-0.01, 0.01}
    lower, upper = ws.box_around_body(FOOT, 0.02, (0.0, 0.0, 1.0))
    assert lower == pytest.approx((0.2, 0.2, -0.01)) and upper == pytest.approx((0.6, 0.5, 0.01))
    assert ws.footprint_is_axis_aligned(FOOT)
    turned = ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 2.0, 0.0), (-1.0, 1.0, 0.0))
    assert not ws.footprint_is_axis_aligned(turned)
    with pytest.raises(ValueError, match="thickness"):
        ws.body_corners(FOOT, 0.0)
    scene = ws.body_scene(FOOT, 0.02)
    assert sum(len(patch.polygons) for patch in scene.faces) == 6


def test_plate_choices_name_the_plate_and_prefer_sectioned_faces():
    faces = {3: SimpleNamespace(name="Deck"), 4: SimpleNamespace(name=""), 5: SimpleNamespace(name="Bulkhead")}
    project = SimpleNamespace(geometry=SimpleNamespace(faces=faces), face_sections={3: "p10", 5: "p20"},
                              plate_sections={"p10": SimpleNamespace(thickness=0.01), "p20": SimpleNamespace(thickness=0.02)})
    assert ws.plate_choices(project) == [(3, "Deck · face 3 · t 0.01"), (5, "Bulkhead · face 5 · t 0.02")]
    project.face_sections = {}
    assert [face for face, _ in ws.plate_choices(project)] == [3, 4, 5]
    assert ws.plate_choices(SimpleNamespace()) == []


def test_use_selected_plate_picks_from_the_selection(panel, window, monkeypatch):
    from anygeometry.entities import EntityRef

    panel.mode.setCurrentIndex(panel.mode.findData("solid"))
    # the window's selection is controller-owned (read-only attribute): fill the real Selection with real refs
    window.selection.restore([EntityRef("edge", 7)])
    with pytest.raises(ValueError, match="Select a plate"):
        panel.use_selected_plate()
    window.selection.restore([EntityRef("face", 7)])
    panel.use_selected_plate()
    assert panel.plate.currentData() == 7


# ---- tabs, source guidance, box from selection / geometry --------------------
def test_three_top_level_tabs_and_focus_follow_create_and_edit(panel, window):
    from PySide6.QtWidgets import QFormLayout

    assert [panel.tabs.tabText(i) for i in range(3)] == ["Define", "Children", "Results"]
    assert panel.current_tab() == "define"
    forms = panel.findChildren(QFormLayout)
    assert forms and all(f.rowWrapPolicy() == QFormLayout.WrapLongRows for f in forms)
    panel.preview()
    settle(panel)
    panel.create()
    assert panel.current_tab() == "children"
    made = add_solid(window, name="detail")
    panel.reload_lists()
    panel.child_list.setCurrentIndex(panel.child_list.findData(made.id))
    panel.show_tab("results")
    panel.edit_selected()
    assert panel.current_tab() == "define" and panel._editing_id == made.id


def test_no_qualifying_source_gives_guidance_and_never_enables_preview(panel, monkeypatch):
    refusal = "modal/load_case is not a linear static load-case solve"
    monkeypatch.setattr(ws, "source_choices",
                        lambda project: [("job-9", "Modal · case ? · completed", "", refusal)])
    panel.reload_lists()
    panel.sync()
    assert panel.source.currentData() is None and panel.source.currentText() == ws.NO_SOURCE
    assert panel.case.text() == ws.NO_CASE and not panel.source_usable()
    assert not panel.preview_button.isEnabled()
    assert panel.source_note.isVisibleTo(panel)
    assert "No qualifying solved job" in panel.source_note.text() and refusal in panel.source_note.text()
    assert "Modal" in panel.source_note.text()  # reported by source
    assert refusal in panel.status.text() or "No qualifying" in panel.status.text()
    with pytest.raises(ValueError, match="Choose a completed source job"):
        panel.preview()


def test_a_refused_source_is_never_defaulted_to_or_kept(panel, monkeypatch):
    assert panel.source.currentData() == "job-1"
    monkeypatch.setattr(ws, "source_choices", lambda project: [
        ("job-1", "A · case default · completed", "default", "the job is partial"),
        ("job-2", "B · case default · completed", "default", None)])
    panel.reload_lists()
    assert panel.source.currentData() == "job-2" and panel.source_usable() and panel.preview_button.isEnabled()
    monkeypatch.setattr(ws, "source_choices", lambda project: [
        ("job-1", "A · case default · completed", "default", "the job is partial")])
    panel.reload_lists()
    panel.sync()
    assert panel.source.currentData() is None and not panel.preview_button.isEnabled()
    assert "A · case default" in panel.source_note.text() and "the job is partial" in panel.source_note.text()


class FakeGeometry:
    def __init__(self, bounds, faces=(7,)):
        self.bounds_result, self.calls, self.faces = bounds, [], {f: object() for f in faces}

    def bounds(self, keys):
        self.calls.append(list(keys))
        return self.bounds_result


def fake_project(panel, monkeypatch, geometry):
    project = SimpleNamespace(geometry=geometry, submodels={}, jobs={}, submodel_runs={})
    monkeypatch.setattr(panel, "_project_now", lambda: project)
    return project


def test_box_from_selection_uses_geometry_bounds_and_pads_a_flat_plate(panel, window, monkeypatch):
    from anygeometry.entities import EntityRef

    geometry = FakeGeometry((0.0, 0.0, 0.0, 2.0, 1.0, 0.0))  # xmin, ymin, zmin, xmax, ymax, zmax: flat XY plate
    fake_project(panel, monkeypatch, geometry)
    with pytest.raises(ValueError, match="Select points, lines or plates"):
        panel.use_selection_box()
    window.selection.restore([EntityRef("face", 7), EntityRef("edge", 3)])
    panel.use_selection_box()
    assert geometry.calls == [[EntityRef("face", 7), EntityRef("edge", 3)]]
    lower, upper = panel.box()
    assert lower == pytest.approx((0.0, 0.0, -THICKNESS)) and upper == pytest.approx((2.0, 1.0, THICKNESS))
    assert panel.task.preview is None
    geometry.bounds_result = (0.0, 0.0, 0.0, 2.0, 1.0, 3.0)  # a solid extent is not padded
    panel.use_selection_box()
    assert panel.box() == ((0.0, 0.0, 0.0), (2.0, 1.0, 3.0))


def test_fit_and_seed_use_project_geometry_not_the_scene(panel, window, monkeypatch):
    geometry = FakeGeometry((0.0, 0.0, 0.0, 4.0, 2.0, 0.0))
    fake_project(panel, monkeypatch, geometry)
    monkeypatch.setattr(window.viewport, "_scene", SimpleNamespace(bounds=lambda: ((-9, -9, -9), (9, 9, 9))),
                        raising=False)
    panel.fit_box()
    lower, upper = panel.box()
    assert lower == pytest.approx((1.0, 0.5, -THICKNESS)) and upper == pytest.approx((3.0, 1.5, THICKNESS))
    panel._set_box((0, 0, 0), (1, 1, 1), notify=False)
    panel._seed_box()
    assert panel.box()[0] == pytest.approx((1.0, 0.5, -THICKNESS))
    geometry.faces = {}
    with pytest.raises(ValueError, match="no geometry"):
        panel.fit_box()


def test_section_box_rotated_by_90_degrees_permutes_half_extents_and_oblique_is_refused(panel):
    canvas = panel.fake_canvas
    root = 0.7071067811865476
    canvas.box = FakeBox((1.0, 2.0, 3.0), (0.5, 1.0, 2.0), orientation=(0.0, 0.0, root, root))  # 90° about z
    panel.use_view_box()
    lower, upper = panel.box()
    assert lower == pytest.approx((0.0, 1.5, 1.0)) and upper == pytest.approx((2.0, 2.5, 5.0))
    canvas.box = FakeBox((0, 0, 0), (1, 2, 3), orientation=(root, 0.0, 0.0, root))  # 90° about x: y<->z
    panel.use_view_box()
    lower, upper = panel.box()
    assert lower == pytest.approx((-1.0, -3.0, -2.0)) and upper == pytest.approx((1.0, 3.0, 2.0))
    canvas.box = FakeBox((0, 0, 0), (1, 1, 1), orientation=(0.0, 0.0, 0.2588190451, 0.9659258263))  # 30°
    with pytest.raises(ValueError, match="axis-aligned"):
        panel.use_view_box()
    assert ws.orientation_is_axis_aligned((0, 0, root, root)) and not ws.orientation_is_axis_aligned((0, 0, 0, 0))


# ---- exact footprint, concurrent edits, retained result meshes --------------------
TURNED = ((0.2, 0.2, 0.0), (0.2, 0.5, 0.0), (0.6, 0.5, 0.0), (0.6000000000001, 0.2, 0.0))  # reversed, round-off


def test_replacing_keeps_the_exact_stored_footprint_until_the_box_or_plate_is_edited(panel, window):
    made = add_solid(window, name="legacy", footprint=TURNED)
    panel.reload_lists()
    panel.edit_selected()
    panel.layers.setValue(3)
    panel.name.setText("renamed")
    panel.order.setCurrentIndex(1)
    panel.target_size.setValue(0.2)
    assert panel.request().footprint == TURNED  # same order, same round-off: never shrunk, grown or relabelled
    panel.preview()
    settle(panel)
    assert panel.task.preview[1].definition.footprint == TURNED
    assert panel.replace() == made.id
    assert window.project.submodels[made.id].footprint == TURNED

    again = add_solid(window, name="legacy2", footprint=TURNED)
    panel.reload_lists()
    panel.child_list.setCurrentIndex(panel.child_list.findData(again.id))
    panel.edit_selected()
    panel.box_max[0].setValue(panel.box_max[0].value() + 0.1)  # an explicit box edit regenerates
    lower, upper = panel.box()
    assert panel.request().footprint == _rectangle(None, 7, lower, upper) != TURNED


def test_a_concurrent_edit_or_deletion_between_begin_replace_and_preview_or_replace_is_refused(panel, window):
    made = add_solid(window, name="detail")
    panel.reload_lists()
    panel.edit_selected()
    window.project.submodels[made.id] = dataclasses.replace(made, layers=5)  # changed elsewhere
    with pytest.raises(ValueError, match="changed elsewhere"):
        panel.preview()
    window.project.submodels[made.id] = made
    panel.preview()
    settle(panel)
    assert panel.task.preview is not None
    window.project.submodels[made.id] = dataclasses.replace(made, layers=6)
    with pytest.raises(ValueError, match="changed elsewhere"):
        panel.replace()
    assert panel.task.preview is None and window.project.submodels[made.id].layers == 6
    del window.project.submodels[made.id]
    with pytest.raises(ValueError, match="no longer exists"):
        panel.preview()


def test_task_replace_refuses_when_the_stored_record_differs_from_the_expected_one(window, design, monkeypatch):
    monkeypatch.setattr(SubmodelTask, "_cut", staticmethod(fake_cut))
    task = submodel_task(window)
    made = add_solid(window, name="detail", job="job-1")
    expected = made.to_dict()
    task.start_preview(SubmodelRequest(kind="solid", job_id="job-1", case="default", target_size=0.05, face_id=7,
                                       footprint=FOOT, layers=1))
    task.wait(30)
    window.project.submodels[made.id] = dataclasses.replace(made, layers=9)
    with pytest.raises(ValueError, match="changed since you began"):
        task.replace(made.id, expected_record=expected)
    assert task.preview is None and window.project.submodels[made.id].layers == 9


def test_old_shell_result_mesh_is_drawn_on_an_empty_project_when_its_definition_changed_or_is_gone(window, monkeypatch):
    from anyfem.presentation import scene as presentation_scene

    task = submodel_task(window)
    made = shell_definition("shell")
    window.project.submodels[made.id] = made
    run = dataclasses.replace(stored_run(made.id), child={"child_kind": "shell"})
    window.project.submodel_runs[run.id] = run
    task.inspection = {"run_id": run.id, "child_mesh": object(), "child_kind": "shell"}
    owners, shown = [], []
    monkeypatch.setattr(presentation_scene, "build_mesh_scene", lambda project, mesh, **kw: owners.append(project) or "scene")
    monkeypatch.setattr(window.viewport, "show", lambda scene: shown.append(scene), raising=False)
    monkeypatch.setattr(SubmodelTask, "_definition_matches", staticmethod(lambda definition, run: True))
    task.show_child_mesh(run.id)
    assert owners[-1] is made.child  # the definition the run answered is still current
    monkeypatch.setattr(SubmodelTask, "_definition_matches", staticmethod(lambda definition, run: False))
    task.show_child_mesh(run.id)
    assert owners[-1] is not made.child and isinstance(owners[-1], Project) and not owners[-1].geometry.faces
    del window.project.submodels[made.id]
    task.show_child_mesh(run.id)  # no refusal: the stored mesh stays viewable after deletion
    assert isinstance(owners[-1], Project) and owners[-1] is not made.child and shown == ["scene"] * 3


# ---- task additions ---------------------------------------------------------
def test_task_replace_requires_a_fresh_preview_with_the_original_source(window, design, monkeypatch):
    monkeypatch.setattr(SubmodelTask, "_cut", staticmethod(fake_cut))
    task = submodel_task(window)
    made = add_solid(window, name="detail", job="job-1")
    with pytest.raises(ValueError, match="Preview the replacement first"):
        task.replace(made.id)
    request = SubmodelRequest(kind="solid", job_id="job-2", case="default", target_size=0.05, face_id=7,
                              footprint=FOOT, layers=1)
    task.start_preview(request)
    task.wait(30)
    with pytest.raises(ValueError, match="original source"):
        task.replace(made.id)
    assert window.project.submodels[made.id] is made
    with pytest.raises(ValueError, match="no longer exists"):
        task.replace("missing")
    task.invalidate_preview("x")
    assert task.preview is None and task.preview_request is None


def test_task_cancel_while_previewing_discards_the_result(window, monkeypatch):
    monkeypatch.setattr(SubmodelTask, "_cut", staticmethod(fake_cut))
    task = submodel_task(window)
    task.start_preview(SubmodelRequest(kind="shell", job_id="job-1", case="default", target_size=0.1))
    assert task.cancel() and "Cancelling the preview" in task.message
    task.wait(30)
    assert task.preview is None and task.state == task.IDLE and "discarded" in task.message
    assert not task_module.SubmodelTask.cancel(task)  # nothing to cancel any more
