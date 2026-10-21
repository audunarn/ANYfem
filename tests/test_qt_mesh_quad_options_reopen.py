"""Offscreen Qt coverage: a project with saved quad-first options reopens.

NativeMeshSettings freezes nested mappings, so the mesh panel must read the
thawed parameters when it rehydrates its quad-first controls.
"""

from __future__ import annotations

import os

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets", allow_module_level=True)
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from anyfem.io.project_file import save_project
from anyfem.model.materials import steel
from anyfem.model.project import Project
from anyfem.native_meshing import NativeMeshSettings
from anyfem.quad_first import sg1_quad_options


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    from anyfem.io import recovery
    from anyfem.ui.qt.app import QtFemWindow

    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    window = QtFemWindow(viewer_backend=os.environ.get("ANYFEM_QT_TEST_BACKEND", "software"))
    window.show()
    qapp.processEvents()
    yield window
    window.session.mark_saved()
    window.close()
    qapp.processEvents()


def test_saved_quad_options_reopen_into_the_mesh_panel(window, qapp, tmp_path):
    project = Project(name="quad options")
    project.add_material(steel("S355", 0.010))
    project.add_plate_section("plate", thickness=0.010, material="S355")
    geometry = project.geometry
    points = geometry.add_points([(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)])
    project.assign_plate(geometry.add_face(geometry.add_polyline(points, close=True)), "plate")
    saved = {**sg1_quad_options().to_dict(), "max_local_optimizations": 32}
    project.set_native_mesh_settings(NativeMeshSettings.create(
        0.25, backend="quad_first", parameters={
            "quad_options": saved,
            "quad_t3_minimum_angle": 12.0,
            "quad_t3_angle_action": "warning",
        },
    ))
    path = save_project(project, tmp_path / "quad_options.anyfem")

    window.open_project(str(path))
    qapp.processEvents()

    assert window.project.name == "quad options"
    values = window.panels["Mesh"].quad_controls.values()
    assert values["max_local_optimizations"] == 32
    assert values["orientation"] == saved["orientation"]
    assert window.panels["Mesh"].t3_minimum_angle.text() == "12.0"
    assert window.panels["Mesh"].t3_angle_action.currentText() == "warning"
    submitted = window.panels["Mesh"].settings()
    assert submitted["quad_t3_minimum_angle"] == 12.0
    assert submitted["quad_t3_angle_action"] == "warning"
