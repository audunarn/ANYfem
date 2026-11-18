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

    report = project_orientation_report(project, mesh)
    drawn = len(report.element_axes)
    before = _digest(project, mesh, window.session)
    controls.show_shell_axes(); qapp.processEvents()

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
    assert _digest(project, mesh, window.session) == before
    assert window.mesh is mesh and window.project is project


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
    controls.show_shell_axes(); qapp.processEvents()
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
