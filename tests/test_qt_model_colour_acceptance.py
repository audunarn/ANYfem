"""Toolbar colour workflows preserve engineering data and cached defaults."""
import os
from dataclasses import replace
from uuid import uuid4

import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolBar
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.document import canonical_hash
from anyfem.io.project_file import project_to_dict
from anyfem.mesh.refinement import Refinement
from test_qt_geometry_actions import qapp, window


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch, qapp):
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QFontDatabase, QFont
    from anyfem.ui.qt import app
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    qapp.setFont(QFont("Segoe UI", 9))
    monkeypatch.setattr(app, "QSettings", lambda *_: QSettings(str(tmp_path / "view.ini"), QSettings.IniFormat))


def populated(window, qapp):
    project = default_project()
    stack = commands.CommandStack(project)
    project.add_plate_section("thick", .02, "S355")
    project.add_beam_section(replace(project.beam_sections["stiffener"], name="Other", id=str(uuid4())))
    for index, offset in enumerate((0, 2)):
        points = [stack.run(commands.AddPoint(offset+x, y)) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))]
        face = stack.run(commands.AddPlate(points))
        project.assign_plate(face, "thick" if index else "plate")
        edge = project.geometry.faces[face].loop[0].edge
        project.assign_beam(edge, "Other" if index else "stiffener")
        if not index:
            project.add_refinement(Refinement(.2, ref=project.face(face), name="Fine"))
    project.target_size = .5
    mesh = project.generate_mesh(.5)
    window._set_project(project)
    window.mesh = mesh
    window.resize(1900, 1000)
    window.show_geometry(reset_view=True)
    qapp.processEvents()
    return project


def click_action(window, qapp, action):
    toolbar = next(bar for bar in window.findChildren(QToolBar) if action in bar.actions())
    button = toolbar.widgetForAction(action)
    assert button is not None and button.isVisible() and not action.icon().isNull(), (
        action.text(), window.width(), toolbar.width(), toolbar.isVisible(), button.geometry())
    QTest.mouseClick(button, Qt.LeftButton)
    qapp.processEvents()


def colours(scene):
    return tuple(tuple(face.colors) for face in scene.faces), tuple(line.color for line in scene.lines)


def test_native_colour_buttons_work_in_model_and_mesh_and_restore_defaults(window, qapp):
    project = populated(window, qapp)
    baseline = colours(window.viewport._scene)
    digest = canonical_hash(project_to_dict(project))
    revision = window.session.revision
    actions = window._model_colour_actions
    for key, title in (("thickness", "Plate thickness"), ("section", "Beam section"), ("density", "Target mesh size")):
        click_action(window, qapp, actions[key])
        assert window._view_mode == "geometry" and actions[key].isChecked()
        assert title in window.viewport._scene.legend["title"]
        assert colours(window.viewport._scene) != baseline
    click_action(window, qapp, window._view_mode_actions["mesh"])
    assert window._view_mode == "mesh" and actions["density"].isChecked()
    assert "Actual element size" in window.viewport._scene.legend["title"]
    legend = window.viewport._colour_legend
    assert legend.height() >= legend.heightForWidth(legend.width())
    assert "0.5 m" in legend.text(), "the coarsest actual element size must have a visible key"
    for key in ("thickness", "section"):
        click_action(window, qapp, actions[key])
        assert window._view_mode == "mesh" and window.viewport._scene.legend["entries"]
    click_action(window, qapp, actions["default"])
    assert window.viewport._scene.legend is None
    click_action(window, qapp, window._view_mode_actions["geometry"])
    assert colours(window.viewport._scene) == baseline
    assert canonical_hash(project_to_dict(project)) == digest
    assert window.session.revision is revision


def test_result_and_inspection_views_disable_model_colour_buttons(window, qapp):
    populated(window, qapp)
    window._view_mode = "results"
    window._sync_view_mode_buttons()
    assert all(not action.isEnabled() for action in window._model_colour_actions.values())
    window._view_mode = "inspection_mesh"
    window._sync_view_mode_buttons()
    assert all(not action.isEnabled() for action in window._model_colour_actions.values())
