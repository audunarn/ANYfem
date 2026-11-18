"""Shell local-axes overlay in the Qt presentation controls (direction programme D1)."""
import hashlib
import json
import os
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)
pytest.importorskip("PySide6")
import numpy as np
from test_qt_geometry_actions import qapp, window  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from anyfem.ui.qt import app
    monkeypatch.setattr(app, "QSettings", lambda *_: QSettings(str(tmp_path/"layout.ini"), QSettings.IniFormat))


def _plain(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return repr(value)


def _digest(project, mesh, session):
    """Mesh content incl. structural preparation, and the full project document incl. geometry."""
    from anyfem.io.project_file import project_to_dict
    nodes = sorted((k, tuple(np.asarray(v, float))) for k, v in mesh.nodes.items())
    payload = dict(
        nodes=nodes, quads=sorted(mesh.quads.items()), tris=sorted(mesh.tris.items()),
        faces=sorted((k, list(v)) for k, v in mesh.elements_of_face.items()),
        preparation=getattr(mesh, "structural_preparation", None),
        diagnostics=getattr(mesh, "hybrid_diagnostics", None),
        project=project_to_dict(project), document=session.revision.document_hash)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=_plain).encode()).hexdigest()


def _mesh(project):
    from anyfem.jobs import _cancellation_token
    from anyfem.mesh_jobs import MeshSettings, run_mesh_job
    from anymesher import MeshAutomationOptions
    settings = MeshSettings.create(.5, element_order="quadratic", strategy="quad_first", layout_policy="adaptive",
                                   automation=MeshAutomationOptions(strict_method=False, allow_inspection=True))
    return run_mesh_job(project, "axes", settings, lambda *a: None, _cancellation_token()).mesh


def _wait(controls, qapp, limit=300.0):
    """Let the background check finish and its completion reach the GUI thread."""
    import time
    deadline = time.monotonic() + limit
    while controls.shell_axes_busy:
        assert time.monotonic() < deadline, "shell-axis check did not finish"
        qapp.processEvents(); controls.poll(); time.sleep(.02)
    qapp.processEvents(); controls.poll()


def _section_state(project):
    """What the solve-equivalent normalisation may touch (scope A)."""
    return (len(project.section_assignments), len(project.regions), len(project.geometry.sheets),
            dict(project.face_sections), dict(project.edge_sections),
            dict(project.face_assignment_ids), dict(project.edge_assignment_ids))


def _raw_state(project, mesh, session):
    """Live project and mesh state read directly, without project_to_dict (which normalises)."""
    from anyfem.mesh_orientation import mesh_content_digest
    assignments = sorted((key, repr(value)) for key, value in project.section_assignments.items())
    regions = sorted((key, repr(getattr(value, "__dict__", value))) for key, value in project.regions.items()) \
        if hasattr(project.regions, "items") else repr(project.regions)
    return (assignments, regions, _section_state(project), len(project.geometry.faces),
            len(project.geometry.edges), mesh_content_digest(mesh), session.revision.id,
            session.revision.document_hash)


def _live(group):
    return bool(group.handles) and not group.removed and not any(h.removed for h in group.handles)


