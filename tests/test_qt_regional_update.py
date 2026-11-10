"""Publish a regional update and reselect an actual retired reference in the Qt panel."""
import os
import time

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets", allow_module_level=True)
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from PySide6.QtCore import QTimer

from anygeometry import EntityRef
from anyfem.selection import entity_tag

from anyfem.io import recovery
from anyfem.model.records import OutputRequest
from anyfem.model.regions import ManualRegion, MeshEntityRef, Region, RegionRef
from anyfem.ui.qt.app import QtFemWindow
from anyfem.ui.qt.tasks import MeshRepairDialog, MeshTask
from test_regional_mesh import two_plates


def _finish(window, app, expected="completed"):
    deadline = time.monotonic() + 40
    record_id = next(reversed(window.project.mesh_records))
    while window.project.mesh_records[record_id].status == "running" and time.monotonic() < deadline:
        app.processEvents()
        window._poll_mesh_jobs()
        time.sleep(.01)
    record = window.project.mesh_records[record_id]
    assert record.status == expected, record.diagnostics
    return record


def test_mesh_panel_updates_two_plates_and_repairs_selected_parent_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("ANYFEM_JOB_EXECUTOR", "thread")
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    app = QApplication.instance() or QApplication([])
    project, faces, edges = two_plates()
    window = QtFemWindow(project, viewer_backend="software")
    try:
        panel = window.panels["Mesh"]
        assert panel.settings()["automation"] is not None
        panel.regional_generation.click()
        panel.size.setText("0.5")
        assert panel.strategy_token() == "mapped" and panel.settings()["automation"] is None
        panel.start()
        parent = _finish(window, app)
        assert "regional_baseline" in parent.summary and panel.update_mesh.isEnabled()
        restored = MeshTask(window)
        assert restored.regional_generation.isChecked() and restored.settings()["automation"] is None
        panel.regional_generation.click()
        panel.reset_mesh_drafts()
        assert panel.regional_generation.isChecked() and panel.settings()["automation"] is None
        old = window.mesh
        kept = list(old.elements_of_face[faces[1]])
        source = next(iter(old.quads[old.elements_of_face[faces[0]][0]]))
        anchor = MeshEntityRef(parent.id, "node", source)
        region = Region("node output", "mesh", "node", ManualRegion([anchor]), mesh_id=parent.id)
        with window.session.transaction("Select a mesh node for output"):
            window.project.regions.add(region)
            window.project.output_requests["regional"] = OutputRequest(
                ("displacement",), RegionRef(region.id), "node")
        window.seeding_overrides[edges[0][0]] = 4
        dialogs = []

        def choose_target(dialog):
            assert f"node {source}" in dialog.issues.toPlainText()
            target = max(dialog.new.nodes)
            dialog.source.setText(str(source))
            assert dialog.source_info.text().endswith(" m")
            dialog.find_number.setText(str(target))
            dialog.candidates.selectRow(0)
            dialog._add()
            assert dialog.repairs == {anchor: MeshEntityRef(dialog.child_id, "node", target)}
            dialogs.append((dialog.child_id, target))
            return QDialog.Accepted

        # Inspect the live canvas from the nested event loop of the real
        # confirmation dialog, then cancel without starting a mesh worker.
        desc = window.preview_regional_update(**panel.settings())
        assert isinstance(desc, str)
        selected = [EntityRef("vertex", min(window.project.geometry.vertices))]
        window.selection.restore(selected)
        previous = frozenset({EntityRef("vertex", max(window.project.geometry.vertices))})
        window.viewport.set_collection_highlight(previous)
        original_tags = window.viewport.canvas.highlighted_tags()
        observations = []

        def cancel_confirmation():
            dialog = QApplication.activeModalWidget()
            observations.append((dialog.windowTitle(), window.viewport.canvas.highlighted_tags(),
                                 tuple(window.selection.items)))
            dialog.button(QMessageBox.No).click()

        QTimer.singleShot(0, cancel_confirmation)
        panel.start_update()
        assert observations == [("Update mesh", original_tags | {entity_tag(EntityRef("face", faces[0]))},
                                 tuple(selected))]
        assert window.viewport.canvas.highlighted_tags() == original_tags
        assert window.viewport._collection_refs == previous and list(window.selection.items) == selected
        assert window.mesh_record_id == parent.id and not window.mesh_job_running

        monkeypatch.setattr(window.dialogs, "confirm", lambda *_: True)
        monkeypatch.setattr(MeshRepairDialog, "exec", lambda dlg: QDialog.Rejected)
        submit = window.update_mesh_async

        def submit_after_preview(**options):
            # The normal queue transaction can refresh the tree. Observe the
            # handoff before that separate application action changes its view.
            assert window.viewport._collection_refs == previous
            assert list(window.selection.items) == selected
            return submit(**options)

        with monkeypatch.context() as patch:
            patch.setattr(window, "update_mesh_async", submit_after_preview)
            panel.start_update()
        _finish(window, app, expected="failed")
        assert window.mesh_record_id == parent.id
        assert window.project.regions[region.id].definition.anchors == (anchor,)
        monkeypatch.setattr(MeshRepairDialog, "exec", choose_target)
        panel.start_update()
        child = _finish(window, app)
        assert window.mesh_record_id == child.id and child.id != parent.id
        assert window.mesh.elements_of_face[faces[1]] == kept
        assert dialogs == [(child.id, max(window.mesh.nodes))]
        assert window.project.regions[region.id].definition.anchors == (
            MeshEntityRef(child.id, "node", max(window.mesh.nodes)),)
    finally:
        window.session.mark_saved()
        window.close()
        app.processEvents()


