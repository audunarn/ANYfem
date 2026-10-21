"""Real dock geometry: shared footer, backend control and compact monitor."""
import os
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS")!="1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1",allow_module_level=True)

from PySide6.QtCore import Qt,QPoint
from PySide6.QtGui import QStatusTipEvent
from PySide6.QtWidgets import QApplication,QStatusBar
from PySide6.QtTest import QTest
from test_qt_geometry_actions import qapp,window


def settle(qapp):
    for _ in range(3):qapp.processEvents()


def test_renderer_and_status_share_native_bottom_tab_row(window,qapp):
    window.run_dock.show();window.run_dock.raise_();settle(qapp)
    footer=window._dock_footer
    assert footer.in_tab_row and footer.tab_bar is not None
    bar=footer.tab_bar
    renderer=window.renderer
    top=renderer.mapTo(window,QPoint(0,0)).y()
    assert bar.geometry().top()<=top<=bar.geometry().bottom()
    assert window.statusBar().geometry().bottom()==bar.geometry().bottom()
    assert window.statusBar().geometry().left()>max(bar.tabRect(i).right() for i in range(bar.count()))
    for dock in window._workspace_docks[2:]:
        dock.show();dock.raise_();settle(qapp)
        assert renderer.isVisible() and footer.in_tab_row
    window.set_status("Footer status is visible")
    assert window.statusBar().currentMessage()=="Footer status is visible"
    QApplication.sendEvent(window,QStatusTipEvent("Hover feedback"));settle(qapp)
    assert len(window.findChildren(QStatusBar))==1


def test_bottom_dock_can_shrink_with_monitor_active(window,qapp):
    dock=window.run_dock
    dock.show();dock.raise_();settle(qapp)
    window.resizeDocks([dock],[350],Qt.Vertical);settle(qapp)
    expanded=dock.height()
    window.resizeDocks([dock],[90],Qt.Vertical);settle(qapp)
    assert dock.height()<expanded-100
    assert dock.height()<180
    assert window.run_monitor.content_scroll.verticalScrollBar().maximum()>0
    assert window.renderer.isVisible()


def test_native_dock_divider_drags_down(window,qapp):
    dock=window.run_dock
    dock.show();dock.raise_();settle(qapp)
    window.resizeDocks([dock],[350],Qt.Vertical);settle(qapp)
    QTest.qWait(150)
    expanded=dock.height()
    start=QPoint(dock.geometry().center().x(),dock.geometry().top()-2)
    end=start+QPoint(0,160)
    QTest.mouseMove(window,start);QTest.qWait(50)
    QTest.mousePress(window,Qt.LeftButton,pos=start)
    for distance in range(10,161,10):
        QTest.mouseMove(window,start+QPoint(0,distance),delay=10)
        qapp.processEvents()
    QTest.mouseRelease(window,Qt.LeftButton,pos=end)
    settle(qapp)
    assert dock.height()<expanded-80


def test_footer_follows_closed_and_restored_bottom_docks(window,qapp):
    for dock in window._workspace_docks[2:]:dock.hide()
    settle(qapp)
    assert not window._dock_footer.in_tab_row
    assert window.renderer.isVisible()
    jobs,messages=window._workspace_docks[2:4]
    jobs.show();messages.show();jobs.raise_();settle(qapp)
    assert window._dock_footer.in_tab_row
    window.resize(1350,850);settle(qapp)
    bar=window._dock_footer.tab_bar
    assert window.statusBar().geometry().right()<=bar.geometry().right()
    assert window.renderer.isVisible()
