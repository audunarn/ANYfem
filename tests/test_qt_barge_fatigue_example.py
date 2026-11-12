"""Exercise the packaged bracket example through the Qt Examples menu and Save As."""

import hashlib
import json
import os
import tempfile
from zipfile import ZipFile

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for Qt example checks", allow_module_level=True)
pytest.importorskip("anyfatigue.plate")
from PySide6.QtCore import Qt

from anyfem.io.artifacts import ArtifactStore
from anyfem.ui.qt import barge_fatigue_example as example
from test_qt_geometry_actions import qapp, window
from test_qt_workbench import wait_until


ACTION = "5 m barge — internal bracket weld fatigue…"


@pytest.fixture(autouse=True)
def isolate_working_copies(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def test_dirty_cancellation_keeps_the_current_document(window, qapp, monkeypatch):
    current = window.project
    window.session.dirty = True
    monkeypatch.setattr(window.dialogs, "confirm_save", lambda *_: None)
    monkeypatch.setattr(example, "example_archive", lambda: pytest.fail("cancelled before extraction"))
    window._actions[ACTION].trigger()
    qapp.processEvents()
    assert window.project is current and window.session.dirty
    assert window.panels["Results"].fatigue_dialog is None
    window.session.mark_saved()


def test_bad_resource_keeps_the_current_document(window, tmp_path, monkeypatch):
    current = window.project
    broken = tmp_path / "broken.zip"
    with ZipFile(broken, "w") as archive:
        archive.writestr("preset.json", "{}")
    monkeypatch.setattr(example, "example_archive", lambda: broken)
    window._actions[ACTION].trigger()
    assert window.project is current and window.path is None
    assert example.PROJECT_FILE in window._status.get()
    assert getattr(window, "_fatigue_example_copy", None) is None


def test_oblique_view_frames_knee_and_toe_on_a_hull_spanning_support(window):
    from anygeometry.entities import EntityRef
    from anyfem.ui.scene import FacePatch, Scene

    knee = np.array([(0, 0, 0), (.4, 0, 0), (0, 0, .4)])
    host = np.array([(-5, 0, 0), (5, 0, 0), (5, 1, 0), (-5, 1, 0)])
    toe = np.array([(.4, 0, 0), (.55, 0, 0), (.55, .1, 0)])
    window.viewport.show(Scene(faces=[
        FacePatch(EntityRef("face", 1), [knee], ["#808080"], polygon_node_ids=[(1, 2, 3)]),
        FacePatch(EntityRef("face", 2), [host, toe], ["#808080"] * 2,
                  polygon_node_ids=[(4, 5, 6, 7), (2, 8, 9)]),
    ]))
    direction = np.array([.35, -1., .55])
    example._frame_bracket(window.viewport, {1}, {2, 8}, direction)
    camera = example.camera_record(window.viewport.canvas)
    eye = np.subtract(camera["position"], camera["target"])
    assert eye / np.linalg.norm(eye) == pytest.approx(direction / np.linalg.norm(direction))
    size = np.array(window.viewport.viewport_size)
    detail = np.array([window.viewport.project_point(tuple(p))[:2] for p in [*knee, *toe[:2]]])
    assert np.all((detail >= 0) & (detail <= size))
    assert np.max(np.ptp(detail, axis=0) / size) > .5
    # Both the knee and the perpendicular host need visible surface area.
    for polygon in (knee, toe):
        triangle = np.array([window.viewport.project_point(tuple(p))[:2] for p in polygon])
        assert abs(np.linalg.det(triangle[1:] - triangle[0])) > 1


def test_saved_example_opens_with_rainflow_ready_and_saves_a_portable_copy(window, qapp, tmp_path, monkeypatch):
    resource = example.example_archive()
    fingerprint = hashlib.sha256(resource.read_bytes()).digest()
    with resource.open("rb") as stream, ZipFile(stream) as archive:
        preset = json.loads(archive.read("preset.json"))
    window._actions[ACTION].trigger()
    panel = window.panels["Results"]
    # Allow the software renderer to open the bundled mesh and display the
    # saved stress contour before the first pick can be previewed.
    wait_until(qapp, lambda: panel.fatigue_dialog is not None and panel.fatigue_dialog.prepared is not None,
               timeout=60)
    dialog = panel.fatigue_dialog
    backing = window.path
    assert backing.is_file() and backing.name == example.PROJECT_FILE and not window.session.read_only
    assert str(backing) not in window._recent_paths
    assert window._view_mode == "results" and window.active_job_id == preset["job_id"]
    assert window.viewport.displayed_result_identity == (window.project.document_id, preset["job_id"])
    assert panel.field_name() == preset.get("field", "stress_global_xx_top")
    assert panel.frame.value() == window.shape_index == preset.get("frame", 0)
    assert dialog.isVisible() and dialog.run_button.isEnabled()
    face_ids = {patch.ref.id for patch in window.viewport._scene.faces
                if patch.ref is not None and patch.ref.kind == "face"}
    assert face_ids == set(preset.get("context_face_ids", (dialog.prepared.face_id,)))
    if "face_id" in preset:
        assert dialog.prepared.face_id == preset["face_id"]
    assert dialog.prepared.face_id in face_ids
    focus_faces = set(preset.get("focus_face_ids", face_ids))
    points = np.unique(np.concatenate([polygon for patch in window.viewport._scene.faces
                                       if patch.ref is not None and patch.ref.kind == "face" and patch.ref.id in focus_faces
                                       for polygon in patch.polygons]), axis=0)
    screen = np.array([window.viewport.project_point(tuple(point))[:2] for point in points])
    size = np.array(window.viewport.viewport_size)
    assert np.all((screen >= 0) & (screen <= size))
    assert np.max(np.ptp(screen, axis=0) / size) > .5
    assert dialog.location_kind.currentData() == "line" and dialog.method.currentData() == "time_series"
    assert dialog.measure.currentData() == "normal_to_weld"
    assert dialog.element_ids == (preset["element_id"],)
    assert (dialog.target.kind, dialog.target.id) == ("edge", preset["edge_id"])
    assert dialog.surface.currentData() == preset["surface"] and dialog.side.currentData() == preset["side"]
    if "direction" in preset:
        assert tuple(map(float, dialog.direction.text().split(","))) == pytest.approx(preset["direction"])
    assert len(dialog.prepared.stations) > 1 and all(len(s.probes) == 2 for s in dialog.prepared.stations)
    count = dialog.case_series.count()
    assert count > 1 and all(dialog.case_series.item(i).checkState() == Qt.Checked for i in range(count))
    assert float(dialog.thickness.text()) == pytest.approx(dialog.data.thickness_mm_for(dialog.element_ids))
    dialog.run_button.click()
    wait_until(qapp, lambda: dialog.result is not None)
    assert max(point.damage for point in dialog.result.points) > 0
    assert dialog.table.rowCount() == len(dialog.result.points)
    dialog.close()
    destination = tmp_path / "My bracket study.anyfem"
    monkeypatch.setattr(window.dialogs, "save_file", lambda **_: str(destination))
    window.save_project()
    assert window.path == destination and backing.is_file()
    job = window.project.jobs[preset["job_id"]]
    assert ArtifactStore(destination).verify(window.project.artifacts[job.result_artifact_id])
    window.new_project()
    wait_until(qapp, lambda: not backing.parent.exists())
    window.open_project(str(destination))
    dataset = window.result_datasets[preset["job_id"]]
    assert window.retained_result_mesh(preset["job_id"]).num_elements > 0
    assert len(dataset.frames) == count and preset["field"] in dataset.field_keys
    panel.activate_job(preset["job_id"])
    wait_until(qapp, lambda: window.viewport.displayed_result_identity == (window.project.document_id, preset["job_id"]))
    reopened = panel.open_fatigue()
    wait_until(qapp, lambda: reopened.data is not None)
    assert reopened.case_series.count() == count and preset["element_id"] in reopened.data.mesh.shells
    reopened.close()
    assert hashlib.sha256(resource.read_bytes()).digest() == fingerprint
