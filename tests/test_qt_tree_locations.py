"""Real Qt tree scrolling and read-only transient geometry location."""
import os
import numpy as np
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('enable Qt checks',allow_module_level=True)
from PySide6.QtCore import Qt,QPoint,QTimer
from PySide6.QtWidgets import QApplication,QMenu,QStyle,QStyleOptionSlider
from PySide6.QtTest import QTest
from anygeometry import EntityRef
from anyfem.model.project import Project
from anyfem.io.project_file import project_to_dict
from test_qt_geometry_actions import qapp,window


def model(window):
    p=Project('location')
    points=p.geometry.add_points(((0.,0.,0.),(2.,0.,0.),(2.,1.,0.),(0.,1.,0.)))
    face=p.geometry.add_plate(points)
    window._set_project(p);window.refresh_all();window.show_geometry(reset_view=True)
    return face,points


def test_tree_scrollbar_is_visible_and_scrolls_expanded_rows(window,qapp):
    face,_=model(window)
    with window.project.geometry.transaction():
        for i in range(250):window.project.geometry.add_point(i,2.,0.)
    window.tree.refresh();window.resize(700,450);window.tree.expandAll();qapp.processEvents()
    tree=window.tree;bar=tree.verticalScrollBar()
    assert tree.verticalScrollBarPolicy()==Qt.ScrollBarAlwaysOn
    assert bar.isVisible() and bar.maximum()>bar.minimum()
    assert bar.width()>=16 and bar.accessibleName()=='Model tree vertical scrollbar'
    assert not bar.style().styleHint(QStyle.SH_ScrollBar_Transient,None,bar)
    option=QStyleOptionSlider();bar.initStyleOption(option)
    handle=bar.style().subControlRect(QStyle.CC_ScrollBar,option,QStyle.SC_ScrollBarSlider,bar)
    QTest.mouseClick(bar,Qt.LeftButton,pos=QPoint(bar.width()//2,min(handle.bottom()+40,bar.height()-4)))
    qapp.processEvents();assert bar.value()>bar.minimum()
    bar.setValue(bar.maximum());qapp.processEvents();assert bar.value()==bar.maximum()


def test_tree_show_and_hide_location_read_only_without_document_edit(window,qapp,monkeypatch):
    face,_=model(window);window.session.read_only=True
    before=project_to_dict(window.project);ref=EntityRef('face',face)
    index=window.tree._ref_rows[ref][0].index();window.tree.scrollTo(index);qapp.processEvents()
    errors=[]
    def choose(label):
        menu=QApplication.activePopupWidget()
        try:
            assert isinstance(menu,QMenu)
            action=next(a for a in menu.actions() if a.text()==label)
            assert action.isEnabled();action.trigger()
        except BaseException as error:errors.append(error)
        finally:
            if menu is not None:menu.close()
    QTimer.singleShot(0,lambda:choose('Show location'))
    window.tree._context_menu(window.tree.visualRect(index).center());qapp.processEvents()
    assert not errors,errors
    marker=window.viewport.location_callout
    assert marker.reference==ref and marker.isVisible()
    assert f'Face {face}' in marker.text and 'x, y, z' in marker.text
    edge=window.project.geometry.faces[face].loop[0].edge
    np.testing.assert_array_equal(marker.world,window.project.geometry.sample_edge(edge,[.5])[0])
    assert marker.testAttribute(Qt.WA_TransparentForMouseEvents)
    marker.grab()  # exercise the actual painter/projection path
    assert project_to_dict(window.project)==before
    QTimer.singleShot(0,lambda:choose('Hide location'))
    window.tree._context_menu(QPoint(-1,-1));qapp.processEvents()
    assert not errors,errors
    assert marker.reference is None and not marker.isVisible() and not marker._timer.isActive()
    assert project_to_dict(window.project)==before


def test_location_tracks_points_edges_and_clears_on_document_replacement(window,qapp):
    face,points=model(window);g=window.project.geometry
    for ref in (EntityRef('vertex',points[0]),EntityRef('edge',g.faces[face].loop[0].edge)):
        selected=tuple(window.selection.items);window.viewport.show_location(ref);qapp.processEvents()
        assert window.viewport.location_callout.reference==ref
        assert tuple(window.selection.items)==selected
    marker=window.viewport.location_callout
    g.add_point(20.,30.,40.);marker.refresh()
    assert marker.reference is None and not marker._timer.isActive()
    window.viewport.show_location(EntityRef('face',face))
    window._set_project(Project('replacement'));qapp.processEvents()
    assert marker.reference is None and not marker._timer.isActive()


def test_location_can_be_dismissed_when_target_is_behind_camera(window,qapp,monkeypatch):
    face,_=model(window)
    window.viewport.show_location(EntityRef('face',face))
    marker=window.viewport.location_callout
    monkeypatch.setattr(window.viewport.canvas,'project_point',lambda point:None)
    marker.grab();qapp.processEvents()
    assert marker.reference==EntityRef('face',face)
    window.viewport.hide_location()
    assert marker.reference is None and not marker._timer.isActive()


def test_beam_location_uses_a_live_member_edge(window,qapp):
    face,_=model(window);g=window.project.geometry
    edge=g.faces[face].loop[0].edge
    with g.transaction():
        part=g.add_part()
        member=g.add_members(((edge,),),part_id=part)[0]
    menu=QMenu(window.tree)
    window.tree._location_actions(menu,('members',member))
    next(action for action in menu.actions() if action.text()=='Show location').trigger()
    marker=window.viewport.location_callout
    assert marker.reference==EntityRef('edge',edge) and f'Beam {member}' in marker.text


def test_location_reprojects_arrow_with_device_pixel_ratio(window,qapp,monkeypatch):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QFontMetrics,QPainter
    from anyfem.ui.qt import location
    face,_=model(window);window.viewport.show_location(EntityRef('face',face))
    marker=window.viewport.location_callout;marker._timer.stop()
    canvas=window.viewport.canvas;surface=canvas.event_widget;projection=[(200.,100.,1.)];lines=[]
    monkeypatch.setattr(canvas,'project_point',lambda world:projection[0])
    monkeypatch.setattr(surface,'devicePixelRatioF',lambda:2.)
    class Painter:
        Antialiasing=QPainter.Antialiasing
        def __init__(self,*args):pass
        def setRenderHint(self,*args):pass
        def setPen(self,*args):pass
        def setBrush(self,*args):pass
        def drawLine(self,start,end):lines.append((start,end))
        def drawPolygon(self,*args):pass
        def drawRoundedRect(self,*args):pass
        def drawText(self,*args):pass
        def fontMetrics(self):return QFontMetrics(window.font())
    monkeypatch.setattr(location,'QPainter',Painter)
    offset=surface.mapTo(window.viewport.widget,QPoint(0,0))
    marker.paintEvent(None)
    assert lines[-1][1]==QPointF(offset.x()+100.,offset.y()+50.)
    projection[0]=(400.,200.,1.);marker.paintEvent(None)
    assert lines[-1][1]==QPointF(offset.x()+200.,offset.y()+100.)
