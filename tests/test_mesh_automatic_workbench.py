"""Control intent from the real Mesh panel through the workbench and worker."""
from dataclasses import replace
import os

import pytest

from anyfem.mesh_controls import MeshControls, StructuredMeshControls
from anyfem.mesh_jobs import MeshTaskManager
from anyfem.native_meshing import NativeMeshSettings
from test_mesh_general_update import panel
from test_mesh_publication_lifecycle import FileWorkbench
from test_mesh_publication_numbers import plates
from test_source_mesh_publication import _published


@pytest.fixture
def gui():
    if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
        pytest.skip("enable real Qt widget tests with ANYFEM_RUN_QT_TESTS=1")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _mesh_task(app):
    from anyfem.ui.qt.tasks import MeshTask
    task = MeshTask(app)
    task.reset_mesh_drafts()
    task.set_strategy("auto")
    task.size.setText(".45")
    return task


def test_untouched_source_qt_task_generates_numbered_mesh_in_process(gui):
    project, _, face, _ = panel("nurbs")
    project.set_native_mesh_settings(NativeMeshSettings.create(.45, backend="automatic",
        parameters={"geometry_approximation_error": .02}))
    project.element_order = "linear"
    app = FileWorkbench(project, mesh_task_manager_factory=lambda: MeshTaskManager(executor="process"))
    task = _mesh_task(app)
    try:
        options = task.settings()
        assert not task.source_controls_hint.isHidden()
        assert options["mesh_controls"] is None and options["structured_controls"] is None
        assert options["automation"] is None and options["quality_policy"]
        task.start()
        submitted = project.mesh_records[app._active_mesh_task_id]
        mesh = _published(app, submitted)
        assert mesh.elements_of_face[face] and mesh.quads and not mesh.tris
        assert set(mesh.shells) == set(mesh.elements_of_face[face])
        assert project.mesh_numbering.active_mesh_id == submitted.id
        assert not any(k.startswith(("native_", "structured_")) for k in dict(project.native_mesh_settings.parameters))
        assert submitted.summary["controls_requested"] is None
        # Re-selecting the inspection setting explicitly keeps the GUI's recovery policy.
        inspection = task.automation_controls.fields["allow_inspection"][0]
        inspection.click()
        inspection.click()
        assert task.settings()["automation"].allow_inspection
        task.start()
        explicit = project.mesh_records[app._active_mesh_task_id]
        next_mesh = _published(app, explicit)
        assert next_mesh.elements_of_face[face] and min(next_mesh.shells) > max(mesh.shells)
        assert explicit.summary["automation_requested"]["allow_inspection"]
    finally:
        app.mesh_task_manager.shutdown()
        task.close()


def test_explicit_control_forms_and_saved_inheritance(gui, monkeypatch):
    project, _, _, _ = panel("nurbs")
    project.element_order = "linear"
    project.set_native_mesh_settings(NativeMeshSettings.create(.45, backend="automatic",
        parameters={"native_point_placement":"frontal_delaunay", "structured_max_element_growth":1.6}))
    app = FileWorkbench(project)
    submissions = []
    monkeypatch.setattr(app.mesh_task_manager, "submit", lambda *args, **kwargs: submissions.append(args))
    task = _mesh_task(app)
    try:
        assert task.settings()["mesh_controls"] is None and task.settings()["structured_controls"] is None
        task.start()
        parameters = dict(project.native_mesh_settings.parameters)
        assert parameters["native_point_placement"] == "frontal_delaunay"
        assert parameters["structured_max_element_growth"] == 1.6 and "native_metric_mode" not in parameters
        # An explicit user edit to the displayed value remains an authored setting.
        widget = task.native_controls.fields["point_placement"][0]
        widget.textEdited.emit(widget.text())
        assert task.settings()["mesh_controls"].point_placement == "frontal_delaunay"
        task.options.setPlainText('{"mesh_controls": {}, "structured_controls": {"max_element_growth": 1.7},'
                                  '"automation":{"strict_method":true}}')
        authored = task.settings()
        assert authored["mesh_controls"] == MeshControls()
        assert authored["structured_controls"] == replace(StructuredMeshControls(), max_element_growth=1.7)
        assert authored["automation"].strict_method
        app.generate_mesh_async(**authored)
        assert len(submissions) == 2
        stored = dict(project.native_mesh_settings.parameters)
        assert stored["native_point_placement"] == "legacy_lattice" and stored["structured_max_element_growth"] == 1.7
    finally:
        app.mesh_task_manager.shutdown()
        task.close()


def test_planar_automatic_preserves_workbench_defaults_and_explicit_controls(gui):
    app, _, _ = plates()
    app.project.element_order = "linear"
    task = _mesh_task(app)
    try:
        defaults = task.settings()
        assert task.source_controls_hint.isHidden()
        assert defaults["mesh_controls"] == MeshControls() and defaults["structured_controls"] == StructuredMeshControls()
        assert defaults["automation"] is not None and defaults["quality_policy"]
        explicit = replace(MeshControls(), recombine=False)
        full = app.generate_mesh(.5, strategy="auto", mesh_controls=explicit)
        assert full.shells and dict(app.project.native_mesh_settings.parameters)["recombine"] is False
    finally:
        app.mesh_task_manager.shutdown()
        task.close()
