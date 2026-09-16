"""Headless app-bridge coverage for native safe-phase cancellation."""

from __future__ import annotations

import threading
import time

import anymesher.surface_mesh as surface_mesh

from anyfem.document import DocumentSession
from anyfem.mesh_jobs import MeshSettings, MeshTaskManager
from anyfem.model.project import Project
from anyfem.native_meshing import NativeMeshSettings


def test_desktop_mesh_task_cancels_before_native_recombination(monkeypatch) -> None:
    project = Project("desktop-native-cancellation")
    vertices = project.geometry.add_points(
        ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 1.0, 0.0), (1.0, 0.4, 0.0), (0.0, 1.0, 0.0))
    )
    project.geometry.add_plate(vertices)
    project.set_native_mesh_settings(
        NativeMeshSettings(target_size=0.2, backend="native")
    )
    snapshot = DocumentSession(project).snapshot()
    manager = MeshTaskManager()
    entered = threading.Event()
    release = threading.Event()
    recombined = threading.Event()
    original_triangulate = surface_mesh.triangulate_polygon
    original_recombine = surface_mesh.recombine_triangles_with_report

    def blocked_triangulate(*args, **kwargs):
        entered.set()
        assert release.wait(2.0)
        return original_triangulate(*args, **kwargs)

    def observed_recombine(*args, **kwargs):
        recombined.set()
        return original_recombine(*args, **kwargs)

    monkeypatch.setattr(surface_mesh, "triangulate_polygon", blocked_triangulate)
    monkeypatch.setattr(
        surface_mesh, "recombine_triangles_with_report", observed_recombine
    )

    try:
        manager.submit(
            "native-cancel",
            snapshot,
            MeshSettings.create(0.2, element_order="linear"),
        )
        assert entered.wait(2.0)
        assert manager.cancel("native-cancel")
        release.set()

        events = []
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            events.extend(manager.poll())
            if any(event.kind in {"completed", "cancelled", "failed"} for event in events):
                break
            time.sleep(0.01)

        kinds = [event.kind for event in events]
        assert "cancelling" in kinds
        assert "cancelled" in kinds
        assert "completed" not in kinds
        assert "failed" not in kinds
        assert not recombined.is_set()
    finally:
        release.set()
        manager.shutdown()
