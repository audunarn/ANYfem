import os
import numpy as np
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt tests disabled',allow_module_level=True)
from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from anygeometry import EntityRef
from anyfem import commands
from anyfem.selection import plate_boundary_edge_ids
from test_qt_geometry_actions import qapp,window


def _point(window,world):
    canvas=window.viewport.canvas; x,y,_=canvas.project_point(world); ratio=canvas.event_widget.devicePixelRatioF()
    return QPoint(round(x/ratio),round(y/ratio))


def _drag(window,qapp,start,end,modifiers=Qt.NoModifier):
    surface=window.viewport.canvas.event_widget
    QTest.mouseMove(surface,start); QTest.mousePress(surface,Qt.LeftButton,modifiers,start)
    for t in np.linspace(0,1,12)[1:]:
        QTest.mouseMove(surface,QPoint(round(start.x()+(end.x()-start.x())*t),round(start.y()+(end.y()-start.y())*t)),10)
        qapp.processEvents()
    QTest.mouseRelease(surface,Qt.LeftButton,modifiers,end);qapp.processEvents()


def test_native_window_drag_selects_all_six_lower_panel_segments(window,qapp):
    window.run(commands.AddStiffenedPanel(4,6,1,u_direction=(0,1,0),v_direction=(1,0,0)))
    window.viewport.set_view('top');window.viewport.fit();window._quick_select('plate_edge');qapp.processEvents()
    geometry=window.project.geometry; eligible=plate_boundary_edge_ids(window.project)
    lower={edge for edge in eligible if np.allclose(geometry.sample_edge(edge,[0,1])[:,1],0)}
    assert len(lower)==6
    a=_point(window,(-.15,.15,0));b=_point(window,(6.15,-.15,0))
    assert a.x()<b.x()
    _drag(window,qapp,a,b)
    assert {ref.id for ref in window.selection.items}==lower
    assert window.selection.filter.flavor=='plate_edge' and window.selection.mode=='edge'
    assert window.quick_selection_actions['plate_edge'].isChecked()
    member=next(edge for edge in geometry.edges if geometry.members_using_edge(edge))
    assert member not in eligible
    window.selection.apply([EntityRef('edge',member)])
    assert {ref.id for ref in window.selection.items}==lower
    window._quick_select('edge');qapp.processEvents()
    assert not window.selection.filter.flavor and not window.quick_selection_actions['plate_edge'].isChecked()
    assert not (eligible&{line.ref.id for line in window.viewport._scene.lines if line.ref and line.ref.kind=='edge'})


def test_native_edge_click_and_shift_exclude_stiffeners(window,qapp):
    window.run(commands.AddStiffenedPanel(4,6,1))
    window.viewport.set_view('top');window.viewport.fit();window._quick_select('plate_edge');qapp.processEvents()
    geometry=window.project.geometry; eligible=sorted(plate_boundary_edge_ids(window.project))
    surface=window.viewport.canvas.event_widget
    for edge,modifier in zip(eligible[:2],(Qt.NoModifier,Qt.ShiftModifier)):
        QTest.mouseClick(surface,Qt.LeftButton,modifier,_point(window,geometry.sample_edge(edge,[.37])[0]));qapp.processEvents()
    assert {ref.id for ref in window.selection.items}==set(eligible[:2])


def test_edge_flavor_refreshes_after_edit_undo_and_hides_pick_geometry(window,qapp):
    window.run(commands.AddStiffenedPanel(4,6,1))
    window.viewport.set_view('top');window.viewport.fit();window._quick_select('plate_edge');qapp.processEvents()
    initial=window.selection.filter.eligible_ids
    edge=next(iter(initial)); ref=EntityRef('edge',edge)
    window.viewport.visibility.set_hidden([ref])
    window.show_geometry();qapp.processEvents()
    assert not any(ref in line.pick_owners for line in window.viewport._scene.lines)
    window.run(commands.AddStiffenedPanel(2,2,1,origin=(8,0,0)))
    qapp.processEvents()
    assert initial<window.selection.filter.eligible_ids
    window.undo();qapp.processEvents()
    assert window.selection.filter.flavor=='plate_edge' and window.selection.filter.eligible_ids==initial


def test_native_crossing_drag_excludes_internal_stiffener(window,qapp):
    window.run(commands.AddStiffenedPanel(4,6,1,u_direction=(0,1,0),v_direction=(1,0,0)))
    window.viewport.set_view('top');window.viewport.fit();window._quick_select('plate_edge');qapp.processEvents()
    geometry=window.project.geometry
    lower={i for i in window.selection.filter.eligible_ids if np.allclose(geometry.sample_edge(i,[0,1])[:,1],0)}
    # A crossing gesture includes the partially intersected lower segment;
    # the internal stiffener crossing the same region remains ineligible.
    start=_point(window,(.7,.2,0));end=_point(window,(.3,-.2,0))
    assert start.x()>end.x()
    _drag(window,qapp,start,end)
    assert window.selection.items and {r.id for r in window.selection.items}<=lower
