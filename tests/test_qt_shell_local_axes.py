"""Shell local-axes overlay in the Qt presentation controls (direction programme D1)."""
import hashlib
import os
import pickle
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


def _digest(project, mesh, session):
    nodes = sorted((k, tuple(np.asarray(v, float))) for k, v in mesh.nodes.items())
    return hashlib.sha256(pickle.dumps((nodes, sorted(mesh.quads.items()), sorted(mesh.tris.items()),
                                        sorted((k, list(v)) for k, v in mesh.elements_of_face.items()),
                                        (session.revision.document_hash, repr(project))))).hexdigest()


def _mesh(project):
    from anyfem.jobs import _cancellation_token
    from anyfem.mesh_jobs import MeshSettings, run_mesh_job
    from anymesher import MeshAutomationOptions
    settings = MeshSettings.create(.5, element_order="quadratic", strategy="quad_first", layout_policy="adaptive",
                                   automation=MeshAutomationOptions(strict_method=False, allow_inspection=True))
    return run_mesh_job(project, "axes", settings, lambda *a: None, _cancellation_token()).mesh


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
    checked = sum(f.element_count - len(f.unchecked) for f in report.faces)
    before = _digest(project, mesh, window.session)
    controls.show_shell_axes(); qapp.processEvents()

    stats = controls.shell_axes_stats
    assert checked > 0
    assert controls.status.text() == report.summary().splitlines()[0]
    assert stats["arrows"] == 2*checked == sum(n for n, _ in added)
    assert stats["x_warn"] == len(report.axis_minority)
    assert stats["normal_warn"] == len(report.normal_minority)
    colours = {c: n for n, c in added}
    from anyfem.ui.qt.shell_axes_overlay import COLOURS
    assert colours.get(COLOURS["x_warn"], 0) == len(report.axis_minority)
    assert colours.get(COLOURS["normal_warn"], 0) == len(report.normal_minority)
    group = controls._shell_axes_group
    assert group.handles and not any(h.removed for h in group.handles)

    controls.clear_shell_axes(); qapp.processEvents()
    assert group.removed and all(h.removed for h in group.handles)
    assert controls._shell_axes_group is None
    assert _digest(project, mesh, window.session) == before
    assert window.mesh is mesh and window.project is project
