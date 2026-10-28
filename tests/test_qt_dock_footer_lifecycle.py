"""Real Qt lifecycle checks for the status footer hosted in the bottom dock tab row."""
from __future__ import annotations

import os
import time
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets",allow_module_level=True)
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QDockWidget, QLabel, QTabBar, QWidget
from shiboken6 import isValid
from anyfem.ui.qt.app import QtFemWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _new_window(tmp_path,monkeypatch):
    from anyfem.io import recovery
    monkeypatch.setattr(recovery,"default_recovery_root",lambda:tmp_path/"recovery")
    return QtFemWindow(viewer_backend=os.environ.get("ANYFEM_QT_TEST_BACKEND","software"))


@pytest.fixture
def window(qapp,tmp_path,monkeypatch):
    window=_new_window(tmp_path,monkeypatch)
    window.show();qapp.processEvents()
    window.reset_workspace_layout();qapp.processEvents()
    yield window
    window.session.mark_saved()
    window.close();qapp.processEvents()


def wait_until(qapp,predicate,timeout=10):
    end=time.monotonic()+timeout
    while not predicate() and time.monotonic()<end:
        qapp.processEvents()
        time.sleep(0.01)
    assert predicate(), "Qt footer did not reach the expected state within its bounded wait"


def settled(window):
    footer=window._dock_footer
    return not footer._timer.isActive() and all(isValid(bar) for bar in footer._watched)


def hosted(window):
    footer=window._dock_footer;bar=footer.tab_bar
    return (settled(window) and footer.in_tab_row and bar is not None and isValid(bar) and bar.isVisible()
            and footer.status.parentWidget() is window and footer.status.isVisible())


def test_constructor_owns_footer_and_hosts_status_in_bottom_tab_row(window,qapp):
    footer=window._dock_footer
    assert footer.parent() is window and footer._timer.parent() is footer
    wait_until(qapp,lambda:hosted(window))
    bar=footer.tab_bar
    assert {"Jobs","Messages"}<={bar.tabText(i) for i in range(bar.count())}
    assert bar in footer._watched


def test_child_added_only_schedules_one_coalesced_sync(window,qapp,monkeypatch):
    wait_until(qapp,lambda:settled(window))
    calls=[];find=window.findChildren
    def spy(*args):
        calls.append(args);return find(*args)
    monkeypatch.setattr(window,"findChildren",spy)

    class Child(QWidget):
        # A Python subclass is still under construction when Qt sends ChildAdded.
        def __init__(self,parent):
            super().__init__(parent);self.marker=QLabel("child",self)

    children=[Child(window) for _ in range(5)]+[QObject(window) for _ in range(5)]
    assert calls==[]
    assert window._dock_footer._timer.isActive()
    wait_until(qapp,lambda:not window._dock_footer._timer.isActive())
    assert calls
    for child in children:child.deleteLater()
    qapp.sendPostedEvents(None,QEvent.DeferredDelete);qapp.processEvents()
    assert not any(isValid(child) for child in children)
    wait_until(qapp,lambda:hosted(window))


def test_added_removed_and_hidden_docks_keep_footer_consistent(window,qapp):
    footer=window._dock_footer
    jobs,messages,run=window._workspace_docks[2:]
    wait_until(qapp,lambda:hosted(window))
    for round_ in range(3):
        extra=QDockWidget(f"Extra {round_}",window);extra.setObjectName(f"Extra {round_}")
        extra.setWidget(QLabel("extra"));window.addDockWidget(Qt.BottomDockWidgetArea,extra)
        window.tabifyDockWidget(jobs,extra);extra.raise_()
        wait_until(qapp,lambda:hosted(window))
        assert "Jobs" in {footer.tab_bar.tabText(i) for i in range(footer.tab_bar.count())}
        window.removeDockWidget(extra);extra.deleteLater()
        qapp.sendPostedEvents(None,QEvent.DeferredDelete)
        wait_until(qapp,lambda:hosted(window))
    for dock in (jobs,messages,run):dock.hide()
    wait_until(qapp,lambda:settled(window) and not footer.in_tab_row)
    assert footer.tab_bar is None
    assert footer.status.parentWidget() is window and footer.status.isVisible()
    jobs.show();messages.show();window.tabifyDockWidget(jobs,messages);jobs.raise_()
    wait_until(qapp,lambda:hosted(window))
    tab_bars=[bar for bar in window.findChildren(QTabBar) if bar.parentWidget() is window]
    assert footer.tab_bar in tab_bars and set(tab_bars)<=footer._watched


def test_close_and_destruction_flush_without_late_footer_sync(qapp,tmp_path,monkeypatch):
    window=_new_window(tmp_path,monkeypatch)
    window.show();qapp.processEvents()
    footer=window._dock_footer;timer=footer._timer
    wait_until(qapp,lambda:not timer.isActive())
    window.session.mark_saved()
    window.close();qapp.processEvents()
    QApplication.sendEvent(window,QEvent(QEvent.LayoutRequest))
    wait_until(qapp,lambda:not timer.isActive())
    window.deleteLater()
    qapp.sendPostedEvents(None,QEvent.DeferredDelete);qapp.processEvents()
    assert not isValid(footer) and not isValid(timer)
    footer.schedule();footer.sync()
