"""Native grouped picking and exact record highlight scope on generated plates."""
import os
import numpy as np
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt tests disabled',allow_module_level=True)
from PySide6.QtCore import QPoint,Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from anygeometry import EntityRef
from anyfem import commands
from anyfem.selection import entity_tag
from test_qt_geometry_actions import qapp,window


def prepare(window,qapp,monkeypatch,smooth=False):
    window.run(commands.AddStiffenedPanel(4,20,1))
    if smooth:monkeypatch.setattr(window.viewport,'_smooth_options',lambda patches:{'smooth_angle':35})
    window.resize(1500,900);window.show_geometry(reset_view=True)
    window.viewport.set_view('top');window.viewport.fit();window._quick_select('face');qapp.processEvents()
    return [EntityRef('face',i) for i in sorted(window.project.geometry.faces)]


def click(window,qapp,world,modifier=Qt.NoModifier):
    canvas=window.viewport.canvas;surface=canvas.event_widget;x,y,_=canvas.project_point(world);ratio=surface.devicePixelRatioF()
    QTest.mouseClick(surface,Qt.LeftButton,modifier,QPoint(round(x/ratio),round(y/ratio)));qapp.processEvents()


@pytest.mark.parametrize('smooth',[False,True])
@pytest.mark.parametrize('view',['top','iso'])
def test_grouped_pressure_panel_has_no_unselected_triangle_wedges(window,qapp,monkeypatch,smooth,view):
    faces=prepare(window,qapp,monkeypatch,smooth)
    click(window,qapp,(2,10.3,0))
    assert set(window.selection.items)==set(faces)
    window.run_many(commands.AddPressure(ref,1000) for ref in window.selection.items)
    assert {load.ref for load in window.project.load_case('default').pressures}==set(faces)
    window.viewport.set_view(view);window.viewport.fit();qapp.processEvents()
    canvas=window.viewport.canvas
    pixels=np.asarray(canvas.capture_image().convert('RGB'),dtype=int)
    expected=np.asarray(QColor(canvas._highlight_fill).getRgb()[:3])
    for bay in range(20):
        for x in (.8,1.7,3.1):
            for fraction in (.23,.47,.79):
                sx,sy,_=canvas.project_point((x,bay+fraction,0))
                patch=pixels[round(sy)-1:round(sy)+2,round(sx)-1:round(sx)+2]
                assert patch.size and np.min(np.max(np.abs(patch-expected),axis=2))<8,(view,smooth,bay,x,fraction)


def test_partial_record_highlights_actual_bay_and_group_pick_ignores_hidden_faces(window,qapp,monkeypatch):
    faces=prepare(window,qapp,monkeypatch,True)
    actual=faces[9]
    window.run(commands.AddPressure(actual,1000))
    window.selection.clear();window.viewport.canvas.set_preselection(None)
    window.viewport.set_collection_highlight([actual]);qapp.processEvents()
    assert set(window.viewport.canvas._highlighted_tags)=={entity_tag(actual)}
    renderer=window.viewport.canvas._renderer
    entries=window.viewport.canvas._display_entries()
    sheet=next(e for e in entries.values() if e['handle'].mesh.triangle_count>=40)
    selected=renderer.masks[id(sheet['handle'])]['selected_elements']
    assert len(selected)==2  # The bay's two real fan triangles, never half a bay.
    pixels=np.asarray(window.viewport.canvas.capture_image().convert('RGB'),dtype=int)
    expected=np.asarray(QColor(window.viewport.canvas._highlight_fill).getRgb()[:3])
    for bay in (0,9,19):
        x,y,_=window.viewport.canvas.project_point((1.3,bay+.31,0))
        patch=pixels[round(y)-1:round(y)+2,round(x)-1:round(x)+2]
        distance=np.min(np.max(np.abs(patch-expected),axis=2))
        assert (distance<8)==(bay==9)
    window.viewport.visibility.set_hidden([faces[0]])
    window.show_geometry();click(window,qapp,(2,10.3,0))
    assert set(window.selection.items)==set(faces[1:])
    click(window,qapp,(2,11.3,0),Qt.ShiftModifier)
    assert set(window.selection.items)==set(faces[1:])  # Shift-add is idempotent.


@pytest.mark.parametrize('smooth',[False,True])
@pytest.mark.parametrize('view',['top','iso'])
def test_hover_only_previews_visible_group_yellow_before_and_after_pressure(window,qapp,monkeypatch,smooth,view):
    faces=prepare(window,qapp,monkeypatch,smooth)
    window.viewport.set_view(view);window.viewport.fit();qapp.processEvents()
    canvas=window.viewport.canvas;surface=canvas.event_widget
    def hover():
        x,y,_=canvas.project_point((2,10.3,0));ratio=surface.devicePixelRatioF()
        QTest.mouseMove(surface,QPoint(5,5));QTest.mouseMove(surface,QPoint(round(x/ratio),round(y/ratio)));QTest.qWait(160);qapp.processEvents()
        assert not window.selection.items
        assert canvas.preselected_key.startswith('anyfem:face-group:')
        pixels=np.asarray(canvas.capture_image().convert('RGB'),dtype=int)
        expected=np.asarray(QColor('#ffd166').getRgb()[:3])
        for bay in range(20):
            x,y,_=canvas.project_point((1.3,bay+.31,0))
            patch=pixels[round(y)-1:round(y)+2,round(x)-1:round(x)+2]
            assert np.min(np.max(np.abs(patch-expected),axis=2))<8,(view,smooth,bay)
    hover()
    window.run(commands.AddPressure(faces[9],1000));window.selection.clear();qapp.processEvents()
    hover()


def test_gpu_hit_local_triangle_cache_promotes_to_full_semantic_hover_group(window,qapp,monkeypatch):
    from any3dview.selection import PickOwner,SelectionHit
    faces=prepare(window,qapp,monkeypatch,True)
    canvas=window.viewport.canvas
    entry=next(e for e in canvas._display_entries().values() if e['handle'].mesh.triangle_count==40)
    owner=PickOwner(entity_tag(faces[0]),'geometry.face',10)
    # Reproduce the GPU ID readback's hit-local optimization: it intentionally
    # caches one triangle, which is insufficient as a collapsed-group preview.
    canvas._cache_hit_elements(entry['handle'],entry,'triangle',0,(owner,))
    canvas._set_hit_preselection(owner.key)
    assert len(canvas._renderer.masks[id(entry['handle'])]['preselected_elements'])==1
    window.viewport._handle_hover(SelectionHit(owner,primitive=0,depth=0.))
    assert canvas.preselected_key.startswith('anyfem:face-group:')
    assert not canvas._preselection_from_hit
    assert len(canvas._renderer.masks[id(entry['handle'])]['preselected_elements'])==40
    pixels=np.asarray(canvas.capture_image().convert('RGB'),dtype=int)
    expected=np.asarray(QColor('#ffd166').getRgb()[:3])
    for bay in range(20):
        x,y,_=canvas.project_point((1.3,bay+.31,0))
        assert np.max(np.abs(pixels[round(y),round(x)]-expected))<8