def test_repair_dialog_scopes_element_faces_and_turbines():
    from types import SimpleNamespace
    from anyfem.model.regions import ElementFaceRef
    from anymesher.mesh import Mesh

    app = QApplication.instance() or QApplication([])
    mesh = Mesh()
    mesh.nodes = {i: (float(i), 0., 0.) for i in range(1, 7)}
    mesh.quads = {1: (1, 2, 3, 4), 2: (2, 5, 6, 3)}
    turbine = SimpleNamespace(id="T1", name="turbine", hub_node_id=2, attachment_element_id=1)
    dialog = MeshRepairDialog(None, project=SimpleNamespace(wind_turbine_loads={"T1": turbine}),
                              parent_id="parent", child_id="child", old_mesh=mesh, new_mesh=mesh,
                              diagnostics=["Retired hub and shell face"])
    dialog.kind.setCurrentIndex(2)
    dialog.source.setText("1")
    dialog.old_face.setText("2")
    dialog.new_face.setText("4")
    dialog.candidates.selectRow(1)
    dialog._add()
    assert not dialog.repairs and "outside" in dialog.feedback.text()
    dialog.new_face.setText("0")
    dialog._add()
    assert dialog.repairs == {ElementFaceRef("parent", 1, 2): ElementFaceRef("child", 2, 0)}
    dialog.chosen.setCurrentRow(0)
    dialog._remove()
    assert not dialog.repairs
    dialog.kind.setCurrentIndex(3)
    assert dialog.source.isReadOnly() and dialog.source.text() == "2"
    dialog.candidates.selectRow(4)
    dialog._add()
    assert dialog.repairs == {("wind_turbine", "T1", "hub_node_id"): 5}
    dialog.reject()


def test_removed_face_highlights_only_the_old_mesh_polygon_owners(tmp_path, monkeypatch):
    from anyfem.mesh_preview import mesh_update_preview
    from anyfem.regional_mesh import plan_regional_update
    from anyfem.selection import MeshEntityRef as SceneMeshRef
    from anyfem.ui.scene import build_mesh_scene
    from test_regional_mesh import full, remove_last_plate

    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    app = QApplication.instance() or QApplication([])
    project, faces, _ = two_plates()
    settings, old, baseline = full(project)
    parent_id = "displayed-parent"
    old._anyfem_mesh_record_id = parent_id
    edited = remove_last_plate(project, faces[-1])
    plan = plan_regional_update(edited, settings, baseline)
    preview = mesh_update_preview(plan, baseline, old, parent_id)
    expected = {SceneMeshRef("element", e, parent_id) for e in old.elements_of_face[faces[-1]]}
    assert set(preview.owners) == expected
    window = QtFemWindow(edited, viewer_backend="software")
    try:
        window.viewport.show(build_mesh_scene(edited, old))
        window.viewport.set_collection_highlight(preview.owners)
        assert window.viewport.canvas.highlighted_tags() == {entity_tag(ref) for ref in expected}
        window.viewport.set_collection_highlight([SceneMeshRef("element", ref.id, "another-mesh") for ref in expected])
        assert not window.viewport.canvas.highlighted_tags()
    finally:
        window.session.mark_saved()
        window.close()


