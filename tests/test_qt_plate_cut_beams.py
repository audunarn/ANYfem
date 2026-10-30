"""Qt cut action, exact trace selection and explicit physical beam creation."""
from test_qt_geometry_actions import qapp, window
from test_plate_cut_beams import project
from anyfem.io.project_file import project_to_dict
from PySide6.QtWidgets import QInputDialog
import pytest


def test_plate_right_click_cut_opens_dialog_without_mutation(window, qapp):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    p, face = project(); window._set_project(p)
    window._quick_select("face")
    window.viewport.set_view("top"); window.viewport.fit(); qapp.processEvents()
    canvas = window.viewport.canvas; surface = canvas.event_widget
    x, y, _ = canvas.project_point((1., 2., 0.))
    ratio = surface.devicePixelRatioF()
    position = QPoint(round(x / ratio), round(y / ratio))
    QTest.mouseClick(surface, Qt.LeftButton, pos=position); qapp.processEvents()
    assert p.geometry.entity_ref("face", face) in window.selection.items
    before = project_to_dict(p)
    QTest.mouseClick(surface, Qt.RightButton, pos=position); qapp.processEvents()
    menu = window.geometry_actions.menu
    assert menu is not None and menu.isVisible()
    action = next(action for action in menu.actions() if action.text() == "Cut")
    assert action.isEnabled()
    action.trigger(); menu.close(); qapp.processEvents()
    dialog = window.geometry_actions.dialogs[-1]
    assert dialog.face_id == face
    assert project_to_dict(p) == before
    dialog.reject(); window.geometry_actions.close()
    assert project_to_dict(p) == before


@pytest.mark.parametrize("read_only", ["session", "project"])
def test_plate_context_cut_disabled_for_read_only(window, read_only):
    p, face = project(); window._set_project(p)
    if read_only == "session":window.session.read_only = True
    else:p.read_only_reason = "Read-only test project"
    menu = window.geometry_actions.menu_for((p.geometry.entity_ref("face", face),))
    action = next(action for action in menu.actions() if action.text() == "Cut")
    assert not action.isEnabled()
    menu.deleteLater()


def test_context_cut_requires_single_plate(window):
    p, face = project(); window._set_project(p)
    edge = p.geometry.faces[face].loop[0].edge
    for refs in ((), (p.geometry.entity_ref("edge", edge),),
                 (p.geometry.entity_ref("face", face), p.geometry.entity_ref("edge", edge))):
        menu = window.geometry_actions.menu_for(refs)
        assert "Cut" not in [action.text() for action in menu.actions()]
        menu.deleteLater()


def test_cut_dialog_selects_trace_then_creates_beam(window, monkeypatch):
    p, face = project(); window._set_project(p)
    window.selection.set_mode("face"); window.selection.select(p.geometry.entity_ref("face", face))
    assert "Cut plate…" in window._actions and "Create beam from edge…" in window._actions
    window._actions["Cut plate…"].trigger()
    dialog = window.geometry_actions.dialogs[-1]
    dialog.origin.setText("2, 0, 0"); dialog.apply()
    assert not dialog.feedback.text(), dialog.feedback.text()
    refs = tuple(window.selection.ordered_items)
    assert refs and all(r.kind == "edge" for r in refs)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("beam", True))
    window._actions["Create beam from edge…"].trigger()
    assert len(p.geometry.members) == 1
    window.undo(); assert not p.geometry.members
    window.undo(); assert face in p.geometry.faces
    window.redo(); window.redo(); assert len(p.geometry.members) == 1


def test_cut_cancel_stale_and_read_only(window):
    p, face = project(); window._set_project(p)
    refs = (p.geometry.entity_ref("face",face),)
    before = project_to_dict(p)
    dialog = window.geometry_actions.cut_plate(refs); dialog.reject()
    assert project_to_dict(p) == before
    dialog = window.geometry_actions.cut_plate(refs)
    p.geometry.add_point(10,10,10)
    before = project_to_dict(p); dialog.apply()
    assert "changed" in dialog.feedback.text()
    assert project_to_dict(p) == before
    dialog.reject()
    window.session.read_only = True
    with pytest.raises(PermissionError):window.geometry_actions.cut_plate(refs)


def test_beam_cancel_and_disconnected_selection(window, monkeypatch):
    p, face = project(); window._set_project(p)
    edges = tuple(use.edge for use in p.geometry.faces[face].loop)
    refs = (p.geometry.entity_ref("edge", edges[0]),)
    before = project_to_dict(p)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("beam", False))
    window.geometry_actions.create_beam_from_edges(refs)
    assert project_to_dict(p) == before
    with pytest.raises(ValueError):window.geometry_actions.create_beam_from_edges((refs[0],p.geometry.entity_ref("edge",edges[2])))
    assert project_to_dict(p) == before


@pytest.mark.parametrize("mode", [1, 2])
def test_other_cut_modes(window, mode):
    from test_plate_cut_beams import plate
    from anyfem.model.workplanes import Workplane
    from anyfem.model.coordinates import CoordinateSystem
    p, face = project()
    if mode == 2:
        plate(p.geometry, ((2,-1,-1),(2,5,-1),(2,5,1),(2,-1,1)))
    else:
        system = CoordinateSystem("Cut plane", origin=(2,0,0), axis=(1,0,0), reference=(0,1,0))
        p.coordinate_systems[system.id] = system
    window._set_project(p)
    if mode == 1:window.session.active_workplane = Workplane(coordinate_system_id=system.id)
    dialog = window.geometry_actions.cut_plate((p.geometry.entity_ref("face",face),))
    dialog.mode.setCurrentIndex(mode); dialog.apply()
    assert not dialog.feedback.text(), dialog.feedback.text()
    assert all(ref.kind == "edge" for ref in window.selection.ordered_items)
    assert window.selection.ordered_items


@pytest.mark.parametrize("change", ["stale", "read_only"])
def test_beam_dialog_late_change_refused(window, monkeypatch, change):
    p, face = project(); window._set_project(p)
    edge = p.geometry.faces[face].loop[0].edge
    def choose(*args):
        if change == "stale":p.geometry.add_point(9,9,9)
        else:window.session.read_only = True
        return "beam", True
    monkeypatch.setattr(QInputDialog, "getItem", choose)
    with pytest.raises((ValueError, PermissionError)):
        window.geometry_actions.create_beam_from_edges((p.geometry.entity_ref("edge",edge),))
    assert not p.geometry.members and not p.edge_sections
