"""Real sketch selectors and preview/apply/edit for the everyday loft inputs."""
import os
import numpy as np
import pytest
if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1", allow_module_level=True)

from anygeometry import to_dict
from anyfem import commands
from anyfem.ui.qt.loft import LoftDialog
from test_qt_geometry_actions import qapp, window
from test_loft_basic_workflow import (
    BASIC_PROFILES, add_basic_sketches, basic_definition,
    oriented_output_loops, assert_reversed_output_loops,
)


def chosen_dialog(window, kind):
    add_basic_sketches(window.run, kind)
    dialog = LoftDialog(window)
    assert dialog.sketches[0].count() == 3
    dialog.sketches[0].setCurrentIndex(1)
    dialog.sketches[1].setCurrentIndex(2)
    return dialog


@pytest.mark.parametrize("kind", BASIC_PROFILES)
def test_basic_profile_preview_apply_undo_edit_save_reopen(window, qapp, tmp_path, kind):
    dialog = chosen_dialog(window, kind)
    before = to_dict(window.project.geometry)
    dialog.preview()
    assert dialog.apply_button.isEnabled(), dialog.status.text()
    assert to_dict(window.project.geometry) == before
    assert window.viewport._scene.faces
    assert any(line.annotation for line in window.viewport._scene.lines)
    assert window.viewport._scene.arrows
    frozen_options = dialog.command.options()
    dialog.apply()
    feature = window.project.geometry.features.records[-1]
    assert feature.kind == "geometry.loft"
    assert all(feature.parameters[key] == value for key, value in frozen_options.items())
    normals = {key: window.project.geometry.face_normal_many(ref.id, [(.5, .5)])[0]
               for key, ref in feature.outputs.items() if ref.kind == "face"}
    original_loops = oriented_output_loops(window.project.geometry, feature)
    assert normals
    window.undo()
    assert not window.project.geometry.faces
    window.redo()
    assert window.project.geometry.faces
    editor = LoftDialog(window)
    editor.feature.setCurrentIndex(editor.feature.findData(feature.feature_id))
    assert not editor.auto_match.isChecked()
    editor.flip.setChecked(True)
    editor.preview()
    assert editor.apply_button.isEnabled(), editor.status.text()
    editor.apply()
    changed = window.project.geometry.features.get(feature.feature_id)
    assert_reversed_output_loops(original_loops, oriented_output_loops(window.project.geometry, changed))
    for key, normal in normals.items():
        np.testing.assert_allclose(window.project.geometry.face_normal_many(changed.outputs[key].id, [(.5, .5)])[0], -normal, rtol=1e-12, atol=1e-12)
        assert np.dot(window.project.geometry.face_normal(changed.outputs[key].id, .5, .5), normal) < 0
    path = tmp_path / f"{kind}-loft.anyfem"
    window.save_project(path=str(path))
    window.new_project()
    window.open_project(str(path))
    persisted = window.project.geometry.features.get(feature.feature_id)
    assert persisted.kind == "geometry.loft" and persisted.parameters["flip_normal"]
    assert window.project.geometry.faces
    dialog.deleteLater()
    editor.deleteLater()


@pytest.mark.parametrize("kind", ("triangle", "square", "circle"))
def test_closed_profile_manual_seam_reverse_and_cancel(window, qapp, kind):
    dialog = chosen_dialog(window, kind)
    before = to_dict(window.project.geometry)
    dialog.preview()
    assert dialog.apply_button.isEnabled(), dialog.status.text()
    for start in dialog.starts:
        start.setValue(1)
    for reverse in dialog.reversals:
        reverse.setChecked(True)
    assert not dialog.auto_match.isChecked()
    assert dialog.command is None
    dialog.flip.setChecked(True)
    dialog.preview()
    assert dialog.apply_button.isEnabled(), dialog.status.text()
    assert dialog.command.first_start == dialog.command.second_start == 1
    assert dialog.command.reverse_first and dialog.command.reverse_second and dialog.command.flip_normal
    dialog.reject()
    assert to_dict(window.project.geometry) == before
    dialog.deleteLater()


def test_mixed_open_closed_preview_refuses_without_mutation(window, qapp):
    window.run(commands.AddPlaneSketch(basic_definition("line"), (0, 0, 0), (0, 0, 1), (1, 0, 0)))
    window.run(commands.AddPlaneSketch(basic_definition("triangle"), (0, 0, 3), (0, 0, 1), (1, 0, 0)))
    dialog = LoftDialog(window)
    dialog.sketches[0].setCurrentIndex(1)
    dialog.sketches[1].setCurrentIndex(2)
    before = to_dict(window.project.geometry)
    dialog.preview()
    assert not dialog.apply_button.isEnabled()
    assert dialog.command is None
    assert "open" in dialog.status.text().lower() and "closed" in dialog.status.text().lower()
    assert to_dict(window.project.geometry) == before
    dialog.reject()
    dialog.deleteLater()