def test_local_patch_reviews_exact_parent_cells_before_publication(tmp_path, monkeypatch):
    from anyfem.selection import MeshEntityRef as SceneMeshRef
    from anymesher.refinement import Refinement
    from test_mesh_surface_update import plate

    monkeypatch.setenv("ANYFEM_JOB_EXECUTOR", "thread")
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    app = QApplication.instance() or QApplication([])
    project, _, _, _, _ = plate(prepared=False)
    window = QtFemWindow(project, viewer_backend="software")
    try:
        panel = window.panels["Mesh"]
        panel.regional_generation.click()
        panel.size.setText("1")
        panel.start()
        parent = _finish(window, app)
        old = window.mesh
        with window.session.transaction("add interior sizing zone"):
            project.add_refinement(Refinement(.5, center=(4., 4., 0.), radius=.35, growth=2.))
        window.show_geometry()
        selected = [EntityRef("vertex", min(project.geometry.vertices))]
        window.selection.restore(selected)
        previous = frozenset({EntityRef("vertex", max(project.geometry.vertices))})
        window.viewport.set_collection_highlight(previous)
        hidden = {("vertex", 2)}
        window.viewport.visibility.hidden = set(hidden)
        window.viewport.refresh_visibility()
        prior_scene = window.viewport._visibility_scene
        before = project.mesh_numbering
        original = window.dialogs.confirm
        confirmations = []
        decision = QMessageBox.No

        def confirm(title, message):
            observed = frozenset(window.viewport._collection_refs)
            assert title == "Publish mesh update" and observed
            assert all(isinstance(r, SceneMeshRef) and r.kind == "element" and r.mesh_id == parent.id for r in observed)
            assert {entity_tag(r) for r in observed} == set(window.viewport.canvas.highlighted_tags())
            assert not window.viewport.visibility.hidden
            assert tuple(window.selection.items) == tuple(selected)
            assert project.mesh_numbering == before
            confirmations.append(observed)
            timer = QTimer()
            def answer():
                dialog = QApplication.activeModalWidget()
                if isinstance(dialog, QMessageBox) and dialog.windowTitle() == title:
                    dialog.button(decision).click()
            timer.timeout.connect(answer)
            timer.start(25)
            try:
                return original(title, message)
            finally:
                timer.stop()

        monkeypatch.setattr(window.dialogs, "confirm", confirm)
        panel.start_update()
        assert not confirmations
        _finish(window, app, expected="cancelled")
        assert len(confirmations) == 1 and window.viewport._collection_refs == previous
        assert window._view_mode == "geometry" and tuple(window.selection.items) == tuple(selected)
        assert project.mesh_numbering == before and window.mesh_record_id == parent.id
        assert window.viewport._visibility_scene is prior_scene and window.viewport.visibility.hidden == hidden

        def fail_review(title, message):
            assert title == "Publish mesh update"
            assert window.viewport._collection_refs == confirmations[0]
            raise RuntimeError("review unavailable")

        monkeypatch.setattr(window.dialogs, "confirm", fail_review)
        panel.start_update()
        failed = _finish(window, app, expected="failed")
        assert any("review unavailable" in d for d in failed.diagnostics)
        assert failed.id not in window._meshes and old is window._meshes[parent.id]
        assert window.mesh_record_id == parent.id and project.mesh_numbering == before
        assert window.viewport._collection_refs == previous and tuple(window.selection.items) == tuple(selected)
        assert window.viewport._visibility_scene is prior_scene and window.viewport.visibility.hidden == hidden

        decision = QMessageBox.Yes
        monkeypatch.setattr(window.dialogs, "confirm", confirm)
        panel.start_update()
        published = _finish(window, app)
        assert len(confirmations) == 2 and confirmations[0] == confirmations[1]
        assert {r.id for r in confirmations[-1]} == old.quads.keys() - window.mesh.quads.keys()
        assert 0 < len(confirmations[-1]) < len(old.quads)
        assert project.mesh_numbering.generation == before.generation + 1
        assert published.id == window.mesh_record_id
    finally:
        window.session.mark_saved()
        window.close()
        app.processEvents()


def test_edit_during_prepared_review_rejects_the_stale_candidate(tmp_path, monkeypatch):
    from anymesher.refinement import Refinement
    from anyfem.selection import MeshEntityRef as SceneMeshRef
    from test_mesh_surface_update import plate

    monkeypatch.setenv("ANYFEM_JOB_EXECUTOR", "thread")
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    app = QApplication.instance() or QApplication([])
    project, _, _, _, _ = plate(prepared=False)
    window = QtFemWindow(project, viewer_backend="software")
    try:
        panel = window.panels["Mesh"]
        panel.regional_generation.click()
        panel.size.setText("1")
        panel.start()
        parent = _finish(window, app)
        old = window.mesh
        ledger = project.mesh_numbering
        with window.session.transaction("add interior sizing zone"):
            project.add_refinement(Refinement(.5, center=(4., 4., 0.), radius=.35, growth=2.))

        def confirm(title, _message):
            assert title == "Publish mesh update"
            with window.session.transaction("change the source during review"):
                project.geometry.move_point(min(project.geometry.vertices), -.25, -.25, 0.)
            return True

        monkeypatch.setattr(window.dialogs, "confirm", confirm)
        panel.start_update()
        failed = _finish(window, app, expected="failed")
        assert any("superseded" in d for d in failed.diagnostics)
        assert failed.id not in window._meshes
        assert window._meshes[parent.id] is old and project.mesh_numbering == ledger
        assert window._view_mode == "geometry"
        assert not any(isinstance(r, SceneMeshRef) for r in window.viewport._collection_refs)
    finally:
        window.session.mark_saved()
        window.close()
        app.processEvents()


def test_connected_preview_includes_only_planned_faces_and_one_step_extension():
    from dataclasses import replace
    from anyfem.mesh_connected_update import plan_connected_update
    from anyfem.mesh_preview import mesh_update_preview
    from test_mesh_connected_update import build, full

    project, (left, right), _, right_edges = build()
    settings, mesh, baseline = full(project)
    for edge, expected in ((right_edges[0], (right,)), (right_edges[1], (left, right))):
        edited = replace(settings, overrides=((edge, 4),))
        plan = plan_connected_update(project, edited, baseline)
        preview = mesh_update_preview(plan, baseline, mesh, "parent")
        assert preview.owners == tuple(EntityRef("face", fid) for fid in sorted(expected))
