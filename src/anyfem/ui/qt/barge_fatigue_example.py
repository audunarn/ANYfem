"""Open a private working copy of the packaged, solved bracket-weld example."""

from importlib import resources
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import numpy as np

from ...presentation.workspace import apply_camera, camera_record


PROJECT_FILE = "Barge5Fatigue.anyfem"


def example_archive():
    return resources.files("anyfem").joinpath("example_data", "barge5_fatigue.zip")


def _frame_bracket(viewport, focus_faces, toe_nodes, camera_direction):
    """Fit the local detail and weld, keeping its larger supporting sheets visible."""
    polygons = []
    toe_points = {}
    for patch in viewport._scene.faces:
        if patch.ref is None or patch.ref.kind != "face":
            continue
        if patch.ref.id in focus_faces:
            polygons.extend(patch.polygons)
        if patch.polygon_node_ids is not None:
            for ids, polygon in zip(patch.polygon_node_ids, patch.polygons):
                toe_points.update((node, point) for node, point in zip(ids, polygon) if node in toe_nodes)
    if not polygons:
        raise ValueError("The example bracket has no visible stress contours")
    if toe_points:
        polygons.append(list(toe_points.values()))
    points = np.concatenate(polygons)
    target = (points.min(axis=0) + points.max(axis=0)) / 2
    if camera_direction is None:
        polygon = polygons[0]
        normal = np.cross(polygon[1] - polygon[0], polygon[2] - polygon[0])
    else:
        normal = np.asarray(camera_direction, dtype=float)
    if normal.shape != (3,) or not np.all(np.isfinite(normal)) or np.linalg.norm(normal) == 0:
        raise ValueError("The example camera direction must be a finite, nonzero 3D vector")
    normal = normal / np.linalg.norm(normal)
    up = (0, 1, 0) if abs(normal[2]) > .95 else (0, 0, 1)
    right = np.cross(up, normal)
    right /= np.linalg.norm(right)
    up = np.cross(normal, right)
    offsets = points - target
    width, height = viewport.viewport_size
    half_height = max(np.max(np.abs(offsets @ up)),
                      np.max(np.abs(offsets @ right)) * height / max(width, 1))
    camera = camera_record(viewport.canvas)
    depth = np.max(np.abs(offsets @ normal))
    distance = depth + max(2 * camera["near"], 1.3 * half_height / np.tan(camera["fov"] / 2))
    camera.update(position=target + distance * normal, target=target, up=up)
    apply_camera(viewport.canvas, camera)


def open_barge_fatigue_example(window):
    from ...application.fatigue import require_fatigue

    require_fatigue()
    if not window._confirm_discard():
        return
    folder = TemporaryDirectory(prefix="anyfem-barge5-fatigue-")
    project_path = Path(folder.name) / PROJECT_FILE
    try:
        with example_archive().open("rb") as source, ZipFile(source) as archive:
            preset = json.loads(archive.read("preset.json"))
            archive.getinfo(PROJECT_FILE)
            archive.extractall(folder.name)
        window._opening_example_path = project_path
        try:
            window.open_project(str(project_path))
        finally:
            window._opening_example_path = None
    except Exception:
        folder.cleanup()
        raise
    window._fatigue_example_copy = folder
    window._release_fatigue_example = lambda: release_example_copy(window)
    job = preset["job_id"]
    dataset = window.result_datasets.get(job)
    if dataset is None:
        raise ValueError("The bracket example's saved stress result is unavailable")
    project = window.project
    window.active_job_id = job
    panel = window.panels["Results"]
    window.notebook.select("Results")
    window.show_result_browser()

    def current():
        return (not window._closing and window.project is project and window.active_job_id == job
                and window.result_datasets.get(job) is dataset)

    def show_fatigue(focus_faces, toe_nodes):
        if current():
            _frame_bracket(window.viewport, focus_faces, toe_nodes, preset.get("camera_direction"))
            dialog = panel.open_fatigue()
            if dialog is not None:
                dialog.configure_weld_history(preset)
                window.set_status("Internal bracket in view. Use View → Show all, then Fit, to see the full barge.")

    def show_stress():
        if not current():
            return
        panel.refresh()
        field = preset.get("field", "stress_global_xx_top")
        if field not in dataset.field_keys or panel.field.findText(field) < 0:
            raise ValueError(f"The example stress field {field!r} is unavailable")
        from anygeometry.entities import EntityRef
        mesh = window.retained_result_mesh(job)
        face = preset.get("face_id")
        if face is None:
            face = next(fid for fid, elements in mesh.elements_of_face.items() if preset["element_id"] in elements)
        bracket_faces = set(preset.get("context_face_ids", (face,)))
        if face not in bracket_faces or not bracket_faces.issubset(mesh.elements_of_face):
            raise ValueError("The example bracket view must contain the gauge face and use submitted shell faces")
        focus_faces = set(preset.get("focus_face_ids", bracket_faces))
        if not focus_faces or not focus_faces.issubset(bracket_faces):
            raise ValueError("The example camera focus must use visible bracket faces")
        toe_nodes = set(mesh.nodes_of_edge[preset["edge_id"]])
        # Ordinary Show all restores the rest of the hull.
        visible = window.viewport.visibility
        visible.set_hidden(EntityRef("face", fid) for fid in mesh.elements_of_face if fid not in bracket_faces)
        visible.set_hidden(EntityRef("edge", eid) for eid in mesh.elements_of_edge if eid != preset["edge_id"])
        window.tree.refresh_visibility()
        panel.field.setCurrentText(field)
        if "frame" in preset:
            if not 0 <= preset["frame"] < len(dataset.frames):
                raise ValueError("The example stress frame is unavailable")
            panel._set_frame_index(preset["frame"])
        panel.cancel_pending_display()
        panel.show_results(on_ready=lambda: show_fatigue(focus_faces, toe_nodes))

    ready = window.guarded(show_stress)
    if not window.prepare_result_dataset(dataset, on_ready=ready):
        ready()


def release_example_copy(window):
    """Wait for dataset reader retirement before removing its working files."""
    folder = getattr(window, "_fatigue_example_copy", None)
    window._fatigue_example_copy = None
    if folder is None:
        return
    retirement = getattr(window, "_result_retirement", None)
    if retirement is None or retirement.done():
        folder.cleanup()
    else:
        retirement.add_done_callback(lambda _completed: folder.cleanup())