def select_sketch_rows(window, qapp, identifiers):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    for position, identifier in enumerate(identifiers):
        item = window.tree._rows["Sketches"][identifier][0]
        window.tree.expand(item.parent().index())
        window.tree.scrollTo(item.index())
        qapp.processEvents()
        QTest.mouseClick(window.tree.viewport(), Qt.LeftButton,
                         Qt.ControlModifier if position else Qt.NoModifier,
                         pos=window.tree.visualRect(item.index()).center())
        qapp.processEvents()


def click_construct(window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    panel = window.panels["Geometry"]
    panel.choice.setCurrentIndex(panel.choice.findData("Loft"))
    window.notebook.select("Geometry")
    qapp.processEvents()
    assert panel.apply.text() == "Construct" and panel.apply.isVisible()
    QTest.mouseClick(panel.apply, Qt.LeftButton)
    qapp.processEvents()


@pytest.mark.parametrize("kind", BASIC_PROFILES)
def test_two_sketch_rows_one_construct(window, qapp, kind):
    ids = add_basic_sketches(window.run, kind)
    before = to_dict(window.project.geometry)
    select_sketch_rows(window, qapp, ids)
    click_construct(window, qapp)
    geometry = window.project.geometry
    assert geometry.faces
    assert geometry.features.records[-1].kind == "geometry.loft"
    assert not getattr(window, "_loft_dialog", None)
    for name in ("vertices", "edges"):
        actual = to_dict(geometry)[name]
        original = before[name]
        for item in original:
            assert item in actual
    window.undo()
    assert not window.project.geometry.faces
    assert len(window.project.geometry.features.records) == 2
    window.redo()
    assert window.project.geometry.faces


@pytest.mark.parametrize("kind", ("line", "circle"))
def test_two_picked_edges_resolve_authored_profile(window, qapp, kind):
    from anygeometry import EntityRef
    from anygeometry.loft import loft_profiles_from_sketch
    ids = add_basic_sketches(window.run, kind)
    chains = [loft_profiles_from_sketch(window.project.geometry, i)[0][1] for i in ids]
    window.selection.restore(tuple(EntityRef("edge", chain[0]) for chain in chains))
    click_construct(window, qapp)
    feature = window.project.geometry.features.records[-1]
    assert feature.kind == "geometry.loft"
    assert len(feature.inputs["first_edges"]) == len(chains[0])
    assert len(feature.inputs["second_edges"]) == len(chains[1])


def test_direct_selection_refusal_is_atomic(window, qapp):
    from anygeometry import EntityRef
    from anyfem.ui.qt.loft import construct_selected_loft
    ids = add_basic_sketches(window.run, "circle")
    before = to_dict(window.project.geometry)
    with pytest.raises(ValueError, match="exactly two"):
        construct_selected_loft(window)
    vertex = next(iter(window.project.geometry.vertices))
    window.selection.restore((EntityRef("vertex", vertex), EntityRef("vertex", vertex+1)))
    with pytest.raises(ValueError, match="points are not profiles"):
        construct_selected_loft(window)
    assert to_dict(window.project.geometry) == before


def test_two_faces_one_construct_and_undo(window, qapp):
    from anygeometry import EntityRef
    faces = []
    for points in (((0,0,0),(2,0,0),(2,2,0),(0,2,0)),
                   ((.2,.1,3),(1.8,.1,2.8),(1.8,1.7,2.8),(.2,1.7,3))):
        vertices = [window.run(commands.AddPoint(*p)) for p in points]
        faces.append(window.run(commands.AddPlate(vertices)))
    before = to_dict(window.project.geometry)
    window.selection.restore(tuple(EntityRef("face", f) for f in faces))
    click_construct(window, qapp)
    geometry = window.project.geometry
    feature = geometry.features.records[-1]
    assert feature.kind == "geometry.loft", window._status.get()
    generated = {ref.id for ref in feature.outputs.values() if ref.kind == "face"}
    assert len(generated) >= 4
    assert generated.isdisjoint(faces)
    assert set(geometry.faces) == set(faces) | generated
    current = to_dict(geometry)
    for name in ("vertices", "edges", "faces"):
        assert all(item in current[name] for item in before[name])
    window.undo()
    assert set(window.project.geometry.faces) == set(faces)
    for name in ("vertices", "edges", "faces"):
        assert to_dict(window.project.geometry)[name] == before[name]


def test_construct_button_reports_wrong_selection_without_mutation(window, qapp):
    before = to_dict(window.project.geometry)
    click_construct(window, qapp)
    assert "Select exactly two" in window._status.get()
    assert to_dict(window.project.geometry) == before


def test_same_circle_pieces_are_not_two_profiles(window, qapp):
    from anygeometry import EntityRef
    from anygeometry.loft import loft_profiles_from_sketch
    ids = add_basic_sketches(window.run, "circle")
    edges = loft_profiles_from_sketch(window.project.geometry, ids[0])[0][1]
    before = to_dict(window.project.geometry)
    window.selection.restore(tuple(EntityRef("edge", e) for e in edges[:2]))
    click_construct(window, qapp)
    assert "two different profiles" in window._status.get()
    assert to_dict(window.project.geometry) == before
