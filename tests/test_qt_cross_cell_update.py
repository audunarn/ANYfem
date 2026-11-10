"""A Qt-confirmed cross-cell trim keeps the other panel through worker publication."""
import json
import os
import time
from collections import Counter
from dataclasses import asdict

import numpy as np
import pytest

from anyfem.io import recovery
from anyfem.io.project_file import load_project
from anyfem.selection import MeshEntityRef, entity_tag
from anygeometry import EntityRef
from test_curved_trim_assembly import curved_assembly
from test_mesh_source_trim import _edit_hole


@pytest.mark.skipif(os.environ.get("ANYFEM_RUN_QT_TESTS") != "1", reason="requires real Qt widgets")
def test_confirm_cross_cell_cut_and_publish_worker_result(curved_assembly, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from anyfem.ui.qt.app import QtFemWindow

    monkeypatch.setenv("ANYFEM_JOB_EXECUTOR", "process")
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    session, old, edited, face, _, ring, hole = _edit_hole(curved_assembly, aligned=False, crossing=True)
    project = session.project
    parent = session.mesh_record_id
    plan = session.preview_update(edited)
    remote, = set(project.geometry.faces) - {face}
    assert plan.retained == plan.reuse_candidates == (remote,)
    assert plan.patched_face == face and plan.hole_edges == tuple(sorted(ring))
    retired = set(plan.replaced_elements)
    # For this bilinear panel, x and y equal the source u and v. Exactly one
    # old grid vertex is inside the authored clockwise hole, shared by four Q4s.
    inside, = (n for n, xyz in old.nodes.items() if all(
        (b[0]-a[0])*(xyz[1]-a[1]) - (b[1]-a[1])*(xyz[0]-a[0]) < 0
        for a, b in zip(hole, hole[1:] + hole[:1])))
    star = {e for e in old.elements_of_face[face] if inside in old.quads[e]}
    assert len(star) == 4 and star <= retired < set(old.elements_of_face[face])
    cells = {MeshEntityRef("element", e, parent) for e in retired}
    edges = {EntityRef("edge", e) for e in ring}
    ledger = project.mesh_numbering
    draft = session.save(tmp_path / "edited-parent.anyfem")
    session.close()
    app = QApplication.instance() or QApplication([])
    window = QtFemWindow(viewer_backend="software")
    try:
        window.open_project(path=str(draft))
        project = window.project
        assert not window.session.read_only and project.mesh_numbering.active_mesh_id == parent
        window.show()
        panel = window.panels["Mesh"]
        window.details.setCurrentWidget(panel)
        app.processEvents()
        window.show_geometry()
        window.seeding_overrides.update(edited.overrides)
        panel.set_strategy(edited.strategy)
        panel.size.setText(str(edited.target_size))
        panel.options.setPlainText(json.dumps({"mesh_controls": asdict(edited.controls), "quality_policy": None,
                                              "structured_controls": None, "automation": None}))
        app.processEvents()
        assert panel.update_mesh.isEnabled()
        reviewed = []
        timer = QTimer()

        def accept_review():
            dialog = QApplication.activeModalWidget()
            if not isinstance(dialog, QMessageBox):
                return
            timer.stop()
            scene = window.viewport._visibility_scene
            reviewed.append((dialog.windowTitle(), dialog.text(), set(window.viewport._collection_refs),
                             set(window.viewport.canvas.highlighted_tags()), scene))
            button = dialog.button(QMessageBox.Yes)
            if button is None:
                dialog.reject()
            else:
                button.click()

        timer.timeout.connect(accept_review)
        timer.start(20)
        try:
            panel.start_update()
        finally:
            timer.stop()
        (title, message, refs, tags, scene), = reviewed
        assert title == "Update mesh" and "Remesh the material" in message
        child_id = window._active_mesh_task_id
        assert child_id and child_id != parent
        worker_ids = set()
        deadline = time.monotonic() + 120
        while project.mesh_records[child_id].status == "running" and time.monotonic() < deadline:
            worker = window.mesh_task_manager._process
            if worker is not None and worker.pid is not None:
                worker_ids.add(worker.pid)
            app.processEvents()
            window._poll_mesh_jobs()
            time.sleep(.02)
        record = project.mesh_records[child_id]
        assert refs == cells | edges and tags == {entity_tag(r) for r in refs}, (record.status, record.diagnostics)
        rendered_cells = {r for patch in scene.faces for owners in patch.polygon_owners for r in owners}
        assert cells <= rendered_cells
        for ref in edges:
            overlay, = (line for line in scene.lines if line.draw_overlay and line.ref == ref)
            curve = project.geometry.edges[ref.id].curve
            endpoints = np.asarray([curve.exact_point(t) for t in (0., 1.)], dtype=float)
            np.testing.assert_allclose(overlay.points[[0, -1]], endpoints,
                                       rtol=0., atol=project.geometry.tolerance.effective_length(edited.target_size))
        assert record.status == "completed", record.diagnostics
        assert worker_ids and os.getpid() not in worker_ids
        assert window.mesh_record_id == child_id
        child = window.mesh
        assert old.shells.keys() - child.shells.keys() == retired
        assert child.elements_of_face[remote] == old.elements_of_face[remote]
        for e in old.shells.keys() - retired:
            assert child.shells[e] == old.shells[e]
            for n in old.shells[e]:
                np.testing.assert_array_equal(child.nodes[n], old.nodes[n])
        added = child.shells.keys() - old.shells.keys()
        assert added and added <= set(child.elements_of_face[face])
        assert min(added) > ledger.element_high_water
        rim_nodes = {n for e in ring for n in child.nodes_of_edge[e]}
        assert len(rim_nodes) == 8 and min(rim_nodes) > ledger.node_high_water
        sides = Counter((a, b) for e in added for cell in [child.shells[e]]
                        for a, b in zip(cell, cell[1:] + cell[:1]))
        stations_by_edge = dict(plan._patch["hole_rim"])
        for edge in ring:
            chain = child.nodes_of_edge[edge]
            assert len(chain) == 3
            geometry_edge = project.geometry.edges[edge]
            for a, b in zip(chain, chain[1:]):
                assert sides[a, b] == 1 and sides[b, a] == 0
            stations = [t for t, _ in stations_by_edge[edge]]
            assert len(stations) == 3 and stations[0] == 0 < stations[1] < stations[2] == 1
            np.testing.assert_allclose([child.nodes[n] for n in chain],
                                       np.asarray([geometry_edge.curve.exact_point(t) for t in stations], dtype=float),
                                       rtol=0., atol=project.geometry.tolerance.effective_length(edited.target_size))
        evidence = record.summary["regional_baseline"]["last_update"]
        assert evidence["reused_source_faces"] == [remote] and evidence["new_elements"] == sorted(added)
        saved = tmp_path / "cross-cell.anyfem"
        window.save_project(path=str(saved))
        reopened = load_project(saved).mesh_session()
        assert reopened.mesh_record_id == child_id and reopened.mesh.shells == child.shells
        assert reopened.mesh.nodes_of_edge == child.nodes_of_edge
        for n in rim_nodes:
            np.testing.assert_array_equal(reopened.mesh.nodes[n], child.nodes[n])
    finally:
        window.session.mark_saved()
        window.close()
        app.processEvents()
