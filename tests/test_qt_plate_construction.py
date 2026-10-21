"""Focused real-widget regressions for existing-vertex boundary drafts."""
import os
import numpy as np
import pytest
if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from anyfem import commands
from test_qt_geometry_actions import qapp, window


def vertices(window, coordinates=((0,0,0),(1,0,0),(1,1,0),(0,1,0))):
    ids = [window.run(commands.AddPoint(*xyz)) for xyz in coordinates]
    refs = [window.project.geometry.entity_ref("vertex", identifier) for identifier in ids]
    return ids, refs


def test_selection_fields_preserve_manual_edit_and_switch(window):
    ids, refs = vertices(window)
    editor = window.panels["Geometry"]
    window.selection.set_mode("vertex");window.selection.restore(refs)
    editor.choice.setCurrentIndex(editor.choice.findData("AddPlate"))
    field = editor.fields["vertex_ids"][0]
    assert field.text() == ", ".join(map(str, ids))
    field.setText("4, 3, 2, 1");editor.populate_selection()
    assert field.text() == "4, 3, 2, 1"
    window.selection.restore(refs[:3]);assert field.text() == ", ".join(map(str, ids[:3]))
    editor.choice.setCurrentIndex(editor.choice.findData("AddFace"))
    assert editor.fields["edge_ids"][0].text() == ""
    editor.choice.setCurrentIndex(editor.choice.findData("AddPolyline"))
    assert editor.fields["vertex_ids"][0].text() == ", ".join(map(str, ids[:3]))


def test_selected_three_points_create_a_triangle_in_viewport(window, qapp):
    coordinates = ((0, 0, 0), (2, 0, 0), (2, 1, 0))
    ids, refs = vertices(window, coordinates)
    editor = window.panels["Geometry"]
    window.selection.set_mode("vertex")
    window.selection.restore(refs)
    editor.choice.setCurrentIndex(editor.choice.findData("AddPlate"))
    assert editor.fields["vertex_ids"][0].text() == ", ".join(map(str, ids))

    editor.apply.click()
    qapp.processEvents()

    face_id = next(iter(window.project.geometry.faces))
    patch = next(patch for patch in window.viewport._scene.faces
                 if patch.ref == window.project.geometry.entity_ref("face", face_id))
    assert len(patch.polygons) == 1
    np.testing.assert_allclose(patch.polygons[0], coordinates)


def test_draft_preview_validation_commit_undo(window):
    ids, refs = vertices(window)
    before = len(window.project.geometry.edges)
    draft = window.geometry_actions.start_plate(refs)
    assert draft.ids == ids
    assert len(window.project.geometry.edges) == before
    draft.order.setText(", ".join(map(str, (ids[0], ids[2], ids[1], ids[3]))))
    draft.commit()
    assert draft.feedback.text() and window.viewport.plate_draft is draft
    assert not window.project.geometry.faces
    draft.order.setText(", ".join(map(str, ids)))
    draft.commit();draft.commit()
    assert len(window.project.geometry.faces) == 1
    assert len(window.project.geometry.edges) == before + 4
    window.undo()
    assert not window.project.geometry.faces and len(window.project.geometry.edges) == before


@pytest.mark.parametrize("order", [(0,1), (0,1,2,0)])
def test_invalid_draft_stays_editable(window, order):
    ids, refs = vertices(window)
    draft = window.geometry_actions.start_plate([refs[index] for index in order])
    draft.commit()
    assert draft.feedback.text() and window.viewport.plate_draft is draft
    draft.reject();assert window.viewport.plate_draft is None


def test_nonplanar_and_backend_cancel(window):
    ids, refs = vertices(window, ((0,0,0),(1,0,0),(1,1,0),(0,1,1)))
    draft = window.geometry_actions.start_plate(refs)
    draft.commit()
    assert draft.feedback.text() and not window.project.geometry.faces
    window.viewport.switch_backend("software")
    assert window.viewport.plate_draft is None


def test_shift_context_preserves_selection_and_offers_plate(window, qapp):
    ids, refs = vertices(window)
    window.selection.set_mode("vertex");window.selection.restore(refs)
    surface = window.viewport.canvas.event_widget
    window.viewport.context_menu_at(surface, QPoint(0,0), preserve_selection=True)
    assert tuple(window.selection.ordered_items) == tuple(refs)
    assert "Add plate" in [action.text() for action in window.geometry_actions.menu.actions()]
    window.geometry_actions.menu.close()