def test_shell_axes_overlay_counts_caption_clear_and_no_mutation(window, qapp, monkeypatch):
    from anyfem.mesh_orientation import project_orientation_report
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Ok)
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    window._actions["Floating barge section…"].trigger(); qapp.processEvents()
    project = window.project
    assert len(project.geometry.faces) == 5
    mesh = _mesh(project)
    window.mesh = mesh
    controls = window.panels["Visualization"].presentation_controls
    canvas = window.viewport.canvas
    added = []
    original = canvas.add_mesh_arrays

    def spy(batch, *args, **kwargs):
        added.append((len(batch.lines) // 3, kwargs.get("color")))
        return original(batch, *args, **kwargs)
    monkeypatch.setattr(canvas, "add_mesh_arrays", spy)

    # Round 5: the worker builds on a thawed snapshot, so the live project is
    # left exactly as it was - no prior normalisation, compared raw.
    raw = _raw_state(project, mesh, window.session)
    report = project_orientation_report(window.session.snapshot().thaw(), mesh)
    drawn = len(report.element_axes)
    controls.show_shell_axes(); qapp.processEvents()
    assert controls.shell_axes_busy and "background" in controls.status.text()
    _wait(controls, qapp)

    stats = controls.shell_axes_stats
    assert drawn > 0
    assert controls.status.text() == report.summary().splitlines()[0]
    assert stats["arrows"] == 2*drawn == sum(n for n, _ in added)
    assert stats["unchecked"] == len(report.unchecked)
    assert stats["x_warn"] == len(report.turned)
    assert stats["normal_warn"] == len(set(report.reversed) | set(report.inconsistent))
    colours = {c: n for n, c in added}
    from anyfem.ui.qt.shell_axes_overlay import COLOURS
    assert colours.get(COLOURS["x_warn"], 0) == len(report.turned)
    group = controls._shell_axes_group
    assert _live(group)

    controls.clear_shell_axes(); qapp.processEvents()
    assert group.removed and all(h.removed for h in group.handles)
    assert controls._shell_axes_group is None
    # Show again on the same mesh content and document: the cached check is drawn at once.
    controls.show_shell_axes()
    assert not controls.shell_axes_busy and controls.shell_axes_stats == stats
    assert _live(controls._shell_axes_group)
    assert _raw_state(project, mesh, window.session) == raw
    controls.clear_shell_axes(); qapp.processEvents()
    assert _raw_state(project, mesh, window.session) == raw
    assert window.mesh is mesh and window.project is project
    # An in-place edit of the same mesh object changes its content key: no cache reuse.
    first = next(iter(mesh.quads))
    mesh.quads[first] = tuple(reversed(mesh.quads[first][:4])) + tuple(mesh.quads[first][4:])
    controls.show_shell_axes()
    assert controls.shell_axes_busy, "a mutated mesh must not reuse the cached check"
    _wait(controls, qapp); controls.clear_shell_axes()


def test_r1_f8_overlay_follows_scene_replacement_and_mesh_change(window, qapp, monkeypatch):
    """Geometry/Mesh view switches clear the canvas; the overlay and its state stay in step."""
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Ok)
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    window._actions["Floating barge section…"].trigger(); qapp.processEvents()
    project = window.project
    mesh = _mesh(project)
    window.mesh = mesh
    window.show_mesh(); qapp.processEvents()
    controls = window.panels["Visualization"].presentation_controls
    before = _digest(project, mesh, window.session)
    controls.show_shell_axes(); _wait(controls, qapp)
    first = controls._shell_axes_group
    stats = dict(controls.shell_axes_stats)
    batches = len(first.handles)

    window.show_geometry(); qapp.processEvents()
    window.show_mesh(); qapp.processEvents()
    controls.poll(); qapp.processEvents()
    group = controls._shell_axes_group
    assert group is not None and _live(group), "arrows must be redrawn after the scene was replaced"
    assert group.viewer is window.viewport.canvas
    assert controls.shell_axes_stats == stats
    assert len(group.handles) == batches

    # A canvas.clear() from any other redraw path is recovered the same way.
    window.viewport.canvas.clear(); controls.poll(); qapp.processEvents()
    assert _live(controls._shell_axes_group) and controls.shell_axes_stats == stats

    # A different mesh: the check no longer applies, the overlay and state clear together.
    last = controls._shell_axes_group
    window.mesh = _mesh(project); controls.poll(); qapp.processEvents()
    assert controls._shell_axes_group is None and controls.shell_axes_stats is None
    assert last.removed or all(h.removed for h in last.handles)
    assert "mesh changed" in controls.status.text()
    assert _digest(project, mesh, window.session) == before


def test_r4_background_check_is_cancelled_by_a_mesh_change(window, qapp, monkeypatch):
    """The check runs off the GUI thread; a mesh change while it runs discards it."""
    import threading
    from PySide6.QtWidgets import QMessageBox
    from anyfem.ui.qt import shell_axes_overlay
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Ok)
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    window._actions["Floating barge section…"].trigger(); qapp.processEvents()
    project = window.project
    window.mesh = _mesh(project)
    controls = window.panels["Visualization"].presentation_controls
    started, release, seen = threading.Event(), threading.Event(), {}
    original = shell_axes_overlay.prepare_shell_axes

    def held(project, mesh, cancel=None):
        seen["thread"] = threading.current_thread()
        started.set(); release.wait(60)
        seen["cancelled"] = cancel.is_set()
        return original(project, mesh, cancel=cancel)
    monkeypatch.setattr(shell_axes_overlay, "prepare_shell_axes", held)
    controls.show_shell_axes()
    assert started.wait(60) and seen["thread"] is not threading.main_thread()
    window.mesh = _mesh(project); controls.poll(); qapp.processEvents()
    assert "cancelled" in controls.status.text()
    release.set(); _wait(controls, qapp)
    assert seen["cancelled"] is True
    assert controls._shell_axes_group is None and controls.shell_axes_stats is None


def test_r5_worker_never_touches_the_live_project(window, qapp, monkeypatch):
    """Codex round 4, finding 1: a worker build that clears the shared section maps
    while the GUI resolves them corrupts the live project.  The worker must build on
    a detached copy, so the interleaving cannot reach the live project."""
    import threading
    from PySide6.QtWidgets import QMessageBox
    from anyfem.model.project import Project
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Ok)
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    window._actions["Floating barge section…"].trigger(); qapp.processEvents()
    live = window.project
    window.mesh = _mesh(live)
    controls = window.panels["Visualization"].presentation_controls
    assignments = len(live.section_assignments)
    assert assignments > 0
    paused, resume, touched = threading.Event(), threading.Event(), []
    original = Project.resolve_section_assignments

    def interleaved(self, *args, **kwargs):
        if threading.current_thread() is not threading.main_thread() and not paused.is_set():
            touched.append(self is live)
            self.face_sections.clear()  # the worker's resolver mid-way: maps cleared
            paused.set(); resume.wait(60)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Project, "resolve_section_assignments", interleaved)
    controls.show_shell_axes()
    assert paused.wait(120)
    live.resolve_section_assignments(strict=False)  # the GUI resolves meanwhile
    resume.set(); _wait(controls, qapp)
    assert touched == [False]
    assert len(live.section_assignments) == assignments
    assert controls.shell_axes_stats is not None


def test_r5_close_joins_the_worker_thread(window, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Ok)
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    window._actions["Floating barge section…"].trigger(); qapp.processEvents()
    window.mesh = _mesh(window.project)
    controls = window.panels["Visualization"].presentation_controls
    controls.show_shell_axes()
    executor = controls._shell_axes_requests._executor
    threads = list(executor._threads)
    assert threads
    controls.close(); qapp.processEvents()
    assert not any(thread.is_alive() for thread in threads)
    assert controls._shell_axes_requests is None