def test_click_order_and_enter_cancel(window, qapp):
    ids, refs = vertices(window)
    draft = window.geometry_actions.start_plate()
    for ref in refs:draft.add_vertex(ref)
    draft.add_vertex(refs[-1])
    assert draft.ids == ids
    QTest.keyClick(window.viewport.canvas.event_widget, Qt.Key_Return)
    assert len(window.project.geometry.faces) == 1
    draft = window.geometry_actions.start_plate()
    QTest.keyClick(window.viewport.canvas.event_widget, Qt.Key_Escape)
    assert window.viewport.plate_draft is None


def test_existing_vertex_click_and_close_one_command(window, qapp, tmp_path):
    ids, refs = vertices(window)
    window.viewport.set_view("top");window.viewport.fit();qapp.processEvents()
    draft = window.geometry_actions.start_plate()
    surface = window.viewport.canvas.event_widget
    def position(identifier):
        x,y,_ = window.viewport.canvas.project_point(window.project.geometry.vertex_position(identifier))
        ratio = surface.devicePixelRatioF()
        return QPoint(round(x/ratio),round(y/ratio))
    for identifier in ids[:3]:
        QTest.mouseClick(surface,Qt.LeftButton,pos=position(identifier));qapp.processEvents()
    assert draft.ids == ids[:3]
    from pathlib import Path
    window.grab().save(str(Path(__file__).resolve().parents[1]/"reports/qt/plate-construction/live-draft.png"))
    draft.grab().save(str(Path(__file__).resolve().parents[1]/"reports/qt/plate-construction/live-order-review.png"))
    QTest.mouseClick(surface,Qt.LeftButton,pos=position(ids[3]));qapp.processEvents()
    QTest.mouseClick(surface,Qt.LeftButton,pos=position(ids[0]));qapp.processEvents()
    assert len(window.project.geometry.faces) == 1
    window.undo()
    assert not window.project.geometry.faces


def test_actual_shift_rightclick_and_beam_choice(window,qapp,monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    ids, refs = vertices(window)
    window.selection.set_mode("vertex");window.selection.restore(refs)
    surface=window.viewport.canvas.event_widget
    QTest.mouseClick(surface,Qt.RightButton,Qt.ShiftModifier,pos=QPoint(2,2));qapp.processEvents()
    assert tuple(window.selection.ordered_items) == tuple(refs)
    assert "Add plate" in [action.text() for action in window.geometry_actions.menu.actions()]
    window.geometry_actions.menu.close()
    edge_ids=[window.run(commands.AddLine(ids[index],ids[index+1])) for index in range(2)]
    edges=[window.project.geometry.entity_ref("edge",identifier) for identifier in edge_ids]
    assert "Add beam" in [action.text() for action in window.geometry_actions.menu_for(edges).actions()]
    # Established command contracts, with an explicit selected section and one undo group.
    chosen = next(iter(window.project.beam_sections))
    captured=[]
    execute = window.run
    def run(command):
        captured.append(command)
        return execute(command)
    monkeypatch.setattr(QInputDialog,"getItem",lambda *args:(chosen,True))
    monkeypatch.setattr(window,"run",run)
    window.geometry_actions.assign_beams(edges)
    assert len(captured)==1 and isinstance(captured[0],commands.CompositeCommand)
    assert [(item.edge_id,item.section) for item in captured[0].commands] == [(identifier,chosen) for identifier in edge_ids]
    assert all(window.project.edge_sections[identifier] == chosen for identifier in edge_ids)
    window.undo()
    assert all(identifier not in window.project.edge_sections for identifier in edge_ids)


def test_invalid_text_and_replacement_cancel(window):
    ids,refs=vertices(window)
    draft=window.geometry_actions.start_plate(refs)
    draft.edit_order("oops")
    assert draft.ids == [] and "preview unavailable" in draft.summary.text()
    window.new_project()
    assert window.viewport.plate_draft is None
    draft.commit()
    assert "changed" in draft.feedback.text() and not window.project.geometry.faces


def test_triangle_and_read_only_refusal(window):
    ids,refs=vertices(window)
    draft=window.geometry_actions.start_plate(refs[:3])
    draft.commit()
    assert len(window.project.geometry.faces)==1
    draft=window.geometry_actions.start_plate(refs)
    window.project.read_only_reason="test read-only"
    draft.commit()
    assert "read-only" in draft.feedback.text()
    assert len(window.project.geometry.faces)==1
    with pytest.raises(PermissionError):window.geometry_actions.start_plate(refs)
    window.project.read_only_reason=None
